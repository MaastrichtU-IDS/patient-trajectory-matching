"""Live mapped pressure batches, review lifecycle and HTTP evidence checks."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parent))
import mapped_pressure as service_module
from mapped_pressure import MappedPressureService
from benchmark_mapped_pressure import benchmark, artifacts, comparable, DEFAULT, CHANGED
from patterns import mapped_pressure_session as mapped


class MappedPressureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.pack = self.root/'pack'; self.pack.mkdir()
        self.sources = self.root/'source'; self.sources.mkdir()
        for path in (mapped.ei.ROOT/'examples/measurement-source-catalogue').glob('*.json'): shutil.copy(path,self.pack/path.name)
        for path in (mapped.ei.ROOT/'examples/source-mixed-query').glob('*.csv'): shutil.copy(path,self.sources/path.name)

    def service(self, **kwargs):
        service = MappedPressureService(self.pack,**kwargs); service.folder = self.sources
        self.addCleanup(service.close); return service

    def run_job(self, service, controls=DEFAULT):
        job = service.start({'stratum':'synthetic','controls':controls})
        deadline = time.monotonic()+90
        while job['status']=='RUNNING' and time.monotonic()<deadline:
            time.sleep(.01); job = service.get(job['id'])
        self.assertNotEqual(job['status'],'RUNNING'); return job

    def edit_review(self, change):
        path = self.pack/'review.json'; value = json.loads(path.read_text()); change(value)
        path.write_text(json.dumps(value))

    def test_prepared_and_fresh_mapped_service_results_and_inspections_agree(self):
        service = self.service(); cold = self.run_job(service)
        self.assertEqual(cold['status'],'COMPLETED')
        self.assertEqual(cold['execution']['batch_preparation']['fresh_batches'],3)
        changed = self.run_job(service,CHANGED)
        self.assertEqual(changed['execution']['batch_preparation']['reused_batches'],3)
        self.assertEqual(changed['execution']['batch_preparation']['fresh_batches'],0)
        actual = service.jobs[changed['id']]['_result']; reference = service.session.execute(CHANGED)
        self.assertEqual(comparable(actual),comparable(reference))
        for token in actual['details']:
            self.assertEqual(service.inspect(changed['id'],token),service.session.inspect(reference,token))
        repeated = self.run_job(service,CHANGED)
        self.assertEqual(repeated['execution']['mode'],'cached_complete_result')
        self.assertEqual(repeated['execution']['batch_preparation']['reused_batches'],0)
        self.assertEqual(changed['summary']['measurement_mapping']['selected_item_ids'],['2000'])

    def test_pending_review_cannot_fall_back_to_literal_queries(self):
        self.edit_review(lambda r:r.update(decisions=[])); service = self.service()
        with patch.object(mapped.source.mappings.semantic,'check',side_effect=AssertionError('pending backend')):
            result = self.run_job(service)
        self.assertEqual(result['status'],'FAILED'); self.assertIn('BLOCKED_MAPPING_REVIEW',result['error'])
        self.assertNotIn('summary',result); self.assertIsNone(service.session)

    def test_withdrawn_mapping_cannot_prepare(self):
        def withdraw(review):
            old = review['decisions'][0]
            review['decisions'].append({**old,'id':'withdraw','action':'withdraw','supersedes':old['id']})
        self.edit_review(withdraw); result = self.run_job(self.service())
        self.assertEqual(result['status'],'FAILED'); self.assertIn('BLOCKED_MAPPING_REVIEW',result['error'])

    def test_mapping_change_blocks_complete_cache_and_closes_old_preparation(self):
        service = self.service(); old = self.run_job(service); executor = service.prepared_executor
        sessions = [v[0] for v in executor.entries.values()]
        self.edit_review(lambda r:r.update(decisions=[]))
        result = self.run_job(service)
        self.assertEqual(result['status'],'FAILED'); self.assertIn('MAPPING_REVIEW_CHANGED',result['error'])
        self.assertNotIn('summary',result); self.assertEqual(len(service.result_cache.entries),0)
        self.assertIsNone(service.prepared_executor); self.assertTrue(all(s.snapshot()['invalidated'] for s in sessions))
        self.assertEqual(service.get(old['id'])['summary'],old['summary'])

    def test_source_review_change_blocks_cached_result(self):
        service = self.service(); self.run_job(service)
        request,declaration = service._inputs('synthetic'); declaration['reviewer']='Changed review context'
        with patch.object(service,'_inputs',return_value=(request,declaration)): result = self.run_job(service)
        self.assertEqual(result['status'],'FAILED'); self.assertIn('REVIEW_CHANGED',result['error'])
        self.assertEqual(len(service.result_cache.entries),0); self.assertIsNone(service.prepared_executor)

    def test_mapping_change_during_evaluation_suppresses_membership(self):
        service = self.service(); self.run_job(service); executor = service.prepared_executor
        sessions = [v[0] for v in executor.entries.values()]; evaluate = mapped.prepared.prepared._evaluate
        def change(*args,**kwargs):
            result = evaluate(*args,**kwargs)
            self.edit_review(lambda r:r['decisions'][0].update(reason='Changed during query'))
            return result
        with patch.object(mapped.prepared.prepared,'_evaluate',side_effect=change): result = self.run_job(service,CHANGED)
        self.assertEqual(result['status'],'FAILED'); self.assertNotIn('summary',result)
        self.assertTrue(all(s.snapshot()['invalidated'] for s in sessions))

    def test_source_change_during_complete_cache_lookup_is_detected(self):
        service = self.service(); self.run_job(service); lookup = service.result_cache.get
        def change(key):
            result = lookup(key)
            with (self.sources/'chartevents.csv').open('a') as handle: handle.write('\n')
            return result
        with patch.object(service.result_cache,'get',side_effect=change): result = self.run_job(service)
        self.assertEqual(result['status'],'FAILED'); self.assertIn('SOURCE_CHANGED',result['error'])
        self.assertNotIn('summary',result); self.assertEqual(len(service.result_cache.entries),0)

    def test_sql_detects_corrupted_prepared_deltas_and_clears_both_caches(self):
        service = self.service(); self.run_job(service); executor = service.prepared_executor
        evaluate = mapped.prepared.prepared._evaluate
        def corrupt(*args,**kwargs):
            result = deepcopy(evaluate(*args,**kwargs))
            for binding in result['baseline_bindings']:
                for followup in binding['followup']:
                    if followup['value_change'] and followup['value_change'].get('delta') is not None:
                        followup['value_change']['delta']='999'
            return result
        with patch.object(mapped.prepared.prepared,'_evaluate',side_effect=corrupt): result = self.run_job(service,CHANGED)
        self.assertEqual(result['status'],'BLOCKED'); self.assertNotIn('summary',result)
        self.assertEqual(len(service.result_cache.entries),0); self.assertEqual(len(executor.entries),0)

    def test_reviewed_window_and_single_stratum_http_contract_cannot_expand(self):
        service = self.service(); self.run_job(service)
        for controls in ({**DEFAULT,'baseline_minutes':31},{**DEFAULT,'followup_minutes':121}):
            with self.assertRaises(ValueError): service.start({'stratum':'synthetic','controls':controls})
            with self.assertRaises(ValueError): service.session.execute(controls)
        for body in ({'stratum':'arterial','controls':DEFAULT},
                     {'stratum':'synthetic','controls':DEFAULT,'item_ids':['2000','2001']},
                     {'stratum':'synthetic','controls':{**DEFAULT,'unit':'mm[Hg]'}}):
            with self.assertRaises(ValueError): service.start(body)

    def test_unsupported_selector_or_unit_cannot_prepare_a_literal_batch(self):
        service = self.service(); plan = mapped.source.mappings.plan
        def unsupported(*args,**kwargs):
            result = plan(*args,**kwargs); result['supported_item_ids']=[]; return result
        with patch.object(mapped.source.mappings,'plan',side_effect=unsupported): result = self.run_job(service)
        self.assertEqual(result['status'],'FAILED'); self.assertIn('ONE_SUPPORTED_ITEM',result['error'])
        path = self.pack/'selector.json'; selector = json.loads(path.read_text()); selector['unit_lexical']='mm[Hg]'; path.write_text(json.dumps(selector))
        result = self.run_job(service)
        self.assertEqual(result['status'],'FAILED'); self.assertIn('UNIT_SCOPE_MISMATCH',result['error'])

    def test_missing_malformed_or_oversized_mapping_file_fails_without_fallback(self):
        service = self.service(); path = self.pack/'review.json'; original = path.read_bytes()
        for payload in (b'{', b'x'*(mapped.cr.MAX_BYTES+1), None):
            if payload is None: path.unlink()
            else: path.write_bytes(payload)
            result = self.run_job(service); self.assertEqual(result['status'],'FAILED'); self.assertNotIn('summary',result)
            path.write_bytes(original)

    def test_preparation_budget_failure_blocks_cohort_and_cache(self):
        service = self.service(); factory = mapped.BatchExecutor
        with patch.object(mapped,'BatchExecutor',side_effect=lambda folder,pack:factory(folder,pack,max_bytes=1)):
            result = self.run_job(service)
        self.assertEqual(result['status'],'BLOCKED'); self.assertNotIn('summary',result)
        self.assertEqual(len(service.result_cache.entries),0); self.assertEqual(len(service.prepared_executor.entries),0)

    def test_lru_eviction_closes_sessions_and_recomputes_verified_batches(self):
        service = self.service(); self.run_job(service); parent = service.session
        executor = mapped.BatchExecutor(self.sources,parent._pack,max_entries=1); self.addCleanup(executor.clear)
        def args(batch):
            return [batch[n] for n in ('interval_store','interval_policy','semantic_policy','measurement_store','measurement_policy','alignment')]+[parent.query(DEFAULT)]
        first,second = [b for b in parent.built if b['measurement_store'] is not None][:2]
        expected = executor.execute(*args(first)); old = next(iter(executor.entries.values()))[0]
        executor.execute(*args(second)); self.assertTrue(old.snapshot()['invalidated'])
        self.assertEqual(executor.execute(*args(first)),expected)
        self.assertEqual(executor.snapshot()['evicted'],2); self.assertEqual(len(executor.entries),1)

    def test_returned_job_and_mapping_evidence_cannot_mutate_saved_results(self):
        service = self.service(); job = self.run_job(service)
        token = job['summary']['anchors'][0]['token']; original = service.inspect(job['id'],token)
        returned = service.inspect(job['id'],token); returned['measurement_mapping'].clear()
        job['summary']['measurement_mapping']['selected_item_ids'].clear()
        self.assertEqual(service.inspect(job['id'],token),original)
        self.assertEqual(service.get(job['id'])['summary']['measurement_mapping']['selected_item_ids'],['2000'])

    def test_implementation_change_blocks_complete_cache_and_closes_preparation(self):
        service = self.service(); self.run_job(service); executor = service.prepared_executor
        old = [v[0] for v in executor.entries.values()]
        with patch.object(service_module,'stamp',return_value={}): result = self.run_job(service)
        self.assertEqual(result['status'],'FAILED'); self.assertNotIn('summary',result)
        self.assertTrue(all(s.snapshot()['invalidated'] for s in old)); self.assertEqual(len(service.result_cache.entries),0)

    def test_real_http_benchmark_and_committed_report_provenance(self):
        actual = benchmark()
        committed = json.loads((mapped.ei.ROOT/'verification/mapped-pressure-synthetic-report.json').read_text())
        for report in (actual,committed):
            self.assertEqual(report['status'],'VERIFIED'); self.assertEqual(report['artifacts'],artifacts())
            self.assertEqual(report['warm_operation_counts'],{'source_audits':0,'mapping_plans':0,'network_compilations':0,'anchor_sql_checks':3})
            self.assertEqual(report['http_anchor_inspections_equal'],3); self.assertEqual(report['stays_retained'],5)
            self.assertTrue(report['all_result_fields_except_duration_equal']); self.assertFalse(report['clinical_mapping_verified'])
            self.assertFalse(report['patient_rows_or_identifiers_included'])
        for field in ('mapping_files','session_context_id','original_metrics','changed_metrics','original_controls','changed_controls'):
            self.assertEqual(actual[field],committed[field])
        output = mapped.ei.ROOT/'verification/mapped-pressure-run/report.json'
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(actual,indent=2)+'\n')


if __name__ == '__main__': unittest.main()
