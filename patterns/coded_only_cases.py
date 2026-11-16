"""Small snapshots, seeded requests and the independent reference for coded-only requests (D39, profile
`execution-store-conditions-1.3`).

Shared by `test_coded_only.py` and the ad hoc soundness runs the decision log reports.

**The reference** (`without_kinds`, `reference_conditions`, `Reference`) shares no code with `store_conditions_coded`.
It removes every event of an excluded kind, with its series rows, from the ingest document with dictionaries and loops,
and calls the existing reference (`prepared_reference.PreparedReference`, or `execution_store_reference.exhaustive_match`)
on what is left, with the request as profile 1.2 (or without conditions when no step is conditioned). It knows nothing of
exclusion by request, so it tests the store's filters, coverage, candidate refinement and loaders against the
definition itself (decision log, D39: "as the same request would be answered on the snapshot without those events").

**The small cases** are tiny patients on an integer grid, many to a snapshot, so that the store answers them through
`store_conditions_coded.match` over a real snapshot and `trajectory_graph_worlds` can enumerate every choice of the times
of each patient's **kept** events. Two generators:
- `coded-plain`: coded events of x, y or both (z is wider than both) and letter mentions of x or y, point events and
  exposures, some proper; shared variables, among them a mention naming a coded event's start or an exposure's end over part of its range; excluded records;
  partial coverage, among it a letter source that alone covers some windows. Requests of one to three steps with `any`,
  `first` and `last`, relations, relaxations and every scope.
- `coded-derived`: the same patients; each request with an Nth occurrence or a count (`derived_cases.tiny_request`)
  beside such steps.
Event ids are drawn from one sequence per patient, so a mention in one patient often has the id of a coded event in
another (which the planted defect `coded_dropped` needs to show itself).
"""
from __future__ import annotations

from collections import Counter
import json
import random
from unittest.mock import patch

from . import derived_cases as dc
from . import execution_store as es
from . import execution_store_reference as ref
from . import prepared_reference as pr
from . import store_conditions_coded as cc
from . import trajectory_graph as tg
from . import trajectory_graph_worlds as tw

PROFILE = 'execution-store-conditions-1.3'
RELATIVE_PROFILE = 'execution-store-conditions-1.2'
GENERATORS = ('coded-plain', 'coded-derived')
KINDS = ['mention']
ANCESTORS = {'x': ['z'], 'y': ['z'], 'z': []}


# The reference.

def without_kinds(ingest, kinds):
    """The ingest document with every event whose kind is in `kinds` removed, with its series rows; everything else
    (patients, coverage, excluded records, closure, provenance) as it was."""
    kinds = set(kinds)
    patients = []
    for p in ingest['patients']:
        gone = {e['event_id'] for e in p['events'] if e['kind'] in kinds}
        q = {k: v for k, v in p.items() if k not in ('events', 'series')}
        q['events'] = [e for e in p['events'] if e['event_id'] not in gone]
        rows = [r for r in p.get('series', []) if r['event_id'] not in gone]
        if rows:
            q['series'] = rows
        patients.append(q)
    return {**ingest, 'patients': patients}


def reference_conditions(conditions):
    """A 1.3 request's conditions as the reference reads them: profile 1.2 without `exclude_kinds`, or None when no
    step is conditioned."""
    if not conditions['steps']:
        return None
    out = {k: v for k, v in conditions.items() if k != 'exclude_kinds'}
    out['profile'] = RELATIVE_PROFILE
    return out


class Reference:
    """The independent reference over one ingest: `match` is `PreparedReference(without_kinds(ingest)).match` on the
    request read by `reference_conditions`; `exhaustive` is `exhaustive_match` likewise (every event, no filter)."""

    def __init__(self, ingest, kinds=KINDS):
        self.ingest = without_kinds(ingest, kinds)
        self.prepared = pr.PreparedReference(self.ingest)

    def match(self, query, windows, conditions):
        return self.prepared.match(query, windows, reference_conditions(conditions))

    def exhaustive(self, query, windows, conditions):
        return ref.exhaustive_match(self.ingest, query, windows, reference_conditions(conditions))


def unfiltered(result):
    """A store result as the reference writes it: a patient the candidate filter proved `none` (reason FILTERED) is the
    reference's `none`."""
    if result.get('reason') == 'FILTERED':
        return {k: v for k, v in result.items() if k != 'reason'}
    return result


def coded(query, conditions):
    """A request with `exclude_kinds: ["mention"]`, as profile 1.3."""
    out = {'profile': PROFILE, 'steps': dict((conditions or {}).get('steps', {})), 'exclude_kinds': list(KINDS)}
    if (conditions or {}).get('relaxations'):
        out['relaxations'] = conditions['relaxations']
    return query, out


# Small snapshots.

