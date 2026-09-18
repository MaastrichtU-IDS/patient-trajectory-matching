"""Derive bounded, pinned public-demo supplemental measurements outside the repo.

Admission checks source fidelity only; clinical interpretation remains pending.
No generated source rows or pack are suitable as committed evaluation summaries.
"""
import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import clinical_features as cf
from patterns import mimic_inputevents as inputs
from patterns import mimic_measurement_import as measurements
from patterns import clinical_source_preflight as scan
from patterns.reviewed_pressure_session import fingerprint

PIN = ROOT / 'data/clinical-source-demo-pin.json'
VARIABLES = (('heart_rate', '220045', 'Heart Rate', 'bpm'),
             ('respiratory_rate', '220210', 'Respiratory Rate', 'insp/min'))
VERSION = 'public-demo-distinct-stream-extraction-1.0'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def admitted_windows(segments):
    windows = defaultdict(list)
    count = 0
    for segment in segments:
        if segment['item']['itemid'] != '221906':
            continue
        start = datetime.fromisoformat(segment['start']['raw_value'])
        windows[(segment['patient_id'], segment['icu_stay_id'])].append(start)
        count += 1
    for values in windows.values():
        values.sort()
    return dict(windows), count


def within_window(row, windows):
    starts = windows.get((row['subject_id'], row['stay_id']), [])
    point = datetime.fromisoformat(row['charttime'])
    index = bisect_right(starts, point)
    return index < len(starts) and starts[index] <= point + timedelta(minutes=30)


def variables_from_dictionary(items):
    result = []
    for identity, item, label, unit in VARIABLES:
        require(item in items, 'Pinned dictionary is missing a selected item')
        row = items[item][1]
        require(row['label'] == label and row['unitname'] == unit and
                row['linksto'] == 'chartevents' and row['param_type'] == 'Numeric',
                'Pinned selected dictionary definition differs')
        result.append({'id': identity, 'label': row['label'], 'item_id': item,
                       'unit': row['unitname'], 'lookback_minutes': 30})
    return result


