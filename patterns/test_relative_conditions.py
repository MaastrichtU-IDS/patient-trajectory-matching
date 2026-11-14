"""Relative conditions in the execution store's match request (D33): "rises to at least x f" and "falls to at most / f"
against the mean of up to k earlier readings of the same item class within W, with a positive floor; period readings
(the diary's monthly counts) timed by their period's start.

- Small cases (`derived_cases.relative_case`): the store's per-patient path against the true semantics by enumerating
  every choice of times (`trajectory_graph_worlds.check_derived`) and against the reference, on the multi-concept,
  coupled and period-reading generators; strengths against enumeration; planted defects.
- Snapshots: the store against the reference, result for result, on fixtures made for it (period and point readings,
  readings in another unit, excluded readings, shared variables, a grouping over two item classes) and on the synthetic
  cohort built with series, at 1 and 2 workers, both detail levels, pruning on and off; factor relaxations hold every
  stricter event; refusals; requests without a relative condition unchanged.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
import copy
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import derived_cases as dc
from . import execution_store as es
from . import execution_store_reference as ref
from . import prepared_reference as pr
from . import series_values as sv
from . import synthetic_ingest as si
from . import trajectory_graph as tg
from . import trajectory_graph_worlds as tw

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400_000_000
T0 = 63_712_000_000_000_000
L12 = es.CONDITIONS_PROFILE_1_2
SMALL = 1000  # small cases per generator in the suite (the decision log reports the ad hoc runs)
DEFECTS = ('window_off_by_one', 'k_plus_one', 'floor_ignored', 'unit_mixing', 'excluded_ignored', 'determined_only')


def run_small(generator, n, defect=None, reference=True, first=0):
    """(violations, mismatches, status mix) over n small cases."""
    bad, mismatched, mix = [], [], Counter()
    with patch.object(es, '_D33_DEFECT', defect):
        for seed in range(first, first + n):
            q, c, p, m, r = dc.relative_case(seed, generator)
            got = dc.relative_store_result(q, c, p, m, r)
            mix[got['status']] += 1
            wrong = tw.check_derived(got, q, c, p, m, readings=dc.checker_readings(r))
            if wrong:
                bad.append((seed, wrong))
            if reference and json.dumps(got) != json.dumps(dc.relative_reference_result(q, c, p, m, r)):
                mismatched.append(seed)
    return bad, mismatched, mix


def rel(kind='rise', factor='2', unit='u', floor='1', previous=2, within=3):
    return {'relative': {'kind': kind, 'factor': factor, 'unit': unit, 'floor': floor,
                         'baseline': {'previous': previous, 'within': within}}}


def one_step(concept='x', **extra):
    return {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': concept, **extra}], 'relations': [],
            'relaxations': [], 'budget': '1'}


class SmallCaseTests(unittest.TestCase):
    """The store's relative conditions are sound on small grids and agree with the reference."""

    def test_no_claim_is_false_and_the_reference_agrees(self):
        for generator in dc.RELATIVE_GENERATORS:
            bad, mismatched, mix = run_small(generator, SMALL)
            self.assertEqual(bad[:3], [], generator)
            self.assertEqual(mismatched[:3], [], generator)
            self.assertTrue({'exact', 'possible', 'relaxed', 'none', 'unresolved'} <= set(mix), (generator, mix))

    def test_strengths_equal_enumeration(self):
        exact_cases = inexact = 0
        for generator in dc.RELATIVE_GENERATORS:
            for seed in range(300):
                q, c, p, m, r = dc.relative_case(seed, generator)
                request = es.Request(q, c)
                stored = dc.store_readings(r)
                times = tg.patient_times(p, trusted=True)
                by_class, linked = es._patient_readings(stored, times)
                for name, spec in request.derived_used.items():
                    if spec['kind'] != 'relative':
                        continue
                    concept, (unit, _, _) = request.tags[spec['concept']]
                    cands = [x for item in sorted(stored['covered'].get(concept, ())) for x in by_class.get(item, ())
                             if x.eid is not None and x.unit == unit]
                    got, exact = es._relative_strengths(spec['relative'], cands, by_class, linked)
                    raw = {'kind': spec['relative']['kind'], 'factor': spec['relative']['factor'], 'unit': unit,
                           'floor': spec['relative']['floor'],
                           'baseline': {'previous': spec['relative']['previous'], 'within': spec['relative']['within']}}
                    truth = tw.relative_strengths(p, concept, raw, dc.checker_readings(r))
                    if exact:
                        exact_cases += 1
                        self.assertEqual(got, truth, (generator, seed, name))
                    else:  # certain is sound, possible covers every reading the condition may hold at
                        inexact += 1
                        self.assertTrue({k for k, v in got.items() if v == 'certain'}
                                        <= {k for k, v in truth.items() if v == 'certain'}, (generator, seed))
                        self.assertTrue(set(truth) <= set(got), (generator, seed))
        self.assertGreater(exact_cases, 800)
        self.assertGreater(inexact, 10)

    def test_period_readings_are_timed_by_their_start(self):
        """Three monthly counts, W = 31: by period starts the second lies 30 before the third, so the third rises against
        it, certainly; read as point readings over their recorded ranges (a month wide each), the second may lie more
        than 31 before the third, which then has no baseline, so the rise is only possible."""
        month = 30
        events = [{'id': f'm{k}', 'concepts': ['x', 'z'], 'start': [k * month, k * month + month - 1]} for k in range(3)]
        readings = {'rows': {'m0': {'item_class': 'x', 'unit': 'u', 'value': '2', 'kind': 'period', 'start': (0, 29)},
                             'm1': {'item_class': 'x', 'unit': 'u', 'value': '2', 'kind': 'period', 'start': (30, 59)},
                             'm2': {'item_class': 'x', 'unit': 'u', 'value': '5', 'kind': 'period', 'start': (60, 89)}},
                    'excluded': [], 'period': {'period'}}
        q, c = one_step(), {'profile': L12, 'steps': {'A': rel(factor='2', within=31)}}
        p = {'patient_id': 'P', 'window': {'start': 0, 'end': 100}, 'events': events}
        request = es.Request(q, c)
        d = es.derive_patient(request, 'P', p['window'], events, frozenset(), dc.store_readings(readings))
        self.assertEqual([e['id'] for e in d.first['events'] if 'derived-step:A/own' in e['concepts']], ['m2'])
        self.assertIsNone(d.second)
        self.assertEqual(es.match_derived(tg.PreparedQuery(request.query), request, d)['status'], 'exact')
        points = {**readings, 'period': set()}
        d = es.derive_patient(request, 'P', p['window'], events, frozenset(), dc.store_readings(points))
        self.assertNotIn('derived-step:A/own', [c for e in d.first['events'] for c in e['concepts']])
        self.assertEqual([e['id'] for e in d.second['events'] if 'derived-step:A/own' in e['concepts']], ['m2'])

    def test_another_unit_or_an_excluded_reading_leaves_the_baseline_undetermined(self):
        events = [{'id': 'a', 'concepts': ['x'], 'start': [0, 0]}, {'id': 'b', 'concepts': ['x'], 'start': [2, 2]}]
        base = {'rows': {'a': {'item_class': 'x', 'unit': 'u', 'value': '1', 'kind': 'm', 'start': (0, 0)},
                         'b': {'item_class': 'x', 'unit': 'u', 'value': '9', 'kind': 'm', 'start': (2, 2)}},
                'excluded': [], 'period': set()}
        q, c = one_step(), {'profile': L12, 'steps': {'A': rel(previous=1, within=3)}}
        request = es.Request(q, c)

        def strength(readings):
            d = es.derive_patient(request, 'P', {'start': 0, 'end': 5}, events, frozenset(), dc.store_readings(readings))
            first = {e['id'] for e in d.first['events'] if 'derived-step:A/own' in e['concepts']}
            second = {e['id'] for e in (d.second or d.first)['events'] if 'derived-step:A/own' in e['concepts']}
            return 'certain' if 'b' in first else 'possible' if 'b' in second else None
        self.assertEqual(strength(base), 'certain')
        other = copy.deepcopy(base)
        other['rows']['a']['unit'] = 'v'
        self.assertEqual(strength(other), 'possible')  # the previous reading is in another unit
        dropped = {**base, 'excluded': [{'item_class': 'x', 'start': (1, 1)}]}
        self.assertEqual(strength(dropped), 'possible')  # an excluded reading is the previous one
        before = {**base, 'excluded': [{'item_class': 'x', 'start': (-5, -5)}]}
        self.assertEqual(strength(before), 'certain')  # outside W: no effect

    def test_the_floor_and_exact_arithmetic(self):
        events = [{'id': 'a', 'concepts': ['x'], 'start': [0, 0]}, {'id': 'b', 'concepts': ['x'], 'start': [1, 1]},
                  {'id': 'c', 'concepts': ['x'], 'start': [2, 2]}]

        def tagged(values, **condition):
            readings = {'rows': {eid: {'item_class': 'x', 'unit': 'u', 'value': v, 'kind': 'm', 'start': (k, k)}
                                 for k, (eid, v) in enumerate(zip('abc', values))}, 'excluded': [], 'period': set()}
            request = es.Request(one_step(), {'profile': L12, 'steps': {'A': rel(**condition)}})
            d = es.derive_patient(request, 'P', {'start': 0, 'end': 5}, events, frozenset(), dc.store_readings(readings))
            return [e['id'] for e in d.first['events'] if 'derived-step:A/own' in e['concepts']]
        # baseline (0.1 + 0.2) / 2 = 0.15 is under the floor 1, so a rise to 2 needs 2 x 1
        self.assertEqual(tagged(('0.1', '0.2', '2'), factor='2', floor='1'), ['c'])
        self.assertEqual(tagged(('0.1', '0.2', '1.999999'), factor='2', floor='1'), [])
        # without the floor's help: (1 + 2) / 2 = 1.5, x 3 = 4.5 exactly
        self.assertEqual(tagged(('1', '2', '4.5'), factor='3', floor='0.5'), ['c'])
        self.assertEqual(tagged(('1', '2', '4.4999999999999999999999999999999'), factor='3', floor='0.5'), [])
        # a fall to at most max(floor, baseline) / f: (3 + 3) / 2 / 3 = 1
        self.assertEqual(tagged(('3', '3', '1'), kind='fall', factor='3', floor='0.000000001'), ['c'])
        self.assertEqual(tagged(('3', '3', '1.0000001'), kind='fall', factor='3', floor='0.000000001'), [])


