"""Recorded scalar/point claim descriptions and explicit record selection.

No duration, clinical occurrence graph, unit conversion or temporal join is inferred.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import re
from rdflib import Graph
from . import bounded_intervals as bt, claim_projection as cp, claim_rdf as cr
from . import claim_isolation, exact_intervals as ei, patient_local as local

PROFILE = 'patient-local-measurement-claim-store-1.0'
SELECTION_PROFILE = 'measurement-claim-selection-1.0'
SCHEMA = ei.ROOT / 'schemas/measurement-claim-store.schema.json'
FILES = tuple(sorted(set(cp.FILES + local.FILES + ('patterns/measurement_claims.py',
    'schemas/measurement-claim-store.schema.json', 'ontology/local-claim-description-profile.ttl',
    'ontology/measurement-claim-description-profile.ttl'))))
# The measurement selection route has no semantic inference operation.
SEMANTIC_POLICY = {'profile': 'joint-semantic-policy-1.0', 'id': 'measurement_record_selection_only',
                   'classes': [], 'rules': [], 'disjoint': []}


def decimal_value(value):
    ei.require(type(value) is str and len(value) <= 64 and bool(re.fullmatch(
        r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value)), 'INVALID_MEASUREMENT_DECIMAL')
    try:
        result = Decimal(value)
    except InvalidOperation as error:
        raise ei.ContractError('INVALID_MEASUREMENT_DECIMAL') from error
    ei.require(result.is_finite() and abs(result.as_tuple().exponent) <= 308, 'MEASUREMENT_DECIMAL_LIMIT')
    return result


def validate_store(store):
    def variable(v):
        ei.require(local.parse_local(v['local_lower']) <= local.parse_local(v['local_upper']),
                   'REVERSED_LOCAL_CLAIM_BOUNDS')
    cp._validate_store(store, SCHEMA, lambda c: local.parse_local(c['origin'], origin=True), variable)
    for claim in store['claims']:
        for event in claim['bundle']['events']:
            decimal_value(event['value_lexical'])
            ei.require(event['unit_lexical'].strip() == event['unit_lexical'] and bool(event['unit_lexical']),
                       'INVALID_MEASUREMENT_UNIT')
    return store


def pending_policy(store):
    validate_store(store)
    return {'profile': cp.POLICY_PROFILE, 'id': 'pending_' + cr.digest(store), 'store_sha256': cr.digest(store),
            'semantic_policy_sha256': cr.digest(SEMANTIC_POLICY), 'decisions': []}


def select(store, policy):
    if isinstance(store, Graph): store = cr.decode(store, fields=cr.MEASUREMENT_FIELDS)
    validate_store(store)
    store, policy = deepcopy((store, policy))
    selection = cp._select_validated(store, policy, SEMANTIC_POLICY)
    isolation = claim_isolation.check(cr.encode(store, fields=cr.MEASUREMENT_FIELDS), local=True, measurement=True)
    context = {'profile': SELECTION_PROFILE, 'store_sha256': cr.digest(store), 'policy': policy,
        'semantic_policy': deepcopy(SEMANTIC_POLICY), 'interpretation': 'selected_record_descriptions_only',
        'time_domain': local.TIME_DOMAIN, 'time_semantics': 'recorded_point_label_bounds',
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    result = {'profile': SELECTION_PROFILE, 'context': context, 'context_id': cr.digest(context),
        'selection': selection, 'isolation': isolation, 'records': None, 'matching': None,
        'accepted_graph_turtle': None, 'clinical_mapping_verified': False, 'source_history_verified': False,
        'physical_elapsed_time_verified': False, 'clinical_knowledge_status': 'UNKNOWN',
        'temporal_query_supported': False, 'semantic_inference_performed': False}
    if selection['blockers']:
        result['status'] = 'BLOCKED_ACCEPTED_CONFLICT'; return result
    variables = {v['id']: v for v in selection['selected']['variables']}
    clocks = {c['clock_id']: c for c in store['clocks']}
    records = []
    try:
        bt.unique(selection['selected']['events'], 'record_id')
        for event in selection['selected']['events']:
            ei.require(event['time_var'] in variables, 'MISSING_SELECTED_MEASUREMENT_TIME')
            variable = variables[event['time_var']]
            ei.require(bt.scope(variable) == bt.scope(event), 'MEASUREMENT_TIME_SCOPE_MISMATCH')
            clock = clocks[variable['clock_id']]
            records.append({'event': event, 'time_variable': variable, 'clock': clock,
                'lower_us': local.coordinate(variable['local_lower'], clock['origin']),
                'upper_us': local.coordinate(variable['local_upper'], clock['origin']),
                'numeric_value': format(decimal_value(event['value_lexical']), 'f'),
                'event_supports': selection['row_supports']['events:' + event['id']],
                'time_supports': selection['row_supports']['variables:' + variable['id']]})
    except ei.ContractError as error:
        result.update(status='INVALID_SELECTED_MEASUREMENTS', reason=str(error)); return result
    result.update(status='SELECTED_MEASUREMENT_RECORDS' if records else 'EMPTY_SELECTED_RECORDS', records=records)
    return result
