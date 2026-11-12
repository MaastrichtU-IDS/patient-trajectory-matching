#!/usr/bin/env python3
"""D30: explain every per-query result digest the matcher comparison moved, by the stage that moved it.

D30 changed three things at once (decision log, D30): `last` takes the latest start, the order rule decides an
uncertain `first` or `last` target on its envelope or leaves it unresolved, and synthetic letter mentions range
from birth to their letter. Arm A runs the code before D21 on its own snapshots (letter-timed mentions, the old
`last`), so its digests differ from arm B's wherever any of the three reaches. This tool reproduces arm A's digest
query by query with this tree's code, on a snapshot this tree builds with letter-timed mentions
(`synthetic_ingest`, `mention_time='letter'`) and with both engine switches set as before D30, then turns the
fixes on one at a time:

  S0  letter-timed snapshot, order rule off, `last` by the order before D30  (must equal arm A's digest)
  S1  letter-timed snapshot, order rule off, `last` by the latest start
  S2  letter-timed snapshot, order rule on                                   (both engine fixes)
  S3  this run's snapshot (mentions at or before their letter), as arm B ran it  (must equal arm B's digest)

S0 to S2 draw their queries as arm A's worker does, from the cohort converted with letter-timed mentions: each arm's
worker draws the queries with its own converter, and the query generator is shaped around event times, so arm A's
queries differ from arm B's wherever an anchor event is a letter mention. That change is part of the mention timing.

and names, for each query whose S0 and S3 digests differ, the stages at which it changed. A second ordering
(S0, S1, this run's snapshot with the order rule off, S3) puts the mention timing before the order rule, so that the
rule acting on the wider mentions is counted under the rule. The stages localize a change; they do not by themselves
show that it is a fix (the engine's worlds check is the evidence for that). The queries, windows
and digests are the matcher comparison's own (`benchmark_matcher.worker`), run in this process so that the
switches apply. For the confirmation run, which has no arm A, S0 is compared with the D28 report's arm B, the
code before D30 on the snapshots before D30.

    python tools/d30_attribution.py --build-legacy 1000=DIR 10000=DIR ...        # prints SIZE=SNAPSHOT
    python tools/d30_attribution.py --sizes 1000=SNAPSHOT=LEGACY ... --confirm 300000 \\
        --before-report OLD_MATCHER_REPORT.json

The report holds digests and counts only, no patient-level data.
"""
from __future__ import annotations

import argparse
import functools
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
ARTIFACTS = ('tools/d30_attribution.py', 'tools/benchmark_matcher.py', 'tools/benchmark_execution_store.py',
             'patterns/execution_store.py', 'patterns/trajectory_graph.py', 'patterns/synthetic_ingest.py')
STAGES = ('last order', 'order rule', 'mention timing')
STAGES_TIMING_FIRST = ('last order', 'mention timing', 'order rule')


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_legacy(size, directory, memory_limit):
    """A snapshot of the scaling cohort of `size`, converted with letter-timed mentions (the timing before D30)."""
    from patterns import synthetic_ingest as si
    bench = load('bench_d30', ROOT / 'tools/benchmark_execution_store.py')
    si.iter_patients = functools.partial(si.iter_patients, mention_time='letter')
    si.synthetic_provenance = functools.partial(si.synthetic_provenance, mention_time='letter')
    work = Path(directory)
    work.mkdir(parents=True, exist_ok=True)
    out = bench.build_worker(size, work, 1, memory_limit, work / 'progress.json')
    (work / 'pool.ndjson').unlink(missing_ok=True)
    return out['snapshot_path']


def digests(matcher, snapshot, size, queries, memory_limit, order_rule, last_key, letter=False):
    """The matcher comparison's per-query digests on `snapshot` with the engine switches as given. With `letter`, the
    queries are drawn from the cohort converted with letter-timed mentions, as arm A's worker draws them: the query
    generator is shaped around event times, and each arm's worker draws its queries with its own converter."""
    from patterns import synthetic_ingest as si
    from patterns import trajectory_graph as tg
    args = types.SimpleNamespace(code_root=str(ROOT), snapshot=str(snapshot), size=size, queries=queries, repeats=1,
                                 detail='full', memory_limit=memory_limit, arm_workers=1, no_prune=False,
                                 no_prune_ends=False)
    saved = si.iter_patients, si.synthetic_provenance
    if letter:
        si.iter_patients = functools.partial(saved[0], mention_time='letter')
        si.synthetic_provenance = functools.partial(saved[1], mention_time='letter')
    tg._ORDER_RULE, tg._LAST_KEY = order_rule, last_key
    try:
        out = matcher.worker(args)
    finally:
        tg._ORDER_RULE, tg._LAST_KEY = True, tg.last_key
        si.iter_patients, si.synthetic_provenance = saved
    return {row['seed']: row['digest'] for row in out['runs'][0]}


