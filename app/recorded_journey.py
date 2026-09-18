"""Guided access to the existing reviewed recorded-pressure services.

Profiles retain their own source population, bounded job history, review and query
contexts. Descriptive pre-index ranking preserves the existing temporal job.
"""
from copy import deepcopy
import re
import threading

PROFILE = 'recorded-patient-journey-1.0'
JOB_ID = re.compile(r'[0-9a-f]{32}\Z')
ANCHOR_ID = re.compile(r'[0-9a-f]{24}\Z')


class RecordedJourneyWorkspace:
    def __init__(self, config=None, *, literal_service=None, mapped_service=None):
        if config is not None and (literal_service is not None or mapped_service is not None):
            raise ValueError('Choose a startup configuration or injected services')
        self.config = config
        self._services = None
        self._injected = {'literal': literal_service, 'reviewed': mapped_service}
        self._lock = threading.RLock()
        self._closed = False
        if config is not None:
            self._ensure_services()

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
            'history': 'Up to three jobs per profile are retained in this server process.',
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
        job = service.start({'stratum': request['stratum'], 'controls': deepcopy(request['controls'])})
        return {**job, 'profile': request['profile']}

    def get(self, profile, job_id):
        self._identifier(job_id, JOB_ID, 'job')
        return {**self._service(profile).get(job_id), 'profile': profile}

    def inspect(self, profile, job_id, token):
        self._identifier(job_id, JOB_ID, 'job')
        self._identifier(token, ANCHOR_ID, 'anchor')
        detail = self._service(profile).inspect(job_id, token)
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

    def references(self, profile, job_id):
        from app.recorded_similarity import references
        return references(self.snapshot(profile, job_id))

    def compare(self, request):
        from app.recorded_similarity import compare
        if not isinstance(request, dict) or set(request) != {'profile', 'job_id', 'reference_token', 'top_k'}:
            raise ValueError('Comparison fields must be profile, job_id, reference_token, top_k')
        self._identifier(request['reference_token'], ANCHOR_ID, 'anchor')
        return compare(self.snapshot(request['profile'], request['job_id']),
                       request['reference_token'], request['top_k'])

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
        for service in services:
            service.close()
