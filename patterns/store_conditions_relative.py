"""Rises and falls (relative conditions) in the execution store's match request: profile
`execution-store-conditions-1.2` (D33, built at bccee57; restored in D36).

A step may carry `relative: {kind: rise | fall, factor, unit, floor, baseline: {previous: k, within: W}}`: a reading
(a series row of the step's concept in `unit`) rises to at least `factor` times, or falls to at most 1/`factor` of,
max(floor, the mean of up to k earlier readings of its item class within W). The relaxation `widen_factor` names a
smaller factor. The contract is the store doc's *Relative conditions* section and the decision log's D33 and D36.

Why a module of its own (D36, owner policy). The scaling report's builds are reused when only matching changed (D34):
its build record hashes every repository module a build loads, `patterns/execution_store.py` and the converter
(`patterns/synthetic_ingest.py`) among them. Rises are matching only, so they live here, in a module no build loads:
`execution_store.py` and the converter are byte-identical to main, and `--reuse-builds` can reuse the kept builds. A
caller imports this module only for a 1.2 request; `match` and `candidates` here answer every other request by
handing it to `execution_store` unchanged.

What this module adds to the store's own derived steps (D32), and nothing else:
- `Request`, the store's `Request` with profile 1.2: a relative step's concept becomes the value tag of its unit (no
  bound) and then a relative derived tag (`derived-step:<step>/<own or relaxation id>`); `widen_factor` becomes a
  `widen_concept` to the relative tag of the smaller factor;
- the readings (`_readings`) and their exact strengths (`_relative_strengths`), in `derive_patient`;
- the two-pass rule of D32 with relative steps counted among the uncertain steps (`match_derived`);
- candidates (a relative tag's patients are those with an event of its unit tag), D18 reach by the step's scope, the
  context (the relative rules and this module's digest) and the output (`relative_rules`).
Everything else (loading, pruning, comparability, excluded records, workers' pools) is `execution_store`'s own code,
called as it is.

The period label. Which readings summarise a whole period is declared by the source, in the snapshot's labels
(`period_reading_kinds`). D33 had the converter set it; in D36 the converter must stay byte-identical, so the synthetic
source's declaration is made here (`synthetic_provenance`, `synthetic_ingest`): the converter's own provenance, with
the label added when series are on. The label is part of the snapshot's identity either way.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import copy
from decimal import Decimal
from fractions import Fraction
import hashlib
from heapq import nlargest
from itertools import combinations
import os

from . import execution_store as es
from . import synthetic_ingest as si
from . import trajectory_graph as tg
from ._resources import ROOT

MODULE_PATH = 'patterns/store_conditions_relative.py'
CONDITIONS_PROFILE_1_2 = 'execution-store-conditions-1.2'  # D33: rises and falls (relative conditions)
CONDITIONS_PROFILES = (*es.CONDITIONS_PROFILES, CONDITIONS_PROFILE_1_2)

# Relative conditions (D33): "rises to at least x f" and "falls to at most / f" against a baseline of up to k previous
# readings within W, in a 1.2 match request. A relative step's tag is carried by the readings (events with a series row
# of the step's concept in the condition's unit) whose condition holds, certain or possible, decided exactly from the
# readings' times (`_relative_strengths`); the two-pass rule of D32 then applies unchanged.
PERIOD_LABEL = 'period_reading_kinds'  # snapshot label: the event kinds whose series rows summarise a whole period
PERIOD_KINDS = ('diary_summary',)  # the synthetic source's period readings: the diary's monthly counts
RELATIVE_MAX_PREVIOUS = 3
RELATIVE_UNCERTAIN_LIMIT = 6
RELATIVE_RULES = {
    'readings': 'relative_previous_k_v1: the readings of an item class are its series rows, in any unit, and its '
                'excluded records; a reading whose event kind the snapshot label period_reading_kinds lists is a period '
                'summary, timed exactly at its recorded start\'s lower bound (the period\'s start); any other series '
                'reading is timed by its start over Times.dom, an excluded record by its recorded start range',
    'baseline': 'with the reading at tau: the k most recent readings of its item class with tau - W <= t <= tau - 1, '
                'by time, at equal times excluded records first, then event id ascending; none: no baseline (false); '
                'an excluded record or a reading in another unit among them: undetermined; otherwise their mean, exact',
    'comparison': 'rise: value >= factor * max(floor, baseline); fall: value <= max(floor, baseline) / factor; exact '
                  'rationals over value_decimal; the reading must be in the condition\'s unit',
    'strength': 'certain when the condition holds in every choice of the readings\' times, possible when it holds or '
                'is undetermined in some; exact when no two point readings of the item class have linked start '
                'variables and at most 6 readings of uncertain time could precede the reading within W; otherwise '
                'sound (certain under, possible over) and marked inexact',
    'exclusions': 'excluded records that could be among the previous k make the baseline undetermined, inside the '
                  'tag; an excluded record that could be the rising reading is reached by the step\'s scope (D18)',
}
# Planted defects for the tests (D33 rules); None in every real run.
_D33_DEFECT = None


def is_relative_request(conditions):
    """Whether a match request's conditions are of profile 1.2 (which `execution_store` alone refuses)."""
    return isinstance(conditions, dict) and conditions.get('profile') == CONDITIONS_PROFILE_1_2


# The synthetic source's declaration of its period readings (see the module's docstring).

def synthetic_provenance(cohort, skipped, mention_time='at_or_before_letter', series=False):
    """The converter's provenance; with series on, labelled with the diary's summaries as period readings (D33): a
    monthly count describes its month, not one moment in it."""
    out = si.synthetic_provenance(cohort, skipped, mention_time, series)
    if series:
        out['labels'] = {**(out.get('labels') or {}), PERIOD_LABEL: ','.join(PERIOD_KINDS)}
    return out


def synthetic_ingest(cohort, mention_time='at_or_before_letter', series=False):
    """The converter's ingest document, its provenance as `synthetic_provenance` gives it."""
    ingest = si.synthetic_ingest(cohort, mention_time, series)
    if series:
        ingest['provenance'] = {**ingest['provenance'],
                                'labels': {**(ingest['provenance'].get('labels') or {}),
                                           PERIOD_LABEL: ','.join(PERIOD_KINDS)}}
    return ingest


# The request.

