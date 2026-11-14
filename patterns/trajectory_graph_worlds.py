"""The true semantics of trajectory-graph results, by enumerating every choice of a patient's times (D30).

`trajectory_graph_reference.py` re-implements the engine's decision rule, so a defect in the rule itself (the
`last` order before D30, or reading the end of a `first` step that may not be the first) would reappear there,
and the differential between the two could not see it. This module checks results against what they mean.

A *world* is one choice of an integer time for every variable the patient's events name, inside every range
that names it, with each event ending at or after it starts (strictly after, for a proper event). In a world,
the target of a `first` step is the event of its concept that starts earliest, ties broken by the profile's
order (start range, end range, id), and of a `last` step the one that starts latest, ties broken by
(latest start, earliest start, end range, id). A trajectory *holds* in a world under a choice of events for the
`any` steps when every scope and relation constraint holds there, each `first` or `last` step taking its
target in that world. So, for a query and a relaxation set:
- it is *possible* when it holds in some world under some choice;
- a choice of `any` events is *exact* when it holds in every world; the set is exact when some choice is.

A result is sound when what it claims is true: `exact` (its binding's `any` events are exact), `possible`
(the query is possible and no choice is exact), `relaxed` (the query and every cheaper set are impossible,
and the set's binding has the certainty claimed), `none` (the query and every set within the budget are
impossible), and a match reported beside `unresolved` (it has the certainty claimed). `unresolved` itself
claims nothing. Where a result is not `complete`, only the claims it still makes are checked.

Small grids only: the number of worlds is the product of the variables' range widths.
"""
from fractions import Fraction
from itertools import combinations, product
import math

INF = math.inf


def _events(patient):
    out = []
    for e in patient['events']:
        s = tuple(e['start'])
        sv = e.get('start_var', e['id'] + '.s')
        if 'end' in e:
            f = (e['end'][0], INF if e['end'][1] is None else e['end'][1])
            fv = e.get('end_var', e['id'] + '.f')
        else:
            f, fv = s, sv
        out.append({'id': e['id'], 'concepts': set(e['concepts']), 's': s, 'f': f, 'sv': sv, 'fv': fv,
                    'point': 'end' not in e, 'proper': e.get('proper') is True})
    return out


def worlds(patient, horizon):
    """Every world, as {variable: time}; an open end is capped at `horizon`, past every time a constraint
    can compare it with."""
    events = _events(patient)
    ranges = {}
    def name(v, lo, hi):
        old = ranges.get(v, (-INF, INF))
        ranges[v] = (max(old[0], lo), min(old[1], min(hi, horizon)))
    for e in events:
        name(e['sv'], *e['s'])
        if not e['point']:
            name(e['fv'], *e['f'])
    names = sorted(ranges)
    out = []
    for values in product(*[range(ranges[v][0], ranges[v][1] + 1) for v in names]):
        t = dict(zip(names, values))
        if all(e['point'] or t[e['fv']] - t[e['sv']] >= (1 if e['proper'] else 0) for e in events):
            out.append(t)
    return events, out


def count_worlds(patient, horizon):
    events = _events(patient)
    ranges = {}
    for e in events:
        for v, (lo, hi) in [(e['sv'], e['s'])] + ([] if e['point'] else [(e['fv'], e['f'])]):
            old = ranges.get(v, (-INF, INF))
            ranges[v] = (max(old[0], lo), min(old[1], min(hi, horizon)))
    return math.prod(max(0, hi - lo + 1) for lo, hi in ranges.values())


def _plan(query, chosen):
    concept = {s['id']: s['concept'] for s in query['steps']}
    relations = {r['id']: dict(r) for r in query.get('relations', [])}
    for x in chosen:
        if x['kind'] == 'widen_concept':
            concept[x['step']] = x['concept']
        elif x['kind'] == 'extend_max':
            relations[x['relation']]['max'] = x['max']
        else:
            relations[x['relation']]['grace'] = x['grace']
    return concept, list(relations.values())


