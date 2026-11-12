"""Trajectory-graph matching: sequence, tree and graph trajectories over uncertain event times.

A trajectory is a set of steps, each bound to one of a patient's events, and relations
between them. An event has a start time known only to lie in a range and, if it is not a
point, an end time in another range. `after` constrains the gap between two starts;
`during` puts a start inside another step's exposure, up to a grace period after it ends.
Times are variables named by the events. An event's start and end variables default to
its own, and two events that name the same variable, or one event bound to two steps,
share one time. A patient's times form one simple temporal network: each variable lies in
every range that names it, and each event ends at or after it starts (a `proper` event
strictly after, by at least one grid step). A binding is
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
- `first` and `last` (D30): the earliest event by start range, and the latest by latest
  start. When the patient's facts leave it uncertain which event is first or last, the step
  is decided on a point over the range the true first (last) start can take, where that is
  exact, and the result is otherwise unresolved (`decide_ordered`).

`patterns/trajectory_graph_reference.py` enumerates every binding instead, and the tests
compare the two on seeded random trajectories of every shape.
"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from itertools import combinations
from operator import attrgetter
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
        require(isinstance(event, dict) and set(event) <= {'id', 'concepts', 'start', 'end', 'start_var', 'end_var', 'proper'}
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
        # A proper event ends strictly after it starts; a point, whose end is its start, cannot be one.
        require('proper' not in event or (type(event['proper']) is bool and 'end' in event), 'INVALID_EVENT_PROPER')
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
    proper: bool = False  # ends strictly after it starts (start - end <= -1); a point never is


def events_of(patient):
    """The patient's events as `Event`s. Each is built without the frozen dataclass's `__init__`, which
    sets every field through `object.__setattr__`: the object is the same, an `Event` whose attribute
    dictionary holds the same fields in the same order, so equality, hashing and repr are unchanged."""
    out = []
    new = object.__new__
    for e in patient.get('events', []):
        event = new(Event)
        d = event.__dict__
        s = tuple(e['start'])
        sv = e.get('start_var', f"{e['id']}.s")
        if 'end' in e:
            f = (e['end'][0], INF if e['end'][1] is None else e['end'][1])
            d['id'], d['concepts'], d['s'], d['f'], d['point'], d['sv'], d['fv'], d['proper'] = (
                e['id'], frozenset(e['concepts']), s, f, False, sv, e.get('end_var', f"{e['id']}.f"),
                e.get('proper') is True)
        else:
            d['id'], d['concepts'], d['s'], d['f'], d['point'], d['sv'], d['fv'], d['proper'] = (
                e['id'], frozenset(e['concepts']), s, s, True, sv, sv, False)
        out.append(event)
    return out


class Times:
    """A patient's times as one simple temporal network, before any trajectory applies.

    Each variable lies in the intersection of every range that names it, and every event
    ends at or after it starts, a proper event at least one grid step after (start - end <= -1
    rather than <= 0). `dom[v]` is the tightest range those facts leave `v`, and
    `gap(x, y)` the largest value of y - x they allow. A variable no event links to another
    ranges independently, so all-pairs distances are kept only inside larger components.
    The anchors START and END are constants.
    """

    def __init__(self, events, window):
        self.events, self.window = events, window
        # Each variable's range is the intersection of the ranges naming it. A first naming gives its range
        # as it is (its intersection with (-inf, inf)), and naming a variable again with the same range
        # (a point's end is its start) leaves it as it is.
        bounds, names = {}, 0
        get = bounds.get
        for e in events:
            sv, fv, s, f = e.sv, e.fv, e.s, e.f
            old = get(sv)
            bounds[sv] = (s[0], s[1]) if old is None else (max(old[0], s[0]), min(old[1], s[1]))
            if fv == sv:
                names += 1
                # An exposure naming one variable twice ends as it starts, which a proper event cannot.
                require(not e.proper, 'EMPTY_PROPER_EVENT')
                if f != s:
                    old = bounds[sv]
                    bounds[sv] = (max(old[0], f[0]), min(old[1], f[1]))
            else:
                names += 2
                old = get(fv)
                bounds[fv] = (f[0], f[1]) if old is None else (max(old[0], f[0]), min(old[1], f[1]))
        require(all(lo <= hi for lo, hi in bounds.values()), 'EMPTY_SHARED_VARIABLE')
        self.distance = {}
        # Some variable is named by two events (an event naming one variable twice is one name).
        self.shared = names != len(bounds)
        if not self.shared:
            # Then every component is one event's: its start alone (a point, or an exposure naming one
            # variable twice), or its start and end. The general path below finds the same: with no
            # variable in two events, the union of an exposure's start and end makes its end the root,
            # and the members are listed in the order the event names them; no component exceeds two.
            self.component, self.dom = {v: v for v in bounds}, dict(bounds)
            for e in events:
                if e.sv != e.fv:
                    # Only a proper event's own order can leave its two variables no time (D25).
                    self._solve(e.fv, [e.sv, e.fv], bounds, [(e.sv, e.fv)], {(e.sv, e.fv)} if e.proper else (),
                                'EMPTY_PROPER_EVENT' if e.proper else 'EMPTY_SHARED_VARIABLE')
        else:
            parent = {v: v for v in bounds}
            def find(v):
                while parent[v] != v:
                    parent[v] = parent[parent[v]]
                    v = parent[v]
                return v
            orders = {(e.sv, e.fv) for e in events if e.sv != e.fv}
            # Orders some proper event puts on its variables: start - end <= -1 (several events may name one pair).
            strict = {(e.sv, e.fv) for e in events if e.proper}
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
            # A component no two events name a variable of is one event's own two variables (only orders join
            # variables, and an order is one event's): if it has no time, that event is proper and cannot end
            # after it starts, which is no shared variable (D25).
            namers = {}
            for e in events:
                for v in {e.sv, e.fv}:
                    namers[v] = namers.get(v, 0) + 1
            self.component, self.dom = {}, {}
            for root, members in groups.items():
                alone = all(namers[v] == 1 for v in members)
                self._solve(root, members, bounds, ordered.get(root), strict,
                            'EMPTY_PROPER_EVENT' if alone else 'EMPTY_SHARED_VARIABLE')
        if window is not None:
            for a in ANCHORS:
                key = ('anchor', a)
                value = window['start'] if a == 'START' else window['end']
                self.dom[key], self.component[key] = (value, value), key
        self._records = None
        self._by_concept = {}
        self._occurrences = {}

    def _solve(self, root, members, bounds, orders, strict=(), empty='EMPTY_SHARED_VARIABLE'):
        """One component's variables: their ranges, and all-pairs distances if there are several.
        `orders` are its (start, end) pairs, end at or after start; those in `strict`, a proper
        event's, end at least one grid step after start. `empty` is the refusal if they leave no time."""
        for v in members:
            self.component[v] = root
        if len(members) == 1:
            self.dom[members[0]] = bounds[members[0]]
            return
        # d[i][j] bounds value(j) - value(i); node 0 is time zero, member k is node k + 1.
        index = {v: k + 1 for k, v in enumerate(members)}
        n = len(members) + 1
        d = [[0 if i == j else INF for j in range(n)] for i in range(n)]
        for v, k in index.items():
            lo, hi = bounds[v]
            d[0][k], d[k][0] = hi, -lo
        for sv, fv in orders:
            # start - end <= 0, or <= -1 for a proper event
            d[index[fv]][index[sv]] = min(d[index[fv]][index[sv]], -1 if (sv, fv) in strict else 0)
        for k in range(n):
            dk = d[k]
            for i in range(n):
                dik = d[i][k]
                if dik != INF:
                    d[i] = [a if a <= dik + b else dik + b for a, b in zip(d[i], dk)]
        require(all(d[i][i] >= 0 for i in range(n)), empty)
        for v, k in index.items():
            self.dom[v] = (-d[k][0], d[0][k])
        self.distance[root] = (index, d)

    def candidate_records(self, concept):
        """The kernel records of the events carrying `concept`, in `candidates` order.

        A record is (start lo, start hi, end lo, end hi, keys, event): the tightest ranges of the event's
        two variables, and the components of its variables if another event has a variable in one of
        them (None otherwise). Two variables are linked (`linked`) exactly when they lie in one
        component, since a component of several variables always has a distance matrix and a lone
        variable is its own component; so the variables of two events can be linked only if they are
        one event, or both have keys and the keys meet.

        `candidates` sorts the matching events by (s, f, id). Filtering the events sorted once by that
        key keeps the same order, ties included, since both sorts are stable."""
        found = self._by_concept.get(concept)
        if found is None:
            if self._records is None:
                self._records = self._make_records()
            found = self._by_concept[concept] = [r for r in self._records if concept in r[_EVENT].concepts]
        return found

    def occurrence(self, concept, which):
        """What a `first` or `last` step on `concept` binds, and how sure that is (D30): an `_Occurrence`, or
        None when no event carries the concept. Cached per concept and occurrence."""
        key = (concept, which)
        if key not in self._occurrences:
            records = self.candidate_records(concept)
            self._occurrences[key] = _Occurrence(self, records, which) if records else None
        return self._occurrences[key]

    def _make_records(self):
        order, dom = sorted(self.events, key=attrgetter('s', 'f', 'id')), self.dom
        if not self.shared:
            return [(*dom[e.sv], *dom[e.fv], None, e) for e in order]
        component, holders = self.component, {}
        for e in self.events:
            for k in {component[e.sv], component[e.fv]}:
                holders[k] = holders.get(k, 0) + 1
        out = []
        for e in order:
            keys = frozenset((component[e.sv], component[e.fv]))
            out.append((*dom[e.sv], *dom[e.fv], keys if any(holders[k] > 1 for k in keys) else None, e))
        return out

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
    """A step's candidate events, in the order `decide` tries them (`Times.candidate_records` gives the
    same, as kernel records)."""
    matching = sorted((e for e in events if concept in e.concepts), key=lambda e: (e.s, e.f, e.id))
    occurrence = step.get('occurrence', 'any')
    if occurrence == 'first':
        return matching[:1]
    if occurrence == 'last':
        return [max(matching, key=lambda e: _LAST_KEY(e))] if matching else []
    return matching


