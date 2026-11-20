#!/usr/bin/env python3
"""Count requests through the dispatcher at scale, paired against a reference code (D43b's latency rule).

D43 timed a path it never ran: the matcher benchmark's queries carry no count and call `execution_store.match`
directly, so the cost D43 added, its post-pass on count requests, went unmeasured (decision log, D43: "a latency rule
must time the path the change executes"). This tool times that path: fixed count requests (`REQUESTS`: Event
frequency and an Nth step beside a count, each at profiles 1.1 and 1.3) through `store_conditions.match`, the one entry
point the page and the tools call, on one snapshot (the kept D34 build of 300,000 patients), with D43b's windows
(each patient's second coded seizure for 730 days, the benchmark's choice; the page's period starts at the first
seizure; patients without one have no window) and `detail='decided'` (the
page's store query server's). The reference code (a git ref, extracted to a scratch directory as the matcher
benchmark extracts its arms) and the candidate (this tree) run back to back in fresh processes with a warm pool, for
each repeat and worker count, the order alternating across repeats (ABBA): D31's paired interleaved protocol
(`paired-interleaved-1.0`, `tools/benchmark_matcher.py --paired-confirm`) applied to these requests. In each run every
request runs `--runs` times in a row; a pair's time for a request is the median of its runs.

Two requests without a count (`NO_COUNT_REQUESTS`: a 1.1 request with an Nth step, a 1.3 request with no step
condition) run in the same job (D43b rule 4.2b): through the dispatcher their only added cost is the `has_counts`
check. Before the timed runs, the structural check (rule 4.2a, `structural_check`) runs the matcher benchmark's own
worker (`tools/benchmark_matcher.py`, requests without counts through `execution_store.match`) for a few queries, lists
every repository file it loaded, and compares each with the reference's by sha256.

Beside the times it checks, on the runs of the first repeat, that each code's answer to each request is the same at
every worker count and in every run, and that the candidate's answer differs from the reference's only where the
reference's result is `unresolved` with STEP_CONDITION_UNCERTAIN (D43 rule 2 at scale), with the candidate's
`count_refinement` tally. The answers themselves are written to a scratch directory and removed; the report holds
aggregates only. Synthetic data; one machine.

`--rules d44` (decision log, D44, rule 6.2) reuses the protocol against main with D44's bars: every count request at most
1.05 of main's time in every pair at 14 workers, the 1.3 Event frequency p95 at most 10 s, the requests without a count
at most 1.05 (pooled median); no identity run; the candidate's answers equal main's at every result, with the same
tally (the scaling snapshot holds no excluded record, so D44's mark cannot apply, while the post-pass's comparison
that reads an answer without the mark runs on every result it refines); the structural check is reported, not ruled
(`execution_store.py` differs from main by design).

`--rules d44b` (decision log, D44b, rule 6.2) is D44's with the per-pair bar **control-normalised** (the D44 lesson: a
per-pair bar of 1.05 sits below this machine's noise floor). In each ABBA pair at 14 workers, each count request's
candidate-over-main ratio is divided by the geometric mean of the same pair's ratios for the two requests without a
count (`NO_COUNT_REQUESTS`, which run no changed code, timed in the same processes); the bar, at most 1.05, applies to
that normalised ratio. The controls' own pooled medians are reported, not ruled. The 1.3 Event frequency p95 (at most
10 s), the answers, tally and protocol checks stay. A micro-measure bounds the changed code's own cost (`micro`): the
post-pass's comparison without the mark (`store_conditions_counts.unmarked`), timed call by call over every row it
compares, with each count request run once through the dispatcher in one process (one worker) by this tree; its total
must be at most 1% of the request's median time at 14 workers (the timer's own overhead is counted, so it bounds).
Normalising is valid only if the controls cannot be slowed by the change, so a control-structural check is ruled too
(`control_worker`, `control_check`; the review's F1): every function or method the change adds or alters in a
`patterns/` module (by the AST against the reference, `tools/d44_measure.changed_definitions`) gets a call counter, and
each control runs once through the dispatcher at 1 worker and at 14; every counter must read 0. The counters live in
the dispatcher's process. At 1 worker every unit runs there, through the same chunk functions a pool unit calls (each
module's `_worker_unit` calls `_load_events` and `match_prepared`, or `_match_derived_chunk` / `_match_plain_chunk`, on
the worker's store), so those counters cover the pool path; at 14 workers the dispatcher's own part is counted. The check
fails if a pool unit's entry point is itself changed or names a changed function in its body, or if a module imports a
changed function by name (on the AST), which would bypass a counter.

`--changed-set FILE` (D48's amendment, for rebinding D44b's verdict on a later tree) fixes the set the control-structural
check counts: a count-latency verdict's recorded `changed_functions`, or a {module: [names]} map, instead of the AST
against `--reference`, which on a later tree would also sweep in that tree's own changes. The report names its source
(`changed_set_source`).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PROFILE = 'count-request-latency-1.0'
PROTOCOL = 'paired-interleaved-1.0'
DAY = 86_400_000_000
WINDOW_DAYS = 730
ARTIFACTS = ('tools/benchmark_count_requests.py', 'patterns/store_conditions.py', 'patterns/store_conditions_counts.py',
             'patterns/store_conditions_relative.py', 'patterns/store_conditions_coded.py',
             'patterns/store_conditions_gene.py', 'patterns/execution_store.py', 'patterns/trajectory_graph.py',
             'patterns/trajectory_times.py', 'patterns/temporal_stn.py')
# The files a code at a git ref is described by (listed, not as a path-to-digest map, which the provenance check would
# read as bindings of this tree).
REF_FILES = ARTIFACTS[1:]


def _event_frequency(profile):
    """The page's Event frequency template as the store receives it: at least 3 seizures within 183 days, then the
    first sodium channel blocker within 183 days of one of them (D39, D43's timed request)."""
    query = {'profile': 'trajectory-graph-1.0', 'scope': 'from_start',
             'steps': [{'id': 'A', 'concept': 'seizure', 'occurrence': 'any'},
                       {'id': 'B', 'concept': 'sodium_channel_blocker', 'occurrence': 'first'}],
             'relations': [{'id': 'edge:0', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 183 * DAY}],
             'relaxations': [{'id': 'edge:0', 'kind': 'extend_max', 'relation': 'edge:0', 'max': 274 * DAY,
                              'cost': '0.25'}],
             'budget': '1', 'search_budget': 40000}
    conditions = {'profile': profile, 'steps': {'A': {'count': {'at_least': 3, 'within': {'span': 183 * DAY}}}},
                  'relaxations': [{'id': 'count:A', 'kind': 'widen_count', 'step': 'A', 'cost': '0.5', 'at_least': 2}]}
    return query, conditions


def _nth_count(profile):
    """An Nth step beside a count: a cluster of at least 2 seizures within 30 days, within a year after the second
    sodium channel blocker record (one fewer seizure, or 18 months, as relaxations)."""
    query = {'profile': 'trajectory-graph-1.0', 'scope': 'from_start',
             'steps': [{'id': 'A', 'concept': 'sodium_channel_blocker', 'occurrence': 'any'},
                       {'id': 'B', 'concept': 'seizure', 'occurrence': 'any'}],
             'relations': [{'id': 'edge:0', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 365 * DAY}],
             'relaxations': [{'id': 'edge:0', 'kind': 'extend_max', 'relation': 'edge:0', 'max': 548 * DAY,
                              'cost': '0.25'}],
             'budget': '1', 'search_budget': 40000}
    conditions = {'profile': profile, 'steps': {'A': {'occurrence': {'nth': 2}},
                                                'B': {'count': {'at_least': 2, 'within': {'span': 30 * DAY}}}},
                  'relaxations': [{'id': 'count:B', 'kind': 'widen_count', 'step': 'B', 'cost': '0.5', 'at_least': 1}]}
    return query, conditions


def _coded(make):
    query, conditions = make('execution-store-conditions-1.3')
    return query, {**conditions, 'exclude_kinds': ['mention']}


REQUESTS = {
    '1.3 event frequency': _coded(_event_frequency),
    '1.1 event frequency': _event_frequency('execution-store-conditions-1.1'),
    '1.3 nth and count': _coded(_nth_count),
    '1.1 nth and count': _nth_count('execution-store-conditions-1.1'),
}


def _sequence():
    """A sequence without a count: a seizure, then the first sodium channel blocker within 183 days (9 months as a
    relaxation)."""
    query, _ = _event_frequency('execution-store-conditions-1.1')
    return query


# Requests without a count (D43b rule 4.2b): the dispatcher's only added cost for them is the `has_counts` check.
NO_COUNT_REQUESTS = {
    # Profile 1.1 without a count: the second seizure (an Nth step), then the first sodium channel blocker.
    '1.1 sequence': (_sequence(), {'profile': 'execution-store-conditions-1.1', 'steps': {'A': {'occurrence': {'nth': 2}}}}),
    # Profile 1.3 without any step condition: the same sequence over coded records only.
    '1.3 coded only': (_sequence(), {'profile': 'execution-store-conditions-1.3', 'exclude_kinds': ['mention'],
                                     'steps': {}}),
}
REQUESTS.update(NO_COUNT_REQUESTS)
COUNT_REQUESTS = [name for name in REQUESTS if name not in NO_COUNT_REQUESTS]
RULED_WORKERS = 14
RATIO_BAR = 2.0  # the candidate's time over the reference's, per count request and pair (D43b rule 4.1)
ABSOLUTE_BAR = {'request': '1.3 event frequency', 'p95_seconds': 10.0}  # D43b rule 4.1, the D21 target's p95
NO_COUNT_BAR = 1.05  # a request without a count: pooled median ratio over the pairs (D43b rule 4.2b)
RULES = {'d43c': {'ratio_bar': RATIO_BAR, 'identity_ref': True, 'answers_equal_main': False, 'structural_ruled': True},
         # D44 rule 6.2: the same protocol against main, the bar 1.05, the answers main's (see the docstring)
         'd44': {'ratio_bar': 1.05, 'identity_ref': False, 'answers_equal_main': True, 'structural_ruled': False},
         # D44b rule 6.2: D44's, the per-pair bar on the control-normalised ratio, the controls reported, a micro-measure
         'd44b': {'ratio_bar': 1.05, 'identity_ref': False, 'answers_equal_main': True, 'structural_ruled': False,
                  'normalised': True, 'micro': True}}
OUTPUTS = {'d43c': 'verification/d43b-count-latency-verdict.json', 'd44': 'verification/d44-count-latency-verdict.json',
           'd44b': 'verification/d44b-count-latency-verdict.json'}
MICRO_SHARE = 0.01  # D44b: the changed code's measured cost at most 1% of the request's median time at 14 workers

WINDOWS_SQL = """
    WITH s AS (SELECT DISTINCT e.patient_idx, e.event_id, e.start_lo FROM events e
               JOIN event_concepts ec USING (patient_idx, event_id)
               JOIN concept_closure cc ON cc.concept = ec.concept
               WHERE cc.ancestor = 'seizure' AND e.kind <> 'mention')
    SELECT p.patient_id, list_sort(list(s.start_lo))[2] FROM s JOIN patients p USING (patient_idx)
    GROUP BY p.patient_id"""


def windows_of(store):
    """D43b's windows (not the page's): from each patient's second coded seizure, 730 days; none for a patient without
    one."""
    return {pid: {'start': second, 'end': second + WINDOW_DAYS * DAY}
            for pid, second in store.query(WINDOWS_SQL) if second is not None}


def tally_of(answer):
    """The answer's `derived_steps.count_refinement` without its rule text (None without the post-pass)."""
    tally = answer.get('derived_steps', {}).get('count_refinement')
    return None if tally is None else {k: v for k, v in tally.items() if k != 'rule'}


def answer_digest(answer):
    """sha256 of the counts, the results and the post-pass's tally (D43b review, M1: a post-pass that refined nothing
    or skipped re-derivations changes the digest)."""
    return hashlib.sha256(json.dumps({'counts': answer['counts'], 'results': answer['results'],
                                      'count_refinement': answer.get('derived_steps', {}).get('count_refinement')},
                                     sort_keys=True).encode()).hexdigest()


def _pool_code():
    """The `execution_store` a pool worker imported (D43b review, S1)."""
    from patterns import execution_store
    return execution_store.__file__


# One run, in its own process, with the code at --code-root first on the path.

def worker(args):
    code_root = str(Path(args.code_root).resolve())
    # This tree's ROOT goes last (as `benchmark_matcher.import_arm` puts it): the pool's spawned workers re-run this
    # script's header, which puts ROOT in front only when it is absent (D43b review, S1).
    sys.path[:] = ([code_root] + [p for p in sys.path if Path(p or '.').resolve() not in (ROOT, ROOT / 'tools')]
                   + [str(ROOT)])
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    if not Path(sc.__file__).resolve().is_relative_to(Path(code_root)):
        raise RuntimeError(f'imported {sc.__file__}, not from {code_root}')
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    try:
        pool = es.get_pool(store, args.arm_workers)
        pool.warm()
        pool_code = sorted({f.result() for f in [pool._pool().submit(_pool_code) for _ in range(args.arm_workers)]})
        if not all(Path(c).resolve().is_relative_to(Path(code_root)) for c in pool_code):
            raise RuntimeError(f'pool workers imported {pool_code}, not from {code_root}')
        windows = windows_of(store)
        out = {'code': sc.__file__, 'pool_code': pool_code, 'patients_with_window': len(windows),
               'pool_start_seconds': pool.start_seconds, 'requests': {}}
        for name in args.requests:
            query, conditions = REQUESTS[name]
            seconds, digests, tallies = [], [], []
            for run in range(args.runs):
                started = time.perf_counter()
                answer = sc.match(store, query, windows, detail='decided', workers=args.arm_workers,
                                  conditions=conditions)
                seconds.append(time.perf_counter() - started)
                digests.append(answer_digest(answer))
                tallies.append(tally_of(answer))
                if run == 0 and args.keep_results:
                    path = Path(args.keep_results) / f'{name.replace(" ", "_")}.json'
                    path.write_text(json.dumps({'counts': answer['counts'], 'results': answer['results']}))
                    first = {'candidates': answer['candidates'], 'counts': answer['counts']}
                del answer
            out['requests'][name] = {'seconds': seconds, 'digests': digests, 'tallies': tallies,
                                     **(first if args.keep_results else {})}
        out['worker_peak_rss_bytes'] = pool.worker_peak_rss
        import resource
        out['parent_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if sys.platform == 'darwin' else 1024)
    finally:
        es.close_pools()
        store.close()
    return out


def micro_worker(args):
    """D44b: each count request once through `store_conditions.match` (this tree, one worker, `decided`), with
    `store_conditions_counts.unmarked` timed call by call (the timer's overhead included)."""
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    from patterns import store_conditions_counts as scn
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    out = {}
    try:
        windows = windows_of(store)
        real = scn.unmarked
        for name in args.requests:
            if name in NO_COUNT_REQUESTS:
                continue
            query, conditions = REQUESTS[name]
            spent = {'calls': 0, 'seconds': 0.0}

            def timed(answer, v1):
                t = time.perf_counter()
                try:
                    return real(answer, v1)
                finally:
                    spent['seconds'] += time.perf_counter() - t
                    spent['calls'] += 1
            scn.unmarked = timed
            try:
                started = time.perf_counter()
                answer = sc.match(store, query, windows, detail='decided', workers=1, conditions=conditions)
                spent['match_seconds_one_worker'] = time.perf_counter() - started
                spent['refined'] = tally_of(answer)['refined']
            finally:
                scn.unmarked = real
            out[name] = spent
    finally:
        es.close_pools()
        store.close()
    return out


def run_micro(args):
    out = Path(tempfile.mkdtemp(prefix='count-latency-micro-')) / 'out.json'
    subprocess.run([sys.executable, str(Path(__file__).resolve()), '--micro-worker', '--snapshot', args.snapshot_path,
                    '--memory-limit', args.memory_limit, '--worker-output', str(out), '--requests', *args.requests],
                   cwd=ROOT, check=True, timeout=args.budget_seconds)
    result = json.loads(out.read_text())
    shutil.rmtree(out.parent, ignore_errors=True)
    return result


POOL_ENTRY_POINTS = {'execution_store': ('_worker_init', '_worker_ready', '_worker_unit'),
                     'store_conditions_relative': ('_worker_unit',), 'store_conditions_coded': ('_worker_unit',),
                     'store_conditions_gene': ('_worker_unit',), 'store_conditions_counts': ('_worker_unit',)}


def entry_references(targets, modules=None):
    """Changed function names that a pool unit's entry point is, or names in its body (one level, on the AST), over
    `modules` (default: every module with a pool unit): for the controls, the modules the dispatcher routes them to and
    `execution_store`, whose pool they use; the count post-pass's unit is submitted only for a request with a count."""
    import ast
    changed = {f.split('.')[-1] for fs in targets.values() for f in fs}
    found = []
    for module, entries in POOL_ENTRY_POINTS.items():
        if modules is not None and module not in modules:
            continue
        path = ROOT / 'patterns' / f'{module}.py'
        if not path.is_file():
            continue
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, ast.FunctionDef) and node.name in entries:
                names = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)} | \
                        {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}
                hit = sorted(({node.name} | names) & changed)
                if hit:
                    found.append(f'patterns.{module}:{node.name} -> {hit}')
    return found


