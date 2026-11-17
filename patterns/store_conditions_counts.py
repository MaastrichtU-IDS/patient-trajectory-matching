"""Counts decided where D32's two-pass rule leaves them undecided (D43, rule `count_narrowed_v1`), as a post-pass of the
dispatcher (`store_conditions.match`).

D32 tags each counted record as *certain* (the count is reached at it in every choice of the patient's times) or
*possible* (in some). Pass 1 matches over the certain records, pass 2 over certain and possible ones, and where the two
differ the result is `unresolved` with STEP_CONDITION_UNCERTAIN: pass 2 tests "the count is reached at e" and "the
relation holds" separately, so nothing but its `none` can be read. This module decides such results where it can.

The rule (`RULE`), only for a result `unresolved` with STEP_CONDITION_UNCERTAIN, only when every count tag a tried set
uses was decided by the exact sweep (`inexact` false) and no tried tag is a rise or fall (D33):
1. *The narrowed pass (two').* For every count tag a tried set uses, each possible-only record e is replaced, for that
   tag, by copies over the maximal integer intervals of
       Narrow(e) = {tau in e's tightened start range (cut to the window for a period count) :
                    at least n - 1 other counted records can lie in [tau - span, tau] (in [window start, tau])},
   computed by the sweep's own interval counters at the same breakpoints (`narrow`). A copy is e with its start cut to
   the interval, its own end range and proper flag kept, and fresh variables; it carries only the tag. Certain
   records keep the tag, Nth and gene points stay, as in pass 1. The engine runs once over that patient, with D32's
   coupling rule extended to count steps (`_coupled_counts`): a set whose result would be `possible` and that rests on a
   count step with copies is undecided when the step's records share a variable or another step can bind one of them.
2. *Combination*, with pass 1 as D32 ran it:
   - two' `none`, both passes complete: `none`;
   - two' finds the unrelaxed query (exact or possible), at most one copy in its binding: `possible`, with two''s
     binding (a copy read as its record) and a witness built in the patient's own times (`_witness`: the relations, the
     scope, and n - 1 counted records placed in the span or period of each step bound to a copy); where no such witness
     is found, D32's answer stands;
   - two' relaxed by S, both passes complete, at most one copy in its binding: pass 1's result if pass 1 is relaxed by
     S, otherwise relaxed by S with certainty `possible` and two''s binding;
   - anything else: D32's answer stands.

Why it is sound (decision log, D43): every world in which e reaches its count has t(e) in Narrow(e), so it maps into
two' with the copy at t(e) (a `none` is sound); an exact binding with one copy holds at every tau in Narrow(e), and the
sweep is exact, so a world reaching the count there exists (truly possible); a possible binding lifts when the count
step is not coupled (the witnesses constrain nothing else); exactness decomposes, so it is pass 1's, which tags the
certain records only.

Where it runs. `store_conditions.match` calls `post_pass` after the routed module answers a request with a count step
(profiles 1.1, 1.2, 1.3, gene, and D42's joint gene-coded profile). Each STEP_CONDITION_UNCERTAIN patient is re-derived through the answering module's own
`_derived_chunk` (`execution_store`, `store_conditions_relative`, `store_conditions_coded`, `store_conditions_gene`), its
two-pass result recomputed and checked against the answer, the rule applied, and a changed result given the store's
later rules as the module applies them, a chunk at a time: coverage to a refined `none`, then the excluded-records
rule (D18). A patient whose re-derived two-pass result differs from the answer is left as it is and counted
(`count_refinement.skipped_mismatch`). The chunks (`_chunk_size`) run in the store's worker pool
when the request runs with `workers > 1` (D43b), in this process otherwise; every step is per patient, so the answer is
byte-identical at any worker count and chunk size. No build loads this module, and no file the D34 build record lists changes for
it, so the scaling builds stay reusable; the dispatcher, `store_parity.py` and `gene_no_change.py` do change, and the
reports binding them are rebound or frozen (D43). Callers that skip the dispatcher (`execution_store.match`, a module's
own `match`) keep D32's rule: never contradicted by this one, less decided (the declared difference, D43).
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
import hashlib
from itertools import combinations, product
import json
import os

from . import execution_store as es
from . import trajectory_graph as tg
from ._resources import ROOT

MODULE_PATH = 'patterns/store_conditions_counts.py'
METHOD = 'count_narrowed_v1'
RULE = ('count_narrowed_v1: where the two-pass rule answers unresolved (STEP_CONDITION_UNCERTAIN) and every count tag a '
        'tried set uses was decided by the exact sweep, each possible-only record of a count tag is replaced by copies '
        'over the maximal integer intervals of the times in its tightened range at which at least N - 1 other counted '
        'records can lie in [t - span, t] (in [window start, t], t in the window), each keeping the record\'s end, and '
        'the engine runs once (two\'), with the coupling rule extended to count steps that rest on copies; two\' none: '
        'none; the unrelaxed query found with at most one copy bound: possible; relaxed by S with at most one copy bound: '
        'pass 1\'s result if pass 1 is relaxed by S, else relaxed by S, certainty possible; both passes complete for '
        'none and relaxed; a possible result carries a witness in the patient\'s own times where each step bound to a '
        'copy reaches its count, and without one the two-pass answer stands; otherwise the two-pass answer stands. '
        'Coverage and the excluded-records rule then apply to a changed result. Applied by the dispatcher only')
SEP = '@narrowed/'  # a copy's id: <tag>@narrowed/<record id>/<interval index>
COUNT_PROFILES = (es.CONDITIONS_PROFILE_1_1, 'execution-store-conditions-1.2', 'execution-store-conditions-1.3',
                  'execution-store-conditions-gene-1.0', 'execution-store-conditions-gene-coded-1.0')  # D42's joint too
# Planted defects for the tests (D43 rules); None in every real run. The post-pass passes the parent's to its pool
# workers with each unit (D43b), so a defect acts at any worker count.
DEFECTS = ('whole_range', 'coupling_ignored', 'certainty_from_two', 'point_without_end', 'two_copies',
           'decided_refined', 'no_d18', 'no_coverage', 'any_witness')
# D43b: the results to refine are cut into about POST_PASS_UNITS chunks of at least POST_PASS_MIN_CHUNK results (and at
# most the request's `chunk`), whatever the worker count: enough units to keep a pool of 14 busy, each large enough to
# amortise its loads. Any cut gives the same output.
POST_PASS_UNITS = 64
POST_PASS_MIN_CHUNK = 50
WITNESS_TRIES = 20_000  # choices of count witnesses tried per refined result before D32's answer is kept
_D43_DEFECT = None


def has_counts(conditions):
    """Whether a request's conditions give some step a count (the requests the post-pass reads)."""
    return (isinstance(conditions, dict) and conditions.get('profile') in COUNT_PROFILES
            and isinstance(conditions.get('steps'), dict)
            and any(isinstance(raw, dict) and 'count' in raw for raw in conditions['steps'].values()))


