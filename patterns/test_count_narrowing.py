"""Counts decided where D32's two-pass rule leaves them undecided (D43 and D43b, `count_narrowed_v1`,
`patterns/store_conditions_counts.py`), as a post-pass of the dispatcher (`patterns/store_conditions.py`).

- Small cases (`count_narrowing_cases`): every refined result checked against every choice of times
  (`trajectory_graph_worlds.check_derived`) and equal to the truth by enumeration; only STEP_CONDITION_UNCERTAIN results
  move, each to a decided, complete one; on D32's span and period generators and the two new ones.
- The oracle against enumeration on small cases.
- Planted defects, each caught (pinned seeds for the per-patient ones; the differential for D18 and coverage).
- Snapshots: the dispatcher against the reference (the D32 reference, and the oracle with coverage and D18 reapplied
  on its STEP_CONDITION_UNCERTAIN patients) for profiles 1.1, 1.2, 1.3 and gene, at 1 and 2 workers, both detail levels,
  pruning on and off; D39's identity through the dispatcher.
- The pool (D43b): the post-pass in the store's worker pool gives the in-process answer at 1, 2 and 3 workers and any
  cut into chunks; with workers, no chunk is refined in the parent; a planted defect reaches the workers; a failing
  worker fails the request.
- The boundary: every request without a count step is the routed module's answer, byte for byte; with one, only
  STEP_CONDITION_UNCERTAIN results differ, and the context binds this module and the rule.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest import mock

from . import coded_only_cases as co
from . import count_narrowing_cases as cn
from . import execution_store as es
from . import store_conditions as sc
from . import store_conditions_coded as cc
from . import store_conditions_counts as scn
from . import store_conditions_gene as gc
from . import store_conditions_relative as rc
from . import test_derived_steps as tds
from . import test_gene_steps as tgs
from . import test_relative_conditions as trc
from . import trajectory_graph as tg
from . import trajectory_graph_worlds as tw

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400_000_000
SMALL = 300  # small cases per generator in the suite; the decision log reports the ad hoc runs
SNAPSHOT = 8  # snapshot requests per fixture and profile in the suite


class SmallCaseTests(unittest.TestCase):
    """The refined result is sound and equal to the truth; only STEP_CONDITION_UNCERTAIN results move."""

    def test_no_claim_is_false_and_every_refined_result_is_the_truth(self):
        refined = 0
        for generator in cn.GENERATORS:
            tally, failures = cn.run_small(generator, range(SMALL), prefix='suite')
            self.assertEqual(failures, [], generator)
            self.assertGreater(tally['STEP_CONDITION_UNCERTAIN'], 0, generator)
            refined += tally['refined']
        self.assertGreater(refined, 50)

    def test_the_oracle_equals_enumeration(self):
        checked = 0
        for generator in ('span-multi', 'window-coupled', 'during-from-count', 'two-counts'):
            for seed in range(120):
                query, conditions, patient, mentions = cn.small_case(f'suite-oracle-{seed}', generator)
                if any(set(raw) != {'count'} for raw in conditions['steps'].values()):
                    continue  # an Nth step beside: the oracle does not cover it
                self.assertEqual(cn.truth(query, conditions, patient, mentions),
                                 cn.enum_truth(query, conditions, patient, mentions), (generator, seed))
                checked += 1
        self.assertGreater(checked, 300)

    def test_a_record_counted_twice_is_decided_possible(self):
        """The design study's commonest case: two records of one seizure overlap, so which is the third is uncertain;
        D32 leaves it undecided, the narrowed pass finds it possible (and no binding is exact)."""
        patient = {'patient_id': 'P', 'window': {'start': 0, 'end': 20}, 'events': [
            {'id': 'a', 'concepts': ['x'], 'start': [1, 1]},
            {'id': 'b', 'concepts': ['x'], 'start': [5, 9]},
            {'id': 'b2', 'concepts': ['x'], 'start': [4, 10]},  # the registry copy of b, wider
            {'id': 'd', 'concepts': ['d'], 'start': [11, 11]}]}
        query = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'A', 'concept': 'x'}, {'id': 'B', 'concept': 'd'}],
                 'relations': [{'id': 'R1', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 3}],
                 'relaxations': [], 'budget': 0}
        conditions = {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'A': {'count': {'at_least': 3, 'within': {'span': 10}}}}}
        v1, v2, how = cn.refine_case(query, conditions, patient, set())
        self.assertEqual((v1['status'], v1['reason']), ('unresolved', 'STEP_CONDITION_UNCERTAIN'))
        self.assertEqual((v2['status'], v2['method'], v2['complete']), ('possible', scn.METHOD, True), how)
        self.assertEqual(tw.check_derived(v2, query, conditions, patient, set()), [])
        self.assertEqual(cn.enum_truth(query, conditions, patient, set()), ('possible', None, None))
        self.assertEqual(set(v2['witness']), {'A', 'B'})

    def test_the_witness_leaves_room_for_the_counts_records(self):
        """The review's must-fix (M1): with B bound to e1 at 6, e0 at 5 does not reach 3 in the window, so a witness
        must place e1 at 5. Any time two' allows is not enough (the planted defect `any_witness`)."""
        patient = {'patient_id': 'P', 'window': {'start': 1, 'end': 6}, 'events': [
            {'id': 'e0', 'concepts': ['z'], 'start': [5, 9], 'end': [6, 6], 'proper': True},
            {'id': 'e1', 'concepts': ['z'], 'start': [5, 6]},
            {'id': 'e2', 'concepts': ['z'], 'start': [5, 7], 'end': [6, 8], 'proper': True},
            {'id': 'e3', 'concepts': ['x', 'y'], 'start': [2, 6]}]}
        query = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'z'}, {'id': 'B', 'concept': 'z'}],
                 'relations': [{'id': 'R1', 'kind': 'during', 'from': 'A', 'to': 'B', 'grace': 1}], 'relaxations': [],
                 'budget': 0}
        conditions = {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'A': {'count': {'at_least': 3, 'within': 'window'}}}}
        _, v2, _ = cn.refine_case(query, conditions, patient, set())
        self.assertEqual(v2['status'], 'possible')
        self.assertTrue(cn.witness_holds(v2, query, conditions, patient, set()), v2)
        scn._D43_DEFECT = 'any_witness'
        try:
            _, bad, _ = cn.refine_case(query, conditions, patient, set())
        finally:
            scn._D43_DEFECT = None
        self.assertFalse(cn.witness_holds(bad, query, conditions, patient, set()), bad)

    def test_narrow_is_the_set_of_times_the_count_can_be_reached(self):
        """Narrow(e) against a direct count over every integer time, span and period, on random families."""
        rng = random.Random('d43/narrow')
        for _ in range(400):
            k = rng.randint(1, 5)
            ranges = []
            for _ in range(k):
                lo = rng.randint(0, 8)
                ranges.append((lo, lo + rng.randint(0, 4)))
            family = [(lo, hi, lo, hi, None, None) for lo, hi in ranges]
            n = rng.randint(1, 4)
            window = {'start': rng.randint(0, 4), 'end': rng.randint(5, 12)}
            within = 'window' if rng.random() < 0.4 else rng.randint(0, 4)
            for j in range(k):
                got = {t for lo, hi in scn.narrow(family, j, n, within, window) for t in range(lo, hi + 1)}
                a, b = ranges[j]
                if within == 'window':
                    a, b = max(a, window['start']), min(b, window['end'])
                    able = lambda t, x: x[1] >= window['start'] and x[0] <= t
                else:
                    able = lambda t, x: x[0] <= t and x[1] >= t - within
                want = {t for t in range(a, b + 1) if sum(able(t, x) for i, x in enumerate(ranges) if i != j) >= n - 1}
                self.assertEqual(got, want, (ranges, j, n, within, window))