def attribute(matcher, snapshot, legacy, size, queries, memory_limit, before, after):
    from patterns import trajectory_graph as tg
    started = time.monotonic()
    stages = [digests(matcher, legacy, size, queries, memory_limit, False, tg._LAST_KEY_BEFORE_D30, letter=True),
              digests(matcher, legacy, size, queries, memory_limit, False, tg.last_key, letter=True),
              digests(matcher, legacy, size, queries, memory_limit, True, tg.last_key, letter=True),
              digests(matcher, snapshot, size, queries, memory_limit, True, tg.last_key),
              digests(matcher, snapshot, size, queries, memory_limit, False, tg.last_key)]
    per_query, tally, other = [], {}, {}
    for seed in sorted(stages[0]):
        chain = [s[seed] for s in stages[:4]]
        moved = [name for name, a, b in zip(STAGES, chain, chain[1:]) if a != b]
        # The second ordering: mention timing (order rule off) before the order rule, which separates the rule
        # acting on the wider mentions from the timing itself.
        alt = [chain[0], chain[1], stages[4][seed], chain[3]]
        moved_alt = [name for name, a, b in zip(STAGES_TIMING_FIRST, alt, alt[1:]) if a != b]
        per_query.append({'seed': seed, 'before_reproduced': chain[0] == before.get(seed),
                          'after_reproduced': chain[-1] == after.get(seed), 'stages': moved,
                          'stages_timing_first': moved_alt})
        key = ' + '.join(moved) or 'unchanged'
        tally[key] = tally.get(key, 0) + 1
        key = ' + '.join(moved_alt) or 'unchanged'
        other[key] = other.get(key, 0) + 1
    return {'size': size, 'queries': queries,
            'legacy_snapshot_id': json.loads((Path(legacy) / 'manifest.json').read_text())['snapshot_id'],
            'snapshot_id': json.loads((Path(snapshot) / 'manifest.json').read_text())['snapshot_id'],
            'before_reproduced': sum(q['before_reproduced'] for q in per_query),
            'after_reproduced': sum(q['after_reproduced'] for q in per_query),
            'moved': sum(1 for q in per_query if q['stages']), 'by_stages': dict(sorted(tally.items())),
            'by_stages_timing_first': dict(sorted(other.items())),
            'seconds': time.monotonic() - started, 'per_query': per_query}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--build-legacy', nargs='+', metavar='SIZE=DIRECTORY')
    parser.add_argument('--sizes', nargs='+', metavar='SIZE=SNAPSHOT=LEGACY_SNAPSHOT')
    parser.add_argument('--confirm', type=int, help='size of the confirmation run to attribute as well')
    parser.add_argument('--report', type=Path, default=ROOT / 'verification/matcher-comparison-report.json')
    parser.add_argument('--before-report', type=Path, help='the matcher report before D30 (for the confirmation)')
    parser.add_argument('--memory-limit', default='4GB')
    parser.add_argument('--output', type=Path, default=ROOT / 'verification/d30-attribution-report.json')
    args = parser.parse_args(argv)
    if args.build_legacy:
        for item in args.build_legacy:
            size, _, directory = item.partition('=')
            print(f'{size}={build_legacy(int(size), directory, args.memory_limit)}', flush=True)
        return 0
    matcher = load('matcher_d30', ROOT / 'tools/benchmark_matcher.py')
    report = json.loads(args.report.read_text())
    by_size = {e['size']: e for e in report['sizes']}
    out = {'about': __doc__.split('\n\n')[0], 'stages': ['S0 before D30', *STAGES], 'sizes': [], 'confirmation': None}
    pairs = {}
    for item in args.sizes:
        size, snapshot, legacy = item.split('=')
        pairs[int(size)] = (snapshot, legacy)
        entry = by_size[int(size)]
        a = {q['seed']: q['digest'] for q in entry['arms']['A']['per_query']}
        b = {q['seed']: q['digest'] for q in entry['arms']['B']['per_query']}
        print(f'size {size} ...', file=sys.stderr, flush=True)
        out['sizes'].append({**attribute(matcher, snapshot, legacy, int(size), entry['queries'], args.memory_limit, a, b),
                             'compared_with': {'before': 'arm A (this report)', 'after': 'arm B (this report)'}})
    if args.confirm:
        conf = report['confirmation']
        old = json.loads(args.before_report.read_text())['confirmation']
        before = {q['seed']: q['digest'] for q in old['arms']['B']['per_query']}
        after = {q['seed']: q['digest'] for q in conf['arms']['B']['per_query']}
        snapshot, legacy = pairs[args.confirm]
        print(f'confirmation at {args.confirm} ...', file=sys.stderr, flush=True)
        out['confirmation'] = {**attribute(matcher, snapshot, legacy, args.confirm, conf['queries'], args.memory_limit,
                                           before, after),
                               'compared_with': {'before': "arm B of the D28 report's confirmation",
                                                 'after': "arm B of this report's confirmation"}}
    out['artifacts'] = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS}
    out['contains_patient_level_data'] = False
    args.output.write_text(json.dumps(out, indent=1) + '\n')
    for e in out['sizes'] + ([out['confirmation']] if out['confirmation'] else []):
        print(f"{e['size']}: before reproduced {e['before_reproduced']}/{e['queries']}, after {e['after_reproduced']}/"
              f"{e['queries']}, moved {e['moved']}: {e['by_stages']}; timing first: {e['by_stages_timing_first']}", file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
