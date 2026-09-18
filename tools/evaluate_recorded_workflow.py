"""Reproduce aggregate-only public-demo workflow correctness and local performance.

The independent Python oracle enumerates the admitted records without using the
query compiler, temporal graph, or SQL reference. Clinical review is not inferred.
"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, localcontext
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
DEFAULT = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}
CHANGED = {'threshold': '60', 'baseline_minutes': 25, 'followup_minutes': 60}
PROFILE = 'recorded-workflow-evaluation-1.0'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def independent_bindings(detail, controls):
    """Exhaustive exact arithmetic over one anchor's admitted evidence records."""
    anchor = detail['anchor']
    start = datetime.fromisoformat(anchor['start'])
    baseline, followup = [], []
    item = detail['query']['baseline']['item_ids'][0]
    for point in detail['measurements']:
        if point['item_id'] != item or point['unit'] != 'mmHg':
            continue
        offset = datetime.fromisoformat(point['time']) - start
        microseconds = (offset.days * 86400 + offset.seconds) * 1000000 + offset.microseconds
        if -controls['baseline_minutes'] * 60000000 <= microseconds < 0:
            if Decimal(point['value']) < Decimal(controls['threshold']):
                baseline.append(point)
        if 0 <= microseconds <= controls['followup_minutes'] * 60000000:
            followup.append(point)
    expected = []
    for before in baseline:
        for after in followup or [None]:
            with localcontext() as context:
                context.prec = 800
                difference = format(Decimal(after['value']) - Decimal(before['value']), 'f') if after else None
            expected.append([anchor['patient_id'], anchor['episode_id'], detail['treatment_event_id'],
                             before['event_id'], after['event_id'] if after else None,
                             difference, 'mmHg' if after else None])
    return sorted(expected, key=lambda row: tuple('' if value is None else value for value in row))


def verify_result(service, job, result, controls):
    require(result['status'] == 'COMPLETED', 'Incomplete result')
    require(result['anchors_verified'] == result['anchors_total'], 'Incomplete anchor coverage')
    rows, missing, signs = [], 0, Counter()
    for token in result['details']:
        detail = service.inspect(job['id'], token)
        require(detail['sql_agreement'] is True, 'Missing independent SQL agreement')
        expected = independent_bindings(detail, controls)
        actual = sorted(detail['bindings'], key=lambda row: tuple('' if value is None else value for value in row))
        require(expected == actual, 'Independent enumeration disagrees')
        for row in actual:
            if row[4] is None:
                missing += 1
                require(row[5] is None and row[6] is None, 'Missing follow-up was replaced')
            else:
                signs['positive' if Decimal(row[5]) > 0 else 'negative' if Decimal(row[5]) < 0 else 'zero'] += 1
        rows.extend(actual)
    metrics = {'patients': len({row[0] for row in rows}), 'stays': len({tuple(row[:2]) for row in rows}),
               'segments': len({row[2] for row in rows}), 'eligible_pairs': len({tuple(row[:4]) for row in rows}),
               'followup_bindings': sum(row[4] is not None for row in rows), 'pairs_without_followup': missing}
    require(metrics == result['metrics'], 'Aggregate reconciliation failed')
    return {'metrics': metrics, 'anchors_verified': result['anchors_verified'],
            'stays_retained': len(result['roster']),
            'patients_retained': len({row['patient_id'] for row in result['roster']}),
            'roster_status_counts': dict(sorted(Counter(row['status'] for row in result['roster']).items())),
            'delta_sign_binding_counts': {key: signs[key] for key in ('positive', 'negative', 'zero')},
            'all_anchors_agree_with_sql_and_independent_enumeration': True,
            'missing_followup_preserved': True}


def comparable(result):
    value = deepcopy(result)
    value.pop('elapsed_seconds', None)
    return value


def artifact_hashes():
    from patterns.reviewed_pressure_session import FILES
    from patterns.prepared_mixed_query import ARTIFACTS
    files = sorted(set(FILES + ARTIFACTS + ('demo/pressure.py', 'demo/pressure_cache.py',
        'demo/profile_pressure_workload.py', 'tools/evaluate_recorded_workflow.py',
        'data/clinical-source-demo-pin.json', 'data/clinical-candidate-plan.json')))
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files}


