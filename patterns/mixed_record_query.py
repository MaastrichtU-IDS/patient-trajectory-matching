"""Baseline/treatment eligibility followed by recorded point follow-up inspection."""
import argparse
from collections import defaultdict
from copy import deepcopy
from dataclasses import asdict
from decimal import localcontext
import json
from pathlib import Path
import tempfile

from jsonschema import Draft202012Validator
from . import bounded_cohort as cohort, bounded_intervals as bt, claim_projection as cp, claim_rdf as cr
from . import exact_intervals as ei, local_claim_projection as intervals, measurement_claims as points
from . import patient_local as local, semantic_support as semantic
from .temporal_stn import Edge, Network, ZERO

PROFILE = 'mixed-record-trajectory-1.0'
SCHEMA = ei.ROOT / 'schemas/mixed-record-query.schema.json'
COORDINATE_ORIGIN = '0001-01-01T00:00:00'
MAX_VARIABLES = 64
MAX_BASELINE_TESTS = 128
MAX_FOLLOWUP_TESTS = 256
FILES = tuple(sorted(set(cp.FILES + intervals.FILES + points.FILES + (
    'patterns/mixed_record_query.py', 'schemas/mixed-record-query.schema.json',
    'patterns/bounded_cohort.py', 'patterns/temporal_stn.py'))))


def validate_query(query):
    schema = json.loads(SCHEMA.read_text())
    ei.require(not list(Draft202012Validator(schema).iter_errors(query)), 'INVALID_MIXED_QUERY')
    semantic.iri(query['treatment_class_iri'])
    points.decimal_value(query['baseline']['value_lexical'])
    for key, lo, hi in [('baseline', 'min_before_start_us', 'max_before_start_us'),
                        ('followup', 'min_after_anchor_us', 'max_after_anchor_us')]:
        window = query[key]
        ei.checked_us(window[lo]); ei.checked_us(window[hi])
        ei.require(window[lo] <= window[hi], 'REVERSED_MIXED_WINDOW')
        ei.require(window['unit_lexical'].strip() == window['unit_lexical'], 'INVALID_QUERY_UNIT')
    return query


def validate_alignment(alignment, interval_store, measurement_store):
    schema = json.loads(SCHEMA.read_text())['$defs']['alignment']
    ei.require(not list(Draft202012Validator(schema).iter_errors(alignment)), 'INVALID_CLOCK_ALIGNMENT')
    ei.require(alignment['interval_store_sha256'] == cr.digest(interval_store) and
               alignment['measurement_store_sha256'] == cr.digest(measurement_store), 'STALE_CLOCK_ALIGNMENT')
    ei.require(interval_store['dataset_id'] == measurement_store['dataset_id'], 'CROSS_DATASET_MIXED_QUERY')
    clocks = [{c['clock_id']: c for c in store['clocks']} for store in (interval_store, measurement_store)]
    bindings = set()
    for binding in alignment['bindings']:
        key = (binding['patient_id'], binding['interval_clock_id'], binding['measurement_clock_id'])
        ei.require(key not in bindings, 'DUPLICATE_CLOCK_ALIGNMENT'); bindings.add(key)
        for table, name in zip(clocks, ('interval_clock_id', 'measurement_clock_id')):
            ei.require(binding[name] in table, 'UNKNOWN_ALIGNMENT_CLOCK')
            clock = table[binding[name]]
            ei.require(clock['scope'] == 'patient:' + binding['patient_id'] and clock['policy'] == local.POLICY,
                       'ALIGNMENT_PATIENT_OR_POLICY_MISMATCH')
    return bindings


