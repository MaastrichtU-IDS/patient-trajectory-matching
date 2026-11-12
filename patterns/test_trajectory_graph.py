"""Trajectory-graph profile: semantics, decision methods, budgets and the exhaustive reference."""
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import time
import unittest

from . import trajectory_graph as tg
from . import trajectory_graph_reference as ref
from . import trajectory_graph_worlds as worlds


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


def proper_case(rng, shape):
    """A differential case with proper exposures: some may otherwise end as they start, events often
    start when an exposure ends (between events two related steps can bind), and `after` bounds sit
    near zero and `during` graces at zero, where whether an end may equal its start decides."""
    q, p = random_case(rng, shape, True)
    events = p['events']
    for e in events:
        if 'end' in e and rng.random() < 0.7:
            before = list(e['end'])
            if rng.random() < 0.6:
                e['end'] = [e['start'][0], e['start'][1] + rng.choice([1, 1, 2, 5])]
            e['proper'] = True
            try:
                tg.validate_patient(p)
            except tg.TrajectoryError:  # an exposure that already shares its end: left as it was
                del e['proper']
                e['end'] = before
    concept = {s['id']: s['concept'] for s in q['steps']}
    related = [(r['from'], r['to']) for r in q['relations'] if r['from'] in concept and r['to'] in concept]
    for _ in range(rng.randint(1, 4)):
        x, y = rng.choice(related) if related and rng.random() < 0.7 else (None, None)
        exposures = [e for e in events if 'end' in e and e['end'][1] is not None
                     and (x is None or concept[x] in e['concepts'])]
        others = [e for e in events if (y is None or concept[y] in e['concepts'])]
        if not exposures:
            continue
        a = rng.choice(exposures)
        others = [e for e in others if e is not a]
        if not others:
            continue
        b = rng.choice(others)
        before = dict(b)
        b['start_var'], b['start'] = a.get('end_var', a['id'] + '.f'), list(a['end'])
        if 'end' in b:
            b['end'] = [max(b['end'][0], b['start'][0]),
                        None if b['end'][1] is None else max(b['end'][1], b['start'][1] + 1)]
        try:
            tg.validate_patient(p)
        except tg.TrajectoryError:
            b.clear()
            b.update(before)
    if rng.random() < 0.3:
        share_variables(rng, events)
    for r in q['relations']:
        if r['kind'] == 'after' and r['from'] not in tg.ANCHORS and r['to'] not in tg.ANCHORS and rng.random() < 0.6:
            r.pop('min', None), r.pop('max', None)
            lo = rng.choice([None, 0, 1, 1, 2])
            hi = rng.choice([None, 0, 1, 3]) if lo is None else rng.choice([None, lo, lo + 2])
            r.update({} if lo is None else {'min': lo}, **({} if hi is None else {'max': hi}))
        elif r['kind'] == 'during' and rng.random() < 0.5:
            r['grace'] = 0
    for x in list(q['relaxations']):
        if x['kind'] == 'extend_max':
            r = next(r for r in q['relations'] if r['id'] == x['relation'])
            if r.get('max') is None:
                q['relaxations'].remove(x)
            else:
                x['max'] = max(x['max'], r['max'])
    tg.validate_query(q)
    return q, p


def without_proper(p):
    return {**p, 'events': [{k: v for k, v in e.items() if k != 'proper'} for e in p['events']]}


def differential_view(r, reference=False):
    """What the engine and the reference both report (the reference has no search budget and no bindings)."""
    status = r['status']
    if reference:
        if status == 'relaxed':
            return status, (r['certainty'], r['relaxations'], r['cost']), not r['passed']
        if status == 'none':
            return status, None, not r['passed']
        if status == 'unresolved':
            beside = r.get('unverified') or (r['relaxed'] and (r['relaxed']['certainty'], r['relaxed']['relaxations'],
                                                                r['relaxed']['cost']) if 'relaxed' in r else None)
            return status, r['reason'], beside
        return status, None, True
    if status == 'relaxed':
        return status, (r['certainty'], r['relaxations'], r['cost']), r['complete']
    if status == 'none':
        return status, None, r['complete']
    if status == 'unresolved':
        if 'unverified_match' in r:
            beside = r['unverified_match']['certainty']
        elif 'relaxed_match' in r:
            m = r['relaxed_match']
            beside = (m['certainty'], m['relaxations'], m['cost'])
        else:
            beside = None
        return status, r['reason'], beside
    return status, None, r.get('complete', True)