def relaxed_wider_case(seed, generator):
    """A small case whose `widen_factor` names a larger factor (refused by the store; allowed by the planted defect)."""
    q, c, p, m, r = dc.relative_case(seed, generator)
    c = json.loads(json.dumps(c))
    factor = c['steps']['A']['relative']['factor']
    c['relaxations'] = [{'id': 'fA', 'kind': 'widen_factor', 'step': 'A', 'cost': '0.5',
                         'factor': str(float(factor) + 1)}]
    return q, c, p, m, r


def containment_failures(n, defect=None, cases=dc.relative_case):
    """(relaxations checked, those whose tag does not hold every event of the stricter tag at both strengths)."""
    checked = failed = 0
    with patch.object(es, '_D33_DEFECT', defect):
        for generator in dc.RELATIVE_GENERATORS:
            for seed in range(n):
                q, c, p, m, r = cases(seed, generator)
                if not c.get('relaxations'):
                    continue
                request = es.Request(q, c)
                wide = next((x['concept'] for x in request.query['relaxations'] if x['id'] == 'fA'), None)
                if wide not in request.derived_used:  # beyond the budget: never tried, never derived
                    continue
                strict = request.query['steps'][0]['concept']
                d = es.derive_patient(request, 'P', p['window'], p['events'], m, dc.store_readings(r))
                checked += 1
                for doc in (d.first, d.second or d.first):
                    have = lambda name: {e['id'] for e in doc['events'] if name in e['concepts']}
                    failed += not have(strict) <= have(wide)
    return checked, failed


