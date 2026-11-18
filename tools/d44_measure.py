#!/usr/bin/env python3
"""D44's ruled soundness, reference, planted-defect and movement measurements (decision log, D44, rules 1 to 4).

    python tools/d44_measure.py worlds --first 44000 --snapshots 20 --gene-first 4400000 --gene-cases 20000
    python tools/d44_measure.py defects --first 45000 --snapshots 4 --gene-first 4500000 --gene-cases 4000
    python tools/d44_measure.py reference --first 44000
    (each with --main REF, the code the movement is measured against, and --output FILE)

- `worlds` (rule 1): the worlds check with no carve-out on D39's coded-only snapshots (`coded_only_cases`, both
  generators, each result against the kept events and, for a patient with excluded records, against the whole source),
  D42's joint snapshots (`gene_coded_cases`, both generators, the whole source: excluded records and every reading of an
  undetermined gene record) and D38's gene cases (`gene_cases`, three generators, every reading); each result also
  against its independent reference.
- `defects` (rule 3): the two planted defects of the D44 rules (`execution_store._D44_DEFECT`) on the same harnesses.
- `reference` (rule 2): the snapshot differential through the dispatcher (`store_conditions.match`) for every profile:
  1.0 (D32's derived fixtures and the synthetic cohort, the requests without their conditions), 1.1 (Nth occurrences;
  D28's value fixtures), 1.2 (D33's relative fixtures and the cohort with series), 1.3 (D39's), gene (D38's), the joint
  profile (D42's), each against its own reference (`prepared_reference`, `coded_only_cases.Reference`,
  `gene_reference`, `gene_coded_cases.Reference`) at 1 and 2 workers, `decided` and with pruning off (a request with a
  count among them with the dispatcher's count post-pass off: those references are D32's two-pass rule, as D43b
  compared D42's); and the count post-pass itself on D43c's plan of count requests (fixture 1.1, 1.2, 1.3; sparse
  coverage 1.1, 1.3; gene fixture gene, 1.3, joint) against D43's oracle (`count_narrowing_cases.snapshot_compare`:
  the D32 reference with the oracle's truth for each STEP_CONDITION_UNCERTAIN patient, coverage and D18 reapplied, its
  variants included).
- Movement (rule 4), in every run: each call of the excluded-records rule (`execution_store.apply_exclusions`) in this
  process is also answered by MAIN's (`--main`, loaded from `git show REF:patterns/execution_store.py`), and the two
  answers are classified (`classify`): equal; this tree's is MAIN's with D44's mark (the note
  EXCLUDED_RECORDS_MAY_MAKE_EXACT on a `possible` match beside `unresolved`, and `excluded_steps` where MAIN's names
  none); or anything else, which the rule forbids. Calls in pool workers are not shadowed: a 2-worker answer must equal
  the 1-worker one.

Synthetic data only; the output holds counts and at most a few small-case identifiers.
"""
from __future__ import annotations

import argparse
from collections import Counter
import contextlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns import coded_only_cases as co  # noqa: E402
from patterns import execution_store as es  # noqa: E402
from patterns import gene_cases as gk  # noqa: E402
from patterns import gene_coded_cases as gcc  # noqa: E402
from patterns import store_conditions as sc  # noqa: E402
from patterns import store_conditions_counts as scn  # noqa: E402

DEFECTS = ('beside_unmarked', 'beside_exact_marked')


