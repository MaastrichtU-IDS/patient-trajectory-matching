#!/usr/bin/env python3
"""D52's rule 6: the latency of the requests whose index period is a hidden step, control-normalised (decision log, D52).

D52 changes no store or engine code. What it changes is the request the page sends when an event places the index period:
a hidden step for the event, relations from it, and a base window the server narrows to the hull of the possible periods
(`demos/mockups/index_hull.py`). So, as in D45, the two arms of the paired protocol are two request sets in this one tree:
- the *candidate* arm sends each template's request as the page now writes it (its index block included; at the page's
  size through the server's own `match`, which narrows; at 300,000 the narrowing is timed inside the request);
- the *reference* arm sends the same definition with D49's point windows (`indexAnchor: false`), the request the page
  sent before D52;
- both arms send the same *controls*, requests D52 leaves byte-identical (constant periods), and each pair's ratio is
  divided by the geometric mean of the controls' ratios in the same processes (D44b's normalisation).

Arms run in fresh processes, back to back, ABBA, at two sizes:
- *page*: the page's cohort in a store built once by the server's builder, opened in each arm by the server's own class
  (`store_trajectory.TrajectoryStore`, as `benchmark_coded_default.py` does) and answered by its `match`; one untimed run,
  then `--page-runs` timed runs. Controls: the Sequence and Blank templates from birth to 6 years, coded only.
- *scale*: the kept 300,000-patient build, `store_conditions.match` with `decided` detail at 14 and 8 workers,
  `--scale-runs` runs. The build holds no series, no gene label and no follow-up header, so Sequence, Tree and Graph run
  without their rise and gene steps (as D45 ran them), and each patient's last contact is the store's stand-in, its
  latest coverage end; its birth is its floor (the earliest start of any of its events, the converter's floor for
  letter mentions). Reference windows: the earliest start of the patient's first (third) coded seizure for the period's
  length, excluded when that runs past the last contact (the page's rule over the store's literal records). Candidate:
  base windows from the floor to the last contact for every patient, narrowed by `index_hull.narrow` inside the timed
  request. Controls: D43b's `1.1 sequence` and the plain sequence, with D43b's windows.

The bars (ruled): the control-normalised ratio at most 2.0 in every pair, and for the count requests (Event frequency,
under both periods) the median over the pairs at most 2.0 (D51's lesson); the candidate's p95 at most 2 s at the page's
size and 10 s at 300,000 (14 workers); each answer one digest per arm. Structural (ruled): the narrowing (`index_hull`'s
call counter) and the translator's rewrite (the requests file's count) read 0 for every reference and control, and more
than 0 for every candidate. Micro bound (ruled at 300,000; D52b): the narrowing's own time per candidate request in one
process as the server runs it, with its cache (`index_hull._CACHE`): the first request's (the cache empty, its queries on
14 DuckDB threads) reported, the steady state's (the median of 3 later requests) at most 10% of the candidate's 14-worker
median; at the page's size it runs inside the timed server path and is not bounded apart. D52b: at 300,000, as at the
page's size, each request runs once untimed before its timed runs; that first run (the narrowing's cache empty) is ruled
apart (review, S1): at 14 workers each candidate's first run at most 10 s in every pair, and its ratio to the reference's
first run, normalised by the controls' first runs in the same pair, at most 2.0 in every pair.

D52c (profile 1.2): the candidate's timed path at 300,000 is the server's: the narrowing, `index_simplify.simplify`
(`index_simplify_v1`), the match of the simplified request and the answer mapped back (`index_simplify.apply`); at the
page's size the server's own `match` does the same. Pre-registered: each arm's process fills the narrowing's cache (and
the simplification's facts) for the page's template anchors when its store opens, as the server does at its start
(`index_hull.prefill`, `index_simplify.prefill`; at the page's size inside `TrajectoryStore`'s build), before its first
request; the fill's time is reported per process (`prefill_seconds`) and is in no timed run. The S1 first-run rule is
unchanged. The micro bound's steady state is the narrowing and the simplification together (reported apart too). Ruled
too (rule 3b at 300,000, `--identity-worker`): on every patient of each candidate request, the answer with the
simplification equals the answer without it but in the declared classes (`index_simplify.compare`).

    python tools/benchmark_index_anchor.py --requests-file REQUESTS.json --snapshot 300000=SNAPSHOT_DIR [--output FILE]
"""
from __future__ import annotations

