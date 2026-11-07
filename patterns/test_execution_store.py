"""Execution store, stage 1: tables against the pure-Python reference, the candidate filter
against exhaustive matching, determinism, planted defects, the manifest and ingest refusals.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
from copy import deepcopy
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
    ids = {i: p for i, p, _ in store.rows('patients')}
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
            idx = {pid: i for i, pid, _ in store.rows('patients')}
            postings = {(c, p): (n, lo, hi) for c, p, n, lo, hi in store.rows('concept_postings')}
            # One event coded dee and dravet: one posting each for dee and epilepsy, not two.
            self.assertEqual(postings[('dee', idx['P2'])], (1, day(10), day(10)))
            self.assertEqual(postings[('epilepsy', idx['P2'])], (1, day(10), day(10)))
            self.assertEqual(postings[('seizure', idx['P1'])], (2, day(0), day(120)))
            self.assertEqual(postings[('antiseizure', idx['P1'])], (1, day(30), day(30)))
            self.assertNotIn('unused', {c for c, _ in postings})
            events = {(store.rows('patients')[i][1], e): (elo, ehi, ongoing)
                      for i, e, _, _, _, _, _, elo, ehi, ongoing, _ in store.rows('events')}
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
        q, w = MatcherScalingTests.case(0)
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
        def own_concepts_only(store, idxs, per_step):
            return original(store, idxs, {s: cs[:1] for s, cs in per_step.items()})
        def without_earliest(store, idxs, per_step):
            loaded = original(store, idxs, per_step)
            return {i: sorted(evs, key=lambda e: (e['start'], e['id']))[1:] for i, evs in loaded.items()}
        for defect in (own_concepts_only, without_earliest):
            with self.subTest(defect=defect.__name__), patch.object(es, '_load_events', defect):
                mismatches, dropped, _, _ = differential(_STATE['store'], _STATE['ingest'], _STATE['reference'], seeds=40)
                self.assertEqual(dropped, [])  # the filter itself is intact
                self.assertTrue([m for m in mismatches if m[1].get('reason') != 'FILTERED'],
                                'the planted defect went unnoticed')


class ContractTests(unittest.TestCase):
    def test_the_manifest_validates_and_tampering_is_refused(self):
        manifest = _STATE['store'].manifest
        schema = json.loads((ROOT / es.SCHEMA_PATH).read_text())
        Draft202012Validator.check_schema(schema)
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(manifest)), [])
        self.assertEqual(manifest['profile'], 'execution-store-2.0')
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
                         "SELECT patient_idx, patient_id || 'x' AS patient_id, bucket FROM {src}")
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
        }
        for code, mutate in cases.items():
            g = fixture()
            mutate(g)
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                with self.assertRaises(es.IngestError) as caught:
                    es.build(g, Path(tmp) / 'store')
                self.assertEqual(caught.exception.code, code)
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
