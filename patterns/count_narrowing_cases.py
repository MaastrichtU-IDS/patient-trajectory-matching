"""Small cases, the exact oracle and the snapshot reference for D43 (`count_narrowed_v1`, `store_conditions_counts`).

Shared by `test_count_narrowing.py` and the ad hoc soundness runs the decision log reports.

**The oracle** (`truth`, `reaches`), written again from the D43 design study's joint oracle. It shares no code with the
store or the engine: it decides the true result (status, relaxation ids, certainty), as `trajectory_graph_worlds` names
it, by simple temporal networks (difference constraints, Bellman-Ford) instead of enumerating integer worlds, so it runs
on snapshot patients (microsecond times) as well as on small grids.
- *possible* for a relaxation set: some choice of the `any` steps' events, of each `first` or `last` step's target and of
  n - 1 witnesses per count step whose network (the patient's ranges, ends and shared variables; the target's order with
  the worlds checker's tie-breaks; the witnesses inside the span or period; scope; relations) is consistent;
- *exact* for a choice of `any` events: every count step's count reached at its event in every world (by a disjunction
  over which other records lie outside the span, or, with no shared variable, the forced count at every breakpoint) and
  the scope and relations holding in every world (refuted by one consistent network with one constraint negated).
It covers count steps (span or period), `any`, `first` and `last` steps, `after` and `during`, every scope, and
`widen_concept`, `extend_max`, `extend_grace` and `widen_count`. Nth, value, relative and gene conditions are not covered
(a gene step is read as a plain step over the reference's gene points).

**The small cases** (`small_case`): D32's span and period generators (`derived_cases.tiny_case`, `multi` and `coupled`)
and two new ones: `during-from-count` (a `during` relation from a count step, exposures among the counted records) and
`two-counts` (two count steps, on one family or overlapping ones), and since D43b's review `two-counts-relaxed` (two
count steps each with a cheap `widen_count`, a budget of at least 1, so that pass 1 and two' relax on counts and the
binding-on-two-copies guard can fire). `run_small` checks each refined result against every choice of times
(`trajectory_graph_worlds.check_derived`) and against the truth by enumeration (`enum_truth`); under a planted defect,
with `clean`, it also flags a result the clean rule decides equal to the truth that the defective rule leaves undecided
(`lost`). `narrow_mismatches` checks `narrow` against a direct count over every integer time.

**The snapshot reference** (`ReferenceRun`): each patient's D32 answer by the existing reference (D32's derived
reference, on the ingest without mentions for profile 1.3, with D38's gene points for a gene request), and for a patient
it leaves `STEP_CONDITION_UNCERTAIN`, the oracle's truth with coverage and the excluded-records rule reapplied by the
existing reference's own functions (`compare` checks a store answer against it).
"""
from __future__ import annotations

from collections import Counter
from itertools import combinations, product
import json
import math
import random

from . import coded_only_cases as co
from . import derived_cases as dc
from . import execution_store as es
from . import execution_store_reference as ref
from . import gene_reference as gr
from . import store_conditions_counts as scn
from . import trajectory_graph as tg
from . import trajectory_graph_worlds as tw

INF = math.inf
_Z = '#zero'
GENERATORS = ('span-multi', 'span-coupled', 'window-multi', 'window-coupled', 'during-from-count', 'two-counts',
              'two-counts-relaxed')


# The oracle.

class _Net:
    """Difference constraints t(v) - t(u) <= w; consistency by Bellman-Ford."""

    def __init__(self):
        self.edges = []

    def le(self, u, v, w):  # t(v) - t(u) <= w
        if w != INF:
            self.edges.append((u, v, w))

    def consistent(self):
        nodes = {_Z}
        for u, v, _ in self.edges:
            nodes.add(u)
            nodes.add(v)
        dist = {n: 0 for n in nodes}
        for _ in range(len(nodes)):
            changed = False
            for u, v, w in self.edges:
                if dist[u] + w < dist[v]:
                    dist[v] = dist[u] + w
                    changed = True
            if not changed:
                return True
        return False


class _Patient:
    def __init__(self, patient, mentions=frozenset()):
        self.window = patient['window']
        self.ev = {}
        for e in patient['events']:
            sv = e.get('start_var', e['id'] + '.s')
            point = 'end' not in e
            fv = sv if point else e.get('end_var', e['id'] + '.f')
            f = None if point else (e['end'][0], INF if e['end'][1] is None else e['end'][1])
            self.ev[e['id']] = {'id': e['id'], 'concepts': set(e['concepts']), 's': tuple(e['start']), 'f': f, 'sv': sv,
                                'fv': fv, 'point': point, 'proper': e.get('proper') is True}
        self.mentions = set(mentions)
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        for e in self.ev.values():
            find(e['sv'])
            find(e['fv'])
            parent[find(e['sv'])] = find(e['fv'])
        self.find = find
        self.members = {}
        for e in self.ev.values():
            self.members.setdefault(find(e['sv']), []).append(e)
        self.linked = any(len(m) > 1 for m in self.members.values())

    def base(self, events):
        net, seen = _Net(), set()
        for e in events:
            root = self.find(e['sv'])
            if root in seen:
                continue
            seen.add(root)
            for x in self.members[root]:
                lo, hi = x['s']
                net.le(_Z, x['sv'], hi)
                net.le(x['sv'], _Z, -lo)
                if not x['point']:
                    flo, fhi = x['f']
                    net.le(_Z, x['fv'], fhi)
                    net.le(x['fv'], _Z, -flo)
                    net.le(x['fv'], x['sv'], -1 if x['proper'] else 0)
        return net


def _f(x):
    return x['f'] if x['f'] is not None else x['s']


def _dom(x):
    lo, hi = x['s']
    if x['f'] is not None:
        hi = min(hi, x['f'][1] - (1 if x['proper'] else 0))
    return lo, hi