class Request(es.Request):
    """`execution_store.Request` with profile 1.2 (D33): per step, beside the 1.1 forms, a relative condition
    (`_relative_spec`), and the relaxation `widen_factor`. `kinds` maps a relative step to ('relative', 'rise' or
    'fall'); its derived tag's spec is {kind: 'relative', concept: the unit tag, relative: the condition}. A 1.0 or 1.1
    request is compiled exactly as `execution_store.Request` compiles it (the code below is that class's, with the
    relative branches added)."""

    def __init__(self, query, conditions):
        tg.validate_query(query)
        if not (isinstance(conditions, dict) and conditions.get('profile') in CONDITIONS_PROFILES
                and set(conditions) <= {'profile', 'steps', 'relaxations'} and 'steps' in conditions):
            raise es.StoreError('INVALID_CONDITIONS', 'profile, steps and relaxations')
        self.profile = conditions['profile']
        later = self.profile != es.CONDITIONS_PROFILE
        steps = {s['id']: s for s in query['steps']}
        given = conditions['steps']
        if not (isinstance(given, dict) and given):
            raise es.StoreError('INVALID_CONDITIONS', 'steps')
        unknown = sorted(str(k) for k in set(given) - set(steps))
        if unknown:
            raise es.StoreError('UNKNOWN_CONDITION_STEP', ', '.join(unknown))
        own, derived_of = {}, {}
        relative_ok = self.profile == CONDITIONS_PROFILE_1_2
        allowed = es._VALUE_KEYS | {'occurrence', 'count'} | ({'relative'} if relative_ok else set())
        for sid, raw in given.items():
            if not later:
                own[sid] = es._condition(raw, sid)
                continue
            if not (isinstance(raw, dict) and raw and set(raw) <= allowed):
                raise es.StoreError('INVALID_CONDITION', sid)
            value = {k: v for k, v in raw.items() if k in es._VALUE_KEYS}
            if 'relative' in raw:  # D33
                derived_of[sid] = _relative_spec(raw, sid, steps[sid])
                continue
            if value:
                own[sid] = es._condition(value, sid)
            if 'occurrence' in raw or 'count' in raw:
                derived_of[sid] = es._derived_spec(raw, sid, steps[sid], query)
        # D33: a relative step reads the events with a series row of its concept in the condition's unit, which the
        # value tag of the unit alone (no bound) holds exactly; it is the step's concept for loading, candidates,
        # pruning and coverage, as a value tag is for a counted value condition.
        units = {sid: (spec[1]['unit'], None, None) for sid, spec in derived_of.items() if spec[0] == 'relative'}
        self.tags = {}

        def tag(concept, condition):
            name = es.tag_name(concept, condition)
            self.tags[name] = (concept, condition)
            return name
        compiled = copy.deepcopy(query)
        for step in compiled['steps']:
            if step['id'] in own:
                step['concept'] = tag(steps[step['id']]['concept'], own[step['id']])
            elif step['id'] in units:
                step['concept'] = tag(steps[step['id']]['concept'], units[step['id']])
        relaxations = []
        for x in compiled.get('relaxations', []):
            if x['kind'] == 'widen_concept' and x['step'] in own:
                x = {**x, 'concept': tag(x['concept'], own[x['step']])}  # the step's condition on the wider concept
            elif x['kind'] == 'widen_concept' and x['step'] in units:
                x = {**x, 'concept': tag(x['concept'], units[x['step']])}
            relaxations.append(x)
        ids = {x['id'] for x in relaxations}
        given = conditions.get('relaxations', [])
        if not isinstance(given, list):
            raise es.StoreError('INVALID_CONDITIONS', 'relaxations')
        counts, factors = [], []
        for x in given:
            if relative_ok and isinstance(x, dict) and x.get('kind') == 'widen_factor':  # D33
                if not (set(x) == {'id', 'kind', 'step', 'cost', 'factor'} and type(x['id']) is str and x['id']
                        and type(x['step']) is str):
                    raise es.StoreError('INVALID_FACTOR_RELAXATION', repr(x)[:120])
                if x['id'] in ids:
                    raise es.StoreError('DUPLICATE_RELAXATION', x['id'])
                ids.add(x['id'])
                spec = derived_of.get(x['step'])
                if spec is None or spec[0] != 'relative':
                    raise es.StoreError('FACTOR_RELAXATION_STEP_NOT_RELATIVE', str(x['step']))
                factor = _factor(x['factor'], x['id'])
                # A smaller factor (still above 1) admits every reading the step's factor admits, at both strengths;
                # a larger one would not, and is refused (`_D33_DEFECT == 'relaxation_not_containing'` skips this).
                if not factor < _decimal_of(spec[1]['factor']) and _D33_DEFECT != 'relaxation_not_containing':
                    raise es.StoreError('FACTOR_RELAXATION_MUST_WIDEN', x['id'])
                factors.append({**x, 'factor': es._decimal_text(factor)})
                continue
            if later and isinstance(x, dict) and x.get('kind') == 'widen_count':
                if not (set(x) <= {'id', 'kind', 'step', 'cost', 'at_least', 'concept'}
                        and {'id', 'kind', 'step', 'cost', 'at_least'} <= set(x) and type(x['id']) is str and x['id']
                        and type(x['step']) is str and ('concept' not in x or (type(x['concept']) is str and x['concept']))):
                    raise es.StoreError('INVALID_COUNT_RELAXATION', repr(x)[:120])
                if x['id'] in ids:
                    raise es.StoreError('DUPLICATE_RELAXATION', x['id'])
                ids.add(x['id'])
                spec = derived_of.get(x['step'])
                if spec is None or spec[0] != 'count':
                    raise es.StoreError('COUNT_RELAXATION_STEP_NOT_COUNTED', str(x['step']))
                n = x['at_least']
                if not (type(n) is int and 1 <= n < spec[1]):
                    raise es.StoreError('COUNT_RELAXATION_MUST_NARROW', x['id'])
                counts.append(x)
                continue
            if not (isinstance(x, dict) and set(x) == {'id', 'kind', 'step', 'cost', 'condition'}
                    and x['kind'] == 'widen_value' and type(x['id']) is str and x['id'] and type(x['step']) is str):
                raise es.StoreError('INVALID_VALUE_RELAXATION', repr(x)[:120])
            if x['id'] in ids:
                raise es.StoreError('DUPLICATE_RELAXATION', x['id'])
            ids.add(x['id'])
            if x['step'] not in own:
                raise es.StoreError('VALUE_RELAXATION_STEP_NOT_CONDITIONED', str(x['step']))
            wider = es._condition(x['condition'], x['id'])
            if not es.contains(wider, own[x['step']]):
                raise es.StoreError('VALUE_RELAXATION_MUST_WIDEN', x['id'])
            relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                'concept': tag(steps[x['step']]['concept'], wider)})
        # D32: each derived step's concept, and each widening of it, becomes the derived tag of that concept (or value
        # tag) under the step's occurrence or count; a `widen_count` becomes a widening to the count tag of the smaller
        # N, on the step's concept or on the wider concept it names (one relaxation per step, as the engine allows).
        self.derived, self.kinds, self.step_n = {}, {}, {}

        def derived_tag(sid, concept, variant, n=None, factor=None):
            # The tag's name, which is also its Nth point's event id, names the step and what introduced the variant
            # (`own`, or the relaxation's id), so a reader reproduces it from the request alone.
            spec = derived_of[sid]
            if spec[0] == 'nth':
                form = {'kind': 'nth', 'concept': concept, 'which': spec[1], 'n': spec[2]}
            elif spec[0] == 'relative':  # D33: `concept` is the unit tag of the step's (or the wider) concept
                form = {'kind': 'relative', 'concept': concept,
                        'relative': {**spec[1], 'factor': spec[1]['factor'] if factor is None else factor}}
            else:
                form = {'kind': 'count', 'concept': concept, 'n': spec[1] if n is None else n, 'within': spec[2]}
            name = f'{es._DERIVED_PREFIX}{sid}/{variant}'
            # D32 review: a relaxation named `own`, or ids holding '/' across steps (step A with relaxation `r/own`,
            # step `A/r` with its own tag), would give two tags one name and the later would silently replace the
            # earlier. Own tags are named first, so this one check refuses every such request.
            if name in self.derived:
                raise es.StoreError('DERIVED_TAG_NAME_COLLISION', name)
            self.derived[name] = form
            self.step_n[name] = spec[2] if spec[0] == 'nth' else 1 if spec[0] == 'relative' else spec[1]
            return name
        if derived_of:
            for sid, spec in derived_of.items():
                self.kinds[sid] = (('nth', spec[1]) if spec[0] == 'nth' else ('relative', spec[1]['kind'])
                                   if spec[0] == 'relative' else ('count', spec[2]))
            for step in compiled['steps']:
                if step['id'] in derived_of:
                    step['concept'] = derived_tag(step['id'], step['concept'], 'own')
            relaxations = [{**x, 'concept': derived_tag(x['step'], x['concept'], x['id'])}
                           if x['kind'] == 'widen_concept' and x['step'] in derived_of else x for x in relaxations]
            for x in counts:
                concept = x.get('concept', steps[x['step']]['concept'])
                if x['step'] in own:
                    concept = tag(concept, own[x['step']])
                relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                    'concept': derived_tag(x['step'], concept, x['id'], x['at_least'])})
            for x in factors:  # D33: the relative tag of the step's concept under the smaller factor
                concept = tag(steps[x['step']]['concept'], units[x['step']])
                relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                    'concept': derived_tag(x['step'], concept, x['id'], factor=x['factor'])})
        if relaxations or 'relaxations' in compiled:
            compiled['relaxations'] = relaxations
        self.query = tg.validate_query(compiled)
        self.conditions = conditions
        per_step = es._step_concepts(self.query)
        # What each engine concept is loaded by: a derived tag by the concept or value tag it counts.
        self.load = {sid: list(dict.fromkeys(self.derived[c]['concept'] if c in self.derived else c for c in cs))
                     for sid, cs in per_step.items()}
        self.base = {sid: list(dict.fromkeys(self.tags[c][0] if c in self.tags else c for c in cs))
                     for sid, cs in self.load.items()}
        # The tags some relaxation set the engine would try names (a relaxation beyond the budget is never tried).
        used = {c for cs in self.load.values() for c in cs}
        self.used = {name: spec for name, spec in self.tags.items() if name in used}
        self.derived_used = {name: self.derived[name] for name in dict.fromkeys(c for cs in per_step.values() for c in cs)
                             if name in self.derived}

    def describe_derived(self):
        """The derived steps (D32), for the match output: the rules and each derived tag's spec; with a relative
        condition (D33), also the relative rules."""
        out = {'profile': self.profile, 'rules': es.DERIVED_RULES,
               'steps': {sid: {'occurrence': kind[1]} if kind[0] == 'nth' else {'relative': kind[1]}
                         if kind[0] == 'relative' else {'count_within': kind[1]}
                         for sid, kind in sorted(self.kinds.items())},
               'tags': {name: spec for name, spec in sorted(self.derived.items())}}
        if self.relative:
            out['relative_rules'] = RELATIVE_RULES
        return out

    @property
    def relative(self):
        """Whether some step carries a relative condition (D33)."""
        return any(kind[0] == 'relative' for kind in self.kinds.values())


