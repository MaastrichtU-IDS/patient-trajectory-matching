"""Execution store, stage 1: tables against the pure-Python reference, the candidate filter
against exhaustive matching, determinism, planted defects, the manifest and ingest refusals.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from jsonschema import Draft202012Validator

from . import execution_store as es
from . import execution_store_reference as ref
from . import synthetic_ingest as si
from . import trajectory_graph as tg
from .pro_solid import ROOT

DAY = 86_400_000_000
T0 = si.micros('2020-01-01T00:00')
GENERATOR = ROOT / 'demos/mockups/synthetic/generate.py'
SEEDS = 300
COSTS = ('0.25', '0.5', '0.75', '1')
MATCHED = ('exact', 'possible', 'relaxed', 'unresolved')


def ev(eid, concepts, start, end=None, source='ehr', kind='condition', **evidence):
    out = {'event_id': eid, 'kind': kind, 'source': source, 'concepts': list(concepts), 'start': list(start),
           'evidence': {'record': eid, **evidence}}
    if end is not None:
        out['end'] = list(end)
    return out


def cover(source, lo, hi, basis='declared'):
    return {'source': source, 'lo': lo, 'hi': hi, 'basis': basis}


def day(n):
    return T0 + n * DAY


def fixture():
    """Small hand-built ingest: a diamond in the closure, an event with two direct concepts sharing
    ancestors, a closed and an ongoing exposure, a patient with no events, missing coverage."""
    ancestors = {'seizure': [], 'febrile_seizure': ['seizure'], 'epilepsy': [], 'dee': ['epilepsy'],
                 'generalized_epilepsy': ['epilepsy'], 'dravet': ['dee', 'epilepsy', 'generalized_epilepsy'],
                 'antiseizure': [], 'sodium_channel_blocker': [],
                 'carbamazepine': ['antiseizure', 'sodium_channel_blocker'], 'valproate': ['antiseizure'],
                 'seizure_frequency': [], 'unused': []}
    span = (day(-30), day(400))
    def patient(pid, events, coverage):
        return {'patient_id': pid, 'clock_id': f'patient_{pid}', 'events': events, 'coverage': coverage}
    return {'profile': es.INGEST_PROFILE, 'dataset_id': 'fixture', 'closure': {'version': 'fixture-1', 'ancestors': ancestors},
            'patients': [
                patient('P1', [ev('fs', ['febrile_seizure'], (day(0), day(1) - 1)),
                               ev('cbz', ['carbamazepine'], (day(30), day(30)), (day(60), day(70)), 'pharmacy', 'exposure'),
                               ev('sz', ['seizure'], (day(90), day(120)))],
                        [cover('ehr', *span), cover('pharmacy', *span)]),
                patient('P2', [ev('dx', ['dee', 'dravet'], (day(10), day(10)), note='Résumé de sortie'),
                               ev('vpa', ['valproate'], (day(5), day(5)), (day(5), None), 'pharmacy', 'exposure')],
                        [cover('ehr', *span), cover('pharmacy', *span)]),
                patient('P3', [ev('fs', ['febrile_seizure'], (day(0), day(0)))], [cover('ehr', *span)]),
                patient('P4', [], []),
                patient('P5', [ev('fs', ['febrile_seizure'], (day(0), day(0))),
                               ev('sf', ['seizure_frequency'], (day(31), day(59)), source='diary', kind='diary_summary')],
                        [cover('ehr', *span), cover('pharmacy', *span), cover('diary', day(20), day(200))]),
                patient('P6', [ev('fs', ['febrile_seizure'], (day(0), day(0))),
                               ev('vpa', ['valproate'], (day(20), day(20)), (day(80), day(80)), 'pharmacy', 'exposure')],
                        [cover('ehr', *span), cover('pharmacy', day(10), day(90)), cover('pharmacy', day(200), day(300))]),
            ]}


FIXTURE_QUERY = {'profile': tg.PROFILE_ID, 'scope': 'any',
                 'steps': [{'id': 'A', 'concept': 'febrile_seizure', 'occurrence': 'first'},
                           {'id': 'B', 'concept': 'carbamazepine'}],
                 'relations': [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 60 * DAY}],
                 'relaxations': [{'id': 'wB', 'kind': 'widen_concept', 'step': 'B', 'concept': 'antiseizure', 'cost': '0.5'}],
                 'budget': '1'}


_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='execution-store-')
    tmp = Path(_STATE['tmp'].name)
    started = time.perf_counter()
    subprocess.run([sys.executable, str(GENERATOR), '--output', str(tmp / 'cohort.json')], check=True,
                   capture_output=True, cwd=ROOT)
    cohort = json.loads((tmp / 'cohort.json').read_text(encoding='utf-8'))
    _STATE['cohort'] = cohort
    _STATE['ingest'] = si.synthetic_ingest(cohort)
    # The cohort as the converter gave it before D30 (letter mentions timed by their letter), only to reproduce
    # digests recorded before then.
    _STATE['legacy'] = si.synthetic_ingest(cohort, mention_time='letter')
    _STATE['generated'] = time.perf_counter() - started
    started = time.perf_counter()
    _STATE['snapshot'] = es.build(_STATE['ingest'], tmp / 'store')
    _STATE['built'] = time.perf_counter() - started
    _STATE['store'] = es.open_store(_STATE['snapshot'], verify='content')
    _STATE['reference'] = ref.build_tables(_STATE['ingest'])


def tearDownModule():
    if 'store' in _STATE:
        _STATE['store'].close()
    _STATE['tmp'].cleanup()


def table_mismatches(store, reference):
    """Tables whose rows differ, with a few example rows from each side."""
    out = {}
    for name in es.TABLES:
        mine, theirs = store.rows(name), reference[name]
        if mine != [tuple(r) for r in theirs]:
            a, b = set(mine), set(map(tuple, theirs))
            out[name] = {'store_only': sorted(a - b, key=str)[:3], 'reference_only': sorted(b - a, key=str)[:3]}
    return out


# Seeded random queries over the synthetic cohort.

def concept_pool(reference):
    seen = Counter(r[0] for r in reference['concept_postings'])
    return sorted(c for c, n in seen.items() if n >= 2)


def random_windows(rng, ingest):
    out = {}
    for p in ingest['patients']:
        if not p['coverage'] or rng.random() < 0.05:
            out[p['patient_id']] = None
            continue
        lo, hi = min(c['lo'] for c in p['coverage']), max(c['hi'] for c in p['coverage'])
        width = max(hi - lo, DAY)
        if rng.random() < 0.08:  # a window after every source stopped recording
            start = hi + rng.randint(30, 400) * DAY
        else:
            start = lo + int(width * rng.uniform(-0.1, 0.6))
        out[p['patient_id']] = {'start': start, 'end': start + int(width * rng.uniform(0.2, 1.0))}
    return out


def random_query(rng, pool, ingest):
    """A 2-4 step query shaped around one anchor patient's events, so that matches occur: each
    step takes the concept (or an ancestor) of one of that patient's events, or a decoy concept
    with a widening back to it; each gap is set to fit, fit tightly, miss (with an extend_max
    that may recover it) or stay open. Other patients match or not by chance."""
    ancestors = ingest['closure']['ancestors']
    anchor = rng.choice([p for p in ingest['patients'] if len(p['events']) >= 4])
    k = rng.randint(2, 4)
    chosen = rng.sample(anchor['events'], k)
    steps, relaxations = [], []
    for i, e in enumerate(chosen):
        own = rng.choice(e['concepts'])
        concept = rng.choice([own, *ancestors[own]]) if rng.random() < 0.85 else rng.choice(pool)
        step = {'id': f'S{i}', 'concept': concept, 'occurrence': rng.choice(('any', 'any', 'any', 'first', 'last'))}
        if rng.random() < 0.2:  # decoy: only the widening reaches the anchor's event
            relaxations.append({'id': f'wS{i}', 'kind': 'widen_concept', 'step': step['id'], 'concept': concept,
                                'cost': rng.choice(COSTS)})
            step['concept'] = rng.choice(pool)
        elif rng.random() < 0.3:
            relaxations.append({'id': f'wS{i}', 'kind': 'widen_concept', 'step': step['id'],
                                'concept': rng.choice(sorted(ancestors[concept] or pool)), 'cost': rng.choice(COSTS)})
        steps.append(step)
    relations = []
    def relation(a, b):
        r = {'id': f'R{len(relations) + 1}', 'from': a, 'to': b}
        if rng.random() < 0.1:
            return {**r, 'kind': 'during', 'grace': rng.choice((0, 7, 30)) * DAY}
        gap = chosen[int(b[1:])]['start'][0] - chosen[int(a[1:])]['start'][0]
        mode = rng.choice(('fit', 'fit', 'tight', 'miss', 'open'))
        if mode == 'fit':
            r.update(min=gap - rng.randint(0, 60) * DAY, max=gap + rng.randint(0, 60) * DAY)
        elif mode == 'tight':
            r.update(min=gap, max=gap)
        elif mode == 'miss':
            r.update(min=gap - rng.randint(60, 400) * DAY, max=gap - rng.randint(1, 50) * DAY)
            if rng.random() < 0.7:
                relaxations.append({'id': f"x{r['id']}", 'kind': 'extend_max', 'relation': r['id'],
                                    'max': r['max'] + rng.choice((60, 400, 2000)) * DAY, 'cost': rng.choice(COSTS)})
        elif rng.random() < 0.5:
            r['min'] = 0
        return {**r, 'kind': 'after'}
    for i in range(1, k):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        relations.append(relation(*((b, a) if rng.random() < 0.3 else (a, b))))
    if k >= 3 and rng.random() < 0.3:  # a cycle
        relations.append(relation(steps[0]['id'], steps[-1]['id']))
    if rng.random() < 0.15:
        relations.append({'id': f'R{len(relations) + 1}', 'kind': 'after', 'from': 'START',
                          'to': rng.choice(steps)['id'], 'min': 0})
    return {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
            'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1', '1')),
            'search_budget': rng.choice((1, 40, 300))}


def differential(store, ingest, reference, seeds=SEEDS):
    """Store path against exhaustive matching over every patient.

    Returns (mismatches, dropped, status mix, filtered count). A mismatch is a patient whose store
    status differs from the exhaustive one or, if the store ran the engine on them, whose result
    differs at all. Dropped are patients outside the candidate set whom exhaustive matching finds
    exact, possible, relaxed or unresolved: soundness means there are none."""
    pool = concept_pool(reference)
    ids = {i: p for i, p, *_ in store.rows("patients")}
    mismatches, dropped, mix, filtered = [], [], Counter(), 0
    for seed in range(seeds):
        rng = random.Random(seed)
        query, windows = random_query(rng, pool, ingest), random_windows(rng, ingest)
        kept = {ids[i] for i in es.candidates(store, query)['patient_idx']}
        got = es.match(store, query, windows)['results']
        want = ref.exhaustive_match(ingest, query, windows)
        for g, w in zip(got, want, strict=True):
            assert g['patient_id'] == w['patient_id'], (seed, g['patient_id'], w['patient_id'])
            mix[w['status']] += 1
            if w['patient_id'] not in kept:
                filtered += 1
                if w['status'] in MATCHED:
                    dropped.append((seed, w))
            if g['status'] != w['status'] or (g.get('reason') != 'FILTERED' and g != w):
                mismatches.append((seed, g, w))
    return mismatches, dropped, mix, filtered


def engine_runs(store, query, windows):
    """The candidates with a window that time pruning leaves to the engine (D25)."""
    pq = tg.PreparedQuery(query)
    ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
    by_idx = {ids[pid]: w for pid, w in windows.items()}
    live = [i for i in es._candidates(store, pq)['patient_idx'] if by_idx.get(i) is not None]
    return sorted(set(live) - es._prune(store, pq, live, by_idx))


def shared_variable_cohort(ingest, seed):
    """The synthetic cohort with shared time variables and proper exposures added at random (after a D24
    reviewer's fuzz script): in about 60% of patients, some closed exposures become proper and one to six
    events take another event's start or end variable, with ranges moved to meet it; a change the ingest
    would refuse is undone."""
    ingest, rng = deepcopy(ingest), random.Random(seed)
    ancestors = ingest['closure']['ancestors']
    for p in ingest['patients']:
        if rng.random() >= 0.6 or len(p['events']) < 2:
            continue
        evs = p['events']
        for e in evs:
            if 'end' in e and e['end'][1] is not None and e['end'][1] > e['start'][0] and rng.random() < 0.5:
                e['proper'] = True
        for _ in range(rng.randint(1, 6)):
            a, b = rng.sample(evs, 2)
            before = deepcopy(b)
            part = 'end' if 'end' in a and a['end'][1] is not None and rng.random() < 0.6 else 'start'
            name = a.get(part + '_var', a['event_id'] + ('.f' if part == 'end' else '.s'))
            mine = 'end' if 'end' in b and rng.random() < 0.3 else 'start'
            b[mine + '_var'] = name
            lo, hi = a[part]
            if mine == 'start':
                b['start'] = [lo, hi]
                if 'end' in b:
                    b['end'] = [max(b['end'][0], lo), None if b['end'][1] is None else max(b['end'][1], hi + 1)]
            else:
                b['end'] = [max(lo, b['start'][0]), max(hi, b['start'][0] + 1)]
            try:
                es.validate_patient(p, ancestors)
            except es.IngestError:
                b.clear()
                b.update(before)
    return ingest


class TableTests(unittest.TestCase):
    def test_every_synthetic_table_equals_the_reference_row_for_row(self):
        self.assertEqual(table_mismatches(_STATE['store'], _STATE['reference']), {})
        counts = {n: len(r) for n, r in _STATE['reference'].items()}
        self.assertEqual(counts['patients'], 48)
        self.assertGreater(counts['events'], 8000)
        self.assertGreater(counts['concept_postings'], counts['patients'])

    def test_every_fixture_table_equals_the_reference_row_for_row(self):
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(fixture(), tmp), verify='content') as store:
            self.assertEqual(table_mismatches(store, ref.build_tables(fixture())), {})

    def test_an_empty_partitioned_table_is_written_and_read_back(self):
        g = fixture()
        for p in g['patients']:
            p['coverage'] = []
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp), verify='content') as store:
            self.assertEqual(store.manifest['tables']['coverage']['rows'], 0)
            self.assertEqual(table_mismatches(store, ref.build_tables(g)), {})

    def test_manifest_digests_equal_the_reference_digests_table_by_table_and_bucket_by_bucket(self):
        cases = [('synthetic', _STATE['ingest'], _STATE['store'].manifest, _STATE['reference'])]
        with tempfile.TemporaryDirectory() as tmp:
            g = fixture()
            g['patients'][0]['excluded'] = [{'source': 'ehr', 'concept': 'seizure', 'start': [day(3), day(4)],
                                             'reason': 'DROPPED'}] * 2
            cases.append(('fixture', g, json.loads((es.build(g, tmp) / 'manifest.json').read_text()),
                          ref.build_tables(g)))
        for label, ingest, manifest, tables in cases:
            want = ref.table_digests(tables)
            for name in es.TABLES:
                recorded = manifest['tables'][name]
                with self.subTest(case=label, table=name):
                    self.assertEqual(recorded['rows'], len(tables[name]))
                    self.assertEqual(recorded['buckets'], want[name]['buckets'])
                    self.assertEqual(recorded['content_digest'], want[name]['content_digest'])
                    self.assertEqual(list(recorded['buckets']), es.bucket_labels(name))
            self.assertEqual(manifest['input_digest'], ref.input_digest(ingest), label)
        self.assertTrue(_STATE['store'].verify('content'))
        self.assertEqual(_STATE['store'].verified, 'content')

    def test_postings_count_each_event_once_through_the_closure(self):
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(fixture(), tmp), verify='content') as store:
            idx = {pid: i for i, pid, *_ in store.rows("patients")}
            postings = {(c, p): (n, lo, hi, end) for c, p, n, lo, hi, end in store.rows('concept_postings')}
            # One event coded dee and dravet: one posting each for dee and epilepsy, not two.
            self.assertEqual(postings[('dee', idx['P2'])], (1, day(10), day(10), day(10)))
            self.assertEqual(postings[('epilepsy', idx['P2'])], (1, day(10), day(10), day(10)))
            self.assertEqual(postings[('seizure', idx['P1'])], (2, day(0), day(120), day(120)))
            # The latest end (D26): an exposure's recorded end; none when an exposure is ongoing.
            self.assertEqual(postings[('antiseizure', idx['P1'])], (1, day(30), day(30), day(70)))
            self.assertEqual(postings[('antiseizure', idx['P2'])], (1, day(5), day(5), None))
            self.assertNotIn('unused', {c for c, _ in postings})
            events = {(store.rows('patients')[i][1], e): (elo, ehi, ongoing)
                      for i, e, _, _, _, _, _, elo, ehi, ongoing, *_ in store.rows("events")}
            self.assertEqual(events[('P2', 'vpa')], (day(5), None, True))
            self.assertEqual(events[('P1', 'fs')], (None, None, False))
            self.assertEqual(sorted(r[2] for r in store.rows('patients')), sorted(ref.bucket_of(p) for p in idx))


class MatchingTests(unittest.TestCase):
    def test_fixture_statuses_including_filtered_and_not_comparable(self):
        windows = {f'P{i}': {'start': day(-365), 'end': day(365)} for i in range(1, 7)}
        windows['P2'] = None
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(fixture(), tmp), verify='content') as store:
            out = es.match(store, FIXTURE_QUERY, windows)
            by = {r['patient_id']: r for r in out['results']}
            self.assertEqual(by['P1']['status'], 'exact')
            self.assertEqual(by['P2'], {'patient_id': 'P2', 'status': 'excluded', 'reason': 'NO_INDEX_WINDOW'})
            self.assertEqual(by['P3'], {'patient_id': 'P3', 'status': 'not_comparable', 'reason': 'NO_SOURCE_COVERAGE',
                                        'uncovered_steps': ['B']})
            self.assertEqual(by['P4']['uncovered_steps'], ['A', 'B'])
            self.assertEqual(by['P5'], {'patient_id': 'P5', 'status': 'none', 'reason': 'FILTERED', 'complete': True})
            self.assertEqual((by['P6']['status'], by['P6']['relaxations']), ('relaxed', ['wB']))
            # B (carbamazepine or, widened, antiseizure) is rarer than A, so it is intersected first;
            # P2 has an antiseizure but no febrile seizure, so only P1 and P6 survive.
            self.assertEqual([(s['step'], s['patients']) for s in out['plan']], [('B', 3), ('A', 4)])
            self.assertEqual(out['candidates'], 2)
            want = ref.exhaustive_match(fixture(), FIXTURE_QUERY, windows)
            self.assertEqual([r['status'] for r in out['results']], [r['status'] for r in want])
            self.assertEqual([r for r in out['results'] if r.get('reason') != 'FILTERED'],
                             [w for r, w in zip(out['results'], want) if r.get('reason') != 'FILTERED'])
            # The result says what it depends on.
            self.assertEqual((out['verified'], out['context']['verified'], out['coverage_rule']),
                             ('content', 'content', es.COVERAGE_RULE))
            context = out['context']
            self.assertEqual(context['snapshot_id'], store.snapshot_id)
            self.assertEqual(set(context['artifacts']), set(es.ENGINE_ARTIFACTS))
            self.assertEqual(out['context_id'], es.match_context(store, FIXTURE_QUERY, windows)[1])
            self.assertNotEqual(out['context_id'], es.match(store, FIXTURE_QUERY, {**windows, 'P2': windows['P1']})['context_id'])
            # A concept no source records anywhere: not comparable for its own reason.
            q = deepcopy(FIXTURE_QUERY)
            q['steps'][1]['concept'] = 'unused'
            q['relaxations'] = []
            by = {r['patient_id']: r for r in es.match(store, q, windows)['results']}
            self.assertEqual(by['P1'], {'patient_id': 'P1', 'status': 'not_comparable', 'reason': 'NO_SOURCE_RECORDS_CONCEPT',
                                        'uncovered_steps': ['B'], 'unrecorded_steps': ['B']})
            self.assertEqual([by[p] for p in sorted(by)], ref.exhaustive_match(fixture(), q, windows))
            self.assertIn(('pharmacy', 'antiseizure', 'inferred_from_records'), store.rows('source_concepts'))

    def test_excluded_records_turn_a_none_into_not_comparable(self):
        """Records the source held but the ingest left out: a `none` that could have used them is not a non-match."""
        ingest = fixture()
        ingest['closure']['bases'] = {c: ('drug_class' if c in ('antiseizure', 'sodium_channel_blocker') else 'condition_or_drug')
                                      for c in ingest['closure']['ancestors']}
        p5 = next(p for p in ingest['patients'] if p['patient_id'] == 'P5')
        held = {'source': 'pharmacy', 'concept': 'carbamazepine', 'start': [day(500), day(500)], 'reason': 'NOT_ADMITTED'}
        p5['excluded'] = [held, dict(held)]
        windows = {f'P{i}': {'start': day(-365), 'end': day(365)} for i in range(1, 7)}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, tmp), verify='content') as store:
            tables = ref.build_tables(ingest)
            for name in es.TABLES:
                self.assertEqual(store.rows(name), [tuple(r) for r in tables[name]], name)
            self.assertEqual(store.rows('excluded_records'), [(4, 'pharmacy', 'carbamazepine', day(500), day(500), 'NOT_ADMITTED', 2)])
            self.assertIn(('carbamazepine', 'antiseizure', 'declared', 'fixture-1', 'condition_or_drug', 'drug_class'),
                          store.rows('concept_closure'))
            for scope, status in (('any', 'not_comparable'), ('within', 'none')):
                q = {**FIXTURE_QUERY, 'scope': scope}
                out = es.match(store, q, windows)
                self.assertEqual(out['exclusion_rule'], es.EXCLUSION_RULE)
                p5_result = next(r for r in out['results'] if r['patient_id'] == 'P5')
                self.assertEqual(p5_result['status'], status, scope)  # day 500 lies outside the window
                if status == 'not_comparable':
                    self.assertEqual(p5_result, {'patient_id': 'P5', 'status': 'not_comparable',
                                                 'reason': 'ADMISSION_EXCLUDED_RECORDS', 'excluded_steps': ['B']})
                want = ref.exhaustive_match(ingest, q, windows)
                self.assertEqual([r['status'] for r in out['results']], [w['status'] for w in want])
        with self.assertRaises(es.IngestError) as caught:
            es.validate_ingest({**ingest, 'closure': {**ingest['closure'], 'bases': {'seizure': 'x'}}})
        self.assertEqual(caught.exception.code, 'INVALID_CONCEPT_BASES')

    def admission_case(self, events, excluded, steps, relaxations=(), labels=None):
        """One patient, items X and Y, `after A->B` within 60 minutes; the store's result and the reference's."""
        m = lambda n: T0 + n * 60_000_000
        ingest = {'profile': es.INGEST_PROFILE, 'dataset_id': 'admission', 'provenance': {'labels': labels} if labels else {},
                  'closure': {'version': 'v', 'ancestors': {'X': [], 'Y': [], 'K': []},
                              'bases': {'X': 'source_item_class', 'Y': 'source_item_class', 'K': 'ontology_closure'}},
                  'patients': [{'patient_id': 'P', 'clock_id': 'c',
                                'events': [ev(eid, [c, 'K'], (m(lo), m(hi))) for eid, c, lo, hi in events],
                                'excluded': [{'source': 'ehr', 'concept': c, 'start': [m(t), m(t)], 'reason': 'DROPPED'}
                                             for c, t in excluded],
                                'coverage': [cover('ehr', m(-1000), m(1000))]}]}
        query = {'profile': tg.PROFILE_ID, 'steps': steps,
                 'relations': [{'id': 'R', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 60 * 60_000_000}],
                 'relaxations': list(relaxations), 'budget': '1'}
        windows = {'P': {'start': m(-1000), 'end': m(1000)}}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, tmp), verify='content') as store:
            got = es.match(store, query, windows)['results'][0]
        want = ref.exhaustive_match(ingest, query, windows)[0]
        self.assertEqual(got, want)
        return got, ingest, query

    def test_an_excluded_record_can_supply_the_unrelaxed_binding_a_relaxed_result_denies(self):
        extend = {'id': 'wider', 'kind': 'extend_max', 'relation': 'R', 'max': 200 * 60_000_000, 'cost': '0.5'}
        steps = [{'id': 'A', 'concept': 'X'}, {'id': 'B', 'concept': 'Y'}]
        got, _, _ = self.admission_case([('x', 'X', 0, 0), ('y', 'Y', 100, 100)], [], steps, [extend])
        self.assertEqual(got['status'], 'relaxed')
        got, _, _ = self.admission_case([('x', 'X', 0, 0), ('y', 'Y', 100, 100)], [('X', 50)], steps, [extend])
        self.assertEqual(got, {'patient_id': 'P', 'status': 'unresolved', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                               'relaxed_match': {'relaxations': ['wider'], 'cost': '0.5', 'certainty': 'exact',
                                                 'binding': {'A': 'x', 'B': 'y'}, 'method': got['relaxed_match']['method']},
                               'excluded_steps': ['A'], 'complete': False})

    def test_an_excluded_record_can_displace_a_first_or_last_target(self):
        first = [{'id': 'A', 'concept': 'X', 'occurrence': 'first'}, {'id': 'B', 'concept': 'Y'}]
        events = [('x', 'X', 100, 100), ('y', 'Y', 130, 130)]
        self.assertEqual(self.admission_case(events, [], first)[0]['status'], 'exact')
        got, _, _ = self.admission_case(events, [('X', 10)], first)
        self.assertEqual(got, {'patient_id': 'P', 'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                               'excluded_steps': ['A']})
        # A later excluded X cannot be first, so the exact result stands.
        self.assertEqual(self.admission_case(events, [('X', 120)], first)[0]['status'], 'exact')
        last = [{'id': 'A', 'concept': 'X'}, {'id': 'B', 'concept': 'Y', 'occurrence': 'last'}]
        self.assertEqual(self.admission_case(events, [('Y', 500)], last)[0]['status'], 'not_comparable')
        self.assertEqual(self.admission_case(events, [('Y', 50)], last)[0]['status'], 'exact')
        # Occurrence `any`: an extra record only adds bindings, so exact stands.
        anyx = [{'id': 'A', 'concept': 'X'}, {'id': 'B', 'concept': 'Y'}]
        self.assertEqual(self.admission_case(events, [('X', 10)], anyx)[0]['status'], 'exact')

    def first_x(self, patients, scope, windows):
        """Store and reference results for the one-step query `first X` over hand-built patients."""
        ingest = {'profile': es.INGEST_PROFILE, 'dataset_id': 'first-x',
                  'closure': {'version': 'v', 'ancestors': {'X': []}},
                  'patients': [{'patient_id': pid, 'clock_id': 'c', 'coverage': [cover('ehr', day(0), day(400))],
                                'events': [ev(eid, ['X'], (day(d), day(d))) for eid, d in events],
                                'excluded': [{'source': 'ehr', 'concept': 'X', 'start': [day(d), day(d)], 'reason': 'DROPPED'}
                                             for d in excluded]}
                               for pid, events, excluded in patients]}
        query = {'profile': tg.PROFILE_ID, 'scope': scope, 'steps': [{'id': 'A', 'concept': 'X', 'occurrence': 'first'}]}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, tmp), verify='content') as store:
            got = es.match(store, query, windows)['results']
        self.assertEqual(got, ref.exhaustive_match(ingest, query, windows))
        return {r['patient_id']: r for r in got}

    def test_an_excluded_record_the_scope_rejects_can_still_displace_a_first_target(self):
        # The engine picks the first X among all X records, then applies the scope: the true first X is
        # on day 50, outside the window, so with every record the answer is none, not exact.
        window = {'P': {'start': day(100), 'end': day(200)}}
        self.assertEqual(self.first_x([('P', [('x150', 150)], [])], 'within', window)['P']['status'], 'exact')
        got = self.first_x([('P', [('x150', 150)], [50])], 'within', window)['P']
        self.assertEqual(got, {'patient_id': 'P', 'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                               'excluded_steps': ['A']})

    def test_displacement_reads_each_patients_own_event_times(self):
        # Event ids are unique within a patient only: P2's e1 on day 10 must not stand in for P1's e1.
        windows = {'P1': {'start': day(0), 'end': day(400)}, 'P2': {'start': day(0), 'end': day(400)}}
        got = self.first_x([('P1', [('e1', 150)], [120]), ('P2', [('e1', 10)], [])], 'any', windows)
        self.assertEqual((got['P1']['status'], got['P1']['reason']), ('not_comparable', 'ADMISSION_EXCLUDED_RECORDS'))
        self.assertEqual(got['P2']['status'], 'exact')

    def test_an_excluded_record_may_make_a_possible_result_exact(self):
        steps = [{'id': 'A', 'concept': 'X'}, {'id': 'B', 'concept': 'Y'}]
        events = [('x', 'X', 0, 0), ('y', 'Y', 50, 70)]
        self.assertEqual(self.admission_case(events, [], steps)[0]['status'], 'possible')
        got, _, _ = self.admission_case(events, [('Y', 30)], steps)
        self.assertEqual((got['status'], got['complete'], got['note'], got['excluded_steps']),
                         ('possible', False, 'EXCLUDED_RECORDS_MAY_MAKE_EXACT', ['B']))

    def test_structural_concepts_are_refused_where_excluded_records_cannot_carry_them(self):
        steps = [{'id': 'A', 'concept': 'K'}, {'id': 'B', 'concept': 'Y'}]
        labels = {'structural_concept_basis': 'ontology_closure'}
        with self.assertRaises(es.StoreError) as caught:
            self.admission_case([('x', 'X', 0, 0)], [('X', 5)], steps, labels=labels)
        self.assertEqual(caught.exception.code, 'STRUCTURAL_CONCEPT_EXCLUSION_UNKNOWN')
        # Without excluded records there is nothing they could hide.
        self.assertEqual(self.admission_case([('x', 'X', 0, 0), ('y', 'Y', 30, 30)], [], steps, labels=labels)[0]['status'],
                         'exact')

    def test_store_matches_exhaustive_matching_and_never_drops_a_match(self):
        started = time.perf_counter()
        mismatches, dropped, mix, filtered = differential(_STATE['store'], _STATE['ingest'], _STATE['reference'])
        elapsed = time.perf_counter() - started
        self.assertEqual(dropped[:3], [])
        self.assertEqual(mismatches[:3], [])
        # Not vacuous: every status the store reports occurs, and the filter does real work.
        for status in ('exact', 'possible', 'relaxed', 'none', 'not_comparable', 'excluded', 'unresolved'):
            self.assertGreater(mix[status], 0, status)
        self.assertGreater(filtered, sum(mix.values()) // 4)
        print(f'\n  differential: {SEEDS} queries, {sum(mix.values())} patient results, '
              f'{dict(sorted(mix.items()))}, {filtered} filtered, {elapsed:.1f}s '
              f"(cohort generated and converted in {_STATE['generated']:.1f}s, snapshot built in {_STATE['built']:.1f}s)",
              file=sys.stderr)

    def test_unknown_query_concepts_and_window_patients_are_refused(self):
        store = _STATE['store']
        q = deepcopy(FIXTURE_QUERY)
        q['steps'][0]['concept'] = 'not_a_concept'
        with self.assertRaises(es.StoreError) as caught:
            es.candidates(store, q)
        self.assertEqual(caught.exception.code, 'UNKNOWN_QUERY_CONCEPT')
        q = deepcopy(FIXTURE_QUERY)
        q['relaxations'][0]['concept'] = 'not_a_concept'
        with self.assertRaises(es.StoreError) as caught:
            es.match(store, q, {})
        self.assertEqual(caught.exception.code, 'UNKNOWN_QUERY_CONCEPT')
        with self.assertRaises(es.StoreError) as caught:
            es.match(store, FIXTURE_QUERY, {'NOBODY': None})
        self.assertEqual(caught.exception.code, 'UNKNOWN_WINDOW_PATIENT')


class MatcherScalingTests(unittest.TestCase):
    """The chunked, prepared matcher (D19 step 3) decides exactly what the one-shot matcher did."""

    FIXTURE = ROOT / 'patterns/fixtures/execution_store_match_digests.json'

    @staticmethod
    def case(seed):
        rng = random.Random(seed)
        pool, ingest = concept_pool(_STATE['reference']), _STATE['ingest']
        return random_query(rng, pool, ingest), random_windows(rng, ingest)

    @staticmethod
    def legacy_case(seed):
        """The case `case` drew from the cohort as converted before D30 (the queries are shaped around event times)."""
        rng = random.Random(seed)
        pool, ingest = concept_pool(_STATE['reference']), _STATE['legacy']
        return random_query(rng, pool, ingest), random_windows(rng, ingest)

    def test_output_is_byte_identical_to_the_matcher_before_chunking(self):
        """`patterns/fixtures/execution_store_match_digests.json` holds, for each of the 300 differential seeds, the
        sha256 of `json.dumps({plan, candidates, counts, results}, sort_keys=True)` as the one-shot matcher
        (commit 7dc3364) produced it on this module's synthetic store. A fixture, not a kept copy of the old
        code: the old path stays unmaintained nowhere, and any later change that moves a result fails here.
        The snapshot id and the engine-file digests in `context` change with the code, so they are excluded."""
        want = json.loads(self.FIXTURE.read_text())
        self.assertEqual(len(want), SEEDS)
        wrong = []
        for seed in range(SEEDS):
            out = es.match(_STATE['store'], *self.case(seed))
            body = {k: out[k] for k in ('plan', 'candidates', 'counts', 'results')}
            if hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest() != want[str(seed)]:
                wrong.append(seed)
        self.assertEqual(wrong, [])

    def test_the_chunk_size_changes_nothing(self):
        for seed in range(40):
            q, w = self.case(seed)
            whole = json.dumps(es.match(_STATE['store'], q, w), sort_keys=True)
            for chunk in (1, 7):
                self.assertEqual(json.dumps(es.match(_STATE['store'], q, w, chunk=chunk), sort_keys=True), whole, (seed, chunk))

    def test_decided_detail_agrees_with_full(self):
        store, seen = _STATE['store'], Counter()
        for seed in range(80):
            q, w = self.case(seed)
            full, decided = es.match(store, q, w), es.match(store, q, w, detail='decided')
            self.assertEqual({k: v for k, v in decided.items() if k not in ('results', 'detail', 'implicit')},
                             {k: v for k, v in full.items() if k != 'results'}, seed)
            self.assertEqual(decided['detail'], 'decided')
            by_id = {r['patient_id']: r for r in full['results']}
            for r in decided['results']:
                self.assertEqual(r, by_id[r['patient_id']])
            listed = {r['patient_id'] for r in decided['results']}
            order = [r['patient_id'] for r in full['results'] if r['patient_id'] in listed]
            self.assertEqual([r['patient_id'] for r in decided['results']], order)
            left_out = [r for r in full['results'] if r['patient_id'] not in listed]
            filtered = sum(1 for r in left_out if r['status'] == 'none' and r['reason'] == 'FILTERED')
            windowless = sum(1 for r in left_out if r['status'] == 'excluded')
            self.assertEqual(len(left_out), filtered + windowless)
            self.assertEqual(decided['implicit'], {'FILTERED': filtered, 'NO_INDEX_WINDOW': windowless})
            # every candidate with a window, and every patient a rule changed, is listed
            kept = {pid for pid, r in by_id.items()
                    if r['status'] in ('exact', 'possible', 'relaxed', 'unresolved', 'not_comparable')}
            self.assertLessEqual(kept, listed)
            for r in full['results']:
                seen[r['status']] += 1
        self.assertTrue({'exact', 'not_comparable', 'none', 'excluded'} <= set(seen))
        with self.assertRaises(es.StoreError) as caught:
            es.match(store, *self.case(0), detail='some')
        self.assertEqual(caught.exception.code, 'INVALID_DETAIL')

    def test_windows_are_refused_with_the_codes_the_engine_gave(self):
        store = _STATE['store']
        pid = store.query('SELECT patient_id FROM patients ORDER BY patient_idx')[0][0]
        bad = [{'start': 5, 'end': 4}, {'start': 1.0, 'end': 2}, {'start': True, 'end': 5}, {'start': 1},
               {'start': 1, 'end': 2, 'x': 3}, {'start': '1', 'end': 2}, [1, 2], 7]
        for window in bad:
            with self.subTest(window=window):
                with self.assertRaises(tg.TrajectoryError) as expected:
                    tg.validate_patient({'patient_id': pid, 'window': window})
                with self.assertRaises(tg.TrajectoryError) as got:
                    es.match(store, FIXTURE_QUERY, {pid: window})
                self.assertEqual(str(got.exception), str(expected.exception))
        self.assertEqual(es.match(store, FIXTURE_QUERY, {pid: None})['results'][0]['status'], 'excluded')

    def test_temporary_tables_are_dropped_after_a_match_and_after_a_failure(self):
        store = _STATE['store']
        q, w = self.case(3)
        es.match(store, q, w)
        with self.assertRaises(tg.TrajectoryError):
            es.match(store, q, {next(iter(w)): {'start': 2, 'end': 1}})
        self.assertEqual(store.query("SELECT table_name FROM duckdb_tables() WHERE temporary"), [])


class ParallelMatchTests(unittest.TestCase):
    """Partition-parallel matching (D21 arm C) gives the same bytes for any worker count."""

    SEEDS = range(0, SEEDS, 12)

    @classmethod
    def tearDownClass(cls):
        es.close_pools()

    @staticmethod
    def dump(out):
        return json.dumps(out, sort_keys=True)

    def test_workers_1_2_and_4_give_identical_full_output_equal_to_the_recorded_digests(self):
        store, want, seen = _STATE['store'], json.loads(MatcherScalingTests.FIXTURE.read_text()), Counter()
        for seed in self.SEEDS:
            q, w = MatcherScalingTests.case(seed)
            serial = es.match(store, q, w)
            for workers, chunk in ((2, 3), (4, 1), (2, es.CHUNK_PATIENTS)):
                self.assertEqual(self.dump(es.match(store, q, w, workers=workers, chunk=chunk)), self.dump(serial),
                                 (seed, workers, chunk))
            body = {k: serial[k] for k in ('plan', 'candidates', 'counts', 'results')}
            self.assertEqual(hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest(), want[str(seed)])
            seen.update(r['status'] for r in serial['results'])
            seen.update(r['reason'] for r in serial['results'] if r['status'] == 'not_comparable')
            self.assertEqual(self.dump(es.match(store, q, w, detail='decided', workers=2, chunk=2)),
                             self.dump(es.match(store, q, w, detail='decided', chunk=2)))
        self.assertTrue({'exact', 'none', 'not_comparable', 'excluded', 'relaxed'} <= set(seen), seen)

    def test_a_worker_failure_fails_the_whole_match_by_name_and_the_pool_recovers(self):
        store = _STATE['store']
        # A query with candidates left to the engine after time pruning (D25), so that units run.
        seed = next(seed for seed in range(SEEDS) if engine_runs(store, *MatcherScalingTests.case(seed)))
        q, w = MatcherScalingTests.case(seed)
        good = self.dump(es.match(store, q, w, workers=2, chunk=1))
        pool = es.get_pool(store, 2)
        for fault in ('raise', 'exit'):
            pool.fault = fault
            with self.assertRaises(es.MatchWorkerError) as caught:
                es.match(store, q, w, workers=2, chunk=1)
            pool.fault = None
            self.assertEqual(caught.exception.code, 'MATCH_WORKER_FAILED')
            self.assertIsInstance(caught.exception, es.StoreError)
            self.assertEqual(self.dump(es.match(store, q, w, workers=2, chunk=1)), good)
        with self.assertRaises(es.StoreError) as caught:
            es.match(store, q, w, workers=0)
        self.assertEqual(caught.exception.code, 'INVALID_WORKERS')

    def test_the_pool_is_reused_and_closing_it_leaves_no_process(self):
        store = _STATE['store']
        q, w = MatcherScalingTests.case(1)
        es.close_pools()
        pool = es.get_pool(store, 2)
        pool.warm()
        pids = list(pool.pids)
        self.assertEqual(len(pids), 2)
        es.match(store, q, w, workers=2, chunk=1)
        self.assertIs(es.get_pool(store, 2), pool)
        self.assertEqual(pool.pids, pids)
        pool.close()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and any(_alive(p) for p in pids):
            time.sleep(.05)
        self.assertEqual([p for p in pids if _alive(p)], [])
        with es.MatchPool(store, 2) as scoped:
            scoped.warm()
            alive = list(scoped.pids)
        self.assertEqual([p for p in alive if _alive(p)], [])


class WorkerResourceTests(unittest.TestCase):
    def test_closing_a_pool_leaves_no_spill_directory_and_workers_share_the_memory_limit(self):
        import glob
        pattern = os.path.join(tempfile.gettempdir(), 'execution-store-spill-*')
        before = set(glob.glob(pattern))
        with es.MatchPool(_STATE['store'], 2) as pool:
            pool.warm()
            self.assertEqual(len(set(glob.glob(pattern)) - before), 2)
        self.assertEqual(set(glob.glob(pattern)) - before, set())
        self.assertEqual(es.worker_memory_limit('4GB', 2), f'{2 * 10**9 // 1024}KiB')
        self.assertEqual(es.worker_memory_limit('1GB', 14), f'{256 * 10**6 // 1024}KiB')
        self.assertEqual(es.worker_memory_limit(None, 4), f'{10**9 // 1024}KiB')


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class DeterminismTests(unittest.TestCase):
    def test_thread_counts_and_rebuilds_give_one_snapshot_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = es.build(_STATE['ingest'], Path(tmp) / 'a', threads=1)
            four = es.build(_STATE['ingest'], Path(tmp) / 'b', threads=4)
            self.assertEqual(one.name, four.name)
            self.assertEqual(one.name, _STATE['snapshot'].name)
            stamp = (one / 'manifest.json').stat().st_mtime_ns
            shuffled = deepcopy(_STATE['ingest'])
            random.Random(7).shuffle(shuffled['patients'])
            for p in shuffled['patients']:
                p['events'].reverse()
            again = es.build(shuffled, Path(tmp) / 'a', threads=4)
            self.assertEqual(again, one)
            self.assertEqual((one / 'manifest.json').stat().st_mtime_ns, stamp)  # immutable: not rewritten
            self.assertEqual(sorted(os.listdir(Path(tmp) / 'a')), [one.name])

    def test_partitioned_tables_are_written_by_bucket(self):
        snapshot = _STATE['snapshot']
        for name in es.PARTITIONED:
            buckets = sorted(p.name for p in (snapshot / name).iterdir())
            if _STATE['store'].manifest['tables'][name]['rows'] == 0:  # the cohort has no excluded records
                self.assertEqual(buckets, ['empty.parquet'], name)
                continue
            self.assertTrue(buckets and all(b.startswith('bucket=') for b in buckets), name)
        self.assertEqual(len(list((snapshot / 'events').iterdir())), es.BUCKETS)


    def test_the_iterator_form_equals_the_dict_form(self):
        cohort = _STATE['cohort']
        with tempfile.TemporaryDirectory() as tmp:
            skipped = {}
            with es.StoreBuilder(Path(tmp) / 'stream', si.synthetic_header(cohort)) as builder:
                for patient in si.iter_patients(cohort, skipped):
                    builder.add(patient)
                streamed = builder.finish(si.synthetic_provenance(cohort, skipped))
            self.assertEqual(streamed.name, _STATE['snapshot'].name)
            self.assertEqual(skipped, _STATE['ingest']['provenance']['skipped'])
            self.assertEqual(sorted(os.listdir(Path(tmp) / 'stream')), [streamed.name])  # no staging left behind

    def test_fetch_sizes_and_memory_limits_give_one_snapshot_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            ids = {es.build(fixture(), Path(tmp) / f'f{n}', fetch_rows=n).name for n in (1, 7, 10_000)}
            self.assertEqual(len(ids), 1)
            small = deepcopy(_STATE['ingest'])
            small['patients'] = small['patients'][:6]
            ids = {es.build(small, Path(tmp) / f's{n}', fetch_rows=n).name for n in (1, 7, 10_000)}
            self.assertEqual(len(ids), 1)
            ids = {es.build(_STATE['ingest'], Path(tmp) / name, threads=threads, memory_limit=limit).name
                   for name, threads, limit in (('m', 1, '256MB'), ('m4', 4, '256MB'), ('g4', 4, '4GB'))}
            self.assertEqual(ids, {_STATE['snapshot'].name})
            manifest = json.loads((Path(tmp) / 'm' / _STATE['snapshot'].name / 'manifest.json').read_text())
            self.assertEqual(manifest['builder']['memory_limit'], '256MB')
            for bad in ('lots', 256, '4 bananas'):
                with self.subTest(memory_limit=bad), self.assertRaises(es.StoreError) as caught:
                    es.build(fixture(), Path(tmp) / 'bad', memory_limit=bad)
                self.assertEqual(caught.exception.code, 'INVALID_MEMORY_LIMIT')
            self.assertFalse((Path(tmp) / 'bad').exists())

    def test_digest_spans_give_one_digest_and_cover_every_row(self):
        """D24: a partitioned bucket is digested in `patient_idx` ranges of `span` indices (then its null
        indices), one ordered query each; any span gives the manifest's digests, on the synthetic store and on
        files whose rows carry an index outside every patient's (negative, beyond the last, null)."""
        store = _STATE['store']
        want = {n: {k: t[k] for k in ('rows', 'content_digest', 'buckets')} for n, t in store.manifest['tables'].items()}
        for span in (1, 3, 7, 40, 10**6):
            with self.subTest(span=span):
                got = es.content_digests(store.db, span=span)
                self.assertEqual({n: {k: t[k] for k in ('rows', 'content_digest', 'buckets')} for n, t in got.items()},
                                 want)
        # The slices of one bucket: below the first cut, the ranges, from the last cut up, then nulls.
        top = store.query('SELECT max(patient_idx) FROM events WHERE bucket = 0')[0][0]
        self.assertEqual(len(es._slices(store.db, 'events', '0', 10)), len(range(10, top + 1, 10)) + 2)
        self.assertEqual(es._slices(store.db, 'events', '0', 10**6), [(' WHERE bucket = ?', [0])])
        self.assertEqual(es._slices(store.db, 'concept_postings', 'all', 1), [('', [])])
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp)
            events = next((snapshot / 'events').glob('bucket=*/data_0.parquet'))
            with es.open_store(snapshot, verify=False) as clean:
                bucket = clean.query(f"SELECT DISTINCT bucket FROM read_parquet('{events}')")[0][0]
                # Odd indices: copies of the rows with idx -5, 10**6 and null. A span beyond them is one query over
                # the bucket, the reference; smaller spans cut it in three or four ranges plus the nulls.
                clean.db.execute(f"""CREATE TABLE odd AS SELECT * FROM read_parquet('{events}') UNION ALL
                    SELECT * REPLACE (-5 AS patient_idx) FROM read_parquet('{events}') UNION ALL
                    SELECT * REPLACE (1000000 AS patient_idx) FROM read_parquet('{events}') UNION ALL
                    SELECT * REPLACE (NULL::INTEGER AS patient_idx) FROM read_parquet('{events}')""")
                clean.db.execute('CREATE OR REPLACE VIEW events AS SELECT * FROM odd')
                digests = {span: es.content_digests(clean.db, span=span)['events']['buckets'][str(bucket)]
                           for span in (300_000, 700_000, 10**7)}
        self.assertEqual(len(set(map(json.dumps, digests.values()))), 1, digests)

    def test_content_verification_takes_the_builds_memory_limit(self):
        """D24: a store opened without a memory limit verifies content under the manifest's
        `builder.memory_limit`, then returns to DuckDB's default; one opened with a limit keeps its own."""
        seen = []
        original = es.Store.derived_tables
        def spy(self):
            seen.append(self.db.execute("SELECT current_setting('memory_limit')").fetchone()[0])
            return original(self)
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp, memory_limit='300MB')
            with patch.object(es.Store, 'derived_tables', spy):
                with es.open_store(snapshot, verify='content') as store:
                    after = store.db.execute("SELECT current_setting('memory_limit')").fetchone()[0]
                    store.verify('content')
                with es.open_store(snapshot, verify='content', memory_limit='500MB') as store:
                    own = store.db.execute("SELECT current_setting('memory_limit')").fetchone()[0]
        self.assertEqual(seen[:2], ['286.1 MiB', '286.1 MiB'])  # 300 MB, as DuckDB prints it
        self.assertNotEqual(after, '286.1 MiB')
        self.assertEqual((seen[2], own), ('476.8 MiB', '476.8 MiB'))

    def test_add_holds_one_patient_at_a_time(self):
        """tracemalloc over about 2,000 streamed patients: the peak inside `add()` is bounded by a
        small multiple of one patient's JSON size, and nothing accumulates between calls."""
        import tracemalloc
        pool = _STATE['ingest']['patients']
        largest = max(len(es._json(p)) for p in pool)
        count = 42 * len(pool)  # 2,016
        def stream():  # replicas made on the fly, so the stream itself holds no cohort
            for i in range(count):
                p = pool[i % len(pool)]
                yield {**p, 'patient_id': f"{p['patient_id']}~{i // len(pool)}"}
        header = {k: _STATE['ingest'][k] for k in ('profile', 'dataset_id', 'closure')}
        peaks, retained = [], []
        with tempfile.TemporaryDirectory() as tmp, es.StoreBuilder(Path(tmp) / 'mem', header) as builder:
            tracemalloc.start()
            try:
                for patient in stream():
                    before = tracemalloc.get_traced_memory()[0]
                    tracemalloc.reset_peak()
                    builder.add(patient)
                    current, peak = tracemalloc.get_traced_memory()
                    peaks.append(peak - before)
                    retained.append(current)
            finally:
                tracemalloc.stop()
            self.assertEqual(builder.patients, count)
        whole = count * sum(len(es._json(p)) for p in pool) // len(pool)
        self.assertLess(max(peaks), 4 * largest, (max(peaks), largest))
        self.assertLess(max(peaks), whole // 200)
        # Nothing per patient is kept: what is held after the last 500 adds is what was held after the
        # first few hundred (staging-file buffers make it wobble by tens of KiB), not 1,500 patients more.
        growth = max(retained[-500:]) - max(retained[100:600])
        self.assertLess(growth, 1024 * 1024, growth)
        print(f'\n  add(): {count} patients, peak {max(peaks) / 2**20:.2f} MiB per add, largest patient '
              f'{largest / 2**20:.2f} MiB of JSON, stream {whole / 2**20:.0f} MiB, retained growth '
              f'{growth / 1024:.0f} KiB', file=sys.stderr)


def main_derive(db, relation):
    """`_derive` as at main fdd6e97, frozen: every table in one statement over whole staging files (D23).
    The only changes since are the 2.1 columns (D24): `patients.shared_variables` and the events' variables
    and `proper`, each a copy of its staged value (false where the staging leaves it out); and 3.0's (D26): the
    events' record fields, copies of their staged values, the evidence in its own table, and the postings'
    latest end; and 3.1's (D27): the series rows, copies of their staged values. `relation(name)` reads one staged
    table's rows over the whole build (D26: staging may be Parquet)."""
    staged = relation
    db.execute(f"""CREATE TABLE p AS SELECT
        (row_number() OVER (ORDER BY patient_id ASC) - 1)::INTEGER AS patient_idx, patient_id,
        (('0x' || substr(sha256(patient_id), 1, 8))::UBIGINT % {es.BUCKETS})::INTEGER AS bucket, clock_id,
        coalesce(shared_variables, false) AS shared_variables
        FROM s_patients""")
    db.execute('CREATE TABLE patients AS SELECT patient_idx, patient_id, bucket, shared_variables FROM p')
    db.execute(f"""CREATE TABLE events AS SELECT p.patient_idx, e.event_id, e.kind, e.source, p.clock_id,
        e.start_lo, e.start_hi, e.end_lo, e.end_hi, (e.end_lo IS NOT NULL AND e.end_hi IS NULL) AS ongoing,
        e.start_var, e.end_var, coalesce(e.proper, false) AS proper,
        e.status, e.selection_state, e.recorded_at, e.available_at, e.claim_id, e.claim_sha256, e.source_table,
        e.record_sha256, e.file_sha256, p.bucket FROM {staged('events')} e JOIN p USING (patient_id)""")
    db.execute(f"""CREATE TABLE event_evidence AS SELECT p.patient_idx, x.event_id, x.evidence, p.bucket
        FROM {staged('evidence')} x JOIN p USING (patient_id)""")
    db.execute(f"""CREATE TABLE event_concepts AS SELECT p.patient_idx, c.event_id, c.concept, 'direct' AS basis,
        p.bucket FROM {staged('concepts')} c JOIN p USING (patient_id)""")
    db.execute(f'''CREATE TABLE concept_closure AS SELECT concept, ancestor, basis, version, concept_basis, ancestor_basis
        FROM {staged('closure')}''')
    db.execute("""CREATE TABLE concept_postings AS
        WITH expanded AS (SELECT DISTINCT ec.patient_idx, ec.event_id, cc.ancestor AS concept
                          FROM event_concepts ec JOIN concept_closure cc ON cc.concept = ec.concept)
        SELECT x.concept, x.patient_idx, count(*)::BIGINT AS n, min(e.start_lo) AS min_start_lo,
               max(e.start_hi) AS max_start_hi,
               CASE WHEN bool_or(e.ongoing) THEN NULL ELSE max(coalesce(e.end_hi, e.start_hi)) END AS max_end_hi
        FROM expanded x JOIN events e USING (patient_idx, event_id) GROUP BY x.concept, x.patient_idx""")
    db.execute(f"""CREATE TABLE coverage AS SELECT p.patient_idx, c.source, p.clock_id, c.lo, c.hi, c.basis, p.bucket
        FROM {staged('coverage')} c JOIN p USING (patient_id)""")
    db.execute("""CREATE TABLE source_concepts AS SELECT DISTINCT e.source, cc.ancestor AS concept,
        'inferred_from_records' AS basis
        FROM events e JOIN event_concepts ec USING (patient_idx, event_id)
        JOIN concept_closure cc ON cc.concept = ec.concept""")
    db.execute(f"""CREATE TABLE excluded_records AS SELECT p.patient_idx, x.source, x.concept, x.start_lo, x.start_hi,
        x.reason, count(*)::BIGINT AS n, p.bucket
        FROM {staged('excluded')} x JOIN p USING (patient_id)
        GROUP BY p.patient_idx, x.source, x.concept, x.start_lo, x.start_hi, x.reason, p.bucket""")
    db.execute(f"""CREATE TABLE series AS SELECT p.patient_idx, s.event_id, s.item_class, s.start_lo, s.start_hi,
        s.value_lexical, s.value_decimal, s.value_micro, s.unit, p.bucket
        FROM {staged('series')} s JOIN p USING (patient_id)""")
    db.execute('DROP TABLE p')


def main_path_digests(ingest, tmp, staging=None):
    """(input digest, table digests) of main's single-statement derive over this ingest's staging, every part of
    each staged table read at once, as main staged it in one file."""
    tmp = Path(tmp)
    header = {k: ingest[k] for k in ('profile', 'dataset_id', 'closure')}
    (tmp / 'spill').mkdir(parents=True)
    with es.StoreBuilder(tmp / 'unused', header, part_rows=1, staging=staging) as builder:  # left unfinished
        for p in ingest['patients']:
            builder.add(p)
        for row in es._closure_rows(header['closure']):
            builder._put('closure', row)
        for f in builder._files.values():
            f.close()
        for bucket in list(builder._current):
            builder._close_part(bucket)
        relation = lambda name, parts=(): builder.relation(name, builder.parts)
        db = es._connect(tmp / 'main.duckdb', 1, '1GB', tmp / 'spill')
        try:
            es._load_patients(db, relation)
            input_digest = es._input_digest(db, header, ingest.get('provenance', {}), 1000)
            main_derive(db, relation)
            es._check_types(db)
            return input_digest, es.content_digests(db)
        finally:
            db.close()


def series_patient():
    """A patient with measured points (D27): exact micro-unit values, one finer than 10**-6, and an interval."""
    def value(eid, text, decimal, micro, unit='per day'):
        return {'event_id': eid, 'item_class': 'seizure_frequency', 'value_lexical': text, 'value_decimal': decimal,
                'value_micro': micro, 'unit': unit}
    return {'patient_id': 'S-series', 'clock_id': 'patient_S', 'coverage': [cover('diary', day(0), day(30))],
            'events': [ev('m1', ['seizure_frequency'], (day(1), day(1)), source='diary', kind='measurement'),
                       ev('m2', ['seizure_frequency'], (day(2), day(2) + 5), source='diary', kind='measurement'),
                       ev('m3', ['seizure_frequency', 'seizure'], (day(3), day(3)), source='diary', kind='measurement'),
                       ev('x', ['valproate'], (day(1), day(1)), (day(4), day(4)), 'pharmacy', 'exposure')],
            'series': [value('m2', '1e-7', '0.0000001', None), value('m1', '7.350', '7.350', 7_350_000),
                       value('m3', '-12', '-12', -12_000_000, 'count')]}


def tables_of(manifest):
    """Every table's digests but `series` (D27), which the 3.0 records predate; callers check it is empty."""
    return {n: {k: t[k] for k in ('rows', 'content_digest', 'buckets')} for n, t in manifest['tables'].items()
            if n != 'series'}


# The 2.0 columns of the tables 2.1 (D24) and 3.0 (D26) changed, in their 2.0 order: `events` with its evidence,
# which 3.0 keeps in `event_evidence`, and without the variables, `proper` and the record fields.
COLUMNS_2_0 = {'patients': es.TABLES['patients'][:3],
               'events': es.TABLES['events'][:10] + (('evidence', 'VARCHAR'),),
               'concept_postings': es.TABLES['concept_postings'][:5]}


def digests_2_0(snapshot):
    """Content digests of a 3.0 snapshot with its tables read as 2.0's: `patients` and `concept_postings` as
    their 2.0 columns, and `events` joined to `event_evidence` as 2.0 held it. The canonical form is unchanged,
    so on an ingest without shared variables, `proper` or record fields these must be the digests 2.0 recorded
    for the same document, and the other tables' digests too."""
    assert [c for c, _ in COLUMNS_2_0['events']][-2:] == ['ongoing', 'evidence']
    tables = {n: COLUMNS_2_0.get(n, c) for n, c in es.TABLES.items() if n not in ('event_evidence', 'series')}
    with es.open_store(snapshot, verify=False) as store, patch.dict(es.TABLES, tables, clear=True):
        store.db.execute('ALTER VIEW events RENAME TO events_3_0')
        store.db.execute("""CREATE VIEW events AS SELECT e.*, x.evidence FROM events_3_0 e
                            JOIN event_evidence x ON x.patient_idx = e.patient_idx AND x.event_id = e.event_id""")
        return {n: {k: t[k] for k in ('rows', 'content_digest', 'buckets')}
                for n, t in es.content_digests(store.db).items()}


def x10(ingest):
    """The synthetic cohort ten times over, with relabelled patients."""
    return {**ingest, 'dataset_id': 'synthetic-x10',
            'patients': [{**p, 'patient_id': f"{p['patient_id']}~{k}"} if k else p
                         for k in range(10) for p in ingest['patients']]}


D30_DIGESTS = ROOT / 'patterns/fixtures/execution_store_match_digests_d30.json'
D30_STAGES = ('last order', 'order rule', 'mention timing')


def match_digest(store, q, w):
    out = es.match(store, q, w)
    return hashlib.sha256(json.dumps({k: out[k] for k in ('plan', 'candidates', 'counts', 'results')},
                                     sort_keys=True).encode()).hexdigest()


def d30_stage_digests(legacy_store, seed):
    """The seed's result digest at each stage of D30 (decision log, D30): before it (the store converted with
    letter-timed mentions, the order rule off and `last` by the old order), with `last` by the latest start,
    with the order rule as well, and with the mention timing (the store and the case as they are now); and, for a
    second ordering, this store and case with the order rule off (mention timing before the order rule)."""
    q, w = MatcherScalingTests.legacy_case(seed)
    out = []
    for order_rule, last_key in ((False, tg._LAST_KEY_BEFORE_D30), (False, tg.last_key), (True, tg.last_key)):
        tg._ORDER_RULE, tg._LAST_KEY = order_rule, last_key
        try:
            out.append(match_digest(legacy_store, q, w))
        finally:
            tg._ORDER_RULE, tg._LAST_KEY = True, tg.last_key
    out.append(match_digest(_STATE['store'], *MatcherScalingTests.case(seed)))
    tg._ORDER_RULE = False
    try:
        out.append(match_digest(_STATE['store'], *MatcherScalingTests.case(seed)))
    finally:
        tg._ORDER_RULE = True
    return out


def d30_explanation(digests):
    """The stages at which the digest moved, in the order last order, order rule, mention timing."""
    return [stage for stage, a, b in zip(D30_STAGES, digests[:4], digests[1:4]) if a != b]


def d30_explanation_timing_first(digests):
    """The same, in the order last order, mention timing (order rule off), order rule."""
    chain = [digests[0], digests[1], digests[4], digests[3]]
    return [stage for stage, a, b in zip(('last order', 'mention timing', 'order rule'), chain, chain[1:]) if a != b]


class D30ExplanationTests(unittest.TestCase):
    """Every recorded match digest D30 moved is localized to the stages of its three changes (decision log, D30):
    this tree, on the store converted as before D30 and with both engine switches set as before, gives the digest
    recorded before D30; the stages at which it then moves are recorded in two orderings (the order rule before or
    after the mention timing); and the last stage gives the digest recorded now. The stages localize a change;
    they do not by themselves show that it is a fix (the worlds check in the engine's tests does that)."""

    def test_the_300_recorded_digests_move_only_at_the_three_stages(self):
        recorded = json.loads(D30_DIGESTS.read_text())
        now = json.loads(MatcherScalingTests.FIXTURE.read_text())
        self.assertEqual(sorted(recorded['seeds'], key=int), [str(s) for s in range(SEEDS)])
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(_STATE['legacy'], Path(tmp) / 'store')) as legacy:
            for seed in range(SEEDS):
                entry = recorded['seeds'][str(seed)]
                digests = d30_stage_digests(legacy, seed)
                with self.subTest(seed=seed):
                    self.assertEqual(digests[0], entry['before'])
                    self.assertEqual(digests[3], entry['after'])
                    self.assertEqual(digests[3], now[str(seed)])
                    self.assertEqual(d30_explanation(digests), entry['stages'])
                    self.assertEqual(d30_explanation_timing_first(digests), entry['stages_timing_first'])
                    self.assertEqual(entry['case_changed'], MatcherScalingTests.legacy_case(seed) != MatcherScalingTests.case(seed))

    def test_the_levers_change_no_recorded_digest(self):
        """D30 review: the cost levers off give every recorded digest too."""
        now = json.loads(MatcherScalingTests.FIXTURE.read_text())
        tg._LEVERS = False
        try:
            wrong = [seed for seed in range(SEEDS)
                     if match_digest(_STATE['store'], *MatcherScalingTests.case(seed)) != now[str(seed)]]
        finally:
            tg._LEVERS = True
        self.assertEqual(wrong, [])