def reported_bindings(r):
    """(certainty, relaxation ids, binding) for every binding a result reports, beside its status or as it."""
    if r['status'] in ('exact', 'possible'):
        yield r['status'], [], r['binding']
    if r['status'] == 'relaxed':
        yield r['certainty'], r['relaxations'], r['binding']
    if 'relaxed_match' in r:
        yield r['relaxed_match']['certainty'], r['relaxed_match']['relaxations'], r['relaxed_match']['binding']
    if 'unverified_match' in r:
        yield r['unverified_match']['certainty'], [], r['unverified_match']['binding']


class DifferentialTests(unittest.TestCase):
    """The engine agrees with exhaustive enumeration, and every binding it reports really has
    the certainty it claims."""

    def check(self, shape, intervals, seeds, make=None):
        for seed in seeds:
            rng = random.Random(f'{shape}-{intervals}-{seed}' if make is None else f'proper-{shape}-{seed}')
            q, p = random_case(rng, shape, intervals) if make is None else make(rng, shape)
            with self.subTest(shape=shape, intervals=intervals, seed=seed):
                engine, oracle = tg.match(q, p), ref.match(q, p)
                self.assertEqual(engine['status'], oracle['status'], (q, p))
                self.assertEqual(differential_view(engine), differential_view(oracle, reference=True), (q, p))
                for certainty, relaxations, binding in reported_bindings(engine):
                    chosen = [x for x in q['relaxations'] if x['id'] in relaxations]
                    self.assertTrue(ref.binding_reaches(q, p, chosen, binding, certainty))

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

    def test_proper_exposures_with_shared_variables(self):
        for shape in ('chain', 'tree', 'graph'):
            self.check(shape, True, range(300), make=proper_case)