def strength_mismatches(n, defect=None):
    """Readings whose tag strength differs from enumeration (`trajectory_graph_worlds.relative_strengths`) where the
    store calls its strengths exact, over n small cases per generator."""
    wrong = 0
    with patch.object(es, '_D33_DEFECT', defect):
        for generator in dc.RELATIVE_GENERATORS:
            for seed in range(n):
                q, c, p, m, r = dc.relative_case(seed, generator)
                request = es.Request(q, c)
                stored = dc.store_readings(r)
                by_class, linked = es._patient_readings(stored, tg.patient_times(p, trusted=True))
                for spec in request.derived_used.values():
                    if spec['kind'] != 'relative':
                        continue
                    concept, (unit, _, _) = request.tags[spec['concept']]
                    cands = [x for item in sorted(stored['covered'].get(concept, ())) for x in by_class.get(item, ())
                             if x.eid is not None and x.unit == unit]
                    got, exact = es._relative_strengths(spec['relative'], cands, by_class, linked)
                    rel_ = spec['relative']
                    raw = {'kind': rel_['kind'], 'factor': rel_['factor'], 'unit': unit, 'floor': rel_['floor'],
                           'baseline': {'previous': rel_['previous'], 'within': rel_['within']}}
                    if exact:
                        truth = tw.relative_strengths(p, concept, raw, dc.checker_readings(r))
                        wrong += sum(1 for k in set(got) | set(truth) if got.get(k) != truth.get(k))
    return wrong


class PlantedDefectTests(unittest.TestCase):
    """Each planted defect of the D33 rules makes the worlds check (or the containment check) fail."""

    def test_each_defect_is_caught_by_the_worlds_check(self):
        for defect in DEFECTS:
            if defect == 'determined_only':
                continue
            found = sum(len(run_small(g, 2500, defect, reference=False)[0]) for g in dc.RELATIVE_GENERATORS)
            self.assertGreater(found, 0, defect)

    def test_certainty_only_on_a_determined_configuration_is_caught(self):
        """The plan's rule (certain only when the baseline is determined) under-claims certain: the enumeration of the
        tags catches it on many readings, and the results' claims on a few (a pass-1 `possible` that asserts no binding
        is exact, where one is)."""
        self.assertEqual(strength_mismatches(300), 0)
        self.assertGreater(strength_mismatches(300, 'determined_only'), 20)
        found = sum(len(run_small(g, 4000, 'determined_only', reference=False)[0]) for g in ('multi', 'coupled'))
        self.assertGreater(found, 0)

    def test_a_relaxation_that_does_not_contain_the_stricter_condition_is_caught(self):
        checked, failed = containment_failures(600)
        self.assertGreater(checked, 100)
        self.assertEqual(failed, 0)
        with self.assertRaises(es.StoreError) as raised:
            es.Request(*relaxed_wider_case(0, 'multi')[:2])
        self.assertEqual(raised.exception.code, 'FACTOR_RELAXATION_MUST_WIDEN')
        checked, failed = containment_failures(600, 'relaxation_not_containing', relaxed_wider_case)
        self.assertGreater(failed, 0)