def load_main(ref):
    """MAIN's `execution_store` module, from `git show REF:patterns/execution_store.py` (its relative imports resolve
    to this tree's package, which is enough for `apply_exclusions`, a function of its arguments alone)."""
    commit = subprocess.run(['git', 'rev-parse', ref], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    source = subprocess.run(['git', 'show', f'{commit}:patterns/execution_store.py'], cwd=ROOT, check=True,
                            capture_output=True).stdout
    path = Path(tempfile.mkdtemp(prefix='d44-main-')) / 'execution_store_main.py'
    path.write_bytes(source)
    spec = importlib.util.spec_from_file_location('patterns._d44_main_execution_store', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return commit, module


def classify(old, new):
    """'equal', 'marked' (`new` is `old` with D44's mark and nothing else) or 'other'."""
    if json.dumps(old) == json.dumps(new):
        return 'equal'
    if old.get('status') != 'unresolved' or new.get('status') != 'unresolved':
        return 'other'
    marked = False
    for key in sorted(set(old) | set(new)):
        a, b = old.get(key), new.get(key)
        if key in es.BESIDE and a is not None and b is not None and a != b:
            notes = [n for n in (a.get('note') or '').split('; ') if n]
            if a['certainty'] != 'possible' or b != {**a, 'note': '; '.join(notes + [es.MAY_MAKE_EXACT])}:
                return 'other'
            marked = True
        elif key == 'excluded_steps' and a is None and b is not None:
            continue
        elif a != b:
            return 'other'
    if 'excluded_steps' in new and 'excluded_steps' not in old and not marked:
        return 'other'
    return 'marked' if marked else 'other'


class Shadow:
    """Every in-process call of `execution_store.apply_exclusions`, also answered by MAIN's, classified."""

    def __init__(self, main):
        self.main, self.tally, self.other, self.marked_by = main, Counter(), [], Counter()

    @contextlib.contextmanager
    def watching(self, workload):
        real = es.apply_exclusions

        def both(query, result, touched, starts, every=None, occurrence=None):
            new = real(query, result, touched, starts, every, occurrence)
            old = self.main.apply_exclusions(query, result, touched, starts, every, occurrence)
            kind = classify(old, new)
            self.tally[(workload, kind)] += 1
            if kind == 'marked':
                which = '+'.join(k for k in es.BESIDE if k in new and new[k] != old.get(k))
                self.marked_by[(workload, f'{old.get("reason")} / {which}')] += 1
            elif kind == 'other' and len(self.other) < 5:
                self.other.append({'workload': workload, 'main': old, 'tree': new})
            return new
        with patch.object(es, 'apply_exclusions', both):
            yield

    def summary(self):
        workloads = sorted({w for w, _ in self.tally})
        return {'by_workload': {w: {k: self.tally[(w, k)] for k in ('equal', 'marked', 'other')} for w in workloads},
                'marked_by_reason_and_match': {f'{w}: {r}': n for (w, r), n in sorted(self.marked_by.items())},
                'calls': sum(self.tally.values()), 'marked': sum(n for (_, k), n in self.tally.items() if k == 'marked'),
                'other': sum(n for (_, k), n in self.tally.items() if k == 'other'), 'other_examples': self.other}


# Rule 1: the worlds checks.

def gene_small(generator, n, first, defect=None):
    """D38's small cases (`gene_cases`): (violations, mismatches, status mix) over n seeds from `first`."""
    from patterns import store_conditions_gene as gc  # noqa: F401  (the store side `gene_cases` calls)
    bad, mismatched, mix = [], [], Counter()
    with patch.object(es, '_D44_DEFECT', defect):
        for seed in range(first, first + n):
            q, c, p, m, r, rd = gk.case(seed, generator)
            got = gk.store_result(q, c, p, m, r, rd)
            mix[got['status']] += 1
            if co.beside_marked(got):
                mix[co.MARKED] += 1
            wrong = gk.check(got, q, c, p, m, r, rd)
            if wrong:
                bad.append((seed, wrong))
            if json.dumps(got) != json.dumps(gk.reference_result(q, c, p, m, r, rd)):
                mismatched.append(seed)
    return bad, mismatched, mix


def summarise(violations, mismatches, mix, cases):
    return {'cases': cases, 'violations': len(violations), 'mismatches': len(mismatches), 'mix': dict(sorted(mix.items())),
            'first_violations': [list(v[:3]) for v in violations[:3]], 'first_mismatches': [list(m) for m in mismatches[:3]]}


def worlds(args, shadow, defect=None):
    out = {}
    with tempfile.TemporaryDirectory() as tmp:
        for generator in co.GENERATORS:
            with shadow.watching(f'worlds {generator}' if defect is None else f'defect {defect} {generator}'), \
                    patch.object(es, '_D44_DEFECT', defect):
                v, m, mix, n = co.run_small(Path(tmp) / generator, generator, args.snapshots, first=args.first,
                                            pruning=defect is None)
            out[generator] = summarise(v, m, mix, n)
        for generator in gcc.GENERATORS:
            with shadow.watching(f'worlds {generator}' if defect is None else f'defect {defect} {generator}'), \
                    patch.object(es, '_D44_DEFECT', defect):
                v, m, mix, n = gcc.run_small(Path(tmp) / generator, generator, args.snapshots, first=args.first,
                                             pruning=defect is None, itself=defect is None)
            out[generator] = summarise(v, m, mix, n)
    for generator in gk.GENERATORS:
        with shadow.watching(f'worlds gene-{generator}' if defect is None else f'defect {defect} gene-{generator}'):
            v, m, mix = gene_small(generator, args.gene_cases, args.gene_first, defect)
        out[f'gene-{generator}'] = summarise(v, m, mix, args.gene_cases)
    return out


# Rule 2: the snapshot differential through the dispatcher.

def _variants(store, query, windows, conditions, base):
    """Which of the 2-worker (chunk 9), `decided` and unpruned answers differ from `base`."""
    bad = []
    if json.dumps(sc.match(store, query, windows, conditions=conditions, workers=2, chunk=9)) != json.dumps(base):
        bad.append('2 workers')
    decided = sc.match(store, query, windows, conditions=conditions, detail='decided')
    listed = {r['patient_id']: r for r in base['results']}
    if any(json.dumps(r) != json.dumps(listed[r['patient_id']]) for r in decided['results']) \
            or decided['counts'] != base['counts']:
        bad.append('decided')
    with patch.object(es, '_PRUNE', False):
        if json.dumps(sc.match(store, query, windows, conditions=conditions)) != json.dumps(base):
            bad.append('pruning off')
    return bad


def _unfiltered(result):
    return {k: v for k, v in result.items() if k != 'reason'} if result.get('reason') == 'FILTERED' else result


class Profile:
    """One profile's tally: requests, results, mismatching results, variant differences, count requests sent to the
    oracle, refinements, status mix."""

    def __init__(self):
        self.requests = self.results = self.oracle_requests = self.counted_requests = 0
        self.mismatches, self.variants, self.mix = [], [], Counter()

    def plain(self, store, query, windows, conditions, want_of, label):
        out = sc.match(store, query, windows, conditions=conditions)
        want = want_of()
        self.requests += 1
        self.results += len(out['results'])
        for g, w in zip(out['results'], want, strict=True):
            self.mix[g['status']] += 1
            if co.beside_marked(g):
                self.mix[co.MARKED] += 1
            if json.dumps(_unfiltered(g)) != json.dumps(w):
                self.mismatches.append((label, g['patient_id']))
        self.variants += [(label, v) for v in _variants(store, query, windows, conditions, out)]

    def counted(self, store, ingest, query, windows, conditions, label, without=None):
        from patterns import count_narrowing_cases as cn
        bad, tally = cn.snapshot_compare(store, ingest, query, conditions, windows, variants=True, without=without)
        self.requests += 1
        self.oracle_requests += 1
        self.results += sum(tally[s] for s in ('exact', 'possible', 'relaxed', 'unresolved', 'none', 'not_comparable',
                                               'excluded'))
        self.mix.update({k: v for k, v in tally.items()})
        self.mismatches += [(label, str(b[0])[:80]) for b in bad]

    def summary(self):
        return {'requests': self.requests, 'results': self.results, 'count_requests_by_the_oracle': self.oracle_requests,
                'count_requests_post_pass_off': self.counted_requests,
                'mismatches': len(self.mismatches), 'variant_differences': len(self.variants),
                'mix': dict(sorted(self.mix.items())), 'first_mismatches': self.mismatches[:3],
                'first_variant_differences': self.variants[:3]}


def reference(args, shadow):
    from patterns import test_coded_only as tco
    from patterns import test_count_narrowing as tcn
    from patterns import test_derived_steps as tds
    from patterns import test_gene_coded as tgc
    from patterns import test_gene_steps as tgs
    from patterns import test_relative_conditions as trc
    from patterns import test_value_conditions as tvc
    from patterns import count_narrowing_cases as cn
    from patterns import gene_reference as gr
    n, first = args.requests, args.first
    profiles = {p: Profile() for p in ('1.0', '1.1', '1.2', '1.3', 'gene', 'gene-coded', 'counts')}
    seeds = range(first, first + n)

    def send(profile, state_store, ingest, query, windows, conditions, want_of, label, without=None):
        # A request with a count is the routed module's answer with the dispatcher's post-pass off (its reference is
        # D32's two-pass rule), as D43b compared D42's; the post-pass itself is D43c's plan below, against D43's oracle.
        if scn.has_counts(conditions):
            profiles[profile].counted_requests += 1
            with patch.object(sc, 'COUNT_POST_PASS', False):
                profiles[profile].plain(state_store, query, windows, conditions, want_of, label)
        else:
            profiles[profile].plain(state_store, query, windows, conditions, want_of, label)

    with shadow.watching('reference'):
        tds.setUpModule()
        try:
            for cohort in ('fixture', 'shared', 'clean', 'cohort'):
                ingest, store, prepared, _ = tds._STATE[cohort]
                for feature in ('nth', 'span', 'window'):
                    for seed in seeds:
                        query, conditions, windows = tds.case(seed, cohort, feature)
                        send('1.1', store, ingest, query, windows, conditions,
                             lambda: prepared.match(query, windows, conditions), f'{cohort}/{feature}/{seed}')
                        if feature == 'nth':  # 1.0: the request without its conditions
                            profiles['1.0'].plain(store, query, windows, None, lambda: prepared.match(query, windows),
                                                  f'{cohort}/plain/{seed}')
        finally:
            tds.tearDownModule()
        tvc.setUpModule()
        try:
            for cohort in ('plain', 'shared', 'clean'):
                ingest, store, prepared = tvc._STATE[cohort]
                for seed in seeds:
                    query, conditions, windows = tvc.case(seed, cohort)
                    send('1.1', store, ingest, query, windows, conditions,
                         lambda: prepared.match(query, windows, conditions), f'values/{cohort}/{seed}')
        finally:
            tvc.tearDownModule()
        trc.setUpModule()
        try:
            for cohort in ('fixture', 'shared', 'points', 'cohort'):
                ingest, store, prepared, _ = trc._STATE[cohort]
                for seed in seeds:
                    query, conditions, windows = trc.case(seed, cohort)
                    send('1.2', store, ingest, query, windows, conditions,
                         lambda: prepared.match(query, windows, conditions), f'{cohort}/{seed}')
        finally:
            trc.tearDownModule()
        tco.setUpModule()
        try:
            for cohort in ('fixture', 'shared', 'relative', 'cohort'):
                state = tco._STATE[cohort]
                for feature in ('plain', 'nth', 'span', 'window', 'relative'):
                    for seed in range(first, first + max(1, n // 4)):
                        made = tco.request(seed, cohort, feature)
                        if made is None:
                            continue
                        query, conditions, windows = made
                        send('1.3', state['store'], state['ingest'], query, windows, conditions,
                             lambda: state['reference'].match(query, windows, conditions), f'{cohort}/{feature}/{seed}',
                             without=state['without'])
        finally:
            tco.tearDownModule()
        tgs.setUpModule()
        try:
            for cohort in ('fixture', 'shared', 'cohort'):
                ingest, store, _ = tgs._STATE[cohort]
                for seed in seeds:
                    query, conditions, windows = tgs.case(seed, cohort)
                    send('gene', store, ingest, query, windows, conditions,
                         lambda: gr.gene_exhaustive_match(ingest, query, windows, conditions), f'{cohort}/{seed}')
        finally:
            tgs.tearDownModule()
        tgc.setUpModule()
        try:
            for cohort in ('fixture', 'shared', 'cohort'):
                ingest, store, _ = tgc._STATE[cohort]
                reference_of = tgc._STATE[cohort + '/reference']
                for seed in seeds:
                    query, conditions, windows = tgc.case(seed, cohort)
                    send('gene-coded', store, ingest, query, windows, conditions,
                         lambda: reference_of.match(query, windows, conditions), f'{cohort}/{seed}')
        finally:
            tgc.tearDownModule()
        tcn.setUpModule()
        try:
            for cohort, plan in (('fixture', ('1.1', '1.2', '1.3')), ('sparse', ('1.1', '1.3')),
                                 ('gene', ('gene', '1.3', 'gene-coded'))):
                state = tcn._STATE[cohort]
                for profile in plan:
                    for seed in range(first, first + max(1, n // 2)):
                        query, conditions, windows = cn.snapshot_case(seed, cohort, state['ingest'], state['pool'],
                                                                      profile)
                        profiles['counts'].counted(state['store'], state['ingest'], query, windows, conditions,
                                                   f'D43c plan {cohort}/{profile}/{seed}',
                                                   state['without'] if profile == '1.3' else None)
        finally:
            tcn.tearDownModule()
    return {p: t.summary() for p, t in profiles.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('what', choices=('worlds', 'defects', 'reference'))
    parser.add_argument('--main', default='main', help='git ref of the code movement is measured against')
    parser.add_argument('--first', type=int, default=44_000, help='first seed (snapshots, requests)')
    parser.add_argument('--snapshots', type=int, default=20, help='small snapshots per generator (1,000 cases each)')
    parser.add_argument('--gene-first', type=int, default=4_400_000, help="first seed of D38's gene cases")
    parser.add_argument('--gene-cases', type=int, default=20_000, help="D38's gene cases per generator")
    parser.add_argument('--requests', type=int, default=40, help='snapshot requests per cohort (and feature)')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    commit, main_es = load_main(args.main)
    shadow = Shadow(main_es)
    started = time.monotonic()
    if args.what == 'worlds':
        out = {'worlds': worlds(args, shadow)}
    elif args.what == 'defects':
        out = {'defects': {d: worlds(args, shadow, d) for d in DEFECTS}}
    else:
        out = {'reference': reference(args, shadow)}
    out.update(movement=shadow.summary(), main=commit, arguments={k: str(v) for k, v in vars(args).items()},
               seconds=round(time.monotonic() - started, 1))
    text = json.dumps(out, indent=1, default=str)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n')
    print(text)
    return 0


if __name__ == '__main__':
    sys.exit(main())
