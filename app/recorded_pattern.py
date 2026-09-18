"""Typed point/interval patterns over a completed job's retained reviewed records.

The form is a lossless subset of the existing mixed-record query AST. It does
not turn measurements into intervals, reopen files, or expand reviewed windows.
Execution reuses the temporal engine and its independent SQLite binding check.
"""
from copy import deepcopy
from datetime import datetime

from patterns import mixed_record_query as mixed
from patterns import reviewed_pressure_session as pressure

PROFILE = 'recorded-point-interval-pattern-1.0'
OPERATORS = ['lt', 'le', 'eq', 'ge', 'gt']
MAX_US = 9223372036854775807


def _keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError('INVALID_RECORDED_PATTERN_FIELDS')


def _offset(value):
    if type(value) is not int or not -MAX_US <= value <= MAX_US:
        raise ValueError('INVALID_RECORDED_PATTERN_OFFSET')
    return value


def decompile_query(query):
    mixed.validate_query(query)
    baseline, followup = query['baseline'], query['followup']
    if baseline['min_before_start_us'] < 1:
        raise ValueError('BASELINE_MUST_BE_STRICTLY_PREINDEX')
    return {'profile': PROFILE,
            'baseline': {'operator': baseline['operator'], 'value_lexical': baseline['value_lexical'],
                         'minimum_offset_us': -baseline['max_before_start_us'],
                         'maximum_offset_us': -baseline['min_before_start_us']},
            'followup': {'anchor': followup['anchor'], 'within_interval': followup['within_interval'],
                         'minimum_offset_us': followup['min_after_anchor_us'],
                         'maximum_offset_us': followup['max_after_anchor_us']}}


def compile_pattern(form, parent_query, *, duration_bounds_us=None):
    """Compile within the admitted temporal envelope; scalar filters may change.

Changing an anchor needs exact retained duration bounds. The conservative proof
requires the complete child window to lie inside the parent for every anchor.
"""
    mixed.validate_query(parent_query)
    _keys(form, ('profile', 'baseline', 'followup'))
    if form['profile'] != PROFILE:
        raise ValueError('INVALID_RECORDED_PATTERN_PROFILE')
    b, f = form['baseline'], form['followup']
    _keys(b, ('operator', 'value_lexical', 'minimum_offset_us', 'maximum_offset_us'))
    _keys(f, ('anchor', 'within_interval', 'minimum_offset_us', 'maximum_offset_us'))
    blo, bhi = _offset(b['minimum_offset_us']), _offset(b['maximum_offset_us'])
    flo, fhi = _offset(f['minimum_offset_us']), _offset(f['maximum_offset_us'])
    if not blo <= bhi < 0 or not 0 <= flo <= fhi:
        raise ValueError('UNSUPPORTED_POINT_WINDOW')
    if b['operator'] not in OPERATORS or type(f['within_interval']) is not bool or f['anchor'] not in ('start', 'end'):
        raise ValueError('UNSUPPORTED_RECORDED_PATTERN_OPERATOR')
    # Keep numeric lexical validation and its exact decimal arithmetic in engine.
    q = deepcopy(parent_query)
    q['baseline'].update(operator=b['operator'], value_lexical=b['value_lexical'],
                         min_before_start_us=-bhi, max_before_start_us=-blo)
    q['followup'].update(anchor=f['anchor'], within_interval=f['within_interval'],
                         min_after_anchor_us=flo, max_after_anchor_us=fhi)
    mixed.validate_query(q)
    pb, pf = parent_query['baseline'], parent_query['followup']
    if -blo > pb['max_before_start_us'] or -bhi < pb['min_before_start_us']:
        raise ValueError('OUTSIDE_REVIEWED_BASELINE_WINDOW')
    relative_lo, relative_hi = flo, fhi
    if f['anchor'] != pf['anchor']:
        if (not isinstance(duration_bounds_us, (tuple, list)) or len(duration_bounds_us) != 2 or
                any(type(v) is not int for v in duration_bounds_us) or
                not 0 < duration_bounds_us[0] <= duration_bounds_us[1] <= MAX_US):
            raise ValueError('ANCHOR_CHANGE_REQUIRES_RETAINED_DURATIONS')
        low, high = duration_bounds_us
        if f['anchor'] == 'end':
            relative_lo, relative_hi = low + flo, high + fhi
        else:
            relative_lo, relative_hi = flo - high, fhi - low
    if (relative_lo < pf['min_after_anchor_us'] or relative_hi > pf['max_after_anchor_us'] or
            (pf['within_interval'] and not f['within_interval'])):
        raise ValueError('OUTSIDE_REVIEWED_FOLLOWUP_WINDOW')
    return q