# Snapshots: fixtures made for relative conditions, and the synthetic cohort built with series.

ANCESTORS = {'c:seizure': [], 'c:febrile': ['c:seizure'], 'd:drug': [], 'd:a': ['d:drug'], 'm:measure': [],
             'm:freq': ['m:measure'], 'm:weight': ['m:measure'], 'x:other': []}


def series_row(eid, item, text, unit):
    decimal, micro = sv.columns(text)
    return {'event_id': eid, 'item_class': item, 'value_lexical': text, 'value_decimal': decimal, 'value_micro': micro,
            'unit': unit}


def relative_ingest(seed, patients=40, shared=0.2, excluded=True, period=True):
    """Patients with monthly frequency counts (period readings, `/month`, a month wide each, some months missing), weights
    (point readings, exact or a day wide, a few in `lb`, a few with values beyond micro-units), excluded readings of both,
    shared variables between weights, seizures and drug exposures, and partial coverage."""
    rng = random.Random(seed)
    out = []
    for k in range(patients):
        pid = f'R{seed}-{k:03d}'
        events, series, dropped = [], [], []
        birth = T0 + rng.randint(0, 30) * DAY
        month = birth + rng.randint(0, 60) * DAY
        for j in range(rng.randint(0, 10)):
            month += rng.choice((30, 31, 31, 61)) * DAY
            eid = f'f{j}'
            events.append({'event_id': eid, 'kind': 'diary_summary', 'source': 'diary', 'concepts': ['m:freq'],
                           'start': [month, month + 29 * DAY], 'evidence': {}})
            series.append(series_row(eid, 'm:freq', rng.choice(('0', '1', '2', '3', '4', '6', '8', '12', '2.5')),
                                     '/month'))
        for j in range(rng.randint(0, 6)):
            lo = birth + rng.randint(0, 400) * DAY
            eid = f'w{j}'
            events.append({'event_id': eid, 'kind': 'measurement', 'source': 'ehr', 'concepts': ['m:weight'],
                           'start': [lo, lo + rng.choice((0, 0, 0, DAY - 1))], 'evidence': {}})
            text = rng.choice(('3.5', '4', '5', '7.25', '8', '10', '16', '4.0000001234'))
            series.append(series_row(eid, 'm:weight', text, 'lb' if rng.random() < 0.1 else 'kg'))
        for j in range(rng.randint(0, 6)):
            lo = birth + rng.randint(0, 400) * DAY
            events.append({'event_id': f's{j}', 'kind': 'condition', 'source': 'ehr', 'concepts': ['c:febrile'],
                           'start': [lo, lo + rng.choice((0, DAY - 1, 30 * DAY))], 'evidence': {}})
        for j in range(rng.randint(0, 2)):
            lo = birth + rng.randint(0, 400) * DAY
            e = {'event_id': f'x{j}', 'kind': 'medication_exposure', 'source': 'ehr', 'concepts': ['d:a'],
                 'start': [lo, lo], 'end': [lo + 10 * DAY, lo + 90 * DAY], 'evidence': {}}
            events.append(e)
        if rng.random() < shared:
            weights = [e for e in events if e['event_id'].startswith('w')]
            if len(weights) >= 2:
                a, b = rng.sample(weights, 2)
                b['start'] = list(a['start'])
                b['start_var'] = a['event_id'] + '.s'
        for _ in range(rng.choice((0, 0, 0, 1, 2)) if excluded else 0):
            lo = birth + rng.randint(0, 430) * DAY
            dropped.append({'source': rng.choice(('ehr', 'diary')), 'concept': rng.choice(('m:freq', 'm:weight')),
                            'start': [lo, lo + rng.choice((0, DAY))], 'reason': 'MEASUREMENT_WITHOUT_UNIT'})
        coverage = [{'source': s, 'lo': birth, 'hi': birth + 460 * DAY, 'basis': 'declared'}
                    for s in ('ehr', 'diary') if rng.random() < 0.9]
        patient = {'patient_id': pid, 'clock_id': f'patient_{pid}', 'events': sorted(events, key=lambda e: e['event_id']),
                   'coverage': coverage}
        if series:
            patient['series'] = sorted(series, key=lambda r: r['event_id'])
        if dropped:
            patient['excluded'] = dropped
        out.append(patient)
    provenance = {'labels': {es.PERIOD_LABEL: 'diary_summary'}} if period else {}
    return {'profile': es.INGEST_PROFILE, 'dataset_id': f'relative-fixture-{seed}',
            'closure': {'version': 'relative-fixture-1', 'ancestors': ANCESTORS}, 'provenance': provenance,
            'patients': out}


