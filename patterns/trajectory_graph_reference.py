"""Exhaustive reference for trajectory-graph matching. No engine code is reused.

Every binding of steps to candidate events is enumerated. Candidate selection, the
constraint semantics and the time model are written here again from
docs/trajectory-graph-profile.md, so a defect in the engine's arc consistency, interval
propagation, search or variable sharing does not reappear in the oracle it is compared
with. Each test runs Bellman-Ford over the whole patient's times: one node per named
variable, so events and steps that name one variable share it, and an edge from each
event's end to its start, of weight -1 for a proper event and 0 otherwise.
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
            out.append({'id': e['id'], 'concepts': set(e['concepts']), 's': start, 'f': end, 'point': False, 'var': names,
                        'proper': e.get('proper') is True})
        else:
            out.append({'id': e['id'], 'concepts': set(e['concepts']), 's': start, 'f': start, 'point': True,
                        'var': {'s': s_name, 'f': s_name}, 'proper': False})
    return out


def _facts(events, window):
    """What every choice of the patient's times satisfies, as (u, v, w): value(v) - value(u) <= w.
    Every range naming a variable bounds it; every event ends at or after it starts, and a proper
    event at least one grid step after: value(start) - value(end) <= -1."""
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
            out.append((('time', e['var']['f']), ('time', e['var']['s']), -1 if e['proper'] else 0))
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
    """A step's candidates. `first` is the least by (start range, end range, id); `last` the greatest by
    (latest start, earliest start, end range, id) (D30)."""
    found = [e for e in events if concept in e['concepts']]
    occurrence = step.get('occurrence', 'any')
    if not found or occurrence == 'any':
        return sorted(found, key=lambda e: (e['s'], e['f'], e['id']))
    if occurrence == 'first':
        return [min(found, key=lambda e: (e['s'], e['f'], e['id']))]
    return [max(found, key=lambda e: (e['s'][1], e['s'][0], e['f'], e['id']))]


def _shortest(edges, source):
    """Bellman-Ford distances from `source` over (u, v, w) edges, value(v) - value(u) <= w; the patient's
    facts are satisfiable, so there is no negative cycle."""
    distance = {source: 0}
    nodes = {u for u, _, _ in edges} | {v for _, v, _ in edges}
    for _ in range(len(nodes) + 1):
        changed = False
        for u, v, w in edges:
            if u in distance and distance[u] + w < distance.get(v, INF):
                distance[v] = distance[u] + w
                changed = True
        if not changed:
            break
    return distance


def _range(facts, name):
    """The least and greatest value the facts allow a time variable."""
    node = ('time', name)
    up = _shortest(facts, 'ZERO').get(node, INF)
    down = _shortest([(v, u, w) for u, v, w in facts], 'ZERO').get(node, INF)
    return -down, up


def _shared(events):
    """The ids of events with a variable in a component (variables joined by an exposure's start and end)
    that another event also names a variable of."""
    parent = {}
    def root(v):
        parent.setdefault(v, v)
        while parent[v] != v:
            v = parent[v]
        return v
    for e in events:
        a, b = root(e['var']['s']), root(e['var']['f'])
        if a != b:
            parent[a] = b
    namers = {}
    for e in events:
        for r in {root(e['var']['s']), root(e['var']['f'])}:
            namers[r] = namers.get(r, 0) + 1
    return {e['id'] for e in events if any(namers[root(e['var'][p])] > 1 for p in 'sf')}


def _ordered_pools(query, concept, constraints, events, facts):
    """D30, written again from docs/trajectory-graph-profile.md. Each step's candidate pool, with every safe
    uncertain `first` or `last` step on a point of its own ranging over its envelope; the uncertain steps; the
    unsafe ones; their possible targets, for the second search; and the envelope points."""
    steps = query['steps']
    occurrence = {s['id']: s.get('occurrence', 'any') for s in steps}
    pools = {s['id']: _candidates(s, concept[s['id']], events) for s in steps}
    during_from = {c[0][0] for c in constraints if isinstance(c[0], tuple) and c[0][1] == 'f'}
    shared = _shared(events)
    # First, every ordered step's picked event, whether it is certain, its family and its possible targets.
    info = {}
    for s in steps:
        sid, which = s['id'], occurrence[s['id']]
        if which == 'any' or not pools[sid]:
            continue
        a = pools[sid][0]
        family = [e for e in events if concept[sid] in e['concepts']]
        def settled(y, a=a, which=which):
            u, v = ('time', a['var']['s']), ('time', y['var']['s'])
            # first: y cannot start at or before a; last: at or after a; or the two always start together
            if not _satisfiable(facts + [(u, v, 0)] if which == 'first' else facts + [(v, u, 0)]):
                return True
            return not _satisfiable(facts + [(u, v, -1)]) and not _satisfiable(facts + [(v, u, -1)])
        def can(x, y, which=which):  # x can start at or before y (at or after, for last), the two alone
            u, v = ('time', x['var']['s']), ('time', y['var']['s'])
            return _satisfiable(facts + ([(v, u, 0)] if which == 'first' else [(u, v, 0)]))
        certain = all(settled(y) for y in family if y is not a)
        ordered = sorted(family, key=lambda e: (e['s'], e['f'], e['id']))
        possible = [a] if certain else [x for x in ordered if all(can(x, y) for y in family if y is not x)]
        info[sid] = {'picked': a, 'family': family, 'certain': certain, 'possible': possible, 'which': which}
    uncertain, safe, unsafe, wide = [], {}, [], {}
    for s in steps:
        sid = s['id']
        if sid not in info or info[sid]['certain']:
            continue
        a, family, which = info[sid]['picked'], info[sid]['family'], info[sid]['which']
        uncertain.append(sid)
        # Another step couples if it can bind an event of this family: an `any` step whose concept one carries, or
        # an ordered step one of whose possible targets carries this step's concept (D30 review, M1).
        coupled = False
        for t in steps:
            tid = t['id']
            if tid == sid:
                continue
            if occurrence[tid] == 'any':
                coupled = coupled or any(concept[tid] in e['concepts'] for e in family)
            elif tid in info:
                coupled = coupled or any(concept[sid] in e['concepts'] for e in info[tid]['possible'])
        reads_end = sid in during_from and not all(e['point'] for e in family)
        linked = any(e['id'] in shared for e in family)
        info[sid]['covered'] = not reads_end and not linked  # D31: the envelope search's argument covers it
        if not coupled and not reads_end and not linked:
            starts = [_range(facts, e['var']['s']) for e in family]
            pick = min if which == 'first' else max
            lo, hi = pick(x for x, _ in starts), pick(y for _, y in starts)
            name = ('occurrence', which, sid)
            safe[sid] = {'id': a['id'], 'concepts': set(), 's': (lo, hi), 'f': (lo, hi), 'point': True,
                         'var': {'s': name, 'f': name}, 'proper': False}
        else:
            unsafe.append(sid)
            wide[sid] = info[sid]['possible']
    for sid, e in safe.items():
        pools[sid] = [e]
    return pools, uncertain, unsafe, wide, list(safe.values()), info


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


def _best(names, pools, constraints, facts):
    best, memo = None, {}
    for chosen in product(*[pools[n] for n in names]):
        binding = dict(zip(names, chosen))
        if is_exact(binding, constraints, facts, memo):
            return 'exact'
        if best is None and is_possible(binding, constraints, facts):
            best = 'possible'
    return best


# D31: the envelope search (lever 1), switched as the engine's `_ENVELOPE` is, so that the tests can compare both.
ENVELOPE = True


def _envelope_models(query, concept, events, window, facts, pools, unsafe, info):
    """D31, written again from docs/trajectory-graph-profile.md. For a set whose unsafe uncertain steps the envelope
    search covers: the exact phase's pools (each unsafe step on a point of its own over its envelope), the possible
    phase's pools (each unsafe step on its split), the points both add, and a function giving the order facts that
    apply to a binding of the possible phase, as edges."""
    steps = [s['id'] for s in query['steps']]
    occurrence = {s['id']: s.get('occurrence', 'any') for s in query['steps']}
    start = lambda e: _range(facts, e['var']['s'])
    exact_pools, possible_pools, points = dict(pools), dict(pools), []
    def point(sid, lo, hi, tag):
        # Two steps with one concept and one occurrence have one target in every choice of times, so one point.
        name = ('envelope', tag, concept[sid], info[sid]['which'], lo, hi)
        e = {'id': info[sid]['picked']['id'], 'concepts': set(), 's': (lo, hi), 'f': (lo, hi), 'point': True,
             'var': {'s': name, 'f': name}, 'proper': False}
        points.append(e)
        return e
    for sid in unsafe:
        which, family, possible = info[sid]['which'], info[sid]['family'], info[sid]['possible']
        first = which == 'first'
        ranges = [start(e) for e in family]
        lo = min(r[0] for r in ranges) if first else max(r[0] for r in ranges)
        hi = min(r[1] for r in ranges) if first else max(r[1] for r in ranges)
        exact_pools[sid] = [point(sid, lo, hi, 'exact')]
        # The possible targets another step can bind in this phase, and how many steps can.
        blocked, others = set(), 0
        for tid in steps:
            if tid == sid:
                continue
            if occurrence[tid] == 'any':
                hits = {e['id'] for e in possible if concept[tid] in e['concepts']}
            elif tid not in info:
                hits = set()
            elif info[tid]['certain']:
                hits = {e['id'] for e in possible if e['id'] == info[tid]['picked']['id']}
            elif tid in unsafe:
                theirs = {e['id'] for e in info[tid]['possible']}
                hits = {e['id'] for e in possible if e['id'] in theirs}
            else:
                hits = set()
            if hits:
                others += 1
                blocked |= hits
        key = (lambda e: start(e)[0]) if first else (lambda e: -start(e)[1])
        lam = None
        for v in sorted({key(e) for e in possible}):
            upto = [e for e in possible if key(e) <= v]
            if any(e['id'] not in blocked for e in upto) or sum(e['id'] in blocked for e in upto) > others:
                lam = v
                break
        ordered = sorted(possible, key=lambda e: (e['s'], e['f'], e['id']))
        if lam is None:
            possible_pools[sid] = [e for e in ordered if e['id'] in blocked]
        else:
            real = [e for e in ordered if e['id'] in blocked and key(e) < lam]
            span = (lam, hi) if first else (lo, -lam)
            possible_pools[sid] = [point(sid, *span, 'possible')] + real
    def order_edges(binding):
        """(u, v, w): value(v) - value(u) <= w, for each order fact the binding meets."""
        out = []
        node = lambda sid: ('time', binding[sid]['var']['s'])
        for sid in unsafe:
            first = info[sid]['which'] == 'first'
            for tid in steps:
                if tid != sid and concept[sid] in binding[tid]['concepts']:  # a point carries no concept
                    out.append((node(tid), node(sid), 0) if first else (node(sid), node(tid), 0))
        for sid in info:
            for uid in info:
                if sid == uid:
                    continue
                ws, wu = info[sid]['which'], info[uid]['which']
                mine = {e['id'] for e in info[sid]['family']}
                theirs = {e['id'] for e in info[uid]['family']}
                if ws == 'first' and ((wu == 'last' and mine & theirs) or (wu == 'first' and theirs <= mine)):
                    out.append((node(uid), node(sid), 0))  # start(s) - start(u) <= 0
                elif ws == 'last' and wu == 'last' and theirs <= mine:
                    out.append((node(sid), node(uid), 0))  # start(u) - start(s) <= 0
        return out
    return exact_pools, possible_pools, points, order_edges


def _envelope_classify(query, concept, constraints, events, window, facts, pools, extra, unsafe, info):
    """D31: the envelope search. Exact over the exact phase's pools; otherwise any binding of the possible phase's
    pools that the patient's facts, the points, the constraints and the order facts it meets allow."""
    exact_pools, possible_pools, points, order_edges = _envelope_models(query, concept, events, window, facts, pools,
                                                                        unsafe, info)
    names = [s['id'] for s in query['steps']]
    every = _facts(events + extra + points, window)
    memo = {}
    for chosen in product(*[exact_pools[n] for n in names]):
        if is_exact(dict(zip(names, chosen)), constraints, every, memo):
            return 'exact', None
    for chosen in product(*[possible_pools[n] for n in names]):
        binding = dict(zip(names, chosen))
        if is_possible(binding, constraints, every + order_edges(binding)):
            return 'possible', 'possible'
    return None, None


def classify_ordered(query, patient, relaxations=()):
    """The best certainty any binding reaches under a relaxation set ('exact', 'possible' or None), and
    whether the order rule leaves the set undecided (D30): None, the certainty found, or 'none'. Where the envelope
    search covers every unsafe step (D31), it decides instead of the two searches."""
    concept, constraints = _constraints(query, relaxations)
    events = _events(patient)
    facts = _facts(events, patient['window'])
    pools, _, unsafe, wide, extra, info = _ordered_pools(query, concept, constraints, events, facts)
    if unsafe and ENVELOPE and all(info[s]['covered'] for s in unsafe):
        return _envelope_classify(query, concept, constraints, events, patient['window'], facts, pools, extra, unsafe,
                                  info)
    facts = _facts(events + extra, patient['window'])
    names = [s['id'] for s in query['steps']]
    best = _best(names, pools, constraints, facts)
    if not unsafe:
        return best, None
    if best:
        return best, best
    if _best(names, {**pools, **wide}, constraints, facts):
        return None, 'none'
    return None, None


def classify(query, patient, relaxations=()):
    """The best certainty any binding reaches under a relaxation set: 'exact', 'possible' or None."""
    return classify_ordered(query, patient, relaxations)[0]


def binding_reaches(query, patient, relaxations, binding_ids, certainty):
    """Whether a reported binding really has the reported certainty; a safe uncertain `first` or `last` step
    is checked on its envelope (D30)."""
    concept, constraints = _constraints(query, relaxations)
    events = _events(patient)
    facts = _facts(events, patient['window'])
    pools, _, unsafe, _, extra, info = _ordered_pools(query, concept, constraints, events, facts)
    order_edges, every = (lambda binding: []), _facts(events + extra, patient['window'])
    if unsafe and ENVELOPE and all(info[s]['covered'] for s in unsafe):
        # D31: an exact binding on the exact phase's points; a binding beside `unresolved` on the possible phase's
        # split, where a point and a target may share an identifier.
        exact_pools, possible_pools, points, order_edges = _envelope_models(query, concept, events, patient['window'],
                                                                            facts, pools, unsafe, info)
        pools = exact_pools if certainty == 'exact' else possible_pools
        every = _facts(events + extra + points, patient['window'])
    names = [s['id'] for s in query['steps']]
    options = [[e for e in pools[n] if e['id'] == binding_ids[n]] for n in names]
    for chosen in product(*options):
        binding = dict(zip(names, chosen))
        if certainty == 'exact' and is_exact(binding, constraints, every):
            return True
        if certainty != 'exact' and is_possible(binding, constraints, every + order_edges(binding)):
            return True
    return False


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
    """Status, certainty and relaxation set, with no search budget. A `first` or `last` step whose target is
    uncertain can leave a set undecided (D30). An undecided unrelaxed query is `unresolved` (reason
    OCCURRENCE_ORDER_UNCERTAIN), with the certainty its binding reached (`unverified`) or else the first relaxed
    match (`relaxed`) beside it; an undecided relaxation set is passed over, and `passed` says so."""
    pid = patient['patient_id']
    if patient.get('window') is None:
        return {'patient_id': pid, 'status': 'excluded'}
    first, open_ = classify_ordered(query, patient)
    if open_ in ('exact', 'possible'):
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'OCCURRENCE_ORDER_UNCERTAIN', 'unverified': open_}
    if first:
        return {'patient_id': pid, 'status': first}
    passed = False
    for cost, chosen in relaxation_sets(query):
        found, undecided = classify_ordered(query, patient, chosen)
        if undecided:
            passed = True
            continue
        if found:
            relaxed = {'certainty': found, 'relaxations': [x['id'] for x in chosen],
                       'cost': format((Decimal(cost.numerator) / Decimal(cost.denominator)).normalize(), 'f') if cost else '0'}
            if open_:
                return {'patient_id': pid, 'status': 'unresolved', 'reason': 'OCCURRENCE_ORDER_UNCERTAIN', 'relaxed': relaxed}
            return {'patient_id': pid, 'status': 'relaxed', **relaxed, 'passed': passed}
    if open_:
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'OCCURRENCE_ORDER_UNCERTAIN'}
    return {'patient_id': pid, 'status': 'none', 'passed': passed}
