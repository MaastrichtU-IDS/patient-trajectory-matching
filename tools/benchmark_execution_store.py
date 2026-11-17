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

Build provenance (D34). The build phase (cohort construction, the build worker, and the code that runs and measures a
build) lives in `tools/scaling_build_worker.py`, which runs each size's build in Python's isolated mode. That process
loads none of the engine's matching code, and at its end it records every repository file it loaded or read, by
whole-file digest, with the library versions and the root's kind (`patterns/build_record.py`); every size's record must
be the same, and the report keeps it as `build_provenance`, which `tools/check_report_provenance.py` checks as any
artifact map. Its query phase stays bound by whole-file digests (`artifacts`). `--reuse-builds REPORT` runs only the query
phase, on the snapshots an earlier run kept (`--keep`) in `--work-dir`, when a small build of this tree records exactly
what that report's builds recorded, its build parameters and environment equal this run's, and each kept snapshot still
has its identifier (`reuse_plan` refuses by name otherwise). Each reused size says so (`build_reused`) and keeps the
earlier run's build figures and snapshot identifier; `build_origin` names the report that first built them (its path,
digest, commit, time and machine) and the report reused directly, through any chain of reuses.

Search budgets (D41). Every query is the generator's draw with its `search_budget` replaced by the engine's default,
40,000, which is what every shipped client sends (`budget_policy` in the report, policy `engine-default`). The generator
draws the budget last, from 1, 40 and 300, so the replacement changes nothing else in the query. The drawn budgets are
kept as a stress test of stopped searches: up to `--stress-max-size` patients each size also runs the queries once with
them (`stress_drawn_budgets`, policy `drawn-stress`), reported apart and never in the headline figures.