def windows_for(rng, ingest):
    out = {}
    for p in ingest['patients']:
        if rng.random() < 0.05:
            out[p['patient_id']] = None
            continue
        first = min([e['start'][0] for e in p['events']] or [T0])
        start = first + rng.randint(-30, 200) * DAY
        out[p['patient_id']] = {'start': start, 'end': start + rng.randint(60, 500) * DAY}
    return out


def relative_request(rng, ingest, pool):
    """(query, conditions, windows): a relative step on a measured concept, sometimes a factor or concept relaxation,
    and up to two other steps (one of them sometimes on the readings, or a count over them)."""
    concept = rng.choice(pool['measured'])
    unit = rng.choice(pool['units'][concept])
    within = rng.choice((31, 62, 75, 120)) * DAY if 'freq' in concept or 'frequency' in concept else rng.choice(
        (1, 30, 90, 200)) * DAY
    factor = rng.choice(('1.5', '2', '3'))
    entry = {'relative': {'kind': rng.choice(('rise', 'rise', 'fall')), 'factor': factor, 'unit': unit,
                          'floor': rng.choice(('1', '0.5', '0.000000001')),
                          'baseline': {'previous': rng.randint(1, 3), 'within': within}}}
    steps, conds, relaxations, factors = [{'id': 'A', 'concept': concept}], {'A': entry}, [], []
    if rng.random() < 0.4:
        smaller = [f for f in ('1.1', '1.25', '1.5', '2') if float(f) < float(factor)]
        factors.append({'id': 'fA', 'kind': 'widen_factor', 'step': 'A', 'cost': rng.choice(('0.5', '1')),
                        'factor': rng.choice(smaller)})
    if pool['parent'].get(concept) and rng.random() < 0.2:
        relaxations.append({'id': 'wA', 'kind': 'widen_concept', 'step': 'A', 'concept': pool['parent'][concept],
                            'cost': rng.choice(('0.5', '1'))})
    for i in range(rng.randint(0, 2)):
        sid = 'BC'[i]
        other = rng.choice(pool['other'] + pool['measured'])
        step = {'id': sid, 'concept': other, 'occurrence': rng.choice(('any', 'any', 'first', 'last'))}
        steps.append(step)
        if other in pool['measured'] and step['occurrence'] == 'any' and rng.random() < 0.3:
            conds[sid] = {'count': {'at_least': 2, 'within': {'span': 90 * DAY}}}
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        r = {'id': f'R{i}', 'from': a, 'to': b}
        if rng.random() < 0.15:
            relations.append({**r, 'kind': 'during', 'grace': rng.choice((0, 30)) * DAY})
            continue
        lo = rng.choice((-60, 0, 0, 30)) * DAY
        r.update(min=lo, max=lo + rng.choice((60, 180, 365)) * DAY)
        relations.append({**r, 'kind': 'after'})
        if rng.random() < 0.3:
            relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                'max': r['max'] + 90 * DAY, 'cost': rng.choice(('0.25', '0.5'))})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1')), 'search_budget': rng.choice((300, 40000))}
    conditions = {'profile': L12, 'steps': conds}
    if factors:
        conditions['relaxations'] = factors
    return query, conditions, windows_for(rng, ingest)


FIXTURE_POOL = {'measured': ['m:freq', 'm:weight', 'm:measure'], 'other': ['d:a', 'c:febrile', 'c:seizure'],
                'parent': {'m:freq': 'm:measure', 'm:weight': 'm:measure'},
                'units': {'m:freq': ['/month'], 'm:weight': ['kg', 'kg', 'lb'], 'm:measure': ['/month', 'kg']}}
COHORT_POOL = {'measured': ['seizure_frequency', 'weight', 'height', 'head_circ'],
               'other': ['sodium_channel_blocker', 'febrile_seizure', 'seizure', 'valproate', 'stiripentol'],
               'parent': {}, 'units': {'seizure_frequency': ['/month'], 'weight': ['kg'], 'height': ['cm'],
                                       'head_circ': ['cm']}}

_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='relative-conditions-')
    tmp = Path(_STATE['tmp'].name)
    for name, seed, options in (('fixture', 41, {}), ('shared', 42, {'shared': 0.7}), ('points', 43, {'period': False})):
        ingest = relative_ingest(seed, **options)
        store = es.open_store(es.build(ingest, tmp / name), verify='content')
        _STATE[name] = (ingest, store, pr.PreparedReference(ingest), FIXTURE_POOL)
    subprocess.run([sys.executable, str(ROOT / 'demos/mockups/synthetic/generate.py'), '--output', str(tmp / 'cohort.json')],
                   check=True, capture_output=True, cwd=ROOT)
    cohort = json.loads((tmp / 'cohort.json').read_text(encoding='utf-8'))
    ingest = si.synthetic_ingest(cohort, series=True)
    store = es.open_store(es.build(ingest, tmp / 'cohort'), verify='content')
    _STATE['cohort'] = (ingest, store, pr.PreparedReference(ingest), COHORT_POOL)
    plain = si.synthetic_ingest(cohort)
    _STATE['plain'] = (plain, es.open_store(es.build(plain, tmp / 'plain'), verify='content'))