import argparse
import hashlib
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

ROOT = Path(__file__).resolve().parents[1]
MOCKUPS = ROOT / 'demos' / 'mockups'
for p in (str(ROOT), str(ROOT / 'tools'), str(MOCKUPS)):
    if p not in sys.path:
        sys.path.insert(0, p)

PROFILE = 'index-anchor-latency-1.2'  # D52c: the simplification in the timed path; the cache filled at each process's start
OUTPUT = 'verification/d52d-anchor-latency-verdict.json'
RATIO_BAR, PAGE_P95_BAR, SCALE_P95_BAR = 2.0, 2.0, 10.0
MICRO_SHARE = 0.10
RULED_WORKERS = 14
PAGE_RUNS_MIN, SCALE_RUNS_MIN, REPEATS_MIN = 20, 3, 8
COUNT_REQUESTS = ('event frequency', 'event frequency under the 3rd seizure for 12 months')
# D52c (review S2): the requests whose anchor the server does not pre-fill, so their first run is the cold path S1 rules.
COLD_REQUESTS = ('event frequency under the 3rd seizure for 12 months', 'blank under the last valproate start, 6 months before it to it')
SCALE_CONTROLS = ('1.1 sequence', 'plain sequence')
MINUTE = 60_000_000
ARTIFACTS = ('tools/benchmark_index_anchor.py', 'tools/benchmark_coded_default.py', 'tools/benchmark_count_requests.py',
             'demos/mockups/d52_anchor.cjs', 'demos/mockups/index_hull.py', 'demos/mockups/index_simplify.py',
             'demos/mockups/store_trajectory.py',
             'demos/mockups/store_parity.py', 'demos/mockups/trajectory/store_request.js', 'patterns/store_conditions.py',
             'patterns/store_conditions_coded.py', 'patterns/store_conditions_gene.py', 'patterns/store_conditions_relative.py',
             'patterns/store_conditions_counts.py', 'patterns/execution_store.py', 'patterns/trajectory_graph.py',
             'patterns/trajectory_times.py', 'patterns/temporal_stn.py')


def page_requests(data, arm):
    out = {name: t[arm] for name, t in data['page'].items()}
    out.update(data['page_controls'])
    return out


# The windows at 300,000 (in the store's clock).

FIRST_SQL = """
    WITH s AS (SELECT DISTINCT e.patient_idx, e.event_id, e.start_lo FROM events e
               JOIN event_concepts ec USING (patient_idx, event_id)
               JOIN concept_closure cc ON cc.concept = ec.concept
               WHERE cc.ancestor = ? AND e.kind <> 'mention')
    SELECT p.patient_id, list_sort(list(s.start_lo), '{order}')[{k}] FROM s JOIN patients p USING (patient_idx) GROUP BY p.patient_id"""
# The anchors the scale requests name (concept, k, from the end): the first and third seizure; D52c (review S2): the last
# valproate start, an anchor outside the server's pre-fill, so a request without counts runs a cold key too.
ANCHORS = (('seizure', 1, False), ('seizure', 3, False), ('valproate', 1, True))


def scale_facts(store):
    """(last contact stand-ins, floors, each anchor's coded earliest starts by its order statistic), by patient id."""
    last = dict(store.query('SELECT p.patient_id, max(c.hi) FROM coverage c JOIN patients p USING (patient_idx) GROUP BY ALL'))
    floor = dict(store.query('SELECT p.patient_id, min(e.start_lo) FROM events e JOIN patients p USING (patient_idx) GROUP BY ALL'))
    nth = {key: {pid: t for pid, t in store.query(FIRST_SQL.format(k=key[1], order='DESC' if key[2] else 'ASC'), [key[0]])
                 if t is not None} for key in ANCHORS}
    return last, floor, nth


