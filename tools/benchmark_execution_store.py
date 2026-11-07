#!/usr/bin/env python3
"""Scaling benchmark for the execution store (docs/execution-store.md, "Scaling").

For each cohort size it builds a snapshot and runs a fixed set of seeded queries through
`candidates()` and `match()`, each size in fresh subprocesses so that peak resident memory is
measured cleanly: one process generates, converts and builds (the build phase), another opens and
queries the snapshot (the query phase). The parent enforces a wall-time budget and a memory guard
and records a size that cannot finish as a finding, with the reason.

Cohorts. Distinct patients come from the synthetic generator with one seed per 48 patients,
relabelled per seed so ids are unique, up to DISTINCT_CAP. Beyond that the distinct pool is
replicated: copy k of a pool patient gets the id `<id>~r<k>` and every time shifted by a whole
number of days that depends only on k. Replication keeps the distributions realistic and adds no
variety, and a replica shares its source's evidence objects in memory (which makes the in-memory
document smaller than a real one). Queries are anchored on the first seed's patients and are the same
for every size. A patient's index window is derived from its pool patient's coverage and shifted
with its copy.

The report holds aggregates only. It is evidence for one machine and synthetic data.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import random
import resource
import signal
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROFILE = 'execution-store-scaling-1.0'
GENERATOR = ROOT / 'demos/mockups/synthetic/generate.py'
PER_COHORT = 48
DISTINCT_CAP = 10_000
SEED_BASE = 20261103  # the generator's default seed, so the first cohort is the committed one
DAY = 86_400_000_000
RSS_LIMIT_BYTES = 40 * 2**30
ARTIFACTS = ('tools/benchmark_execution_store.py', 'patterns/execution_store.py', 'patterns/synthetic_ingest.py',
             'patterns/trajectory_graph.py', 'demos/mockups/synthetic/generate.py')
LIMITATIONS = (
    'One machine (see environment); no other hardware, operating system or storage was measured.',
    'The data are synthetic. Event density, concept frequencies and sources reflect the generator, not a clinical record system.',
    'Beyond the distinct pool the cohort is replicated: copies repeat the pool patients with a whole-day time shift and add '
    'no variety. Concept posting sizes, candidate sets and match outcomes therefore scale as exact multiples, which a real '
    'population would not.',
    'A replica shares its source patient\'s evidence objects in memory, so the in-memory ingest of a replicated cohort is '
    'smaller than a real one of the same size; the staged and stored bytes are not.',
    'Queries are 50 seeded random queries from the differential test\'s generator, not a clinical query mix.',
    'Latencies are wall-clock times of one thread of Python and DuckDB with the thread setting stated; no tuning was attempted.',
    'Peak memory is the process\'s maximum resident set size, which includes the interpreter and DuckDB buffers.',
    'No exhaustive reference ran at scale; correctness at scale rests on the differential test at 48 patients and the '
    'optional check at 1,000.')


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def pct(values, q):
    """Nearest-rank percentile."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None


def summary(values):
    return {'p50': pct(values, .5), 'p95': pct(values, .95), 'max': max(values) if values else None}


def shift_for(k):
    """Whole-day shift of copy k, positive and depending on k alone."""
    return (1 + (k * 37) % 700) * DAY


def patient_ref(pid):
    """(pool patient id, copy number) from a benchmark patient id."""
    base, sep, k = pid.partition('~r')
    return base, int(k) if sep else 0


# Cohort construction.

