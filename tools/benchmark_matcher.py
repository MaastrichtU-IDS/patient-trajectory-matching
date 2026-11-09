#!/usr/bin/env python3
"""Matcher comparison for the execution store (docs/execution-store.md, "Matching cost"; D19, D21).

The same seeded queries and windows as the scaling benchmark are run through `execution_store.match`
of different code (arms) over the same snapshots, each arm and size in a fresh process so that
peak resident memory is the query process's alone:

  A  the matcher as it stood before D21 (a git ref, extracted to a scratch directory and imported
     from there): one query loads every candidate's events with an IN list, then matches.
  B  this tree: candidates loaded and matched in chunks, a prepared query, trusted events, windows
     checked as plain integers, comparability in SQL, the engine's rewritten pairwise kernel, and time
     pruning on the posting summaries (D25), their end summaries included (D26) (results unchanged).
     `B_decided` is B with `detail='decided'`; `B_unpruned` is B with the pruning turned off, a same-run
     baseline whose digests must equal B's.
  C  this tree with `workers=N` (N = 1, 2, 4, 8, 14): the engine's work units run in a persistent pool of
     spawned processes; recorded as `C_w1` ... `C_w14`.  `C_w1` is the unchanged single-process path (a
     control).  The pool is started and warmed before the first query (its start-up is recorded
     separately, as the one cold-start figure), so the timings are of a warm pool.  The query process's
     peak memory is its own (`getrusage(RUSAGE_SELF)`), the largest worker's is reported beside it; their
     sum would double-count the shared page cache and is not given.
  D, D'  Rust single-threaded, Rust parallel (D21): later changes, recorded as `not_run` so the report
     structure is already in place for them.

Per query it records wall seconds of `match` (median over repeats), the seconds spent inside the
engine (`trajectory_graph` match calls), inside the event loader and inside time pruning, the candidate
count, how many candidates with a window time pruning proved `none` (and, in the same run, how many the
start summaries alone would have, D26), and a digest of the full result (plan, candidates, counts, every
patient's result, canonical JSON), so that arms are compared for byte identity query by query, not only on
the small cohort the unit tests use. Snapshots are built by `tools/benchmark_execution_store.py --worker
build` (or kept by its `--keep`).

Arm A's code reads an older store profile than this tree writes (2.0 against 3.0 since D26, which moved
columns, so no relabelled view of the same files can serve it). It reads its own snapshot of the same cohort
instead, built by its own builder and converter from the same generator output (`--build-arm-a`, then
`--arm-a-snapshots`); the report records, per size, that the two snapshots hold the same rows in every
column matching reads (`arm_a_snapshot.same_rows`), and the per-query result digests are compared as before.

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
import re
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
    'A': 'matcher before D21: one IN-list load of every candidate, validate every patient, one result dict per patient; '
         'on its own store 2.0 snapshot of the same cohort, built by its own builder (D26)',
    'B': 'D21 step 1: chunked load and match, PreparedQuery, trusted events, integer window checks, SQL comparability; '
         'then the engine kernel: closed-form pair and unary tests over per-patient records, Times and events built '
         "without per-call overhead (results unchanged); then time pruning on the posting summaries (D25): a candidate "
         "the summaries prove `none` gets the engine's own `none` without loading its events (results unchanged); "
         "store 3.0 (D26): typed record columns, evidence in its own table, end summaries that let `during`'s grace "
         "prune; detail='full'",
    'B_decided': "arm B with detail='decided'",
    'B_unpruned': 'arm B with time pruning on the posting summaries (D25) turned off (`execution_store._PRUNE = False`): '
                  'the same tree, as a same-run baseline; its results must equal B\'s byte for byte',
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
    'Arm A is the code of the stated git ref extracted and imported from a scratch directory. It reads execution store '
    '2.0, so it reads its own snapshot of each size, built by its own builder and converter from the same generator '
    'output (`arm_a_snapshot` in each size entry, with a check that both snapshots hold the same rows in every column '
    'matching reads); the other arms read the 3.0 snapshot. The snapshots were built by the builder at the ref the '
    'report states, not by the final tree.',
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

# Arm A's own snapshot (D26): the same cohort through arm A's builder, and a check that it holds the same rows.

# The columns matching reads, as store 2.0 named them; every later profile still holds them, unchanged.
READ_COLUMNS = {
    'patients': ('patient_idx', 'patient_id', 'bucket'),
    'events': ('patient_idx', 'event_id', 'kind', 'source', 'clock_id', 'start_lo', 'start_hi', 'end_lo', 'end_hi',
               'ongoing'),
    'event_concepts': ('patient_idx', 'event_id', 'concept', 'basis'),
    'concept_closure': ('concept', 'ancestor', 'basis', 'version', 'concept_basis', 'ancestor_basis'),
    'concept_postings': ('concept', 'patient_idx', 'n', 'min_start_lo', 'max_start_hi'),
    'coverage': ('patient_idx', 'source', 'clock_id', 'lo', 'hi', 'basis'),
    'source_concepts': ('source', 'concept', 'basis'),
    'excluded_records': ('patient_idx', 'source', 'concept', 'start_lo', 'start_hi', 'reason', 'n'),
}


def read_digests(snapshot, memory_limit):
    """{table: content digests} of a snapshot's tables as `READ_COLUMNS` names them, by this tree's canonical form,
    read from the Parquet files directly (any profile that holds those columns)."""
    from unittest.mock import patch
    from patterns import execution_store as es
    snapshot = Path(snapshot)
    db = es._connect(':memory:', 1, memory_limit, tempfile.mkdtemp(prefix='matcher-digest-'))
    try:
        types = dict(c for cols in es.TABLES.values() for c in cols)
        for name, cols in READ_COLUMNS.items():
            files = snapshot / name / '**' / '*.parquet' if name in es.PARTITIONED else snapshot / f'{name}.parquet'
            extra = ', bucket' if name in es.PARTITIONED else ''
            db.execute(f"CREATE VIEW {name} AS SELECT {', '.join(cols)}{extra} FROM read_parquet({es._lit(files)}, "
                       'hive_partitioning = false)')
        tables = {name: tuple((c, types[c]) for c in cols) for name, cols in READ_COLUMNS.items()}
        with patch.dict(es.TABLES, tables, clear=True):
            return {name: {k: t[k] for k in ('rows', 'content_digest')} for name, t in es.content_digests(db).items()}
    finally:
        db.close()


def same_rows(arm_a_snapshot, snapshot, memory_limit):
    """Whether arm A's snapshot holds the same rows as the snapshot the other arms read, table by table, in every
    column matching reads (`READ_COLUMNS`), with each side's row count and digest."""
    mine, theirs = read_digests(snapshot, memory_limit), read_digests(arm_a_snapshot, memory_limit)
    return {'columns': {n: list(c) for n, c in READ_COLUMNS.items()},
            'tables': {n: {'equal': mine[n] == theirs[n], 'rows': mine[n]['rows'], 'arm_a_rows': theirs[n]['rows'],
                           'content_digest': mine[n]['content_digest'],
                           'arm_a_content_digest': theirs[n]['content_digest']} for n in READ_COLUMNS},
            'all_equal': mine == theirs}