def reference_windows(spec, facts):
    """The page's rule over the store's literal records: the anchor's earliest start plus the period; none past the last
    contact."""
    last, _, nth = facts
    index = spec['candidate']['index']
    step = next(s for s in spec['candidate']['query']['steps'] if s['id'] == index['start']['step'])
    hidden = ((spec['candidate']['conditions'] or {}).get('steps') or {}).get(step['id'])
    k = list(hidden['occurrence'].values())[0] if hidden else 1
    from_end = step['occurrence'] == 'last' or bool(hidden and 'nth_last' in hidden['occurrence'])
    start_off, end_off = index['start']['offset'] * MINUTE, index['end']['offset'] * MINUTE
    out = {}
    for pid in last:
        t = nth[(step['concept'], k, from_end)].get(pid)
        out[pid] = None if t is None or t + end_off > last[pid] else {'start': t + start_off, 'end': t + end_off}
    return out


def base_windows(facts):
    last, floor, _ = facts
    return {pid: {'start': floor[pid], 'end': last[pid]} for pid in last if pid in floor}


def scale_requests(data, arm):
    import benchmark_count_requests as bcr
    import store_parity
    out = {}
    for name, s in data['scale'].items():
        r = s[arm]
        out[name] = {'query': store_parity.to_micros(r['query']), 'conditions': store_parity.conditions_micros(r['conditions']),
                     'raw_conditions': r['conditions'], 'index': r.get('index'), 'spec': s}
    out['1.1 sequence'] = {'query': bcr.REQUESTS['1.1 sequence'][0], 'conditions': bcr.REQUESTS['1.1 sequence'][1], 'control': True}
    out['plain sequence'] = {'query': bcr._sequence(), 'conditions': None, 'control': True}
    return out


# Workers.

def page_worker(args):
    import benchmark_coded_default as bcd
    import index_hull
    data = json.loads(Path(args.requests_file).read_text())
    ts = bcd.trajectory_store(args.snapshot)
    out = {'requests': {}, 'worker_peak_rss_bytes': 0, 'prefill_seconds': ts.prefill_s}
    try:
        for name, spec in page_requests(data, args.arm).items():
            calls = index_hull.CALLS['narrow']
            started = time.perf_counter()
            first = ts.match(spec)
            row = {'first_run_seconds': time.perf_counter() - started, 'seconds': [], 'digests': [bcd.page_digest(first)]}
            for _ in range(args.runs):
                started = time.perf_counter()
                answer = ts.match(spec)
                row['seconds'].append(time.perf_counter() - started)
                row['digests'].append(bcd.page_digest(answer))
            row['narrow_calls'] = index_hull.CALLS['narrow'] - calls
            out['requests'][name] = row
    finally:
        ts.close()
    out['parent_peak_rss_bytes'] = bcd._peak_rss()
    return out


def prefill(store, threads):
    """D52c (pre-registered): the server's start, the narrowing's cache and the simplification's facts filled for the page's
    template anchors; its seconds and keys."""
    import index_hull
    import index_simplify
    started = time.perf_counter()
    keys = index_hull.prefill(store, threads=threads)
    index_simplify.prefill(store, sorted({k[0] for k in index_hull.PREFILL}))
    return time.perf_counter() - started, keys


def candidate_answer(store, r, windows, workers):
    """The candidate's request as the server answers it (D52c): narrowed, simplified, matched, mapped back."""
    import index_hull
    import index_simplify
    from patterns import store_conditions as sc
    w, _, found = index_hull.narrow(store, r['query'], r['raw_conditions'], r['index'], windows, MINUTE, threads=workers)
    s = index_simplify.simplify(store, r['query'], r['raw_conditions'], r['index'], w, found)
    answer = sc.match(store, s['query'], w, detail='decided', workers=workers, conditions=r['conditions'])
    return index_simplify.apply(answer, s, r['query']), s, w


