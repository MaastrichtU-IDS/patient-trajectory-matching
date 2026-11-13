"""Derived steps in the execution store's match request (D32): Nth and Nth-from-last occurrence, and "at least N times
within a span or the index period", over coded records.

- Small cases (`derived_cases`): the store's per-patient path checked against the true semantics by enumerating every
  choice of times (`trajectory_graph_worlds.check_derived`), and against the reference (`execution_store_reference`),
  on multi-concept and coupled generators; envelopes and count strengths against enumeration; planted defects.
- Snapshots: the store against the reference, result for result, on a fixture made for it (wide and exact ranges, ties,
  duplicates, letter mentions, exposures, shared variables, excluded records before, inside and after windows, partial
  coverage, values) and on the synthetic cohort, at 1 and 2 workers, both detail levels, pruning on and off; relaxation
  containment; refusals; requests without the new fields unchanged.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
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
L11 = es.CONDITIONS_PROFILE_1_1
SMALL = 1000  # small cases per feature and generator in the suite (the decision log reports the ad hoc runs)


def run_small(feature, generator, n, defect=None, reference=True, cases=dc.tiny_case):
    """(violations, mismatches, status mix) over n small cases."""
    bad, mismatched, mix = [], [], Counter()
    with patch.object(es, '_D32_DEFECT', defect):
        for seed in range(n):
            q, c, p, m = cases(seed, feature, generator)
            got = dc.store_result(q, c, p, m)
            mix[got['status']] += 1
            wrong = tw.check_derived(got, q, c, p, m)
            if wrong:
                bad.append((seed, wrong))
            if reference and json.dumps(got) != json.dumps(dc.reference_result(q, c, p, m)):
                mismatched.append(seed)
    return bad, mismatched, mix


class SmallCaseTests(unittest.TestCase):
    """The store's derived steps are sound on small grids and agree with the reference."""

    def test_no_claim_is_false_and_the_reference_agrees(self):
        for feature in dc.FEATURES:
            for generator in ('multi', 'coupled'):
                bad, mismatched, mix = run_small(feature, generator, SMALL)
                self.assertEqual(bad[:3], [], (feature, generator))
                self.assertEqual(mismatched[:3], [], (feature, generator))
                self.assertTrue({'exact', 'possible', 'none', 'unresolved'} <= set(mix), (feature, generator, mix))

    def test_envelopes_and_count_strengths_equal_enumeration(self):
        unlinked = linked = 0
        for feature in dc.FEATURES:
            for generator in ('multi', 'coupled'):
                for seed in range(300):
                    q, c, p, m = dc.tiny_case(seed, feature, generator)
                    request = es.Request(q, c)
                    derived = es.derive_patient(request, 'P', p['window'], p['events'], m)
                    for name, spec in request.derived_used.items():
                        exact = spec['concept'] not in derived.linked
                        unlinked, linked = unlinked + exact, linked + (not exact)
                        if spec['kind'] == 'nth':
                            truth = tw.nth_times(p, spec['concept'], spec['which'], spec['n'], m)
                            got = derived.points.get(name)
                            if not truth:
                                self.assertIsNone(got, (feature, generator, seed))
                                continue
                            # Every time the Nth occurrence takes lies in the envelope; without shared variables the
                            # envelope is exactly those times.
                            self.assertTrue(got[0] <= min(truth) and max(truth) <= got[1], (seed, got, truth))
                            if exact:
                                self.assertEqual(set(range(got[0], got[1] + 1)), truth, (feature, generator, seed))
                            continue
                        truth = tw.count_strengths(p, spec['concept'], spec['n'], spec['within'], m)
                        tags = {}
                        for e in derived.first['events']:
                            if name in e['concepts']:
                                tags[e['id']] = 'certain'
                        for e in (derived.second or derived.first)['events']:
                            if name in e['concepts'] and e['id'] not in tags:
                                tags[e['id']] = 'possible'
                        if exact:
                            self.assertEqual(tags, truth, (feature, generator, seed, name))
                        else:  # certain is sound, possible covers every possibly reached record
                            self.assertTrue({k for k, v in tags.items() if v == 'certain'}
                                            <= {k for k, v in truth.items() if v == 'certain'}, seed)
                            self.assertTrue(set(truth) <= set(tags), seed)
        self.assertGreater(unlinked, 1000)
        self.assertGreater(linked, 20)


