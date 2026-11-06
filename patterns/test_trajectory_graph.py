"""Trajectory-graph profile: semantics, decision methods, budgets and the exhaustive reference."""
from copy import deepcopy
import json
import random
import time
import unittest

from . import trajectory_graph as tg
from . import trajectory_graph_reference as ref


def query(steps, relations=(), **extra):
    return {'profile': tg.PROFILE_ID, 'steps': list(steps), 'relations': list(relations), **extra}


def patient(events, window=(0, 1000), pid='P1'):
    return {'patient_id': pid, 'window': None if window is None else {'start': window[0], 'end': window[1]},
            'events': list(events)}


def event(eid, concept, start, end=None, *more):
    out = {'id': eid, 'concepts': [concept, *more], 'start': list(start)}
    if end is not None:
        out['end'] = list(end)
    return out


SEQUENCE = query([{'id': 'A', 'concept': 'febrile_seizure', 'occurrence': 'first'},
                  {'id': 'B', 'concept': 'blocker'},
                  {'id': 'C', 'concept': 'rise'}],
                 [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 365},
                  {'id': 'BC', 'kind': 'after', 'from': 'B', 'to': 'C', 'min': 0, 'max': 60}])


class SemanticsTests(unittest.TestCase):
    def test_a_sequence_with_exact_times_is_an_exact_match(self):
        p = patient([event('a', 'febrile_seizure', (100, 100)), event('b', 'blocker', (200, 200)),
                     event('c', 'rise', (230, 230))])
        result = tg.match(SEQUENCE, p)
        self.assertEqual(result['status'], 'exact')
        self.assertEqual(result['binding'], {'A': 'a', 'B': 'b', 'C': 'c'})
        self.assertEqual(result['method'], 'forest_arc_consistency')

    def test_uncertain_times_that_only_some_choices_satisfy_are_possible(self):
        # The rise is known only to fall in [200, 300]; it is within 60 days of the blocker
        # for some times in that range and not for others.
        p = patient([event('a', 'febrile_seizure', (100, 100)), event('b', 'blocker', (200, 200)),
                     event('c', 'rise', (200, 300))])
        result = tg.match(SEQUENCE, p)
        self.assertEqual(result['status'], 'possible')
        self.assertEqual(result['method'], 'point_forest_intervals')
        self.assertLessEqual(result['witness']['C'] - result['witness']['B'], 60)

    def test_first_occurrence_binds_only_the_earliest_event(self):
        p = patient([event('a1', 'febrile_seizure', (10, 10)), event('a2', 'febrile_seizure', (150, 150)),
                     event('b', 'blocker', (500, 500)), event('c', 'rise', (520, 520))])
        # Only a2 is within a year of b, but A is the first febrile seizure.
        self.assertEqual(tg.match(SEQUENCE, p)['status'], 'none')
        anyq = deepcopy(SEQUENCE)
        anyq['steps'][0]['occurrence'] = 'any'
        self.assertEqual(tg.match(anyq, p)['binding']['A'], 'a2')

    def test_during_uses_the_exposure_end_and_its_grace(self):
        q = query([{'id': 'M', 'concept': 'blocker'}, {'id': 'R', 'concept': 'rise'}],
                  [{'id': 'MR', 'kind': 'during', 'from': 'M', 'to': 'R', 'grace': 10}])
        exposure = event('m', 'blocker', (100, 100), (200, 200))
        self.assertEqual(tg.match(q, patient([exposure, event('r', 'rise', (205, 205))]))['status'], 'exact')
        self.assertEqual(tg.match(q, patient([exposure, event('r', 'rise', (215, 215))]))['status'], 'none')
        ongoing = event('m', 'blocker', (100, 100), (150, None))
        # An ongoing exposure may still be running at 400, but need not be.
        self.assertEqual(tg.match(q, patient([ongoing, event('r', 'rise', (400, 400))]))['status'], 'possible')

    def test_scope_and_anchors_are_unary_constraints(self):
        q = query([{'id': 'A', 'concept': 'x'}],
                  [{'id': 'SA', 'kind': 'after', 'from': 'START', 'to': 'A', 'min': 0, 'max': 30}], scope='within')
        self.assertEqual(tg.match(q, patient([event('a', 'x', (110, 120))], (100, 200)))['status'], 'exact')
        self.assertEqual(tg.match(q, patient([event('a', 'x', (120, 140))], (100, 200)))['status'], 'possible')
        self.assertEqual(tg.match(q, patient([event('a', 'x', (90, 95))], (100, 200)))['status'], 'none')

    def test_no_index_window_excludes_the_patient(self):
        self.assertEqual(tg.match(SEQUENCE, patient([], None))['status'], 'excluded')

    def test_relaxation_is_cost_ordered_and_stops_at_the_first_match(self):
        q = deepcopy(SEQUENCE)
        q['relaxations'] = [
            {'id': 'wider-gap', 'kind': 'extend_max', 'cost': '0.25', 'relation': 'BC', 'max': 90},
            {'id': 'any-seizure', 'kind': 'widen_concept', 'cost': '0.5', 'step': 'A', 'concept': 'seizure'}]
        q['budget'] = 1
        p = patient([event('a', 'seizure', (100, 100)), event('b', 'blocker', (200, 200)),
                     event('c', 'rise', (280, 280))])
        result = tg.match(q, p)
        self.assertEqual((result['status'], result['relaxations'], result['cost']),
                         ('relaxed', ['wider-gap', 'any-seizure'], '0.75'))
        self.assertEqual(result['certainty'], 'exact')
        q['budget'] = '0.5'
        self.assertEqual(tg.match(q, p)['status'], 'none')


