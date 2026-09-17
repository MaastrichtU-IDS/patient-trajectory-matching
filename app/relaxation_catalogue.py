"""Bounded explicit metric alternatives for the configurable journey builder."""
from copy import deepcopy
from decimal import Decimal
import re

from app.interval_editor import keys, minutes
from app import pattern_builder as builder
from patterns import robust_relaxation as relax

MAX_OPTIONS, MAX_CHANGES, MAX_COST = 3, 6, 100
METRIC_OPERATORS = ('gap', 'duration', 'minimum_overlap')
COST = re.compile(r'(?:0|[1-9][0-9]{0,2})(?:\.[0-9]{1,6})?\Z')
EMPTY_CATALOGUE = {'relaxable_targets': [], 'max_changed_targets': 1, 'options': []}


def cost_string(value):
    if type(value) is not str or not COST.fullmatch(value) or Decimal(value) > MAX_COST:
        raise ValueError('Cost and budget must be decimal strings from 0 to 100 with at most six fractional digits')
    return value


def metadata():
    return {'max_options': MAX_OPTIONS, 'max_changes_per_option': MAX_CHANGES,
            'max_changed_targets': MAX_CHANGES, 'max_evaluations': MAX_OPTIONS + 1,
            'cost_range': ['0', str(MAX_COST)], 'fractional_digits': 6,
            'metric_operators': list(METRIC_OPERATORS), 'protected_operators': list(builder.extended.ALLEN),
            'default_catalogue': deepcopy(EMPTY_CATALOGUE),
            'composition': 'Only declared options are evaluated; changes from separate options are never combined.',
            'tie_break': 'Exact cost, original first, then option ID.'}


def compile_catalogue(query, catalogue, budget):
    """Validate every option before exclusion and compile exact minute values.

    All changes are relative to the original query. The robust relaxation engine
    remains responsible for budget exclusion, execution and least-cost selection.
    """
    builder.decompile_query(query)
    budget = cost_string(budget)
    keys(catalogue, ('relaxable_targets', 'max_changed_targets', 'options'))
    if type(catalogue['relaxable_targets']) is not list or len(catalogue['relaxable_targets']) > MAX_CHANGES:
        raise ValueError('Select at most six relaxable metric targets')
    targets = {c['id']: c for c in query['constraints'] if c['operator'] in METRIC_OPERATORS}
    allowed = []
    for name in catalogue['relaxable_targets']:
        builder.identifier(name)
        if name not in targets or name in allowed:
            raise ValueError('Relaxable targets must be distinct existing metric constraint IDs')
        allowed.append(name)
    maximum = catalogue['max_changed_targets']
    if type(maximum) is not int or not 0 <= maximum <= MAX_CHANGES:
        raise ValueError('Maximum changed targets must be an integer from 0 to 6')
    if type(catalogue['options']) is not list or len(catalogue['options']) > MAX_OPTIONS:
        raise ValueError('Declare at most three relaxation options')
    policy = {'profile': relax.PROFILE, 'kind': 'extended', 'relaxable_targets': allowed,
              'max_cost': budget, 'max_changed_targets': maximum, 'options': []}
    option_ids = {'original'}
    for option in catalogue['options']:
        keys(option, ('id', 'cost', 'changes'))
        name = builder.identifier(option['id'])
        if name in option_ids:
            raise ValueError('Option IDs must be unique and cannot be original')
        option_ids.add(name)
        cost = cost_string(option['cost'])
        if type(option['changes']) is not list or not 1 <= len(option['changes']) <= MAX_CHANGES:
            raise ValueError('Each option must change one to six targets')
        changes, changed = [], set()
        for change in option['changes']:
            if type(change) is not dict:
                raise ValueError('Each change must identify a relaxable metric target')
            target = builder.identifier(change.get('target'))
            if target not in allowed or target in changed:
                raise ValueError('Each change must identify a distinct explicitly relaxable metric target')
            changed.add(target)
            old = targets[target]
            op = old['operator']
            keys(change, ('target', 'minimum_minutes', *(('maximum_minutes',) if op != 'minimum_overlap' else ())))
            lower = minutes(change['minimum_minutes'], minimum=-1440 if op == 'gap' else 0)
            if op == 'minimum_overlap':
                if not 0 < lower < old['minimum_us']:
                    raise ValueError('Overlap must be lowered strictly and remain positive')
                changes.append({'target': target, 'minimum_us': lower})
                continue
            upper = minutes(change['maximum_minutes'], minimum=-1440 if op == 'gap' else 0)
            low_key, high_key = ('min_gap_us', 'max_gap_us') if op == 'gap' else ('minimum_us', 'maximum_us')
            if not (lower <= old[low_key] and upper >= old[high_key] and
                    (lower, upper) != (old[low_key], old[high_key])):
                raise ValueError('Gap and duration options must strictly widen the original inclusive range')
            if op == 'duration' and lower <= 0:
                raise ValueError('Duration bounds must remain positive')
            changes.append({'target': target, 'lower_us': lower, 'upper_us': upper})
        policy['options'].append({'id': name, 'cost': cost, 'changes': changes})
    relax.variants(query, policy)
    return policy