def occurrence_on_count_case(seed, feature, generator):
    """A small case whose count step also asks for first or last: refused by the store, allowed by the planted defect."""
    q, c, p, m = dc.tiny_case(seed, feature, generator)
    rng = random.Random(seed)
    q = json.loads(json.dumps(q))
    q['steps'][0]['occurrence'] = rng.choice(('first', 'last'))
    return q, c, p, m


class PlantedDefectTests(unittest.TestCase):
    """Each planted defect of the D32 rules makes the worlds check (or the differential) fail."""

    def caught(self, feature, defect, n=3000, cases=dc.tiny_case):
        found = 0
        for generator in ('multi', 'coupled'):
            bad, _, _ = run_small(feature, generator, n, defect, reference=False, cases=cases)
            found += len(bad)
        return found

    def test_nth_defects(self):
        for defect in ('nth_off_by_one', 'rule_r', 'nth_last_from_front', 'recorded_bounds', 'coupling_ignored'):
            self.assertGreater(self.caught('nth', defect), 0, defect)

    def test_count_defects(self):
        for feature, defect in (('span', 'span_off_by_one'), ('span', 'own_witness'), ('span', 'forced_allowed_swapped'),
                                ('window', 'period_overlap'), ('window', 'tau_cut'), ('window', 'own_witness')):
            self.assertGreater(self.caught(feature, defect), 0, (feature, defect))

    def test_two_pass_defects(self):
        self.assertGreater(self.caught('span', 'pass2_unrelaxed_only', 5000), 0)
        self.assertGreater(self.caught('span', 'occurrence_on_derived', 2000, occurrence_on_count_case), 0)

    def test_the_review_case_of_rule_r(self):
        # e1 in [1, 10], e2 exactly 2: rule R ranks e2 second, so "the 2nd x at or before 3" would be exact; the 2nd
        # occurrence is in [2, 10], so it is only possible.
        p = {'patient_id': 'P', 'window': {'start': 0, 'end': 3},
             'events': [{'id': 'e1', 'concepts': ['x'], 'start': [1, 10]}, {'id': 'e2', 'concepts': ['x'], 'start': [2, 2]}]}
        q = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'x'}], 'relations': []}
        c = {'profile': L11, 'steps': {'A': {'occurrence': {'nth': 2}}}}
        self.assertEqual(dc.store_result(q, c, p, set())['status'], 'possible')
        with patch.object(es, '_D32_DEFECT', 'rule_r'):
            wrong = dc.store_result(q, c, p, set())
        self.assertEqual(wrong['status'], 'exact')
        self.assertTrue(tw.check_derived(wrong, q, c, p))

    def test_the_coupling_example(self):
        # Two events: "the 2nd x at least 1 before the last x" is impossible; the envelope alone would say possible.
        p = {'patient_id': 'P', 'window': {'start': 0, 'end': 9},
             'events': [{'id': 'e1', 'concepts': ['x'], 'start': [0, 5]}, {'id': 'e2', 'concepts': ['x'], 'start': [0, 5]}]}
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'x'}, {'id': 'B', 'concept': 'x', 'occurrence': 'last'}],
             'relations': [{'id': 'R', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 1}]}
        c = {'profile': L11, 'steps': {'A': {'occurrence': {'nth': 2}}}}
        got = dc.store_result(q, c, p, set())
        self.assertEqual((got['status'], got.get('reason')), ('unresolved', 'OCCURRENCE_TIME_COUPLED'))
        self.assertEqual(tw.check_derived(got, q, c, p), [])
        with patch.object(es, '_D32_DEFECT', 'coupling_ignored'):
            wrong = dc.store_result(q, c, p, set())
        self.assertNotEqual(tw.check_derived(wrong, q, c, p), [])


# Snapshot differentials.

ANCESTORS = {'c:seizure': [], 'c:febrile': ['c:seizure'], 'c:absence': ['c:seizure'], 'd:drug': [], 'd:a': ['d:drug'],
             'd:b': ['d:drug'], 'm:freq': [], 'x:other': []}
SEIZURES = ('c:febrile', 'c:febrile', 'c:absence')


