"""Exhaustive reference for trajectory-graph matching. No engine code is reused.

Every binding of steps to candidate events is enumerated. Candidate selection, the
constraint semantics and the time model are written here again from
docs/trajectory-graph-profile.md, so a defect in the engine's arc consistency, interval
propagation, search or variable sharing does not reappear in the oracle it is compared
with. Each test runs Bellman-Ford over the whole patient's times: one node per named
variable, so events and steps that name one variable share it.
"""
from decimal import Decimal
from fractions import Fraction
from itertools import combinations, product
import math

INF = math.inf


def _events(patient):
    """Each event with its ranges and the variables its start and end name."""
    out = []
    for e in patient.get('events', []):
        start = (e['start'][0], e['start'][1])
        s_name = e.get('start_var', e['id'] + '.s')
        if 'end' in e:
            end = (e['end'][0], INF if e['end'][1] is None else e['end'][1])
            names = {'s': s_name, 'f': e.get('end_var', e['id'] + '.f')}
            out.append({'id': e['id'], 'concepts': set(e['concepts']), 's': start, 'f': end, 'point': False, 'var': names})
        else:
            out.append({'id': e['id'], 'concepts': set(e['concepts']), 's': start, 'f': start, 'point': True,
                        'var': {'s': s_name, 'f': s_name}})
    return out


def _facts(events, window):
    """What every choice of the patient's times satisfies, as (u, v, w): value(v) - value(u) <= w.
    Every range naming a variable bounds it; every event ends at or after it starts."""
    out = []
    for anchor, value in (('START', window['start']), ('END', window['end'])):
        out += [('ZERO', anchor, value), (anchor, 'ZERO', -value)]
    for e in events:
        for part in 'sf':
            node = ('time', e['var'][part])
            lo, hi = e[part]
            out.append((node, 'ZERO', -lo))
            if hi != INF:
                out.append(('ZERO', node, hi))
        if not e['point']:
            out.append((('time', e['var']['f']), ('time', e['var']['s']), 0))
    return out


def _satisfiable(edges):
    """Bellman-Ford from a virtual source; a negative cycle means no choice of times."""
    distance = {}
    for u, v, _ in edges:
        distance[u] = distance[v] = 0
    for _ in range(len(distance) + 1):
        changed = False
        for u, v, w in edges:
            if distance[u] + w < distance[v]:
                distance[v] = distance[u] + w
                changed = True
        if not changed:
            return True
    return False


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


def _node(term, binding):
    if term in ('START', 'END'):
        return term
    return ('time', binding[term[0]]['var'][term[1]])


def is_exact(binding, constraints, facts, memo=None):
    """No choice of the patient's times violates any constraint. On an integer grid, value(y)
    - value(x) > hi is value(x) - value(y) <= -(hi + 1), and < lo is value(y) - value(x) <= lo - 1."""
    memo = {} if memo is None else memo
    for x, y, lo, hi in constraints:
        key = (_node(x, binding), _node(y, binding), lo, hi)
        if key not in memo:
            u, v = key[:2]
            memo[key] = not ((hi != INF and _satisfiable(facts + [(v, u, -(hi + 1))]))
                             or (lo != -INF and _satisfiable(facts + [(u, v, lo - 1)])))
        if not memo[key]:
            return False
    return True


def is_possible(binding, constraints, facts):
    """Some choice of the patient's times satisfies every constraint at once."""
    edges = list(facts)
    for x, y, lo, hi in constraints:
        u, v = _node(x, binding), _node(y, binding)
        if hi != INF:
            edges.append((u, v, hi))
        if lo != -INF:
            edges.append((v, u, -lo))
    return _satisfiable(edges)


def classify(query, patient, relaxations=()):
    """The best certainty any binding reaches under a relaxation set: 'exact', 'possible' or None."""
    concept, constraints = _constraints(query, relaxations)
    events = _events(patient)
    facts = _facts(events, patient['window'])
    names = [s['id'] for s in query['steps']]
    pools = [_candidates(s, concept[s['id']], events) for s in query['steps']]
    best, memo = None, {}
    for chosen in product(*pools):
        binding = dict(zip(names, chosen))
        if is_exact(binding, constraints, facts, memo):
            return 'exact'
        if best is None and is_possible(binding, constraints, facts):
            best = 'possible'
    return best


def binding_reaches(query, patient, relaxations, binding_ids, certainty):
    """Whether a reported binding really has the reported certainty."""
    concept, constraints = _constraints(query, relaxations)
    events = _events(patient)
    by_id = {e['id']: e for e in events}
    binding = {s: by_id[e] for s, e in binding_ids.items()}
    for step in query['steps']:
        if binding[step['id']] not in _candidates(step, concept[step['id']], events):
            return False
    check = is_exact if certainty == 'exact' else is_possible
    return check(binding, constraints, _facts(events, patient['window']))


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