def _sets(query):
    """The relaxation sets within the budget, at most one per step or relation, cheapest first, then smaller,
    then by identifiers."""
    options = query.get('relaxations', [])
    budget = Fraction(query.get('budget', 0))
    out = []
    for k in range(1, len(options) + 1):
        for chosen in combinations(options, k):
            if len({(x['kind'], x.get('step') or x.get('relation')) for x in chosen}) < k:
                continue
            cost = sum((Fraction(x['cost']) for x in chosen), Fraction(0))
            if cost <= budget:
                out.append((cost, k, [x['id'] for x in chosen], chosen))
    return [c for *_, c in sorted(out, key=lambda t: t[:3])]


class _Question:
    """One query under one relaxation set, over one patient's worlds."""

    def __init__(self, query, chosen, events, all_worlds, window):
        self.concept, self.relations = _plan(query, chosen)
        self.steps = [s['id'] for s in query['steps']]
        self.occurrence = {s['id']: s.get('occurrence', 'any') for s in query['steps']}
        self.scope, self.events, self.worlds, self.window = query.get('scope', 'any'), events, all_worlds, window
        self.family = {s: [e for e in events if self.concept[s] in e['concepts']] for s in self.steps}
        self.free = [s for s in self.steps if self.occurrence[s] == 'any']

    def target(self, step, t):
        family = self.family[step]
        if not family:
            return None
        if self.occurrence[step] == 'first':
            return min(family, key=lambda e: (t[e['sv']], e['s'], e['f'], e['id']))
        return max(family, key=lambda e: (t[e['sv']], e['s'][1], e['s'][0], e['f'], e['id']))

    def holds(self, t, chosen):
        """Whether the trajectory holds in world t, the `any` steps on `chosen` ({step: event})."""
        bound = dict(chosen)
        for s in self.steps:
            if self.occurrence[s] != 'any':
                e = self.target(s, t)
                if e is None:
                    return False
                bound[s] = e
        anchor = {'START': self.window['start'], 'END': self.window['end']}
        start = lambda x: anchor[x] if x in anchor else t[bound[x]['sv']]
        for s in self.steps:
            v = t[bound[s]['sv']]
            if self.scope in ('from_start', 'within') and v < anchor['START']:
                return False
            if self.scope == 'within' and v > anchor['END']:
                return False
            if self.scope == 'after' and v < anchor['END']:
                return False
        for r in self.relations:
            a, b = start(r['from']), start(r['to'])
            if r['kind'] == 'after':
                if (r.get('min') is not None and b - a < r['min']) or (r.get('max') is not None and b - a > r['max']):
                    return False
            else:
                end = t[bound[r['from']]['fv']]
                if b < a or b - end > r.get('grace', 0):
                    return False
        return True

    def choices(self):
        for events in product(*[self.family[s] for s in self.free]):
            yield dict(zip(self.free, events))

    def possible(self):
        return any(self.holds(t, c) for c in self.choices() for t in self.worlds)

    def exact_for(self, chosen):
        return bool(self.worlds) and all(self.holds(t, chosen) for t in self.worlds)

    def exact(self):
        return any(self.exact_for(c) for c in self.choices())


def check(result, query, patient, horizon=None):
    """The claims of an engine result that are false, as strings; [] when it is sound."""
    window = patient.get('window')
    if window is None:
        return [] if result['status'] == 'excluded' else ['a patient without a window is not excluded']
    if horizon is None:
        horizon = max([window['end']] + [v for e in patient['events'] for v in e['start'] + e.get('end', [])
                                         if v is not None]) + 1 + max(
            [r.get('grace', 0) for r in query.get('relations', []) if r['kind'] == 'during'] + [0])
    events, all_worlds = worlds(patient, horizon)
    by_id = {e['id']: e for e in events}
    sets = _sets(query)
    ask = lambda chosen: _Question(query, chosen, events, all_worlds, window)
    def certainty_holds(question, certainty, binding):
        free = {s: by_id[binding[s]] for s in question.free}
        if any(question.concept[s] not in free[s]['concepts'] for s in free):
            return False
        if certainty == 'exact':
            return question.exact_for(free)
        return any(question.holds(t, free) for t in all_worlds) and not question.exact()
    wrong = []
    status, complete = result['status'], result.get('complete', True)
    unrelaxed = ask(())
    if status == 'exact' and not certainty_holds(unrelaxed, 'exact', result['binding']):
        wrong.append('exact, but its binding fails in some world')
    if status == 'possible' and not certainty_holds(unrelaxed, 'possible', result['binding']):
        wrong.append('possible, but impossible, or some binding is exact')
    if status in ('relaxed', 'none') and unrelaxed.possible():
        wrong.append(f'{status}, but the unrelaxed query is possible')
    if status == 'relaxed':
        ids = result['relaxations']
        chosen = next(c for c in sets if [x['id'] for x in c] == ids)
        if complete:
            for c in sets[:sets.index(chosen)]:
                if ask(c).possible():
                    wrong.append(f'relaxed by {ids}, but the cheaper set {[x["id"] for x in c]} is possible')
        if not certainty_holds(ask(chosen), result['certainty'], result['binding']):
            wrong.append(f'relaxed {result["certainty"]} by {ids}, which its binding does not reach')
    if status == 'none' and complete:
        for c in sets:
            if ask(c).possible():
                wrong.append(f'none, but the set {[x["id"] for x in c]} is possible')
    if status == 'unresolved' and 'relaxed_match' in result:
        m = result['relaxed_match']
        chosen = next(c for c in sets if [x['id'] for x in c] == m['relaxations'])
        if not certainty_holds(ask(chosen), m['certainty'], m['binding']):
            wrong.append('the relaxed match beside unresolved does not reach its certainty')
    return wrong