# Per patient.

def narrow(family, k, n, within, window):
    """The maximal integer intervals [lo, hi] of Narrow(e) for the record at index k of `family` (kernel records): the
    times in e's tightened range (cut to the window for a period count) at which at least n - 1 other records can lie in
    [tau - span, tau] (in [window start, tau]). The count is `execution_store._count_strengths`'s allowed counter, over
    the same intervals, read at the same breakpoints."""
    a, b = family[k][tg._SLO], family[k][tg._SHI]
    ivs = []
    for j, r in enumerate(family):
        if j == k:
            continue
        ax, bx = r[tg._SLO], r[tg._SHI]
        if within == 'window':
            if bx >= window['start']:
                ivs.append((ax, tg.INF))
        else:
            ivs.append((ax, bx + within))
    if within == 'window':
        a, b = max(a, window['start']), min(b, window['end'])
    if a > b:
        return []
    if _D43_DEFECT == 'whole_range':
        return [[a, b]]
    los, his = sorted(lo for lo, _ in ivs), sorted(hi for _, hi in ivs)
    points = {a}
    points.update(x for x in los if a < x <= b)
    points.update(h + 1 for h in his if h != tg.INF and a < h + 1 <= b)
    points = sorted(points)
    out = []
    for i, p in enumerate(points):
        q = points[i + 1] - 1 if i + 1 < len(points) else b
        if bisect_right(los, p) - bisect_left(his, p) >= n - 1:
            if out and out[-1][1] == p - 1:
                out[-1][1] = q
            else:
                out.append([p, q])
    return out


