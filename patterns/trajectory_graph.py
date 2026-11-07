"""Trajectory-graph matching: sequence, tree and graph trajectories over uncertain event times.

A trajectory is a set of steps, each bound to one of a patient's events, and relations
between them. An event has a start time known only to lie in a range and, if it is not a
point, an end time in another range. `after` constrains the gap between two starts;
`during` puts a start inside another step's exposure, up to a grace period after it ends.
Times are variables named by the events. An event's start and end variables default to
its own, and two events that name the same variable, or one event bound to two steps,
share one time. A patient's times form one simple temporal network: each variable lies in
every range that names it, and each event ends at or after it starts. A binding is
*possible* when some choice of those times satisfies every constraint jointly, and *exact*
when every choice does. Times are integers on one declared grid (days, microseconds); the
profile does not fix the unit.

The decision procedure (docs/trajectory-graph-profile.md):

- Exact. Certainty of a constraint depends only on its two variables, through the
  patient's own network, so exactness is a binary constraint problem over each step's
  candidate events. Arc consistency decides it with no search when the relations form a
  forest (Freuder 1982).
- Possible. When the relations form a forest and every step is a point event, dynamic
  programming over unions of time intervals decides it, leaves to root, and a top-down
  pass picks the binding and a witness time for each step. The programme treats steps as
  independent, so a binding whose steps share a variable is checked against the joint
  network, and searched for instead if that check fails.
- Otherwise: backtracking over arc-consistent domains with an explicit step budget. An
  exhausted budget is reported as unresolved, never as a non-match.
- Relaxation: a cost-ordered search over declared relaxation sets, stopping at the first
  that matches.

`patterns/trajectory_graph_reference.py` enumerates every binding instead, and the tests
compare the two on seeded random trajectories of every shape.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
import json
import math
from pathlib import Path
import sys

from .temporal_stn import ZERO, Edge, Network

PROFILE_ID = 'trajectory-graph-1.0'
ANCHORS = ('START', 'END')
SCOPES = ('any', 'from_start', 'within', 'after')
OCCURRENCES = ('any', 'first', 'last')
RELAXATION_KINDS = {'widen_concept': 'step', 'extend_max': 'after', 'extend_grace': 'during'}
MAX_SEARCH_BUDGET = 10_000_000
MAX_LINKED_VARIABLES = 300  # one linked component of 300 variables solves in under a second
INF = math.inf


class TrajectoryError(ValueError):
    """A query or patient record outside the profile; the message is a stable code."""


def require(condition, code):
    if not condition:
        raise TrajectoryError(code)


def _int(value, code):
    require(type(value) is int, code)
    return value


def _cost(value):
    require(type(value) is int or (type(value) is str and value.replace('.', '', 1).isdigit()), 'INVALID_COST')
    return Fraction(value)


def decimal_text(value):
    """A cost as a plain decimal string: '0.75', '1'."""
    d = Decimal(value.numerator) / Decimal(value.denominator)
    return format(d.normalize(), 'f') if d else '0'


def validate_query(query):
    require(isinstance(query, dict) and query.get('profile') == PROFILE_ID, 'UNSUPPORTED_PROFILE')
    require(set(query) <= {'profile', 'scope', 'steps', 'relations', 'relaxations', 'budget', 'search_budget'}, 'UNKNOWN_QUERY_FIELD')
    require(query.get('scope', 'any') in SCOPES, 'UNSUPPORTED_SCOPE')
    steps = query.get('steps')
    require(isinstance(steps, list) and steps, 'NO_STEPS')
    ids = []
    for step in steps:
        require(isinstance(step, dict) and set(step) <= {'id', 'concept', 'occurrence'} and {'id', 'concept'} <= set(step), 'INVALID_STEP')
        require(type(step['id']) is str and step['id'] and step['id'] not in ANCHORS, 'INVALID_STEP_ID')
        require(type(step['concept']) is str and step['concept'], 'INVALID_CONCEPT')
        require(step.get('occurrence', 'any') in OCCURRENCES, 'UNSUPPORTED_OCCURRENCE')
        ids.append(step['id'])
    require(len(ids) == len(set(ids)), 'DUPLICATE_STEP')
    relations = query.get('relations', [])
    require(isinstance(relations, list), 'INVALID_RELATIONS')
    relation_ids = []
    for r in relations:
        require(isinstance(r, dict) and r.get('kind') in ('after', 'during'), 'UNSUPPORTED_RELATION')
        allowed = {'id', 'kind', 'from', 'to', 'min', 'max'} if r['kind'] == 'after' else {'id', 'kind', 'from', 'to', 'grace'}
        require(set(r) <= allowed and {'id', 'from', 'to'} <= set(r), 'INVALID_RELATION_FIELDS')
        require(type(r['id']) is str and r['id'], 'INVALID_RELATION_ID')
        require(r['from'] in ids or (r['kind'] == 'after' and r['from'] in ANCHORS), 'UNKNOWN_RELATION_STEP')
        require(r['to'] in ids or (r['kind'] == 'after' and r['to'] in ANCHORS), 'UNKNOWN_RELATION_STEP')
        require(r['from'] != r['to'] and not (r['from'] in ANCHORS and r['to'] in ANCHORS), 'DEGENERATE_RELATION')
        if r['kind'] == 'after':
            lo, hi = r.get('min'), r.get('max')
            require(lo is None or type(lo) is int, 'INTEGER_BOUND_REQUIRED')
            require(hi is None or type(hi) is int, 'INTEGER_BOUND_REQUIRED')
            require(lo is None or hi is None or lo <= hi, 'INVALID_BOUNDS')
        else:
            require(type(r.get('grace', 0)) is int and r.get('grace', 0) >= 0, 'INVALID_GRACE')
        relation_ids.append(r['id'])
    require(len(relation_ids) == len(set(relation_ids)), 'DUPLICATE_RELATION')
    by_relation = {r['id']: r for r in relations}
    relaxation_ids = []
    for x in query.get('relaxations', []):
        require(isinstance(x, dict) and x.get('kind') in RELAXATION_KINDS, 'UNSUPPORTED_RELAXATION')
        require(type(x.get('id')) is str and x['id'], 'INVALID_RELAXATION_ID')
        _cost(x.get('cost'))
        if x['kind'] == 'widen_concept':
            require(set(x) == {'id', 'kind', 'cost', 'step', 'concept'} and x['step'] in ids, 'INVALID_RELAXATION_FIELDS')
            require(type(x['concept']) is str and x['concept'], 'INVALID_CONCEPT')
        else:
            field = 'max' if x['kind'] == 'extend_max' else 'grace'
            require(set(x) == {'id', 'kind', 'cost', 'relation', field}, 'INVALID_RELAXATION_FIELDS')
            r = by_relation.get(x['relation'])
            require(r is not None and r['kind'] == RELAXATION_KINDS[x['kind']], 'UNKNOWN_RELAXATION_RELATION')
            original = r.get(field, 0 if field == 'grace' else None)
            require(type(x[field]) is int and original is not None and x[field] >= original, 'RELAXATION_MUST_WIDEN')
        relaxation_ids.append(x['id'])
    require(len(relaxation_ids) == len(set(relaxation_ids)), 'DUPLICATE_RELAXATION')
    _cost(query.get('budget', 0))
    budget = query.get('search_budget', 40_000)
    require(type(budget) is int and 1 <= budget <= MAX_SEARCH_BUDGET, 'INVALID_SEARCH_BUDGET')
    return query


def validate_patient(patient):
    patient_times(patient)
    return patient


def patient_times(patient, trusted=False):
    """Validate a patient record and return its `Times`, the network `match` decides over.

    `trusted` skips the record's checks, for a caller that has already enforced them (the execution
    store validates every event at ingest); the `Times` build, which also refuses an unsatisfiable
    shared variable, still runs."""
    if trusted:
        return Times(events_of(patient), patient.get('window'))
    require(isinstance(patient, dict) and set(patient) <= {'patient_id', 'window', 'events'}, 'INVALID_PATIENT')
    require(type(patient.get('patient_id')) is str and patient['patient_id'], 'INVALID_PATIENT_ID')
    window = patient.get('window')
    if window is not None:
        require(isinstance(window, dict) and set(window) == {'start', 'end'}, 'INVALID_WINDOW')
        require(_int(window['start'], 'INVALID_WINDOW') <= _int(window['end'], 'INVALID_WINDOW'), 'INVALID_WINDOW')
    ids = []
    for event in patient.get('events', []):
        require(isinstance(event, dict) and set(event) <= {'id', 'concepts', 'start', 'end', 'start_var', 'end_var'}
                and {'id', 'concepts', 'start'} <= set(event), 'INVALID_EVENT')
        require(type(event['id']) is str and event['id'], 'INVALID_EVENT_ID')
        require(isinstance(event['concepts'], list) and event['concepts'] and all(type(c) is str for c in event['concepts']), 'INVALID_EVENT_CONCEPTS')
        start = event['start']
        require(isinstance(start, list) and len(start) == 2 and all(type(v) is int for v in start) and start[0] <= start[1], 'INVALID_EVENT_START')
        if 'end' in event:
            end = event['end']
            require(isinstance(end, list) and len(end) == 2 and type(end[0]) is int and (end[1] is None or type(end[1]) is int), 'INVALID_EVENT_END')
            require(end[0] >= start[0] and (end[1] is None or end[1] >= end[0]), 'INVALID_EVENT_END')
        for field in ('start_var', 'end_var'):
            require(field not in event or (type(event[field]) is str and event[field]), 'INVALID_EVENT_VARIABLE')
        # A point's end is its start, so it has no end variable of its own.
        require('end_var' not in event or 'end' in event, 'INVALID_EVENT_VARIABLE')
        ids.append(event['id'])
    require(len(ids) == len(set(ids)), 'DUPLICATE_EVENT')
    # Refuses a shared variable no time satisfies, or a linked component too large to solve.
    return Times(events_of(patient), window)


@dataclass(frozen=True)
class Event:
    id: str
    concepts: frozenset
    s: tuple  # (earliest, latest) start, as recorded
    f: tuple  # (earliest, latest) end; latest may be INF for an ongoing exposure
    point: bool
    sv: str  # start variable
    fv: str  # end variable; a point's is its start variable


def events_of(patient):
    out = []
    for e in patient.get('events', []):
        s = tuple(e['start'])
        sv = e.get('start_var', f"{e['id']}.s")
        if 'end' in e:
            f = (e['end'][0], INF if e['end'][1] is None else e['end'][1])
            out.append(Event(e['id'], frozenset(e['concepts']), s, f, False, sv, e.get('end_var', f"{e['id']}.f")))
        else:
            out.append(Event(e['id'], frozenset(e['concepts']), s, s, True, sv, sv))
    return out


class Times:
    """A patient's times as one simple temporal network, before any trajectory applies.

    Each variable lies in the intersection of every range that names it, and every event
    ends at or after it starts. `dom[v]` is the tightest range those facts leave `v`, and
    `gap(x, y)` the largest value of y - x they allow. A variable no event links to another
    ranges independently, so all-pairs distances are kept only inside larger components.
    The anchors START and END are constants.
    """

    def __init__(self, events, window):
        self.events, self.window = events, window
        bounds = {}
        for e in events:
            for v, (lo, hi) in ((e.sv, e.s), (e.fv, e.f)):
                old = bounds.get(v, (-INF, INF))
                bounds[v] = (max(old[0], lo), min(old[1], hi))
        require(all(lo <= hi for lo, hi in bounds.values()), 'EMPTY_SHARED_VARIABLE')
        parent = {v: v for v in bounds}
        def find(v):
            while parent[v] != v:
                parent[v] = parent[parent[v]]
                v = parent[v]
            return v
        orders = {(e.sv, e.fv) for e in events if e.sv != e.fv}
        for sv, fv in orders:
            parent[find(sv)] = find(fv)
        groups, ordered = {}, {}
        for v in bounds:
            groups.setdefault(find(v), []).append(v)
        for sv, fv in orders:
            ordered.setdefault(find(sv), []).append((sv, fv))
        # All-pairs shortest paths are cubic in a component. A lone exposure (two variables)
        # costs nothing; components that sharing builds may together cost no more than one
        # of MAX_LINKED_VARIABLES, checked before any is solved.
        require(sum(len(m) ** 3 for m in groups.values() if len(m) > 2) <= MAX_LINKED_VARIABLES ** 3,
                'SHARED_VARIABLE_COMPONENT_LIMIT')
        self.component, self.distance, self.dom = {}, {}, {}
        for root, members in groups.items():
            for v in members:
                self.component[v] = root
            if len(members) == 1:
                self.dom[members[0]] = bounds[members[0]]
                continue
            # d[i][j] bounds value(j) - value(i); node 0 is time zero, member k is node k + 1.
            index = {v: k + 1 for k, v in enumerate(members)}
            n = len(members) + 1
            d = [[0 if i == j else INF for j in range(n)] for i in range(n)]
            for v, k in index.items():
                lo, hi = bounds[v]
                d[0][k], d[k][0] = hi, -lo
            for sv, fv in ordered[root]:
                d[index[fv]][index[sv]] = min(d[index[fv]][index[sv]], 0)  # start - end <= 0
            for k in range(n):
                dk = d[k]
                for i in range(n):
                    dik = d[i][k]
                    if dik != INF:
                        d[i] = [a if a <= dik + b else dik + b for a, b in zip(d[i], dk)]
            require(all(d[i][i] >= 0 for i in range(n)), 'EMPTY_SHARED_VARIABLE')
            for v, k in index.items():
                self.dom[v] = (-d[k][0], d[0][k])
            self.distance[root] = (index, d)
        if window is not None:
            for a in ANCHORS:
                key = ('anchor', a)
                value = window['start'] if a == 'START' else window['end']
                self.dom[key], self.component[key] = (value, value), key

    def linked(self, x, y):
        """Whether the patient's facts relate the two variables beyond their own ranges."""
        return x == y or (self.component[x] == self.component[y] and self.component[x] in self.distance)

    def gap(self, x, y):
        """The largest value(y) - value(x) the patient's facts allow; zero for one variable."""
        if x == y:
            return 0
        if self.linked(x, y):
            index, d = self.distance[self.component[x]]
            return d[index[x]][index[y]]
        return self.dom[y][1] - self.dom[x][0]