The report holds aggregates only. It is evidence for one machine and synthetic data.
"""
from __future__ import annotations

import argparse
import datetime
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


def _build_phase():
    """(`tools/scaling_build_worker.py` loaded from its file beside this one, the sha256 of the bytes executed) (D34): the
    build phase's code, which the matcher's arms also use with their own tree's `patterns` first on `sys.path`. Each
    build's record must name the same digest for that file, so the code that ran and measured a build from here is the
    code the build process recorded."""
    import types
    path = Path(__file__).resolve().with_name('scaling_build_worker.py')
    source = path.read_bytes()
    module = types.ModuleType('scaling_build_worker')
    module.__file__ = str(path)
    exec(compile(source, str(path), 'exec'), module.__dict__)
    return module, hashlib.sha256(source).hexdigest()


_BUILD, _BUILD_SHA256 = _build_phase()
(GENERATOR, PER_COHORT, DISTINCT_CAP, SEED_BASE, DAY, RSS_LIMIT_BYTES, DEFAULT_MEMORY_LIMIT, ADD_SAMPLE_EVERY,
 BUILD_FILES, BUILD_FIELDS) = (getattr(_BUILD, n) for n in (
    'GENERATOR', 'PER_COHORT', 'DISTINCT_CAP', 'SEED_BASE', 'DAY', 'RSS_LIMIT_BYTES', 'DEFAULT_MEMORY_LIMIT',
    'ADD_SAMPLE_EVERY', 'BUILD_FILES', 'BUILD_FIELDS'))
(pct, shift_for, load_generator, generate_pool, pool_ingest, write_pool, stream_patients, shifted, replicate,
 directory_bytes, progress, build_worker, limit_bytes, rss_now, peak_now, current_rss, run_phase, build_size) = (
    getattr(_BUILD, n) for n in (
        'pct', 'shift_for', 'load_generator', 'generate_pool', 'pool_ingest', 'write_pool', 'stream_patients',
        'shifted', 'replicate', 'directory_bytes', 'progress', 'build_worker', 'limit_bytes', 'rss_now', 'peak_now',
        'current_rss', 'run_phase', 'build_size'))
ARTIFACTS = ('tools/benchmark_execution_store.py', 'patterns/execution_store.py', 'patterns/synthetic_ingest.py',
             'patterns/trajectory_graph.py', 'demos/mockups/synthetic/generate.py',
             'patterns/test_execution_store.py', 'patterns/execution_store_reference.py', 'patterns/temporal_stn.py',
             'patterns/trajectory_times.py',
             'schemas/execution-store-manifest.schema.json', 'tools/scaling_build_worker.py')
# What shapes a build or its figures: a reused build must have been made with the same ones.
BUILD_PARAMETERS = ('threads', 'memory_limit', 'staging', 'distinct_pool_cap', 'patients_per_cohort',
                    'budget_seconds_per_size', 'rss_limit_bytes')
# The environment a reused build's figures were measured in.
BUILD_ENVIRONMENT = ('platform', 'machine', 'cpu', 'logical_cpus', 'memory_bytes', 'python', 'duckdb')
LIMITATIONS = (
    'One machine (see environment); no other hardware, operating system or storage was measured.',
    'The data are synthetic. Event density, concept frequencies and sources reflect the generator, not a clinical record system.',
    'Beyond the distinct pool the cohort is replicated: copies repeat the pool patients with a whole-day time shift and add '
    'no variety. Concept posting sizes, candidate sets and match outcomes therefore scale as exact multiples, which a real '
    'population would not.',
    'Queries are 50 seeded random queries from the differential test\'s generator, not a clinical query mix. Their '
    'search budget is the engine\'s default, 40,000 (budget_policy, D41); the generator\'s own budgets (1, 40, 300) '
    'appear only in the stress stratum (stress_drawn_budgets), which is a stress test of stopped searches.',
    'Latencies are wall-clock times of one thread of Python and DuckDB with the thread setting stated; no tuning was attempted.',
    'Above the profile size, the query phase runs fewer queries and repeats (see parameters), so its latency percentiles '
    'rest on fewer samples.',
    'Peak memory is the process\'s maximum resident set size, which includes the interpreter and DuckDB buffers.',
    'No exhaustive reference ran at scale; correctness at scale rests on the differential test at 48 patients and the '
    'optional check at 1,000.')


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def summary(values):
    return {'p50': pct(values, .5), 'p95': pct(values, .95), 'max': max(values) if values else None}


def patient_ref(pid):
    """(pool patient id, copy number) from a benchmark patient id."""
    base, sep, k = pid.partition('~r')
    return base, int(k) if sep else 0


# Cohort construction.


# The query set.

# Search budgets (D41). `random_query` draws `search_budget` from 1, 40 and 300 as the last draw of each seed's own
# stream, so replacing it after the draw leaves every other field of the query as drawn. A budget of 1 stops any
# search of two or more steps at its second step, so a third of the drawn queries measured the budget, not the engine
# (D40's calibration). Every shipped client sends the engine's default.
ENGINE_DEFAULT_SEARCH_BUDGET = 40_000
BUDGET_POLICIES = {
    'engine-default': {
        'search_budget': ENGINE_DEFAULT_SEARCH_BUDGET,
        'description': "every query's search_budget is 40,000: the engine's default when the field is absent and what "
                       'every shipped client sends (the trajectory page, the store query page, the request examples). '
                       "The query is random_query's draw with only search_budget replaced (D41)."},
    'drawn-stress': {
        'search_budget': 'drawn by random_query from 1, 40 and 300',
        'description': "random_query's draw unchanged. A STRESS TEST of stopped searches, not a realistic query mix: a "
                       'budget of 1 stops any search of two or more steps at its second step, so its unresolved and '
                       'incomplete results measure the budget, not the engine (D40, D41). Reported apart from the '
                       'headline figures.'},
}
BUDGET_POLICY = 'engine-default'
STRESS_POLICY = 'drawn-stress'


def with_budget(query, policy):
    """The query under a budget policy (D41): only `search_budget` may change."""
    if policy not in BUDGET_POLICIES:
        raise ValueError(f'unknown budget policy {policy!r}')
    if policy == STRESS_POLICY:
        return query
    return {**query, 'search_budget': BUDGET_POLICIES[policy]['search_budget']}


def budget_policy_record(policy):
    """What a report states about the search budgets of its queries."""
    return {'name': policy, **BUDGET_POLICIES[policy]}


def query_inputs(seeds, policy=BUDGET_POLICY):
    """[(seed, query)] over the first cohort, and that cohort's ingest. The same for every size. `policy` sets the
    queries' search budget (D41); nothing else of a query depends on it."""
    from patterns.test_execution_store import random_query
    ingest = pool_ingest(PER_COHORT)[0]
    ancestors = ingest['closure']['ancestors']
    holders = {}
    for p in ingest['patients']:
        for concept in {a for e in p['events'] for c in e['concepts'] for a in (c, *ancestors[c])}:
            holders[concept] = holders.get(concept, 0) + 1
    pool = sorted(c for c, n in holders.items() if n >= 2)
    return [(s, with_budget(random_query(random.Random(s), pool, ingest), policy)) for s in range(seeds)], ingest


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