class PlantedDefectTests(unittest.TestCase):
    """Each planted defect makes some small case fail (seeds found once and pinned), or the differential."""

    PINNED = {'whole_range': ('span-multi', 2944), 'coupling_ignored': ('span-coupled', 948),
              'certainty_from_two': ('span-multi', 3), 'point_without_end': ('during-from-count', 61),
              'two_copies': ('two-counts', 53), 'decided_refined': ('span-multi', 9),
              'any_witness': ('span-multi', 103)}

    def tearDown(self):
        scn._D43_DEFECT = None

    def test_each_per_patient_defect_is_caught(self):
        for defect, (generator, seed) in self.PINNED.items():
            tally, _ = cn.run_small(generator, [seed], prefix='suite-defect')
            self.assertEqual(tally['failing'], 0, (defect, 'fails without the defect'))
            scn._D43_DEFECT = defect
            tally, _ = cn.run_small(generator, [seed], prefix='suite-defect')
            scn._D43_DEFECT = None
            self.assertEqual(tally['failing'], 1, defect)

    def test_a_bare_point_breaks_a_during_from_the_count_step(self):
        """A copy must keep its record's end: a `during` from the count step reads it."""
        scn._D43_DEFECT = 'point_without_end'
        query, conditions, patient, mentions = cn.small_case('suite-defect-61', 'during-from-count')
        self.assertTrue(any(r['kind'] == 'during' and r['from'] == 'A' for r in query['relations']))
        _, v2, _ = cn.refine_case(query, conditions, patient, mentions)
        scn._D43_DEFECT = None
        _, fixed, _ = cn.refine_case(query, conditions, patient, mentions)
        self.assertNotEqual(cn.enum_truth(query, conditions, patient, mentions),
                            (v2['status'], v2.get('relaxations'), v2.get('certainty')))
        self.assertEqual(tw.check_derived(fixed, query, conditions, patient, mentions), [])