def control_targets(reference):
    """{module: [changed or added function or Class.method]} over the `patterns/` modules (tests aside) that differ
    from `reference`, by the AST (`tools/d44_measure.changed_definitions`), and the by-name imports of those names
    anywhere in `patterns/` ({module: [names]}, which would bypass a counter)."""
    sys.path.insert(0, str(ROOT / 'tools'))
    import d44_measure
    changed = subprocess.run(['git', 'diff', '--name-only', reference, '--', 'patterns'], cwd=ROOT, check=True,
                             capture_output=True, text=True).stdout.split()
    targets = {}
    for path in changed:
        name = Path(path).name
        if not path.endswith('.py') or name.startswith('test_') or not (ROOT / path).is_file():
            continue
        functions, _, _, _ = d44_measure.changed_definitions(path, reference)
        if functions:
            targets[path[:-3].replace('/', '.')] = functions
    return targets, by_name_imports(targets)


def by_name_imports(targets):
    """The by-name imports anywhere in `patterns/` of the functions in `targets` ({module: [names]}), which would bypass
    a counter."""
    import ast
    bypass = {}
    for path in sorted((ROOT / 'patterns').glob('*.py')):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and node.module and node.level >= 1:
                module = 'patterns.' + node.module
                hit = sorted({a.name for a in node.names} & {f.split('.')[0] for f in targets.get(module, ())})
                if hit:
                    bypass.setdefault(str(path.relative_to(ROOT)), []).extend(hit)
    return bypass