class ProperIntervalTests(unittest.TestCase):
    """`proper: true` makes an exposure end strictly after it starts: start - end <= -1 in `Times`."""

    def chain(self, proper=True, x_end=(1, 6)):
        # Y starts when X ends.
        x = {**event('X', 'x', (1, 5), x_end), **({'proper': True} if proper else {})}
        return patient([x, {**event('Y', 'y', (0, 10)), 'start_var': 'X.f'}])

    def test_an_event_that_starts_when_a_proper_exposure_ends_starts_strictly_later(self):
        q = lambda lo, hi=None: query([{'id': 'a', 'concept': 'x'}, {'id': 'b', 'concept': 'y'}],
                                      [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': lo,
                                        **({} if hi is None else {'max': hi})}])
        # Equal starts need X to end as it starts: possible without `proper`, none with it.
        self.assertEqual(tg.match(q(0, 0), self.chain(False))['status'], 'possible')
        self.assertEqual(tg.match(q(0, 0), self.chain())['status'], 'none')
        # At least one step later holds in every timeline: possible without `proper`, exact with it.
        self.assertEqual(tg.match(q(1), self.chain(False))['status'], 'possible')
        self.assertEqual(tg.match(q(1), self.chain())['status'], 'exact')
        for case in (q(0, 0), q(1), q(1, 3), q(0)):
            for p in (self.chain(), self.chain(False)):
                self.assertEqual(tg.match(case, p)['status'], ref.match(case, p)['status'], (case, p))

    def test_strictness_tightens_a_lone_exposure_and_links_only_its_own_variables(self):
        times = tg.Times(tg.events_of(patient([{**event('m', 'x', (5, 9), (5, 9)), 'proper': True},
                                               event('n', 'x', (5, 9), (5, 9))])), None)
        self.assertEqual((times.dom['m.s'], times.dom['m.f'], times.gap('m.f', 'm.s')), ((5, 8), (6, 9), -1))
        self.assertEqual((times.dom['n.s'], times.dom['n.f'], times.gap('n.f', 'n.s')), ((5, 9), (5, 9), 0))
        self.assertFalse(times.shared)
        self.assertFalse(times.linked('m.f', 'n.s'))
        self.assertEqual(times.gap('m.f', 'n.s'), 9 - 6)  # still the product of the two ranges
        # Shared: Y's start is m's end, so it is at least one step after m's start.
        times = tg.Times(tg.events_of(patient([{**event('m', 'x', (5, 9), (5, 9)), 'proper': True},
                                               {**event('y', 'x', (0, 20)), 'start_var': 'm.f'}])), None)
        self.assertEqual((times.dom['m.f'], times.gap('m.f', 'm.s')), ((6, 9), -1))

    def test_proper_is_a_boolean_on_an_exposure_that_can_end_after_it_starts(self):
        for code, events in [
                ('INVALID_EVENT_PROPER', [{**event('a', 'x', (1, 1)), 'proper': True}]),  # a point
                ('INVALID_EVENT_PROPER', [{**event('a', 'x', (1, 1)), 'proper': False}]),
                ('INVALID_EVENT_PROPER', [{**event('a', 'x', (1, 1), (2, 2)), 'proper': 1}]),
                # It cannot end after it starts: both at 5. Nothing is shared (D25).
                ('EMPTY_PROPER_EVENT', [{**event('a', 'x', (5, 5), (5, 5)), 'proper': True}]),
                # The same with names of its own, and beside an unrelated pair of events that do share a variable.
                ('EMPTY_PROPER_EVENT', [{**event('a', 'x', (5, 5), (5, 5)), 'start_var': 'p', 'end_var': 'q',
                                         'proper': True},
                                        {**event('b', 'x', (0, 9)), 'start_var': 'w'},
                                        {**event('c', 'x', (0, 9)), 'start_var': 'w'}]),
                # One variable for start and end ends as it starts.
                ('EMPTY_PROPER_EVENT', [{**event('a', 'x', (1, 5), (1, 5)), 'end_var': 'a.s', 'proper': True}]),
                # Proper, and sharing its end with another event's start whose range leaves it no room: shared.
                ('EMPTY_SHARED_VARIABLE', [{**event('a', 'x', (5, 9), (5, 20)), 'proper': True},
                                           {**event('b', 'x', (0, 5)), 'start_var': 'a.f'}]),
                # Y starts when X ends and X when Y ends, each strictly after it starts.
                ('EMPTY_SHARED_VARIABLE', [{**event('X', 'x', (0, 9), (0, 9)), 'start_var': 'u', 'end_var': 'v',
                                            'proper': True},
                                           {**event('Y', 'x', (0, 9), (0, 9)), 'start_var': 'v', 'end_var': 'u'}])]:
            with self.subTest(code=code, events=events):
                with self.assertRaisesRegex(tg.TrajectoryError, code):
                    tg.validate_patient(patient(events))
        # The same records without `proper` are valid: an end may equal its start.
        tg.validate_patient(patient([event('a', 'x', (5, 5), (5, 5))]))
        tg.validate_patient(patient([{**event('a', 'x', (1, 5), (1, 5)), 'end_var': 'a.s'}]))
        # The store's trusted path builds the same network, so it refuses the same records.
        with self.assertRaisesRegex(tg.TrajectoryError, 'EMPTY_PROPER_EVENT'):
            tg.match_prepared(tg.PreparedQuery(query([{'id': 'a', 'concept': 'x'}])),
                              patient([{**event('a', 'x', (5, 5), (5, 5)), 'proper': True}]), trusted=True)

    def test_proper_false_is_the_default_byte_for_byte(self):
        for shape, intervals in (('tree', True), ('graph', True)):
            for seed in range(150):
                q, p = random_case(random.Random(f'{shape}-{intervals}-{seed}'), shape, intervals)
                explicit = {**p, 'events': [{**e, 'proper': False} if 'end' in e else e for e in p['events']]}
                self.assertEqual(json.dumps(tg.match(q, explicit)), json.dumps(tg.match(q, p)))
                times, plain = tg.Times(tg.events_of(explicit), p['window']), tg.Times(tg.events_of(p), p['window'])
                self.assertEqual((times.dom, times.distance), (plain.dom, plain.distance))

    def test_proper_changes_decisions_and_the_closed_form_stays_exact(self):
        # The proper cases are not vacuous: dropping `proper` changes the reference's answer on some of
        # them, and an engine that ignores it (the 0 edge) disagrees with the reference there.
        changed, wrong = 0, 0
        original_solve = tg.Times._solve
        cases = [proper_case(random.Random(f'proper-{shape}-{seed}'), shape)
                 for shape in ('chain', 'tree', 'graph') for seed in range(300)]
        for q, p in cases:
            changed += ref.match(q, p) != ref.match(q, without_proper(p))
        tg.Times._solve = lambda self, root, members, bounds, orders, strict=(), empty='EMPTY_SHARED_VARIABLE': \
            original_solve(self, root, members, bounds, orders, (), empty)
        try:
            wrong = sum(tg.match(q, p)['status'] != ref.match(q, p)['status'] for q, p in cases)
        finally:
            tg.Times._solve = original_solve
        self.assertGreaterEqual(changed, 10)
        self.assertGreaterEqual(wrong, 5)
        # The pairwise kernel's closed form decides as the original tests do on every proper case.
        original = tg._arc_spec
        want = [json.dumps(tg.match(q, p)) for q, p in cases]
        tg._arc_spec = lambda a, constraints, kind: None
        try:
            self.assertEqual([json.dumps(tg.match(q, p)) for q, p in cases], want)
        finally:
            tg._arc_spec = original


