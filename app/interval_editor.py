"""Compile bounded form controls into extended interval queries on authored sources."""
from collections import OrderedDict
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from patterns import bounded_intervals as bt, extended_interval_query as extended, robust_relaxation as relax
from app.temporal import ROOT, LIMITS as DEMO_LIMITS, check_limits, digest
from app.interval_explanations import explain_patient

LIMITS = {**DEMO_LIMITS, 'query_constraints': 3, 'catalogue_options': 1,
          'evaluations': 2, 'saved_results': 16}
OPTION_COST = '1.25'
SOURCES = {'sequential': 'examples/temporal-workspace/source.json',
           'overlap': 'examples/interval-editor/overlap-source.json'}
ARTIFACTS = ('app/interval_editor.py', 'app/interval_explanations.py', 'app/temporal.py', 'app/editor.html', 'app/editor.js')
DECIMAL = re.compile(r'-?(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,6})?\Z')


def keys(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError('Expected fields: ' + ', '.join(expected))


def minutes(value, *, minimum=-1440):
    if type(value) is not str or not DECIMAL.fullmatch(value):
        raise ValueError('Use decimal minute strings with at most six fractional digits')
    number = Decimal(value)
    if not Decimal(minimum) <= number <= 1440:
        raise ValueError(f'Minutes must be between {minimum} and 1440')
    return int(number * 60000000)


def compile_query(controls):
    fields = ('fixture', 'relation', 'gap', 'duration', 'minimum_overlap_minutes')
    keys(controls, fields + (('relaxation',) if type(controls) is dict and 'relaxation' in controls else ()))
    if type(controls['fixture']) is not str or controls['fixture'] not in SOURCES:
        raise ValueError('Select an authored fixture')
    relation = controls['relation']
    if type(relation) is not str or relation not in (*extended.ALLEN, 'gap'):
        raise ValueError('Select a supported interval relation')
    constraints = [{'id': 'relation', 'left': 'infusion', 'right': 'collection', 'operator': relation}]
    if relation == 'gap':
        keys(controls['gap'], ('minimum_minutes', 'maximum_minutes'))
        low, high = (minutes(controls['gap'][k]) for k in ('minimum_minutes', 'maximum_minutes'))
        if low > high:
            raise ValueError('Gap minimum exceeds maximum')
        constraints[0].update(min_gap_us=low, max_gap_us=high)
    elif controls['gap'] is not None:
        raise ValueError('Gap controls apply only to the gap relation')
    duration = controls['duration']
    if duration is not None:
        keys(duration, ('minimum_minutes', 'maximum_minutes'))
        low, high = (minutes(duration[k], minimum=0) for k in ('minimum_minutes', 'maximum_minutes'))
        if low <= 0 or high <= 0:
            raise ValueError('Duration bounds must be positive')
        if low > high:
            raise ValueError('Duration minimum exceeds maximum')
        constraints.append({'id': 'infusion-duration', 'operator': 'duration', 'slot': 'infusion',
                            'minimum_us': low, 'maximum_us': high})
    overlap = controls['minimum_overlap_minutes']
    if overlap is not None:
        value = minutes(overlap, minimum=0)
        if value <= 0:
            raise ValueError('Minimum overlap must be positive')
        constraints.append({'id': 'shared-time', 'operator': 'minimum_overlap',
                            'left': 'infusion', 'right': 'collection', 'minimum_us': value})
    query = {'profile': extended.PROFILE, 'id': 'authored-edited-interval-query',
             'slots': [{'id': 'infusion', 'class_iri': str(bt.KINDS['infusion'])},
                       {'id': 'collection', 'class_iri': str(bt.KINDS['specimen_collection'])}],
             'constraints': constraints}
    extended.validate(query)
    return query


def compile_policy(controls, query):
    policy = {'profile': relax.PROFILE, 'kind': 'extended', 'relaxable_targets': [],
              'max_cost': '0', 'max_changed_targets': 3, 'options': []}
    option = controls.get('relaxation')
    if option is None:
        return policy
    keys(option, ('max_cost', 'gap', 'duration', 'minimum_overlap_minutes'))
    if type(option['max_cost']) is not str or option['max_cost'] not in ('0', OPTION_COST):
        raise ValueError('Relaxation budget must be the string 0 or 1.25')
    changes = []
    for field, target in (('gap', 'relation'), ('duration', 'infusion-duration')):
        value = option[field]
        if value is None:
            continue
        if (field == 'gap' and controls['relation'] != 'gap') or controls[field] is None:
            raise ValueError('Relaxation requires an enabled ' + field + ' constraint')
        keys(value, ('minimum_minutes', 'maximum_minutes'))
        low, high = (minutes(value[k], minimum=0 if field == 'duration' else -1440)
                     for k in ('minimum_minutes', 'maximum_minutes'))
        changes.append({'target': target, 'lower_us': low, 'upper_us': high})
    if option['minimum_overlap_minutes'] is not None:
        if controls['minimum_overlap_minutes'] is None:
            raise ValueError('Relaxation requires an enabled minimum overlap constraint')
        minimum = minutes(option['minimum_overlap_minutes'], minimum=0)
        if minimum <= 0:
            raise ValueError('Relaxed minimum overlap must be positive')
        changes.append({'target': 'shared-time', 'minimum_us': minimum})
    if not changes:
        raise ValueError('Select at least one metric to relax')
    policy.update(max_cost=option['max_cost'], relaxable_targets=[c['target'] for c in changes],
                  options=[{'id': 'edited-option', 'cost': OPTION_COST, 'changes': changes}])
    # Validate all changes even when budget excludes the option, before source preparation.
    relax.variants(query, policy)
    return policy


def classifications(result):
    rows = []
    for trajectory in result['trajectories']:
        statuses = {binding['status'] for binding in trajectory['bindings']}
        status = next((name for name in ('CERTAIN', 'POSSIBLE', 'INCOMPARABLE') if name in statuses), 'NO_RECORDED_MATCH')
        rows.append({'patient_id': trajectory['patient_id'], 'episode_id': trajectory['episode_id'], 'status': status})
    return rows


class IntervalEditor:
    def __init__(self):
        self._sources = {name: json.loads((ROOT / path).read_text()) for name, path in SOURCES.items()}
        self._reports = OrderedDict()

    def metadata(self):
        return {'fixtures': list(SOURCES), 'relations': [*extended.ALLEN, 'gap'],
                'limits': deepcopy(LIMITS), 'minute_range': [-1440, 1440], 'fractional_digits': 6,
                'relaxation_cost': OPTION_COST, 'relaxation_budgets': ['0', OPTION_COST],
                'source_sha256': {name: digest(source) for name, source in self._sources.items()},
                'default_controls': {'fixture': 'overlap', 'relation': 'contains', 'gap': None,
                                     'duration': None, 'minimum_overlap_minutes': None}}

    def run(self, controls):
        query = compile_query(controls)
        policy = compile_policy(controls, query)
        source = deepcopy(self._sources[controls['fixture']])
        workload = check_limits(source, query, policy, limits=LIMITS)
        relaxation = relax.execute(source, query, policy)
        if relaxation.get('search_complete') is not True or any(
                e['result'].get('search_complete') is not True for e in relaxation['evaluations']):
            raise ValueError('Incomplete query; no result or export is available')
        result = relaxation['evaluations'][0]['result']
        patients = classifications(result)
        option_rows = {r['patient_id']: r for e in relaxation['evaluations'][1:] for r in classifications(e['result'])}
        best = {r['patient_id']: r for r in relaxation['best_robust_matches']}
        for row in patients:
            selected = best.get(row['patient_id'])
            row.update(option_status=option_rows.get(row['patient_id'], {}).get('status'),
                       selected_option=selected['option_id'] if selected else None,
                       selected_cost=selected['cost'] if selected else None,
                       explanation=explain_patient(source, query, relaxation, row['patient_id'], policy))
        report = {'format': 'interval-editor-export-1', 'scope': 'authored-two-slot-interval-editor',
                  'controls': deepcopy(controls), 'query': query, 'source': source,
                  'policy': policy, 'relaxation': relaxation,
                  'limits': deepcopy(LIMITS), 'workload': workload, 'patients': patients, 'result': result,
                  'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ARTIFACTS}}
        report['report_id'] = digest(report)
        self._reports[report['report_id']] = deepcopy(report)
        self._reports.move_to_end(report['report_id'])
        while len(self._reports) > LIMITS['saved_results']:
            self._reports.popitem(last=False)
        return report

    def export(self, report_id):
        return deepcopy(self._reports[report_id])


def verify(bundle):
    if type(bundle) is not dict or bundle.get('format') != 'interval-editor-export-1':
        raise ValueError('Unsupported interval-editor export')
    if bundle.get('report_id') != digest({k: v for k, v in bundle.items() if k != 'report_id'}):
        raise ValueError('Interval-editor export fingerprint differs')
    expected = IntervalEditor().run(bundle.get('controls'))
    if expected != bundle:
        raise ValueError('Interval-editor export differs from replay on the admitted fixture and implementation')
    return {'verified': True, 'report_id': expected['report_id'],
            'certain_patient_ids': expected['result']['certain_patient_ids']}