def derived_ingest(seed, patients=40, shared=0.2, excluded=True):
    """Patients with several seizure records each (exact, day-wide and month-wide ranges, ties and duplicates), letter
    mentions spanning from birth, drug exposures (some ongoing, some proper), monthly frequency readings with values,
    shared variables, excluded records and partial coverage."""
    rng = random.Random(seed)
    out = []
    for k in range(patients):
        pid = f'D{seed}-{k:03d}'
        events, series, dropped = [], [], []
        birth = T0 + rng.randint(0, 30) * DAY
        for j in range(rng.randint(0, 9)):
            lo = birth + rng.randint(0, 400) * DAY
            width = rng.choice((0, 0, 0, DAY - 1, 30 * DAY, 120 * DAY))
            concept = rng.choice(SEIZURES)
            events.append({'event_id': f's{j}', 'kind': rng.choice(('condition', 'registry')), 'source': rng.choice(('ehr', 'reg')),
                           'concepts': [concept], 'start': [lo, lo + width], 'evidence': {}})
            if rng.random() < 0.15:  # a duplicate of the same happening in another source
                events.append({'event_id': f's{j}d', 'kind': 'registry', 'source': 'reg', 'concepts': [concept],
                               'start': [lo - DAY, lo + width + DAY], 'evidence': {}})
        for j in range(rng.randint(0, 3)):
            letter = birth + rng.randint(100, 500) * DAY
            events.append({'event_id': f'm{j}', 'kind': 'mention', 'source': 'letters', 'concepts': [rng.choice(SEIZURES)],
                           'start': [birth, letter], 'evidence': {}})
        for j in range(rng.randint(0, 3)):
            lo = birth + rng.randint(0, 400) * DAY
            e = {'event_id': f'x{j}', 'kind': 'medication_exposure', 'source': 'ehr', 'concepts': [rng.choice(('d:a', 'd:b'))],
                 'start': [lo, lo + rng.choice((0, DAY))], 'evidence': {}}
            e['end'] = [lo + rng.randint(1, 90) * DAY, None] if rng.random() < 0.3 else [lo + 10 * DAY, lo + 60 * DAY]
            if e['end'][1] is not None and rng.random() < 0.3:
                e['proper'] = True
            events.append(e)
        for j in range(rng.randint(0, 4)):
            lo = birth + rng.randint(0, 400) * DAY
            eid = f'f{j}'
            events.append({'event_id': eid, 'kind': 'diary_summary', 'source': 'diary', 'concepts': ['m:freq'],
                           'start': [lo, lo + 29 * DAY], 'evidence': {}})
            text = rng.choice(('0', '1', '2', '2.0', '3', '8', '10'))
            decimal, micro = sv.columns(text)
            series.append({'event_id': eid, 'item_class': 'm:freq', 'value_lexical': text, 'value_decimal': decimal,
                           'value_micro': micro, 'unit': '/month'})
        if rng.random() < shared:
            infusion = next((e for e in events if e.get('end') and e['end'][1] is not None), None)
            point = next((e for e in events if 'end' not in e and e['kind'] != 'mention'), None)
            if infusion and point:
                point['start'] = list(infusion['end'])
                point['start_var'] = infusion['event_id'] + '.f'
        for _ in range(rng.choice((0, 0, 0, 1, 2)) if excluded else 0):
            lo = birth + rng.randint(-30, 430) * DAY
            dropped.append({'source': 'ehr', 'concept': rng.choice(('c:febrile', 'c:absence', 'd:a')), 'start': [lo, lo + DAY],
                            'reason': 'NOT_ADMITTED'})
        coverage = [{'source': s, 'lo': birth, 'hi': birth + 420 * DAY, 'basis': 'declared'}
                    for s in ('ehr', 'reg', 'letters', 'diary') if rng.random() < 0.85]
        patient = {'patient_id': pid, 'clock_id': f'patient_{pid}', 'events': sorted(events, key=lambda e: e['event_id']),
                   'coverage': coverage}
        if series:
            patient['series'] = sorted(series, key=lambda r: r['event_id'])
        if dropped:
            patient['excluded'] = dropped
        out.append(patient)
    return {'profile': es.INGEST_PROFILE, 'dataset_id': f'derived-fixture-{seed}',
            'closure': {'version': 'derived-fixture-1', 'ancestors': ANCESTORS}, 'patients': out}


def windows_for(rng, ingest):
    out = {}
    for p in ingest['patients']:
        if rng.random() < 0.05:
            out[p['patient_id']] = None
            continue
        first = min([e['start'][0] for e in p['events']] or [T0])
        start = first + rng.randint(-30, 200) * DAY
        out[p['patient_id']] = {'start': start, 'end': start + rng.randint(30, 400) * DAY}
    return out


