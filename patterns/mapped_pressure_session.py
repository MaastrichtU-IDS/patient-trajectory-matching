"""Reviewed single-item measurement mappings inside the pressure batch/query envelope."""
from collections import OrderedDict
from copy import deepcopy
import json
from pathlib import Path
import time
from . import reviewed_pressure_session as pressure, prepared_measurement_session as prepared

source = prepared.source
ei, cr = prepared.ei, prepared.cr
PROFILE = 'mapped-pressure-session-1.0'
NAMES = ('request', 'catalogue', 'terminology', 'mappings', 'review', 'selector')
FILES = tuple(sorted(set(pressure.FILES + prepared.FILES + ('patterns/mapped_pressure_session.py',))))


def load_pack(folder):
    values, stamps = {}, {}
    for name in NAMES:
        path = Path(folder) / (name + '.json')
        with path.open('rb') as handle: raw = handle.read(cr.MAX_BYTES + 1)
        ei.require(len(raw) <= cr.MAX_BYTES, 'MAPPING_PACK_FILE_LIMIT')
        values[name] = json.loads(raw); stamps[name] = source.inputs.source.digest(raw)
    return values, stamps


def mapped_arguments(pack, args):
    return [pack['request'], *(pack[n] for n in source.mappings.mappings.NAMES), pack['selector'], *args]


def extract(result, item):
    ei.require(result['status'] == 'COMPLETED_REVIEWED_MEASUREMENT_QUERY', 'INCOMPLETE_MAPPED_PRESSURE_BATCH:' + result['status'])
    qr = result['query_result']; strata = qr['strata']
    ei.require(qr['search_complete_over_requested_strata'] and len(strata) == 1 and strata[0]['item_id'] == item,
               'MAPPED_PRESSURE_STRATUM_DISAGREEMENT')
    return strata[0]['result']


class FreshExecutor:
    def __init__(self, folder, pack): self.folder, self.pack = folder, deepcopy(pack)
    def execute(self, *args):
        return extract(source.execute(self.folder, *mapped_arguments(self.pack, args)), self.pack['selector']['source_item_ids'][0])


class BatchExecutor:
    """Bounded LRU of complete source-audited sessions; eviction closes private state."""
    def __init__(self, folder, pack, max_entries=1024, max_bytes=128 * 1024 * 1024):
        ei.require(type(max_entries) is int and 0 < max_entries <= 1024 and
                   type(max_bytes) is int and 0 < max_bytes <= 128 * 1024 * 1024, 'INVALID_MAPPED_PRESSURE_CACHE_LIMIT')
        self.folder, self.pack = folder, deepcopy(pack)
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.entries = OrderedDict(); self.bytes = 0
        self.artifacts = prepared.artifact_stamp()
        self.stats = {'hits':0, 'misses':0, 'admitted':0, 'evicted':0, 'fresh_seconds':0.0, 'reevaluation_seconds':0.0}

    def clear(self):
        for session, _ in self.entries.values(): session.close()
        self.entries.clear(); self.bytes = 0

    def snapshot(self):
        return {**self.stats, 'entries':len(self.entries), 'serialized_bytes':self.bytes,
                'max_entries':self.max_entries, 'max_serialized_bytes':self.max_bytes,
                'implementation_context_id':cr.digest(self.artifacts), 'profile':PROFILE}

    def execute(self, *args):
        try:
            ei.require(prepared.artifact_stamp() == self.artifacts, 'MAPPED_PREPARATION_IMPLEMENTATION_CHANGED')
            query = args[-1]
            key = cr.digest([self.pack, args[:-1], query['id'], query['treatment_class_iri'], self.artifacts])
            start = time.monotonic()
            if key in self.entries:
                self.stats['hits'] += 1; self.entries.move_to_end(key)
                session = self.entries[key][0]
                result = session.execute(query)['result']
                self.stats['reevaluation_seconds'] += time.monotonic() - start
            else:
                self.stats['misses'] += 1
                session = prepared.Session(self.folder, *mapped_arguments(self.pack, args))
                size = session.snapshot()['context']['serialized_prepared_bytes']
                if size > self.max_bytes:
                    session.close(); raise ei.ContractError('MAPPED_PRESSURE_CACHE_BUDGET_EXCEEDED')
                while self.entries and (len(self.entries) >= self.max_entries or self.bytes + size > self.max_bytes):
                    old, old_size = self.entries.popitem(last=False)[1]; old.close(); self.bytes -= old_size
                    self.stats['evicted'] += 1
                self.entries[key] = (session, size); self.bytes += size; self.stats['admitted'] += 1
                result = session.execute(query)['result']
                self.stats['fresh_seconds'] += time.monotonic() - start
            return extract(result, self.pack['selector']['source_item_ids'][0])
        except Exception:
            self.clear(); raise


