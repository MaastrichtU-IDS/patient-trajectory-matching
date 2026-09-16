"""Opt-in reviewed mapping integration using the existing local pressure job interface."""
from copy import deepcopy
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pressure import PressureService, SOURCE
from pressure_cache import implementation_stamp
from patterns import mapped_pressure_session as mapped, pressure_service_config as configuration

FILES = ('demo/mapped_pressure.py', 'demo/serve_mapped_pressure.py', 'demo/configured_pressure.js') + configuration.FILES


def stamp():
    return {**implementation_stamp(), **{f:mapped.ei.digest((SOURCE/f).read_text()) for f in FILES}}


class MappedPressureService(PressureService):
    """Fixed startup sources/reviews; HTTP callers cannot choose source paths."""
    def __init__(self, mapping_dir=None, *, config=None, prepared=True):
        if mapping_dir is not None and config is not None: raise ValueError('CHOOSE_CONFIG_OR_MAPPING_DIR')
        snapshot = configuration.load(config) if config is not None else None
        super().__init__(synthetic=True, prepared=prepared)
        self.configuration = snapshot
        self._configuration_invalidated = False
        self.mapping_dir = Path(mapping_dir or SOURCE/'examples/measurement-source-catalogue').resolve()
        if snapshot:
            self.folder = snapshot['paths']['source_dir']
            self.mapping_dir = snapshot['paths']['mapping_dir']
        self.implementation = stamp()

    def metadata(self):
        value = super().metadata()
        value['strata'] = {'synthetic':'Authored reviewed measurement category · separate source item'}
        value['interpretation'] = 'Authored synthetic mapping and source review. Recorded segment starts, all eligible baselines and optional same-item follow-ups. Clinical interpretation unverified.'
        value['measurement_mapping'] = 'explicit_review_required'
        if self.configuration:
            snapshot = self.configuration; defaults = snapshot['defaults']
            value.update(source_mode='configured-records', source_label=snapshot['context']['label'],
                strata={'configured':f"Reviewed measurement item · {snapshot['measurement_item']}"},
                treatment_label=f"Recorded input item {snapshot['treatment_item']} segments",
                defaults=deepcopy(defaults),
                limits={'baseline_minutes':[1,defaults['baseline_minutes']],
                        'followup_minutes':[0,defaults['followup_minutes']], 'threshold':['0','300']},
                interpretation='Supplied recorded pressure trajectories with explicit source and mapping reviews; clinical interpretation unverified.',
                configuration_context_id=snapshot['context_id'])
        return value

    def _inputs(self, name):
        if self.configuration:
            if name != 'configured': raise ValueError('UNKNOWN_CONFIGURED_STRATUM')
            return deepcopy(self.configuration['request']), deepcopy(self.configuration['declaration'])
        return super()._inputs(name)

    def start(self, request):
        if self.configuration and isinstance(request,dict) and 'controls' in request:
            controls = mapped.pressure.controls(request['controls']); parent = self.configuration['defaults']
            if any(controls[k] > parent[k] for k in ('baseline_minutes','followup_minutes')):
                raise ValueError('OUTSIDE_CONFIGURED_REVIEWED_WINDOWS')
        return super().start(request)

    def _check_implementation(self):
        if stamp() != self.implementation: raise ValueError('IMPLEMENTATION_CHANGED_RESTART_SERVER')
        if self.configuration:
            if self._configuration_invalidated: raise ValueError('PRESSURE_CONFIGURATION_CHANGED_RESTART_SERVER')
            try: configuration.check_current(self.configuration)
            except (ValueError, OSError):
                self._configuration_invalidated = True
                raise

    def _check_inputs(self, name, session):
        super()._check_inputs(name, session)
        if mapped.load_pack(self.mapping_dir)[1] != session.context['mapping_files']:
            raise ValueError('MAPPING_REVIEW_CHANGED_RESTART_SESSION')

    def _run(self, jid, request):
        previous = self.prepared_executor
        self._job_executor = previous
        try: super()._run(jid, request)
        finally:
            if self.jobs[jid]['status'] != 'COMPLETED':
                for executor in (previous, self._job_executor):
                    if executor is not None: executor.clear()
            self._job_executor = None

    def _prepare(self, name, progress):
        if self.session is not None and self.session_name == name:
            self._check_inputs(name, self.session); self.session.check_current()
            self._job_executor = self.prepared_executor
            return self.session
        self.result_cache.clear()
        if self.prepared_executor: self.prepared_executor.clear()
        self.prepared_executor = None; self.session = None; self.session_name = None
        request, declaration = self._inputs(name)
        session = mapped.Session(self.folder, request, declaration, self.mapping_dir, progress)
        if self.configuration:
            session.context['service_configuration'] = deepcopy(self.configuration['context'])
            session.id = mapped.cr.digest(session.context)
        if self.use_prepared: self.prepared_executor = mapped.BatchExecutor(self.folder, session._pack)
        self._job_executor = self.prepared_executor
        self.session = session; self.session_name = name
        return session

    def close(self):
        super().close()
        if self.prepared_executor: self.prepared_executor.clear()
        self.result_cache.clear(); self.session = None; self.session_name = None