def applied(query, relaxations):
    """The query with a relaxation set applied: wider concepts, wider bounds."""
    concepts = {s['id']: s['concept'] for s in query['steps']}
    relations = {r['id']: dict(r) for r in query.get('relations', [])}
    for x in relaxations:
        if x['kind'] == 'widen_concept':
            concepts[x['step']] = x['concept']
        elif x['kind'] == 'extend_max':
            relations[x['relation']]['max'] = x['max']
        else:
            relations[x['relation']]['grace'] = x['grace']
    return concepts, [relations[r['id']] for r in query.get('relations', [])]


def candidates(step, concept, events):
    matching = sorted((e for e in events if concept in e.concepts), key=lambda e: (e.s, e.f, e.id))
    occurrence = step.get('occurrence', 'any')
    if occurrence == 'first':
        return matching[:1]
    if occurrence == 'last':
        return matching[-1:]
    return matching


@dataclass(frozen=True)
class Constraint:
    """value(y) - value(x) lies in [lo, hi]. A term is (step, 's' or 'f') or (anchor, None);
    under a binding it names a time variable."""
    x: tuple
    y: tuple
    lo: float
    hi: float
    source: str


def compile_constraints(query, relations):
    out = []
    scope = query.get('scope', 'any')
    for step in query['steps']:
        s = (step['id'], 's')
        if scope in ('from_start', 'within'):
            out.append(Constraint(('START', None), s, 0, INF, 'scope'))
        if scope == 'within':
            out.append(Constraint(s, ('END', None), 0, INF, 'scope'))
        if scope == 'after':
            out.append(Constraint(('END', None), s, 0, INF, 'scope'))
    for r in relations:
        term = lambda name, part: (name, None) if name in ANCHORS else (name, part)
        if r['kind'] == 'after':
            lo = -INF if r.get('min') is None else r['min']
            hi = INF if r.get('max') is None else r['max']
            out.append(Constraint(term(r['from'], 's'), term(r['to'], 's'), lo, hi, r['id']))
        else:
            out.append(Constraint((r['from'], 's'), (r['to'], 's'), 0, INF, r['id']))
            out.append(Constraint((r['from'], 'f'), (r['to'], 's'), -INF, r.get('grace', 0), r['id']))
    return out