def duration_bounds(session):
    values = []
    for anchor in session.planned['anchors']:
        segment = session.selection['interval_segments'][anchor['anchor_id']]
        delta = datetime.fromisoformat(segment['end']['raw_value']) - datetime.fromisoformat(segment['start']['raw_value'])
        values.append((delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds)
    return [min(values), max(values)] if values else None


def describe(session, current_query=None):
    parent = session.context['parent_request']['query']
    return {'profile': PROFILE, 'default_pattern': decompile_query(current_query or parent),
            'operators': OPERATORS[:], 'offset_unit': 'microseconds',
            'shape': ['baseline_point', 'treatment_interval', 'optional_followup_point'],
            'baseline': {k: deepcopy(parent['baseline'][k]) for k in ('item_ids', 'unit_lexical')},
            'followup': {k: deepcopy(parent['followup'][k]) for k in ('item_ids', 'unit_lexical')},
            'treatment_class_iri': parent['treatment_class_iri'], 'parent_query': deepcopy(parent),
            'duration_bounds_us': duration_bounds(session),
            'followup_required_for_eligibility': False,
            'scope': 'Retained reviewed records and complete admitted roster of the selected job.'}


class _RetainedSession(pressure.Session):
    """Read-only adapter for the established engine's aggregation and SQL check."""
    def __init__(self, session, query, form, parent_query_context_id):
        # Engine aggregation reads these source objects and constructs new output.
        # Copy the dictionary so adapter state never modifies the live session.
        self.__dict__.update(session.__dict__)
        self._pattern_query = deepcopy(query)
        self._pattern_form = deepcopy(form)
        self._parent_query_context_id = parent_query_context_id

    def query(self, options):
        return deepcopy(self._pattern_query)

    def execute(self, options, progress=lambda **kw: None, **kwargs):
        result = pressure.Session.execute(self, options, progress)
        result['context'].pop('controls')
        result['context'].update(profile=PROFILE, pattern=deepcopy(self._pattern_form),
            parent_query_context_id=self._parent_query_context_id,
            execution_source='immutable_retained_reviewed_records',
            artifacts={'app/recorded_pattern.py': pressure.p.ei.digest((pressure.p.ei.ROOT / 'app/recorded_pattern.py').read_text())})
        result['context_id'] = pressure.p.cr.digest(result['context'])
        if 'measurement_mapping' in self.context:
            mapping = self.context['measurement_mapping']
            plan = mapping['selection_plan']
            result['measurement_mapping'] = {
                'mapping_context_id': plan['mapping']['context_id'],
                'class_iri': self._pack['selector']['class_iri'],
                'selected_item_ids': deepcopy(plan['supported_item_ids']),
                'catalogue_context_id': mapping['catalogue_report']['context_id'],
                'clinical_mapping_verified': False}
        result['evidence'] = [self.inspect(result, a['token']) for a in result['anchors']] if result['status'] == 'COMPLETED' else []
        return result

    def check_current(self):
        # This operation explicitly queries a historical snapshot, never new files.
        for path, expected in self.context['artifacts'].items():
            if pressure.p.ei.digest((pressure.p.ei.ROOT / path).read_text()) != expected:
                raise ValueError('IMPLEMENTATION_CHANGED_RESTART_SERVER')

    def inspect(self, result, token):
        value = super().inspect(result, token)
        b = self._pattern_query['baseline']
        start = datetime.fromisoformat(value['anchor']['start'])
        for measurement in value['measurements']:
            delta = datetime.fromisoformat(measurement['time']) - start
            offset = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
            reasons = []
            if not -b['max_before_start_us'] <= offset <= -b['min_before_start_us']:
                reasons.append('OUTSIDE_BASELINE_WINDOW')
            if not pressure.p.reference.compare(measurement['value'], b['operator'], b['value_lexical']):
                reasons.append('SCALAR_PREDICATE_NOT_SATISFIED')
            if measurement['unit'] != b['unit_lexical']:
                reasons.append('UNIT_NOT_SELECTED')
            if measurement['item_id'] not in b['item_ids']:
                reasons.append('ITEM_NOT_SELECTED')
            measurement['baseline_exclusions'] = reasons
        if 'measurement_mapping' in self.context:
            value['measurement_mapping'] = deepcopy(self.context['measurement_mapping'])
        return value


def prepare(session, form, *, parent_query_context_id):
    parent = session.context['parent_request']['query']
    query = compile_pattern(form, parent, duration_bounds_us=duration_bounds(session))
    return _RetainedSession(session, query, form, parent_query_context_id)


def execute(session, form, *, query_context_id, job_id=None):
    retained = prepare(session, form, parent_query_context_id=query_context_id)
    # The established aggregation's legacy controls are ignored by query() and
    # removed from the returned context. The canonical AST is authoritative.
    return retained.execute({'threshold': '65', 'baseline_minutes': 1, 'followup_minutes': 0})
