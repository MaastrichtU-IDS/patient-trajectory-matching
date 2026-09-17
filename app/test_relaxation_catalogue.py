"""Finite custom catalogues preserve whole-query certainty and source evidence."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from app import journey, pattern_builder as builder, relaxation_catalogue as catalogue
from patterns import robust_relaxation as relax


def example():
    pattern = {'slots': deepcopy(builder.DEFAULT_PATTERN['slots'][:2]), 'constraints': [
        {'id': 'inside', 'operator': 'contains', 'left': 'infusion', 'right': 'collection'},
        {'id': 'shared', 'operator': 'minimum_overlap', 'left': 'infusion', 'right': 'collection',
         'minimum_minutes': '3'},
        {'id': 'length', 'operator': 'duration', 'slot': 'infusion',
         'minimum_minutes': '11', 'maximum_minutes': '12'}]}
    overlap = {'target': 'shared', 'minimum_minutes': '2'}
    duration = {'target': 'length', 'minimum_minutes': '10', 'maximum_minutes': '12'}
    options = {'relaxable_targets': ['shared', 'length'], 'max_changed_targets': 2, 'options': [
        {'id': 'overlap-only', 'cost': '0.1', 'changes': [overlap]},
        {'id': 'duration-only', 'cost': '0.2', 'changes': [duration]},
        {'id': 'both', 'cost': '0.3', 'changes': [deepcopy(overlap), deepcopy(duration)]}]}
    return pattern, options


class CatalogueCompilerTests(unittest.TestCase):
    def setUp(self):
        self.pattern, self.catalogue = example()
        self.query = builder.compile_pattern(self.pattern)

    def compile(self, value=None, budget='1'):
        return catalogue.compile_catalogue(self.query, self.catalogue if value is None else value, budget)

    def test_exact_costs_and_minutes_and_original_preservation(self):
        original_query = deepcopy(self.query)
        self.catalogue['options'][0]['changes'][0]['minimum_minutes'] = '0.000001'
        self.catalogue['options'][0]['cost'] = '0.100001'
        original_catalogue = deepcopy(self.catalogue)
        policy = self.compile(budget='0.100000')
        self.assertEqual(policy['options'][0]['changes'][0]['minimum_us'], 60)
        self.assertEqual(policy['options'][0]['cost'], '0.100001')
        self.assertEqual(relax.variants(self.query, policy)[1], ['overlap-only', 'duration-only', 'both'])
        self.assertEqual(self.query, original_query)
        self.assertEqual(self.catalogue, original_catalogue)

    def test_zero_options_and_zero_change_budget(self):
        policy = self.compile(catalogue.EMPTY_CATALOGUE, '100.000000')
        self.assertEqual(policy['options'], [])
        self.catalogue['max_changed_targets'] = 0
        self.catalogue['options'][0]['cost'] = '0'
        variants, excluded = relax.variants(self.query, self.compile(budget='0'))
        self.assertEqual([v['id'] for v in variants], ['original'])
        self.assertEqual(excluded, [o['id'] for o in self.catalogue['options']])

    def test_decimal_contract_for_both_cost_and_budget(self):
        for value in (0, 1.25, True, None, [], {}, '-0', '-1', '+1', '01', '1.', '.1',
                      '1e0', 'NaN', 'Infinity', ' 1', '1\n', '100.000001', '101',
                      '0.0000001', '0'*1000, '1'*1000):
            with self.subTest(value=value, field='budget'), self.assertRaises(ValueError):
                self.compile(budget=value)
            invalid = deepcopy(self.catalogue); invalid['options'][0]['cost'] = value
            with self.subTest(value=value, field='cost'), self.assertRaises(ValueError):
                self.compile(invalid)
        for value in ('0', '0.000001', '1.250000', '100', '100.000000'):
            self.assertEqual(catalogue.cost_string(value), value)

    def test_schema_bounds_ids_and_protected_fields(self):
        invalid = [[], {}, {**self.catalogue, 'source': {}}]
        for key, value in [('options', None), ('options', self.catalogue['options'] * 2),
                           ('relaxable_targets', ['shared', 'shared']), ('relaxable_targets', ['missing']),
                           ('relaxable_targets', ['inside']), ('relaxable_targets', [[]]),
                           ('max_changed_targets', True), ('max_changed_targets', '1'),
                           ('max_changed_targets', -1), ('max_changed_targets', 7)]:
            invalid.append({**self.catalogue, key: value})
        for key, value in [('id', 'original'), ('id', 'duration-only'), ('id', []), ('id', 'x'*33),
                           ('changes', []), ('changes', None), ('changes', [{}]*7), ('query', {})]:
            item = deepcopy(self.catalogue); item['options'][0][key] = value; invalid.append(item)
        for change in ({'target': 'inside', 'minimum_minutes': '1'},
                       {'target': 'missing', 'minimum_minutes': '1'},
                       {'target': 'shared', 'minimum_minutes': '2', 'left': 'collection'},
                       {'target': 'shared', 'minimum_minutes': '2', 'maximum_minutes': '4'},
                       {'target': [], 'minimum_minutes': '2'}, None):
            item = deepcopy(self.catalogue); item['options'][0]['changes'] = [change]; invalid.append(item)
        item = deepcopy(self.catalogue); item['relaxable_targets'] = ['length']; invalid.append(item)
        item = deepcopy(self.catalogue); item['options'][0]['changes'] *= 2; invalid.append(item)
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError): self.compile(item, '0')

    def test_all_allen_relations_are_protected_even_when_excluded(self):
        for operator in builder.extended.ALLEN:
            pattern = deepcopy(self.pattern); pattern['constraints'][0]['operator'] = operator
            query = builder.compile_pattern(pattern)
            invalid = deepcopy(self.catalogue); invalid['relaxable_targets'].append('inside')
            with self.subTest(operator=operator), self.assertRaises(ValueError):
                catalogue.compile_catalogue(query, invalid, '0')

    def test_strict_widening_positive_metrics_and_minute_bounds(self):
        for minimum in ('0', '-1', '3', '4', '0.0000001', '1441'):
            invalid = deepcopy(self.catalogue); invalid['options'][0]['changes'][0]['minimum_minutes'] = minimum
            with self.subTest(overlap=minimum), self.assertRaises(ValueError): self.compile(invalid, '0')
        for minimum, maximum in (('0', '12'), ('11', '12'), ('12', '13'), ('10', '11'), ('13', '10')):
            invalid = deepcopy(self.catalogue)
            invalid['options'][1]['changes'][0].update(minimum_minutes=minimum, maximum_minutes=maximum)
            with self.subTest(duration=(minimum, maximum)), self.assertRaises(ValueError): self.compile(invalid, '0')
        query = builder.compile_pattern(builder.DEFAULT_PATTERN)
        gap = {'relaxable_targets': ['followup-gap'], 'max_changed_targets': 1,
               'options': [{'id': 'wider', 'cost': '0', 'changes': [
                   {'target': 'followup-gap', 'minimum_minutes': '-0.000001', 'maximum_minutes': '30'}]}]}
        policy = catalogue.compile_catalogue(query, gap, '0')
        self.assertEqual(policy['options'][0]['changes'][0]['lower_us'], -60)


class CatalogueJourneyTests(unittest.TestCase):
    def setUp(self):
        self.workspace = journey.JourneyWorkspace()
        pattern, options = example()
        self.request = {**journey.DEFAULT_REQUEST, 'question': 'custom', 'budget': '1',
                        'pattern': pattern, 'catalogue': options}

    def test_separate_options_do_not_compose_and_explicit_combination_works(self):
        separate = deepcopy(self.request); separate['catalogue']['options'].pop()
        report = self.workspace.run(separate)
        self.assertEqual(report['relaxation']['robust_patient_ids'], [])
        report = self.workspace.run(self.request)
        patient = next(p for p in report['patients'] if p['patient_id'] == 'T01')
        self.assertEqual(patient['original_status'], 'NO_RECORDED_MATCH')
        self.assertEqual([o['status'] for o in patient['option_results']], ['NO_RECORDED_MATCH', 'POSSIBLE', 'CERTAIN'])
        self.assertEqual((patient['selected_option'], patient['selected_cost']), ('both', '0.3'))
        self.assertIsNone(patient['option_status'])
        self.assertEqual([o['selected'] for o in patient['option_results']], [False, False, True])
        self.assertEqual(len(report['relaxation']['evaluations']), 4)
        self.assertEqual(report['relaxation']['context']['policy'], report['policy'])
        self.assertEqual(report['relaxation']['evaluations'][0]['option']['query'], report['query'])

    def test_exact_minimum_cost_and_deterministic_ties(self):
        both = self.request['catalogue']['options'][-1]
        self.request['catalogue']['options'] = [{**deepcopy(both), 'id': name, 'cost': cost} for name, cost in
                                               [('a-dear', '0.100001'), ('z-cheap', '0.1'), ('b-cheap', '0.100000')]]
        self.request['budget'] = '0.100000'
        report = self.workspace.run(self.request)
        patient = next(p for p in report['patients'] if p['patient_id'] == 'T01')
        self.assertEqual(patient['selected_option'], 'b-cheap')
        self.assertEqual(patient['option_results'][0], {'option_id': 'a-dear', 'cost': '0.100001',
                         'status': None, 'excluded_by_budget': True, 'selected': False})
        self.request['catalogue']['options'].pop()
        self.assertEqual(self.workspace.run(self.request)['patients'][0]['selected_option'], 'z-cheap')

    def test_zero_cost_original_wins_ties_and_legacy_remains(self):
        self.request['pattern'] = deepcopy(builder.DEFAULT_PATTERN)
        self.request['budget'] = '0'
        self.request['catalogue'] = {'relaxable_targets': ['followup-gap'], 'max_changed_targets': 1,
            'options': [{'id': 'a-free', 'cost': '0', 'changes': [
                {'target': 'followup-gap', 'minimum_minutes': '0', 'maximum_minutes': '31'}]}]}
        report = self.workspace.run(self.request)
        self.assertEqual(report['patients'][0]['selected_option'], 'original')
        self.assertFalse(report['patients'][0]['option_results'][0]['selected'])
        del self.request['catalogue']
        report = self.workspace.run(self.request)
        self.assertEqual(report['patients'][0]['option_results'], [])
        self.request['budget'] = '0.0'
        with self.assertRaises(ValueError): self.workspace.run(self.request)
        preset = self.workspace.run(journey.DEFAULT_REQUEST)
        self.assertEqual(preset['patients'][0]['option_status'], 'CERTAIN')

    def test_budget_and_change_count_exclusions_have_no_inferred_status(self):
        self.request['budget'] = '0.2'
        report = self.workspace.run(self.request)
        self.assertEqual(report['relaxation']['excluded_by_budget'], ['both'])
        self.request['budget'] = '1'
        self.request['catalogue']['max_changed_targets'] = 1
        report = self.workspace.run(self.request)
        self.assertEqual(report['relaxation']['excluded_by_budget'], ['both'])
        for patient in report['patients']:
            self.assertIsNone(patient['option_results'][2]['status'])
        self.request['budget'] = '0'
        report = self.workspace.run(self.request)
        self.assertEqual(len(report['relaxation']['evaluations']), 1)
        self.assertTrue(all(o['excluded_by_budget'] for p in report['patients'] for o in p['option_results']))

    def test_source_top_k_and_baseline_exclusion_remain_independent(self):
        report = self.workspace.run(self.request)
        self.request['top_k'] = 3
        changed = self.workspace.run(self.request)
        self.assertEqual(report['patients'], changed['patients'])
        self.assertEqual(report['source'], changed['source'])
        self.assertEqual(report['source']['events'], [e for e in self.workspace._source['events'] if e['patient_id'] != 'T03'])
        self.request['maximum_baseline'] = 50
        filtered = self.workspace.run(self.request)
        self.assertEqual([p['patient_id'] for p in filtered['patients']], ['T02'])
        self.assertEqual(filtered['admitted_source_sha256'], report['admitted_source_sha256'])
        self.request['maximum_baseline'] = 0
        self.assertEqual(self.workspace.run(self.request)['patients'], [])

    def test_invalid_excluded_options_rejected_before_preparation(self):
        self.request['budget'] = '0'
        self.request['catalogue']['options'][0]['changes'][0]['minimum_minutes'] = '0'
        with patch.object(journey.bt, 'prepare', side_effect=AssertionError('must validate first')):
            with self.assertRaises(ValueError): self.workspace.run(self.request)
        self.assertEqual(len(self.workspace._reports), 0)
        for question in ('overlap', 'sequential'):
            with self.assertRaises(ValueError): self.workspace.run({**journey.DEFAULT_REQUEST,
                                                                  'question': question, 'catalogue': catalogue.EMPTY_CATALOGUE})

    def test_incomplete_execution_is_not_saved_and_export_replay_detects_tampering(self):
        with patch.object(journey.relax, 'execute', return_value={'status': 'BLOCKED', 'search_complete': False}):
            with self.assertRaisesRegex(ValueError, 'Incomplete'): self.workspace.run(self.request)
        self.assertEqual(len(self.workspace._reports), 0)
        report = self.workspace.run(self.request)
        self.assertTrue(journey.verify(self.workspace.export(report['report_id']))['verified'])
        report['patients'][0]['option_results'][0]['status'] = 'CERTAIN'
        report['report_id'] = journey.digest({k: v for k, v in report.items() if k != 'report_id'})
        with self.assertRaisesRegex(ValueError, 'replay'): journey.verify(report)


if __name__ == '__main__':
    unittest.main()