def _certain_independent(e, others, n, within, window):
    a, b = _dom(e)
    others = [dict(x, s=_dom(x)) for x in others]
    if within == 'window':
        if a < window['start'] or b > window['end']:
            return False
        forced = lambda t: sum(1 for x in others if x['s'][1] <= t and x['s'][0] >= window['start'])
    else:
        forced = lambda t: sum(1 for x in others if x['s'][1] <= t and x['s'][0] >= t - within)
    points = {a, b}
    for x in others:
        for p in (x['s'][1], x['s'][0] + (within if within != 'window' else 0) + 1):
            if a <= p <= b:
                points.add(p)
    return min(forced(t) for t in points) >= n - 1


class _Question:
    def __init__(self, P, query, conditions, chosen):
        plain = [x for x in chosen if x['kind'] != 'widen_count']
        self.concept, self.relations = tw._plan(query, plain)  # parsing only
        self.steps = [s['id'] for s in query['steps']]
        self.occ = {s['id']: s.get('occurrence', 'any') for s in query['steps']}
        self.scope = query.get('scope', 'any')
        self.count = {}
        for sid, raw in ((conditions or {}).get('steps') or {}).items():
            if set(raw) != {'count'}:
                raise ValueError('ORACLE_COVERS_COUNTS_ONLY')
            w = raw['count']['within']
            self.count[sid] = (raw['count']['at_least'], 'window' if w == 'window' else w['span'])
        for x in chosen:
            if x['kind'] == 'widen_count':
                self.count[x['step']] = (x['at_least'], self.count[x['step']][1])
                if 'concept' in x:
                    self.concept[x['step']] = x['concept']
        self.P = P
        ev = sorted(P.ev.values(), key=lambda e: e['id'])
        self.family = {s: [e for e in ev if self.concept[s] in e['concepts']
                           and (s not in self.count or e['id'] not in P.mentions)] for s in self.steps}
        self.free = [s for s in self.steps if self.occ[s] == 'any']
        self.ordered = [s for s in self.steps if self.occ[s] != 'any']

    def order(self, net, s, b):
        for x in self.family[s]:
            if x is b:
                continue
            if self.occ[s] == 'first':
                strict = (x['s'], _f(x), x['id']) < (b['s'], _f(b), b['id'])
                net.le(x['sv'], b['sv'], -1 if strict else 0)
            else:
                strict = (x['s'][1], x['s'][0], _f(x), x['id']) > (b['s'][1], b['s'][0], _f(b), b['id'])
                net.le(b['sv'], x['sv'], -1 if strict else 0)

    def atoms(self, bound):
        W = self.P.window
        var = lambda x: (_Z, W['start']) if x == 'START' else (_Z, W['end']) if x == 'END' else (bound[x]['sv'], 0)
        out = []
        for s in self.steps:
            v = bound[s]['sv']
            if self.scope in ('from_start', 'within'):
                out.append((v, _Z, -W['start']))
            if self.scope == 'within':
                out.append((_Z, v, W['end']))
            if self.scope == 'after':
                out.append((v, _Z, -W['end']))
        for r in self.relations:
            (ua, ca), (ub, cb) = var(r['from']), var(r['to'])
            if r['kind'] == 'after':
                if r.get('max') is not None:
                    out.append((ua, ub, r['max'] + ca - cb))
                if r.get('min') is not None:
                    out.append((ub, ua, -r['min'] - ca + cb))
            else:
                fv = bound[r['from']]['fv']
                out.append((ub, ua, cb - ca))
                out.append((fv, ub, r.get('grace', 0) - cb))
        return out

    def witnesses(self, net, s, e, S):
        _, within = self.count[s]
        W = self.P.window
        if within == 'window':
            net.le(e['sv'], _Z, -W['start'])
            net.le(_Z, e['sv'], W['end'])
            for x in S:
                net.le(x['sv'], _Z, -W['start'])
                net.le(e['sv'], x['sv'], 0)
        else:
            for x in S:
                net.le(e['sv'], x['sv'], 0)
                net.le(x['sv'], e['sv'], within)

    def possible(self, fixed=None):
        """The free steps' binding of some consistent configuration (restricted to `fixed` when given), or None."""
        if any(not self.family[s] for s in self.steps):
            return None
        pools = [[fixed[s]] if fixed is not None else self.family[s] for s in self.free]
        for free in product(*pools):
            bound = dict(zip(self.free, free))
            for tgt in product(*[self.family[s] for s in self.ordered]):
                b2 = {**bound, **dict(zip(self.ordered, tgt))}
                counts = [s for s in self.free if s in self.count]
                witness_pools = []
                for s in counts:
                    others = [x for x in self.family[s] if x is not b2[s]]
                    if len(others) < self.count[s][0] - 1:
                        break
                    witness_pools.append(list(combinations(others, self.count[s][0] - 1)))
                else:
                    for Ss in product(*witness_pools):
                        evs = list(b2.values()) + [x for S in Ss for x in S] + \
                            [x for s in self.ordered for x in self.family[s]]
                        net = self.P.base(evs)
                        for s in self.ordered:
                            self.order(net, s, b2[s])
                        for u, v, w in self.atoms(b2):
                            net.le(u, v, w)
                        for s, S in zip(counts, Ss):
                            self.witnesses(net, s, b2[s], S)
                        if net.consistent():
                            return {s: b2[s]['id'] for s in self.free}
        return None

    def relations_certain(self, bound):
        if any(not self.family[s] for s in self.ordered):
            return False
        for tgt in product(*[self.family[s] for s in self.ordered]):
            b2 = {**bound, **dict(zip(self.ordered, tgt))}
            evs = list(b2.values()) + [x for s in self.ordered for x in self.family[s]]
            for u, v, w in self.atoms(b2):
                net = self.P.base(evs)
                for s in self.ordered:
                    self.order(net, s, b2[s])
                net.le(v, u, -w - 1)
                if net.consistent():
                    return False
        return True

    def count_certain(self, s, e):
        n, within = self.count[s]
        others = [x for x in self.family[s] if x is not e]
        if len(others) < n - 1:
            return False
        if not self.P.linked:
            return _certain_independent(e, others, n, within, self.P.window)
        W = self.P.window
        if within == 'window':
            for below in (True, False):
                net = self.P.base([e])
                if below:
                    net.le(_Z, e['sv'], W['start'] - 1)
                else:
                    net.le(e['sv'], _Z, -(W['end'] + 1))
                if net.consistent():
                    return False
        k = len(others)
        for O in combinations(others, k - n + 2):
            for sides in product((0, 1), repeat=len(O)):
                net = self.P.base([e] + list(O))
                for x, side in zip(O, sides):
                    if within == 'window':
                        if side == 0:
                            net.le(_Z, x['sv'], W['start'] - 1)
                        else:
                            net.le(x['sv'], e['sv'], -1)
                    elif side == 0:
                        net.le(e['sv'], x['sv'], -within - 1)
                    else:
                        net.le(x['sv'], e['sv'], -1)
                if within == 'window':
                    net.le(e['sv'], _Z, -W['start'])
                    net.le(_Z, e['sv'], W['end'])
                if net.consistent():
                    return False
        return True

    def exact_for(self, bound):
        return all(self.count_certain(s, bound[s]) for s in self.free if s in self.count) and self.relations_certain(bound)

    def exact(self):
        if any(not self.family[s] for s in self.steps):
            return None
        for free in product(*[self.family[s] for s in self.free]):
            bound = dict(zip(self.free, free))
            if self.exact_for(bound):
                return {s: bound[s]['id'] for s in self.free}
        return None


