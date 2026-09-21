"""Bounded chartevents scalar import with pending claims and complete row accounting."""
import argparse
from collections import Counter, defaultdict
import csv
import gzip
import io
import json
from pathlib import Path
import sys
import tempfile
import zlib
from jsonschema import Draft202012Validator

from . import claim_rdf as cr, claim_isolation, exact_intervals as ei
from . import measurement_claims as claims, mimic_inputevents as source, patient_local as local

PROFILE = 'mimic-measurement-import-1.0'
SCHEMA = ei.ROOT / 'schemas/mimic-measurement-import.schema.json'
HEADER = 'subject_id hadm_id stay_id caregiver_id charttime storetime itemid value valuenum valueuom warning'.split()
MAX_ROWS = 100_000
MAX_BYTES = 64 * 1024 * 1024
MAX_CLAIMS = 32
MAX_EPISODES = 256
FILES = tuple(sorted(set(claims.FILES + ('patterns/mimic_measurement_import.py',
    'patterns/mimic_inputevents.py', 'schemas/mimic-measurement-import.schema.json'))))
RESOURCE_ERRORS = {'CLAIM_DOCUMENT_LIMIT', 'CLAIM_STRING_LIMIT', 'CLAIM_TREE_LIMIT', 'CLAIM_GRAPH_LIMIT'}


def paths_for(folder):
    paths = {}
    for table in ('chartevents', 'icustays', 'd_items'):
        existing = [Path(folder) / (table + suffix) for suffix in ('.csv', '.csv.gz')
                    if (Path(folder) / (table + suffix)).is_file()]
        ei.require(len(existing) == 1, 'MISSING_OR_AMBIGUOUS_TABLE:' + table)
        paths[table] = existing[0]
    return paths


def read_observations(path):
    with Path(path).open('rb') as handle: raw = handle.read(MAX_BYTES + 1)
    ei.require(len(raw) <= MAX_BYTES, 'FILE_SIZE_LIMIT:chartevents')
    if Path(path).suffix == '.gz':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle: payload = handle.read(MAX_BYTES + 1)
    else: payload = raw
    ei.require(len(payload) <= MAX_BYTES, 'EXPANDED_SIZE_LIMIT:chartevents')
    reader = csv.reader(io.StringIO(payload.decode('utf-8'), newline=''), strict=True)
    header = next(reader, [])
    ei.require(len(header) == len(HEADER) and set(header) == set(HEADER), 'HEADER_MISMATCH:chartevents')
    rows = []
    for number, values in enumerate(reader, 1):
        ei.require(number <= MAX_ROWS, 'ROW_LIMIT:chartevents')
        ei.require(len(values) == len(header), 'MALFORMED_CSV_RECORD:chartevents:' + str(number))
        rows.append(dict(zip(header, values)))
    return rows, {'table': 'chartevents', 'file_sha256': source.digest(raw),
        'csv_sha256': source.digest(payload), 'columns': header, 'rows': len(rows)}