def fixed_targets(path):
    """D48's amendment for rebinding D44b's verdict: the changed functions to count, fixed rather than recomputed by the
    AST against `--reference` (which would also sweep in a later increment's changes): a JSON file that is either a
    count-latency verdict, whose `verdict.control_structural.changed_functions` is taken, or a {module: [names]} map."""
    data = json.loads(Path(path).read_text())
    targets = data['verdict']['control_structural']['changed_functions'] if 'verdict' in data else data
    if not targets or not all(isinstance(v, list) and v for v in targets.values()):
        raise SystemExit(f'{path}: no changed functions to count')
    return {m: list(v) for m, v in targets.items()}, by_name_imports(targets)


def control_worker(args):
    """D44b, F1: each control (a request without a count) once through `store_conditions.match` at 1 worker and at
    14, with a call counter on every changed function (`control_targets`)."""
    import importlib
    targets = json.loads(args.targets)
    calls = {}
    for module, names in targets.items():
        m = importlib.import_module(module)
        for name in names:
            owner, attr = (getattr(m, name.split('.')[0]), name.split('.')[1]) if '.' in name else (m, name)
            real = getattr(owner, attr)
            key = f'{module}:{name}'
            calls[key] = 0

            def counted(*a, _real=real, _key=key, **k):
                calls[_key] += 1
                return _real(*a, **k)
            setattr(owner, attr, counted)
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    runs = {}
    try:
        windows = windows_of(store)
        for name in NO_COUNT_REQUESTS:
            query, conditions = REQUESTS[name]
            for workers in (1, RULED_WORKERS):
                answer = sc.match(store, query, windows, detail='decided', workers=workers, conditions=conditions)
                runs[f'{name} at {workers}'] = len(answer['results'])
                del answer
    finally:
        es.close_pools()
        store.close()
    loaded = sorted(m for m in sys.modules if m.startswith('patterns.'))
    routed = sorted({sc._module(REQUESTS[name][1]).__name__.split('.')[-1] for name in NO_COUNT_REQUESTS})
    return {'calls': calls, 'runs': runs, 'loaded_changed_modules': sorted(m for m in targets if m in loaded),
            'routed': routed}