class PerBucketDeriveTests(unittest.TestCase):
    """D23: deriving one part (a run of whole patients of one bucket) at a time gives the tables and
    digests main's single-statement derive gave, for any part size."""
    RECORDED = ROOT / 'patterns/fixtures/execution_store_content_digests.json'
    RECORDED_3_0 = ROOT / 'patterns/fixtures/execution_store_content_digests_3_0.json'

    @classmethod
    def setUpClass(cls):
        cls.recorded = json.loads(cls.RECORDED.read_text())['cases']
        cls.recorded_3_0 = json.loads(cls.RECORDED_3_0.read_text())['cases']

    def build(self, ingest, tmp, **options):
        return json.loads((es.build(ingest, Path(tmp) / 'store', **options) / 'manifest.json').read_text())

    def test_the_digests_recorded_before_the_change_are_reproduced_at_any_part_size(self):
        """Two records. `RECORDED_3_0` holds the input digest and every table's digests the 3.0 builder (D26) gave
        with ndjson staging, before typed staging: they must be reproduced at any part size. `RECORDED` holds
        2.0's, at main fdd6e97 before the per-bucket derive: 3.0's tables read as 2.0's (`digests_2_0`, `events`
        joined to its evidence) give them all for the fixture, whose document 3.0 leaves as it was, input digest
        included; for the synthetic cohorts, whose converter now gives each event its record fields (D26), every
        table but `events` gives 2.0's. The 2.1 columns hold nothing here.

        The synthetic cohorts are converted with the mention timing before D30 (`mention_time='letter'`), which
        leaves every table as it was. Their input digests in `RECORDED_3_0` were re-recorded at D30, since the
        input digest covers the converter's own sha256 (`provenance.converter_sha256`), which D30 changed."""
        ingest = _STATE['legacy']
        times_ten = x10(ingest)
        cases = [('synthetic', ingest, {}), ('synthetic', ingest, {'part_rows': 1}),
                 ('synthetic_x10', times_ten, {}), ('synthetic_x10', times_ten, {'part_rows': 5000}),
                 ('fixture', fixture(), {}), ('fixture', fixture(), {'part_rows': 1})]
        for name, case, options in cases:
            with self.subTest(case=name, **options), tempfile.TemporaryDirectory() as tmp:
                snapshot = es.build(case, Path(tmp) / 'store', **options)
                manifest = json.loads((snapshot / 'manifest.json').read_text())
                self.assertEqual({'input_digest': manifest['input_digest'], 'tables': tables_of(manifest)},
                                 {k: self.recorded_3_0[name][k] for k in ('input_digest', 'tables')})
                self.assertEqual(manifest['tables']['series']['rows'], 0)  # 3.1 adds an empty table here
                want, mine = self.recorded[name], digests_2_0(snapshot)
                changed = set() if name == 'fixture' else {'events'}
                self.assertEqual(manifest['input_digest'] == want['input_digest'], name == 'fixture')
                self.assertEqual({n: t for n, t in mine.items() if n not in changed},
                                 {n: t for n, t in want['tables'].items() if n not in changed})
                for n in changed:
                    self.assertNotEqual(mine[n]['content_digest'], want['tables'][n]['content_digest'])
                with es.open_store(snapshot) as store:
                    self.assertEqual(store.query('SELECT count(*) FROM patients WHERE shared_variables')[0][0], 0)
                    self.assertEqual(store.query('SELECT count(*) FROM events WHERE start_var IS NOT NULL OR '
                                                 'end_var IS NOT NULL OR proper')[0][0], 0)

    def test_the_per_part_derive_equals_mains_single_statement_derive(self):
        g = fixture()
        g['patients'][0]['excluded'] = [{'source': 'ehr', 'concept': 'seizure', 'start': [day(3), day(4)],
                                         'reason': 'DROPPED'}] * 2
        g['patients'][2]['excluded'] = [{'source': 'diary', 'concept': 'valproate', 'start': [day(5), day(5)],
                                         'reason': 'NOT_ADMITTED'}]
        g['patients'].append({'patient_id': 'Ünïcode-😀', 'clock_id': 'c', 'coverage': [cover('ehr', day(0), day(9))],
                              'events': [ev('é', ['dravet', 'valproate'], (day(1), day(2)), (day(3), None))]})
        g['patients'] += shared_variable_patients()
        g['patients'].append(series_patient())
        for label, ingest in (('synthetic', _STATE['ingest']),
                              ('fixture with excluded records, shared variables and series', g)):
            with tempfile.TemporaryDirectory() as tmp:
                input_digest, tables = main_path_digests(ingest, Path(tmp) / 'main')
                self.assertEqual(main_path_digests(ingest, Path(tmp) / 'json', staging='ndjson'), (input_digest, tables))
            for options in ({}, {'part_rows': 1}, {'part_rows': 700}, {'staging': 'ndjson', 'part_rows': 700}):
                with self.subTest(case=label, **options), tempfile.TemporaryDirectory() as tmp:
                    manifest = self.build(ingest, tmp, **options)
                    self.assertEqual(manifest['input_digest'], input_digest)
                    self.assertEqual({n: {k: t[k] for k in ('rows', 'content_digest', 'buckets')}
                                      for n, t in manifest['tables'].items()}, tables)
        self.assertGreater(tables['excluded_records']['rows'], 0)
        self.assertEqual(tables['series']['rows'], 3)

    def test_parts_hold_whole_patients_of_one_bucket_and_each_bucket_is_reported(self):
        stages = []
        header = {k: _STATE['ingest'][k] for k in ('profile', 'dataset_id', 'closure')}
        with tempfile.TemporaryDirectory() as tmp:
            with es.StoreBuilder(Path(tmp) / 's', header, part_rows=2000,
                                 on_stage=lambda name, info: stages.append((name, info))) as builder:
                for p in _STATE['ingest']['patients']:
                    builder.add(p)
                for bucket in list(builder._current):
                    builder._close_part(bucket)
                seen = Counter()
                import duckdb
                for bucket, index in builder.parts:
                    ids = {pid for name in ('events', 'coverage') for pid, in duckdb.sql(
                        f'SELECT patient_id FROM {builder.relation(name, [(bucket, index)])}').fetchall()}
                    self.assertTrue(ids and {es.bucket_of(pid) for pid in ids} == {bucket}, (bucket, index))
                    seen.update(ids)
                self.assertEqual(set(seen.values()), {1})  # no patient in two parts
                self.assertGreater(len(builder.parts), es.BUCKETS)
                snapshot = builder.finish(_STATE['ingest']['provenance'])
            with es.open_store(snapshot) as store:
                for idx, pid, bucket, _ in store.rows("patients"):
                    self.assertEqual(bucket, es.bucket_of(pid))
                    self.assertEqual(bucket, ref.bucket_of(pid))
        names = [n for n, _ in stages]
        self.assertEqual([n for n in names if n.startswith('derived:bucket=')],
                         [f'derived:bucket={b}' for b in range(es.BUCKETS)])
        info = dict(stages)
        self.assertEqual(sum(info[f'derived:bucket={b}']['parts'] for b in range(es.BUCKETS)), info['staged']['parts'])
        for b in range(es.BUCKETS):
            row = info[f'derived:bucket={b}']
            self.assertEqual(set(row['statement_seconds']),
                             {'events', 'event_concepts', 'concept_postings', 'source_concepts', 'event_evidence',
                              'coverage', 'excluded_records', 'series'})
            self.assertGreater(row['duckdb_memory_max_bytes'], 0)
        self.assertEqual(info['derived:whole']['statements'], 3)
        with self.assertRaises(es.StoreError) as caught:
            es.StoreBuilder(Path(tempfile.gettempdir()) / 'never', header, part_rows=0)
        self.assertEqual(caught.exception.code, 'INVALID_PART_ROWS')


