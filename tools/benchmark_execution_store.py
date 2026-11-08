#!/usr/bin/env python3
"""Scaling benchmark for the execution store (docs/execution-store.md, "Scaling").

For each cohort size it builds a snapshot and runs a fixed set of seeded queries through
`candidates()` and `match()`, each size in fresh subprocesses so that peak resident memory is
measured cleanly: one process generates, converts and builds (the build phase), another opens and
queries the snapshot (the query phase). The parent enforces a wall-time budget and a memory guard
and records a size that cannot finish as a finding, with the reason.

The build streams. The distinct pool is generated one generator seed at a time and written to an
ndjson file, one patient per line; the build reads it back a line at a time, replicating as it
goes, and feeds `execution_store.StoreBuilder`, so no process holds the cohort. The builder's own
stage callback records time and resident memory after each stage of `finish()`, and resident memory
is sampled while patients are added.

Cohorts. Distinct patients come from the synthetic generator with one seed per 48 patients,
relabelled per seed so ids are unique, up to DISTINCT_CAP. Beyond that the distinct pool is
replicated: copy k of a pool patient gets the id `<id>~r<k>` and every time shifted by a whole
number of days that depends only on k. Replication keeps the distributions realistic and adds no
variety. Queries are anchored on the first seed's patients and are the same for every size. A
patient's index window is derived from its pool patient's coverage and shifted with its copy.

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

PROFILE = 'execution-store-scaling-2.0'
GENERATOR = ROOT / 'demos/mockups/synthetic/generate.py'
PER_COHORT = 48
DISTINCT_CAP = 10_000
SEED_BASE = 20261103  # the generator's default seed, so the first cohort is the committed one
DAY = 86_400_000_000
RSS_LIMIT_BYTES = 40 * 2**30
DEFAULT_MEMORY_LIMIT = '4GB'
# Resident memory is sampled every this many patients while they are added.
ADD_SAMPLE_EVERY = 1000
ARTIFACTS = ('tools/benchmark_execution_store.py', 'patterns/execution_store.py', 'patterns/synthetic_ingest.py',
             'patterns/trajectory_graph.py', 'demos/mockups/synthetic/generate.py',
             'patterns/test_execution_store.py', 'patterns/execution_store_reference.py', 'patterns/temporal_stn.py',
             'schemas/execution-store-manifest.schema.json')
LIMITATIONS = (
    'One machine (see environment); no other hardware, operating system or storage was measured.',
    'The data are synthetic. Event density, concept frequencies and sources reflect the generator, not a clinical record system.',
    'Beyond the distinct pool the cohort is replicated: copies repeat the pool patients with a whole-day time shift and add '
    'no variety. Concept posting sizes, candidate sets and match outcomes therefore scale as exact multiples, which a real '
    'population would not.',
    'Queries are 50 seeded random queries from the differential test\'s generator, not a clinical query mix.',
    'Latencies are wall-clock times of one thread of Python and DuckDB with the thread setting stated; no tuning was attempted.',
    'Above the profile size, the query phase runs fewer queries and repeats (see parameters), so its latency percentiles '
    'rest on fewer samples.',
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


def generate_pool(size, sink, generator=None):
    """Generate min(size, DISTINCT_CAP) distinct patients one generator seed at a time, passing each
    converted, relabelled patient to `sink`. Returns (header, provenance, generation seconds, conversion
    seconds, cohorts, seeds the generator failed on and that were skipped, events per patient)."""
    from patterns import execution_store as es
    from patterns import synthetic_ingest as si
    generator = generator or load_generator()
    want = min(size, DISTINCT_CAP)
    cohorts = math.ceil(want / PER_COHORT)
    generating = converting = 0.0
    header = provenance = None
    failed, skipped, events, offset, index, written = [], {}, [], 0, 0, 0
    while index < cohorts:
        seed = SEED_BASE + offset
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
        own, why = si.synthetic_header(cohort), {}
        patients = list(si.iter_patients(cohort, why))  # one cohort of 48 at a time
        try:  # a cohort the store would refuse (a generator defect) is skipped too
            for p in patients:
                es.validate_patient(p, own['closure']['ancestors'])
        except es.IngestError as exc:
            failed.append({'seed': seed, 'error': exc.code})
            converting += time.perf_counter() - middle
            continue
        if header is None:
            header, provenance = own, si.synthetic_provenance(cohort, {})
        elif own['closure'] != header['closure']:
            raise ValueError('the cohorts disagree on the concept closure')
        for k, v in why.items():
            skipped[k] = skipped.get(k, 0) + v
        converting += time.perf_counter() - middle
        for p in patients:
            if written == want:
                break
            p['patient_id'] = f"{index:04d}-{p['patient_id']}"
            p['clock_id'] = f"patient_{p['patient_id']}"
            events.append(len(p['events']))
            sink(p)
            written += 1
        index += 1
    header = {**header, 'dataset_id': f'scaling-{size}'}
    provenance = {**provenance, 'skipped': dict(sorted(skipped.items()))}
    return header, provenance, generating, converting, cohorts, failed, events


def pool_ingest(size, generator=None):
    """(ingest document of min(size, DISTINCT_CAP) distinct patients, generation seconds, conversion seconds,
    cohorts, failed seeds), in memory: for the query anchors and the exhaustive check at small sizes."""
    patients = []
    header, provenance, generating, converting, cohorts, failed, _ = generate_pool(size, patients.append, generator)
    return {**header, 'provenance': provenance, 'patients': patients}, generating, converting, cohorts, failed


def write_pool(size, path):
    """The distinct pool as ndjson at `path`, one patient per line; the rest of generate_pool's result."""
    with open(path, 'w', encoding='utf-8') as out:
        def sink(p):
            out.write(json.dumps(p, ensure_ascii=False, separators=(',', ':')) + '\n')
        return generate_pool(size, sink)