def _var(term, binding):
    """The time variable a term names under a binding: the bound event's, or an anchor."""
    name, part = term
    if name in ANCHORS:
        return ('anchor', name)
    event = binding[name]
    return event.sv if part == 's' else event.fv


def certain(c, binding, times):
    """Every choice of the patient's times satisfies the constraint. Two terms on one
    variable differ by exactly zero."""
    x, y = _var(c.x, binding), _var(c.y, binding)
    return -times.gap(y, x) >= c.lo and times.gap(x, y) <= c.hi


def plausible(c, binding, times):
    """Necessary for possibility: some choice of the patient's times satisfies this
    constraint alone. The patient's network is minimal, so the two variables' difference
    can take any value in [-gap(y, x), gap(x, y)]."""
    x, y = _var(c.x, binding), _var(c.y, binding)
    return max(c.lo, -times.gap(y, x)) <= min(c.hi, times.gap(x, y))


def _node(v):
    return f'anchor:{v[1]}' if isinstance(v, tuple) else f'var:{v}'


def feasible(binding, constraints, times):
    """Joint satisfiability of a (partial) binding: a simple temporal network over the
    variables its events name. The patient's network is minimal, so its distances between
    those variables carry every fact about them; the rest of the patient can be left out."""
    edges = []
    def edge(left, right, upper):
        if upper != INF:
            edges.append(Edge(f'e{len(edges)}', left, right, upper))
    variables = sorted({v for e in binding.values() for v in (e.sv, e.fv)}) + [('anchor', a) for a in ANCHORS]
    for v in variables:
        lo, hi = times.dom[v]
        edge(_node(v), ZERO, hi); edge(ZERO, _node(v), -lo)
    for u, v in combinations(variables, 2):
        if times.linked(u, v):
            edge(_node(v), _node(u), times.gap(u, v)); edge(_node(u), _node(v), times.gap(v, u))
    for c in constraints:
        if all(t[0] in ANCHORS or t[0] in binding for t in (c.x, c.y)):
            x, y = _node(_var(c.x, binding)), _node(_var(c.y, binding))
            edge(y, x, c.hi)
            edge(x, y, -c.lo)
    return Network({_node(v) for v in variables}, edges)


