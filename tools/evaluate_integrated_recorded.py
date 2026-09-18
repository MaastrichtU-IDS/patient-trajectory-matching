"""Aggregate-only acceptance of the actual recorded workspace on pinned public data.

Runs a full population query, typed pattern revision, exact weighted comparison,
durable restart, export and admitted-source replay. This is technical verification,
not clinical retrieval validation. Source rows and patient identifiers stay local.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCHEMA = 'integrated-recorded-workflow-evaluation-1'
CONTROLS = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}
FEATURES = {'schema': 'recorded-similarity-features-1', 'features': [
    {'id': 'latest_value', 'weight': '2', 'scale': '10'},
    {'id': 'measurement_count', 'weight': '1', 'scale': '2'},
    {'id': 'latest_recency', 'weight': '1', 'scale': '5'},
]}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()


def micros(value):
    return (value.days * 86400 + value.seconds) * 1000000 + value.microseconds


def fraction(value):
    return Fraction(int(value['numerator']), int(value['denominator']))


def independent_bindings(detail):
    """Enumerate raw admitted points independently of the compiler and SQL oracle."""
    query, anchor = detail['query'], detail['anchor']
    baseline, followup = query['baseline'], query['followup']
    start = datetime.fromisoformat(anchor['start'])
    end = datetime.fromisoformat(anchor['end'])
    selected, after = [], []
    threshold = Fraction(Decimal(baseline['value_lexical']))
    predicates = {'lt': lambda x: x < threshold, 'le': lambda x: x <= threshold,
                  'eq': lambda x: x == threshold, 'ge': lambda x: x >= threshold,
                  'gt': lambda x: x > threshold}
    for point in detail['measurements']:
        timestamp = datetime.fromisoformat(point['time'])
        offset = micros(timestamp - start)
        if (point['item_id'] in baseline['item_ids'] and point['unit'] == baseline['unit_lexical']
                and -baseline['max_before_start_us'] <= offset <= -baseline['min_before_start_us']
                and predicates[baseline['operator']](Fraction(Decimal(point['value'])))):
            selected.append(point)
        follow_offset = micros(timestamp - (start if followup['anchor'] == 'start' else end))
        if (point['item_id'] in followup['item_ids'] and point['unit'] == followup['unit_lexical']
                and followup['min_after_anchor_us'] <= follow_offset <= followup['max_after_anchor_us']
                and (not followup['within_interval'] or start <= timestamp < end)):
            after.append(point)
    expected = []
    for before in selected:
        for later in after or [None]:
            expected.append((before['event_id'], later['event_id'] if later else None,
                Fraction(Decimal(later['value'])) - Fraction(Decimal(before['value'])) if later else None,
                baseline['unit_lexical'] if later else None))
    return Counter(expected)


def check_snapshot(snapshot):
    from app.recorded_export import _validate_snapshot
    _validate_snapshot(snapshot)
    bindings = missing = 0
    for detail in snapshot['details']:
        actual = Counter((row[3], row[4], Fraction(Decimal(row[5])) if row[5] is not None else None, row[6])
                         for row in detail['bindings'])
        require(actual == independent_bindings(detail), 'Independent point enumeration disagrees')
        bindings += len(detail['bindings'])
        missing += sum(row[4] is None for row in detail['bindings'])
    roster = snapshot['temporal_result']['roster']
    return {'anchors_verified': len(snapshot['details']), 'patients_retained': len({r['patient_id'] for r in roster}),
            'stays_retained': len(roster), 'bindings': bindings, 'missing_followup_bindings': missing,
            'all_anchors_agree_with_sql_and_independent_enumeration': True,
            'clinical_mapping_verified': False}


def independent_features(detail, query):
    start = datetime.fromisoformat(detail['anchor']['start'])
    before = query['baseline']
    points = {}
    for point in detail['measurements']:
        offset = micros(datetime.fromisoformat(point['time']) - start)
        if (point['item_id'] in before['item_ids'] and point['unit'] == before['unit_lexical']
                and -before['max_before_start_us'] <= offset <= -before['min_before_start_us']):
            points[point['event_id']] = point
    if not points:
        return None
    latest_time = max(point['time'] for point in points.values())
    latest = min((p for p in points.values() if p['time'] == latest_time), key=lambda p: p['event_id'])
    return {'latest_value': Fraction(Decimal(latest['value'])), 'measurement_count': Fraction(len(points)),
            'latest_recency': Fraction(micros(start - datetime.fromisoformat(latest['time'])), 60000000)}


def check_comparison(snapshot, comparison):
    """Recompute feature extraction, patient-best anchor and full ordering exactly."""
    query = snapshot['query_context']['query']
    features = {d['anchor']['token']: independent_features(d, query) for d in snapshot['details']}
    reference = comparison['reference']
    ref = features[reference['token']]
    require(ref is not None, 'Reference lacks independent pre-index features')
    require({f['id']: fraction(f['value_exact']) for f in reference['features']} == ref,
            'Reference feature values differ from raw evidence')
    weights = sum(Fraction(Decimal(f['weight'])) for f in FEATURES['features'])
    candidates = {}
    for detail in snapshot['details']:
        anchor = detail['anchor']
        values = features[anchor['token']]
        if anchor['patient_id'] == reference['patient_id'] or values is None:
            continue
        distance = sum(Fraction(Decimal(f['weight'])) * abs(values[f['id']] - ref[f['id']]) /
                       Fraction(Decimal(f['scale'])) for f in FEATURES['features']) / weights
        key = (distance, anchor['patient_id'], anchor['episode_id'], anchor['token'])
        if anchor['patient_id'] not in candidates or key < candidates[anchor['patient_id']]:
            candidates[anchor['patient_id']] = key
    expected = sorted(candidates.values())
    actual = [(fraction(r['distance_exact']), r['patient_id'], r['selected_anchor']['episode_id'],
               r['selected_anchor']['token']) for r in comparison['ranked_patients']]
    require(expected == actual, 'Independent exact patient ranking disagrees')
    for row in comparison['ranked_patients']:
        selected = row['selected_anchor']
        require({f['id']: fraction(f['value_exact']) for f in selected['features']} == features[selected['token']],
                'Candidate feature values differ from raw evidence')
        require(sum((fraction(f['contribution_exact']) for f in row['feature_contributions']), Fraction()) ==
                fraction(row['distance_exact']), 'Per-feature contributions do not sum exactly')
    roster = {row['patient_id'] for row in snapshot['temporal_result']['roster']} - {reference['patient_id']}
    unresolved = {row['patient_id'] for row in comparison['unresolved_patients']}
    require(unresolved == roster - set(candidates), 'Missing feature coverage differs from raw evidence')
    require(comparison['temporal_result'] == snapshot['temporal_result'], 'Ranking altered temporal population')
    return {'ranked_patients': len(actual), 'unresolved_patients': len(unresolved),
            'eligible_patients': len(roster), 'reference_patient_excluded': True,
            'exact_features_distances_best_anchors_and_ranks_verified': True,
            'exact_contribution_sums_verified': True, 'temporal_population_preserved': True}


def artifact_hashes():
    from app.recorded_export import ARTIFACTS
    from patterns.reviewed_pressure_session import FILES
    from patterns.prepared_mixed_query import ARTIFACTS as PREPARED_ARTIFACTS
    files = sorted(set(ARTIFACTS + FILES + PREPARED_ARTIFACTS + ('tools/evaluate_integrated_recorded.py', 'app/recorded_store.py',
        'data/clinical-source-demo-pin.json')))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}


def wait_completed(workspace, job):
    deadline = time.monotonic() + 3600
    while job['status'] == 'RUNNING':
        require(time.monotonic() < deadline, 'Integrated recorded query deadline exceeded')
        time.sleep(.05)
        job = workspace.get('literal', job['id'])
    require(job['status'] == 'COMPLETED', 'Integrated query failed: ' + str(job.get('error', job['status'])))
    return job


def evaluate_stratum(folder, stratum):
    from app.recorded_export import export_recorded, verify_recorded
    from app.recorded_journey import RecordedJourneyWorkspace
    artifacts = artifact_hashes()
    timings = {}
    with tempfile.TemporaryDirectory(prefix='integrated-recorded-state-') as temporary:
        state = Path(temporary) / 'state'
        def workspace():
            return RecordedJourneyWorkspace(public_demo_dir=folder, state_dir=state)
        current = workspace()
        try:
            started = time.monotonic()
            base = wait_completed(current, current.start({'profile': 'literal', 'stratum': stratum, 'controls': CONTROLS}))
            timings['cold_query_and_durable_save_seconds'] = round(time.monotonic() - started, 6)
            base_snapshot = current.snapshot('literal', base['id'])
            original = check_snapshot(base_snapshot)
            pattern = deepcopy(current.pattern_metadata('literal', base['id'])['default_pattern'])
            pattern['baseline'].update(operator='le', value_lexical='60', minimum_offset_us=-1500000000)
            pattern['followup']['maximum_offset_us'] = 3600000000
            started = time.monotonic()
            revised = current.pattern({'profile': 'literal', 'job_id': base['id'], 'pattern': pattern})
            require(revised['status'] == 'COMPLETED', 'Typed revision did not complete: ' + str(revised.get('error', revised['status'])))
            timings['custom_pattern_and_durable_save_seconds'] = round(time.monotonic() - started, 6)
            snapshot = current.snapshot('literal', revised['id'])
            revision = check_snapshot(snapshot)
            require(current.snapshot('literal', base['id']) == base_snapshot, 'Revision mutated original retained evidence')
            require({(r['patient_id'], r['episode_id']) for r in snapshot['temporal_result']['roster']} ==
                    {(r['patient_id'], r['episode_id']) for r in base_snapshot['temporal_result']['roster']},
                    'Typed revision changed admitted population')
            references = current.references('literal', revised['id'], FEATURES)
            available = [r for r in references['anchors'] if r['feature_status'] == 'AVAILABLE']
            require(available, 'No complete reference for explicit weighted profile')
            request = {'profile': 'literal', 'job_id': revised['id'], 'reference_token': available[0]['token'],
                       'top_k': 5, 'feature_profile': FEATURES}
            started = time.monotonic()
            comparison = current.compare(request)
            timings['weighted_comparison_seconds'] = round(time.monotonic() - started, 6)
            ranking = check_comparison(snapshot, comparison)
            started = time.monotonic()
            report = export_recorded(current, request)
            timings['export_seconds'] = round(time.monotonic() - started, 6)
            report_bytes = len(encoded(report))
            snapshot_bytes = len(encoded(snapshot))
        finally:
            current.close()
        started = time.monotonic()
        restored = workspace()
        try:
            require(restored.get('literal', revised['id']) == revised, 'Restart changed completed job')
            require(restored.snapshot('literal', revised['id']) == snapshot, 'Restart changed evidence')
            require(restored.compare(request) == comparison, 'Restart changed comparison')
            require(export_recorded(restored, request) == report, 'Restart changed exported report')
            timings['restart_and_exact_readback_seconds'] = round(time.monotonic() - started, 6)
        finally:
            restored.close()
        started = time.monotonic()
        replay = verify_recorded(report, public_demo_dir=folder)
        require(replay['verified'] is True, 'Source-backed export replay did not verify')
        timings['cold_source_replay_seconds'] = round(time.monotonic() - started, 6)
        require(artifacts == artifact_hashes(), 'Implementation changed during integrated evaluation')
        return {'stratum': stratum, 'status': 'TECHNICALLY_VERIFIED', 'artifacts': artifacts,
                'source_sha256': snapshot['source_context']['source_files'], 'timings': timings,
                'original': original, 'custom_pattern': {'definition': pattern, **revision}, 'weighted_comparison': ranking,
                'feature_definition': FEATURES, 'complete_reference_anchors': len(available),
                'snapshot_bytes': snapshot_bytes, 'export_bytes': report_bytes,
                'original_evidence_unchanged': True, 'durable_restart_exact_readback': True,
                'source_backed_replay_verified': True}


def evaluate(folder, strata, workers=1):
    from demo.profile_pressure_workload import monitor
    require(platform.system() == 'Linux', 'Linux process-tree memory measurement required')
    require(workers in (1, 3), 'Use one or three isolated workers')
    with tempfile.TemporaryDirectory(prefix='integrated-recorded-evaluation-') as temporary:
        def run(stratum):
            output = Path(temporary) / (stratum + '.json')
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--mimic-dir', str(folder),
                '--worker', stratum, '--output', str(output)], cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, start_new_session=True)
            measured = monitor(child, .05, 7200)
            require(measured['child_exit_code'] == 0 and not measured['timed_out'] and
                    measured['sampling_failure'] is None and output.is_file(), 'Isolated integrated evaluation failed')
            trial = {**json.loads(output.read_text()), 'process_measurement': measured}
            print(json.dumps({'stratum': stratum, 'status': 'VERIFIED', 'elapsed_seconds': measured['elapsed_seconds']}), flush=True)
            return trial
        with ThreadPoolExecutor(max_workers=workers) as pool:
            trials = list(pool.map(run, strata))
    require(all(t['artifacts'] == trials[0]['artifacts'] for t in trials), 'Implementation changed between strata')
    return {'schema': SCHEMA, 'status': 'TECHNICALLY_VERIFIED', 'executed_at_utc': datetime.now(timezone.utc).isoformat(),
            'python_version': platform.python_version(), 'dataset_id': 'mimic-iv-demo-2.2', 'strata': trials,
            'concurrent_isolated_workers': min(workers, len(strata)),
            'patient_rows_or_identifiers_included': False, 'clinical_mapping_verified': False,
            'clinical_retrieval_relevance_evaluated': False, 'full_mimic_evaluated': False,
            'reproduce': 'python -m tools.evaluate_integrated_recorded --mimic-dir /path/to/mimic-demo/icu --output verification/integrated-recorded-evaluation.json',
            'limitations': ['One sequence per stratum; cold means application state, not cleared OS caches.',
                'Sampled process-tree RSS includes independent checking and exact replay; shared host load is uncontrolled.',
                'Overlapping stratum populations must not be summed as distinct patients.',
                'Three features describe the same reviewed pressure stream; independent clinical variables are not evaluated here.',
                'No reviewer relevance labels, full credentialed MIMIC cohort, clinical validity or production-scale claim.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mimic-dir', required=True, type=Path)
    parser.add_argument('--strata', nargs='+', choices=['arterial', 'noninvasive', 'art'], default=['arterial', 'noninvasive', 'art'])
    parser.add_argument('--worker', choices=['arterial', 'noninvasive', 'art'], help=argparse.SUPPRESS)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--workers', type=int, choices=[1, 3], default=1, help='Isolated concurrent strata; timings reflect shared host contention')
    args = parser.parse_args()
    target, source = args.output.resolve(), args.mimic_dir.resolve()
    require(source != target and source not in target.parents, 'Output must not overwrite source data')
    require(target.suffix == '.json' and target.name not in {'clinical-source-demo-pin.json', 'clinical-candidate-plan.json'}, 'Invalid output destination')
    report = evaluate_stratum(source, args.worker) if args.worker else evaluate(source, args.strata, args.workers)
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix('.json.tmp')
    pending.write_text(json.dumps(report, indent=2) + '\n')
    pending.replace(target)


if __name__ == '__main__':
    main()