def derived_request(rng, ingest, feature, steps_pool):
    """(query, conditions, windows): a derived step of `feature` on a seizure concept, and up to two other steps."""
    concept = rng.choice(steps_pool['derived'])
    steps = [{'id': 'A', 'concept': concept}]
    conds, relaxations, count_relax = {}, [], []
    if feature == 'nth':
        conds['A'] = {'occurrence': {rng.choice(('nth', 'nth_last')): rng.randint(1, 4)}}
    else:
        n = rng.randint(1, 4)
        within = 'window' if feature == 'window' else {'span': rng.choice((0, 1, 30, 90, 183)) * DAY}
        conds['A'] = {'count': {'at_least': n, 'within': within}}
        if n > 1 and rng.random() < 0.5:
            x = {'id': 'cA', 'kind': 'widen_count', 'step': 'A', 'cost': rng.choice(('0.5', '1')), 'at_least': n - 1}
            if rng.random() < 0.3 and steps_pool['parent'].get(concept):
                x['concept'] = steps_pool['parent'][concept]
            count_relax.append(x)
    if steps_pool['parent'].get(concept) and rng.random() < 0.3:
        relaxations.append({'id': 'wA', 'kind': 'widen_concept', 'step': 'A', 'concept': steps_pool['parent'][concept],
                            'cost': rng.choice(('0.5', '1'))})
    for i in range(rng.randint(0, 2)):
        sid = 'BC'[i]
        other = rng.choice(steps_pool['other'] + steps_pool['derived'])
        step = {'id': sid, 'concept': other, 'occurrence': rng.choice(('any', 'any', 'first', 'last'))}
        steps.append(step)
        if other in steps_pool.get('measured', ()) and rng.random() < 0.6:
            conds[sid] = {'unit': '/month', rng.choice(('ge', 'le')): rng.choice(('2', '3', '8'))}
        elif step['occurrence'] == 'any' and rng.random() < 0.2:
            conds[sid] = {'occurrence': {'nth': rng.randint(1, 2)}} if rng.random() < 0.5 else {
                'count': {'at_least': 2, 'within': {'span': 60 * DAY}}}
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        r = {'id': f'R{i}', 'from': a, 'to': b}
        nth_from = 'occurrence' in conds.get(a, {})
        if rng.random() < 0.15 and not nth_from:
            relations.append({**r, 'kind': 'during', 'grace': rng.choice((0, 30)) * DAY})
            continue
        lo = rng.choice((-60, 0, 0, 30)) * DAY
        r.update(min=lo, max=lo + rng.choice((30, 180, 365)) * DAY)
        relations.append({**r, 'kind': 'after'})
        if rng.random() < 0.3:
            relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                'max': r['max'] + 90 * DAY, 'cost': rng.choice(('0.25', '0.5'))})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1')), 'search_budget': rng.choice((300, 40000))}
    conditions = {'profile': L11, 'steps': conds}
    if count_relax:
        conditions['relaxations'] = count_relax
    return query, conditions, windows_for(rng, ingest)


FIXTURE_POOL = {'derived': ['c:seizure', 'c:febrile', 'c:absence'], 'other': ['d:drug', 'd:a', 'm:freq'],
                'parent': {'c:febrile': 'c:seizure', 'c:absence': 'c:seizure'}, 'measured': ['m:freq']}
COHORT_POOL = {'derived': ['seizure', 'febrile_seizure', 'focal_seizure', 'generalised_tonic_clonic'],
               'other': ['sodium_channel_blocker', 'valproate', 'levetiracetam', 'status_epilepticus', 'seizure'],
               'parent': {'febrile_seizure': 'seizure', 'focal_seizure': 'seizure', 'generalised_tonic_clonic': 'seizure'}}

