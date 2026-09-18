"""Exercise evaluator oracles against actual engine output and deliberate corruption."""
from copy import deepcopy
import time
import unittest
from app.recorded_journey import RecordedJourneyWorkspace
from tools.evaluate_integrated_recorded import CONTROLS, FEATURES, check_comparison, check_snapshot


class IntegratedEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = RecordedJourneyWorkspace()
        job = cls.workspace.start({'profile': 'literal', 'stratum': 'synthetic', 'controls': CONTROLS})
        deadline = time.monotonic() + 45
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.02)
            job = cls.workspace.get('literal', job['id'])
        if job['status'] != 'COMPLETED':
            raise RuntimeError('Authored engine did not complete')
        pattern = cls.workspace.pattern_metadata('literal', job['id'])['default_pattern']
        pattern['baseline'].update(operator='ge', value_lexical='60')
        job = cls.workspace.pattern({'profile': 'literal', 'job_id': job['id'], 'pattern': pattern})
        cls.snapshot = cls.workspace.snapshot('literal', job['id'])
        references = cls.workspace.references('literal', job['id'], FEATURES)
        ref = next(row for row in references['anchors'] if row['feature_status'] == 'AVAILABLE')
        cls.comparison = cls.workspace.compare({'profile': 'literal', 'job_id': job['id'],
            'reference_token': ref['token'], 'top_k': 2, 'feature_profile': FEATURES})

    @classmethod
    def tearDownClass(cls):
        cls.workspace.close()

    def test_custom_operator_and_missing_followup_independently_verified(self):
        result = check_snapshot(self.snapshot)
        self.assertTrue(result['all_anchors_agree_with_sql_and_independent_enumeration'])
        self.assertGreater(result['missing_followup_bindings'], 0)

    def test_corrupted_binding_is_rejected(self):
        value = deepcopy(self.snapshot)
        detail = next(d for d in value['details'] if d['bindings'])
        detail['bindings'].pop()
        with self.assertRaisesRegex(ValueError, 'enumeration'):
            check_snapshot(value)

    def test_independent_feature_extraction_patient_minimum_and_ranking(self):
        result = check_comparison(self.snapshot, self.comparison)
        self.assertTrue(result['exact_features_distances_best_anchors_and_ranks_verified'])
        self.assertEqual(result['eligible_patients'], result['ranked_patients'] + result['unresolved_patients'])

    def test_corrupted_distance_is_rejected(self):
        value = deepcopy(self.comparison)
        value['ranked_patients'][0]['distance_exact']['numerator'] = '999'
        with self.assertRaisesRegex(ValueError, 'ranking'):
            check_comparison(self.snapshot, value)

    def test_corrupted_missingness_is_rejected(self):
        value = deepcopy(self.comparison)
        value['unresolved_patients'] = []
        with self.assertRaisesRegex(ValueError, 'coverage'):
            check_comparison(self.snapshot, value)


if __name__ == '__main__':
    unittest.main()
