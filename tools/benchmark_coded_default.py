#!/usr/bin/env python3
"""D45's rule 4: the latency of the page's coded-only requests, control-normalised (decision log, D45).

The change D45 assesses is a default in the page's translator (`STORE_CODED_ONLY`, and `STORE_GENE_CODED_ONLY` with it),
not code: the store, the engine and every module run the same bytes before and after. What the flip changes is which
request the page sends under "coded records only": today the translator refuses it and the page searches in the
browser; flipped, it sends a coded-only request (profile 1.3, or the joint gene profile), which runs D39's exclusion
code (`patterns/store_conditions_coded.py`). So the two arms of D44's paired protocol are two request sets in this one
tree, not two codes:
- the *candidate* arm sends each template's coded-only request as the flipped translator writes it;
- the *reference* arm sends the same request without the exclusion (`coded_default.cjs` `withoutExclusion`: query and
  windows identical, `exclude_kinds` dropped, the profile the store uses without it), which is the store's answer to
  the same template reading letter mentions: what the page translator writes today with D37's derived reading
  (`codedOnly: 'derived'`: Sequence, Event frequency, Blank) or, for the gene templates (Tree, Graph), with stated
  findings, their query and conditions (`coded_default.cjs requests` checks both);
- both arms send the same *controls*, requests that leave nothing out, so each pair's candidate-over-reference ratio is
  divided by the geometric mean of the controls' ratios in the same processes (D44's lesson, D44b's normalisation).
The ratio is the store's cost of leaving letter mentions out, at the same windows and template. It includes the coded
module's route (its relative-module machinery) against the plain or 1.1 route. It is not the user's before and after:
today "coded only" is refused and the page searches in the browser. What bounds the user's experience after the flip is
the absolute p95 bars (D45 review, S1).

Arms run in fresh processes, back to back, the order alternating across repeats (ABBA), at two sizes:
- *page* (rule 4.1): the page's cohort (48 children) in a store built once by the server's builder
  (`store_parity.build`) outside the timed runs, opened in each arm by the server's own class
  (`store_trajectory.TrajectoryStore`, its build pointed at that snapshot) and answered by its own `match` (the server's
  path: request checks, the conversion to the store's clock, `store_conditions.match` at one worker, `full` detail).
  Each request runs once untimed (recorded: the first request after the server starts fills caches), then `--page-runs`
  times. Controls: two parity trajectories under stated findings (no conditions; a 1.1 value condition).
- *scale* (rule 4.2): the kept 300,000-patient build, `store_conditions.match` with `decided` detail at 14 and 8
  workers, windows from each patient's second coded seizure for 730 days (D43b's), `--scale-runs` runs per request.
  The build holds no series and no gene label, so Event frequency and Blank run as the page writes them and Sequence,
  Tree and Graph without their rise and gene steps. Controls: D43b's `1.1 sequence` and the same sequence with no
  conditions.

Rule 4.3, the structural check (`structural_worker`): every function of `store_conditions_coded` that does exclusion work
gets a call counter (a function with a `kinds` argument, or a `request` whose `exclude` is set, counts only when that is
non-empty; every other function of the module but the profile predicate `is_coded_request` counts every call); the
controls and every reference must read 0, at page size and at 1 and 14 workers at scale, and every candidate more than 0
(the counters work): at one worker each candidate must call `_left_out`, `refined` and `_comparability` with kinds
(`POSITIVE`; `_as_excluded` runs only where a left-out event meets a decided result, and is reported), at 14 workers
it must count more than 0 (D45 review, S3). The counters live in the measuring process only: a zero at 14 workers
covers the dispatcher's own part, and the pool's part rests on the one-worker readings (where every unit runs in the
counted process) together with the AST check of the pool entry points, not on counters in the pool processes. No module of `patterns/` may import a function of the module by name, and no pool-unit entry point
the references and controls reach at scale may name one. Reported beside it (rule 4.4): the time inside the exclusion
work's four disjoint entry points (`_left_out`, `refined`, `_comparability` with kinds, `_as_excluded`) per candidate, at
page size and at 300,000 at one worker in one process, and the translator's own time from the page report.

The answers are hashed (digests), never kept; the report holds aggregates. Synthetic data; one machine.

    python tools/benchmark_coded_default.py --requests-file REQUESTS.json --snapshot 300000=SNAPSHOT_DIR \\
        [--page-report verification/d45-coded-default-page-report.json] [--output FILE]
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MOCKUPS = ROOT / 'demos' / 'mockups'
for p in (str(ROOT), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

PROFILE = 'coded-default-latency-1.0'
PROTOCOL = 'paired-interleaved-1.0, arms of requests in one tree (D45)'
OUTPUT = 'verification/d45-coded-default-latency-verdict.json'
RATIO_BAR = 2.0  # the control-normalised candidate-over-reference ratio, per request and pair (D43b rule 4.1's bar)
PAGE_P95_BAR = 2.0  # seconds, each candidate at the page's size (an interactive bound, a fifth of the D21 target)
SCALE_P95_BAR = 10.0  # seconds, each candidate at 300,000 and 14 workers (the D21 target)
RULED_WORKERS = 14
PAGE_RUNS_MIN, SCALE_RUNS_MIN, REPEATS_MIN = 20, 3, 4
SCALE_CONTROLS = ('1.1 sequence', 'plain sequence')
JOINT_TEMPLATES = ('tree', 'graph')  # judged for the joint profile; the other three for "coded only"
# The requests file must hold every one of these (D45 review, S2): a missing template fails the protocol.
PAGE_TEMPLATES = ('sequence', 'tree', 'graph', 'event frequency', 'blank')
SCALE_SHAPES = ('event frequency', 'blank', 'sequence without its rise', 'tree without its gene and rise steps',
                'graph without its gene and rise steps')
POSITIVE = ('_left_out', 'refined', '_comparability')  # each called by every candidate at one worker (review, S3)
EXEMPT = ('is_coded_request',)  # a predicate on the profile's name: no exclusion work
TIMED = ('_left_out', 'refined', '_comparability', '_as_excluded')  # disjoint entry points of the exclusion work
ARTIFACTS = ('tools/benchmark_coded_default.py', 'tools/benchmark_count_requests.py', 'demos/mockups/coded_default.cjs',
             'demos/mockups/store_trajectory.py', 'demos/mockups/store_parity.py', 'patterns/store_conditions.py',
             'patterns/store_conditions_coded.py', 'patterns/store_conditions_gene.py',
             'patterns/store_conditions_relative.py', 'patterns/store_conditions_counts.py', 'patterns/execution_store.py',
             'patterns/trajectory_graph.py', 'patterns/trajectory_times.py', 'patterns/temporal_stn.py')


def page_digest(answer):
    """sha256 of the server's answer but its timing."""
    return hashlib.sha256(json.dumps({k: v for k, v in answer.items() if k != 'elapsed_ms'},
                                     sort_keys=True).encode()).hexdigest()


