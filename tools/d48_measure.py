#!/usr/bin/env python3
"""D48's ruled measurements, reused by D48b: the hull check of a joint search (decision log, D48 and D48b).

    python tools/d48_measure.py worlds --cases 50000 --mid-cases 20000 --seed d48b-rule
    python tools/d48_measure.py oracle --refuted 20000 --seed d48b-oracle-rule
    python tools/d48_measure.py defects --cases 5000 --networks 20000 --seed d48b-defects-rule
    python tools/d48_measure.py identity --main REF --snapshot 1000=DIR --snapshot 10000=DIR --queries 50
    python tools/d48_measure.py latency --main REF --snapshot 300000=DIR --queries 30 --repeats 4 --runs 3 \\
        --workers 14 8 --output verification/d48b-search-latency-verdict.json
    (each with --output FILE; the first four print their summary as JSON)

- `worlds` (rules 1.1 and 1.3): D40's four budget-stressing generators (`patterns/test_trajectory_graph.BUDGET_GENERATORS`),
  new seeds, every case through `d48_check` with HULL_AFTER = 1 (every search with a budget above 1 is checked before its
  first step), and again at HULL_AFTER 2 and 3 (`--mid-after`: the check fires after a first descent, with a partial
  binding on the stack, as at 1,000 in production): the worlds check, the truth by enumeration and the reference on every decided result, the switch off's
  result (a changed result must have been undecided; a decided one must not change in any field), and the same case at
  a search budget of 40,000, where no search stops, equal to the switch off's in every field.
- `oracle` (rule 1): random small networks (`oracle_check`: a budgeted case, one of its relaxation sets, each step's
  candidates as its domain, random order facts); wherever `_hull_refutes` refutes, a brute force over every binding and
  every world (no network) finds none that satisfies the constraints and the facts that apply.
- `defects` (rule 4): `planted_hull_defects`, each in place of `_hull_refutes`, on the generators (HULL_AFTER = 1) and
  the oracle; each is caught when some case fails a check or the oracle finds a wrong refutation.
- `identity` (rules 2 and 3): this tree and MAIN (extracted from git, as the matcher benchmark extracts its arms), each
  in its own process, on kept scaling builds with the benchmark's queries (`query_inputs`), at both budget policies;
  every engine result (`trajectory_graph.match_prepared` and the store's `_coupled_match`, before the store's coverage
  and excluded-records rules) and every store result compared, and each changed engine result checked against MAIN's at
  a search budget of 1,000,000. Aggregates only.
- `latency` (rule 5): the structural check (a), the paired, control-normalised timing (b), the micro-measured cost bound
  (c), and the verdict.
  - (a) This tree, in one process at one worker on the snapshot, runs every query once with a call counter on `_meter`,
    `_HullBudget.spend`, `_hull_refutes` and `_backtrack`. A query that calls `_meter` or `_backtrack` is *searching*
    (one whose searches are all certainty searches, on MAIN's `Budget`, runs no changed line either, but is ruled with
    the searching ones); every other query is a *control*. By the AST against MAIN, `trajectory_graph.py` changes only in
    D48's definitions; `decide` and `_exact_with_facts` equal MAIN's once each `_meter(budget, <lambda>)` is read as
    `Budget(budget)` and the `hull_refuted` statement after the search is removed, so on a control no changed line
    runs; `_backtrack` and `Budget` are MAIN's; no other file under `patterns/` differs. Met when every control reads 0
    on every counter, `_hull_refutes` is called on no query, and the checks on the AST hold.
  - (b) MAIN and this tree run back to back, each in a fresh process with a warm pool (`benchmark_matcher.run_worker`),
    ABBA over the repeats, at each worker count; each process runs every query `--runs` times in a row, and a query's
    time in a pair is the median of its runs. Each searching query's candidate-over-MAIN ratio in a pair is divided by
    the geometric mean of the same pair's control ratios. Met at 14 workers when every normalised ratio is at most 1.05
    in every pair, the candidate's p95 over the queries is at most 10 s in every pair, and both codes' answers (per-query
    digests) are identical.
  - (c) This tree, in the same counting process: each searching query's `_HullBudget.spend` calls times the measured
    per-call cost of `_HullBudget.spend` over `Budget.spend`, plus its meters times the measured extra cost of a
    `_meter` with its closure and the `hull_refuted` test over a `Budget`, plus the time inside `_hull_refutes` (timed
    call by call): at most 1% of the query's median time at 14 workers (a one-process total against a 14-worker
    median, which overstates it).

Synthetic data only; the outputs hold counts and seconds.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

PROFILE = 'd48-search-latency-1.0'
PROTOCOL = 'paired-interleaved-1.0, runs per query, control-normalised (D44b)'
ENGINE = 'patterns/trajectory_graph.py'
ARTIFACTS = ('tools/d48_measure.py', 'tools/benchmark_matcher.py', 'tools/benchmark_execution_store.py',
             'patterns/trajectory_graph.py', 'patterns/execution_store.py', 'patterns/trajectory_times.py',
             'patterns/temporal_stn.py')
RATIO_BAR = 1.05
P95_BAR = 10.0
MICRO_SHARE = 0.01
RULED_WORKERS = 14
NEW_DEFINITIONS = ['_HullBudget.__init__', '_HullBudget.spend', '_exact_with_facts', '_hull_refutes', '_meter', 'decide']
NEW_VALUES = ['HULL_AFTER', '_HULL']
SITES = ('decide', '_exact_with_facts')


def digest(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def classify(failure):
    if failure.startswith('worlds:'):
        return 'worlds_violation'
    if 'the truth is' in failure:
        return 'truth_mismatch'
    if 'the reference says' in failure:
        return 'reference_mismatch'
    if failure.startswith('a decided result changed'):
        return 'decided_changed'
    if failure.startswith('at a budget of 40,000'):
        return 'large_budget_differs'
    return 'other'


# Rule 1: the worlds check, and the oracle.

def worlds_run(cases, seed, refute=None, after=1):
    from patterns import test_trajectory_graph as t
    from patterns import trajectory_graph as tg
    out = {}
    saved = tg._hull_refutes
    if refute is not None:
        tg._hull_refutes = refute
    try:
        for name, make in t.BUDGET_GENERATORS:
            rng = random.Random(f'{seed}-{name}')
            seen, failed = Counter(), Counter()
            for _ in range(cases):
                q, p = make(rng)
                failures = t.d48_check(q, p, seen, after=after)
                for f in failures:
                    failed[classify(f)] += 1
                failed['cases_failing'] += bool(failures)
            out[name] = {'cases': cases, **{k: seen[k] for k in ('checks', 'refuted', 'to a decision', 'within undecided',
                                                                  'undecided before')},
                         'failures': dict(failed)}
    finally:
        tg._hull_refutes = saved
    return out


def oracle_run(refuted, seed, refute=None, networks=None):
    from patterns import test_trajectory_graph as t
    rng = random.Random(seed)
    counted = Counter()
    while (counted['refuted'] < refuted) if networks is None else (counted['drawn'] < networks):
        ran, was_refuted, wrong = t.oracle_check(rng, refute)
        counted['drawn'] += 1
        counted['ran'] += ran
        counted['refuted'] += was_refuted
        counted['wrong'] += wrong
    return dict(counted)


def worlds_cmd(args):
    """Rule 1.1 at HULL_AFTER = 1 (`--cases` per generator, at least one refutation each), and rule 1.3 at each of
    `--mid-after` (`--mid-cases` per generator; the check fires after a first descent, D48 review S2): 0 failures."""
    from patterns import trajectory_graph as tg
    report = {'rule': 'D48b rules 1.1 and 1.3 (worlds)', 'default_hull_after': tg.HULL_AFTER, 'by_hull_after': {}}
    met = True
    for after, cases, seed in [(1, args.cases, args.seed)] + [(a, args.mid_cases, f'{args.seed}-after-{a}')
                                                             for a in args.mid_after]:
        generators = worlds_run(cases, seed, after=after)
        failing = sum(g['failures'].get('cases_failing', 0) for g in generators.values())
        entry = {'cases_per_generator': cases, 'seed': seed, 'generators': generators, 'cases_failing': failing,
                 'violations': sum(g['failures'].get('worlds_violation', 0) for g in generators.values()),
                 'refuted': sum(g['refuted'] for g in generators.values())}
        entry['met'] = failing == 0 and (after != 1 or all(g['refuted'] > 0 for g in generators.values()))
        report['by_hull_after'][str(after)] = entry
        met = met and entry['met']
    report['met'] = met
    return report


def oracle_cmd(args):
    counts = oracle_run(args.refuted, args.seed)
    return {'rule': 'D48b rule 1 (oracle)', **counts, 'met': counts['wrong'] == 0 and counts['refuted'] >= args.refuted}


def defects_cmd(args):
    from patterns import test_trajectory_graph as t
    out = {}
    for name, planted in t.planted_hull_defects().items():
        worlds_part = worlds_run(args.cases, f'{args.seed}-{name}', planted)
        oracle_part = oracle_run(None, f'{args.seed}-oracle-{name}', planted, networks=args.networks)
        failing = {g: v['failures'].get('cases_failing', 0) for g, v in worlds_part.items()}
        out[name] = {'cases_failing_by_generator': failing,
                     'failures_by_kind': dict(sum((Counter(v['failures']) for v in worlds_part.values()), Counter())),
                     'oracle': oracle_part, 'caught': sum(failing.values()) + oracle_part['wrong'] > 0}
    wrongly = ('refutes always', 'the refutation inverted')
    met = all(v['caught'] for v in out.values()) and all(out[n]['oracle']['wrong'] > 0 for n in wrongly)
    return {'rule': 'D48b rule 4 (planted defects)', 'defects': out, 'met': met}


# Rules 2 and 3: identity on the kept builds, against MAIN.

def extract(ref, into):
    import benchmark_matcher as bm
    return bm.extract_ref(ref, into)


def identity_worker(args):
    """One code (`--code-root`), one snapshot: every engine and store result of every query, written as JSON lines."""
    import benchmark_matcher as bm
    bench, es = bm.import_arm(args.code_root)
    from patterns import trajectory_graph as tg
    if args.budget:
        bench.BUDGET_POLICIES['engine-default'] = {**bench.BUDGET_POLICIES['engine-default'], 'search_budget': args.budget}
    inputs, _ = bench.query_inputs(args.queries, args.policy)
    seeds = set(json.loads(args.seeds)) if args.seeds else None
    engine = {}
    inner, coupled = tg.match_prepared, getattr(es, '_coupled_match', None)
    def spy(pq, patient, trusted=False):
        r = inner(pq, patient, trusted)
        engine[patient['patient_id']] = r
        return r
    tg.match_prepared = spy
    if coupled is not None:
        def spy_coupled(pq, patient, which):
            r = coupled(pq, patient, which)
            engine[patient['patient_id']] = r
            return r
        es._coupled_match = spy_coupled
    store = es.open_store(args.snapshot, 1, 'files', '4GB')
    with open(args.worker_output, 'w') as out:
        try:
            ids = [r[0] for r in store.query('SELECT patient_id FROM patients ORDER BY patient_idx')]
            bounds = {pid: (lo, hi) for pid, lo, hi in store.query(
                "SELECT p.patient_id, min(c.lo), max(c.hi) FROM patients p LEFT JOIN coverage c USING (patient_idx) "
                "WHERE p.patient_id NOT LIKE '%~r%' GROUP BY p.patient_id")}
            for seed, query in inputs:
                if seeds is not None and seed not in seeds:
                    continue
                engine.clear()
                windows = bench.expand_windows(bench.pool_windows(seed, bounds), ids)
                result = es.match(store, query, windows)
                out.write(json.dumps({'seed': seed, 'budget': query.get('search_budget'), 'counts': result['counts'],
                                      'store': {r['patient_id']: r for r in result['results']},
                                      'engine': engine}, sort_keys=True) + '\n')
        finally:
            store.close()


def run_identity_worker(code_root, snapshot, queries, policy, scratch, budget=None, seeds=None):
    out = Path(tempfile.mkstemp(prefix='d48-identity-', suffix='.jsonl', dir=scratch)[1])
    command = [sys.executable, str(Path(__file__).resolve()), 'identity-worker', '--code-root', str(code_root),
               '--snapshot', str(snapshot), '--queries', str(queries), '--policy', policy, '--worker-output', str(out)]
    if budget:
        command += ['--budget', str(budget)]
    if seeds is not None:
        command += ['--seeds', json.dumps(sorted(seeds))]
    subprocess.run(command, cwd=ROOT, check=True)
    rows = {}
    with open(out) as f:
        for line in f:
            row = json.loads(line)
            rows[row['seed']] = row
    out.unlink()
    return rows


def undecided(r):
    return r['status'] == 'unresolved' or r.get('complete') is False


def identity_cmd(args):
    scratch = Path(tempfile.mkdtemp(prefix='d48-identity-'))
    try:
        main_root = scratch / 'main'
        commit = extract(args.main, main_root)
        report = {'rule': 'D48b rules 2 and 3 (identity on the kept builds)', 'main': args.main, 'main_commit': commit,
                  'sizes': {}}
        met = True
        for item in args.snapshot:
            size, _, path = item.partition('=')
            entry = report['sizes'][size] = {}
            for policy in ('engine-default', 'drawn-stress'):
                started = time.monotonic()
                mine = run_identity_worker(ROOT, path, args.queries, policy, scratch)
                theirs = run_identity_worker(main_root, path, args.queries, policy, scratch)
                moved, store_moved, transitions, decided_changed, other = Counter(), Counter(), Counter(), 0, 0
                for seed in mine:
                    a, b = theirs[seed], mine[seed]
                    for pid in set(a['engine']) | set(b['engine']):
                        old, new = a['engine'].get(pid), b['engine'].get(pid)
                        if old == new:
                            continue
                        moved[seed] += 1
                        if old is None or new is None or not undecided(old):
                            decided_changed += 1
                        else:
                            transitions[f"{old['status']}/{old.get('reason') or old.get('note')} -> {new['status']}"
                                        f"{'' if undecided(new) else ' (decided)'}"] += 1
                    store_diff = sum(a['store'].get(pid) != b['store'].get(pid) for pid in set(a['store']) | set(b['store']))
                    store_moved[seed] += store_diff
                    if store_diff and not moved[seed]:
                        other += 1
                # Each changed engine result against MAIN's at a search budget of 1,000,000.
                large_equal = large_differs = 0
                if moved and policy == 'engine-default':
                    large = run_identity_worker(main_root, path, args.queries, policy, scratch, 1_000_000, set(moved))
                    for seed in moved:
                        for pid, new in mine[seed]['engine'].items():
                            if new != theirs[seed]['engine'].get(pid):
                                if large[seed]['engine'].get(pid) == new:
                                    large_equal += 1
                                else:
                                    large_differs += 1
                ok = decided_changed == 0 and other == 0 and large_differs == 0 and (
                    policy == 'engine-default' or not moved)
                met = met and ok
                entry[policy] = {'queries': len(mine),
                                 'engine_results_compared': sum(len(theirs[s]['engine']) for s in theirs),
                                 'store_results_compared': sum(len(theirs[s]['store']) for s in theirs),
                                 'engine_results_changed': sum(moved.values()),
                                 'changed_by_seed': dict(sorted(moved.items())),
                                 'store_results_changed_by_seed': {s: n for s, n in sorted(store_moved.items()) if n},
                                 'transitions': dict(transitions), 'decided_results_changed': decided_changed,
                                 'store_changes_without_an_engine_change': other,
                                 'changed_equal_to_main_at_budget_1000000': large_equal,
                                 'changed_not_equal_to_main_at_budget_1000000': large_differs,
                                 'counts_identical_seeds': sum(mine[s]['counts'] == theirs[s]['counts'] for s in mine),
                                 'seconds': time.monotonic() - started, 'met': ok}
        report['met'] = met
        return report
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# Rule 5: latency.

def _normalise_site(fn):
    """`decide` or `_exact_with_facts` with D48's lines read as MAIN's: `_meter(budget, <lambda>)` as `Budget(budget)`, and
    the `if getattr(meter, 'refuted', False): ...` statement removed."""
    class Undo(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id == '_meter' and len(node.args) == 2 \
                    and isinstance(node.args[1], ast.Lambda):
                return ast.Call(func=ast.Name(id='Budget', ctx=ast.Load()), args=[node.args[0]], keywords=[])
            return node

        def visit_If(self, node):
            self.generic_visit(node)
            t = node.test
            if isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id == 'getattr' and \
                    len(t.args) == 3 and isinstance(t.args[1], ast.Constant) and t.args[1].value == 'refuted':
                return None
            return node
    return ast.dump(Undo().visit(fn))


def _functions(source):
    tree = ast.parse(source)
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef):
            out[node.name] = node
    return out


def ast_check(main):
    """Rule 5(a)'s checks on the AST against MAIN."""
    import d44_measure
    functions, values, _, other = d44_measure.changed_definitions(ENGINE, main)
    changed_files = subprocess.run(['git', 'diff', '--name-only', main, '--', 'patterns'], cwd=ROOT, check=True,
                                   capture_output=True, text=True).stdout.split()
    code_files = [f for f in changed_files if f.endswith('.py') and not Path(f).name.startswith('test_')]
    theirs = _functions(subprocess.run(['git', 'show', f'{main}:{ENGINE}'], cwd=ROOT, check=True, capture_output=True,
                                       text=True).stdout)
    mine = _functions((ROOT / ENGINE).read_text())
    sites = {name: _normalise_site(mine[name]) == ast.dump(theirs[name]) for name in SITES}
    same = {name: ast.dump(mine[name]) == ast.dump(theirs[name]) for name in ('_backtrack', 'Budget')}
    out = {'changed_definitions': functions, 'changed_values': values, 'other_changes': other,
           'changed_code_files_under_patterns': code_files, 'sites_equal_to_main_once_normalised': sites,
           'unchanged_at_main': same}
    out['met'] = (sorted(functions) == sorted(NEW_DEFINITIONS) and sorted(values) == sorted(NEW_VALUES)
                  and other == ['_HullBudget#class'] and code_files == [ENGINE] and all(sites.values())
                  and all(same.values()))
    return out