def shared_variable_patients():
    """Patients with shared time variables and proper exposures, on the fixture's closure (D24): S1's
    seizure starts when its carbamazepine exposure ends; S2's two points are ordered only through an
    exposure that carries no other step's concept (`unused`), naming one's start and the other's end;
    S3 has S2's times without the sharing; S4 names its own default variables explicitly (which the store
    normalises away, D25)."""
    span = (day(-30), day(400))
    cbz = {**ev('cbz', ['carbamazepine'], (day(30), day(35)), (day(30), day(40)), 'pharmacy', 'exposure'),
           'proper': True}
    return [
        {'patient_id': 'S1', 'clock_id': 'patient_S1', 'coverage': [cover('ehr', *span), cover('pharmacy', *span)],
         'events': [cbz, {**ev('fs', ['febrile_seizure'], (day(30), day(40))), 'start_var': 'cbz.f'}]},
        {'patient_id': 'S2', 'clock_id': 'patient_S2', 'coverage': [cover('ehr', *span), cover('pharmacy', *span)],
         'events': [{**ev('link', ['unused'], (day(10), day(12)), (day(10), day(12)), 'pharmacy', 'exposure'),
                     'start_var': 'on', 'end_var': 'off', 'proper': True},
                    {**ev('fs', ['febrile_seizure'], (day(10), day(12))), 'start_var': 'on'},
                    {**ev('vpa', ['valproate'], (day(10), day(12))), 'start_var': 'off'}]},
        {'patient_id': 'S3', 'clock_id': 'patient_S3', 'coverage': [cover('ehr', *span), cover('pharmacy', *span)],
         'events': [ev('link', ['unused'], (day(10), day(12)), (day(10), day(12)), 'pharmacy', 'exposure'),
                    ev('fs', ['febrile_seizure'], (day(10), day(12))), ev('vpa', ['valproate'], (day(10), day(12)))]},
        {'patient_id': 'S4', 'clock_id': 'patient_S4', 'coverage': [cover('ehr', *span)],
         'events': [{**ev('fs', ['febrile_seizure'], (day(0), day(2)), (day(1), day(3))), 'start_var': 'fs.s',
                     'end_var': 'fs.f', 'proper': True}]},
    ]