def tearDownModule():
    for name in ('fixture', 'shared', 'points', 'cohort', 'plain'):
        if name in _STATE:
            _STATE[name][1].close()
    es.close_pools()
    _STATE['tmp'].cleanup()


def case(seed, cohort):
    ingest, _, _, pool = _STATE[cohort]
    rng = random.Random(f'{cohort}/relative/{seed}')
    for _ in range(50):
        query, conditions, windows = relative_request(rng, ingest, pool)
        try:
            es.Request(query, conditions)
        except es.StoreError:
            continue
        return query, conditions, windows
    raise AssertionError(seed)


def unfiltered(result):
    if result.get('reason') == 'FILTERED':
        return {k: v for k, v in result.items() if k != 'reason'}
    return result


def compare(cohort, seeds, **options):
    ingest, store, prepared, _ = _STATE[cohort]
    wrong, mix = [], Counter()
    for seed in seeds:
        query, conditions, windows = case(seed, cohort)
        got = es.match(store, query, windows, conditions=conditions, **options)['results']
        want = prepared.match(query, windows, conditions)
        if options.get('detail') == 'decided':
            listed = {r['patient_id'] for r in got}
            want = [w for w in want if w['patient_id'] in listed]
        for g, w in zip(got, want, strict=True):
            if json.dumps(unfiltered(g)) != json.dumps(w):
                wrong.append((seed, g, w))
        mix.update(g['status'] for g in got)
    return wrong, mix


DIFFERENTIAL = {'fixture': 60, 'shared': 40, 'points': 30, 'cohort': 50}  # relative requests per cohort (180)


class RelativeDifferentialTests(unittest.TestCase):
    """The store against the reference, result for result, on snapshots."""

    def test_the_store_equals_the_reference(self):
        for cohort, n in DIFFERENTIAL.items():
            wrong, mix = compare(cohort, range(n))
            self.assertEqual(wrong[:2], [], cohort)
            self.assertTrue({'exact', 'none'} <= set(mix), (cohort, mix))

    def test_the_exhaustive_reference_agrees_too(self):
        ingest, store, _, _ = _STATE['fixture']
        for seed in range(12):
            query, conditions, windows = case(seed, 'fixture')
            got = es.match(store, query, windows, conditions=conditions)['results']
            want = ref.exhaustive_match(ingest, query, windows, conditions)
            for g, w in zip(got, want, strict=True):
                self.assertEqual(json.dumps(unfiltered(g)), json.dumps(w), seed)

    def test_workers_detail_and_pruning_change_nothing(self):
        for cohort in ('fixture', 'cohort'):
            _, store, _, _ = _STATE[cohort]
            for seed in range(0, 30, 3):
                query, conditions, windows = case(seed, cohort)
                base = es.match(store, query, windows, conditions=conditions)
                two = es.match(store, query, windows, conditions=conditions, workers=2, chunk=7)
                self.assertEqual(json.dumps(base), json.dumps(two), (cohort, seed))
                decided = es.match(store, query, windows, conditions=conditions, detail='decided')
                listed = {r['patient_id']: r for r in decided['results']}
                for r in base['results']:
                    if r['patient_id'] in listed:
                        self.assertEqual(listed[r['patient_id']], r)
                with patch.object(es, '_PRUNE', False):
                    off = es.match(store, query, windows, conditions=conditions)
                self.assertEqual(json.dumps(base), json.dumps(off), (cohort, seed))

    def test_pruning_acts_on_relative_steps(self):
        pruned = 0
        _, store, _, _ = _STATE['fixture']
        ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
        for seed in range(40):
            query, conditions, windows = case(seed, 'fixture')
            request = es.Request(query, conditions)
            pq = tg.PreparedQuery(request.query)
            found = es._candidates(store, pq, request)
            by_idx = {ids[p]: w for p, w in windows.items()}
            live = [i for i in found['patient_idx'] if by_idx[i] is not None]
            pruned += len(es._prune(store, pq, live, by_idx, tags=request.used, derived=request.derived_used))
        self.assertGreater(pruned, 0)

    def test_every_factor_relaxation_holds_the_stricter_tags_events(self):
        checked = 0
        for cohort in ('fixture', 'shared', 'cohort'):
            _, store, _, _ = _STATE[cohort]
            pids = dict(store.query('SELECT patient_idx, patient_id FROM patients'))
            idxs = sorted(pids)
            for seed in range(60):
                query, conditions, windows = case(seed, cohort)
                request = es.Request(query, conditions)
                wide = next((x['concept'] for x in request.query['relaxations'] if x['id'] == 'fA'), None)
                if wide not in request.derived_used:
                    continue
                strict = request.query['steps'][0]['concept']
                ws = [windows[pids[i]] or {'start': 0, 'end': 0} for i in idxs]
                for idx, d in es._derived_chunk(store, request, idxs, [pids[i] for i in idxs], ws, []):
                    for doc in (d.first, d.second or d.first):
                        have = lambda name: {e['id'] for e in doc['events'] if name in e['concepts']}
                        self.assertTrue(have(strict) <= have(wide), (cohort, seed, idx))
                        checked += 1
        self.assertGreater(checked, 300)

    def test_the_period_label_is_read_from_the_snapshot(self):
        _, store, _, _ = _STATE['fixture']
        self.assertEqual(store.manifest['labels'], {es.PERIOD_LABEL: 'diary_summary'})
        _, points, _, _ = _STATE['points']
        self.assertIsNone(points.manifest['labels'])
        _, cohort, _, _ = _STATE['cohort']
        self.assertEqual(cohort.manifest['labels'], {es.PERIOD_LABEL: 'diary_summary'})