def stream_patients(path, pool_size, size):
    """`size` patients read back from the pool file a line at a time: the pool, then copy 1, 2, ... of it."""
    produced = copy = 0
    while produced < size:
        with open(path, encoding='utf-8') as lines:
            for line in lines:
                if produced == size:
                    return
                patient = json.loads(line)
                yield patient if copy == 0 else shifted(patient, copy)
                produced += 1
        copy += 1
        if copy * pool_size != produced:
            raise ValueError('the pool file changed while it was read')


def shifted(patient, k):
    """Copy k of a pool patient: new id, whole-day shift, evidence shared with the source object."""
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


def build_worker(size, work, threads, memory_limit, marker):
    """Generate the distinct pool to disk, then stream every patient into `StoreBuilder`."""
    from patterns import execution_store as es
    work = Path(work)
    progress(marker, 'generate_and_convert_distinct_pool')
    pool_path = work / 'pool.ndjson'
    header, provenance, generating, converting, cohorts, failed, per_patient = write_pool(size, pool_path)
    per_patient.sort()
    stages = []
    started = time.perf_counter()
    def mark(name, info=None):
        row = {'stage': name, 'seconds': time.perf_counter() - started, 'rss_bytes': rss_now(),
               'peak_rss_bytes': peak_now(), **(info or {})}
        stages.append(row)
        progress(marker, 'stage:' + name, peak_rss_bytes_so_far=row['peak_rss_bytes'], stages=stages)
    mark('pool_on_disk', {'pool_bytes': pool_path.stat().st_size, 'distinct_patients': len(per_patient)})
    started = time.perf_counter()
    adding, add_rss = 0.0, []
    root = work / 'store'
    with es.StoreBuilder(root, header, threads=threads, memory_limit=memory_limit, on_stage=mark) as builder:
        for i, patient in enumerate(stream_patients(pool_path, len(per_patient), size)):
            t = time.perf_counter()
            builder.add(patient)
            adding += time.perf_counter() - t
            if i % ADD_SAMPLE_EVERY == 0:
                add_rss.append(rss_now())
                progress(marker, 'add', patients=i, peak_rss_bytes_so_far=peak_now(), stages=stages)
        del patient
        events = builder.events
        mark('added', {'patients': builder.patients, 'events': events, 'add_seconds': adding,
                       'add_rss_max_bytes': max(add_rss), 'add_rss_samples': len(add_rss)})
        snapshot = builder.finish(provenance)
    built = time.perf_counter() - started
    manifest = json.loads((snapshot / 'manifest.json').read_text())
    return {'distinct_patients': len(per_patient), 'cohorts_generated': cohorts, 'generator_seeds_failed': failed,
            'patients': size, 'ingest_events': events,
            'events_per_distinct_patient': {'mean': statistics.fmean(per_patient), 'p95': pct(per_patient, .95),
                                            'max': per_patient[-1], 'patients': len(per_patient)},
            'generate_seconds': generating, 'convert_seconds': converting, 'pool_bytes': pool_path.stat().st_size,
            'build_seconds': built, 'add_seconds': adding, 'memory_limit': memory_limit,
            'build_stages': stages,
            'snapshot_path': str(snapshot), 'snapshot_id': manifest['snapshot_id'],
            'snapshot_bytes': directory_bytes(snapshot),
            'tables': {n: t['rows'] for n, t in manifest['tables'].items()}}