def d30_moves(q, p):
    """Whether D30's occurrence fixes can touch this case: under the unrelaxed query or some relaxation set, a
    `first` or `last` step's target is uncertain, or `last` picks another event than the order before D30 did.
    The kernel comparison with the frozen engine is made on every other case (OccurrenceOrderTests and the
    differential cover these)."""
    times = tg.patient_times(p)
    pq = tg.PreparedQuery(q)
    for _, chosen in [(0, ())] + pq.sets:
        plan = pq.plan(chosen)
        for s in plan.ordered:
            target = times.occurrence(plan.concepts[s], plan.occurrence[s])
            if target is None:
                continue
            if not target.certain or (plan.occurrence[s] == 'last' and target.record is not target.records[-1]):
                return True
    return False


class KernelIdentityTests(unittest.TestCase):
    """The engine decides as it did before its pairwise kernel was rewritten (D21): every result field,
    and every intermediate decision, equals the old engine's on seeded cases of every shape.

    The old engine is kept whole, byte for byte, as `patterns/fixtures/trajectory_graph_before_kernel.py`
    (the engine at main 12b3440; its digest is pinned below, so it cannot drift). A frozen copy, rather
    than a fixture of recorded outputs, lets the comparison run on any number of new cases, including
    wider ones than the differential's, and compare `decide`'s intermediate results (method, search steps,
    witness) as well as `match`'s. The fixtures directory is not packaged."""
    FROZEN = Path(__file__).resolve().parent / 'fixtures/trajectory_graph_before_kernel.py'
    FROZEN_SHA256 = 'aa7a2e7abf3ccee2f7350182acebaccf24ab20023e022685554e31b097df3722'

    @classmethod
    def frozen(cls):
        assert hashlib.sha256(cls.FROZEN.read_bytes()).hexdigest() == cls.FROZEN_SHA256, 'the frozen engine changed'
        name = 'patterns._trajectory_graph_before_kernel'
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, cls.FROZEN)
            sys.modules[name] = module = importlib.util.module_from_spec(spec)  # dataclasses look it up
            spec.loader.exec_module(module)
        return sys.modules[name]

    @classmethod
    def setUpClass(cls):
        cls.old = cls.frozen()

    @staticmethod
    def decisions(engine, q, p):
        """`decide` for the unrelaxed query and every relaxation set, with events as their fields."""
        times = engine.Times(engine.events_of(p), p['window'])
        event = lambda e: (e.id, sorted(e.concepts), e.s, e.f, e.point, e.sv, e.fv)
        out = []
        for _, chosen in [(0, ())] + engine.relaxation_sets(q):
            d = engine.decide(q, times, chosen, q.get('search_budget', 40_000))
            row = {'forest': d['forest']}
            for kind in ('exact', 'possible'):
                if kind in d:
                    row[kind] = {k: [(s, event(e)) for s, e in v.items()] if k == 'binding' else
                                 list(v.items()) if k == 'witness' else v for k, v in d[kind].items()}
            out.append(row)
        return out

    def setUp(self):
        self.counts = Counter()

    def tearDown(self):
        # Most cases are untouched by D30 and still decided exactly as by the frozen engine.
        if self.counts:
            assert self.counts['compared'] > self.counts['moved by D30'], self.counts

    def compare(self, q, p):
        if p['window'] is not None and d30_moves(q, p):
            self.counts['moved by D30'] += 1
            return
        self.counts['compared'] += 1
        old, new = self.old.match(q, p), tg.match(q, p)
        self.assertEqual(new, old)
        self.assertEqual(json.dumps(new), json.dumps(old))  # key order too
        pq = tg.PreparedQuery(q)
        self.assertEqual(tg.match_prepared(pq, p, trusted=True), old)
        if p['window'] is not None:
            self.assertEqual(self.decisions(tg, q, p), self.decisions(self.old, q, p))

    def wide(self, rng, q, p, intervals):
        """The case with more events, so that domains are wider, and more variables shared."""
        events = list(p['events'])
        for i in range(rng.randint(6, 24)):
            start = rng.randint(-25, 80)  # some before time zero
            width = rng.choice([0, 0, 2, 6, 15])
            e = event(f'w{i}', rng.choice(['x', 'y']), (start, start + width), None, 'any')
            if intervals and rng.random() < 0.4:
                end = start + width + rng.randint(0, 20)
                e['end'] = [end, rng.choice([end, end + 5, None])]
            events.append(e)
        if rng.random() < 0.5:
            share_variables(rng, events)
        q = {**q, 'search_budget': rng.choice([q['search_budget'], 1, 7, 60])}
        if rng.random() < 0.3:
            # Unary constraints the differential's generator does not make: a step before the window's end,
            # or a scope after it.
            step = rng.choice(q['steps'])['id']
            bound = rng.choice([{'max': 0}, {'min': -30}, {'min': -40, 'max': -5}])
            q = {**q, 'relations': q['relations'] + [{'id': 'to-end', 'kind': 'after', 'from': step, 'to': 'END', **bound}]}
        elif rng.random() < 0.15:
            q = {**q, 'scope': 'after'}
        return q, {**p, 'events': events}

    def test_the_engine_decides_as_before_on_seeded_cases(self):
        for shape, intervals in (('chain', False), ('tree', False), ('graph', False), ('tree', True), ('graph', True),
                                 ('chain', True)):
            for seed in range(400):
                rng = random.Random(f'kernel-{shape}-{intervals}-{seed}')
                q, p = random_case(rng, shape, intervals)
                with self.subTest(shape=shape, intervals=intervals, seed=seed):
                    self.compare(q, p)
                    self.compare(*self.wide(rng, q, p, intervals))

    def test_the_differential_cases_and_their_relatives_decide_as_before(self):
        moved = PreparedQueryTests.variants
        for shape, intervals in (('chain', False), ('tree', False), ('graph', False), ('tree', True), ('graph', True)):
            for seed in range(300):
                rng = random.Random(f'{shape}-{intervals}-{seed}')
                q, p = random_case(rng, shape, intervals)
                for patient_ in moved(None, p) + [{**p, 'window': None}]:
                    with self.subTest(shape=shape, intervals=intervals, seed=seed):
                        self.compare(q, patient_)

    def test_the_original_test_decides_where_the_closed_form_is_refused(self):
        # `_arc_spec` refuses the closed form only for bounds outside the profile, which validation never
        # lets through; with it refusing everything, every pair and unary check takes `certain` or
        # `plausible` itself, and the engine still decides as before.
        contradiction = [tg.Constraint(('A', 's'), ('B', 's'), 5, 1, 'r')]
        self.assertIsNone(tg._arc_spec('A', contradiction, 'plausible'))
        self.assertIsNotNone(tg._arc_spec('A', contradiction, 'certain'))
        original = tg._arc_spec
        tg._arc_spec = lambda a, constraints, kind: None
        try:
            for seed in range(150):
                rng = random.Random(f'kernel-refused-{seed}')
                intervals = rng.random() < 0.5
                q, p = random_case(rng, rng.choice(['chain', 'tree', 'graph']), intervals)
                with self.subTest(seed=seed):
                    self.compare(*self.wide(rng, q, p, intervals))
        finally:
            tg._arc_spec = original

    def test_shared_variable_cases_decide_as_before(self):
        # Every case shares variables, between points, exposures and their ends, so pairs of steps meet
        # linked events often: those pairs take the original test.
        for seed in range(400):
            rng = random.Random(f'kernel-shared-{seed}')
            q, p = random_case(rng, rng.choice(['chain', 'tree', 'graph']), True)
            for _ in range(rng.randint(1, 3)):
                share_variables(rng, p['events'])
            with self.subTest(seed=seed):
                self.compare(q, p)
                self.compare(*self.wide(rng, q, p, True))