def count_worker(args):
    """Rule 5(a) and (c): this tree, one process, one worker; every query once with counters, then the micro-benchmarks."""
    import benchmark_matcher as bm
    bench, es = bm.import_arm(ROOT)
    from patterns import trajectory_graph as tg
    counts = Counter()
    hull_seconds = [0.0]
    real_meter, real_refutes, real_backtrack = tg._meter, tg._hull_refutes, tg._backtrack
    real_spend = tg._HullBudget.spend
    def meter(limit, refute):
        counts['_meter'] += 1
        return real_meter(limit, refute)
    def refutes(*a):
        counts['_hull_refutes'] += 1
        t = time.perf_counter()
        try:
            return real_refutes(*a)
        finally:
            hull_seconds[0] += time.perf_counter() - t
    def backtrack(*a):
        counts['_backtrack'] += 1
        return real_backtrack(*a)
    def spend(self):
        counts['_HullBudget.spend'] += 1
        return real_spend(self)
    tg._meter, tg._hull_refutes, tg._backtrack, tg._HullBudget.spend = meter, refutes, backtrack, spend
    inputs, _ = bench.query_inputs(args.queries, bench.BUDGET_POLICY)
    store = es.open_store(args.snapshot, 1, 'files', '4GB')
    rows = []
    try:
        ids = [r[0] for r in store.query('SELECT patient_id FROM patients ORDER BY patient_idx')]
        bounds = {pid: (lo, hi) for pid, lo, hi in store.query(
            "SELECT p.patient_id, min(c.lo), max(c.hi) FROM patients p LEFT JOIN coverage c USING (patient_idx) "
            "WHERE p.patient_id NOT LIKE '%~r%' GROUP BY p.patient_id")}
        for seed, query in inputs:
            counts.clear()
            hull_seconds[0] = 0.0
            windows = bench.expand_windows(bench.pool_windows(seed, bounds), ids)
            es.match(store, query, windows)
            rows.append({'seed': seed, 'calls': {k: counts[k] for k in ('_meter', '_HullBudget.spend', '_hull_refutes',
                                                                          '_backtrack')},
                         'hull_seconds': hull_seconds[0]})
    finally:
        store.close()
    tg._meter, tg._hull_refutes, tg._backtrack, tg._HullBudget.spend = real_meter, real_refutes, real_backtrack, real_spend
    json.dump({'rows': rows, 'micro': micro_costs()}, open(args.worker_output, 'w'))


