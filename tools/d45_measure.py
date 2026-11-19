#!/usr/bin/env python3
"""D45's rule 1.1: the coded-only small cases through the dispatcher, the page's path (decision log, D45).

    python tools/d45_measure.py worlds --first 50000 --snapshots 20 --output FILE

D39's and D42's small-case harnesses (`coded_only_cases`, `gene_coded_cases`) call the routed module, or the dispatcher
with its count post-pass off (D43b), so the post-pass on coded-only requests (D43c) is checked there only by its own
generators and by the oracle on snapshots. The page's server calls `store_conditions.match` with the post-pass on.
This runs those harnesses' generators (coded-plain, coded-derived, gene-coded-plain, gene-coded-derived), with their
own tiny patients, requests and windows, through that path. For each request it takes:
- `on`: `store_conditions.match` as the server calls it (the post-pass on);
- `off`: the same with the post-pass off (the routed module's own answer);
- `unpruned`: `on` with the posting summaries' pruning off;
- `itself`: the dispatcher's answer on the snapshot built without letter mentions, to the same request without
  `exclude_kinds` (`coded_only_cases.reference_conditions`, `gene_coded_cases.gene_conditions`), post-pass on.
Checks, per result: `on` passes the worlds check against the patient's kept events (and, for a coded-only patient with
excluded records, the whole source, as D44 reads it); `off` equals the independent reference (D39's or D42's: the
existing reference on the ingest with mentions removed); `on` differs from `off` only where `off` is `unresolved` with
STEP_CONDITION_UNCERTAIN (D43's movement rule); `unpruned` equals `on` byte for byte; `itself` equals `on` result for
result (the meaning by removal, through the post-pass). Synthetic data only; the output holds counts and at most a few
small-case identifiers.
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import json
from pathlib import Path
import random
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns import coded_only_cases as co  # noqa: E402
from patterns import execution_store as es  # noqa: E402
from patterns import gene_coded_cases as gcc  # noqa: E402
from patterns import store_conditions as sc  # noqa: E402

GENERATORS = (*co.GENERATORS, *gcc.GENERATORS)
UNCERTAIN = 'STEP_CONDITION_UNCERTAIN'


def _harness(generator):
    """(rng seed prefix, ingest maker, request maker, request without exclusion, worlds check of one result)."""
    if generator in co.GENERATORS:
        def check(got, query, conditions, p, window, mix):
            wrong = co.check_result(got, query, conditions, co.kept_patient(p, window))
            if p.get('excluded'):
                mix[co.WHOLE] += 1
                wrong += [f'{w} (whole source)' for w in co.check_result(
                    got, query, conditions, co.kept_patient(p, window, whole=True), whole=True)]
            return wrong
        return 'coded', co.tiny_ingest, co.tiny_request, co.reference_conditions, check

    def check(got, query, conditions, p, window, mix):
        patient, reports = gcc.kept_patient(p, window)
        return gcc.check_result(got, query, conditions, patient, reports)
    return 'gene-coded', gcc.tiny_ingest, gcc.tiny_request, gcc.gene_conditions, check


def moved_ok(on, off):
    """D43's movement rule for one result: unchanged, or `off` is `unresolved` with STEP_CONDITION_UNCERTAIN."""
    return json.dumps(on) == json.dumps(off) or (off['status'] == 'unresolved' and off.get('reason') == UNCERTAIN)