_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='count-narrowing-')
    tmp = Path(_STATE['tmp'].name)
    for name, ingest, pool in (('fixture', tds.derived_ingest(31), cn.FIXTURE_POOL),
                               ('sparse', cn.sparse_coverage(tds.derived_ingest(33), 33), cn.FIXTURE_POOL),
                               ('gene', tgs.gene_ingest(51), cn.GENE_FIXTURE_POOL)):
        store = es.open_store(es.build(ingest, tmp / name), verify='content')
        without = es.open_store(es.build(co.without_kinds(ingest, co.KINDS), tmp / f'{name}-coded'), verify='content')
        _STATE[name] = {'ingest': ingest, 'store': store, 'without': without, 'pool': pool}


def tearDownModule():
    for name in ('fixture', 'sparse', 'gene'):
        if name in _STATE:
            _STATE[name]['store'].close()
            _STATE[name]['without'].close()
    es.close_pools()
    _STATE['tmp'].cleanup()


def differential(name, profile, seeds, variants=True):
    state = _STATE[name]
    bad, tally = [], Counter()
    for seed in seeds:
        query, conditions, windows = cn.snapshot_case(seed, name, state['ingest'], state['pool'], profile)
        wrong, t = cn.snapshot_compare(state['store'], state['ingest'], query, conditions, windows, variants=variants,
                                       without=state['without'] if profile == '1.3' else None)
        bad.extend((seed, w) for w in wrong)
        tally.update(t)
    return bad, tally


class SnapshotTests(unittest.TestCase):
    """The dispatcher against the reference on snapshots."""

    def tearDown(self):
        scn._D43_DEFECT = None

    def test_the_dispatcher_equals_the_reference(self):
        refined = Counter()
        for name, profiles in (('fixture', ('1.1', '1.2', '1.3')), ('sparse', ('1.1', '1.3')),
                               ('gene', ('gene', '1.3', 'gene-coded'))):
            for profile in profiles:
                bad, tally = differential(name, profile, range(SNAPSHOT))
                self.assertEqual(bad[:1], [], (name, profile))
                refined[profile] += tally['refined']
        self.assertTrue(all(refined[p] for p in ('1.1', '1.2', '1.3')), refined)

    def test_d18_and_coverage_not_reapplied_are_caught(self):
        for defect in ('no_d18', 'no_coverage'):
            scn._D43_DEFECT = defect
            bad, _ = differential('sparse', '1.1', range(30), variants=False)
            scn._D43_DEFECT = None
            self.assertTrue(bad, defect)

    def test_a_refined_none_meets_coverage_and_d18(self):
        """Over the sparse fixture, some refined results are then not comparable or unresolved by the store's later
        rules, as the reference reapplies them."""
        _, tally = differential('sparse', '1.1', range(30), variants=False)
        self.assertTrue(tally['refined not_comparable'] + tally['refined unresolved'], tally)


