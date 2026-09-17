"""Compile bounded form controls into extended interval queries on authored sources."""
from collections import OrderedDict
from copy import deepcopy
from decimal import Decimal
import hashlib
import json
import re

from patterns import bounded_intervals as bt, extended_interval_query as extended
from app.temporal import ROOT, LIMITS as DEMO_LIMITS, check_limits, digest

LIMITS = {**DEMO_LIMITS, 'query_constraints': 3, 'catalogue_options': 0,
          'evaluations': 1, 'saved_results': 16}
SOURCES = {'sequential': 'examples/temporal-workspace/source.json',
           'overlap': 'examples/interval-editor/overlap-source.json'}
ARTIFACTS = ('app/interval_editor.py', 'app/temporal.py', 'app/editor.html', 'app/editor.js')
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
    keys(controls, ('fixture', 'relation', 'gap', 'duration', 'minimum_overlap_minutes'))
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


class IntervalEditor:
    def __init__(self):
        self._sources = {name: json.loads((ROOT / path).read_text()) for name, path in SOURCES.items()}
        self._reports = OrderedDict()

    def metadata(self):
        return {'fixtures': list(SOURCES), 'relations': [*extended.ALLEN, 'gap'],
                'limits': deepcopy(LIMITS), 'minute_range': [-1440, 1440], 'fractional_digits': 6,
                'source_sha256': {name: digest(source) for name, source in self._sources.items()},
                'default_controls': {'fixture': 'overlap', 'relation': 'contains', 'gap': None,
                                     'duration': None, 'minimum_overlap_minutes': None}}

    def run(self, controls):
        query = compile_query(controls)
        source = deepcopy(self._sources[controls['fixture']])
        workload = check_limits(source, query, {'options': []}, limits=LIMITS)
        result = extended.execute(bt.prepare(source), query)
        if result.get('search_complete') is not True:
            raise ValueError('Incomplete query; no result or export is available')
        patients = []
        for trajectory in result['trajectories']:
            statuses = {binding['status'] for binding in trajectory['bindings']}
            status = next((name for name in ('CERTAIN', 'POSSIBLE', 'INCOMPARABLE') if name in statuses), 'NO_RECORDED_MATCH')
            patients.append({'patient_id': trajectory['patient_id'], 'episode_id': trajectory['episode_id'], 'status': status})
        report = {'format': 'interval-editor-export-1', 'scope': 'authored-two-slot-interval-editor',
                  'controls': deepcopy(controls), 'query': query, 'source': source,
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
