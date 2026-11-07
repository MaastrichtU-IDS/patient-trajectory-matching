#!/usr/bin/env python3
"""Matcher comparison for the execution store (docs/execution-store.md, "Matching cost"; D19, D21).

The same seeded queries and windows as the scaling benchmark are run through `execution_store.match`
of different code (arms) over the same snapshots, each arm and size in a fresh process so that
peak resident memory is the query process's alone:

  A  the matcher as it stood before D21 (a git ref, extracted to a scratch directory and imported
     from there): one query loads every candidate's events with an IN list, then matches.
  B  this tree: candidates loaded and matched in chunks, a prepared query, trusted events, windows
     checked as plain integers, comparability in SQL.  `B_decided` is B with `detail='decided'`.
  C  this tree with `workers=N` (N = 1, 2, 4, 8, 14): the engine's work units run in a persistent pool of
     spawned processes; recorded as `C_w1` ... `C_w14`.  `C_w1` is the unchanged single-process path (a
     control).  The pool is started and warmed before the first query (its start-up is recorded
     separately, as the one cold-start figure), so the timings are of a warm pool.  The query process's
     peak memory is its own (`getrusage(RUSAGE_SELF)`), the largest worker's is reported beside it; their
     sum would double-count the shared page cache and is not given.
  D, D'  Rust single-threaded, Rust parallel (D21): later changes, recorded as `not_run` so the report
     structure is already in place for them.

Per query it records wall seconds of `match` (median over repeats), the seconds spent inside the
engine (`trajectory_graph` match calls) and inside the event loader, the candidate count, and a
digest of the full result (plan, candidates, counts, every patient's result, canonical JSON), so that
arms are compared for byte identity query by query, not only on the small cohort the unit tests use.
Snapshots are built by `tools/benchmark_execution_store.py --worker build` (or kept by its
`--keep`); this tool only reads them.

The report holds aggregates only. It is evidence for one machine and synthetic data.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROFILE = 'matcher-comparison-1.0'
ARMS = {
    'A': 'matcher before D21: one IN-list load of every candidate, validate every patient, one result dict per patient',
    'B': 'D21 step 1: chunked load and match, PreparedQuery, trusted events, integer window checks, SQL comparability; '
         "detail='full'",
    'B_decided': "arm B with detail='decided'",
}
WORKER_COUNTS = (1, 2, 4, 8, 14)
ARMS['C'] = ('D21 arm C: B plus partition-parallel matching in a persistent pool of spawned worker processes, '
             f'at {", ".join(map(str, WORKER_COUNTS))} workers; detail=\'full\'')
FUTURE_ARMS = {
    'D': 'Rust matcher, single-threaded and batched, including FFI (a later change, built only if B+C misses the target)',
    "D'": 'Rust matcher, parallel (a later change, same condition)',
}
TARGET = {'size': 300_000, 'match_seconds_p95_max': 10.0, 'match_seconds_max_max': 30.0,
          'query_process_peak_rss_bytes_below': 4 * 2**30, 'detail': 'full', 'machine': 'Apple M4 Pro, 64 GB'}
ARTIFACTS = ('tools/benchmark_matcher.py', 'tools/benchmark_execution_store.py', 'patterns/execution_store.py',
             'patterns/trajectory_graph.py', 'patterns/temporal_stn.py', 'patterns/test_execution_store.py',
             'patterns/fixtures/execution_store_match_digests.json')
RSS_LIMIT_BYTES = 48 * 2**30
LIMITATIONS = (
    'One machine (see environment); no other hardware, operating system or storage was measured.',
    'The data are synthetic and replicated beyond 10,000 distinct patients (see the scaling report): candidate sets and '
    'outcomes scale as exact multiples, which a real population would not.',
    'Queries are the scaling benchmark\'s seeded random queries (10 at 100,000 patients and above), not a clinical mix; '
    'percentiles over 10 queries rest on few samples, and the maximum is the slowest single query.',
    'Arm A is the code of the stated git ref extracted and imported from a scratch directory; both arms read the same '
    'snapshot files. The snapshots were built by the builder at the ref the report states, not by the final tree.',
    'Peak memory is the query process\'s maximum resident set size. It includes the interpreter, DuckDB buffers, the '
    'caller\'s windows dictionary (one entry per patient, rebuilt for each query) and the full result, which for '
    "detail='full' is one dict per patient and is part of what the caller asked for.",
    '"Engine-only" time is the seconds inside `trajectory_graph` match calls as measured by a wrapper (about 0.1 '
    'microsecond of overhead per call); "end-to-end" is the whole `match` call including candidates(), loading, '
    'comparability, exclusions and the context digests.',
    'Latencies are wall-clock times of one Python thread and one DuckDB thread; no tuning was attempted.',
    'Byte identity is checked by a digest of canonical JSON of the plan, candidate count, counts and every patient result '
    '(not the context block, whose snapshot and code digests change with the code); no exhaustive reference ran at scale.',
    'Arm C: workers and the query process share one machine (10 performance and 4 efficiency cores): counts above 10 '
    'put work on efficiency cores. Workers are warm for the timings; start-up is one cold figure per count, with '
    'the page cache warm from earlier arms. The comparability and exclusion steps still run in the query process.',
    'Arms D and D\' have not been run.')


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def pct(values, q):
    import math
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None


def summary(values):
    return {'p50': pct(values, .5), 'p95': pct(values, .95), 'max': max(values) if values else None}


def result_digest(result):
    """sha256 of canonical JSON of what the matcher decided, streamed so no second copy of the result is built."""
    h = hashlib.sha256()
    for key in ('plan', 'candidates', 'counts'):
        h.update(json.dumps(result[key], sort_keys=True).encode())
    for r in result['results']:
        h.update(json.dumps(r, sort_keys=True).encode())
    return h.hexdigest()


# The worker: one arm, one snapshot.

def rss_now():
    out = subprocess.run(['ps', '-o', 'rss=', '-p', str(os.getpid())], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) * 1024


def worker(args):
    code_root = str(Path(args.code_root).resolve())
    # `patterns` comes from the arm's tree; the helpers are this tree's, imported without letting them
    # put this tree in front.
    sys.path[:] = [code_root] + [p for p in sys.path if Path(p or '.').resolve() != ROOT and p != str(ROOT / 'tools')] + [str(ROOT)]
    spec = importlib.util.spec_from_file_location('benchmark_execution_store_helpers', ROOT / 'tools/benchmark_execution_store.py')
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    from patterns import execution_store as es
    from patterns import trajectory_graph as tg
    if not Path(es.__file__).resolve().is_relative_to(Path(code_root)):
        raise RuntimeError(f'imported {es.__file__}, not from {code_root}')
    engine = {'seconds': 0.0}
    loading = {'seconds': 0.0}
    name = 'match_prepared' if hasattr(tg, 'match_prepared') else 'match'
    inner = getattr(tg, name)
    def timed_engine(*a, **k):
        t = time.perf_counter()
        try:
            return inner(*a, **k)
        finally:
            engine['seconds'] += time.perf_counter() - t
    setattr(tg, name, timed_engine)
    loader = es._load_events
    def timed_load(*a, **k):
        t = time.perf_counter()
        try:
            return loader(*a, **k)
        finally:
            loading['seconds'] += time.perf_counter() - t
    es._load_events = timed_load
    inputs, _ = bench.query_inputs(args.queries)
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    ids = [r[0] for r in store.query('SELECT patient_id FROM patients ORDER BY patient_idx')]
    bounds = {pid: (lo, hi) for pid, lo, hi in store.query(
        "SELECT p.patient_id, min(c.lo), max(c.hi) FROM patients p LEFT JOIN coverage c USING (patient_idx) "
        "WHERE p.patient_id NOT LIKE '%~r%' GROUP BY p.patient_id")}
    kwargs = {'detail': args.detail} if args.detail != 'full' else {}
    pool_info = None
    if args.arm_workers > 1:
        kwargs['workers'] = args.arm_workers
        pool = es.get_pool(store, args.arm_workers)
        pool.warm()
        pool_info = {'workers': args.arm_workers, 'start_seconds_cold': pool.start_seconds}
    baseline = rss_now()
    pooled, runs = {}, []
    for repeat in range(args.repeats):
        rows = []
        for seed, query in inputs:
            if seed not in pooled:
                pooled[seed] = bench.pool_windows(seed, bounds)
            windows = bench.expand_windows(pooled[seed], ids)
            found = es.candidates(store, query)
            candidates = len(found['patient_idx'])
            del found
            gc.collect()
            engine['seconds'] = loading['seconds'] = 0.0
            started = time.perf_counter()
            result = es.match(store, query, windows, **kwargs)
            seconds = time.perf_counter() - started
            row = {'seed': seed, 'match_seconds': seconds, 'engine_seconds': engine['seconds'],
                   'load_seconds': loading['seconds'], 'candidates': candidates, 'counts': result['counts'],
                   'listed_results': len(result['results'])}
            if repeat == 0:
                row['digest'] = result_digest(result) if args.detail == 'full' else None
            rows.append(row)
            del result, windows
            gc.collect()
        runs.append(rows)
    out = {'runs': runs, 'rss_after_open_bytes': baseline, 'code': es.__file__,
           'engine_wrapper': name, 'detail': args.detail}
    if pool_info:
        pool_info['worker_peak_rss_bytes'] = pool.worker_peak_rss
        pool_info['worker_memory_limit'] = pool.memory_limit
        pool_info['parent_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if sys.platform == 'darwin' else 1024)
        out['pool'] = pool_info
        es.close_pools()
    store.close()
    return out


def run_worker(arm, code_root, snapshot, size, queries, repeats, detail, memory_limit, budget, workers=1):
    out = Path(tempfile.mkdtemp(prefix='matcher-bench-')) / 'out.json'
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--code-root', str(code_root), '--snapshot',
               str(snapshot), '--size', str(size), '--queries', str(queries), '--repeats', str(repeats), '--detail', detail,
               '--memory-limit', memory_limit, '--arm-workers', str(workers), '--worker-output', str(out)]
    started = time.monotonic()
    child = subprocess.Popen(command, cwd=ROOT, stderr=subprocess.PIPE, text=True, start_new_session=True)
    failure = None
    while True:
        done, status, usage = os.wait4(child.pid, os.WNOHANG)
        if done:
            break
        if time.monotonic() - started > budget:
            failure = {'reason': 'TIME_BUDGET', 'detail': f'exceeded {budget:.0f} s'}
        else:
            ps = subprocess.run(['ps', '-o', 'rss=', '-p', str(child.pid)], capture_output=True, text=True)
            if int(ps.stdout.strip() or 0) * 1024 > RSS_LIMIT_BYTES:
                failure = {'reason': 'MEMORY_GUARD', 'detail': f'above {RSS_LIMIT_BYTES / 2**30:.0f} GiB'}
        if failure:
            os.killpg(child.pid, signal.SIGKILL)
            _, status, usage = os.wait4(child.pid, 0)
            break
        time.sleep(.5)
    child.returncode = os.waitstatus_to_exitcode(status)  # reaped by wait4, not by Popen
    stderr = child.stderr.read()
    child.stderr.close()
    maxrss = usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    record = {'wall_seconds': time.monotonic() - started, 'peak_rss_bytes': maxrss}
    # With a pool, wait4's maximum covers the workers it reaped: the query process's own is measured inside.
    if failure is None and (not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0):
        failure = {'reason': 'WORKER_FAILED', 'detail': (stderr.strip().splitlines() or ['no message'])[-1][:300]}
    if failure:
        return None, {**record, 'failure': failure}
    result = json.loads(out.read_text())
    shutil.rmtree(out.parent, ignore_errors=True)
    if result.get('pool'):
        record['peak_rss_bytes'] = result['pool']['parent_peak_rss_bytes']
    return result, record


# Statistics.

def arm_statistics(result, record, patients):
    runs = result['runs']
    per_query = []
    for i in range(len(runs[0])):
        rows = [run[i] for run in runs]
        per_query.append({k: statistics.median(r[k] for r in rows) for k in ('match_seconds', 'engine_seconds', 'load_seconds')}
                         | {'seed': rows[0]['seed'], 'candidates': rows[0]['candidates'], 'counts': rows[0]['counts'],
                            'digest': rows[0].get('digest'), 'listed_results': rows[0]['listed_results']})
    candidates = sum(q['candidates'] for q in per_query)
    us = lambda key: 1e6 * sum(q[key] for q in per_query) / candidates if candidates else None
    pool = result.get('pool')
    each = [summary([r['match_seconds'] for r in run]) for run in runs]
    spread = {k: {'min': min(e[k] for e in each), 'max': max(e[k] for e in each)} for k in ('p50', 'p95', 'max')}
    return {'queries': len(per_query), 'repeats': len(runs),
            'per_repeat_match_seconds': each, 'spread_across_repeats': spread,
            'all_runs_match_seconds': summary([r['match_seconds'] for run in runs for r in run]),
            **({'workers': pool['workers'], 'worker_start_seconds_cold': pool['start_seconds_cold'],
                'largest_worker_peak_rss_bytes': pool['worker_peak_rss_bytes'],
                'worker_memory_limit': pool['worker_memory_limit']} if pool else {'workers': 1}),
            'match_seconds': summary([q['match_seconds'] for q in per_query]),
            'candidate_count': summary([q['candidates'] for q in per_query]),
            'per_candidate_microseconds': {'end_to_end': us('match_seconds'), 'engine_only': us('engine_seconds'),
                                           'event_loading': us('load_seconds')},
            'seconds_share': {'engine': sum(q['engine_seconds'] for q in per_query) / sum(q['match_seconds'] for q in per_query),
                              'event_loading': sum(q['load_seconds'] for q in per_query) / sum(q['match_seconds'] for q in per_query)},
            'microseconds_per_patient_end_to_end': 1e6 * sum(q['match_seconds'] for q in per_query) / (patients * len(per_query)),
            'query_process': {'peak_rss_bytes': record['peak_rss_bytes'], 'rss_after_open_bytes': result['rss_after_open_bytes'],
                              'wall_seconds': record['wall_seconds']},
            'code': result['code'], 'detail': result['detail'],
            'per_query': [{k: q[k] for k in ('seed', 'candidates', 'match_seconds', 'engine_seconds', 'load_seconds',
                                              'listed_results')} for q in per_query]}, per_query


def identity(a_queries, b_queries):
    """Query-by-query comparison of result digests between two arms."""
    by_seed = {q['seed']: q['digest'] for q in a_queries}
    same = [q['seed'] for q in b_queries if by_seed.get(q['seed']) == q['digest']]
    return {'compared': len(b_queries), 'identical': len(same),
            'mismatching_seeds': sorted(set(q['seed'] for q in b_queries) - set(same))}


def target_check(entry, arm='B'):
    b = entry['arms'].get(arm)
    if not b or 'match_seconds' not in b:
        return None
    t = TARGET
    return {'size': entry['size'], 'arm': arm, 'p95_seconds': b['match_seconds']['p95'],
            'max_seconds': b['match_seconds']['max'], 'peak_rss_bytes': b['query_process']['peak_rss_bytes'],
            'p95_met': b['match_seconds']['p95'] <= t['match_seconds_p95_max'],
            'max_met': b['match_seconds']['max'] <= t['match_seconds_max_max'],
            'memory_met': b['query_process']['peak_rss_bytes'] < t['query_process_peak_rss_bytes_below']}


def extract_ref(ref, into):
    into.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(['git', 'archive', ref, 'patterns', 'schemas', 'ontology', 'demos/mockups/synthetic'], cwd=ROOT,
                             capture_output=True, check=True).stdout
    subprocess.run(['tar', '-x', '-C', str(into)], input=archive, check=True)
    commit = subprocess.run(['git', 'rev-parse', ref], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    return commit


def benchmark(args):
    started = time.monotonic()
    helpers = importlib.util.spec_from_file_location('bench_env', ROOT / 'tools/benchmark_execution_store.py')
    bench = importlib.util.module_from_spec(helpers)
    helpers.loader.exec_module(bench)
    scratch = Path(tempfile.mkdtemp(prefix='matcher-arm-a-'))
    commit = extract_ref(args.arm_a_ref, scratch)
    arm_a = {'description': ARMS['A'], 'git_ref': args.arm_a_ref, 'git_commit': commit, 'code_root': 'extracted from git archive',
             # A list, not a path-to-digest map: the provenance gate reads every such map as a
             # binding of the current tree, and these digests describe the old code at git_ref.
             'artifacts_at_ref': [{'path': p, 'sha256': hashlib.sha256((scratch / p).read_bytes()).hexdigest()} for p in
                                  ('patterns/execution_store.py', 'patterns/trajectory_graph.py', 'patterns/temporal_stn.py')]}
    sizes, snapshots = [], {}
    for item in args.snapshots:
        size, _, path = item.partition('=')
        snapshots[int(size)] = path
    for size, path in sorted(snapshots.items()):
        manifest = json.loads((Path(path) / 'manifest.json').read_text())
        small = size <= args.small_max_size
        queries, repeats = (args.queries, args.repeats) if small else (args.large_queries, args.large_repeats)
        entry = {'size': size, 'snapshot_id': manifest['snapshot_id'], 'queries': queries, 'repeats': repeats, 'arms': {}}
        per_arm = {}
        plan = [('A', scratch, 'full', 1), ('B', ROOT, 'full', 1), ('B_decided', ROOT, 'decided', 1)]
        plan += [(f'C_w{n}', ROOT, 'full', n) for n in WORKER_COUNTS if n in args.workers]
        for arm, root, detail, workers in plan:
            if arm not in args.arms and not (arm.startswith('C_w') and 'C' in args.arms):
                continue
            print(f'size {size} arm {arm} ...', file=sys.stderr, flush=True)
            result, record = run_worker(arm, root, path, size, queries, repeats, detail, args.memory_limit,
                                        args.budget_seconds, workers)
            if result is None:
                entry['arms'][arm] = {'status': 'failed', **record}
                continue
            entry['arms'][arm], per_arm[arm] = arm_statistics(result, record, size)
            print(f'  {arm}: p50 {entry["arms"][arm]["match_seconds"]["p50"]:.2f} s, max {entry["arms"][arm]["match_seconds"]["max"]:.2f} s, '
                  f'peak {record["peak_rss_bytes"] / 2**30:.2f} GiB', file=sys.stderr, flush=True)
        if 'A' in per_arm and 'B' in per_arm:
            entry['byte_identity_B_against_A'] = identity(per_arm['A'], per_arm['B'])
        entry['byte_identity_C_against_B'] = {
            arm: identity(per_arm['B'], per_arm[arm]) for arm in per_arm if arm.startswith('C_w') and 'B' in per_arm}
        for future in (*FUTURE_ARMS, ):
            entry['arms'][future] = {'status': 'not_run', 'description': FUTURE_ARMS[future]}
        entry['target_check_arm_B'] = target_check(entry)
        entry['target_check_arm_C'] = {arm: target_check(entry, arm) for arm in entry['arms'] if arm.startswith('C_w')}
        sizes.append(entry)
    shutil.rmtree(scratch, ignore_errors=True)
    return {'profile': PROFILE,
            'parameters': {'sizes': sorted(snapshots), 'queries': args.queries, 'repeats': args.repeats,
                           'large_queries': args.large_queries, 'large_repeats': args.large_repeats,
                           'small_max_size': args.small_max_size, 'threads': 1, 'memory_limit': args.memory_limit,
                           'chunk_patients': args.chunk_patients, 'arm_c_workers': sorted(args.workers)},
            'environment': bench.environment(1, args.memory_limit),
            'arms': {'A': arm_a, 'B': {'description': ARMS['B'], 'git_ref': 'this tree'},
                     'B_decided': {'description': ARMS['B_decided']},
                     'C': {'description': ARMS['C'], 'git_ref': 'this tree', 'workers': list(WORKER_COUNTS)},
                     **{k: {'status': 'not_run', 'description': v} for k, v in FUTURE_ARMS.items()}},
            'target': TARGET, 'sizes': sizes,
            'artifacts': {p: digest(p) for p in ARTIFACTS},
            'limitations': list(LIMITATIONS), 'contains_patient_level_data': False,
            'total_seconds': time.monotonic() - started}


def confirm(args):
    """Add `confirmation` to an existing report: arm C (and arm B once, for digests) at one size with more queries
    and repeats than the main run, so that p95 and the maximum rest on more than ten single measurements."""
    report = json.loads(args.output.read_text())
    (item,) = args.snapshots
    size, _, path = item.partition('=')
    size = int(size)
    manifest = json.loads((Path(path) / 'manifest.json').read_text())
    entry = next(e for e in report['sizes'] if e['size'] == size)
    if manifest['snapshot_id'] != entry['snapshot_id']:
        raise SystemExit(f'snapshot {manifest["snapshot_id"]} is not the report\'s {entry["snapshot_id"]}')
    arms, per_arm = {}, {}
    plan = [('B', 1)] + [(f'C_w{n}', n) for n in args.workers]
    for arm, workers in plan:
        repeats = 1 if arm == 'B' else args.large_repeats
        print(f'confirm size {size} arm {arm} ...', file=sys.stderr, flush=True)
        result, record = run_worker(arm, ROOT, path, size, args.large_queries, repeats, 'full', args.memory_limit,
                                    args.budget_seconds, workers)
        if result is None:
            arms[arm] = {'status': 'failed', **record}
            continue
        arms[arm], per_arm[arm] = arm_statistics(result, record, size)
        print(f'  {arm}: {arms[arm]["match_seconds"]}', file=sys.stderr, flush=True)
    t = TARGET
    checks = {}
    for arm in arms:
        if arm.startswith('C_w') and 'match_seconds' in arms[arm]:
            a = arms[arm]
            checks[arm] = {'p95_seconds_all_runs': a['all_runs_match_seconds']['p95'],
                           'max_seconds_all_runs': a['all_runs_match_seconds']['max'],
                           'p95_seconds_by_repeat': [e['p95'] for e in a['per_repeat_match_seconds']],
                           'max_seconds_by_repeat': [e['max'] for e in a['per_repeat_match_seconds']],
                           'p95_met_all_runs': a['all_runs_match_seconds']['p95'] <= t['match_seconds_p95_max'],
                           'max_met_all_runs': a['all_runs_match_seconds']['max'] <= t['match_seconds_max_max'],
                           'memory_met': a['query_process']['peak_rss_bytes'] < t['query_process_peak_rss_bytes_below']}
    report['confirmation'] = {
        'size': size, 'queries': args.large_queries, 'repeats': args.large_repeats, 'seeds': f'0..{args.large_queries - 1}',
        'note': 'Arm B ran once (digests and per-candidate reference); arm C at each count with the repeats stated. '
                '"all_runs" pools every (query, repeat) measurement; per-query figures elsewhere are medians over repeats.',
        'arms': arms, 'byte_identity_C_against_B': {a: identity(per_arm['B'], per_arm[a]) for a in per_arm if a.startswith('C_w') and 'B' in per_arm},
        'target_check': checks}
    report['artifacts'] = {p: digest(p) for p in ARTIFACTS}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(args.output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--snapshots', nargs='+', help='SIZE=SNAPSHOT_DIRECTORY, one per size')
    parser.add_argument('--arm-a-ref', default='7dc3364', help='git ref of the matcher before D21')
    parser.add_argument('--arms', nargs='+', default=['A', 'B', 'B_decided', 'C'])
    parser.add_argument('--workers', nargs='+', type=int, default=list(WORKER_COUNTS), help='arm C worker counts')
    parser.add_argument('--queries', type=int, default=50)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--small-max-size', type=int, default=10_000)
    parser.add_argument('--large-queries', type=int, default=10)
    parser.add_argument('--large-repeats', type=int, default=1)
    parser.add_argument('--memory-limit', default='4GB')
    parser.add_argument('--budget-seconds', type=float, default=4 * 3600, help='per arm and size')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/matcher-comparison-report.json')
    parser.add_argument('--confirm', action='store_true', help='add a confirmation run (arm C, one snapshot, --large-queries x '
                        '--large-repeats) to the existing report at --output')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--code-root', help=argparse.SUPPRESS)
    parser.add_argument('--snapshot', help=argparse.SUPPRESS)
    parser.add_argument('--size', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--detail', default='full', help=argparse.SUPPRESS)
    parser.add_argument('--arm-workers', type=int, default=1, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        args.worker_output.write_text(json.dumps(worker(args)))
        return 0
    if not args.snapshots:
        parser.error('--snapshots is required')
    if args.confirm:
        confirm(args)
        return 0
    from patterns import execution_store as es
    args.chunk_patients = es.CHUNK_PATIENTS
    report = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(args.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
