"""Exhaustive search oracle. No index or production temporal predicate reuse."""
from itertools import product


def classify(binding, constraints):
    unknown = False
    for constraint in constraints:
        a, b = binding[constraint['left']], binding[constraint['right']]
        if (a.context_id, a.patient_bearer, a.patient_id, a.episode_id, a.clock) != (
                b.context_id, b.patient_bearer, b.patient_id, b.episode_id, b.clock):
            unknown = True
            continue
        s, e, u, v = a.start_us, a.end_us, b.start_us, b.end_us
        op = constraint['operator']
        if op == 'before':
            holds = e < u
        elif op == 'meets':
            holds = e == u
        elif op == 'overlaps':
            holds = s < u < e < v
        elif op == 'gap':
            holds = constraint['min_gap_us'] <= u - e <= constraint['max_gap_us']
        else:
            raise ValueError('Unvalidated operator')
        if not holds:
            return 'NOT_SATISFIED'
    return 'INCOMPARABLE' if unknown else 'SATISFIED'


def search(candidates, constraints):
    """Enumerate every injective tuple, retaining true and unresolved conjunctions."""
    names = sorted(candidates)
    found = []
    examined = 0
    for values in product(*(candidates[name] for name in names)):
        if len({value.process for value in values}) != len(values):
            continue
        examined += 1
        binding = dict(zip(names, values))
        status = classify(binding, constraints)
        if status != 'NOT_SATISFIED':
            found.append((binding, status))
    return found, {'complete_bindings_examined': examined}