def micro_costs(n=1_000_000, repeats=7):
    """Per-call costs on this machine: `_HullBudget.spend` against `Budget.spend` (below the threshold, the common
    path), and a `_meter` with its closure and the `hull_refuted` test against a `Budget`. The median of `repeats`; each
    extra cost is floored at 0."""
    from patterns import trajectory_graph as tg
    def per_call(make, body):
        times = []
        for _ in range(repeats):
            x = make()
            t = time.perf_counter()
            body(x)
            times.append((time.perf_counter() - t) / n)
        return statistics.median(times)
    def spends(b):
        s = b.spend
        for _ in range(n):
            s()
    plain = per_call(lambda: tg.Budget(10 ** 12), spends)
    with_hull = per_call(lambda: tg._HullBudget(10 ** 12, lambda: False), spends)
    def plain_site(_):
        for _ in range(n):
            m = tg.Budget(40_000)
    def hull_site(_):
        domains = constraints = facts = times = step_ids = None
        for _ in range(n):
            m = tg._meter(40_000, lambda: tg._hull_refutes(step_ids, domains, constraints, facts, times))
            if getattr(m, 'refuted', False):
                pass
    site_plain = per_call(lambda: None, plain_site)
    site_hull = per_call(lambda: None, hull_site)
    return {'spend_plain_seconds': plain, 'spend_hull_seconds': with_hull, 'spend_extra_seconds': max(0.0, with_hull - plain),
            'site_plain_seconds': site_plain, 'site_hull_seconds': site_hull,
            'site_extra_seconds': max(0.0, site_hull - site_plain), 'calls_per_measure': n, 'repeats': repeats}


