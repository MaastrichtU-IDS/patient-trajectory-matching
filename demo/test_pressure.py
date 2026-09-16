"""Reviewed-window queries, source-backed inspection and local asynchronous HTTP behavior."""
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
import pressure
import serve
from patterns import reviewed_pressure_session as engine

DEFAULT={'threshold':'65','baseline_minutes':30,'followup_minutes':120}


class PressureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        cls.folder=Path(cls.temp.name)/'source';shutil.copytree(engine.p.ei.ROOT/'examples/source-mixed-query',cls.folder)
        cls.request=json.loads((engine.p.ei.ROOT/'examples/indexed-source-windows/synthetic-request.json').read_text())
        cls.declaration=json.loads((pressure.ROOT/'pressure-synthetic-review.json').read_text())
        cls.session=engine.Session(cls.folder,cls.request,cls.declaration)
        cls.result=cls.session.execute(DEFAULT)

    def test_default_query_equals_existing_partition_executor(self):
        s=self.session
        original=engine.p.execute(s.selection,s.planned,s.compiled['compiled_review'])
        observed=[r for d in self.result['details'].values() for r in d['bindings']]
        self.assertEqual(observed,original['bindings'])
        self.assertEqual(self.result['metrics'],{'patients':3,'stays':3,'segments':3,'eligible_pairs':3,'followup_bindings':3,'pairs_without_followup':1})
        self.assertEqual(self.result['anchors_verified'],3);self.assertEqual(len(self.result['roster']),5)

    def test_narrowed_windows_thresholds_and_optional_followup(self):
        for options,patients,followups,missing in [({**DEFAULT,'threshold':'59'},1,2,0),
            ({**DEFAULT,'baseline_minutes':10},1,1,0),({**DEFAULT,'followup_minutes':30},3,2,1),
            ({**DEFAULT,'followup_minutes':0},3,0,3)]:
            result=self.session.execute(options)
            self.assertEqual(result['status'],'COMPLETED')
            self.assertEqual((result['metrics']['patients'],result['metrics']['followup_bindings'],result['metrics']['pairs_without_followup']),(patients,followups,missing))
        self.assertEqual(self.result['metrics']['followup_bindings'],3)

    def test_controls_cannot_expand_windows_change_items_or_inject_rules(self):
        for bad in [{**DEFAULT,'baseline_minutes':31},{**DEFAULT,'followup_minutes':121},{**DEFAULT,'threshold':'NaN'},
            {**DEFAULT,'threshold':'Infinity'},{**DEFAULT,'threshold':'0'},{**DEFAULT,'threshold':65},
            {**DEFAULT,'baseline_minutes':True},{**DEFAULT,'item_id':'999'}, {}, []]:
            with self.subTest(bad=bad),self.assertRaises(ValueError):self.session.query(bad)

    def test_requery_preserves_review_and_parent_context(self):
        before=deepcopy([self.session.context,self.session.review,self.session.compiled])
        self.session.execute({**DEFAULT,'threshold':'75'})
        self.assertEqual(before,[self.session.context,self.session.review,self.session.compiled])
        self.assertEqual(self.session.context['review_sha256'],engine.p.cr.digest(self.session.review))

    def test_inspector_matches_source_decisions_roles_and_missing_followup(self):
        inspected=[self.session.inspect(self.result,t) for t in self.result['details']]
        self.assertTrue(any(any(row[4] is None for row in d['bindings']) for d in inspected))
        for d in inspected:
            self.assertTrue(d['sql_agreement']);self.assertEqual(d['query_context_id'],self.result['context_id'])
            self.assertEqual(d['treatment']['source']['table'],'inputevents')
            self.assertTrue(d['treatment']['decisions'])
            for m in d['measurements']:
                claim=self.session.claims[m['event_id']]
                self.assertEqual(m['value'],claim['source_record']['valuenum'])
                self.assertEqual(m['source'],claim['source_evidence'])
                self.assertEqual(m['decisions'],self.session.decisions[m['claim_id']])
            for witness in d['witnesses'].values():
                self.assertTrue(witness['treatment']['patient_role'])
                self.assertTrue(witness['treatment']['patient_bearer'].endswith('/'+d['anchor']['patient_id']))
        reduced=self.session.execute({**DEFAULT,'threshold':'59'})
        no=next(t for t,d in reduced['details'].items() if not d['bindings'])
        self.assertTrue(any('NOT_BELOW_THRESHOLD' in m['baseline_exclusions'] for m in self.session.inspect(reduced,no)['measurements']))

    def test_changed_original_source_invalidates_cached_session(self):
        path=self.folder/'chartevents.csv';before=path.read_bytes()
        try:
            path.write_bytes(before+b'\n')
            with self.assertRaisesRegex(ValueError,'SOURCE_CHANGED'):self.session.execute(DEFAULT)
        finally:path.write_bytes(before)

    def test_backend_failure_suppresses_all_membership_and_inspection(self):
        with patch.object(engine.p.mixed,'execute',side_effect=ValueError('RESOURCE_LIMIT')):
            result=self.session.execute(DEFAULT)
        self.assertEqual(result['status'],'BLOCKED');self.assertIsNone(result['metrics'])
        self.assertEqual(result['anchors'],[]);self.assertEqual(result['details'],{})
        with self.assertRaises(ValueError):self.session.inspect(result,'anything')

    def test_sql_detects_corrupted_binding_arithmetic(self):
        original=engine.p.source_query.graph_bindings
        def corrupt(result):
            rows=deepcopy(original(result))
            if rows and rows[0][4] is not None:rows[0][5]='999'
            return rows
        with patch.object(engine.p.source_query,'graph_bindings',side_effect=corrupt):result=self.session.execute(DEFAULT)
        self.assertEqual(result['status'],'BLOCKED');self.assertIsNone(result['metrics'])

    def service(self):
        s=pressure.PressureService(synthetic=True);self.addCleanup(s.close);return s

    def wait(self,s,jid):
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            job=s.get(jid)
            if job['status']!='RUNNING':return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_service_progress_busy_gate_failure_and_previous_result_retention(self):
        s=self.service();request={'stratum':'synthetic','controls':DEFAULT}
        first=self.wait(s,s.start(request)['id']);self.assertEqual(first['status'],'COMPLETED')
        with patch.object(s,'_prepare',side_effect=ValueError('STALE_REVIEW')):
            failed=self.wait(s,s.start(request)['id'])
        self.assertEqual(failed['status'],'FAILED');self.assertNotIn('summary',failed)
        self.assertEqual(s.get(first['id'])['summary'],first['summary'])
        entered=threading.Event();release=threading.Event()
        def delayed(*args,**kwargs):entered.set();release.wait(5);return self.result
        with patch.object(s,'_prepare',return_value=self.session),patch.object(self.session,'execute',side_effect=delayed):
            pending=s.start(request)
            try:
                self.assertTrue(entered.wait(5));self.assertNotIn('summary',s.get(pending['id']))
                with self.assertRaises(RuntimeError):s.start(request)
            finally:release.set()
            self.assertEqual(self.wait(s,pending['id'])['status'],'COMPLETED')

    def test_service_bounds_jobs_and_never_returns_internal_snapshots(self):
        s=self.service();ids=[]
        with patch.object(s,'_prepare',return_value=self.session),patch.object(self.session,'execute',return_value=self.result):
            for _ in range(4):
                job=self.wait(s,s.start({'stratum':'synthetic','controls':DEFAULT})['id']);ids.append(job['id'])
                self.assertNotIn('_session',job);self.assertNotIn('_result',job)
                self.assertNotIn('details',job['summary'])
        self.assertEqual(len(s.jobs),3)
        with self.assertRaises(KeyError):s.get(ids[0])

    def http(self,service):
        swap=patch.object(serve,'PRESSURE',service);swap.start();self.addCleanup(swap.stop)
        silent=patch.object(serve.Handler,'log_message');silent.start();self.addCleanup(silent.stop)
        server=serve.ThreadingHTTPServer(('127.0.0.1',0),serve.Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def test_http_live_query_and_context_bound_inspection(self):
        s=self.service();base=self.http(s)
        with urlopen(base+'/pressure') as response:
            page=response.read().decode();self.assertIn('Explore recorded pressure',page);self.assertNotIn('/*PRESSURE_JS*/',page)
        with urlopen(base+'/api/pressure/config') as response:self.assertEqual(json.load(response)['source_mode'],'synthetic')
        data=json.dumps({'stratum':'synthetic','controls':DEFAULT}).encode()
        with urlopen(Request(base+'/api/pressure/jobs',data=data,headers={'Content-Type':'application/json'})) as response:
            self.assertEqual(response.status,202);jid=json.load(response)['id']
        self.wait(s,jid)
        with urlopen(base+'/api/pressure/jobs/'+jid) as response:job=json.load(response)
        token=job['summary']['anchors'][0]['token']
        with urlopen(base+'/api/pressure/jobs/'+jid+'/anchors/'+token) as response:detail=json.load(response)
        self.assertEqual(detail['query_context_id'],job['summary']['context_id']);self.assertTrue(detail['sql_agreement'])
        with self.assertRaises(HTTPError) as caught:urlopen(base+'/api/pressure/jobs/'+jid+'/anchors/'+'0'*24)
        self.assertEqual(caught.exception.code,409)

    def test_http_rejects_cross_origin_paths_and_unbounded_inputs(self):
        s=self.service();base=self.http(s)
        cases=[Request(base+'/api/pressure/config',headers={'Origin':'https://elsewhere.invalid'}),
            Request(base+'/api/pressure/config',headers={'Host':'elsewhere.invalid'}),
            Request(base+'/api/pressure/config?path=/etc/passwd'),
            Request(base+'/api/pressure/jobs',data=b'{}',headers={'Content-Type':'text/plain'}),
            Request(base+'/api/pressure/jobs',data=b' '*4097,headers={'Content-Type':'application/json'}),
            Request(base+'/api/pressure/jobs',data=b'{',headers={'Content-Type':'application/json'}),
            Request(base+'/api/pressure/jobs',data=json.dumps({'stratum':{},'controls':DEFAULT}).encode(),headers={'Content-Type':'application/json'})]
        for request in cases:
            with self.assertRaises(HTTPError) as caught:urlopen(request)
            self.assertIn(caught.exception.code,(400,403))
        self.assertEqual(s.jobs,{})

    def test_unconfigured_and_unpinned_sources_cannot_start_queries(self):
        s=pressure.PressureService();self.addCleanup(s.close)
        self.assertFalse(s.metadata()['configured'])
        with self.assertRaisesRegex(ValueError,'Configure'):s.start({'stratum':'arterial','controls':DEFAULT})
        unpinned=pressure.PressureService(self.folder);self.addCleanup(unpinned.close)
        with patch.object(engine,'Session',side_effect=AssertionError('Reject before source preparation')):
            failed=self.wait(unpinned,unpinned.start({'stratum':'arterial','controls':DEFAULT})['id'])
        self.assertEqual(failed['status'],'FAILED');self.assertIn('PUBLIC_DEMO_SOURCE_PIN_MISMATCH',failed['error'])

    def test_review_changes_invalidate_cached_and_inflight_queries(self):
        s=self.service();request={'stratum':'synthetic','controls':DEFAULT}
        first=self.wait(s,s.start(request)['id']);self.assertEqual(first['status'],'COMPLETED')
        parent,declaration=s._inputs('synthetic');changed=deepcopy(declaration);changed['reviewer']='Changed declaration'
        with patch.object(s,'_inputs',return_value=(parent,changed)):
            failed=self.wait(s,s.start(request)['id'])
        self.assertEqual(failed['status'],'FAILED');self.assertIn('REVIEW_CHANGED',failed['error'])
        entered=threading.Event();release=threading.Event()
        def delayed(*args,**kwargs):entered.set();release.wait(5);return self.result
        with patch.object(s,'_prepare',return_value=self.session),patch.object(self.session,'execute',side_effect=delayed):
            job=s.start(request)
            self.assertTrue(entered.wait(5))
            with patch.object(s,'_inputs',return_value=(parent,changed)):
                release.set();failed=self.wait(s,job['id'])
            self.assertEqual(failed['status'],'FAILED');self.assertNotIn('summary',failed)
        self.assertEqual(s.get(first['id'])['summary'],first['summary'])

    def test_committed_live_public_demo_report_provenance(self):
        report=json.loads((engine.p.ei.ROOT/'verification/live-pressure-demo-report.json').read_text())
        self.assertEqual(report['session_context_id'],engine.p.cr.digest(report['session_context']))
        self.assertEqual(report['query_context_id'],engine.p.cr.digest(report['query_context']))
        for path,digest in report['session_context']['artifacts'].items():
            self.assertEqual(digest,engine.p.ei.digest((engine.p.ei.ROOT/path).read_text()),path)
        self.assertEqual(report['metrics'],{'patients':13,'stays':15,'segments':66,'eligible_pairs':86,'followup_bindings':340,'pairs_without_followup':1})
        self.assertEqual(report['anchors_verified'],944);self.assertEqual(report['stays_retained'],140)
        self.assertTrue(all(report['http_inspector_cases'].values()))
        self.assertFalse(report['patient_rows_or_identifiers_included']);self.assertFalse(report['clinical_mapping_verified'])
        self.assertNotIn('anchors',report);self.assertNotIn('bindings',report)


if __name__=='__main__':unittest.main()