# Requests.

def page_requests(data, arm):
    """{name: the page's request body}: each template's request for `arm`, and the controls."""
    out = {name: t[arm] for name, t in data['page'].items()}
    out.update(data['page_controls'])
    return out


def scale_requests(data, arm):
    """{name: (query, conditions)} in the store's clock: each shape's request for `arm`, and the controls."""
    import benchmark_count_requests as bcr
    sys.path.insert(0, str(MOCKUPS))
    import store_parity
    out = {name: (store_parity.to_micros(s[arm]['query']), store_parity.conditions_micros(s[arm]['conditions']))
           for name, s in data['scale'].items()}
    out['1.1 sequence'] = bcr.REQUESTS['1.1 sequence']
    out['plain sequence'] = (bcr._sequence(), None)
    return out


def trajectory_store(snapshot):
    """The server's `TrajectoryStore` on the page's cohort, its build pointed at `snapshot` (built once by the server's
    own builder, `store_parity.build`); everything else is the server's."""
    sys.path.insert(0, str(MOCKUPS))
    import store_parity
    import store_trajectory
    ts = store_trajectory.TrajectoryStore()
    with patch.object(store_parity, 'build', lambda cohort, root: Path(snapshot)):
        ts._open()
    if ts._store is None:
        raise RuntimeError(f'the trajectory store did not open: {ts._error}')
    return ts


