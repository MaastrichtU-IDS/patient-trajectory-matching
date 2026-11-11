"""The store query endpoints: aggregate counts only, and only the open MIMIC-IV demo.

The stores here are built from the synthetic MIMIC fixture (`examples/mimic-store-ingest-chartevents`), never from
real data: once under the open demo's dataset id and once under a credentialed dataset id. The endpoints decide by
content, the open demo's 100 subject ids, so the fixture is served only with its own pin passed in (`demo_pin`, a
test hook); with the real pin it is refused, however it is labelled. One test runs against a real demo store when one
is built locally (`PTM_MIMIC_STORE`, the default build location, or the scratch store of the stage 1 run), and is
skipped otherwise. Needs `patterns/requirements-store.lock.txt` (DuckDB).
"""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import server  # noqa: E402
import store_query  # noqa: E402
from patterns import execution_store as es  # noqa: E402
from patterns import mimic_store_ingest as msi  # noqa: E402

FIXTURE = ROOT / 'examples/mimic-store-ingest-chartevents'
I = 'https://example.org/trajectory/mimic-record-item/'
G = 'https://example.org/trajectory/mimic-record-item-category/'
RATE = I + '910001'        # chart item, unit 'per min', values 70 upward
FLUID = I + '2001'         # inputevents item (an interval)
MEDICATION = I + '2003'    # inputevents item under 'Medications'


def query(**extra):
    """A two-step query on the fixture: the rate at or above a threshold, then a fluid within a day."""
    spec = {'window': 'all_stays', 'scope': 'any', 'budget': '1',
            'steps': [{'id': 'A', 'concept': RATE, 'occurrence': 'any',
                       'condition': {'unit': 'per min', 'lower': {'op': 'ge', 'value': '74'}},
                       'widen_value': {'condition': {'unit': 'per min', 'lower': {'op': 'ge', 'value': '70'}},
                                       'cost': '0.5'}},
                      {'id': 'B', 'concept': FLUID, 'occurrence': 'any'}],
            'relations': [{'kind': 'after', 'from': 'A', 'to': 'B', 'min': {'amount': -1, 'unit': 'day'},
                           'max': {'amount': 1, 'unit': 'day'}}]}
    spec.update(extra)
    return spec