# D32: derived steps read as what they mean. The store compiles an Nth occurrence into a point over its envelope and a
# count into tags on the records at which it is reached; here neither is used. In a world:
# - an Nth step's time is the Nth smallest start (Nth from last: the Nth largest) among the patient's coded events of
#   its concept (an event whose kind is `mention` is never counted), and the step fails when there are fewer than N;
# - a count step is bound to one of those coded events, e, and holds only if at least N - 1 *other* coded events of the
#   concept start in [t(e) - span, t(e)] (for the index period: in [window start, t(e)], with t(e) in the window).
# A relaxation set may widen a derived step's concept (`widen_concept`) or a count's N and concept (`widen_count`).

def derived_sets(query, conditions):
    """The relaxation sets within the budget, as the store compiles them: the query's relaxations, then each
    `widen_count`, then each `widen_factor` (D33) (one relaxation per step: a `widen_count`, `widen_factor` and
    `widen_concept` of one step are alternatives)."""
    options = list(query.get('relaxations', []))
    options += [dict(x) for x in (conditions or {}).get('relaxations', []) if x['kind'] == 'widen_count']
    options += [dict(x) for x in (conditions or {}).get('relaxations', []) if x['kind'] == 'widen_factor']
    target = lambda x: (('step', x['step']) if x['kind'] in ('widen_concept', 'widen_count', 'widen_factor')
                        else (x['kind'], x['relation']))
    budget = Fraction(query.get('budget', 0))
    out = []
    for k in range(1, len(options) + 1):
        for chosen in combinations(options, k):
            if len({target(x) for x in chosen}) < k:
                continue
            cost = sum((Fraction(x['cost']) for x in chosen), Fraction(0))
            if cost <= budget:
                out.append((cost, k, [x['id'] for x in chosen], chosen))
    return [c for *_, c in sorted(out, key=lambda t: t[:3])]


