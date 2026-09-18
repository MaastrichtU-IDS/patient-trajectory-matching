"""Held-out retrieval metrics and isolated authored scaling; no clinical labels inferred."""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import platform
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROFILE = {'schema': 'recorded-similarity-features-1', 'features': [
    {'id': 'latest_value', 'weight': '2', 'scale': '10'},
    {'id': 'value_change', 'weight': '1', 'scale': '5'},
    {'id': 'measurement_count', 'weight': '1', 'scale': '2'},
    {'id': 'latest_recency', 'weight': '1', 'scale': '5'}]}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identities(value, label):
    require(isinstance(value, list) and all(isinstance(v, str) and v for v in value),
            label + ' must contain nonempty patient IDs')
    require(len(set(value)) == len(value), label + ' contains duplicate patients')
    return set(value)


def evaluate(bundle, k=10):
    """Aggregate only. Missing judgments remain unknown, never negative labels.

    The externally frozen manifest binds source context, profile, split, rankings,
    and reviewer judgments. Hash agreement does not prove pre-registration or
    reviewer independence; those require external governance evidence.
    """
    require(type(k) is int and 1 <= k <= 20, 'k must be an integer from 1 to 20')
    require(bundle.get('schema') == 'heldout-retrieval-evaluation-1', 'Invalid evaluation schema')
    require(bundle.get('label_origin') in ('independent_reviewer', 'authored_metric_fixture'),
            'Labels must come from independent reviewers or explicitly authored metric fixtures')
    require(isinstance(bundle.get('review_protocol_id'), str) and bundle['review_protocol_id'],
            'A review protocol ID is required')
    fields = ('source_context', 'feature_profile', 'split', 'queries')
    require(set(bundle.get('frozen_hashes', {})) == set(fields), 'Missing frozen artifact hashes')
    for key in fields:
        require(digest(bundle[key]) == bundle['frozen_hashes'][key], 'Frozen artifact mismatch: ' + key)
    split = bundle['split']
    development = identities(split['development_patient_ids'], 'Development split')
    heldout = identities(split['evaluation_patient_ids'], 'Evaluation split')
    require(development and heldout and not development.intersection(heldout),
            'Development and evaluation patients must be nonempty and disjoint')
    queries = bundle['queries']
    require(isinstance(queries, list) and queries, 'At least one evaluated query is required')
    seen, scores = set(), []
    for query in queries:
        reference = query['reference_patient_id']
        require(reference in heldout and reference not in seen, 'Each reference must be a distinct held-out patient')
        seen.add(reference)
        candidates = identities(query['candidate_patient_ids'], 'Candidate pool')
        require(candidates <= heldout and reference not in candidates,
                'Candidates must be held out and exclude every episode of the reference patient')
        ranked = query['ranked_patient_ids']
        require(identities(ranked, 'Ranking') <= candidates, 'Ranking contains a patient outside the candidate pool')
        labels = query['judgments']
        require(isinstance(labels, dict) and set(labels) <= candidates, 'Judgments contain unknown patients')
        require(all(type(v) is int and 0 <= v <= 3 for v in labels.values()),
                'Relevance grades must be integers 0..3; omit unjudged patients')
        top = ranked[:k]
        judged_top = [patient for patient in top if patient in labels]
        positive_top = sum(labels[patient] > 0 for patient in judged_top)
        unknown_top = len(top) - len(judged_top)
        complete_pool = len(labels) == len(candidates)
        positives = sum(grade > 0 for grade in labels.values())
        precision = positive_top / k if unknown_top == 0 else None
        recall = positive_top / positives if complete_pool and positives else None
        dcg = sum((2 ** labels.get(patient, 0) - 1) / math.log2(rank + 2)
                  for rank, patient in enumerate(top))
        ideal = sum((2 ** grade - 1) / math.log2(rank + 2)
                    for rank, grade in enumerate(sorted(labels.values(), reverse=True)[:k]))
        scores.append({'precision_at_k': precision, 'recall_at_k': recall,
                       'ndcg_at_k': dcg / ideal if complete_pool and ideal else None,
                       'precision_lower_bound': positive_top / k,
                       'precision_upper_bound': (positive_top + unknown_top) / k,
                       'judged_top_count': len(judged_top), 'returned_top_count': len(top),
                       'judged_candidate_count': len(labels), 'candidate_count': len(candidates),
                       'ranked_candidate_count': len(ranked),
                       'complete_candidate_judgments': complete_pool,
                       'zero_relevant_complete_pool': complete_pool and positives == 0})
    names = ('precision_at_k', 'recall_at_k', 'ndcg_at_k',
             'precision_lower_bound', 'precision_upper_bound')
    aggregates = {}
    for name in names:
        values = [row[name] for row in scores if row[name] is not None]
        aggregates[name] = {'macro_mean': sum(values) / len(values) if values else None,
                            'defined_queries': len(values), 'undefined_queries': len(scores) - len(values)}
    return {'schema': 'heldout-retrieval-report-1', 'k': k, 'queries': len(scores),
            'label_origin': bundle['label_origin'], 'review_protocol_id': bundle['review_protocol_id'],
            'frozen_hashes': deepcopy(bundle['frozen_hashes']), 'metrics': aggregates,
            'coverage': {key: sum(row[key] for row in scores) for key in
                ('judged_top_count', 'returned_top_count', 'judged_candidate_count', 'candidate_count',
                 'ranked_candidate_count', 'complete_candidate_judgments', 'zero_relevant_complete_pool')},
            'patient_identifiers_included': False, 'clinical_validation_established': False,
            'limitations': ['Relevance grades and rankings are externally supplied; no labels are generated from distances.',
                'Hash agreement binds inputs but does not prove reviewer independence or pre-registration chronology.',
                'Precision uses k as denominator, including unfilled slots. Unknown retrieved judgments produce bounds only.',
                'Recall and nDCG require complete candidate-pool judgments and at least one relevant candidate.',
                'Macro means include only defined queries; coverage and undefined denominators must accompany them.']}