def store_ingest_from_adapter_rows(rows, origin_us):
    """The SULO adapter's ingest rows as an execution-store ingest: one event per row, its tightened bounds
    on the store's time line (`origin_us` plus the bounds on the clock), its source variables and `proper`,
    and every concept the row carries as a direct concept with no ancestors."""
    patients, concepts = {}, set()
    for r in rows:
        p = patients.setdefault(r['patient_id'], {'patient_id': r['patient_id'], 'clock_id': r['clock_id'],
                                                  'events': [], 'coverage': []})
        mine = sorted(c['concept'] for c in r['concepts'])
        concepts.update(mine)
        p['events'].append({'event_id': r['event_id'], 'kind': r['event_kind'], 'source': 'bounded',
                            'concepts': mine, 'start': [origin_us + t for t in r['start_us']],
                            'end': [origin_us + t for t in r['end_us']], 'start_var': r['start_var'],
                            'end_var': r['end_var'], 'proper': r['proper'], 'evidence': {'row': r['event_id']}})
        assert r['box_representable'], r
    for p in patients.values():
        p['coverage'] = [cover('bounded', min(e['start'][0] for e in p['events']), max(e['end'][1] for e in p['events']))]
    return {'profile': es.INGEST_PROFILE, 'dataset_id': 'adapter-rows',
            'closure': {'version': 'adapter-rows-1', 'ancestors': {c: [] for c in sorted(concepts)}},
            'patients': sorted(patients.values(), key=lambda p: p['patient_id'])}