def _compile(source, measurement_selection):
    """Preserve all selected bounds/constraints; only intervals get duration edges."""
    groups, edges, variables, evidence = defaultdict(list), defaultdict(list), {}, {}
    for namespace, rows in [('i', source['variables']), ('m', measurement_selection['selected']['variables'])]:
        for row in rows:
            name = namespace + ':' + row['id']; variables[name] = row; group = bt.scope(row)
            groups[group].append(name)
            lower = local.coordinate(row['local_lower'], COORDINATE_ORIGIN)
            upper = local.coordinate(row['local_upper'], COORDINATE_ORIGIN)
            for side, left, right, limit in [('lower', ZERO, name, -lower), ('upper', name, ZERO, upper)]:
                edge = Edge('bound:' + name + ':' + side, left, right, limit)
                edges[group].append(edge)
                evidence[edge.id] = {'kind': 'variable_bound', 'namespace': namespace, 'source': row,
                                     'coordinate_origin': COORDINATE_ORIGIN, 'bound_us': lower if side == 'lower' else upper}
    for constraint in source['constraints']:
        group = bt.scope(variables['i:' + constraint['left_var']])
        edge = Edge('source:i:' + constraint['id'], 'i:' + constraint['left_var'],
                    'i:' + constraint['right_var'], constraint['upper_us'])
        edges[group].append(edge); evidence[edge.id] = {'kind': 'source_constraint', 'source': constraint}
    for event in source['events']:
        edge = Edge('proper:i:' + event['id'], 'i:' + event['start_var'], 'i:' + event['end_var'], -1)
        edges[bt.scope(event)].append(edge); evidence[edge.id] = {'kind': 'proper_interval', 'source': event}
    networks = {}
    for group, names in sorted(groups.items()):
        ei.require(len(names) <= MAX_VARIABLES, 'MIXED_VARIABLE_LIMIT')
        network = Network(names, sorted(edges[group], key=lambda e: e.id))
        if not network.feasible: raise bt.InconsistentSource(group, network, evidence)
        networks[group] = network
    return networks, variables, evidence


def _baseline_edges(event, record, query):
    p, start = 'm:' + record['event']['time_var'], 'i:' + event['start_var']
    return [Edge('query:baseline:min', p, start, -query['min_before_start_us']),
            Edge('query:baseline:max', start, p, query['max_before_start_us'])]


def _followup_edges(event, record, query):
    p, anchor = 'm:' + record['event']['time_var'], 'i:' + event[query['anchor'] + '_var']
    edges = [Edge('query:followup:min', anchor, p, -query['min_after_anchor_us']),
             Edge('query:followup:max', p, anchor, query['max_after_anchor_us'])]
    if query['within_interval']:
        edges += [Edge('query:followup:inside-start', 'i:' + event['start_var'], p, 0),
                  Edge('query:followup:inside-end', p, 'i:' + event['end_var'], -1)]
    return edges


def _item_unit(record, selector):
    event = record['event']
    return event['item_id'] in selector['item_ids'] and event['unit_lexical'] == selector['unit_lexical']


def _scalar(record, selector):
    value, bound = points.decimal_value(record['event']['value_lexical']), points.decimal_value(selector['value_lexical'])
    return {'lt': value < bound, 'le': value <= bound, 'eq': value == bound,
            'ge': value >= bound, 'gt': value > bound}[selector['operator']]


def _change(baseline, followup):
    a, b = baseline['event'], followup['event']
    if (a['item_id'], a['unit_lexical']) != (b['item_id'], b['unit_lexical']):
        return {'status': 'INCOMPARABLE_ITEM_OR_UNIT', 'delta': None}
    # The bounded input decimal spellings need < 750 digits for any exact difference.
    with localcontext() as context:
        context.prec = 800
        value = points.decimal_value(b['value_lexical']) - points.decimal_value(a['value_lexical'])
    return {'status': 'RECORDED_NUMERIC_DIFFERENCE', 'delta': format(value, 'f'),
            'unit_lexical': a['unit_lexical'], 'clinical_improvement_classified': False}


