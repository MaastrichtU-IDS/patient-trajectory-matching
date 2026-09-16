"""Local-only pressure jobs. Sources are configured at startup, never by HTTP clients."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import threading
import time
import uuid

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT.parent
STRATA={'arterial':'Arterial Blood Pressure mean · 220052','noninvasive':'Non Invasive Blood Pressure mean · 220181','art':'ART BP Mean · 225312'}


class PressureService:
    def __init__(self, folder=None, synthetic=False):
        self.folder=SOURCE/'examples/source-mixed-query' if synthetic else folder
        self.synthetic=synthetic; self.lock=threading.RLock(); self.pool=ThreadPoolExecutor(max_workers=1)
        self.jobs={}; self.active=None; self.session=None; self.session_name=None

    def metadata(self):
        return {'configured':self.folder is not None,'source_mode':'synthetic' if self.synthetic else 'public-demo' if self.folder else 'unconfigured',
            'strata':{'synthetic':'Authored synthetic pressure records'} if self.synthetic else STRATA,
            'defaults':{'threshold':'65','baseline_minutes':30,'followup_minutes':120},
            'limits':{'baseline_minutes':[1,30],'followup_minutes':[0,120],'threshold':['0','300']},
            'interpretation':'Recorded segment starts; all eligible baselines and optional same-item follow-ups. Clinical interpretation unverified.'}

    def _inputs(self,name):
        request=SOURCE/('examples/indexed-source-windows/synthetic-request.json' if self.synthetic else f'examples/indexed-source-windows/demo-{name}-request.json')
        declaration=ROOT/'pressure-synthetic-review.json' if self.synthetic else SOURCE/f'data/{name}-source-fidelity-review.json'
        if request.stat().st_size>256*1024 or declaration.stat().st_size>256*1024:
            raise ValueError('Review/request input size limit')
        return json.loads(request.read_text()),json.loads(declaration.read_text())

    def _check_inputs(self,name,session):
        request,declaration=self._inputs(name)
        if request!=session.context['parent_request'] or declaration!=session.declaration:
            raise ValueError('REVIEW_CHANGED_RESTART_SESSION')

    def _prepare(self,name,progress):
        from patterns.reviewed_pressure_session import Session, fingerprint
        from patterns.verify_indexed_source_windows import validate_summary
        if self.session is not None and self.session_name==name:
            self._check_inputs(name,self.session)
            self.session.check_current(); return self.session
        request,declaration=self._inputs(name)
        if not self.synthetic:
            expected=json.loads((SOURCE/'data/clinical-source-demo-pin.json').read_text())['files']
            if fingerprint(self.folder)!={table:entry['file_sha256'] for table,entry in expected.items()}:
                raise ValueError('PUBLIC_DEMO_SOURCE_PIN_MISMATCH')
        self.session=None; self.session_name=None
        session=Session(self.folder,request,declaration,progress)
        if not self.synthetic: validate_summary(session.selection['summary'],'demo-'+name)
        self.session=session; self.session_name=name
        return session

    def start(self,request):
        from patterns.reviewed_pressure_session import controls
        if self.folder is None: raise ValueError('Configure --mimic-dir or explicitly use --pressure-synthetic, then restart this server.')
        if not isinstance(request,dict) or set(request)!={'stratum','controls'}: raise ValueError('INVALID_PRESSURE_REQUEST')
        if not isinstance(request['stratum'],str) or request['stratum'] not in self.metadata()['strata']: raise ValueError('UNKNOWN_STRATUM')
        controls(request['controls'])
        with self.lock:
            if self.active is not None: raise RuntimeError('A pressure query is already running. Wait for it to finish.')
            jid=uuid.uuid4().hex
            while len(self.jobs)>=3: self.jobs.pop(next(iter(self.jobs)))
            self.jobs[jid]={'id':jid,'status':'RUNNING','request':deepcopy(request),'progress':{'stage':'Preparing reviewed source'},'created':time.time()}
            self.active=jid; self.pool.submit(self._run,jid,deepcopy(request))
            return self.get(jid)

    def _run(self,jid,request):
        start=time.monotonic()
        def progress(**value):
            with self.lock:self.jobs[jid]['progress']=value
        try:
            session=self._prepare(request['stratum'],progress)
            prepared=time.monotonic()
            result=session.execute(request['controls'],progress)
            self._check_inputs(request['stratum'],session)
            with self.lock:
                job=self.jobs[jid]; job['status']=result['status']
                job['timing']={'prepare_seconds':round(prepared-start,3),'execute_seconds':result['elapsed_seconds']}
                if result['status']=='COMPLETED':
                    job['_result']=result; job['_session']=session
                    job['summary']={k:v for k,v in result.items() if k!='details'}
                    job['summary']['source_mode']=self.metadata()['source_mode']
                    job['summary']['stratum']=request['stratum']
                else: job['error']='One or more anchors could not be verified. No cohort count is available.'
        except Exception as error:
            with self.lock:
                self.jobs[jid].update(status='FAILED',error=f'{type(error).__name__}: {error}')
                self.session=None; self.session_name=None
        finally:
            with self.lock:self.active=None

    def get(self,jid):
        with self.lock:
            if jid not in self.jobs: raise KeyError('Unknown or expired pressure job')
            return deepcopy({k:v for k,v in self.jobs[jid].items() if not k.startswith('_')})

    def inspect(self,jid,token):
        with self.lock:
            if jid not in self.jobs: raise KeyError('Unknown or expired pressure job')
            job=self.jobs[jid]
            if job['status']!='COMPLETED': raise ValueError('Evidence is available only for a completed query.')
            return job['_session'].inspect(job['_result'],token)

    def close(self):self.pool.shutdown(wait=True)