def last_key(e):
    """The order `last` takes the greatest of (D30): latest start, then earliest start, then end range, then
    identifier. `first` takes the least of (start range, end range, identifier). Before D30 `last` took the
    greatest of `first`'s order, the largest *earliest* start, so an event known only to lie in [60, 365] lost
    to one exactly at 69, though it may well be the later. On exact times the two orders are the same."""
    return (e.s[1], e.s[0], e.f, e.id)


# The order `last` takes the greatest of; a switch, so that the tests can show what the D30 fix moves. This and
# the other switches here (`_ORDER_RULE`, `_LEVERS`) are module globals for tests and attribution runs in one
# process; worker processes (execution_store's pool) import the module afresh and never see them changed.
_LAST_KEY = last_key
_LAST_KEY_BEFORE_D30 = attrgetter('s', 'f', 'id')
# D30 review: levers that change no output, only cost (the sweep in `_Arc.revise`, the second search of an unsafe
# step without its exact phase, the direct certainty test in `_Occurrence`). A switch, so that tests can show that
# every output is byte-identical with them on and off.
_LEVERS = True


class _Occurrence:
    """A `first` or `last` step's target in one patient, and whether the patient's facts make it certain (D30).

    `record` is the kernel record the order picks. It is *certain* when every other event carrying the concept
    starts strictly after it (before it, for `last`) under every choice of times, or certainly at the same time
    (the order then breaks the tie). Otherwise the step's true target may be another event:
    - the true first start lies in the *envelope* [least earliest start, least latest start] of the concept's
      events (for `last`, [greatest earliest, greatest latest]), over the patient's ranges (`Times.dom`); when
      the events are independent, every time in it is the first start in some choice of times;
    - `possible` lists the events that can be first (last) in some choice of times, tested pair by pair, in
      candidate order: a superset of the true targets.
    `unlinked` says no event of the concept names a variable another event names, `points` that every one is a
    point, so its end is its start."""

    __slots__ = ('times', 'records', 'which', 'record', 'certain', 'unlinked', 'points', 'envelope',
                 '_possible', '_synthetic')

    def __init__(self, times, records, which):
        self.times, self.records, self.which = times, records, which
        self.record = records[0] if which == 'first' else max(records, key=lambda r: _LAST_KEY(r[_EVENT]))
        self.unlinked = all(r[_KEYS] is None for r in records)
        self.points = all(r[_EVENT].point for r in records)
        if which == 'first':
            self.envelope = (min(r[_SLO] for r in records), min(r[_SHI] for r in records))
        else:
            self.envelope = (max(r[_SLO] for r in records), max(r[_SHI] for r in records))
        gap, a = times.gap, self.record[_EVENT].sv
        def settled(y):
            # first: y certainly after a, or certainly at a's time; last: y certainly before a, or at its time
            g = gap(y, a) if which == 'first' else gap(a, y)
            return g <= -1 or (g == 0 and (gap(a, y) if which == 'first' else gap(y, a)) == 0)
        if self.unlinked and _LEVERS:
            # D30 review, lever 4. With no shared variable, gap(x, y) is y's latest minus x's earliest, so y is
            # settled when it starts after a ends its range (before it starts, for last), or when both are one
            # identical point. The same verdict as `settled`, without gap calls.
            r0 = self.record
            alo, ahi = r0[_SLO], r0[_SHI]
            if which == 'first':
                far = lambda r: r[_SLO] > ahi
            else:
                far = lambda r: r[_SHI] < alo
            self.certain = all(far(r) or (r[_SLO] == r[_SHI] == alo == ahi) for r in records if r is not r0)
        else:
            self.certain = all(settled(r[_EVENT].sv) for r in records if r is not self.record)
        self._possible = self._synthetic = None

    def possible(self):
        """The records that can be first (last) in some choice of times, in candidate order: each can start at
        or before (after) every other event of the concept, pair by pair."""
        if self._possible is None:
            records, gap = self.records, self.times.gap
            if self.unlinked:
                # gap(x, y) is y's latest minus x's earliest: x can precede every other event when its earliest
                # start is at most the least latest start of the others (greatest earliest, for last).
                if self.which == 'first':
                    his = sorted(r[_SHI] for r in records)
                    keep = lambda r: r[_SLO] <= (his[1] if len(his) > 1 and r[_SHI] == his[0] else his[0])
                else:
                    los = sorted((r[_SLO] for r in records), reverse=True)
                    keep = lambda r: r[_SHI] >= (los[1] if len(los) > 1 and r[_SLO] == los[0] else los[0])
                self._possible = [r for r in records if len(records) == 1 or keep(r)]
            else:
                if self.which == 'first':
                    can = lambda x, y: gap(x[_EVENT].sv, y[_EVENT].sv) >= 0
                else:
                    can = lambda x, y: gap(y[_EVENT].sv, x[_EVENT].sv) >= 0
                self._possible = [x for x in records if all(can(x, y) for y in records if y is not x)]
        return self._possible

    def synthetic(self):
        """A point record standing for the step's true target's start: the picked event, with its own variable
        ranging over the envelope, linked to nothing. Used only where that is exact (`_order_plan`)."""
        if self._synthetic is None:
            times, e = self.times, self.record[_EVENT]
            name = f'\x00{self.which}\x00{e.id}'
            while name in times.dom:
                name += '\x00'
            lo, hi = self.envelope
            times.dom[name], times.component[name] = (lo, hi), name
            event = object.__new__(Event)
            d = event.__dict__
            d['id'], d['concepts'], d['s'], d['f'], d['point'], d['sv'], d['fv'], d['proper'] = (
                e.id, e.concepts, (lo, hi), (lo, hi), True, name, name, False)
            self._synthetic = (lo, hi, lo, hi, None, event)
        return self._synthetic


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


