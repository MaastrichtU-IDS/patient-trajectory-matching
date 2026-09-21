"""End-to-end retrospective queries over admitted MIMIC input-component records."""
import argparse
import csv
from collections import Counter, defaultdict
from itertools import product
import json
from pathlib import Path
import zlib

from jsonschema import Draft202012Validator
from rdflib import Literal, RDF, XSD

from . import bounded_intervals as bt
from . import exact_intervals as ei
from . import mimic_inputevents as admission
from . import patient_local as local
from . import semantic_support as semantic

PROFILE = 'mimic-record-query-1.0'
SCHEMA = ei.ROOT / 'schemas/mimic-record-query.schema.json'
FILES = ('patterns/mimic_record_query.py', 'schemas/mimic-record-query.schema.json',
         'patterns/patient_local.py', 'schemas/patient-local.schema.json', 'schemas/patient-local-record.schema.json',
         'patterns/mimic_inputevents.py', 'patterns/bounded_intervals.py', 'patterns/bounded_rdf.py',
         'patterns/temporal_stn.py', 'patterns/exact_intervals.py', 'patterns/pro_solid.py',
         'schemas/bounded-interval.schema.json', 'ontology/patient-local-profile.ttl',
         'ontology/bounded-interval-profile.ttl', 'ontology/bounded-rdf-profile.ttl',
         'ontology/exact-interval-profile.ttl', 'ontology/pro-solid-profile.ttl',
         'ontology/vendor/sulo-0.2.14.ttl', 'patterns/requirements.lock.txt', *semantic.FILES)
N = 'https://example.org/trajectory/record-selection/'
MAX_EVENTS = 30  # At most 61 process/role/person individuals per episode.
MAX_BINDINGS = 20_000
MAX_EPISODES = 256


def validate_request(request):
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(request)), 'INVALID_RECORD_QUERY')
    slots = bt.unique(request['slots'], 'id')
    selected_items = set()
    for slot in slots.values():
        ei.require(len(slot['itemids']) == len(set(slot['itemids'])), 'DUPLICATE_ITEM_SELECTOR')
        selected_items.update(slot['itemids'])
    ei.require(len(selected_items) <= 16, 'ITEM_SELECTOR_LIMIT')
    query = {'profile': local.RECORD_QUERY_PROFILE, 'id': request['id'],
             'slots': [{'id': s['id'], 'class_iri': str(bt.BT.RecordedInputSegment)} for s in request['slots']],
             'constraints': request['constraints']}
    local.validate_query(query)
    if request['patient_ids'] != 'all':
        ei.require(len(set(request['patient_ids'])) == len(request['patient_ids']), 'DUPLICATE_PATIENT_SCOPE')
    return selected_items


def event_id(segment):
    return 'input_' + segment['source']['csv_sha256'] + '_' + str(segment['source']['record_number'])