_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='derived-steps-')
    tmp = Path(_STATE['tmp'].name)
    for name, seed, options in (('fixture', 31, {}), ('shared', 32, {'shared': 0.6}), ('clean', 33, {'excluded': False})):
        ingest = derived_ingest(seed, **options)
        store = es.open_store(es.build(ingest, tmp / name), verify='content')
        _STATE[name] = (ingest, store, pr.PreparedReference(ingest), FIXTURE_POOL)
    subprocess.run([sys.executable, str(ROOT / 'demos/mockups/synthetic/generate.py'), '--output', str(tmp / 'cohort.json')],
                   check=True, capture_output=True, cwd=ROOT)
    cohort = _STATE['cohort_doc'] = json.loads((tmp / 'cohort.json').read_text(encoding='utf-8'))
    ingest = si.synthetic_ingest(cohort)
    known = set(ingest['closure']['ancestors'])
    pool = {k: [c for c in v if c in known] if isinstance(v, list) else {a: b for a, b in v.items() if a in known}
            for k, v in COHORT_POOL.items()}
    store = es.open_store(es.build(ingest, tmp / 'cohort'), verify='content')
    _STATE['cohort'] = (ingest, store, pr.PreparedReference(ingest), pool)


def tearDownModule():
    for name in ('fixture', 'shared', 'clean', 'cohort'):
        if name in _STATE:
            _STATE[name][1].close()
    es.close_pools()
    _STATE['tmp'].cleanup()


def case(seed, cohort, feature):
    ingest, _, _, pool = _STATE[cohort]
    rng = random.Random(f'{cohort}/{feature}/{seed}')
    for _ in range(50):
        query, conditions, windows = derived_request(rng, ingest, feature, pool)
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


def compare(cohort, feature, seeds, **options):
    ingest, store, prepared, _ = _STATE[cohort]
    wrong, mix = [], Counter()
    for seed in seeds:
        query, conditions, windows = case(seed, cohort, feature)
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


DIFFERENTIAL = {'fixture': 60, 'shared': 40, 'clean': 30, 'cohort': 50}  # requests per feature per cohort (180 each)


class DerivedDifferentialTests(unittest.TestCase):
    """The store against the reference, result for result, on snapshots."""

    def test_the_store_equals_the_reference(self):
        for cohort, n in DIFFERENTIAL.items():
            for feature in dc.FEATURES:
                wrong, mix = compare(cohort, feature, range(n))
                self.assertEqual(wrong[:2], [], (cohort, feature))
                self.assertTrue({'exact', 'none'} <= set(mix), (cohort, feature, mix))

    def test_workers_detail_and_pruning_change_nothing(self):
        for feature in dc.FEATURES:
            ingest, store, _, _ = _STATE['fixture']
            for seed in range(0, 30, 3):
                query, conditions, windows = case(seed, 'fixture', feature)
                base = es.match(store, query, windows, conditions=conditions)
                two = es.match(store, query, windows, conditions=conditions, workers=2, chunk=7)
                self.assertEqual(json.dumps(base), json.dumps(two), (feature, seed))
                decided = es.match(store, query, windows, conditions=conditions, detail='decided')
                listed = {r['patient_id']: r for r in decided['results']}
                for r in base['results']:
                    if r['patient_id'] in listed:
                        self.assertEqual(listed[r['patient_id']], r)
                with patch.object(es, '_PRUNE', False):
                    off = es.match(store, query, windows, conditions=conditions)
                self.assertEqual(json.dumps(base), json.dumps(off), (feature, seed))

    def test_pruning_acts_on_derived_steps(self):
        pruned = 0
        for feature in dc.FEATURES:
            ingest, store, _, _ = _STATE['fixture']
            for seed in range(40):
                query, conditions, windows = case(seed, 'fixture', feature)
                request = es.Request(query, conditions)
                pq = tg.PreparedQuery(request.query)
                found = es._candidates(store, pq, request)
                ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
                by_idx = {ids[p]: w for p, w in windows.items()}
                live = [i for i in found['patient_idx'] if by_idx[i] is not None]
                pruned += len(es._prune(store, pq, live, by_idx, tags=request.used, derived=request.derived_used))
        self.assertGreater(pruned, 0)

    def test_every_count_relaxation_holds_the_stricter_tags_events(self):
        checked = 0
        for cohort in ('fixture', 'cohort'):
            ingest, store, _, _ = _STATE[cohort]
            for seed in range(60):
                query, conditions, windows = case(seed, cohort, 'span' if seed % 2 else 'window')
                request = es.Request(query, conditions)
                if not any(x['kind'] == 'widen_count' and 'concept' not in x for x in conditions.get('relaxations', [])):
                    continue
                pq = tg.PreparedQuery(request.query)
                strict = request.query['steps'][0]['concept']
                wider = [x['concept'] for x in request.query['relaxations'] if x['id'] == 'cA'][0]
                if wider not in request.derived_used:  # beyond the budget: never tried, never derived
                    continue
                idxs = sorted(i for i, in store.query('SELECT patient_idx FROM patients'))
                pids = dict(store.query('SELECT patient_idx, patient_id FROM patients'))
                ws = [windows[pids[i]] or {'start': 0, 'end': 0} for i in idxs]
                for idx, d in es._derived_chunk(store, request, idxs, [pids[i] for i in idxs], ws, []):
                    for doc in (d.first, d.second or d.first):
                        have = lambda name: {e['id'] for e in doc['events'] if name in e['concepts']}
                        self.assertTrue(have(strict) <= have(wider), (cohort, seed, idx))
                        checked += 1
        self.assertGreater(checked, 100)

    def test_a_filter_on_n_instead_of_the_smallest_n_loses_matches(self):
        lost = 0
        for seed in range(80):
            query, conditions, windows = case(seed, 'fixture', 'span')
            if not conditions.get('relaxations'):
                continue
            ingest, store, _, _ = _STATE['fixture']
            right = es.match(store, query, windows, conditions=conditions)['results']
            with patch.object(es, '_D32_DEFECT', 'filter_n'):
                wrong = es.match(store, query, windows, conditions=conditions)['results']
            lost += sum(1 for a, b in zip(right, wrong) if json.dumps(a) != json.dumps(b))
        self.assertGreater(lost, 0)

    def test_a_scoped_reach_for_nth_turns_a_none_it_cannot_support_into_a_result(self):
        moved = 0
        for seed in range(120):
            query, conditions, windows = case(seed, 'fixture', 'nth')
            ingest, store, prepared, _ = _STATE['fixture']
            want = prepared.match(query, windows, conditions)
            with patch.object(es, '_D32_DEFECT', 'scoped_reach'):
                got = es.match(store, query, windows, conditions=conditions)['results']
            moved += sum(1 for g, w in zip(got, want) if json.dumps(unfiltered(g)) != json.dumps(w))
        self.assertGreater(moved, 0)


