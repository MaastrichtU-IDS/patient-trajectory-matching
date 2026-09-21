"""Indexed, exact source-window selection with original row identities and pending batch export."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import zlib
from jsonschema import Draft202012Validator
from . import clinical_source_preflight as scan, mimic_inputevents as source
from . import mimic_claim_import as inputs, mimic_measurement_import as measurements
from . import claim_rdf as cr, claim_projection as cp, claim_isolation, exact_intervals as ei
from . import local_claim_projection as intervals, measurement_claims as points, patient_local as local
from . import mixed_record_query as mixed

PROFILE = 'indexed-source-windows-1.0'
SCHEMA = ei.ROOT / 'schemas/indexed-source-windows.schema.json'
MAX_ANCHORS = 2000
ORIGIN = '0001-01-01T00:00:00'
MAX_POSITION = local.coordinate('9999-12-31T23:59:59.999999', ORIGIN)
FILES = tuple(sorted(set(scan.FILES + ('patterns/indexed_source_windows.py', 'schemas/indexed-source-windows.schema.json'))))


def validate(request):
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(request)), 'INVALID_WINDOW_REQUEST')
    mixed.validate_query(request['query']); q = request['query']
    treatment = q['treatment_class_iri'].removeprefix(inputs.ITEM_NS)
    ei.require(q['treatment_class_iri'] == inputs.ITEM_NS + treatment and source.positive_id(treatment), 'LITERAL_TREATMENT_ITEM_REQUIRED')
    ei.require(len(q['baseline']['item_ids']) == 1 and q['baseline']['item_ids'] == q['followup']['item_ids']
               and source.positive_id(q['baseline']['item_ids'][0]), 'SINGLE_MEASUREMENT_STRATUM_REQUIRED')
    return treatment, q['baseline']['item_ids'][0]


def windows(segment, query):
    s, e = (local.coordinate(segment[k]['raw_value'], ORIGIN) for k in ('start', 'end'))
    b, f = query['baseline'], query['followup']; anchor = s if f['anchor'] == 'start' else e
    ranges = [(s-b['max_before_start_us'], s-b['min_before_start_us'])]
    lo, hi = anchor+f['min_after_anchor_us'], anchor+f['max_after_anchor_us']
    if f['within_interval']: lo, hi = max(lo, s), min(hi, e-1)
    ranges.append((lo, hi))
    # Arbitrarily large legal gaps are clipped to the represented calendar, never floated by SQLite.
    return [(max(0, lo), min(MAX_POSITION, hi)) for lo, hi in ranges if max(0, lo) <= min(MAX_POSITION, hi)]


def indexed_ids(db, segment, item, query):
    found = set()
    for lo, hi in windows(segment, query):
        found.update(row[0] for row in db.execute('''SELECT number FROM measurement
            WHERE patient=? AND stay=? AND item=? AND position BETWEEN ? AND ?''',
            (segment['patient_id'], segment['icu_stay_id'], item, lo, hi)))
    return sorted(found)


def reference_ids(segment, records, query):
    """Independent direct datetime/gap membership; no SQL, normalized coordinate or window compiler."""
    start, end = (datetime.fromisoformat(segment[k]['raw_value']) for k in ('start', 'end'))
    b, f = query['baseline'], query['followup']; anchor = start if f['anchor'] == 'start' else end
    def us(delta): return (delta.days*86400 + delta.seconds)*1000000 + delta.microseconds
    result = []
    for number, row in records:
        if (row['subject_id'], row['stay_id']) != (segment['patient_id'], segment['icu_stay_id']): continue
        if row['itemid'] not in b['item_ids']: continue
        point = datetime.fromisoformat(row['charttime'])
        baseline = b['min_before_start_us'] <= us(start-point) <= b['max_before_start_us']
        follow = f['min_after_anchor_us'] <= us(point-anchor) <= f['max_after_anchor_us']
        if f['within_interval']: follow = follow and start <= point < end
        if baseline or follow: result.append(number)
    return sorted(result)


def count_reasons(n):
    reasons = []
    if n > measurements.MAX_CLAIMS: reasons.append('MEASUREMENT_CLAIM_LIMIT')
    if 2+n > mixed.MAX_VARIABLES: reasons.append('MIXED_VARIABLE_LIMIT')
    if n > mixed.MAX_BASELINE_TESTS: reasons.append('BASELINE_UPPER_BOUND')
    if n*max(0,n-1) > mixed.MAX_FOLLOWUP_TESTS: reasons.append('FOLLOWUP_UPPER_BOUND')
    return reasons


def run(folder, request):
    treatment, item = validate(request); request = deepcopy(request)
    paths = {**inputs.source_paths(folder), **measurements.paths_for(folder)}
    staged = source.stage(**{t: paths[t] for t in source.HEADERS}, dataset_id=request['dataset_id'])
    manifests = {m['table']: m for m in staged['summary']['context']['files']}
    dimensions = {}
    for table in ('icustays', 'd_items'):
        rows, manifest = source.read_table(paths[table], table)
        ei.require(manifest == manifests[table], 'SHARED_SOURCE_CHANGED:' + table); dimensions[table] = rows
    stays = source.index_dimension(dimensions['icustays'], 'icustays', 'stay_id')
    items = source.index_dimension(dimensions['d_items'], 'd_items', 'itemid')
    ei.require(len(stays) <= inputs.MAX_EPISODES, 'WINDOW_STAY_LIMIT')
    ei.require(treatment in items and items[treatment][1]['linksto'] == 'inputevents', 'UNKNOWN_TREATMENT_ITEM')
    ei.require(item in items and items[item][1]['linksto'] == 'chartevents', 'UNKNOWN_MEASUREMENT_ITEM')
    segments = [s for s in staged['segments'] if s['item']['itemid'] == treatment]
    ei.require(len(segments) <= MAX_ANCHORS, 'WINDOW_ANCHOR_LIMIT')
    interval_origins = {}
    for s in sorted(staged['segments'], key=lambda s: (s['start']['raw_value'], s['id'])):
        interval_origins.setdefault(s['patient_id'], {'label': s['start']['raw_value'], 'source_key': s['id'], 'source': s['source']})
    input_ledger = [{'record_id': e['record_id'], 'source': e['source'],
        'outcome': e['outcome'] if e['raw']['itemid'] == treatment else 'OUTSIDE_ITEM_SCOPE',
        'reasons': e['reasons'] if e['raw']['itemid'] == treatment else [], 'duplicate_of': e['duplicate_of']}
        for e in staged['reconciliation']]
    entries, measurement_origins, chart_ledger, manifest = {}, {}, [], {}
    chart_counts = Counter(); seen = {}; candidate_count = 0
    with sqlite3.connect(':memory:') as db:
        db.execute('CREATE TABLE measurement(number INTEGER PRIMARY KEY, patient TEXT, stay TEXT, item TEXT, position INTEGER)')
        for number, raw in enumerate(scan.stream_chart(paths['chartevents'], manifest), 1):
            if raw['itemid'] != item:
                chart_counts['OUTSIDE_ITEM_SCOPE'] += 1; continue
            candidate_count += 1
            ei.require(candidate_count <= scan.MAX_SELECTED_ROWS, 'WINDOW_CANDIDATE_ROW_LIMIT')
            row_hash = source.digest(source.canonical(raw)); prior = seen.setdefault(row_hash, number)
            outcome, reasons = (('DUPLICATE_ROW', ['EXACT_DUPLICATE_SOURCE_ROW']) if prior != number
                                else measurements.admit(raw, stays, items))
            chart_counts[outcome] += 1
            chart_ledger.append({'record_number': number, 'row_sha256': row_hash, 'outcome': outcome,
                                 'reasons': reasons, 'duplicate_of_record_number': prior if prior != number else None})
            if outcome != 'ADMITTED_MEASUREMENT': continue
            entries[number] = raw
            db.execute('INSERT INTO measurement VALUES (?,?,?,?,?)',
                       (number, raw['subject_id'], raw['stay_id'], item, local.coordinate(raw['charttime'], ORIGIN)))
            old = measurement_origins.get(raw['subject_id'])
            if old is None or (raw['charttime'], number) < (old['label'], old['record_number']):
                measurement_origins[raw['subject_id']] = {'label': raw['charttime'], 'record_number': number}
        manifests['chartevents'] = manifest
        db.execute('CREATE INDEX measurement_window ON measurement(patient,stay,item,position,number)')
        grouped = defaultdict(list)
        for number, raw in entries.items(): grouped[(raw['subject_id'], raw['stay_id'])].append((number, raw))
        anchors = []; used = set()
        for segment in segments:
            observed = indexed_ids(db, segment, item, request['query'])
            expected = reference_ids(segment, grouped[(segment['patient_id'], segment['icu_stay_id'])], request['query'])
            agreement = observed == expected; reasons = count_reasons(len(observed)) if agreement else ['INDEX_REFERENCE_DISAGREEMENT']
            used.update(observed)
            anchors.append({'id': segment['id'], 'patient_id': segment['patient_id'], 'episode_id': segment['icu_stay_id'],
                'source': segment['source'], 'measurement_record_numbers': observed,
                'selected_measurement_count': len(observed), 'reference_agreement': agreement,
                'status': 'BLOCKED_WINDOW' if reasons else 'COUNT_BOUNDED_WINDOW' if observed else 'EMPTY_WINDOW', 'reasons': reasons})
    for entry in chart_ledger:
        entry['window_selection'] = ('NOT_ADMITTED' if entry['outcome'] != 'ADMITTED_MEASUREMENT' else
                                    'IN_ANCHOR_WINDOW' if entry['record_number'] in used else 'OUTSIDE_ALL_ANCHOR_WINDOWS')
    roster = []
    for _, stay in sorted(stays.values(), key=lambda value: (value[1]['subject_id'], value[1]['stay_id'])):
        own = [a for a in anchors if (a['patient_id'], a['episode_id']) == (stay['subject_id'], stay['stay_id'])]
        roster.append({'patient_id': stay['subject_id'], 'episode_id': stay['stay_id'], 'anchor_ids': [a['id'] for a in own],
                       'status': 'HAS_CANDIDATE_ANCHORS' if own else 'NO_ADMITTED_ANCHOR'})
    context = {'profile': PROFILE, 'request': request, 'source_files': list(manifests.values()),
        'interval_admission_context_id': staged['summary']['context_id'], 'calendar_basis': request['calendar_basis'],
        'interpretation': 'conditional_exact_record_window_selection_not_occurrence_or_clinical_acceptance',
        'measurement_origin_policy': 'minimum_admitted_requested_item_per_patient_before_window_selection',
        'interval_origin_policy': 'minimum_admitted_input_start_per_patient_before_item_selection',
        'index_columns': ['patient','stay','item','position','number'], 'coordinate_origin': ORIGIN,
        'limits': {'anchors': MAX_ANCHORS, 'stays': inputs.MAX_EPISODES, 'candidate_chart_rows': scan.MAX_SELECTED_ROWS,
            'chart_rows': scan.MAX_CHART_ROWS, 'raw_bytes': scan.MAX_RAW_BYTES, 'expanded_bytes': scan.MAX_EXPANDED_BYTES,
            'measurement_claims': measurements.MAX_CLAIMS, 'variables': mixed.MAX_VARIABLES,
            'baseline_tests': mixed.MAX_BASELINE_TESTS, 'followup_tests': mixed.MAX_FOLLOWUP_TESTS},
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    agreement = all(a['reference_agreement'] for a in anchors); blocked = sum(a['status'] == 'BLOCKED_WINDOW' for a in anchors)
    ei.require(sum(chart_counts.values()) == manifest['rows'], 'WINDOW_ACCOUNTING_FAILED')
    summary = {'profile': PROFILE, 'context': context, 'context_id': cr.digest(context),
        'status': 'COMPLETED_WINDOW_SELECTION' if not blocked else 'BLOCKED_INCOMPLETE_WINDOW_SELECTION',
        'full_source_scan_complete': True, 'row_accounting_complete': True, 'all_indexed_windows_agree_with_reference': agreement,
        'all_anchor_windows_within_count_bounds': not blocked,
        'input_rows': {'inputevents': len(input_ledger), 'chartevents': manifest['rows']},
        'row_outcomes': {'inputevents': dict(sorted(Counter(e['outcome'] for e in input_ledger).items())),
                         'chartevents': dict(sorted(chart_counts.items()))},
        'measurement_window_outcomes': dict(sorted(Counter(e['window_selection'] for e in chart_ledger).items())),
        'patients': len({r['patient_id'] for r in roster}), 'icu_stays': len(roster),
        'stays_without_admitted_anchor': sum(not r['anchor_ids'] for r in roster), 'anchors': len(anchors),
        'anchor_outcomes': dict(sorted(Counter(a['status'] for a in anchors).items())),
        'blocked_anchor_reasons': dict(sorted(Counter(reason for a in anchors for reason in a['reasons']).items())),
        'admitted_measurement_records': len(entries), 'unique_records_in_windows': len(used),
        'window_record_memberships': sum(a['selected_measurement_count'] for a in anchors),
        'max_window_records': max((a['selected_measurement_count'] for a in anchors), default=0),
        'claims_created': 0, 'accepted_claims': 0, 'mixed_query_executed': False, 'cohort_membership': None,
        'clinical_mapping_verified': False, 'source_history_verified': False, 'physical_elapsed_time_verified': False,
        'full_mimic_analyzed': False}
    return {'summary': summary, 'roster': roster, 'anchors': anchors, 'interval_segments': {s['id']: s for s in segments},
        'measurement_records': {str(n): r for n, r in entries.items()}, 'interval_origins': interval_origins,
        'measurement_origins': measurement_origins, 'source_ledger': {'inputevents': input_ledger, 'chartevents_candidates': chart_ledger}}


def pending_batch(selection, anchor_id):
    """Internal export from an in-memory run() result; never load edited selection JSON as source evidence."""
    summary = selection['summary']; request = summary['context']['request']; context_id = summary['context_id']
    anchor = next((a for a in selection['anchors'] if a['id'] == anchor_id), None)
    ei.require(anchor is not None, 'UNKNOWN_ANCHOR')
    ei.require(anchor['status'] != 'BLOCKED_WINDOW' and anchor['reference_agreement'], 'BLOCKED_ANCHOR_BATCH')
    manifests = {f['table']: f for f in summary['context']['source_files']}
    segment = selection['interval_segments'][anchor_id]; patient = anchor['patient_id']; scope = {'patient_id': patient, 'episode_id': anchor['episode_id']}
    dataset = request['dataset_id']; batch_id = cr.digest([context_id, anchor_id, anchor['measurement_record_numbers']])
    semantic_policy = {'profile':'joint-semantic-policy-1.0','id':'source_item_codes',
        'classes':[request['query']['treatment_class_iri']], 'rules':[], 'disjoint':[]}
    def store(profile, claims, label, source_key, kind):
        return {'profile':profile,'dataset_id':'dataset_'+cr.digest(dataset),'snapshot_id':kind+'_'+batch_id,
            'clocks':[{'clock_id':'patient_'+patient,'scope':'patient:'+patient,'origin':label.replace(' ','T'),
                       'origin_source_key':source_key,'policy':local.POLICY}], 'claims':claims}
    origin = selection['interval_origins'][patient]
    interval_store = store(intervals.RECORD_STORE_PROFILE,[inputs.describe(segment,dataset)],origin['label'],origin['source_key'],'interval')
    intervals.validate_store(interval_store)
    interval_policy = {'profile':cp.POLICY_PROFILE,'id':'pending_'+cr.digest(interval_store),'store_sha256':cr.digest(interval_store),
                       'semantic_policy_sha256':cr.digest(semantic_policy),'decisions':[]}
    cp.validate_policy(interval_store,interval_policy,semantic_policy)
    measurement_store = measurement_policy = alignment = None; evidence = {}
    for number in anchor['measurement_record_numbers']:
        raw = selection['measurement_records'][str(number)]
        rid = 'chartevents:'+dataset+':'+manifests['chartevents']['csv_sha256']+':'+str(number)
        evidence[number] = {'record_id':rid,'raw':raw,'source':source.evidence('chartevents',number,raw,manifests['chartevents'])}
    if evidence:
        origin = selection['measurement_origins'][patient]
        origin_key = 'chartevents:'+dataset+':'+manifests['chartevents']['csv_sha256']+':'+str(origin['record_number'])
        measurement_store = store(points.PROFILE,[measurements.describe(e,dataset) for e in evidence.values()],origin['label'],origin_key,'measurement')
        measurement_policy = points.pending_policy(measurement_store)
        # An undecided template: source-selection assumptions are not silently turned into query alignment approval.
        alignment = {'profile':'patient-local-clock-alignment-1.0','id':'alignment_'+batch_id,
            'interval_store_sha256':cr.digest(interval_store),'measurement_store_sha256':cr.digest(measurement_store),'bindings':[]}
    isolation = {'interval':claim_isolation.check(cr.encode(interval_store,fields=cr.LOCAL_FIELDS),local=True),
        'measurement':claim_isolation.check(cr.encode(measurement_store,fields=cr.MEASUREMENT_FIELDS),local=True,measurement=True) if measurement_store else None}
    return {'profile':'pending-window-batch-1.0','status':'PENDING_REVIEW','selection_context_id':context_id,'batch_id':batch_id,
        'selection_context':deepcopy(summary['context']),
        'anchor_id':anchor_id, **scope, 'interval_store':interval_store,'interval_policy':interval_policy,
        'measurement_store':measurement_store,'measurement_policy':measurement_policy,'semantic_policy':semantic_policy,
        'alignment_template':alignment,'query':deepcopy(request['query']), 'isolation':isolation,
        'source_evidence':{'anchor':segment['source'], 'item':segment['item_source'], 'stay':segment['stay_source'],
            'interval_origin':selection['interval_origins'][patient],
            'measurement_origin':selection['measurement_origins'].get(patient),
            'measurements':[e['source'] for e in evidence.values()]},
        'accepted_claims':0,'clinical_mapping_verified':False,'cohort_membership':None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,default=ei.ROOT/'examples/source-mixed-query')
    parser.add_argument('--request',type=Path,default=ei.ROOT/'examples/indexed-source-windows/synthetic-request.json')
    parser.add_argument('--output',type=Path,default=ei.ROOT/'verification/indexed-source-windows-run/result.json')
    group=parser.add_mutually_exclusive_group();group.add_argument('--aggregate-only',action='store_true');group.add_argument('--anchor-id')
    args=parser.parse_args(argv); temporary=None
    try:
        protected={p.resolve() for p in [*inputs.source_paths(args.input_dir).values(),*measurements.paths_for(args.input_dir).values(),args.request]}
        ei.require(args.output.resolve() not in protected,'OUTPUT_OVERWRITES_INPUT')
        ei.require(args.request.stat().st_size <= cr.MAX_BYTES,'REQUEST_SIZE_LIMIT')
        result=run(args.input_dir,json.loads(args.request.read_text()))
        output=pending_batch(result,args.anchor_id) if args.anchor_id else result['summary'] if args.aggregate_only else result
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=args.output.parent,delete=False) as handle:
            temporary=Path(handle.name);json.dump(output,handle,indent=2);handle.write('\n')
        temporary.replace(args.output)
    except (ValueError,OSError,EOFError,csv.Error,zlib.error,sqlite3.Error,RecursionError) as error:
        print(json.dumps({'status':'INVALID_OR_INCOMPLETE_SELECTION','reason':str(error)}),file=sys.stderr);return 2
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)
    print(json.dumps({k:result['summary'][k] for k in ('status','anchors','anchor_outcomes','max_window_records')}))
    return 0 if result['summary']['status']=='COMPLETED_WINDOW_SELECTION' else 2


if __name__=='__main__':raise SystemExit(main())