class Budget:
    def __init__(self, limit):
        self.limit, self.steps = limit, 0

    def spend(self):
        self.steps += 1
        return self.steps <= self.limit


def _components(step_ids, pairs):
    """Undirected step graph; returns whether it is a forest and the adjacency."""
    parent = {s: s for s in step_ids}
    def find(s):
        while parent[s] != s:
            parent[s] = parent[parent[s]]
            s = parent[s]
        return s
    forest = True
    for a, b in pairs:
        ra, rb = find(a), find(b)
        if ra == rb:
            forest = False
        else:
            parent[ra] = rb
    adjacency = {s: set() for s in step_ids}
    for a, b in pairs:
        adjacency[a].add(b); adjacency[b].add(a)
    return forest, adjacency


def _split(constraints, step_ids):
    """Unary constraints (one step, possibly an anchor) and binary ones grouped by step pair."""
    unary, binary = {s: [] for s in step_ids}, {}
    for c in constraints:
        steps = sorted({t[0] for t in (c.x, c.y) if t[0] not in ANCHORS})
        if len(steps) == 1:
            unary[steps[0]].append(c)
        else:
            binary.setdefault(tuple(steps), []).append(c)
    return unary, binary


def _arc_consistency(domains, binary, test, times):
    """AC-3 over step domains; `test` decides one constraint for two bound events."""
    arcs = [(a, b) for a, b in binary] + [(b, a) for a, b in binary]
    queue = list(arcs)
    while queue:
        a, b = queue.pop(0)
        key = (a, b) if (a, b) in binary else (b, a)
        kept = [ea for ea in domains[a]
                if any(all(test(c, {a: ea, b: eb}, times) for c in binary[key]) for eb in domains[b])]
        if len(kept) != len(domains[a]):
            domains[a] = kept
            if not kept:
                return False
            queue.extend((c, a) for c, d in arcs if d == a and c != b)
    return True