# The pairwise kernel. `certain` and `plausible` decide a constraint through `Times.gap`, one call per
# constraint and pair of events. Arc consistency asks the same question of every pair of candidate
# events of two related steps, so it works on records instead (`Times.candidate_records`: the four range
# ends of an event's variables, its keys, the event) and decides each pair by a closed form of the same
# test.
#
# Why the closed form is the same test. Take a constraint value(y) - value(x) in [lo, hi] between steps
# a and b, with a's event ra and b's event rb, u the variable a's term names and v b's. When u and v are
# not linked, `Times.gap(x, y)` is dom[y].hi - dom[x].lo, so with (ulo, uhi) and (vlo, vhi) their ranges:
#   x is a's term:  certain   = -(uhi - vlo) >= lo  and  vhi - ulo <= hi
#                   plausible = max(lo, -(uhi - vlo)) <= min(hi, vhi - ulo)
#   x is b's term:  certain   = -(vhi - ulo) >= lo  and  uhi - vlo <= hi
#                   plausible = max(lo, -(vhi - ulo)) <= min(hi, uhi - vlo)
# Times and bounds are integers (the profile validates them), and only an upper end (uhi, vhi) or a
# bound can be infinite: a variable's lower end is always finite (a range's start is), lo is never +inf
# and hi never -inf (`_arc_spec` refuses the closed form otherwise). So integer arithmetic is exact, no inf - inf arises, and each comparison
# can move ra's term to the other side with its value unchanged:
#   certain, x a's:  vlo >= uhi + lo  (always true if lo = -inf),  vhi <= ulo + hi  (always true if hi = inf)
#   certain, x b's:  vhi <= ulo - lo  (always true if lo = -inf),  vlo >= uhi - hi  (always true if hi = inf)
# An infinite uhi gives vlo >= inf, false, as -(inf - vlo) >= lo is; an infinite vhi fails vhi <= t as
# inf - ulo <= hi does. `max(p, q) <= min(r, s)` holds exactly when p <= r, p <= s, q <= r and q <= s,
# and one of the four, -(uhi - vlo) <= vhi - ulo (or its mirror), holds for any two ranges, so
#   plausible, x a's:  lo <= hi,  vhi >= ulo + lo  (if lo > -inf),  vlo <= uhi + hi  (if hi < inf)
#   plausible, x b's:  lo <= hi,  vlo <= uhi - lo  (if lo > -inf),  vhi >= ulo - hi  (if hi < inf)
# with the same treatment of infinite ends. Every test is a bound on one end of rb's ranges set by ra;
# several lower bounds on one end are their maximum, several upper bounds their minimum.
#
# A proper event's start - end <= -1 changes nothing here: it links only the event's own two variables,
# so it narrows that event's ranges (`dom`, solved with the -1 edge) and its own start-to-end distance,
# never the joint range of two unlinked variables, which stays the product of their ranges.
#
# A pair whose variables can be linked (one event bound to both steps, or events with variables in one
# component) is decided by the original `certain` or `plausible`, and never by the closed form. So every
# pair is decided exactly as before. A revision filters a domain, so domains keep their order; the
# queue is the one AC-3 always used; the tree assignment takes the same first supporting event.