class SharedVariableStoreTests(unittest.TestCase):
    """The store holds shared time variables and `proper` (2.1, D24) and matches as the engine does."""

    def test_variables_and_proper_are_columns_and_patients_say_whether_they_share(self):
        g = fixture()
        g['patients'] += shared_variable_patients()
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp), verify='content') as store:
            self.assertEqual(table_mismatches(store, ref.build_tables(g)), {})
            shared = {pid: flag for _, pid, _, flag in store.rows('patients')}
            self.assertEqual({p for p, flag in shared.items() if flag}, {'S1', 'S2'})
            ids = {i: pid for i, pid, *_ in store.rows('patients')}
            events = {(ids[r[0]], r[1]): r[10:13] for r in store.rows('events')}
            self.assertEqual(events[('S1', 'cbz')], (None, None, True))
            self.assertEqual(events[('S1', 'fs')], ('cbz.f', None, False))
            self.assertEqual(events[('S2', 'link')], ('on', 'off', True))
            self.assertEqual(events[('S4', 'fs')], (None, None, True))  # named its defaults: kept as none (D25)
            self.assertEqual(events[('P1', 'cbz')], (None, None, False))

    def test_a_linking_event_no_step_can_bind_is_loaded(self):
        # S2's seizure starts when the link starts and its valproate when the link ends, strictly later:
        # equal starts are impossible, at least one microsecond apart is exact. S3, without the sharing,
        # is possible both ways. The store must load the link though no step carries `unused`.
        g = fixture()
        g['patients'] += shared_variable_patients()
        q = lambda lo, hi=None: {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': 'febrile_seizure'},
                                                                      {'id': 'B', 'concept': 'valproate'}],
                                 'relations': [{'id': 'AB', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': lo,
                                                **({} if hi is None else {'max': hi})}]}
        windows = {p['patient_id']: {'start': day(-30), 'end': day(400)} for p in g['patients']}
        original = es._load_events
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp)) as store:
            for query, want in ((q(0, 0), {'S2': 'none', 'S3': 'possible'}), (q(1), {'S2': 'exact', 'S3': 'possible'})):
                got = es.match(store, query, windows)
                self.assertEqual({r['patient_id']: r['status'] for r in got['results'] if r['patient_id'] in want}, want)
                for mine, theirs in zip(got['results'], ref.exhaustive_match(g, query, windows), strict=True):
                    self.assertEqual(mine['status'], theirs['status'])
                    if mine.get('reason') != 'FILTERED':
                        self.assertEqual(mine, theirs)
                self.assertEqual(json.dumps(es.match(store, query, windows, workers=2, chunk=1), sort_keys=True),
                                 json.dumps(got, sort_keys=True))
                # A loader that filters a sharing patient's events by concept, as 2.0 did, loses the link.
                with patch.object(es, '_load_events', lambda s, idxs, per_step, linked=(): original(s, idxs, per_step)):
                    wrong = {r['patient_id']: r['status'] for r in es.match(store, query, windows)['results']}
                self.assertEqual(wrong['S2'], 'possible')
            es.close_pools()

    def test_a_shared_variable_patient_through_the_store_equals_the_adapters_own_engine_result(self):
        """Bounded sources with shared variables (the D22 probes and a three-event chain) go through the
        SULO adapter's ingest rows into the store; the store's match of each patient equals the adapter's
        own `match_snapshot` (status, binding, certainty, relaxations, method, completeness and witness,
        on the store's time line), which the engine decides alone (no adapter code)."""
        from . import trajectory_graph_sulo as tgs
        from .test_trajectory_graph_sulo import (DAY as D, INFUSION, SPECIMEN, SharedVariableTests, after, request_for,
                                                 source_for)
        probes = SharedVariableTests()
        chain = source_for([('X', 'P1', 'infusion', (0, 10 * D), (0, 20 * D)),
                            ('Y', 'P1', 'infusion', (0, 0), (21 * D, 30 * D), 'X-end'),
                            ('Z', 'P1', 'specimen_collection', (0, 0), (31 * D, 31 * D), 'Y-end'),
                            ('X2', 'P2', 'infusion', (0, 10 * D), (0, 20 * D)),
                            ('Z2', 'P2', 'specimen_collection', (D, 30 * D), (31 * D, 31 * D))])
        origin_us = si.micros('2026-01-01T00:00')
        fields = ('status', 'binding', 'certainty', 'relaxations', 'cost', 'method', 'complete', 'note', 'reason')
        compared, statuses = 0, Counter()
        for source in (probes.probe(), probes.strict_chain(), chain):
            view = tgs.from_bounded_json(source)
            ingest = store_ingest_from_adapter_rows(tgs.ingest_rows(view), origin_us)
            self.assertTrue(any(es.shares_variables(p['events']) for p in ingest['patients']))
            with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, tmp), verify='content') as store:
                for relations in ([after(0, 0)], [after(1)], [after(1, 5 * D)], [after(0)], [after(None, 0)],
                                  [{'id': 'a-b', 'kind': 'during', 'from': 'a', 'to': 'b', 'grace': 0}]):
                    request = request_for([('a', INFUSION), ('b', SPECIMEN)], relations, unit='microsecond',
                                          window=(0, 40 * D))
                    adapter = tgs.match_snapshot(view, request, point_projection=False)  # the store sends intervals
                    windows = {pid: {'start': origin_us, 'end': origin_us + 40 * D} for pid in adapter['statuses']}
                    mine = {r['patient_id']: r for r in es.match(store, adapter['engine_query'], windows)['results']}
                    for a in adapter['results']:
                        self.assertEqual((a['decided_by'], a.get('adapter')), ('engine', None))
                        m = mine[a['patient_id']]
                        self.assertEqual({k: m[k] for k in fields if k in m}, {k: a[k] for k in fields if k in a},
                                         (a['patient_id'], relations))
                        if 'witness' in a:
                            self.assertEqual(m['witness'], {s: origin_us + t for s, t in a['witness'].items()})
                        statuses[a['status']] += 1
                        compared += 1
        self.assertEqual(set(statuses), {'exact', 'possible', 'none'})
        self.assertGreater(compared, 30)

    def test_shared_variables_the_engine_would_refuse_are_refused_at_ingest(self):
        def bad(*events):
            g = fixture()
            g['patients'].append({'patient_id': 'B', 'clock_id': 'c', 'coverage': [], 'events': list(events)})
            return g
        cases = {
            'INVALID_EVENT_VARIABLE': bad({**ev('a', ['seizure'], (day(0), day(1))), 'end_var': 'v'}),
            'INVALID_EVENT_PROPER': bad({**ev('a', ['seizure'], (day(0), day(1))), 'proper': True}),
            'EMPTY_SHARED_VARIABLE': bad({**ev('a', ['seizure'], (day(0), day(1))), 'start_var': 'v'},
                                         {**ev('b', ['seizure'], (day(2), day(3))), 'start_var': 'v'}),
        }
        cases['INVALID_EVENT_VARIABLE '] = bad({**ev('a', ['seizure'], (day(0), day(1))), 'start_var': ''})
        # A proper event that cannot end after it starts shares nothing: its own code (D25).
        cases['EMPTY_PROPER_EVENT'] = bad({**ev('a', ['seizure'], (day(0), day(0)), (day(0), day(0))), 'proper': True})
        for code, g in cases.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(es.IngestError) as caught:
                    es.build(g, Path(tmp) / 'store')
                self.assertEqual(caught.exception.code, code.strip())
                self.assertFalse((Path(tmp) / 'store').exists())
        chain = [{**ev(f'e{i}', ['valproate'], (day(i), day(i + 1)), (day(i + 1), day(i + 2)), 'pharmacy', 'exposure'),
                  'start_var': f'v{i}', 'end_var': f'v{i + 1}'} for i in range(4)]
        with patch.object(tg, 'MAX_LINKED_VARIABLES', 3), self.assertRaises(es.IngestError) as caught:
            es.validate_ingest(bad(*chain))
        self.assertEqual(caught.exception.code, 'SHARED_VARIABLE_COMPONENT_LIMIT')
        es.validate_ingest(bad(*chain))