def run_control(args, targets):
    out = Path(tempfile.mkdtemp(prefix='count-latency-control-')) / 'out.json'
    subprocess.run([sys.executable, str(Path(__file__).resolve()), '--control-worker', '--snapshot', args.snapshot_path,
                    '--memory-limit', args.memory_limit, '--worker-output', str(out), '--targets', json.dumps(targets)],
                   cwd=ROOT, check=True, timeout=args.budget_seconds)
    result = json.loads(out.read_text())
    shutil.rmtree(out.parent, ignore_errors=True)
    return result


def control_check(control, targets, bypass):
    """D44b, F1: met when every counter reads 0 after both controls ran at 1 and 14 workers, no changed function is a
    pool unit's entry point, and no module imports a changed function by name."""
    if control is None:
        return {'met': False, 'ran': False}
    entry = entry_references(targets, set(control.get('routed', ())) | {'execution_store'})
    out = {'ran': len(control['runs']) == 2 * len(NO_COUNT_REQUESTS), 'runs': control['runs'],
           'calls': control['calls'], 'nonzero': sorted(k for k, n in control['calls'].items() if n),
           'changed_functions': targets, 'pool_entry_points_changed': entry, 'by_name_imports': bypass,
           'loaded_changed_modules': control['loaded_changed_modules'], 'routed': control.get('routed')}
    out['met'] = out['ran'] and not out['nonzero'] and not entry and not bypass and bool(control['calls'])
    return out


def run_side(code_root, args, workers, keep=None):
    out = Path(tempfile.mkdtemp(prefix='count-latency-run-')) / 'out.json'
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--code-root', str(code_root),
               '--snapshot', args.snapshot_path, '--arm-workers', str(workers), '--runs', str(args.runs),
               '--memory-limit', args.memory_limit, '--worker-output', str(out), '--requests', *args.requests]
    if keep is not None:
        keep.mkdir(parents=True, exist_ok=True)
        command += ['--keep-results', str(keep)]
    started = time.monotonic()
    subprocess.run(command, cwd=ROOT, check=True, timeout=args.budget_seconds)
    result = json.loads(out.read_text())
    shutil.rmtree(out.parent, ignore_errors=True)
    return {**result, 'wall_seconds': time.monotonic() - started}


# Statistics and the verdict.

def pct(values, q):
    import math
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)] if ordered else None