_SLO, _SHI, _FLO, _FHI, _KEYS, _EVENT = range(6)


def _arc_spec(a, constraints, kind):
    """The closed form of `kind` ('certain' or 'plausible') for every constraint between step a (or an
    anchor) and one other step b: (lowers, uppers), each ((b end, ((a end, k), ...)), ...), meaning
    rb[b end] >= max(ra[a end] + k) and rb[b end] <= min(ra[a end] + k); ends index a record. None where
    the closed form is not used (a pair the constraints contradict under `plausible`, or a bound that is
    +inf as a minimum or -inf as a maximum): every pair then takes the original test."""
    lowers, uppers = {}, {}
    for c in constraints:
        lo, hi = c.lo, c.hi
        if lo == INF or hi == -INF or (kind == 'plausible' and lo > hi):
            return None
        x_is_a = c.x[0] == a
        a_part, b_part = (c.x[1], c.y[1]) if x_is_a else (c.y[1], c.x[1])
        alo, ahi = (_FLO, _FHI) if a_part == 'f' else (_SLO, _SHI)
        blo, bhi = (_FLO, _FHI) if b_part == 'f' else (_SLO, _SHI)
        terms = []
        if kind == 'certain' and x_is_a:
            terms += [(lowers, blo, ahi, lo)] if lo != -INF else []
            terms += [(uppers, bhi, alo, hi)] if hi != INF else []
        elif kind == 'certain':
            terms += [(uppers, bhi, alo, -lo)] if lo != -INF else []
            terms += [(lowers, blo, ahi, -hi)] if hi != INF else []
        elif x_is_a:
            terms += [(lowers, bhi, alo, lo)] if lo != -INF else []
            terms += [(uppers, blo, ahi, hi)] if hi != INF else []
        else:
            terms += [(uppers, blo, ahi, -lo)] if lo != -INF else []
            terms += [(lowers, bhi, alo, -hi)] if hi != INF else []
        for side, b_end, a_end, k in terms:
            side.setdefault(b_end, []).append((a_end, k))
    freeze = lambda side: tuple((end, tuple(terms)) for end, terms in sorted(side.items()))
    return freeze(lowers), freeze(uppers)


