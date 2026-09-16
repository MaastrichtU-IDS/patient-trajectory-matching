"""Prepared query equivalence, immutable snapshots and fail-closed reuse."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from . import prepared_measurement_session as p, verify_prepared_measurement_session as verify


class PreparedMeasurementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.values, cls.prepared = verify.load_example()

    def setUp(self):
        self.args = deepcopy(verify.arguments(self.values, self.prepared, 0))
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for path in verify.EXAMPLE.glob('*.csv'): shutil.copy(path, self.folder/path.name)

    def session(self, **kwargs):
        result = p.Session(self.folder, *self.args, **kwargs)
        self.addCleanup(result.close); return result

    def reference(self, query): return p.source.execute(self.folder, *self.args[:-1], query)

    def test_cold_admission_uses_real_audit_mapping_and_two_separate_strata(self):
        with patch.object(p.source, 'audit_store', wraps=p.source.audit_store) as audit, \
             patch.object(p.source.mappings, 'plan', wraps=p.source.mappings.plan) as plan, \
             patch.object(p.source.mappings.mixed, 'execute', wraps=p.source.mappings.mixed.execute) as mixed:
            session = self.session()
        self.assertEqual((audit.call_count, plan.call_count, mixed.call_count), (1,1,2))
        result = session.execute(self.args[-1])['result']
        self.assertEqual(result, self.reference(self.args[-1]))
        self.assertEqual([x['item_id'] for x in result['query_result']['strata']], ['2000','2001'])

    def test_warm_queries_do_not_reparse_audit_classify_or_compile(self):
        session = self.session(); expected = [self.reference(q) for q in verify.queries(self.args[-1])]
        with patch.object(p.source, 'audit_store', side_effect=AssertionError('reaudit')), \
             patch.object(p.source.mappings, 'plan', side_effect=AssertionError('replan')), \
             patch.object(p.source.mappings.mixed, 'execute', side_effect=AssertionError('fresh query')), \
             patch.object(p.source.mappings.mixed, '_compile', side_effect=AssertionError('compile')), \
             patch.object(p.source.inputs, 'read_observations', side_effect=AssertionError('parse CSV')), \
             patch.object(p.source.inputs.source, 'read_table', side_effect=AssertionError('parse dimension')):
            for q, reference in zip(verify.queries(self.args[-1]), expected):
                self.assertEqual(session.execute(q)['result'], reference)
        self.assertEqual(session.snapshot()['evaluations'], 4)

    def test_inputs_results_and_session_metadata_are_defensive_copies(self):
        query = deepcopy(self.args[-1]); session = self.session(); first = session.execute(query)
        self.args[4]['decisions'] = []; self.args[9]['claims'][0]['bundle']['events'][0]['value_lexical'] = '999'
        returned = session.execute(query)
        returned['result']['source_audit']['checked_claims'] = 999
        returned['result']['query_result']['selection_plan']['supported_item_ids'] = []
        returned['result']['query_result']['strata'][0]['result']['measurement_view']['records'] = []
        metadata = session.snapshot(); metadata['context']['source_files'].clear()
        query['baseline']['value_lexical'] = '300'
        self.assertEqual(session.execute(self.values['source-request']['query']), first)

    def test_pending_and_withdrawn_mappings_cannot_prepare(self):
        original = deepcopy(self.args[4]); self.args[4]['decisions'] = []
        with patch.object(p.source.mappings.semantic, 'check', side_effect=AssertionError('pending reasoning')):
            with self.assertRaisesRegex(ValueError, 'REQUIRES_COMPLETED_QUERY:BLOCKED_MAPPING_REVIEW'): self.session()
        self.args[4] = original; old = original['decisions'][0]
        self.args[4]['decisions'].append({**old, 'id':'withdraw-2000', 'action':'withdraw', 'supersedes':old['id']})
        with self.assertRaisesRegex(ValueError, 'REQUIRES_COMPLETED_QUERY:BLOCKED_MAPPING_REVIEW'): self.session()

    def test_unresolved_backend_cannot_be_cached(self):
        with patch.object(p.source.mappings.semantic, 'check', return_value={'status':'BLOCKED_TIMEOUT'}):
            with self.assertRaisesRegex(ValueError, 'REQUIRES_COMPLETED_QUERY:BLOCKED_MEASUREMENT_MAPPING_SEMANTICS'): self.session()

    def test_changed_source_invalidates_before_evaluation_even_if_later_restored(self):
        session = self.session(); path = self.folder/'chartevents.csv'; original = path.read_bytes()
        path.write_bytes(original+b'\n')
        with patch.object(p.prepared, '_evaluate', side_effect=AssertionError('changed source')):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED_RESTART_SESSION'): session.execute(self.args[-1])
        path.write_bytes(original)
        self.assertTrue(session.snapshot()['invalidated'])
        with self.assertRaisesRegex(ValueError, 'SESSION_INVALIDATED'): session.execute(self.args[-1])

    def test_changed_source_during_query_never_returns_a_success(self):
        session = self.session(); original = p.prepared._evaluate
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            with (self.folder/'d_items.csv').open('a') as handle: handle.write('\n')
            return result
        with patch.object(p.prepared, '_evaluate', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED_RESTART_SESSION'): session.execute(self.args[-1])
        self.assertTrue(session.snapshot()['invalidated'])

    def test_source_change_during_preparation_rejected(self):
        original = p.source.execute
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            with (self.folder/'icustays.csv').open('a') as handle: handle.write('\n')
            return result
        with patch.object(p.source, 'execute', side_effect=changed):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED_DURING_PREPARATION'): self.session()

    def test_implementation_change_during_preparation_rejected(self):
        with patch.object(p, 'artifact_stamp', side_effect=[p.artifact_stamp(), {'changed':'implementation'}]):
            with self.assertRaisesRegex(ValueError, 'IMPLEMENTATION_CHANGED_DURING_PREPARATION'): self.session()

    def test_implementation_changes_invalidate_before_or_after_query(self):
        for during in (False, True):
            session = self.session(); stamp = p.artifact_stamp()
            with patch.object(p, 'artifact_stamp', side_effect=[stamp, {}] if during else [{}]):
                with self.assertRaisesRegex(ValueError, 'IMPLEMENTATION_CHANGED_RESTART_SESSION'): session.execute(self.args[-1])
            self.assertTrue(session.snapshot()['invalidated'])

    def test_prepared_serialization_budget_is_bounded(self):
        for budget in (0, -1, True, '1024', p.MAX_PREPARED_BYTES+1):
            with self.assertRaisesRegex(ValueError, 'INVALID_PREPARED_MEASUREMENT_BUDGET'):
                self.session(max_prepared_bytes=budget)
        with self.assertRaisesRegex(ValueError, 'PREPARED_MEASUREMENT_BUDGET_EXCEEDED'):
            self.session(max_prepared_bytes=1)
        session = self.session(); context = session.snapshot()['context']
        self.assertLessEqual(context['serialized_prepared_bytes'], context['max_serialized_prepared_bytes'])

    def test_treatment_id_and_class_require_a_new_session(self):
        session = self.session()
        for key, value in [('id','changed'), ('treatment_class_iri','https://example.org/changed')]:
            query = deepcopy(self.args[-1]); query[key] = value
            with self.assertRaisesRegex(ValueError, 'TREATMENT_SCOPE_CHANGED'): session.execute(query)
        self.assertFalse(session.snapshot()['invalidated'])
        self.assertEqual(session.execute(self.args[-1])['result'], self.reference(self.args[-1]))

    def test_item_unit_changes_rejected_but_item_order_may_vary(self):
        session = self.session()
        for side in ('baseline','followup'):
            for key,value in [('item_ids',['2000']), ('unit_lexical','mm[Hg]')]:
                query = deepcopy(self.args[-1]); query[side][key] = value
                with self.assertRaisesRegex(ValueError, 'ITEM_UNIT_SCOPE_CHANGED'): session.execute(query)
        query = deepcopy(self.args[-1]); query['baseline']['item_ids'].reverse()
        self.assertEqual(session.execute(query)['result'], self.reference(query))

    def test_invalid_controls_do_not_poison_a_valid_session(self):
        session = self.session()
        for change in [lambda q:q.update(extra=True), lambda q:q['baseline'].update(value_lexical='NaN'),
                       lambda q:q['followup'].update(max_after_anchor_us=-1)]:
            query = deepcopy(self.args[-1]); change(query)
            with self.assertRaises(ValueError): session.execute(query)
        self.assertEqual(session.snapshot()['evaluations'], 0)
        self.assertFalse(session.snapshot()['invalidated'])
        self.assertEqual(session.execute(self.args[-1])['result'], self.reference(self.args[-1]))

    def test_missing_alignment_stays_incomparable(self):
        self.args[-2]['bindings'] = []; session = self.session()
        result = session.execute(self.args[-1])['result']; self.assertEqual(result, self.reference(self.args[-1]))
        self.assertEqual(result['query_result']['certain_patient_ids'], [])
        bindings = result['query_result']['strata'][0]['result']['baseline_bindings']
        self.assertEqual(bindings[0]['eligibility']['status'], 'INCOMPARABLE')

    def test_empty_source_acceptance_remains_empty(self):
        self.args[10]['decisions'] = []; session = self.session()
        result = session.execute(self.args[-1])['result']; self.assertEqual(result, self.reference(self.args[-1]))
        self.assertEqual(result['query_result']['certain_patient_ids'], [])
        self.assertEqual(result['source_audit']['checked_claims'], 3)

    def test_uncertain_treatment_preserves_possible_certain_and_optional_followup(self):
        store, policy = self.args[6], self.args[7]
        store['claims'][0]['bundle']['variables'][0]['local_lower'] = '2150-01-01 09:30:00'
        policy['store_sha256'] = p.cr.digest(store)
        claims = {c['id']:c for c in store['claims']}
        for decision in policy['decisions']:
            decision['claim_sha256'] = p.cr.digest(claims[decision['claim_id']])
            decision['reason'] = 'Explicit authored uncertain treatment fixture'
        self.args[-2]['interval_store_sha256'] = p.cr.digest(store)
        session = self.session()
        result = session.execute(self.args[-1])['result']; self.assertEqual(result, self.reference(self.args[-1]))
        self.assertEqual(result['query_result']['certain_patient_ids'], [])
        self.assertEqual(result['query_result']['possible_patient_ids'], ['1'])
        query = deepcopy(self.args[-1]); query['followup']['max_after_anchor_us'] = 0
        actual = session.execute(query)['result']; self.assertEqual(actual, self.reference(query))
        self.assertEqual(actual['query_result']['possible_patient_ids'], ['1'])

    def test_partial_stratum_failure_withholds_memberships_and_closes_session(self):
        session = self.session(); original = p.prepared._evaluate; count = 0
        def fail_second(*args, **kwargs):
            nonlocal count
            count += 1
            return original(*args, **kwargs) if count == 1 else {'status':'BLOCKED_MIXED_SOURCE'}
        with patch.object(p.prepared, '_evaluate', side_effect=fail_second): result = session.execute(self.args[-1])
        self.assertEqual(result['status'], 'BLOCKED_INCOMPLETE_MEASUREMENT_STRATA')
        self.assertIsNone(result['result']['query_result']['certain_patient_ids'])
        self.assertFalse(result['result']['query_result']['search_complete_over_requested_strata'])
        self.assertEqual(len(result['result']['query_result']['strata']), 2)
        self.assertTrue(session.snapshot()['invalidated'])
        with self.assertRaisesRegex(ValueError, 'SESSION_INVALIDATED'): session.execute(self.args[-1])

    def test_closed_session_cannot_be_reused(self):
        session = self.session(); session.close()
        self.assertTrue(session.snapshot()['invalidated'])
        with self.assertRaisesRegex(ValueError, 'SESSION_INVALIDATED'): session.execute(self.args[-1])

    def test_committed_equivalence_operation_counts_and_sql_report_reproduce(self):
        report = verify.verify()
        self.assertEqual(report, json.loads((p.ei.ROOT/'verification/prepared-measurement-session-report.json').read_text()))
        self.assertEqual(report['warm_queries'], 12)
        self.assertEqual(set(report['warm_operation_counts'].values()), {0})


if __name__ == '__main__': unittest.main()
