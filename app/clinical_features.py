"""Startup-admitted, source-pinned supplemental pre-index measurement streams.

Technical review admits a source representation, not a clinical interpretation.
The server reads this pack locally; HTTP profiles select only admitted variable
IDs. Historical snapshots retain their evidence and never reopen source files.
"""
from copy import deepcopy
import csv
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
from pathlib import Path
import re

from app import feature_profiles as fp

SCHEMA = 'recorded-clinical-features-1'
PACK_SCHEMA = 'recorded-clinical-feature-pack-1'
EVIDENCE_SCHEMA = 'recorded-clinical-evidence-1'
VERSION = 'recorded-distinct-variable-distance-1.0'
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 20000
ID = re.compile(r'[a-z][a-z0-9_]{0,39}\Z')
SHA = re.compile(r'[a-f0-9]{64}\Z')
DECIMAL = re.compile(r'[+-]?[0-9]{1,12}(?:\.[0-9]{1,12})?\Z')
FIELDS = ('event_id', 'patient_id', 'episode_id', 'item_id', 'unit', 'time', 'value')
CLOCK = 'patient-local-charttime'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _read(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('Clinical feature input exceeds 4 MiB')
    with path.open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError('Clinical feature input exceeds 4 MiB')
    return raw


def _files(snapshot):
    context = snapshot['source_context']
    # Mapped sessions preserve their admitted literal source context.
    if 'source_files' in context:
        return context['source_files']
    for key in ('source_context', 'literal_source_context', 'literal_context'):
        if isinstance(context.get(key), dict) and 'source_files' in context[key]:
            return context[key]['source_files']
    raise ValueError('Clinical features require a pinned source-file context')


def _validate_definition(definition):
    if (not isinstance(definition, dict) or set(definition) !=
            {'schema', 'source_kind', 'source_files', 'csv_sha256', 'clock', 'review', 'variables'}
            or definition['schema'] != PACK_SCHEMA
            or definition['source_kind'] not in ('AUTHORED', 'RECORDED')
            or definition['clock'] != CLOCK
            or not isinstance(definition['csv_sha256'], str)
            or SHA.fullmatch(definition['csv_sha256']) is None):
        raise ValueError('Invalid clinical feature pack definition')
    files = definition['source_files']
    if (not isinstance(files, dict) or not files or
            any(not isinstance(k, str) or not isinstance(v, str) or SHA.fullmatch(v) is None
                for k, v in files.items())):
        raise ValueError('Clinical feature pack requires exact parent source hashes')
    review = definition['review']
    if (not isinstance(review, dict) or set(review) != {'status', 'reviewer', 'rationale', 'clinical_status'}
            or review['status'] != 'TECHNICAL_ACCEPTED' or review['clinical_status'] != 'PENDING'
            or any(not isinstance(review[key], str) or not 1 <= len(review[key]) <= 1000
                   for key in ('reviewer', 'rationale'))):
        raise ValueError('Clinical feature pack requires explicit technical review; clinical acceptance is pending')
    variables = definition['variables']
    if not isinstance(variables, list) or not 1 <= len(variables) <= 8:
        raise ValueError('Clinical feature pack requires one to eight variables')
    ids, selectors = set(), set()
    for variable in variables:
        if (not isinstance(variable, dict) or set(variable) !=
                {'id', 'label', 'item_id', 'unit', 'lookback_minutes'}
                or not isinstance(variable['id'], str) or ID.fullmatch(variable['id']) is None
                or variable['id'] in ids or variable['id'] in {x['id'] for x in fp.CATALOGUE}
                or any(not isinstance(variable[k], str) or not 1 <= len(variable[k]) <= 120
                       for k in ('label', 'item_id', 'unit'))
                or type(variable['lookback_minutes']) is not int
                or not 1 <= variable['lookback_minutes'] <= 30
                or variable['item_id'] in selectors):
            raise ValueError('Invalid or duplicate admitted clinical variable')
        ids.add(variable['id'])
        selectors.add(variable['item_id'])


def _time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is not None:
        raise ValueError('Clinical feature clock must be a naive patient-local charted time')
    return result


def _source_kind(definition, snapshot):
    mode = snapshot['temporal_result']['source_mode']
    if ((mode in ('synthetic', 'authored') and definition['source_kind'] != 'AUTHORED')
            or (mode == 'public-demo' and definition['source_kind'] != 'RECORDED')
            or mode not in ('synthetic', 'authored', 'public-demo', 'configured-records')):
        raise ValueError('Clinical feature source kind differs from the parent source')


class ClinicalFeaturePack:
    def __init__(self, path, *, lazy=False):
        self.path = Path(path).resolve()
        raw = _read(self.path)
        self.pack_sha256 = hashlib.sha256(raw).hexdigest()
        pack = json.loads(raw)
        if not isinstance(pack, dict) or 'csv_path' not in pack:
            raise ValueError('Clinical feature pack requires csv_path')
        csv_path = pack.pop('csv_path')
        if not isinstance(csv_path, str) or not csv_path:
            raise ValueError('Clinical feature pack requires a local CSV path')
        self.csv_path = (self.path.parent / csv_path).resolve()
        _validate_definition(pack)
        self.definition = deepcopy(pack)
        self.rows, self.by_episode = None, {}
        if not lazy:
            self.check_current()

    def _load_source(self, source):
        pack = self.definition
        reader = csv.DictReader(io.StringIO(source.decode('utf-8')))
        if reader.fieldnames != list(FIELDS):
            raise ValueError('Clinical feature CSV columns differ')
        rows, seen = [], set()
        selectors = {(v['item_id'], v['unit']) for v in pack['variables']}
        for number, row in enumerate(reader, 2):
            if number > MAX_ROWS + 1:
                raise ValueError('Clinical feature source exceeds 20000 rows')
            if (set(row) != set(FIELDS) or any(not isinstance(v, str) or len(v) > 256 or not v for v in row.values())
                    or row['event_id'] in seen):
                raise ValueError('Invalid or duplicate clinical source record')
            seen.add(row['event_id'])
            # Rows outside the admitted item/unit pairs are retained in the CSV
            # hash but never become evidence. No conversion or numeric coercion.
            if (row['item_id'], row['unit']) not in selectors:
                continue
            try:
                value = Decimal(row['value'])
                if not value.is_finite():
                    continue
            except InvalidOperation:
                continue
            if DECIMAL.fullmatch(row['value']) is None:
                raise ValueError('Clinical feature numeric values require plain decimals with at most 12 integer and 12 fractional digits')
            try:
                timestamp = _time(row['time'])
            except (InvalidOperation, ValueError):
                continue
            record = {**row, 'time': timestamp.isoformat(), 'clock': CLOCK,
                      'source': {'table': 'supplemental_measurements', 'record_number': number,
                                 'file_sha256': pack['csv_sha256']}}
            record['claim_sha256'] = digest(record)
            rows.append(record)
        self.rows = rows
        self.by_episode = {}
        for row in self.rows:
            self.by_episode.setdefault((row['patient_id'], row['episode_id']), []).append(row)

    def configuration(self):
        return {'clinical_features_sha256': self.pack_sha256,
                'clinical_source_sha256': self.definition['csv_sha256']}

    def check_current(self):
        try:
            source = _read(self.csv_path)
            if (hashlib.sha256(_read(self.path)).hexdigest() != self.pack_sha256 or
                    hashlib.sha256(source).hexdigest() != self.definition['csv_sha256']):
                raise ValueError('CLINICAL_FEATURE_SOURCE_CHANGED_RESTART_SERVER: source or pack hash differs')
        except OSError as error:
            raise ValueError('CLINICAL_FEATURE_SOURCE_CHANGED_RESTART_SERVER: source unavailable') from error
        if self.rows is None:
            self._load_source(source)

    def metadata(self):
        return {'schema': SCHEMA, 'version': VERSION, 'available': True,
                'features': [deepcopy(fp.CATALOGUE[0]),
                             *[{**deepcopy(v), 'description': 'Latest finite exact-item/unit value strictly before index in the admitted lookback window.'}
                               for v in self.definition['variables']]],
                'review': deepcopy(self.definition['review']),
                'source_kind': self.definition['source_kind'],
                'max_features': len(self.definition['variables']) + 1,
                'missing_policy': fp.metadata()['missing_policy'],
                'distance': fp.metadata()['distance'],
                'time_basis': 'Charted time only; availability at index is not established.'}

    def bind(self, snapshot):
        self.check_current()
        if _files(snapshot) != self.definition['source_files']:
            raise ValueError('CLINICAL_FEATURE_PARENT_SOURCE_MISMATCH')
        _source_kind(self.definition, snapshot)
        scope = snapshot['query_context']['query']['baseline']
        if any(v['item_id'] in scope['item_ids'] for v in self.definition['variables']):
            raise ValueError('Supplemental variables must use distinct measurement items')
        roster = {(r['patient_id'], r['episode_id']) for r in snapshot['temporal_result']['roster']}
        # A known stay cannot silently change patient ownership.
        owners = {}
        for patient, episode in roster:
            owners.setdefault(episode, set()).add(patient)
        if any(row['episode_id'] in owners and row['patient_id'] not in owners[row['episode_id']]
               for row in self.rows):
            raise ValueError('Clinical feature patient/episode ownership differs')
        context = {'definition': deepcopy(self.definition), 'pack_sha256': self.pack_sha256,
                   'parent_source_files': deepcopy(_files(snapshot)),
                   'query_context_id': snapshot['query_context_id']}
        anchors = []
        for detail in snapshot['details']:
            anchor = detail['anchor']
            start = _time(anchor['start'])
            selected = []
            for variable in self.definition['variables']:
                lower = start - timedelta(microseconds=min(scope['max_before_start_us'], variable['lookback_minutes'] * 60000000))
                upper = start - timedelta(microseconds=max(1, scope.get('min_before_start_us', 1)))
                selected.extend(deepcopy(row) for row in self.by_episode.get((anchor['patient_id'], anchor['episode_id']), [])
                                if (row['patient_id'], row['episode_id']) == (anchor['patient_id'], anchor['episode_id'])
                                and (row['item_id'], row['unit']) == (variable['item_id'], variable['unit'])
                                and lower <= _time(row['time']) <= upper)
            anchors.append({'token': anchor['token'], 'patient_id': anchor['patient_id'],
                            'episode_id': anchor['episode_id'],
                            'measurements': sorted(selected, key=lambda row: (row['time'], row['event_id']))})
        result = deepcopy(snapshot)
        result['clinical_features'] = {'schema': EVIDENCE_SCHEMA, 'context': context,
                                       'context_id': digest(context), 'anchors': anchors}
        validate_snapshot(result)
        self.check_current()
        return result


def validate_snapshot(snapshot):
    """Validate internal retained bindings, without claiming hash authentication."""
    if 'clinical_features' not in snapshot:
        return
    try:
        evidence = snapshot['clinical_features']
        context = evidence['context']
        definition = context['definition']
        _validate_definition(definition)
        _source_kind(definition, snapshot)
        if (set(evidence) != {'schema', 'context', 'context_id', 'anchors'}
                or evidence['schema'] != EVIDENCE_SCHEMA
                or set(context) != {'definition', 'pack_sha256', 'parent_source_files', 'query_context_id'}
                or evidence['context_id'] != digest(context)
                or not isinstance(context['pack_sha256'], str) or SHA.fullmatch(context['pack_sha256']) is None
                or context['parent_source_files'] != _files(snapshot)
                or definition['source_files'] != _files(snapshot)
                or context['query_context_id'] != snapshot['query_context_id']):
            raise ValueError('Clinical feature context differs')
        actual = {d['anchor']['token']: d['anchor'] for d in snapshot['details']}
        if len(evidence['anchors']) != len(actual) or {a['token'] for a in evidence['anchors']} != set(actual):
            raise ValueError('Clinical feature anchor coverage differs')
        selectors = {(v['item_id'], v['unit']): v for v in definition['variables']}
        baseline = snapshot['query_context']['query']['baseline']
        if any(v['item_id'] in baseline['item_ids'] for v in definition['variables']):
            raise ValueError('Clinical feature items overlap the baseline item')
        for retained in evidence['anchors']:
            anchor = actual[retained['token']]
            if (set(retained) != {'token', 'patient_id', 'episode_id', 'measurements'} or
                    any(retained[k] != anchor[k] for k in ('patient_id', 'episode_id'))):
                raise ValueError('Clinical feature patient/episode binding differs')
            start = _time(anchor['start'])
            seen = set()
            for row in retained['measurements']:
                variable = selectors.get((row['item_id'], row['unit']))
                if (variable is None or set(row) != set(FIELDS) | {'clock', 'source', 'claim_sha256'}
                        or any(not isinstance(row[k], str) or not 1 <= len(row[k]) <= 256 for k in FIELDS)
                        or any(row[k] != anchor[k] for k in ('patient_id', 'episode_id'))
                        or row['event_id'] in seen or row['clock'] != CLOCK
                        or row['claim_sha256'] != digest({k: v for k, v in row.items() if k != 'claim_sha256'})
                        or set(row['source']) != {'table', 'record_number', 'file_sha256'}
                        or row['source']['table'] != 'supplemental_measurements'
                        or row['source']['file_sha256'] != definition['csv_sha256']
                        or type(row['source']['record_number']) is not int or row['source']['record_number'] < 2
                        or not isinstance(row['value'], str) or DECIMAL.fullmatch(row['value']) is None
                        or not Decimal(row['value']).is_finite()):
                    raise ValueError('Clinical feature retained source evidence differs')
                lower = start - timedelta(microseconds=min(baseline['max_before_start_us'], variable['lookback_minutes'] * 60000000))
                upper = start - timedelta(microseconds=max(1, baseline.get('min_before_start_us', 1)))
                if not lower <= _time(row['time']) <= upper:
                    raise ValueError('Clinical feature evidence is outside the pre-index window')
                seen.add(row['event_id'])
    except (KeyError, TypeError, InvalidOperation, OverflowError) as error:
        raise ValueError('Invalid retained clinical feature evidence') from error


def compile_profile(value, scope, snapshot):
    if 'clinical_features' not in snapshot:
        raise ValueError('This job has no startup-admitted clinical feature pack')
    validate_snapshot(snapshot)
    envelope = snapshot['clinical_features']
    variables = envelope['context']['definition']['variables']
    catalogue = [deepcopy(fp.CATALOGUE[0]), *deepcopy(variables)]
    if (not isinstance(value, dict) or set(value) != {'schema', 'features'} or value['schema'] != SCHEMA
            or not isinstance(value['features'], list) or not 1 <= len(value['features']) <= len(catalogue)):
        raise ValueError('Invalid clinical feature profile')
    allowed, selected = {v['id'] for v in catalogue}, {}
    for feature in value['features']:
        if (not isinstance(feature, dict) or set(feature) != {'id', 'weight', 'scale'}
                or not isinstance(feature['id'], str) or feature['id'] not in allowed or feature['id'] in selected):
            raise ValueError('Clinical profile requires unique startup-admitted variable IDs')
        selected[feature['id']] = {'id': feature['id'], 'weight': fp._number(feature['weight']),
                                   'scale': fp._number(feature['scale'])}
    definition = {'schema': SCHEMA, 'features': [selected[v['id']] for v in catalogue if v['id'] in selected]}
    binding = {'version': VERSION, 'definition': definition, 'clinical_context_id': envelope['context_id'],
               'item_id': scope['item_id'], 'unit': scope['unit'], 'baseline_window_us': scope['baseline_window_us'],
               'baseline_min_before_start_us': scope.get('baseline_min_before_start_us', 1),
               'variables': variables}
    return {**deepcopy(scope), **deepcopy(binding), 'id': VERSION, 'sha256': digest(binding),
            'label': 'Explicit distinct-variable pre-index profile',
            'selection': 'Latest finite exact-item/unit value for each variable in its admitted pre-index window; equal times use ascending event ID.',
            'clinical_validation': 'Technical source review only; clinical acceptance and usefulness are pending.',
            'distance': fp.metadata()['distance'], 'missing_policy': fp.metadata()['missing_policy'],
            'ranking_arithmetic': fp.metadata()['ranking_arithmetic'],
            'review': deepcopy(envelope['context']['definition']['review'])}


def extract(detail, profile, snapshot):
    retained = next(row for row in snapshot['clinical_features']['anchors'] if row['token'] == detail['anchor']['token'])
    variables = {v['id']: v for v in profile['variables']}
    results = []
    for feature in profile['definition']['features']:
        if feature['id'] == 'latest_value':
            local = {**profile, 'definition': {'features': [{'id': 'latest_value'}]}}
            results.extend(fp.extract(detail, local)['features'])
            continue
        variable = variables[feature['id']]
        local = {**profile, 'item_id': variable['item_id'], 'unit': variable['unit'],
                 'baseline_window_us': min(profile['baseline_window_us'], variable['lookback_minutes'] * 60000000),
                 'definition': {'features': [{'id': 'latest_value'}]}}
        row = fp.extract({'anchor': detail['anchor'], 'measurements': retained['measurements']}, local)['features'][0]
        for evidence in row['evidence']:
            evidence.update(patient_id=retained['patient_id'], episode_id=retained['episode_id'])
        row.update({key: variable[key] for key in ('id', 'label', 'unit')})
        results.append(row)
    missing = [row['id'] for row in results if row['status'] != 'AVAILABLE']
    return {'features': results, 'missing_feature_ids': missing,
            'coverage': {'available': len(results) - len(missing), 'requested': len(results), 'complete': not missing}}