def statistics_of(records, requests):
    """Per worker count and request: each pair's medians and ratio, every run's times pooled (p50, p95, max), and the
    ratios by order. `records` is [{'workers', 'repeat', 'order', 'reference': run, 'candidate': run}]."""
    out = {}
    for n in sorted({r['workers'] for r in records}):
        mine = sorted((r for r in records if r['workers'] == n), key=lambda r: r['repeat'])
        per_request = {}
        for name in requests:
            pairs = []
            for rec in mine:
                ref = statistics.median(rec['reference']['requests'][name]['seconds'])
                cand = statistics.median(rec['candidate']['requests'][name]['seconds'])
                pairs.append({'repeat': rec['repeat'], 'order': list(rec['order']), 'reference_median_seconds': ref,
                              'candidate_median_seconds': cand, 'ratio': cand / ref,
                              'reference_seconds': rec['reference']['requests'][name]['seconds'],
                              'candidate_seconds': rec['candidate']['requests'][name]['seconds']})
            pooled = {side: [s for rec in mine for s in rec[side]['requests'][name]['seconds']]
                      for side in ('reference', 'candidate')}
            per_request[name] = {
                'pairs': pairs, 'ratio_by_repeat': [p['ratio'] for p in pairs],
                'ratio_max': max(p['ratio'] for p in pairs), 'ratio_min': min(p['ratio'] for p in pairs),
                'ratio_by_order': {first: [p['ratio'] for p in pairs if p['order'][0] == first]
                                   for first in ('reference', 'candidate')},
                **{side: {'p50_seconds': pct(v, .5), 'p95_seconds': pct(v, .95), 'max_seconds': max(v)}
                   for side, v in pooled.items()},
                'ratio_p95_pooled': pct(pooled['candidate'], .95) / pct(pooled['reference'], .95),
                'ratio_median_pooled': statistics.median(pooled['candidate']) / statistics.median(pooled['reference'])}
        out[str(n)] = {'workers': n, 'requests': per_request,
                       'peak_rss_bytes': {side: max(max(r[side]['parent_peak_rss_bytes'], r[side]['worker_peak_rss_bytes'])
                                                    for r in mine) for side in ('reference', 'candidate')}}
    return out


def protocol_check(workers, requests, repeats, runs, identity_ref=None, rules='d43c'):
    """D43b review, S2: the run follows the committed protocol, or no verdict is met; since D43c (review, S1) the
    protocol includes the identity run against the retry base (`--identity-ref`), which D44's rules do not have."""
    out = {'ruled_workers_run': RULED_WORKERS in workers, 'every_request_run': sorted(requests) == sorted(REQUESTS),
           'repeats_at_least_4_and_even': repeats >= 4 and repeats % 2 == 0, 'runs_at_least_3': runs >= 3}
    if RULES[rules].get('normalised'):  # D44b: both controls run beside the count requests
        out['controls_run'] = all(name in requests for name in NO_COUNT_REQUESTS)
    if RULES[rules]['identity_ref']:
        out['identity_ref_run'] = bool(identity_ref)
    out['met'] = all(out.values())
    return out


def answers_check(records, movement_of, requests, rules='d43c'):
    """D43b review, M1: the post-pass did its work, the same way, in every run. For each count request: one answer
    digest per side over every run and worker count (the candidate's digest binds the tally), the candidate's tally the
    same in every run and worker count, with `refined` above 0 and `skipped_mismatch` 0, and only results the reference
    leaves STEP_CONDITION_UNCERTAIN moved. For each request without a count: one digest per side, the same on both
    sides, no tally, nothing moved. `movement_of` is `movement`'s output on the first repeat."""
    out = {}
    for name in requests:
        digests = {side: sorted({d for rec in records for d in rec[side]['requests'][name]['digests']})
                   for side in ('reference', 'candidate')}
        tallies = sorted({json.dumps(t, sort_keys=True) for rec in records
                          for t in rec['candidate']['requests'][name]['tallies']})
        moved = movement_of[name]
        row = {'digests_per_side': {side: len(d) for side, d in digests.items()},
               'one_digest_per_side': all(len(d) == 1 for d in digests.values()),
               'tallies_seen': [json.loads(t) for t in tallies]}
        if name in NO_COUNT_REQUESTS:
            row.update(same_answer_both_sides=digests['reference'] == digests['candidate'], no_tally=tallies == ['null'],
                       nothing_moved=moved['moved'] == 0 and moved['other_results_differing'] == 0)
            row['met'] = row['one_digest_per_side'] and row['same_answer_both_sides'] and row['no_tally'] \
                and row['nothing_moved']
        else:
            tally = json.loads(tallies[0]) if len(tallies) == 1 else None
            row.update(tally_identical=len(tallies) == 1,
                       refined_above_0=bool(tally) and tally['refined'] > 0,
                       skipped_mismatch_0=bool(tally) and tally['skipped_mismatch'] == 0,
                       only_uncertain_moved=moved['only_uncertain_moved'])
            needed = ['one_digest_per_side', 'tally_identical', 'refined_above_0', 'skipped_mismatch_0',
                      'only_uncertain_moved']
            if RULES[rules]['answers_equal_main']:  # D44: main's answer at every result, with main's tally
                theirs = sorted({json.dumps(t, sort_keys=True) for rec in records
                                 for t in rec['reference']['requests'][name]['tallies']})
                row.update(nothing_moved=moved['moved'] == 0 and moved['other_results_differing'] == 0,
                           tally_equal_to_reference=tallies == theirs)
                needed += ['nothing_moved', 'tally_equal_to_reference']
            row['met'] = all(row[k] for k in needed)
        out[name] = row
    return {'requests': out, 'met': bool(out) and all(r['met'] for r in out.values())}