def _narrowed(request, derived):
    """(two''s engine patient, {copy id: record id}, the tags with copies), or None where the rule does not apply: a
    tried count tag decided by the pairwise test, a tried rise or fall, or a copy id an event already carries."""
    if derived.inexact:
        return None
    first = derived.first
    times = tg.patient_times(first, trusted=True)
    events = {e['id']: e for e in first['events']}
    copies, back, tags = [], {}, set()
    for name, spec in request.derived_used.items():
        if spec['kind'] in ('nth', 'gene'):
            continue  # its points are pass 1's
        if spec['kind'] != 'count':
            return None  # a rise or fall (D33): its possible readings are not narrowed
        mine = derived.families.get(name, set())
        family = [r for r in times.candidate_records(spec['concept']) if r[tg._EVENT].id in mine]
        strengths, _ = es._count_strengths(times, family, spec['n'], spec['within'], first['window'])
        # (a family the pairwise test decided would make `derived.inexact` true, refused above)
        for k, r in enumerate(family):
            eid = r[tg._EVENT].id
            if strengths.get(eid) != 'possible':
                continue
            src = events[eid]
            kept = {key: v for key, v in src.items() if key not in ('id', 'concepts', 'start', 'start_var', 'end_var')}
            if _D43_DEFECT == 'point_without_end':
                kept = {}
            for j, (lo, hi) in enumerate(narrow(family, k, spec['n'], spec['within'], first['window'])):
                cid = f'{name}{SEP}{eid}/{j}'
                if cid in events:
                    return None
                copies.append({'id': cid, 'concepts': [name], 'start': [lo, hi], **kept})
                back[cid] = eid
                tags.add(name)
    return {**first, 'events': first['events'] + copies}, back, tags


def _coupled_counts(pq, request, derived, tags):
    """{relaxation set key: [count steps]}: the count steps with copies that are coupled in that set (their counted
    records share a variable, or another step can bind one of them), as `execution_store._coupled_steps` reads an Nth
    step."""
    out = {}
    if _D43_DEFECT == 'coupling_ignored':
        return out
    for chosen in [()] + [chosen for _, chosen in pq.sets]:
        plan = pq.plan(chosen)
        steps = []
        for s in pq.step_ids:
            name = plan.concepts[s]
            spec = request.derived.get(name)
            if spec is None or spec['kind'] != 'count' or name not in tags:
                continue
            family = derived.families[name]
            # A family sharing a variable is never narrowed (`derived.inexact`), so only another step can couple it.
            hit = False
            for t in pq.step_ids:
                if hit or t == s:
                    continue
                other = plan.concepts[t]
                if other in request.derived:
                    hit = not derived.families.get(other, set()).isdisjoint(family)
                else:
                    hit = any(other in derived.concepts[eid] for eid in family)
            if hit:
                steps.append(s)
        if steps:
            out[tuple(x['id'] for x in chosen)] = steps
    return out


def _order_key(e, which):
    """The worlds checker's tie-break between events of a `first` (`last`) step's concept at one time."""
    f = e.s if e.point else e.f
    return (e.s, f, e.id) if which == 'first' else (e.s[1], e.s[0], f, e.id)