def truth(query, conditions, patient, mentions=frozenset()):
    """(status, relaxation ids, certainty) of a request with count steps, as `trajectory_graph_worlds` names it."""
    P = _Patient(patient, mentions)
    q = _Question(P, query, conditions, ())
    if q.exact():
        return ('exact', None, None)
    if q.possible():
        return ('possible', None, None)
    for chosen in tw.derived_sets(query, conditions):
        q = _Question(P, query, conditions, chosen)
        if q.possible():
            return ('relaxed', [x['id'] for x in chosen], 'exact' if q.exact() else 'possible')
    return ('none', None, None)


def reaches(query, conditions, patient, mentions, relaxations, binding, certainty):
    """Whether `binding` (its `any` steps) reaches `certainty` under the relaxation set named `relaxations` (None for the
    unrelaxed query): `exact`, holding in every world; `possible`, holding in some and no binding exact."""
    P = _Patient(patient, mentions)
    chosen = ()
    if relaxations is not None:
        chosen = next(c for c in tw.derived_sets(query, conditions) if [x['id'] for x in c] == relaxations)
    q = _Question(P, query, conditions, chosen)
    if any(binding.get(s) not in P.ev for s in q.free):
        return False
    fixed = {s: P.ev[binding[s]] for s in q.free}
    if any(fixed[s] not in q.family[s] for s in q.free):
        return False
    if certainty == 'exact':
        return q.exact_for(fixed)
    return q.possible(fixed) is not None and q.exact() is None


# Enumeration (the truth on small grids).

def _horizon(query, conditions, patient):
    window = patient['window']
    spans = [raw['count']['within']['span'] for raw in (conditions or {}).get('steps', {}).values()
             if 'count' in raw and raw['count']['within'] != 'window']
    return max([window['end']] + [v for e in patient['events'] for v in e['start'] + e.get('end', []) if v is not None]) \
        + 1 + max([r.get('grace', 0) for r in query.get('relations', []) if r['kind'] == 'during'] + [0] + spans)


def enum_truth(query, conditions, patient, mentions):
    """The truth by enumeration of every choice of times (`trajectory_graph_worlds`), Nth steps included."""
    events, worlds = tw.worlds(patient, _horizon(query, conditions, patient))
    ask = lambda chosen: tw._DerivedQuestion(query, conditions, chosen, events, worlds, patient['window'], mentions)
    q = ask(())
    if q.exact():
        return ('exact', None, None)
    if q.possible():
        return ('possible', None, None)
    for chosen in tw.derived_sets(query, conditions):
        q = ask(chosen)
        if q.possible():
            return ('relaxed', [x['id'] for x in chosen], 'exact' if q.exact() else 'possible')
    return ('none', None, None)


def witness_holds(result, query, conditions, patient, mentions):
    """Whether a `possible` result's witness describes a world (D43 review, M1): some choice of the patient's times in
    which each `any` step's bound event starts at its witness time and the query holds (counts reached included)."""
    events, worlds = tw.worlds(patient, _horizon(query, conditions, patient))
    by_id = {e['id']: e for e in events}
    q = tw._DerivedQuestion(query, conditions, (), events, worlds, patient['window'], mentions)
    binding, witness = result['binding'], result['witness']
    free = {s: by_id[binding[s]] for s in q.free}
    return any(all(t[free[s]['sv']] == witness[s] for s in free if s in witness) and q.holds(t, free, True)
               for t in worlds)


# The small cases.

def _tiny_patient(rng, ends, shared):
    """`derived_cases.tiny_patient`, with exposures (events with an end) at the rate `ends`."""
    events, mentions = [], set()
    for k in range(rng.randint(2, 5)):
        concepts = sorted(set(rng.choice((['x'], ['x'], ['y'], ['x', 'y']))) | {'z'})
        lo = rng.randint(0, 6)
        e = {'id': f'e{k}', 'concepts': concepts, 'start': [lo, lo + rng.choice((0, 0, 1, 2, 3))]}
        if rng.random() < ends:
            end_lo = lo + rng.randint(0, 2)
            e['end'] = [end_lo, end_lo + rng.randint(0, 3)]
            if rng.random() < 0.3 and e['end'][1] > lo:
                e['proper'] = True
        events.append(e)
        if rng.random() < 0.1:
            mentions.add(e['id'])
    if rng.random() < shared and len(events) >= 2:
        a, b = rng.sample(events, 2)
        name = a['id'] + ('.f' if 'end' in a else '.s')
        b['start_var'] = name
        b['start'] = list(a['end'] if 'end' in a else a['start'])
        b['start'][1] = max(b['start'])
        if 'end' in b and b['end'][1] < b['start'][0]:
            b['end'] = [b['start'][0], b['start'][1] + 1]
            b.pop('proper', None)
    start = rng.randint(0, 3)
    return {'patient_id': 'P', 'window': {'start': start, 'end': start + rng.randint(2, 6)}, 'events': events}, mentions