def limit_bytes(limit):
    """A DuckDB memory limit in bytes: KB, MB, GB and TB are powers of 1000, KiB to TiB powers of 1024."""
    number, unit = re.fullmatch(r'([0-9.]+)\s*([KMGT]i?B|B)', limit).groups()
    power = 'BKMGT'.index(unit[0])
    return float(number) * (1024 if 'i' in unit else 1000) ** power


def timed(function, *args):
    started = time.perf_counter()
    value = function(*args)
    return value, time.perf_counter() - started


def query_worker(snapshot, size, queries, repeats, threads, memory_limit, exhaustive):
    from patterns import execution_store as es
    inputs, _ = query_inputs(queries)
    opened = []
    for _ in range(2):  # the second open is warm; the first reads the files cold
        store, seconds = timed(es.open_store, snapshot, threads, 'files', memory_limit)
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
    return {'open_verify_seconds_cold': opened[0], 'open_verify_seconds_warm': opened[1], 'open_verify_level': 'files',
            'runs': runs, 'exhaustive_check': check}


def exhaustive_check(store, size, first, count):
    """Store results against exhaustive matching over every patient, for the first `count` queries (in memory:
    small sizes only)."""
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


# Memory and time profiles of build(), verify() and one wide match().

def rss_now():
    return current_rss(os.getpid())


def peak_now():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


def verify_profile_worker(snapshot, threads, memory_limit):
    """Both verification levels on a freshly opened store: the file digests, then the content digests
    table by table, with time and resident memory after each."""
    from patterns import execution_store as es
    started = time.perf_counter()
    store = es.open_store(snapshot, threads, False, memory_limit)
    rows = [{'stage': 'opened_without_verify', 'seconds': time.perf_counter() - started, 'rss_bytes': rss_now(),
             'peak_rss_bytes': peak_now()}]
    started = time.perf_counter()
    store.verify('files')
    files = time.perf_counter() - started
    rows.append({'stage': 'verify:files', 'seconds': files, 'rss_bytes': rss_now(), 'peak_rss_bytes': peak_now()})
    started = last = time.perf_counter()
    def table_done(name):
        nonlocal last
        now = time.perf_counter()
        rows.append({'stage': f'verify:content:{name}', 'rows': store.manifest['tables'][name]['rows'],
                     'seconds': now - last, 'rss_bytes': rss_now(), 'peak_rss_bytes': peak_now()})
        last = now
    derived = es.content_digests(store.db, store.fetch_rows, table_done)
    content = time.perf_counter() - started
    agrees = all(derived[n]['buckets'] == t['buckets'] for n, t in store.manifest['tables'].items())
    store.close()
    total_rows = sum(t['rows'] for t in derived.values())
    parquet = sum((Path(snapshot) / path).stat().st_size for path in store.manifest['files'])
    return {'stages': rows, 'files_seconds': files, 'content_seconds': content, 'content_agrees': agrees,
            'digest_span_patients': es.DIGEST_SPAN, 'memory_limit': memory_limit,
            'content_rows': total_rows, 'content_rows_per_second': total_rows / content if content else None,
            'parquet_bytes': parquet, 'files_bytes_per_second': parquet / files if files else None}