def _witness(pq, request, derived, binding, back, doc):
    """The bound steps' start times in one choice of the patient's own times (pass 1's patient, each copy read as its
    record) in which the unrelaxed query's relations and scope hold, every step bound to a copy reaches its count (n - 1
    other counted records placed in [t - span, t], or in [window start, t] with t in the window), and each `first` or
    `last` step's event, chosen among its concept's events, is the first (last) one with the worlds checker's
    tie-breaks. None when no choice gives one within `WITNESS_TRIES` (D43 review, M1: a time two' allows need not leave
    room for the count's records, nor fit another step that binds one of them)."""
    if _D43_DEFECT == 'any_witness':  # the reviewed defect: any times two' allows
        times = tg.patient_times(doc, trusted=True)
        by_id = {e.id: e for e in times.events}
        bound = {s: by_id[v] for s, v in binding.items()}
        at = tg.feasible(bound, [], times).witness()
        return {s: at[tg._node(e.sv)] for s, e in sorted(bound.items())}
    times = tg.patient_times(derived.first, trusted=True)
    by_id = {e.id: e for e in times.events}
    bound = {s: by_id[back.get(v, v)] for s, v in binding.items()}
    plan = pq.plan()
    constraints = list(plan.constraints)
    pools, ordered = [], []
    for s in sorted(binding):
        v = binding[s]
        if plan.occurrence[s] in ('first', 'last'):
            family = [e for e in times.events if plan.concepts[s] in e.concepts]
            ordered.append((s, plan.occurrence[s], family))
            pools.append(family)
        elif v in back:
            spec = request.derived[plan.concepts[s]]
            others = [by_id[eid] for eid in sorted(derived.families[plan.concepts[s]]) if eid != back[v]]
            pools.append(list(combinations(others, spec['n'] - 1)))
            if spec['within'] == 'window':
                constraints += [tg.Constraint(('START', None), (s, 's'), 0, tg.INF, 'count'),
                                tg.Constraint((s, 's'), ('END', None), 0, tg.INF, 'count')]
    kinds = [('order', s) if plan.occurrence[s] in ('first', 'last') else ('count', s) for s in sorted(binding)
             if plan.occurrence[s] in ('first', 'last') or binding[s] in back]
    which = dict((s, w) for s, w, _ in ordered)
    families = dict((s, f) for s, _, f in ordered)
    for tries, picks in enumerate(product(*pools)):
        if tries >= WITNESS_TRIES:
            break
        extra, more = dict(bound), list(constraints)
        for (kind, s), pick in zip(kinds, picks):
            if kind == 'order':
                extra[s] = pick
                for k, x in enumerate(families[s]):
                    if x is pick:
                        continue
                    name = f'\x00order\x00{s}\x00{k}'
                    extra[name] = x
                    strict = -1 if ((_order_key(x, which[s]) < _order_key(pick, which[s])) == (which[s] == 'first')) else 0
                    if which[s] == 'first':  # t(pick) - t(x) <= 0, or -1 when x ranks first at a tie
                        more.append(tg.Constraint((name, 's'), (s, 's'), -tg.INF, strict, 'order'))
                    else:  # t(x) - t(pick) <= 0, or -1 when x ranks last at a tie
                        more.append(tg.Constraint((s, 's'), (name, 's'), -tg.INF, strict, 'order'))
                continue
            spec = request.derived[plan.concepts[s]]
            for k, x in enumerate(pick):
                name = f'\x00count-witness\x00{s}\x00{k}'
                extra[name] = x
                if spec['within'] == 'window':
                    more += [tg.Constraint(('START', None), (name, 's'), 0, tg.INF, 'count'),
                             tg.Constraint((name, 's'), (s, 's'), 0, tg.INF, 'count')]
                else:
                    more.append(tg.Constraint((name, 's'), (s, 's'), 0, spec['within'], 'count'))
        network = tg.feasible(extra, more, times)
        if network.feasible:
            at = network.witness()
            return {s: at[tg._node(extra[s].sv)] for s in sorted(binding)}
    return None


def refine(pq, request, derived, v1):
    """(result, how): D32's result `v1` for this patient (the answering module's `match_derived` on `derived`), or the
    decided result `count_narrowed_v1` gives; `how` names the case (`kept: ...` or `decided: ...`)."""
    if not (v1['status'] == 'unresolved' and v1.get('reason') == 'STEP_CONDITION_UNCERTAIN') \
            and _D43_DEFECT != 'decided_refined':
        return v1, 'kept: not STEP_CONDITION_UNCERTAIN'
    built = _narrowed(request, derived)
    if built is None:
        return v1, 'kept: not exact'
    doc, back, tags = built
    d32 = es._coupled_steps(pq, request, derived)
    coupled = dict(d32)
    for key, steps in _coupled_counts(pq, request, derived, tags).items():
        coupled[key] = sorted(set(coupled.get(key, [])) | set(steps), key=pq.step_ids.index)
    one = es._one_pass(pq, derived.first, d32)
    two = es._one_pass(pq, doc, coupled)
    pid = v1['patient_id']
    copies = lambda binding: sum(1 for v in binding.values() if v in back)
    as_records = lambda binding: {s: back.get(v, v) for s, v in binding.items()}
    if one['status'] not in ('relaxed', 'none') and _D43_DEFECT != 'decided_refined':
        return v1, 'kept: pass 1 ' + one['status']
    if two['status'] in ('none', 'relaxed') and not (one['complete'] and two['complete']):
        return v1, 'kept: a search stopped or a cheaper set was skipped'
    if two['status'] == 'none':
        out = {'patient_id': pid, 'status': 'none', 'complete': True}
        note = es._notes(one, two)
        return ({**out, 'note': note} if note else out), 'decided: none'
    if two['status'] in ('exact', 'possible'):
        if copies(two['binding']) > 1 and _D43_DEFECT != 'two_copies':
            return v1, 'kept: two copies bound'
        if _D43_DEFECT == 'certainty_from_two' and two['status'] == 'exact':
            return {'patient_id': pid, 'status': 'exact', 'binding': as_records(two['binding']), 'method': METHOD,
                    'complete': True}, 'decided: exact (defect)'
        witness = _witness(pq, request, derived, two['binding'], back, doc)
        if witness is None:
            return v1, 'kept: no witness'
        return {'patient_id': pid, 'status': 'possible', 'binding': as_records(two['binding']), 'witness': witness,
                'method': METHOD, 'complete': True}, f'decided: possible (two\' {two["status"]})'
    if two['status'] == 'relaxed':
        if copies(two['binding']) > 1 and _D43_DEFECT != 'two_copies':
            return v1, 'kept: two copies bound'
        if one['status'] == 'relaxed' and one['relaxations'] == two['relaxations'] \
                and _D43_DEFECT != 'certainty_from_two':
            out = {**{k: v for k, v in one.items() if k not in ('complete', 'note')}, 'complete': True}
            return out, 'decided: relaxed (pass 1\'s set)'
        certainty = two['certainty'] if _D43_DEFECT == 'certainty_from_two' else 'possible'
        return {'patient_id': pid, 'status': 'relaxed', 'relaxations': two['relaxations'], 'cost': two['cost'],
                'certainty': certainty, 'binding': as_records(two['binding']), 'method': METHOD,
                'complete': True}, f'decided: relaxed possible (two\' {two["certainty"]})'
    return v1, f'kept: two\' {two["status"]} ({two.get("reason")})'