def normalised_of(stats, requests, ruled=RULED_WORKERS):
    """D44b: {count request: [its ratio over the geometric mean of the controls' ratios, pair by pair]} at `ruled`
    workers; the controls are the requests without a count (`NO_COUNT_REQUESTS`), timed in the same processes."""
    import math
    at = stats[str(ruled)]['requests']
    controls = [at[name]['ratio_by_repeat'] for name in NO_COUNT_REQUESTS]
    gmean = [math.prod(ratios) ** (1 / len(ratios)) for ratios in zip(*controls)]
    return {name: [r / g for r, g in zip(at[name]['ratio_by_repeat'], gmean, strict=True)]
            for name in requests if name not in NO_COUNT_REQUESTS}, gmean


def micro_check(micro, stats, ruled=RULED_WORKERS, share=MICRO_SHARE):
    """D44b: each count request's measured `unmarked` seconds at most `share` of its candidate median at `ruled`
    workers."""
    at = stats[str(ruled)]['requests']
    rows = {}
    for name, m in (micro or {}).items():
        median = at[name]['candidate']['p50_seconds']
        rows[name] = {**m, 'request_median_seconds': median, 'share': m['seconds'] / median,
                      'met': m['calls'] > 0 and m['seconds'] <= share * median}
    return {'share_bar': share, 'requests': rows,
            'met': bool(rows) and sorted(rows) == sorted(n for n in at if n not in NO_COUNT_REQUESTS)
            and all(r['met'] for r in rows.values())}


def verdict_of(stats, requests, ruled=RULED_WORKERS, ratio_bar=RATIO_BAR, absolute=ABSOLUTE_BAR,
               no_count_bar=NO_COUNT_BAR, protocol=None, answers=None, identity=None, normalised=False, micro=None,
               control=None):
    """D43b rules 4.1 and 4.2b at `ruled` workers. 4.1: every count request's ratio at most `ratio_bar` in every pair,
    and the absolute request's candidate p95 over all its runs at most the bar. 4.2b: every request without a count has
    a pooled median ratio (the candidate's median over all its runs, over the reference's) at most `no_count_bar`. No
    verdict is met unless the protocol was followed (`protocol_check`), the answers check passed (`answers_check`) and,
    since D43c (review, S1), rule 2's identity with the retry base held at scale (`identity_check`)."""
    at = stats[str(ruled)]['requests']
    counted = [name for name in requests if name not in NO_COUNT_REQUESTS]
    plain = [name for name in requests if name in NO_COUNT_REQUESTS]
    ratios = {name: {'ratio_by_repeat': at[name]['ratio_by_repeat'],
                     'every_pair_at_most': all(r <= ratio_bar for r in at[name]['ratio_by_repeat'])} for name in counted}
    if normalised:  # D44b: the bar on the control-normalised ratio; the raw ratio reported beside it
        by, gmean = normalised_of(stats, requests, ruled)
        ratios = {name: {'ratio_by_repeat': at[name]['ratio_by_repeat'], 'normalised_by_repeat': by[name],
                         'every_pair_at_most': all(r <= ratio_bar for r in by[name])} for name in counted}
    p95 = at[absolute['request']]['candidate']['p95_seconds']
    rule_41 = {'ratio_bar': ratio_bar, 'ratios': ratios,
               'absolute': {**absolute, 'candidate_p95_seconds': p95, 'met': p95 <= absolute['p95_seconds']}}
    rule_41['met'] = all(r['every_pair_at_most'] for r in ratios.values()) and rule_41['absolute']['met']
    rule_42b = {'bar': no_count_bar, 'requests': {
        name: {'ratio_median_pooled': at[name]['ratio_median_pooled'], 'ratio_by_repeat': at[name]['ratio_by_repeat'],
               'met': at[name]['ratio_median_pooled'] <= no_count_bar} for name in plain}}
    rule_42b['met'] = all(r['met'] for r in rule_42b['requests'].values())
    if normalised:  # D44b: the requests without a count are the controls, reported
        rule_42b.update(ruled=False, controls_geometric_mean_by_repeat=gmean)
    protocol = protocol or {'met': False}
    answers = answers or {'met': False}
    if identity == 'not ruled':  # D44: no identity run; the answers check compares with main instead
        rule_2 = {'met': True, 'ruled': False}
    else:
        rule_2 = {'met': bool(identity) and identity['met'],
                  'requests': {} if not identity else {n: r['identical'] for n, r in identity['requests'].items()}}
    out = {'workers': ruled, 'protocol': protocol, 'answers': {'met': answers['met']}, 'rule_2': rule_2,
           'rule_4_1': rule_41, 'rule_4_2b': rule_42b,
           'met': protocol['met'] and answers['met'] and rule_2['met'] and rule_41['met'] and rule_42b['met']}
    if normalised:
        out['micro'] = micro_check(micro, stats, ruled)
        out['control_structural'] = control or {'met': False, 'ran': False}
        out['met'] = (protocol['met'] and answers['met'] and rule_2['met'] and rule_41['met'] and out['micro']['met']
                      and out['control_structural']['met'])
    return out


# D43b rule 4.2a: the matcher benchmark's path (requests without counts through `execution_store.match`) loads only
# files byte-identical to the reference. The files are found by running that path, not listed by hand.

LOADED_FILES_SCRIPT = """
import json, sys, types
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / 'tools'))
import benchmark_matcher as bm
args = types.SimpleNamespace(code_root=str(root), queries=int(sys.argv[3]), budget_policy=bm.DEFAULT_BUDGET_POLICY,
                             no_prune=False, no_prune_ends=False, snapshot=sys.argv[2], memory_limit='2GB',
                             detail='full', arm_workers=2, repeats=1)
bm.worker(args)
files = set()
for module in list(sys.modules.values()):
    path = getattr(module, '__file__', None)
    if path and Path(path).resolve().is_relative_to(root):
        files.add(str(Path(path).resolve().relative_to(root)))
files.update(bm.ARTIFACTS)
print(json.dumps(sorted(files)))
"""


