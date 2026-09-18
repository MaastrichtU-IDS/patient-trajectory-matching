"""Boundary and reconciliation checks for the independent recorded-data oracle."""
import hashlib
import json
from pathlib import Path
import unittest

from tools.evaluate_recorded_workflow import DEFAULT, independent_bindings, verify_result


def fixture():
    def point(identifier, time, value='60', **changes):
        return {'event_id': identifier, 'time': time, 'value': value,
                'item_id': 'pressure', 'unit': 'mmHg', **changes}
    return {'anchor': {'patient_id': 'authored-patient', 'episode_id': 'authored-stay',
                       'start': '2026-01-01T12:00:00'},
            'query': {'baseline': {'item_ids': ['pressure']}},
            'treatment_event_id': 'authored-treatment', 'sql_agreement': True,
            'measurements': [point('outside-before', '2026-01-01T11:29:59.999999'),
                point('baseline', '2026-01-01T11:30:00'),
                point('at-threshold', '2026-01-01T11:59:00', '65'),
                point('wrong-unit', '2026-01-01T11:59:00', unit='kPa'),
                point('wrong-item', '2026-01-01T11:59:00', item_id='other'),
                point('at-start', '2026-01-01T12:00:00', '61'),
                point('last-followup', '2026-01-01T14:00:00', '59'),
                point('outside-after', '2026-01-01T14:00:00.000001', '70')]}


class RecordedEvaluationTests(unittest.TestCase):
    def test_half_open_baseline_closed_followup_exact_threshold_and_identity(self):
        rows = independent_bindings(fixture(), DEFAULT)
        self.assertEqual([(row[3], row[4], row[5]) for row in rows],
                         [('baseline', 'at-start', '1'), ('baseline', 'last-followup', '-1')])

    def test_optional_followup_keeps_pair_without_zero_imputation(self):
        detail = fixture()
        detail['measurements'] = detail['measurements'][:5]
        rows = independent_bindings(detail, DEFAULT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][4:], [None, None, None])

    def test_narrower_window_does_not_reuse_default_membership(self):
        self.assertEqual(independent_bindings(fixture(), {**DEFAULT, 'baseline_minutes': 25}), [])

    def test_reconciliation_rejects_false_engine_delta_or_aggregate(self):
        detail = fixture()
        detail['bindings'] = independent_bindings(detail, DEFAULT)
        result = {'status': 'COMPLETED', 'anchors_verified': 1, 'anchors_total': 1,
                  'details': {'token': detail}, 'roster': [{'patient_id': 'authored-patient', 'status': 'MATCH'}],
                  'metrics': {'patients': 1, 'stays': 1, 'segments': 1, 'eligible_pairs': 1,
                              'followup_bindings': 2, 'pairs_without_followup': 0}}
        class Service:
            def inspect(self, job, token):
                return detail
        aggregate = verify_result(Service(), {'id': 'job'}, result, DEFAULT)
        self.assertEqual(aggregate['delta_sign_binding_counts'], {'positive': 1, 'negative': 1, 'zero': 0})
        detail['bindings'][0][5] = '999'
        with self.assertRaisesRegex(ValueError, 'Independent enumeration'):
            verify_result(Service(), {'id': 'job'}, result, DEFAULT)
        detail['bindings'] = independent_bindings(detail, DEFAULT)
        result['metrics']['patients'] = 2
        with self.assertRaisesRegex(ValueError, 'Aggregate reconciliation'):
            verify_result(Service(), {'id': 'job'}, result, DEFAULT)

    def test_committed_report_contains_aggregates_and_explicit_limits(self):
        path = Path(__file__).resolve().parents[1] / 'verification/recorded-workflow-evaluation.json'
        if not path.is_file():
            self.skipTest('Fresh source evaluation has not yet produced the report')
        report = json.loads(path.read_text())
        self.assertEqual(report['status'], 'TECHNICALLY_VERIFIED')
        self.assertEqual(report['dataset_id'], 'mimic-iv-demo-2.2')
        self.assertFalse(report['clinical_mapping_verified'])
        self.assertFalse(report['clinical_retrieval_relevance_evaluated'])
        self.assertFalse(report['patient_rows_or_identifiers_included'])
        self.assertEqual({row['stratum'] for row in report['strata']}, {'arterial', 'noninvasive', 'art'})
        forbidden = {'patient_id', 'episode_id', 'event_id', 'claim_id', 'bindings', 'roster', 'details', 'folder', 'source_path'}
        def check(value):
            if isinstance(value, dict):
                self.assertFalse(set(value) & forbidden)
                for item in value.values():
                    check(item)
            elif isinstance(value, list):
                for item in value:
                    check(item)
            elif isinstance(value, str):
                self.assertNotIn('/workspace/', value)
        check(report)
        for stratum in report['strata']:
            for relative, digest in stratum['artifacts'].items():
                self.assertEqual(hashlib.sha256((path.parents[1] / relative).read_bytes()).hexdigest(), digest, relative)
            self.assertEqual(len(stratum['trials']), 4)
            self.assertTrue(stratum['warm_full_evidence_equal'])
            self.assertTrue(stratum['cached_full_evidence_equal'])
            self.assertGreater(stratum['process_measurement']['memory']['maximum_sampled_aggregate_rss_bytes'], 0)
            for trial in stratum['trials']:
                self.assertEqual(trial['anchors_verified'], 944)
                self.assertEqual(trial['stays_retained'], 140)
                self.assertTrue(trial['all_anchors_agree_with_sql_and_independent_enumeration'])


if __name__ == '__main__':
    unittest.main()
