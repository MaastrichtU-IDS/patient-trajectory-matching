"""Small seeded cases for the derived steps of the store's match request (D32): Nth occurrence and counts; and (D33)
rises and falls against a baseline of earlier readings.

Shared by `test_derived_steps.py` and the ad hoc soundness runs the decision log reports. A case is an engine patient
on a small integer grid (so `trajectory_graph_worlds` can enumerate every choice of its times), the ids of its events
whose kind is `mention`, and a 1.1 request (query and conditions) with at least one derived step of the case's
feature:
- `multi`: events of concept x, y or both (and z, carried by every event of x or y, as a wider concept), point events
  and exposures, some proper, some letter mentions, the other steps on x, y or z with any, first or last;
- `coupled`: the same, with steps chosen to bind the counted or ranked events (an `any` or ordered step on the derived
  step's concept, or a second derived step on an overlapping family) and shared variables in a third of the cases.

`store_result` runs the store's own per-patient path (`Request`, `derive_patient`, `match_derived`) and
`reference_result` the reference's (`execution_store_reference.derived_result`); neither needs a snapshot.

Relative conditions (D33, `relative_case`): readings (point events with a value and a unit, of item class x or y;
z covers both) and, per generator:
- `multi`: point readings of uncertain or exact time, a few in another unit, other steps on x, y, z or a drug d;
- `coupled`: as multi, with other steps binding the readings (an `any`, `first` or `last` step on their concept, a
  second derived step, an Nth or a count, over them) and shared variables among readings in a third of the cases;
- `period`: half the readings are period summaries (their time the start of their range), with excluded readings
  (an item class and a start range, no value) and readings in another unit.
"""
from __future__ import annotations

import random

from . import execution_store as es
from . import execution_store_reference as ref
from . import series_values as sv
from . import trajectory_graph as tg

FEATURES = ('nth', 'span', 'window')
CONCEPTS = ('x', 'y', 'z')


def tiny_patient(rng, coupled):
    events, mentions = [], set()
    for k in range(rng.randint(2, 5)):
        concepts = rng.choice((['x'], ['x'], ['y'], ['x', 'y']))
        concepts = sorted(set(concepts) | {'z'})
        lo = rng.randint(0, 6)
        hi = lo + rng.choice((0, 0, 1, 2, 3))
        e = {'id': f'e{k}', 'concepts': concepts, 'start': [lo, hi]}
        if rng.random() < 0.25:
            end_lo = lo + rng.randint(0, 2)
            e['end'] = [end_lo, end_lo + rng.randint(0, 2)]
            if rng.random() < 0.3 and e['end'][1] > lo:
                e['proper'] = True
        events.append(e)
        if rng.random() < 0.15:
            mentions.add(e['id'])
    if coupled and rng.random() < 0.33 and len(events) >= 2:
        a, b = rng.sample(events, 2)
        if 'end' in a:
            b['start_var'] = a['id'] + '.f'
            b['start'] = [max(b['start'][0], a['end'][0]), max(b['start'][1], a['end'][1])]
            b['start'][1] = max(b['start'])
            b['start'][0] = min(b['start'][0], b['start'][1])
        else:
            b['start_var'] = a['id'] + '.s'
            b['start'] = list(a['start'])
            if 'end' in b and b['end'][0] < b['start'][0]:
                b['end'] = [b['start'][0], max(b['start'][0], b['end'][1])]
                b.pop('proper', None)
    start = rng.randint(0, 3)
    window = {'start': start, 'end': start + rng.randint(2, 6)}
    return {'patient_id': 'P', 'window': window, 'events': events}, mentions


