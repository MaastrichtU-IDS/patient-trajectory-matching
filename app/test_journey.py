"""Baseline-only selection, whole-pool matching, and admitted-fixture replay."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from app import journey


class JourneyTests(unittest.TestCase):
    def setUp(self):
        self.workspace = journey.JourneyWorkspace()
        self.request = deepcopy(journey.DEFAULT_REQUEST)

    def test_default_journey_evaluates_beyond_displayed_neighbours(self):
        report = self.workspace.run(self.request)
        self.assertEqual(report['ranking']['displayed_patient_ids'], ['T02'])
        self.assertEqual(report['eligibility']['eligible_patient_ids'], ['T01', 'T02', 'T04'])
        rows = {p['patient_id']: p for p in report['patients']}
        self.assertEqual(set(rows), {'T01', 'T02', 'T04'})
        self.assertEqual((rows['T01']['original_status'], rows['T01']['option_status']), ('POSSIBLE', 'CERTAIN'))
        self.assertEqual((rows['T02']['original_status'], rows['T02']['option_status']), ('NO_RECORDED_MATCH', 'NO_RECORDED_MATCH'))
        self.assertEqual(rows['T04']['option_status'], 'INCOMPARABLE')
        self.assertEqual(report['added_robust_patient_ids'], ['T01'])
        self.assertEqual(report['workload']['candidate_bindings_per_evaluation'], 3)
        self.assertNotIn('T03', {e['patient_id'] for e in report['source']['events']})
        self.assertTrue(journey.verify(self.workspace.export(report['report_id']))['verified'])
        for row in rows.values():
            self.assertIn('Not recorded', row['evidence']['clinical_outcome'])
            self.assertEqual({e['patient_id'] for e in row['evidence']['records']}, {row['patient_id']})

    def test_top_k_changes_display_only(self):
        first = self.workspace.run(self.request)
        self.request['top_k'] = 3
        second = self.workspace.run(self.request)
        self.assertEqual(second['ranking']['displayed_patient_ids'], ['T02', 'T01'])
        for field in ('source', 'result', 'relaxation', 'patients', 'eligibility'):
            self.assertEqual(first[field], second[field])

    def test_baseline_filter_is_cohort_scope_and_missing_is_unresolved(self):
        self.request['maximum_baseline'] = 50
        report = self.workspace.run(self.request)
        self.assertEqual(report['eligibility']['eligible_patient_ids'], ['T02'])
        self.assertEqual(report['eligibility']['excluded_patient_ids'], ['T01'])
        self.assertEqual(report['eligibility']['unresolved_patient_ids'], ['T04'])
        self.assertEqual([p['patient_id'] for p in report['patients']], ['T02'])
        self.assertEqual({e['patient_id'] for e in report['source']['events']}, {'T02'})
        self.assertEqual(report['relaxation']['robust_patient_ids'], [])

    def test_empty_eligible_pool_has_explicit_empty_selection(self):
        self.request['maximum_baseline'] = 0
        with patch.object(journey.relax, 'execute', side_effect=AssertionError('No empty solver execution')):
            report = self.workspace.run(self.request)
        self.assertEqual(report['patients'], [])
        self.assertIsNone(report['source'])
        self.assertEqual(report['relaxation']['reason'], 'EMPTY_ELIGIBLE_POOL')
        self.assertTrue(journey.verify(report)['verified'])

    def test_budget_and_source_preservation(self):
        allowed = self.workspace.run(self.request)
        self.request['budget'] = '0'
        denied = self.workspace.run(self.request)
        self.assertEqual(allowed['source'], denied['source'])
        self.assertEqual(allowed['result'], denied['result'])
        self.assertEqual(denied['relaxation']['excluded_by_budget'], ['edited-option'])
        self.assertTrue(all(p['option_status'] is None for p in denied['patients']))
        admitted = self.workspace._source
        for collection in ('events', 'variables', 'clocks'):
            self.assertTrue(all(row in admitted[collection] for row in allowed['source'][collection]))

    def test_question_change_keeps_same_histories(self):
        original = self.workspace.run(self.request)
        self.request['question'] = 'sequential'
        report = self.workspace.run(self.request)
        rows = {p['patient_id']: p for p in report['patients']}
        self.assertEqual(report['source'], original['source'])
        self.assertEqual(report['admitted_source_sha256'], original['admitted_source_sha256'])
        self.assertEqual(rows['T01']['original_status'], 'NO_RECORDED_MATCH')
        self.assertEqual(rows['T02']['original_status'], 'NO_RECORDED_MATCH')
        self.assertEqual(rows['T04']['original_status'], 'INCOMPARABLE')
        self.assertEqual(report['added_robust_patient_ids'], [])

    def test_replay_rejects_rehashed_tampering(self):
        report = self.workspace.run(self.request)
        for field in ('baseline_source', 'ranking', 'eligibility', 'source', 'patients', 'relaxation'):
            tampered = deepcopy(report)
            if field == 'baseline_source': tampered[field]['patients'][0]['baseline']['value'] = 0
            elif field == 'ranking': tampered[field]['displayed_patient_ids'] = ['T01']
            elif field == 'eligibility': tampered[field]['eligible_patient_ids'] = ['T01']
            elif field == 'source': tampered[field]['variables'][0]['upper_us'] += 1
            elif field == 'patients': tampered[field][0]['original_status'] = 'CERTAIN'
            else: tampered[field]['robust_patient_ids'] = ['T04']
            tampered['report_id'] = journey.digest({k:v for k,v in tampered.items() if k != 'report_id'})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'differs from replay'):
                journey.verify(tampered)

    def test_baseline_only_ranking_and_no_followup_leakage(self):
        baseline = self.workspace._baseline['patients']
        before = journey.select_and_rank(baseline, self.request)
        modified = deepcopy(baseline)
        for patient in modified:
            patient['followup_outcome'] = 'arbitrary untrusted outcome'
        after = journey.select_and_rank(modified, self.request)
        self.assertEqual(before[1:], after[1:])
        for patient in baseline:
            if patient['baseline']['value'] is not None:
                for field in ('observed_at', 'recorded_at'):
                    self.assertLess(patient['baseline'][field], patient['index_at'])
        original = journey.json.loads
        with patch.object(journey.json, 'loads', wraps=original) as loads:
            def altered(value):
                result = original(value)
                if result.get('dataset_id') == 'authored-patient-journey-baseline':
                    result['patients'][0]['baseline']['recorded_at'] = '2026-09-03T00:00:00Z'
                return result
            loads.side_effect = altered
            with self.assertRaisesRegex(ValueError, 'before the index'):
                journey.JourneyWorkspace()

    def test_missing_reference_baseline_cannot_create_similarity(self):
        self.request['reference_patient_id'] = 'T04'
        report = self.workspace.run(self.request)
        self.assertEqual(report['ranking']['displayed_patient_ids'], [])
        self.assertTrue(all(row['similarity'] is None for row in report['ranking']['all_candidates']))
        self.assertNotIn('T04', {row['patient_id'] for row in report['patients']})

    def test_strict_request_validation(self):
        invalid = [('top_k', True), ('top_k', 0), ('top_k', 4), ('top_k', 1.0),
                   ('maximum_baseline', True), ('maximum_baseline', -1), ('maximum_baseline', 101),
                   ('maximum_baseline', '50'), ('budget', 1.25), ('budget', '2'),
                   ('question', 'arbitrary'), ('reference_patient_id', 'P01')]
        for field, value in invalid:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.workspace.run({**self.request, field: value})
        for value in (None, [], {**self.request, 'source': {}}, {'question': 'overlap'}):
            with self.assertRaises(ValueError): self.workspace.run(value)

    def test_incomplete_evaluation_has_no_saved_report(self):
        with patch.object(journey.relax, 'execute', return_value={'status': 'BLOCKED', 'search_complete': False}):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                self.workspace.run(self.request)
        self.assertEqual(len(self.workspace._reports), 0)

    def test_retains_sixteen_reports_and_exports_deep_copies(self):
        first = self.workspace.run(self.request)
        copied = self.workspace.export(first['report_id'])
        copied['patients'].clear()
        self.assertTrue(self.workspace.export(first['report_id'])['patients'])
        # Empty-pool requests keep this retention check cheap while retaining distinct requests.
        for maximum in range(17):
            self.workspace.run({**self.request, 'maximum_baseline': maximum})
        self.assertEqual(len(self.workspace._reports), 16)
        with self.assertRaises(KeyError): self.workspace.export(first['report_id'])


if __name__ == '__main__':
    unittest.main()
