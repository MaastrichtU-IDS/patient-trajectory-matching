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