def admit(row, stays, items):
    invalid, unsupported = [], []
    for field in ('subject_id', 'hadm_id', 'stay_id', 'itemid'):
        if not source.positive_id(row[field]): invalid.append('INVALID_ID:' + field)
    if row['caregiver_id'] and not source.positive_id(row['caregiver_id']): invalid.append('INVALID_CAREGIVER_ID')
    stay, item = stays.get(row['stay_id']), items.get(row['itemid'])
    if stay is None: invalid.append('UNKNOWN_STAY')
    elif (row['subject_id'], row['hadm_id']) != (stay[1]['subject_id'], stay[1]['hadm_id']):
        invalid.append('STAY_IDENTITY_MISMATCH')
    if item is None: invalid.append('UNKNOWN_ITEM')
    elif item[1]['linksto'] != 'chartevents': invalid.append('ITEM_TABLE_MISMATCH')
    for field in ('charttime', 'storetime'):
        if field == 'storetime' and not row[field]: continue
        try: source.local_time(row[field])
        except ValueError: invalid.append('INVALID_LOCAL_TIMESTAMP:' + field)
    numeric = None
    if not row['valuenum']: unsupported.append('MISSING_NUMERIC_VALUE')
    else:
        try: numeric = claims.decimal_value(row['valuenum'])
        except ei.ContractError: invalid.append('INVALID_NUMERIC_VALUE')
    if not row['value']: unsupported.append('MISSING_VALUE_TEXT')
    else:
        try: textual = claims.decimal_value(row['value'])
        except ei.ContractError: unsupported.append('TEXTUAL_OR_QUALIFIED_VALUE')
        else:
            if numeric is not None and textual != numeric: invalid.append('VALUE_NUMERIC_DISAGREEMENT')
    if not row['valueuom'].strip(): unsupported.append('MISSING_UNIT')
    elif row['valueuom'].strip() != row['valueuom'] or len(row['valueuom']) > 64:
        unsupported.append('UNSUPPORTED_UNIT_LEXICAL')
    if row['warning'] == '1': unsupported.append('SOURCE_WARNING_FLAGGED')
    elif row['warning'] == '': unsupported.append('SOURCE_WARNING_UNKNOWN')
    elif row['warning'] != '0': invalid.append('INVALID_WARNING_FLAG')
    return ('INVALID_ROW' if invalid else 'UNSUPPORTED_ROW' if unsupported else 'ADMITTED_MEASUREMENT',
            invalid + unsupported)


def describe(entry, dataset_id):
    row, evidence = entry['raw'], entry['source']
    rid = 'chart_' + evidence['csv_sha256'] + '_' + str(evidence['record_number'])
    scope = {'patient_id': row['subject_id'], 'episode_id': row['stay_id']}
    return {'id': 'claim_' + rid, **scope, 'source_id': 'chartevents_' + cr.digest(dataset_id),
        'source_record_id': rid, 'source_sha256': evidence['file_sha256'], 'bundle': {
            'variables': [{'id': rid + '_time', **scope, 'clock_id': 'patient_' + row['subject_id'],
                'local_lower': row['charttime'], 'local_upper': row['charttime'],
                'source_key': entry['record_id'] + ':charttime:recorded-point-label-exact'}],
            'events': [{'id': rid, 'record_id': rid, **scope, 'event_kind': 'recorded_measurement',
                'status': 'recorded', 'time_var': rid + '_time', 'value_lexical': row['valuenum'],
                'unit_lexical': row['valueuom'], 'item_id': row['itemid'], 'source_key': entry['record_id']}],
            'constraints': [], 'semantic_facts': []}}