def tiny_case(rng):
    """A case small enough to enumerate every choice of times: two to four events on a short grid, often of
    one concept with overlapping ranges, so that which is first or last is uncertain; some exposures, some
    proper; sometimes one event naming another's variable."""
    while True:
        events = []
        for i in range(rng.randint(2, 4)):
            lo = rng.randint(0, 5)
            # Concept x, y or both (D30 review): families of two steps' concepts overlap without nesting.
            e = {'id': f'e{i}', 'concepts': [*rng.choice([['x'], ['x'], ['y'], ['x', 'y']]), 'any'],
                 'start': [lo, lo + rng.choice([0, 1, 3, 4])]}
            if rng.random() < 0.35:
                end = e['start'][1] + rng.randint(0, 2)
                e['end'] = [end, end + rng.choice([0, 1, 2])]
                if rng.random() < 0.3 and e['end'][1] > e['start'][0]:
                    e['proper'] = True
            events.append(e)
        if rng.random() < 0.15:
            a, b = rng.sample(events, 2)
            b['start_var'], b['start'] = a.get('start_var', a['id'] + '.s'), list(a['start'])
            if 'end' in b:
                b['end'] = [max(b['end'][0], b['start'][1] + 1), max(b['end'][1], b['start'][1] + 1)]
        n = rng.randint(1, 3)
        names = 'ABC'[:n]
        steps = [{'id': s, 'concept': rng.choice(['x', 'x', 'y']),
                  'occurrence': rng.choice(['first', 'last', 'first', 'last', 'any'])} for s in names]
        relations = []
        for k in range(1, n):
            a, b = names[rng.randrange(k)], names[k]
            if rng.random() < 0.4:
                relations.append({'id': f'r{k}', 'kind': 'during', 'from': a, 'to': b, 'grace': rng.choice([0, 1, 2])})
            else:
                lo, hi = rng.choice([(None, 3), (0, None), (1, 4), (-2, 2), (2, None), (None, None)])
                relations.append({'id': f'r{k}', 'kind': 'after', 'from': a, 'to': b,
                                  **({} if lo is None else {'min': lo}), **({} if hi is None else {'max': hi})})
        if rng.random() < 0.5:
            s = rng.choice(names)
            bound = rng.choice([{'min': 0}, {'max': 0}, {'min': 1}, {'max': -1}, {'min': -2, 'max': 2}])
            relations.append({'id': 'anchor', 'kind': 'after', **rng.choice([{'from': 'END', 'to': s}, {'from': s, 'to': 'END'},
                                                                           {'from': 'START', 'to': s}]), **bound})
        relaxations = []
        if rng.random() < 0.4:
            relaxations.append({'id': 'widen', 'kind': 'widen_concept', 'cost': '0.5', 'step': rng.choice(names),
                                'concept': 'any'})
        afters = [r for r in relations if r['kind'] == 'after' and r.get('max') is not None]
        if afters and rng.random() < 0.4:
            r = rng.choice(afters)
            relaxations.append({'id': 'longer', 'kind': 'extend_max', 'cost': '0.25', 'relation': r['id'], 'max': r['max'] + 3})
        start = rng.randint(0, 4)
        q = query(steps, relations, scope=rng.choice(['any', 'any', 'from_start', 'within', 'after']),
                  relaxations=relaxations, budget=1, search_budget=10_000_000)
        p = patient(events, (start, start + rng.randint(1, 6)))
        try:
            tg.validate_query(q)
            tg.validate_patient(p)
        except tg.TrajectoryError:
            continue
        if worlds.count_worlds(p, 20) <= 3000:
            return q, p