def scale_worker(args):
    import benchmark_coded_default as bcd
    import benchmark_count_requests as bcr
    import index_hull
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    data = json.loads(Path(args.requests_file).read_text())
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    try:
        pool = es.get_pool(store, args.arm_workers)
        pool.warm()
        # D52c: the server's start fills the cache before any request, in both arms (one tree, as the server runs it).
        filled, keys = prefill(store, args.arm_workers)
        facts = scale_facts(store)
        d43b = bcr.windows_of(store)
        base = base_windows(facts)
        out = {'pool_start_seconds': pool.start_seconds, 'prefill_seconds': filled, 'prefill_keys': keys,
               'requests': {}, 'patients': len(facts[0])}
        for name, r in scale_requests(data, args.arm).items():
            windows = d43b if r.get('control') else base if r.get('index') else reference_windows(r['spec'], facts)
            row = {'seconds': [], 'digests': [], 'narrow_calls': 0, 'patients_with_window': sum(1 for w in windows.values() if w)}
            # D52b: one untimed run first, as at the page's size (since D52c the cache is filled at the process's start;
            # DuckDB's own caches and the store's per-snapshot ones fill here); its time is recorded as the first-request cost.
            calls = index_hull.CALLS['narrow']
            started = time.perf_counter()
            if r.get('index'):
                first = candidate_answer(store, r, windows, args.arm_workers)[0]
            else:
                first = sc.match(store, r['query'], windows, detail='decided', workers=args.arm_workers, conditions=r['conditions'])
            row['first_run_seconds'] = time.perf_counter() - started
            row['narrow_calls'] += index_hull.CALLS['narrow'] - calls
            row['first_digest'] = bcr.answer_digest(first)
            del first
            for _ in range(args.runs):
                calls = index_hull.CALLS['narrow']
                started = time.perf_counter()
                if r.get('index'):
                    answer = candidate_answer(store, r, windows, args.arm_workers)[0]
                else:
                    answer = sc.match(store, r['query'], windows, detail='decided', workers=args.arm_workers, conditions=r['conditions'])
                row['seconds'].append(time.perf_counter() - started)
                row['digests'].append(bcr.answer_digest(answer))
                row['narrow_calls'] += index_hull.CALLS['narrow'] - calls
                del answer
            out['requests'][name] = row
        out['worker_peak_rss_bytes'] = pool.worker_peak_rss
        out['parent_peak_rss_bytes'] = bcd._peak_rss()
    finally:
        es.close_pools()
        store.close()
    return out


def micro_worker(args):
    """The narrowing's own time per candidate request at 300,000, in one process, as the server runs it (D52b): the first
    request's (the cache empty; its queries on the ruled number of DuckDB threads, 14, as the match runs on 14 workers),
    reported, and the steady state's, the median of 3 later requests (the cache filled), ruled."""
    import index_hull
    import index_simplify
    from patterns import execution_store as es
    data = json.loads(Path(args.requests_file).read_text())
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    try:
        facts = scale_facts(store)
        base = base_windows(facts)
        out = {}
        for name, r in scale_requests(data, 'candidate').items():
            if not r.get('index'):
                continue
            index_hull.clear_cache()
            index_simplify.clear_cache()
            started = time.perf_counter()
            index_hull.narrow(store, r['query'], r['raw_conditions'], r['index'], base, MINUTE, threads=RULED_WORKERS)
            cold = time.perf_counter() - started
            started = time.perf_counter()
            index_simplify.prefill(store, sorted({k[0] for k in index_hull.PREFILL}))
            cold_facts = time.perf_counter() - started
            times, narrowing, simplifying = [], [], []
            for _ in range(3):
                started = time.perf_counter()
                w, _, found = index_hull.narrow(store, r['query'], r['raw_conditions'], r['index'], base, MINUTE, threads=RULED_WORKERS)
                middle = time.perf_counter()
                index_simplify.simplify(store, r['query'], r['raw_conditions'], r['index'], w, found)
                done = time.perf_counter()
                times.append(done - started)
                narrowing.append(middle - started)
                simplifying.append(done - middle)
            # D52b review, S3 (reported): the memory the cache holds with this one key (the page's anchor for the request).
            # D52c: the steady state ruled is the narrowing and the simplification together; each is reported apart.
            out[name] = {'narrow_seconds': statistics.median(times), 'runs': times,
                         'narrowing_only_seconds': statistics.median(narrowing), 'simplification_only_seconds': statistics.median(simplifying),
                         'first_request_seconds': cold, 'simplification_facts_first_seconds': cold_facts,
                         'cache_keys': len(index_hull._CACHE['ranges']), 'cache_rows': index_hull._CACHE['rows'],
                         'cache_bytes': index_hull.cache_bytes()}
    finally:
        store.close()
    return out