def evaluate_stratum(folder, stratum):
    from demo.pressure import PressureService
    before = artifact_hashes()
    service = PressureService(folder=folder, synthetic=False)
    def run(label, controls):
        started = time.monotonic()
        job = service.start({'stratum': stratum, 'controls': controls})
        deadline = started + 3600
        while job['status'] == 'RUNNING':
            require(time.monotonic() < deadline, 'Job deadline exceeded')
            time.sleep(.05)
            job = service.get(job['id'])
        require(job['status'] == 'COMPLETED', 'Recorded query did not complete')
        elapsed = time.monotonic() - started
        result = service.jobs[job['id']]['_result']
        checked = verify_result(service, job, result, controls)
        record = {'label': label, 'controls': controls, 'mode': job['execution']['mode'],
                  'wall_seconds_including_polling': round(elapsed, 6), 'job_timing': job['timing'],
                  'batch_preparation': job['execution'].get('batch_preparation'), **checked}
        return record, deepcopy(result)
    try:
        cold, original = run('cold_default', DEFAULT)
        service.result_cache.clear()
        warm, repeated = run('warm_default_fresh_execution', DEFAULT)
        require(comparable(original) == comparable(repeated), 'Warm execution changed evidence')
        changed, changed_result = run('warm_changed_windows_and_threshold', CHANGED)
        cached, cached_result = run('cached_changed_complete_result', CHANGED)
        require(comparable(changed_result) == comparable(cached_result), 'Cache changed evidence')
        require(warm['mode'] == 'fresh_query' and cached['mode'] == 'cached_complete_result', 'Unexpected execution mode')
        service.session.check_current()
        require(before == artifact_hashes(), 'Implementation changed during evaluation')
        return {'stratum': stratum, 'source_sha256': service.session.context['source_files'],
                'declaration_sha256': hashlib.sha256((ROOT / f'data/{stratum}-source-fidelity-review.json').read_bytes()).hexdigest(),
                'artifacts': before, 'trials': [cold, warm, changed, cached],
                'warm_full_evidence_equal': True, 'cached_full_evidence_equal': True}
    finally:
        service.close()


def evaluate(folder, strata):
    from demo.profile_pressure_workload import monitor
    require(platform.system() == 'Linux', 'Linux process-tree memory measurement required')
    trials = []
    with tempfile.TemporaryDirectory(prefix='recorded-evaluation-') as temporary:
        for stratum in strata:
            output = Path(temporary) / (stratum + '.json')
            child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--mimic-dir', str(folder),
                '--worker', stratum, '--output', str(output)], cwd=ROOT, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            measured = monitor(child, .05, 3600)
            require(measured['child_exit_code'] == 0 and not measured['timed_out'] and
                    measured['sampling_failure'] is None and output.is_file(), 'Isolated evaluation failed')
            trial = json.loads(output.read_text())
            trials.append({**trial, 'process_measurement': measured})
            print(json.dumps({'stratum': stratum, 'status': 'VERIFIED',
                              'elapsed_seconds': measured['elapsed_seconds']}), flush=True)
    require(all(trial['artifacts'] == trials[0]['artifacts'] for trial in trials), 'Implementation changed between strata')
    return {'profile': PROFILE, 'status': 'TECHNICALLY_VERIFIED',
        'executed_at_utc': datetime.now(timezone.utc).isoformat(), 'python_version': platform.python_version(),
        'dataset_id': 'mimic-iv-demo-2.2', 'source_mode': 'pinned_public_demo',
        'source_row_counts': {name: item['rows'] for name, item in json.loads((ROOT / 'data/clinical-source-demo-pin.json').read_text())['files'].items()},
        'strata': trials, 'patient_rows_or_identifiers_included': False,
        'clinical_mapping_verified': False, 'clinical_retrieval_relevance_evaluated': False,
        'full_mimic_evaluated': False, 'scaled_authored_fixture_evaluated': False,
        'representative_production_scale_established': False,
        'reproduce': 'python -m tools.evaluate_recorded_workflow --mimic-dir /path/to/mimic-demo/icu --output verification/recorded-workflow-evaluation.json',
        'limitations': ['One cold/warm/cache sequence per stratum, run sequentially in isolated processes; shared host load is uncontrolled.',
            'Memory covers the worker and observed descendants over all four trials, including independent verification; it is sampled RSS, not an exact peak.',
            'Cold means fresh application state, not cleared operating-system disk caches.',
            'Independent SQL and Python enumeration share the admitted source boundary; neither establishes clinical correctness of that boundary.',
            'Per-stratum populations overlap and must not be summed as distinct patients.',
            'No clinical reviewer judgments, held-out relevance labels, full credentialed MIMIC data, or production throughput claim.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mimic-dir', required=True, type=Path)
    parser.add_argument('--worker', choices=['arterial', 'noninvasive', 'art'], help=argparse.SUPPRESS)
    parser.add_argument('--strata', nargs='+', choices=['arterial', 'noninvasive', 'art'], default=['arterial', 'noninvasive', 'art'])
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    target, source = args.output.resolve(), args.mimic_dir.resolve()
    require(source != target and source not in target.parents, 'Output must not overwrite source data')
    require(target.suffix == '.json' and target.name not in {'clinical-source-demo-pin.json', 'clinical-candidate-plan.json'}, 'Invalid output destination')
    report = evaluate_stratum(args.mimic_dir, args.worker) if args.worker else evaluate(args.mimic_dir, args.strata)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(target)


if __name__ == '__main__':
    main()
