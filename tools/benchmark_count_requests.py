#!/usr/bin/env python3
"""Count requests through the dispatcher at scale, paired against a reference code (D43b's latency rule).

D43 timed a path it never ran: the matcher benchmark's queries carry no count and call `execution_store.match`
directly, so the cost D43 added, its post-pass on count requests, went unmeasured (decision log, D43: "a latency rule
must time the path the change executes"). This tool times that path: fixed count requests (`REQUESTS`: Event
frequency and an Nth step beside a count, each at profiles 1.1 and 1.3) through `store_conditions.match`, the one entry
point the page and the tools call, on one snapshot (the kept D34 build of 300,000 patients), with the page's windows
(each patient's second coded seizure for 730 days; patients without one have no window) and `detail='decided'` (the
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

WINDOWS_SQL = """
    WITH s AS (SELECT DISTINCT e.patient_idx, e.event_id, e.start_lo FROM events e
               JOIN event_concepts ec USING (patient_idx, event_id)
               JOIN concept_closure cc ON cc.concept = ec.concept
               WHERE cc.ancestor = 'seizure' AND e.kind <> 'mention')
    SELECT p.patient_id, list_sort(list(s.start_lo))[2] FROM s JOIN patients p USING (patient_idx)
    GROUP BY p.patient_id"""


def windows_of(store):
    """The page's windows: from each patient's second coded seizure, 730 days; none for a patient without one."""
    return {pid: {'start': second, 'end': second + WINDOW_DAYS * DAY}
            for pid, second in store.query(WINDOWS_SQL) if second is not None}


def answer_digest(answer):
    return hashlib.sha256(json.dumps({'counts': answer['counts'], 'results': answer['results']},
                                     sort_keys=True).encode()).hexdigest()


# One run, in its own process, with the code at --code-root first on the path.

def worker(args):
    code_root = str(Path(args.code_root).resolve())
    sys.path[:] = [code_root] + [p for p in sys.path if Path(p or '.').resolve() not in (ROOT, ROOT / 'tools')]
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    if not Path(sc.__file__).resolve().is_relative_to(Path(code_root)):
        raise RuntimeError(f'imported {sc.__file__}, not from {code_root}')
    store = es.open_store(args.snapshot, 1, 'files', args.memory_limit)
    try:
        pool = es.get_pool(store, args.arm_workers)
        pool.warm()
        windows = windows_of(store)
        out = {'code': sc.__file__, 'patients_with_window': len(windows), 'pool_start_seconds': pool.start_seconds,
               'requests': {}}
        for name in args.requests:
            query, conditions = REQUESTS[name]
            seconds, digests = [], []
            for run in range(args.runs):
                started = time.perf_counter()
                answer = sc.match(store, query, windows, detail='decided', workers=args.arm_workers,
                                  conditions=conditions)
                seconds.append(time.perf_counter() - started)
                digests.append(answer_digest(answer))
                if run == 0 and args.keep_results:
                    path = Path(args.keep_results) / f'{name.replace(" ", "_")}.json'
                    path.write_text(json.dumps({'counts': answer['counts'], 'results': answer['results']}))
                    tally = answer.get('derived_steps', {}).get('count_refinement')
                    first = {'candidates': answer['candidates'], 'counts': answer['counts'],
                             'count_refinement': None if tally is None else {k: v for k, v in tally.items()
                                                                             if k != 'rule'}}
                del answer
            out['requests'][name] = {'seconds': seconds, 'digests': digests, **(first if args.keep_results else {})}
        out['worker_peak_rss_bytes'] = pool.worker_peak_rss
        import resource
        out['parent_peak_rss_bytes'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (
            1 if sys.platform == 'darwin' else 1024)
    finally:
        es.close_pools()
        store.close()
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


def verdict_of(stats, requests, ruled=RULED_WORKERS, ratio_bar=RATIO_BAR, absolute=ABSOLUTE_BAR,
               no_count_bar=NO_COUNT_BAR):
    """D43b rules 4.1 and 4.2b at `ruled` workers. 4.1: every count request's ratio at most `ratio_bar` in every pair,
    and the absolute request's candidate p95 over all its runs at most the bar. 4.2b: every request without a count has
    a pooled median ratio (the candidate's median over all its runs, over the reference's) at most `no_count_bar`."""
    at = stats[str(ruled)]['requests']
    counted = [name for name in requests if name not in NO_COUNT_REQUESTS]
    plain = [name for name in requests if name in NO_COUNT_REQUESTS]
    ratios = {name: {'ratio_by_repeat': at[name]['ratio_by_repeat'],
                     'every_pair_at_most': all(r <= ratio_bar for r in at[name]['ratio_by_repeat'])} for name in counted}
    p95 = at[absolute['request']]['candidate']['p95_seconds']
    rule_41 = {'ratio_bar': ratio_bar, 'ratios': ratios,
               'absolute': {**absolute, 'candidate_p95_seconds': p95, 'met': p95 <= absolute['p95_seconds']}}
    rule_41['met'] = all(r['every_pair_at_most'] for r in ratios.values()) and rule_41['absolute']['met']
    rule_42b = {'bar': no_count_bar, 'requests': {
        name: {'ratio_median_pooled': at[name]['ratio_median_pooled'], 'ratio_by_repeat': at[name]['ratio_by_repeat'],
               'met': at[name]['ratio_median_pooled'] <= no_count_bar} for name in plain}}
    rule_42b['met'] = all(r['met'] for r in rule_42b['requests'].values())
    return {'workers': ruled, 'rule_4_1': rule_41, 'rule_4_2b': rule_42b, 'met': rule_41['met'] and rule_42b['met']}


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
        identity = {
            'each_code_one_answer_per_request': {side: {name: len(d) == 1 for name, d in by.items()}
                                                 for side, by in digests.items()},
            'movement': movement(kept[('reference', ruled)], kept[('candidate', ruled)], args.requests),
            'requests_first_repeat': {side: {name: {k: v for k, v in first[(side, ruled)]['requests'][name].items()
                                                    if k not in ('seconds', 'digests')} for name in args.requests}
                                      for side in ('reference', 'candidate')}}
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    verdict = verdict_of(stats, args.requests, ruled)
    verdict['rule_4_2a'] = {'met': structural['met'], 'files': len(structural['files'])}
    verdict['met'] = verdict['met'] and structural['met']
    report = {
        'profile': PROFILE, 'protocol': PROTOCOL, 'size': int(size), 'snapshot_id': manifest['snapshot_id'],
        'requests': {name: {'query': q, 'conditions': c} for name, (q, c) in REQUESTS.items() if name in args.requests},
        'windows': f"each patient's second coded seizure (a seizure record that is not a letter mention), "
                   f'{WINDOW_DAYS} days; no window without one',
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
    parser.add_argument('--workers', nargs='+', type=int, default=[RULED_WORKERS, 8])
    parser.add_argument('--repeats', type=int, default=4, help='repeats (even: balanced ABBA)')
    parser.add_argument('--runs', type=int, default=3, help='runs of each request in one process')
    parser.add_argument('--requests', nargs='+', default=list(REQUESTS), choices=list(REQUESTS))
    parser.add_argument('--memory-limit', default='4GB')
    parser.add_argument('--budget-seconds', type=float, default=3600, help='per run')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/d43b-count-latency-verdict.json')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--code-root', help=argparse.SUPPRESS)
    parser.add_argument('--arm-workers', type=int, default=1, help=argparse.SUPPRESS)
    parser.add_argument('--keep-results', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        args.worker_output.write_text(json.dumps(worker(args)))
        return 0
    if not (args.snapshot and args.reference):
        parser.error('--snapshot and --reference are required')
    paired(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