def _decimal_of(text):
    from . import series_values as sv
    return sv.decimal_of(text)


def _factor(text, where):
    """A factor: a decimal string, in the measurement decimal rule, greater than 1 (D33)."""
    from . import series_values as sv
    try:
        value = sv.decimal_of(text)
    except sv.SeriesValueError:
        raise es.StoreError('INVALID_FACTOR', where) from None
    if not value > 1:
        raise es.StoreError('INVALID_FACTOR', where)
    return value


def _relative_spec(raw, sid, step):
    """('relative', {kind, factor, unit, floor, previous, within}) for a 1.2 step entry, each decimal in its shortest
    exact form, refused by name when malformed or in conflict with the step (D33)."""
    from . import series_values as sv
    if any(k in raw for k in es._VALUE_KEYS):
        raise es.StoreError('RELATIVE_WITH_ABSOLUTE', sid)
    # A first, last or Nth over a relative condition's events would pick a different reading in each pass.
    if 'occurrence' in raw or 'count' in raw or step.get('occurrence', 'any') != 'any':
        raise es.StoreError('OCCURRENCE_ON_DERIVED_STEP', sid)
    rel = raw['relative']
    if not (isinstance(rel, dict) and set(rel) == {'kind', 'factor', 'unit', 'floor', 'baseline'}
            and rel['kind'] in ('rise', 'fall')):
        raise es.StoreError('INVALID_RELATIVE', sid)
    unit, base = rel['unit'], rel['baseline']
    if not (type(unit) is str and unit and unit.strip() == unit):
        raise es.StoreError('INVALID_RELATIVE', f'{sid}: unit')
    if not (isinstance(base, dict) and set(base) == {'previous', 'within'} and type(base['previous']) is int
            and 1 <= base['previous'] <= RELATIVE_MAX_PREVIOUS and type(base['within']) is int and base['within'] >= 1):
        raise es.StoreError('INVALID_RELATIVE', f'{sid}: baseline')
    factor = _factor(rel['factor'], sid)
    try:
        floor = sv.decimal_of(rel['floor'])
    except sv.SeriesValueError:
        raise es.StoreError('INVALID_RELATIVE', f'{sid}: floor') from None
    if not floor > 0:
        raise es.StoreError('INVALID_RELATIVE', f'{sid}: floor')
    return ('relative', {'kind': rel['kind'], 'factor': es._decimal_text(factor), 'unit': unit,
                         'floor': es._decimal_text(floor), 'previous': base['previous'], 'within': base['within']})