# The post-pass.

class _Answering:
    """The module that answered a request, and what the post-pass calls of it: its request, two-pass rule, chunk
    derivation, coverage and D18 reach, as its own `match` calls them."""

    def __init__(self, query, conditions):
        from . import store_conditions_relative as rc
        profile = conditions['profile']
        self.rc, self.gene = rc, None
        if profile == es.CONDITIONS_PROFILE_1_1:
            self.module, self.request = es, es.Request(query, conditions)
            self.match_derived, self.reach = es.match_derived, es._derived_reach
        elif profile == rc.CONDITIONS_PROFILE_1_2:
            self.module, self.request = rc, rc.Request(query, conditions)
            self.match_derived, self.reach = rc.match_derived, rc._derived_reach
        elif profile == 'execution-store-conditions-1.3':
            from . import store_conditions_coded as cc
            self.module, self.request = cc, cc.Request(query, conditions)
            self.match_derived, self.reach = rc.match_derived, rc._derived_reach
        else:  # the gene profile, or D42's joint profile (gene steps under coded only), both answered there
            from . import store_conditions_gene as gc
            self.module, self.request, self.gene = gc, gc.Request(query, conditions), gc
            self.match_derived, self.reach = rc.match_derived, gc._derived_reach

    def chunk(self, store, idxs, pids, windows, linked, findings):
        if self.gene is not None:
            return self.module._derived_chunk(store, self.request, idxs, pids, windows, linked, findings)
        return self.module._derived_chunk(store, self.request, idxs, pids, windows, linked)

    def uncovered(self, store, idxs, windows_by_idx):
        """The coverage rule's verdicts on refined `none` results: {patient_idx: (reason, uncovered, unrecorded)}."""
        if not idxs:
            return {}
        request = self.request
        per_step, tags, nones = request.load, request.used, sorted(idxs)
        if self.gene is not None:  # the gene module's coverage: gene steps by their reports' sources, D39's hook for
            # the rest with the kinds a joint request leaves out (none for the gene profile), as its `match` reads it
            from . import store_conditions_coded as cc
            steps = {sid: [request.genes[sid]] if sid in request.genes else cs for sid, cs in per_step.items()}
            found = cc._comparability(store, steps, nones, windows_by_idx, request.exclude,
                                      es.CHUNK_PATIENTS * es.SET_CHUNK_FACTOR, tags, self.gene._gene_sources(store, request))
        elif self.module.__name__.endswith('store_conditions_coded'):
            found = self.module._comparability(store, per_step, nones, windows_by_idx, request.exclude, tags=tags)
        else:
            found = es._comparability(store, per_step, nones, windows_by_idx, tags=tags)
        return {idx: (reason, missing, unrecorded) for idx, reason, missing, unrecorded in found}

    def exclusions(self, store, query, refined, windows_by_idx, derived, findings):
        """D18 on refined results ({patient_idx: result}), as the module's `match` applies it, in one pass."""
        if not refined:
            return refined
        request = self.request
        touched = es._excluded_starts(store, query, request.base, set(refined), windows_by_idx)
        if self.gene is not None:
            self.gene._undetermined_reach(store, request, query, touched, set(refined), windows_by_idx, findings)
        if not touched:
            return refined
        self.reach(request, query, touched, windows_by_idx)
        occurrence = {sid: 'first' if which == 'nth' else 'last'
                      for sid, (kind, which) in request.kinds.items() if kind == 'nth'}
        bound = [idx for idx in touched if refined[idx].get('binding') or refined[idx].get('relaxed_match', {}).get('binding')]
        starts = es._bound_starts(store, bound)
        out = dict(refined)
        for idx, (hit, every) in touched.items():
            mine = {**starts.get(idx, {}), **derived[idx].points} if occurrence else starts.get(idx, {})
            out[idx] = es.apply_exclusions(query, refined[idx], hit, mine, every, occurrence)
        return out