def prepare(source_dir, output_dir, *, pin_path=PIN):
    source_dir, requested_output = Path(source_dir).resolve(), Path(output_dir)
    require(not requested_output.is_symlink(), 'Output directory must not be a symlink')
    output_dir = requested_output.resolve()
    require(not output_dir.is_relative_to(ROOT), 'Generated patient rows must remain outside the repository')
    expected = json.loads(Path(pin_path).read_text())
    require(expected['dataset_id'] == 'mimic-iv-demo-2.2', 'Only the pinned public demo is supported')
    pins = {table: entry['file_sha256'] for table, entry in expected['files'].items()}
    require(fingerprint(source_dir) == pins, 'Public demo source pin differs')
    paths = measurements.paths_for(source_dir)
    # Reuse the exact treatment-segment admission used by the recorded service.
    input_path = next(p for p in (source_dir/'inputevents.csv', source_dir/'inputevents.csv.gz') if p.exists())
    staged = inputs.stage(input_path, paths['icustays'], paths['d_items'], dataset_id=expected['dataset_id'])
    windows, anchors = admitted_windows(staged['segments'])
    require(anchors > 0, 'No admitted norepinephrine segments')
    items = inputs.index_dimension(inputs.read_table(paths['d_items'], 'd_items')[0], 'd_items', 'itemid')
    stays = inputs.index_dimension(inputs.read_table(paths['icustays'], 'icustays')[0], 'icustays', 'stay_id')
    variables = variables_from_dictionary(items)
    selectors = {v['item_id']: v['unit'] for v in variables}
    selected, seen, counts, manifest = [], set(), Counter(), {}
    for number, raw in enumerate(scan.stream_chart(paths['chartevents'], manifest), 1):
        counts['source_rows_scanned'] += 1
        if raw['itemid'] not in selectors:
            continue
        counts['candidate_rows'] += 1
        row_hash = inputs.digest(inputs.canonical(raw))
        if row_hash in seen:
            counts['duplicate_rows'] += 1
            continue
        seen.add(row_hash)
        outcome, _ = measurements.admit(raw, stays, items)
        if outcome != 'ADMITTED_MEASUREMENT':
            counts[outcome.lower()] += 1
            continue
        if raw['valueuom'] != selectors[raw['itemid']]:
            counts['wrong_unit_rows'] += 1
            continue
        if not within_window(raw, windows):
            counts['outside_preindex_windows'] += 1
            continue
        require(cf.DECIMAL.fullmatch(raw['valuenum']) is not None, 'Selected numeric value exceeds technical decimal bound')
        selected.append({'event_id': f"chartevents:{pins['chartevents']}:{number}",
                         'patient_id': raw['subject_id'], 'episode_id': raw['stay_id'],
                         'item_id': raw['itemid'], 'unit': raw['valueuom'],
                         'time': datetime.fromisoformat(raw['charttime']).isoformat(), 'value': raw['valuenum']})
        counts['selected_' + raw['itemid']] += 1
        require(len(selected) <= cf.MAX_ROWS, 'Selected supplemental source exceeds 20000 rows; no cohort truncation is permitted')
    require(manifest['file_sha256'] == pins['chartevents'], 'Chart source changed during extraction')
    require(fingerprint(source_dir) == pins, 'Parent sources changed during extraction')
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=cf.FIELDS, lineterminator='\n')
    writer.writeheader()
    writer.writerows(selected)
    payload = buffer.getvalue().encode()
    require(len(payload) <= cf.MAX_BYTES, 'Supplemental source exceeds 4 MiB; no cohort truncation is permitted')
    tool_sha = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    dependencies = {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
                    ('patterns/mimic_inputevents.py', 'patterns/mimic_measurement_import.py',
                     'patterns/clinical_source_preflight.py', 'app/clinical_features.py')}
    provenance_sha = cf.digest({'version': VERSION, 'tool_sha256': tool_sha, 'dependencies': dependencies})
    pack = {'schema': cf.PACK_SCHEMA, 'csv_path': 'measurements.csv',
            'csv_sha256': hashlib.sha256(payload).hexdigest(), 'source_kind': 'RECORDED',
            'source_files': pins, 'clock': cf.CLOCK, 'variables': variables,
            'review': {'status': 'TECHNICAL_ACCEPTED', 'clinical_status': 'PENDING',
                       'reviewer': 'automated-pinned-demo-source-fidelity',
                       'rationale': f'{VERSION}; tool_sha256={tool_sha}; dependency_context={provenance_sha}. '
                       'Exact dictionary labels/units for 220045 bpm and 220210 insp/min. Existing source-row '
                       'admission, duplicate rejection, and same-patient/stay 30-minute strictly pre-index windows '
                       'of every admitted 221906 recorded segment. Event ID ends with original chartevents '
                       'one-based data-record number. Automated technical source fidelity only; no clinical approval.'}}
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    stat = output_dir.stat()
    require(stat.st_uid == os.getuid() and stat.st_mode & 0o077 == 0,
            'Output directory must be owned by the current user with private permissions')
    require(not (output_dir/'measurements.csv').exists() and not (output_dir/'pack.json').exists(),
            'Output files already exist; choose a fresh local directory')
    for name, raw in [('measurements.csv', payload), ('pack.json', (json.dumps(pack, indent=2)+'\n').encode())]:
        fd = os.open(output_dir/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
    cf.ClinicalFeaturePack(output_dir/'pack.json')
    return {'schema': 'public-demo-clinical-extraction-1', 'version': VERSION,
            'dataset_id': expected['dataset_id'], 'source_files': pins,
            'anchors': anchors, 'roster_stays': len(stays),
            'roster_patients': len({row[1]['subject_id'] for row in stays.values()}),
            'counts': dict(sorted(counts.items())), 'selected_rows': len(selected),
            'csv_sha256': pack['csv_sha256'], 'pack_sha256': hashlib.sha256((output_dir/'pack.json').read_bytes()).hexdigest(),
            'csv_bytes': len(payload), 'variables': variables, 'tool_sha256': tool_sha,
            'dependencies': dependencies, 'dependency_context': provenance_sha,
            'clinical_mapping_verified': False,
            'interpretation': 'Automated source-fidelity extraction only; clinical review remains pending.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mimic-dir', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.mimic_dir, args.output_dir), indent=2))


if __name__ == '__main__':
    main()