def _backtrack(order, domains, accept, budget):
    """First binding in candidate order that `accept` admits at every prefix; or None."""
    binding = {}
    def walk(k):
        if k == len(order):
            return True
        step = order[k]
        for event in domains[step]:
            if not budget.spend():
                return False
            binding[step] = event
            if accept(binding) and walk(k + 1):
                return True
            del binding[step]
            if budget.steps > budget.limit:
                return False
        return False
    return dict(binding) if walk(0) else None


def _tree_assign(step_ids, adjacency, domains, binary, times):
    """Backtrack-free assignment of an arc-consistent forest: each child takes the first
    event supporting its parent's."""
    binding, seen = {}, set()
    for root in step_ids:
        if root in seen:
            continue
        binding[root] = domains[root][0]
        seen.add(root)
        stack = [root]
        while stack:
            p = stack.pop()
            for c in sorted(adjacency[p]):
                if c in seen:
                    continue
                key = (p, c) if (p, c) in binary else (c, p)
                binding[c] = next(e for e in domains[c]
                                  if all(certain(x, {p: binding[p], c: e}, times) for x in binary[key]))
                seen.add(c)
                stack.append(c)
    return binding


def _shift(intervals, lo, hi):
    """{t - d : t in intervals, d in [lo, hi]} as merged intervals."""
    out = sorted((a - hi, b - lo) for a, b in intervals)
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _intersect(tagged, intervals):
    out = []
    for lo, hi, event in tagged:
        for a, b in intervals:
            l, h = max(lo, a), min(hi, b)
            if l <= h:
                out.append((l, h, event))
    return out