def load_generator():
    spec = importlib.util.spec_from_file_location('benchmark_generate', GENERATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pool_ingest(size, generator=None, convert=None):
    """(ingest of min(size, DISTINCT_CAP) distinct patients, generation seconds, conversion seconds, cohorts,
    seeds the generator failed on and that were skipped)."""
    from patterns import execution_store as es
    from patterns.synthetic_ingest import synthetic_ingest
    generator = generator or load_generator()
    convert = convert or synthetic_ingest
    want = min(size, DISTINCT_CAP)
    cohorts = math.ceil(want / PER_COHORT)
    generating = converting = 0.0
    ingests, failed, offset = [], [], 0
    while len(ingests) < cohorts:
        index, seed = len(ingests), SEED_BASE + offset
        offset += 1
        started = time.perf_counter()
        try:
            cohort = json.loads(json.dumps(generator.serialise(generator.build(seed), seed), ensure_ascii=False))
        except Exception as exc:  # the generator cannot build every seed; such a seed is skipped and counted
            failed.append({'seed': seed, 'error': type(exc).__name__})
            generating += time.perf_counter() - started
            continue
        middle = time.perf_counter()
        generating += middle - started
        ingest = convert(cohort)
        try:
            es.validate_ingest(ingest)  # a cohort the store would refuse (a generator defect) is skipped too
        except es.IngestError as exc:
            failed.append({'seed': seed, 'error': exc.code})
            converting += time.perf_counter() - middle
            continue
        converting += time.perf_counter() - middle
        for p in ingest['patients']:
            p['patient_id'] = f"{index:04d}-{p['patient_id']}"
            p['clock_id'] = f"patient_{p['patient_id']}"
        ingests.append(ingest)
    first = ingests[0]
    skipped = {}
    patients = []
    for ingest in ingests:
        if ingest['closure'] != first['closure']:
            raise ValueError('the cohorts disagree on the concept closure')
        patients.extend(ingest['patients'])
        for k, v in ingest['provenance']['skipped'].items():
            skipped[k] = skipped.get(k, 0) + v
    merged = {**first, 'dataset_id': f'scaling-{size}',
              'provenance': {**first['provenance'], 'skipped': dict(sorted(skipped.items()))},
              'patients': patients[:want]}
    return merged, generating, converting, cohorts, failed


def shifted(patient, k):
    """Copy k of a pool patient: new id, whole-day shift, evidence shared with the source."""
    delta = shift_for(k)
    pid = f"{patient['patient_id']}~r{k}"
    def move(pair):
        return None if pair is None else [None if t is None else t + delta for t in pair]
    events = []
    for e in patient['events']:
        copy = {**e, 'start': move(e['start'])}
        if e.get('end') is not None:
            copy['end'] = move(e['end'])
        events.append(copy)
    coverage = [{**c, 'lo': c['lo'] + delta, 'hi': c['hi'] + delta} for c in patient['coverage']]
    out = {**patient, 'patient_id': pid, 'clock_id': f'patient_{pid}', 'events': events, 'coverage': coverage}
    if 'excluded' in patient:
        out['excluded'] = [{**x, 'start': move(x['start'])} for x in patient['excluded']]
    return out


def replicate(ingest, size):
    """The pool repeated up to `size` patients (the pool itself when it already has that many)."""
    pool = ingest['patients']
    if size <= len(pool):
        return ingest
    patients = list(pool)
    for i in range(len(pool), size):
        patients.append(shifted(pool[i % len(pool)], i // len(pool)))
    return {**ingest, 'patients': patients}


# The query set.

def query_inputs(seeds):
    """[(seed, query)] over the first cohort, and that cohort's ingest. The same for every size."""
    from patterns.test_execution_store import random_query
    ingest = pool_ingest(PER_COHORT)[0]
    ancestors = ingest['closure']['ancestors']
    holders = {}
    for p in ingest['patients']:
        for concept in {a for e in p['events'] for c in e['concepts'] for a in (c, *ancestors[c])}:
            holders[concept] = holders.get(concept, 0) + 1
    pool = sorted(c for c, n in holders.items() if n >= 2)
    return [(s, random_query(random.Random(s), pool, ingest)) for s in range(seeds)], ingest


def pool_windows(seed, bounds):
    """Index windows for the pool patients, one deterministic stream per (seed, patient)."""
    from patterns.test_execution_store import random_windows
    out = {}
    for pid in sorted(bounds):
        lo, hi = bounds[pid]
        stub = {'patients': [{'patient_id': pid, 'coverage': [{'lo': lo, 'hi': hi}] if lo is not None else []}]}
        out.update(random_windows(random.Random(f'{seed}:{pid}'), stub))
    return out


def expand_windows(pool, ids):
    """Windows of every patient: its pool patient's, shifted with its copy."""
    out = {}
    for pid in ids:
        base, k = patient_ref(pid)
        w = pool[base]
        out[pid] = w if w is None or k == 0 else {'start': w['start'] + shift_for(k), 'end': w['end'] + shift_for(k)}
    return out


# Workers: each runs in its own process.

def directory_bytes(path):
    return sum(f.stat().st_size for f in Path(path).rglob('*') if f.is_file())


def progress(path, phase, **extra):
    """Say which phase the worker is in and its peak memory so far, so a killed worker can be placed."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    Path(path).write_text(json.dumps({'phase': phase, 'peak_rss_bytes_so_far': peak, **extra}))


def build_worker(size, work, threads, marker):
    from patterns import execution_store as es
    started = time.perf_counter()
    progress(marker, 'generate_and_convert_distinct_pool')
    ingest, generating, converting, cohorts, failed = pool_ingest(size)
    progress(marker, 'replicate', distinct_patients=len(ingest['patients']))
    started_replication = time.perf_counter()
    ingest = replicate(ingest, size)
    replicating = time.perf_counter() - started_replication
    events = sum(len(p['events']) for p in ingest['patients'])
    prepared = time.perf_counter() - started
    progress(marker, 'build', patients=len(ingest['patients']), ingest_events=events, prepare_seconds=prepared)
    started = time.perf_counter()
    root = Path(work) / 'store'
    snapshot = es.build(ingest, root, threads=threads)
    built = time.perf_counter() - started
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    return {'distinct_patients': min(size, DISTINCT_CAP), 'cohorts_generated': cohorts, 'generator_seeds_failed': failed, 'patients': len(ingest['patients']),
            'ingest_events': events, 'generate_seconds': generating, 'convert_seconds': converting,
            'replicate_seconds': replicating, 'prepare_seconds': prepared, 'build_seconds': built,
            'snapshot_path': str(snapshot), 'snapshot_id': manifest['snapshot_id'],
            'snapshot_bytes': directory_bytes(snapshot),
            'tables': {n: t['rows'] for n, t in manifest['tables'].items()}}


def timed(function, *args):
    started = time.perf_counter()
    value = function(*args)
    return value, time.perf_counter() - started


def query_worker(snapshot, size, queries, repeats, threads, exhaustive):
    from patterns import execution_store as es
    inputs, _ = query_inputs(queries)
    opened = []
    for _ in range(2):  # the second open is warm; the first reads the files cold
        store, seconds = timed(es.open_store, snapshot, threads, True)
        opened.append(seconds)
        if len(opened) == 1:
            store.close()
    patients = store.query('SELECT patient_id FROM patients ORDER BY patient_idx')
    ids = [r[0] for r in patients]
    bounds = {pid: (lo, hi) for pid, lo, hi in store.query(
        "SELECT p.patient_id, min(c.lo), max(c.hi) FROM patients p LEFT JOIN coverage c USING (patient_idx) "
        "WHERE p.patient_id NOT LIKE '%~r%' GROUP BY p.patient_id")}
    runs = []
    first = {}
    pooled = {}
    for repeat in range(repeats):
        rows = []
        for seed, query in inputs:
            if seed not in pooled:
                pooled[seed] = pool_windows(seed, bounds)
            windows = expand_windows(pooled[seed], ids)
            found, t_candidates = timed(es.candidates, store, query)
            result, t_match = timed(es.match, store, query, windows)
            if repeat == 0:
                first[seed] = (query, windows, result)
            rows.append({'seed': seed, 'candidates_seconds': t_candidates, 'match_seconds': t_match,
                         'candidates': len(found['patient_idx']), 'counts': result['counts']})
        runs.append(rows)
    check = None
    if exhaustive:
        check = exhaustive_check(store, size, first, exhaustive)
    store.close()
    return {'open_verify_seconds_cold': opened[0], 'open_verify_seconds_warm': opened[1], 'runs': runs,
            'exhaustive_check': check}


def exhaustive_check(store, size, first, count):
    """Store results against exhaustive matching over every patient, for the first `count` queries."""
    from patterns import execution_store_reference as ref
    ingest = replicate(pool_ingest(size)[0], size)
    checked = mismatches = 0
    for seed in sorted(first)[:count]:
        query, windows, got = first[seed]
        want = ref.exhaustive_match(ingest, query, windows)
        for g, w in zip(got['results'], want, strict=True):
            checked += 1
            if g['patient_id'] != w['patient_id'] or g['status'] != w['status'] or \
                    (g.get('reason') != 'FILTERED' and g != w):
                mismatches += 1
    return {'queries': min(count, len(first)), 'patient_results': checked, 'mismatches': mismatches}


# The orchestrating process.

def current_rss(pid):
    try:
        out = subprocess.run(['ps', '-o', 'rss=', '-p', str(pid)], capture_output=True, text=True, timeout=5)
        return int(out.stdout.strip() or 0) * 1024
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 0


def run_phase(name, arguments, output, budget, rss_limit):
    """Run one worker; (result or None, record). The record has wall seconds, peak RSS and any failure."""
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', name, '--worker-output', str(output), *arguments]
    started = time.monotonic()
    child = subprocess.Popen(command, cwd=ROOT, stderr=subprocess.PIPE, text=True, start_new_session=True)
    failure, peak = None, 0
    while True:
        done, status, usage = os.wait4(child.pid, os.WNOHANG)
        if done:
            break
        elapsed = time.monotonic() - started
        peak = max(peak, current_rss(child.pid))
        if elapsed > budget:
            failure = {'reason': 'TIME_BUDGET', 'detail': f'exceeded {budget:.0f} s'}
        elif peak > rss_limit:
            failure = {'reason': 'MEMORY_GUARD', 'detail': f'resident memory reached {peak / 2**30:.1f} GiB, '
                                                           f'limit {rss_limit / 2**30:.0f} GiB'}
        if failure:
            os.killpg(child.pid, signal.SIGKILL)
            _, status, usage = os.wait4(child.pid, 0)
            break
        time.sleep(.5)
    stderr = child.stderr.read() if child.stderr else ''
    seconds = time.monotonic() - started
    maxrss = usage.ru_maxrss * (1 if sys.platform == 'darwin' else 1024)
    record = {'wall_seconds': seconds, 'peak_rss_bytes': max(maxrss, peak)}
    if failure is None and (not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0):
        failure = {'reason': 'WORKER_FAILED', 'detail': (stderr.strip().splitlines() or ['no message'])[-1][:300]}
    if failure:
        marker = Path(output).with_suffix('.progress')
        if marker.exists():
            failure['last_progress'] = json.loads(marker.read_text())
        return None, {**record, 'failure': failure}
    return json.loads(Path(output).read_text()), record


def query_statistics(result, patients):
    """Per-query medians over the repeats, then p50/p95/max across queries."""
    runs = result['runs']
    per_query = []
    for i in range(len(runs[0])):
        rows = [run[i] for run in runs]
        per_query.append({'seed': rows[0]['seed'],
                          'candidates_seconds': statistics.median(r['candidates_seconds'] for r in rows),
                          'match_seconds': statistics.median(r['match_seconds'] for r in rows),
                          'candidates': rows[0]['candidates'], 'counts': rows[0]['counts']})
    mix = {}
    for q in per_query:
        for status, n in q['counts'].items():
            mix[status] = mix.get(status, 0) + n
    pruning = [1 - q['candidates'] / patients for q in per_query]
    return {'queries': len(per_query), 'repeats': len(runs),
            'candidates_seconds': summary([q['candidates_seconds'] for q in per_query]),
            'match_seconds': summary([q['match_seconds'] for q in per_query]),
            'candidate_count': summary([q['candidates'] for q in per_query]),
            'pruning_rate': {**summary(pruning), 'mean': statistics.fmean(pruning)},
            'status_mix': dict(sorted(mix.items())),
            'candidates_seconds_per_repeat_p50': [pct([r['candidates_seconds'] for r in run], .5) for run in runs],
            'match_seconds_per_repeat_p50': [pct([r['match_seconds'] for r in run], .5) for run in runs],
            'per_query': [{'seed': q['seed'], 'candidates': q['candidates'], 'candidates_seconds': q['candidates_seconds'],
                           'match_seconds': q['match_seconds']} for q in per_query]}


def run_size(size, args, work):
    """The report entry for one size."""
    scratch = Path(work) / f'size-{size}'
    scratch.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    entry = {'size': size, 'status': 'completed'}
    built, record = run_phase('build', ['--size', str(size), '--work-dir', str(scratch), '--threads', str(args.threads)],
                              scratch / 'build.json', args.budget_seconds, RSS_LIMIT_BYTES)
    entry['build_phase'] = {k: v for k, v in record.items() if k != 'failure'}
    if built is None:
        entry.update(status='failed', failed_phase='build', failure=record['failure'])
        entry['wall_seconds'] = time.monotonic() - started
        return entry
    entry.update({k: v for k, v in built.items() if k != 'snapshot_path'})
    remaining = max(1.0, args.budget_seconds - (time.monotonic() - started))
    exhaustive = args.exhaustive if size <= args.exhaustive_max_size else 0
    queried, record = run_phase('query', ['--size', str(size), '--snapshot', built['snapshot_path'], '--queries', str(args.queries),
                                          '--repeats', str(args.repeats), '--threads', str(args.threads),
                                          '--exhaustive', str(exhaustive)],
                                scratch / 'query.json', remaining, RSS_LIMIT_BYTES)
    entry['query_phase'] = {k: v for k, v in record.items() if k != 'failure'}
    if queried is None:
        entry.update(status='failed', failed_phase='query', failure=record['failure'])
    else:
        entry['open_verify_seconds'] = {'cold': queried['open_verify_seconds_cold'],
                                        'warm': queried['open_verify_seconds_warm']}
        entry['queries'] = query_statistics(queried, built['patients'])
        if queried['exhaustive_check']:
            entry['exhaustive_check'] = queried['exhaustive_check']
    entry['wall_seconds'] = time.monotonic() - started
    if not args.keep:
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)
    return entry


def cpu_model():
    if sys.platform == 'darwin':
        out = subprocess.run(['sysctl', '-n', 'machdep.cpu.brand_string'], capture_output=True, text=True)
        return out.stdout.strip() or platform.processor()
    try:
        for line in Path('/proc/cpuinfo').read_text().splitlines():
            if line.startswith('model name'):
                return line.split(':', 1)[1].strip()
    except OSError:
        pass
    return platform.processor()


def memory_bytes():
    try:
        if sys.platform == 'darwin':
            return int(subprocess.run(['sysctl', '-n', 'hw.memsize'], capture_output=True, text=True).stdout)
        return os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES')
    except (OSError, ValueError):
        return None


def environment(threads):
    import duckdb
    return {'platform': platform.platform(), 'machine': platform.machine(), 'cpu': cpu_model(),
            'logical_cpus': os.cpu_count(), 'memory_bytes': memory_bytes(),
            'python': platform.python_version(), 'duckdb': duckdb.__version__,
            'duckdb_threads': threads, 'python_threads_for_matching': 1}


def benchmark(args):
    started = time.monotonic()
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix='store-scaling-', dir='/tmp'))
    work.mkdir(parents=True, exist_ok=True)
    entries, stopped = [], None
    for size in args.sizes:
        if stopped:
            entries.append({'size': size, 'status': 'not_attempted',
                            'reason': f'size {stopped["size"]} failed ({stopped["failure"]["reason"]}); a larger size would hold '
                                      'more patients in the same in-memory ingest'})
            continue
        print(f'size {size} ...', file=sys.stderr, flush=True)
        entry = run_size(size, args, work)
        entries.append(entry)
        print(f'size {size}: {entry["status"]} in {entry["wall_seconds"]:.0f} s', file=sys.stderr, flush=True)
        if entry['status'] == 'failed':
            stopped = entry
    return {'profile': PROFILE, 'generator_seed_base': SEED_BASE,
            'parameters': {'sizes': args.sizes, 'queries': args.queries, 'repeats': args.repeats,
                           'budget_seconds_per_size': args.budget_seconds, 'rss_limit_bytes': RSS_LIMIT_BYTES,
                           'threads': args.threads, 'distinct_pool_cap': DISTINCT_CAP, 'patients_per_cohort': PER_COHORT},
            'environment': environment(args.threads),
            'cohort_construction': {
                'distinct': f'one generator seed per {PER_COHORT} patients (seed {SEED_BASE} + i, skipping seeds the generator cannot build), relabelled per cohort, up to '
                            f'{DISTINCT_CAP:,} distinct patients',
                'replicated': 'beyond the distinct pool, copy k of pool patient j is `<id>~r<k>` with every time shifted by '
                              f'(1 + (37k mod 700)) whole days; no new variety, and evidence is shared in memory',
                'queries': 'seeded random_query / random_windows from patterns/test_execution_store.py, anchored on the '
                           'first cohort, identical for every size; windows follow the pool patient and its shift'},
            'sizes': entries,
            'artifacts': {p: digest(p) for p in ARTIFACTS},
            'limitations': list(LIMITATIONS),
            'contains_patient_level_data': False,
            'total_seconds': time.monotonic() - started}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--sizes', type=lambda s: [int(x) for x in s.split(',')], default=[1000, 10000, 100000])
    parser.add_argument('--queries', type=int, default=50)
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/execution-store-scaling-report.json')
    parser.add_argument('--work-dir', default='/tmp/execution-store-scaling', help='scratch for snapshots (not tracked)')
    parser.add_argument('--budget-seconds', type=float, default=1800, help='wall time allowed per size')
    parser.add_argument('--threads', type=int, default=1, help='DuckDB threads for build and open')
    parser.add_argument('--exhaustive', type=int, default=5, help='queries compared with exhaustive matching at small sizes')
    parser.add_argument('--exhaustive-max-size', type=int, default=1000)
    parser.add_argument('--keep', action='store_true', help='keep each size\'s snapshot')
    parser.add_argument('--worker', choices=('build', 'query'), help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--size', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--snapshot', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if args.worker == 'build':
            result = build_worker(args.size, args.work_dir, args.threads, args.worker_output.with_suffix('.progress'))
        else:
            result = query_worker(args.snapshot, args.size, args.queries, args.repeats, args.threads, args.exhaustive)
        args.worker_output.write_text(json.dumps(result))
        return 0
    if min(args.sizes) < PER_COHORT:
        parser.error(f'sizes start at {PER_COHORT}, the first cohort the queries are anchored on')
    report = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(args.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