def _bounds(spec, ra):
    """The lower and upper bounds `spec` puts on rb's range ends, given ra."""
    lowers, uppers = spec
    return ([(end, max(ra[i] + k for i, k in terms)) for end, terms in lowers],
            [(end, min(ra[i] + k for i, k in terms)) for end, terms in uppers])


def _holds(lows, highs, rb):
    for end, t in lows:
        if rb[end] < t:
            return False
    for end, t in highs:
        if rb[end] > t:
            return False
    return True


class _Arc:
    """One direction (a, b) of a related step pair under one test: which events of a some event of b
    supports."""

    __slots__ = ('a', 'b', 'constraints', 'test', 'spec')

    def __init__(self, a, b, constraints, test):
        self.a, self.b, self.constraints, self.test = a, b, constraints, test
        self.spec = _arc_spec(a, constraints, test.__name__)

    def original(self, ra, rb, times):
        """The test as `certain` or `plausible` decides it, constraint by constraint."""
        a, b, test = self.a, self.b, self.test
        binding = {a: ra[_EVENT], b: rb[_EVENT]}
        return all(test(c, binding, times) for c in self.constraints)

    def holds(self, ra, rb, times):
        """`original`, by the closed form when the two events' variables cannot be linked."""
        if self.spec is None or rb is ra or (ra[_KEYS] is not None and rb[_KEYS] is not None
                                             and not ra[_KEYS].isdisjoint(rb[_KEYS])):
            return self.original(ra, rb, times)
        return _holds(*_bounds(self.spec, ra), rb)

    def revise(self, da, db, times):
        """The events of da that some event of db supports, in da's order."""
        spec = self.spec
        if spec is None:
            return [ra for ra in da if any(self.original(ra, rb, times) for rb in db)]
        lowers, uppers = spec
        in_db = {id(rb) for rb in db}
        shared = [rb for rb in db if rb[_KEYS] is not None]
        kept = []
        if len(lowers) == 1 and len(uppers) == 1 and len(lowers[0][1]) == 1 and len(uppers[0][1]) == 1:
            # The common case, one bound on each side, each from one constraint, scanned as pairs.
            (bl, ((al, kl),)), = lowers
            (bu, ((au, ku),)), = uppers
            if _LEVERS and len(db) >= SWEEP_MIN:
                return _sweep(self, da, db, times, bl, al, kl, bu, au, ku, in_db, shared)
            pairs = [(rb[bl], rb[bu], rb) for rb in db]
            for ra in da:
                low, high = ra[al] + kl, ra[au] + ku
                if ra[_KEYS] is None and id(ra) not in in_db:
                    ok = any(x >= low and y <= high for x, y, _ in pairs)
                else:
                    linked = _linked(ra, shared, in_db)
                    ok = any(self.original(ra, rb, times) for rb in linked.values()) or \
                        any(x >= low and y <= high for x, y, rb in pairs if id(rb) not in linked)
                if ok:
                    kept.append(ra)
            return kept
        for ra in da:
            lows, highs = _bounds(spec, ra)
            if ra[_KEYS] is None and id(ra) not in in_db:
                ok = any(_holds(lows, highs, rb) for rb in db)
            else:
                linked = _linked(ra, shared, in_db)
                ok = any(self.original(ra, rb, times) for rb in linked.values()) or \
                    any(_holds(lows, highs, rb) for rb in db if id(rb) not in linked)
            if ok:
                kept.append(ra)
        return kept


SWEEP_MIN = 8  # below this many events in the other domain, the scan is as cheap as the sort


def _sweep(arc, da, db, times, bl, al, kl, bu, au, ku, in_db, shared):
    """`_Arc.revise`'s common case (one lower and one upper bound) by a sorted sweep, O((n + m) log m) instead of
    O(n * m) (D30 review, lever 2). ra is supported by an unlinked rb when rb[bl] >= low and rb[bu] <= high: so db
    is sorted by rb[bl], and from each position on the two least rb[bu] are kept (two, so that ra itself can be set
    aside when it is also in db). Linked pairs take the original test, as in the scan. The same events are kept,
    in da's order."""
    pairs = sorted(((rb[bl], rb[bu], rb) for rb in db), key=lambda p: p[0])
    xs = [p[0] for p in pairs]
    n = len(pairs)
    least = [(INF, None)] * (n + 1)
    second = [(INF, None)] * (n + 1)
    for k in range(n - 1, -1, -1):
        y, rb = pairs[k][1], pairs[k][2]
        a, b = least[k + 1], second[k + 1]
        if y < a[0]:
            least[k], second[k] = (y, rb), a
        elif y < b[0]:
            least[k], second[k] = a, (y, rb)
        else:
            least[k], second[k] = a, b
    kept = []
    for ra in da:
        low, high = ra[al] + kl, ra[au] + ku
        k = bisect_left(xs, low)
        if ra[_KEYS] is None and id(ra) not in in_db:
            ok = least[k][0] <= high
        elif ra[_KEYS] is None:
            # ra is in db and linked to nothing else: ra with itself by the original test, the rest by the sweep
            ok = arc.original(ra, ra, times) or (least[k][0] <= high if least[k][1] is not ra else second[k][0] <= high)
        else:
            linked = _linked(ra, shared, in_db)
            ok = any(arc.original(ra, rb, times) for rb in linked.values()) or \
                any(x >= low and y <= high for x, y, rb in pairs if id(rb) not in linked)
        if ok:
            kept.append(ra)
    return kept