def bind_context(context):
    """A count request's context, binding this module's code and the rule (whether or not a result moved)."""
    context = {**context, 'artifacts': {**context['artifacts'],
                                        MODULE_PATH: hashlib.sha256((ROOT / MODULE_PATH).read_bytes()).hexdigest()},
               'count_rule': es._digest(RULE)}
    return context, es._digest(context)


def _refine_chunk(store, answering, pq, rows, windows_by_idx, linked, findings):
    """([(k, refined result)], mismatches) for one chunk of STEP_CONDITION_UNCERTAIN results: `rows` is
    [(k, patient_idx, patient_id, the answer's result)], `windows_by_idx` and `linked` (the patients sharing variables)
    cover the chunk, `findings` are its gene findings (None outside a gene request). The chunk is re-derived through the
    answering module's `_derived_chunk`, each patient's two-pass result checked against the answer (a mismatch is left
    as it is, and counted), the rule applied, then coverage to a refined `none` and D18 to the rest, as the module
    applies them. Every step is per patient, so the output does not depend on how the results are cut into chunks, nor
    on which process runs a chunk (D43b)."""
    request = answering.request
    idxs = [idx for _, idx, _, _ in rows]
    derived = dict(answering.chunk(store, idxs, [pid for _, _, pid, _ in rows], [windows_by_idx[i] for i in idxs],
                                   [i for i in idxs if i in linked], findings))
    refined, where, mismatches = {}, {}, 0
    for k, idx, _, answer in rows:
        v1 = answering.match_derived(pq, request, derived[idx])
        if json.dumps(v1) != json.dumps(answer):
            mismatches += 1  # not the module's own two-pass answer: left as it is, counted
            continue
        result, _ = refine(pq, request, derived[idx], v1)
        if result is not v1:
            refined[idx], where[idx] = result, k
    if _D43_DEFECT != 'no_coverage':
        for idx, (reason, missing, unrecorded) in answering.uncovered(
                store, [i for i, r in refined.items() if r['status'] == 'none'], windows_by_idx).items():
            refined[idx] = {'patient_id': refined[idx]['patient_id'], 'status': 'not_comparable', 'reason': reason,
                            'uncovered_steps': missing}
            if unrecorded:
                refined[idx]['unrecorded_steps'] = unrecorded
    if _D43_DEFECT != 'no_d18':
        kept = {i: r for i, r in refined.items() if r['status'] != 'not_comparable'}
        refined.update(answering.exclusions(store, pq.query, kept, windows_by_idx, derived, findings))
    return sorted(((where[idx], result) for idx, result in refined.items()), key=lambda item: item[0]), mismatches


# The post-pass in the store's worker pool (D43b). Each chunk of STEP_CONDITION_UNCERTAIN results is a unit run by
# `_worker_unit` in the processes of `execution_store.get_pool`, over the store each opened, as the answering modules
# run their own units (`store_conditions_relative._worker_unit`); the parent merges the units by result position. No
# file a build loads changes for it.

_W = {}  # per worker process: the answering module and prepared query of the count request it last ran