# Readings and their strengths.

class _Reading:
    """One reading of an item class (D33): a series row (`eid` its event, `value` a Fraction, `unit`) or an excluded
    record (`eid`, `value` and `unit` None); [a, b] the range of its time; `link` the component of its start variable
    in the patient's network (None for a period reading, an excluded record or an event not loaded); `rank` the
    tie order at equal times, larger first (excluded records, then event ids ascending)."""
    __slots__ = ('eid', 'item', 'a', 'b', 'value', 'unit', 'link', 'rank')

    def __init__(self, eid, item, a, b, value, unit, link, rank):
        self.eid, self.item, self.a, self.b, self.value, self.unit, self.link, self.rank = (
            eid, item, a, b, value, unit, link, rank)


def _patient_readings(readings, times):
    """({item class: [_Reading] sorted by a}, {item class: whether two of its point readings are linked}) for one
    patient. `readings` holds `rows` {event id: (item class, unit, value_decimal, kind, recorded start)}, `excluded`
    {item class: [recorded start]}, `period` (the event kinds the snapshot declares period summaries) and `covered`
    {concept: item classes it covers}."""
    period = readings['period']
    loaded = {e.id: e for e in times.events}
    by_class = {}
    for r, (eid, (item, unit, text, kind, start)) in enumerate(sorted(readings['rows'].items())):
        e = loaded.get(eid)
        if kind in period:  # a period summary: its period's start, exactly
            a = b = start[0]
            link = None
        elif e is not None:
            a, b = times.dom[e.sv]
            link = times.component[e.sv]
        else:  # an event no step loads, in a patient without shared variables: its recorded range is its range
            a, b = start
            link = None
        by_class.setdefault(item, []).append(_Reading(eid, item, a, b, Fraction(Decimal(text)), unit, link, (0, -r)))
    for item, starts in readings['excluded'].items():
        for j, (lo, hi) in enumerate(sorted(starts)):
            by_class.setdefault(item, []).append(_Reading(None, item, lo, hi, None, None, None, (1, -j)))
    linked = {}
    for item, members in by_class.items():
        members.sort(key=lambda x: (x.a, x.b))
        links = [x.link for x in members if x.link is not None]
        linked[item] = len(links) != len(set(links))
    return by_class, linked


def _relative_strengths(rel, cands, by_class, linked):
    """({event id: 'certain' or 'possible'}, exact) of a relative condition `rel` (D33, `relative_previous_k_v1`) over
    the readings `cands` (in the condition's unit, of item classes its concept covers).

    With reading c at tau, a reading x of c's item class is *in the window* when tau - W <= t(x) <= tau - 1; the
    baseline is the k window members ranked first by (time, rank), larger first. Each x is forced in (its range lies in
    the window), optional (it can lie in the window or outside it), or out. The baselines realizable at tau are:
    - of fewer than k members: exactly the forced readings plus fewer than k - |forced| optional ones;
    - of k members S: exactly those for which, with each member at its latest time in the window, every non-member
      that can be in the window can also lie outside it or rank below the lowest member at its earliest time there
      (the members' latest times make that lowest rank as high as it can be, since times are independent).
    Every test compares tau - W or tau - 1 with a bound, so the set of baselines is constant between the points where
    one test changes: tau is tried at c's earliest time and at every bound plus 0 to 3 or W - 1 to W + 3 inside c's
    range. Certain when every baseline at every tau is true, possible when one is true or undetermined. Exact when the
    times are independent: unless two point readings of the item class are linked (then sound and inexact), or more
    than RELATIVE_UNCERTAIN_LIMIT readings of uncertain time could precede c within W (then possible, inexact)."""
    defect = _D33_DEFECT
    factor, floor = Fraction(_decimal_of(rel['factor'])), Fraction(_decimal_of(rel['floor']))
    k = rel['previous'] + (1 if defect == 'k_plus_one' else 0)
    W = rel['within'] + (1 if defect == 'window_off_by_one' else 0)
    unit, rise = rel['unit'], rel['kind'] == 'rise'
    out, exact = {}, True
    sorted_a = {item: [x.a for x in members] for item, members in by_class.items()}
    widest = {item: max((x.b - x.a for x in members), default=0) for item, members in by_class.items()}
    key = lambda x, t: (t, x.rank)

    def verdict(c, S):  # True, False, or None (undetermined)
        if not S:
            return False
        if any(x.value is None for x in S):
            return None
        if defect != 'unit_mixing' and any(x.unit != unit for x in S):
            return None
        base = sum((x.value for x in S), Fraction(0)) / len(S)
        m = base if defect == 'floor_ignored' else max(floor, base)
        return c.value >= factor * m if rise else c.value * factor <= m
    for c in cands:
        members = by_class[c.item]
        a_c, b_c = c.a, c.b
        arr = sorted_a[c.item]
        window = members[bisect_left(arr, a_c - W - widest[c.item]):bisect_right(arr, b_c - 1)]
        near = [x for x in window if x is not c and x.b >= a_c - W]
        if defect == 'excluded_ignored':
            near = [x for x in near if x.value is not None]
        if linked[c.item]:
            exact = False
        uncertain = sum(1 for x in near if x.a != x.b) + (a_c != b_c)
        if uncertain > RELATIVE_UNCERTAIN_LIMIT:
            out[c.eid] = 'possible'
            exact = False
            continue
        if uncertain == 0:  # every time exact (as on MIMIC, and for period readings): one baseline
            tau = a_c
            S = nlargest(k, (x for x in near if tau - W <= x.a <= tau - 1), key=lambda x: key(x, x.a))
            v = verdict(c, S)
            if v:
                out[c.eid] = 'certain'
            elif v is None:
                out[c.eid] = 'possible'
            continue
        offsets = (0, 1, 2, 3, W - 1, W, W + 1, W + 2, W + 3)
        taus = {a_c} | {p + d for x in near for p in (x.a, x.b) for d in offsets if a_c <= p + d <= b_c}
        holds = undecided = fails = False
        baselines = set()
        for tau in sorted(taus):
            inable = []
            for x in near:
                lo, hi = max(x.a, tau - W), min(x.b, tau - 1)
                if lo <= hi:
                    inable.append((x, lo, hi, x.a <= tau - W - 1 or x.b >= tau))
            forced = [i for i in inable if not i[3]]
            optional = [i for i in inable if i[3]]
            found = []
            if len(forced) < k:
                for z in range(k - len(forced)):
                    found.extend(forced + list(extra) for extra in combinations(optional, z))
            top = [i for i in inable
                   if sum(1 for y, lo, _, _ in forced if y is not i[0] and key(y, lo) > key(i[0], i[2])) < k]
            for S in combinations(top, k):
                theta = min(key(s, hi) for s, _, hi, _ in S)
                chosen = {id(s) for s, _, _, _ in S}
                if all(out_ or key(x, lo) < theta for x, lo, _, out_ in inable if id(x) not in chosen):
                    found.append(list(S))
            for S in found:
                members_ = [s[0] for s in S]
                baselines.add(frozenset(id(x) for x in members_))
                v = verdict(c, members_)
                if v:
                    holds = True
                elif v is None:
                    undecided = True
                else:
                    fails = True
            if (fails or undecided) and (holds or undecided) and defect != 'determined_only':
                break
        certain = holds and not undecided and not fails
        if defect == 'determined_only':
            certain = certain and len(baselines) == 1
        if certain:
            out[c.eid] = 'certain'
        elif holds or undecided:
            out[c.eid] = 'possible'
    return out, exact


