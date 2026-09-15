"""Aggregate-only source coverage and capacity scan; no claim acceptance or trajectory query."""
import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
import gzip
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import zlib
from jsonschema import Draft202012Validator
from . import claim_rdf as cr, exact_intervals as ei, mimic_inputevents as source
from . import mimic_claim_import as intervals, mimic_measurement_import as measurements
from . import mixed_record_query as mixed

PROFILE = 'clinical-source-preflight-1.0'
SCHEMA = ei.ROOT / 'schemas/clinical-source-preflight.schema.json'
MAX_RAW_BYTES = 128 * 1024 * 1024
MAX_EXPANDED_BYTES = 512 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_CHART_ROWS = 2_000_000
MAX_SELECTED_ROWS = 100_000
FILES = tuple(sorted(set(intervals.FILES + measurements.FILES + mixed.FILES + (
    'patterns/clinical_source_preflight.py', 'schemas/clinical-source-preflight.schema.json'))))


def file_digest(path):
    digest = hashlib.sha256(); size = 0
    with path.open('rb') as handle:
        while chunk := handle.read(1024 * 1024):
            size += len(chunk); ei.require(size <= MAX_RAW_BYTES, 'PREFLIGHT_RAW_BYTE_LIMIT')
            digest.update(chunk)
    return digest.hexdigest(), size


def stream_chart(path, manifest):
    """Stream complete CSV including multiline records; publish a manifest only after EOF and rehash."""
    before, raw_bytes = file_digest(path)
    expanded = hashlib.sha256(); size = 0
    def lines(handle):
        nonlocal size
        while line := handle.readline(MAX_LINE_BYTES + 1):
            ei.require(len(line) <= MAX_LINE_BYTES, 'PREFLIGHT_LINE_LIMIT')
            size += len(line); ei.require(size <= MAX_EXPANDED_BYTES, 'PREFLIGHT_EXPANDED_BYTE_LIMIT')
            expanded.update(line)
            yield line.decode('utf-8')
    opener = gzip.open if path.suffix == '.gz' else open
    count = 0
    with opener(path, 'rb') as handle:
        reader = csv.reader(lines(handle), strict=True); header = next(reader, [])
        ei.require(len(header) == len(measurements.HEADER) and set(header) == set(measurements.HEADER), 'HEADER_MISMATCH:chartevents')
        for count, values in enumerate(reader, 1):
            ei.require(count <= MAX_CHART_ROWS, 'PREFLIGHT_CHART_ROW_LIMIT')
            ei.require(len(values) == len(header), 'MALFORMED_CSV_RECORD:chartevents')
            yield dict(zip(header, values))
    ei.require(file_digest(path) == (before, raw_bytes), 'SOURCE_CHANGED_DURING_PREFLIGHT')
    manifest.update(table='chartevents', file_sha256=before, csv_sha256=expanded.hexdigest(),
                    raw_bytes=raw_bytes, expanded_bytes=size, columns=header, rows=count)


def counts(counter):
    return dict(sorted(counter.items()))


def item_summary(item):
    return {'itemid': item['itemid'], 'label': item['label'], 'linksto': item['linksto'],
            'dictionary_unit': item['unitname'], 'rows': 0, 'admission_outcomes': Counter(),
            'reason_counts': Counter(), 'unit_buckets': Counter(), 'warning_buckets': Counter()}


