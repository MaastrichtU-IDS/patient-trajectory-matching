#!/usr/bin/env python3
"""The excluded-records path, timed where it runs, paired against main (decision log, D44, rule 6.3).

D44 changes the excluded-records rule (`execution_store.apply_exclusions`), which runs once for every result of a
patient whose excluded records a step's concepts reach. The scaling cohort holds no excluded record, so the matcher
benchmark's paired run never calls it (D43's lesson: a latency rule must time the path the change executes). The
MIMIC-IV demo does: 448,419 excluded records (admission's left-out inputevents and chartevents rows), and its
differentials' requests reach them on most results.

    python tools/benchmark_exclusions.py --input-dir data/local-mimic/icu --reference main \\
        --work-dir ~/ptm-local/d44/exclusions --output verification/d44-exclusions-latency-verdict.json

- The demo is converted (`mimic_store_ingest.convert`, chartevents included) and built once, by this tree (the build is
  main's code byte for byte; only matching changed), into `--work-dir`.
- The requests are the MIMIC demo report's own (`verify_mimic_store_ingest`): the 500 seeded queries of its
  differential, its value, derived and relative requests, each with its seeded windows, through the dispatcher
  (`store_conditions.match`, one worker, the page's entry point). They are written into the work directory and removed
  at the end; they name demo patients, so nothing of them is committed.
- Protocol: D31's paired interleaved protocol (`paired-interleaved-1.0`). The reference code (a git ref, extracted as
  the matcher benchmark extracts its arms) and the candidate (this tree) each run every request in a fresh process,
  back to back for each repeat, the order alternating (ABBA). Each process first runs the first `--warm` requests
  untimed, then every request once, timed. A pair's ratio is the candidate's total over the reference's.
- Structure and movement (untimed, after the timed runs): the candidate runs every request once more with each call
  of `apply_exclusions` also answered by the reference code's (`tools/d44_measure.py`'s `Shadow`); the counts show
  that the changed rule ran, how often it met an `unresolved` result, how often it marked a match, and that it changed
  nothing else.
- Verdict (D44 rule 6.3): the ratio at most 1.05 in every pair; at least 4 repeats, even; the structural check: the
  rule ran on at least 1,000 results, at least one of them `unresolved`, and every call's answer equals the
  reference's or is it with D44's mark.

Aggregates only (D17): counts and seconds, no patient rows, identifiers or times.
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

PROFILE = 'exclusions-latency-1.0'
PROTOCOL = 'paired-interleaved-1.0'
RATIO_BAR = 1.05
MIN_CALLS = 1000
REQUESTS = 912  # the MIMIC demo report's differentials: 500 seeded, 200 value, 150 derived, 2 named and 60 relative
ARTIFACTS = ('tools/benchmark_exclusions.py', 'tools/d44_measure.py', 'patterns/execution_store.py',
             'patterns/store_conditions.py', 'patterns/store_conditions_counts.py', 'patterns/store_conditions_relative.py',
             'patterns/verify_mimic_store_ingest.py', 'patterns/mimic_store_ingest.py', 'patterns/trajectory_graph.py')
REF_FILES = ('patterns/execution_store.py', 'patterns/store_conditions.py', 'patterns/store_conditions_counts.py',
             'patterns/store_conditions_relative.py', 'patterns/trajectory_graph.py')


def prepare(args):
    """Convert and build the demo, write the requests: (snapshot path, requests path, counts by group)."""
    sys.path.insert(0, str(ROOT))
    from patterns import execution_store as es
    from patterns import mimic_store_ingest as msi
    from patterns import verify_mimic_store_ingest as v
    work = Path(args.work_dir).expanduser()
    work.mkdir(parents=True, exist_ok=True)
    result = msi.convert(Path(args.input_dir), v.DATASET, workers=args.convert_workers, chartevents=True)
    ingest = result['ingest']
    snapshot = es.build(ingest, work / 'store')
    pool = v.concept_pool(ingest)
    requests = []
    import random
    for seed in range(v.SEEDS):
        rng = random.Random(seed)
        query, windows = v.random_query(rng, pool, ingest), v.random_windows(rng, ingest)
        requests.append({'group': 'differential', 'query': query, 'windows': windows, 'conditions': None})
    for _, query, conditions, windows in v.value_cases(ingest, v.VALUE_SEEDS):
        requests.append({'group': 'value', 'query': query, 'windows': windows, 'conditions': conditions})
    for _, _, query, conditions, windows in v.derived_cases(ingest, v.DERIVED_SEEDS):
        requests.append({'group': 'derived', 'query': query, 'windows': windows, 'conditions': conditions})
    for _, query, conditions, windows in v.relative_cases(ingest, v.RELATIVE_SEEDS):
        requests.append({'group': 'relative', 'query': query, 'windows': windows, 'conditions': conditions})
    if args.limit:  # a trial of the tool only: the protocol check fails
        requests = requests[:args.limit]
    path = work / 'requests.json'
    path.write_text(json.dumps(requests))
    groups = {}
    for r in requests:
        groups[r['group']] = groups.get(r['group'], 0) + 1
    with es.open_store(snapshot) as store:
        tables = {n: t['rows'] for n, t in sorted(store.manifest['tables'].items())}
        snapshot_id = store.snapshot_id
    return Path(snapshot), path, groups, tables, snapshot_id


def worker(args):
    """One side's run: every request through `store_conditions.match` of the tree at --code-root (the first --warm
    untimed), timed; or with --shadow, the untimed movement pass of the candidate against the reference at --shadow."""
    code_root = str(Path(args.code_root).resolve())
    sys.path[:] = [code_root] + [p for p in sys.path if Path(p or '.').resolve() != ROOT and p != str(ROOT / 'tools')]
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    if not Path(es.__file__).resolve().is_relative_to(Path(code_root)):
        raise RuntimeError(f'imported {es.__file__}, not from {code_root}')
    requests = json.loads(Path(args.requests).read_text())
    out = {'code': es.__file__}
    with es.open_store(args.snapshot) as store:
        if args.shadow:
            import importlib.util
            spec = importlib.util.spec_from_file_location('d44_measure', ROOT / 'tools/d44_measure.py')
            d44 = importlib.util.module_from_spec(spec)
            sys.path.append(str(ROOT))  # d44_measure's own imports (this tree is the candidate: the same code)
            spec.loader.exec_module(d44)
            spec = importlib.util.spec_from_file_location('patterns._d44_reference_execution_store',
                                                          Path(args.shadow) / 'patterns/execution_store.py')
            main_es = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(main_es)
            shadow = d44.Shadow(main_es)
            unresolved = calls = marked_results = 0
            real = es.apply_exclusions

            def counting(query, result, *rest, **kw):
                nonlocal unresolved, calls
                calls += 1
                unresolved += result['status'] == 'unresolved'
                return real(query, result, *rest, **kw)
            from unittest.mock import patch
            for r in requests:
                with patch.object(es, 'apply_exclusions', counting), shadow.watching(r['group']):
                    answer = sc.match(store, r['query'], r['windows'], conditions=r['conditions'])
                marked_results += sum(1 for g in answer['results'] if g['status'] == 'unresolved' and any(
                    es.MAY_MAKE_EXACT in (g.get(k, {}).get('note') or '').split('; ') for k in es.BESIDE))
            out.update(movement=shadow.summary(), calls=calls, unresolved_calls=unresolved,
                       results_marked=marked_results)
            return out
        for r in requests[:args.warm]:
            sc.match(store, r['query'], r['windows'], conditions=r['conditions'])
        seconds = []
        for r in requests:
            t = time.perf_counter()
            sc.match(store, r['query'], r['windows'], conditions=r['conditions'])
            seconds.append(time.perf_counter() - t)
    import resource
    out.update(seconds=seconds, peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return out


def run_side(root, args, scratch, shadow=None):
    out = scratch / f'side-{time.monotonic_ns()}.json'
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--code-root', str(root), '--snapshot',
               str(args.snapshot_path), '--requests', str(args.requests_path), '--warm', str(args.warm),
               '--worker-output', str(out)]
    if shadow is not None:
        command += ['--shadow', str(shadow)]
    subprocess.run(command, cwd=scratch, check=True)
    return json.loads(out.read_text())


def statistics_of(records, groups):
    pairs = []
    for rec in records:
        ref, cand = rec['reference']['seconds'], rec['candidate']['seconds']
        by_group = {}
        for g, a, b in zip(groups, ref, cand, strict=True):
            x = by_group.setdefault(g, [0.0, 0.0])
            x[0] += a
            x[1] += b
        pairs.append({'repeat': rec['repeat'], 'order': rec['order'], 'reference_seconds': sum(ref),
                      'candidate_seconds': sum(cand), 'ratio': sum(cand) / sum(ref),
                      'ratio_by_group': {g: b / a for g, (a, b) in sorted(by_group.items())}})
    return pairs


def verdict_of(pairs, repeats, structure, limit=None, requests_run=REQUESTS):
    protocol = {'repeats_at_least_4_and_even': repeats >= 4 and repeats % 2 == 0,
                'every_request_run': not limit and requests_run == REQUESTS, 'requests_run': requests_run}
    protocol['met'] = protocol['repeats_at_least_4_and_even'] and protocol['every_request_run']
    ratio = {'bar': RATIO_BAR, 'ratios': [p['ratio'] for p in pairs],
             'every_pair_at_most': bool(pairs) and all(p['ratio'] <= RATIO_BAR for p in pairs)}
    moved = structure['movement']
    structural = {'calls': structure['calls'], 'unresolved_calls': structure['unresolved_calls'],
                  'rule_ran_at_least': MIN_CALLS, 'ran': structure['calls'] >= MIN_CALLS,
                  'met_unresolved': structure['unresolved_calls'] > 0, 'other_changes': moved['other'],
                  'marked_calls': moved['marked'], 'results_marked': structure['results_marked']}
    structural['met'] = structural['ran'] and structural['met_unresolved'] and moved['other'] == 0
    return {'protocol': protocol, 'ratio': ratio, 'structural': structural,
            'met': protocol['met'] and ratio['every_pair_at_most'] and structural['met']}


def paired(args):
    sys.path.insert(0, str(ROOT))
    from tools.benchmark_matcher import extract_ref
    started = time.monotonic()
    scratch = Path(tempfile.mkdtemp(prefix='exclusions-latency-'))
    try:
        print('converting and building the demo ...', file=sys.stderr, flush=True)
        args.snapshot_path, args.requests_path, groups, tables, snapshot_id = prepare(args)
        names = [r['group'] for r in json.loads(args.requests_path.read_text())]
        root = scratch / 'reference'
        commit = extract_ref(args.reference, root)
        codes = {'candidate': {'root': ROOT, 'git_ref': 'this tree', 'git_commit': None},
                 'reference': {'root': root, 'git_ref': args.reference, 'git_commit': commit,
                               'files_at_ref': [{'path': p, 'sha256': hashlib.sha256((root / p).read_bytes()).hexdigest()}
                                                for p in REF_FILES if (root / p).is_file()]}}
        records = []
        for repeat in range(args.repeats):
            order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
            rec = {'repeat': repeat, 'order': order}
            for side in order:
                print(f'exclusions repeat {repeat} {side} ...', file=sys.stderr, flush=True)
                rec[side] = run_side(codes[side]['root'], args, scratch)
                print(f'  total {sum(rec[side]["seconds"]):.1f} s', file=sys.stderr, flush=True)
            records.append(rec)
        print('structure and movement ...', file=sys.stderr, flush=True)
        structure = run_side(ROOT, args, scratch, shadow=root)
        pairs = statistics_of(records, names)
        ran = {len(rec[side]['seconds']) for rec in records for side in ('reference', 'candidate')}
        verdict = verdict_of(pairs, args.repeats, structure, args.limit, ran.pop() if len(ran) == 1 else -1)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
        if not args.keep_requests:
            Path(args.work_dir).expanduser().joinpath('requests.json').unlink(missing_ok=True)
    report = {
        'profile': PROFILE, 'protocol': PROTOCOL, 'snapshot_id': snapshot_id, 'tables': tables,
        'requests': groups, 'through': 'store_conditions.match, one worker, detail full',
        'warm_requests_untimed': args.warm, 'repeats': args.repeats, 'limit': args.limit,
        'order': 'ABBA: the reference first in even repeats, the candidate first in odd',
        'reference': {k: v for k, v in codes['reference'].items() if k != 'root'},
        'candidate': {k: v for k, v in codes['candidate'].items() if k != 'root'},
        'pairs': pairs, 'candidate_seconds_median': statistics.median(p['candidate_seconds'] for p in pairs),
        'reference_seconds_median': statistics.median(p['reference_seconds'] for p in pairs),
        'peak_rss_bytes': {side: max(rec[side]['peak_rss_bytes'] for rec in records) for side in ('reference', 'candidate')},
        'structure_and_movement': {k: v for k, v in structure.items() if k != 'code'},
        'verdict': verdict, 'seconds': round(time.monotonic() - started, 1),
        'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS},
        'contains_patient_level_data': False,
        'limitations': ('one machine and one session; the open MIMIC-IV demo (100 patients), whose times are exact, so '
                        'D44\'s mark is rare there; a pair\'s time is one timed pass of every request per process'),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1) + '\n')
    print(args.output)
    print(json.dumps(verdict), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--input-dir', help='the verified MIMIC-IV demo icu directory (D11)')
    parser.add_argument('--reference', default='main', help='git ref of the reference code')
    parser.add_argument('--work-dir', default='~/ptm-local/d44/exclusions')
    parser.add_argument('--repeats', type=int, default=4, help='repeats (even: balanced ABBA)')
    parser.add_argument('--warm', type=int, default=20, help='requests run untimed first in each process')
    parser.add_argument('--convert-workers', type=int, default=8)
    parser.add_argument('--keep-requests', action='store_true')
    parser.add_argument('--limit', type=int, help='run only the first N requests (a trial of the tool; no verdict)')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/d44-exclusions-latency-verdict.json')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker-output', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--code-root', help=argparse.SUPPRESS)
    parser.add_argument('--snapshot', help=argparse.SUPPRESS)
    parser.add_argument('--requests', help=argparse.SUPPRESS)
    parser.add_argument('--shadow', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        args.worker_output.write_text(json.dumps(worker(args)))
        return 0
    if not args.input_dir:
        parser.error('--input-dir is required')
    paired(args)
    return 0


if __name__ == '__main__':
    sys.exit(main())