def _linked(ra, shared, in_db):
    """The events of the other domain whose variables can be linked to ra's, by id: ra itself if that domain
    holds it, and the events with keys (`shared`) that have a variable in a component of ra's."""
    keys = ra[_KEYS]
    if keys is None:
        return {id(ra): ra} if id(ra) in in_db else {}
    return {id(rb): rb for rb in shared if not keys.isdisjoint(rb[_KEYS])}


def _arcs(binary, test):
    """Both directions of every related step pair, in the order AC-3 has always queued them."""
    pairs = [(a, b) for a, b in binary] + [(b, a) for a, b in binary]
    return [_Arc(a, b, binary[(a, b) if (a, b) in binary else (b, a)], test) for a, b in pairs]


def _arc_consistency(domains, arcs, times):
    """AC-3 over step domains of records; each arc decides its pairs as its test does."""
    queue = list(arcs)
    while queue:
        arc = queue.pop(0)
        a, b = arc.a, arc.b
        kept = arc.revise(domains[a], domains[b], times)
        if len(kept) != len(domains[a]):
            domains[a] = kept
            if not kept:
                return False
            queue.extend(other for other in arcs if other.b == a and other.a != b)
    return True


def _unary_spec(constraints, kind):
    """The closed form of a step's unary constraints: (low end, ((anchor, anchor end, k), ...), high end,
    (...)), meaning r[low end] >= max(value + k) and r[high end] <= min(value + k), from one `_arc_spec`
    per anchor with (value, value) as the anchor's range. A unary constraint names only the step's
    start, so under either test its lower bounds fall on one end of the start's range and its upper
    bounds on the other; None (the original test) if they did not, or if `_arc_spec` refuses."""
    by_anchor, lows, highs = {}, {}, {}
    for c in constraints:
        by_anchor.setdefault(c.x[0] if c.x[0] in ANCHORS else c.y[0], []).append(c)
    for anchor, cs in by_anchor.items():
        spec = _arc_spec(anchor, cs, kind)
        if spec is None:
            return None
        for side, bounds in zip((lows, highs), spec):
            for end, terms in bounds:
                side.setdefault(end, []).extend((anchor, i, k) for i, k in terms)
    if len(lows) > 1 or len(highs) > 1:
        return None
    (low_end, low_terms), = lows.items() or [(_SLO, [])]
    (high_end, high_terms), = highs.items() or [(_SHI, [])]
    return low_end, tuple(low_terms), high_end, tuple(high_terms)


def _unary_filter(records, step, constraints, spec, test, times):
    """The records whose event passes `test` on every unary constraint of the step, in order. An anchor is a
    constant, never linked to an event's variable, so the closed form applies to every record; a missing
    bound is -inf or inf, which every range end passes."""
    if not constraints:
        return list(records)
    if spec is None:
        return [r for r in records if all(test(c, {step: r[_EVENT]}, times) for c in constraints)]
    low_end, low_terms, high_end, high_terms = spec
    dom = times.dom
    # (value, value) is the anchor's range: ends 0 and 2 of a record are lower ends, 1 and 3 upper.
    low = max((dom[('anchor', a)][i % 2] + k for a, i, k in low_terms), default=-INF)
    high = min((dom[('anchor', a)][i % 2] + k for a, i, k in high_terms), default=INF)
    return [r for r in records if r[low_end] >= low and r[high_end] <= high]