def _point_forest(step_ids, adjacency, domains, unary, binary, times):
    """Possible on a forest of point steps, each step's time chosen independently. Each
    step's domain is a set of times tagged by event, inside the event's variable's range;
    leaves restrict parents (t_p must reach some child time), then a top-down pass takes the
    latest admissible time at each step.

    Steps whose events share a variable, or are linked through the patient's own facts,
    are coupled in fact, so this is a relaxation of the problem: None is a sound refutation,
    but a binding is a solution only if its steps' variables are unlinked (`decide` checks)."""
    tagged = {}
    for s in step_ids:
        out = []
        for e in domains[s]:
            lo, hi = times.dom[e.sv]
            for c in unary[s]:
                if c.x[0] in ANCHORS:  # t - anchor in [lo, hi]
                    a = times.window['start'] if c.x[0] == 'START' else times.window['end']
                    lo, hi = max(lo, a + c.lo), min(hi, a + c.hi)
                else:  # anchor - t in [lo, hi]
                    a = times.window['start'] if c.y[0] == 'START' else times.window['end']
                    lo, hi = max(lo, a - c.hi), min(hi, a - c.lo)
            if lo <= hi:
                out.append((lo, hi, e))
        tagged[s] = out
    def gap(p, c):
        """Bounds on t_c - t_p from every constraint between the two steps."""
        key = (p, c) if (p, c) in binary else (c, p)
        lo, hi = -INF, INF
        for x in binary[key]:
            if x.x[0] == p:
                lo, hi = max(lo, x.lo), min(hi, x.hi)
            else:
                lo, hi = max(lo, -x.hi), min(hi, -x.lo)
        return lo, hi
    order, parent, seen = [], {}, set()
    for root in step_ids:
        if root in seen:
            continue
        seen.add(root); parent[root] = None
        stack = [root]
        while stack:
            p = stack.pop()
            order.append(p)
            for c in sorted(adjacency[p]):
                if c not in seen:
                    seen.add(c); parent[c] = p; stack.append(c)
    for c in reversed(order):
        if not tagged[c]:
            return None
        p = parent[c]
        if p is not None:
            lo, hi = gap(p, c)
            if lo > hi:
                # The pair's constraints contradict each other: t_c - t_p has no value,
                # whatever events are bound.
                return None
            tagged[p] = _intersect(tagged[p], _shift([(a, b) for a, b, _ in tagged[c]], lo, hi))
    at, binding = {}, {}
    for s in order:
        p = parent[s]
        options = tagged[s]
        if p is not None:
            lo, hi = gap(p, s)
            options = _intersect(options, [(at[p] + lo, at[p] + hi)])
        lo, hi, event = max(options, key=lambda o: (o[1], o[2].id))
        at[s], binding[s] = hi, event
    return binding, at


class _Plan:
    """What `decide` derives from a query and one relaxation set before it sees any patient."""

    __slots__ = ('concepts', 'constraints', 'unary', 'binary', 'forest', 'adjacency')

    def __init__(self, query, relaxations):
        step_ids = [s['id'] for s in query['steps']]
        self.concepts, relations = applied(query, relaxations)
        self.constraints = compile_constraints(query, relations)
        self.unary, self.binary = _split(self.constraints, step_ids)
        self.forest, self.adjacency = _components(step_ids, list(self.binary))


