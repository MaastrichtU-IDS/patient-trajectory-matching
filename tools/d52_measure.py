"""D52's measurements on the Python side (decision log, D52): the worlds harness (rule 1), its planted defects, the
reference check (rule 2) and the census's admission facts (rule 3).

    python tools/d52_measure.py worlds [--cases N] [--seed LABEL] [--output REPORT.json]
    python tools/d52_measure.py defects [--cases N] [--seed LABEL] [--output REPORT.json]
    python tools/d52_measure.py reference-check SNAPSHOT ROWS.json      # called by d52_anchor.cjs reference
    python tools/d52_measure.py facts SNAPSHOT < ITEMS.json             # called by d52_anchor.cjs census

**The worlds harness (rule 1): the anchor's uncertainty must be covered.** Tiny patients on an integer grid, many to a
snapshot (as `patterns/coded_only_cases.py`): coded events of x, y or both (z is the ancestor of both), points and
exposures with uncertain ranges, some prenatal (before 0); letter mentions from the patient's floor (0, or an earlier
record) to their letter, as the converter times them; shared variables; partial coverage. Each request is a page-like
definition: one or two user steps (any, first or last) with a relation, relations from START and END with and without a
maximum, their relaxations, the four scopes, coded only or not; and an index period of every supported shape (an event
start with a span, a span with an event end, two events with one or two hidden steps, an event start with a constant
end, a constant start with an event end), the event's occurrence first, last, Nth or Nth from last (N up to 3; an Nth
ranks coded events only, as D32's derived occurrence, with or without coded only), offsets from -3 to 3.

Each request is rewritten by `index_anchor_reference.rewrite_sides` (the translator's mirror, tied to it byte for byte
by rule 2) and answered by `store_conditions.match` twice: over the base windows (H1) and over the windows
`index_hull.narrow` narrows (H3). The truth is computed without the rewrite: in each world (`trajectory_graph_worlds`'
worlds and target rules; an Nth event's time is the Nth smallest start among the coded events of its concept), the
anchor is placed, the period by the page's rule (start and end from the anchor and its offsets, or the constants; the
patient excluded unless the end is at or after birth, after the start and at or before last contact), and the user's
query is evaluated with that period: the scope against the start clamped at birth and the end, relations from START
against the unclamped start (D52, Q2), from END against the end. Exact, possible, relaxed and none are judged as
`trajectory_graph_worlds.check` judges them, the D44 and D48b readings included; an `excluded` result must have no world
with a period. 0 failing cases is the bar; H1 and H3 must also give equal results wherever both are decided (not
`unresolved`, not excluded) and neither by a window-reading rule (coverage, admission).

**Planted defects** (`defects`): each of `index_anchor_reference.DEFECTS` and `index_hull`'s three (`HULL_DEFECTS`, among
them `nth_counts_mentions`, the review's M1) must fail at least one case.

Synthetic data only.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MOCKUPS = ROOT / 'demos' / 'mockups'
for p in (str(ROOT), str(MOCKUPS)):
    if p not in sys.path:
        sys.path.insert(0, p)

ANCESTORS = {'x': ['z'], 'y': ['z'], 'z': []}
KINDS = ['mention']
CODED = 'execution-store-conditions-1.3'
WINDOW_RULES = ('NO_SOURCE_COVERAGE', 'NO_SOURCE_RECORDS_CONCEPT', 'ADMISSION_EXCLUDED_RECORDS')
SHAPES = ('event+span', 'span+event', 'two events', 'one event twice', 'event+constant', 'constant+event')
MAX_WORLDS = 4000
HULL_DEFECTS = ('hull_from_hi', 'nth_off_by_one', 'nth_counts_mentions')  # index_hull's planted defects


def closure(concepts):
    return sorted(set(concepts) | {a for c in concepts for a in ANCESTORS[c]})


# Tiny patients.

def tiny_patient(rng, pid):
    specs = []
    for _ in range(rng.randint(1, 4)):
        concepts = rng.choice((['x'], ['x'], ['y'], ['x', 'y']))
        lo = rng.randint(-2, 6) if rng.random() < 0.15 else rng.randint(0, 6)
        e = {'kind': rng.choice(('condition', 'registry')), 'source': rng.choice(('ehr', 'reg')), 'concepts': concepts,
             'start': [lo, lo + rng.choice((0, 0, 1, 2))]}
        if rng.random() < 0.2:
            end_lo = e['start'][0] + rng.randint(0, 2)
            e['end'] = [end_lo, end_lo + rng.randint(0, 2)]
        specs.append(e)
    floor = min([0] + [e['start'][0] for e in specs])
    for _ in range(rng.choice((0, 0, 1, 1, 2))):
        letter = rng.randint(1, 8)
        lo = floor if rng.random() < 0.3 and letter - floor <= 6 else max(floor, letter - rng.randint(0, 3))
        specs.append({'kind': 'mention', 'source': 'letters', 'concepts': [rng.choice(('x', 'y'))], 'start': [lo, letter]})
    order = list(range(len(specs)))
    rng.shuffle(order)
    events = [{'event_id': f'e{k}', **specs[i], 'evidence': {}} for k, i in enumerate(order)]
    points = [e for e in events if e['kind'] != 'mention' and 'end' not in e]
    if len(points) >= 2 and rng.random() < 0.15:  # two coded events naming one start
        a, b = rng.sample(points, 2)
        b['start_var'] = a['event_id'] + '.s'
        b['start'] = list(a['start'])
    coverage = [{'source': s, 'lo': rng.choice((-2, 0, 0, 3)), 'hi': rng.choice((8, 12, 12)), 'basis': 'declared'}
                for s in ('ehr', 'reg', 'letters') if rng.random() < 0.85]
    statics = {'last': rng.randint(5, 12), 'c_end': rng.randint(-1, 11), 'c_start': rng.randint(0, 5)}
    return {'patient_id': pid, 'clock_id': f'clock_{pid}', 'events': sorted(events, key=lambda e: e['event_id']),
            'coverage': coverage}, statics


def engine_patient(p, coded):
    """The patient's kept events as the worlds check reads them (concepts through the closure)."""
    events = []
    for e in p['events']:
        if coded and e['kind'] in KINDS:
            continue
        out = {'id': e['event_id'], 'concepts': closure(e['concepts']), 'start': list(e['start'])}
        if 'end' in e:
            out['end'] = list(e['end'])
        out.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
        events.append(out)
    return {'patient_id': p['patient_id'], 'window': None, 'events': events}


