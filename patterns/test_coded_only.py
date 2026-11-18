"""Coded records only, by request, in the execution store's match request (D39, profile `execution-store-conditions-1.3`,
`patterns/store_conditions_coded.py`).

- Small snapshots (`coded_only_cases`): each tiny patient's result checked against every choice of the times of its kept
  events (`trajectory_graph_worlds`), and against the independent reference (the existing reference on the ingest
  without letter mentions), on the `coded-plain` and `coded-derived` generators; pruning on and off; planted defects.
- Snapshots: the store against the reference, result for result, on D32's derived fixtures and D33's relative fixture
  (letter mentions added, some sharing a coded event's start, some with a series row) and on the synthetic cohort with
  series, for plain steps, value conditions, Nth occurrences, counts and rises, at 1 and 2 workers, both detail levels,
  pruning on and off; the store against itself on the snapshot built without mentions; `exhaustive_match` on a slice.
- The semantics on named cases (a left-out mention is not an excluded record; coverage by kept events; a first step
  whose earliest record is a mention), refusals, the output, and the module's boundary: every request without profile
  1.3 is `store_conditions_relative`'s answer, byte for byte; the copied functions are pinned.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
import hashlib
import inspect
import json
from pathlib import Path
import random
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import coded_only_cases as co
from . import execution_store as es
from . import series_values as sv
from . import store_conditions as sc
from . import store_conditions_coded as cc
from . import store_conditions_counts as scn
from . import store_conditions_relative as rc
from . import test_derived_steps as tds
from . import test_relative_conditions as trc
from . import trajectory_graph as tg

ROOT = Path(__file__).resolve().parents[1]
DAY = 86_400_000_000
T0 = 63_712_000_000_000_000
L13 = cc.CONDITIONS_PROFILE_1_3
SMALL = 3  # small snapshots per generator in the suite (20 patients, 10 requests: 600 cases); the log reports the ad hoc runs
FEATURES = ('plain', 'nth', 'span', 'window', 'relative')


def with_mentions(ingest, seed, concepts, measured=()):
    """The ingest with letter mentions added: 0 to 3 per patient, from the patient's earliest record to a letter, of the
    given concepts; about a third of the patients have a mention naming a coded event's start or a closed exposure's end,
    over a random part of its range (so that it narrows that time while it is kept); a mention of a measured
    concept sometimes carries a series row (which a value tag or a rise would read if the mention were kept); letters
    cover most patients."""
    rng = random.Random(f'coded/mentions/{seed}')
    out = []
    for p in ingest['patients']:
        events = [dict(e) for e in p['events']]
        series = list(p.get('series', []))
        first = min([e['start'][0] for e in events] or [T0])
        added = []
        for j in range(rng.choice((0, 1, 1, 2, 3))):
            concept = rng.choice(concepts)
            letter = first + rng.randint(30, 500) * DAY
            m = {'event_id': f'm-coded-{j}', 'kind': 'mention', 'source': 'letters', 'concepts': [concept],
                 'start': [first, letter], 'evidence': {}}
            added.append(m)
            if concept in measured and rng.random() < 0.5:
                decimal, micro = sv.columns(rng.choice(('2', '9', '20')))
                series.append({'event_id': m['event_id'], 'item_class': concept, 'value_lexical': decimal,
                               'value_decimal': decimal, 'value_micro': micro, 'unit': rng.choice(('/month', 'kg'))})
        coded = [e for e in events if e['kind'] != 'mention' and 'start_var' not in e and 'end_var' not in e]
        if added and coded and rng.random() < 0.3:
            # A mention naming a coded event's start, or a closed exposure's end (D39 review), over a random part of that
            # time's range, so that it narrows the coded event's time while it is kept.
            m, c = added[0], rng.choice(coded)
            if 'end' in c and c['end'][1] is not None and rng.random() < 0.5:
                lo = max(c['end'][0], c['start'][1] + (1 if c.get('proper') else 0))
                name, hi = c['event_id'] + '.f', c['end'][1]
            else:
                name, (lo, hi) = c['event_id'] + '.s', c['start']
            if lo <= hi:
                a = rng.randint(lo, hi)
                m['start_var'], m['start'] = name, [a, rng.randint(a, hi)]
        coverage = list(p['coverage'])
        if added and not any(c['source'] == 'letters' for c in coverage) and rng.random() < 0.8:
            coverage.append({'source': 'letters', 'lo': first - 30 * DAY, 'hi': first + 600 * DAY, 'basis': 'declared'})
        q = {**p, 'events': sorted(events + added, key=lambda e: e['event_id']), 'coverage': coverage}
        if series:
            q['series'] = sorted(series, key=lambda r: r['event_id'])
        out.append(q)
    return {**ingest, 'patients': out}


_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='coded-only-')
    tmp = Path(_STATE['tmp'].name)
    fixtures = {
        'fixture': (with_mentions(tds.derived_ingest(31), 31, ['c:seizure', 'c:febrile', 'd:a', 'm:freq'], ['m:freq']),
                    tds.FIXTURE_POOL, None),
        'shared': (with_mentions(tds.derived_ingest(32, shared=0.6), 32, ['c:febrile', 'c:absence', 'm:freq'], ['m:freq']),
                   tds.FIXTURE_POOL, None),
        'relative': (with_mentions(trc.relative_ingest(41), 41, ['c:febrile', 'm:freq', 'm:weight', 'd:a'],
                                   ['m:freq', 'm:weight']), None, trc.FIXTURE_POOL),
    }
    subprocess.run([sys.executable, str(ROOT / 'demos/mockups/synthetic/generate.py'), '--output', str(tmp / 'cohort.json')],
                   check=True, capture_output=True, cwd=ROOT)
    cohort = json.loads((tmp / 'cohort.json').read_text(encoding='utf-8'))
    ingest = rc.synthetic_ingest(cohort, series=True)
    known = set(ingest['closure']['ancestors'])
    pool = {k: [c for c in v if c in known] if isinstance(v, list) else {a: b for a, b in v.items() if a in known}
            for k, v in tds.COHORT_POOL.items()}
    fixtures['cohort'] = (ingest, pool, trc.COHORT_POOL)
    for name, (ingest, derived_pool, relative_pool) in fixtures.items():
        store = es.open_store(es.build(ingest, tmp / name), verify='content')
        without = es.open_store(es.build(co.without_kinds(ingest, co.KINDS), tmp / f'{name}-coded'), verify='content')
        _STATE[name] = {'ingest': ingest, 'store': store, 'without': without, 'reference': co.Reference(ingest),
                        'derived_pool': derived_pool, 'relative_pool': relative_pool}


def tearDownModule():
    for name in ('fixture', 'shared', 'relative', 'cohort'):
        if name in _STATE:
            _STATE[name]['store'].close()
            _STATE[name]['without'].close()
    es.close_pools()
    _STATE['tmp'].cleanup()


def request(seed, cohort, feature):
    """(query, 1.3 conditions, windows) for one seed: `plain` steps with any, first or last (and the value conditions,
    Nth occurrences or counts D32's generator gives the other steps), an Nth occurrence, a span or period count, or a
    rise or fall; or None when the cohort has no pool for the feature."""
    state = _STATE[cohort]
    pool = state['relative_pool'] if feature == 'relative' else state['derived_pool']
    if pool is None:
        return None
    rng = random.Random(f'{cohort}/coded/{feature}/{seed}')
    for _ in range(50):
        if feature == 'relative':
            query, conditions, windows = trc.relative_request(rng, state['ingest'], pool)
        else:
            query, conditions, windows = tds.derived_request(rng, state['ingest'], 'nth' if feature == 'plain' else feature,
                                                             pool)
            if feature == 'plain':
                conditions = {'profile': conditions['profile'],
                              'steps': {k: v for k, v in conditions['steps'].items() if k != 'A'}}
                query['steps'][0]['occurrence'] = rng.choice(('any', 'first', 'last'))
        query, conditions = co.coded(query, conditions)
        try:
            cc.Request(query, conditions)
        except (es.StoreError, tg.TrajectoryError):
            continue
        return query, conditions, windows
    raise AssertionError((seed, cohort, feature))


def compare(cohort, feature, seeds, **options):
    """[(seed, store result, reference result)] that differ, and the status mix."""
    state = _STATE[cohort]
    wrong, mix = [], Counter()
    for seed in seeds:
        made = request(seed, cohort, feature)
        if made is None:
            continue
        query, conditions, windows = made
        got = cc.match(state['store'], query, windows, conditions=conditions, **options)['results']
        want = state['reference'].match(query, windows, conditions)
        if options.get('detail') == 'decided':
            listed = {r['patient_id'] for r in got}
            want = [w for w in want if w['patient_id'] in listed]
        for g, w in zip(got, want, strict=True):
            if json.dumps(co.unfiltered(g)) != json.dumps(w):
                wrong.append((seed, g, w))
        mix.update(g['status'] for g in got)
    return wrong, mix


DIFFERENTIAL = {'fixture': 12, 'shared': 8, 'relative': 12, 'cohort': 10}  # requests per cohort and feature


class SmallCaseTests(unittest.TestCase):
    """The store's coded-only answers are sound against every choice of the kept events' times, and equal the reference."""

    def test_no_claim_is_false_and_the_reference_agrees(self):
        for generator in co.GENERATORS:
            violations, mismatches, mix, cases = co.run_small(Path(_STATE['tmp'].name) / 'small', generator, SMALL,
                                                              patients=20, requests=10)
            self.assertEqual(violations[:2], [], generator)
            self.assertEqual(mismatches[:2], [], generator)
            self.assertEqual(cases, SMALL * 200)
            self.assertTrue({'exact', 'possible', 'none', 'not_comparable'} <= set(mix), (generator, mix))


class PlantedDefectTests(unittest.TestCase):
    """Each D39 planted defect is caught by the worlds check or the reference on the small snapshots."""

    CAUGHT_BY = {'mention_read': ('coded-plain', 'both'), 'coded_dropped': ('coded-plain', 'both'),
                 'mentions_as_excluded': ('coded-plain', 'reference'), 'coverage_all_sources': ('coded-plain', 'reference'),
                 'filter_own_concept': ('coded-derived', 'reference')}

    def test_every_defect_is_caught(self):
        for defect, (generator, by) in self.CAUGHT_BY.items():
            violations, mismatches, _, _ = co.run_small(Path(_STATE['tmp'].name) / defect, generator, 3, first=100,
                                                        patients=20, requests=10, defect=defect)
            self.assertGreater(len(mismatches), 0, defect)
            if by == 'both':
                self.assertGreater(len(violations), 0, defect)


class DifferentialTests(unittest.TestCase):
    """The store against the reference, and against itself without mentions, on snapshots."""

    def test_the_store_equals_the_reference(self):
        for cohort, n in DIFFERENTIAL.items():
            for feature in FEATURES:
                wrong, mix = compare(cohort, feature, range(n))
                self.assertEqual(wrong[:2], [], (cohort, feature))

    def test_workers_detail_and_pruning_change_nothing(self):
        for cohort in ('fixture', 'relative'):
            store = _STATE[cohort]['store']
            for feature in FEATURES:
                for seed in range(0, 12, 4):
                    made = request(seed, cohort, feature)
                    if made is None:
                        continue
                    query, conditions, windows = made
                    base = cc.match(store, query, windows, conditions=conditions)
                    two = cc.match(store, query, windows, conditions=conditions, workers=2, chunk=7)
                    self.assertEqual(json.dumps(base), json.dumps(two), (cohort, feature, seed))
                    decided = cc.match(store, query, windows, conditions=conditions, detail='decided')
                    listed = {r['patient_id']: r for r in decided['results']}
                    for r in base['results']:
                        if r['patient_id'] in listed:
                            self.assertEqual(listed[r['patient_id']], r)
                    with patch.object(es, '_PRUNE', False):
                        off = cc.match(store, query, windows, conditions=conditions)
                    self.assertEqual(json.dumps(base), json.dumps(off), (cohort, feature, seed))

    def test_the_store_equals_itself_on_the_snapshot_without_mentions(self):
        """The 1.3 answer on the full snapshot equals the 1.2 answer on the snapshot built without letter mentions,
        result for result (FILTERED reasons included) and in `candidates`; only the plan's counts may differ."""
        compared = 0
        for cohort, n in DIFFERENTIAL.items():
            state = _STATE[cohort]
            for feature in FEATURES:
                for seed in range(n):
                    made = request(seed, cohort, feature)
                    if made is None:
                        continue
                    query, conditions, windows = made
                    a = cc.match(state['store'], query, windows, conditions=conditions)
                    b = rc.match(state['without'], query, windows, conditions=co.reference_conditions(conditions))
                    self.assertEqual(json.dumps(a['results']), json.dumps(b['results']), (cohort, feature, seed))
                    self.assertEqual((a['candidates'], a['counts']), (b['candidates'], b['counts']))
                    found = cc.candidates(state['store'], query, conditions)['patient_idx']
                    self.assertEqual(len(found), a['candidates'])
                    compared += 1
        self.assertGreater(compared, 130)

    def test_exhaustive_reference_on_a_slice(self):
        """`exhaustive_match` (every event of the filtered ingest, no filter) on the first requests of each feature, on
        the derived fixture (the relative one for rises)."""
        for feature in FEATURES:
            cohort = 'relative' if feature == 'relative' else 'fixture'
            state = _STATE[cohort]
            for seed in range(3):
                query, conditions, windows = request(seed, cohort, feature)
                got = cc.match(state['store'], query, windows, conditions=conditions)['results']
                want = state['reference'].exhaustive(query, windows, conditions)
                self.assertEqual([json.dumps(co.unfiltered(g)) for g in got], [json.dumps(w) for w in want],
                                 (feature, seed))


def one(store, query, windows, conditions):
    return {r['patient_id']: r for r in cc.match(store, query, windows, conditions=conditions)['results']}


def tiny(events, coverage=None, excluded=None):
    """A one-patient ingest on the integer grid (concepts x, y under z; d a drug)."""
    p = {'patient_id': 'P', 'clock_id': 'c', 'events': [{'evidence': {}, **e} for e in events],
         'coverage': coverage if coverage is not None else [{'source': s, 'lo': 0, 'hi': 100, 'basis': 'declared'}
                                                             for s in ('ehr', 'letters')]}
    if excluded:
        p['excluded'] = excluded
    return {'profile': es.INGEST_PROFILE, 'dataset_id': 'coded-only-named',
            'closure': {'version': '1', 'ancestors': {'x': ['z'], 'y': ['z'], 'z': [], 'd': []}}, 'patients': [p]}


CODED = {'profile': L13, 'steps': {}, 'exclude_kinds': ['mention']}


class SemanticsTests(unittest.TestCase):
    """D39's semantics on named cases."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='coded-named-')
        self.addCleanup(self.tmp.cleanup)
        self.n = 0

    def store(self, ingest):
        self.n += 1
        store = es.open_store(es.build(ingest, Path(self.tmp.name) / str(self.n)))
        self.addCleanup(store.close)
        return store

    def test_a_first_step_whose_earliest_record_is_a_mention(self):
        """A mention from birth to its letter makes the first x uncertain over every record; over coded records the
        first x is the coded one, exactly."""
        store = self.store(tiny([{'event_id': 'm', 'kind': 'mention', 'source': 'letters', 'concepts': ['x'], 'start': [0, 50]},
                                 {'event_id': 'c', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [40, 40]}]))
        q = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'x', 'occurrence': 'first'}],
             'relations': []}
        w = {'P': {'start': 30, 'end': 60}}
        stated = one(store, q, w, None)['P']
        self.assertNotEqual(stated['status'], 'exact')
        coded = one(store, q, w, CODED)['P']
        self.assertEqual((coded['status'], coded['binding']), ('exact', {'A': 'c'}))

    def test_a_left_out_mention_is_not_an_excluded_record(self):
        """Only a mention of x in the window: over coded records the answer is `none`, comparable (the ehr covers the
        window), never `not_comparable` by D18; an excluded record of x still makes it not comparable."""
        events = [{'event_id': 'm', 'kind': 'mention', 'source': 'letters', 'concepts': ['x'], 'start': [10, 20]},
                  {'event_id': 'c', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [90, 90]}]
        q = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'x'}], 'relations': []}
        w = {'P': {'start': 0, 'end': 50}}
        self.assertEqual(one(self.store(tiny(events)), q, w, None)['P']['status'], 'exact')
        self.assertEqual(one(self.store(tiny(events)), q, w, CODED)['P'], {'patient_id': 'P', 'status': 'none', 'complete': True})
        excluded = [{'source': 'ehr', 'concept': 'x', 'start': [30, 31], 'reason': 'NOT_ADMITTED'}]
        got = one(self.store(tiny(events, excluded=excluded)), q, w, CODED)['P']
        self.assertEqual((got['status'], got['reason']), ('not_comparable', 'ADMISSION_EXCLUDED_RECORDS'))

    def test_coverage_counts_only_sources_with_kept_records(self):
        """The letters cover the window but hold x only as mentions: over every record a `none` stands on their
        coverage; over coded records the letters do not record x, and the ehr, which does, does not cover the window."""
        events = [{'event_id': 'm', 'kind': 'mention', 'source': 'letters', 'concepts': ['x'], 'start': [80, 85]},
                  {'event_id': 'c', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [90, 90]}]
        coverage = [{'source': 'letters', 'lo': 0, 'hi': 100, 'basis': 'declared'},
                    {'source': 'ehr', 'lo': 70, 'hi': 100, 'basis': 'declared'}]
        q = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'x'}], 'relations': []}
        w = {'P': {'start': 0, 'end': 50}}
        store = self.store(tiny(events, coverage))
        self.assertEqual(one(store, q, w, None)['P']['status'], 'none')
        got = one(store, q, w, CODED)['P']
        self.assertEqual((got['status'], got['reason'], got['uncovered_steps']), ('not_comparable', 'NO_SOURCE_COVERAGE', ['A']))
        only = [{'event_id': 'm', 'kind': 'mention', 'source': 'letters', 'concepts': ['y'], 'start': [80, 85]},
                {'event_id': 'c', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [90, 90]}]
        q2 = {**q, 'steps': [{'id': 'A', 'concept': 'y'}]}
        got = one(self.store(tiny(only, coverage)), q2, w, CODED)['P']
        self.assertEqual((got['status'], got['reason'], got.get('unrecorded_steps')),
                         ('not_comparable', 'NO_SOURCE_RECORDS_CONCEPT', ['A']))

    def test_a_shared_variable_with_a_left_out_mention_goes_with_it(self):
        """A coded record of x with a wide range shares its start with a mention whose range is narrow: over every record
        the shared start is narrow (exact inside the window); over coded records the record's own range stands, so the
        step is only possible there."""
        events = [{'event_id': 'm', 'kind': 'mention', 'source': 'letters', 'concepts': ['y'], 'start': [20, 20],
                   'start_var': 'c.s'},
                  {'event_id': 'c', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [10, 60]}]
        q = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'x'}], 'relations': []}
        w = {'P': {'start': 15, 'end': 30}}
        store = self.store(tiny(events))
        self.assertEqual(one(store, q, w, None)['P']['status'], 'exact')
        self.assertEqual(one(store, q, w, CODED)['P']['status'], 'possible')

    def test_a_value_tag_on_a_mentions_series_row_is_not_carried(self):
        events = [{'event_id': 'm', 'kind': 'mention', 'source': 'letters', 'concepts': ['x'], 'start': [20, 20]},
                  {'event_id': 'c', 'kind': 'measurement', 'source': 'ehr', 'concepts': ['x'], 'start': [25, 25]}]
        ingest = tiny(events)
        ingest['patients'][0]['series'] = [
            {'event_id': 'm', 'item_class': 'x', 'value_lexical': '9', 'value_decimal': '9', 'value_micro': 9_000_000,
             'unit': 'kg'},
            {'event_id': 'c', 'item_class': 'x', 'value_lexical': '1', 'value_decimal': '1', 'value_micro': 1_000_000,
             'unit': 'kg'}]
        store = self.store(ingest)
        q = {'profile': tg.PROFILE_ID, 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'x'}], 'relations': []}
        w = {'P': {'start': 0, 'end': 50}}
        steps = {'A': {'unit': 'kg', 'ge': '5'}}
        self.assertEqual(one(store, q, w, {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': steps})['P']['status'], 'exact')
        self.assertEqual(one(store, q, w, {**CODED, 'steps': steps})['P']['status'], 'none')

    def test_a_count_and_a_first_blocker_read_coded_records_only(self):
        """The event-frequency shape: a count of x and a first d after it. Over every record the first d step reads a
        mention of d from birth; over coded records it reads only the coded exposure."""
        events = [{'event_id': f'c{k}', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [t, t]}
                  for k, t in enumerate((10, 12, 14))]
        events += [{'event_id': 'm', 'kind': 'mention', 'source': 'letters', 'concepts': ['d'], 'start': [0, 30]},
                   {'event_id': 'e', 'kind': 'medication_exposure', 'source': 'ehr', 'concepts': ['d'], 'start': [20, 20],
                    'end': [40, 40]}]
        store = self.store(tiny(events))
        q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'A', 'concept': 'x'},
                                                               {'id': 'B', 'concept': 'd', 'occurrence': 'first'}],
             'relations': [{'id': 'R', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 10}]}
        count = {'A': {'count': {'at_least': 3, 'within': {'span': 10}}}}
        w = {'P': {'start': 0, 'end': 50}}
        stated = one(store, q, w, {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': count})['P']
        self.assertNotEqual(stated['status'], 'exact')
        coded = one(store, q, w, {**CODED, 'steps': count})['P']
        self.assertEqual((coded['status'], coded['binding']['B']), ('exact', 'e'))


class RequestTests(unittest.TestCase):
    """Refusals, the output and the context."""

    def setUp(self):
        state = _STATE['fixture']
        self.store, self.ingest = state['store'], state['ingest']
        self.q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'A', 'concept': 'c:seizure'}], 'relations': []}
        self.w = {self.ingest['patients'][0]['patient_id']: {'start': T0, 'end': T0 + 400 * DAY}}

    def refused(self, conditions, module=cc):
        with self.assertRaises(es.StoreError) as raised:
            module.match(self.store, self.q, self.w, conditions=conditions)
        return raised.exception.code

    def test_refusals(self):
        self.assertEqual(self.refused({'profile': L13, 'steps': {}}), 'INVALID_CONDITIONS')
        self.assertEqual(self.refused({**CODED, 'other': 1}), 'INVALID_CONDITIONS')
        for kinds in ([], 'mention', ['mention', 'mention'], [''], [1], None):
            self.assertEqual(self.refused({**CODED, 'exclude_kinds': kinds}), 'INVALID_EXCLUDE_KINDS', kinds)
        self.assertEqual(self.refused({**CODED, 'exclude_kinds': ['note']}), 'EXCLUDE_KIND_UNSUPPORTED')
        self.assertEqual(self.refused({**CODED, 'exclude_kinds': ['mention', 'measurement']}), 'EXCLUDE_KIND_UNSUPPORTED')
        self.assertEqual(self.refused({**CODED, 'steps': []}), 'INVALID_CONDITIONS')
        self.assertEqual(self.refused({**CODED, 'relaxations': [{'id': 'v', 'kind': 'widen_value', 'step': 'A', 'cost': '1',
                                                                 'condition': {'unit': 'kg', 'ge': '1'}}]}),
                         'INVALID_CONDITIONS')
        self.assertEqual(self.refused({**CODED, 'steps': {'A': {'occurrence': {'nth': 0}}}}), 'INVALID_OCCURRENCE')
        # The store and the relative module alone refuse 1.3, as before D39.
        self.assertEqual(self.refused(CODED, es), 'INVALID_CONDITIONS')
        self.assertEqual(self.refused(CODED, rc), 'INVALID_CONDITIONS')
        with self.assertRaises(es.StoreError) as raised:
            cc.candidates(self.store, self.q, {**CODED, 'exclude_kinds': ['note']})
        self.assertEqual(raised.exception.code, 'EXCLUDE_KIND_UNSUPPORTED')

    def test_output_names_the_kinds_and_the_context_binds_the_module(self):
        out = cc.match(self.store, self.q, self.w, conditions=CODED)
        self.assertEqual(out['excluded_kinds'], {'kinds': ['mention'], 'rule': cc.EXCLUDE_RULE})
        self.assertEqual(out['value_conditions']['profile'], L13)
        self.assertIn(cc.MODULE_PATH, out['context']['artifacts'])
        self.assertIn(rc.MODULE_PATH, out['context']['artifacts'])
        self.assertEqual(out['context']['exclude_rule'], es._digest(cc.EXCLUDE_RULE))
        plain = cc.match(self.store, self.q, self.w)
        self.assertNotEqual(out['context_id'], plain['context_id'])
        self.assertNotIn('excluded_kinds', plain)
        nth = cc.match(self.store, self.q, self.w, conditions={**CODED, 'steps': {'A': {'occurrence': {'nth': 2}}}})
        self.assertIn('derived_steps', nth)
        self.assertEqual(nth['derived_steps']['profile'], L13)


def through_d43(store, query, windows, conditions, answer):
    """A module's answer as the dispatcher gives it: through D43's post-pass when the request has a count step."""
    if not scn.has_counts(conditions):
        return answer
    return scn.post_pass(store, query, windows, es.CHUNK_PATIENTS, conditions, answer)


class ModuleBoundaryTests(unittest.TestCase):
    """D39 lives in a module no build loads; every request without profile 1.3 is the relative module's own answer,
    through this module and through the dispatcher (with D43's post-pass for a count step)."""

    def test_the_dispatcher_routes_coded_only_requests_here(self):
        """`store_conditions` (D38's one entry point) answers a 1.3 request by this module, byte for byte, and lists 1.3."""
        state = _STATE['fixture']
        self.assertEqual(sc.CODED_PROFILE, L13)
        self.assertIn(L13, sc.profiles())
        for feature in FEATURES:
            made = request(1, 'fixture', feature)
            if made is None:
                continue
            query, conditions, windows = made
            self.assertEqual(json.dumps(sc.match(state['store'], query, windows, conditions=conditions)),
                             json.dumps(through_d43(state['store'], query, windows, conditions,
                                                    cc.match(state['store'], query, windows, conditions=conditions))))
            self.assertEqual(sc.candidates(state['store'], query, conditions), cc.candidates(state['store'], query, conditions))
            context = cc.match_context(state['store'], query, windows, conditions)
            self.assertEqual(sc.match_context(state['store'], query, windows, conditions),
                             scn.bind_context(context[0]) if scn.has_counts(conditions) else context)

    def test_every_other_request_is_the_relative_modules_answer(self):
        state = _STATE['relative']
        store, ingest = state['store'], state['ingest']
        rng = random.Random('d39/boundary')
        q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'A', 'concept': 'm:freq'},
                                                               {'id': 'B', 'concept': 'c:febrile'}],
             'relations': [{'id': 'R1', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': -400 * DAY, 'max': 400 * DAY}],
             'relaxations': [], 'budget': '1'}
        relative = {'kind': 'rise', 'factor': '2', 'unit': '/month', 'floor': '1', 'baseline': {'previous': 2, 'within': 75 * DAY}}
        for conditions in (None, {'profile': es.CONDITIONS_PROFILE, 'steps': {'A': {'unit': '/month', 'ge': '3'}}},
                           {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'A': {'count': {'at_least': 2, 'within': 'window'}}}},
                           {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'B': {'occurrence': {'nth': 2}}}},
                           {'profile': rc.CONDITIONS_PROFILE_1_2, 'steps': {'A': {'relative': relative}}}):
            windows = trc.windows_for(rng, ingest)
            for workers in (1, 2):
                answer = rc.match(store, q, windows, workers=workers, conditions=conditions)
                # Through this module and through the dispatcher (the path every caller takes since D38; with D43's
                # post-pass for a count).
                self.assertEqual(json.dumps(cc.match(store, q, windows, workers=workers, conditions=conditions)),
                                 json.dumps(answer))
                self.assertEqual(json.dumps(sc.match(store, q, windows, workers=workers, conditions=conditions)),
                                 json.dumps(through_d43(store, q, windows, conditions, answer)))
            context = rc.match_context(store, q, windows, conditions)
            for module in (cc, sc):
                self.assertEqual(module.candidates(store, q, conditions), rc.candidates(store, q, conditions))
            self.assertEqual(cc.match_context(store, q, windows, conditions), context)
            self.assertEqual(sc.match_context(store, q, windows, conditions),
                             scn.bind_context(context[0]) if scn.has_counts(conditions) else context)

    # The originals this module carries a copy of (with D39's hooks): if one changes, the copy must follow. Each is
    # pinned by the sha256 of its source.
    COPIED = {
        (rc, 'match'): '7090b8dbed6afc0f35784df33466367ab798486f04456573970d11e8c35864aa',
        (rc, '_worker_unit'): 'f5c58944a229d1a9b91bd3b00ca70507c5e9b09883faa6592a77fc3f6f7cd55b',
        (rc, '_run'): '8cb4044758cf408e05a4060c7867202da8a0184384542d539ade7e36c85b72e8',
        (rc, 'match_context'): '440b9ab9d76c51b58701f3ff9d8f4aac477d09b374cf82657a5342befcdaca93',
        (rc, '_derived_chunk'): '4acb5558dfc794ec7f1432ee9b5841d64fa9fb42c609715245be3644fbdeed61',
        (es, '_comparability'): 'b16a9f9e578f2ae7f9d6f41a496b1134190d209da26c6ec03ec9b0ee8fcac729',
        (es, '_tag_sources'): 'aa2cc0c94e75ccd2eb5b1c4024fae3928649ee23f67eb65c15fa65d53c0000ba',
    }

    # The originals `_refine`, `_kept_counts`, `_left_out` and `_sources` mirror over kept events (D39 review): if one
    # changes, the mirror must follow.
    MIRRORED = {
        (es, '_derived_patients'): 'f444d69aa9d174f998bb001615eccfcfdb2992bdbb3551e60257cfc7764501b9',
        (rc, '_derived_patients'): '33e52c7d3f3e523858ef8e40f25d13f4e38d56e66968cd2be8f6552a81266f0a',
        (es, '_tag_counts'): 'b59bc585f971f6d5f6dbd939e908b5bdeb47cf685abb7444377d622551461c6d',
        (es, '_mentions'): '3d4c71b3a61703dc6abdcad78bbf177805d77ef0e5b215e884c24fd8b33b453a',
    }
    SOURCE_CONCEPTS_SQL = '545a35583242198890a8bd2c66c617ea000be37726ccfef2bb7b2a062ca06058'

    def test_the_mirrored_store_code_has_not_changed(self):
        for (module, name), digest in self.MIRRORED.items():
            got = hashlib.sha256(inspect.getsource(getattr(module, name)).encode()).hexdigest()
            self.assertEqual(got, digest, f'{module.__name__}.{name} changed: update store_conditions_coded, whose '
                                          f'mirror over kept events must follow, then this pin')
        found = re.search(r"run\('source_concepts', \"\"\"INSERT INTO part_source_concepts.*?\"\"\"\)",
                          inspect.getsource(es._derive), re.S)
        self.assertIsNotNone(found, 'the build of source_concepts moved: update store_conditions_coded._sources')
        self.assertEqual(hashlib.sha256(found.group(0).encode()).hexdigest(), self.SOURCE_CONCEPTS_SQL,
                         'the build of source_concepts changed: update store_conditions_coded._sources, then this pin')

    def test_sources_without_left_out_kinds_are_the_stores_source_concepts(self):
        """`_sources` with no kind left out equals the store's own `source_concepts` (and `_tag_sources` for a value tag),
        on every concept of each fixture; a second call is answered from the cache, equally."""
        checked = 0
        for name in ('fixture', 'shared', 'relative', 'cohort'):
            store = _STATE[name]['store']
            concepts = sorted(c for c, in store.query('SELECT DISTINCT concept FROM concept_closure'))
            want = {}
            for source, concept in store.query('SELECT source, concept FROM source_concepts'):
                want.setdefault(concept, set()).add(source)
            tags = {}
            for item, unit in store.query('SELECT DISTINCT item_class, unit FROM series'):
                tags[f'tag:{item}:{unit}'] = (item, (unit, None, None))
            for _ in range(2):
                got = cc._sources(store, {'all': concepts}, tags, ())
                self.assertEqual({c: s for c, s in got.items() if c not in tags}, want, name)
                for tag, spec in tags.items():
                    self.assertEqual(got[tag], es._tag_sources(store, spec), (name, tag))
            checked += len(concepts)
        self.assertGreater(checked, 50)

    def test_the_copied_functions_have_not_changed(self):
        for (module, name), digest in self.COPIED.items():
            got = hashlib.sha256(inspect.getsource(getattr(module, name)).encode()).hexdigest()
            self.assertEqual(got, digest, f'{module.__name__}.{name} changed: update store_conditions_coded, whose copy '
                                          f'of it must follow, then this pin')

    def test_no_build_or_store_module_names_it(self):
        # D42 withdrew `store_conditions_gene.py` from this list: the gene module runs this module's hooks for the joint
        # profile (gene steps under coded only), by design; no build loads either.
        for path in ('patterns/execution_store.py', 'patterns/synthetic_ingest.py', 'patterns/store_conditions_relative.py',
                     'tools/scaling_build_worker.py', 'patterns/build_record.py'):
            self.assertNotIn('store_conditions_coded', (ROOT / path).read_text(encoding='utf-8'), path)


if __name__ == '__main__':
    unittest.main()