def build_episode(segments, origin, context_id, request):
    patient = segments[0]['patient_id']; episode = segments[0]['icu_stay_id']
    clock_id = 'patient_' + patient
    source = {'profile': local.RECORD_PROFILE, 'dataset_id': request['dataset_id'],
        'snapshot_id': context_id + '_' + patient + '_' + episode,
        'clocks': [{'clock_id': clock_id, 'origin': origin['label'].replace(' ', 'T'),
                    'scope': 'patient:' + patient, 'policy': local.POLICY,
                    'origin_source_key': origin['source_record_id']}],
        'variables': [], 'events': [], 'constraints': []}
    row_evidence = {}
    for segment in segments:
        eid = event_id(segment)
        for side in ('start', 'end'):
            raw = segment[side]['raw_value']
            source['variables'].append({'id': eid + '_' + side, 'patient_id': patient,
                'episode_id': episode, 'clock_id': clock_id, 'local_lower': raw, 'local_upper': raw,
                'source_key': segment['id'] + ':' + side + ':recorded-label-exact'})
        source['events'].append({'id': eid, 'record_id': 'record_' + eid, 'patient_id': patient,
            'episode_id': episode, 'event_kind': 'recorded_input_segment', 'status': 'recorded',
            'start_var': eid + '_start', 'end_var': eid + '_end', 'source_key': segment['id']})
        row_evidence[eid] = {'record_id': segment['id'], 'source': segment['source'],
            'item_source': segment['item_source'], 'stay_source': segment['stay_source'],
            'itemid': segment['item']['itemid'], 'item_label': segment['item']['label'],
            'source_status': segment['statusdescription'], 'recorded_at': segment['recorded_at'],
            'source_available_at': None, 'clinical_precision': 'unverified',
            'orderid': segment['orderid'], 'linkorderid': segment['linkorderid']}
    # Execute through the closed RDF route, checking labels against coordinates.
    graph = local.export_source(source)
    for node in graph.subjects(RDF.type, ei.EX.SourceLocation):
        graph.set((node, ei.S.hasValue, Literal('derived-from:sha256:' + segments[0]['source']['file_sha256'],
                                              datatype=XSD.string, normalize=False)))
    snapshot = local.prepare_graph(graph)
    items = sorted({item for slot in request['slots'] for item in slot['itemids']})
    item_classes = {item: N + 'item_' + item for item in items}
    group_classes = {slot['id']: N + 'slot_' + slot['id'] for slot in request['slots']}
    module = {'profile': semantic.PROFILE, 'source_sha256': ei.digest(ei.canonical(snapshot.source)),
        'classes': sorted([*item_classes.values(), *group_classes.values()]),
        'individuals': [], 'class_assertions': [], 'property_assertions': [], 'rules': [], 'disjoint': []}
    for segment in segments:
        module['class_assertions'].append({'individual': snapshot.evidence[event_id(segment)]['process'],
                                          'class': item_classes[segment['item']['itemid']]})
    for slot in request['slots']:
        for item in slot['itemids']:
            module['rules'].append({'id': slot['id'] + '_item_' + item,
                                   'if': {'class': item_classes[item]}, 'then': group_classes[slot['id']]})
    query = {'profile': local.SEMANTIC_QUERY_PROFILE, 'id': request['id'],
        'slots': [{'id': slot['id'], 'class_iri': group_classes[slot['id']]} for slot in request['slots']],
        'constraints': request['constraints']}
    return snapshot, module, query, row_evidence


def reference_bindings(segments, request):
    """Independent exact-record oracle; no STN, normalized coordinates or OWL engine."""
    names = sorted(slot['id'] for slot in request['slots'])
    selectors = {s['id']: set(s['itemids']) for s in request['slots']}
    candidates = [[s for s in segments if s['item']['itemid'] in selectors[name]] for name in names]
    found = set()
    for values in product(*candidates):
        if len({s['id'] for s in values}) != len(values):
            continue
        binding = dict(zip(names, values)); holds = True
        for constraint in request['constraints']:
            a, b = binding[constraint['left']], binding[constraint['right']]
            s, e = (admission.local_time(a[side]['raw_value']) for side in ('start', 'end'))
            u, v = (admission.local_time(b[side]['raw_value']) for side in ('start', 'end'))
            op = constraint['operator']
            if op == 'before': truth = e < u
            elif op == 'meets': truth = e == u
            elif op == 'overlaps': truth = s < u < e < v
            else:
                delta = u - e
                gap = (delta.days * 86400 + delta.seconds) * 1_000_000 + delta.microseconds
                truth = constraint['min_gap_us'] <= gap <= constraint['max_gap_us']
            holds = holds and truth
        if holds:
            found.add(tuple((name, event_id(binding[name])) for name in names))
    return found