class _DerivedQuestion(_Question):
    """One query with derived steps under one relaxation set, over one patient's worlds."""

    def __init__(self, query, conditions, chosen, events, all_worlds, window, mentions, readings=None):
        plain = [x for x in chosen if x['kind'] not in ('widen_count', 'widen_factor')]
        super().__init__(query, plain, events, all_worlds, window)
        self.derived = {}
        self.readings = readings
        for sid, raw in ((conditions or {}).get('steps') or {}).items():
            if 'relative' in raw:
                self.derived[sid] = ('relative', dict(raw['relative']))
            elif 'occurrence' in raw:
                (which, n), = raw['occurrence'].items()
                self.derived[sid] = ('nth', which, n)
            elif 'count' in raw:
                within = raw['count']['within']
                self.derived[sid] = ('count', raw['count']['at_least'], within if within == 'window' else within['span'])
        for x in chosen:
            if x['kind'] == 'widen_count':
                kind, _, within = self.derived[x['step']]
                self.derived[x['step']] = ('count', x['at_least'], within)
                if 'concept' in x:
                    self.concept[x['step']] = x['concept']
            elif x['kind'] == 'widen_factor':
                self.derived[x['step']] = ('relative', dict(self.derived[x['step']][1], factor=x['factor']))
        self.coded = {s: [e for e in events if self.concept[s] in e['concepts'] and e['id'] not in mentions]
                      for s in self.steps}
        for s, spec in self.derived.items():
            if spec[0] == 'count':
                self.family[s] = self.coded[s]
            elif spec[0] == 'relative':  # the readings of an item class the concept covers, in the condition's unit
                rows = readings['rows']
                self.family[s] = [e for e in events if e['id'] in rows and rows[e['id']]['unit'] == spec[1]['unit']
                                  and readings['covers'](rows[e['id']]['item_class'], self.concept[s])]
        self.free = [s for s in self.steps if self.occurrence[s] == 'any' and self.derived.get(s, ('',))[0] != 'nth']

    def reached(self, s, e, t):
        _, n, within = self.derived[s]
        te = t[e['sv']]
        if within == 'window':
            if not self.window['start'] <= te <= self.window['end']:
                return False
            lo = self.window['start']
        else:
            lo = te - within
        return sum(1 for x in self.coded[s] if x is not e and lo <= t[x['sv']] <= te) >= n - 1

    def outcome(self, s, e, t):
        """D33: 'true', 'false' or 'unknown' for the relative condition of step s at reading e in world t: the k most
        recent readings of e's item class with time in [t(e) - W, t(e) - 1] (a period reading's time is its recorded
        start's lower bound; an excluded record's its own variable), at equal times excluded records first, then
        event ids; none: false; an excluded record or another unit among them: unknown; else the mean, compared."""
        from decimal import Decimal
        rel, rows, by_id = self.derived[s][1], self.readings['rows'], {x['id']: x for x in self.events}

        def when(eid):
            if rows[eid]['kind'] in self.readings['period']:
                return by_id[eid]['s'][0]
            return t[by_id[eid]['sv']]
        item, tau, W = rows[e['id']]['item_class'], when(e['id']), rel['baseline']['within']
        inside = [(when(x), 1, x, rows[x]) for x in rows if x != e['id'] and rows[x]['item_class'] == item]
        inside += [(t[v], 0, v, None) for v, x in zip(self.readings['excluded_vars'], self.readings['excluded'])
                   if x['item_class'] == item]
        inside = sorted((p for p in inside if tau - W <= p[0] <= tau - 1), key=lambda p: (-p[0], p[1], p[2]))
        chosen = inside[:rel['baseline']['previous']]
        if not chosen:
            return 'false'
        if any(r is None or r['unit'] != rel['unit'] for _, _, _, r in chosen):
            return 'unknown'
        mean = sum(Fraction(Decimal(r['value'])) for _, _, _, r in chosen) / len(chosen)
        bar = max(Fraction(Decimal(rel['floor'])), mean)
        value, factor = Fraction(Decimal(rows[e['id']]['value'])), Fraction(Decimal(rel['factor']))
        ok = value >= factor * bar if rel['kind'] == 'rise' else value * factor <= bar
        return 'true' if ok else 'false'

    def possible(self):
        return any(self.holds(t, c, True) for c in self.choices() for t in self.worlds)

    def exact_for(self, chosen):
        return bool(self.worlds) and all(self.holds(t, chosen) for t in self.worlds)

    def holds(self, t, chosen, lenient=False):
        """Whether the trajectory holds in world t; a relative condition whose outcome is unknown (an excluded
        record's value, another unit) may hold (`lenient`) and does not certainly hold."""
        bound, times = dict(chosen), {}
        for s in self.steps:
            spec = self.derived.get(s)
            if spec is not None and spec[0] == 'nth':
                starts = sorted(t[e['sv']] for e in self.coded[s])
                if len(starts) < spec[2]:
                    return False
                times[s] = starts[spec[2] - 1] if spec[1] == 'nth' else starts[-spec[2]]
                continue
            if spec is not None and spec[0] == 'count' and self.occurrence[s] != 'any':
                # an occurrence on a count step (refused by the store; read here for a planted defect): the first
                # (last) coded event at which the count is reached
                hits = [e for e in self.coded[s] if self.reached(s, e, t)]
                if not hits:
                    return False
                pick = min if self.occurrence[s] == 'first' else max
                bound[s] = pick(hits, key=lambda e: t[e['sv']])
            elif self.occurrence[s] != 'any':
                e = self.target(s, t)
                if e is None:
                    return False
                bound[s] = e
            elif spec is not None and spec[0] == 'relative':
                result = self.outcome(s, bound[s], t)
                if result == 'false' or (result == 'unknown' and not lenient):
                    return False
            elif spec is not None and not self.reached(s, bound[s], t):
                return False
            times[s] = t[bound[s]['sv']]
        anchor = {'START': self.window['start'], 'END': self.window['end']}
        start = lambda x: anchor[x] if x in anchor else times[x]
        for s in self.steps:
            v = times[s]
            if self.scope in ('from_start', 'within') and v < anchor['START']:
                return False
            if self.scope == 'within' and v > anchor['END']:
                return False
            if self.scope == 'after' and v < anchor['END']:
                return False
        for r in self.relations:
            a, b = start(r['from']), start(r['to'])
            if r['kind'] == 'after':
                if (r.get('min') is not None and b - a < r['min']) or (r.get('max') is not None and b - a > r['max']):
                    return False
            else:
                end = t[bound[r['from']]['fv']]
                if b < a or b - end > r.get('grace', 0):
                    return False
        return True


