"""Reuse a successful source-audited measurement query for an immutable input snapshot."""
from copy import deepcopy
import hashlib
from pathlib import Path

from . import measurement_source_catalogue as source, prepared_mixed_query as prepared

PROFILE = 'prepared-measurement-session-1.0'
ei, cr = source.ei, source.cr
FILES = tuple(sorted(set(source.FILES + prepared.ARTIFACTS + ('patterns/prepared_measurement_session.py',))))
MAX_PREPARED_BYTES = 64 * 1024 * 1024


def artifact_stamp():
    return {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}


def fingerprint(folder):
    """Hash bounded raw bytes without reparsing CSV; digest checks remain per query."""
    answer = {}
    for table, path in source.inputs.paths_for(folder).items():
        digest = hashlib.sha256(); size = 0
        with path.open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                size += len(chunk)
                ei.require(size <= source.inputs.MAX_BYTES, 'PREPARED_SOURCE_FILE_LIMIT')
                digest.update(chunk)
        answer[table] = digest.hexdigest()
    return answer


class Session:
    """One worker and fixed stores/policies/mappings; only numeric/temporal controls vary."""
    def __init__(self, folder, request, catalogue, terminology, proposals, review, selector,
                 interval_store, interval_policy, semantic_policy, measurement_store,
                 measurement_policy, alignment, query, *, timeout_seconds=20,
                 max_prepared_bytes=16 * 1024 * 1024):
        ei.require(type(max_prepared_bytes) is int and 0 < max_prepared_bytes <= MAX_PREPARED_BYTES,
                   'INVALID_PREPARED_MEASUREMENT_BUDGET')
        self._folder = Path(folder).resolve(); self._invalidated = True
        values = deepcopy((request, catalogue, terminology, proposals, review, selector, interval_store,
                           interval_policy, semantic_policy, measurement_store, measurement_policy, alignment, query))
        before = fingerprint(self._folder); artifacts = artifact_stamp()
        # A real successful reference execution is the sole admission route. No
        # public method accepts a caller-constructed audit, plan or prepared view.
        cold = source.execute(self._folder, *values, timeout_seconds=timeout_seconds)
        ei.require(cold['status'] == 'COMPLETED_REVIEWED_MEASUREMENT_QUERY',
                   'PREPARED_MEASUREMENT_REQUIRES_COMPLETED_QUERY:' + cold['status'])
        query_result = cold['query_result']; strata = query_result['strata']
        ei.require(bool(strata) and query_result['search_complete_over_requested_strata'], 'INCOMPLETE_PREPARED_STRATA')
        first = strata[0]['result']
        views = {'point_view': first['measurement_view'], 'interval_view': first['interval_view']}
        for stratum in strata:
            run = stratum['result']
            ei.require(run['status'] == 'COMPLETED_RECORD_QUERY' and run['search_complete_over_selected_records'],
                       'INCOMPLETE_PREPARED_STRATUM')
            ei.require(run['measurement_view'] == views['point_view'] and run['interval_view'] == views['interval_view'],
                       'PREPARED_MEASUREMENT_VIEW_DISAGREEMENT')
        template = deepcopy(query_result)
        template.update(strata=[], certain_patient_ids=None, possible_patient_ids=None,
                        search_complete_over_requested_strata=False)
        payload = deepcopy({'audit': cold['source_audit'], 'template': template, 'views': views,
                            'query': values[-1], 'alignment': values[-2]})
        size = len(cr.canonical(payload).encode())
        ei.require(size <= max_prepared_bytes, 'PREPARED_MEASUREMENT_BUDGET_EXCEEDED')
        self._payload = payload
        self._aligned = source.mappings.mixed.validate_alignment(values[-2], values[6], values[9])
        interval_source = views['interval_view']['source'] or {'variables': [], 'events': [], 'constraints': []}
        self._networks = source.mappings.mixed._compile(interval_source, views['point_view']['selection'])
        ei.require(before == fingerprint(self._folder), 'SOURCE_CHANGED_DURING_PREPARATION')
        ei.require(artifacts == artifact_stamp(), 'IMPLEMENTATION_CHANGED_DURING_PREPARATION')
        self._context = {'profile': PROFILE, 'source_files': before, 'artifacts': artifacts,
                         'initial_source_query_context_id': cold['context_id'],
                         'source_audit_context_id': cold['source_audit']['context_id'],
                         'mapping_context_id': query_result['selection_plan']['mapping']['context_id'],
                         'max_serialized_prepared_bytes': max_prepared_bytes,
                         'serialized_prepared_bytes': size,
                         'reuse_scope': 'fixed_source_stores_policies_mapping_selector_alignment_and_treatment',
                         'source_scope': 'supplied_store_fidelity_not_complete_source_coverage'}
        self._id = cr.digest(self._context); self._evaluations = 0; self._invalidated = False

    def snapshot(self):
        return {'session_id': self._id, 'context': deepcopy(self._context),
                'evaluations': self._evaluations, 'invalidated': self._invalidated}

    def close(self):
        self._invalidated = True
        self._payload = None; self._networks = None; self._aligned = None

    def _check_current(self):
        ei.require(fingerprint(self._folder) == self._context['source_files'], 'SOURCE_CHANGED_RESTART_SESSION')
        ei.require(artifact_stamp() == self._context['artifacts'], 'IMPLEMENTATION_CHANGED_RESTART_SESSION')

    def execute(self, query):
        ei.require(not self._invalidated, 'PREPARED_MEASUREMENT_SESSION_INVALIDATED')
        query = deepcopy(query); source.mappings.mixed.validate_query(query)
        parent = self._payload['query']
        ei.require((query['id'], query['treatment_class_iri']) == (parent['id'], parent['treatment_class_iri']),
                   'PREPARED_MEASUREMENT_TREATMENT_SCOPE_CHANGED')
        for side in ('baseline', 'followup'):
            ei.require(set(query[side]['item_ids']) == set(parent[side]['item_ids']) and
                       query[side]['unit_lexical'] == parent[side]['unit_lexical'],
                       'PREPARED_MEASUREMENT_ITEM_UNIT_SCOPE_CHANGED')
        try:
            self._check_current()
            result = self._evaluate(query)
            self._check_current()  # No successful result can escape a detected mid-query change.
            self._evaluations += 1
            if result['status'] != 'COMPLETED_REVIEWED_MEASUREMENT_QUERY': self.close()
            context = {'profile': PROFILE, 'session_id': self._id, 'source_query_context_id': result['context_id']}
            return {'profile': PROFILE, 'status': result['status'], 'context': context,
                    'context_id': cr.digest(context), 'result': result}
        except Exception:
            self.close()
            raise

    def _evaluate(self, query):
        result = deepcopy(self._payload['template'])
        result['context']['input_sha256']['query'] = cr.digest(query)
        result['context_id'] = cr.digest(result['context'])
        certain, possible = set(), set()
        for item in result['selection_plan']['supported_item_ids']:
            item_query = deepcopy(query)
            for side in ('baseline', 'followup'): item_query[side]['item_ids'] = [item]
            views = self._payload['views']
            # _evaluate retains the original source-network certainty semantics.
            # Copy its result immediately: views and path evidence must stay private.
            run = deepcopy(prepared._evaluate(views['point_view'], views['interval_view'], self._aligned,
                                              deepcopy(self._payload['alignment']), item_query, self._networks))
            result['strata'].append({'item_id': item, 'unit_lexical': query['baseline']['unit_lexical'],
                                     'query': item_query, 'result': run})
            if run['status'] == 'COMPLETED_RECORD_QUERY':
                certain.update(run['certain_patient_ids']); possible.update(run['possible_patient_ids'])
        if any(row['result']['status'] != 'COMPLETED_RECORD_QUERY' for row in result['strata']):
            result['status'] = 'BLOCKED_INCOMPLETE_MEASUREMENT_STRATA'
        else:
            result.update(status='COMPLETED_REVIEWED_MEASUREMENT_QUERY', certain_patient_ids=sorted(certain),
                          possible_patient_ids=sorted(possible), search_complete_over_requested_strata=True)
        audit = deepcopy(self._payload['audit'])
        context = {'profile': source.PROFILE, 'source_audit_context_id': audit['context_id'],
                   'query_context_id': result['context_id']}
        return {'profile': source.PROFILE, 'status': result['status'], 'context': context,
                'context_id': cr.digest(context), 'source_audit': audit, 'query_result': result}