_NO_READINGS = {'rows': {}, 'excluded': {}, 'period': frozenset(), 'covered': {}}


def derive_patient(request, pid, window, events, mentions, readings=None):
    """`execution_store.derive_patient` (D32), with relative tags (D33): a relative condition also reads the patient's
    `readings` (`_readings`), the series rows and excluded records of the item classes its concept covers. Raises
    `DERIVED_EVENT_ID_TAKEN` when an event of the patient carries a derived point's identifier. For Nth and count tags
    the code is the store's, line for line."""
    times = tg.patient_times({'patient_id': pid, 'window': window, 'events': events}, trusted=True)
    defect = es._D32_DEFECT
    out = es.Derived()
    out.families, out.linked, out.points, out.inexact = {}, set(), {}, False
    out.concepts = {e.id: e.concepts for e in times.events}
    certain, possible, second = {}, {}, False
    trigger = request.derived_used if defect != 'pass2_unrelaxed_only' else {
        name: spec for name, spec in request.derived_used.items() if name in {s['concept'] for s in request.query['steps']}}
    points, ids = [], {e.id for e in times.events}
    prepared = None
    for name, spec in request.derived_used.items():
        if spec['kind'] == 'relative':  # D33
            if prepared is None:
                prepared = _patient_readings(readings or _NO_READINGS, times)
            by_class, linked = prepared
            concept, (unit, _, _) = request.tags[spec['concept']]
            cands = [x for item in sorted((readings or _NO_READINGS)['covered'].get(concept, ()))
                     for x in by_class.get(item, ()) if x.eid is not None and x.unit == unit and x.eid in ids]
            out.families[name] = {x.eid for x in cands}
            strengths, exact = _relative_strengths(spec['relative'], cands, by_class, linked)
            if not exact and strengths:
                out.inexact = True
            for eid, strength in strengths.items():
                (certain if strength == 'certain' else possible).setdefault(eid, []).append(name)
                if strength == 'certain':
                    possible.setdefault(eid, []).append(name)
            if name in trigger and any(v == 'possible' for v in strengths.values()):
                second = True
            continue
        family = [r for r in times.candidate_records(spec['concept']) if r[tg._EVENT].id not in mentions]
        out.families[name] = {r[tg._EVENT].id for r in family}
        if any(r[tg._KEYS] is not None for r in family):
            out.linked.add(spec['concept'])
        n = spec['n']
        if spec['kind'] == 'nth':
            if defect == 'nth_off_by_one':
                n += 1
            if len(family) < n:
                continue
            if defect == 'rule_r':
                ranked = sorted(family, key=lambda r: (r[tg._SLO], r[tg._SHI], r[tg._EVENT].id))
                pick = ranked[n - 1] if spec['which'] == 'nth' else ranked[-n]
                lo, hi = pick[tg._SLO], pick[tg._SHI]
            else:
                bounds = [r[tg._EVENT].s if defect == 'recorded_bounds' else (r[tg._SLO], r[tg._SHI]) for r in family]
                lows, highs = sorted(b[0] for b in bounds), sorted(b[1] for b in bounds)
                front = spec['which'] == 'nth' or defect == 'nth_last_from_front'
                lo, hi = (lows[n - 1], highs[n - 1]) if front else (lows[-n], highs[-n])
            if name in ids:
                raise es.StoreError('DERIVED_EVENT_ID_TAKEN', f'{pid}/{name}')
            points.append({'id': name, 'concepts': [name], 'start': [lo, hi]})
            out.points[name] = [lo, hi]
        else:
            strengths, exact = es._count_strengths(times, family, n, spec['within'], window)
            if not exact and strengths:
                out.inexact = True
            for eid, strength in strengths.items():
                (certain if strength == 'certain' else possible).setdefault(eid, []).append(name)
                if strength == 'certain':
                    possible.setdefault(eid, []).append(name)
            if name in trigger and any(v == 'possible' for v in strengths.values()):
                second = True

    def doc(tagged):
        return {'patient_id': pid, 'window': window,
                'events': [{**e, 'concepts': sorted(set(e['concepts']) | set(tagged[e['id']]))} if e['id'] in tagged else e
                           for e in events] + points}
    out.first = doc(certain)
    out.second = doc(possible) if second else None
    return out