class TimePruningTests(unittest.TestCase):
    """Time pruning on the posting summaries (D25), with the end summaries (D26), changes no byte of any result:
    pruning on against off, at both detail levels and at 1 and 2 workers, on the synthetic cohort, two
    shared-variable fuzz cohorts and the fixture with shared-variable patients. The existing differentials
    (exhaustive matching, the recorded digests, workers, the MIMIC fixture) run with pruning on."""

    @classmethod
    def tearDownClass(cls):
        es.close_pools()

    def compare(self, store, cases, decided=(), parallel=(), totals=None):
        """(pruned candidates, candidates with a window, pruned patients that share variables) over `cases`,
        asserting that pruning on and off give the same bytes for every case, also with `detail='decided'` for
        the indices in `decided` and with `workers=2` (chunk 3, both details) for those in `parallel`. Without the
        end summaries (D26) pruning must prune a subset; `totals`, when given, counts what they add (`by_ends`)."""
        linked = {i for i, in store.query('SELECT patient_idx FROM patients WHERE shared_variables')}
        ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
        pruned = live = pruned_linked = 0
        for k, (q, w) in enumerate(cases):
            runs = [{}] + ([{'detail': 'decided'}] if k in decided else []) + \
                   ([{'workers': 2, 'chunk': 3}, {'workers': 2, 'chunk': 3, 'detail': 'decided'}] if k in parallel else [])
            for kwargs in runs:
                with patch.object(es, '_PRUNE', False):
                    off = json.dumps(es.match(store, q, w, **kwargs))
                self.assertEqual(json.dumps(es.match(store, q, w, **kwargs)), off, (k, kwargs))
            pq = tg.PreparedQuery(q)
            by_idx = {ids[pid]: v for pid, v in w.items()}
            mine = [i for i in es._candidates(store, pq)['patient_idx'] if by_idx.get(i) is not None]
            gone = es._prune(store, pq, mine, by_idx)
            with patch.object(es, '_PRUNE_ENDS', False):
                starts_only = es._prune(store, pq, mine, by_idx)
            self.assertLessEqual(starts_only, gone, k)
            if totals is not None:
                totals['by_ends'] += len(gone - starts_only)
                totals['during'] += any(r['kind'] == 'during' for r in q.get('relations', []))
            pruned, live, pruned_linked = pruned + len(gone), live + len(mine), pruned_linked + len(gone & linked)
        return pruned, live, pruned_linked

    def test_pruning_on_and_off_give_the_same_bytes_on_the_synthetic_cohort(self):
        cases = [MatcherScalingTests.case(seed) for seed in range(SEEDS)]
        totals = Counter()
        pruned, live, _ = self.compare(_STATE['store'], cases, decided=range(0, SEEDS, 3), parallel=range(0, SEEDS, 6),
                                       totals=totals)
        print(f"\n  time pruning: {pruned} of {live} candidates with a window pruned over {SEEDS} queries, "
              f"{totals['by_ends']} of them by end summaries ({totals['during']} queries with `during`)", file=sys.stderr)
        self.assertGreater(pruned, live // 10)  # not vacuous

    def test_pruning_on_and_off_give_the_same_bytes_with_shared_variables_and_proper_events(self):
        totals = Counter()
        for seed in (5, 6):
            ingest = shared_variable_cohort(_STATE['ingest'], seed)
            self.assertGreater(sum(es.shares_variables(p['events']) for p in ingest['patients']), 10)
            reference = ref.build_tables(ingest)
            with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, tmp), verify='content') as store:
                self.assertEqual(table_mismatches(store, reference), {})
                pool = concept_pool(reference)
                cases = []
                for q in range(150):
                    rng = random.Random(f'fuzz-{seed}-{q}')
                    cases.append((random_query(rng, pool, ingest), random_windows(rng, ingest)))
                pruned, live, linked = self.compare(store, cases, decided=range(0, 150, 3), parallel=range(0, 150, 10),
                                                    totals=totals)
                totals.update(pruned=pruned, live=live, linked=linked)
                # Pruning on, against exhaustive matching over every patient's whole record.
                mismatches, dropped, _, _ = differential(store, ingest, reference, seeds=60)
                self.assertEqual((mismatches, dropped), ([], []), seed)
                es.close_pools()
        print(f"\n  time pruning, shared-variable cohorts: {totals['pruned']} of {totals['live']} pruned, "
              f"{totals['linked']} of them sharing variables, {totals['by_ends']} by end summaries", file=sys.stderr)
        self.assertGreater(totals['pruned'], totals['live'] // 10)
        self.assertGreater(totals['linked'], 0)  # sharing patients were pruned too

    def test_pruning_on_and_off_give_the_same_bytes_on_the_fixture(self):
        g = fixture()
        g['patients'] += shared_variable_patients()
        # A patient with enough events for `random_query` to anchor on: a proper exposure whose end is a seizure's
        # start, and an ongoing one.
        span = (day(-30), day(400))
        g['patients'].append({'patient_id': 'Q1', 'clock_id': 'patient_Q1', 'coverage': [cover('ehr', *span),
                                                                                         cover('pharmacy', *span)],
                              'events': [ev('fs', ['febrile_seizure'], (day(0), day(2))),
                                         {**ev('cbz', ['carbamazepine'], (day(20), day(25)), (day(20), day(60)),
                                               'pharmacy', 'exposure'), 'proper': True},
                                         {**ev('sz', ['seizure'], (day(40), day(50))), 'start_var': 'cbz.f'},
                                         ev('vpa', ['valproate'], (day(70), day(70)), (day(70), None), 'pharmacy',
                                            'exposure'),
                                         ev('dx', ['dravet'], (day(90), day(95)))]})
        reference = ref.build_tables(g)
        pool = concept_pool(reference)
        cases = []
        for seed in range(200):
            rng = random.Random(f'fixture-{seed}')
            cases.append((random_query(rng, pool, g), random_windows(rng, g)))
        cases.append((FIXTURE_QUERY, {p['patient_id']: {'start': day(-30), 'end': day(400)} for p in g['patients']}))
        totals = Counter()
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp)) as store:
            pruned, live, _ = self.compare(store, cases, decided=range(0, 201, 2), parallel=range(0, 201, 20),
                                           totals=totals)
            es.close_pools()
        self.assertGreater(pruned, 0)
        self.assertGreater(totals['by_ends'], 0)  # the end summaries prune here too

    def during(self, grace, start=('A', 'febrile_seizure'), within=('B', 'carbamazepine'), scope='any'):
        """A query whose step `start[0]` (concept `start[1]`) starts during step `within[0]`'s event, up to `grace`
        after it ends."""
        return {'profile': tg.PROFILE_ID, 'scope': scope,
                'steps': [{'id': within[0], 'concept': within[1]}, {'id': start[0], 'concept': start[1]}],
                'relations': [{'id': 'D', 'kind': 'during', 'from': within[0], 'to': start[0], 'grace': grace}]}

    def test_end_summaries_prune_at_the_grace_and_never_before_it(self):
        """D26. G1's carbamazepine exposure ends between day 60 and day 70 and its seizure diary entry starts from
        day 90, so an entry during the exposure needs a grace of at least 20 days: with 20 days the engine finds it
        possible, and the summaries must not prune; with 19 it is `none`, and they prune it without loading its
        events. P2's valproate exposure is ongoing, so no grace is too short for its summaries to prove anything.
        Planted defects fail: the grace's bound off by one, an exposure's start read as its end, and an ongoing
        exposure read as ending when it starts."""
        g = fixture()
        g['patients'].append({'patient_id': 'G1', 'clock_id': 'patient_G1',
                              'coverage': [cover('pharmacy', day(-30), day(400)), cover('diary', day(-30), day(400))],
                              'events': [ev('cbz', ['carbamazepine'], (day(30), day(30)), (day(60), day(70)),
                                            'pharmacy', 'exposure'),
                                         ev('sf', ['seizure_frequency'], (day(90), day(120)), source='diary',
                                            kind='diary_summary')]})
        windows = {p['patient_id']: {'start': day(-30), 'end': day(400)} for p in g['patients']}
        original = es._impossible

        def off_by_one(spec, idx, window):
            for x, y, k, sense in spec[2]:
                a, b = x[idx], y[idx]
                if a is not None and b is not None and sense == -1 and b - a >= k:
                    return True
            return original(spec, idx, window)

        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp)) as store:
            ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
            by_idx = {ids[pid]: w for pid, w in windows.items()}
            status = lambda q: {r['patient_id']: r['status'] for r in es.match(store, q, windows)['results']}
            def pruned(q, ends=True):
                pq = tg.PreparedQuery(q)
                live = [i for i in es._candidates(store, pq)['patient_idx'] if by_idx.get(i) is not None]
                with patch.object(es, '_PRUNE_ENDS', ends):
                    return {pid for pid, i in ids.items() if i in es._prune(store, pq, live, by_idx)}
            at, below = (self.during(k * DAY, ('A', 'seizure_frequency')) for k in (20, 19))
            self.assertEqual(status(at)['G1'], 'possible')
            self.assertEqual(status(below)['G1'], 'none')
            self.assertNotIn('G1', pruned(at))
            self.assertIn('G1', pruned(below))
            self.assertNotIn('G1', pruned(below, ends=False))  # the start summaries alone cannot see it
            with patch.object(es, '_PRUNE', False):
                self.assertEqual(json.dumps(es.match(store, below, windows)), json.dumps(es.match(store, below, windows)))
            # The bound off by one prunes the possible match at exactly the grace.
            with patch.object(es, '_impossible', off_by_one):
                self.assertEqual(status(at)['G1'], 'none')
            # An exposure's start read as its end (`max_start_hi` for `max_end_hi`): the grace looks too short.
            with patch.dict(es._SUMMARY, {('f', 'hi'): 1}):
                self.assertEqual(status(at)['G1'], 'none')
            # P2: diagnosis on day 10 during valproate from day 5, ongoing: possible with no grace at all.
            ongoing = self.during(0, ('A', 'dravet'), ('B', 'valproate'))
            self.assertEqual(status(ongoing)['P2'], 'possible')
            self.assertNotIn('P2', pruned(ongoing))
            # Summaries that read an ongoing exposure as ending when it starts prune it.
            store.db.execute('ALTER VIEW concept_postings RENAME TO postings_as_built')
            store.db.execute("""CREATE VIEW concept_postings AS SELECT concept, patient_idx, n, min_start_lo,
                                max_start_hi, coalesce(max_end_hi, max_start_hi) AS max_end_hi FROM postings_as_built""")
            self.assertEqual(status(ongoing)['P2'], 'none')

    def test_an_unsound_pruning_fails_the_comparison(self):
        """Planted defects: pruning on the unrelaxed query alone (a relaxation set could still match), and a bound
        off by one (a start exactly at the bound can still satisfy it). Each changes some result."""
        original_plan, original_impossible = es._prune_plan, es._impossible

        def unrelaxed_only(pq):
            return original_plan(pq)[:1]

        def off_by_one(spec, idx, window):
            steps, _, _ = spec
            anchor = {'START': window['start'], 'END': window['end']}
            for postings, lows, highs in steps:
                found = postings.get(idx)
                if found is not None and (any(found[1] <= anchor[a] + k for a, k in lows)
                                          or any(found[0] >= anchor[a] + k for a, k in highs)):
                    return True
            return original_impossible(spec, idx, window)

        changed = []
        with patch.object(es, '_prune_plan', unrelaxed_only):
            for seed in range(SEEDS):
                q, w = MatcherScalingTests.case(seed)
                with patch.object(es, '_PRUNE', False):
                    off = json.dumps(es.match(_STATE['store'], q, w))
                if json.dumps(es.match(_STATE['store'], q, w)) != off:
                    changed.append(seed)
                    if len(changed) >= 3:
                        break
        self.assertTrue(changed, 'pruning on the unrelaxed query alone went unnoticed')
        # Random windows rarely put a bound exactly on a start, so the boundary is checked by construction: P3's
        # one seizure starts at day 0 exactly, and so does each window's bound. Every scope's bound is met with
        # equality, which the engine accepts and the defect prunes.
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(fixture(), tmp)) as store:
            for scope, window in (('from_start', (day(0), day(5))), ('within', (day(-5), day(0))),
                                  ('after', (day(-5), day(0)))):
                q = {'profile': tg.PROFILE_ID, 'scope': scope, 'steps': [{'id': 'A', 'concept': 'febrile_seizure'}]}
                w = {'P3': {'start': window[0], 'end': window[1]}}
                right = {r['patient_id']: r['status'] for r in es.match(store, q, w)['results']}
                self.assertEqual(right['P3'], 'exact', scope)
                with patch.object(es, '_impossible', off_by_one):
                    wrong = {r['patient_id']: r['status'] for r in es.match(store, q, w)['results']}
                self.assertEqual(wrong['P3'], 'none', scope)

    def test_each_loaded_chunk_lies_inside_a_chunk_cut_before_pruning(self):
        """The loader scans a chunk's index range, so chunks are cut from the candidates before pruning and
        pruned patients are dropped from them: no chunk spans more indices than it would without pruning."""
        store, original = _STATE['store'], es._load_events
        ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
        seen = []
        def recording(s, idxs, per_step, linked=()):
            seen.append(list(idxs))
            return original(s, idxs, per_step, linked)
        checked = 0
        for seed in range(60):
            q, w = MatcherScalingTests.case(seed)
            by_idx = {ids[pid]: v for pid, v in w.items()}
            live = [i for i in es._candidates(store, tg.PreparedQuery(q))['patient_idx'] if by_idx.get(i) is not None]
            cut = [set(live[k:k + 3]) for k in range(0, len(live), 3)]
            seen.clear()
            with patch.object(es, '_load_events', recording):
                es.match(store, q, w, chunk=3)
            for part in seen:
                self.assertTrue(part and any(set(part) <= c for c in cut), (seed, part))
                checked += 1
        self.assertGreater(checked, 50)

    def test_pruning_refuses_candidates_out_of_order(self):
        """The summaries are read between the first and the last candidate, so `_prune` takes them only sorted."""
        store = _STATE['store']
        q, w = MatcherScalingTests.case(0)
        ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
        by_idx = {ids[pid]: v for pid, v in w.items()}
        live = [i for i in es._candidates(store, tg.PreparedQuery(q))['patient_idx'] if by_idx.get(i) is not None]
        self.assertGreater(len(live), 2)
        for wrong in (live[::-1], live[:1] + live):
            with self.assertRaises(es.StoreError) as caught:
                es._prune(store, tg.PreparedQuery(q), wrong, by_idx)
            self.assertEqual(caught.exception.code, 'PRUNE_CANDIDATES_NOT_SORTED')

    def test_the_slice_size_changes_nothing(self):
        """Summaries are read one slice of candidates at a time, to bound memory; any slice gives the same set."""
        store = _STATE['store']
        ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
        for seed in range(40):
            q, w = MatcherScalingTests.case(seed)
            pq, by_idx = tg.PreparedQuery(q), {ids[pid]: v for pid, v in w.items()}
            live = [i for i in es._candidates(store, pq)['patient_idx'] if by_idx.get(i) is not None]
            whole = es._prune(store, pq, live, by_idx)
            for size in (1, 7):
                self.assertEqual(es._prune(store, pq, live, by_idx, slice_size=size), whole, (seed, size))

    def test_a_pruned_candidate_carries_the_engines_own_none(self):
        """A pruned candidate's result is the dict the engine returns for it on its loaded events, key order
        included."""
        store, seen = _STATE['store'], 0
        ids = {i: pid for i, pid, *_ in store.rows('patients')}
        for seed in range(60):
            q, w = MatcherScalingTests.case(seed)
            pq = tg.PreparedQuery(q)
            by_idx = {i: w.get(pid) for i, pid in ids.items()}
            live = [i for i in es._candidates(store, pq)['patient_idx'] if by_idx[i] is not None]
            gone = sorted(es._prune(store, pq, live, by_idx))
            events = es._load_events(store, gone, es._step_concepts(pq.query)) if gone else {}
            for i in gone:
                engine = tg.match_prepared(pq, {'patient_id': ids[i], 'window': by_idx[i], 'events': events.get(i, [])},
                                           trusted=True)
                self.assertEqual(json.dumps(engine), json.dumps(es._NONE(ids[i])), (seed, ids[i]))
                seen += 1
        self.assertGreater(seen, 100)


class IngestNormalisationTests(unittest.TestCase):
    """Fields that restate the engine's default are normalised away at ingest (D25), so documents the engine
    reads alike have one input digest and one set of tables."""

    def test_explicit_defaults_give_the_same_input_digest_and_tables(self):
        g = fixture()
        g['patients'] += shared_variable_patients()
        explicit = deepcopy(g)
        for p in explicit['patients']:
            for e in p['events']:
                e.setdefault('start_var', e['event_id'] + '.s')
                if 'end' in e:
                    e.setdefault('end_var', e['event_id'] + '.f')
                    e.setdefault('proper', False)
        self.assertNotEqual(explicit, g)
        self.assertEqual(ref.input_digest(explicit), ref.input_digest(g))
        self.assertEqual(ref.build_tables(explicit), ref.build_tables(g))
        with tempfile.TemporaryDirectory() as tmp:
            plain, spelled = es.build(g, Path(tmp) / 'a'), es.build(explicit, Path(tmp) / 'b')
            self.assertEqual(plain.name, spelled.name)
            manifest = json.loads((spelled / 'manifest.json').read_text())
            self.assertEqual(manifest['input_digest'], ref.input_digest(g))
            with es.open_store(spelled, verify='content') as store:
                self.assertEqual(table_mismatches(store, ref.build_tables(explicit)), {})
        # Only exact restatements go: a variable named after another event's default, or after the event's own
        # default for the other end, is a real name.
        e = {'event_id': 'a', 'start_var': 'b.s', 'end_var': 'a.s', 'proper': True, 'end': [0, 1]}
        self.assertEqual(es.normal_event(e), e)
        self.assertEqual(ref.normalised(e), e)

    def test_the_synthetic_cohort_is_unchanged(self):
        """Normalisation is the identity on the synthetic cohort, so its input digest is unchanged (the recorded
        digests of `PerBucketDeriveTests` say the same); the MIMIC fixture's test checks its own ingest."""
        for p in _STATE['ingest']['patients']:
            for e in p['events']:
                self.assertEqual(es.normal_event(e), e, (p['patient_id'], e['event_id']))
                self.assertEqual(ref.normalised(e), e)


class BuildLeverTests(unittest.TestCase):
    """D28, part 3: the build levers change no byte of content and no identifier."""

    def test_rows_text_writes_what_row_line_writes(self):
        rng = random.Random(5)
        texts = ['', 'a', 'é', 'é', '\U0001f600', '"', '\\', '\n\t\r\x00\x1f\x7f', '  ', '{"k":[1,"\\""]}',
                 'Résumé de sortie', '퟿']
        ints = [0, -1, 1, 2 ** 63 - 1, -2 ** 63, 10 ** 30, -(10 ** 30)]
        kinds = [lambda: rng.choice(texts), lambda: rng.choice(ints), lambda: rng.choice((True, False)), lambda: None]
        for trial in range(300):
            width = rng.randint(1, 6)
            columns = [rng.sample(range(4), rng.randint(1, 4)) for _ in range(width)]
            rows = [tuple(kinds[rng.choice(c)]() for c in columns) for _ in range(rng.randint(1, 30))]
            self.assertEqual(es.rows_text(rows), ''.join(es.row_line(r) for r in rows), rows)
        # A type the tables never hold falls back to the row-by-row form, which is the definition.
        odd = [(1.5, 'x'), (2, 'y')]
        self.assertEqual(es.rows_text(odd), ''.join(es.row_line(r) for r in odd))
        self.assertEqual(es.rows_text([]), '')

    def test_the_levers_give_the_snapshot_without_them(self):
        g = fixture()
        g['patients'].append(series_patient())
        ids = set()
        with tempfile.TemporaryDirectory() as tmp:
            for k, (fast, staging) in enumerate([(True, None), (False, None), (True, 'ndjson'), (False, 'ndjson')]):
                with patch.object(es, '_FAST_ROWS', fast):
                    snapshot = es.build(g, Path(tmp) / str(k), staging=staging, stage_rows=2)
                    with es.open_store(snapshot, verify='content') as store:
                        self.assertEqual(table_mismatches(store, ref.build_tables(g)), {})
                ids.add(json.loads((snapshot / 'manifest.json').read_text())['snapshot_id'])
        self.assertEqual(len(ids), 1)


class PlantedDefectTests(unittest.TestCase):
    def test_a_dropped_closure_row_fails_the_table_comparison(self):
        original = es._closure_rows
        def drop(closure):
            rows = original(closure)
            victim = next(r for r in rows if r['basis'] == 'declared' and r['ancestor'] == 'sodium_channel_blocker')
            return [r for r in rows if r is not victim]
        with patch.object(es, '_closure_rows', drop), tempfile.TemporaryDirectory() as tmp:
            with es.open_store(es.build(_STATE['ingest'], tmp), verify='content') as store:
                wrong = table_mismatches(store, _STATE['reference'])
        self.assertLessEqual({'concept_closure', 'concept_postings'}, set(wrong))

    def test_a_widening_ignored_by_the_filter_fails_the_differential(self):
        narrow = lambda query: {s['id']: [s['concept']] for s in query['steps']}
        with patch.object(es, '_step_concepts', narrow):
            mismatches, dropped, _, _ = differential(_STATE['store'], _STATE['ingest'], _STATE['reference'], seeds=60)
        self.assertTrue(dropped, 'the planted defect went unnoticed')
        self.assertTrue(all(w['status'] == 'relaxed' for _, w in dropped))
        self.assertTrue(any(g.get('reason') == 'FILTERED' and w['status'] == 'relaxed' for _, g, w in mismatches))

    def test_a_loader_that_misses_events_fails_the_differential(self):
        """Survivors must see every event a tried relaxation set could bind. Two loader defects:
        one loads only each step's own concept (not its widenings); one loses each candidate's
        earliest event, which changes which event is first."""
        original = es._load_events
        def own_concepts_only(store, idxs, per_step, linked=()):
            return original(store, idxs, {s: cs[:1] for s, cs in per_step.items()}, linked)
        def without_earliest(store, idxs, per_step, linked=()):
            loaded = original(store, idxs, per_step, linked)
            return {i: sorted(evs, key=lambda e: (e['start'], e['id']))[1:] for i, evs in loaded.items()}
        for defect in (own_concepts_only, without_earliest):
            with self.subTest(defect=defect.__name__), patch.object(es, '_load_events', defect):
                mismatches, dropped, _, _ = differential(_STATE['store'], _STATE['ingest'], _STATE['reference'], seeds=40)
                self.assertEqual(dropped, [])  # the filter itself is intact
                self.assertTrue([m for m in mismatches if m[1].get('reason') != 'FILTERED'],
                                'the planted defect went unnoticed')