def run(input_dir, request):
    request = json.loads(ei.canonical(request))
    selected_items = validate_request(request)
    folder = Path(input_dir); paths = {}
    for table in admission.HEADERS:
        options = [folder / (table + suffix) for suffix in ('.csv', '.csv.gz')]
        present = [p for p in options if p.is_file()]
        ei.require(len(present) == 1, 'MISSING_OR_AMBIGUOUS_TABLE:' + table)
        paths[table] = present[0]
    staged = admission.stage(*(paths[t] for t in admission.HEADERS), dataset_id=request['dataset_id'])
    dimensions = {}
    for table in ('icustays', 'd_items'):
        dimensions[table], descriptor = admission.read_table(paths[table], table)
        expected = next(f for f in staged['summary']['context']['files'] if f['table'] == table)
        ei.require(descriptor == expected, 'SOURCE_CHANGED_DURING_RUN')
    stays = dimensions['icustays']
    ei.require(selected_items <= {r['itemid'] for r in dimensions['d_items'] if r['linksto'] == 'inputevents'},
               'UNKNOWN_INPUT_ITEM_SELECTOR')
    roster_patients = {r['subject_id'] for r in stays}
    patients = roster_patients if request['patient_ids'] == 'all' else set(request['patient_ids'])
    ei.require(patients <= roster_patients, 'UNKNOWN_REQUESTED_PATIENT')
    roster = sorted((r['subject_id'], r['stay_id']) for r in stays if r['subject_id'] in patients)
    ei.require(len(roster) <= MAX_EPISODES, 'EPISODE_LIMIT')
    context = {'profile': PROFILE, 'request': request, 'admission_context_id': staged['summary']['context_id'],
        'time_semantics': 'recorded_source_intervals', 'endpoint_policy': 'recorded-label-exact-v1',
        'origin_policy': 'minimum_admitted_start_per_patient_before_query_filtering',
        'mapping_policy': 'item-code-to-recorded-process-class-v1',
        'scope': 'requested_patients_and_admitted_inputevents_in_supplied_snapshot',
        'history_mode': 'retrospective_source_records', 'physical_elapsed_time_verified': False,
        'clinical_mapping_verified': False, 'source_history_verified': False,
        'limits': {'events_per_episode': MAX_EVENTS, 'candidate_bindings': MAX_BINDINGS, 'episodes': MAX_EPISODES},
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    context_id = ei.digest(ei.canonical(context))
    origins = {}
    groups = defaultdict(list)
    for segment in staged['segments']:
        patient = segment['patient_id']; label = segment['start']['raw_value']
        if patient not in origins or (label, segment['id']) < (origins[patient]['label'], origins[patient]['source_record_id']):
            origins[patient] = {'label': label, 'source_record_id': segment['id']}
        if patient in patients and segment['item']['itemid'] in selected_items:
            groups[(patient, segment['icu_stay_id'])].append(segment)
    reconciliation = []
    for row in staged['reconciliation']:
        if row['outcome'] != 'STAGED_SEGMENT': outcome = 'NOT_ADMITTED'
        elif row['raw']['subject_id'] not in patients: outcome = 'OUTSIDE_PATIENT_SCOPE'
        elif row['raw']['itemid'] not in selected_items: outcome = 'OUTSIDE_ITEM_SCOPE'
        else: outcome = 'SELECTED_RECORD'
        reconciliation.append({'record_id': row['record_id'], 'admission_outcome': row['outcome'], 'query_outcome': outcome})
    episodes = []; blocked = False
    for patient, stay in roster:
        segments = sorted(groups[(patient, stay)], key=lambda s: s['id'])
        counts = [sum(s['item']['itemid'] in slot['itemids'] for s in segments) for slot in request['slots']]
        candidate_count = 1
        for n in counts: candidate_count *= n
        episode = {'patient_id': patient, 'icu_stay_id': stay, 'selected_records': len(segments),
                   'candidate_bindings_before_distinctness': candidate_count, 'run': None,
                   'reference_verified': False, 'source_coverage': 'ADMITTED_RECORDS_ONLY'}
        if not all(counts):
            episode.update(status='NO_ADMITTED_RECORD_MATCH', reference_verified=True,
                           reason='A_REQUIRED_ITEM_SELECTOR_HAS_NO_ADMITTED_RECORD')
        elif len(segments) > MAX_EVENTS or candidate_count > MAX_BINDINGS:
            episode.update(status='BLOCKED_RESOURCE_LIMIT'); blocked = True
        else:
            snapshot, module, query, evidence = build_episode(segments, origins[patient], context_id, request)
            result = local.execute_semantic(snapshot, query, module)
            episode.update(run=result, graph_turtle=ei.turtle_text(snapshot.graph),
                           semantic_module=module, row_evidence=evidence)
            if result['status'] != 'READY':
                episode.update(status='BLOCKED_SEMANTICS'); blocked = True
            else:
                bindings = result['matching']['trajectories'][0]['bindings']
                actual = {tuple(sorted((name, value['event_id']) for name, value in b['slots'].items()))
                          for b in bindings if b['status'] == 'CERTAIN'}
                expected_bindings = reference_bindings(segments, request)
                if actual != expected_bindings or any(b['status'] not in ('CERTAIN', 'IMPOSSIBLE') for b in bindings):
                    episode.update(status='BLOCKED_REFERENCE_DISAGREEMENT'); blocked = True
                else:
                    episode.update(status='RECORDED_PATTERN_MATCH' if actual else 'NO_ADMITTED_RECORD_MATCH',
                                   reference_verified=True, matched_bindings=len(actual))
        episodes.append(episode)
    matched = sorted({e['patient_id'] for e in episodes if e['status'] == 'RECORDED_PATTERN_MATCH'})
    summary = {'profile': PROFILE, 'context_id': context_id, 'context': context,
        'status': 'BLOCKED_INCOMPLETE_EXECUTION' if blocked else 'COMPLETED_RECORDED_QUERY',
        'search_complete_over_admitted_records': not blocked,
        'clinical_mapping_verified': False, 'clinical_knowledge_status': 'UNKNOWN',
        'source_history_verified': False, 'physical_elapsed_time_verified': False,
        'input_rows': staged['summary']['input_rows'], 'admission_outcome_counts': staged['summary']['outcome_counts'],
        'query_outcome_counts': dict(sorted(Counter(r['query_outcome'] for r in reconciliation).items())),
        'represented_patients': len(patients), 'represented_icu_stays': len(roster),
        'episode_outcome_counts': dict(sorted(Counter(e['status'] for e in episodes).items())),
        'matched_patients': None if blocked else len(matched),
        'matched_bindings': None if blocked else sum(e.get('matched_bindings', 0) for e in episodes),
        'semantic_episodes': sum(e['run'] is not None for e in episodes),
        'candidate_bindings_checked': sum(e['candidate_bindings_before_distinctness'] for e in episodes if e['run'] is not None),
        'reference_verified': not blocked and all(e['reference_verified'] for e in episodes)}
    return {'summary': summary, 'matched_patient_ids': None if blocked else matched,
            'admission': staged, 'query_reconciliation': reconciliation, 'episodes': episodes,
            'origins': {p: o for p, o in sorted(origins.items()) if p in patients}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ei.ROOT / 'examples/mimic-inputevents')
    parser.add_argument('--request', type=Path, default=ei.ROOT / 'examples/mimic-record-query/synthetic-request.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/mimic-record-query-run/result.json')
    args = parser.parse_args()
    try:
        protected = {args.request.resolve(), *( (args.input_dir / (table + suffix)).resolve()
                     for table in admission.HEADERS for suffix in ('.csv', '.csv.gz'))}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        result = run(args.input_dir, json.loads(args.request.read_text()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(result, indent=2) + '\n'); temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    print(json.dumps(result['summary'], sort_keys=True))
    if result['summary']['status'] != 'COMPLETED_RECORDED_QUERY':
        parser.exit(2)


if __name__ == '__main__':
    main()