def run_snapshot(root, seed, generator, patients=40, requests=25, defect=None):
    """One small snapshot through the dispatcher: a dict of counts and failing cases (see the module's docstring). With
    `defect`, one of D43's planted defects of the post-pass (`store_conditions_counts._D43_DEFECT`, in this process: every
    run here is at one worker)."""
    from patterns import store_conditions_counts as scn
    prefix, make_ingest, make_request, without, check = _harness(generator)
    rng = random.Random(f'{prefix}/{generator}/{seed}')  # the harnesses' own seeding, so the cases are theirs
    ingest = make_ingest(rng, f'{generator}-{seed}', patients)
    by_id = {p['patient_id']: p for p in ingest['patients']}
    reference = co.Reference(ingest) if generator in co.GENERATORS else gcc.Reference(ingest)
    out = {'cases': 0, 'violations': [], 'mismatches': [], 'moved_wrongly': [], 'pruning': [], 'itself': [],
           'mix': Counter(), 'moved': Counter()}
    with contextlib.ExitStack() as stack:
        store = stack.enter_context(es.open_store(es.build(ingest, root / f'{generator}-{seed}')))
        bare = stack.enter_context(es.open_store(es.build(co.without_kinds(ingest, co.KINDS),
                                                          root / f'{generator}-{seed}-bare')))
        stack.enter_context(patch.object(scn, '_D43_DEFECT', defect))
        for k in range(requests):
            query, conditions = make_request(rng, generator)
            windows = co.tiny_windows(rng, ingest)
            on = sc.match(store, query, windows, conditions=conditions)
            with patch.object(sc, 'COUNT_POST_PASS', False):
                off = sc.match(store, query, windows, conditions=conditions)
            with patch.object(es, '_PRUNE', False):
                unpruned = sc.match(store, query, windows, conditions=conditions)
            if json.dumps(unpruned) != json.dumps(on):
                out['pruning'].append((seed, k))
            itself = sc.match(bare, query, windows, conditions=without(conditions))
            if json.dumps(itself['results']) != json.dumps(on['results']):
                out['itself'].append((seed, k))
            want = reference.match(query, windows, conditions)
            for i, (got, before) in enumerate(zip(on['results'], off['results'], strict=True)):
                out['cases'] += 1
                out['mix'][got['status']] += 1
                pid = got['patient_id']
                if co.beside_marked(got):
                    out['mix'][co.MARKED] += 1
                wrong = check(got, query, conditions, by_id[pid], windows[pid], out['mix'])
                if wrong:
                    out['violations'].append((seed, k, pid, wrong[:2]))
                if json.dumps(co.unfiltered(before)) != json.dumps(want[i]):
                    out['mismatches'].append((seed, k, pid))
                if json.dumps(got) != json.dumps(before):
                    out['moved'][f"{before['status']} -> {got['status']}"] += 1
                if not moved_ok(got, before):
                    out['moved_wrongly'].append((seed, k, pid))
    return out


def run_generator(root, generator, snapshots, first, defect=None):
    total = {'cases': 0, 'violations': [], 'mismatches': [], 'moved_wrongly': [], 'pruning': [], 'itself': [],
             'mix': Counter(), 'moved': Counter()}
    for seed in range(first, first + snapshots):
        one = run_snapshot(root, seed, generator, defect=defect)
        for key, value in one.items():
            total[key] += value
    return {'cases': total['cases'], 'snapshots': snapshots, 'first_seed': first,
            'violations': len(total['violations']), 'reference_mismatches': len(total['mismatches']),
            'moved_outside_the_rule': len(total['moved_wrongly']), 'pruning_differences': len(total['pruning']),
            'itself_differences': len(total['itself']),
            'moved_by_the_post_pass': dict(sorted(total['moved'].items())), 'mix': dict(sorted(total['mix'].items())),
            'first_failures': {k: [list(map(str, v)) for v in total[k][:3]]
                               for k in ('violations', 'mismatches', 'moved_wrongly', 'pruning', 'itself')}}


def met(row):
    return (row['cases'] > 0 and row['violations'] == 0 and row['reference_mismatches'] == 0
            and row['moved_outside_the_rule'] == 0 and row['pruning_differences'] == 0
            and row['itself_differences'] == 0)


def worlds(args):
    started = time.monotonic()
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for generator in args.generators:
            print(f'd45 worlds {generator} ...', file=sys.stderr, flush=True)
            out[generator] = run_generator(Path(tmp), generator, args.snapshots, args.first, args.defect)
            out[generator]['met'] = met(out[generator])
            print('  ' + json.dumps({k: out[generator][k] for k in ('cases', 'violations', 'reference_mismatches',
                                                                        'moved_outside_the_rule', 'pruning_differences',
                                                                        'itself_differences')}),
                  file=sys.stderr, flush=True)
    return {'rule': 'D45 rule 1.1', 'defect': args.defect, 'path': 'store_conditions.match, count post-pass on (the page server\'s call)',
            'generators': out, 'met': bool(out) and all(r['met'] for r in out.values()) and all(
                r['cases'] >= args.minimum for r in out.values()),
            'minimum_cases_per_generator': args.minimum, 'seconds': time.monotonic() - started,
            'contains_patient_level_data': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('what', choices=('worlds',))
    parser.add_argument('--first', type=int, default=50_000, help='first snapshot seed')
    parser.add_argument('--snapshots', type=int, default=20, help='small snapshots per generator (1,000 cases each)')
    parser.add_argument('--generators', nargs='+', default=list(GENERATORS), choices=list(GENERATORS))
    parser.add_argument('--minimum', type=int, default=20_000, help='cases per generator the rule needs')
    parser.add_argument('--defect', help="one of D43's planted defects of the post-pass (store_conditions_counts)")
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    report = worlds(args)
    text = json.dumps(report, indent=1) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text)
    else:
        sys.stdout.write(text)
    return 0 if report['met'] else 1


if __name__ == '__main__':
    sys.exit(main())
