"""Bounded MIMIC-IV demo 2.2 CSV admission; no clinical or replay export."""
import argparse
from collections import Counter
import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import sys
import zlib

PROFILE = 'mimic-inputevents-staging-1.0'
DATASET = 'mimic-iv-demo-2.2'
ROOT = Path(__file__).resolve().parents[1]
HEADERS = {
    'inputevents': 'subject_id hadm_id stay_id caregiver_id starttime endtime storetime itemid amount amountuom rate rateuom orderid linkorderid ordercategoryname secondaryordercategoryname ordercomponenttypedescription ordercategorydescription patientweight totalamount totalamountuom isopenbag continueinnextdept statusdescription originalamount originalrate'.split(),
    'icustays': 'subject_id hadm_id stay_id first_careunit last_careunit intime outtime los'.split(),
    'd_items': 'itemid label abbreviation linksto category unitname param_type lownormalvalue highnormalvalue'.split(),
}
MAX_BYTES = 64 * 1024 * 1024  # Each compressed AND decompressed input.
MAX_ROWS = 100_000            # Each table; this is deliberately not a full-release loader.
STATUSES = {'FinishedRunning', 'ChangeDose/Rate', 'Paused', 'Stopped'}
CATEGORIES = {'Continuous IV', 'Continuous Med'}
OUTCOMES = ('STAGED_SEGMENT', 'UNSUPPORTED_ROW', 'INVALID_ROW', 'DUPLICATE_ROW')
BLOCKERS = ['PATIENT_LOCAL_CLOCK_BRIDGE_REQUIRED', 'CLINICAL_MAPPING_REVIEW_REQUIRED',
            'OCCURRENCE_PRECISION_POLICY_REQUIRED', 'SOURCE_AVAILABILITY_UNVERIFIED',
            'SOURCE_REVISION_HISTORY_UNAVAILABLE']