def execute(interval_store, interval_policy, semantic_policy, measurement_store, measurement_policy,
            alignment, query, *, timeout_seconds=20):
    # Closed source profiles and stale alignment fail before backend work.
    intervals.validate_store(interval_store); points.validate_store(measurement_store); validate_query(query)
    ei.require(interval_store['profile'] == intervals.RECORD_STORE_PROFILE, 'MIXED_REQUIRES_RECORDED_INTERVALS')
    aligned = validate_alignment(alignment, interval_store, measurement_store)
    interval_store, interval_policy, semantic_policy, measurement_store, measurement_policy, alignment, query = deepcopy(
        (interval_store, interval_policy, semantic_policy, measurement_store, measurement_policy, alignment, query))
    interval_query = {'profile': local.SEMANTIC_QUERY_PROFILE, 'id': query['id'],
                      'slots': [{'id': 'treatment', 'class_iri': query['treatment_class_iri']}], 'constraints': []}
    point_view = points.select(measurement_store, measurement_policy)
    interval_view = intervals.execute(interval_store, interval_policy, semantic_policy, interval_query,
                                      timeout_seconds=timeout_seconds)
    context = {'profile': PROFILE, 'query': query, 'alignment': alignment,
        'interval_context_id': interval_view['context_id'], 'measurement_context_id': point_view['context_id'],
        'coordinate_origin': COORDINATE_ORIGIN, 'time_domain': local.TIME_DOMAIN,
        'clock_alignment_basis': 'explicit_caller_declaration_not_independent_source_verification',
        'certainty_semantics': 'exists_named_binding_forall_feasible_selected_source_timelines',
        'eligibility': 'baseline_and_treatment_only_followup_never_required',
        'limits': {'variables_per_episode': MAX_VARIABLES, 'baseline_tests': MAX_BASELINE_TESTS,
                   'followup_tests': MAX_FOLLOWUP_TESTS},
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    result = {'profile': PROFILE, 'context': context, 'context_id': cr.digest(context),
        'interval_view': interval_view, 'measurement_view': point_view, 'baseline_bindings': None,
        'certain_patient_ids': None, 'possible_patient_ids': None, 'source_networks': None, 'edge_evidence': None,
        'search_complete_over_selected_records': False, 'clinical_mapping_verified': False,
        'clinical_knowledge_status': 'UNKNOWN', 'physical_elapsed_time_verified': False,
        'source_history_verified': False, 'causal_effect_estimated': False,
        'full_mixed_owl_reasoning_verified': False, 'scope': 'selected_record_descriptions_within_patient_episode'}
    if point_view['status'] not in ('SELECTED_MEASUREMENT_RECORDS', 'EMPTY_SELECTED_RECORDS'):
        result.update(status='BLOCKED_MEASUREMENT_VIEW'); return result
    if interval_view['status'] not in ('READY', 'EMPTY_ACCEPTED_VIEW'):
        result.update(status='BLOCKED_INTERVAL_VIEW'); return result
    source = interval_view['source'] or {'variables': [], 'events': [], 'constraints': []}
    records = point_view['records']; selected_intervals = {e['id']: e for e in source['events']}
    treatments = []
    if interval_view['matching'] is not None:
        for trajectory in interval_view['matching']['trajectories']:
            for binding in trajectory['bindings']:
                witness = binding['slots']['treatment']; event = selected_intervals[witness['event_id']]
                treatments.append((event, witness))
    baseline_records = [r for r in records if _item_unit(r, query['baseline']) and _scalar(r, query['baseline'])]
    followup_records = [r for r in records if _item_unit(r, query['followup'])]
    candidates = [(e, w, b) for e, w in treatments for b in baseline_records if bt.scope(e) == bt.scope(b['event'])]
    followup_work = sum(sum(bt.scope(e) == bt.scope(f['event']) and b['event']['id'] != f['event']['id']
                           for f in followup_records) for e, _, b in candidates)
    try:
        ei.require(len(candidates) <= MAX_BASELINE_TESTS and followup_work <= MAX_FOLLOWUP_TESTS, 'MIXED_CANDIDATE_LIMIT')
        networks, variables, edge_evidence = _compile(source, point_view['selection'])
    except (ei.ContractError, bt.InconsistentSource) as error:
        result.update(status='BLOCKED_MIXED_SOURCE', reason=str(error)); return result
    def compatible(event, record):
        clock = variables['i:' + event['start_var']]['clock_id']
        return (event['patient_id'], clock, record['clock']['clock_id']) in aligned
    bindings = []
    for event, witness, baseline in candidates:
        group = bt.scope(event); network = networks[group]
        base_edges = _baseline_edges(event, baseline, query['baseline']) if compatible(event, baseline) else []
        base = cohort.classify(network, base_edges, [] if base_edges else ['baseline_clock_alignment'])
        row = {'patient_id': group[0], 'episode_id': group[1], 'treatment': witness,
            'baseline_id': baseline['event']['id'], 'eligibility': base, 'followup': [],
            'followup_status': 'NOT_EVALUATED_INELIGIBLE', 'clinical_response_status': 'UNKNOWN'}
        if base['status'] in ('CERTAIN', 'POSSIBLE'):
            for followup in followup_records:
                if bt.scope(followup['event']) != group or followup['event']['id'] == baseline['event']['id']: continue
                edges = _followup_edges(event, followup, query['followup']) if compatible(event, followup) else []
                # Classify the whole named triple against SOURCE, not a baseline-narrowed network.
                joint = cohort.classify(network, base_edges + edges, [] if edges else ['followup_clock_alignment'])
                row['followup'].append({'measurement_id': followup['event']['id'], 'joint_binding': joint,
                    'value_change': _change(baseline, followup) if joint['status'] in ('CERTAIN', 'POSSIBLE') else None})
            statuses = {f['joint_binding']['status'] for f in row['followup']}
            row['followup_status'] = ('RECORDED_FOLLOWUP' if 'CERTAIN' in statuses else
                'POSSIBLE_RECORDED_FOLLOWUP' if 'POSSIBLE' in statuses else
                'UNKNOWN_CLOCK_ALIGNMENT' if 'INCOMPARABLE' in statuses else 'NO_SELECTED_FOLLOWUP_IN_WINDOW')
        elif base['status'] == 'INCOMPARABLE': row['followup_status'] = 'UNKNOWN_BASELINE_ALIGNMENT'
        bindings.append(row)
    result.update(status='COMPLETED_RECORD_QUERY', search_complete_over_selected_records=True,
        baseline_bindings=bindings, certain_patient_ids=sorted({b['patient_id'] for b in bindings if b['eligibility']['status'] == 'CERTAIN'}),
        possible_patient_ids=sorted({b['patient_id'] for b in bindings if b['eligibility']['status'] in ('CERTAIN', 'POSSIBLE')}),
        source_networks=[{'patient_id': g[0], 'episode_id': g[1], 'edges': [asdict(e) for e in n.edges]} for g, n in sorted(networks.items())],
        edge_evidence=edge_evidence, measurement_filter={'baseline_candidate_ids': [r['event']['id'] for r in baseline_records],
        'followup_candidate_ids': [r['event']['id'] for r in followup_records], 'source': 'selected_measurement_records_only'})
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__); folder = ei.ROOT / 'examples/mixed-record-query'
    names = ('interval-store', 'interval-policy', 'semantic-policy', 'measurement-store', 'measurement-policy', 'alignment', 'query')
    for name in names: parser.add_argument('--' + name, type=Path, default=folder / (name + '.json'))
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/mixed-record-query-run/result.json')
    args = parser.parse_args(argv); temporary = None
    try:
        paths = [getattr(args, name.replace('-', '_')) for name in names]
        ei.require(args.output.resolve() not in {p.resolve() for p in paths}, 'OUTPUT_OVERWRITES_INPUT')
        for p in paths: ei.require(p.stat().st_size <= cr.MAX_BYTES, 'INPUT_FILE_LIMIT')
        result = execute(*(json.loads(p.read_text()) for p in paths))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, RecursionError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status': result['status'], 'certain_patient_ids': result['certain_patient_ids'],
                      'possible_patient_ids': result['possible_patient_ids']}))
    return 0 if result['status'] == 'COMPLETED_RECORD_QUERY' else 2


if __name__ == '__main__':
    raise SystemExit(main())