def cprofile_worker(snapshot, size, threads):
    """cProfile of one `match()` for the query with the most candidates among the benchmark's queries."""
    import cProfile
    import pstats
    from patterns import execution_store as es
    inputs, _ = query_inputs(50)
    store = es.open_store(snapshot, threads, False)
    ids = [r[0] for r in store.query('SELECT patient_id FROM patients ORDER BY patient_idx')]
    bounds = {pid: (lo, hi) for pid, lo, hi in store.query(
        "SELECT p.patient_id, min(c.lo), max(c.hi) FROM patients p LEFT JOIN coverage c USING (patient_idx) "
        "WHERE p.patient_id NOT LIKE '%~r%' GROUP BY p.patient_id")}
    seed, query = max(inputs, key=lambda sq: len(es.candidates(store, sq[1])['patient_idx']))
    windows = expand_windows(pool_windows(seed, bounds), ids)
    profiler = cProfile.Profile()
    started = time.perf_counter()
    profiler.enable()
    result = es.match(store, query, windows)
    profiler.disable()
    unprofiled = time.perf_counter() - started
    stats = pstats.Stats(profiler).stats
    def cumulative(file_part, name):
        return sum(v[3] for (f, _, n), v in stats.items() if n == name and f.endswith(file_part))
    total = cumulative('execution_store.py', 'match')
    parts = {'candidates_inside_match': cumulative('execution_store.py', '_candidates'),
             'load_candidate_events': cumulative('execution_store.py', '_load_events'),
             'engine_trajectory_graph_match': cumulative('trajectory_graph.py', 'match_prepared'),
             'window_validation': cumulative('execution_store.py', '_check_window'),
             'comparability_of_none_results': cumulative('execution_store.py', '_comparability'),
             'excluded_records_rule': cumulative('execution_store.py', '_excluded_starts')
                                       + cumulative('execution_store.py', 'apply_exclusions')
                                       + cumulative('execution_store.py', '_bound_starts'),
             'match_context_digests': cumulative('execution_store.py', 'match_context')}
    parts['other_result_handling'] = total - sum(parts.values())
    store.close()
    return {'seed': seed, 'patients': len(ids), 'candidates': result['candidates'], 'counts': result['counts'],
            'match_seconds_profiled_total': total, 'wall_seconds_with_profiler': unprofiled,
            'parts_seconds': parts,
            'parts_share': {k: v / total for k, v in parts.items()},
            'note': 'cumulative seconds under cProfile, which slows Python calls; shares are indicative. '
                    'match() calls candidates() internally, so its time is inside the total.'}


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
    child.returncode = os.waitstatus_to_exitcode(status)  # reaped by wait4, not by Popen
    stderr = child.stderr.read() if child.stderr else ''
    child.stderr.close()
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
    shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    started = time.monotonic()
    entry = {'size': size, 'status': 'completed'}
    built, record = run_phase('build', ['--size', str(size), '--work-dir', str(scratch), '--threads', str(args.threads),
                                        '--memory-limit', args.memory_limit],
                              scratch / 'build.json', args.budget_seconds, RSS_LIMIT_BYTES)
    entry['build_phase'] = {k: v for k, v in record.items() if k != 'failure'}
    if built is None:
        entry.update(status='failed', failed_phase='build', failure=record['failure'])
        entry['wall_seconds'] = time.monotonic() - started
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
        return entry
    entry.update({k: v for k, v in built.items() if k not in ('snapshot_path', 'build_stages')})
    entry['build_stages'] = {'stages': built['build_stages'], 'process_peak_rss_bytes': record['peak_rss_bytes'],
                             'process_peak_rss_over_memory_limit': record['peak_rss_bytes'] / limit_bytes(args.memory_limit)}
    (scratch / 'pool.ndjson').unlink(missing_ok=True)
    remaining = max(1.0, args.budget_seconds - (time.monotonic() - started))
    exhaustive = args.exhaustive if size <= args.exhaustive_max_size else 0
    small = size <= args.profile_max_size
    queries, repeats = (args.queries, args.repeats) if small else (args.large_queries, args.large_repeats)
    queried, record = run_phase('query', ['--size', str(size), '--snapshot', built['snapshot_path'], '--queries', str(queries),
                                          '--repeats', str(repeats), '--threads', str(args.threads),
                                          '--memory-limit', args.memory_limit, '--exhaustive', str(exhaustive)],
                                scratch / 'query.json', remaining, RSS_LIMIT_BYTES)
    entry['query_phase'] = {k: v for k, v in record.items() if k != 'failure'}
    if queried is None:
        entry.update(status='failed', failed_phase='query', failure=record['failure'])
    else:
        entry['open_verify_seconds'] = {'cold': queried['open_verify_seconds_cold'],
                                        'warm': queried['open_verify_seconds_warm'],
                                        'level': queried['open_verify_level'],
                                        'cold_note': 'first open after build, page cache not purged'}
        entry['queries'] = query_statistics(queried, built['patients'])
        if queried['exhaustive_check']:
            entry['exhaustive_check'] = queried['exhaustive_check']
    if queried is not None:
        profile = {}
        workers = [('verify_stages', 'verify')] + ([('match_cprofile', 'cprofile')] if small else [])
        for name, worker in workers:
            out, rec = run_phase(worker, ['--size', str(size), '--snapshot', built['snapshot_path'], '--threads', str(args.threads),
                                          '--memory-limit', args.memory_limit],
                                 scratch / f'{worker}.json', args.budget_seconds, RSS_LIMIT_BYTES)
            profile[name] = out if out is not None else {'failure': rec['failure']}
            profile[name]['process_peak_rss_bytes'] = rec['peak_rss_bytes']
        entry['profile'] = profile
    entry['wall_seconds'] = time.monotonic() - started
    if not args.keep:
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