def import_arm(code_root):
    """Put the arm's tree first on sys.path (helpers come from this tree, without letting it in front) and return this
    tree's benchmark helpers, loaded from their file."""
    code_root = str(Path(code_root).resolve())
    sys.path[:] = [code_root] + [p for p in sys.path if Path(p or '.').resolve() != ROOT and p != str(ROOT / 'tools')] + [str(ROOT)]
    spec = importlib.util.spec_from_file_location('benchmark_execution_store_helpers', ROOT / 'tools/benchmark_execution_store.py')
    bench = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bench)
    from patterns import execution_store as es
    if not Path(es.__file__).resolve().is_relative_to(Path(code_root)):
        raise RuntimeError(f'imported {es.__file__}, not from {code_root}')
    return bench, es


def build_worker(args):
    """Build one size's snapshot with the arm's own builder and converter, from this tree's generator."""
    bench, es = import_arm(args.code_root)
    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    out = bench.build_worker(args.size, work, 1, args.memory_limit, args.worker_output.with_suffix('.progress'))
    (work / 'pool.ndjson').unlink(missing_ok=True)
    return {**{k: v for k, v in out.items() if k != 'build_stages'}, 'code': es.__file__, 'profile': es.PROFILE}


def build_arm_a(args):
    """`--build-arm-a SIZE=DIRECTORY ...`: arm A's own snapshots, each in a fresh process; prints SIZE=SNAPSHOT."""
    scratch = Path(tempfile.mkdtemp(prefix='matcher-arm-a-build-'))
    try:
        commit = extract_ref(args.arm_a_ref, scratch)
        for item in args.build_arm_a:
            size, _, directory = item.partition('=')
            out = scratch / f'build-{size}.json'
            command = [sys.executable, str(Path(__file__).resolve()), '--build-worker', '--code-root', str(scratch),
                       '--size', size, '--work-dir', directory, '--memory-limit', args.memory_limit,
                       '--worker-output', str(out)]
            print(f'arm A ({args.arm_a_ref}, {commit[:7]}) build of {size} ...', file=sys.stderr, flush=True)
            subprocess.run(command, cwd=ROOT, check=True)
            built = json.loads(out.read_text())
            print(f"{size}={built['snapshot_path']}", flush=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def rss_now():
    out = subprocess.run(['ps', '-o', 'rss=', '-p', str(os.getpid())], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) * 1024


def worker(args):
    # `patterns` comes from the arm's tree; the helpers are this tree's, imported without letting them
    # put this tree in front.
    bench, es = import_arm(args.code_root)
    from patterns import trajectory_graph as tg
    engine = {'seconds': 0.0}
    loading = {'seconds': 0.0}
    pruning = {'seconds': 0.0, 'calls': []}
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
    pruner = getattr(es, '_prune', None)  # time pruning (D25), absent in older arms
    def timed_prune(store, pq, live, windows_by_idx, *a, **k):
        t = time.perf_counter()
        try:
            gone = pruner(store, pq, live, windows_by_idx, *a, **k)
        finally:
            pruning['seconds'] += time.perf_counter() - t
        pruning['calls'].append((store, pq, live, windows_by_idx, len(gone)))
        return gone
    if pruner is not None:
        es._prune = timed_prune
    def starts_only(call):
        """How many of a pruning call's candidates the start summaries alone prove (D26), outside the timing."""
        store, pq, live, windows_by_idx, _ = call
        if not hasattr(es, '_PRUNE_ENDS'):
            return None
        saved, es._PRUNE_ENDS = es._PRUNE_ENDS, False
        try:
            return len(pruner(store, pq, live, windows_by_idx))
        finally:
            es._PRUNE_ENDS = saved
    inputs, _ = bench.query_inputs(args.queries)
    if args.no_prune:
        if not hasattr(es, '_PRUNE'):
            raise RuntimeError(f'{es.__file__} has no time pruning to turn off')
        es._PRUNE = False
    if args.no_prune_ends:  # time pruning on the start summaries alone (D26), for a pilot
        es._PRUNE_ENDS = False
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    pool = None
    try:  # the store and any pool are closed however the queries end (D25 review)
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
                engine['seconds'] = loading['seconds'] = pruning['seconds'] = 0.0
                pruning['calls'].clear()
                started = time.perf_counter()
                result = es.match(store, query, windows, **kwargs)
                seconds = time.perf_counter() - started
                row = {'seed': seed, 'match_seconds': seconds, 'engine_seconds': engine['seconds'],
                       'load_seconds': loading['seconds'], 'candidates': candidates, 'counts': result['counts'],
                       'listed_results': len(result['results'])}
                if pruner is not None and not args.no_prune:
                    row['prune_seconds'] = pruning['seconds']
                    row['candidates_with_window'] = sum(len(c[2]) for c in pruning['calls'])
                    row['pruned'] = sum(c[4] for c in pruning['calls'])
                    if repeat == 0:
                        counted = [starts_only(c) for c in pruning['calls']]
                        row['pruned_by_start_summaries'] = None if None in counted else sum(counted)
                pruning['calls'].clear()
                if repeat == 0:
                    row['digest'] = result_digest(result) if args.detail == 'full' else None
                rows.append(row)
                del result, windows
                gc.collect()
            runs.append(rows)
        out = {'runs': runs, 'rss_after_open_bytes': baseline, 'code': es.__file__, 'profile': es.PROFILE,
               'engine_wrapper': name, 'detail': args.detail}
        if pool_info:
            pool_info['worker_peak_rss_bytes'] = pool.worker_peak_rss
            pool_info['worker_memory_limit'] = pool.memory_limit
            pool_info['parent_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
                1 if sys.platform == 'darwin' else 1024)
            out['pool'] = pool_info
    finally:
        if pool is not None:
            es.close_pools()
        store.close()
    return out


def run_worker(arm, code_root, snapshot, size, queries, repeats, detail, memory_limit, budget, workers=1, prune=True):
    out = Path(tempfile.mkdtemp(prefix='matcher-bench-')) / 'out.json'
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--code-root', str(code_root), '--snapshot',
               str(snapshot), '--size', str(size), '--queries', str(queries), '--repeats', str(repeats), '--detail', detail,
               '--memory-limit', memory_limit, '--arm-workers', str(workers), '--worker-output', str(out)]
    command += [] if prune else ['--no-prune']
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
    pruned = 'pruned' in runs[0][0] if runs[0] else False  # time pruning ran, and was counted (D26)
    for i in range(len(runs[0])):
        rows = [run[i] for run in runs]
        timed_keys = ('match_seconds', 'engine_seconds', 'load_seconds') + (('prune_seconds',) if pruned else ())
        per_query.append({k: statistics.median(r[k] for r in rows) for k in timed_keys}
                         | {'seed': rows[0]['seed'], 'candidates': rows[0]['candidates'], 'counts': rows[0]['counts'],
                            'digest': rows[0].get('digest'), 'listed_results': rows[0]['listed_results']}
                         | ({k: rows[0][k] for k in ('candidates_with_window', 'pruned', 'pruned_by_start_summaries')}
                            if pruned else {}))
    candidates = sum(q['candidates'] for q in per_query)
    us = lambda key: 1e6 * sum(q[key] for q in per_query) / candidates if candidates else None
    pruning = None
    if pruned:
        live = sum(q['candidates_with_window'] for q in per_query)
        gone = sum(q['pruned'] for q in per_query)
        starts = [q['pruned_by_start_summaries'] for q in per_query]
        starts = None if None in starts else sum(starts)
        pruning = {'candidates_with_window': live, 'pruned': gone, 'pruned_share': gone / live if live else None,
                   'pruned_by_start_summaries_alone': starts,
                   'pruned_share_by_start_summaries_alone': starts / live if live and starts is not None else None,
                   'pruned_only_by_end_summaries': None if starts is None else gone - starts,
                   'seconds': sum(q['prune_seconds'] for q in per_query),
                   'microseconds_per_candidate_with_window':
                       1e6 * sum(q['prune_seconds'] for q in per_query) / live if live else None,
                   'note': 'pruned counts come from the first repeat; "start summaries alone" re-runs the pruning of '
                           'each query with the end summaries off (execution_store._PRUNE_ENDS = False) outside the '
                           'timing, on the same candidates and windows'}
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
            'code': result['code'], 'store_profile': result.get('profile'), 'detail': result['detail'],
            **({'pruning': pruning} if pruning else {}),
            'per_query': [{k: q[k] for k in ('seed', 'candidates', 'match_seconds', 'engine_seconds', 'load_seconds',
                                              'listed_results', 'prune_seconds', 'candidates_with_window', 'pruned',
                                              'pruned_by_start_summaries') if k in q} for q in per_query]}, per_query


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
    arm_a_snapshots = {int(size): path for size, _, path in (item.partition('=') for item in args.arm_a_snapshots or ())}
    arm_a_profile = re.search(r"^PROFILE = '([^']+)'", (scratch / 'patterns/execution_store.py').read_text(), re.M).group(1)
    arm_a['store_profile'] = arm_a_profile
    for size, path in sorted(snapshots.items()):
        manifest = json.loads((Path(path) / 'manifest.json').read_text())
        small = size <= args.small_max_size
        queries, repeats = (args.queries, args.repeats) if small else (args.large_queries, args.large_repeats)
        entry = {'size': size, 'snapshot_id': manifest['snapshot_id'], 'queries': queries, 'repeats': repeats, 'arms': {}}
        # Arm A reads its own snapshot of the same cohort when its store profile is older (D26).
        arm_a_path = arm_a_snapshots.get(size, path)
        if size in arm_a_snapshots:
            own = json.loads((Path(arm_a_path) / 'manifest.json').read_text())
            print(f'size {size}: comparing arm A\'s snapshot with the others\' ...', file=sys.stderr, flush=True)
            started_check = time.monotonic()
            entry['arm_a_snapshot'] = {'snapshot_id': own['snapshot_id'], 'profile': own['profile'],
                                       'input_digest': own['input_digest'],
                                       'built_by': f'arm A\'s own builder and converter at {args.arm_a_ref} '
                                                   '(benchmark_matcher.py --build-arm-a), from this tree\'s generator',
                                       'same_rows': same_rows(arm_a_path, path, args.memory_limit)}
            entry['arm_a_snapshot']['same_rows']['seconds'] = time.monotonic() - started_check
        per_arm = {}
        plan = [('A', scratch, 'full', 1, True), ('B', ROOT, 'full', 1, True), ('B_unpruned', ROOT, 'full', 1, False),
                ('B_decided', ROOT, 'decided', 1, True)]
        plan += [(f'C_w{n}', ROOT, 'full', n, True) for n in WORKER_COUNTS if n in args.workers]
        for arm, root, detail, workers, prune in plan:
            if arm not in args.arms and not (arm.startswith('C_w') and 'C' in args.arms):
                continue
            target = arm_a_path if arm == 'A' else path
            if arm == 'A' and json.loads((Path(target) / 'manifest.json').read_text())['profile'] != arm_a_profile:
                entry['arms'][arm] = {'status': 'not_run', 'reason': f'ARM_A_SNAPSHOT_PROFILE: arm A reads {arm_a_profile}; '
                                                                     'give its own snapshot with --arm-a-snapshots'}
                continue
            print(f'size {size} arm {arm} ...', file=sys.stderr, flush=True)
            result, record = run_worker(arm, root, target, size, queries, repeats, detail, args.memory_limit,
                                        args.budget_seconds, workers, prune)
            if result is None:
                entry['arms'][arm] = {'status': 'failed', **record}
                continue
            entry['arms'][arm], per_arm[arm] = arm_statistics(result, record, size)
            print(f'  {arm}: p50 {entry["arms"][arm]["match_seconds"]["p50"]:.2f} s, max {entry["arms"][arm]["match_seconds"]["max"]:.2f} s, '
                  f'peak {record["peak_rss_bytes"] / 2**30:.2f} GiB', file=sys.stderr, flush=True)
        if 'A' in per_arm and 'B' in per_arm:
            entry['byte_identity_B_against_A'] = identity(per_arm['A'], per_arm['B'])
        if 'B_unpruned' in per_arm and 'B' in per_arm:
            entry['byte_identity_B_against_B_unpruned'] = identity(per_arm['B_unpruned'], per_arm['B'])
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
                     'B_unpruned': {'description': ARMS['B_unpruned'], 'git_ref': 'this tree'},
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
    plan = [('B', 1, True), ('B_unpruned', 1, False)] + [(f'C_w{n}', n, True) for n in args.workers]
    for arm, workers, prune in plan:
        if arm == 'B_unpruned' and 'B_unpruned' not in args.arms:
            continue
        repeats = 1 if arm.startswith('B') else args.large_repeats
        print(f'confirm size {size} arm {arm} ...', file=sys.stderr, flush=True)
        result, record = run_worker(arm, ROOT, path, size, args.large_queries, repeats, 'full', args.memory_limit,
                                    args.budget_seconds, workers, prune)
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
        'note': 'Arms B and B_unpruned ran once (digests and per-candidate reference); arm C at each count with the '
                'repeats stated. "all_runs" pools every (query, repeat) measurement; per-query figures elsewhere are medians '
                'over repeats.',
        'arms': arms, 'byte_identity_C_against_B': {a: identity(per_arm['B'], per_arm[a]) for a in per_arm if a.startswith('C_w') and 'B' in per_arm},
        **({'byte_identity_B_against_B_unpruned': identity(per_arm['B_unpruned'], per_arm['B'])}
           if 'B_unpruned' in per_arm and 'B' in per_arm else {}),
        'target_check': checks}
    report['artifacts'] = {p: digest(p) for p in ARTIFACTS}
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(args.output)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--snapshots', nargs='+', help='SIZE=SNAPSHOT_DIRECTORY, one per size')
    parser.add_argument('--arm-a-ref', default='7dc3364', help='git ref of the matcher before D21')
    parser.add_argument('--arm-a-snapshots', nargs='+', help='SIZE=SNAPSHOT_DIRECTORY of arm A\'s own snapshots (D26), '
                        'built by --build-arm-a; without one, arm A reads the size\'s snapshot if it has arm A\'s profile')
    parser.add_argument('--build-arm-a', nargs='+', metavar='SIZE=DIRECTORY',
                        help='build arm A\'s own snapshot of each size with its own builder and converter, under DIRECTORY')
    parser.add_argument('--arms', nargs='+', default=['A', 'B', 'B_unpruned', 'B_decided', 'C'])
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
    parser.add_argument('--no-prune', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--no-prune-ends', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--build-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--work-dir', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        args.worker_output.write_text(json.dumps(worker(args)))
        return 0
    if args.build_worker:
        args.worker_output.write_text(json.dumps(build_worker(args)))
        return 0
    if args.build_arm_a:
        build_arm_a(args)
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