def identity_worker(args):
    """D52c (rule 3b at 300,000, ruled): each candidate request at the ruled worker count, matched with and without the
    simplification on the same narrowed windows; per patient, the answers equal but in the declared classes
    (`index_simplify.compare`). Untimed; one process."""
    import index_hull
    import index_simplify
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    data = json.loads(Path(args.requests_file).read_text())
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    try:
        pool = es.get_pool(store, RULED_WORKERS)
        pool.warm()
        prefill(store, RULED_WORKERS)
        facts = scale_facts(store)
        base = base_windows(facts)
        out = {}
        for name, r in scale_requests(data, 'candidate').items():
            if not r.get('index'):
                continue
            with_it, s, w = candidate_answer(store, r, base, RULED_WORKERS)
            without = sc.match(store, r['query'], w, detail='decided', workers=RULED_WORKERS, conditions=r['conditions'])
            res = index_simplify.compare(store, r['query'], r['raw_conditions'], w, s, without, with_it)
            res.pop('classes')
            res['unexplained_count'] = len(res['unexplained'])
            res['unexplained'] = res['unexplained'][:10]
            res['record'] = index_simplify.describe(s)
            out[name] = res
            print(f'identity {name}: {res["changed"]} of {res["results"]} changed {res["by_class"]}, '
                  f'{res["unexplained_count"]} unexplained', file=sys.stderr, flush=True)
    finally:
        es.close_pools()
        store.close()
    return out


def _run(args, mode, extra, timeout):
    out = Path(tempfile.mkdtemp(prefix='index-anchor-')) / 'out.json'
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
                print(f'index anchor {size} repeat {repeat} workers {n} {arm} ...', file=sys.stderr, flush=True)
                rec[arm] = _run(args, f'--{size}-worker', ['--arm', arm, '--runs', str(runs), *extra_of(n)], args.budget_seconds)
                print('  ' + '; '.join(f'{name} {statistics.median(r["seconds"]):.3f} s' for name, r in rec[arm]['requests'].items()),
                      file=sys.stderr, flush=True)
            records.append(rec)
    return records


# The verdict.

def first_run_check(records, templates, controls, workers, bar_seconds):
    """D52b review, S1, ruled at 300,000: each candidate's first run (the narrowing's cache empty) at most `bar_seconds`
    in every pair at `workers`, and its first run over the reference's first run, normalised by the geometric mean of the
    controls' first-run ratios in the same pair, at most RATIO_BAR in every pair."""
    mine = sorted((r for r in records if r['workers'] == workers), key=lambda r: r['repeat'])
    first = lambda rec, arm, name: rec[arm]['requests'][name]['first_run_seconds']
    gmean = [math.prod(first(rec, 'candidate', c) / first(rec, 'reference', c) for c in controls) ** (1 / len(controls)) for rec in mine]
    rows = {}
    for name in templates:
        cand = [first(rec, 'candidate', name) for rec in mine]
        norm = [first(rec, 'candidate', name) / first(rec, 'reference', name) / g for rec, g in zip(mine, gmean)]
        rows[name] = {'candidate_first_run_seconds': cand, 'reference_first_run_seconds': [first(rec, 'reference', name) for rec in mine],
                      'normalised_first_run_ratio': norm, 'every_first_run_at_most_bar': all(t <= bar_seconds for t in cand),
                      'every_normalised_at_most_ratio_bar': all(r <= RATIO_BAR for r in norm)}
        rows[name]['met'] = rows[name]['every_first_run_at_most_bar'] and rows[name]['every_normalised_at_most_ratio_bar']
    return {'workers': workers, 'bar_seconds': bar_seconds, 'ratio_bar': RATIO_BAR, 'controls_geometric_mean_by_repeat': gmean,
            'requests': rows, 'met': bool(rows) and all(r['met'] for r in rows.values())}