def _count(rng, sid, relaxations, n_max=3):
    within = 'window' if rng.random() < 0.4 else {'span': rng.randint(0, 4)}
    n = rng.randint(1, n_max)
    if n > 1 and rng.random() < 0.4:
        x = {'id': f'c{sid}', 'kind': 'widen_count', 'step': sid, 'cost': rng.choice(('0.5', '1')),
             'at_least': rng.randint(1, n - 1)}
        if rng.random() < 0.3:
            x['concept'] = 'z'
        relaxations.append(x)
    return {'count': {'at_least': n, 'within': within}}


def _relations(rng, steps, conds, relaxations, during_from=None):
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        if during_from is not None and i == 1:
            a = during_from
        r = {'id': f'R{i}', 'from': a, 'to': b}
        if (during_from is not None and i == 1) or (rng.random() < 0.2 and 'occurrence' not in conds.get(a, {})):
            relations.append({**r, 'kind': 'during', 'grace': rng.choice((0, 0, 1))})
            if rng.random() < 0.2:
                relaxations.append({'id': f'g{r["id"]}', 'kind': 'extend_grace', 'relation': r['id'], 'grace': 2,
                                    'cost': rng.choice(('0.5', '1'))})
            continue
        lo = rng.randint(-3, 2)
        r.update(min=lo, max=lo + rng.randint(0, 4))
        relations.append({**r, 'kind': 'after'})
        if rng.random() < 0.3:
            relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                'max': r['max'] + rng.randint(1, 3), 'cost': rng.choice(('0.5', '1'))})
    if rng.random() < 0.2:
        relations.append({'id': 'RS', 'kind': 'after', 'from': 'START', 'to': steps[0]['id'], 'min': 0,
                          'max': rng.randint(0, 4)})
    return relations