def run_count_worker(snapshot, queries, scratch):
    out = Path(tempfile.mkstemp(prefix='d48-count-', suffix='.json', dir=scratch)[1])
    subprocess.run([sys.executable, str(Path(__file__).resolve()), 'count-worker', '--snapshot', str(snapshot),
                    '--queries', str(queries), '--worker-output', str(out)], cwd=ROOT, check=True)
    result = json.loads(out.read_text())
    out.unlink()
    return result


def structural_of(rows, ast_part):
    searching = [r['seed'] for r in rows if r['calls']['_meter'] > 0 or r['calls']['_backtrack'] > 0]
    controls = [r['seed'] for r in rows if r['seed'] not in searching]
    meters = [r['seed'] for r in rows if r['calls']['_meter'] > 0]
    nonzero = [r['seed'] for r in rows if r['seed'] in controls and any(r['calls'].values())]
    refuting = [r['seed'] for r in rows if r['calls']['_hull_refutes'] > 0]
    out = {'searching': searching, 'controls': controls, 'building_a_meter': meters, 'controls_nonzero': nonzero,
           'hull_checks_on': refuting, 'per_query': rows, 'ast': ast_part}
    out['met'] = bool(controls) and not nonzero and not refuting and ast_part['met']
    return out


def micro_of(rows, micro, medians):
    out, met = [], True
    for r in rows:
        if r['calls']['_meter'] == 0:
            continue
        cost = (r['calls']['_HullBudget.spend'] * micro['spend_extra_seconds'] + r['calls']['_meter'] * micro['site_extra_seconds']
                + r['hull_seconds'])
        share = cost / medians[r['seed']]
        out.append({'seed': r['seed'], 'spend_calls': r['calls']['_HullBudget.spend'], 'meters': r['calls']['_meter'],
                    'hull_seconds': r['hull_seconds'], 'bound_seconds': cost, 'median_seconds_at_14': medians[r['seed']],
                    'share': share})
        met = met and share <= MICRO_SHARE
    return {'per_query': out, 'costs': micro, 'bar': MICRO_SHARE, 'met': met}