class RequestTests(unittest.TestCase):
    """The 1.2 contract: refusals by name, output and context, a 1.0 or 1.1 request answered as before."""

    def setUp(self):
        self.ingest, self.store, _, _ = _STATE['fixture']
        self.pid = self.ingest['patients'][0]['patient_id']
        self.windows = {self.pid: {'start': T0, 'end': T0 + 500 * DAY}}

    def test_refusals(self):
        good = rel(unit='/month', within=75 * DAY)['relative']
        q = one_step('m:freq')
        cases = [
            ({'A': {'relative': good, 'unit': '/month', 'ge': '2'}}, 'RELATIVE_WITH_ABSOLUTE'),
            ({'A': {'relative': good, 'occurrence': {'nth': 2}}}, 'OCCURRENCE_ON_DERIVED_STEP'),
            ({'A': {'relative': good, 'count': {'at_least': 2, 'within': 'window'}}}, 'OCCURRENCE_ON_DERIVED_STEP'),
            ({'A': {'relative': {**good, 'kind': 'jump'}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {**good, 'factor': '1'}}}, 'INVALID_FACTOR'),
            ({'A': {'relative': {**good, 'factor': '0.5'}}}, 'INVALID_FACTOR'),
            ({'A': {'relative': {**good, 'factor': 'two'}}}, 'INVALID_FACTOR'),
            ({'A': {'relative': {**good, 'factor': 2}}}, 'INVALID_FACTOR'),
            ({'A': {'relative': {**good, 'floor': '0'}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {**good, 'floor': '-1'}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {**good, 'unit': ' kg'}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {**good, 'baseline': {'previous': 4, 'within': DAY}}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {**good, 'baseline': {'previous': 0, 'within': DAY}}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {**good, 'baseline': {'previous': 2, 'within': 0}}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {**good, 'baseline': {'previous': 2}}}}, 'INVALID_RELATIVE'),
            ({'A': {'relative': {k: v for k, v in good.items() if k != 'floor'}}}, 'INVALID_RELATIVE'),
        ]
        for steps, code in cases:
            with self.assertRaises(es.StoreError) as raised:
                es.Request(q, {'profile': L12, 'steps': steps})
            self.assertEqual(raised.exception.code, code, steps)
        for occurrence in ('first', 'last'):
            with self.assertRaises(es.StoreError) as raised:
                es.Request(one_step('m:freq', occurrence=occurrence), {'profile': L12, 'steps': {'A': {'relative': good}}})
            self.assertEqual(raised.exception.code, 'OCCURRENCE_ON_DERIVED_STEP')
        steps = {'A': {'relative': good}}
        for relax, code in (({'id': 'f', 'kind': 'widen_factor', 'step': 'A', 'cost': '1', 'factor': '2'},
                             'FACTOR_RELAXATION_MUST_WIDEN'),
                            ({'id': 'f', 'kind': 'widen_factor', 'step': 'A', 'cost': '1', 'factor': '3'},
                             'FACTOR_RELAXATION_MUST_WIDEN'),
                            ({'id': 'f', 'kind': 'widen_factor', 'step': 'A', 'cost': '1', 'factor': '1'}, 'INVALID_FACTOR'),
                            ({'id': 'f', 'kind': 'widen_factor', 'step': 'A', 'cost': '1'}, 'INVALID_FACTOR_RELAXATION'),
                            ({'id': 'own', 'kind': 'widen_factor', 'step': 'A', 'cost': '1', 'factor': '1.5'},
                             'DERIVED_TAG_NAME_COLLISION')):
            with self.assertRaises(es.StoreError) as raised:
                es.Request(q, {'profile': L12, 'steps': steps, 'relaxations': [relax]})
            self.assertEqual(raised.exception.code, code, relax)
        two = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'm:freq'}, {'id': 'B', 'concept': 'd:a'}],
               'relations': []}
        with self.assertRaises(es.StoreError) as raised:
            es.Request(two, {'profile': L12, 'steps': steps, 'relaxations': [
                {'id': 'f', 'kind': 'widen_factor', 'step': 'B', 'cost': '1', 'factor': '1.5'}]})
        self.assertEqual(raised.exception.code, 'FACTOR_RELAXATION_STEP_NOT_RELATIVE')
        with self.assertRaises(es.StoreError) as raised:  # a value relaxation on a relative step
            es.Request(q, {'profile': L12, 'steps': steps, 'relaxations': [
                {'id': 'v', 'kind': 'widen_value', 'step': 'A', 'cost': '1', 'condition': {'unit': '/month', 'ge': '1'}}]})
        self.assertEqual(raised.exception.code, 'VALUE_RELAXATION_STEP_NOT_CONDITIONED')
        for profile in (es.CONDITIONS_PROFILE, es.CONDITIONS_PROFILE_1_1):  # earlier profiles know no relative condition
            with self.assertRaises(es.StoreError) as raised:
                es.Request(q, {'profile': profile, 'steps': steps})
            self.assertEqual(raised.exception.code, 'INVALID_CONDITION')
        with self.assertRaises(es.StoreError) as raised:
            es.Request(q, {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'A': {'unit': '/month', 'ge': '1'}},
                           'relaxations': [{'id': 'f', 'kind': 'widen_factor', 'step': 'A', 'cost': '1', 'factor': '1.5'}]})
        self.assertEqual(raised.exception.code, 'INVALID_VALUE_RELAXATION')

    def test_output_names_the_rules(self):
        q = one_step('m:freq')
        c = {'profile': L12, 'steps': {'A': rel(unit='/month', within=75 * DAY, factor='2.50')},
             'relaxations': [{'id': 'f', 'kind': 'widen_factor', 'step': 'A', 'cost': '0.5', 'factor': '1.50'}]}
        out = es.match(self.store, q, self.windows, conditions=c)
        tags = out['derived_steps']['tags']
        self.assertEqual(tags['derived-step:A/own']['relative']['factor'], '2.5')
        self.assertEqual(tags['derived-step:A/f']['relative']['factor'], '1.5')
        self.assertEqual(out['derived_steps']['steps'], {'A': {'relative': 'rise'}})
        self.assertEqual(out['derived_steps']['relative_rules'], es.RELATIVE_RULES)
        self.assertEqual(list(out['value_conditions']['tags'].values()), [{'concept': 'm:freq', 'condition': {'unit': '/month'}}])
        self.assertIn('relative_rules', out['context'])
        # a 1.1 request carries no relative rules
        out = es.match(self.store, q, self.windows, conditions={'profile': es.CONDITIONS_PROFILE_1_1,
                                                                'steps': {'A': {'occurrence': {'nth': 1}}}})
        self.assertNotIn('relative_rules', out['derived_steps'])
        self.assertNotIn('relative_rules', out['context'])

    def test_a_series_less_snapshot_refuses_by_name(self):
        plain, store = _STATE['plain']
        pid = plain['patients'][0]['patient_id']
        q = one_step('seizure_frequency')
        c = {'profile': L12, 'steps': {'A': rel(unit='/month', within=75 * DAY)}}
        with self.assertRaises(es.StoreError) as raised:
            es.match(store, q, {pid: {'start': 0, 'end': 10}}, conditions=c)
        self.assertEqual(raised.exception.code, 'NO_SERIES_IN_SNAPSHOT')

    def test_earlier_profiles_are_answered_as_before(self):
        q = one_step('m:freq')
        for steps in ({'A': {'unit': '/month', 'ge': '3'}}, {'A': {'count': {'at_least': 2, 'within': 'window'}}}):
            for profile in (es.CONDITIONS_PROFILE_1_1, L12):
                if profile == es.CONDITIONS_PROFILE_1_1 or 'count' in steps['A']:
                    continue
            a = es.match(self.store, q, self.windows, conditions={'profile': es.CONDITIONS_PROFILE_1_1, 'steps': steps})
            b = es.match(self.store, q, self.windows, conditions={'profile': L12, 'steps': steps})
            self.assertEqual(a['results'], b['results'])
            self.assertNotIn('relative_rules', a.get('derived_steps', {}))


class ConverterTests(unittest.TestCase):
    def test_the_converter_labels_diary_summaries_as_period_readings_only_with_series(self):
        plain, _ = _STATE['plain']
        ingest, _, _, _ = _STATE['cohort']
        self.assertEqual(ingest['provenance']['labels'], {es.PERIOD_LABEL: 'diary_summary'})
        self.assertNotIn('labels', plain['provenance'])
        kinds = {e['kind'] for p in ingest['patients'] for e in p['events']
                 if any(r['event_id'] == e['event_id'] for r in p.get('series', []))}
        self.assertEqual(kinds, {'diary_summary', 'measurement'})


if __name__ == '__main__':
    unittest.main()