class AdmissionError(ValueError):
    """File/dimension failure: no complete row-reconciliation claim is possible."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def digest(value):
    return hashlib.sha256(value).hexdigest()


def require(condition, code):
    if not condition:
        raise AdmissionError(code)


def positive_id(value):
    return bool(re.fullmatch(r'[1-9][0-9]*', value))


def local_time(value):
    # Neither a timezone, a fractional precision nor a missing time is invented.
    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}', value):
        raise ValueError('not a pinned timestamp spelling')
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S')


def read_table(path, table):
    path = Path(path)
    with path.open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, 'FILE_SIZE_LIMIT:' + table)
    if path.suffix == '.gz':
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as handle:
            payload = handle.read(MAX_BYTES + 1)
    else:
        payload = raw
    require(len(payload) <= MAX_BYTES, 'EXPANDED_SIZE_LIMIT:' + table)
    reader = csv.reader(io.StringIO(payload.decode('utf-8'), newline=''), strict=True)
    header = next(reader, [])
    require(len(header) == len(HEADERS[table]) and set(header) == set(HEADERS[table]),
            'HEADER_MISMATCH:' + table)
    rows = []
    for number, values in enumerate(reader, 1):
        require(number <= MAX_ROWS, 'ROW_LIMIT:' + table)
        require(len(values) == len(header), f'MALFORMED_CSV_RECORD:{table}:{number}')
        rows.append(dict(zip(header, values)))
    return rows, {'table': table, 'file_sha256': digest(raw),
                  'csv_sha256': digest(payload), 'columns': header, 'rows': len(rows)}


def index_dimension(rows, table, key):
    result = {}
    for number, row in enumerate(rows, 1):
        for field in (('subject_id', 'hadm_id', 'stay_id') if table == 'icustays' else ('itemid',)):
            require(positive_id(row[field]), f'INVALID_DIMENSION_ID:{table}:{number}:{field}')
        require(row[key] not in result, f'DUPLICATE_DIMENSION_ID:{table}:{row[key]}')
        result[row[key]] = (number, row)
    return result


def evidence(table, number, row, manifest):
    return {'table': table, 'record_number': number, 'file_sha256': manifest['file_sha256'],
            'csv_sha256': manifest['csv_sha256'], 'row_sha256': digest(canonical(row))}


def timestamp_envelope(raw, role):
    return {'raw_value': raw or None, 'local_datetime': raw.replace(' ', 'T') if raw else None,
            'source_datatype': 'TIMESTAMP(0)', 'semantic_kind': role,
            'timezone': None, 'calendar': 'proleptic-gregorian',
            'lexical_resolution': 'second', 'occurrence_precision': 'unverified',
            'normalization_status': 'UNRESOLVED' if raw else 'MISSING'}


def stage(inputevents, icustays, d_items, *, dataset_id=DATASET):
    require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', dataset_id)), 'INVALID_DATASET_ID')
    loaded = {name: read_table(path, name) for name, path in
              [('inputevents', inputevents), ('icustays', icustays), ('d_items', d_items)]}
    manifests = {name: data[1] for name, data in loaded.items()}
    stays = index_dimension(loaded['icustays'][0], 'icustays', 'stay_id')
    items = index_dimension(loaded['d_items'][0], 'd_items', 'itemid')
    # Also reject contradictory stay-to-admission patient assignments.
    admissions = {}
    for _, row in stays.values():
        previous = admissions.setdefault(row['hadm_id'], row['subject_id'])
        require(previous == row['subject_id'], 'CONFLICTING_ADMISSION_PATIENT')
    reconciliation, segments, seen = [], [], {}
    for number, row in enumerate(loaded['inputevents'][0], 1):
        source = evidence('inputevents', number, row, manifests['inputevents'])
        record_id = 'inputevents:' + dataset_id + ':' + manifests['inputevents']['csv_sha256'] + ':' + str(number)
        entry = {'record_id': record_id, 'source': source, 'raw': row, 'reasons': [],
                 'duplicate_of': None, 'segment_id': None}
        prior = seen.setdefault(source['row_sha256'], record_id)
        if prior != record_id:
            entry.update(outcome='DUPLICATE_ROW', reasons=['EXACT_DUPLICATE_SOURCE_ROW'], duplicate_of=prior)
            reconciliation.append(entry)
            continue
        invalid, unsupported = [], []
        for field in ('subject_id', 'hadm_id', 'stay_id', 'itemid', 'orderid', 'linkorderid'):
            if not positive_id(row[field]):
                invalid.append('INVALID_ID:' + field)
        stay = stays.get(row['stay_id'])
        if stay is None:
            invalid.append('UNKNOWN_STAY')
        elif (row['subject_id'], row['hadm_id']) != (stay[1]['subject_id'], stay[1]['hadm_id']):
            invalid.append('STAY_IDENTITY_MISMATCH')
        item = items.get(row['itemid'])
        if item is None:
            invalid.append('UNKNOWN_ITEM')
        elif item[1]['linksto'] != 'inputevents':
            invalid.append('ITEM_TABLE_MISMATCH')
        times = {}
        for field in ('starttime', 'endtime', 'storetime'):
            if field == 'storetime' and row[field] == '':
                continue
            try:
                times[field] = local_time(row[field])
            except ValueError:
                invalid.append('INVALID_LOCAL_TIMESTAMP:' + field)
        if 'starttime' in times and 'endtime' in times:
            seconds = int((times['endtime'] - times['starttime']).total_seconds())
            if seconds <= 0:
                invalid.append('NON_POSITIVE_RECORDED_INTERVAL')
            elif seconds == 60:
                unsupported.append('ONE_MINUTE_BOLUS_AMBIGUITY')
        if row['statusdescription'] not in STATUSES:
            unsupported.append('UNSUPPORTED_STATUS')
        if row['ordercategorydescription'] not in CATEGORIES:
            unsupported.append('UNSUPPORTED_ORDER_CATEGORY')
        for field in ('rate', 'amount'):
            if not row[field].strip() or not row[field + 'uom'].strip():
                unsupported.append('MISSING_QUANTITY_OR_UNIT:' + field)
                continue
            try:
                if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', row[field]):
                    raise InvalidOperation
                value = Decimal(row[field])
                if not value.is_finite():
                    raise InvalidOperation
                if value <= 0:
                    unsupported.append('NON_POSITIVE_QUANTITY:' + field)
            except InvalidOperation:
                invalid.append('INVALID_QUANTITY:' + field)
        entry['reasons'] = invalid + unsupported
        if invalid:
            entry['outcome'] = 'INVALID_ROW'
        elif unsupported:
            entry['outcome'] = 'UNSUPPORTED_ROW'
        else:
            entry.update(outcome='STAGED_SEGMENT', segment_id=record_id)
            clock = {'clock_id': 'mimic-local:' + digest(canonical([dataset_id, row['subject_id']])),
                     'dataset_id': dataset_id, 'patient_id': row['subject_id'],
                     'policy': 'mimic-shifted-patient-local-v1', 'timezone': None,
                     'origin': None, 'cross_patient_comparable': False}
            segments.append({'id': record_id, 'patient_id': row['subject_id'],
                'hospital_admission_id': row['hadm_id'], 'icu_stay_id': row['stay_id'],
                'kind': 'recorded_input_component_segment', 'clock': clock,
                'start': timestamp_envelope(row['starttime'], 'recorded_start'),
                'end': timestamp_envelope(row['endtime'], 'recorded_end'),
                'recorded_at': timestamp_envelope(row['storetime'], 'recording_or_validation'),
                'source_available_at': None, 'clinical_class': None,
                'mapping_status': 'UNREVIEWED', 'source': source,
                'stay_source': evidence('icustays', *stay, manifests['icustays']),
                'item_source': evidence('d_items', *item, manifests['d_items']),
                'item': dict(item[1]), 'orderid': row['orderid'], 'linkorderid': row['linkorderid'],
                'component_description': row['ordercomponenttypedescription'],
                'statusdescription': row['statusdescription'],
                'rate': {'raw_value': row['rate'], 'raw_unit': row['rateuom']},
                'amount': {'raw_value': row['amount'], 'raw_unit': row['amountuom']}})
        reconciliation.append(entry)
    outcome_counts = Counter(r['outcome'] for r in reconciliation)
    counts = {key: outcome_counts[key] for key in OUTCOMES}
    context = {'dataset_id': dataset_id, 'schema_version': 'MIMIC-IV-demo-2.2',
               'profile': PROFILE, 'files': list(manifests.values()),
               'implementation_sha256': digest(Path(__file__).read_bytes())}
    summary = {'profile': PROFILE, 'context_id': digest(canonical(context)), 'context': context,
               'status': 'STAGED_NOT_MATCHER_READY', 'matcher_ready': False,
               'matcher_blockers': BLOCKERS.copy(), 'input_rows': len(reconciliation),
               'outcome_counts': counts,
               'reason_counts': dict(sorted(Counter(reason for r in reconciliation for reason in r['reasons']).items())),
               'reconciliation_complete': sum(counts.values()) == len(loaded['inputevents'][0]),
               'staged_segments': len(segments), 'clinical_mapping_verified': False,
               'availability_verified': False, 'history_coverage': 'unavailable',
               'clinical_knowledge_status': 'UNKNOWN', 'matching': None}
    return {'summary': summary, 'reconciliation': reconciliation, 'segments': segments}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ROOT / 'examples/mimic-inputevents')
    parser.add_argument('--dataset-id', help='Required for supplied files; identifies their dataset/shift namespace')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/mimic-inputevents-run/result.json')
    args = parser.parse_args(argv)
    try:
        dataset_id = args.dataset_id
        if dataset_id is None and args.input_dir.resolve() == (ROOT / 'examples/mimic-inputevents').resolve():
            dataset_id = 'synthetic-mimic-demo-2.2'
        require(dataset_id is not None, 'DATASET_ID_REQUIRED')
        paths = []
        for table in HEADERS:
            choices = [args.input_dir / (table + suffix) for suffix in ('.csv', '.csv.gz')]
            existing = [p for p in choices if p.is_file()]
            require(len(existing) == 1, 'MISSING_OR_AMBIGUOUS_TABLE:' + table)
            paths.append(existing[0])
        require(args.output.resolve() not in {p.resolve() for p in paths}, 'OUTPUT_OVERWRITES_SOURCE')
        result = stage(*paths, dataset_id=dataset_id)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        # A single file avoids a partially replaced multi-file evidence bundle.
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        temporary.replace(args.output)
    except (AdmissionError, OSError, EOFError, UnicodeError, csv.Error, zlib.error) as exc:
        print(json.dumps({'status': 'INVALID_INPUT', 'error': str(exc), 'reconciliation_complete': False}), file=sys.stderr)
        return 2
    print(json.dumps(result['summary'], sort_keys=True))
    return 0  # Successful staging, explicitly NOT successful matching.


if __name__ == '__main__':
    sys.exit(main())