def match_derived(pq, request, derived):
    """`execution_store.match_derived` (D32's two-pass rule), with relative steps (D33) among the steps whose condition
    leaves a result undecided (`uncertain_steps` with STEP_CONDITION_UNCERTAIN), as count steps are. Otherwise the
    store's code, line for line; its table and argument hold unchanged."""
    coupled = es._coupled_steps(pq, request, derived)
    counted = [s for s in pq.step_ids if request.kinds.get(s, ('',))[0] in ('count', 'relative')]
    one = es._one_pass(pq, derived.first, coupled)
    pid = derived.first['patient_id']

    def uncertain(other=None, **extra):
        # Pass 2 unresolved for its own reason (an Nth step's coupling, an uncertain first or last target, a stopped
        # search) is reported with that reason (D32 review); otherwise the step's condition left it undecided.
        if other is not None and other['status'] == 'unresolved':
            steps = {'uncertain_steps': other['uncertain_steps']} if 'uncertain_steps' in other else {}
            return {'patient_id': pid, 'status': 'unresolved', 'reason': other['reason'], **steps, **extra,
                    'complete': False}
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'STEP_CONDITION_UNCERTAIN',
                'uncertain_steps': counted, **extra, 'complete': False}
    if derived.inexact:
        if one['status'] == 'possible':
            return uncertain()
        if one['status'] == 'unresolved' and one.get('relaxed_match', {}).get('certainty') == 'possible':
            one = {k: v for k, v in one.items() if k != 'relaxed_match'}
    if derived.second is None or one['status'] in ('exact', 'possible', 'unresolved'):
        if derived.inexact and one['status'] == 'relaxed' and one['certainty'] == 'possible':
            return uncertain()
        return one
    two = es._one_pass(pq, derived.second, coupled)
    if one['status'] == 'relaxed':
        if two['status'] == 'relaxed' and two['relaxations'] == one['relaxations']:
            if derived.inexact and one['certainty'] == 'possible':
                return uncertain()
            out = {**{k: v for k, v in one.items() if k not in ('complete', 'note')},
                   'complete': one['complete'] and two['complete']}
            note = es._notes(one, two)
            return {**out, 'note': note} if note else out
        match = {k: one[k] for k in ('relaxations', 'cost', 'certainty', 'binding', 'method')}
        if derived.inexact and one['certainty'] == 'possible':
            return uncertain(two)
        return uncertain(two, relaxed_match=match)
    if two['status'] == 'none':
        out = {'patient_id': pid, 'status': 'none', 'complete': one['complete'] and two['complete']}
        note = es._notes(one, two)
        return {**out, 'note': note} if note else out
    return uncertain(two)


# The store: candidates, readings, chunks, reach, context and `match`.

def _derived_patients(store, name, request):
    """`execution_store._derived_patients`; a relative tag's events are events of its unit tag, so its patients are
    those holding at least one (D33)."""
    spec = request.derived[name]
    if spec['kind'] != 'relative':
        return es._derived_patients(store, name, request)
    return {idx for idx, k in es._tag_counts(store, request.tags[spec['concept']]).items() if k >= 1}


def _candidates(store, pq, request=None):
    """`execution_store._candidates`, reading a relative tag's patients by `_derived_patients` above."""
    query = pq.query
    per_step = es._step_concepts(query)
    base = per_step if request is None else request.base
    es._known(store, sorted({c for cs in base.values() for c in cs}))
    es._visible_to_exclusions(store, sorted({c for cs in base.values() for c in cs}))
    es._check_tag_names(store, request)
    tags = {} if request is None else request.tags
    derived = {} if request is None else request.derived
    plan = []
    for step, concepts in per_step.items():
        plain = [c for c in concepts if c not in tags and c not in derived]
        patients = {r[0] for r in store.query(
            f'SELECT DISTINCT patient_idx FROM concept_postings WHERE concept IN {es._in(plain)}', plain)} if plain else set()
        for c in concepts:
            if c in tags:
                patients |= es._tag_patients(store, tags[c])
            elif c in derived:
                patients |= _derived_patients(store, c, request)
        plan.append({'step': step, 'concepts': concepts, 'patients': patients})
    plan.sort(key=lambda s: (len(s['patients']), s['step']))
    survivors = None
    for s in plan:
        survivors = set(s['patients']) if survivors is None else survivors & s['patients']
        if not survivors:
            break
    return {'patient_idx': sorted(survivors or ()),
            'plan': [{'step': s['step'], 'concepts': s['concepts'], 'patients': len(s['patients'])} for s in plan]}


def candidates(store, query, conditions=None):
    """`execution_store.candidates`, for a 1.2 request too; any other request is the store's own call."""
    if not is_relative_request(conditions):
        return es.candidates(store, query, conditions)
    request = Request(query, conditions)
    es._require_series(store, request)
    return _candidates(store, tg.PreparedQuery(request.query), request)


def _readings(store, idxs, request):
    """{patient_idx: readings} for these candidates (D33): for every concept a relative tag of the request reads, the
    series rows of the item classes it covers (event id, item class, unit, exact value, event kind, recorded start, in
    any unit) and the excluded records of those item classes (their recorded starts), with the event kinds the
    snapshot's label `period_reading_kinds` declares period summaries."""
    concepts = sorted({request.tags[spec['concept']][0] for spec in request.derived_used.values()
                       if spec['kind'] == 'relative'})
    if not concepts or not idxs:
        return {}
    label = (store.manifest.get('labels') or {}).get(PERIOD_LABEL)
    period = frozenset(k for k in (label or '').split(',') if k)
    out = {}

    def mine(idx):
        if idx not in out:
            out[idx] = {'rows': {}, 'excluded': {}, 'period': period, 'covered': {}}
        return out[idx]
    try:
        es._temp(store, '_m_read', [('idx', 'INTEGER')], list(idxs))
        rows = store.db.execute(f"""
            SELECT s.patient_idx, cc.ancestor, s.event_id, s.item_class, s.unit, s.value_decimal, e.kind, e.start_lo,
                   e.start_hi
            FROM _m_read c JOIN series s ON s.patient_idx = c.idx
            JOIN events e ON e.bucket = s.bucket AND e.patient_idx = s.patient_idx AND e.event_id = s.event_id
            JOIN concept_closure cc ON cc.concept = s.item_class
            WHERE s.patient_idx BETWEEN ? AND ? AND cc.ancestor IN {es._in(concepts)}""",
                                [min(idxs), max(idxs)] + concepts).fetchall()
        for idx, concept, eid, item, unit, text, kind, lo, hi in rows:
            r = mine(idx)
            r['rows'][eid] = (item, unit, text, kind, (lo, hi))
            r['covered'].setdefault(concept, set()).add(item)
        dropped = store.db.execute(f"""
            SELECT x.patient_idx, x.concept, x.start_lo, x.start_hi
            FROM _m_read c JOIN excluded_records x ON x.patient_idx = c.idx
            WHERE x.patient_idx BETWEEN ? AND ? AND x.concept IN (
                SELECT concept FROM concept_closure WHERE ancestor IN {es._in(concepts)})""",
                                   [min(idxs), max(idxs)] + concepts).fetchall()
        for idx, item, lo, hi in dropped:
            mine(idx)['excluded'].setdefault(item, []).append((lo, hi))
    finally:
        es._drop(store, '_m_read')
    return out