def timed(function, *args):
    started = time.perf_counter()
    value = function(*args)
    return value, time.perf_counter() - started


def query_worker(snapshot, size, queries, repeats, threads, memory_limit, exhaustive, policy=BUDGET_POLICY):
    from patterns import execution_store as es
    inputs, _ = query_inputs(queries, policy)
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
                         'candidates': len(found['patient_idx']), 'counts': result['counts'],
                         'incomplete_none': incomplete_none(result)})
        runs.append(rows)
    check = None
    if exhaustive:
        check = exhaustive_check(store, size, first, exhaustive)
    store.close()
    return {'open_verify_seconds_cold': opened[0], 'open_verify_seconds_warm': opened[1], 'open_verify_level': 'files',
            'runs': runs, 'exhaustive_check': check, 'budget_policy': policy,
            'search_budgets': {seed: query.get('search_budget') for seed, query in inputs}}


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


def incomplete_none(result):
    """{note: n} over a match's `none` results that are not complete (D30 review, S2)."""
    out = {}
    for r in result['results']:
        if r['status'] == 'none' and r.get('complete') is False:
            key = r.get('note', 'NO_NOTE')
            out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def query_statistics(result, patients):
    """Per-query medians over the repeats, then p50/p95/max across queries."""
    runs = result['runs']
    per_query = []
    for i in range(len(runs[0])):
        rows = [run[i] for run in runs]
        per_query.append({'seed': rows[0]['seed'],
                          'candidates_seconds': statistics.median(r['candidates_seconds'] for r in rows),
                          'match_seconds': statistics.median(r['match_seconds'] for r in rows),
                          'candidates': rows[0]['candidates'], 'counts': rows[0]['counts'],
                          'incomplete_none': rows[0].get('incomplete_none', {})})
    mix, incomplete = {}, {}
    for q in per_query:
        for status, n in q['counts'].items():
            mix[status] = mix.get(status, 0) + n
        for note, n in q['incomplete_none'].items():
            incomplete[note] = incomplete.get(note, 0) + n
    pruning = [1 - q['candidates'] / patients for q in per_query]
    return {'queries': len(per_query), 'repeats': len(runs),
            'candidates_seconds': summary([q['candidates_seconds'] for q in per_query]),
            'match_seconds': summary([q['match_seconds'] for q in per_query]),
            'candidate_count': summary([q['candidates'] for q in per_query]),
            'pruning_rate': {**summary(pruning), 'mean': statistics.fmean(pruning)},
            'status_mix': dict(sorted(mix.items())),
            # D30 review (S2): the `none` results above that are not complete, by note. `RELAXATION_ORDER_UNCERTAIN`
            # is a `none` whose relaxation search was left undecided by the order rule, often a relaxed match before.
            'incomplete_none_by_note': dict(sorted(incomplete.items())),
            'candidates_seconds_per_repeat_p50': [pct([r['candidates_seconds'] for r in run], .5) for run in runs],
            'match_seconds_per_repeat_p50': [pct([r['match_seconds'] for r in run], .5) for run in runs],
            # D41: each query's counts too, so that a moved status mix can be traced to its queries.
            'per_query': [{'seed': q['seed'], 'candidates': q['candidates'], 'candidates_seconds': q['candidates_seconds'],
                           'match_seconds': q['match_seconds'], 'counts': q['counts'],
                           'incomplete_none': q['incomplete_none']} for q in per_query]}