def loaded_files(snapshot, queries=3):
    """Every repository file the matcher benchmark's worker (`tools/benchmark_matcher.py --worker`: queries without
    counts through `execution_store.match`, with a pool of 2) loads over `snapshot` for `queries` queries: its modules,
    found by running it, and the files its report binds (`ARTIFACTS`)."""
    out = subprocess.run([sys.executable, '-c', LOADED_FILES_SCRIPT, str(ROOT), snapshot, str(queries)], cwd=ROOT,
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout.strip().splitlines()[-1])


def structural_check(reference, snapshot, queries=3):
    """D43b rule 4.2a: each file `loaded_files` lists, by sha256 here and at the git ref `reference`."""
    rows = []
    for path in loaded_files(snapshot, queries):
        mine = hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
        shown = subprocess.run(['git', 'show', f'{reference}:{path}'], cwd=ROOT, capture_output=True)
        theirs = hashlib.sha256(shown.stdout).hexdigest() if shown.returncode == 0 else None
        rows.append({'path': path, 'sha256': mine, 'sha256_at_reference': theirs, 'identical': mine == theirs})
    return {'reference': reference, 'queries': queries, 'files': rows,
            'met': bool(rows) and all(r['identical'] for r in rows)}


def movement(reference_dir, candidate_dir, requests):
    """Per request: whether the candidate's answer equals the reference's but at results the reference leaves
    `unresolved` with STEP_CONDITION_UNCERTAIN, how many of those moved, and to what."""
    out = {}
    for name in requests:
        stem = f'{name.replace(" ", "_")}.json'
        ref = json.loads((reference_dir / stem).read_text())['results']
        cand = json.loads((candidate_dir / stem).read_text())['results']
        uncertain = moved = other = 0
        to = {}
        for a, b in zip(ref, cand, strict=True):
            if a['status'] == 'unresolved' and a.get('reason') == 'STEP_CONDITION_UNCERTAIN':
                uncertain += 1
                if json.dumps(a) != json.dumps(b):
                    moved += 1
                    to[b['status']] = to.get(b['status'], 0) + 1
            elif json.dumps(a) != json.dumps(b):
                other += 1
        out[name] = {'listed_results': len(ref), 'reference_step_condition_uncertain': uncertain, 'moved': moved,
                     'moved_to': dict(sorted(to.items())), 'other_results_differing': other,
                     'only_uncertain_moved': other == 0}
    return out


def identity_with_ref(args, scratch, records, ruled):
    """D43c rule 2 at scale: the code at `--identity-ref` (D43c: its retry base) runs each count request once at the
    ruled worker count, and its answer digest (which binds the post-pass's tally) must equal the candidate's, which is
    one digest per request over every run."""
    from tools.benchmark_matcher import extract_ref
    root = scratch / 'identity'
    commit = extract_ref(args.identity_ref, root)
    print(f'identity run ({args.identity_ref}) ...', file=sys.stderr, flush=True)
    names = [name for name in args.requests if name not in NO_COUNT_REQUESTS]
    other = run_side(root, argparse.Namespace(**{**vars(args), 'requests': names, 'runs': 1}), ruled)
    return {'git_ref': args.identity_ref, 'git_commit': commit, 'workers': ruled,
            **identity_check(records, other, names)}


def identity_check(records, other, names):
    """Each count request's candidate digests (one over every run) against the identity run's (`other`, a run's
    output): {'requests': {name: {'identical', 'tally_at_ref'}}, 'met'}."""
    out = {'requests': {}}
    for name in names:
        mine = sorted({d for rec in records for d in rec['candidate']['requests'][name]['digests']})
        theirs = other['requests'][name]['digests']
        out['requests'][name] = {'identical': mine == theirs, 'tally_at_ref': other['requests'][name]['tallies'][0]}
    out['met'] = bool(names) and all(r['identical'] for r in out['requests'].values())
    return out