class PreparedQuery:
    """A validated query with everything patient-independent computed once: the declared relaxation
    sets in search order, and for each set (and the unrelaxed query) its applied concepts,
    compiled constraints, unary/binary split and step graph. `match_prepared` reads them for
    every patient. The query must not be changed after it is prepared."""

    def __init__(self, query):
        self.query = validate_query(query)
        self.step_ids = [s['id'] for s in query['steps']]
        self.budget = query.get('search_budget', 40_000)
        self.sets = relaxation_sets(query)
        self._plans = {}

    def plan(self, relaxations=()):
        key = tuple(x['id'] for x in relaxations)
        if key not in self._plans:
            self._plans[key] = _Plan(self.query, relaxations)
        return self._plans[key]


def decide(query, times, relaxations, budget, plan=None):
    """Exact and possible decisions for one relaxation set over one patient's `Times`.
    `plan` is the set's `_Plan` when the caller has prepared it."""
    steps = query['steps']
    step_ids = [s['id'] for s in steps]
    plan = plan or _Plan(query, relaxations)
    concepts, constraints, unary, binary = plan.concepts, plan.constraints, plan.unary, plan.binary
    forest, adjacency = plan.forest, plan.adjacency
    base = {s['id']: candidates(s, concepts[s['id']], times.events) for s in steps}
    result = {'forest': forest}

    # Exact: unary certainty, then arc consistency on the pairwise certainty relation.
    domains = {s: [e for e in base[s] if all(certain(c, {s: e}, times) for c in unary[s])] for s in step_ids}
    if not all(domains.values()) or not _arc_consistency(domains, binary, certain, times):
        result['exact'] = {'found': False, 'complete': True, 'method': 'arc_consistency'}
    elif forest:
        result['exact'] = {'found': True, 'complete': True, 'method': 'forest_arc_consistency',
                           'binding': _tree_assign(step_ids, adjacency, domains, binary, times)}
    else:
        def accept(b):
            return all(certain(c, b, times) for c in constraints
                       if all(t[0] in ANCHORS or t[0] in b for t in (c.x, c.y)))
        meter = Budget(budget)
        order = sorted(step_ids, key=lambda s: len(domains[s]))
        found = _backtrack(order, domains, accept, meter)
        result['exact'] = {'found': found is not None, 'complete': found is not None or meter.steps <= meter.limit,
                           'method': 'bounded_backtracking', 'search_steps': meter.steps}
        if found is not None:
            result['exact']['binding'] = found
    if result['exact']['found']:
        return result

    # Possible: pairwise plausibility prunes soundly (every witness of a binding satisfies
    # each constraint alone, with the patient's facts); the joint test is a temporal network.
    domains = {s: [e for e in base[s] if all(plausible(c, {s: e}, times) for c in unary[s])] for s in step_ids}
    pruned = all(domains.values()) and _arc_consistency(domains, binary, plausible, times)
    points = all(e.point for s in step_ids for e in domains[s])
    if not pruned:
        result['possible'] = {'found': False, 'complete': True, 'method': 'arc_consistency'}
        return result
    if forest and points:
        solved = _point_forest(step_ids, adjacency, domains, unary, binary, times)
        if solved is None:
            result['possible'] = {'found': False, 'complete': True, 'method': 'point_forest_intervals'}
            return result
        binding, at = solved
        if not any(times.linked(binding[a].sv, binding[b].sv) for a, b in combinations(step_ids, 2)):
            result['possible'] = {'found': True, 'complete': True, 'method': 'point_forest_intervals',
                                  'binding': binding, 'witness': at}
            return result
        # Steps share a variable: the independent times may disagree, so check the binding
        # jointly, and search if it fails.
        network = feasible(binding, constraints, times)
        if network.feasible:
            witness = network.witness()
            result['possible'] = {'found': True, 'complete': True, 'method': 'point_forest_intervals_checked',
                                  'binding': binding, 'witness': {s: witness[_node(e.sv)] for s, e in binding.items()}}
            return result
    meter = Budget(budget)
    order = sorted(step_ids, key=lambda s: len(domains[s]))
    found = _backtrack(order, domains, lambda b: feasible(b, constraints, times).feasible, meter)
    result['possible'] = {'found': found is not None, 'complete': found is not None or meter.steps <= meter.limit,
                          'method': 'bounded_backtracking', 'search_steps': meter.steps}
    if found is not None:
        witness = feasible(found, constraints, times).witness()
        result['possible'].update(binding=found, witness={s: witness[_node(e.sv)] for s, e in found.items()})
    return result