def _peak_rss():
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


# Workers, each in its own process.

def page_worker(args):
    data = json.loads(Path(args.requests_file).read_text())
    ts = trajectory_store(args.snapshot)
    out = {'requests': {}, 'worker_peak_rss_bytes': 0}
    try:
        for name, spec in page_requests(data, args.arm).items():
            started = time.perf_counter()
            first = ts.match(spec)
            row = {'first_run_seconds': time.perf_counter() - started, 'seconds': [], 'digests': [page_digest(first)]}
            for _ in range(args.runs):
                started = time.perf_counter()
                answer = ts.match(spec)
                row['seconds'].append(time.perf_counter() - started)
                row['digests'].append(page_digest(answer))
            out['requests'][name] = row
    finally:
        ts.close()
    out['parent_peak_rss_bytes'] = _peak_rss()
    return out


def scale_worker(args):
    import benchmark_count_requests as bcr
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    data = json.loads(Path(args.requests_file).read_text())
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    try:
        pool = es.get_pool(store, args.arm_workers)
        pool.warm()
        windows = bcr.windows_of(store)
        out = {'patients_with_window': len(windows), 'pool_start_seconds': pool.start_seconds, 'requests': {}}
        for name, (query, conditions) in scale_requests(data, args.arm).items():
            row = {'seconds': [], 'digests': []}
            for _ in range(args.runs):
                started = time.perf_counter()
                answer = sc.match(store, query, windows, detail='decided', workers=args.arm_workers,
                                  conditions=conditions)
                row['seconds'].append(time.perf_counter() - started)
                row['digests'].append(bcr.answer_digest(answer))
                del answer
            out['requests'][name] = row
        out['worker_peak_rss_bytes'] = pool.worker_peak_rss
        out['parent_peak_rss_bytes'] = _peak_rss()
    finally:
        es.close_pools()
        store.close()
    return out


def exclusion_targets():
    """Every top-level function and class method of `store_conditions_coded` (Class.method), but `EXEMPT`."""
    tree = ast.parse((ROOT / 'patterns' / 'store_conditions_coded.py').read_text())
    out = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name not in EXEMPT:
            out.append(node.name)
        elif isinstance(node, ast.ClassDef):
            out += [f'{node.name}.{m.name}' for m in node.body if isinstance(m, ast.FunctionDef)]
    return out


def doing_exclusion(function, args, kwargs):
    """Whether a call does exclusion work: a `kinds` argument, or a `request` with `exclude`, that is non-empty; any
    other call of the module's functions counts."""
    try:
        bound = inspect.signature(function).bind_partial(*args, **kwargs).arguments
    except TypeError:
        return True
    if 'kinds' in bound:
        return bool(bound['kinds'])
    if 'request' in bound:
        return bool(getattr(bound['request'], 'exclude', ()))
    return True


class Counters:
    """Call counters (and, on `TIMED`, timers) on the exclusion work of `store_conditions_coded`."""

    def __init__(self):
        from patterns import store_conditions_coded as cc
        self.calls, self.seconds, self.undo = {}, {}, []
        for name in exclusion_targets():
            owner, attr = (getattr(cc, name.split('.')[0]), name.split('.')[1]) if '.' in name else (cc, name)
            real = getattr(owner, attr)
            self.calls[name] = 0

            def counted(*a, _real=real, _name=name, **k):
                if not doing_exclusion(_real, a, k):
                    return _real(*a, **k)
                self.calls[_name] += 1
                started = time.perf_counter()
                try:
                    return _real(*a, **k)
                finally:
                    if _name in TIMED:
                        self.seconds[_name] = self.seconds.get(_name, 0.0) + time.perf_counter() - started
            setattr(owner, attr, counted)
            self.undo.append((owner, attr, real))

    def reset(self):
        for k in self.calls:
            self.calls[k] = 0
        self.seconds = {}

    def total(self):
        return sum(self.calls.values())

    def read(self):
        return {'calls': self.total(), 'nonzero': {k: v for k, v in self.calls.items() if v},
                'timed_seconds': dict(self.seconds), 'timed_total_seconds': sum(self.seconds.values())}