REVIEW_CASES = {
    # The review of the D30 draft. M may fall anywhere from day 60 to 365; L is day 69.
    'last before END': (query([{'id': 'a', 'concept': 'K', 'occurrence': 'last'}],
                              [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'END', 'min': 0}]),
                        patient([event('M', 'K', (60, 365)), event('L', 'K', (69, 69))], (0, 90)), 'possible'),
    'last after END': (query([{'id': 'a', 'concept': 'K', 'occurrence': 'last'}],
                             [{'id': 'r', 'kind': 'after', 'from': 'END', 'to': 'a', 'min': 1}]),
                       patient([event('M', 'K', (60, 365)), event('L', 'K', (69, 69))], (0, 90)), 'possible'),
    'first after END': (query([{'id': 'a', 'concept': 'K', 'occurrence': 'first'}],
                              [{'id': 'r', 'kind': 'after', 'from': 'END', 'to': 'a', 'min': 1}]),
                        patient([event('M', 'K', (60, 365)), event('L', 'K', (69, 69))], (0, 90)), 'none'),
    'first before END': (query([{'id': 'a', 'concept': 'K', 'occurrence': 'first'}],
                               [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'END', 'min': 0}]),
                         patient([event('M', 'K', (60, 365)), event('L', 'K', (69, 69))], (0, 90)), 'exact'),
    # A exposure A starting between day 60 and 365, ending at 800; B from 64 to 70 (or to 900); X at 400 (or 850).
    'X during the first exposure (exact claimed)': (
        query([{'id': 'e', 'concept': 'E', 'occurrence': 'first'}, {'id': 'x', 'concept': 'X'}],
              [{'id': 'r', 'kind': 'during', 'from': 'e', 'to': 'x'}]),
        patient([event('A', 'E', (60, 365), (800, 800)), event('B', 'E', (64, 64), (70, 70)), event('X', 'X', (400, 400))],
                (0, 900)), 'unresolved'),
    'X during the first exposure (none claimed)': (
        query([{'id': 'e', 'concept': 'E', 'occurrence': 'first'}, {'id': 'x', 'concept': 'X'}],
              [{'id': 'r', 'kind': 'during', 'from': 'e', 'to': 'x'}]),
        patient([event('A', 'E', (60, 365), (800, 800)), event('B', 'E', (64, 64), (900, 900)), event('X', 'X', (850, 850))],
                (0, 900)), 'unresolved'),
    # One event bound to a first and a last step that may be different events (found while fixing the above).
    'first then last on one event': (
        query([{'id': 'a', 'concept': 'K', 'occurrence': 'first'}, {'id': 'b', 'concept': 'K', 'occurrence': 'last'}],
              [{'id': 'r', 'kind': 'after', 'from': 'a', 'to': 'b', 'min': 10}]),
        patient([event('M', 'K', (60, 365)), event('L', 'K', (69, 69))], (0, 900)), 'unresolved'),
    # The review of D30 (M1): coupling through another uncertain ordered step. A {K}, M {J, K} and Z {J}, each in
    # [0, 3]; f the first K, l the last J. M may be either step's target.
    'last J strictly before first K (impossible)': (
        query([{'id': 'f', 'concept': 'K', 'occurrence': 'first'}, {'id': 'l', 'concept': 'J', 'occurrence': 'last'}],
              [{'id': 'r', 'kind': 'after', 'from': 'l', 'to': 'f', 'min': 1}]),
        patient([event('A', 'K', (0, 3)), event('M', 'J', (0, 3), None, 'K'), event('Z', 'J', (0, 3))], (0, 10)),
        'unresolved'),
    'first K at or before last J (holds in every choice)': (
        query([{'id': 'f', 'concept': 'K', 'occurrence': 'first'}, {'id': 'l', 'concept': 'J', 'occurrence': 'last'}],
              [{'id': 'r', 'kind': 'after', 'from': 'f', 'to': 'l', 'min': 0}]),
        patient([event('A', 'K', (0, 3)), event('M', 'J', (0, 3), None, 'K'), event('Z', 'J', (0, 3))], (0, 10)),
        'unresolved'),
}