def check_derived(result, query, conditions, patient, mentions=frozenset(), horizon=None, readings=None):
    """The claims of a store result for a request with derived steps (D32) that are false, as strings; [] when it is
    sound. `query` and `conditions` are the request as the caller wrote it (1.1 or 1.2, without value conditions);
    `patient` is the engine patient without any derived event, `mentions` the ids of its events whose kind is
    `mention`. Every status but `unresolved` is checked, and the relaxed match beside `unresolved`; a binding names its
    Nth steps by their derived point, which carries no claim, and its count steps by the record at which the count is
    reached. A relative condition (D33) reads `readings`: `rows` {event id: item class, unit, value, kind},
    `excluded` [{item class, start}] (each excluded record a variable of its own in every world), `period` (the
    period kinds) and `covers(item class, concept)`; an unknown outcome may be true or false."""
    window = patient.get('window')
    if window is None:
        return [] if result['status'] == 'excluded' else ['a patient without a window is not excluded']
    if readings is not None:  # each excluded record is a variable of its own, carrying no concept a step names
        pseudo = [{'id': f'#excluded/{j}', 'concepts': ['#excluded'], 'start': list(x['start'])}
                  for j, x in enumerate(readings['excluded'])]
        readings = dict(readings, excluded_vars=[x['id'] + '.s' for x in pseudo])
        patient = dict(patient, events=list(patient['events']) + pseudo)
    if horizon is None:
        spans = [raw['count']['within']['span'] for raw in (conditions or {}).get('steps', {}).values()
                 if 'count' in raw and raw['count']['within'] != 'window']
        horizon = max([window['end']] + [v for e in patient['events'] for v in e['start'] + e.get('end', [])
                                         if v is not None]) + 1 + max(
            [r.get('grace', 0) for r in query.get('relations', []) if r['kind'] == 'during'] + [0] + spans)
    events, all_worlds = worlds(patient, horizon)
    by_id = {e['id']: e for e in events}
    sets = derived_sets(query, conditions)
    ask = lambda chosen: _DerivedQuestion(query, conditions, chosen, events, all_worlds, window, mentions, readings)

    def certainty_holds(question, certainty, binding):
        if any(binding.get(s) not in by_id for s in question.free):
            return False
        free = {s: by_id[binding[s]] for s in question.free}
        if any(free[s] not in question.family[s] for s in free):
            return False
        if certainty == 'exact':
            return question.exact_for(free)
        return any(question.holds(t, free, True) for t in all_worlds) and not question.exact()
    wrong = []
    status, complete = result['status'], result.get('complete', True)
    unrelaxed = ask(())
    if status == 'exact' and not certainty_holds(unrelaxed, 'exact', result['binding']):
        wrong.append('exact, but its binding fails in some world')
    if status == 'possible' and not certainty_holds(unrelaxed, 'possible', result['binding']):
        wrong.append('possible, but impossible, or some binding is exact')
    if status in ('relaxed', 'none') and unrelaxed.possible():
        wrong.append(f'{status}, but the unrelaxed query is possible')
    if status == 'relaxed':
        ids = result['relaxations']
        chosen = next(c for c in sets if [x['id'] for x in c] == ids)
        if complete:
            for c in sets[:sets.index(chosen)]:
                if ask(c).possible():
                    wrong.append(f'relaxed by {ids}, but the cheaper set {[x["id"] for x in c]} is possible')
        if not certainty_holds(ask(chosen), result['certainty'], result['binding']):
            wrong.append(f'relaxed {result["certainty"]} by {ids}, which its binding does not reach')
    if status == 'none' and complete:
        for c in sets:
            if ask(c).possible():
                wrong.append(f'none, but the set {[x["id"] for x in c]} is possible')
    if status == 'unresolved' and 'relaxed_match' in result:
        m = result['relaxed_match']
        chosen = next(c for c in sets if [x['id'] for x in c] == m['relaxations'])
        if not certainty_holds(ask(chosen), m['certainty'], m['binding']):
            wrong.append('the relaxed match beside unresolved does not reach its certainty')
    return wrong