def size_verdict(records, templates, controls, workers, p95_bar, runs, runs_min, repeats, ruled_run=True):
    import benchmark_count_requests as bcr
    stats = bcr.statistics_of(records, [*templates, *controls])
    at = stats[str(workers)]['requests']
    gmean = [math.prod(rs) ** (1 / len(rs)) for rs in zip(*(at[c]['ratio_by_repeat'] for c in controls))]
    answers = {}
    for name in [*templates, *controls]:
        digests = {arm: {d for rec in records for d in rec[arm]['requests'][name]['digests']} for arm in ('reference', 'candidate')}
        answers[name] = {'digests_per_arm': {a: len(d) for a, d in digests.items()}, 'met': all(len(d) == 1 for d in digests.values())}
        if name in controls:
            answers[name]['same_in_both_arms'] = digests['reference'] == digests['candidate']
            answers[name]['met'] = answers[name]['met'] and answers[name]['same_in_both_arms']
    structural = {}
    for name in [*templates, *controls]:
        calls = {arm: sum(rec[arm]['requests'][name]['narrow_calls'] for rec in records) for arm in ('reference', 'candidate')}
        want_candidate = name in templates
        structural[name] = {'narrow_calls': calls,
                            'met': calls['reference'] == 0 and ((calls['candidate'] > 0) if want_candidate else calls['candidate'] == 0)}
    rows = {}
    for name in templates:
        norm = [r / g for r, g in zip(at[name]['ratio_by_repeat'], gmean, strict=True)]
        count = name in COUNT_REQUESTS
        median = statistics.median(norm)
        ratio_met = median <= RATIO_BAR if count else all(r <= RATIO_BAR for r in norm)
        p95 = at[name]['candidate']['p95_seconds']
        rows[name] = {'ratio_by_repeat': at[name]['ratio_by_repeat'], 'normalised_by_repeat': norm, 'normalised_median': median,
                      'bar': 'median over pairs at most 2.0 (a count request, D51)' if count else 'every pair at most 2.0',
                      'ratio_met': ratio_met, 'candidate_p95_seconds': p95, 'reference_p95_seconds': at[name]['reference']['p95_seconds'],
                      'candidate_p50_seconds': at[name]['candidate']['p50_seconds'], 'reference_p50_seconds': at[name]['reference']['p50_seconds'],
                      'p95_met': p95 <= p95_bar, 'answers_met': answers[name]['met']}
        rows[name]['met'] = ratio_met and rows[name]['p95_met'] and rows[name]['answers_met']
    protocol = {'ruled_workers_run': ruled_run and str(workers) in stats,
                'repeats_at_least_8_and_even': repeats >= REPEATS_MIN and repeats % 2 == 0, f'runs_at_least_{runs_min}': runs >= runs_min,
                'every_request_run': all(set(rec[a]['requests']) == {*templates, *controls} for rec in records for a in ('reference', 'candidate')),
                'controls_answers_met': all(answers[c]['met'] for c in controls)}
    protocol['met'] = all(protocol.values())
    return stats, {'workers': workers, 'ratio_bar': RATIO_BAR, 'p95_bar_seconds': p95_bar, 'requests': rows,
                   'controls': {'names': list(controls), 'geometric_mean_by_repeat': gmean},
                   'answers': answers, 'structural_narrowing': structural, 'protocol': protocol}