def structural_worker(args):
    """Rule 4.3 (and 4.4's attribution): each control, reference and candidate once with the counters, at page size and
    at scale (controls and references at 1 and 14 workers, candidates at 14 for the counters and at 1 for the timers)."""
    import benchmark_count_requests as bcr
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    data = json.loads(Path(args.requests_file).read_text())
    counters = Counters()
    out = {'targets': list(counters.calls), 'page': {}, 'scale': {}}
    ts = trajectory_store(args.page_snapshot)
    try:
        for arm in ('reference', 'candidate'):
            for name, spec in page_requests(data, arm).items():
                if arm == 'candidate' and name in data['page_controls']:
                    continue
                counters.reset()
                started = time.perf_counter()
                ts.match(spec)
                key = name if name in data['page_controls'] else f'{name} ({arm})'
                out['page'][key] = {**counters.read(), 'seconds': time.perf_counter() - started, 'workers': 1}
    finally:
        ts.close()
    if args.snapshot:
        store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
        try:
            windows = bcr.windows_of(store)
            many = args.arm_workers  # the ruled count (14), passed by the main process
            plan = [('reference', 1), ('reference', many), ('candidate', many), ('candidate', 1)]
            for arm, workers in plan:
                for name, (query, conditions) in scale_requests(data, arm).items():
                    if arm == 'candidate' and name in SCALE_CONTROLS:
                        continue
                    counters.reset()
                    started = time.perf_counter()
                    sc.match(store, query, windows, detail='decided', workers=workers, conditions=conditions)
                    key = name if name in SCALE_CONTROLS else f'{name} ({arm})'
                    out['scale'].setdefault(key, {})[str(workers)] = {**counters.read(),
                                                                      'seconds': time.perf_counter() - started}
        finally:
            es.close_pools()
            store.close()
    return out


POOL_ENTRIES = {'execution_store': ('_worker_init', '_worker_ready', '_worker_unit'),
                'store_conditions_relative': ('_worker_unit',), 'store_conditions_counts': ('_worker_unit',)}


def _coded_aliases(tree):
    """Names a module binds to `store_conditions_coded` (`from . import store_conditions_coded as cc`)."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for a in node.names:
                if a.name == 'store_conditions_coded':
                    out.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.endswith('store_conditions_coded'):
                    out.add((a.asname or a.name).split('.')[0])
    return out


def bypasses():
    """Imports of a `store_conditions_coded` function by name anywhere in `patterns/` (a counter would miss them), and
    the pool-unit entry points the references and controls reach at scale (`POOL_ENTRIES`: those of `execution_store`,
    `store_conditions_relative` and `store_conditions_counts`) that reach into the module themselves: an attribute of a
    name bound to it, or an import of it, in the entry point's body (on the AST)."""
    names = {n.split('.')[0] for n in exclusion_targets()}
    found = []
    for path in sorted((ROOT / 'patterns').glob('*.py')):
        if path.name.startswith('test_'):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom) and (node.module or '').endswith('store_conditions_coded'):
                hit = sorted({a.name for a in node.names} & names)
                if hit:
                    found.append(f'{path.relative_to(ROOT)} imports {hit}')
    entries = []
    for module, functions in POOL_ENTRIES.items():
        tree = ast.parse((ROOT / 'patterns' / f'{module}.py').read_text())
        aliases = _coded_aliases(tree)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in functions:
                inner = aliases | _coded_aliases(node)
                if _coded_aliases(node) or any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                                               and n.value.id in inner for n in ast.walk(node)):
                    entries.append(f'patterns.{module}:{node.name}')
    return found, entries


