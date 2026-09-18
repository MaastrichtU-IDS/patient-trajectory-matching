"""Authored relevance labels test metric mechanics, never clinical validity."""
from copy import deepcopy
import math
import unittest

from tools.evaluate_retrieval import digest, evaluate, scale_worker


def bundle():
    value = {'schema': 'heldout-retrieval-evaluation-1', 'label_origin': 'authored_metric_fixture',
             'review_protocol_id': 'authored-metric-unit-test-1',
             'source_context': {'fixture': 'metric-arithmetic-only'},
             'feature_profile': {'id': 'frozen-external-profile'},
             'split': {'development_patient_ids': ['dev'],
                       'evaluation_patient_ids': ['ref', 'a', 'b', 'c', 'd']},
             'queries': [{'reference_patient_id': 'ref', 'candidate_patient_ids': ['a', 'b', 'c', 'd'],
                          'ranked_patient_ids': ['b', 'a', 'd'],
                          'judgments': {'a': 3, 'b': 0, 'c': 1, 'd': 0}}]}
    return freeze(value)


def freeze(value):
    value['frozen_hashes'] = {key: digest(value[key]) for key in
                              ('source_context', 'feature_profile', 'split', 'queries')}
    return value


class RetrievalEvaluationTests(unittest.TestCase):
    def test_exact_metrics_include_relevant_unretrieved_candidate(self):
        report = evaluate(bundle(), 2)
        self.assertEqual(report['metrics']['precision_at_k']['macro_mean'], .5)
        self.assertEqual(report['metrics']['recall_at_k']['macro_mean'], .5)
        self.assertAlmostEqual(report['metrics']['ndcg_at_k']['macro_mean'],
                               (7 / math.log2(3)) / (7 + 1 / math.log2(3)))
        self.assertFalse(report['clinical_validation_established'])
        self.assertEqual(report['coverage']['ranked_candidate_count'], 3)

    def test_missing_labels_are_unknown_not_negative(self):
        value = bundle()
        del value['queries'][0]['judgments']['b']
        metrics = evaluate(freeze(value), 2)['metrics']
        for name in ('precision_at_k', 'recall_at_k', 'ndcg_at_k'):
            self.assertIsNone(metrics[name]['macro_mean'])
            self.assertEqual(metrics[name]['undefined_queries'], 1)
        self.assertEqual(metrics['precision_lower_bound']['macro_mean'], .5)
        self.assertEqual(metrics['precision_upper_bound']['macro_mean'], 1)

    def test_complete_topk_allows_precision_but_not_recall_or_ndcg(self):
        value = bundle()
        del value['queries'][0]['judgments']['c']
        metrics = evaluate(freeze(value), 2)['metrics']
        self.assertEqual(metrics['precision_at_k']['macro_mean'], .5)
        self.assertIsNone(metrics['recall_at_k']['macro_mean'])
        self.assertIsNone(metrics['ndcg_at_k']['macro_mean'])

    def test_no_positive_labels_and_short_ranking(self):
        value = bundle()
        value['queries'][0]['ranked_patient_ids'] = ['a']
        metrics = evaluate(freeze(value), 2)['metrics']
        self.assertEqual(metrics['precision_at_k']['macro_mean'], .5)
        value['queries'][0]['judgments'] = dict.fromkeys(['a', 'b', 'c', 'd'], 0)
        report = evaluate(freeze(value), 2)
        self.assertEqual(report['metrics']['precision_at_k']['macro_mean'], 0)
        self.assertIsNone(report['metrics']['recall_at_k']['macro_mean'])
        self.assertIsNone(report['metrics']['ndcg_at_k']['macro_mean'])
        self.assertEqual(report['coverage']['zero_relevant_complete_pool'], 1)

    def test_empty_ranking_records_zero_recall(self):
        value = bundle()
        value['queries'][0]['ranked_patient_ids'] = []
        metrics = evaluate(freeze(value), 2)['metrics']
        self.assertEqual(metrics['precision_at_k']['macro_mean'], 0)
        self.assertEqual(metrics['recall_at_k']['macro_mean'], 0)
        self.assertEqual(metrics['ndcg_at_k']['macro_mean'], 0)

    def test_frozen_artifacts_and_patient_disjointness(self):
        value = bundle()
        value['feature_profile']['id'] = 'tuned-after-freeze'
        with self.assertRaisesRegex(ValueError, 'Frozen artifact mismatch'):
            evaluate(value)
        value = bundle()
        value['split']['development_patient_ids'].append('a')
        with self.assertRaisesRegex(ValueError, 'disjoint'):
            evaluate(freeze(value))

    def test_no_reference_or_development_candidates(self):
        for patient in ('ref', 'dev', 'outside'):
            value = bundle()
            value['queries'][0]['candidate_patient_ids'].append(patient)
            with self.subTest(patient=patient), self.assertRaises(ValueError):
                evaluate(freeze(value))

    def test_duplicate_ranks_and_invalid_grades_rejected(self):
        value = bundle()
        value['queries'][0]['ranked_patient_ids'].append('a')
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            evaluate(freeze(value))
        for grade in (None, True, -1, 4, float('nan'), '1'):
            value = bundle()
            value['queries'][0]['judgments']['a'] = grade
            with self.subTest(grade=grade), self.assertRaises(ValueError):
                evaluate(freeze(value))

    def test_macro_denominators_report_undefined_queries(self):
        value = bundle()
        value['queries'].append({'reference_patient_id': 'b', 'candidate_patient_ids': ['a', 'c', 'd'],
                                 'ranked_patient_ids': ['a', 'c'], 'judgments': {'a': 1}})
        report = evaluate(freeze(value), 2)
        self.assertEqual(report['metrics']['precision_at_k']['defined_queries'], 1)
        self.assertEqual(report['metrics']['precision_at_k']['undefined_queries'], 1)

    def test_reject_algorithm_generated_labels_and_invalid_k(self):
        value = bundle()
        value['label_origin'] = 'nearest-distance'
        with self.assertRaises(ValueError):
            evaluate(value)
        for k in (True, 0, 21, 1.0):
            with self.subTest(k=k), self.assertRaises(ValueError):
                evaluate(bundle(), k)

    def test_report_is_detached_and_aggregate_only(self):
        value = bundle()
        before = deepcopy(value)
        report = evaluate(value)
        self.assertEqual(value, before)
        self.assertNotIn('split', report)
        self.assertNotIn('reference_patient_id', str(report))
        self.assertNotIn('ranked_patient_ids', str(report))

    def test_exact_pipeline_small_fixture_invariants(self):
        report = scale_worker(12)
        self.assertEqual(report['ranked_patients'], 11)
        self.assertTrue(report['patient_wide_exclusion_verified'])
        self.assertTrue(report['followup_and_end_time_independence_verified'])
        self.assertTrue(report['exact_contributions_verified'])


if __name__ == '__main__':
    unittest.main()
