"""Startup configuration, immutable reviews and configured HTTP/UI contract."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
sys.path.insert(0,str(Path(__file__).resolve().parent))
import serve
from serve_mapped_pressure import Handler
from mapped_pressure import MappedPressureService, stamp
from benchmark_mapped_pressure import comparable
from patterns import pressure_service_config as config, mapped_pressure_session as mapped

EXAMPLE = mapped.ei.ROOT/'examples/configured-pressure-service'
DEFAULT = {'threshold':'65','baseline_minutes':15,'followup_minutes':60}
CHANGED = {**DEFAULT,'threshold':'59'}


class ConfiguredPressureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        shutil.copytree(EXAMPLE,self.root/'configuration')
        shutil.copytree(mapped.ei.ROOT/'examples/prepared-measurement-session',self.root/'source')
        self.path = self.root/'configuration/config.json'
        self.edit(self.path,lambda value:value.update(source_dir='../source'))

    def edit(self,path,change):
        value = json.loads(path.read_text()); change(value); path.write_text(json.dumps(value)+'\n')

    def service(self):
        value = MappedPressureService(config=self.path); self.addCleanup(value.close); return value

    def run_job(self,service,controls=DEFAULT):
        job = service.start({'stratum':'configured','controls':controls}); deadline = time.monotonic()+90
        while job['status']=='RUNNING' and time.monotonic()<deadline:
            time.sleep(.01); job = service.get(job['id'])
        self.assertNotEqual(job['status'],'RUNNING'); return job

    def test_relative_paths_and_request_defaults(self):
        value = config.load(self.path)
        self.assertEqual(value['paths']['source_dir'],self.root/'source')
        self.assertEqual(value['defaults'],DEFAULT); self.assertEqual(value['measurement_item'],'2001')
        self.assertEqual(value['treatment_item'],'1000')
        self.edit(self.path,lambda v:v.update(source_dir=str(self.root/'source')))
        self.assertEqual(config.load(self.path)['paths']['source_dir'],self.root/'source')

    def test_closed_configuration_schema_and_size_limit(self):
        original = self.path.read_bytes()
        for change in (lambda v:v.update(extra=True),lambda v:v.pop('source_review'),lambda v:v.update(label='   ')):
            self.path.write_bytes(original); self.edit(self.path,change)
            with self.assertRaisesRegex(ValueError,'INVALID_PRESSURE_SERVICE_CONFIG'): self.service()
        self.path.write_bytes(b' '*(config.MAX_CONFIG_BYTES+1))
        with self.assertRaisesRegex(ValueError,'PRESSURE_CONFIG_INPUT_LIMIT'): self.service()

    def test_missing_ambiguous_and_malformed_inputs_fail_startup(self):
        review = self.path.parent/'source-review.json'; original = review.read_bytes()
        for payload in (None,b'{',b'{}'):
            if payload is None: review.unlink()
            else: review.write_bytes(payload)
            with self.assertRaises((ValueError,OSError)): self.service()
            review.write_bytes(original)
        shutil.copyfile(self.root/'source/chartevents.csv',self.root/'source/chartevents.csv.gz')
        with self.assertRaises(ValueError): self.service()

    def test_unsupported_pressure_units_windows_and_resolution_fail_startup(self):
        request = self.path.parent/'request.json'; original = request.read_bytes()
        changes = [lambda q:q['baseline'].update(unit_lexical='kPa'),
                   lambda q:q['followup'].update(anchor='end'),
                   lambda q:q['baseline'].update(max_before_start_us=60_000_001),
                   lambda q:q['baseline'].update(operator='lte'),
                   lambda q:q['followup'].update(within_interval=True)]
        for change in changes:
            request.write_bytes(original); self.edit(request,lambda v:change(v['query']))
            with self.assertRaises(ValueError): self.service()

    def test_metadata_does_not_expose_paths_or_claim_clinical_identity(self):
        service = self.service(); metadata = service.metadata()
        self.assertEqual(metadata['defaults'],DEFAULT)
        self.assertEqual(metadata['source_mode'],'configured-records')
        self.assertEqual(metadata['limits']['baseline_minutes'],[1,15])
        self.assertEqual(metadata['limits']['followup_minutes'],[0,60])
        self.assertIn('1000',metadata['treatment_label']); self.assertNotIn('norepinephrine',json.dumps(metadata).lower())
        self.assertNotIn(str(self.root),json.dumps(metadata))
        self.assertEqual(set(metadata['strata']),{'configured'})
        metadata['defaults']['threshold']='1'; self.assertEqual(service.metadata()['defaults'],DEFAULT)

    def test_request_cannot_expand_envelope_or_inject_source_or_item(self):
        service = self.service()
        bodies = [{'stratum':'configured','controls':{**DEFAULT,'baseline_minutes':16}},
                  {'stratum':'configured','controls':{**DEFAULT,'followup_minutes':61}},
                  {'stratum':'synthetic','controls':DEFAULT},
                  {'stratum':'configured','controls':DEFAULT,'source_dir':'/tmp'},
                  {'stratum':'configured','controls':{**DEFAULT,'item_ids':['2000']}}]
        for body in bodies:
            with self.assertRaises(ValueError): service.start(body)
        self.assertEqual(service.jobs,{})

    def test_alternative_source_item_prepared_and_fresh_results_agree(self):
        service = self.service(); cold = self.run_job(service)
        self.assertEqual(cold['status'],'COMPLETED',cold.get('error'))
        self.assertEqual(cold['summary']['metrics']['patients'],1)
        self.assertEqual(cold['summary']['measurement_mapping']['selected_item_ids'],['2001'])
        warm = self.run_job(service,CHANGED); self.assertEqual(warm['status'],'COMPLETED',warm.get('error'))
        actual = service.jobs[warm['id']]['_result']; fresh = service.session.execute(CHANGED)
        self.assertEqual(comparable(actual),comparable(fresh))
        self.assertEqual(warm['execution']['batch_preparation']['fresh_batches'],0)
        self.assertGreater(warm['execution']['batch_preparation']['reused_batches'],0)
        for token in actual['details']:
            self.assertEqual(service.inspect(warm['id'],token),service.session.inspect(fresh,token))
        self.assertEqual(service.session.context['service_configuration'],service.configuration['context'])
        cached = self.run_job(service,CHANGED)
        self.assertEqual(cached['execution']['mode'],'cached_complete_result')

    def test_configuration_change_blocks_cache_and_latches_until_restart(self):
        service = self.service(); old = self.run_job(service); self.assertEqual(old['status'],'COMPLETED')
        sessions = [v[0] for v in service.prepared_executor.entries.values()]
        original = self.path.read_bytes(); self.path.write_bytes(original+b'\n')
        failed = self.run_job(service); self.assertEqual(failed['status'],'FAILED')
        self.assertIn('RESTART_SERVER',failed['error']); self.assertNotIn('summary',failed)
        self.assertEqual(len(service.result_cache.entries),0)
        self.assertTrue(all(s.snapshot()['invalidated'] for s in sessions))
        self.path.write_bytes(original)
        self.assertEqual(self.run_job(service)['status'],'FAILED')
        self.assertEqual(self.run_job(self.service())['status'],'COMPLETED')
        self.assertEqual(service.get(old['id'])['summary'],old['summary'])

    def test_every_frozen_review_input_is_checked_even_before_first_job(self):
        paths = [self.path.parent/'request.json',self.path.parent/'source-review.json']
        paths += sorted((self.path.parent/'mapping').glob('*.json'))
        for path in paths:
            service = self.service(); original = path.read_bytes(); path.write_bytes(original+b'\n')
            result = self.run_job(service); path.write_bytes(original)
            self.assertEqual(result['status'],'FAILED',str(path)); self.assertNotIn('summary',result)
            service.close()

    def test_change_during_complete_cache_lookup_withholds_result(self):
        service = self.service(); self.run_job(service); lookup = service.result_cache.get
        def change(key):
            result = lookup(key)
            with (self.path.parent/'source-review.json').open('a') as handle: handle.write('\n')
            return result
        with patch.object(service.result_cache,'get',side_effect=change): failed = self.run_job(service)
        self.assertEqual(failed['status'],'FAILED'); self.assertNotIn('summary',failed)
        self.assertEqual(len(service.result_cache.entries),0); self.assertIsNone(service.prepared_executor)

    def test_change_during_warm_execution_closes_preparation(self):
        service = self.service(); self.run_job(service)
        sessions = [v[0] for v in service.prepared_executor.entries.values()]
        evaluate = mapped.prepared.prepared._evaluate
        def change(*args,**kwargs):
            result = evaluate(*args,**kwargs)
            with self.path.open('a') as handle: handle.write('\n')
            return result
        with patch.object(mapped.prepared.prepared,'_evaluate',side_effect=change): failed = self.run_job(service,CHANGED)
        self.assertEqual(failed['status'],'FAILED'); self.assertNotIn('summary',failed)
        self.assertTrue(all(s.snapshot()['invalidated'] for s in sessions))

    def test_pending_mapping_and_stale_source_review_do_not_gain_acceptance(self):
        review = self.path.parent/'mapping/review.json'; original = review.read_bytes()
        self.edit(review,lambda v:v.update(decisions=[]))
        job = self.run_job(self.service()); self.assertEqual(job['status'],'FAILED'); self.assertIn('BLOCKED_MAPPING_REVIEW',job['error'])
        review.write_bytes(original)
        self.edit(self.path.parent/'source-review.json',lambda v:v.update(package_context_id='0'*64))
        job = self.run_job(self.service()); self.assertEqual(job['status'],'FAILED'); self.assertIn('STALE_FIDELITY_DECLARATION',job['error'])

    def test_real_http_configured_page_job_inspections_and_limits(self):
        service = MappedPressureService(config=EXAMPLE/'config.json'); self.addCleanup(service.close)
        previous = serve.PRESSURE; serve.PRESSURE = service
        server = serve.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        def read(path,body=None,headers=None):
            request = Request(base+path,data=None if body is None else json.dumps(body).encode(),headers=headers or {'Content-Type':'application/json'})
            with urlopen(request,timeout=30) as response:
                data = response.read(); return data.decode() if path=='/pressure' else json.loads(data)
        try:
            self.assertEqual(read('/api/pressure/config')['defaults'],DEFAULT)
            self.assertIn('config.source_label',read('/pressure'))
            for path,body,headers,code in [('/pressure?bad=1',None,None,400),('/pressure',None,{'Origin':'https://example.org'},403),
                    ('/api/pressure/jobs',{'stratum':'configured','controls':{**DEFAULT,'baseline_minutes':16}},None,400)]:
                with self.assertRaises(HTTPError) as error: read(path,body,headers)
                self.assertEqual(error.exception.code,code)
            job = read('/api/pressure/jobs',{'stratum':'configured','controls':DEFAULT}); deadline = time.monotonic()+90
            while job['status']=='RUNNING' and time.monotonic()<deadline:
                time.sleep(.02); job = read('/api/pressure/jobs/'+job['id'])
            self.assertEqual(job['status'],'COMPLETED',job.get('error'))
            reference = service.session.execute(DEFAULT)
            self.assertEqual(comparable(service.jobs[job['id']]['_result']),comparable(reference))
            for token in reference['details']:
                self.assertEqual(read('/api/pressure/jobs/'+job['id']+'/anchors/'+token),service.session.inspect(reference,token))
            self.assertEqual(len(reference['roster']),5)
            self.assertEqual(job['summary']['source_mode'],'configured-records')
            report = {'profile':'configured-pressure-http-verification-1.0','status':'VERIFIED',
                'synthetic_only':True,'clinical_mapping_verified':False,'patient_rows_or_identifiers_included':False,
                'configuration_context':service.configuration['context'], 'session_context_id':service.session.id,
                'controls':DEFAULT, 'selected_item_ids':job['summary']['measurement_mapping']['selected_item_ids'],
                'metrics':reference['metrics'], 'stays_retained':len(reference['roster']),
                'http_anchor_inspections_equal':len(reference['details']), 'all_result_fields_except_duration_equal':True,
                'artifacts':{**stamp(),**{f:mapped.ei.digest((mapped.ei.ROOT/f).read_text()) for f in
                    ('demo/test_configured_pressure.py','demo/test_configured_pressure_ui.cjs')}},
                'visual_browser_inspection_verified':False}
            output = mapped.ei.ROOT/'verification/configured-pressure-run/report.json'
            output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(report,indent=2)+'\n')
        finally:
            server.shutdown(); thread.join(timeout=5); server.server_close(); serve.PRESSURE = previous


if __name__ == '__main__': unittest.main()
