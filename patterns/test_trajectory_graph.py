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


class SharedTimeTests(unittest.TestCase):
    """One event bound to two steps, or two events naming one variable, has one time."""
    TWICE = [{'id': 'A', 'concept': 'x'}, {'id': 'B', 'concept': 'x'}]

    def test_one_event_cannot_take_two_times_for_two_steps(self):
        # The only x is known to fall in [0, 100]. Taking it at 0 for A and at 100 for B
        # would satisfy the gap, but it happened once: B - A is 0, never 5 or more.
        q = query(self.TWICE, [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 5}])
        for e in (event('e', 'x', (0, 100)), event('e', 'x', (0, 100), (100, 200))):
            with self.subTest(point='end' not in e):
                self.assertEqual(tg.match(q, patient([e])), {'patient_id': 'P1', 'status': 'none', 'complete': True})
                self.assertIsNone(ref.classify(q, patient([e])))
        # With a second x the steps can take one each.
        result = tg.match(q, patient([event('e', 'x', (0, 100)), event('f', 'x', (50, 60))]))
        self.assertEqual((result['status'], result['binding']), ('possible', {'A': 'e', 'B': 'f'}))

    def test_terms_on_one_variable_differ_by_exactly_zero(self):
        # B starts 0 to 0 days after A: always true of one event, however uncertain its time.
        q = query(self.TWICE, [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 0}])
        self.assertEqual(tg.match(q, patient([event('e', 'x', (0, 100))]))['status'], 'exact')
        # An exposure ends at or after it starts, so a step during itself always holds.
        q = query(self.TWICE, [{'id': 'AB', 'kind': 'during', 'from': 'A', 'to': 'B'}])
        self.assertEqual(tg.match(q, patient([event('e', 'x', (0, 50), (10, 80))]))['status'], 'exact')

    def test_events_that_name_one_variable_share_its_time(self):
        q = query([{'id': 'A', 'concept': 'x'}, {'id': 'B', 'concept': 'y'}],
                  [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 0}])
        a, b = event('a', 'x', (0, 100)), event('b', 'y', (40, 60))
        self.assertEqual(tg.match(q, patient([a, b]))['status'], 'possible')
        shared = [{**a, 'start_var': 'onset'}, {**b, 'start_var': 'onset'}]
        self.assertEqual(tg.match(q, patient(shared))['status'], 'exact')
        q['relations'][0].update(min=5, max=None)
        del q['relations'][0]['max']
        self.assertEqual(tg.match(q, patient([a, b]))['status'], 'possible')
        self.assertEqual(tg.match(q, patient(shared))['status'], 'none')

    def test_a_shared_variable_lies_in_every_range_that_names_it(self):
        # a alone could be in the first 30 days; b says their shared time is 40 to 60.
        q = query([{'id': 'A', 'concept': 'x'}], [{'id': 'SA', 'kind': 'after', 'from': 'START', 'to': 'A', 'max': 30}])
        a, b = event('a', 'x', (0, 100)), event('b', 'y', (40, 60))
        self.assertEqual(tg.match(q, patient([a, b]))['status'], 'possible')
        self.assertEqual(tg.match(q, patient([{**a, 'start_var': 'v'}, {**b, 'start_var': 'v'}]))['status'], 'none')

    def test_an_exposure_orders_the_variables_it_names(self):
        # p and r are a dose's start and stop, recorded as points; the exposure m names the
        # same two times, so r is never before p, even if no step is bound to m.
        q = query([{'id': 'P', 'concept': 'x'}, {'id': 'R', 'concept': 'y'}],
                  [{'id': 'PR', 'kind': 'after', 'from': 'P', 'to': 'R', 'min': 0}])
        p, r = event('p', 'x', (0, 50)), event('r', 'y', (10, 60))
        m = {**event('m', 'z', (0, 50), (10, 60)), 'start_var': 'on', 'end_var': 'off'}
        self.assertEqual(tg.match(q, patient([p, r, m]))['status'], 'possible')
        linked = [{**p, 'start_var': 'on'}, {**r, 'start_var': 'off'}, m]
        self.assertEqual(tg.match(q, patient(linked))['status'], 'exact')
        self.assertEqual(ref.classify(q, patient(linked)), 'exact')

    def test_a_forest_binding_that_shares_a_variable_is_checked_jointly(self):
        # The interval programme over a forest of point steps takes each step's time
        # independently, and arc consistency relates only adjacent steps. A and B, either side
        # of C, both bind e, at 40 and at 100, which e cannot be; the joint check refuses
        # that, and search finds f for A. Without f nothing matches.
        q = query([{'id': 'A', 'concept': 'x'}, {'id': 'C', 'concept': 'y'}, {'id': 'B', 'concept': 'x'}],
                  [{'id': 'AC', 'kind': 'after', 'from': 'A', 'to': 'C', 'min': 10},
                   {'id': 'CB', 'kind': 'after', 'from': 'C', 'to': 'B', 'min': 10}])
        e, f, g = event('e', 'x', (0, 100)), event('f', 'x', (0, 10)), event('g', 'y', (50, 50))
        p = patient([e, f, g])
        decision = tg.decide(q, tg.Times(tg.events_of(p), p['window']), (), 10_000)['possible']
        self.assertEqual((decision['method'], tg._binding_ids(decision['binding'])),
                         ('bounded_backtracking', {'A': 'f', 'B': 'e', 'C': 'g'}))
        self.assertEqual(tg.match(q, patient([e, g]))['status'], 'none')
        # Here the programme's binding e, e, f is consistent, and the check confirms it.
        q = query([{'id': s, 'concept': 'x'} for s in 'ABC'],
                  [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 0},
                   {'id': 'BC', 'kind': 'after', 'from': 'B', 'to': 'C', 'min': 20, 'max': 30}])
        p = patient([event('e', 'x', (0, 100)), event('f', 'x', (95, 100))])
        result = tg.match(q, p)
        self.assertEqual((result['status'], result['method'], result['binding']),
                         ('possible', 'point_forest_intervals_checked', {'A': 'e', 'B': 'e', 'C': 'f'}))
        self.assertEqual(result['witness']['A'], result['witness']['B'])
        self.assertTrue(20 <= result['witness']['C'] - result['witness']['B'] <= 30)
        self.assertTrue(ref.binding_reaches(q, p, (), result['binding'], 'possible'))


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
        decision = tg.decide(q, tg.Times(tg.events_of(p), p['window']), (), 1)
        self.assertEqual(decision['possible']['method'], 'arc_consistency')

    def test_two_relations_that_leave_a_pair_no_gap_are_no_match(self):
        # C during the point A (so 0 to 0 after it) and at least 3 days after it: each is
        # plausible alone, together impossible. The interval programme used to crash here.
        q = query([{'id': 'A', 'concept': 'x'}, {'id': 'C', 'concept': 'y'}],
                  [{'id': 'd', 'kind': 'during', 'from': 'A', 'to': 'C', 'grace': 0},
                   {'id': 'a', 'kind': 'after', 'from': 'A', 'to': 'C', 'min': 3}])
        p = patient([event('e', 'x', (0, 50)), event('f', 'y', (0, 50))])
        self.assertEqual(tg.match(q, p), {'patient_id': 'P1', 'status': 'none', 'complete': True})
        self.assertIsNone(ref.classify(q, p))


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

    def test_time_variables_are_named_by_strings_and_must_admit_a_time(self):
        exposure = event('m', 'x', (0, 10), (5, 20))
        cases = [
            ('INVALID_EVENT_VARIABLE', [{**event('a', 'x', (1, 1)), 'start_var': 3}]),
            ('INVALID_EVENT_VARIABLE', [{**exposure, 'end_var': ''}]),
            # A point's end is its start: it has no end variable.
            ('INVALID_EVENT_VARIABLE', [{**event('a', 'x', (1, 1)), 'end_var': 'v'}]),
            # The ranges naming one variable do not meet.
            ('EMPTY_SHARED_VARIABLE', [{**event('a', 'x', (0, 5)), 'start_var': 'v'},
                                       {**event('b', 'x', (6, 9)), 'start_var': 'v'}]),
            # They meet, but each exposure ends after the other starts: on is off, and the
            # ranges of on (12 to 20) and off (5 to 10) do not meet.
            ('EMPTY_SHARED_VARIABLE', [{**event('a', 'x', (0, 20), (5, 10)), 'start_var': 'on', 'end_var': 'off'},
                                       {**event('b', 'x', (5, 10), (12, 20)), 'start_var': 'off', 'end_var': 'on'}]),
        ]
        for code, events in cases:
            with self.subTest(code=code, events=events):
                with self.assertRaisesRegex(tg.TrajectoryError, code):
                    tg.validate_patient(patient(events))
        # Defaults are names too: naming another event's default shares its time.
        tg.validate_patient(patient([exposure, {**event('b', 'x', (6, 9)), 'start_var': 'm.f'}]))
        times = tg.Times(tg.events_of(patient([exposure, {**event('b', 'x', (6, 9)), 'start_var': 'm.f'}])), None)
        self.assertEqual((times.dom['m.f'], times.dom['m.s'], times.gap('m.s', 'm.f')), ((6, 9), (0, 9), 9))

    def test_linked_variables_are_capped_before_any_is_solved(self):
        # Back-to-back exposures, each starting when the one before ends, link every
        # variable. The patient's network is solved in time cubic in that component, so
        # components built by sharing may together cost no more than one of the limit.
        def chain(n, tag='v'):
            return [{**event(f'{tag}{i}', 'x', (10 * i, 10 * i + 15), (10 * i + 10, 10 * i + 25)),
                     'start_var': f'{tag}{i}', 'end_var': f'{tag}{i + 1}'} for i in range(n - 1)]
        limit = tg.MAX_LINKED_VARIABLES
        started = time.perf_counter()
        tg.validate_patient(patient(chain(limit)))
        self.assertLess(time.perf_counter() - started, 10)
        for events in (chain(limit + 1), chain(limit, 'v') + chain(limit // 2, 'w')):
            with self.assertRaisesRegex(tg.TrajectoryError, 'SHARED_VARIABLE_COMPONENT_LIMIT'):
                tg.validate_patient(patient(events))
        # Unshared exposures link only their own two variables and are not counted.
        tg.validate_patient(patient([event(f'e{i}', 'x', (i, i + 1), (i + 2, i + 3)) for i in range(2 * limit)]))


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
    if rng.random() < 0.25:
        # A second relation on a pair already related, either way round; together the two
        # may leave the pair no gap at all.
        a, b = rng.choice(pairs)
        if rng.random() < 0.5:
            a, b = b, a
        if rng.random() < 0.5:
            relations.append({'id': 'twin', 'kind': 'during', 'from': a, 'to': b, 'grace': rng.choice([0, 0, 4])})
        else:
            lo = rng.choice([None, -10, 0, 3, 12])
            hi = rng.choice([None, *[v for v in (0, 5, 20) if lo is None or v >= lo]])
            relations.append({'id': 'twin', 'kind': 'after', 'from': a, 'to': b,
                              **({} if lo is None else {'min': lo}), **({} if hi is None else {'max': hi})})
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
    if rng.random() < 0.4:
        share_variables(rng, events)
    return q, patient(events, (10, 70))


def share_variables(rng, events):
    """Let some events name another's variable: the same time recorded twice, perhaps with a
    different range, or an event that starts or ends when an exposure does. A sharing the
    profile refuses (no time fits every range) is undone."""
    for _ in range(rng.randint(1, 3)):
        a, b = rng.sample(events, 2)
        before = dict(b)
        ends = [p for p in ('end',) if 'end' in a and a['end'][1] is not None]
        part = rng.choice(['start', 'start', *ends])
        name = a.get(f'{part}_var', f"{a['id']}.{'s' if part == 'start' else 'f'}")
        lo, hi = a[part]
        lo, hi = lo + rng.choice([0, 0, 1, 4]), hi + rng.choice([0, 0, 3, -2])
        mine = rng.choice(['start', 'end']) if 'end' in b else 'start'
        b[f'{mine}_var'] = name
        if mine == 'start':
            b['start'] = [lo, max(lo, hi)]
            if 'end' in b and b['end'][0] < lo:
                b['end'] = [lo, None if b['end'][1] is None else max(lo, b['end'][1])]
        else:
            b['end'] = [max(lo, b['start'][0]), max(lo, hi, b['start'][0])]
        try:
            tg.validate_patient(patient(events))
        except tg.TrajectoryError:
            b.clear()
            b.update(before)


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


class PreparedQueryTests(unittest.TestCase):
    """A prepared query, reused across patients, decides as a fresh `match` does; `trusted` changes
    only what is checked, never what is decided."""

    def variants(self, p):
        """The case's patient and two relatives, to run one prepared query over several patients."""
        shift = lambda pair, d: [None if t is None else t + d for t in pair]
        moved = {**p, 'events': [{**e, 'start': shift(e['start'], 3), **({'end': shift(e['end'], 3)} if 'end' in e else {})}
                                 for e in p['events']]}
        return [p, moved, {**p, 'events': list(reversed(p['events']))}]

    def test_one_prepared_query_equals_fresh_matches_on_the_differential_cases(self):
        for shape, intervals in (('chain', False), ('tree', False), ('graph', False), ('tree', True), ('graph', True)):
            for seed in range(150):
                rng = random.Random(f'{shape}-{intervals}-{seed}')
                q, p = random_case(rng, shape, intervals)
                pq = tg.PreparedQuery(q)
                for patient_ in self.variants(p):
                    with self.subTest(shape=shape, intervals=intervals, seed=seed):
                        want = tg.match(q, patient_)
                        self.assertEqual(json.dumps(tg.match_prepared(pq, patient_), sort_keys=True),
                                         json.dumps(want, sort_keys=True))
                        self.assertEqual(tg.match_prepared(pq, patient_, trusted=True), want)

    def test_the_plan_of_each_relaxation_set_is_built_once_and_not_shared(self):
        q = deepcopy(SEQUENCE)
        q['relations'][0]['max'] = 10
        q['relaxations'] = [{'id': 'x', 'kind': 'extend_max', 'relation': 'AB', 'max': 400, 'cost': '0.5'}]
        q['budget'] = '1'
        pq = tg.PreparedQuery(q)
        self.assertEqual([tuple(x['id'] for x in chosen) for _, chosen in pq.sets], [('x',)])
        self.assertIs(pq.plan(), pq.plan())
        self.assertIs(pq.plan(pq.sets[0][1]), pq.plan(pq.sets[0][1]))
        self.assertIsNot(pq.plan(), pq.plan(pq.sets[0][1]))
        p = patient([event('a', 'febrile_seizure', (100, 100)), event('b', 'blocker', (200, 200)),
                     event('c', 'rise', (230, 230))])
        first = tg.match_prepared(pq, p)
        self.assertEqual(first['status'], 'relaxed')
        self.assertEqual(tg.match_prepared(pq, p), first)  # the original relation was not widened in place
        self.assertEqual(q['relations'][0]['max'], 10)

    def test_a_query_is_refused_when_it_is_prepared_and_a_patient_when_it_is_matched(self):
        with self.assertRaisesRegex(tg.TrajectoryError, 'NO_STEPS'):
            tg.PreparedQuery({'profile': tg.PROFILE_ID, 'steps': []})
        pq = tg.PreparedQuery(SEQUENCE)
        bad = patient([event('a', 'febrile_seizure', (100, 90))])
        with self.assertRaisesRegex(tg.TrajectoryError, 'INVALID_EVENT_START'):
            tg.match_prepared(pq, bad)
        # `trusted` skips the record's checks (the store validated it) but not the time network's.
        clash = patient([{**event('a', 'febrile_seizure', (100, 110)), 'start_var': 'v'},
                         {**event('b', 'blocker', (200, 210)), 'start_var': 'v'}])
        with self.assertRaisesRegex(tg.TrajectoryError, 'EMPTY_SHARED_VARIABLE'):
            tg.match_prepared(pq, clash, trusted=True)


if __name__ == '__main__':
    unittest.main()