def relative_strengths(patient, concept, rel, readings, horizon=None):
    """{event id: 'certain' | 'possible'} of a relative condition (D33) by enumeration: certain when its outcome is
    true in every world, possible when true or unknown in some. `rel` is the request's `relative` entry."""
    window = patient.get('window') or {'start': 0, 'end': 0}
    query = {'steps': [{'id': 'A', 'concept': concept}], 'relations': []}
    conditions = {'steps': {'A': {'relative': rel}}}
    pseudo = [{'id': f'#excluded/{j}', 'concepts': ['#excluded'], 'start': list(x['start'])}
              for j, x in enumerate(readings['excluded'])]
    readings = dict(readings, excluded_vars=[x['id'] + '.s' for x in pseudo])
    patient = dict(patient, events=list(patient['events']) + pseudo)
    if horizon is None:
        horizon = max([window['end']] + [v for e in patient['events'] for v in e['start'] + e.get('end', [])
                                         if v is not None]) + 1
    events, all_worlds = worlds(patient, horizon)
    question = _DerivedQuestion(query, conditions, (), events, all_worlds, window, frozenset(), readings)
    out = {}
    for e in question.family['A']:
        seen = {question.outcome('A', e, t) for t in all_worlds}
        if seen == {'true'}:
            out[e['id']] = 'certain'
        elif seen & {'true', 'unknown'}:
            out[e['id']] = 'possible'
    return out


def nth_times(patient, concept, which, n, mentions=frozenset(), horizon=None):
    """The set of times the Nth (from last) occurrence of `concept` takes over every world: what an envelope must equal
    when the coded events of the concept share no variable."""
    window = patient.get('window') or {'end': 0}
    if horizon is None:
        horizon = max([window['end']] + [v for e in patient['events'] for v in e['start'] + e.get('end', [])
                                         if v is not None]) + 1
    events, all_worlds = worlds(patient, horizon)
    coded = [e for e in events if concept in e['concepts'] and e['id'] not in mentions]
    out = set()
    if len(coded) < n:
        return out
    for t in all_worlds:
        starts = sorted(t[e['sv']] for e in coded)
        out.add(starts[n - 1] if which == 'nth' else starts[-n])
    return out


def count_strengths(patient, concept, n, within, mentions=frozenset(), horizon=None):
    """{event id: 'certain' | 'possible'} of a count by enumeration: certain when the count is reached at the event in
    every world, possible in some."""
    window = patient.get('window')
    if horizon is None:
        horizon = max([window['end']] + [v for e in patient['events'] for v in e['start'] + e.get('end', [])
                                         if v is not None]) + 1
    events, all_worlds = worlds(patient, horizon)
    coded = [e for e in events if concept in e['concepts'] and e['id'] not in mentions]
    out = {}
    for e in coded:
        hits = []
        for t in all_worlds:
            te = t[e['sv']]
            if within == 'window':
                ok = window['start'] <= te <= window['end'] and sum(
                    1 for x in coded if x is not e and window['start'] <= t[x['sv']] <= te) >= n - 1
            else:
                ok = sum(1 for x in coded if x is not e and te - within <= t[x['sv']] <= te) >= n - 1
            hits.append(ok)
        if hits and all(hits):
            out[e['id']] = 'certain'
        elif any(hits):
            out[e['id']] = 'possible'
    return out