def run(folder, request):
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(request)), 'INVALID_PREFLIGHT_REQUEST')
    request = deepcopy(request)
    paths = {**intervals.source_paths(folder), **measurements.paths_for(folder)}
    staged = source.stage(**{k: paths[k] for k in source.HEADERS}, dataset_id=request['dataset_id'])
    manifests = {f['table']: f for f in staged['summary']['context']['files']}
    dimensions = {}
    for table in ('icustays', 'd_items'):
        rows, manifest = source.read_table(paths[table], table)
        ei.require(manifest == manifests[table], 'SHARED_SOURCE_CHANGED:' + table)
        dimensions[table] = rows
    stays = source.index_dimension(dimensions['icustays'], 'icustays', 'stay_id')
    items = source.index_dimension(dimensions['d_items'], 'd_items', 'itemid')
    ei.require(len(stays) <= intervals.MAX_EPISODES, 'PREFLIGHT_STAY_LIMIT')
    selected_i, selected_m = set(request['treatment_itemids']), set(request['measurement_itemids'])
    for selected, table in ((selected_i, 'inputevents'), (selected_m, 'chartevents')):
        ei.require(all(i in items and items[i][1]['linksto'] == table for i in selected), 'UNKNOWN_OR_WRONG_TABLE_ITEM:' + table)
    summaries = {i: item_summary(items[i][1]) for i in sorted(selected_i | selected_m)}
    interval_stays, measurement_stays = Counter(), Counter()
    per_item_stays = {i: Counter() for i in summaries}
    input_outcomes = Counter()
    for entry in staged['reconciliation']:
        raw = entry['raw']; item = raw['itemid']
        if item not in selected_i:
            input_outcomes['OUTSIDE_ITEM_SCOPE'] += 1; continue
        outcome = entry['outcome']; input_outcomes[outcome] += 1
        s = summaries[item]; s['rows'] += 1; s['admission_outcomes'][outcome] += 1; s['reason_counts'].update(entry['reasons'])
        if outcome == 'STAGED_SEGMENT':
            interval_stays[raw['stay_id']] += 1; per_item_stays[item][raw['stay_id']] += 1
    chart_outcomes, seen = Counter(), set(); manifest = {}; selected_count = 0
    for raw in stream_chart(paths['chartevents'], manifest):
        item = raw['itemid']
        if item not in selected_m:
            chart_outcomes['OUTSIDE_ITEM_SCOPE'] += 1; continue
        selected_count += 1
        ei.require(selected_count <= MAX_SELECTED_ROWS, 'PREFLIGHT_SELECTED_ROW_LIMIT')
        s = summaries[item]; s['rows'] += 1
        s['unit_buckets'][raw['valueuom'] if raw['valueuom'] in ('mmHg', '') else 'OTHER'] += 1
        s['warning_buckets'][raw['warning'] if raw['warning'] in ('0', '1', '') else 'OTHER'] += 1
        row_hash = source.digest(source.canonical(raw))
        if row_hash in seen: outcome, reasons = 'DUPLICATE_ROW', ['EXACT_DUPLICATE_SOURCE_ROW']
        else:
            seen.add(row_hash); outcome, reasons = measurements.admit(raw, stays, items)
        chart_outcomes[outcome] += 1; s['admission_outcomes'][outcome] += 1; s['reason_counts'].update(reasons)
        if outcome == 'ADMITTED_MEASUREMENT':
            measurement_stays[raw['stay_id']] += 1; per_item_stays[item][raw['stay_id']] += 1
    manifests['chartevents'] = manifest
    screen = Counter(); two_sided = Counter({'stays_with_both_admitted_record_types': 0,
        'within_conservative_count_bounds': 0, 'exceed_conservative_count_bounds': 0})
    for stay in stays:
        i, m = interval_stays[stay], measurement_stays[stay]
        reasons = []
        if i > intervals.MAX_CLAIMS: reasons.append('INTERVAL_CLAIM_LIMIT')
        if i > 30: reasons.append('INTERVAL_EVENT_LIMIT')
        if m > measurements.MAX_CLAIMS: reasons.append('MEASUREMENT_CLAIM_LIMIT')
        if 2*i + m > mixed.MAX_VARIABLES: reasons.append('MIXED_VARIABLE_LIMIT')
        if i*m > mixed.MAX_BASELINE_TESTS: reasons.append('BASELINE_UPPER_BOUND')
        if i*m*max(0,m-1) > mixed.MAX_FOLLOWUP_TESTS: reasons.append('FOLLOWUP_UPPER_BOUND')
        screen.update(reasons)
        if i and m:
            two_sided['stays_with_both_admitted_record_types'] += 1
            two_sided['exceed_conservative_count_bounds' if reasons else 'within_conservative_count_bounds'] += 1
    # File-level checks precede item filtering in the existing importers.
    blockers = []
    if manifest['rows'] > measurements.MAX_ROWS: blockers.append('CHARTEVENTS_IMPORT_ROW_LIMIT')
    if manifest['raw_bytes'] > measurements.MAX_BYTES: blockers.append('CHARTEVENTS_IMPORT_RAW_BYTE_LIMIT')
    if manifest['expanded_bytes'] > measurements.MAX_BYTES: blockers.append('CHARTEVENTS_IMPORT_EXPANDED_BYTE_LIMIT')
    for item, s in summaries.items():
        s.update(admitted_stays=len(per_item_stays[item]), max_admitted_records_per_stay=max(per_item_stays[item].values(), default=0))
        for field in ('admission_outcomes', 'reason_counts', 'unit_buckets', 'warning_buckets'): s[field] = counts(s[field])
    context = {'profile': PROFILE, 'request': request, 'source_files': list(manifests.values()),
        'interval_admission_context_id': staged['summary']['context_id'],
        'measurement_admission_scope': 'requested_item_codes_only_other_rows_counted_as_outside_scope',
        'limits': {'raw_bytes': MAX_RAW_BYTES, 'expanded_bytes': MAX_EXPANDED_BYTES, 'line_bytes': MAX_LINE_BYTES,
                   'chart_rows': MAX_CHART_ROWS, 'selected_chart_rows': MAX_SELECTED_ROWS, 'stays': intervals.MAX_EPISODES},
        'downstream_count_limits': {'interval_claims': intervals.MAX_CLAIMS, 'interval_events': 30,
            'measurement_claims': measurements.MAX_CLAIMS, 'variables': mixed.MAX_VARIABLES,
            'baseline_tests': mixed.MAX_BASELINE_TESTS, 'followup_tests': mixed.MAX_FOLLOWUP_TESTS},
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    ei.require(sum(chart_outcomes.values()) == manifest['rows'] and sum(input_outcomes.values()) == staged['summary']['input_rows'],
               'PREFLIGHT_ACCOUNTING_FAILED')
    return {'profile': PROFILE, 'status': 'COMPLETED_SOURCE_PREFLIGHT', 'context': context, 'context_id': cr.digest(context),
        'full_source_scan_complete': True, 'row_accounting_complete': True,
        'input_rows': {'inputevents': staged['summary']['input_rows'], 'chartevents': manifest['rows']},
        'row_outcomes': {'inputevents': counts(input_outcomes), 'chartevents': counts(chart_outcomes)},
        'items': list(summaries.values()), 'roster': {'patients': len({s['subject_id'] for s in dimensions['icustays']}),
            'stays': len(stays), 'stays_with_admitted_treatment': len(interval_stays),
            'stays_with_admitted_measurement': len(measurement_stays),
            'stays_without_either_admitted_type': len(set(stays) - set(interval_stays) - set(measurement_stays))},
        'capacity_screen': {'direct_chartevents_file_within_import_limits': not blockers,
            'direct_import_blockers': blockers, 'stay_count_limit_reasons': counts(screen), **counts(two_sided),
            'screen_is_sufficient_count_condition_only': True, 'query_readiness_established': False},
        'patient_rows_or_identifiers_included': False, 'claims_created': 0, 'accepted_claims': 0,
        'clinical_mapping_verified': False, 'clock_alignment_declared': False,
        'mixed_query_executed': False, 'cohort_membership': None, 'full_mimic_analyzed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=ei.ROOT / 'examples/source-mixed-query')
    parser.add_argument('--request', type=Path, default=ei.ROOT / 'examples/clinical-source-preflight/synthetic-request.json')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/clinical-source-preflight-run/result.json')
    args = parser.parse_args(argv); temporary = None
    try:
        protected = {p.resolve() for p in [*intervals.source_paths(args.input_dir).values(),
            *measurements.paths_for(args.input_dir).values(), args.request]}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        ei.require(args.request.stat().st_size <= cr.MAX_BYTES, 'REQUEST_SIZE_LIMIT')
        result = run(args.input_dir, json.loads(args.request.read_text()))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, RecursionError) as error:
        print(json.dumps({'status':'INVALID_OR_INCOMPLETE_PREFLIGHT','reason':str(error)}), file=sys.stderr); return 2
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status':result['status'],'input_rows':result['input_rows'],'capacity_screen':result['capacity_screen']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
