"""Bounded, server-owned temporal examples using the merged relaxation executor."""
from collections import Counter, OrderedDict
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from patterns import bounded_intervals as bt, robust_relaxation as relax
from patterns import exact_intervals as ei

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'examples/temporal-workspace'
BUDGETS = ('0', '1.25')
LIMITS = {'patients': 4, 'episodes': 4, 'events': 8, 'variables': 16,
          'clocks': 5, 'source_constraints': 0, 'slots': 2, 'query_constraints': 1,
          'catalogue_options': 1, 'evaluations': 2,
          'candidate_bindings_per_evaluation': 4, 'saved_results': 2}
ARTIFACTS = ('app/temporal.py', 'app/temporal_replay.py',
             'app/temporal.html', 'app/temporal.js')


def digest(value):
    return hashlib.sha256(ei.canonical(value).encode()).hexdigest()


def check_limits(source, query, policy, *, limits=None):
    """Reject a larger workload before source closure or binding enumeration."""
    limits = LIMITS if limits is None else limits
    sizes = {name: len(source[name]) for name in ('events', 'variables', 'clocks')}
    sizes.update(patients=len({e['patient_id'] for e in source['events']}),
                 episodes=len({bt.scope(e) for e in source['events']}),
                 source_constraints=len(source['constraints']), slots=len(query['slots']),
                 query_constraints=len(query['constraints']), catalogue_options=len(policy['options']),
                 evaluations=1 + len(policy['options']))
    # Count the full slot Cartesian product, before distinctness pruning. This
    # bound does not truncate answers or classify an unfinished search as empty.
    groups = {}
    for event in source['events']:
        groups.setdefault(bt.scope(event), []).append(event)
    candidates = 0
    for events in groups.values():
        product = 1
        for slot in query['slots']:
            product *= sum(slot['class_iri'] in bt.selected_classes(e) for e in events)
        candidates += product
    sizes['candidate_bindings_per_evaluation'] = candidates
    if any(value > limits[name] for name, value in sizes.items()):
        raise ValueError('Temporal demonstration exceeds its execution limits')
    return sizes


class TemporalWorkspace:
    def __init__(self):
        # Read once. Requests cannot supply paths, sources, queries or catalogues.
        self._inputs = {name: json.loads((FIXTURE / (name + '.json')).read_text())
                        for name in ('source', 'query', 'policy')}
        self._sizes = check_limits(**self._inputs)
        bt.validate(self._inputs['source'])
        relax.variants(self._inputs['query'], self._inputs['policy'])
        self._reports = OrderedDict()

    def metadata(self):
        return {'scope': 'separate-authored-temporal-fixture', 'budgets': list(BUDGETS),
                'query': deepcopy(self._inputs['query']), 'policy': deepcopy(self._inputs['policy']),
                'limits': deepcopy(LIMITS), 'workload': deepcopy(self._sizes),
                'source_sha256': digest(self._inputs['source']),
                'time_domain': 'integer-microseconds',
                'interpretation': 'Minute-scale arithmetic demonstration; no clinical window or mapping approval.'}

    def run(self, budget):
        if type(budget) is not str or budget not in BUDGETS:
            raise ValueError('Temporal budget must be the string 0 or 1.25')
        inputs = deepcopy(self._inputs)
        inputs['policy']['max_cost'] = budget
        sizes = check_limits(**inputs)
        result = relax.execute(**inputs)
        if result['status'] != 'COMPLETED' or result['search_complete'] is not True:
            raise ValueError('Temporal evaluation blocked; no completed cohort or export is available')
        if any(e['result'].get('search_complete') is not True for e in result['evaluations']):
            raise ValueError('Incomplete temporal evaluation; no completed cohort or export is available')
        original = result['evaluations'][0]['result']
        # The fixed fixture has one episode per patient. Preserve the original
        # status even when a permitted modification makes that patient robust.
        best = {r['patient_id']: r for r in result['best_robust_matches']}
        rows = [{'patient_id': t['patient_id'], 'episode_id': t['episode_id'],
                 'original_status': t['status'],
                 'selected_option': best[t['patient_id']]['option_id'] if t['patient_id'] in best else None,
                 'selected_cost': best[t['patient_id']]['cost'] if t['patient_id'] in best else None}
                for t in original['trajectories']]
        report = {'format': 'temporal-workspace-export-1',
                  'scope': 'separate-authored-temporal-fixture', 'budget': budget,
                  'limits': deepcopy(LIMITS), 'workload': sizes,
                  'inputs': inputs, 'result': result, 'patients': rows,
                  'original_counts': dict(sorted(Counter(r['original_status'] for r in rows).items())),
                  'added_robust_patient_ids': sorted(set(result['robust_patient_ids']) - set(original['certain_patient_ids'])),
                  'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS}}
        report['report_id'] = digest(report)
        self._reports[report['report_id']] = deepcopy(report)
        self._reports.move_to_end(report['report_id'])
        while len(self._reports) > LIMITS['saved_results']:
            self._reports.popitem(last=False)
        return report

    def export(self, report_id):
        return deepcopy(self._reports[report_id])