def structural_check(raw, data, bypass, entries):
    """Rule 4.3's verdict over `structural_worker`'s output."""
    rows, failures = [], []

    def judge(key, reading, want_zero, one_worker):
        positive = want_zero is False and one_worker
        rows.append({'request': key, 'calls': reading['calls'],
                     'want': '0' if want_zero else f"> 0 in each of {', '.join(POSITIVE)}" if positive else '> 0',
                     'entry_points_called': sorted(set(reading.get('nonzero', {})) & set(TIMED))})
        if (reading['calls'] == 0) != want_zero:
            failures.append(f"{key}: {reading['calls']} calls")
        missing = [f for f in POSITIVE if not reading.get('nonzero', {}).get(f)] if positive else []
        if missing:
            failures.append(f'{key}: no call of {missing}')
    for key, reading in raw['page'].items():
        judge(f'page {key}', reading, not key.endswith('(candidate)'), True)
    for key, by in raw['scale'].items():
        for workers, reading in by.items():
            judge(f'scale {key} at {workers}', reading, not key.endswith('(candidate)'), workers == '1')
    expected_page = len(data['page']) * 2 + len(data['page_controls'])
    expected_scale = len(data['scale']) * 4 + len(SCALE_CONTROLS) * 2  # (fewer when a trial runs 1 worker as the many)
    ran = {'page': len(raw['page']) == expected_page,
           'scale': sum(len(v) for v in raw['scale'].values()) == expected_scale}
    return {'targets': raw['targets'], 'exempt': list(EXEMPT), 'positive_control': list(POSITIVE),
            'pool_note': ("a zero at 14 workers covers the dispatcher's own part; the pool's part rests on the "
                          'one-worker readings and the AST check of the pool entry points'),
            'readings': rows, 'failures': failures, 'ran': ran,
            'by_name_imports': bypass, 'pool_entry_references': entries,
            'met': not failures and all(ran.values()) and not bypass and not entries}


# Running the arms.

def _run(args, mode, extra, timeout):
    out = Path(tempfile.mkdtemp(prefix='coded-default-')) / 'out.json'
    command = [sys.executable, str(Path(__file__).resolve()), mode, '--requests-file', str(args.requests_file),
               '--worker-output', str(out), '--memory-limit', args.memory_limit, *extra]
    started = time.monotonic()
    subprocess.run(command, cwd=ROOT, check=True, timeout=timeout)
    result = json.loads(out.read_text())
    shutil.rmtree(out.parent, ignore_errors=True)
    return {**result, 'wall_seconds': time.monotonic() - started}


def run_pairs(args, size, workers_list, runs, extra_of):
    records = []
    for repeat in range(args.repeats):
        order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
        for n in workers_list:
            rec = {'workers': n, 'repeat': repeat, 'order': order}
            for arm in order:
                print(f'coded default {size} repeat {repeat} workers {n} {arm} ...', file=sys.stderr, flush=True)
                rec[arm] = _run(args, f'--{size}-worker', ['--arm', arm, '--runs', str(runs), *extra_of(n)],
                                args.budget_seconds)
                print('  ' + '; '.join(f'{name} {statistics.median(r["seconds"]):.3f} s'
                                       for name, r in rec[arm]['requests'].items()), file=sys.stderr, flush=True)
            records.append(rec)
    return records


# Statistics and the verdict.

def normalised(stats, workers, names, controls):
    """{name: each pair's ratio over the geometric mean of the controls' ratios in that pair}, and those means."""
    at = stats[str(workers)]['requests']
    gmean = [math.prod(rs) ** (1 / len(rs)) for rs in zip(*(at[c]['ratio_by_repeat'] for c in controls))]
    return {n: [r / g for r, g in zip(at[n]['ratio_by_repeat'], gmean, strict=True)] for n in names}, gmean


def answers_check(records, names):
    """Each request's answer is one digest per arm over every run, repeat and worker count; a control's is the same in
    both arms."""
    out = {}
    for name in names:
        digests = {arm: sorted({d for rec in records for d in rec[arm]['requests'][name]['digests']})
                   for arm in ('reference', 'candidate')}
        out[name] = {'digests_per_arm': {a: len(d) for a, d in digests.items()},
                     'met': all(len(d) == 1 for d in digests.values())}
    return out