def _derived_chunk(store, request, idxs, pids, windows, linked):
    """Each candidate's `Derived` (D32), from its loaded events: the request's plain concepts and value tags, the
    concepts and value tags its derived tags count included; with a relative condition (D33), its readings too."""
    tags = request.used
    events = (es._load_events(store, idxs, request.load, linked, tags) if tags
              else es._load_events(store, idxs, request.load, linked))
    mentions = es._mentions(store, idxs)
    readings = _readings(store, idxs, request) if request.relative else {}
    return [(idx, derive_patient(request, pid, w, events.get(idx, []), mentions.get(idx, set()), readings.get(idx)))
            for idx, pid, w in zip(idxs, pids, windows)]


def _match_derived_chunk(store, pq, request, idxs, pids, windows, linked):
    """[(patient_idx, result)] for one chunk of a request with derived steps (D32, D33)."""
    return [(idx, match_derived(pq, request, derived))
            for idx, derived in _derived_chunk(store, request, idxs, pids, windows, linked)]


def _derived_reach(request, query, touched, windows_by_idx):
    """`execution_store._derived_reach` (D18 for derived steps, in place); a relative step's reach is its scope (D33),
    so it is left as the excluded-records rule found it."""
    for idx, (admitted, every) in touched.items():
        w = windows_by_idx[idx]
        for sid, (kind, detail) in request.kinds.items():
            if sid not in every or kind == 'relative':  # D33: a relative step's reach is its scope
                continue
            if kind == 'nth':
                if es._D32_DEFECT != 'scoped_reach':
                    admitted[sid] = every[sid]
            elif detail != 'window':
                admitted[sid] = every[sid]
            else:
                hits = [(lo, hi) for lo, hi in every[sid] if lo <= w['end'] and hi >= w['start']]
                if hits:
                    admitted[sid] = hits
                else:
                    admitted.pop(sid, None)


def match_context(store, query, windows, conditions=None):
    """`execution_store.match_context`; for a 1.2 request it adds the derived and relative rules' digests and binds this
    module's code beside the engine's artifacts."""
    context, context_id = es.match_context(store, query, windows, conditions)
    if not is_relative_request(conditions):
        return context, context_id
    context['artifacts'] = {**context['artifacts'],
                            MODULE_PATH: hashlib.sha256((ROOT / MODULE_PATH).read_bytes()).hexdigest()}
    context.update(derived_rules=es._digest(es.DERIVED_RULES), relative_rules=es._digest(RELATIVE_RULES))
    return context, es._digest(context)


_W = {}  # per worker process: the prepared 1.2 request it last ran (the store is `execution_store._W['store']`)


def _worker_unit(unit):
    """`execution_store._worker_unit` for a 1.2 request: ('ok', [(idx, result)], peak RSS) or ('error', type, message).
    It runs in the store's own worker processes (`execution_store.get_pool`), over the store they opened."""
    key, query, conditions, idxs, pids, windows, linked, fault = unit
    try:
        if fault == 'exit':
            os._exit(3)
        if fault == 'raise':
            raise RuntimeError('injected failure')
        if _W.get('key') != key:
            request = Request(query, conditions)
            pq = tg.PreparedQuery(request.query)
            _W.update(key=key, pq=pq, per_step=es._step_concepts(pq.query), request=request)
        pq, per_step, request, store = _W['pq'], _W['per_step'], _W['request'], es._W['store']
        if request.derived:
            return ('ok', _match_derived_chunk(store, pq, request, idxs, pids, windows, linked), es._peak_rss())
        tags = request.used
        events = (es._load_events(store, idxs, per_step, linked, tags) if tags
                  else es._load_events(store, idxs, per_step, linked))
        out = [(idx, tg.match_prepared(pq, {'patient_id': pid, 'window': w, 'events': events.get(idx, [])}, trusted=True))
               for idx, pid, w in zip(idxs, pids, windows)]
        return ('ok', out, es._peak_rss())
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc)[:300])


def _run(pool, units):
    """`execution_store.MatchPool.run`, each unit run by `_worker_unit` above in the pool's own workers."""
    pool.warm()
    futures = []
    try:
        for unit in units:
            futures.append(pool._pool().submit(_worker_unit, (*unit, pool.fault)))
    except Exception as exc:
        pool.close()
        raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
    out = []
    try:
        for future in futures:
            try:
                reply = future.result()
            except Exception as exc:  # BrokenProcessPool: a worker died
                pool.close()
                raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
            if reply[0] != 'ok':
                raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{reply[1]}: {reply[2]}')
            pool.worker_peak_rss = max(pool.worker_peak_rss, reply[2])
            out.append(reply[1])
    finally:
        for future in futures:
            future.cancel()
    return out