def paired_of(records, searching, controls):
    """Per worker count and pair: each query's median over its runs, the ratios, the control-normalised ratios of the
    searching queries, the candidate's p95, and the digests."""
    import benchmark_matcher as bm
    by_workers = {}
    digests = {'reference': {}, 'candidate': {}}
    for rec in records:
        med = {}
        for side in ('reference', 'candidate'):
            times = {}
            for run in rec[side]['runs']:
                for row in run:
                    times.setdefault(row['seed'], []).append(row['match_seconds'])
                    if row.get('digest'):
                        digests[side].setdefault(row['seed'], set()).add(row['digest'])
            med[side] = {s: statistics.median(v) for s, v in times.items()}
        ratio = {s: med['candidate'][s] / med['reference'][s] for s in med['candidate']}
        control = math.exp(statistics.mean(math.log(ratio[s]) for s in controls))
        pair = {'repeat': rec['repeat'], 'order': list(rec['order']), 'control_geometric_mean': control,
                'normalised': {s: ratio[s] / control for s in searching}, 'raw': {s: ratio[s] for s in searching},
                'candidate_p95': bm.summary(list(med['candidate'].values()))['p95'],
                'reference_p95': bm.summary(list(med['reference'].values()))['p95'],
                'candidate_max': max(med['candidate'].values()),
                'candidate_medians': med['candidate'], 'reference_medians': med['reference'],
                'peak_rss_bytes': {s: rec[s]['peak_rss_bytes'] for s in ('reference', 'candidate')}}
        by_workers.setdefault(str(rec['workers']), []).append(pair)
    seeds = sorted(digests['candidate'])
    identical = [s for s in seeds if digests['candidate'][s] == digests['reference'].get(s) and len(digests['candidate'][s]) == 1]
    return by_workers, {'compared': len(seeds), 'identical': len(identical),
                        'mismatching_seeds': [s for s in seeds if s not in identical]}