def tiny_ingest(rng, name, patients):
    from patterns import execution_store as es
    from patterns import trajectory_graph_worlds as tw
    out, statics = [], {}
    while len(out) < patients:
        p, s = tiny_patient(rng, f'{name}-{len(out):03d}')
        try:
            es.validate_patient(p, ANCESTORS)
        except es.IngestError:
            continue
        if tw.count_worlds(engine_patient(p, False), 40) > MAX_WORLDS:
            continue
        out.append(p)
        statics[p['patient_id']] = s
    return {'profile': es.INGEST_PROFILE, 'dataset_id': f'd52-{name}',
            'closure': {'version': 'd52-1', 'ancestors': ANCESTORS}, 'patients': out}, statics


# Tiny requests: a base (page-semantics) request and an index period.

def tiny_anchor(rng):
    concept = rng.choice(('x', 'x', 'y', 'z'))
    occ = rng.choice(('first', 'first', 'last', 'nth', 'nth_last'))
    if occ in ('first', 'last'):
        return {'concept': concept, 'occurrence': occ, 'derived': None}
    return {'concept': concept, 'occurrence': 'any', 'derived': {occ: rng.choice((2, 2, 3)) if occ == 'nth' else 2}}


def tiny_spec(rng):
    """(shape, start, end, steps), in `index_anchor_reference.sides`' form; a constant side names 'c_start' or 'c_end'."""
    shape = rng.choice(SHAPES)
    a, b, d = rng.randint(-3, 3), rng.randint(-3, 3), rng.randint(0, 5)
    S, E = '@index_start', '@index_end'
    def step(sid, anchor):
        return {'id': sid, 'bound': {'event': anchor['concept']}, 'occurrence': anchor['occurrence'], 'derived': anchor['derived'],
                'concept': anchor['concept']}
    if shape == 'event+span':
        steps = [step(S, tiny_anchor(rng))]
        return shape, {'step': S, 'offset': a}, {'step': S, 'offset': a + d}, steps
    if shape == 'span+event':
        steps = [step(E, tiny_anchor(rng))]
        return shape, {'step': E, 'offset': b - d}, {'step': E, 'offset': b}, steps
    if shape == 'two events':
        steps = [step(S, tiny_anchor(rng)), step(E, tiny_anchor(rng))]
        return shape, {'step': S, 'offset': a}, {'step': E, 'offset': b}, steps
    if shape == 'one event twice':
        steps = [step(S, tiny_anchor(rng))]
        return shape, {'step': S, 'offset': a}, {'step': S, 'offset': b}, steps
    if shape == 'event+constant':
        steps = [step(S, tiny_anchor(rng))]
        return shape, {'step': S, 'offset': a}, {'constant': 'c_end'}, steps
    steps = [step(E, tiny_anchor(rng))]
    return shape, {'constant': 'c_start'}, {'step': E, 'offset': b}, steps