def environment(threads, memory_limit):
    import duckdb
    return {'platform': platform.platform(), 'machine': platform.machine(), 'cpu': cpu_model(),
            'logical_cpus': os.cpu_count(), 'memory_bytes': memory_bytes(),
            'python': platform.python_version(), 'duckdb': duckdb.__version__,
            'duckdb_threads': threads, 'duckdb_memory_limit': memory_limit, 'python_threads_for_matching': 1}


def benchmark(args):
    started = time.monotonic()
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix='store-scaling-', dir='/tmp'))
    work.mkdir(parents=True, exist_ok=True)
    entries, stopped = [], None
    for size in args.sizes:
        if stopped:
            entries.append({'size': size, 'status': 'not_attempted',
                            'reason': f'size {stopped["size"]} failed ({stopped["failure"]["reason"]}) in phase '
                                      f'{stopped["failed_phase"]}'})
            continue
        print(f'size {size} ...', file=sys.stderr, flush=True)
        entry = run_size(size, args, work)
        entries.append(entry)
        print(f'size {size}: {entry["status"]} in {entry["wall_seconds"]:.0f} s', file=sys.stderr, flush=True)
        if entry['status'] == 'failed':
            stopped = entry
    return {'profile': PROFILE, 'generator_seed_base': SEED_BASE,
            'parameters': {'sizes': args.sizes, 'queries': args.queries, 'repeats': args.repeats,
                           'profile_max_size': args.profile_max_size, 'large_queries': args.large_queries,
                           'large_repeats': args.large_repeats,
                           'budget_seconds_per_size': args.budget_seconds, 'rss_limit_bytes': RSS_LIMIT_BYTES,
                           'threads': args.threads, 'memory_limit': args.memory_limit,
                           'distinct_pool_cap': DISTINCT_CAP, 'patients_per_cohort': PER_COHORT},
            'environment': environment(args.threads, args.memory_limit),
            'cohort_construction': {
                'distinct': f'one generator seed per {PER_COHORT} patients (seed {SEED_BASE} + i, skipping seeds the generator cannot build), relabelled per cohort, up to '
                            f'{DISTINCT_CAP:,} distinct patients',
                'replicated': 'beyond the distinct pool, copy k of pool patient j is `<id>~r<k>` with every time shifted by '
                              f'(1 + (37k mod 700)) whole days; no new variety',
                'streaming': 'the distinct pool is written to an ndjson file one seed at a time and read back a line at a '
                             'time, replicated as it is read, into StoreBuilder.add',
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
    parser.add_argument('--memory-limit', default=DEFAULT_MEMORY_LIMIT, help='DuckDB memory limit for build and open')
    parser.add_argument('--exhaustive', type=int, default=5, help='queries compared with exhaustive matching at small sizes')
    parser.add_argument('--exhaustive-max-size', type=int, default=1000)
    parser.add_argument('--keep', action='store_true', help='keep each size\'s snapshot')
    parser.add_argument('--profile-max-size', type=int, default=10000,
                        help='sizes up to this get the full query set and a cProfile of one match; larger ones get '
                             '--large-queries and --large-repeats')
    parser.add_argument('--large-queries', type=int, default=10)
    parser.add_argument('--large-repeats', type=int, default=1)
    parser.add_argument('--worker', choices=('build', 'query', 'verify', 'cprofile'), help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--size', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--snapshot', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        marker = args.worker_output.with_suffix('.progress')
        if args.worker == 'build':
            result = build_worker(args.size, args.work_dir, args.threads, args.memory_limit, marker)
        elif args.worker == 'verify':
            result = verify_profile_worker(args.snapshot, args.threads, args.memory_limit)
        elif args.worker == 'cprofile':
            result = cprofile_worker(args.snapshot, args.size, args.threads)
        else:
            result = query_worker(args.snapshot, args.size, args.queries, args.repeats, args.threads, args.memory_limit,
                                  args.exhaustive)
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
