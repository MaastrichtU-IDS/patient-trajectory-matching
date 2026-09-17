"""Bounded form/AST conversion; execution remains the extended interval engine."""
from copy import deepcopy
from decimal import Decimal
import re

from app.interval_editor import keys, minutes
from patterns import bounded_intervals as bt, extended_interval_query as extended

QUERY_ID = 'authored-configurable-trajectory-query'
MIN_SLOTS, MAX_SLOTS, MAX_CONSTRAINTS = 2, 3, 6
EVENT_TYPES = [
    {'id': 'infusion', 'label': 'Infusion', 'class_iri': str(bt.KINDS['infusion'])},
    {'id': 'specimen_collection', 'label': 'Specimen collection',
     'class_iri': str(bt.KINDS['specimen_collection'])},
    {'id': 'recorded_input_segment', 'label': 'Recorded input segment',
     'class_iri': str(bt.KINDS['recorded_input_segment'])}]
OPERATORS = [*extended.ALLEN, 'gap', 'duration', 'minimum_overlap']
IDENTIFIER = re.compile(r'[A-Za-z][A-Za-z0-9_-]{0,31}\Z')
DEFAULT_PATTERN = {
    'slots': [{'id': 'infusion', 'event_kind': 'infusion'},
              {'id': 'collection', 'event_kind': 'specimen_collection'},
              {'id': 'followup', 'event_kind': 'specimen_collection'}],
    'constraints': [{'id': 'during-infusion', 'operator': 'contains',
                     'left': 'infusion', 'right': 'collection'},
                    {'id': 'followup-gap', 'operator': 'gap', 'left': 'collection', 'right': 'followup',
                     'minimum_minutes': '0', 'maximum_minutes': '30'}]}


def identifier(value):
    if type(value) is not str or not IDENTIFIER.fullmatch(value):
        raise ValueError('IDs must start with a letter and contain at most 32 letters, digits, underscores or hyphens')
    return value


def compile_pattern(pattern):
    keys(pattern, ('slots', 'constraints'))
    if type(pattern['slots']) is not list or not MIN_SLOTS <= len(pattern['slots']) <= MAX_SLOTS:
        raise ValueError('Select two or three event slots')
    if type(pattern['constraints']) is not list or not 1 <= len(pattern['constraints']) <= MAX_CONSTRAINTS:
        raise ValueError('Select one to six constraints')
    selectors = {row['id']: row['class_iri'] for row in EVENT_TYPES}
    slots, slot_ids = [], set()
    for slot in pattern['slots']:
        keys(slot, ('id', 'event_kind'))
        name = identifier(slot['id'])
        if name in slot_ids:
            raise ValueError('Duplicate slot ID')
        if type(slot['event_kind']) is not str or slot['event_kind'] not in selectors:
            raise ValueError('Select a supported event type')
        slots.append({'id': name, 'class_iri': selectors[slot['event_kind']]})
        slot_ids.add(name)
    constraints, constraint_ids = [], set()
    for item in pattern['constraints']:
        if type(item) is not dict or type(item.get('operator')) is not str or item['operator'] not in OPERATORS:
            raise ValueError('Select a supported constraint operator')
        op = item['operator']
        fields = ['id', 'operator', *(('slot',) if op == 'duration' else ('left', 'right'))]
        if op in ('gap', 'duration'):
            fields.extend(('minimum_minutes', 'maximum_minutes'))
        elif op == 'minimum_overlap':
            fields.append('minimum_minutes')
        keys(item, fields)
        name = identifier(item['id'])
        if name in constraint_ids:
            raise ValueError('Duplicate constraint ID')
        constraint_ids.add(name)
        refs = ('slot',) if op == 'duration' else ('left', 'right')
        names = [identifier(item[key]) for key in refs]
        if not set(names) <= slot_ids or len(set(names)) != len(names):
            raise ValueError('Constraint references must name existing, distinct slots')
        constraint = {key: item[key] for key in ('id', 'operator', *refs)}
        if op in ('gap', 'duration'):
            low, high = (minutes(item[key], minimum=0 if op == 'duration' else -1440)
                         for key in ('minimum_minutes', 'maximum_minutes'))
            if low > high:
                raise ValueError('Constraint minimum exceeds maximum')
            if op == 'duration' and low <= 0:
                raise ValueError('Duration bounds must be positive')
            bounds = ('min_gap_us', 'max_gap_us') if op == 'gap' else ('minimum_us', 'maximum_us')
            constraint.update(zip(bounds, (low, high)))
        elif op == 'minimum_overlap':
            minimum = minutes(item['minimum_minutes'], minimum=0)
            if minimum <= 0:
                raise ValueError('Minimum overlap must be positive')
            constraint['minimum_us'] = minimum
        constraints.append(constraint)
    query = {'profile': extended.PROFILE, 'id': QUERY_ID, 'slots': slots, 'constraints': constraints}
    extended.validate(query)
    return query


def minute_string(value):
    # Six decimal minute places represent multiples of 60 microseconds exactly.
    if type(value) is not int or value % 60:
        raise ValueError('Imported times must be integer multiples of 60 microseconds')
    text = format(Decimal(value) / Decimal(60000000), 'f')
    return text.rstrip('0').rstrip('.') if '.' in text else text


def decompile_query(query):
    """Import only the builder's canonical AST subset, with lossless round trips."""
    keys(query, ('profile', 'id', 'slots', 'constraints'))
    if query['profile'] != extended.PROFILE or query['id'] != QUERY_ID:
        raise ValueError('Import a canonical configurable trajectory query')
    if type(query['slots']) is not list or type(query['constraints']) is not list:
        raise ValueError('Query slots and constraints must be lists')
    selectors = {row['class_iri']: row['id'] for row in EVENT_TYPES}
    pattern = {'slots': [], 'constraints': []}
    for slot in query['slots']:
        keys(slot, ('id', 'class_iri'))
        if type(slot['class_iri']) is not str or slot['class_iri'] not in selectors:
            raise ValueError('Import a supported event selector')
        pattern['slots'].append({'id': slot['id'], 'event_kind': selectors[slot['class_iri']]})
    for constraint in query['constraints']:
        if type(constraint) is not dict or type(constraint.get('operator')) is not str:
            raise ValueError('Invalid query constraint')
        op = constraint['operator']
        refs = ('slot',) if op == 'duration' else ('left', 'right')
        metrics = {'gap': ('min_gap_us', 'max_gap_us'), 'duration': ('minimum_us', 'maximum_us'),
                   'minimum_overlap': ('minimum_us',)}.get(op, ())
        keys(constraint, ('id', 'operator', *refs, *metrics))
        item = {key: constraint[key] for key in ('id', 'operator', *refs)}
        item.update((name, minute_string(constraint[key])) for key, name in
                    zip(metrics, ('minimum_minutes', 'maximum_minutes')))
        pattern['constraints'].append(item)
    if compile_pattern(pattern) != query:
        raise ValueError('Query is outside the canonical builder profile')
    return pattern


def metadata():
    return {'event_types': deepcopy(EVENT_TYPES), 'operators': OPERATORS[:],
            'min_slots': MIN_SLOTS, 'max_slots': MAX_SLOTS, 'max_constraints': MAX_CONSTRAINTS,
            'minute_range': [-1440, 1440], 'fractional_digits': 6,
            'default_pattern': deepcopy(DEFAULT_PATTERN), 'default_query': compile_pattern(DEFAULT_PATTERN)}