def check_policy(result, policy):
    """A query worker ran the budget policy it was asked for (D41); a failed worker returned nothing to check."""
    if result is not None and result.get('budget_policy') != policy:
        raise RuntimeError(f'the query worker ran budget policy {result.get("budget_policy")!r}, not {policy!r}')


def stress_statistics(result):
    """The stress stratum (D41): the queries once with the generator's drawn budgets, counted apart."""
    (rows,) = result['runs']
    mix, incomplete = {}, {}
    for row in rows:
        for status, n in row['counts'].items():
            mix[status] = mix.get(status, 0) + n
        for note, n in row.get('incomplete_none', {}).items():
            incomplete[note] = incomplete.get(note, 0) + n
    budgets = {int(k): v for k, v in result['search_budgets'].items()}
    return {'label': 'STRESS TEST: not a realistic query mix (D40, D41)',
            'budget_policy': budget_policy_record(result['budget_policy']),
            'queries': len(rows), 'repeats': 1,
            'queries_by_search_budget': {str(b): sum(1 for v in budgets.values() if v == b)
                                         for b in sorted(set(budgets.values()))},
            'status_mix': dict(sorted(mix.items())),
            'incomplete_none_by_note': dict(sorted(incomplete.items())),
            'per_query': [{'seed': row['seed'], 'search_budget': budgets[row['seed']], 'candidates': row['candidates'],
                           'match_seconds': row['match_seconds'], 'counts': row['counts'],
                           'incomplete_none': row.get('incomplete_none', {})} for row in rows]}


class ReuseRefused(ValueError):
    def __init__(self, code, where=''):
        super().__init__(f'{code}: {where}' if where else code)
        self.code = code