class SeriesTests(unittest.TestCase):
    """Store 3.1 (D27): the `series` table of measurement values."""

    def ingest(self):
        g = fixture()
        g['patients'].append(series_patient())
        return g

    def test_store_equals_the_reference_and_the_accessor_reads_it(self):
        g = self.ingest()
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp), verify='content') as store:
            self.assertEqual(table_mismatches(store, ref.build_tables(g)), {})
            reference = ref.table_digests(ref.build_tables(g))
            self.assertEqual({n: (t['rows'], t['content_digest']) for n, t in store.manifest['tables'].items()},
                             {n: (t['rows'], t['content_digest']) for n, t in reference.items()})
            self.assertEqual(store.manifest['input_digest'], ref.input_digest(g))
            rows = store.series('S-series')
            self.assertEqual([r['event_id'] for r in rows], ['m1', 'm2', 'm3'])
            self.assertEqual(rows[1], {'event_id': 'm2', 'item_class': 'seizure_frequency', 'start_lo': day(2),
                                       'start_hi': day(2) + 5, 'value_lexical': '1e-7', 'value_decimal': '0.0000001',
                                       'value_micro': None, 'unit': 'per day'})
            self.assertEqual(store.series('S-series', ['m3'])[0]['value_micro'], -12_000_000)
            self.assertEqual(store.series('P1'), [])
            self.assertEqual(store.series('nobody'), [])

    def test_series_changes_no_other_table_and_order_does_not_matter(self):
        g = self.ingest()
        without = deepcopy(g)
        del without['patients'][-1]['series']
        shuffled = deepcopy(g)
        shuffled['patients'][-1]['series'].reverse()
        with tempfile.TemporaryDirectory() as tmp:
            a = json.loads((es.build(g, Path(tmp) / 'a') / 'manifest.json').read_text())
            b = json.loads((es.build(without, Path(tmp) / 'b') / 'manifest.json').read_text())
            c = json.loads((es.build(shuffled, Path(tmp) / 'c', staging='ndjson', part_rows=1) / 'manifest.json').read_text())
            # The evidence stage's character cap only cuts staging files (one event a file here).
            d = json.loads((es.build(g, Path(tmp) / 'd', stage_evidence_chars=1) / 'manifest.json').read_text())
        self.assertEqual(a['snapshot_id'], c['snapshot_id'])
        self.assertEqual(a['snapshot_id'], d['snapshot_id'])
        self.assertNotEqual(a['input_digest'], b['input_digest'])
        self.assertEqual({n: t['content_digest'] for n, t in a['tables'].items() if n != 'series'},
                         {n: t['content_digest'] for n, t in b['tables'].items() if n != 'series'})
        self.assertEqual((a['tables']['series']['rows'], b['tables']['series']['rows']), (3, 0))

    def test_an_empty_series_is_no_series(self):
        """D28 (a D27 follow-up): `series: []` and an absent `series` have one input digest and one snapshot, in the
        store and in the reference; a document without `series: []` keeps its digest."""
        g = self.ingest()
        empty = deepcopy(g)
        for p in empty['patients']:
            p.setdefault('series', [])
        self.assertEqual(ref.input_digest(empty), ref.input_digest(g))
        with tempfile.TemporaryDirectory() as tmp:
            a = json.loads((es.build(g, Path(tmp) / 'a') / 'manifest.json').read_text())
            b = json.loads((es.build(empty, Path(tmp) / 'b') / 'manifest.json').read_text())
        self.assertEqual((a['input_digest'], a['snapshot_id']), (b['input_digest'], b['snapshot_id']))
        self.assertEqual(a['input_digest'], ref.input_digest(g))

    def test_refusals(self):
        def refused(code, change):
            g = self.ingest()
            change(g['patients'][-1])
            with tempfile.TemporaryDirectory() as tmp, self.assertRaises(es.IngestError) as raised:
                es.build(g, tmp)
            self.assertEqual(raised.exception.code, code)

        def at(i, **fields):
            return lambda p: p['series'][i].update(fields)
        refused('SERIES_UNKNOWN_EVENT', at(0, event_id='nope'))
        refused('SERIES_EVENT_NOT_POINT', at(0, event_id='x', item_class='valproate'))
        refused('DUPLICATE_SERIES_EVENT', at(0, event_id='m1'))
        refused('SERIES_ITEM_CLASS_MISMATCH', at(2, item_class='epilepsy'))
        refused('INVALID_SERIES_VALUE', at(1, value_lexical='<5'))
        refused('INVALID_SERIES_VALUE', at(1, value_decimal='1e-7'))
        refused('INVALID_SERIES_VALUE', at(1, value_micro=0))
        refused('INVALID_SERIES_VALUE', at(0, value_micro=7_350_001))
        refused('INVALID_SERIES_VALUE', at(0, value_micro=7350000.0))
        refused('INVALID_SERIES_UNIT', at(0, unit=' per day'))
        refused('INVALID_SERIES_UNIT', at(0, unit=''))
        refused('INVALID_SERIES', at(0, extra=1))
        refused('INVALID_SERIES', lambda p: p.update(series={}))
        # D28 (a D27 follow-up): an event id that cannot be hashed, or is not a non-empty string, is refused by name.
        for bad in (['m1'], {'m': 1}, 5, None, ''):
            refused('INVALID_SERIES', at(0, event_id=bad))
        # Spellings beyond the 28-digit decimal context: the micro-unit integer must be null, not rounded.
        for text, decimal in (('1000000000000.0000000000000000001', '1000000000000.0000000000000000001'),
                              ('9000000000000.00000000000000000049', '9000000000000.00000000000000000049')):
            refused('INVALID_SERIES_VALUE', at(0, value_lexical=text, value_decimal=decimal, value_micro=10 ** 18))
            g = self.ingest()
            g['patients'][-1]['series'][0].update(value_lexical=text, value_decimal=decimal, value_micro=None)
            es.validate_ingest(g)

    def test_series_values_agrees_with_the_measurement_decimal_rule(self):
        from decimal import Decimal
        from . import measurement_claims as mc
        from . import series_values as sv
        for text in ('58', '58.00', '-1.25', '+.5', '5.', '1e-7', '12E2', '1e308', '1e309', '1e-309', '', '.', 'e5',
                     '1.2.3', ' 1', '0x1', 'NaN', 'Infinity', '1' * 64, '1' * 65, '<5', '15 Alert'):
            try:
                want = ('ok', mc.decimal_value(text))
            except Exception as exc:  # noqa: BLE001 - the code is what is compared
                want = ('refused', str(exc))
            try:
                got = ('ok', sv.decimal_of(text))
            except sv.SeriesValueError as exc:
                got = ('refused', str(exc))
            self.assertEqual(got, want, text)
        self.assertEqual(sv.micro_value(Decimal('9223372036854.775807')), 2 ** 63 - 1)
        self.assertIsNone(sv.micro_value(Decimal('9223372036854.775808')))


class ContractTests(unittest.TestCase):
    def test_the_manifest_validates_and_tampering_is_refused(self):
        manifest = _STATE['store'].manifest
        schema = json.loads((ROOT / es.SCHEMA_PATH).read_text())
        Draft202012Validator.check_schema(schema)
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(manifest)), [])
        self.assertEqual(manifest['profile'], 'execution-store-3.1')
        self.assertEqual(set(manifest['builder']['artifacts']), {es.MODULE_PATH, es.SCHEMA_PATH})
        self.assertEqual(manifest['builder']['memory_limit'], es.DEFAULT_MEMORY_LIMIT)
        self.assertEqual(sorted(manifest['files']), sorted(es.file_digests(_STATE['snapshot'])))
        for mutate, code in [(lambda m: m.pop('input_digest'), 'INVALID_MANIFEST'),
                             (lambda m: m.pop('files'), 'INVALID_MANIFEST'),
                             (lambda m: m['tables']['events'].update(rows=-1), 'INVALID_MANIFEST'),
                             (lambda m: m['tables']['events']['columns'][5].update(type='DOUBLE'), 'INVALID_MANIFEST'),
                             (lambda m: m['tables']['events'].update(rows=1), 'BUCKET_DIGEST_MISMATCH'),
                             (lambda m: m['tables']['events']['buckets']['3'].update(content_digest='0' * 64),
                              'BUCKET_DIGEST_MISMATCH'),
                             (lambda m: m['tables']['events']['buckets'].pop('3'), 'BUCKET_DIGEST_MISMATCH'),
                             (lambda m: m.update(closure_version='other'), 'SNAPSHOT_ID_MISMATCH')]:
            broken = deepcopy(manifest)
            mutate(broken)
            with self.subTest(code=code), self.assertRaises(es.StoreError) as caught:
                es.validate_manifest(broken)
            self.assertEqual(caught.exception.code, code)
        # Neither the file digests nor the memory limit are part of the identity.
        other = deepcopy(manifest)
        other['builder']['memory_limit'] = '256MB'
        other['files'] = {path: '0' * 64 for path in other['files']}
        self.assertEqual(es.validate_manifest(other)['snapshot_id'], manifest['snapshot_id'])

    def test_verification_levels_are_named_and_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp)
            for level in ('files', 'content', False):
                with es.open_store(snapshot, verify=level) as store:
                    self.assertEqual(store.verified, level)
                    out = es.match(store, FIXTURE_QUERY, {})
                    self.assertEqual((out['verified'], out['context']['verified']), (level, level))
            with es.open_store(snapshot) as store:
                self.assertEqual(store.verified, 'files')  # the default
            for level in (True, 'bytes', None, 0):
                with self.subTest(level=level), self.assertRaises(es.StoreError) as caught:
                    es.open_store(snapshot, verify=level)
                self.assertEqual(caught.exception.code, 'INVALID_VERIFY_LEVEL')

    def test_the_directory_name_and_an_expected_identifier_pin_the_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), Path(tmp) / 'store')
            other = Path(tmp) / ('0' * 64)
            shutil.copytree(snapshot, other)
            with self.assertRaises(es.StoreError) as caught:  # a snapshot under another snapshot's name
                es.open_store(other)
            self.assertEqual(caught.exception.code, 'SNAPSHOT_DIRECTORY_MISMATCH')
            plain = Path(tmp) / 'copy'
            os.replace(other, plain)  # a name that is no identifier pins nothing
            with es.open_store(plain, expected_snapshot_id=snapshot.name) as store:
                self.assertEqual(store.snapshot_id, snapshot.name)
            with self.assertRaises(es.StoreError) as caught:
                es.open_store(plain, expected_snapshot_id='f' * 64)
            self.assertEqual(caught.exception.code, 'SNAPSHOT_ID_NOT_EXPECTED')
            # The match context names the exact files read.
            with es.open_store(snapshot) as store:
                context = es.match(store, FIXTURE_QUERY, {})['context']
                want = hashlib.sha256(es._json(store.manifest['files']).encode('utf-8')).hexdigest()
                self.assertEqual(context['files_digest'], want)

    def test_typed_staging_gives_the_snapshot_ndjson_staging_gives(self):
        """D26: Parquet staging (the default) and ndjson staging give one snapshot, for any number of rows per Parquet
        file; staging's options are refused by name; a refused build leaves nothing, Parquet files included."""
        g = fixture()
        g['patients'] += shared_variable_patients()
        g['patients'][0]['excluded'] = [{'source': 'ehr', 'concept': 'seizure', 'start': [day(3), day(4)],
                                         'reason': 'DROPPED'}]
        g['patients'][0]['events'][0].update(status='final', recorded_at=day(1), claim_sha256='0' * 64)
        sizes = []
        with tempfile.TemporaryDirectory() as tmp:
            names = set()
            for k, options in enumerate(({}, {'staging': 'ndjson'}, {'stage_rows': 1}, {'stage_rows': 7},
                                         {'staging': 'parquet', 'part_rows': 1})):
                watch = lambda name, info: sizes.append((options.get('staging', es.STAGING), info['staged_files']
                                                         )) if name == 'staged' else None
                names.add(es.build(g, Path(tmp) / str(k), on_stage=watch, **options).name)
            self.assertEqual(len(names), 1)
            self.assertEqual(es.STAGING, 'parquet')
            for options, code in (({'staging': 'csv'}, 'INVALID_STAGING'), ({'stage_rows': 0}, 'INVALID_STAGE_ROWS')):
                with self.subTest(code=code), self.assertRaises(es.StoreError) as caught:
                    es.StoreBuilder(Path(tmp) / 'never', {k: g[k] for k in ('profile', 'dataset_id', 'closure')},
                                    **options)
                self.assertEqual(caught.exception.code, code)
            root = Path(tmp) / 'refused'
            bad = deepcopy(g)
            bad['patients'][-1]['events'][0]['concepts'] = ['nowhere']
            with self.assertRaises(es.IngestError):
                es.build(bad, root, stage_rows=1)
            self.assertFalse(root.exists())
        self.assertTrue(all(n > 0 for _, n in sizes))

    def test_staging_can_live_elsewhere_and_stale_builds_are_swept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root, work = Path(tmp) / 'store', Path(tmp) / 'scratch'
            seen = []
            def watch(name, info):
                if name == 'staged':
                    seen.append((sorted(p.name for p in work.iterdir()), sorted(p.name for p in root.iterdir())))
            snapshot = es.build(fixture(), root, work_dir=work, on_stage=watch)
            self.assertEqual(snapshot.name, es.build(fixture(), Path(tmp) / 'plain').name)
            self.assertEqual(len(seen[0][0]), 1)  # the staging lived in work_dir ...
            self.assertTrue(all(n.startswith('.building-') for n in seen[0][0] + seen[0][1]))
            self.assertEqual(os.listdir(work), [])  # ... and nothing is left in either place
            self.assertEqual(os.listdir(root), [snapshot.name])
            old, new = root / '.building-old', root / '.building-new'
            old.mkdir()
            new.mkdir()
            os.utime(old, (time.time() - 3 * 86400,) * 2)
            self.assertEqual(es.sweep_stale_builds(root), [old])
            self.assertEqual(sorted(os.listdir(root)), sorted(['.building-new', snapshot.name]))

    def test_non_ascii_order_matches_the_reference(self):
        """Code-point order everywhere: precomposed and combining accents, an astral character and
        capitals, in patient ids, event ids and concepts; nulls in evidence and an ongoing end;
        four threads and odd fetch batches."""
        pre, comb, astral, u_pre, u_comb = 'é', 'é', '\U0001d518', 'ü', 'ü'
        ancestors = {pre: [], comb: [pre], 'E': [], astral: ['E'], 'z': []}
        self.assertEqual(len({pre, comb, u_pre, u_comb}), 4)  # precomposed and combining forms differ
        def patient(pid, events):
            return {'patient_id': pid, 'clock_id': f'c-{pid}', 'events': events,
                    'coverage': [cover('ehr', day(0), day(100)), cover('Ehr', day(5), day(50))]}
        g = {'profile': es.INGEST_PROFILE, 'dataset_id': f'unicode-{pre}', 'provenance': {'labels': {'note': u_comb + astral}},
             'closure': {'version': f'v-{comb}', 'ancestors': ancestors},
             'patients': [patient(pre, [ev(u_pre, [comb], (day(1), day(1)), evidence_null=None),
                                        ev(u_comb, [pre, 'z'], (day(2), day(3)), (day(4), None))]),
                          patient(comb, [ev(astral, [astral], (day(1), day(2)), source='Ehr', nested={'x': None})]),
                          patient('E', [ev('E', ['E'], (day(0), day(0))), ev('e', ['z'], (day(0), day(0)))]),
                          patient(astral, []), patient('z', [ev(pre, [pre, 'E'], (day(9), day(9)))])]}
        g['patients'][0]['excluded'] = [{'source': 'ehr', 'concept': astral, 'start': [day(5), day(6)], 'reason': comb}]
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(g, tmp, threads=4, fetch_rows=3)
            with es.open_store(snapshot, threads=4, verify='content', fetch_rows=3) as store:
                tables = ref.build_tables(g)
                self.assertEqual(table_mismatches(store, tables), {})
                want = ref.table_digests(tables)
                for name in es.TABLES:
                    recorded = store.manifest['tables'][name]
                    self.assertEqual({k: recorded[k] for k in ('rows', 'content_digest', 'buckets')}, want[name], name)
                self.assertEqual(store.manifest['input_digest'], ref.input_digest(g))
                self.assertEqual([r[1] for r in sorted(store.rows('patients'))], sorted(p['patient_id'] for p in g['patients']))
                self.assertEqual(store.rows('patients')[0][1], 'E')  # capitals before lower case, astral last
                self.assertEqual(max(r[1] for r in store.rows('patients')), astral)
            reordered = deepcopy(g)
            reordered['patients'].reverse()
            for p in reordered['patients']:
                p['events'].reverse()
                p['coverage'].reverse()
            self.assertEqual(es.build(reordered, Path(tmp) / 'again', fetch_rows=1).name, snapshot.name)

    def rewrite(self, snapshot, path, select):
        """Replace one Parquet file with `select` over its current rows, and record the new file's
        byte digest in the manifest (the identity and content digests are left as they were)."""
        target = snapshot / path
        with es.open_store(snapshot, verify=False) as store:
            source = f"read_parquet('{target}')"
            store.db.execute(f"COPY ({select.format(src=source)}) TO '{target}.new' (FORMAT parquet)")
        os.replace(f'{target}.new', target)
        manifest = json.loads((snapshot / 'manifest.json').read_text())
        manifest['files'][path] = hashlib.sha256(target.read_bytes()).hexdigest()
        (snapshot / 'manifest.json').write_text(json.dumps(manifest))

    def test_a_flipped_byte_fails_the_file_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp)
            victim = snapshot / 'concept_postings.parquet'
            data = bytearray(victim.read_bytes())
            data[len(data) // 2] ^= 0x01
            victim.write_bytes(bytes(data))
            with self.assertRaises(es.StoreError) as caught:
                es.open_store(snapshot)
            self.assertEqual(caught.exception.code, 'SNAPSHOT_FILE_MISMATCH')
            self.assertIn('concept_postings.parquet', str(caught.exception))

    def test_different_valid_content_with_a_matching_byte_digest_fails_the_content_and_identity_checks(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp)
            self.rewrite(snapshot, 'patients.parquet',
                         "SELECT patient_idx, patient_id || 'x' AS patient_id, bucket, shared_variables FROM {src}")
            with es.open_store(snapshot) as store:  # the bytes are what the manifest now says
                self.assertEqual(store.verified, 'files')
            with self.assertRaises(es.StoreError) as caught:
                es.open_store(snapshot, verify='content')
            self.assertEqual(caught.exception.code, 'SNAPSHOT_CONTENT_MISMATCH')
            self.assertIn('patients/all', str(caught.exception))
            with es.open_store(snapshot, verify=False) as store:
                self.assertNotEqual(store.derived_snapshot_id(), store.snapshot_id)
            with self.assertRaises(es.StoreError) as caught:  # build must not reuse it either
                es.build(fixture(), tmp)
            self.assertEqual(caught.exception.code, 'SNAPSHOT_CONTENT_MISMATCH')
            # Skipping verification is explicit, and the match result says so.
            with es.open_store(snapshot, verify=False) as store:
                out = es.match(store, FIXTURE_QUERY, {})
                self.assertEqual((out['verified'], out['context']['verified']), (False, False))

    def test_swapped_rows_still_pass_the_content_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp)
            events = next(p for p in sorted(es.file_digests(snapshot)) if p.startswith('events/'))
            for path, select in (('concept_closure.parquet', 'SELECT * FROM {src} ORDER BY concept DESC, ancestor DESC'),
                                 (events, 'SELECT * FROM {src} ORDER BY event_id DESC')):
                before = (snapshot / path).read_bytes()
                self.rewrite(snapshot, path, select)
                self.assertNotEqual((snapshot / path).read_bytes(), before)
            with es.open_store(snapshot, verify='content') as store:
                self.assertEqual(store.verified, 'content')
                self.assertEqual(store.derived_snapshot_id(), snapshot.name)

    def test_a_missing_partition_is_refused_on_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp)
            shutil.rmtree(next((snapshot / 'events').iterdir()))
            with self.assertRaises(es.StoreError) as caught:
                es.open_store(snapshot)
            self.assertEqual(caught.exception.code, 'SNAPSHOT_FILE_MISMATCH')
            with self.assertRaises(es.StoreError) as caught:
                es.open_store(snapshot, verify='content')
            self.assertEqual(caught.exception.code, 'SNAPSHOT_CONTENT_MISMATCH')

    def test_malformed_ingest_is_refused_by_name_before_anything_is_written(self):
        def patient(g, pid='P1'):
            return next(p for p in g['patients'] if p['patient_id'] == pid)
        cases = {
            'DUPLICATE_EVENT_ID': lambda g: patient(g)['events'].append(deepcopy(patient(g)['events'][0])),
            'INVERTED_BOUNDS': lambda g: patient(g)['events'][2].update(start=[day(5), day(4)]),
            'END_BEFORE_START': lambda g: patient(g)['events'][1].update(end=[day(29), day(70)]),
            'UNKNOWN_CONCEPT': lambda g: patient(g)['events'][0].update(concepts=['not_in_closure']),
            'CLOSURE_UNKNOWN_ANCESTOR': lambda g: g['closure']['ancestors'].update(seizure=['nowhere']),
            'CLOSURE_NOT_TRANSITIVE': lambda g: g['closure']['ancestors'].update(dravet=['dee', 'generalized_epilepsy']),
            'CLOSURE_CYCLE': lambda g: g['closure']['ancestors'].update(seizure=['seizure']),
            'DUPLICATE_PATIENT_ID': lambda g: g['patients'].append(deepcopy(patient(g))),
            'INVALID_TIME': lambda g: patient(g)['events'][0].update(start=[day(0) + 0.5, day(1)]),
            'TIME_OUT_OF_RANGE': lambda g: patient(g)['events'][0].update(start=[-1, day(1)]),
            'DUPLICATE_COVERAGE': lambda g: patient(g)['coverage'].append(dict(patient(g)['coverage'][0])),
            'UNSUPPORTED_INGEST_PROFILE': lambda g: g.update(profile='other'),
            'INGEST_PATIENT_NOT_BOX_REPRESENTABLE': lambda g: patient(g, 'P3').update(box_representable=False),
            'INVALID_SELECTION': lambda g: g.update(provenance={'selection': {'mode': 'retrospective'}}),
            # The record fields (D26): a string, a sha256 or a time of the patient's clock, or absent.
            'INVALID_RECORD_FIELD': lambda g: patient(g)['events'][0].update(claim_sha256='ABC'),
            'INVALID_RECORD_FIELD ': lambda g: patient(g)['events'][0].update(status=''),
            'INVALID_RECORD_FIELD  ': lambda g: patient(g)['events'][0].update(selection_state=None),
            'INVALID_TIME ': lambda g: patient(g)['events'][0].update(recorded_at=day(1) + 0.5),
            'TIME_OUT_OF_RANGE ': lambda g: patient(g)['events'][0].update(available_at=-1),
            'INVALID_EVENT': lambda g: patient(g)['events'][0].update(recorded_at_us=day(1)),
        }
        for code, mutate in cases.items():
            g = fixture()
            mutate(g)
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(es.IngestError) as caught:
                    es.build(g, Path(tmp) / 'store')
                self.assertEqual(caught.exception.code, code.strip())
                self.assertFalse((Path(tmp) / 'store').exists())
        g = fixture()
        patient(g)['coverage'][0].update(lo=day(500))
        with self.assertRaises(es.IngestError) as caught:
            es.validate_ingest(g)
        self.assertEqual(caught.exception.code, 'INVERTED_BOUNDS')
        g = fixture()
        patient(g)['box_representable'] = True  # declared box-representable is accepted
        es.validate_ingest(g)

    def test_a_stream_is_refused_by_name_and_leaves_nothing_behind(self):
        """A duplicate patient anywhere in the stream (found in DuckDB at `finish`), a bad patient
        mid-stream, an empty stream or bad provenance: refused, with nothing written under the root."""
        g = fixture()
        header = {k: g[k] for k in ('profile', 'dataset_id', 'closure')}
        tab = deepcopy(g['patients'][1])
        tab['patient_id'] = 'P\t2'
        bad = deepcopy(g['patients'][2])
        bad['events'][0]['concepts'] = ['nowhere']
        cases = {'DUPLICATE_PATIENT_ID': (g['patients'] + [deepcopy(g['patients'][1])], None),
                 'UNKNOWN_CONCEPT': (g['patients'][:2] + [bad] + g['patients'][3:], None),
                 'INVALID_PATIENT_ID': (g['patients'][:1] + [tab], None),
                 'NO_PATIENTS': ([], None),
                 'INVALID_LABELS': (g['patients'], {'labels': {'x': 1}})}
        for code, (patients, provenance) in cases.items():
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / 'deep' / 'store'
                builder = es.StoreBuilder(root, header)
                with self.assertRaises(es.IngestError) as caught:
                    for p in patients:
                        builder.add(p)
                    builder.finish(provenance)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(os.listdir(tmp), [])
                with self.assertRaises(es.StoreError) as closed:  # a refused builder takes nothing more
                    builder.add(g['patients'][0])
                self.assertEqual(closed.exception.code, 'BUILDER_CLOSED')
        # The same duplicate through the dict wrapper, into a root that already holds a snapshot.
        with tempfile.TemporaryDirectory() as tmp:
            kept = es.build(g, tmp)
            with self.assertRaises(es.IngestError) as caught:
                es.build({**g, 'patients': g['patients'] + [deepcopy(g['patients'][0])]}, tmp)
            self.assertEqual(caught.exception.code, 'DUPLICATE_PATIENT_ID')
            self.assertEqual(os.listdir(tmp), [kept.name])
        # Leaving the block unfinished discards the build.
        with tempfile.TemporaryDirectory() as tmp:
            with es.StoreBuilder(Path(tmp) / 'store', header) as builder:
                builder.add(g['patients'][0])
            self.assertEqual(os.listdir(tmp), [])


