"""Workload equivalence, observable cache behavior and aggregate report publication."""
from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parent))
import benchmark_configured_pressure as workload

ROOT = workload.ROOT
EXAMPLE = ROOT/'examples/configured-pressure-service'


class ConfiguredWorkloadTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name); shutil.copytree(EXAMPLE,self.root/'config')
        self.config = self.root/'config/config.json'; self.queries = self.root/'config/workload.json'
        self.edit(self.config,lambda value:value.update(source_dir=str(ROOT/'examples/prepared-measurement-session')))

    def edit(self,path,change):
        value = json.loads(path.read_text()); change(value); path.write_text(json.dumps(value)+'\n')

    def inputs(self): return workload.load_inputs(self.config,self.queries,1)

    def invoke(self,output):
        capture = io.StringIO()
        with redirect_stdout(capture):
            code = workload.main(['--config',str(self.config),'--workload',str(self.queries),'--repetitions','1','--output',str(output)])
        return code,json.loads(capture.getvalue())

    def test_workload_limits_and_parent_envelope_reject_before_execution(self):
        original = self.queries.read_bytes()
        for change in [lambda value:value.update(extra=True),lambda value:value.update(queries=[]),
                       lambda value:value.update(queries=value['queries']*5),
                       lambda value:value['queries'][0].update(baseline_minutes=16),
                       lambda value:value['queries'][0].update(unit='kPa'),
                       lambda value:value['queries'][0].update(threshold='NaN'),
                       lambda value:value['queries'][0].update(threshold='301')]:
            self.queries.write_bytes(original); self.edit(self.queries,change)
            with self.assertRaises(ValueError): self.inputs()
        self.queries.write_bytes(original)
        for repetitions in (0,11,True,1.5):
            with self.assertRaisesRegex(ValueError,'INVALID_REPETITIONS'): workload.load_inputs(self.config,self.queries,repetitions)
        self.queries.write_bytes(b' '*(64*1024+1))
        with self.assertRaises(ValueError): self.inputs()

    def test_successful_workload_checks_every_result_and_both_http_inspections(self):
        report = workload.run(self.inputs())
        self.assertEqual(report['status'],'VERIFIED'); self.assertEqual(len(report['trials']),2)
        self.assertEqual(report['stays_retained'],5); self.assertEqual(report['anchors'],3)
        self.assertEqual([row['metrics']['patients'] for row in report['trials']],[1,0])
        for row in report['trials']:
            self.assertEqual(row['http_inspections_equal'],6)
            self.assertEqual(row['prepared_operation_counts'],{'source_audits':0,'mapping_plans':0,'network_compilations':0,'anchor_sql_checks':3})
            self.assertEqual(row['repeat_operation_counts'],{'source_audits':0,'mapping_plans':0,'network_compilations':0,'anchor_sql_checks':0})
            self.assertEqual(row['reused_batches'],1); self.assertEqual(row['fresh_batches'],0)
            self.assertEqual(row['repeat_execution_mode'],'cached_complete_result')
        for summary in report['summary']:
            self.assertEqual(summary['prepared_service']['samples'],1)
            self.assertEqual(summary['cached_service']['samples'],1)
        self.assertFalse(report['patient_rows_or_identifiers_included']); self.assertFalse(report['clinical_mapping_verified'])
        serialized = json.dumps(report)
        for forbidden in (str(self.root),'patient_id','episode_id','reviewer','Authored supplied records'):
            self.assertNotIn(forbidden,serialized)
        self.assertEqual(report['context']['artifacts'],workload.artifacts())

    def test_result_cache_budget_is_reported_as_recomputation(self):
        factory = workload.MappedPressureService
        def small(*args,**kwargs):
            service = factory(*args,**kwargs); service.result_cache.max_bytes = 1; return service
        with patch.object(workload,'MappedPressureService',side_effect=small): report = workload.run(self.inputs())
        for row in report['trials']:
            self.assertEqual(row['repeat_execution_mode'],'fresh_query')
            self.assertEqual(row['repeat_operation_counts']['anchor_sql_checks'],3)
        for summary in report['summary']:
            self.assertEqual(summary['cached_service'],workload.distribution([]))
            self.assertEqual(summary['repeat_recomputations'],1)

    def test_prepared_cache_eviction_reports_real_readmission(self):
        self.edit(self.config,lambda value:value.update(source_dir=str(ROOT/'examples/source-mixed-query'),
            pressure_request=str(ROOT/'examples/indexed-source-windows/synthetic-request.json'),
            source_review=str(ROOT/'demo/pressure-synthetic-review.json'),
            mapping_dir=str(ROOT/'examples/measurement-source-catalogue')))
        self.edit(self.queries,lambda value:value.update(queries=value['queries'][:1]))
        factory = workload.mapped.BatchExecutor
        with patch.object(workload.mapped,'BatchExecutor',side_effect=lambda folder,pack:factory(folder,pack,max_entries=1)):
            report = workload.run(self.inputs())
        row = report['trials'][0]
        self.assertEqual(row['fresh_batches'],3); self.assertEqual(row['reused_batches'],0)
        self.assertEqual(row['prepared_operation_counts']['source_audits'],3)
        self.assertGreater(row['prepared_operation_counts']['network_compilations'],0)
        self.assertGreater(row['prepared_cache']['evicted'],0)
        self.assertTrue(row['all_result_fields_except_duration_equal'])

    def test_changed_workload_cannot_use_previously_loaded_inputs(self):
        inputs = self.inputs(); self.queries.write_bytes(self.queries.read_bytes()+b'\n')
        with self.assertRaises(workload.WorkloadError) as error: workload.run(inputs)
        self.assertEqual(error.exception.code,'WORKLOAD_CHANGED'); self.assertEqual(error.exception.stage,'startup')

    def test_workload_change_during_query_prevents_successful_report(self):
        evaluate = workload.mapped.prepared.prepared._evaluate
        def change(*args,**kwargs):
            result = evaluate(*args,**kwargs)
            self.queries.write_bytes(self.queries.read_bytes()+b'\n'); return result
        with patch.object(workload.mapped.prepared.prepared,'_evaluate',side_effect=change):
            with self.assertRaises(workload.WorkloadError) as error: workload.run(self.inputs())
        self.assertEqual(error.exception.code,'WORKLOAD_CHANGED')

    def test_mapping_change_during_reference_prevents_successful_report(self):
        original = workload.mapped.Session.execute
        def change(session,*args,**kwargs):
            result = original(session,*args,**kwargs)
            if 'batch_executor' not in kwargs:
                path = self.root/'config/mapping/review.json'; path.write_bytes(path.read_bytes()+b'\n')
            return result
        with patch.object(workload.mapped.Session,'execute',new=change):
            with self.assertRaises(workload.WorkloadError): workload.run(self.inputs())

    def test_fresh_result_difference_is_not_reported_as_verified(self):
        original = workload.mapped.Session.execute
        def corrupt(session,*args,**kwargs):
            result = deepcopy(original(session,*args,**kwargs))
            if 'batch_executor' not in kwargs: result['metrics']['patients'] = 99
            return result
        with patch.object(workload.mapped.Session,'execute',new=corrupt):
            with self.assertRaises(workload.WorkloadError) as error: workload.run(self.inputs())
        self.assertEqual(error.exception.code,'FRESH_RESULT_DIFFERENCE'); self.assertEqual(error.exception.stage,'reference')

    def test_http_evidence_difference_is_not_reported_as_verified(self):
        original = workload.MappedPressureService.inspect
        def corrupt(service,*args,**kwargs):
            result = original(service,*args,**kwargs); result['sql_agreement'] = False; return result
        with patch.object(workload.MappedPressureService,'inspect',new=corrupt):
            with self.assertRaises(workload.WorkloadError) as error: workload.run(self.inputs())
        self.assertEqual(error.exception.code,'HTTP_INSPECTION_DIFFERENCE'); self.assertEqual(error.exception.stage,'inspection')

    def test_pending_review_publishes_failure_without_stale_success_or_partial_metrics(self):
        output = self.root/'report.json'; output.write_text('{"status":"VERIFIED","metrics":{"patients":42}}')
        self.edit(self.root/'config/mapping/review.json',lambda value:value.update(decisions=[]))
        previous = workload.serve.PRESSURE
        code,summary = self.invoke(output)
        self.assertEqual(code,1); self.assertEqual(summary['failure']['code'],'WORKLOAD_JOB_INCOMPLETE')
        self.assertIs(workload.serve.PRESSURE,previous)
        report = json.loads(output.read_text()); self.assertEqual(report['status'],'FAILED')
        self.assertNotIn('trials',report); self.assertNotIn('metrics',report)
        self.assertFalse(report['completed_workload_metrics_available'])

    def test_output_cannot_overwrite_inputs_or_write_into_source_and_mapping_directories(self):
        inputs = self.inputs()
        for target in (self.config,self.queries,self.root/'config/request.json',self.root/'config/source-review.json',
                       self.root/'config/mapping/report.json',ROOT/'examples/prepared-measurement-session/new-report.json',
                       ROOT/'schemas/pressure-workload.schema.json'):
            with self.assertRaisesRegex(ValueError,'OUTPUT_OVERWRITES_INPUT'): workload.protect_output(target,inputs)
        link = self.root/'link.json'; link.symlink_to(self.queries)
        with self.assertRaisesRegex(ValueError,'OUTPUT_OVERWRITES_INPUT'): workload.protect_output(link,inputs)
        original = self.queries.read_bytes(); code,_ = self.invoke(self.queries)
        self.assertEqual(code,1); self.assertEqual(self.queries.read_bytes(),original)

    def test_committed_report_has_current_provenance_and_reproducible_fixture_behavior(self):
        committed = json.loads((ROOT/'verification/configured-pressure-workload-report.json').read_text())
        expected = workload.load_inputs(EXAMPLE/'config.json',EXAMPLE/'workload.json',3)
        self.assertEqual(committed['context'],expected['context'])
        self.assertEqual(committed['context_id'],expected['context_id'])
        self.assertEqual(committed['status'],'VERIFIED'); self.assertEqual(len(committed['trials']),6)
        self.assertEqual(committed['source_sha256'],workload.mapped.pressure.fingerprint(ROOT/'examples/prepared-measurement-session'))
        for row in committed['trials']:
            self.assertEqual(row['metrics']['patients'],1 if row['query_index']==0 else 0)
            self.assertEqual(row['http_inspections_equal'],6); self.assertEqual(row['anchors_verified'],3)
        output = ROOT/'verification/configured-pressure-workload-run/report.json'
        with redirect_stdout(io.StringIO()):
            code = workload.main(['--config',str(EXAMPLE/'config.json'),'--workload',str(EXAMPLE/'workload.json'),
                                  '--repetitions','1','--output',str(output)])
        self.assertEqual(code,0)
        actual = json.loads(output.read_text())
        for field in ('session_context_id','source_sha256','stays_retained','anchors','batches','nonempty_measurement_batches'):
            self.assertEqual(actual[field],committed[field])
        self.assertEqual(actual['context']['artifacts'],committed['context']['artifacts'])


if __name__ == '__main__': unittest.main()