def main_run(args):
    import benchmark_coded_default as bcd
    import store_parity
    data = json.loads(Path(args.requests_file).read_text())
    size, _, args.snapshot_path = args.snapshot.partition('=')
    scratch = Path(tempfile.mkdtemp(prefix='index-anchor-page-store-'))
    started = time.monotonic()
    try:
        cohort = json.loads((MOCKUPS / 'data' / 'synthetic-paediatric-rare.json').read_text())
        page_snapshot = str(store_parity.build(cohort, scratch / 'store'))
        build = bcd.build_check(args.snapshot_path)
        if not build['met']:
            raise SystemExit(f'the kept build does not match this tree: {json.dumps(build)}')
        page_templates = list(data['page'])
        page_records = run_pairs(args, 'page', [1], args.page_runs, lambda n: ['--snapshot', page_snapshot])
        page_stats, page = size_verdict(page_records, page_templates, list(data['page_controls']), 1, PAGE_P95_BAR,
                                        args.page_runs, PAGE_RUNS_MIN, args.repeats)
        scale_records = run_pairs(args, 'scale', args.workers, args.scale_runs,
                                  lambda n: ['--snapshot', args.snapshot_path, '--arm-workers', str(n)])
        kept = Path(tempfile.gettempdir()) / f'd52-latency-records-{os.getpid()}.json'
        kept.write_text(json.dumps({'page': page_records, 'scale': scale_records}))
        print(f'timed records kept in {kept}', file=sys.stderr, flush=True)
        ruled = RULED_WORKERS if RULED_WORKERS in args.workers else args.workers[0]
        scale_stats, scale = size_verdict(scale_records, list(data['scale']), list(SCALE_CONTROLS), ruled, SCALE_P95_BAR,
                                          args.scale_runs, SCALE_RUNS_MIN, args.repeats, ruled_run=RULED_WORKERS in args.workers)
        first_runs = first_run_check(scale_records, list(data['scale']), list(SCALE_CONTROLS), ruled, SCALE_P95_BAR)
        micro_raw = _run(args, '--micro-worker', ['--snapshot', args.snapshot_path], args.budget_seconds)
        identity_raw = _run(args, '--identity-worker', ['--snapshot', args.snapshot_path], args.budget_seconds)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    micro = {name: {**m, 'candidate_p50_seconds_at_14': scale['requests'][name]['candidate_p50_seconds'],
                    'share': m['narrow_seconds'] / scale['requests'][name]['candidate_p50_seconds']}
             for name, m in micro_raw.items() if name != 'wall_seconds'}
    micro_met = all(m['share'] <= MICRO_SHARE for m in micro.values()) and len(micro) == len(data['scale'])
    identity = {name: x for name, x in identity_raw.items() if name != 'wall_seconds'}
    identity_met = len(identity) == len(data['scale']) and all(x['unexplained_count'] == 0 for x in identity.values())
    rewrites = data.get('control_rewrite_calls', {})
    structural = {'translator_rewrite_calls_for_controls': rewrites, 'translator_met': bool(rewrites) and all(v == 0 for v in rewrites.values()),
                  'page_narrowing_met': all(v['met'] for v in page['structural_narrowing'].values()),
                  'scale_narrowing_met': all(v['met'] for v in scale['structural_narrowing'].values())}
    structural['met'] = structural['translator_met'] and structural['page_narrowing_met'] and structural['scale_narrowing_met']
    ok = lambda s: s['protocol']['met'] and all(r['met'] for r in s['requests'].values())
    verdict = {'page': ok(page), 'scale': ok(scale), 'scale_first_runs': first_runs['met'], 'structural': structural['met'],
               'micro_bound': micro_met, 'build': build['met'], 'simplification_identity': identity_met}
    verdict['met'] = all(verdict.values())
    report = {
        'profile': PROFILE, 'rule': 'D52c rule 6 (D52b\'s rule 6, D52\'s as amended) and rule 3b at 300,000 (decision log)',
        'arms': {'candidate': 'the request as the page writes it, the index period a hidden step (the server narrows and simplifies; '
                              'at 300,000 the narrowing, the simplification and the mapping back are inside the timed request)',
                 'reference': "the same definition with D49's point windows (indexAnchor false)"},
        'order': 'ABBA: the reference first in even pairs', 'repeats': args.repeats,
        'requests_file': {'sha256': hashlib.sha256(Path(args.requests_file).read_bytes()).hexdigest(),
                          'cohort_sha256': data['cohort_sha256'], 'translator_sha256': data['translator_sha256']},
        'page': {'path': 'store_trajectory.TrajectoryStore.match, one worker, full detail', 'runs_per_request': args.page_runs,
                 'by_workers': page_stats, 'verdict': page,
                 'first_run_seconds': {n: {a: [r[a]['requests'][n]['first_run_seconds'] for r in page_records] for a in ('reference', 'candidate')}
                                       for n in [*page_templates, *data['page_controls']]}},
        'scale': {'size': int(size), 'snapshot_id': build['snapshot_id'], 'detail': 'decided', 'workers': list(args.workers),
                  'runs_per_request': args.scale_runs, 'by_workers': scale_stats, 'verdict': scale, 'first_run_check': first_runs,
                  'first_run_seconds': {n: {a: [r[a]['requests'][n]['first_run_seconds'] for r in scale_records if r['workers'] == RULED_WORKERS]
                                            for a in ('reference', 'candidate')} for n in [*data['scale'], *SCALE_CONTROLS]},
                  'stand_ins': ("last contact: the patient's latest coverage end; birth: the patient's floor (earliest event "
                                'start); reference windows: the earliest start of the first (third) coded seizure for the period')},
        'micro': {'share_bar': MICRO_SHARE, 'scale_one_process_14_threads': micro, 'met': micro_met}, 'structural': structural,
        'prefill': {'rule': ('pre-registered (D52c): each process fills the narrowing\'s cache and the simplification\'s facts for '
                             'the page\'s template anchors when its store opens, before its first request, in both arms; not timed'),
                    'page_seconds': {a: [r[a]['prefill_seconds'] for r in page_records] for a in ('reference', 'candidate')},
                    'scale_seconds': {a: {str(n): [r[a]['prefill_seconds'] for r in scale_records if r['workers'] == n] for n in args.workers}
                                      for a in ('reference', 'candidate')}},
        'simplification_identity': {'rule': 'D52c rule 3b at 300,000: per patient, with and without index_simplify_v1, equal but in the declared classes',
                                    'requests': identity, 'met': identity_met},
        'build': build, 'verdict': verdict, 'seconds': time.monotonic() - started,
        'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS},
        'contains_patient_level_data': False,
        'limitations': ('one machine, one session; synthetic data replicated beyond 10,000 patients; at 300,000 the kept build '
                        'holds no series, gene labels or follow-up header (stand-ins above)'),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1) + '\n')
    print(args.output)
    print(json.dumps(verdict), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--requests-file', type=Path, required=True, help='d52_anchor.cjs requests --output FILE')
    parser.add_argument('--snapshot', help='SIZE=SNAPSHOT_DIRECTORY (the kept 300,000 build)')
    parser.add_argument('--workers', nargs='+', type=int, default=[RULED_WORKERS, 8])
    parser.add_argument('--repeats', type=int, default=REPEATS_MIN)
    parser.add_argument('--page-runs', type=int, default=PAGE_RUNS_MIN)
    parser.add_argument('--scale-runs', type=int, default=SCALE_RUNS_MIN)
    parser.add_argument('--memory-limit', default='4GB')
    parser.add_argument('--budget-seconds', type=float, default=3600)
    parser.add_argument('--output', type=Path, default=ROOT / OUTPUT)
    for hidden in ('--page-worker', '--scale-worker', '--micro-worker', '--identity-worker'):
        parser.add_argument(hidden, action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--arm', choices=('reference', 'candidate'), help=argparse.SUPPRESS)
    parser.add_argument('--runs', type=int, help=argparse.SUPPRESS)
    parser.add_argument('--arm-workers', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    for flag, fn in (('page_worker', page_worker), ('scale_worker', scale_worker), ('micro_worker', micro_worker),
                     ('identity_worker', identity_worker)):
        if getattr(args, flag):
            args.worker_output.write_text(json.dumps(fn(args)))
            return 0
    if not args.snapshot:
        parser.error('--snapshot is required')
    main_run(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