class PoolTests(unittest.TestCase):
    """D43b: the post-pass runs in the store's worker pool, with the in-process answer at any worker count and chunk."""

    def tearDown(self):
        scn._D43_DEFECT = None

    def _requests(self, name, profiles):
        state = _STATE[name]
        for profile in profiles:
            for seed in range(SNAPSHOT):
                query, conditions, windows = cn.snapshot_case(seed, name, state['ingest'], state['pool'], profile)
                yield state['store'], query, conditions, windows

    def test_the_pool_gives_the_in_process_answer_at_any_chunk(self):
        refined = 0
        for name, profiles in (('fixture', ('1.1', '1.2', '1.3')), ('gene', ('gene', 'gene-coded'))):
            for store, query, conditions, windows in self._requests(name, profiles):
                one = json.dumps(sc.match(store, query, windows, conditions=conditions))
                refined += json.loads(one)['derived_steps']['count_refinement']['refined']
                for size, chunk, workers in ((1, es.CHUNK_PATIENTS, 1), (10**6, es.CHUNK_PATIENTS, 1),
                                             (None, 9, 2), (1, es.CHUNK_PATIENTS, 2), (10**6, 5, 3), (4, 7, 2)):
                    cut = scn._chunk_size if size is None else (lambda targets, chunk, size=size: min(size, chunk))
                    with mock.patch.object(scn, '_chunk_size', cut):
                        got = sc.match(store, query, windows, conditions=conditions, chunk=chunk, workers=workers)
                    self.assertEqual(json.dumps(got), one, (name, conditions['profile'], size, chunk, workers))
        self.assertGreater(refined, 0)

    def test_with_workers_the_chunks_run_in_the_pool(self):
        """At 2 workers the parent refines no chunk itself: the units, one per chunk, go to the pool."""
        hits = 0
        for store, query, conditions, windows in self._requests('fixture', ('1.1', '1.3')):
            base = sc._module(conditions).match(store, query, windows, conditions=conditions)
            targets = sum(r['status'] == 'unresolved' and r.get('reason') == 'STEP_CONDITION_UNCERTAIN'
                          for r in base['results'])
            with mock.patch.object(scn, '_chunk_size', lambda targets, chunk: 3), \
                    mock.patch.object(scn, '_refine_chunk', wraps=scn._refine_chunk) as local, \
                    mock.patch.object(scn, '_run', wraps=scn._run) as pooled:
                sc.match(store, query, windows, conditions=conditions, workers=2)
            self.assertEqual(local.call_count, 0)
            if targets:
                hits += 1
                self.assertEqual(len(pooled.call_args.args[1]), -(-targets // 3))
            else:
                self.assertEqual(pooled.call_count, 0)
        self.assertGreater(hits, 0)

    def test_the_cut_depends_on_the_number_of_results_only(self):
        self.assertEqual([scn._chunk_size(n, es.CHUNK_PATIENTS) for n in (1, 50, 3_200, 3_201, 5_010, 27_000, 10**6)],
                         [50, 50, 50, 51, 79, 422, es.CHUNK_PATIENTS])
        self.assertEqual(scn._chunk_size(5_010, 9), 9)

    def test_a_planted_defect_reaches_the_workers(self):
        state, moved = _STATE['sparse'], 0
        for seed in range(30):
            query, conditions, windows = cn.snapshot_case(seed, 'sparse', state['ingest'], state['pool'], '1.1')
            clean = json.dumps(sc.match(state['store'], query, windows, conditions=conditions))
            scn._D43_DEFECT = 'no_d18'
            try:
                one = json.dumps(sc.match(state['store'], query, windows, conditions=conditions))
                two = json.dumps(sc.match(state['store'], query, windows, conditions=conditions, workers=2))
            finally:
                scn._D43_DEFECT = None
            self.assertEqual(two, one, seed)
            moved += one != clean
        self.assertGreater(moved, 0)

    def test_a_failing_worker_fails_the_request(self):
        state = _STATE['fixture']
        for seed in range(SNAPSHOT):
            query, conditions, windows = cn.snapshot_case(seed, 'fixture', state['ingest'], state['pool'], '1.1')
            out = es.match(state['store'], query, windows, conditions=conditions, workers=2)
            if any(r.get('reason') == 'STEP_CONDITION_UNCERTAIN' for r in out['results']):
                break
        else:
            self.fail('no request with a STEP_CONDITION_UNCERTAIN result')
        pool = es.get_pool(state['store'], 2)
        pool.fault = 'raise'
        try:
            with self.assertRaises(es.MatchWorkerError):
                scn.post_pass(state['store'], query, windows, es.CHUNK_PATIENTS, conditions, out, workers=2)
        finally:
            pool.fault = None


class BoundaryTests(unittest.TestCase):
    """The hook acts on count requests only, and moves only STEP_CONDITION_UNCERTAIN results."""

    def test_every_request_without_a_count_step_is_the_routed_modules_answer(self):
        state = _STATE['gene']
        store, ingest = state['store'], state['ingest']
        windows = cn.snapshot_windows(random.Random('d43/boundary'), ingest)
        q = {'profile': tg.PROFILE_ID, 'scope': 'any',
             'steps': [{'id': 'A', 'concept': 'c:febrile'}, {'id': 'B', 'concept': 'm:freq'},
                       {'id': 'C', 'concept': 'd:a', 'occurrence': 'first'}],
             'relations': [{'id': 'R1', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': -400 * DAY, 'max': 400 * DAY}],
             'relaxations': [], 'budget': '1'}
        gene_q = {**q, 'steps': q['steps'] + [{'id': 'G', 'concept': 'g:SCN1A'}],
                  'relations': q['relations'] + [{'id': 'R2', 'kind': 'after', 'from': 'A', 'to': 'G',
                                                  'min': -400 * DAY, 'max': 400 * DAY}]}
        gene = {'gene': {'report': 'first_not_negative', 'timed_by': 'recorded_at'}}
        for query, conditions, module in (
                (q, None, rc), (q, {'profile': es.CONDITIONS_PROFILE, 'steps': {'B': {'unit': '/month', 'ge': '2'}}}, rc),
                (q, {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'A': {'occurrence': {'nth': 2}}}}, rc),
                (q, {'profile': rc.CONDITIONS_PROFILE_1_2, 'steps': {'B': trc.rel(unit='/month', within=75 * DAY)}}, rc),
                (q, {'profile': cc.CONDITIONS_PROFILE_1_3, 'steps': {}, 'exclude_kinds': ['mention']}, cc),
                (gene_q, {'profile': gc.GENE_PROFILE, 'steps': {'G': gene}}, gc)):
            self.assertFalse(scn.has_counts(conditions))
            for workers in (1, 2):
                self.assertEqual(json.dumps(sc.match(store, query, windows, workers=workers, conditions=conditions)),
                                 json.dumps(module.match(store, query, windows, workers=workers, conditions=conditions)))
            self.assertEqual(sc.match_context(store, query, windows, conditions),
                             module.match_context(store, query, windows, conditions))

    def test_with_a_count_step_only_undecided_results_move(self):
        state = _STATE['fixture']
        moved = 0
        for profile, module in (('1.1', es), ('1.2', rc), ('1.3', cc)):
            for seed in range(SNAPSHOT):
                query, conditions, windows = cn.snapshot_case(seed, 'fixture', state['ingest'], state['pool'], profile)
                self.assertTrue(scn.has_counts(conditions))
                got = sc.match(state['store'], query, windows, conditions=conditions)
                base = module.match(state['store'], query, windows, conditions=conditions)
                for g, b in zip(got['results'], base['results'], strict=True):
                    if json.dumps(g) != json.dumps(b):
                        moved += 1
                        self.assertEqual((b['status'], b['reason']), ('unresolved', 'STEP_CONDITION_UNCERTAIN'))
                rest = lambda out: {k: v for k, v in out.items() if k not in ('results', 'counts', 'context', 'context_id',
                                                                              'derived_steps')}
                self.assertEqual(rest(got), rest(base))
                self.assertEqual(got['counts'], dict(sorted(Counter(r['status'] for r in got['results']).items())))
                tally = got['derived_steps']['count_refinement']
                self.assertEqual(got['derived_steps'], {**base['derived_steps'], 'count_refinement': tally})
                self.assertEqual((tally['rule'], tally['skipped_mismatch']), (scn.RULE, 0))
                self.assertEqual(tally['refined'], sum(json.dumps(g) != json.dumps(b)
                                                       for g, b in zip(got['results'], base['results'])))
                self.assertIn(scn.MODULE_PATH, got['context']['artifacts'])
                self.assertEqual(got['context']['count_rule'], es._digest(scn.RULE))
                self.assertEqual(sc.match_context(state['store'], query, windows, conditions),
                                 (got['context'], got['context_id']))
        self.assertGreater(moved, 0)

    def test_no_build_or_answering_module_imports_the_module(self):
        """D34: nothing a build or the answering modules load imports it; only the dispatcher does."""
        for path in ('patterns/execution_store.py', 'patterns/store_conditions_relative.py',
                     'patterns/store_conditions_coded.py', 'patterns/store_conditions_gene.py',
                     'patterns/synthetic_ingest.py', 'patterns/build_record.py', 'tools/scaling_build_worker.py'):
            self.assertNotIn('store_conditions_counts', (ROOT / path).read_text(encoding='utf-8'), path)
        self.assertIn('store_conditions_counts', (ROOT / 'patterns/store_conditions.py').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