class ExampleTests(unittest.TestCase):
    def test_the_committed_example_shows_each_outcome(self):
        root = tg.Path(__file__).resolve().parents[1] / 'examples/trajectory-graph'
        q = json.loads((root / 'query.json').read_text())
        patients = json.loads((root / 'patients.json').read_text())
        results = {r['patient_id']: r for r in tg.run(q, patients)['results']}
        self.assertEqual({pid: r['status'] for pid, r in results.items()},
                         {'P1': 'exact', 'P2': 'possible', 'P3': 'relaxed', 'P4': 'none', 'P5': 'excluded'})
        self.assertEqual(results['P3']['relaxations'], ['A-as-any-seizure', '60-days-after-stopping'])
        self.assertEqual([ref.match(q, p)['status'] for p in patients], [results[p['patient_id']]['status'] for p in patients])


class DecisionMethodTests(unittest.TestCase):
    def test_a_large_tree_is_decided_without_search(self):
        """Six steps with 300 candidates each: 300^6 bindings, decided by propagation alone."""
        rng = random.Random(7)
        steps = [{'id': s, 'concept': s} for s in 'ABCDEF']
        relations = [{'id': f'{a}{b}', 'kind': 'after', 'from': a, 'to': b, 'min': 5, 'max': 40}
                     for a, b in ('AB', 'AC', 'BD', 'BE', 'CF')]
        events = [event(f'{s}{i}', s, (t, t + rng.randint(0, 6)))
                  for s in 'ABCDEF' for i, t in enumerate(sorted(rng.sample(range(0, 20000), 300)))]
        q, p = query(steps, relations, search_budget=1), patient(events, (0, 30000))
        started = time.perf_counter()
        result = tg.match(q, p)
        self.assertLess(time.perf_counter() - started, 20)
        self.assertIn(result['status'], ('exact', 'possible'))
        self.assertIn(result['method'], ('forest_arc_consistency', 'point_forest_intervals'))
        self.assertTrue(result['complete'])
        self.assertTrue(ref.binding_reaches(q, p, (), result['binding'], result['status']))

    def test_a_cycle_falls_back_to_bounded_search(self):
        q = query([{'id': s, 'concept': 'x'} for s in 'ABC'],
                  [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 1, 'max': 5},
                   {'id': 'BC', 'kind': 'after', 'from': 'B', 'to': 'C', 'min': 1, 'max': 5},
                   {'id': 'AC', 'kind': 'after', 'from': 'A', 'to': 'C', 'min': 1, 'max': 10}])
        p = patient([event(f'e{i}', 'x', (i * 3, i * 3)) for i in range(6)])
        result = tg.match(q, p)
        self.assertEqual((result['status'], result['method']), ('exact', 'bounded_backtracking'))

    def test_an_exhausted_budget_is_unresolved_never_a_non_match(self):
        # A cycle with no solution: B and C must each be 1-2 after A and 10 apart. With exact
        # times arc consistency proves that alone; with wide uncertain ranges every pair is
        # plausible, so only the joint test can, binding by binding.
        q = query([{'id': s, 'concept': 'x'} for s in 'ABC'],
                  [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 1, 'max': 2},
                   {'id': 'AC', 'kind': 'after', 'from': 'A', 'to': 'C', 'min': 1, 'max': 2},
                   {'id': 'BC', 'kind': 'after', 'from': 'B', 'to': 'C', 'min': 10, 'max': 10}],
                  search_budget=50,
                  relaxations=[{'id': 'loose', 'kind': 'extend_max', 'cost': 1, 'relation': 'AC', 'max': 20}], budget=1)
        p = patient([event(f'e{i}', 'x', (i, i + 500)) for i in range(15)])
        result = tg.match(q, p)
        self.assertEqual((result['status'], result['reason']), ('unresolved', 'SEARCH_BUDGET_EXHAUSTED'))
        self.assertFalse(result['complete'])
        self.assertEqual(result['relaxed_match']['relaxations'], ['loose'])
        q['search_budget'] = 1_000_000
        self.assertEqual(tg.match(q, p)['status'], 'relaxed')
        q['relaxations'] = []
        self.assertEqual(tg.match(q, p), {'patient_id': 'P1', 'status': 'none', 'complete': True})

    def test_arc_consistency_alone_refutes_a_contradictory_cycle_on_exact_times(self):
        q = query([{'id': s, 'concept': 'x'} for s in 'ABC'],
                  [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 1, 'max': 2},
                   {'id': 'AC', 'kind': 'after', 'from': 'A', 'to': 'C', 'min': 1, 'max': 2},
                   {'id': 'BC', 'kind': 'after', 'from': 'B', 'to': 'C', 'min': 10, 'max': 10}], search_budget=1)
        p = patient([event(f'e{i}', 'x', (i, i)) for i in range(40)])
        self.assertEqual(tg.match(q, p), {'patient_id': 'P1', 'status': 'none', 'complete': True})
        decision = tg.decide(q, tg.events_of(p), p['window'], (), 1)
        self.assertEqual(decision['possible']['method'], 'arc_consistency')


