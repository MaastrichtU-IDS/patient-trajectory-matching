"""The store query endpoints: aggregate counts only, and only the open MIMIC-IV demo.

The stores here are built from the synthetic MIMIC fixture (`examples/mimic-store-ingest-chartevents`), never from
real data: once under the open demo's dataset id, which the endpoints serve, and once under a credentialed dataset id,
which they must refuse. Needs `patterns/requirements-store.lock.txt` (DuckDB).
"""
import json
from pathlib import Path
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

    def __init__(self, mimic_store=None, d_items=None):
        self.httpd = server.serve('127.0.0.1', 0, mimic_store, d_items)
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
        cls.served = Running(cls.demo.parent, FIXTURE / 'd_items.csv')
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
            service = store_query.StoreService(self.credentialed)
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
        service = store_query.StoreService(self.demo)
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