def authored_snapshot(patients):
    require(type(patients) is int and 2 <= patients <= 5000, 'Authored scale requires 2..5000 patients')
    details, roster = [], []
    for index in range(patients):
        patient = f'authored-{index:05d}'
        for episode in range(2 if index == 0 else 1):
            token = hashlib.sha256(f'{patient}/{episode}'.encode()).hexdigest()[:24]
            measurements = []
            for position, minute in enumerate((35, 45, 55, 65)):
                at = f'2150-01-01T09:{minute:02d}:00' if minute < 60 else '2150-01-01T10:05:00'
                measurements.append({'event_id': f'{token}/{position}', 'value': str(50 + index % 70 + position),
                    'time': at, 'item_id': '2000', 'unit': 'mmHg',
                    'source': {'table': 'authored_fixture', 'record_number': index * 4 + position + 1},
                    'claim_id': f'authored-claim-{token}-{position}',
                    'claim_sha256': digest({'authored': index, 'episode': episode, 'position': position})})
            anchor = {'patient_id': patient, 'episode_id': f'{patient}/{episode}', 'token': token,
                      'start': '2150-01-01T10:00:00', 'end': '2150-01-01T11:00:00', 'status': 'MATCH'}
            details.append({'anchor': anchor, 'measurements': measurements})
            roster.append({key: anchor[key] for key in ('patient_id', 'episode_id', 'status')})
    return {'profile': 'literal', 'source_context': {'source_mode': 'authored_scaling_fixture'},
            'query_context': {'query': {'baseline': {'item_ids': ['2000'], 'unit_lexical': 'mmHg',
                'max_before_start_us': 1800000000, 'value_lexical': '65'}}},
            'details': details, 'temporal_result': {'roster': roster, 'metrics': {'patients': patients},
                'anchors': [deepcopy(detail['anchor']) for detail in details]}}