class RecordFieldTests(unittest.TestCase):
    """Store 3.0 (D26): the D5 record fields are typed event columns, and the free-form evidence its own table."""

    def test_record_fields_are_columns_and_evidence_comes_back_as_given(self):
        g = fixture()
        sha = hashlib.sha256(b'row').hexdigest()
        g['patients'][0]['events'][0].update(status='final', selection_state='accepted', recorded_at=day(2),
                                             available_at=day(3), claim_id='claim-1', claim_sha256=sha,
                                             source_table='diagnoses', record_sha256=sha, file_sha256=sha)
        g['patients'][1]['events'][0]['evidence']['nested'] = {'list': [1, None, 'é'], 'flag': False}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp), verify='content') as store:
            self.assertEqual(table_mismatches(store, ref.build_tables(g)), {})
            names = [c for c, _ in es.TABLES['events']]
            rows = {(r[0], r[1]): dict(zip(names, r)) for r in store.rows('events')}
            ids = {pid: i for i, pid, *_ in store.rows('patients')}
            first = rows[(ids['P1'], 'fs')]
            self.assertEqual({f: first[f] for f in es.RECORD_FIELDS},
                             {'status': 'final', 'selection_state': 'accepted', 'recorded_at': day(2),
                              'available_at': day(3), 'claim_id': 'claim-1', 'claim_sha256': sha,
                              'source_table': 'diagnoses', 'record_sha256': sha, 'file_sha256': sha})
            self.assertTrue(all(v is None for f, v in rows[(ids['P1'], 'cbz')].items() if f in es.RECORD_FIELDS))
            self.assertNotIn('evidence', names)
            # The evidence table holds each event's evidence as given: the same object, the same canonical text.
            for p in g['patients']:
                stored = store.evidence(p['patient_id'])
                self.assertEqual(stored, {e['event_id']: e['evidence'] for e in p['events']})
                for e in p['events']:
                    self.assertEqual(es._json(stored[e['event_id']]), es._json(e['evidence']))
            self.assertEqual(store.evidence('NOBODY'), {})
            self.assertEqual(store.manifest['tables']['event_evidence']['rows'], store.manifest['tables']['events']['rows'])

    def test_matching_never_reads_the_evidence(self):
        """`match` reads no row of `event_evidence`: with the table replaced by a view that fails when read, every
        result is the same, byte for byte."""
        store = _STATE['store']
        want = [json.dumps(es.match(store, *MatcherScalingTests.case(seed))) for seed in range(30)]
        store.db.execute('ALTER VIEW event_evidence RENAME TO event_evidence_kept')
        try:
            store.db.execute("""CREATE VIEW event_evidence AS SELECT error('event_evidence was read')::INTEGER AS
                                patient_idx, NULL::VARCHAR AS event_id, NULL::VARCHAR AS evidence, 0 AS bucket""")
            with self.assertRaises(Exception):
                store.query('SELECT * FROM event_evidence')
            for seed in range(30):
                self.assertEqual(json.dumps(es.match(store, *MatcherScalingTests.case(seed))), want[seed], seed)
        finally:
            store.db.execute('DROP VIEW IF EXISTS event_evidence')
            store.db.execute('ALTER VIEW event_evidence_kept RENAME TO event_evidence')

    def test_the_synthetic_converter_gives_each_event_its_record_fields(self):
        cohort, ingest = _STATE['cohort'], _STATE['ingest']
        records = {f"{r['source']}/{r['record_id']}": r for p in cohort['patients'] for r in p['records']}
        events = [e for p in ingest['patients'] for e in p['events']]
        for e in events:
            r = records[e['event_id'].split('#')[0]]
            want = {'recorded_at': si.micros(r['recorded_at']), 'available_at': si.micros(r['available_at'])}
            if r.get('status') is not None:
                want['status'] = r['status']
            self.assertEqual({f: e[f] for f in es.RECORD_FIELDS if f in e}, want)
            self.assertFalse({'recorded_at', 'available_at', 'status'} & set(e['evidence']))
        late = sum(1 for e in events if e['available_at'] > e['start'][1])
        self.assertGreater(late, len(events) // 2)  # availability is kept, though selection does not apply it
        closed = [e for e in events if e['kind'] == 'medication_exposure' and e['end'][1] is not None]
        self.assertTrue(all({'recorded_at', 'available_at', 'status'} <= set(e['evidence']['stop']) for e in closed))
        store = _STATE['store']
        for p in ingest['patients'][:6]:
            self.assertEqual(store.evidence(p['patient_id']), {e['event_id']: e['evidence'] for e in p['events']})


class SyntheticConverterTests(unittest.TestCase):
    def test_times_count_microseconds_from_the_fixed_origin(self):
        self.assertEqual(si.micros('0001-01-01T00:00'), 0)
        self.assertEqual(si.micros('0001-01-02T00:01'), DAY + 60_000_000)
        self.assertEqual(si.micros('9999-12-31T23:59:59.999999'), es.MAX_TIME)

    def test_the_converter_reads_records_not_truth(self):
        cohort, ingest = _STATE['cohort'], _STATE['ingest']
        text = json.dumps(ingest)
        self.assertNotIn('truth', text)
        mentions = sum(1 for p in cohort['patients'] for r in p['records'] for m in r.get('mentions', [])
                       if m['assertion'] == 'present' and m['experiencer'] == 'patient')
        events = [e for p in ingest['patients'] for e in p['events']]
        self.assertEqual(sum(e['kind'] == 'mention' for e in events), mentions)
        skipped = ingest['provenance']['skipped']
        self.assertGreater(skipped['mention_absent_patient'], 0)
        self.assertGreater(skipped['unknown_code_local'], 0)
        # Every coded record resolves through the cohort's codings.
        codes = {(c['system'], c['code']): cid for cid, k in cohort['concepts'].items() for c in k['codings']}
        records = {f"{r['source']}/{r['record_id']}": r for p in cohort['patients'] for r in p['records']}
        for e in events:
            if e['kind'] != 'mention':
                r = records[e['event_id']]
                self.assertEqual(e['concepts'], [codes[(r['coding']['system'], r['coding']['code'])]])
                self.assertEqual(e['start'], [si.micros(r['occurred']['earliest']), si.micros(r['occurred']['latest'])])

    def test_a_mention_ranges_from_birth_to_its_letter_and_holds_its_true_time(self):
        """D30. The converter never reads the generator's truth; this test does, to check the bound. A mention
        timed by its letter (the timing before D30) missed its true time for all but one of the 2,444 mentions;
        from birth (or an earlier record) to the letter's latest time, every true time is inside."""
        cohort, ingest = _STATE['cohort'], _STATE['ingest']
        self.assertEqual(ingest['provenance']['mention_time'],
                         'at_or_before_letter_v1: from birth, or an earlier record, to the letter')
        legacy = list(si.iter_patients(cohort, {}, 'letter'))
        counts = Counter()
        for p, converted, before in zip(sorted(cohort['patients'], key=lambda p: p['patient_id']), ingest['patients'], legacy):
            truth = {e['event_id']: e for e in p['truth']['events']}
            records = {r['record_id']: r for r in p['records']}
            birth = datetime.fromisoformat(p['birth_date'][:10] + 'T00:00')
            old = {e['event_id']: e for e in before['events']}
            for e in converted['events']:
                if e['kind'] != 'mention':
                    self.assertEqual(e, old[e['event_id']])
                    continue
                record_id, i = e['event_id'].split('/', 1)[1].split('#m')
                record = records[record_id]
                day = truth[record['mentions'][int(i)]['truth_event_id']]['day']
                lo = si.micros((birth + timedelta(days=day)).isoformat(timespec='minutes'))
                hi = lo + 24 * 3600 * 10**6 - 1  # the truth is a day
                inside = lambda rng: rng[0] <= hi and lo <= rng[1]
                self.assertEqual(e['start'], [si.earliest(p), si.micros(record['occurred']['latest'])])
                counts['inside'] += inside(e['start'])
                counts['inside before D30'] += inside(old[e['event_id']]['start'])
                counts['mentions'] += 1
        self.assertEqual(counts, Counter({'mentions': 2444, 'inside': 2444, 'inside before D30': 1}))

    def test_orders_and_superseded_records_are_not_events_and_selection_is_recorded(self):
        cohort, ingest = _STATE['cohort'], _STATE['ingest']
        events = {e['event_id']: e for p in ingest['patients'] for e in p['events']}
        records = [r for p in cohort['patients'] for r in p['records']]
        ordered = [r for r in records if r.get('status') == 'ordered']
        self.assertEqual(len(ordered), 40)  # 39 test orders and one ordered procedure
        self.assertFalse(any(f"{r['source']}/{r['record_id']}" in events for r in ordered))
        self.assertNotIn('test_order', {e['kind'] for e in events.values()})
        self.assertNotIn('genetics/C001-R0077', events)  # superseded by its amendment
        self.assertIn('genetics/C001-R0078', events)
        skipped = ingest['provenance']['skipped']
        self.assertEqual((skipped['status_ordered_test_order'], skipped['status_ordered_procedure'],
                          skipped['superseded_genetic_result']), (39, 1, 1))
        selection = ingest['provenance']['selection']
        self.assertEqual((selection['mode'], selection['version'], selection['available_at']),
                         ('retrospective', 'latest', 'not_applied'))
        self.assertEqual(selection['records_available_after_occurrence'], 5612)
        self.assertEqual(_STATE['store'].manifest['selection'], selection)
        self.assertEqual(ingest['provenance']['converter_sha256'],
                         hashlib.sha256((ROOT / 'patterns/synthetic_ingest.py').read_bytes()).hexdigest())

    def test_medications_end_where_a_stop_is_recorded_and_coverage_is_the_record_span(self):
        ingest, cohort = _STATE['ingest'], _STATE['cohort']
        exposures = [e for p in ingest['patients'] for e in p['events'] if e['kind'] == 'medication_exposure']
        closed = [e for e in exposures if e['end'][1] is not None]
        self.assertTrue(closed and all(e['evidence']['stop']['status'] == 'stopped' for e in closed))
        self.assertTrue(all(e['end'][0] >= e['start'][0] for e in exposures))
        self.assertGreater(len(exposures), len(closed))
        p, c = ingest['patients'][0], cohort['patients'][0]
        diary = [r for r in c['records'] if r['source'] == 'diary']
        self.assertIn(cover('diary', min(si.micros(r['occurred']['earliest']) for r in diary),
                            max(si.micros(r['occurred']['latest']) for r in diary), 'record_span'), p['coverage'])
        self.assertEqual(ingest['closure']['ancestors']['carbamazepine'], ['antiseizure', 'sodium_channel_blocker'])
        self.assertEqual(ingest['closure']['ancestors']['dravet'], ['dee', 'epilepsy', 'generalized_epilepsy'])


if __name__ == '__main__':
    unittest.main()