class ConverterSeriesTests(unittest.TestCase):
    """The synthetic converter's `series` option (D32)."""

    def test_rows_equal_the_parity_helpers_and_unitless_records_are_excluded(self):
        sys.path.insert(0, str(ROOT / 'demos/mockups'))
        import store_parity
        cohort = _STATE['cohort_doc']
        plain = {p['patient_id']: p for p in si.synthetic_ingest(cohort)['patients']}
        withs = si.synthetic_ingest(cohort, series=True)
        self.assertEqual(withs['provenance']['series'], si.SERIES_RULE)
        self.assertNotIn('series', si.synthetic_ingest(cohort)['provenance'])
        excluded = rows = 0
        for p in withs['patients']:
            records = {r['record_id']: r for r in next(c for c in cohort['patients'] if c['patient_id'] == p['patient_id'])['records']}
            helper = store_parity.with_series(plain[p['patient_id']], records)
            self.assertEqual(p.get('series', []), helper.get('series', []), p['patient_id'])
            rows += len(p.get('series', []))
            gone = {e['event_id'] for e in plain[p['patient_id']]['events']} - {e['event_id'] for e in p['events']}
            self.assertEqual(len(gone), len(p.get('excluded', [])))
            for x in p.get('excluded', []):
                self.assertEqual(x['reason'], 'MEASUREMENT_WITHOUT_UNIT')
            excluded += len(p.get('excluded', []))
            kept = [e for e in plain[p['patient_id']]['events'] if e['event_id'] not in gone]
            self.assertEqual(kept, p['events'])
        self.assertEqual((rows, excluded), (3484, 31))

    def test_a_spelling_that_is_not_a_plain_decimal_is_refused(self):
        self.assertEqual(si.value_spelling(12.5), '12.5')
        self.assertIsNone(si.value_spelling('12'))
        with self.assertRaises(ValueError) as raised:
            si.value_spelling(1e-05)
        self.assertIn('NON_PLAIN_VALUE_SPELLING', str(raised.exception))

    def test_the_default_build_is_unchanged(self):
        ingest = _STATE['cohort'][0]
        self.assertFalse(any('series' in p or 'excluded' in p for p in ingest['patients']))


def one(store, query, windows, conditions):
    return es.match(store, query, windows, conditions=conditions)