def paired(args):
    from tools.benchmark_matcher import extract_ref
    size, _, args.snapshot_path = args.snapshot.partition('=')
    manifest = json.loads((Path(args.snapshot_path) / 'manifest.json').read_text())
    scratch = Path(tempfile.mkdtemp(prefix='count-latency-'))
    try:
        codes = {'candidate': {'root': ROOT, 'git_ref': 'this tree', 'git_commit': None}}
        root = scratch / 'reference'
        commit = extract_ref(args.reference, root)
        codes['reference'] = {'root': root, 'git_ref': args.reference, 'git_commit': commit,
                              'files_at_ref': [{'path': p, 'sha256': hashlib.sha256((root / p).read_bytes()).hexdigest()}
                                               for p in REF_FILES if (root / p).is_file()]}
        started = time.monotonic()
        print('structural check (rule 4.2a) ...', file=sys.stderr, flush=True)
        structural = structural_check(args.reference, args.snapshot_path)
        records, kept = [], {}
        for repeat in range(args.repeats):
            order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
            for n in args.workers:
                rec = {'workers': n, 'repeat': repeat, 'order': order}
                for side in order:
                    print(f'count latency repeat {repeat} workers {n} {side} ...', file=sys.stderr, flush=True)
                    keep = scratch / 'answers' / f'{side}-{n}' if repeat == 0 else None
                    rec[side] = run_side(codes[side]['root'], args, n, keep)
                    if keep is not None:
                        kept[(side, n)] = keep
                    print('  ' + '; '.join(f'{name} {statistics.median(r["seconds"]):.2f} s'
                                           for name, r in rec[side]['requests'].items()), file=sys.stderr, flush=True)
                records.append(rec)
        stats = statistics_of(records, args.requests)
        ruled = RULED_WORKERS if RULED_WORKERS in args.workers else args.workers[0]  # (another count: a trial run)
        digests = {side: {name: sorted({d for rec in records for d in rec[side]['requests'][name]['digests']})
                          for name in args.requests} for side in ('reference', 'candidate')}
        first = {(side, rec['workers']): rec[side] for rec in records if rec['repeat'] == 0
                 for side in ('reference', 'candidate')}
        moved = movement(kept[('reference', ruled)], kept[('candidate', ruled)], args.requests)
        identity = {
            'each_code_one_answer_per_request': {side: {name: len(d) == 1 for name, d in by.items()}
                                                 for side, by in digests.items()},
            'check': answers_check(records, moved, args.requests, args.rules),
            'pool_code': {side: sorted({c for rec in records for c in rec[side]['pool_code']})
                          for side in ('reference', 'candidate')},
            'movement': moved,
            'requests_first_repeat': {side: {name: {k: v for k, v in first[(side, ruled)]['requests'][name].items()
                                                    if k not in ('seconds', 'digests', 'tallies')}
                                             for name in args.requests}
                                      for side in ('reference', 'candidate')}}
        if args.identity_ref:
            identity['with_identity_ref'] = identity_with_ref(args, scratch, records, ruled)
        micro = control = None
        if RULES[args.rules].get('micro'):
            print('micro-measure of unmarked (D44b) ...', file=sys.stderr, flush=True)
            micro = run_micro(args)
            print('control-structural check (D44b, F1) ...', file=sys.stderr, flush=True)
            targets, bypass = fixed_targets(args.changed_set) if args.changed_set else control_targets(args.reference)
            control = control_check(run_control(args, targets), targets, bypass)
            control['changed_set_source'] = (f'fixed: {args.changed_set_label or args.changed_set}' if args.changed_set
                                             else f'the AST against {args.reference}')
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    rules = RULES[args.rules]
    verdict = verdict_of(stats, args.requests, ruled, ratio_bar=rules['ratio_bar'],
                         protocol=protocol_check(args.workers, args.requests, args.repeats, args.runs,
                                                 args.identity_ref, args.rules),
                         answers=identity['check'],
                         identity=identity.get('with_identity_ref') if rules['identity_ref'] else 'not ruled',
                         normalised=rules.get('normalised', False), micro=micro, control=control)
    verdict['rules'] = args.rules
    verdict['rule_4_2a'] = {'met': structural['met'], 'files': len(structural['files']),
                            'ruled': rules['structural_ruled'],
                            'differing': [r['path'] for r in structural['files'] if not r['identical']]}
    verdict['met'] = verdict['met'] and (structural['met'] or not rules['structural_ruled'])
    report = {
        'profile': PROFILE, 'protocol': PROTOCOL, 'size': int(size), 'snapshot_id': manifest['snapshot_id'],
        'requests': {name: {'query': q, 'conditions': c} for name, (q, c) in REQUESTS.items() if name in args.requests},
        'windows': f"each patient's second coded seizure (a seizure record that is not a letter mention), "
                   f'{WINDOW_DAYS} days; no window without one',
        'p95_note': ('a p95 over n runs is the ceil(0.95 n)-th smallest: over the 12 runs of a request at one worker '
                     'count (4 repeats of 3 runs) it is the maximum'),
        'detail': 'decided', 'repeats': args.repeats, 'runs_per_request': args.runs, 'workers': list(args.workers),
        'order': 'ABBA: the reference first in even repeats, the candidate first in odd',
        'reference': {k: v for k, v in codes['reference'].items() if k != 'root'},
        'candidate': {k: v for k, v in codes['candidate'].items() if k != 'root'},
        'patients_with_window': records[0]['candidate']['patients_with_window'],
        'by_workers': stats, 'verdict': verdict, 'structural_check': structural, 'answers': identity,
        'seconds': time.monotonic() - started,
        'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS},
        'contains_patient_level_data': False,
        'limitations': ('one machine and one session; synthetic data replicated beyond 10,000 patients; four fixed '
                        'requests with counts and two without, not a clinical mix; a pair\'s time is the median of a few runs in one process, so '
                        'the pairing cancels slow drift, not noise inside a run'),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1) + '\n')
    print(args.output)
    print(json.dumps(report['verdict']), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--snapshot', help='SIZE=SNAPSHOT_DIRECTORY')
    parser.add_argument('--reference', help='git ref of the reference code')
    parser.add_argument('--identity-ref', help='git ref whose answers to the count requests must be byte-identical to '
                        "this tree's (D43c rule 2: the retry base)")
    parser.add_argument('--workers', nargs='+', type=int, default=[RULED_WORKERS, 8])
    parser.add_argument('--repeats', type=int, default=4, help='repeats (even: balanced ABBA)')
    parser.add_argument('--runs', type=int, default=3, help='runs of each request in one process')
    parser.add_argument('--requests', nargs='*', default=list(REQUESTS), choices=list(REQUESTS))
    parser.add_argument('--memory-limit', default='4GB')
    parser.add_argument('--budget-seconds', type=float, default=3600, help='per run')
    parser.add_argument('--output', type=Path, help='default: verification/d43b-count-latency-verdict.json under '
                        '--rules d43c, verification/d44-count-latency-verdict.json under --rules d44')
    parser.add_argument('--rules', choices=sorted(RULES), default='d43c', help="the bars and checks: D43b/D43c's, or D44's")
    parser.add_argument('--changed-set', help="D48: the changed functions the control-structural check counts, fixed (a "
                        "count-latency verdict's recorded set, or a {module: [names]} JSON file) instead of the AST "
                        'against --reference')
    parser.add_argument('--changed-set-label', help='how the report names the fixed set (default: its path)')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--micro-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--control-worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--targets', help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--code-root', help=argparse.SUPPRESS)
    parser.add_argument('--arm-workers', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--keep-results', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        args.worker_output.write_text(json.dumps(worker(args)))
        return 0
    if args.micro_worker:
        args.worker_output.write_text(json.dumps(micro_worker(args)))
        return 0
    if args.control_worker:
        args.worker_output.write_text(json.dumps(control_worker(args)))
        return 0
    if args.output is None:  # D44 review, S2: never a frozen record by default
        args.output = ROOT / OUTPUTS[args.rules]
    if not (args.snapshot and args.reference):
        parser.error('--snapshot and --reference are required')
    paired(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