def tiny_patient(rng, pid):
    """One tiny patient as an ingest record (see the module's docstring)."""
    specs = []
    for _ in range(rng.randint(1, 5)):
        concepts = rng.choice((['x'], ['x'], ['y'], ['x', 'y']))
        lo = rng.randint(0, 6)
        e = {'kind': rng.choice(('condition', 'registry')), 'source': rng.choice(('ehr', 'reg')), 'concepts': concepts,
             'start': [lo, lo + rng.choice((0, 0, 1, 2, 3))]}
        if rng.random() < 0.25:
            end_lo = lo + rng.randint(0, 2)
            e['end'] = [end_lo, end_lo + rng.randint(0, 2)]
            if rng.random() < 0.3 and e['end'][1] > lo:
                e['proper'] = True
        specs.append(e)
    for _ in range(rng.choice((0, 1, 1, 2, 3))):
        letter = rng.randint(2, 9)
        specs.append({'kind': 'mention', 'source': rng.choice(('letters', 'letters', 'referral')),
                      'concepts': [rng.choice(('x', 'y'))], 'start': [max(0, letter - rng.randint(0, 4)), letter]})
    order = list(range(len(specs)))
    rng.shuffle(order)
    events = []
    for k, i in enumerate(order):
        events.append({'event_id': f'e{k}', **specs[i], 'evidence': {}})
    coded_points = [e for e in events if e['kind'] != 'mention' and 'end' not in e]
    coded = [e for e in events if e['kind'] != 'mention' and 'start_var' not in e]
    mentions = [e for e in events if e['kind'] == 'mention']
    if mentions and coded and rng.random() < 0.3:
        # A mention naming a coded event's start, or the end of a coded exposure (D39 review), over a random part of
        # that time's range, so that it narrows the coded event's time while it is kept.
        m, c = rng.choice(mentions), rng.choice(coded)
        if 'end' in c and rng.random() < 0.5:
            lo = max(c['end'][0], c['start'][1] + (1 if c.get('proper') else 0))
            name, (lo, hi) = c['event_id'] + '.f', (lo, c['end'][1])
        else:
            name, (lo, hi) = c['event_id'] + '.s', c['start']
        if lo <= hi:
            a = rng.randint(lo, hi)
            m['start_var'], m['start'] = name, [a, rng.randint(a, hi)]
    elif len(coded_points) >= 2 and rng.random() < 0.15:  # two coded events naming one start
        a, b = rng.sample(coded_points, 2)
        b['start_var'] = a['event_id'] + '.s'
        b['start'] = list(a['start'])
    excluded = []
    if rng.random() < 0.15:
        lo = rng.randint(0, 8)
        excluded.append({'source': 'ehr', 'concept': rng.choice(('x', 'y')), 'start': [lo, lo + rng.choice((0, 1))],
                         'reason': 'NOT_ADMITTED'})
    if rng.random() < 0.15:  # only the letters cover this patient
        coverage = [{'source': 'letters', 'lo': 0, 'hi': 12, 'basis': 'declared'}]
    else:
        coverage = [{'source': s, 'lo': rng.choice((0, 0, 0, 4)), 'hi': 12, 'basis': 'declared'}
                    for s in ('ehr', 'reg', 'letters', 'referral') if rng.random() < 0.85]
    out = {'patient_id': pid, 'clock_id': f'clock_{pid}', 'events': sorted(events, key=lambda e: e['event_id']),
           'coverage': coverage}
    if excluded:
        out['excluded'] = excluded
    return out


def tiny_ingest(rng, name, patients):
    out = []
    while len(out) < patients:
        p = tiny_patient(rng, f'{name}-{len(out):03d}')
        try:
            es.validate_patient(p, ANCESTORS)
        except es.IngestError:
            continue
        out.append(p)
    return {'profile': es.INGEST_PROFILE, 'dataset_id': f'coded-only-{name}',
            'closure': {'version': 'coded-only-1', 'ancestors': ANCESTORS}, 'patients': out}


def tiny_plain_request(rng):
    """(query, conditions): one to three steps on x, y or z with any, first or last; no conditioned step."""
    steps, relaxations = [], []
    for i in range(rng.randint(1, 3)):
        sid = 'ABC'[i]
        steps.append({'id': sid, 'concept': rng.choice(('x', 'x', 'y', 'z')),
                      'occurrence': rng.choice(('any', 'any', 'first', 'last'))})
        if steps[-1]['concept'] != 'z' and rng.random() < 0.25:
            relaxations.append({'id': f'w{sid}', 'kind': 'widen_concept', 'step': sid, 'concept': 'z',
                                'cost': rng.choice(('0.5', '1'))})
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        r = {'id': f'R{i}', 'from': a, 'to': b}
        if rng.random() < 0.2:
            relations.append({**r, 'kind': 'during', 'grace': rng.choice((0, 1))})
            continue
        lo = rng.randint(-3, 2)
        r.update(min=lo, max=lo + rng.randint(0, 4))
        relations.append({**r, 'kind': 'after'})
        if rng.random() < 0.3:
            relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                'max': r['max'] + rng.randint(1, 3), 'cost': rng.choice(('0.5', '1'))})
    if rng.random() < 0.25:
        relations.append({'id': 'RS', 'kind': 'after', 'from': 'START', 'to': 'A', 'min': 0, 'max': rng.randint(0, 4)})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1', '2')),
             'search_budget': rng.choice((40, 40, 2000))}
    return query, {'profile': PROFILE, 'steps': {}, 'exclude_kinds': list(KINDS)}