class RequestTests(unittest.TestCase):
    """The 1.1 contract: refusals by name, output and context, a 1.0 request answered as before."""

    def setUp(self):
        self.ingest, self.store, _, _ = _STATE['fixture']
        self.pid = self.ingest['patients'][0]['patient_id']
        self.windows = {self.pid: {'start': T0, 'end': T0 + 500 * DAY}}

    def q(self, **step):
        return {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'c:seizure', **step},
                                                    {'id': 'B', 'concept': 'd:drug'}],
                'relations': [{'id': 'R', 'kind': 'during', 'from': 'A', 'to': 'B'}]}

    def test_refusals(self):
        nth = {'occurrence': {'nth': 2}}
        cases = [
            (self.q(), {'A': nth}, 'NTH_STEP_HAS_NO_END'),
            (self.q(occurrence='first'), {'A': {'occurrence': {'nth': 2}}}, 'OCCURRENCE_CONFLICT'),
            (self.q(occurrence='last'), {'A': {'count': {'at_least': 2, 'within': 'window'}}}, 'OCCURRENCE_ON_DERIVED_STEP'),
            (self.q(), {'A': {**nth, 'count': {'at_least': 2, 'within': 'window'}}}, 'OCCURRENCE_ON_DERIVED_STEP'),
            (self.q(), {'A': {'occurrence': {'nth': 0}}}, 'INVALID_OCCURRENCE'),
            (self.q(), {'A': {'occurrence': {'third': 1}}}, 'INVALID_OCCURRENCE'),
            (self.q(), {'A': {'count': {'at_least': 2}}}, 'INVALID_COUNT'),
            (self.q(), {'A': {'count': {'at_least': 2, 'within': {'span': -1}}}}, 'INVALID_COUNT'),
            (self.q(), {'A': {'count': {'at_least': True, 'within': 'window'}}}, 'INVALID_COUNT'),
            (self.q(), {'A': {'colour': 'red'}}, 'INVALID_CONDITION'),
        ]
        for query, steps, code in cases:
            with self.assertRaises(es.StoreError) as raised:
                es.Request(query, {'profile': L11, 'steps': steps})
            self.assertEqual(raised.exception.code, code, steps)
        count = {'A': {'count': {'at_least': 3, 'within': 'window'}}}
        plain = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'c:seizure'}], 'relations': []}
        for relax, code in (({'id': 'c', 'kind': 'widen_count', 'step': 'A', 'cost': '1', 'at_least': 3}, 'COUNT_RELAXATION_MUST_NARROW'),
                            ({'id': 'c', 'kind': 'widen_count', 'step': 'A', 'cost': '1', 'at_least': 0}, 'COUNT_RELAXATION_MUST_NARROW'),
                            ({'id': 'c', 'kind': 'widen_count', 'step': 'A', 'cost': '1'}, 'INVALID_COUNT_RELAXATION')):
            with self.assertRaises(es.StoreError) as raised:
                es.Request(plain, {'profile': L11, 'steps': count, 'relaxations': [relax]})
            self.assertEqual(raised.exception.code, code)
        with self.assertRaises(es.StoreError) as raised:
            es.Request(plain, {'profile': L11, 'steps': {'A': nth},
                               'relaxations': [{'id': 'c', 'kind': 'widen_count', 'step': 'A', 'cost': '1', 'at_least': 1}]})
        self.assertEqual(raised.exception.code, 'COUNT_RELAXATION_STEP_NOT_COUNTED')
        # 1.0 knows no derived step or count relaxation
        with self.assertRaises(es.StoreError) as raised:
            es.Request(plain, {'profile': es.CONDITIONS_PROFILE, 'steps': {'A': nth}})
        self.assertEqual(raised.exception.code, 'INVALID_CONDITION')

    def test_output_names_the_rules_and_a_point_by_step(self):
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'c:seizure'}], 'relations': []}
        out = one(self.store, q, self.windows, {'profile': L11, 'steps': {'A': {'occurrence': {'nth': 1}}}})
        self.assertEqual(out['derived_steps']['tags'], {'derived-step:A/own': {'kind': 'nth', 'concept': 'c:seizure',
                                                                             'which': 'nth', 'n': 1}})
        self.assertIn('records', out['derived_steps']['rules'])
        self.assertNotIn('value_conditions', out)
        self.assertIn('derived_rules', out['context'])
        r = out['results'][0]
        if r['status'] == 'exact':
            self.assertEqual(r['binding'], {'A': 'derived-step:A/own'})

    def test_a_series_less_snapshot_refuses_value_conditions_by_name(self):
        ingest, store, _, _ = _STATE['cohort']  # the plain synthetic build holds no series
        pid = ingest['patients'][0]['patient_id']
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'seizure_frequency'}], 'relations': []}
        with self.assertRaises(es.StoreError) as raised:
            one(store, q, {pid: {'start': 0, 'end': 10}}, {'profile': L11, 'steps': {'A': {'unit': '/month', 'ge': '2'}}})
        self.assertEqual(raised.exception.code, 'NO_SERIES_IN_SNAPSHOT')
        # A 1.0 request keeps its D28 answer (not comparable: no source records the unit), key for key.
        out = one(store, q, {pid: {'start': 0, 'end': 10}}, {'profile': es.CONDITIONS_PROFILE,
                                                              'steps': {'A': {'unit': '/month', 'ge': '2'}}})
        self.assertNotIn('derived_steps', out)

    def test_a_derived_identifier_taken_by_an_event_is_refused(self):
        events = [{'event_id': 'derived-step:A/own', 'kind': 'condition', 'source': 'ehr', 'concepts': ['c:febrile'],
                   'start': [T0, T0], 'evidence': {}}]
        ingest = {'profile': es.INGEST_PROFILE, 'dataset_id': 'taken', 'closure': {'version': '1', 'ancestors': ANCESTORS},
                  'patients': [{'patient_id': 'P', 'clock_id': 'c', 'events': events,
                                'coverage': [{'source': 'ehr', 'lo': T0, 'hi': T0 + DAY, 'basis': 'd'}]}]}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, Path(tmp) / 's')) as store:
            q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'c:seizure'}], 'relations': []}
            with self.assertRaises(es.StoreError) as raised:
                one(store, q, {'P': {'start': T0, 'end': T0 + DAY}}, {'profile': L11, 'steps': {'A': {'occurrence': {'nth': 1}}}})
            self.assertEqual(raised.exception.code, 'DERIVED_EVENT_ID_TAKEN')

    def test_mentions_are_never_counted_or_ranked(self):
        events = [{'event_id': 'a', 'kind': 'condition', 'source': 'ehr', 'concepts': ['c:febrile'], 'start': [T0, T0],
                   'evidence': {}},
                  {'event_id': 'b', 'kind': 'mention', 'source': 'letters', 'concepts': ['c:febrile'],
                   'start': [T0, T0], 'evidence': {}}]
        ingest = {'profile': es.INGEST_PROFILE, 'dataset_id': 'coded', 'closure': {'version': '1', 'ancestors': ANCESTORS},
                  'patients': [{'patient_id': 'P', 'clock_id': 'c', 'events': events,
                                'coverage': [{'source': s, 'lo': T0, 'hi': T0 + DAY, 'basis': 'd'} for s in ('ehr', 'letters')]}]}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, Path(tmp) / 's')) as store:
            q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'c:seizure'}], 'relations': []}
            w = {'P': {'start': T0, 'end': T0 + DAY}}
            self.assertEqual(one(store, q, w, {'profile': L11, 'steps': {'A': {'occurrence': {'nth': 2}}}})['results'][0]['status'], 'none')
            self.assertEqual(one(store, q, w, {'profile': L11, 'steps': {'A': {'occurrence': {'nth': 1}}}})['results'][0]['status'], 'exact')
            count = {'A': {'count': {'at_least': 2, 'within': {'span': 0}}}}
            self.assertEqual(one(store, q, w, {'profile': L11, 'steps': count})['results'][0]['status'], 'none')

    def test_a_request_without_derived_steps_is_answered_as_before(self):
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'm:freq'}], 'relations': []}
        old = {'profile': es.CONDITIONS_PROFILE, 'steps': {'A': {'unit': '/month', 'ge': '3'}}}
        new = {'profile': L11, 'steps': {'A': {'unit': '/month', 'ge': '3'}}}
        a, b = one(self.store, q, self.windows, old), one(self.store, q, self.windows, new)
        self.assertEqual(a['results'], b['results'])
        self.assertNotIn('derived_steps', b)
        self.assertEqual(a['value_conditions']['profile'], es.CONDITIONS_PROFILE)


if __name__ == '__main__':
    unittest.main()