class Session(pressure.Session):
    def __init__(self, folder, request, declaration, mapping_dir, progress=lambda **kw:None):
        self.mapping_dir = Path(mapping_dir).resolve()
        pack, stamps = load_pack(self.mapping_dir)
        catalogue = source.build(folder, pack['request'])
        ei.require(pack['catalogue'] == catalogue['catalogue'], 'MAPPED_PRESSURE_SOURCE_CATALOGUE_MISMATCH')
        ei.require(pack['request']['dataset_id'] == request['dataset_id'], 'MAPPED_PRESSURE_DATASET_MISMATCH')
        plan = source.mappings.plan(*(pack[n] for n in source.mappings.mappings.NAMES), pack['selector'])
        ei.require(plan['status'] == 'READY_MEASUREMENT_SELECTOR', 'MAPPED_PRESSURE_REQUIRES_REVIEWED_SELECTOR:' + plan['status'])
        query = request['query']; item = query['baseline']['item_ids']
        ei.require(len(item) == 1 and query['followup']['item_ids'] == item and
                   pack['selector']['source_item_ids'] == item and plan['supported_item_ids'] == item,
                   'MAPPED_PRESSURE_REQUIRES_ONE_SUPPORTED_ITEM')
        ei.require(pack['selector']['unit_lexical'] == query['baseline']['unit_lexical'] == query['followup']['unit_lexical'],
                   'MAPPED_PRESSURE_UNIT_SCOPE_MISMATCH')
        super().__init__(folder, request, declaration, progress)
        self._pack = deepcopy(pack)
        self.context.update(profile=PROFILE, literal_session_id=self.id, mapping_files=stamps,
                            measurement_mapping={'catalogue_report':catalogue, 'selection_plan':plan})
        self.context['artifacts'] = {f:ei.digest((ei.ROOT/f).read_text()) for f in FILES}
        self.id = cr.digest(self.context)
        self._fresh = FreshExecutor(folder, pack)
        self.check_current()

    def check_current(self):
        super().check_current()
        ei.require(load_pack(self.mapping_dir)[1] == self.context['mapping_files'], 'MAPPING_REVIEW_CHANGED_RESTART_SESSION')

    def execute(self, options, progress=lambda **kw:None, *, batch_executor=None):
        result = super().execute(options, progress, batch_executor=self._fresh.execute if batch_executor is None else batch_executor)
        plan = self.context['measurement_mapping']['selection_plan']
        result['measurement_mapping'] = {'mapping_context_id':plan['mapping']['context_id'],
            'class_iri':self._pack['selector']['class_iri'], 'selected_item_ids':plan['supported_item_ids'],
            'catalogue_context_id':self.context['measurement_mapping']['catalogue_report']['context_id'],
            'clinical_mapping_verified':False}
        return result

    def inspect(self, result, token):
        answer = super().inspect(result, token)
        answer['measurement_mapping'] = deepcopy(self.context['measurement_mapping'])
        return answer
