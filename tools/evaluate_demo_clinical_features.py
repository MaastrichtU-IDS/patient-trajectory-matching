"""Aggregate-only public-demo evidence for distinct-variable comparison and replay."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import csv
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.evaluate_integrated_recorded import wait_completed, check_snapshot
from tools.prepare_demo_clinical_features import prepare, require
from app import clinical_features as cf
from app.recorded_export import export_recorded, verify_recorded, _artifacts
from app.recorded_journey import RecordedJourneyWorkspace
from patterns import clinical_source_preflight as scan
from patterns import mimic_measurement_import as measurements
from patterns.reviewed_pressure_session import fingerprint

FEATURES = {'schema': cf.SCHEMA, 'features': [
    {'id': 'latest_value', 'weight': '1', 'scale': '10'},
    {'id': 'heart_rate', 'weight': '1', 'scale': '10'},
    {'id': 'respiratory_rate', 'weight': '1', 'scale': '4'}]}


def exact(value):
    return Fraction(int(value['numerator']), int(value['denominator']))


def raw_source_check(folder, pack_path):
    pack = cf.ClinicalFeaturePack(pack_path)
    selected = {int(row['event_id'].rsplit(':', 1)[1]): row for row in pack.rows}
    require(len(selected) == len(pack.rows), 'Supplemental original source identities repeat')
    checked, manifest = 0, {}
    for number, raw in enumerate(scan.stream_chart(measurements.paths_for(folder)['chartevents'], manifest), 1):
        if number not in selected:
            continue
        row = selected[number]
        expected = {'patient_id': raw['subject_id'], 'episode_id': raw['stay_id'],
                    'item_id': raw['itemid'], 'unit': raw['valueuom'],
                    'time': datetime.fromisoformat(raw['charttime']).isoformat(), 'value': raw['valuenum']}
        require(all(row[key] == value for key, value in expected.items()), 'Supplemental value differs from its original source row')
        require(row['event_id'] == f"chartevents:{pack.definition['source_files']['chartevents']}:{number}",
                'Supplemental original row provenance differs')
        checked += 1
    require(checked == len(selected), 'Supplemental source record is absent')
    require(fingerprint(folder) == pack.definition['source_files'], 'Source changed during independent verification')
    return pack, checked


def latest(detail, rows, item, unit, window_us, minimum_us):
    anchor = detail['anchor']
    start = datetime.fromisoformat(anchor['start'])
    lower, upper = start - timedelta(microseconds=window_us), start - timedelta(microseconds=minimum_us)
    points = [row for row in rows if row['item_id'] == item and row['unit'] == unit
              and row.get('patient_id', anchor['patient_id']) == anchor['patient_id']
              and row.get('episode_id', anchor['episode_id']) == anchor['episode_id']
              and lower <= datetime.fromisoformat(row['time']) <= upper]
    if not points:
        return None
    timestamp = max(datetime.fromisoformat(row['time']) for row in points)
    return min((row for row in points if datetime.fromisoformat(row['time']) == timestamp), key=lambda row: row['event_id'])


def check_features(snapshot, options, source_pack):
    query = snapshot['query_context']['query']['baseline']
    variables = {v['id']: v for v in source_pack.definition['variables']}
    values, counts = {}, Counter()
    by_token = {a['token']: a for a in options['anchors']}
    for detail in snapshot['details']:
        anchor = detail['anchor']
        expected = {}
        for definition in FEATURES['features']:
            identity = definition['id']
            if identity == 'latest_value':
                rows, item, unit, lookback = detail['measurements'], query['item_ids'][0], query['unit_lexical'], query['max_before_start_us']
            else:
                variable = variables[identity]
                rows = source_pack.by_episode.get((anchor['patient_id'], anchor['episode_id']), [])
                item, unit = variable['item_id'], variable['unit']
                lookback = min(query['max_before_start_us'], variable['lookback_minutes'] * 60000000)
            chosen = latest(detail, rows, item, unit, lookback, max(1, query.get('min_before_start_us', 1)))
            actual = next(f for f in by_token[anchor['token']]['features'] if f['id'] == identity)
            if chosen is None:
                require(actual['status'] == 'MISSING' and actual['value_exact'] is None, 'Missing source value was imputed')
                continue
            expected[identity] = Fraction(Decimal(chosen['value']))
            require(actual['status'] == 'AVAILABLE' and exact(actual['value_exact']) == expected[identity]
                    and actual['evidence'][0]['event_id'] == chosen['event_id'],
                    'Selected feature differs from independently selected source value')
            counts[identity] += 1
        values[anchor['token']] = expected
        require(by_token[anchor['token']]['coverage']['complete'] == (len(expected) == len(FEATURES['features'])),
                'Feature coverage differs')
    return values, dict(counts)


def check_ranking(snapshot, result, values):
    reference = result['reference']
    ref = values[reference['token']]
    total_weight = sum(Fraction(Decimal(f['weight'])) for f in FEATURES['features'])
    candidates = {}
    for detail in snapshot['details']:
        anchor = detail['anchor']
        value = values[anchor['token']]
        if anchor['patient_id'] == reference['patient_id'] or len(value) != len(FEATURES['features']):
            continue
        distance = sum(Fraction(Decimal(f['weight'])) * abs(value[f['id']] - ref[f['id']]) /
                       Fraction(Decimal(f['scale'])) for f in FEATURES['features']) / total_weight
        key = (distance, anchor['patient_id'], anchor['episode_id'], anchor['token'])
        if key < candidates.get(anchor['patient_id'], (float('inf'),)):
            candidates[anchor['patient_id']] = key
    actual = [(exact(row['distance_exact']), row['patient_id'], row['selected_anchor']['episode_id'],
               row['selected_anchor']['token']) for row in result['ranked_patients']]
    require(actual == sorted(candidates.values()), 'Independent multi-variable patient ranking differs')
    for row in result['ranked_patients']:
        require(sum(exact(c['contribution_exact']) for c in row['feature_contributions']) == exact(row['distance_exact']),
                'Exact feature contribution total differs')
    require(result['temporal_result'] == snapshot['temporal_result'], 'Similarity changed temporal evidence')
    return {'ranked_patients': len(actual), 'unresolved_patients': len(result['unresolved_patients']),
            'reference_patient_wide_exclusion': True, 'exact_independent_patient_ranking': True,
            'exact_contribution_totals': True, 'temporal_result_preserved': True}


def evaluate(folder, pack_path, extraction):
    artifacts = _artifacts()
    tool_files = ('tools/evaluate_demo_clinical_features.py', 'tools/prepare_demo_clinical_features.py')
    tool_hashes = {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in tool_files}
    pack, checked = raw_source_check(folder, pack_path)
    require(extraction['tool_sha256'] == tool_hashes['tools/prepare_demo_clinical_features.py']
            and extraction['pack_sha256'] == pack.pack_sha256
            and extraction['csv_sha256'] == pack.definition['csv_sha256']
            and extraction['source_files'] == pack.definition['source_files']
            and extraction['selected_rows'] == checked
            and extraction['variables'] == pack.definition['variables']
            and extraction['clinical_mapping_verified'] is False,
            'Extraction summary does not bind the admitted source pack')
    timings = {}
    with tempfile.TemporaryDirectory(prefix='public-clinical-evaluation-') as state:
        def workspace():
            return RecordedJourneyWorkspace(public_demo_dir=folder, state_dir=state, clinical_features_path=pack_path)
        current = workspace()
        try:
            started = time.monotonic()
            job = wait_completed(current, current.start({'profile': 'literal', 'stratum': 'art',
                'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}}))
            snapshot = current.snapshot('literal', job['id'])
            timings['source_query_and_supplemental_admission_seconds'] = round(time.monotonic()-started, 6)
            temporal = check_snapshot(snapshot)
            require(temporal['anchors_verified'] == 944 and temporal['stays_retained'] == 140 and temporal['patients_retained'] == 100,
                    'Evaluation population differs from the complete pinned public demo')
            options = current.references('literal', job['id'], FEATURES)
            values, coverage = check_features(snapshot, options, pack)
            available = [a for a in options['anchors'] if a['coverage']['complete']]
            require(available, 'No complete multi-variable reference exists')
            request = {'profile': 'literal', 'job_id': job['id'], 'reference_token': available[0]['token'],
                       'top_k': 5, 'feature_profile': FEATURES}
            started = time.monotonic()
            result = current.compare(request)
            timings['distinct_variable_comparison_seconds'] = round(time.monotonic()-started, 6)
            ranking = check_ranking(snapshot, result, values)
            report = export_recorded(current, request)
            export_bytes = len(json.dumps(report, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())
        finally:
            current.close()
        started = time.monotonic()
        restored = workspace()
        try:
            require(restored.snapshot('literal', job['id']) == snapshot, 'Durable restart changed clinical evidence')
            require(restored.compare(request) == result, 'Durable restart changed distinct-variable comparison')
            require(export_recorded(restored, request) == report, 'Durable restart changed export')
        finally:
            restored.close()
        timings['durable_restart_readback_seconds'] = round(time.monotonic()-started, 6)
        started = time.monotonic()
        replay = verify_recorded(report, public_demo_dir=folder, clinical_features_path=pack_path)
        require(replay['verified'] is True, 'Exact source-backed multi-variable replay failed')
        timings['cold_source_backed_replay_seconds'] = round(time.monotonic()-started, 6)
    require(artifacts == _artifacts(), 'Implementation changed during multi-variable evaluation')
    require(tool_hashes == {p: hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in tool_files},
            'Evaluation tool changed during execution')
    return {'schema': 'public-demo-distinct-variable-evaluation-1', 'status': 'TECHNICALLY_VERIFIED',
            'executed_at_utc': datetime.now(timezone.utc).isoformat(), 'stratum': 'art',
            'extraction': extraction, 'original_supplemental_source_rows_verified': checked,
            'temporal': temporal, 'profile': FEATURES, 'available_anchors_by_feature': coverage,
            'complete_reference_anchors': len(available), 'ranking': ranking, 'timings': timings,
            'export_bytes': export_bytes, 'durable_restart_exact': True, 'source_backed_replay_verified': True,
            'artifacts': artifacts, 'evaluation_tools': tool_hashes,
            'clinical_mapping_verified': False, 'clinical_retrieval_usefulness_evaluated': False,
            'limitations': ['Illustrative weights/scales; clinical review pending.',
                           'No relevant-peer labels or clinical usefulness claim.',
                           'ART pressure stratum only for distinct-variable ranking.',
                           'Timings are descriptive single-process measurements on a shared host; concurrent work may affect them.',
                           'Full MIMIC release and production throughput are not evaluated.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mimic-dir', type=Path, required=True)
    parser.add_argument('--clinical-features', type=Path, required=True)
    parser.add_argument('--extraction-summary', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = evaluate(args.mimic_dir, args.clinical_features, json.loads(args.extraction_summary.read_text()))
    args.output.write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({'status': result['status'], 'anchors': result['temporal']['anchors_verified'],
                      'complete_reference_anchors': result['complete_reference_anchors']}))


if __name__ == '__main__':
    main()