def probe_record(memory_limit, staging, threads):
    """The runtime record of a small build of this tree (one cohort, in a scratch directory): what this tree's build
    loads and reads now, for comparison with an earlier report's (D34)."""
    scratch = Path(tempfile.mkdtemp(prefix='scaling-probe-'))
    try:
        fields, built = build_size(PER_COHORT, scratch / 'build', staging, threads, memory_limit, 3600)
        if built is None:
            raise ReuseRefused('PROBE_BUILD_FAILED', json.dumps(fields.get('failure'))[:300])
        check_build_code(built['record'])
        return built['record']
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def reuse_plan(old, sizes, work, parameters, record=None, env=None):
    """{size: (kept snapshot directory, the earlier entry)} for reusing an earlier report's builds, or `ReuseRefused`
    naming why not:
    - the earlier report has no build provenance (`NO_BUILD_PROVENANCE`);
    - a small build of this tree (`probe_record`) does not record exactly what its builds recorded
      (`BUILD_RECORD_CHANGED`): a repository file loaded or read, or its digest, a pinned data file, a library version or
      the root's kind differs;
    - its profile, generator seed or a build parameter (threads, memory limit, staging, cohort sizes, the time budget,
      the memory guard) differs (`BUILD_PARAMETERS_CHANGED`);
    - its environment differs from this one, where its build figures were measured (`ENVIRONMENT_CHANGED`);
    - a size was not completed there (`SIZE_NOT_IN_REPORT`), or its kept snapshot is missing (`SNAPSHOT_MISSING`) or no
      longer opens with its identifier and file digests (`SNAPSHOT_IDENTITY_CHANGED`)."""
    from patterns import execution_store as es
    mine = old.get('build_provenance')
    if not mine:
        raise ReuseRefused('NO_BUILD_PROVENANCE')
    if old.get('profile') != PROFILE or old.get('generator_seed_base') != SEED_BASE or any(
            old['parameters'].get(k) != parameters[k] for k in BUILD_PARAMETERS):
        raise ReuseRefused('BUILD_PARAMETERS_CHANGED', ', '.join(
            ['profile'] * (old.get('profile') != PROFILE) + ['generator_seed_base'] * (old.get('generator_seed_base') != SEED_BASE)
            + [k for k in BUILD_PARAMETERS if old['parameters'].get(k) != parameters[k]]))
    env = env or environment(parameters['threads'], parameters['memory_limit'])
    if any(old.get('environment', {}).get(k) != env[k] for k in BUILD_ENVIRONMENT):
        raise ReuseRefused('ENVIRONMENT_CHANGED', ', '.join(
            k for k in BUILD_ENVIRONMENT if old.get('environment', {}).get(k) != env[k]))
    record = record or probe_record(parameters['memory_limit'], parameters['staging'], parameters['threads'])
    if mine != record:
        differ = []
        for key in sorted(set(mine) | set(record)):
            a, b = mine.get(key), record.get(key)
            if isinstance(a, dict) and isinstance(b, dict):
                differ += [f'{key}:{k}' for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
            elif a != b:
                differ.append(key)
        raise ReuseRefused('BUILD_RECORD_CHANGED', ', '.join(differ))
    entries = {s['size']: s for s in old['sizes']}
    plan = {}
    for size in sizes:
        entry = entries.get(size)
        if entry is None or entry.get('status') != 'completed':
            raise ReuseRefused('SIZE_NOT_IN_REPORT', str(size))
        path = Path(work) / f'size-{size}' / 'store' / entry['snapshot_id']
        if not (path / 'manifest.json').is_file():
            raise ReuseRefused('SNAPSHOT_MISSING', str(path))
        try:
            es.open_store(path, 1, 'files', parameters['memory_limit'], expected_snapshot_id=entry['snapshot_id']).close()
        except es.StoreError as exc:
            raise ReuseRefused('SNAPSHOT_IDENTITY_CHANGED', f'{size}: {exc}') from None
        plan[size] = (path, entry)
    return plan


class BuildFieldsChanged(RuntimeError):
    pass


class BuildCodeDiffers(RuntimeError):
    pass


class BuildSettingsDiffer(RuntimeError):
    pass


def check_settings(parameters, record):
    """A fresh report's build parameters are what its build processes recorded seeing (D34)."""
    differ = [k for k in ('staging', 'threads', 'memory_limit') if record['settings'].get(k) != parameters[k]]
    if differ:
        raise BuildSettingsDiffer('BUILD_SETTINGS_DIFFER: ' + ', '.join(differ))


def check_build_code(record):
    """The build process recorded the same `tools/scaling_build_worker.py` this process executed (its `run_phase` and
    `build_size` started and measured the build)."""
    recorded = record['modules'].get('tools/scaling_build_worker.py')
    if recorded != _BUILD_SHA256:
        raise BuildCodeDiffers(f'PARENT_BUILD_CODE_DIFFERS: executed {_BUILD_SHA256[:12]}, recorded {str(recorded)[:12]}')


def run_size(size, args, work, reuse=None):
    """(the report entry for one size, its build's runtime record or None). With `reuse` ((kept snapshot, earlier
    entry), D34) only the query phase runs. The build fields, from `build_size` or the earlier entry, are checked
    unchanged before the entry is returned (`BuildFieldsChanged`): nothing after the build may rewrite them."""
    scratch = Path(work) / f'size-{size}'
    started = time.monotonic()
    entry = {'size': size, 'status': 'completed'}
    if reuse is not None:
        path, earlier = reuse
        fields = {k: earlier[k] for k in BUILD_FIELDS if k in earlier}
        fields['build_reused'] = {'snapshot_id': earlier['snapshot_id'],
                                  'note': 'build figures and snapshot of the run named in build_origin'}
        built, record = {'snapshot_path': str(path), 'patients': earlier['patients']}, None
        keep = True
    else:
        fields, built = build_size(size, scratch, args.staging, args.threads, args.memory_limit, args.budget_seconds)
        record = None if built is None else built['record']
        if record is not None:
            check_build_code(record)
        keep = args.keep
    frozen = json.dumps(fields, sort_keys=True)
    entry.update(json.loads(frozen))
    if built is None:
        entry['wall_seconds'] = time.monotonic() - started
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
    else:
        entry = _query_size(size, args, scratch, entry, built, started, keep=keep)
    if json.dumps({k: entry.get(k) for k in json.loads(frozen)}, sort_keys=True) != frozen:
        raise BuildFieldsChanged(f'size {size}: a build field changed after the build')
    return entry, record


def _query_size(size, args, scratch, entry, built, started, keep):
    """The query phase of one size, and its profiles, on the snapshot at `built['snapshot_path']`."""
    remaining = max(1.0, args.budget_seconds - (time.monotonic() - started))
    exhaustive = args.exhaustive if size <= args.exhaustive_max_size else 0
    small = size <= args.profile_max_size
    queries, repeats = (args.queries, args.repeats) if small else (args.large_queries, args.large_repeats)
    queried, record = run_phase('query', ['--size', str(size), '--snapshot', built['snapshot_path'], '--queries', str(queries),
                                          '--repeats', str(repeats), '--threads', str(args.threads),
                                          '--memory-limit', args.memory_limit, '--exhaustive', str(exhaustive),
                                          '--budget-policy', BUDGET_POLICY],
                                scratch / 'query.json', remaining, RSS_LIMIT_BYTES)
    entry['query_phase'] = {k: v for k, v in record.items() if k != 'failure'}
    check_policy(queried, BUDGET_POLICY)
    if queried is None:
        entry.update(status='failed', failed_phase='query', failure=record['failure'])
    else:
        entry['open_verify_seconds'] = {'cold': queried['open_verify_seconds_cold'],
                                        'warm': queried['open_verify_seconds_warm'],
                                        'level': queried['open_verify_level'],
                                        'cold_note': 'first open after build, page cache not purged' if 'build_reused' not in entry
                                        else 'first open in this run, of a snapshot an earlier run built; page cache not purged'}
        entry['queries'] = query_statistics(queried, built['patients'])
        if queried['exhaustive_check']:
            entry['exhaustive_check'] = queried['exhaustive_check']
        if size <= args.stress_max_size:  # D41: the drawn budgets, once, as a labelled stress test
            stressed, rec = run_phase('query', ['--size', str(size), '--snapshot', built['snapshot_path'],
                                                '--queries', str(queries), '--repeats', '1', '--threads', str(args.threads),
                                                '--memory-limit', args.memory_limit, '--exhaustive', '0',
                                                '--budget-policy', STRESS_POLICY],
                                      scratch / 'stress.json', max(1.0, args.budget_seconds - (time.monotonic() - started)),
                                      RSS_LIMIT_BYTES)
            check_policy(stressed, STRESS_POLICY)
            entry['stress_drawn_budgets'] = (stress_statistics(stressed) if stressed is not None
                                             else {'label': 'STRESS TEST', 'failure': rec['failure']})
            entry['stress_drawn_budgets']['query_phase'] = {k: v for k, v in rec.items() if k != 'failure'}
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
    if not keep:
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


BUILD_COVERS = ('build_provenance records every repository file (source, never bytecode) the build processes loaded or '
                'read through Python, by whole-file digest; the pinned data files; the versions of Python, DuckDB, pyarrow '
                'and jsonschema; whether the repository was a checkout; and the settings the build processes saw (staging, '
                'threads, memory limit, cohort construction, process flags, environment). DuckDB\'s native file access '
                'in a build is confined to the build\'s directories. A reuse compares a new probe build\'s record with '
                'this one, and the build parameters and environment. Not covered: the operating system, CPU microcode and '
                'library internals beyond their versions. The benchmark that ran the builds and wrote their figures is '
                'bound only by the report that first built them (build_origin.first_built.benchmark_sha256, or this '
                'report\'s artifacts for builds it made). A reused snapshot\'s own manifest names the builder and '
                'converter file digests of the tree that built it.')


def git_state():
    """{'head': the commit checked out, 'dirty': whether tracked files differ from it or untracked files lie under
    `patterns/` or `tools/`}, read when the run starts."""
    try:
        run = lambda *a: subprocess.run(['git', *a], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
        head = run('rev-parse', 'HEAD')
        dirty = bool(run('status', '--porcelain', '--untracked-files=no')) or bool(
            run('status', '--porcelain', '--untracked-files=all', '--', 'patterns', 'tools'))
        return {'head': head, 'dirty': dirty}
    except (OSError, subprocess.CalledProcessError):
        return None


def git_blob(data):
    """The git blob id of a file's bytes: with the commit that holds it, how a committed report can be retrieved."""
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


def report_path(path):
    """A report's path, relative to the repository when it lies inside it."""
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def benchmark(args):
    started = time.monotonic()
    work = Path(args.work_dir) if args.work_dir else Path(tempfile.mkdtemp(prefix='store-scaling-', dir='/tmp'))
    work.mkdir(parents=True, exist_ok=True)
    parameters = {'sizes': args.sizes, 'queries': args.queries, 'repeats': args.repeats,
                  'profile_max_size': args.profile_max_size, 'large_queries': args.large_queries,
                  'large_repeats': args.large_repeats,
                  'budget_seconds_per_size': args.budget_seconds, 'rss_limit_bytes': RSS_LIMIT_BYTES,
                  'threads': args.threads, 'memory_limit': args.memory_limit, 'staging': args.staging,
                  'distinct_pool_cap': DISTINCT_CAP, 'patients_per_cohort': PER_COHORT,
                  'stress_max_size': args.stress_max_size}
    env = environment(args.threads, args.memory_limit)
    generated_at = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds')
    commit = git_state()
    plan, origin, provenance = {}, None, None
    if args.reuse_builds:
        old_text = Path(args.reuse_builds).read_bytes()
        old = json.loads(old_text)
        plan = reuse_plan(old, args.sizes, work, parameters, env=env)
        provenance = old['build_provenance']
        directly = {'path': report_path(args.reuse_builds), 'sha256': hashlib.sha256(old_text).hexdigest(),
                    'git_blob': git_blob(old_text), 'commit': old.get('commit'), 'generated_at': old.get('generated_at'),
                    # the benchmark that wrote its figures, bound only by that report
                    'benchmark_sha256': (old.get('artifacts') or {}).get('tools/benchmark_execution_store.py')}
        # The report that first built the snapshots, carried unchanged through a chain of reuses.
        first = (old.get('build_origin') or {}).get('first_built') or {**directly, 'environment': old.get('environment')}
        origin = {'first_built': first, 'reused_from': directly, 'sizes': sorted(plan)}
    entries, stopped = [], None
    for size in args.sizes:
        if stopped:
            entries.append({'size': size, 'status': 'not_attempted',
                            'reason': f'size {stopped["size"]} failed ({stopped["failure"]["reason"]}) in phase '
                                      f'{stopped["failed_phase"]}'})
            continue
        print(f'size {size} ...', file=sys.stderr, flush=True)
        entry, record = run_size(size, args, work, plan.get(size))
        if record is not None:
            if provenance is not None and record != provenance:  # every build of a run loads and reads the same files
                raise RuntimeError(f'BUILD_RECORD_DIFFERS: size {size}')
            provenance = record
        entries.append(entry)
        if provenance is not None:
            check_settings(parameters, provenance)
        print(f'size {size}: {entry["status"]} in {entry["wall_seconds"]:.0f} s', file=sys.stderr, flush=True)
        if entry['status'] == 'failed':
            stopped = entry
    out = {'profile': PROFILE, 'generator_seed_base': SEED_BASE, 'generated_at': generated_at, 'commit': commit,
            'parameters': parameters,
            'environment': env,
            'cohort_construction': {
                'distinct': f'one generator seed per {PER_COHORT} patients (seed {SEED_BASE} + i, skipping seeds the generator cannot build), relabelled per cohort, up to '
                            f'{DISTINCT_CAP:,} distinct patients',
                'replicated': 'beyond the distinct pool, copy k of pool patient j is `<id>~r<k>` with every time shifted by '
                              f'(1 + (37k mod 700)) whole days; no new variety',
                'streaming': 'the distinct pool is written to an ndjson file one seed at a time and read back a line at a '
                             'time, replicated as it is read, into StoreBuilder.add',
                'queries': 'seeded random_query / random_windows from patterns/test_execution_store.py, anchored on the '
                           'first cohort, identical for every size; windows follow the pool patient and its shift; '
                           'search budgets by budget_policy (D41)'},
            'budget_policy': budget_policy_record(BUDGET_POLICY),
            'stress_policy': {**budget_policy_record(STRESS_POLICY),
                              'sizes': [s for s in args.sizes if s <= args.stress_max_size],
                              'where': 'stress_drawn_budgets in each of those sizes'},
            'sizes': entries,
            'artifacts': {p: digest(p) for p in ARTIFACTS},
            'build_provenance': provenance,
            'build_covers': BUILD_COVERS,
            'limitations': list(LIMITATIONS),
            'contains_patient_level_data': False,
            'total_seconds': time.monotonic() - started}
    if origin is not None:
        out['build_origin'] = origin
    return out


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
    parser.add_argument('--reuse-builds', type=Path, help='an earlier report whose kept snapshots (in --work-dir) this '
                        'run queries instead of building, if a small build of this tree records what its builds recorded (D34)')
    parser.add_argument('--staging', choices=('parquet', 'ndjson'), default='parquet',
                        help='how the builder stages rows (D26); parquet is the store\'s default')
    parser.add_argument('--profile-max-size', type=int, default=10000,
                        help='sizes up to this get the full query set and a cProfile of one match; larger ones get '
                             '--large-queries and --large-repeats')
    parser.add_argument('--large-queries', type=int, default=10)
    parser.add_argument('--large-repeats', type=int, default=1)
    parser.add_argument('--stress-max-size', type=int, default=10000,
                        help='sizes up to this also run the queries once with the generator\'s drawn search budgets, '
                             'reported apart as a stress test (D41); 0 for none')
    parser.add_argument('--budget-policy', choices=sorted(BUDGET_POLICIES), help=argparse.SUPPRESS)  # workers only
    parser.add_argument('--worker', choices=('query', 'verify', 'cprofile'), help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--size', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--snapshot', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        # The build worker runs in its own script (D34: tools/scaling_build_worker.py, in isolated mode), never here.
        if args.worker == 'verify':
            result = verify_profile_worker(args.snapshot, args.threads, args.memory_limit)
        elif args.worker == 'cprofile':
            result = cprofile_worker(args.snapshot, args.size, args.threads)
        else:
            result = query_worker(args.snapshot, args.size, args.queries, args.repeats, args.threads, args.memory_limit,
                                  args.exhaustive, args.budget_policy or BUDGET_POLICY)
        args.worker_output.write_text(json.dumps(result))
        return 0
    if args.budget_policy is not None:  # D41: a report's policy is fixed; only its workers take one
        parser.error('--budget-policy is for the query workers; reports run engine-default with a drawn-stress stratum')
    if min(args.sizes) < PER_COHORT:
        parser.error(f'sizes start at {PER_COHORT}, the first cohort the queries are anchored on')
    report = benchmark(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(args.output)
    return 0


if __name__ == '__main__':
    sys.exit(main())