class OccurrenceOrderTests(unittest.TestCase):
    """D30: `first` and `last` are sound against the true semantics (`trajectory_graph_worlds`), which every
    choice of times defines, not against a second copy of the engine's rule. The engine before D30 is not."""

    @classmethod
    def setUpClass(cls):
        cls.old = KernelIdentityTests.frozen()

    def test_the_review_cases(self):
        for name, (q, p, status) in REVIEW_CASES.items():
            with self.subTest(case=name):
                new, old = tg.match(q, p), self.old.match(q, p)
                self.assertEqual(new['status'], status, new)
                self.assertEqual(worlds.check(new, q, p), [], new)
                self.assertEqual(ref.match(q, p)['status'], status)
                if name != 'first before END':  # the old engine called this one possible: unsound only in its
                    self.assertNotEqual(worlds.check(old, q, p), [], (name, old))  # claim that nothing is exact

    def test_the_old_engine_under_claimed_exact(self):
        q, p, _ = REVIEW_CASES['first before END']
        self.assertEqual(self.old.match(q, p)['status'], 'possible')
        self.assertEqual(worlds.check(self.old.match(q, p), q, p), ['possible, but impossible, or some binding is exact'])

    def test_seeded_small_cases_are_sound_and_the_old_engine_is_not(self):
        rng = random.Random('d30-worlds')
        seen, old_wrong, unresolved = 0, 0, 0
        for seed in range(3000):
            q, p = tiny_case(rng)
            new = tg.match(q, p)
            with self.subTest(seed=seed):
                self.assertEqual(worlds.check(new, q, p), [], (q, p, new))
                self.assertEqual(differential_view(new), differential_view(ref.match(q, p), reference=True), (q, p))
            seen += 1
            unresolved += new['status'] == 'unresolved'
            if not any(e.get('proper') for e in p['events']):  # the frozen engine predates `proper`
                old_wrong += bool(worlds.check(self.old.match(q, p), q, p))
        self.assertGreater(old_wrong, 20)
        self.assertLess(unresolved, seen // 4)

    def test_planted_defects_fail_the_worlds_check(self):
        """Each part of the order rule, undone, makes some small case unsound: the rule switched off, or a step
        taken as safe although another step can bind its events, a relation reads its end, or its events share
        variables. (Taking `last` by the old order is caught with the rule off, by the frozen engine above: with
        the rule on, an uncertain target is never trusted, whichever event the order picks.)"""
        def wrong_cases():
            rng = random.Random('d30-worlds')
            cases = [tiny_case(rng) for _ in range(3000)] + [(q, p) for q, p, _ in REVIEW_CASES.values()]
            return sum(bool(worlds.check(tg.match(q, p), q, p)) for q, p in cases)
        self.assertEqual(wrong_cases(), 0)
        tg._ORDER_RULE = False
        try:
            self.assertGreater(wrong_cases(), 0)
        finally:
            tg._ORDER_RULE = True
        def picked_only(plan, s, targets, step_ids):  # D30 as first built: another ordered step's picked event only
            concept, records = plan.concepts[s], targets[s].records
            for t in step_ids:
                if t == s:
                    continue
                if plan.occurrence[t] == 'any':
                    if any(plan.concepts[t] in r[tg._EVENT].concepts for r in records):
                        return True
                elif targets[t] is not None and concept in targets[t].record[tg._EVENT].concepts:
                    return True
            return False
        for name, planted in (('coupling ignored', ('_coupled', lambda *a: False)),
                              ('coupling through picked events only (M1)', ('_coupled', picked_only)),
                              ('ends ignored', ('_reads_end', lambda *a: False)),
                              ('links ignored', ('_safe', lambda plan, s, targets, ids: not tg._reads_end(plan, s, targets)
                                                 and not tg._coupled(plan, s, targets, ids)))):
            attr, value = planted
            original = getattr(tg, attr)
            setattr(tg, attr, value)
            try:
                with self.subTest(defect=name):
                    self.assertGreater(wrong_cases(), 0)
            finally:
                setattr(tg, attr, original)

    def test_the_reference_is_sound_too(self):
        """The reference re-implements the rule; its statuses must also be true of every world."""
        rng = random.Random('d30-worlds-reference')
        for seed in range(1500):
            q, p = tiny_case(rng)
            r = ref.match(q, p)
            events, all_worlds = worlds.worlds(p, 20)
            ask = lambda chosen: worlds._Question(q, chosen, events, all_worlds, p['window'])
            unrelaxed = ask(())
            with self.subTest(seed=seed):
                if r['status'] == 'exact':
                    self.assertTrue(unrelaxed.exact())
                if r['status'] == 'possible':
                    self.assertTrue(unrelaxed.possible() and not unrelaxed.exact())
                if r['status'] in ('none', 'relaxed'):
                    self.assertFalse(unrelaxed.possible())
                if r['status'] == 'none' and not r['passed']:
                    self.assertFalse(any(ask(c).possible() for c in worlds._sets(q)))

    def test_the_levers_change_no_output(self):
        """D30 review: with the cost levers (`_LEVERS`: the sweep in `_Arc.revise`, the second search without its exact
        phase, the direct certainty test) on and off, `match` and `decide` give the same output, byte for byte, on the
        differential cases, the kernel comparison's wide cases, the small worlds cases and the review's cases."""
        cases = [(q, p) for q, p, _ in REVIEW_CASES.values()]
        for shape, intervals in (('chain', False), ('tree', False), ('graph', False), ('tree', True), ('graph', True)):
            for seed in range(300):
                rng = random.Random(f'{shape}-{intervals}-{seed}')
                q, p = random_case(rng, shape, intervals)
                cases.append((q, p))
                cases.append(KernelIdentityTests.wide(None, rng, q, p, intervals))
        rng = random.Random('d30-worlds')
        cases += [tiny_case(rng) for _ in range(3000)]
        def outputs():
            out = []
            for q, p in cases:
                out.append(json.dumps(tg.match(q, p)))
                if p['window'] is not None:
                    out.append(repr(KernelIdentityTests.decisions(tg, q, p)))
            return out
        on = outputs()
        tg._LEVERS = False
        try:
            off = outputs()
        finally:
            tg._LEVERS = True
        self.assertEqual(len(on), len(off))
        self.assertEqual([i for i, (a, b) in enumerate(zip(on, off)) if a != b], [])

    def test_last_picks_the_same_event_as_before_on_exact_times(self):
        rng = random.Random('d30-exact-times')
        for _ in range(500):
            events = [event(f'e{i}', 'x', (t, t), (t + d, t + d) if d is not None else None)
                      for i, (t, d) in enumerate((rng.randint(0, 9), rng.choice([None, 0, 3])) for _ in range(rng.randint(1, 6)))]
            evs = tg.events_of(patient(events))
            before = sorted(evs, key=lambda e: (e.s, e.f, e.id))[-1]
            self.assertIs(tg.candidates({'occurrence': 'last'}, 'x', evs)[0], before)


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