def relaxation_sets(query):
    """Every declared relaxation set within the budget, at most one per target, cheapest first."""
    options = query.get('relaxations', [])
    limit = _cost(query.get('budget', 0))
    target = lambda x: (x['kind'], x.get('step') or x.get('relation'))
    sets = []
    for k in range(1, len(options) + 1):
        for chosen in combinations(options, k):
            if len({target(x) for x in chosen}) < k:
                continue
            cost = sum((_cost(x['cost']) for x in chosen), Fraction(0))
            if cost <= limit:
                sets.append((cost, k, tuple(x['id'] for x in chosen), chosen))
    return [(cost, chosen) for cost, _, _, chosen in sorted(sets, key=lambda s: s[:3])]


def _binding_ids(binding):
    return {s: e.id for s, e in sorted(binding.items())}


def match(query, patient):
    """One patient's result. Statuses: exact, possible, relaxed, unresolved, none, excluded."""
    return match_prepared(PreparedQuery(query), patient)


def match_prepared(pq, patient, trusted=False):
    """`match` for a `PreparedQuery`; `trusted` skips the patient record's checks (see `patient_times`)."""
    query = pq.query
    times = patient_times(patient, trusted)
    pid, window = patient['patient_id'], patient.get('window')
    if window is None:
        return {'patient_id': pid, 'status': 'excluded', 'reason': 'NO_INDEX_WINDOW'}
    budget = pq.budget
    first = decide(query, times, (), budget, pq.plan())
    if first['exact']['found']:
        return {'patient_id': pid, 'status': 'exact', 'binding': _binding_ids(first['exact']['binding']),
                'method': first['exact']['method'], 'complete': True}
    if first['possible']['found']:
        out = {'patient_id': pid, 'status': 'possible', 'binding': _binding_ids(first['possible']['binding']),
               'witness': dict(sorted(first['possible']['witness'].items())), 'method': first['possible']['method'],
               'complete': first['exact']['complete']}
        if not first['exact']['complete']:
            out['note'] = 'EXACT_SEARCH_STOPPED'
        return out
    # A stopped unrelaxed search ruled nothing out: unresolved, whatever a relaxation finds.
    stopped = not first['possible']['complete']
    skipped = False
    for cost, chosen in pq.sets:
        r = decide(query, times, chosen, budget, pq.plan(chosen))
        winner = 'exact' if r['exact']['found'] else 'possible' if r['possible']['found'] else None
        if winner is None:
            skipped = skipped or not r['possible']['complete']
            continue
        relaxed = {'relaxations': [x['id'] for x in chosen], 'cost': decimal_text(cost), 'certainty': winner,
                   'binding': _binding_ids(r[winner]['binding']), 'method': r[winner]['method']}
        if stopped:
            return {'patient_id': pid, 'status': 'unresolved', 'reason': 'SEARCH_BUDGET_EXHAUSTED',
                    'relaxed_match': relaxed, 'complete': False}
        out = {'patient_id': pid, 'status': 'relaxed', **relaxed,
               'complete': not skipped and (winner == 'exact' or r['exact']['complete'])}
        if skipped:
            out['note'] = 'CHEAPER_RELAXATION_SEARCH_STOPPED'
        return out
    if stopped:
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'SEARCH_BUDGET_EXHAUSTED', 'complete': False}
    out = {'patient_id': pid, 'status': 'none', 'complete': not skipped}
    if skipped:
        out['note'] = 'RELAXATION_SEARCH_STOPPED'
    return out


def run(query, patients):
    pq = PreparedQuery(query)
    return {'profile': PROFILE_ID, 'results': [match_prepared(pq, p) for p in patients]}


def main(argv=None):
    """`python -m patterns.trajectory_graph QUERY.json PATIENTS.json` prints the results."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(main.__doc__, file=sys.stderr)
        return 2
    with open(argv[0]) as q, open(argv[1]) as p:
        print(json.dumps(run(json.load(q), json.load(p)), indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
