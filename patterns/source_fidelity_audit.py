"""Independent CSV-to-claim fidelity audit; no acceptance decisions or temporal execution."""
from collections import Counter
from datetime import datetime
from decimal import Decimal
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
from . import unique_claim_review as u

p = u.p
PROFILE = 'source-fidelity-audit-1.0'
FILES = tuple(sorted(set(u.FILES + ('patterns/source_fidelity_audit.py',))))
MAX_RAW_BYTES = 128 * 1024 * 1024
MAX_CSV_BYTES = 512 * 1024 * 1024


def sha(payload): return hashlib.sha256(payload).hexdigest()


def row_evidence(table, number, row, manifest):
    payload = json.dumps(row, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return {'table': table, 'record_number': number, 'file_sha256': manifest['file_sha256'],
            'csv_sha256': manifest['csv_sha256'], 'row_sha256': sha(payload)}


def read_original(path, table, wanted=None):
    """Independent bounded parser; count and hash the entire original file, retaining requested records."""
    with Path(path).open('rb') as handle: raw = handle.read(MAX_RAW_BYTES + 1)
    p.ei.require(len(raw) <= MAX_RAW_BYTES, 'AUDIT_RAW_SIZE_LIMIT')
    if Path(path).suffix == '.gz':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle: payload = handle.read(MAX_CSV_BYTES + 1)
    else: payload = raw
    p.ei.require(len(payload) <= MAX_CSV_BYTES, 'AUDIT_CSV_SIZE_LIMIT')
    reader = csv.reader(io.StringIO(payload.decode('utf-8'), newline=''), strict=True)
    header = next(reader, [])
    p.ei.require(header and len(header) == len(set(header)), 'AUDIT_INVALID_HEADER')
    rows = {}; total = 0
    for total, values in enumerate(reader, 1):
        p.ei.require(len(values) == len(header), 'AUDIT_MALFORMED_RECORD')
        if wanted is None or total in wanted: rows[total] = dict(zip(header, values))
    return rows, {'table': table, 'file_sha256': sha(raw), 'csv_sha256': sha(payload), 'rows': total, 'columns': header}


def expected_claim(raw, evidence, dataset):
    """Direct reference mapping from original CSV fields; does not call either importer/claim builder."""
    interval = evidence['table'] == 'inputevents'; number = str(evidence['record_number']); digest = evidence['csv_sha256']
    event_id = ('input_' if interval else 'chart_') + digest + '_' + number
    rid = 'row_' + digest + '_' + number if interval else event_id
    scope = {'patient_id': raw['subject_id'], 'episode_id': raw['stay_id']}
    source_key = evidence['table'] + ':' + dataset + ':' + digest + ':' + number
    clock = 'patient_' + raw['subject_id']
    if interval:
        variables = [{'id': event_id + '_' + side, **scope, 'clock_id': clock,
            'local_lower': raw[field], 'local_upper': raw[field],
            'source_key': source_key + ':' + side + ':recorded-label-exact'} for side, field in [('start','starttime'),('end','endtime')]]
        event = {'id': event_id, 'record_id': rid, **scope, 'event_kind': 'recorded_input_segment', 'status': 'recorded',
            'start_var': event_id + '_start', 'end_var': event_id + '_end', 'source_key': source_key}
        facts = [{'id': event_id + '_item', **scope, 'kind': 'class', 'subject': {'event_id': event_id},
            'class_iri': 'https://example.org/trajectory/mimic-record-item/' + raw['itemid']}]
    else:
        variables = [{'id': event_id + '_time', **scope, 'clock_id': clock,
            'local_lower': raw['charttime'], 'local_upper': raw['charttime'], 'source_key': source_key + ':charttime:recorded-point-label-exact'}]
        event = {'id': event_id, 'record_id': rid, **scope, 'event_kind': 'recorded_measurement', 'status': 'recorded',
            'time_var': event_id + '_time', 'value_lexical': raw['valuenum'], 'unit_lexical': raw['valueuom'],
            'item_id': raw['itemid'], 'source_key': source_key}
        facts = []
    return {'id': 'claim_' + event_id, **scope, 'source_id': evidence['table'] + '_' + p.cr.digest(dataset),
        'source_record_id': rid, 'source_sha256': evidence['file_sha256'],
        'bundle': {'variables': variables, 'events': [event], 'constraints': [], 'semantic_facts': facts}}


def audit(folder, package):
    """Package is a fresh internal prepare() result, never a supplied external package."""
    context = package['context']; dataset = context['plan_context']['selection_context']['request']['dataset_id']
    original_manifests = {f['table']: f for f in context['source_selection_summary']['context']['source_files']}
    wanted = {'inputevents': set(), 'chartevents': set()}
    for c in context['claims']: wanted[c['source_evidence']['table']].add(c['source_evidence']['record_number'])
    clocks = {p.cr.digest(c['clock']): c['clock'] for c in context['claims']}
    for clock in clocks.values():
        key = clock['origin_source_key']; table = key.split(':',1)[0]
        p.ei.require(table in wanted, 'AUDIT_UNKNOWN_ORIGIN_TABLE')
        wanted[table].add(int(key.rsplit(':',1)[1]))
    paths = {**p.windows.inputs.source_paths(folder), **p.windows.measurements.paths_for(folder)}
    originals = {}; manifests = {}
    for table, path in paths.items():
        originals[table], manifests[table] = read_original(path, table, wanted.get(table))
        p.ei.require(all(manifests[table][k] == original_manifests[table][k] for k in manifests[table]), 'AUDIT_SOURCE_CHANGED:' + table)
    items = {r['itemid']: (n,r) for n,r in originals['d_items'].items()}
    stays = {r['stay_id']: (n,r) for n,r in originals['icustays'].items()}
    rows = []
    for c in context['claims']:
        declared = c['source_evidence']; table = declared['table']; number = declared['record_number']
        raw = originals[table].get(number); p.ei.require(raw is not None, 'AUDIT_MISSING_RECORD')
        evidence = row_evidence(table, number, raw, manifests[table]); expected = expected_claim(raw, evidence, dataset)
        item = items.get(raw['itemid']); stay = stays.get(raw['stay_id'])
        checks = {'claim_content': c['claim'] == expected,
            'claim_identity_and_hash': c['claim_id'] == expected['id'] and c['claim_sha256'] == p.cr.digest(expected),
            'source_evidence': declared == evidence,
            'item_table': item is not None and item[1]['linksto'] == table,
            'patient_stay_identity': stay is not None and (stay[1]['subject_id'],stay[1]['hadm_id']) == (raw['subject_id'],raw['hadm_id'])}
        clock = c['clock']; key = clock['origin_source_key']; origin_table = key.split(':',1)[0]
        origin = originals[origin_table].get(int(key.rsplit(':',1)[1])); field = 'starttime' if origin_table == 'inputevents' else 'charttime'
        checks['origin_witness'] = bool(origin and origin['subject_id'] == raw['subject_id'] and
            clock['origin'] == origin[field].replace(' ','T') and key.startswith(origin_table + ':' + dataset + ':' + manifests[origin_table]['csv_sha256'] + ':') and
            clock['clock_id'] == 'patient_' + raw['subject_id'] and clock['scope'] == 'patient:' + raw['subject_id'] and clock['policy'] == p.windows.local.POLICY)
        if table == 'chartevents':
            checks['displayed_source_record'] = c['source_record'] == raw
            checks['unflagged_numeric_literal'] = raw['warning'] == '0' and Decimal(raw['value']).is_finite() and Decimal(raw['value']) == Decimal(raw['valuenum']) and bool(raw['valueuom'].strip())
        else:
            segment = c['source_record']
            checks['displayed_source_record'] = (segment['source'] == evidence and segment['item'] == item[1] and
                segment['item_source'] == row_evidence('d_items',item[0],item[1],manifests['d_items']) and
                segment['stay_source'] == row_evidence('icustays',stay[0],stay[1],manifests['icustays']) and
                segment['patient_id'] == raw['subject_id'] and segment['hospital_admission_id'] == raw['hadm_id'] and segment['icu_stay_id'] == raw['stay_id'] and
                segment['start']['raw_value'] == raw['starttime'] and segment['end']['raw_value'] == raw['endtime'] and
                segment['recorded_at']['raw_value'] == raw['storetime'] and
                all(segment[f] == raw[f] for f in ('orderid','linkorderid','statusdescription')) and
                segment['component_description'] == raw['ordercomponenttypedescription'] and
                all(segment[f] == {'raw_value': raw[f], 'raw_unit': raw[f+'uom']} for f in ('rate','amount')))
            checks['proper_recorded_interval'] = datetime.fromisoformat(raw['starttime']) < datetime.fromisoformat(raw['endtime'])
        rows.append({'claim_id': c['claim_id'], 'claim_sha256': c['claim_sha256'], 'checks': checks,
                     'status': 'VERIFIED_SOURCE_FIDELITY' if all(checks.values()) else 'FAILED_SOURCE_FIDELITY'})
    failures = sum(r['status'] != 'VERIFIED_SOURCE_FIDELITY' for r in rows)
    result_context = {'profile': PROFILE, 'package_context_id': package['context_id'], 'source_files': list(manifests.values()),
        'claims': rows, 'artifacts': {f: p.ei.digest((p.ei.ROOT/f).read_text()) for f in FILES}}
    return {'profile': PROFILE, 'context': result_context, 'context_id': p.cr.digest(result_context),
        'summary': {'claims_checked': len(rows), 'claims_verified': len(rows)-failures, 'claims_failed': failures,
            'check_counts': dict(sorted(Counter(k for r in rows for k in r['checks']).items())),
            'acceptance_decisions_created': 0, 'clinical_mapping_verified': False, 'calendar_compatibility_inferred': False},
        'status': 'VERIFIED_SOURCE_FIDELITY' if not failures else 'FAILED_SOURCE_FIDELITY'}