def _tree_assign(step_ids, adjacency, domains, arcs, times):
    """Backtrack-free assignment of an arc-consistent forest: each child takes the first
    event supporting its parent's."""
    by_pair = {(arc.a, arc.b): arc for arc in arcs}
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
                arc = by_pair[(p, c)]
                binding[c] = next(r for r in domains[c] if arc.holds(binding[p], r, times))
                seen.add(c)
                stack.append(c)
    return {s: r[_EVENT] for s, r in binding.items()}


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

    __slots__ = ('concepts', 'constraints', 'unary', 'binary', 'forest', 'adjacency', 'occurrence',
                 'arcs', 'unary_spec', 'ordered', 'during_from')

    def __init__(self, query, relaxations):
        step_ids = [s['id'] for s in query['steps']]
        self.concepts, relations = applied(query, relaxations)
        self.constraints = compile_constraints(query, relations)
        self.unary, self.binary = _split(self.constraints, step_ids)
        self.forest, self.adjacency = _components(step_ids, list(self.binary))
        self.occurrence = {s['id']: s.get('occurrence', 'any') for s in query['steps']}
        # D30: the `first` and `last` steps, and the steps whose end a relation reads (a `during` from them).
        self.ordered = [s for s in step_ids if self.occurrence[s] != 'any']
        self.during_from = {r['from'] for r in relations if r['kind'] == 'during'}
        # The pairwise kernel's compiled tests, per test: the arcs, and each step's unary closed form.
        self.arcs = {test: _arcs(self.binary, test) for test in (certain, plausible)}
        self.unary_spec = {test: {s: _unary_spec(self.unary[s], test.__name__) for s in step_ids}
                           for test in (certain, plausible)}

    def candidates(self, step, times):
        """`candidates` of the step under this set's concept, as kernel records."""
        occurrence = self.occurrence[step]
        if occurrence == 'any':
            return times.candidate_records(self.concepts[step])
        target = times.occurrence(self.concepts[step], occurrence)
        return [] if target is None else [target.record]

    def domains(self, base, test, times):
        """Each step's candidates that pass `test` on its unary constraints."""
        spec = self.unary_spec[test]
        return {s: _unary_filter(records, s, self.unary[s], spec[s], test, times) for s, records in base.items()}


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


def decide(query, times, relaxations, budget, plan=None, override=None, exact=True):
    """Exact and possible decisions for one relaxation set over one patient's `Times`.
    `plan` is the set's `_Plan` when the caller has prepared it. `override` replaces some steps' candidate
    records (D30: `decide_ordered` passes a `first` or `last` step's envelope or its possible targets). With
    `exact=False` the exact phase is skipped, for a caller that asks only whether any binding exists (an exact
    binding is a possible one)."""
    steps = query['steps']
    step_ids = [s['id'] for s in steps]
    plan = plan or _Plan(query, relaxations)
    constraints, unary, binary = plan.constraints, plan.unary, plan.binary
    forest, adjacency = plan.forest, plan.adjacency
    base = {s: plan.candidates(s, times) for s in step_ids}
    if override:
        base.update(override)
    result = {'forest': forest}
    events = lambda domains: {s: [r[_EVENT] for r in domains[s]] for s in step_ids}

    # Exact: unary certainty, then arc consistency on the pairwise certainty relation.
    if not exact:
        result['exact'] = {'found': False, 'complete': True, 'method': 'skipped'}
    else:
        domains = plan.domains(base, certain, times)
        arcs = plan.arcs[certain]
        if not all(domains.values()) or not _arc_consistency(domains, arcs, times):
            result['exact'] = {'found': False, 'complete': True, 'method': 'arc_consistency'}
        elif forest:
            result['exact'] = {'found': True, 'complete': True, 'method': 'forest_arc_consistency',
                               'binding': _tree_assign(step_ids, adjacency, domains, arcs, times)}
        else:
            def accept(b):
                return all(certain(c, b, times) for c in constraints
                           if all(t[0] in ANCHORS or t[0] in b for t in (c.x, c.y)))
            meter = Budget(budget)
            order = sorted(step_ids, key=lambda s: len(domains[s]))
            found = _backtrack(order, events(domains), accept, meter)
            result['exact'] = {'found': found is not None, 'complete': found is not None or meter.steps <= meter.limit,
                               'method': 'bounded_backtracking', 'search_steps': meter.steps}
            if found is not None:
                result['exact']['binding'] = found
    if result['exact']['found']:
        return result

    # Possible: pairwise plausibility prunes soundly (every witness of a binding satisfies
    # each constraint alone, with the patient's facts); the joint test is a temporal network.
    domains = plan.domains(base, plausible, times)
    pruned = all(domains.values()) and _arc_consistency(domains, plan.arcs[plausible], times)
    if not pruned:
        result['possible'] = {'found': False, 'complete': True, 'method': 'arc_consistency'}
        return result
    domains = events(domains)
    points = all(e.point for s in step_ids for e in domains[s])
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


# D30: the order rule for `first` and `last` steps. Switch, for the tests that show what each fix moves.
_ORDER_RULE = True


def _coupled(plan, s, targets, step_ids):
    """Whether another step can bind an event of step s's concept: an `any` step whose concept such an event
    carries, or a `first` or `last` step whose possible targets include one (its picked event if that is certain,
    else every event that can be its target: D30 review, M1)."""
    concept, records = plan.concepts[s], targets[s].records
    for t in step_ids:
        if t == s:
            continue
        if plan.occurrence[t] == 'any':
            if any(plan.concepts[t] in r[_EVENT].concepts for r in records):
                return True
        elif targets[t] is not None:
            other = targets[t]
            pool = (other.record,) if other.certain else other.possible()
            if any(concept in r[_EVENT].concepts for r in pool):
                return True
    return False


def _reads_end(plan, s, targets):
    """Whether a relation reads step s's end (a `during` from it) and its concept has an event with an end of
    its own."""
    return s in plan.during_from and not targets[s].points


def _safe(plan, s, targets, step_ids):
    return targets[s].unlinked and not _reads_end(plan, s, targets) and not _coupled(plan, s, targets, step_ids)