def match(store, query, windows, detail='full', chunk=es.CHUNK_PATIENTS, workers=1, conditions=None):
    """`execution_store.match`, for a 1.2 request too (D33): a relative step's tag is derived per patient from its
    readings (`derive_patient`) and matched by the two-pass rule (`match_derived`); the output's `derived_steps` adds
    `relative_rules` and the context their digest and this module's. Every other request is the store's own `match`,
    called as it is, so its answer is byte-identical to calling the store directly. The code below is the store's
    `match`, with this module's request, candidates, derivation, reach, context and worker units."""
    if not is_relative_request(conditions):
        return es.match(store, query, windows, detail, chunk, workers, conditions)
    if not (type(workers) is int and workers >= 1):
        raise es.StoreError('INVALID_WORKERS', repr(workers))
    if detail not in es.DETAILS:
        raise es.StoreError('INVALID_DETAIL', repr(detail))
    request = Request(query, conditions)
    tags = request.used
    derived = bool(request.derived)
    es._require_series(store, request)
    pq = tg.PreparedQuery(request.query)
    given, query = query, pq.query
    patients = store.query('SELECT patient_idx, patient_id FROM patients ORDER BY patient_idx')
    # Patients whose events share time variables: their events are loaded whole (`_load_events`).
    linked = {idx for idx, in store.query('SELECT patient_idx FROM patients WHERE shared_variables')}
    by_id = {pid: idx for idx, pid in patients}
    unknown = sorted(set(windows) - set(by_id))
    if unknown:
        raise es.StoreError('UNKNOWN_WINDOW_PATIENT', ', '.join(unknown[:5]))
    windows_by_idx = {}
    for idx, pid in patients:
        w = windows.get(pid)
        if w is not None:
            es._check_window(pid, w)
        windows_by_idx[idx] = w
    pid_of = dict(patients)
    found = _candidates(store, pq, request)
    per_step = es._step_concepts(query) if not derived else request.load  # D32: a derived tag by what it counts
    # The concepts each step's engine concepts stand for (a tag by its concept): what excluded records are matched by.
    base = request.base
    live = [i for i in found['patient_idx'] if windows_by_idx[i] is not None]
    results = {}  # the engine's results, and what a rule then changed; patients absent are filtered or windowless
    parts = [live[k:k + chunk] for k in range(0, len(live), chunk)]
    if es._PRUNE:
        # Candidates the posting summaries prove `none` get the engine's own `none`, and no events are loaded (D25).
        if derived:
            pruned = es._prune(store, pq, live, windows_by_idx, tags=tags, derived=request.derived_used)
        else:
            pruned = es._prune(store, pq, live, windows_by_idx, tags=tags) if tags else es._prune(store, pq, live, windows_by_idx)
        for idx in pruned:
            results[idx] = es._NONE(pid_of[idx])
        parts = [kept for part in parts if (kept := [i for i in part if i not in pruned])]
    if workers > 1 and parts:
        key = es._digest([given, conditions])
        units = [(key, given, conditions, part, [pid_of[i] for i in part], [windows_by_idx[i] for i in part],
                  [i for i in part if i in linked]) for part in parts]
        for unit in _run(es.get_pool(store, workers), units):
            results.update(unit)
    elif derived:
        for part in parts:
            results.update(_match_derived_chunk(store, pq, request, part, [pid_of[i] for i in part],
                                                [windows_by_idx[i] for i in part], [i for i in part if i in linked]))
    else:
        for part in parts:
            mine = [i for i in part if i in linked]
            events = es._load_events(store, part, per_step, mine, tags) if tags else es._load_events(store, part, per_step, mine)
            for idx in part:
                results[idx] = tg.match_prepared(
                    pq, {'patient_id': pid_of[idx], 'window': windows_by_idx[idx], 'events': events.get(idx, [])}, trusted=True)
            del events
    filtered = [idx for idx, _ in patients if windows_by_idx[idx] is not None and idx not in results]
    filtered_result = lambda idx: {'patient_id': pid_of[idx], 'status': 'none', 'reason': 'FILTERED', 'complete': True}
    nones = sorted(filtered + [idx for idx, r in results.items() if r['status'] == 'none'])
    for idx, reason, missing, unrecorded in es._comparability(store, per_step, nones, windows_by_idx,
                                                              chunk * es.SET_CHUNK_FACTOR, tags):
        results[idx] = {'patient_id': pid_of[idx], 'status': 'not_comparable', 'reason': reason,
                        'uncovered_steps': missing}
        if unrecorded:
            results[idx]['unrecorded_steps'] = unrecorded
    # Records the ingest left out can change any engine decision, not only a `none`.
    decided = {idx for idx, w in windows_by_idx.items()
               if w is not None and (results[idx]['status'] in es.DECIDED if idx in results else True)}
    touched = es._excluded_starts(store, query, base, decided, windows_by_idx)
    bound = [idx for idx in touched if idx in results and (
        results[idx].get('binding') or results[idx].get('relaxed_match', {}).get('binding')
        or results[idx].get('unverified_match', {}).get('binding'))]
    starts = es._bound_starts(store, bound)
    occurrence = None
    if derived:
        # D32: Nth steps reach and displace as the decision log states; a derived point's start range is its envelope.
        # D33: a relative step's reach is its scope.
        _derived_reach(request, query, touched, windows_by_idx)
        occurrence = {sid: 'first' if which == 'nth' else 'last'
                      for sid, (kind, which) in request.kinds.items() if kind == 'nth'}
        nth_bound = [idx for idx in bound if occurrence]
        for k in range(0, len(nth_bound), chunk):
            part = nth_bound[k:k + chunk]
            for idx, d in _derived_chunk(store, request, part, [pid_of[i] for i in part],
                                         [windows_by_idx[i] for i in part], [i for i in part if i in linked]):
                starts.setdefault(idx, {}).update(d.points)
    for idx, (hit, every) in touched.items():
        results[idx] = es.apply_exclusions(query, results[idx] if idx in results else filtered_result(idx),
                                           hit, starts.get(idx, {}), every, occurrence)
    counts = {}
    for r in results.values():
        counts[r['status']] = counts.get(r['status'], 0) + 1
    implicit_filtered = sum(1 for idx in filtered if idx not in results)
    windowless = sum(1 for w in windows_by_idx.values() if w is None)
    if implicit_filtered:
        counts['none'] = counts.get('none', 0) + implicit_filtered
    if windowless:
        counts['excluded'] = counts.get('excluded', 0) + windowless
    context, context_id = match_context(store, given, windows, conditions)
    out = {'profile': es.MATCH_PROFILE, 'engine': tg.PROFILE_ID, 'snapshot_id': store.snapshot_id,
           'verified': store.verified, 'coverage_rule': es.COVERAGE_RULE, 'exclusion_rule': es.EXCLUSION_RULE,
           'labels': store.manifest.get('labels'), 'context': context, 'context_id': context_id,
           'plan': found['plan'], 'candidates': len(found['patient_idx']), 'counts': dict(sorted(counts.items()))}
    if request.tags or not derived:
        out['value_conditions'] = request.describe()
    if derived:
        out['derived_steps'] = request.describe_derived()
    if detail == 'decided':
        out['detail'] = 'decided'
        out['implicit'] = {'FILTERED': implicit_filtered, 'NO_INDEX_WINDOW': windowless}
        out['results'] = [results[idx] for idx in sorted(results)]
    else:
        out['results'] = [results[idx] if idx in results else
                          filtered_result(idx) if windows_by_idx[idx] is not None else
                          {'patient_id': pid, 'status': 'excluded', 'reason': 'NO_INDEX_WINDOW'}
                          for idx, pid in patients]
    return out
