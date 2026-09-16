"""Opt-in authored mapping integration using the existing local pressure job interface."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pressure import PressureService, SOURCE
from pressure_cache import implementation_stamp
from patterns import mapped_pressure_session as mapped

FILES = ('demo/mapped_pressure.py', 'demo/serve_mapped_pressure.py')


def stamp():
    return {**implementation_stamp(), **{f:mapped.ei.digest((SOURCE/f).read_text()) for f in FILES}}


class MappedPressureService(PressureService):
    """Authored synthetic source/review only; HTTP callers cannot choose source paths."""
    def __init__(self, mapping_dir=None, *, prepared=True):
        super().__init__(synthetic=True, prepared=prepared)
        self.mapping_dir = Path(mapping_dir or SOURCE/'examples/measurement-source-catalogue').resolve()
        self.implementation = stamp()

    def metadata(self):
        value = super().metadata()
        value['strata'] = {'synthetic':'Authored reviewed measurement category · separate source item'}
        value['interpretation'] = 'Authored synthetic mapping and source review. Recorded segment starts, all eligible baselines and optional same-item follow-ups. Clinical interpretation unverified.'
        value['measurement_mapping'] = 'explicit_review_required'
        return value

    def _check_implementation(self):
        if stamp() != self.implementation: raise ValueError('IMPLEMENTATION_CHANGED_RESTART_SERVER')

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
        if self.use_prepared: self.prepared_executor = mapped.BatchExecutor(self.folder, session._pack)
        self._job_executor = self.prepared_executor
        self.session = session; self.session_name = name
        return session

    def close(self):
        super().close()
        if self.prepared_executor: self.prepared_executor.clear()
        self.result_cache.clear(); self.session = None; self.session_name = None
