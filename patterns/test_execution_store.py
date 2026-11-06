"""Execution store, stage 1: tables against the pure-Python reference, the candidate filter
against exhaustive matching, determinism, planted defects, the manifest and ingest refusals.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import random
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
    _STATE['store'] = es.open_store(_STATE['snapshot'])
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
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(fixture(), tmp)) as store:
            self.assertEqual(table_mismatches(store, ref.build_tables(fixture())), {})

    def test_an_empty_partitioned_table_is_written_and_read_back(self):
        g = fixture()
        for p in g['patients']:
            p['coverage'] = []
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(g, tmp)) as store:
            self.assertEqual(store.manifest['tables']['coverage']['rows'], 0)
            self.assertEqual(table_mismatches(store, ref.build_tables(g)), {})

    def test_manifest_digests_equal_the_reference_digests(self):
        manifest = _STATE['store'].manifest
        for name, rows in _STATE['reference'].items():
            with self.subTest(table=name):
                self.assertEqual(manifest['tables'][name]['rows'], len(rows))
                self.assertEqual(manifest['tables'][name]['content_digest'], ref.digest(rows))
        self.assertTrue(_STATE['store'].verify())

    def test_postings_count_each_event_once_through_the_closure(self):
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(fixture(), tmp)) as store:
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
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(fixture(), tmp)) as store:
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
            self.assertTrue(buckets and all(b.startswith('bucket=') for b in buckets), name)
        self.assertEqual(len(list((snapshot / 'events').iterdir())), es.BUCKETS)


class PlantedDefectTests(unittest.TestCase):
    def test_a_dropped_closure_row_fails_the_table_comparison(self):
        original = es._closure_rows
        def drop(closure):
            rows = original(closure)
            victim = next(r for r in rows if r['basis'] == 'declared' and r['ancestor'] == 'sodium_channel_blocker')
            return [r for r in rows if r is not victim]
        with patch.object(es, '_closure_rows', drop), tempfile.TemporaryDirectory() as tmp:
            with es.open_store(es.build(_STATE['ingest'], tmp)) as store:
                wrong = table_mismatches(store, _STATE['reference'])
        self.assertEqual(sorted(wrong), ['concept_closure', 'concept_postings'])

    def test_a_widening_ignored_by_the_filter_fails_the_differential(self):
        narrow = lambda query: {s['id']: [s['concept']] for s in query['steps']}
        with patch.object(es, '_step_concepts', narrow):
            mismatches, dropped, _, _ = differential(_STATE['store'], _STATE['ingest'], _STATE['reference'], seeds=60)
        self.assertTrue(dropped, 'the planted defect went unnoticed')
        self.assertTrue(all(w['status'] == 'relaxed' for _, w in dropped))
        self.assertTrue(any(g.get('reason') == 'FILTERED' and w['status'] == 'relaxed' for _, g, w in mismatches))


class ContractTests(unittest.TestCase):
    def test_the_manifest_validates_and_tampering_is_refused(self):
        manifest = _STATE['store'].manifest
        schema = json.loads((ROOT / es.SCHEMA_PATH).read_text())
        Draft202012Validator.check_schema(schema)
        self.assertEqual(list(Draft202012Validator(schema).iter_errors(manifest)), [])
        self.assertEqual(manifest['profile'], 'execution-store-1.0')
        self.assertEqual(set(manifest['builder']['artifacts']), {es.MODULE_PATH, es.SCHEMA_PATH})
        for mutate, code in [(lambda m: m.pop('input_digest'), 'INVALID_MANIFEST'),
                             (lambda m: m['tables']['events'].update(rows=-1), 'INVALID_MANIFEST'),
                             (lambda m: m['tables']['events'].update(rows=1), 'SNAPSHOT_ID_MISMATCH'),
                             (lambda m: m.update(closure_version='other'), 'SNAPSHOT_ID_MISMATCH')]:
            broken = deepcopy(manifest)
            mutate(broken)
            with self.subTest(code=code), self.assertRaises(es.StoreError) as caught:
                es.validate_manifest(broken)
            self.assertEqual(caught.exception.code, code)

    def test_a_snapshot_whose_files_changed_fails_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = es.build(fixture(), tmp)
            with es.open_store(snapshot) as store:
                store.db.execute(f"COPY (SELECT patient_idx, patient_id || 'x' AS patient_id, bucket FROM patients) "
                                 f"TO '{snapshot}/patients-new.parquet' (FORMAT parquet)")
            os.replace(snapshot / 'patients-new.parquet', snapshot / 'patients.parquet')
            with es.open_store(snapshot) as store, self.assertRaises(es.StoreError) as caught:
                store.verify()
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
