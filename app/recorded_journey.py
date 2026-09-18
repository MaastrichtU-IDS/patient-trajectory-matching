"""Guided access to the existing reviewed recorded-pressure services.

Profiles retain their own source population, bounded job history, review and query
contexts. Descriptive pre-index ranking preserves the existing temporal job.
"""
from copy import deepcopy
import re
import threading
import uuid
from pathlib import Path

PROFILE = 'recorded-patient-journey-1.0'
JOB_ID = re.compile(r'[0-9a-f]{32}\Z')
ANCHOR_ID = re.compile(r'[0-9a-f]{24}\Z')


class RecordedJourneyWorkspace:
    def __init__(self, config=None, *, literal_service=None, mapped_service=None, state_dir=None):
        if config is not None and (literal_service is not None or mapped_service is not None):
            raise ValueError('Choose a startup configuration or injected services')
        self.config = config
        self._services = None
        self._injected = {'literal': literal_service, 'reviewed': mapped_service}
        self._lock = threading.RLock()
        self._closed = False
        self.store = None
        if config is not None:
            self._ensure_services()
        if state_dir is not None:
            from app.recorded_store import RecordedStore
            services = self._ensure_services()
            namespace = {'profile': PROFILE, 'configuration': {
                name: {'source_dir': str(Path(service.folder).resolve()),
                       'mapping_dir': str(Path(service.mapping_dir).resolve()) if hasattr(service, 'mapping_dir') else None,
                       'configuration_context_id': service.metadata().get('configuration_context_id'),
                       'source_mode': service.metadata()['source_mode']}
                for name, service in services.items()}}
            try:
                self.store = RecordedStore(Path(state_dir) / 'recorded-jobs.sqlite3', namespace)
                self.store.recover()
            except Exception:
                if self.store:
                    self.store.close()
                for service in services.values():
                    service.close()
                self._closed = True
                raise

    def _ensure_services(self):
        with self._lock:
            if self._closed:
                raise RuntimeError('Recorded journey workspace is closed')
            if self._services is None:
                # Authored mode keeps optional reasoning dependencies out of startup.
                from demo.pressure import PressureService
                from demo.mapped_pressure import MappedPressureService
                if self.config is not None:
                    self._services = {'reviewed': MappedPressureService(config=self.config)}
                else:
                    self._services = {
                        'literal': self._injected['literal'] or PressureService(synthetic=True),
                        'reviewed': self._injected['reviewed'] or MappedPressureService(),
                    }
            return self._services

    def _service(self, profile):
        if not isinstance(profile, str) or profile not in ('literal', 'reviewed'):
            raise ValueError('Unknown recorded journey profile')
        services = self._ensure_services()
        if profile not in services:
            raise ValueError('Recorded journey profile is not configured')
        return services[profile]

    def metadata(self):
        from app.recorded_selectors import describe_selector
        from app.feature_profiles import metadata as feature_metadata
        profiles = {}
        for profile, service in self._ensure_services().items():
            entry = service.metadata()
            entry.update(available=False, selectors={})
            try:
                with service.lock:
                    entry['selectors'] = {
                        stratum: describe_selector(service, stratum, profile)
                        for stratum in entry['strata']
                    }
                entry['available'] = bool(entry['configured'])
            except (ValueError, RuntimeError, ImportError, OSError) as error:
                entry['error'] = f'{type(error).__name__}: {error}'
            profiles[profile] = entry
        return {
            'profile': PROFILE,
            'default_profile': 'reviewed',
            'profiles': profiles,
            'interpretation': 'Recorded treatment segments and all eligible baseline/follow-up pairs. '
                              'Observed differences are not treatment effects.',
            'population_scope': 'Each profile uses its own configured records and roster; '
                                'the T01–T04 pattern demonstration and its similarity ranking do not apply.',
            'history': ('Completed evidence and interrupted intents are retained in the configured local database.'
                        if self.store else 'Up to three jobs per profile are retained in this server process.'),
            'durable': self.store is not None,
            'feature_profiles': feature_metadata(),
            'query_by_example': {
                'feature_profile': 'recorded-preindex-pressure-distance-1.0',
                'top_k_range': [1, 20],
                'reference': 'Choose a treatment anchor with a finite pre-index pressure measurement.',
                'interpretation': 'Unvalidated single-feature distance in mmHg, using charted time only; '
                                  'availability at index is not established. Top-k highlights do not filter the temporal result.',
            },
        }

    def start(self, request):
        if not isinstance(request, dict) or set(request) != {'profile', 'stratum', 'controls'}:
            raise ValueError('Recorded request fields must be profile, stratum, controls')
        service = self._service(request['profile'])
        if not isinstance(request['stratum'], str) or request['stratum'] not in service.metadata()['strata']:
            raise ValueError('Unknown recorded measurement stratum')
        # Admission comes from the existing service's exact input/review contract.
        # In particular, clients cannot submit files, mappings or arbitrary IRIs.
        if self.store is None:
            job = service.start({'stratum': request['stratum'], 'controls': deepcopy(request['controls'])})
            return {**job, 'profile': request['profile']}
        with self._lock:
            key = uuid.uuid4().hex
            self.store.record_started(key, deepcopy(request))
            return self._start_durable(key, request)

    def _start_durable(self, key, request):
        service = self._service(request['profile'])
        try:
            job = service.start({'stratum': request['stratum'], 'controls': deepcopy(request['controls'])})
            self.store.record_running(key, job['id'])
            # The same one-worker queue saves immediately after execution, before
            # any subsequent query can replace a retained in-memory session.
            service.pool.submit(self._persist_finished, request['profile'], key, job['id'])
            return {**job, 'id': key, 'profile': request['profile']}
        except Exception:
            self.store.record_failure(key, 'admission_failed')
            raise

    def _persist_finished(self, profile, key, service_id):
        with self._lock:
            record = self.store.get(key)
            if record['state'] not in ('queued', 'running'):
                return
            service = self._service(profile)
            job = service.get(service_id)
            if job['status'] == 'RUNNING':
                return
            if job['status'] != 'COMPLETED':
                self.store.record_failure(key, 'execution_failed')
                return
            try:
                snapshot = self._live_snapshot(profile, service_id)
                snapshot['job_id'] = key
                self.store.record_complete(key, {**job, 'id': key, 'profile': profile}, snapshot)
            except Exception:
                self.store.record_failure(key, 'retention_failed')

    def _record(self, profile, key):
        record = self.store.get(key)
        if record['request']['profile'] != profile:
            raise KeyError('Recorded job belongs to another profile')
        return record


    def get(self, profile, job_id):
        self._identifier(job_id, JOB_ID, 'job')
        if self.store is None:
            return {**self._service(profile).get(job_id), 'profile': profile}
        with self._lock:
            record = self._record(profile, job_id)
            if record['state'] in ('queued', 'running') and record['service_id']:
                self._persist_finished(profile, job_id, record['service_id'])
                record = self._record(profile, job_id)
            if record['state'] == 'completed':
                return deepcopy(record['job'])
            if record['state'] == 'running':
                return {**self._service(profile).get(record['service_id']), 'id': job_id, 'profile': profile}
            return {'id': job_id, 'profile': profile, 'status': record['state'].upper(),
                    'request': deepcopy(record['request']), 'error': record['error_code'],
                    'progress': {'stage': 'Interrupted; explicitly resume to re-run source admission.'}}


    def inspect(self, profile, job_id, token):
        self._identifier(job_id, JOB_ID, 'job')
        self._identifier(token, ANCHOR_ID, 'anchor')
        if self.store:
            snapshot = self.snapshot(profile, job_id)
            detail = next((d for d in snapshot['details'] if d['anchor']['token'] == token), None)
            if detail is None:
                raise KeyError('Unknown recorded anchor')
        else:
            detail = self._service(profile).inspect(job_id, token)
        return self._observations(profile, detail)

    @staticmethod
    def _observations(profile, detail):
        by_id = {measurement['event_id']: measurement for measurement in detail['measurements']}
        observations = []
        for binding in detail['bindings']:
            baseline_id, followup_id, delta, unit = binding[3:7]
            observations.append({
                'baseline_id': baseline_id,
                'followup_id': followup_id,
                'baseline': deepcopy(by_id[baseline_id]),
                'followup': deepcopy(by_id[followup_id]) if followup_id is not None else None,
                'delta': delta,
                'unit': unit,
                'followup_status': 'RECORDED' if followup_id is not None else 'MISSING_FOLLOWUP',
            })
        return {**detail, 'profile': profile, 'observations': observations,
                'interpretation': 'Each row is one recorded baseline/follow-up binding. '
                                  'Missing follow-up remains explicit; no causal effect is estimated.'}

    def snapshot(self, profile, job_id):
        """Capture retained completed evidence without reopening changed source files."""
        self._identifier(job_id, JOB_ID, 'job')
        if self.store:
            self.get(profile, job_id)
            record = self._record(profile, job_id)
            if record['state'] != 'completed':
                raise ValueError('Comparison and export require a completed recorded query')
            return deepcopy(record['snapshot'])
        return self._live_snapshot(profile, job_id)

    def _live_snapshot(self, profile, job_id):
        service = self._service(profile)
        with service.lock:
            if job_id not in service.jobs:
                raise KeyError('Unknown or expired recorded job')
            job = service.jobs[job_id]
            if job['status'] != 'COMPLETED':
                raise ValueError('Comparison and export require a completed recorded query')
            result, session = job['_result'], job['_session']
            summary = deepcopy(job['summary'])
            summary.pop('elapsed_seconds', None)
            return {'profile': profile, 'job_id': job_id,
                    'request': deepcopy(job['request']), 'source_mode': summary['source_mode'],
                    'source_context': deepcopy(session.context), 'session_context_id': session.id,
                    'query_context': deepcopy(result['context']), 'query_context_id': result['context_id'],
                    'temporal_result': summary,
                    'details': [session.inspect(result, anchor['token']) for anchor in result['anchors']]}

    def references(self, profile, job_id, feature_profile=None):
        from app.recorded_similarity import references
        return references(self.snapshot(profile, job_id), feature_profile)

    def compare(self, request):
        from app.recorded_similarity import compare
        if not isinstance(request, dict) or not {'profile', 'job_id', 'reference_token', 'top_k'} <= set(request) or set(request) - {'profile', 'job_id', 'reference_token', 'top_k', 'feature_profile'}:
            raise ValueError('Comparison fields must be profile, job_id, reference_token, top_k')
        self._identifier(request['reference_token'], ANCHOR_ID, 'anchor')
        return compare(self.snapshot(request['profile'], request['job_id']),
                       request['reference_token'], request['top_k'], request.get('feature_profile'))

    def audit(self, actor, action, job_id=None, status='recorded'):
        if self.store:
            self.store.audit(actor, action, job_id, status)

    def history(self):
        if self.store:
            jobs = [{'id': r['id'], 'profile': r['request']['profile'],
                     'status': r['state'].upper(), 'created_at': r['created_at'],
                     'updated_at': r['updated_at'], 'request': deepcopy(r['request']),
                     'resumable': r['state'] in ('queued', 'interrupted')}
                    for r in self.store.list_jobs()]
        else:
            jobs = []
            for profile, service in self._ensure_services().items():
                with service.lock:
                    jobs.extend({'id': j['id'], 'profile': profile, 'status': j['status'],
                                 'request': deepcopy(j['request']), 'created_at': j.get('created'),
                                 'updated_at': None, 'resumable': False} for j in service.jobs.values())
        return {'schema': 'recorded-job-history-1', 'durable': self.store is not None, 'jobs': jobs}

    def resume(self, request):
        if not isinstance(request, dict) or set(request) != {'job_id'}:
            raise ValueError('Resume requires exactly job_id')
        if self.store is None:
            raise ValueError('Durable recorded storage is not configured')
        self._identifier(request['job_id'], JOB_ID, 'job')
        with self._lock:
            record = self.store.get(request['job_id'])
            if record['state'] not in ('queued', 'interrupted'):
                raise ValueError('Only an interrupted or queued job can be resumed')
            original = record['request']
            if 'pattern' in original:
                return self._execute_pattern(original, key=record['id'])
            return self._start_durable(record['id'], original)

    def _retained_session(self, profile, job_id):
        """Reopen admitted sources only when an archived query is edited."""
        snapshot = self.snapshot(profile, job_id)
        service = self._service(profile)
        if self.store:
            service_id = self._record(profile, job_id)['service_id']
        else:
            service_id = job_id
        if service_id in service.jobs:
            session = service.jobs[service_id]['_session']
            from patterns.reviewed_pressure_session import Session
            Session.check_current(session)
        else:
            session = service._prepare(snapshot['request']['stratum'], lambda **kw: None)
        service._check_implementation()
        service._check_inputs(snapshot['request']['stratum'], session)
        if session.id != snapshot['session_context_id']:
            raise ValueError('Retained query source context differs from current admitted sources')
        return session, snapshot

    def pattern_metadata(self, profile, job_id):
        from app.recorded_pattern import describe
        service = self._service(profile)
        with self._lock, service.lock:
            session, snapshot = self._retained_session(profile, job_id)
            return describe(session, snapshot['query_context']['query'])

    def pattern(self, request):
        if not isinstance(request, dict) or set(request) != {'profile', 'job_id', 'pattern'}:
            raise ValueError('Pattern revision fields must be profile, job_id, pattern')
        self._identifier(request['job_id'], JOB_ID, 'job')
        with self._lock:
            snapshot = self.snapshot(request['profile'], request['job_id'])
            intent = {'profile': request['profile'], 'stratum': snapshot['request']['stratum'],
                      'controls': deepcopy(snapshot['request']['controls']),
                      'pattern': deepcopy(request['pattern']), 'parent_job_id': request['job_id']}
            key = uuid.uuid4().hex
            if self.store:
                self.store.record_started(key, intent, source_context=snapshot['source_context'])
            return self._execute_pattern(intent, key)

    def _execute_pattern(self, request, key):
        from app.recorded_pattern import prepare
        service = self._service(request['profile'])
        profile = request['profile']
        try:
            with service.lock:
                if service.active is not None:
                    raise RuntimeError('A recorded query is already running')
                session, parent = self._retained_session(profile, request['parent_job_id'])
                prepared = prepare(session, request['pattern'], parent_query_context_id=parent['query_context'].get('parent_query_context_id', parent['query_context_id']))
                if self.store:
                    self.store.record_running(key, key)
                result = prepared.execute(request['controls'])
                from patterns.reviewed_pressure_session import Session
                Session.check_current(prepared)
                service._check_implementation()
                service._check_inputs(request['stratum'], prepared)
                summary = {k: deepcopy(v) for k, v in result.items() if k != 'details'}
                summary.update(source_mode=service.metadata()['source_mode'], stratum=request['stratum'])
                job = {'id': key, 'profile': profile, 'status': result['status'],
                       'parent_job_id': request['parent_job_id'],
                       'request': {k: deepcopy(request[k]) for k in ('stratum', 'controls', 'pattern')},
                       'summary': summary, '_result': result, '_session': prepared}
                while len(service.jobs) >= 3:
                    service.jobs.pop(next(iter(service.jobs)))
                service.jobs[key] = job
                if self.store:
                    self._persist_finished(profile, key, key)
                return self.get(profile, key)
        except Exception:
            if self.store:
                record = self.store.get(key)
                if record['state'] in ('queued', 'running', 'interrupted'):
                    self.store.record_failure(key, 'pattern_failed')
            raise

    @staticmethod
    def _identifier(value, pattern, kind):
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError(f'Invalid recorded {kind} identifier')

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            services = list((self._services or {}).values())
        # Workers and their save callbacks must finish before the store closes.
        # _closed is reset while callbacks use _service; no new HTTP work remains.
        self._closed = False
        try:
            for service in services:
                service.close()
        finally:
            self._closed = True
            if self.store:
                self.store.close()