def _worker_unit(unit):
    """`_refine_chunk` in a worker of the store's pool: ('ok', ([(k, result)], mismatches), peak RSS) or
    ('error', type, message). The planted defect is the parent's, passed with the unit."""
    global _D43_DEFECT
    key, query, conditions, rows, windows_by_idx, linked, findings, defect, fault = unit
    try:
        if fault == 'exit':
            os._exit(3)
        if fault == 'raise':
            raise RuntimeError('injected failure')
        _D43_DEFECT = defect
        if _W.get('key') != key:
            answering = _Answering(query, conditions)
            _W.update(key=key, answering=answering, pq=tg.PreparedQuery(answering.request.query))
        return ('ok', _refine_chunk(es._W['store'], _W['answering'], _W['pq'], rows, windows_by_idx, set(linked),
                                    findings), es._peak_rss())
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


def _chunk_size(targets, chunk):
    """How many of `targets` results to refine one unit takes: about `targets / POST_PASS_UNITS`, at least
    POST_PASS_MIN_CHUNK, at most `chunk`. It depends on neither the worker count nor any answer."""
    return max(1, min(chunk, max(POST_PASS_MIN_CHUNK, -(-targets // POST_PASS_UNITS))))


def post_pass(store, query, windows, chunk, conditions, out, workers=1):
    """The routed module's answer `out` to a request with a count step, with each result `unresolved` with
    STEP_CONDITION_UNCERTAIN refined where `count_narrowed_v1` decides it, coverage and D18 reapplied to it, `counts`
    recomputed, and the rule and the tally named in `derived_steps.count_refinement` and bound in the context. Every
    other result is unchanged.

    The results to refine are cut into chunks (`_chunk_size`, whatever `workers` is). With `workers > 1` the chunks run
    in the store's worker pool (`execution_store.get_pool`, the pool the module's own answer used), otherwise in this
    process. Every step is per patient and the chunks merge by result position, so the output is byte-identical for any
    worker count and any chunk size (D43b)."""
    context, context_id = bind_context(out['context'])
    out = {**out, 'context': context, 'context_id': context_id}
    targets = [k for k, r in enumerate(out['results'])
               if r['status'] == 'unresolved' and r.get('reason') == 'STEP_CONDITION_UNCERTAIN']
    tally = {'rule': RULE, 'refined': 0, 'skipped_mismatch': 0}
    results, counts = list(out['results']), dict(out['counts'])
    if targets:
        answering = _Answering(query, conditions)
        request = answering.request
        by_pid, linked = {}, set()
        try:  # the targets' patients only (D43b: not every patient's row, in the parent)
            es._temp(store, '_m_count_targets', [('pid', 'VARCHAR')], [results[k]['patient_id'] for k in targets])
            for idx, pid, shared in store.query('SELECT p.patient_idx, p.patient_id, p.shared_variables FROM '
                                                '_m_count_targets t JOIN patients p ON p.patient_id = t.pid'):
                by_pid[pid] = idx
                if shared:
                    linked.add(idx)
        finally:
            es._drop(store, '_m_count_targets')
        rows = [(k, by_pid[results[k]['patient_id']], results[k]['patient_id'], results[k]) for k in targets]
        windows_by_idx = {idx: windows[pid] for _, idx, pid, _ in rows}
        findings = None
        if answering.gene is not None and request.genes:
            findings = answering.gene._gene_findings(store, request, [idx for _, idx, _, _ in rows])
        size = _chunk_size(len(rows), chunk)
        units = []
        for start in range(0, len(rows), size):
            part = rows[start:start + size]
            idxs = [idx for _, idx, _, _ in part]
            units.append((part, {i: windows_by_idx[i] for i in idxs}, [i for i in idxs if i in linked],
                          None if findings is None else {i: findings[i] for i in idxs if i in findings}))
        if workers > 1:
            key = es._digest([query, conditions])
            replies = _run(es.get_pool(store, workers), [(key, query, conditions, *unit, _D43_DEFECT) for unit in units])
        else:
            pq = tg.PreparedQuery(request.query)
            replies = [_refine_chunk(store, answering, pq, part, wins, set(mine), found)
                       for part, wins, mine, found in units]
        for changed, mismatches in replies:
            tally['skipped_mismatch'] += mismatches
            for k, result in changed:
                results[k] = result
                tally['refined'] += 1
                counts['unresolved'] -= 1
                if not counts['unresolved']:
                    del counts['unresolved']
                counts[result['status']] = counts.get(result['status'], 0) + 1
    out = {**out, 'counts': dict(sorted(counts.items())), 'results': results}
    if 'derived_steps' in out:
        out['derived_steps'] = {**out['derived_steps'], 'count_refinement': tally}
    return out
