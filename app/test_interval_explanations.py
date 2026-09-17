"""Explanation fidelity against independently checked timeline arithmetic."""
from copy import deepcopy
import json
import unittest

from app.interval_editor import IntervalEditor, ROOT
from app.interval_explanations import ALLEN_LABELS, _example, describe_constraint, explain_patient
from patterns import bounded_intervals, extended_interval_query, robust_relaxation


class ExplanationTests(unittest.TestCase):
    def run_gap(self, budget='1.25'):
        return IntervalEditor().run({
            'fixture': 'sequential', 'relation': 'gap',
            'gap': {'minimum_minutes': '0.000001', 'maximum_minutes': '48'},
            'duration': None, 'minimum_overlap_minutes': None,
            'relaxation': {'max_cost': budget,
                'gap': {'minimum_minutes': '0.000001', 'maximum_minutes': '50'},
                'duration': None, 'minimum_overlap_minutes': None}})

    def test_possible_counterexample_and_exact_option_change(self):
        report = self.run_gap()
        explanation = report['patients'][1]['explanation']
        constraint = explanation['original']['bindings'][0]['constraints'][0]
        binding = report['result']['trajectories'][1]['bindings'][0]
        for key, evidence in [('witness', 'possible_witness'), ('counterexample', 'counterexample')]:
            world = binding[evidence]
            gap = world['T02-b-start'] - world['T02-a-end']
            self.assertEqual(constraint[key]['value_us'], gap)
            self.assertEqual(constraint[key]['satisfied'], 60 <= gap <= 48 * 60000000)
        self.assertTrue(constraint['witness']['satisfied'])
        self.assertFalse(constraint['counterexample']['satisfied'])
        self.assertEqual(explanation['options'][0]['status'], 'CERTAIN')
        change = explanation['options'][0]['changes'][0]
        self.assertIn('48 min', change['before'])
        self.assertIn('50 min', change['after'])
        self.assertIn('uncertainty', explanation['preserved'])

    def test_incomparable_never_subtracts_different_clocks(self):
        explanation = self.run_gap()['patients'][3]['explanation']
        self.assertEqual(explanation['original']['status'], 'INCOMPARABLE')
        constraint = explanation['original']['bindings'][0]['constraints'][0]
        self.assertIsNone(constraint['witness'])
        self.assertIsNone(constraint['counterexample'])
        self.assertEqual(explanation['options'][0]['status'], 'INCOMPARABLE')

    def test_budget_excluded_and_no_option(self):
        report = self.run_gap('0')
        explanation = report['patients'][1]['explanation']
        self.assertEqual(explanation['options'], [])
        self.assertIn('Excluded', explanation['budget_note'])
        controls = deepcopy(report['controls'])
        controls['relaxation'] = None
        explanation = IntervalEditor().run(controls)['patients'][1]['explanation']
        self.assertEqual(explanation['budget_note'], 'No relaxation option was requested.')

    def test_impossible_conjunction_does_not_blame_each_constraint(self):
        source = json.loads((ROOT / 'examples/extended-relaxation/source.json').read_text())
        for variable in source['variables']:
            if variable['id'] == 'ae': variable.update(lower_us=2, upper_us=10)
            if variable['id'] == 'bs': variable.update(lower_us=3, upper_us=12)
            if variable['id'] == 'be': variable.update(lower_us=6, upper_us=15)
        query = json.loads((ROOT / 'examples/extended-relaxation/query.json').read_text())
        query['constraints'] = [query['constraints'][0], {'id': 'later', 'operator': 'gap',
            'left': 'a', 'right': 'b', 'min_gap_us': 1, 'max_gap_us': 20}]
        snapshot = bounded_intervals.prepare(source)
        for c in query['constraints']:
            individual = {**query, 'constraints': [c]}
            self.assertTrue(extended_interval_query.execute(snapshot, individual)['trajectories'][0]['bindings'][0]['possible'])
        policy = {'profile': robust_relaxation.PROFILE, 'kind': 'extended', 'relaxable_targets': [],
                  'max_cost': '0', 'max_changed_targets': 3, 'options': []}
        result = robust_relaxation.execute(source, query, policy)
        explanation = explain_patient(source, query, result, 'P', policy)
        self.assertEqual(explanation['original']['status'], 'NO_RECORDED_MATCH')
        binding = explanation['original']['bindings'][0]
        self.assertIn('individual constraints are not each proven impossible', binding['summary'])
        self.assertEqual(set(binding['contradiction_constraints']), {'containment', 'later'})
        self.assertTrue(all(c['witness'] is None and c['counterexample'] is None for c in binding['constraints']))

    def test_all_allen_relations_and_strict_endpoints(self):
        events = {'a': {'start_var': 's', 'end_var': 'e'}, 'b': {'start_var': 'u', 'end_var': 'v'}}
        variables = {name: {'clock_id': 'one'} for name in ('s', 'e', 'u', 'v')}
        slots = {'a': {'event_id': 'a'}, 'b': {'event_id': 'b'}}
        examples = {'before': (0, 2, 3, 5), 'meets': (0, 2, 2, 5),
                    'overlaps': (0, 3, 2, 5), 'starts': (0, 2, 0, 5),
                    'during': (1, 3, 0, 5), 'finishes': (1, 5, 0, 5),
                    'equals': (0, 5, 0, 5), 'after': (3, 5, 0, 2),
                    'met_by': (2, 5, 0, 2), 'overlapped_by': (2, 5, 0, 3),
                    'started_by': (0, 5, 0, 2), 'contains': (0, 5, 1, 3),
                    'finished_by': (0, 5, 1, 5)}
        self.assertEqual(set(examples), set(ALLEN_LABELS))
        for selected, endpoints in examples.items():
            for operator in ALLEN_LABELS:
                constraint = {'id': 'relation', 'operator': operator, 'left': 'a', 'right': 'b'}
                with self.subTest(selected=selected, operator=operator):
                    self.assertEqual(_example(constraint, slots, events, variables,
                        dict(zip(('s', 'e', 'u', 'v'), endpoints)))['satisfied'], selected == operator)
                    self.assertTrue(describe_constraint(constraint).startswith('a '))

    def test_duration_and_overlap_are_independent_arithmetic(self):
        report = IntervalEditor().run({'fixture': 'overlap', 'relation': 'contains', 'gap': None,
            'duration': {'minimum_minutes': '10', 'maximum_minutes': '10'}, 'minimum_overlap_minutes': '3'})
        binding = report['result']['trajectories'][0]['bindings'][0]
        descriptions = report['patients'][0]['explanation']['original']['bindings'][0]['constraints']
        for label, key in [('witness', 'possible_witness'), ('counterexample', 'counterexample')]:
            w = binding[key]
            duration = w['T01-a-end'] - w['T01-a-start']
            overlap = max(0, min(w['T01-a-end'], w['T01-b-end']) - max(w['T01-a-start'], w['T01-b-start']))
            self.assertEqual(descriptions[1][label]['value_us'], duration)
            self.assertEqual(descriptions[2][label]['value_us'], overlap)
            self.assertEqual(descriptions[1][label]['satisfied'], duration == 600000000)
            self.assertEqual(descriptions[2][label]['satisfied'], overlap >= 180000000)

    def test_incomplete_execution_cannot_get_completed_explanation(self):
        with self.assertRaisesRegex(ValueError, 'complete'):
            explain_patient({}, {}, {'search_complete': False, 'evaluations': []}, 'P')


if __name__ == '__main__':
    unittest.main()