def size_verdict(records, templates, controls, workers, p95_bar, runs, runs_min, repeats, ruled_run=True,
                 expected=None):
    import benchmark_count_requests as bcr
    stats = bcr.statistics_of(records, [*templates, *controls])
    by, gmean = normalised(stats, workers, templates, controls)
    at = stats[str(workers)]['requests']
    answers = answers_check(records, [*templates, *controls])
    for c in controls:
        same = {rec[a]['requests'][c]['digests'][0] for rec in records for a in ('reference', 'candidate')}
        answers[c]['same_in_both_arms'] = len(same) == 1
        answers[c]['met'] = answers[c]['met'] and len(same) == 1
    rows = {}
    for name in templates:
        p95 = at[name]['candidate']['p95_seconds']
        rows[name] = {'ratio_by_repeat': at[name]['ratio_by_repeat'], 'normalised_by_repeat': by[name],
                      'every_pair_at_most': all(r <= RATIO_BAR for r in by[name]),
                      'candidate_p95_seconds': p95, 'reference_p95_seconds': at[name]['reference']['p95_seconds'],
                      'candidate_p50_seconds': at[name]['candidate']['p50_seconds'],
                      'reference_p50_seconds': at[name]['reference']['p50_seconds'],
                      'p95_met': p95 <= p95_bar, 'answers_met': answers[name]['met']}
        rows[name]['met'] = rows[name]['every_pair_at_most'] and rows[name]['p95_met'] and rows[name]['answers_met']
    protocol = {'ruled_workers_run': ruled_run and str(workers) in stats,
                'every_template_in_the_requests_file': expected is None or sorted(templates) == sorted(expected),
                'repeats_at_least_4_and_even': repeats >= REPEATS_MIN
                and repeats % 2 == 0, f'runs_at_least_{runs_min}': runs >= runs_min,
                'every_request_run': all(set(rec[a]['requests']) == {*templates, *controls}
                                         for rec in records for a in ('reference', 'candidate')),
                'controls_answers_met': all(answers[c]['met'] for c in controls)}
    protocol['met'] = all(protocol.values())
    return stats, {'workers': workers, 'ratio_bar': RATIO_BAR, 'p95_bar_seconds': p95_bar, 'requests': rows,
                   'controls': {'names': list(controls), 'geometric_mean_by_repeat': gmean,
                                'ratio_by_repeat': {c: at[c]['ratio_by_repeat'] for c in controls}},
                   'answers': answers, 'protocol': protocol}


def build_check(snapshot):
    """The kept build's runtime record (its `build.json`) and its manifest's builder digest, against this tree."""
    snap = Path(snapshot)
    build = snap.parent.parent / 'build.json'
    rows = []
    if build.is_file():
        record = json.loads(build.read_text()).get('record') or {}
        for section in ('modules', 'files'):
            for path, digest in (record.get(section) or {}).items():
                mine = hashlib.sha256((ROOT / path).read_bytes()).hexdigest() if (ROOT / path).is_file() else None
                rows.append({'path': path, 'identical': mine == digest})
    manifest = json.loads((snap / 'manifest.json').read_text())
    builder = manifest.get('builder', {}).get('artifacts', {})
    for path, digest in builder.items():
        mine = hashlib.sha256((ROOT / path).read_bytes()).hexdigest() if (ROOT / path).is_file() else None
        rows.append({'path': f'{path} (manifest)', 'identical': mine == digest})
    return {'build_record': str(build), 'snapshot_id': manifest.get('snapshot_id'), 'files': rows,
            'met': bool(rows) and build.is_file() and all(r['identical'] for r in rows)}


def verdict_of(page, scale, structural, build, data):
    coded_page = [n for n in data['page'] if n not in JOINT_TEMPLATES]
    joint_page = [n for n in data['page'] if n in JOINT_TEMPLATES]
    ok = lambda size, names: size['protocol']['met'] and all(size['requests'][n]['met'] for n in names)
    rule_41 = {'coded': ok(page, coded_page), 'joint': ok(page, joint_page)}
    rule_42 = ok(scale, list(scale['requests']))
    return {'rule_4_1': rule_41, 'rule_4_2': rule_42, 'rule_4_3': structural['met'], 'build': build['met'],
            'coded': rule_41['coded'] and rule_42 and structural['met'] and build['met'],
            'joint': rule_41['coded'] and rule_41['joint'] and rule_42 and structural['met'] and build['met']}