def decide_ordered(query, times, relaxations, budget, plan=None):
    """`decide`, made sound for `first` and `last` steps whose target the patient's facts leave uncertain (D30).

    Returns (decision, undecided). `undecided` is None when the decision stands, else (kind, steps): kind is
    the certainty a binding reached ('exact' or 'possible', reported beside an unresolved status) or 'none'
    (no binding was found, but one may exist), and steps lists the uncertain `first` or `last` steps.

    A step's target is *certain* when no other event of its concept can start before it (after it, for
    `last`); then the step binds that event and nothing changes. An uncertain step is *safe* when its
    concept's events name no shared variable, no relation reads the step's end (a `during` from it) unless
    every such event is a point, and no other step can bind one of those events. A safe step's time is then
    the true first (last) start, which ranges exactly over the envelope (`_Occurrence`), independently of
    every other step: the step is decided on a point ranging over the envelope, and exact, possible and none
    are all sound. Otherwise the true target may be another event, with another end, or an event another step
    binds, so:
    - a binding found on the picked event proves nothing either way: undecided, with the binding beside it;
    - none found: the search is repeated with every event that can be first (last) as the step's candidates,
      a superset of the true targets, so a none there is sound; anything else is undecided."""
    step_ids = [s['id'] for s in query['steps']]
    plan = plan or _Plan(query, relaxations)
    if not plan.ordered or not _ORDER_RULE:
        return decide(query, times, relaxations, budget, plan), None
    targets = {s: times.occurrence(plan.concepts[s], plan.occurrence[s]) for s in plan.ordered}
    uncertain = [s for s in plan.ordered if targets[s] is not None and not targets[s].certain]
    if not uncertain:
        return decide(query, times, relaxations, budget, plan), None
    safe, unsafe = {}, []
    for s in uncertain:
        if _safe(plan, s, targets, step_ids):
            safe[s] = [targets[s].synthetic()]
        else:
            unsafe.append(s)
    r = decide(query, times, relaxations, budget, plan, safe)
    if not unsafe:
        return r, None
    if r['exact']['found']:
        return r, ('exact', uncertain)
    if r['possible']['found']:
        return r, ('possible', uncertain)
    if not r['possible']['complete']:
        return r, None  # a stopped search: unresolved already
    wide = dict(safe)
    wide.update({s: targets[s].possible() for s in unsafe})
    # Only whether any binding exists matters here, so the exact phase is skipped (D30 review, lever 3).
    again = decide(query, times, relaxations, budget, plan, wide, exact=not _LEVERS)
    if again['exact']['found'] or again['possible']['found'] or not again['possible']['complete']:
        return r, ('none', uncertain)
    return r, None


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
    first, undecided = decide_ordered(query, times, (), budget, pq.plan())
    if undecided is not None and undecided[0] != 'none':
        # D30: a binding on a first or last step whose target is uncertain is reported beside the status.
        kind = undecided[0]
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'OCCURRENCE_ORDER_UNCERTAIN',
                'uncertain_steps': undecided[1],
                'unverified_match': {'certainty': kind, 'binding': _binding_ids(first[kind]['binding']),
                                     'method': first[kind]['method']}, 'complete': False}
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
    # A stopped unrelaxed search ruled nothing out: unresolved, whatever a relaxation finds. So does one whose
    # first or last target is uncertain (D30).
    stopped = not first['possible']['complete']
    unsure = undecided[1] if undecided is not None else None
    def unresolved(**extra):
        if stopped:
            return {'patient_id': pid, 'status': 'unresolved', 'reason': 'SEARCH_BUDGET_EXHAUSTED', **extra,
                    'complete': False}
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'OCCURRENCE_ORDER_UNCERTAIN',
                'uncertain_steps': unsure, **extra, 'complete': False}
    skipped = order_skipped = False
    for cost, chosen in pq.sets:
        r, open_ = decide_ordered(query, times, chosen, budget, pq.plan(chosen))
        if open_ is not None:
            order_skipped = True  # this set's answer is undecided: like a stopped search, it rules nothing out
            continue
        winner = 'exact' if r['exact']['found'] else 'possible' if r['possible']['found'] else None
        if winner is None:
            skipped = skipped or not r['possible']['complete']
            continue
        relaxed = {'relaxations': [x['id'] for x in chosen], 'cost': decimal_text(cost), 'certainty': winner,
                   'binding': _binding_ids(r[winner]['binding']), 'method': r[winner]['method']}
        if stopped or unsure is not None:
            return unresolved(relaxed_match=relaxed)
        out = {'patient_id': pid, 'status': 'relaxed', **relaxed,
               'complete': not skipped and not order_skipped and (winner == 'exact' or r['exact']['complete'])}
        if skipped:
            out['note'] = 'CHEAPER_RELAXATION_SEARCH_STOPPED'
        elif order_skipped:
            out['note'] = 'CHEAPER_RELAXATION_ORDER_UNCERTAIN'
        return out
    if stopped or unsure is not None:
        return unresolved()
    out = {'patient_id': pid, 'status': 'none', 'complete': not skipped and not order_skipped}
    if skipped:
        out['note'] = 'RELAXATION_SEARCH_STOPPED'
    elif order_skipped:
        out['note'] = 'RELAXATION_ORDER_UNCERTAIN'
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
