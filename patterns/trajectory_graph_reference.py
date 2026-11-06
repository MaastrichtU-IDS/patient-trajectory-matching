"""Exhaustive reference for trajectory-graph matching. No engine code is reused.

Every binding of steps to candidate events is enumerated. Candidate selection, the
constraint semantics and the joint feasibility test (Bellman-Ford over the binding's
difference constraints) are written here again from docs/trajectory-graph-profile.md,
so a defect in the engine's arc consistency, interval propagation or search does not
reappear in the oracle it is compared with.
"""
from decimal import Decimal
from fractions import Fraction
from itertools import combinations, product
import math

INF = math.inf


def _events(patient):
    out = []
    for e in patient.get('events', []):
        s = (e['start'][0], e['start'][1])
        if 'end' in e:
            out.append({'id': e['id'], 'concepts': set(e['concepts']), 's': s, 'point': False,
                        'f': (e['end'][0], INF if e['end'][1] is None else e['end'][1])})
        else:
            out.append({'id': e['id'], 'concepts': set(e['concepts']), 's': s, 'f': s, 'point': True})
    return out


def _candidates(step, concept, events):
    found = sorted((e for e in events if concept in e['concepts']), key=lambda e: (e['s'], e['f'], e['id']))
    occurrence = step.get('occurrence', 'any')
    return found[:1] if occurrence == 'first' else found[-1:] if occurrence == 'last' else found


def _constraints(query, relaxations):
    """(x, y, lo, hi): value(y) - value(x) in [lo, hi]; terms are 'START', 'END' or (step, part)."""
    concept = {s['id']: s['concept'] for s in query['steps']}
    relations = {r['id']: dict(r) for r in query.get('relations', [])}
    for x in relaxations:
        if x['kind'] == 'widen_concept':
            concept[x['step']] = x['concept']
        elif x['kind'] == 'extend_max':
            relations[x['relation']]['max'] = x['max']
        else:
            relations[x['relation']]['grace'] = x['grace']
    out = []
    scope = query.get('scope', 'any')
    for s in query['steps']:
        if scope in ('from_start', 'within'):
            out.append(('START', (s['id'], 's'), 0, INF))
        if scope == 'within':
            out.append(((s['id'], 's'), 'END', 0, INF))
        if scope == 'after':
            out.append(('END', (s['id'], 's'), 0, INF))
    for r in relations.values():
        a = r['from'] if r['from'] in ('START', 'END') else (r['from'], 's')
        b = r['to'] if r['to'] in ('START', 'END') else (r['to'], 's')
        if r['kind'] == 'after':
            out.append((a, b, -INF if r.get('min') is None else r['min'], INF if r.get('max') is None else r['max']))
        else:
            out.append((a, b, 0, INF))
            out.append(((r['from'], 'f'), b, -INF, r.get('grace', 0)))
    return concept, out


def _span(term, binding, window):
    if term in ('START', 'END'):
        v = window['start'] if term == 'START' else window['end']
        return v, v
    event = binding[term[0]]
    return event[term[1]]


def is_exact(binding, constraints, window):
    for x, y, lo, hi in constraints:
        (xl, xh), (yl, yh) = _span(x, binding, window), _span(y, binding, window)
        if yl - xh < lo or yh - xl > hi:
            return False
    return True


def is_possible(binding, constraints, window):
    """Bellman-Ford from a virtual source; a negative cycle means no joint choice of times."""
    names = ['ZERO', 'START', 'END'] + [f'{s}.{p}' for s in binding for p in 'sf']
    edges = []  # (u, v, w): value(v) - value(u) <= w
    def bound(u, v, w):
        if w != INF:
            edges.append((u, v, w))
    for anchor, value in (('START', window['start']), ('END', window['end'])):
        bound('ZERO', anchor, value); bound(anchor, 'ZERO', -value)
    for s, e in binding.items():
        for p in 'sf':
            bound('ZERO', f'{s}.{p}', e[p][1]); bound(f'{s}.{p}', 'ZERO', -e[p][0])
        bound(f'{s}.f', f'{s}.s', 0)
        if e['point']:
            bound(f'{s}.s', f'{s}.f', 0)
    name = lambda t: t if t in ('START', 'END') else f'{t[0]}.{t[1]}'
    for x, y, lo, hi in constraints:
        bound(name(x), name(y), hi)
        bound(name(y), name(x), -lo)
    distance = {n: 0 for n in names}
    for _ in range(len(names)):
        changed = False
        for u, v, w in edges:
            if distance[u] + w < distance[v]:
                distance[v] = distance[u] + w
                changed = True
        if not changed:
            return True
    return False


def classify(query, patient, relaxations=()):
    """The best certainty any binding reaches under a relaxation set: 'exact', 'possible' or None."""
    window = patient['window']
    concept, constraints = _constraints(query, relaxations)
    events = _events(patient)
    names = [s['id'] for s in query['steps']]
    pools = [_candidates(s, concept[s['id']], events) for s in query['steps']]
    best = None
    for chosen in product(*pools):
        binding = dict(zip(names, chosen))
        if is_exact(binding, constraints, window):
            return 'exact'
        if best is None and is_possible(binding, constraints, window):
            best = 'possible'
    return best


def binding_reaches(query, patient, relaxations, binding_ids, certainty):
    """Whether a reported binding really has the reported certainty."""
    concept, constraints = _constraints(query, relaxations)
    events = {e['id']: e for e in _events(patient)}
    binding = {s: events[e] for s, e in binding_ids.items()}
    for step in query['steps']:
        if binding[step['id']] not in _candidates(step, concept[step['id']], list(events.values())):
            return False
    check = is_exact if certainty == 'exact' else is_possible
    return check(binding, constraints, patient['window'])


def relaxation_sets(query):
    options = query.get('relaxations', [])
    budget = Fraction(query.get('budget', 0))
    out = []
    for k in range(1, len(options) + 1):
        for chosen in combinations(options, k):
            targets = {(x['kind'], x.get('step', x.get('relation'))) for x in chosen}
            cost = sum(Fraction(x['cost']) for x in chosen)
            if len(targets) == k and cost <= budget:
                out.append((cost, k, [x['id'] for x in chosen], chosen))
    out.sort(key=lambda t: (t[0], t[1], t[2]))
    return [(cost, chosen) for cost, _, _, chosen in out]


def match(query, patient):
    """Status, certainty and relaxation set, with no search budget."""
    pid = patient['patient_id']
    if patient.get('window') is None:
        return {'patient_id': pid, 'status': 'excluded'}
    first = classify(query, patient)
    if first:
        return {'patient_id': pid, 'status': first}
    for cost, chosen in relaxation_sets(query):
        found = classify(query, patient, chosen)
        if found:
            return {'patient_id': pid, 'status': 'relaxed', 'certainty': found,
                    'relaxations': [x['id'] for x in chosen],
                    'cost': format((Decimal(cost.numerator) / Decimal(cost.denominator)).normalize(), 'f') if cost else '0'}
    return {'patient_id': pid, 'status': 'none'}