def _query(rng, steps, relations, relaxations, conds, count_relax):
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1', '2')),
             'search_budget': rng.choice((40, 2000, 2000))}
    conditions = {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': conds}
    if count_relax:
        conditions['relaxations'] = count_relax
    return query, conditions


def during_request(rng):
    """A count step A on x or z, a `during` relation from A to a second step, and sometimes a third step."""
    concept = rng.choice(('x', 'x', 'z'))
    count_relax, relaxations = [], []
    conds = {'A': _count(rng, 'A', count_relax)}
    steps = [{'id': 'A', 'concept': concept, 'occurrence': 'any'},
             {'id': 'B', 'concept': rng.choice(('x', 'y', 'z')), 'occurrence': rng.choice(('any', 'any', 'first', 'last'))}]
    if rng.random() < 0.4:
        steps.append({'id': 'C', 'concept': rng.choice(('x', 'y', 'z')), 'occurrence': rng.choice(('any', 'first'))})
    if rng.random() < 0.2:
        relaxations.append({'id': 'wB', 'kind': 'widen_concept', 'step': 'B', 'concept': 'z', 'cost': '0.5'})
    relations = _relations(rng, steps, conds, relaxations, during_from='A')
    return _query(rng, steps, relations, relaxations, conds, count_relax)


def two_counts_request(rng):
    """Two count steps A and B, on one family or overlapping ones, related either way, and sometimes a third step."""
    a = rng.choice(('x', 'x', 'z'))
    b = rng.choice((a, a, 'x', 'z', 'y'))
    count_relax, relaxations = [], []
    conds = {'A': _count(rng, 'A', count_relax), 'B': _count(rng, 'B', count_relax, n_max=2)}
    steps = [{'id': 'A', 'concept': a, 'occurrence': 'any'}, {'id': 'B', 'concept': b, 'occurrence': 'any'}]
    if rng.random() < 0.3:
        steps.append({'id': 'C', 'concept': rng.choice(('x', 'y', 'z')), 'occurrence': rng.choice(('any', 'first', 'last'))})
    relations = _relations(rng, steps, conds, relaxations)
    return _query(rng, steps, relations, relaxations, conds, count_relax)


def two_counts_relaxed_request(rng):
    """`two_counts_request` with both counts at least 2, each with a `widen_count` to one fewer at cost 0.25 (so a
    set relaxing both counts, at 0.5, is cheaper than any other relaxation the request carries), and a budget of 1 or 2:
    pass 1 and two' then relax on counts, where a binding on two copies can arise (D43b review, S4)."""
    a = rng.choice(('x', 'x', 'z'))
    b = rng.choice((a, a, 'x', 'z'))
    conds = {sid: {'count': {'at_least': rng.randint(2, 3), 'within': 'window' if rng.random() < 0.4
                             else {'span': rng.randint(0, 4)}}} for sid in 'AB'}
    count_relax = [{'id': f'c{sid}', 'kind': 'widen_count', 'step': sid, 'cost': '0.25',
                    'at_least': conds[sid]['count']['at_least'] - 1} for sid in 'AB']
    steps = [{'id': 'A', 'concept': a, 'occurrence': 'any'}, {'id': 'B', 'concept': b, 'occurrence': 'any'}]
    relaxations = []
    relations = _relations(rng, steps, conds, relaxations)
    for x in relaxations:
        x['cost'] = '1'
    query, conditions = _query(rng, steps, relations, relaxations, conds, count_relax)
    query['budget'] = rng.choice(('1', '2'))
    return query, conditions


def narrow_mismatches(prefix, families):
    """How many (family, record) pairs of `families` random families `narrow` gets wrong, against a direct count over
    every integer time, span and period (`prefix` seeds the families)."""
    rng = random.Random(prefix)
    wrong = 0
    for _ in range(families):
        k = rng.randint(1, 5)
        ranges = []
        for _ in range(k):
            lo = rng.randint(0, 8)
            ranges.append((lo, lo + rng.randint(0, 4)))
        family = [(lo, hi, lo, hi, None, None) for lo, hi in ranges]
        n = rng.randint(1, 4)
        window = {'start': rng.randint(0, 4), 'end': rng.randint(5, 12)}
        within = 'window' if rng.random() < 0.4 else rng.randint(0, 4)
        for j in range(k):
            got = {t for lo, hi in scn.narrow(family, j, n, within, window) for t in range(lo, hi + 1)}
            a, b = ranges[j]
            if within == 'window':
                a, b = max(a, window['start']), min(b, window['end'])
                able = lambda t, x: x[1] >= window['start'] and x[0] <= t
            else:
                able = lambda t, x: x[0] <= t and x[1] >= t - within
            want = {t for t in range(a, b + 1) if sum(able(t, x) for i, x in enumerate(ranges) if i != j) >= n - 1}
            wrong += got != want
    return wrong


def small_case(seed, generator):
    """(query, conditions, patient, mentions) for one seed of a generator; the request is valid."""
    if generator in GENERATORS[:4]:
        feature, kind = generator.split('-')
        return dc.tiny_case(seed, feature, kind)
    rng = random.Random(f'd43/{generator}/{seed}')
    while True:
        if generator == 'during-from-count':
            patient, mentions = _tiny_patient(rng, ends=0.6, shared=0.15)
            query, conditions = during_request(rng)
        elif generator == 'two-counts':
            patient, mentions = _tiny_patient(rng, ends=0.3, shared=0.25)
            query, conditions = two_counts_request(rng)
        else:
            patient, mentions = _tiny_patient(rng, ends=0.3, shared=0.25)
            query, conditions = two_counts_relaxed_request(rng)
        try:
            tg.patient_times(patient)
            es.Request(query, conditions)
        except (tg.TrajectoryError, es.StoreError):
            continue
        return query, conditions, patient, mentions


def refine_case(query, conditions, patient, mentions):
    """(D32's result, the refined result, how): the store's per-patient path, then `count_narrowed_v1`."""
    request = es.Request(query, conditions)
    pq = tg.PreparedQuery(request.query)
    derived = es.derive_patient(request, patient['patient_id'], patient['window'], patient['events'], mentions)
    v1 = es.match_derived(pq, request, derived)
    v2, how = scn.refine(pq, request, derived, v1)
    return v1, v2, how


def prune_violations(query, conditions, patient, mentions, log):
    """D43c rule 3: the entries of `log` (candidates the witness search dropped, `store_conditions_counts._PRUNE_LOG`)
    that some choice of the engine patient's times could use, by enumeration of every choice (`trajectory_graph_worlds`):
    a counted record whose time lies in the count's span (or period) around the bound record's, or a `first` (`last`)
    candidate that precedes (follows) every other event of its concept, with the witness search's tie-breaks."""
    if not log:
        return []
    request = es.Request(query, conditions)
    derived = es.derive_patient(request, patient['patient_id'], patient['window'], patient['events'], mentions)
    first = derived.first
    events = {e.id: e for e in tg.patient_times(first, trusted=True).events}
    _, worlds = tw.worlds(first, _horizon(query, conditions, patient))
    window = first['window']
    bad = []
    for entry in log:
        if entry['kind'] == 'count':
            x, e = events[entry['candidate']].sv, events[entry['record']].sv
            if entry['within'] == 'window':
                fits = lambda t: window['start'] <= t[x] <= t[e] <= window['end']
            else:
                fits = lambda t: 0 <= t[e] - t[x] <= entry['within']
        else:
            pick = events[entry['candidate']]
            others = [events[i] for i in entry['family'] if i != entry['candidate']]
            if entry['which'] == 'first':
                fits = lambda t: all(t[pick.sv] - t[o.sv] <= scn._strict(o, pick, 'first') for o in others)
            else:
                fits = lambda t: all(t[o.sv] - t[pick.sv] <= scn._strict(o, pick, 'last') for o in others)
        if any(fits(t) for t in worlds):
            bad.append(entry)
    return bad


def run_small(generator, seeds, prefix='d43', truth=True, clean=False, prune=False):
    """A tally over small cases: worlds violations, moved results that were not STEP_CONDITION_UNCERTAIN or do not end
    decided and complete, refined results unequal to the truth by enumeration, refined `possible` results whose witness
    describes no world, and how STEP_CONDITION_UNCERTAIN results fared. `failures` lists the first few cases that fail.

    With `clean` under a planted defect (D43b review, S4), each case is also refined with the defect off: its `how` is
    tallied as `clean: <how>` (so a guard's firings are counted), and a case the clean rule decides equal to the truth
    but the defective rule leaves as D32 answered it is a failure (`lost`).

    With `prune` (D43c rule 3), every candidate the witness search drops is checked by enumeration
    (`prune_violations`); one that some choice of times could use is a failure (`prune`), and the dropped candidates
    are tallied (`pruned candidates`)."""
    tally, failures = Counter(), []
    for seed in seeds:
        query, conditions, patient, mentions = small_case(f'{prefix}-{seed}', generator)
        if prune:
            scn._PRUNE_LOG = []
        try:
            v1, v2, how = refine_case(query, conditions, patient, mentions)
            log, scn._PRUNE_LOG = scn._PRUNE_LOG, None
        finally:
            scn._PRUNE_LOG = None
        tally['cases'] += 1
        bad = []
        if prune and log:
            tally['pruned candidates'] += len(log)
            if prune_violations(query, conditions, patient, mentions, log):
                bad.append('prune')
        if clean and scn._D43_DEFECT is not None:
            saved, scn._D43_DEFECT = scn._D43_DEFECT, None
            try:
                _, fine, fine_how = refine_case(query, conditions, patient, mentions)
            finally:
                scn._D43_DEFECT = saved
            tally[f'clean: {fine_how}'] += 1
            if json.dumps(fine) != json.dumps(v1) and json.dumps(v2) == json.dumps(v1) and \
                    (fine['status'], fine.get('relaxations'), fine.get('certainty')) == \
                    enum_truth(query, conditions, patient, mentions):
                bad.append('lost')
        if tw.check_derived(v2, query, conditions, patient, mentions):
            bad.append('violation')
        uncertain = v1['status'] == 'unresolved' and v1.get('reason') == 'STEP_CONDITION_UNCERTAIN'
        if uncertain:
            tally['STEP_CONDITION_UNCERTAIN'] += 1
        if json.dumps(v2) != json.dumps(v1):
            tally['refined'] += 1
            tally[how] += 1
            if not uncertain or v2['status'] not in ('possible', 'relaxed', 'none') or v2.get('complete') is not True:
                bad.append('moved')
            if truth and (v2['status'], v2.get('relaxations'), v2.get('certainty')) != \
                    enum_truth(query, conditions, patient, mentions):
                bad.append('truth')
            if v2['status'] == 'possible' and not witness_holds(v2, query, conditions, patient, mentions):
                bad.append('witness')
        elif uncertain:
            tally[how] += 1
        for b in bad:
            tally[b] += 1
        if bad:
            tally['failing'] += 1
            if len(failures) < 5:
                failures.append((seed, bad, how))
    return tally, failures


# The snapshot reference.

def _engine_events(p, ancestors):
    out = []
    for e in p['events']:
        event = {'id': e['event_id'], 'concepts': sorted(ref.expanded(e['concepts'], ancestors)), 'start': list(e['start'])}
        if 'end' in e:
            event['end'] = list(e['end'])
        event.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
        out.append(event)
    return out


def _uncertain(result):
    return result['status'] == 'unresolved' and result.get('reason') == 'STEP_CONDITION_UNCERTAIN'


class ReferenceRun:
    """One request over one ingest, by the existing reference, with the oracle on its STEP_CONDITION_UNCERTAIN patients.

    `d32` is the reference's answer (every patient, in patient order). For each patient it leaves STEP_CONDITION_UNCERTAIN,
    `truth` holds the oracle's truth and `expect(pid, binding)` the result that truth gives after coverage and the
    excluded-records rule, for a store binding (`None` where the result carries none)."""

    def __init__(self, ingest, query, windows, conditions):
        profile = conditions['profile']
        self.query, self.windows = query, windows
        gene_of, oracle_query = {}, query
        if profile == 'execution-store-conditions-1.3':  # D39's meaning: the ingest without those kinds, as 1.2
            ingest = co.without_kinds(ingest, conditions['exclude_kinds'])
            conditions = {**{k: v for k, v in conditions.items() if k != 'exclude_kinds'}, 'profile': ref.RELATIVE_PROFILE}
        if profile == 'execution-store-conditions-gene-coded-1.0':  # D42's meaning: the gene request on the ingest without those kinds
            ingest = co.without_kinds(ingest, conditions['exclude_kinds'])
            conditions = {**{k: v for k, v in conditions.items() if k != 'exclude_kinds'}, 'profile': gr.PROFILE}
            profile = gr.PROFILE
        ancestors = dict(ingest['closure']['ancestors'])
        self.oracle_conditions = {'profile': es.CONDITIONS_PROFILE_1_1,
                                  'steps': {sid: raw for sid, raw in conditions['steps'].items() if 'count' in raw}}
        if conditions.get('relaxations'):
            self.oracle_conditions['relaxations'] = conditions['relaxations']
        kinds = set()
        if profile == gr.PROFILE:
            dq, gene_of = gr.gene_query(ingest, query, conditions)
            oracle_query = gr._split(query, conditions)[1]  # the gene steps on their points' tag, the rest as asked
            for sid in gene_of:
                ancestors[gr._pseudo(sid)] = []
            labels = (ingest.get('provenance') or {}).get('labels') or {}
            kinds = {k for k in labels.get(gr.REPORT_KINDS_LABEL, '').split(',') if k}
        else:
            dq = ref.DerivedQuery(ingest, query, conditions)
        self.oracle_query = oracle_query
        recorders = {}
        for p in ingest['patients']:
            for e in p['events']:
                for c in ref.expanded(e['concepts'], ingest['closure']['ancestors']):
                    recorders.setdefault(c, set()).add(e['source'])
        recorders.update(dq.unit_sources)
        for sid, concept in gene_of.items():
            recorders[gr._recorded_by(sid)] = {e['source'] for p in ingest['patients'] for e in p['events']
                                               if e['kind'] in kinds and concept in ref.expanded(e['concepts'], ingest['closure']['ancestors'])}
        period = [k for k in ((ingest.get('provenance') or {}).get('labels') or {}).get('period_reading_kinds', '').split(',') if k]
        self.d32, self.truth, self._ctx = [], {}, {}
        for p in sorted(ingest['patients'], key=lambda p: p['patient_id']):
            pid, window = p['patient_id'], windows.get(p['patient_id'])
            events = _engine_events(p, ingest['closure']['ancestors'])
            excluded = list(p.get('excluded', []))
            if gene_of:
                points, undetermined = gr.patient_points(p, gene_of, ingest['closure']['ancestors'], kinds)
                events += points
                excluded += undetermined
            mentions = {e['event_id'] for e in p['events'] if e['kind'] == 'mention'}
            readings = ref.patient_readings(p, ingest['closure']['ancestors'], period)
            engine, points = ref.derived_result(dq, pid, window, events, mentions, readings)
            seen = dict(p, excluded=excluded)

            def rules(result, dq=dq, p=p, seen=seen, events=events, window=window, points=points):
                if result['status'] == 'none':
                    result = ref.coverage_rule(dq.query, dq.coverage_loads, recorders, p['coverage'], window, result)
                if window is not None and result['status'] != 'not_comparable':
                    result = ref.derived_admission(dq, result, seen, events, ancestors, window, points)
                return result
            final = rules(engine)
            self.d32.append(final)
            if _uncertain(final):
                patient = {'patient_id': pid, 'window': window, 'events': events}
                self.truth[pid] = truth(oracle_query, self.oracle_conditions, patient, mentions)
                self._ctx[pid] = (rules, patient, mentions)

    def reaches(self, pid, relaxations, binding, certainty):
        _, patient, mentions = self._ctx[pid]
        return reaches(self.oracle_query, self.oracle_conditions, patient, mentions, relaxations, binding, certainty)

    def expect(self, pid, binding):
        """The refined result the truth gives, after coverage and the excluded-records rule, with `binding` (a store
        binding, its first and last steps read for displacement)."""
        rules, _, _ = self._ctx[pid]
        status, relaxations, certainty = self.truth[pid]
        engine = {'patient_id': pid, 'status': status}
        if status == 'relaxed':
            engine.update(relaxations=relaxations, cost=None, certainty=certainty, binding=binding, method=None)
        elif status in ('exact', 'possible'):
            engine['binding'] = binding
        engine['complete'] = True
        return rules(engine)


_KEYS = ('status', 'reason', 'relaxations', 'certainty', 'complete', 'note', 'excluded_steps', 'uncovered_steps',
         'unrecorded_steps')


def _key(result):
    out = {k: result.get(k) for k in _KEYS}
    if 'relaxed_match' in result:
        out.update(relaxations=result['relaxed_match']['relaxations'], certainty=result['relaxed_match']['certainty'])
    return out


def unfiltered(result):
    if result.get('reason') == 'FILTERED':
        return {k: v for k, v in result.items() if k != 'reason'}
    return result


def compare(run, results):
    """(mismatches, tally) of a store answer's results (patient order; a `decided` answer's subset) against a
    `ReferenceRun`."""
    want = {r['patient_id']: r for r in run.d32}
    bad, tally = [], Counter()
    for got in results:
        pid = got['patient_id']
        w = want[pid]
        if json.dumps(unfiltered(got)) == json.dumps(w):
            if _uncertain(w):
                tally['kept'] += 1
            continue
        if not _uncertain(w):
            bad.append((pid, 'differs from the D32 reference', got, w))
            continue
        tally['refined'] += 1
        tally[f'refined {got["status"]}'] += 1
        binding = got.get('binding') or got.get('relaxed_match', {}).get('binding')
        status, relaxations, certainty = run.truth[pid]
        if binding is None and got['status'] == 'not_comparable' and got.get('reason') == 'ADMISSION_EXCLUDED_RECORDS' \
                and status != 'none':
            # displaced: the reference reads the binding the store would have reported; accepted when some binding to
            # the truth's certainty is displaced on exactly these steps
            tally['displaced (weak check)'] += 1
            continue
        expected = run.expect(pid, binding)
        if _key(got) != _key(expected):
            bad.append((pid, 'differs from the truth with coverage and D18', got, expected, run.truth[pid]))
            continue
        if binding is not None:
            which = got.get('relaxations') or got.get('relaxed_match', {}).get('relaxations')
            reach = certainty if status == 'relaxed' else status
            if not run.reaches(pid, which, binding, reach):
                bad.append((pid, 'binding does not reach its certainty', got, run.truth[pid]))
    return bad, tally


# Snapshot requests (the differential through the dispatcher).

DAY = 86_400_000_000
FIXTURE_POOL = {'derived': ['c:seizure', 'c:febrile', 'c:absence', 'c:febrile'], 'other': ['d:drug', 'd:a', 'c:seizure'],
                'parent': {'c:febrile': 'c:seizure', 'c:absence': 'c:seizure'}, 'genes': []}
GENE_FIXTURE_POOL = {'derived': ['c:febrile', 'c:febrile', 'c:seizure'], 'other': ['d:a', 'c:febrile', 'c:seizure'],
                     'parent': {'c:febrile': 'c:seizure'}, 'genes': ['g:SCN1A', 'g:SCN1A', 'g:CDKL5']}
COHORT_POOL = {'derived': ['seizure', 'seizure', 'febrile_seizure', 'focal_seizure', 'generalised_tonic_clonic'],
               'other': ['sodium_channel_blocker', 'valproate', 'levetiracetam', 'status_epilepticus', 'seizure'],
               'parent': {'febrile_seizure': 'seizure', 'focal_seizure': 'seizure', 'generalised_tonic_clonic': 'seizure'},
               'genes': ['gene:SCN1A', 'gene:SCN1A', 'gene:CDKL5', 'gene:PCDH19']}
SNAPSHOT_PROFILES = ('1.1', '1.2', '1.3', 'gene', 'gene-coded')


def sparse_coverage(ingest, seed, keep=0.5):
    """The ingest with each patient's coverage periods kept at the rate `keep` (so that refined `none` results meet
    the coverage rule)."""
    rng = random.Random(f'd43/sparse/{seed}')
    return {**ingest, 'dataset_id': f'{ingest["dataset_id"]}-sparse',
            'patients': [{**p, 'coverage': [c for c in p['coverage'] if rng.random() < keep]} for p in ingest['patients']]}


def snapshot_windows(rng, ingest):
    out = {}
    for p in ingest['patients']:
        if rng.random() < 0.05:
            out[p['patient_id']] = None
            continue
        first = min([e['start'][0] for e in p['events']] or [0])
        start = first + rng.randint(-30, 200) * DAY
        out[p['patient_id']] = {'start': start, 'end': start + rng.randint(60, 500) * DAY}
    return out


def _snapshot_count(rng, sid, concept, pool, relaxations, n_max=4):
    n = rng.randint(1, n_max)
    within = 'window' if rng.random() < 0.35 else {'span': rng.choice((0, 1, 30, 90, 183, 183)) * DAY}
    if n > 1 and rng.random() < 0.5:
        x = {'id': f'c{sid}', 'kind': 'widen_count', 'step': sid, 'cost': rng.choice(('0.5', '1')), 'at_least': n - 1}
        if rng.random() < 0.3 and pool['parent'].get(concept):
            x['concept'] = pool['parent'][concept]
        relaxations.append(x)
    return {'count': {'at_least': n, 'within': within}}


def snapshot_request(rng, ingest, pool, profile):
    """(query, conditions, windows): a count step on a seizure concept, up to two other steps (any, first or last, or a
    second count), relations between them (a `during` from a count step sometimes), relaxations; for profile `gene`, a
    gene step related to the others; for `1.3`, letter mentions left out."""
    concept = rng.choice(pool['derived'])
    count_relax, relaxations = [], []
    conds = {'A': _snapshot_count(rng, 'A', concept, pool, count_relax)}
    steps = [{'id': 'A', 'concept': concept}]
    if pool['parent'].get(concept) and rng.random() < 0.3:
        relaxations.append({'id': 'wA', 'kind': 'widen_concept', 'step': 'A', 'concept': pool['parent'][concept],
                            'cost': rng.choice(('0.5', '1'))})
    for i in range(rng.randint(0, 2) if not profile.startswith('gene') else rng.randint(0, 1)):
        sid = 'BC'[i]
        other = rng.choice(pool['other'] + pool['derived'])
        step = {'id': sid, 'concept': other, 'occurrence': rng.choice(('any', 'any', 'first', 'last'))}
        if step['occurrence'] == 'any' and rng.random() < 0.25:
            conds[sid] = _snapshot_count(rng, sid, other, pool, count_relax, n_max=3)
        steps.append(step)
    if profile.startswith('gene'):
        steps.append({'id': 'G', 'concept': rng.choice(pool['genes'])})
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        r = {'id': f'R{i}', 'from': a, 'to': b}
        if rng.random() < 0.25:
            relations.append({**r, 'kind': 'during', 'grace': rng.choice((0, 30)) * DAY})
            continue
        lo = rng.choice((-60, 0, 0, 30)) * DAY
        r.update(min=lo, max=lo + rng.choice((30, 180, 365)) * DAY)
        relations.append({**r, 'kind': 'after'})
        if rng.random() < 0.3:
            relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                'max': r['max'] + 90 * DAY, 'cost': rng.choice(('0.25', '0.5'))})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1')), 'search_budget': rng.choice((300, 40000))}
    name = {'1.1': es.CONDITIONS_PROFILE_1_1, '1.2': ref.RELATIVE_PROFILE, '1.3': 'execution-store-conditions-1.3',
            'gene': gr.PROFILE, 'gene-coded': 'execution-store-conditions-gene-coded-1.0'}[profile]
    conditions = {'profile': name, 'steps': conds}
    if profile.startswith('gene'):
        conditions['steps'] = {**conds, 'G': {'gene': {'report': 'first_not_negative', 'timed_by': 'recorded_at'}}}
    if profile in ('1.3', 'gene-coded'):
        conditions['exclude_kinds'] = ['mention']
    if count_relax:
        conditions['relaxations'] = count_relax
    return query, conditions, snapshot_windows(rng, ingest)