def latency_cmd(args):
    import benchmark_matcher as bm
    (item,) = args.snapshot
    size, _, path = item.partition('=')
    size = int(size)
    manifest = json.loads((Path(path) / 'manifest.json').read_text())
    scratch = Path(tempfile.mkdtemp(prefix='d48-latency-'))
    started = time.monotonic()
    try:
        main_root = scratch / 'main'
        commit = extract(args.main, main_root)
        print('structural and micro counts ...', file=sys.stderr, flush=True)
        counted = run_count_worker(path, args.queries, scratch)
        structural = structural_of(counted['rows'], ast_check(args.main))
        searching, controls = structural['searching'], structural['controls']
        records = []
        codes = {'reference': main_root, 'candidate': ROOT}
        for repeat in range(args.repeats):
            order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
            for n in args.workers:
                rec = {'workers': n, 'repeat': repeat, 'order': order}
                for side in order:
                    print(f'repeat {repeat} workers {n} {side} ...', file=sys.stderr, flush=True)
                    result, record = bm.run_worker(side, codes[side], path, size, args.queries, args.runs, 'full', '4GB',
                                                   args.budget_seconds, n, True, 'engine-default')
                    if result is None:
                        raise SystemExit(f'run failed ({side}, {n} workers, repeat {repeat}): {record["failure"]}')
                    rec[side] = {'runs': [[{k: row.get(k) for k in ('seed', 'match_seconds', 'digest')} for row in run]
                                          for run in result['runs']],
                                 'peak_rss_bytes': record['peak_rss_bytes'], 'wall_seconds': record['wall_seconds']}
                records.append(rec)
        by_workers, identity = paired_of(records, searching, controls)
        ruled = by_workers[str(RULED_WORKERS)]
        medians = {s: statistics.median(p['candidate_medians'][s] for p in ruled) for s in searching}
        micro = micro_of(counted['rows'], counted['micro'], medians)
        normalised_max = max(v for p in ruled for v in p['normalised'].values()) if searching else None
        timing = {'ratio_bar': RATIO_BAR, 'p95_bar_seconds': P95_BAR,
                  'normalised_max_at_14': normalised_max,
                  'every_normalised_within_bar_at_14': all(v <= RATIO_BAR for p in ruled for v in p['normalised'].values()),
                  'candidate_p95_by_pair_at_14': [p['candidate_p95'] for p in ruled],
                  'p95_within_bar_every_pair_at_14': all(p['candidate_p95'] <= P95_BAR for p in ruled),
                  'answers_identical': identity['identical'] == identity['compared'] and not identity['mismatching_seeds'],
                  'by_workers': by_workers, 'identity': identity}
        timing['met'] = (bool(searching) and timing['every_normalised_within_bar_at_14']
                         and timing['p95_within_bar_every_pair_at_14'] and timing['answers_identical'])
        protocol = {'repeats': args.repeats, 'runs': args.runs, 'workers': list(args.workers),
                    'met': args.repeats >= 4 and args.repeats % 2 == 0 and args.runs >= 3 and RULED_WORKERS in args.workers
                    and len(controls) >= 2}
        verdict = {'structural': structural['met'], 'timing': timing['met'], 'micro': micro['met'],
                   'protocol': protocol['met']}
        verdict['met'] = all(verdict.values())
        report = {'profile': PROFILE, 'protocol': PROTOCOL, 'rule': 'D48b rule 5', 'size': size,
                  'snapshot_id': manifest['snapshot_id'], 'queries': args.queries,
                  'reference': {'git_ref': args.main, 'git_commit': commit,
                                'files_at_ref': [{'path': p, 'sha256': hashlib.sha256((main_root / p).read_bytes()).hexdigest()}
                                                 for p in ARTIFACTS if p.startswith('patterns/') and (main_root / p).is_file()]},
                  'candidate': 'this tree', 'budget_policy': 'engine-default', 'structural': structural, 'timing': timing,
                  'micro': micro, 'protocol_check': protocol, 'verdict': verdict,
                  'notes': ['The hull check never fires in this timed workload: no search of the 30 queries reaches '
                            'HULL_AFTER steps (structural.hull_checks_on is empty). What is timed is the meter on the '
                            "searching queries' path; the check's own cost appears only in seed 44's times in the "
                            'scaling report, which are reported, not ruled.',
                            'The counters cover one process: the count runs at one worker, where every unit runs in the '
                            'counted process; the pool path rests on that run and on the AST check of the two sites, '
                            'not on counters in pool processes.'],
                  'seconds': time.monotonic() - started,
                  'artifacts': {p: digest(p) for p in ARTIFACTS}}
        return report
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    w = sub.add_parser('worlds')
    w.add_argument('--cases', type=int, default=50_000)
    w.add_argument('--seed', default='d48b-rule')
    w.add_argument('--mid-after', type=int, nargs='*', default=[2, 3])
    w.add_argument('--mid-cases', type=int, default=20_000)
    o = sub.add_parser('oracle')
    o.add_argument('--refuted', type=int, default=20_000)
    o.add_argument('--seed', default='d48b-oracle-rule')
    d = sub.add_parser('defects')
    d.add_argument('--cases', type=int, default=5_000)
    d.add_argument('--networks', type=int, default=20_000)
    d.add_argument('--seed', default='d48b-defects-rule')
    i = sub.add_parser('identity')
    i.add_argument('--main', required=True)
    i.add_argument('--snapshot', action='append', required=True)
    i.add_argument('--queries', type=int, default=50)
    lat = sub.add_parser('latency')
    lat.add_argument('--main', required=True)
    lat.add_argument('--snapshot', action='append', required=True)
    lat.add_argument('--queries', type=int, default=30)
    lat.add_argument('--repeats', type=int, default=4)
    lat.add_argument('--runs', type=int, default=3)
    lat.add_argument('--workers', type=int, nargs='+', default=[14, 8])
    lat.add_argument('--budget-seconds', type=float, default=3600)
    iw = sub.add_parser('identity-worker')
    iw.add_argument('--code-root', required=True)
    iw.add_argument('--snapshot', required=True)
    iw.add_argument('--queries', type=int, required=True)
    iw.add_argument('--policy', required=True)
    iw.add_argument('--budget', type=int)
    iw.add_argument('--seeds')
    iw.add_argument('--worker-output', required=True)
    cw = sub.add_parser('count-worker')
    cw.add_argument('--snapshot', required=True)
    cw.add_argument('--queries', type=int, required=True)
    cw.add_argument('--worker-output', required=True)
    for p in (w, o, d, i, lat):
        p.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    if args.command == 'identity-worker':
        return identity_worker(args)
    if args.command == 'count-worker':
        return count_worker(args)
    run = {'worlds': worlds_cmd, 'oracle': oracle_cmd, 'defects': defects_cmd, 'identity': identity_cmd,
           'latency': latency_cmd}[args.command]
    started = time.monotonic()
    report = run(args)
    report.setdefault('seconds', time.monotonic() - started)
    text = json.dumps(report, indent=2, sort_keys=True) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
        print(args.output)
    else:
        print(text)
    return 0 if report.get('met') or report.get('verdict', {}).get('met') else 1


if __name__ == '__main__':
    sys.exit(main())
