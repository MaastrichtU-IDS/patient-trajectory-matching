"""Finite-world oracle for small test fixtures; does not use the STN/compiler.

Enumerates original variable domains, source constraints, and direct query
inequalities. Exceeding the explicit workload cap is an error, not truncation.
"""
from itertools import product
from math import prod


def worlds(source, scope, limit=100000):
    variables = sorted((v for v in source['variables'] if (v['patient_id'], v['episode_id']) == scope), key=lambda v: v['id'])
    names = {v['id'] for v in variables}
    if prod(v['upper_us'] - v['lower_us'] + 1 for v in variables) > limit:
        raise ValueError('REFERENCE_ENUMERATION_LIMIT')
    constraints = [c for c in source['constraints'] if c['left_var'] in names]
    events = [e for e in source['events'] if (e['patient_id'], e['episode_id']) == scope]
    result = []
    for values in product(*(range(v['lower_us'], v['upper_us'] + 1) for v in variables)):
        assignment = dict(zip((v['id'] for v in variables), values))
        if any(assignment[c['left_var']] - assignment[c['right_var']] > c['upper_us'] for c in constraints):
            continue
        if any(assignment[e['start_var']] >= assignment[e['end_var']] for e in events):
            continue
        result.append(assignment)
    return result


def holds(binding, constraint, assignment):
    a, b = binding[constraint['left']], binding[constraint['right']]
    s, e, u, v = (assignment[x] for x in (a['start_var'], a['end_var'], b['start_var'], b['end_var']))
    op = constraint['operator']
    if op == 'before': return e < u
    if op == 'meets': return e == u
    if op == 'overlaps': return s < u < e < v
    if op == 'gap': return constraint['min_gap_us'] <= u - e <= constraint['max_gap_us']
    raise ValueError('Unsupported operator')


def classify(source, binding, constraints, feasible_worlds):
    if not feasible_worlds:
        raise ValueError('INCONSISTENT_SOURCE')
    variables = {v['id']: v for v in source['variables']}
    comparable, unknown = [], False
    for c in constraints:
        a, b = binding[c['left']], binding[c['right']]
        if variables[a['start_var']]['clock_id'] == variables[b['start_var']]['clock_id']:
            comparable.append(c)
        else:
            unknown = True
    truth = [all(holds(binding, c, assignment) for c in comparable) for assignment in feasible_worlds]
    if not any(truth): return 'IMPOSSIBLE'
    if unknown: return 'INCOMPARABLE'
    return 'CERTAIN' if all(truth) else 'POSSIBLE'
