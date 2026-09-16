"""Reuse checked batch views while reevaluating each mixed query against source timelines.

The original mixed_record_query.execute remains the differential reference.
This module shares its validation, arithmetic, edge and classification primitives.
"""
from collections import OrderedDict
from copy import deepcopy
from dataclasses import asdict
import json
import time
from . import mixed_record_query as m

PROFILE = m.PROFILE
FILES = m.FILES
COORDINATE_ORIGIN = m.COORDINATE_ORIGIN
MAX_VARIABLES = m.MAX_VARIABLES
MAX_BASELINE_TESTS = m.MAX_BASELINE_TESTS
MAX_FOLLOWUP_TESTS = m.MAX_FOLLOWUP_TESTS
ei, cr, local, bt, cohort = m.ei, m.cr, m.local, m.bt, m.cohort
_item_unit, _scalar, _change = m._item_unit, m._scalar, m._change
_baseline_edges, _followup_edges = m._baseline_edges, m._followup_edges
ARTIFACTS = tuple(sorted(set(m.FILES + ('patterns/prepared_mixed_query.py',))))


def artifact_stamp():
    return {path: ei.digest((ei.ROOT / path).read_text()) for path in ARTIFACTS}


def _evaluate(point_view, interval_view, aligned, alignment, query, prepared_networks):
    context = {'profile': PROFILE, 'query': query, 'alignment': alignment,
        'interval_context_id': interval_view['context_id'], 'measurement_context_id': point_view['context_id'],
        'coordinate_origin': COORDINATE_ORIGIN, 'time_domain': local.TIME_DOMAIN,
        'clock_alignment_basis': 'explicit_caller_declaration_not_independent_source_verification',
        'certainty_semantics': 'exists_named_binding_forall_feasible_selected_source_timelines',
        'eligibility': 'baseline_and_treatment_only_followup_never_required',
        'limits': {'variables_per_episode': MAX_VARIABLES, 'baseline_tests': MAX_BASELINE_TESTS,
                   'followup_tests': MAX_FOLLOWUP_TESTS},
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    result = {'profile': PROFILE, 'context': context, 'context_id': cr.digest(context),
        'interval_view': interval_view, 'measurement_view': point_view, 'baseline_bindings': None,
        'certain_patient_ids': None, 'possible_patient_ids': None, 'source_networks': None, 'edge_evidence': None,
        'search_complete_over_selected_records': False, 'clinical_mapping_verified': False,
        'clinical_knowledge_status': 'UNKNOWN', 'physical_elapsed_time_verified': False,
        'source_history_verified': False, 'causal_effect_estimated': False,
        'full_mixed_owl_reasoning_verified': False, 'scope': 'selected_record_descriptions_within_patient_episode'}
    if point_view['status'] not in ('SELECTED_MEASUREMENT_RECORDS', 'EMPTY_SELECTED_RECORDS'):
        result.update(status='BLOCKED_MEASUREMENT_VIEW'); return result
    if interval_view['status'] not in ('READY', 'EMPTY_ACCEPTED_VIEW'):
        result.update(status='BLOCKED_INTERVAL_VIEW'); return result
    source = interval_view['source'] or {'variables': [], 'events': [], 'constraints': []}
    records = point_view['records']; selected_intervals = {e['id']: e for e in source['events']}
    treatments = []
    if interval_view['matching'] is not None:
        for trajectory in interval_view['matching']['trajectories']:
            for binding in trajectory['bindings']:
                witness = binding['slots']['treatment']; event = selected_intervals[witness['event_id']]
                treatments.append((event, witness))
    baseline_records = [r for r in records if _item_unit(r, query['baseline']) and _scalar(r, query['baseline'])]
    followup_records = [r for r in records if _item_unit(r, query['followup'])]
    candidates = [(e, w, b) for e, w in treatments for b in baseline_records if bt.scope(e) == bt.scope(b['event'])]
    followup_work = sum(sum(bt.scope(e) == bt.scope(f['event']) and b['event']['id'] != f['event']['id']
                           for f in followup_records) for e, _, b in candidates)
    try:
        ei.require(len(candidates) <= MAX_BASELINE_TESTS and followup_work <= MAX_FOLLOWUP_TESTS, 'MIXED_CANDIDATE_LIMIT')
        networks, variables, edge_evidence = prepared_networks
    except (ei.ContractError, bt.InconsistentSource) as error:
        result.update(status='BLOCKED_MIXED_SOURCE', reason=str(error)); return result
    def compatible(event, record):
        clock = variables['i:' + event['start_var']]['clock_id']
        return (event['patient_id'], clock, record['clock']['clock_id']) in aligned
    bindings = []
    for event, witness, baseline in candidates:
        group = bt.scope(event); network = networks[group]
        base_edges = _baseline_edges(event, baseline, query['baseline']) if compatible(event, baseline) else []
        base = cohort.classify(network, base_edges, [] if base_edges else ['baseline_clock_alignment'])
        row = {'patient_id': group[0], 'episode_id': group[1], 'treatment': witness,
            'baseline_id': baseline['event']['id'], 'eligibility': base, 'followup': [],
            'followup_status': 'NOT_EVALUATED_INELIGIBLE', 'clinical_response_status': 'UNKNOWN'}
        if base['status'] in ('CERTAIN', 'POSSIBLE'):
            for followup in followup_records:
                if bt.scope(followup['event']) != group or followup['event']['id'] == baseline['event']['id']: continue
                edges = _followup_edges(event, followup, query['followup']) if compatible(event, followup) else []
                # Classify the whole named triple against SOURCE, not a baseline-narrowed network.
                joint = cohort.classify(network, base_edges + edges, [] if edges else ['followup_clock_alignment'])
                row['followup'].append({'measurement_id': followup['event']['id'], 'joint_binding': joint,
                    'value_change': _change(baseline, followup) if joint['status'] in ('CERTAIN', 'POSSIBLE') else None})
            statuses = {f['joint_binding']['status'] for f in row['followup']}
            row['followup_status'] = ('RECORDED_FOLLOWUP' if 'CERTAIN' in statuses else
                'POSSIBLE_RECORDED_FOLLOWUP' if 'POSSIBLE' in statuses else
                'UNKNOWN_CLOCK_ALIGNMENT' if 'INCOMPARABLE' in statuses else 'NO_SELECTED_FOLLOWUP_IN_WINDOW')
        elif base['status'] == 'INCOMPARABLE': row['followup_status'] = 'UNKNOWN_BASELINE_ALIGNMENT'
        bindings.append(row)
    result.update(status='COMPLETED_RECORD_QUERY', search_complete_over_selected_records=True,
        baseline_bindings=bindings, certain_patient_ids=sorted({b['patient_id'] for b in bindings if b['eligibility']['status'] == 'CERTAIN'}),
        possible_patient_ids=sorted({b['patient_id'] for b in bindings if b['eligibility']['status'] in ('CERTAIN', 'POSSIBLE')}),
        source_networks=[{'patient_id': g[0], 'episode_id': g[1], 'edges': [asdict(e) for e in n.edges]} for g, n in sorted(networks.items())],
        edge_evidence=edge_evidence, measurement_filter={'baseline_candidate_ids': [r['event']['id'] for r in baseline_records],
        'followup_candidate_ids': [r['event']['id'] for r in followup_records], 'source': 'selected_measurement_records_only'})
    return result


class PreparedExecutor:
    """One worker, bounded LRU, in-memory snapshots; never accepts new source claims."""
    def __init__(self, max_entries=1024, max_bytes=128 * 1024 * 1024):
        self.artifacts = artifact_stamp()
        self.max_entries, self.max_bytes = max_entries, max_bytes
        self.entries = OrderedDict()
        self.bytes = 0
        self.stats = {'hits': 0, 'misses': 0, 'admitted': 0, 'evicted': 0,
                      'fresh_seconds': 0.0, 'reevaluation_seconds': 0.0}

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def snapshot(self):
        return {**self.stats, 'entries': len(self.entries), 'serialized_bytes': self.bytes,
                'max_entries': self.max_entries, 'max_serialized_bytes': self.max_bytes,
                'implementation_context_id': cr.digest(self.artifacts)}

    def execute(self, *args, **kwargs):
        try:
            return self._execute(*args, **kwargs)
        except Exception:
            self.clear()
            raise

    def _execute(self, interval_store, interval_policy, semantic_policy, measurement_store,
                measurement_policy, alignment, query, *, timeout_seconds=20):
        ei.require(artifact_stamp() == self.artifacts, 'PREPARED_IMPLEMENTATION_CHANGED_RESTART_SERVER')
        m.validate_query(query)
        # Only baseline/follow-up selectors vary. Treatment query ID/class and the
        # entire accepted stores, policies, alignment and backend timeout are bound.
        inputs = (interval_store, interval_policy, semantic_policy, measurement_store,
                  measurement_policy, alignment)
        key = cr.digest([inputs, query['id'], query['treatment_class_iri'], timeout_seconds, self.artifacts])
        start = time.monotonic()
        if key in self.entries:
            self.stats['hits'] += 1
            self.entries.move_to_end(key)
            entry, _ = self.entries[key]
            result = _evaluate(entry['point_view'], entry['interval_view'], entry['aligned'],
                               deepcopy(alignment), deepcopy(query), entry['networks'])
            # Results may expose views and path certificates. Keep cached objects private.
            result = deepcopy(result)
            self.stats['reevaluation_seconds'] += time.monotonic() - start
        else:
            self.stats['misses'] += 1
            result = m.execute(*inputs, query, timeout_seconds=timeout_seconds)
            if result['status'] == 'COMPLETED_RECORD_QUERY' and result['search_complete_over_selected_records']:
                views = deepcopy({'point_view': result['measurement_view'], 'interval_view': result['interval_view']})
                size = len(json.dumps(views, sort_keys=True, separators=(',', ':')).encode())
                if size <= self.max_bytes and self.max_entries > 0:
                    source = views['interval_view']['source'] or {'variables': [], 'events': [], 'constraints': []}
                    views['networks'] = m._compile(source, views['point_view']['selection'])
                    views['aligned'] = m.validate_alignment(alignment, interval_store, measurement_store)
                    while self.entries and (len(self.entries) >= self.max_entries or self.bytes + size > self.max_bytes):
                        self.bytes -= self.entries.popitem(last=False)[1][1]
                        self.stats['evicted'] += 1
                    self.entries[key] = (views, size)
                    self.bytes += size
                    self.stats['admitted'] += 1
            self.stats['fresh_seconds'] += time.monotonic() - start
        if result['status'] != 'COMPLETED_RECORD_QUERY' or not result['search_complete_over_selected_records']:
            self.clear()
        ei.require(artifact_stamp() == self.artifacts, 'PREPARED_IMPLEMENTATION_CHANGED_DURING_QUERY')
        return result