def main_run(args):
    data = json.loads(Path(args.requests_file).read_text())
    size, _, args.snapshot_path = args.snapshot.partition('=')
    scratch = Path(tempfile.mkdtemp(prefix='coded-default-page-store-'))
    started = time.monotonic()
    try:
        sys.path.insert(0, str(MOCKUPS))
        import store_parity
        cohort = json.loads((MOCKUPS / 'data' / 'synthetic-paediatric-rare.json').read_text())
        page_snapshot = str(store_parity.build(cohort, scratch / 'store'))
        build = build_check(args.snapshot_path)
        if not build['met']:
            raise SystemExit(f'the kept build does not match this tree: {json.dumps(build)}')
        page_templates, scale_templates = list(data['page']), list(data['scale'])
        page_records = run_pairs(args, 'page', [1], args.page_runs, lambda n: ['--snapshot', page_snapshot])
        page_stats, page = size_verdict(page_records, page_templates, list(data['page_controls']), 1, PAGE_P95_BAR,
                                        args.page_runs, PAGE_RUNS_MIN, args.repeats, expected=PAGE_TEMPLATES)
        page['first_run_seconds'] = {name: {arm: [rec[arm]['requests'][name]['first_run_seconds'] for rec in page_records]
                                            for arm in ('reference', 'candidate')}
                                     for name in [*page_templates, *data['page_controls']]}
        scale_records = run_pairs(args, 'scale', args.workers, args.scale_runs,
                                  lambda n: ['--snapshot', args.snapshot_path, '--arm-workers', str(n)])
        # A trial run at another count reads its statistics there, but the protocol requires 14 (D45 review, S2).
        ruled = RULED_WORKERS if RULED_WORKERS in args.workers else args.workers[0]
        scale_stats, scale = size_verdict(scale_records, scale_templates, list(SCALE_CONTROLS), ruled,
                                          SCALE_P95_BAR, args.scale_runs, SCALE_RUNS_MIN, args.repeats,
                                          ruled_run=RULED_WORKERS in args.workers, expected=SCALE_SHAPES)
        print('structural check (rule 4.3) and the exclusion work\'s time (rule 4.4) ...', file=sys.stderr, flush=True)
        raw = _run(args, '--structural-worker', ['--page-snapshot', page_snapshot, '--snapshot', args.snapshot_path,
                                                 '--arm-workers', str(ruled)],
                   args.budget_seconds * 4)
        bypass, entries = bypasses()
        structural = structural_check(raw, data, bypass, entries)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    micro = {'page': {k: {'timed_total_seconds': v['timed_total_seconds'], 'timed_seconds': v['timed_seconds'],
                          'request_seconds': v['seconds']}
                      for k, v in raw['page'].items() if k.endswith('(candidate)')},
             'scale_one_worker': {k: {'timed_total_seconds': v['1']['timed_total_seconds'],
                                      'timed_seconds': v['1']['timed_seconds'], 'request_seconds': v['1']['seconds'],
                                      'share_of_candidate_p50_at_14_attribution_only': v['1']['timed_total_seconds'] / scale['requests'][
                                          k[:-len(' (candidate)')]]['candidate_p50_seconds']}
                                  for k, v in raw['scale'].items() if k.endswith('(candidate)')},
             'translator': (json.loads(Path(args.page_report).read_text()).get('translator_time')
                            if args.page_report else None),
             'ruled': False,
             'note': ('attribution only, not a bound: one-process, one-worker time inside the exclusion work, beside '
                      "the candidate's 14-worker median")}
    verdict = verdict_of(page, scale, structural, build, data)
    report = {
        'profile': PROFILE, 'protocol': PROTOCOL, 'rule': 'D45 rule 4 (decision log)',
        'arms': {'candidate': 'each template\'s coded-only request as the flipped translator writes it',
                 'reference': 'the same request without the exclusion (query and windows identical)',
                 'what_the_ratio_measures': ("the store's cost of leaving letter mentions out, at the same windows "
                                             "and template, including the coded module's route (its relative-module "
                                             "machinery) against the plain or 1.1 route; not the user's before and "
                                             'after (today "coded only" is refused and the page searches in the '
                                             "browser): the absolute p95 bars bound the user's experience after "
                                             'the flip')},
        'order': 'ABBA: the reference first in even repeats, the candidate first in odd',
        'repeats': args.repeats,
        'requests_file': {'sha256': hashlib.sha256(Path(args.requests_file).read_bytes()).hexdigest(),
                          'cohort_sha256': data['cohort_sha256'], 'translators': data['translators'],
                          'requests': {'page': {n: {'mode': t['mode'],
                                                    'candidate': {k: t['candidate'][k] for k in ('query', 'conditions')},
                                                    'reference': {k: t['reference'][k] for k in ('query', 'conditions')}}
                                                for n, t in data['page'].items()},
                                       'page_controls': {n: {k: c[k] for k in ('query', 'conditions')}
                                                         for n, c in data['page_controls'].items()},
                                       'scale': data['scale']}},
        'page': {'size': 'the page\'s cohort (48 children)', 'path': 'store_trajectory.TrajectoryStore.match, one worker, full detail',
                 'runs_per_request': args.page_runs, 'by_workers': page_stats, 'rule_4_1': page},
        'scale': {'size': int(size), 'snapshot_id': build['snapshot_id'], 'detail': 'decided',
                  'windows': "each patient's second coded seizure, 730 days (D43b's)", 'workers': list(args.workers),
                  'runs_per_request': args.scale_runs,
                  'patients_with_window': scale_records[0]['candidate']['patients_with_window'],
                  'by_workers': scale_stats, 'rule_4_2': scale},
        'rule_4_3': structural, 'rule_4_4': micro, 'build': build, 'verdict': verdict,
        'seconds': time.monotonic() - started,
        'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS},
        'contains_patient_level_data': False,
        'limitations': ('one machine and one session; synthetic data replicated beyond 10,000 patients; at 300,000 the '
                        'kept build holds no series and no gene label, so three templates run without their rise and '
                        'gene steps and the joint profile is timed at the page\'s size only; a pair\'s time is the '
                        'median of runs in one process'),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1) + '\n')
    print(args.output)
    print(json.dumps(verdict), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--requests-file', type=Path, required=True, help='coded_default.cjs requests --output FILE')
    parser.add_argument('--snapshot', help='SIZE=SNAPSHOT_DIRECTORY (the kept 300,000 build)')
    parser.add_argument('--page-report', help="coded_default.cjs page's report, for the translator's time (reported)")
    parser.add_argument('--workers', nargs='+', type=int, default=[RULED_WORKERS, 8])
    parser.add_argument('--repeats', type=int, default=REPEATS_MIN)
    parser.add_argument('--page-runs', type=int, default=PAGE_RUNS_MIN)
    parser.add_argument('--scale-runs', type=int, default=SCALE_RUNS_MIN)
    parser.add_argument('--memory-limit', default='4GB')
    parser.add_argument('--budget-seconds', type=float, default=3600, help='per arm process')
    parser.add_argument('--output', type=Path, default=ROOT / OUTPUT)
    for hidden in ('--page-worker', '--scale-worker', '--structural-worker'):
        parser.add_argument(hidden, action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--arm', choices=('reference', 'candidate'), help=argparse.SUPPRESS)
    parser.add_argument('--runs', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--arm-workers', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--page-snapshot', help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for flag, fn in (('page_worker', page_worker), ('scale_worker', scale_worker),
                     ('structural_worker', structural_worker)):
        if getattr(args, flag):
            args.worker_output.write_text(json.dumps(fn(args)))
            return 0
    if not args.snapshot:
        parser.error('--snapshot is required')
    main_run(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