def tiny_request(rng, generator):
    """(query, 1.3 conditions) for a generator; valid for the store (refusals are tested elsewhere)."""
    while True:
        if generator == 'coded-plain':
            query, conditions = tiny_plain_request(rng)
        else:
            query, conditions = coded(*dc.tiny_request(rng, rng.choice(dc.FEATURES), rng.random() < 0.5))
        try:
            cc.Request(query, conditions)
        except es.StoreError:
            continue
        return query, conditions


def tiny_windows(rng, ingest):
    out = {}
    for p in ingest['patients']:
        start = rng.randint(0, 3)
        out[p['patient_id']] = None if rng.random() < 0.05 else {'start': start, 'end': start + rng.randint(2, 6)}
    return out


def kept_patient(p, window, kinds=KINDS):
    """The engine patient of a tiny ingest record's kept events, concepts through the closure: what the worlds check
    enumerates."""
    events = []
    for e in p['events']:
        if e['kind'] in kinds:
            continue
        concepts = sorted(set(e['concepts']) | {a for c in e['concepts'] for a in ANCESTORS[c]})
        out = {'id': e['event_id'], 'concepts': concepts, 'start': list(e['start'])}
        if 'end' in e:
            out['end'] = list(e['end'])
        out.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
        events.append(out)
    return {'patient_id': p['patient_id'], 'window': window, 'events': events}


def check_result(result, query, conditions, patient):
    """The worlds check of one store result against the patient's kept events. A binding naming an event that is not
    kept (nor a derived step's own point) is a violation in itself."""
    kept = {e['id'] for e in patient['events']}
    for m in (result, result.get('relaxed_match') or {}, result.get('unverified_match') or {}):
        foreign = sorted(eid for eid in (m.get('binding') or {}).values()
                         if eid not in kept and not eid.startswith(es._DERIVED_PREFIX))
        if foreign:
            return [f'binds events that are not kept: {foreign}']
    if not conditions['steps']:
        return tw.check(result, query, patient)
    written = {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': conditions['steps']}
    if conditions.get('relaxations'):
        written['relaxations'] = conditions['relaxations']
    return tw.check_derived(result, query, written, patient, frozenset())


def run_snapshot(root, seed, generator, patients=40, requests=25, defect=None, worlds=True, reference=True,
                 pruning=True):
    """One small snapshot of `patients` tiny patients and `requests` seeded 1.3 requests over it, each with its own
    windows: (violations, mismatches, status mix, cases). A case is one patient's result for one request. Each result is
    checked by the worlds check against the patient's kept events (`worlds`), compared with the reference
    (`reference`), and the whole answer with pruning off (`pruning`)."""
    rng = random.Random(f'coded/{generator}/{seed}')
    ingest = tiny_ingest(rng, f'{generator}-{seed}', patients)
    by_id = {p['patient_id']: p for p in ingest['patients']}
    violations, mismatches, mix, cases = [], [], Counter(), 0
    reference_of = Reference(ingest) if reference else None
    with es.open_store(es.build(ingest, root / f'{generator}-{seed}')) as store, patch.object(cc, '_D39_DEFECT', defect):
        for k in range(requests):
            query, conditions = tiny_request(rng, generator)
            windows = tiny_windows(rng, ingest)
            out = cc.match(store, query, windows, conditions=conditions)
            if pruning:
                with patch.object(es, '_PRUNE', False):
                    off = cc.match(store, query, windows, conditions=conditions)
                if json.dumps(off) != json.dumps(out):
                    mismatches.append((seed, k, 'pruning'))
            want = reference_of.match(query, windows, conditions) if reference else None
            for i, got in enumerate(out['results']):
                cases += 1
                mix[got['status']] += 1
                pid = got['patient_id']
                if worlds:
                    wrong = check_result(got, query, conditions, kept_patient(by_id[pid], windows[pid]))
                    if wrong:
                        violations.append((seed, k, pid, wrong))
                if reference and json.dumps(unfiltered(got)) != json.dumps(want[i]):
                    mismatches.append((seed, k, pid))
    return violations, mismatches, mix, cases


def run_small(root, generator, snapshots, first=0, **options):
    """`run_snapshot` over `snapshots` seeds from `first`: (violations, mismatches, status mix, cases)."""
    violations, mismatches, mix, cases = [], [], Counter(), 0
    for seed in range(first, first + snapshots):
        v, m, x, n = run_snapshot(root, seed, generator, **options)
        violations += v
        mismatches += m
        mix.update(x)
        cases += n
    return violations, mismatches, mix, cases