def tiny_request(rng, feature, coupled):
    """(query, conditions) with a derived step of `feature` and up to two other steps."""
    n_steps = rng.randint(1, 3) if not coupled else rng.randint(2, 3)
    derived_concept = rng.choice(('x', 'x', 'z'))
    steps, conds, relaxations, count_relax = [], {}, [], []
    for i in range(n_steps):
        sid = 'ABC'[i]
        if i == 0:
            concept = derived_concept
            occurrence = 'any'
            if feature == 'nth':
                conds[sid] = {'occurrence': {rng.choice(('nth', 'nth_last')): rng.randint(1, 3)}}
            else:
                within = 'window' if feature == 'window' else {'span': rng.randint(0, 4)}
                n = rng.randint(1, 3)
                conds[sid] = {'count': {'at_least': n, 'within': within}}
                if n > 1 and rng.random() < 0.5:
                    x = {'id': f'c{sid}', 'kind': 'widen_count', 'step': sid, 'cost': rng.choice(('0.5', '1')),
                         'at_least': rng.randint(1, n - 1)}
                    if rng.random() < 0.3:
                        x['concept'] = 'z'
                    count_relax.append(x)
        else:
            if coupled and rng.random() < 0.6:
                concept = rng.choice((derived_concept, 'x', 'z'))
            else:
                concept = rng.choice(CONCEPTS)
            occurrence = rng.choice(('any', 'any', 'first', 'last'))
            if coupled and i == 1 and rng.random() < 0.3:
                occurrence = 'any'
                kind = rng.choice(FEATURES)
                if kind == 'nth':
                    conds[sid] = {'occurrence': {rng.choice(('nth', 'nth_last')): rng.randint(1, 2)}}
                else:
                    conds[sid] = {'count': {'at_least': rng.randint(1, 2),
                                            'within': 'window' if kind == 'window' else {'span': rng.randint(0, 3)}}}
        steps.append({'id': sid, 'concept': concept, 'occurrence': occurrence})
        if rng.random() < 0.25:
            relaxations.append({'id': f'w{sid}', 'kind': 'widen_concept', 'step': sid, 'concept': 'z',
                                'cost': rng.choice(('0.5', '1'))})
    relations = []
    for i in range(1, n_steps):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        r = {'id': f'R{i}', 'from': a, 'to': b}
        if rng.random() < 0.15 and 'occurrence' not in conds.get(a, {}):
            relations.append({**r, 'kind': 'during', 'grace': rng.choice((0, 1))})
            continue
        lo = rng.randint(-3, 2)
        r.update(min=lo, max=lo + rng.randint(0, 4))
        relations.append({**r, 'kind': 'after'})
        if rng.random() < 0.3:
            relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                'max': r['max'] + rng.randint(1, 3), 'cost': rng.choice(('0.5', '1'))})
    if rng.random() < 0.25:
        relations.append({'id': 'RS', 'kind': 'after', 'from': 'START', 'to': steps[0]['id'], 'min': 0,
                          'max': rng.randint(0, 4)})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1', '2')),
             'search_budget': rng.choice((40, 40, 2000))}
    conditions = {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': conds}
    if count_relax:
        conditions['relaxations'] = count_relax
    return query, conditions


def tiny_case(seed, feature, generator):
    """(query, conditions, patient, mentions) for one seed; the request is valid (refusals are tested elsewhere)."""
    rng = random.Random(f'{feature}/{generator}/{seed}')
    coupled = generator == 'coupled'
    while True:
        patient, mentions = tiny_patient(rng, coupled)
        try:
            tg.patient_times(patient)
        except tg.TrajectoryError:
            continue
        query, conditions = tiny_request(rng, feature, coupled)
        try:
            es.Request(query, conditions)
        except es.StoreError:
            continue
        return query, conditions, patient, mentions


def store_result(query, conditions, patient, mentions):
    request = es.Request(query, conditions)
    pq = tg.PreparedQuery(request.query)
    derived = es.derive_patient(request, patient['patient_id'], patient['window'], patient['events'], mentions)
    return es.match_derived(pq, request, derived)


_EMPTY = {'closure': {'ancestors': {}}, 'patients': []}


def reference_result(query, conditions, patient, mentions):
    dq = ref.DerivedQuery(_EMPTY, query, conditions)
    return ref.derived_result(dq, patient['patient_id'], patient['window'], patient['events'], mentions)[0]


# D33: relative conditions on small grids.

RELATIVE_GENERATORS = ('multi', 'coupled', 'period')
COVERS = {'x': {'x', 'z'}, 'y': {'y', 'z'}}  # the item classes' closure: z covers x and y
VALUES = ('0', '0.5', '1', '1', '2', '2', '3', '4', '4', '6', '8')


def tiny_readings_patient(rng, generator):
    """(patient, readings): readings of item class x or y, other events of a drug d, excluded readings."""
    events, rows, excluded = [], {}, []
    for k in range(rng.randint(2, 6)):
        item = rng.choice(('x', 'x', 'y'))
        concepts = {item, 'z'}
        if item == 'x' and rng.random() < 0.15:
            concepts.add('y')  # an event of x that also carries y (its reading is of x)
        period = generator == 'period' and rng.random() < 0.5
        lo = rng.randint(0, 6)
        width = rng.choice((1, 2, 3)) if period else rng.choice((0, 0, 0, 1, 2))
        e = {'id': f'r{k}', 'concepts': sorted(concepts), 'start': [lo, lo + width]}
        events.append(e)
        unit = 'v' if rng.random() < (0.15 if generator != 'multi' else 0.08) else 'u'
        rows[e['id']] = {'item_class': item, 'unit': unit, 'value': rng.choice(VALUES),
                         'kind': 'period' if period else 'measurement', 'start': (lo, lo + width)}
    for k in range(rng.randint(0, 2)):
        lo = rng.randint(0, 7)
        e = {'id': f'd{k}', 'concepts': ['d'], 'start': [lo, lo + rng.choice((0, 0, 1, 2))]}
        if rng.random() < 0.3:
            end_lo = e['start'][1] + rng.randint(0, 2)
            e['end'] = [end_lo, end_lo + rng.randint(0, 2)]
        events.append(e)
    if rng.random() < (0.4 if generator == 'period' else 0.2):
        for _ in range(rng.randint(1, 2)):
            lo = rng.randint(0, 7)
            excluded.append({'item_class': rng.choice(('x', 'y')), 'start': (lo, lo + rng.choice((0, 0, 1, 2)))})
    if generator == 'coupled' and rng.random() < 0.33:
        points = [e for e in events if 'end' not in e]
        a, b = rng.sample(points, 2)
        b['start_var'] = a.get('start_var', a['id'] + '.s')
        b['start'] = list(a['start'])
        if b['id'] in rows:
            rows[b['id']]['start'] = tuple(a['start'])
    start = rng.randint(0, 3)
    patient = {'patient_id': 'P', 'window': {'start': start, 'end': start + rng.randint(2, 7)}, 'events': events}
    return patient, {'rows': rows, 'excluded': excluded, 'period': {'period'}}


def tiny_relative_request(rng, generator):
    """(query, conditions): a relative step on x, y or z, a `widen_factor` or `widen_concept` sometimes, and up to two
    other steps (in the coupled generator, often on the readings, or a second derived step over them)."""
    concept = rng.choice(('x', 'x', 'y', 'z'))
    factor = rng.choice(('1.5', '2', '3'))
    rel = {'kind': rng.choice(('rise', 'rise', 'fall')), 'factor': factor, 'unit': 'u',
           'floor': rng.choice(('1', '0.5', '2', '0.25')),
           'baseline': {'previous': rng.randint(1, 3), 'within': rng.randint(1, 4)}}
    steps, conds, relaxations, factors = [{'id': 'A', 'concept': concept}], {'A': {'relative': rel}}, [], []
    if rng.random() < 0.4:
        smaller = [f for f in ('1.25', '1.5', '2') if float(f) < float(factor)]
        factors.append({'id': 'fA', 'kind': 'widen_factor', 'step': 'A', 'cost': rng.choice(('0.5', '1')),
                        'factor': rng.choice(smaller)})
    if concept != 'z' and rng.random() < 0.2:
        relaxations.append({'id': 'wA', 'kind': 'widen_concept', 'step': 'A', 'concept': 'z',
                            'cost': rng.choice(('0.5', '1'))})
    n_steps = rng.randint(1, 3) if generator != 'coupled' else rng.randint(2, 3)
    for i in range(1, n_steps):
        sid = 'BC'[i - 1]
        if generator == 'coupled' and rng.random() < 0.6:
            other = rng.choice((concept, 'x', 'z'))
        else:
            other = rng.choice(('d', 'd', 'x', 'y', 'z'))
        occurrence = rng.choice(('any', 'any', 'first', 'last'))
        if generator == 'coupled' and i == 1 and rng.random() < 0.3 and other != 'd':
            occurrence = 'any'
            conds[sid] = rng.choice(({'occurrence': {'nth': rng.randint(1, 2)}},
                                     {'count': {'at_least': rng.randint(1, 2), 'within': {'span': rng.randint(0, 3)}}},
                                     {'relative': dict(rel, kind=rng.choice(('rise', 'fall')))}))
        steps.append({'id': sid, 'concept': other, 'occurrence': occurrence})
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        r = {'id': f'R{i}', 'from': a, 'to': b}
        if rng.random() < 0.15 and 'occurrence' not in conds.get(a, {}):
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
    conditions = {'profile': es.CONDITIONS_PROFILE_1_2, 'steps': conds}
    if factors:
        conditions['relaxations'] = factors
    return query, conditions


def relative_case(seed, generator):
    """(query, conditions, patient, mentions, readings) for one seed; the request is valid."""
    rng = random.Random(f'relative/{generator}/{seed}')
    while True:
        patient, readings = tiny_readings_patient(rng, generator)
        try:
            tg.patient_times(patient)
        except tg.TrajectoryError:
            continue
        query, conditions = tiny_relative_request(rng, generator)
        try:
            es.Request(query, conditions)
        except es.StoreError:
            continue
        return query, conditions, patient, frozenset(), readings


def store_readings(readings):
    """The readings as the store loads them (`execution_store._readings`)."""
    rows = {eid: (r['item_class'], r['unit'], sv.columns(r['value'])[0], r['kind'], tuple(r['start']))
            for eid, r in readings['rows'].items()}
    excluded = {}
    for x in readings['excluded']:
        excluded.setdefault(x['item_class'], []).append(tuple(x['start']))
    covered = {}
    for r in readings['rows'].values():
        for concept in COVERS[r['item_class']]:
            covered.setdefault(concept, set()).add(r['item_class'])
    return {'rows': rows, 'excluded': excluded, 'period': frozenset(readings['period']), 'covered': covered}


def checker_readings(readings):
    """The readings for the reference and the worlds checker: a test of which item classes a concept covers."""
    return {**readings, 'covers': lambda item, concept: concept in COVERS[item]}


def relative_store_result(query, conditions, patient, mentions, readings):
    request = es.Request(query, conditions)
    pq = tg.PreparedQuery(request.query)
    derived = es.derive_patient(request, patient['patient_id'], patient['window'], patient['events'], mentions,
                                store_readings(readings))
    return es.match_derived(pq, request, derived)


def relative_reference_result(query, conditions, patient, mentions, readings):
    dq = ref.DerivedQuery(_EMPTY, query, conditions)
    return ref.derived_result(dq, patient['patient_id'], patient['window'], patient['events'], mentions,
                              checker_readings(readings))[0]