def scale_worker(patients):
    from app.recorded_similarity import compare_snapshot
    from app.feature_profiles import restore
    snapshot = authored_snapshot(patients)
    reference = snapshot['details'][0]['anchor']['token']
    started = time.perf_counter()
    result = compare_snapshot(snapshot, reference, 10, PROFILE)
    elapsed = time.perf_counter() - started
    rows = result['ranked_patients']
    require(len(rows) == patients - 1 and not result['unresolved_patients'], 'Unexpected candidate coverage')
    require(all(row['patient_id'] != 'authored-00000' for row in rows), 'Reference patient leaked')
    for row in rows:
        require(sum((restore(c['contribution_exact']) for c in row['feature_contributions']), Fraction(0))
                == restore(row['distance_exact']), 'Feature contribution reconciliation failed')
    signature = digest(rows)
    del result, rows
    for detail in snapshot['details']:
        detail['measurements'][-1]['value'] = '999999'
        detail['anchor']['end'] = '2151-01-01T11:00:00'
    rerun = compare_snapshot(snapshot, reference, 10, PROFILE)
    # End time is retained for display, so compare rank/distance/contributions only.
    def rank_signature(value):
        return digest([{key: row[key] for key in ('patient_id', 'distance_exact', 'feature_contributions')}
                       for row in value['ranked_patients']])
    altered = rank_signature(rerun)
    for detail in snapshot['details']:
        detail['measurements'][-1]['value'] = '-999999'
    require(rank_signature(compare_snapshot(snapshot, reference, 10, PROFILE)) == altered,
            'Follow-up values leaked into features')
    # Restore authored input to test both follow-up and end-time independence.
    require(rank_signature(compare_snapshot(authored_snapshot(patients), reference, 10, PROFILE)) == altered,
            'Follow-up or treatment end leaked into ranking')
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return {'patients': patients, 'anchors': patients + 1, 'ranked_patients': patients - 1,
            'first_comparison_wall_seconds': round(elapsed, 6),
            'worker_peak_rss_bytes': rss * 1024 if sys.platform.startswith('linux') else rss,
            'ranked_output_sha256': signature, 'patient_wide_exclusion_verified': True,
            'followup_and_end_time_independence_verified': True, 'exact_contributions_verified': True}


def benchmark(sizes):
    artifacts = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in
                 ('app/recorded_similarity.py', 'app/feature_profiles.py', 'tools/evaluate_retrieval.py')}
    trials = []
    for size in sizes:
        completed = subprocess.run([sys.executable, '-m', 'tools.evaluate_retrieval', '--worker', str(size)],
                                   cwd=ROOT, capture_output=True, text=True, check=True, timeout=180)
        trials.append(json.loads(completed.stdout))
    require(all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == value
                for name, value in artifacts.items()), 'Implementation changed during benchmark')
    return {'schema': 'authored-retrieval-scale-1', 'executed_at_utc': datetime.now(timezone.utc).isoformat(),
            'python_version': platform.python_version(), 'platform': platform.system(),
            'dataset': 'authored_uniform_four_measurement_fixture', 'clinical_relevance_evaluated': False,
            'full_mimic_evaluated': False, 'patient_identifiers_included': False,
            'feature_profile': PROFILE, 'artifacts': artifacts, 'trials': trials,
            'limitations': ['Single trial per size in fresh isolated Python processes on a shared host.',
                'Wall time covers one exact comparison; peak RSS covers fixture construction and subsequent invariant checks.',
                'No ingestion, source reasoning, durable storage, concurrency, real missingness, or clinical quality benchmark.',
                'Four history features of one authored pressure stream; not distinct clinical variables.',
                'Known comparison implementation scans patient anchors and roster repeatedly; timings do not establish production throughput.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--input', type=Path)
    mode.add_argument('--benchmark', action='store_true')
    mode.add_argument('--worker', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--sizes', nargs='+', type=int, default=[100, 1000, 5000])
    parser.add_argument('--k', type=int, default=10)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.worker is not None:
        report = scale_worker(args.worker)
    elif args.benchmark:
        report = benchmark(args.sizes)
    else:
        require(args.input.stat().st_size <= 64 * 1024 * 1024, 'Evaluation bundle exceeds 64 MiB')
        report = evaluate(json.loads(args.input.read_text()), args.k)
    serialized = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if args.output:
        args.output.write_text(serialized)
    else:
        print(serialized, end='')


if __name__ == '__main__':
    main()
