"""The patient's times, as the trajectory-graph engine models them: validation, `Times` and the network it solves.

Split out of `patterns/trajectory_graph.py` (D34) so that the execution store's build, which validates each patient's
times as it ingests them, loads none of the engine's matching code. `trajectory_graph` re-imports every name defined
here, so `tg.patient_times`, `tg.Times`, `tg.TrajectoryError` and the rest are these very objects, and no caller or
result changes. Matching code (search, arc consistency, the order rule, the envelope, `decide*`, `match*`) stays in
`trajectory_graph`; `Times.occurrence`, which builds the order rule's `_Occurrence`, imports it when called.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from operator import attrgetter

ANCHORS = ('START', 'END')
MAX_LINKED_VARIABLES = 300  # one linked component of 300 variables solves in under a second
INF = math.inf

# The fields of a kernel record (`Times.candidate_records`).
_SLO, _SHI, _FLO, _FHI, _KEYS, _EVENT = range(6)


class TrajectoryError(ValueError):
    """A query or patient record outside the profile; the message is a stable code."""


def require(condition, code):
    if not condition:
        raise TrajectoryError(code)


def _int(value, code):
    require(type(value) is int, code)
    return value


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
        # D30/D31: the variables of the points `_Occurrence` adds for a `first` or `last` step (none of an event's).
        self.synthetic = set()
        self._relations = {}

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

    def concepts_meet(self, a, b):
        """Whether some event carries both concepts (D31, cached)."""
        key = ('meet', a, b) if a <= b else ('meet', b, a)
        found = self._relations.get(key)
        if found is None:
            found = self._relations[key] = any(b in r[_EVENT].concepts for r in self.candidate_records(a))
        return found

    def concept_within(self, inner, outer):
        """Whether every event carrying `inner` carries `outer` (D31, cached)."""
        key = ('within', inner, outer)
        found = self._relations.get(key)
        if found is None:
            found = self._relations[key] = all(outer in r[_EVENT].concepts for r in self.candidate_records(inner))
        return found

    def occurrence(self, concept, which):
        """What a `first` or `last` step on `concept` binds, and how sure that is (D30): an `_Occurrence`, or
        None when no event carries the concept. Cached per concept and occurrence."""
        key = (concept, which)
        if key not in self._occurrences:
            records = self.candidate_records(concept)
            from .trajectory_graph import _Occurrence  # matching code, loaded only when matching
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