class ValidationTests(unittest.TestCase):
    def test_queries_outside_the_profile_are_refused_by_name(self):
        cases = [
            ('UNSUPPORTED_PROFILE', {**SEQUENCE, 'profile': 'other'}),
            ('DUPLICATE_STEP', query([{'id': 'A', 'concept': 'x'}, {'id': 'A', 'concept': 'y'}])),
            ('UNKNOWN_RELATION_STEP', query([{'id': 'A', 'concept': 'x'}],
                                            [{'id': 'r', 'kind': 'after', 'from': 'A', 'to': 'Z'}])),
            # An exposure has to be a step: an anchor has no end.
            ('UNKNOWN_RELATION_STEP', query([{'id': 'A', 'concept': 'x'}],
                                            [{'id': 'r', 'kind': 'during', 'from': 'START', 'to': 'A'}])),
            ('INVALID_BOUNDS', query([{'id': 'A', 'concept': 'x'}, {'id': 'B', 'concept': 'y'}],
                                     [{'id': 'r', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 5, 'max': 1}])),
            ('RELAXATION_MUST_WIDEN', {**SEQUENCE, 'relaxations': [
                {'id': 'x', 'kind': 'extend_max', 'cost': 1, 'relation': 'BC', 'max': 10}]}),
        ]
        for code, q in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(tg.TrajectoryError, code):
                    tg.validate_query(q)

    def test_patient_records_outside_the_profile_are_refused_by_name(self):
        for code, p in {'INVALID_EVENT_START': patient([event('a', 'x', (5, 1))]),
                        'INVALID_EVENT_END': patient([event('a', 'x', (5, 6), (1, 2))]),
                        'DUPLICATE_EVENT': patient([event('a', 'x', (1, 1)), event('a', 'y', (2, 2))]),
                        'INVALID_WINDOW': {'patient_id': 'P', 'window': {'start': 5, 'end': 1}, 'events': []}}.items():
            with self.subTest(code=code):
                with self.assertRaisesRegex(tg.TrajectoryError, code):
                    tg.validate_patient(p)