class Running:
    """A mockup server on a free port, with the given store configuration."""

    def __init__(self, mimic_store=None, d_items=None, demo_pin=None):
        self.httpd = server.serve('127.0.0.1', 0, mimic_store, d_items, demo_pin)
        self.url = f'http://127.0.0.1:{self.httpd.server_port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        with urlopen(self.url + path, timeout=30) as response:
            return response.status, json.load(response)

    def post(self, path, body):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = Request(self.url + path, data=data, headers={'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=60) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)


class StoreQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        cls.demo = msi.stream_build(FIXTURE, 'mimic-iv-demo-2.2', tmp / 'demo')
        cls.credentialed = msi.stream_build(FIXTURE, 'mimic-iv-3.1', tmp / 'credentialed')
        with es.open_store(cls.demo) as store:
            cls.patient_ids = {pid for pid, in store.query('SELECT patient_id FROM patients')}
            cls.times = {t for row in store.query('SELECT start_lo, start_hi FROM events') for t in row}
        cls.pin = (len(cls.patient_ids), store_query.subjects_digest(cls.patient_ids))
        cls.served = Running(cls.demo.parent, FIXTURE / 'd_items.csv', cls.pin)
        cls.refused = Running(cls.credentialed)
        cls.bare = Running()

    @classmethod
    def tearDownClass(cls):
        for s in (cls.served, cls.refused, cls.bare):
            s.close()
        cls.tmp.cleanup()

    # ------------------------------------------------------------ rule 2: only the open MIMIC-IV demo

    def test_a_credentialed_snapshot_is_refused_before_it_is_opened(self):
        manifest = json.loads((self.credentialed / 'manifest.json').read_text())
        self.assertEqual(manifest['dataset_id'], 'mimic-iv-3.1:source_item_class')
        with mock.patch.object(es, 'open_store', side_effect=AssertionError('opened')) as opened:
            service = store_query.StoreService(self.credentialed, pin=self.pin)
            status = service.status()
        opened.assert_not_called()
        self.assertEqual((status['available'], status['error']), (False, 'SNAPSHOT_NOT_OPEN_MIMIC_DEMO'))
        self.assertNotIn('dataset_id', status)

    def test_a_refused_snapshot_answers_nothing_over_http(self):
        status, body = self.refused.get('/api/store/status')
        self.assertEqual((status, body['available'], body['error']), (200, False, 'SNAPSHOT_NOT_OPEN_MIMIC_DEMO'))
        self.assertEqual(self.refused.post('/api/store/match', query()), (503, {'error': 'SNAPSHOT_NOT_OPEN_MIMIC_DEMO'}))
        with self.assertRaises(HTTPError) as caught:
            self.refused.get('/api/store/catalogue')
        self.assertEqual(caught.exception.code, 503)

    def test_only_the_demo_dataset_ids_pass(self):
        self.assertEqual(store_query.check_open_dataset({'dataset_id': 'mimic-iv-demo-2.2:source_item_class'}),
                         'mimic-iv-demo-2.2:source_item_class')
        for other in ('mimic-iv-3.1:source_item_class', 'mimic-iv-demo-2.2', 'mimic-iv-demo-2.2:source_item_class ',
                      'synthetic-mimic-store-2.2:source_item_class', None, 7):
            with self.assertRaises(store_query.Unavailable):
                store_query.check_open_dataset({'dataset_id': other})
        with self.assertRaises(store_query.Unavailable):
            store_query.check_open_dataset([])

    def test_no_store_configured_says_how_to_build_one(self):
        status, body = self.bare.get('/api/store/status')
        self.assertEqual((status, body['configured'], body['available'], body['error']),
                         (200, False, False, 'STORE_NOT_CONFIGURED'))
        self.assertTrue(any('mimic_store_ingest' in line for line in body['how_to_build']))
        self.assertEqual(self.bare.post('/api/store/match', query()), (503, {'error': 'STORE_NOT_CONFIGURED'}))

    def test_a_missing_path_is_reported_not_raised(self):
        status = store_query.StoreService(Path(self.tmp.name) / 'nowhere').status()
        self.assertEqual((status['available'], status['error']), (False, 'SNAPSHOT_NOT_FOUND'))

    def test_the_label_decides_nothing_the_content_decides(self):
        # A relabelled snapshot: the credentialed fixture with its manifest's dataset id edited to the demo's. The
        # dataset id is outside the snapshot identity, so the manifest and file checks still pass.
        relabelled = Path(self.tmp.name) / 'relabelled' / self.credentialed.name
        shutil.copytree(self.credentialed, relabelled)
        manifest = json.loads((relabelled / 'manifest.json').read_text())
        manifest['dataset_id'] = 'mimic-iv-demo-2.2:source_item_class'
        (relabelled / 'manifest.json').write_text(json.dumps(manifest))
        es.open_store(relabelled).close()
        for path in (relabelled, self.demo):  # relabelled, and built under the demo's id: neither has its patients
            status = store_query.StoreService(path).status()
            self.assertEqual((status['available'], status['error']), (False, 'SNAPSHOT_CONTENT_NOT_OPEN_MIMIC_DEMO'))

    def test_a_hundred_patients_that_are_not_the_demos_are_refused(self):
        ingest = {'profile': 'execution-store-ingest-2.0', 'dataset_id': 'mimic-iv-demo-2.2:source_item_class',
                  'closure': {'version': 'test', 'ancestors': {'x': []}},
                  'patients': [{'patient_id': str(10_000_000 + k), 'clock_id': f'patient_{k}', 'events': [],
                                'coverage': []} for k in range(store_query.DEMO_PATIENTS)]}
        snapshot = es.build(ingest, Path(self.tmp.name) / 'hundred')
        status = store_query.StoreService(snapshot).status()
        self.assertEqual((status['available'], status['error']), (False, 'SNAPSHOT_CONTENT_NOT_OPEN_MIMIC_DEMO'))
        with es.open_store(snapshot) as store:  # it passes the label and the count, so the digest decides
            self.assertEqual(store.manifest['tables']['patients']['rows'], store_query.DEMO_PATIENTS)

    def test_the_real_demo_store_passes(self):
        candidates = [os.environ.get('PTM_MIMIC_STORE'), ROOT / 'verification/execution-store-mimic-run/store',
                      '/tmp/ptm-store-mockup/mimic/store']
        path = next((Path(p) for p in candidates if p and Path(p).exists()), None)
        if path is None:
            self.skipTest('no MIMIC-IV demo store built locally')
        service = store_query.StoreService(path)
        status = service.status()
        self.assertTrue(status['available'], status)
        self.assertEqual(status['patients'], store_query.DEMO_PATIENTS)
        self.assertGreater(len(service.catalogue()['entries']), 100)

    def test_a_failed_open_leaves_nothing_open(self):
        closed = []
        real_close = es.Store.close

        def spy(store):
            closed.append(store)
            return real_close(store)
        for failure in (store_query.Unavailable('SNAPSHOT_CONTENT_NOT_OPEN_MIMIC_DEMO'), RuntimeError('duckdb failed')):
            closed.clear()
            service = store_query.StoreService(self.demo, pin=self.pin)
            with mock.patch.object(es.Store, 'close', spy), \
                    mock.patch.object(store_query.StoreService, '_load', side_effect=failure):
                status = service.status()
            self.assertFalse(status['available'])
            self.assertIsNone(service.store)
            self.assertEqual(len(closed), 1, 'the store opened by the failed attempt was not closed')
            with self.assertRaises(store_query.Unavailable):
                service.match(query())
        # The second dataset check, on the opened store's manifest, failing.
        service = store_query.StoreService(self.demo, pin=self.pin)
        with mock.patch.object(store_query, 'check_open_dataset', side_effect=[
                'mimic-iv-demo-2.2:source_item_class', store_query.Unavailable('SNAPSHOT_NOT_OPEN_MIMIC_DEMO')]):
            self.assertEqual(service.status()['error'], 'SNAPSHOT_NOT_OPEN_MIMIC_DEMO')
        self.assertIsNone(service.store)

    def test_missing_duckdb_is_named(self):
        service = store_query.StoreService(self.demo, pin=self.pin)
        with mock.patch.object(es, 'open_store', side_effect=es.StoreError('DUCKDB_UNAVAILABLE', 'pip install')):
            self.assertEqual(service.status()['error'], 'STORE_SUPPORT_NOT_INSTALLED')

    # ------------------------------------------------------------ rule 1: aggregate counts only

    def walk(self, value, keys, scalars):
        if isinstance(value, dict):
            for k, v in value.items():
                keys.add(k)
                self.walk(v, keys, scalars)
        elif isinstance(value, list):
            for v in value:
                self.walk(v, keys, scalars)
        else:
            scalars.append(value)

    def test_the_answer_is_counts_only(self):
        status, body = self.served.post('/api/store/match', query())
        self.assertEqual(status, 200, body)
        keys, scalars = set(), []
        self.walk(body, keys, scalars)
        allowed = (set(store_query.SCHEMA) | set(store_query.STATUSES) | {'placed', 'not_placed', 'id', 'relaxed',
                   'beside_unresolved', 'step', 'match_seconds', 'pruning_count_seconds', 'total_seconds',
                   'exact', 'possible', 'ADMISSION_EXCLUDED_RECORDS', 'NO_SOURCE_COVERAGE', 'NO_SOURCE_RECORDS_CONCEPT',
                   'SEARCH_BUDGET_EXHAUSTED', 'EXCLUDED_RECORDS_MAY_MAKE_EXACT', 'EXACT_SEARCH_STOPPED',
                   'CHEAPER_RELAXATION_SEARCH_STOPPED'})
        self.assertLessEqual(keys, allowed)
        for forbidden in ('results', 'patient_id', 'binding', 'window', 'start', 'end', 'value', 'events'):
            self.assertNotIn(forbidden, keys)
        for v in scalars:
            self.assertNotIn(str(v), self.patient_ids)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                self.assertLess(abs(v), 10 ** 6, 'a time or a value, not a count')
                self.assertNotIn(v, self.times)
        self.assertEqual(body['patients'], len(self.patient_ids))
        self.assertEqual(sum(body['counts'].values()), len(self.patient_ids))

    def test_the_counts_are_the_stores_own(self):
        service = store_query.StoreService(self.demo, pin=self.pin)
        service.status()
        compiled, conditions, windows, _ = service.compile(query())
        with es.open_store(self.demo) as store:
            direct = es.match(store, compiled, windows, conditions=conditions)
        _, body = self.served.post('/api/store/match', query())
        self.assertEqual({k: v for k, v in body['counts'].items() if v}, direct['counts'])
        self.assertEqual(body['candidates'], direct['candidates'])
        self.assertEqual(conditions['steps']['A'], {'unit': 'per min', 'ge': '74'})
        self.assertEqual(conditions['relaxations'][0]['condition'], {'unit': 'per min', 'ge': '70'})

    def test_an_answer_that_is_not_aggregate_is_never_sent(self):
        real = store_query.aggregate

        def leaky(out, *args):
            payload = real(out, *args)
            payload['results'] = out['results']
            return payload
        with mock.patch.object(store_query, 'aggregate', leaky):
            status, body = self.served.post('/api/store/match', query())
        self.assertEqual((status, body), (500, {'error': 'AGGREGATE_RULE_VIOLATION'}))

    def test_the_check_refuses_ids_and_unexpected_fields(self):
        _, good = self.served.post('/api/store/match', query())
        pid = sorted(self.patient_ids)[0]
        bad = [
            {**good, 'extra': 1},
            {**good, 'counts': {**good['counts'], 'exact': '3'}},
            {**good, 'counts': {**good['counts'], 'exact': -1}},
            {**good, 'not_comparable_reasons': {pid: 1}},
            {**good, 'notes': {'patient ' + pid: 1}},
            {**good, 'relaxations': [{'id': pid, 'relaxed': 1, 'beside_unresolved': 0}]},
            {**good, 'plan': [{'step': 'A', 'patients': 1, 'patient_id': pid}]},
            {**good, 'profile': pid},
            {**good, 'timing': {**good['timing'], 'match_seconds': 10 ** 12}},
        ]
        for payload in bad:
            with self.assertRaises(store_query.AggregateRuleViolation):
                store_query.check_aggregate(payload, self.patient_ids)
        self.assertEqual(store_query.check_aggregate(good, self.patient_ids), good)

    # ------------------------------------------------------------ the query builder's contract

    def test_thresholds_must_be_strings(self):
        spec = query()
        spec['steps'][0]['condition']['lower']['value'] = 74
        self.assertEqual(self.served.post('/api/store/match', spec), (400, {'error': 'THRESHOLD_MUST_BE_A_STRING'}))
        spec['steps'][0]['condition']['lower']['value'] = '7.4e1'
        self.assertEqual(self.served.post('/api/store/match', spec), (400, {'error': 'THRESHOLD_NOT_A_PLAIN_DECIMAL'}))

    def test_a_threshold_is_compared_exactly(self):
        # 74 is recorded; 73.9999999999999999999999 is not a float-rounded 74.
        near = query()
        near['steps'][0]['condition']['lower'] = {'op': 'gt', 'value': '73.9999999999999999999999'}
        near['steps'][0].pop('widen_value')
        exact = query()
        exact['steps'][0]['condition']['lower'] = {'op': 'ge', 'value': '74'}
        exact['steps'][0].pop('widen_value')
        _, a = self.served.post('/api/store/match', near)
        _, b = self.served.post('/api/store/match', exact)
        self.assertEqual(a['counts'], b['counts'])
        beyond = query()
        beyond['steps'][0]['condition']['lower'] = {'op': 'gt', 'value': '74.0000000000000000000001'}
        beyond['steps'][0].pop('widen_value')
        self.assertEqual(self.served.post('/api/store/match', beyond)[0], 200)

    def test_units_come_from_the_store(self):
        spec = query()
        spec['steps'][0]['condition']['unit'] = 'bpm'
        spec['steps'][0].pop('widen_value')
        self.assertEqual(self.served.post('/api/store/match', spec), (400, {'error': 'UNIT_NOT_RECORDED_FOR_THIS_ITEM'}))

    def test_a_value_relaxation_must_widen(self):
        spec = query()
        spec['steps'][0]['widen_value']['condition']['lower']['value'] = '80'
        self.assertEqual(self.served.post('/api/store/match', spec), (400, {'error': 'VALUE_RELAXATION_MUST_WIDEN'}))

    def test_during_needs_an_interval_step(self):
        spec = query(relations=[{'kind': 'during', 'from': 'A', 'to': 'B', 'grace': {'amount': 0, 'unit': 'hour'}}])
        self.assertEqual(self.served.post('/api/store/match', spec), (400, {'error': 'DURING_NEEDS_AN_INTERVAL_STEP'}))
        spec = query(relations=[{'kind': 'during', 'from': 'B', 'to': 'A', 'grace': {'amount': 2, 'unit': 'hour'},
                                 'extend_grace': {'grace': {'amount': 6, 'unit': 'hour'}, 'cost': '0.25'}}])
        status, body = self.served.post('/api/store/match', spec)
        self.assertEqual(status, 200, body)

    def test_concept_widening_goes_to_the_items_grouping(self):
        spec = query()
        spec['steps'][1] = {'id': 'B', 'concept': MEDICATION, 'occurrence': 'first',
                            'widen_concept': {'concept': G + 'Medications', 'cost': '0.5'}}
        status, body = self.served.post('/api/store/match', spec)
        self.assertEqual(status, 200, body)
        self.assertIn('concept:B', [r['id'] for r in body['relaxations']])
        spec['steps'][1]['widen_concept']['concept'] = G + 'Fluids%2FIntake'
        self.assertEqual(self.served.post('/api/store/match', spec),
                         (400, {'error': 'WIDEN_CONCEPT_MUST_BE_THE_ITEMS_GROUPING'}))

    def test_unknown_fields_and_concepts_are_refused(self):
        self.assertEqual(self.served.post('/api/store/match', {**query(), 'patients': []}),
                         (400, {'error': 'REQUEST_FIELDS'}))
        spec = query()
        spec['steps'][1]['concept'] = 'https://w3id.org/sulo/Process'
        self.assertEqual(self.served.post('/api/store/match', spec), (400, {'error': 'UNKNOWN_CONCEPT'}))
        self.assertEqual(self.served.post('/api/store/match', b'{nope'), (400, {'error': 'MALFORMED_REQUEST'}))

    def test_malformed_requests_get_a_named_400(self):
        def step(**change):
            spec = query()
            spec['steps'][0] = {**spec['steps'][0], **change}
            return spec
        rate = {'unit': 'per min', 'lower': {'op': 'ge', 'value': '70'}}
        cases = {
            'concept as a list': (step(concept=[]), 'CONCEPT_MUST_BE_A_STRING'),
            'concept as an object': (step(concept={}), 'CONCEPT_MUST_BE_A_STRING'),
            'id as a list': (step(id=['A']), 'STEP_ID'),
            'unit as a list': (step(condition={**rate, 'unit': ['per min']}), 'UNIT_NOT_RECORDED_FOR_THIS_ITEM'),
            'op as a list': (step(condition={**rate, 'lower': {'op': ['ge'], 'value': '74'}}), 'CONDITION_BOUND'),
            'relation from as a list': (query(relations=[{'kind': 'after', 'from': ['A'], 'to': 'B'}]),
                                        'RELATION_STEPS'),
            'relation kind as a list': (query(relations=[{'kind': ['after'], 'from': 'A', 'to': 'B'}]),
                                        'RELATION_KIND'),
            'widening concept as a list': (step(widen_concept={'concept': [G + 'Synthetic%20Vitals'], 'cost': '0.5'}),
                                           'WIDEN_CONCEPT_FIELDS'),
            'an enormous cost': (step(widen_value={'condition': rate, 'cost': '0.' + '0' * 100_000}), 'COST'),
            'an enormous gap': (query(relations=[{'kind': 'after', 'from': 'A', 'to': 'B',
                                                  'max': {'amount': '1' * 5000, 'unit': 'day'}}]), 'DURATION_AMOUNT'),
            'window as a list': (query(window=['first_stay']), 'UNKNOWN_WINDOW'),
            'steps as an object': (query(steps={}), 'STEPS_MUST_BE_1_TO_6'),
            'a step as a string': (query(steps=['A']), 'STEP_FIELDS'),
            'a body that is a list': ([], 'REQUEST_FIELDS'),
            'deeply nested': (b'[' * 100_000 + b']' * 100_000, 'MALFORMED_REQUEST'),
        }
        for name, (body, code) in cases.items():
            with self.subTest(name):
                self.assertEqual(self.served.post('/api/store/match', body), (400, {'error': code}))
        # An internal failure is named, with nothing from the failure in the answer.
        with mock.patch.object(es, 'match', side_effect=RuntimeError('internal detail')):
            self.assertEqual(self.served.post('/api/store/match', query()), (500, {'error': 'INTERNAL_ERROR'}))

    def test_counts_are_bounded_by_the_patients(self):
        _, good = self.served.post('/api/store/match', query())
        n = len(self.patient_ids)
        for bad in ({**good, 'candidates': n + 1}, {**good, 'counts': {**good['counts'], 'none': n + 1}},
                    {**good, 'plan': [{'step': 'A', 'patients': n + 1}]}, {**good, 'pruned': '1'}):
            with self.assertRaises(store_query.AggregateRuleViolation):
                store_query.check_aggregate(bad, self.patient_ids)
        self.assertIsNone(store_query.check_aggregate({**good, 'pruned': None}, self.patient_ids)['pruned'])

    def test_pruned_is_unavailable_when_pruning_is_off(self):
        with mock.patch.object(es, '_PRUNE', False):
            status, body = self.served.post('/api/store/match', query())
        self.assertEqual((status, body['pruned']), (200, None))

    def test_status_and_catalogue_are_checked_too(self):
        _, status = self.served.get('/api/store/status')
        _, catalogue = self.served.get('/api/store/catalogue')
        pid = sorted(self.patient_ids)[0]
        self.assertEqual(store_query.check_status(status, self.patient_ids), status)
        self.assertEqual(store_query.check_catalogue(catalogue, self.patient_ids), catalogue)
        entry = catalogue['entries'][0]
        bad_status = [{**status, 'extra': pid}, {**status, 'labels': {**status['labels'], 'who': pid}},
                      {**status, 'windows': {**status['windows'], 'first_stay': len(self.patient_ids) + 1}},
                      {**status, 'snapshot_id': pid}]
        bad_catalogue = [{**catalogue, 'entries': [{**entry, 'label': pid}]},
                         {**catalogue, 'entries': [{**entry, 'patients': len(self.patient_ids) + 1}]},
                         {**catalogue, 'entries': [{**entry, 'patient_id': pid}]}]
        for bad in bad_status:
            with self.assertRaises(store_query.AggregateRuleViolation):
                store_query.check_status(bad, self.patient_ids)
        for bad in bad_catalogue:
            with self.assertRaises(store_query.AggregateRuleViolation):
                store_query.check_catalogue(bad, self.patient_ids)
        with mock.patch.object(store_query, 'check_catalogue', side_effect=store_query.AggregateRuleViolation('x')):
            with self.assertRaises(HTTPError) as caught:
                self.served.get('/api/store/catalogue')
        self.assertEqual((caught.exception.code, json.load(caught.exception)),
                         (500, {'error': 'AGGREGATE_RULE_VIOLATION'}))

    def test_catalogue_lists_item_classes_with_their_units_and_dictionary_labels(self):
        status, body = self.served.get('/api/store/catalogue')
        self.assertEqual(status, 200)
        entries = {e['concept']: e for e in body['entries']}
        self.assertEqual(entries[RATE]['label'], 'Synthetic rate A')
        self.assertEqual([u['unit'] for u in entries[RATE]['units']], ['per min'])
        self.assertTrue(entries[FLUID]['interval'])
        self.assertFalse(entries[RATE]['interval'])
        self.assertEqual(entries[G + 'Medications']['kind'], 'grouping')
        self.assertFalse(any(k.startswith('https://w3id.org/') for k in entries))
        text = json.dumps(body)
        for pid in self.patient_ids:
            self.assertNotIn(f'"{pid}"', text)

    def test_windows_are_chosen_by_rule(self):
        for window in store_query.WINDOWS:
            status, body = self.served.post('/api/store/match', query(window=window, scope='within'))
            self.assertEqual(status, 200, body)
            self.assertEqual(body['windows']['placed'] + body['windows']['not_placed'], len(self.patient_ids))
        self.assertEqual(self.served.post('/api/store/match', query(window='index_date')),
                         (400, {'error': 'UNKNOWN_WINDOW'}))


if __name__ == '__main__':
    unittest.main()