def run(folder, request):
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(request)),
               'INVALID_MEASUREMENT_IMPORT_REQUEST')
    request = json.loads(cr.canonical(request)); paths = paths_for(folder)
    rows, chart_manifest = read_observations(paths['chartevents'])
    dimensions = {t: source.read_table(paths[t], t) for t in ('icustays', 'd_items')}
    manifests = {'chartevents': chart_manifest, **{t: r[1] for t, r in dimensions.items()}}
    stays = source.index_dimension(dimensions['icustays'][0], 'icustays', 'stay_id')
    items = source.index_dimension(dimensions['d_items'][0], 'd_items', 'itemid')
    admissions = {}
    for _, stay in stays.values():
        ei.require(admissions.setdefault(stay['hadm_id'], stay['subject_id']) == stay['subject_id'],
                   'CONFLICTING_ADMISSION_PATIENT')
    selected_items = set(request['itemids'])
    ei.require(all(i in items and items[i][1]['linksto'] == 'chartevents' for i in selected_items),
               'UNKNOWN_CHART_ITEM_SELECTOR')
    all_patients = {s['subject_id'] for _, s in stays.values()}
    patients = all_patients if request['patient_ids'] == 'all' else set(request['patient_ids'])
    ei.require(patients <= all_patients, 'UNKNOWN_REQUESTED_PATIENT')
    roster = sorted((s for _, s in stays.values() if s['subject_id'] in patients),
                    key=lambda s: (s['subject_id'], s['stay_id']))
    ei.require(len(roster) <= MAX_EPISODES, 'EPISODE_LIMIT')
    context = {'profile': PROFILE, 'request': request, 'source_files': list(manifests.values()),
        'dataset_namespace': 'dataset_' + cr.digest(request['dataset_id']),
        'origin_policy': 'minimum_admitted_charttime_per_patient_before_item_filter',
        'acceptance': 'no_decisions_all_claims_pending', 'time_semantics': 'recorded_point_labels',
        'numeric_policy': 'exact_exported_decimal_lexical_no_unit_conversion',
        'warning_policy': 'explicit_zero_only', 'limits': {'claims_per_stay': MAX_CLAIMS,
            'icu_stays': MAX_EPISODES, 'chartevents_rows': MAX_ROWS, 'file_bytes': MAX_BYTES},
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    context_id = cr.digest(context)
    ledger, seen, origins, groups = [], {}, {}, defaultdict(list)
    for number, row in enumerate(rows, 1):
        evidence = source.evidence('chartevents', number, row, chart_manifest)
        record_id = 'chartevents:' + request['dataset_id'] + ':' + chart_manifest['csv_sha256'] + ':' + str(number)
        entry = {'record_id': record_id, 'raw': row, 'source': evidence, 'duplicate_of': None,
                 'claim_id': None, 'store_sha256': None, 'import_reason': None}
        prior = seen.setdefault(evidence['row_sha256'], record_id)
        if prior != record_id:
            entry.update(admission_outcome='DUPLICATE_ROW', reasons=['EXACT_DUPLICATE_SOURCE_ROW'], duplicate_of=prior)
        else:
            outcome, reasons = admit(row, stays, items); entry.update(admission_outcome=outcome, reasons=reasons)
        if entry['admission_outcome'] != 'ADMITTED_MEASUREMENT': entry['import_outcome'] = 'NOT_ADMITTED'
        elif row['subject_id'] not in patients: entry['import_outcome'] = 'OUTSIDE_PATIENT_SCOPE'
        else:
            patient = row['subject_id']; old = origins.get(patient)
            if old is None or (row['charttime'], record_id) < (old['label'], old['source_record_id']):
                origins[patient] = {'label': row['charttime'], 'source_record_id': record_id, 'source': evidence}
            if row['itemid'] not in selected_items: entry['import_outcome'] = 'OUTSIDE_ITEM_SCOPE'
            else: groups[(patient, row['stay_id'])].append(entry)
        ledger.append(entry)
    episodes = []
    for stay in roster:
        patient, episode_id = stay['subject_id'], stay['stay_id']; entries = groups[(patient, episode_id)]
        episode = {'patient_id': patient, 'episode_id': episode_id, 'selected_records': len(entries),
            'status': 'NO_SELECTED_ADMITTED_RECORDS', 'reason': None, 'store': None, 'store_sha256': None,
            'acceptance_policy': None, 'isolation': None, 'claim_provenance': {}}
        if entries:
            if len(entries) > MAX_CLAIMS:
                episode.update(status='BLOCKED_RESOURCE_LIMIT', reason='CLAIMS_PER_STAY_LIMIT')
            else:
                origin = origins[patient]
                store = {'profile': claims.PROFILE, 'dataset_id': context['dataset_namespace'],
                    'snapshot_id': 'import_' + cr.digest([context_id, patient, episode_id]),
                    'clocks': [{'clock_id': 'patient_' + patient, 'scope': 'patient:' + patient,
                        'origin': origin['label'].replace(' ', 'T'), 'policy': local.POLICY,
                        'origin_source_key': origin['source_record_id']}],
                    'claims': [describe(e, request['dataset_id']) for e in entries]}
                try:
                    policy = claims.pending_policy(store)
                    certificate = claim_isolation.check(cr.encode(store, fields=cr.MEASUREMENT_FIELDS),
                                                        local=True, measurement=True)
                except ei.ContractError as error:
                    if str(error) not in RESOURCE_ERRORS: raise
                    episode.update(status='BLOCKED_RESOURCE_LIMIT', reason=str(error))
                else:
                    store_hash = cr.digest(store)
                    episode.update(status='PENDING_MEASUREMENT_CLAIMS', store=store, store_sha256=store_hash,
                                   acceptance_policy=policy, isolation=certificate)
                    for claim, entry in zip(store['claims'], entries):
                        row = entry['raw']; entry.update(import_outcome='PENDING_CLAIM', claim_id=claim['id'], store_sha256=store_hash)
                        episode['claim_provenance'][claim['id']] = {'claim_sha256': cr.digest(claim),
                            'source_record_id': entry['record_id'], 'source': entry['source'],
                            'item_source': source.evidence('d_items', *items[row['itemid']], manifests['d_items']),
                            'stay_source': source.evidence('icustays', *stays[row['stay_id']], manifests['icustays']),
                            'origin_source': origin['source'], 'origin_source_record_id': origin['source_record_id'],
                            'recorded_at': source.timestamp_envelope(row['storetime'], 'recording_or_validation'),
                            'source_available_at': None}
            if episode['status'] == 'BLOCKED_RESOURCE_LIMIT':
                for entry in entries: entry.update(import_outcome='BLOCKED_RESOURCE_LIMIT', import_reason=episode['reason'])
        episodes.append(episode)
    counts = dict(sorted(Counter(e['import_outcome'] for e in ledger).items()))
    blocked = sum(e['status'] == 'BLOCKED_RESOURCE_LIMIT' for e in episodes)
    summary = {'profile': PROFILE, 'context': context, 'context_id': context_id,
        'status': 'BLOCKED_INCOMPLETE_IMPORT' if blocked else 'COMPLETED_PENDING_IMPORT',
        'input_rows': len(rows), 'reconciliation_complete': len(ledger) == len(rows),
        'import_outcome_counts': counts, 'admission_outcome_counts': dict(sorted(Counter(e['admission_outcome'] for e in ledger).items())),
        'selected_records': sum(len(g) for g in groups.values()), 'described_claims': counts.get('PENDING_CLAIM', 0),
        'blocked_stays': blocked, 'description_complete_over_selected_records': blocked == 0,
        'represented_patients': len(patients), 'represented_icu_stays': len(roster),
        'accepted_claims': 0, 'matching': None, 'clinical_mapping_verified': False,
        'clinical_knowledge_status': 'UNKNOWN', 'source_history_verified': False, 'physical_elapsed_time_verified': False}
    return {'summary': summary, 'episodes': episodes, 'origins': origins, 'reconciliation': ledger}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ei.ROOT / 'examples/mimic-measurement')
    parser.add_argument('--request', type=Path, default=ei.ROOT / 'examples/mimic-measurement/request.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/mimic-measurement-run/result.json')
    args = parser.parse_args(argv); temporary = None
    try:
        protected = {p.resolve() for p in paths_for(args.input_dir).values()} | {args.request.resolve()}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        ei.require(args.request.stat().st_size <= cr.MAX_BYTES, 'REQUEST_SIZE_LIMIT')
        result = run(args.input_dir, json.loads(args.request.read_text()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, RecursionError) as error:
        print(json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}), file=sys.stderr); return 2
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({k: v for k, v in result['summary'].items() if k != 'context'}, sort_keys=True))
    return 0 if result['summary']['status'] == 'COMPLETED_PENDING_IMPORT' else 2


if __name__ == '__main__':
    raise SystemExit(main())