def snapshot_case(seed, cohort, ingest, pool, profile):
    """A valid request with a count step for one seed (the module it routes to refuses none)."""
    from . import store_conditions_coded as cc
    from . import store_conditions_gene as gc
    from . import store_conditions_relative as rc
    rng = random.Random(f'd43/{cohort}/{profile}/{seed}')
    check = {'1.1': es.Request, '1.2': rc.Request, '1.3': cc.Request, 'gene': gc.Request, 'gene-coded': gc.Request}[profile]
    for _ in range(50):
        query, conditions, windows = snapshot_request(rng, ingest, pool, profile)
        try:
            check(query, conditions)
        except es.StoreError:
            continue
        return query, conditions, windows
    raise AssertionError((cohort, profile, seed))


def snapshot_compare(store, ingest, query, conditions, windows, variants=True, without=None):
    """(mismatches, tally) for one request through the dispatcher against `ReferenceRun`. With `variants`, the 2-worker
    (chunk 9, and one result to refine per pool unit, D43b), `decided` and unpruned answers must equal the 1-worker
    one; with `without` (a store built without letter mentions, for a 1.3 request), D39's identity: the 1.2 request
    through the dispatcher there gives the same results."""
    from unittest.mock import patch
    from . import store_conditions as sc
    out = sc.match(store, query, windows, conditions=conditions)
    run = ReferenceRun(ingest, query, windows, conditions)
    bad, tally = compare(run, out['results'])
    if out['derived_steps']['count_refinement']['skipped_mismatch']:
        bad.append(('re-derivation differs from the answer', out['derived_steps']['count_refinement']))
    tally.update(r['status'] for r in out['results'])
    if variants:
        # D43b review, S3: one result per pool unit, so that a request with two or more results to refine sends several
        # units to the pool (the module's own units: chunk 9).
        with patch.object(scn, '_chunk_size', lambda targets, chunk: 1):
            two = sc.match(store, query, windows, conditions=conditions, workers=2, chunk=9)
        if json.dumps(two) != json.dumps(out):
            bad.append(('2 workers', None))
        refined = out['derived_steps']['count_refinement']['refined']
        units = refined + sum(1 for r in out['results']
                              if r['status'] == 'unresolved' and r.get('reason') == 'STEP_CONDITION_UNCERTAIN')
        if units >= 2 and refined:
            tally['pool: 2+ units, refined'] += 1
        decided = sc.match(store, query, windows, conditions=conditions, detail='decided')
        listed = {r['patient_id']: r for r in out['results']}
        if any(json.dumps(r) != json.dumps(listed[r['patient_id']]) for r in decided['results']) \
                or decided['counts'] != out['counts']:
            bad.append(('decided', None))
        with patch.object(es, '_PRUNE', False):
            off = sc.match(store, query, windows, conditions=conditions)
        if json.dumps(off) != json.dumps(out):
            bad.append(('pruning off', None))
    if without is not None:
        plain = {**{k: v for k, v in conditions.items() if k != 'exclude_kinds'}, 'profile': ref.RELATIVE_PROFILE}
        other = sc.match(without, query, windows, conditions=plain)
        if [json.dumps(r) for r in other['results']] != [json.dumps(r) for r in out['results']]:
            bad.append(('D39 identity', None))
    return bad, tally