def tiny_base(rng, coded):
    """A page-semantics request (D49's form: scope and relations from START and END against the period)."""
    from patterns import trajectory_graph as tg
    steps, relaxations, relations = [], [], []
    for i in range(rng.randint(1, 2)):
        sid = 'AB'[i]
        steps.append({'id': sid, 'concept': rng.choice(('x', 'x', 'y', 'z')), 'occurrence': rng.choice(('any', 'any', 'first', 'last'))})
        if steps[-1]['concept'] != 'z' and rng.random() < 0.2:
            relaxations.append({'id': f'node:{sid}', 'kind': 'widen_concept', 'step': sid, 'concept': 'z', 'cost': '0.5'})
    if len(steps) == 2:
        lo = rng.randint(-3, 2)
        relations.append({'id': 'edge:0', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': lo, 'max': lo + rng.randint(0, 4)})
        if rng.random() < 0.3:
            relaxations.append({'id': 'x0', 'kind': 'extend_max', 'relation': 'edge:0', 'max': relations[-1]['max'] + 2, 'cost': '0.25'})
    if rng.random() < 0.35:
        r = {'id': 'edge:S', 'kind': 'after', 'from': 'START', 'to': 'A', 'min': rng.randint(-1, 1)}
        if rng.random() < 0.7:
            r['max'] = r['min'] + rng.randint(0, 4)
            if rng.random() < 0.4:
                relaxations.append({'id': 'xS', 'kind': 'extend_max', 'relation': 'edge:S', 'max': r['max'] + 2, 'cost': '0.25'})
        relations.append(r)
    if rng.random() < 0.25:
        lo = rng.randint(-3, 2)
        relations.append({'id': 'edge:E', 'kind': 'after', 'from': 'END', 'to': steps[-1]['id'], 'min': lo, 'max': lo + rng.randint(0, 4)})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1')), 'search_budget': 40000}
    conditions = {'profile': CODED, 'steps': {}, 'exclude_kinds': list(KINDS)} if coded else None
    return query, conditions


def base_window(start, end, s):
    """The page's static checks, on the harness's constants: None when no period can be placed whatever the anchor."""
    if 'constant' in end:
        c = s[end['constant']]
        return None if c < 0 or c > s['last'] else {'start': 0, 'end': c}
    if start.get('step') == end.get('step') and end['offset'] <= start['offset']:
        return None
    return {'start': s[start['constant']] if 'constant' in start else 0, 'end': s['last']}


# The truth: a per-world period, placed by the page's rule from the anchor's time in that world.

def anchored_question():
    from patterns import trajectory_graph_worlds as tw

    class Anchored(tw._Question):
        def __init__(self, query, chosen, events, all_worlds, spec, statics, mentions=frozenset()):
            super().__init__(query, chosen, events, all_worlds, None)
            self.start_side, self.end_side, steps = spec
            self.statics = statics
            self.hidden = {st['id']: st for st in steps}
            # An Nth event ranks coded events only (D32): never a letter mention, whatever the request leaves out.
            self.hfamily = {sid: [e for e in events if st['concept'] in e['concepts'] and not (st['derived'] and e['id'] in mentions)]
                            for sid, st in self.hidden.items()}

        def anchor(self, sid, t):
            st, fam = self.hidden[sid], self.hfamily[sid]
            if not fam:
                return None
            if st['occurrence'] == 'first':
                return t[min(fam, key=lambda e: (t[e['sv']], e['s'], e['f'], e['id']))['sv']]
            if st['occurrence'] == 'last':
                return t[max(fam, key=lambda e: (t[e['sv']], e['s'][1], e['s'][0], e['f'], e['id']))['sv']]
            (which, k), = st['derived'].items()
            times = sorted(t[e['sv']] for e in fam)
            if len(times) < k:
                return None
            return times[k - 1] if which == 'nth' else times[-k]

        def side(self, x, t):
            if 'constant' in x:
                return self.statics[x['constant']]
            a = self.anchor(x['step'], t)
            return None if a is None else a + x['offset']

        def period(self, t):
            start, end = self.side(self.start_side, t), self.side(self.end_side, t)
            if start is None or end is None or end < 0 or end <= start or end > self.statics['last']:
                return None
            return start, end

        def holds(self, t, chosen):
            per = self.period(t)
            if per is None:
                return False
            start, end = per
            clamped = max(0, start)
            bound = dict(chosen)
            for s in self.steps:
                if self.occurrence[s] != 'any':
                    e = self.target(s, t)
                    if e is None:
                        return False
                    bound[s] = e
            for s in self.steps:
                v = t[bound[s]['sv']]
                if self.scope in ('from_start', 'within') and v < clamped:
                    return False
                if self.scope == 'within' and v > end:
                    return False
                if self.scope == 'after' and v < end:
                    return False
            value = lambda x: start if x == 'START' else end if x == 'END' else t[bound[x]['sv']]
            for r in self.relations:
                a, b = value(r['from']), value(r['to'])
                if (r.get('min') is not None and b - a < r['min']) or (r.get('max') is not None and b - a > r['max']):
                    return False
            return True

    return Anchored


def check(result, query, patient, spec, statics, Anchored, horizon, mentions=frozenset()):
    """The claims of a store result that are false under the per-world period (as `trajectory_graph_worlds.check`)."""
    from patterns import trajectory_graph_worlds as tw
    events, all_worlds = tw.worlds(patient, horizon)
    by_id = {e['id']: e for e in events}
    sets = tw._sets(query)
    ask = lambda chosen: Anchored(query, chosen, events, all_worlds, spec, statics, mentions)
    unrelaxed = ask(())
    if result['status'] == 'excluded':
        return ['excluded, but some world places a period'] if any(unrelaxed.period(t) for t in all_worlds) else []
    def certainty_holds(question, certainty, binding, settled=True):
        free = {s: by_id[binding[s]] for s in question.free}
        if any(question.concept[s] not in free[s]['concepts'] for s in free):
            return False
        if certainty == 'exact':
            return question.exact_for(free)
        return any(question.holds(t, free) for t in all_worlds) and (not settled or not question.exact())
    wrong = []
    status, complete = result['status'], result.get('complete', True)
    if status == 'exact' and not certainty_holds(unrelaxed, 'exact', result['binding']):
        wrong.append('exact, but its binding fails in some world')
    if status == 'possible' and not certainty_holds(unrelaxed, 'possible', result['binding'], tw.claims_no_exact(result, False)):
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
        if not certainty_holds(ask(chosen), result['certainty'], result['binding'], not tw.stopped_relaxed(result)):
            wrong.append(f'relaxed {result["certainty"]} by {ids}, which its binding does not reach')
    if status == 'none' and complete:
        for c in sets:
            if ask(c).possible():
                wrong.append(f'none, but the set {[x["id"] for x in c]} is possible')
    if status == 'unresolved' and 'relaxed_match' in result:
        m = result['relaxed_match']
        chosen = next(c for c in sets if [x['id'] for x in c] == m['relaxations'])
        if not certainty_holds(ask(chosen), m['certainty'], m['binding'], tw.claims_no_exact(m, False, result)):
            wrong.append('the relaxed match beside unresolved does not reach its certainty')
    return wrong


def horizon_of(patient, statics):
    values = [v for e in patient['events'] for v in e['start'] + e.get('end', []) if v is not None]
    return max(values + [statics['last'], statics['c_end'], 12]) + 1


def run_snapshot(root, label, k, patients=30, requests=20, defect=None):
    """One snapshot: (failing cases, H1/H3 mismatches, cases, status mix, coverage of shapes)."""
    import index_anchor_reference as ref
    import index_hull as hull
    from patterns import execution_store as es
    from patterns import store_conditions as sc
    Anchored = anchored_question()
    rng = random.Random(f'd52/{label}/{k}')
    ingest, statics = tiny_ingest(rng, f'{k}', patients)
    by_id = {p['patient_id']: p for p in ingest['patients']}
    fails, mismatches, mix, shapes, cases = [], [], Counter(), Counter(), 0
    ref_defect = defect if defect in ref.DEFECTS else None
    hull_defect = defect if defect in HULL_DEFECTS else None
    with es.open_store(es.build(ingest, root / f's{k}')) as store, patch.object(ref, '_DEFECT', ref_defect), \
            patch.object(hull, '_DEFECT', hull_defect):
        for j in range(requests):
            shape, start, end, steps = tiny_spec(rng)
            # An Nth event over every record but mentions (D32's reading, the translator's codedOnly 'derived') as well as
            # under coded only (D52 review, M1).
            coded = rng.random() < 0.5
            query, conditions = tiny_base(rng, coded)
            bases = {pid: base_window(start, end, statics[pid]) for pid in by_id}
            out = ref.rewrite_sides({'query': query, 'conditions': conditions, 'windows': bases}, start, end, steps,
                                    [{'id': s['id']} for s in query['steps']], bases)
            try:
                h1 = sc.match(store, out['query'], out['windows'], conditions=out['conditions'])
                narrowed, reasons, _ = hull.narrow(store, out['query'], out['conditions'], out['index'], out['windows'])
                h3 = sc.match(store, out['query'], narrowed, conditions=out['conditions'])
            except es.StoreError as error:  # a request the store refuses by name: none expected
                fails.append({'snapshot': k, 'request': j, 'refused': str(error)})
                continue
            spec = (start, end, steps)
            r1 = {r['patient_id']: r for r in h1['results']}
            r3 = {r['patient_id']: r for r in h3['results']}
            for pid, p in by_id.items():
                cases += 1
                patient = engine_patient(p, coded)
                mentions = frozenset(e['event_id'] for e in p['events'] if e['kind'] in KINDS)
                hz = horizon_of(patient, statics[pid])
                for form, r in (('H1', r1[pid]), ('H3', r3[pid])):
                    mix[(form, r['status'])] += 1
                    wrong = check(r, query, patient, spec, statics[pid], Anchored, hz, mentions)
                    if wrong:
                        fails.append({'snapshot': k, 'request': j, 'patient': pid, 'form': form, 'shape': shape, 'wrong': wrong})
                a, b = r1[pid], r3[pid]
                decided = lambda r: r['status'] not in ('unresolved', 'excluded') and r.get('reason') not in WINDOW_RULES
                if decided(a) and decided(b):
                    key = lambda r: (r['status'], r.get('certainty'), tuple(r.get('relaxations') or ()))
                    if key(a) != key(b):
                        mismatches.append({'snapshot': k, 'request': j, 'patient': pid, 'H1': key(a), 'H3': key(b)})
                if a['status'] in ('exact', 'possible', 'relaxed', 'none') and steps_uncertain(store, by_id[pid], steps, coded):
                    shapes[(shape, 'decided, uncertain anchor')] += 1
                shapes[(shape, 'cases')] += 1
    return fails, mismatches, cases, mix, shapes


def steps_uncertain(store, p, steps, coded):
    """Whether some hidden step's candidates leave its time uncertain (more than one possible time)."""
    for st in steps:
        fam = [e for e in p['events'] if not ((coded or st['derived']) and e['kind'] in KINDS) and st['concept'] in closure(e['concepts'])]
        if len(fam) > 1 or any(e['start'][0] < e['start'][1] for e in fam):
            return True
    return False


def worlds(cases, label, defect=None, quiet=False):
    started = time.perf_counter()
    fails, mismatches, mix, shapes, total, k = [], [], Counter(), Counter(), 0, 0
    with tempfile.TemporaryDirectory(prefix='d52-worlds-') as tmp:
        while total < cases:
            f, m, c, x, s = run_snapshot(Path(tmp), label, k, defect=defect)
            fails += f
            mismatches += m
            total += c
            mix.update(x)
            shapes.update(s)
            k += 1
            if not quiet and k % 20 == 0:
                print(f'{label}: {total} cases, {len(fails)} failing, {len(mismatches)} H1/H3 mismatches', file=sys.stderr)
    return {'seed': label, 'defect': defect, 'cases': total, 'snapshots': k, 'failing_cases': len(fails),
            'examples': fails[:10], 'h1_h3_mismatches': len(mismatches), 'mismatch_examples': mismatches[:10],
            'status_mix': {f'{a} {b}': n for (a, b), n in sorted(mix.items())},
            'shapes': {f'{a}: {b}': n for (a, b), n in sorted(shapes.items())},
            'seconds': round(time.perf_counter() - started)}


def artifacts():
    import hashlib
    files = ['tools/d52_measure.py', 'demos/mockups/index_anchor_reference.py', 'demos/mockups/index_hull.py',
             'patterns/execution_store.py', 'patterns/store_conditions.py', 'patterns/store_conditions_coded.py',
             'patterns/store_conditions_counts.py', 'patterns/trajectory_graph.py', 'patterns/trajectory_graph_worlds.py',
             'patterns/trajectory_times.py', 'patterns/temporal_stn.py']
    return {f: hashlib.sha256((ROOT / f).read_bytes()).hexdigest() for f in files}


# Rule 2: the reference check.

def reference_check(snapshot, rows_file):
    """The JS translator's rewritten requests against `index_anchor_reference.rewrite`, and `index_hull.narrow` against
    the reference's narrowing over the converted ingest, for each row d52_anchor.cjs exported."""
    import index_anchor_reference as ref
    import index_hull as hull
    import store_parity
    from patterns import execution_store as es
    from patterns import store_conditions_gene as gc
    data = json.loads(Path(rows_file).read_text())
    cohort = json.loads(Path(data['cohort']).read_text())
    patients = {p['patient_id']: p for p in cohort['patients']}
    concepts = cohort['concepts']
    ingest = {p['patient_id']: p for p in gc.iter_patients(cohort, {}, series=True)}
    from patterns import synthetic_ingest as si
    ancestors = si.synthetic_header(cohort)['closure']['ancestors']
    births = store_parity.birth_micros(cohort)
    MINUTE = hull.MINUTE
    out = {'requests': 0, 'request_mismatches': 0, 'point_alike': 0, 'windows': 0, 'window_mismatches': 0, 'examples': []}
    with es.open_store(Path(snapshot)) as store:
        for row in data['rows']:
            out['requests'] += 1
            code = ref.point_reason(row['window'], row['nodes'], row['free_text'], row['coded_mode'], concepts)
            here = row['here']
            if code or here.get('index_anchor') or here.get('refused'):
                # A point period (D52 review, S2): D49's request, unchanged, with the reason.
                theirs = {k: here[k] for k in ('query', 'conditions', 'windows') if k in here}
                if not here.get('refused') and code == (here.get('index_anchor') or {}).get('reason') \
                        and json.dumps(theirs, sort_keys=True) == json.dumps(row['base'], sort_keys=True) and 'index' not in here:
                    out['point_alike'] += 1
                else:
                    out['request_mismatches'] += 1
                    out['examples'].append({'name': row['name'], 'reference': code, 'here': here.get('refused') or here.get('index_anchor') or 'rewritten'})
                continue
            mine = ref.rewrite(row['base'], row['window'], row['nodes'], patients)
            theirs = {k: here[k] for k in ('query', 'conditions', 'windows', 'index')}
            if json.dumps(mine, sort_keys=True) != json.dumps(theirs, sort_keys=True):
                out['request_mismatches'] += 1
                if len(out['examples']) < 10:
                    out['examples'].append({'name': row['name'], 'differs': sorted(k for k in theirs if json.dumps(mine.get(k), sort_keys=True) != json.dumps(theirs[k], sort_keys=True))})
                continue
            # The narrowing, by SQL (index_hull) and by loops over the converted ingest (the reference).
            micros = {pid: None if w is None else {'start': births[pid] + w['start'] * MINUTE, 'end': births[pid] + w['end'] * MINUTE}
                      for pid, w in theirs['windows'].items()}
            narrowed, reasons, _ = hull.narrow(store, store_parity.to_micros(theirs['query']), theirs['conditions'], theirs['index'], micros, MINUTE)
            kinds = (theirs['conditions'] or {}).get('exclude_kinds', ())
            derived = (theirs['conditions'] or {}).get('steps', {})
            for pid, w in micros.items():
                out['windows'] += 1
                rng = {}
                for st in theirs['query']['steps']:
                    if not st['id'].startswith('@index_'):
                        continue
                    rng[st['id']] = ref.anchor_range(ingest[pid]['events'], st['concept'], ancestors, st['occurrence'],
                                                     (derived.get(st['id']) or {}).get('occurrence'), kinds)
                rw = {sid: (lo, hi, n) for sid, (lo, hi, n) in rng.items()}
                idx = {side: None if x is None else {'step': x['step'], 'offset': x['offset'] * MINUTE} for side, x in theirs['index'].items() if side in ('start', 'end')}
                want, why = ref.narrow(w, idx, rw)
                if want != narrowed[pid] or why != reasons.get(pid):
                    out['window_mismatches'] += 1
                    if len(out['examples']) < 10:
                        out['examples'].append({'name': row['name'], 'patient_id': pid, 'reference': [want, why], 'here': [narrowed[pid], reasons.get(pid)]})
    return out


# Rule 3: the census's facts (coverage and admission over two windows).

def facts(snapshot, items):
    """For each item {key, patient_id, steps: {step: [concepts]}, sides: {name: {scope, window}}} (windows in minutes from
    birth, or None): the excluded records of each step's concepts that each side's scope and window admit
    (`execution_store.admits_start`, D44's admission scope)."""
    import store_parity
    from patterns import execution_store as es
    cohort = json.loads(Path(items['cohort']).read_text())
    births = store_parity.birth_micros(cohort)
    MINUTE = 60_000_000
    out = {}
    with es.open_store(Path(snapshot)) as store:
        idx_of = dict((pid, idx) for idx, pid in store.query('SELECT patient_idx, patient_id FROM patients'))
        excluded = {}
        for idx, ancestor, lo, hi in store.query('''SELECT x.patient_idx, cc.ancestor, x.start_lo, x.start_hi FROM excluded_records x
                                                    JOIN concept_closure cc ON cc.concept = x.concept'''):
            excluded.setdefault(idx, []).append((ancestor, lo, hi))
        for item in items['items']:
            pid = item['patient_id']
            idx = idx_of[pid]
            res = {}
            for side in sorted(item['sides']):
                w = item['sides'][side]['window']
                if w is None:
                    res[side] = None
                    continue
                win = {'start': births[pid] + w['start'] * MINUTE, 'end': births[pid] + w['end'] * MINUTE}
                admitted = sorted({(step, lo, hi) for step, cs in item['steps'].items() for c, lo, hi in excluded.get(idx, [])
                                   if c in cs and es.admits_start(item['sides'][side]['scope'], win, lo, hi)})
                res[side] = {'admitted': [list(x) for x in admitted]}
            out[item['key']] = res
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)
    w = sub.add_parser('worlds')
    w.add_argument('--cases', type=int, default=200_000)
    w.add_argument('--seed', default='d52-rule')
    w.add_argument('--output')
    d = sub.add_parser('defects')
    d.add_argument('--cases', type=int, default=12_000)
    d.add_argument('--seed', default='d52-defects-rule')
    d.add_argument('--output')
    r = sub.add_parser('reference-check')
    r.add_argument('snapshot')
    r.add_argument('rows')
    f = sub.add_parser('facts')
    f.add_argument('snapshot')
    args = parser.parse_args(argv)
    if args.command == 'reference-check':
        print(json.dumps(reference_check(args.snapshot, args.rows)))
        return 0
    if args.command == 'facts':
        print(json.dumps(facts(args.snapshot, json.load(sys.stdin))))
        return 0
    if args.command == 'worlds':
        report = worlds(args.cases, args.seed)
        decided = {k: v for k, v in report['shapes'].items() if 'decided' in k}
        report.update(profile='d52-worlds-1.0', rule=(
            'D52 rule 1: 0 failing cases under the per-world period, over both window forms (H1 base, H3 narrowed); H1 and H3 '
            'equal wherever both are decided (not unresolved, not excluded) and neither by a window-reading rule; at least 1,000 decided cases '
            'with an uncertain anchor per shape'),
            met=report['failing_cases'] == 0 and report['h1_h3_mismatches'] == 0
            and all(decided.get(f'{s}: decided, uncertain anchor', 0) >= 1000 for s in SHAPES),
            artifacts=artifacts(), contains_patient_level_data=False)
    else:
        import index_anchor_reference as ref
        runs = {}
        for defect in ref.DEFECTS + HULL_DEFECTS:
            res = worlds(args.cases, f'{args.seed}/{defect}', defect=defect, quiet=True)
            runs[defect] = {'cases': res['cases'], 'failing_cases': res['failing_cases'], 'h1_h3_mismatches': res['h1_h3_mismatches'],
                            'example': res['examples'][:1]}
            print(f'{defect}: {res["failing_cases"]} failing of {res["cases"]}', file=sys.stderr)
        report = {'profile': 'd52-defects-1.0', 'seed': args.seed, 'cases_per_defect': args.cases, 'defects': runs,
                  'rule': 'D52 rule 1: each planted defect fails at least one case',
                  'met': all(v['failing_cases'] > 0 for v in runs.values()), 'artifacts': artifacts(),
                  'contains_patient_level_data': False}
    text = json.dumps(report, indent=1) + '\n'
    if args.output:
        Path(args.output).write_text(text)
    print(text if not args.output else f'{report["profile"]}: met {report["met"]}', file=sys.stderr if args.output else sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
