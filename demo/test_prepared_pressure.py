"""Differential checks for reusable graph/semantic views and fresh temporal queries."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pressure
from patterns import prepared_mixed_query as prepared, test_mixed_record_query as fixtures
from patterns import reviewed_pressure_session as sessions

DEFAULT = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}


class PreparedTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.MixedTests()
        case.setUp()
        return case

    def test_changed_queries_reuse_views_but_rerun_temporal_classification(self):
        f = self.fixture(); executor = prepared.PreparedExecutor()
        executor.execute(*(f.values[n] for n in fixtures.NAMES))
        f.values['query']['baseline']['value_lexical'] = '59'
        expected = f.run_query()
        with (patch.object(prepared.m.intervals, 'execute', side_effect=AssertionError('No repeated projection')),
              patch.object(prepared.m.points, 'select', side_effect=AssertionError('No repeated selection')),
              patch.object(prepared.m, '_compile', side_effect=AssertionError('No repeated source network')),
              patch.object(prepared.m.cohort, 'classify', wraps=prepared.m.cohort.classify) as classify):
            actual = executor.execute(*(f.values[n] for n in fixtures.NAMES))
        self.assertEqual(actual, expected)
        self.assertGreater(classify.call_count, 0)
        self.assertEqual(executor.stats['hits'], 1)

    def test_finite_world_geometry_precision_scope_and_review_cases_match_reference(self):
        names = ['test_exhaustive_uncertain_worlds_agree_with_fixed_witness_classification',
                 'test_each_world_can_have_a_different_baseline_without_a_certain_named_binding',
                 'test_half_open_interval_contains_start_and_excludes_end',
                 'test_source_constraints_survive_composition_and_change_certainty',
                 'test_exact_decimal_predicates_and_subtraction_do_not_round',
                 'test_all_scalar_predicate_operators',
                 'test_patient_episode_binding_prevents_borrowing_followup',
                 'test_followup_clock_unknown_does_not_remove_eligible_baseline',
                 'test_withdrawing_followup_preserves_baseline_eligibility',
                 'test_pending_measurements_are_not_used_for_baseline_or_followup',
                 'test_pending_treatments_yield_no_eligible_binding']
        for name in names:
            with self.subTest(case=name):
                f = self.fixture(); executor = prepared.PreparedExecutor()
                def compare():
                    values = [f.values[n] for n in fixtures.NAMES]
                    reference = prepared.m.execute(*values)
                    self.assertEqual(executor.execute(*values), reference)
                    self.assertEqual(executor.execute(*values), reference)
                    return reference
                f.run_query = compare
                getattr(f, name)()
                self.assertGreater(executor.stats['hits'], 0)

    def test_complete_input_identity_changes_force_new_preparation(self):
        f = self.fixture(); executor = prepared.PreparedExecutor()
        def run(): return executor.execute(*(f.values[n] for n in fixtures.NAMES))
        run()
        changes = [lambda: f.values['query'].update(id='new-query-id'),
                   lambda: f.values['query'].update(treatment_class_iri='https://example.org/trajectory/mixed-example/RecordedItemA'),
                   lambda: f.values['alignment']['bindings'][0].update(reason='another rationale'),
                   lambda: f.values['interval-policy']['decisions'][0].update(reason='another review rationale'),
                   lambda: f.values['measurement-policy']['decisions'][0].update(reason='another point review'),
                   lambda: (f.values['semantic-policy'].update(id='changed-semantic-policy'), f.rebind()),
                   lambda: (f.values['interval-store']['clocks'][0].update(origin='2150-01-01T09:00:00'), f.rebind())]
        for mutate in changes:
            before = executor.stats['misses']; mutate()
            self.assertEqual(run(), f.run_query())
            self.assertEqual(executor.stats['misses'], before + 1)
        f.values['measurement-store']['claims'][0]['bundle']['events'][0]['value_lexical'] = '1'
        f.rebind(); before = executor.stats['misses']
        self.assertEqual(run(), f.run_query())
        self.assertEqual(executor.stats['misses'], before + 1)
        before = executor.stats['misses']
        executor.execute(*(f.values[n] for n in fixtures.NAMES), timeout_seconds=21)
        self.assertEqual(executor.stats['misses'], before + 1)

    def test_invalid_alignment_and_changed_implementation_clear_preparation(self):
        f = self.fixture(); executor = prepared.PreparedExecutor()
        values = [f.values[n] for n in fixtures.NAMES]
        executor.execute(*values)
        with patch.object(prepared, 'artifact_stamp', return_value={}):
            with self.assertRaisesRegex(ValueError, 'IMPLEMENTATION_CHANGED'): executor.execute(*values)
        self.assertEqual(len(executor.entries), 0)
        executor.execute(*values)
        f.values['alignment']['interval_store_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'STALE_CLOCK_ALIGNMENT'): executor.execute(*values)
        self.assertEqual(len(executor.entries), 0)

    def test_failed_backend_and_blocked_queries_are_not_prepared(self):
        f = self.fixture(); executor = prepared.PreparedExecutor()
        with patch.object(prepared.m.semantic, 'run_backend', return_value={'status': 'BACKEND_TIMEOUT'}):
            result = executor.execute(*(f.values[n] for n in fixtures.NAMES))
        self.assertEqual(result['status'], 'BLOCKED_INTERVAL_VIEW')
        self.assertEqual(len(executor.entries), 0)
        executor.execute(*(f.values[n] for n in fixtures.NAMES))
        with patch.object(prepared, 'MAX_BASELINE_TESTS', 0):
            result = executor.execute(*(f.values[n] for n in fixtures.NAMES))
        self.assertEqual(result['status'], 'BLOCKED_MIXED_SOURCE')
        self.assertIsNone(result['certain_patient_ids'])
        self.assertEqual(len(executor.entries), 0)

    def test_cached_views_and_temporal_certificates_are_isolated_from_callers(self):
        f = self.fixture(); executor = prepared.PreparedExecutor()
        values = [f.values[n] for n in fixtures.NAMES]
        original = executor.execute(*values)
        warm = executor.execute(*values)
        warm['interval_view'].clear(); warm['edge_evidence'].clear()
        warm['baseline_bindings'][0]['eligibility'].clear()
        self.assertEqual(executor.execute(*values), original)
        self.assertEqual(f.run_query(), original)

    def test_entry_and_byte_bounds_fall_back_to_fresh_execution(self):
        f = self.fixture(); executor = prepared.PreparedExecutor(max_entries=1)
        values = [f.values[n] for n in fixtures.NAMES]
        executor.execute(*values)
        original = deepcopy(f.values['query'])
        f.values['query']['id'] = 'second'; executor.execute(*values)
        f.values['query'].update(original); executor.execute(*values)
        self.assertEqual(executor.stats['misses'], 3)
        self.assertEqual(executor.stats['evicted'], 2)
        self.assertEqual(len(executor.entries), 1)
        small = prepared.PreparedExecutor(max_bytes=1)
        self.assertEqual(small.execute(*values), small.execute(*values))
        self.assertEqual(len(small.entries), 0)
        self.assertEqual(small.stats['misses'], 2)

    def service(self):
        s = pressure.PressureService(synthetic=True)
        self.addCleanup(s.close)
        return s

    def run_service(self, s, controls):
        jid = s.start({'stratum': 'synthetic', 'controls': controls})['id']
        for _ in range(2000):
            job = s.get(jid)
            if job['status'] != 'RUNNING': return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_service_controls_reuse_preparation_and_recheck_each_anchor_in_sql(self):
        s = self.service(); first = self.run_service(s, DEFAULT)
        self.assertEqual(first['execution']['batch_preparation']['fresh_batches'], 3)
        for changed in [{'threshold': '59'}, {'baseline_minutes': 10}, {'followup_minutes': 30}]:
            controls = {**DEFAULT, **changed}
            with patch.object(sessions.p.reference, 'execute', wraps=sessions.p.reference.execute) as sql:
                job = self.run_service(s, controls)
            self.assertEqual(job['status'], 'COMPLETED')
            self.assertEqual(job['execution']['mode'], 'fresh_query')
            self.assertEqual(job['execution']['batch_preparation']['reused_batches'], 3)
            self.assertEqual(job['execution']['batch_preparation']['fresh_batches'], 0)
            self.assertEqual(sql.call_count, 3)
            reference = s.session.execute(controls)
            actual = deepcopy(s.jobs[job['id']]['_result'])
            actual.pop('elapsed_seconds'); reference.pop('elapsed_seconds')
            self.assertEqual(actual, reference)
        repeated = self.run_service(s, controls)
        self.assertEqual(repeated['execution']['mode'], 'cached_complete_result')
        self.assertEqual(repeated['execution']['batch_preparation']['reused_batches'], 0)

    def test_sql_detects_corrupted_prepared_output_and_suppresses_membership(self):
        s = self.service(); self.run_service(s, DEFAULT)
        evaluate = prepared._evaluate
        def corrupt(*args):
            result = deepcopy(evaluate(*args))
            for binding in result['baseline_bindings']:
                for followup in binding['followup']:
                    if followup['value_change'] and followup['value_change'].get('delta') is not None:
                        followup['value_change']['delta'] = '999'
            return result
        with patch.object(prepared, '_evaluate', side_effect=corrupt):
            job = self.run_service(s, {**DEFAULT, 'threshold': '59'})
        self.assertEqual(job['status'], 'BLOCKED')
        self.assertNotIn('summary', job)
        self.assertEqual(len(s.result_cache.entries), 0)
        self.assertEqual(len(s.prepared_executor.entries), 0)

    def test_committed_prepared_reports_bind_current_code_and_exact_comparison(self):
        from benchmark_prepared_pressure import artifacts, DEFAULT, CHANGED
        for name, anchors, stays in [('synthetic', 3, 5), ('arterial', 944, 140)]:
            report = json.loads((sessions.p.ei.ROOT / f'verification/prepared-pressure-{name}-report.json').read_text())
            self.assertEqual(report['status'], 'VERIFIED')
            self.assertEqual(report['artifacts'], artifacts())
            self.assertEqual(report['original_controls'], DEFAULT)
            self.assertEqual(report['changed_controls'], CHANGED)
            self.assertEqual(report['anchors_verified'], anchors)
            self.assertEqual(report['anchor_inspections_equal'], anchors)
            self.assertEqual(report['stays_retained'], stays)
            self.assertTrue(report['all_result_fields_except_duration_equal'])
            self.assertEqual(report['changed_preparation']['fresh_batches'], 0)
            self.assertEqual(report['cold_preparation']['fresh_batches'], report['changed_preparation']['reused_batches'])
            self.assertEqual(report['changed_preparation']['cache']['implementation_context_id'],
                             prepared.cr.digest(prepared.artifact_stamp()))
            self.assertFalse(report['patient_rows_or_identifiers_included'])
            self.assertFalse(report['clinical_mapping_verified'])
            self.assertNotIn('bindings', report)
            self.assertNotIn('anchors', report)
            if name == 'arterial':
                old = json.loads((sessions.p.ei.ROOT / 'verification/live-pressure-demo-report.json').read_text())
                self.assertEqual(report['original_metrics'], old['metrics'])
                self.assertEqual(report['session_context_id'], old['session_context_id'])
                self.assertEqual(report['original_query_context_id'], old['query_context_id'])

    def test_changed_review_clears_both_caches(self):
        s = self.service(); first = self.run_service(s, DEFAULT)
        parent, declaration = s._inputs('synthetic'); declaration['reviewer'] = 'changed'
        with patch.object(s, '_inputs', return_value=(parent, declaration)):
            failed = self.run_service(s, {**DEFAULT, 'threshold': '59'})
        self.assertEqual(failed['status'], 'FAILED')
        self.assertIsNone(s.prepared_executor)
        self.assertEqual(len(s.result_cache.entries), 0)
        self.assertEqual(s.get(first['id'])['summary'], first['summary'])


if __name__ == '__main__': unittest.main()