def random_case(rng, shape, intervals):
    n = rng.randint(2, 4)
    names = 'ABCD'[:n]
    concepts = ['x', 'y']
    steps = [{'id': s, 'concept': rng.choice(concepts), 'occurrence': rng.choice(['any', 'any', 'first', 'last'])}
             for s in names]
    pairs = [(names[i - 1], names[i]) for i in range(1, n)] if shape == 'chain' else \
            [(names[rng.randrange(i)], names[i]) for i in range(1, n)]
    if shape == 'graph' and n >= 3:
        pairs.append((names[0], names[-1]))
    relations = []
    for k, (a, b) in enumerate(pairs):
        if intervals and rng.random() < 0.4:
            relations.append({'id': f'r{k}', 'kind': 'during', 'from': a, 'to': b, 'grace': rng.choice([0, 5, 15])})
        else:
            lo = rng.choice([None, -5, 0, 3])
            hi = rng.choice([None, 10, 25]) if lo is None or lo <= 10 else None
            relations.append({'id': f'r{k}', 'kind': 'after', 'from': a, 'to': b,
                              **({} if lo is None else {'min': lo}), **({} if hi is None else {'max': hi})})
    if rng.random() < 0.3:
        relations.append({'id': 'anchor', 'kind': 'after', 'from': 'START', 'to': names[0], 'min': 0, 'max': 40})
    relaxations = []
    if rng.random() < 0.6:
        relaxations.append({'id': 'widen', 'kind': 'widen_concept', 'cost': '0.5', 'step': rng.choice(names),
                            'concept': 'any'})
    afters = [r for r in relations if r['kind'] == 'after' and r.get('max') is not None]
    if afters and rng.random() < 0.6:
        r = rng.choice(afters)
        relaxations.append({'id': 'longer', 'kind': 'extend_max', 'cost': '0.25', 'relation': r['id'], 'max': r['max'] + 15})
    q = query(steps, relations, scope=rng.choice(['any', 'any', 'from_start', 'within']),
              relaxations=relaxations, budget=1, search_budget=10_000_000)
    events = []
    for i in range(rng.randint(3, 9)):
        start = rng.randint(0, 80)
        width = rng.choice([0, 0, 2, 6])
        e = event(f'e{i}', rng.choice(concepts), (start, start + width), None, 'any')
        if intervals and rng.random() < 0.4:
            end = start + width + rng.randint(0, 20)
            e['end'] = [end, rng.choice([end, end + 5, None])]
        events.append(e)
    return q, patient(events, (10, 70))


class DifferentialTests(unittest.TestCase):
    """The engine agrees with exhaustive enumeration, and every binding it reports really has
    the certainty it claims."""

    def check(self, shape, intervals, seeds):
        for seed in seeds:
            rng = random.Random(f'{shape}-{intervals}-{seed}')
            q, p = random_case(rng, shape, intervals)
            with self.subTest(shape=shape, intervals=intervals, seed=seed):
                engine, oracle = tg.match(q, p), ref.match(q, p)
                self.assertEqual(engine['status'], oracle['status'], (q, p))
                if engine['status'] == 'relaxed':
                    self.assertEqual((engine['certainty'], engine['relaxations'], engine['cost']),
                                     (oracle['certainty'], oracle['relaxations'], oracle['cost']))
                if engine['status'] in ('exact', 'possible', 'relaxed'):
                    chosen = [x for x in q['relaxations'] if x['id'] in engine.get('relaxations', [])]
                    certainty = engine.get('certainty', engine['status'])
                    self.assertTrue(ref.binding_reaches(q, p, chosen, engine['binding'], certainty))
                self.assertTrue(engine.get('complete', True))

    def test_chains_of_point_events(self):
        self.check('chain', False, range(300))

    def test_trees_of_point_events(self):
        self.check('tree', False, range(300))

    def test_graphs_with_a_cycle(self):
        self.check('graph', False, range(300))

    def test_trees_with_exposures_and_during(self):
        self.check('tree', True, range(300))

    def test_graphs_with_exposures_and_during(self):
        self.check('graph', True, range(300))


if __name__ == '__main__':
    unittest.main()
