"""The trajectory page's store endpoints (D37): the synthetic cohort in a store, answered per patient.

The server builds the generated cohort (`demos/mockups/data/`, from `synthetic/generate.py`) into a store at startup.
These tests check that the endpoint answers with the store's own results (`store_conditions_gene.match` since D38, called
directly on the same request, on a store built the same way), that times come back in minutes from birth exactly, that
every refusal is named, that the store is served only for the synthetic cohort the page downloads, and that its build
lives outside the repository and is removed when the server closes. The MIMIC endpoints' tests are
`test_store_query.py`, unchanged. Run after `synthetic/generate.py`, with DuckDB installed.
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
import store_parity  # noqa: E402
import store_trajectory  # noqa: E402
from patterns import execution_store as es  # noqa: E402
from patterns import store_conditions_gene as gc  # noqa: E402

COHORT = store_trajectory.COHORT
DAY = 1440  # minutes
TEN_YEARS = 3653 * DAY


def requests(pids):
    """Requests in the translator's form (`storeRequest`): a plain query with a relaxation, a value condition with its
    relaxation, an Nth occurrence, a count, and a rise with a smaller factor; minutes from birth throughout."""
    windows = {pid: {'start': 0, 'end': TEN_YEARS} for pid in pids}
    windows[pids[-1]] = None  # a patient without an index period
    q = lambda steps, relations=(), relaxations=(), scope='any': {
        'profile': 'trajectory-graph-1.0', 'scope': scope, 'steps': list(steps), 'relations': list(relations),
        'relaxations': list(relaxations), 'budget': '1', 'search_budget': 40000}
    after = {'id': 'edge:0', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 365 * DAY}
    return {
        'plain': {'query': q([{'id': 'A', 'concept': 'febrile_seizure', 'occurrence': 'first'},
                              {'id': 'B', 'concept': 'sodium_channel_blocker', 'occurrence': 'first'}], [after],
                             [{'id': 'node:A', 'kind': 'widen_concept', 'cost': '0.5', 'step': 'A', 'concept': 'seizure'},
                              {'id': 'edge:0', 'kind': 'extend_max', 'cost': '0.25', 'relation': 'edge:0', 'max': 548 * DAY}]),
                  'conditions': None, 'windows': windows},
        'value': {'query': q([{'id': 'A', 'concept': 'weight', 'occurrence': 'any'}], scope='within'),
                  'conditions': {'profile': 'execution-store-conditions-1.1', 'steps': {'A': {'unit': 'kg', 'le': '9'}},
                                 'relaxations': [{'id': 'node:A', 'kind': 'widen_value', 'step': 'A', 'cost': '0.5',
                                                  'condition': {'unit': 'kg', 'le': '9.9'}}]},
                  'windows': windows},
        'nth': {'query': q([{'id': 'A', 'concept': 'seizure', 'occurrence': 'any'},
                            {'id': 'B', 'concept': 'sodium_channel_blocker', 'occurrence': 'first'}], [after]),
                'conditions': {'profile': 'execution-store-conditions-1.1', 'steps': {'A': {'occurrence': {'nth': 2}}}},
                'windows': windows},
        'count': {'query': q([{'id': 'A', 'concept': 'febrile_seizure', 'occurrence': 'any'}], scope='within'),
                  'conditions': {'profile': 'execution-store-conditions-1.1',
                                 'steps': {'A': {'count': {'at_least': 2, 'within': {'span': 183 * DAY}}}},
                                 'relaxations': [{'id': 'count:A', 'kind': 'widen_count', 'step': 'A', 'cost': '0.5', 'at_least': 1}]},
                  'windows': windows},
        'rise': {'query': q([{'id': 'A', 'concept': 'sodium_channel_blocker', 'occurrence': 'first'},
                             {'id': 'B', 'concept': 'seizure_frequency', 'occurrence': 'any'}],
                            [{'id': 'edge:0', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 60 * DAY}]),
                 'conditions': {'profile': 'execution-store-conditions-1.2',
                                'steps': {'B': {'relative': {'kind': 'rise', 'factor': '2', 'unit': '/month', 'floor': '1',
                                                             'baseline': {'previous': 2, 'within': 75 * DAY}}}},
                                'relaxations': [{'id': 'node:B', 'kind': 'widen_factor', 'step': 'B', 'cost': '0.5', 'factor': '1.5'}]},
                 'windows': windows},
        # D38: a gene step (the tree template's shape: the first seizure, then the SCN1A result within two years).
        'gene': {'query': q([{'id': 'A', 'concept': 'seizure', 'occurrence': 'first'}, {'id': 'B', 'concept': 'gene:SCN1A',
                                                                                        'occurrence': 'any'}],
                            [{'id': 'edge:0', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 0, 'max': 731 * DAY}]),
                 'conditions': {'profile': 'execution-store-conditions-gene-1.0',
                                'steps': {'B': {'gene': {'report': 'first_not_negative', 'timed_by': 'recorded_at'}}}},
                 'windows': windows},
    }


class Running:
    def __init__(self, **options):
        self.httpd = server.serve('127.0.0.1', 0, **options)
        self.url = f'http://127.0.0.1:{self.httpd.server_port}'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def get(self, path):
        with urlopen(self.url + path, timeout=60) as response:
            return response.status, json.load(response)

    def post(self, body, content_type='application/json'):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = Request(self.url + '/api/trajectory-store/match', data=data, headers={'Content-Type': content_type})
        try:
            with urlopen(request, timeout=120) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)


@unittest.skipUnless(COHORT.is_file(), 'run demos/mockups/synthetic/generate.py first')
class TrajectoryStoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.served = Running()
        cls.cohort = json.loads(COHORT.read_text())
        cls.pids = sorted(p['patient_id'] for p in cls.cohort['patients'])
        cls.births = store_parity.birth_micros(cls.cohort)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.snapshot = store_parity.build(cls.cohort, Path(cls.tmp.name) / 'store')

    @classmethod
    def tearDownClass(cls):
        cls.served.close()
        cls.tmp.cleanup()

    def direct(self, spec):
        """The store's own answer: `store_conditions_gene.match` (D38) on a store built the same way."""
        windows = {pid: None if w is None else {'start': self.births[pid] + w['start'] * store_parity.MINUTE,
                                                'end': self.births[pid] + w['end'] * store_parity.MINUTE}
                   for pid, w in spec['windows'].items()}
        with es.open_store(self.snapshot) as store:
            return gc.match(store, store_parity.to_micros(spec['query']), windows,
                            conditions=store_parity.conditions_micros(spec['conditions']))

    def test_status_names_the_synthetic_snapshot(self):
        status, body = self.served.get('/api/trajectory-store/status')
        self.assertEqual(status, 200)
        self.assertTrue(body['available'])
        self.assertEqual(body['dataset_id'], 'synthetic-paediatric-rare-v0')
        self.assertEqual(body['patients'], len(self.pids))
        self.assertRegex(body['snapshot_id'], r'^[0-9a-f]{64}$')
        self.assertEqual(body['store_profile'], 'execution-store-3.1')
        self.assertIn('execution-store-conditions-1.2', body['conditions_profiles'])
        self.assertIn('execution-store-conditions-gene-1.0', body['conditions_profiles'])
        with es.open_store(self.snapshot) as store:  # the converter is deterministic: the same snapshot
            self.assertEqual(body['snapshot_id'], store.snapshot_id)

    def test_the_endpoint_answers_with_the_stores_own_results(self):
        for name, spec in requests(self.pids).items():
            with self.subTest(name):
                status, body = self.served.post(spec)
                self.assertEqual(status, 200, body)
                direct = self.direct(spec)
                self.assertEqual(body['counts'], direct['counts'])
                self.assertEqual(body['context_id'], direct['context_id'])
                self.assertEqual(body['conditions_profile'], (spec['conditions'] or {}).get('profile'))
                self.assertEqual(len(body['results']), len(direct['results']))
                for mine, theirs in zip(body['results'], direct['results']):
                    for key in set(mine) | set(theirs):
                        if key in ('binding', 'witness', 'relaxed_match'):
                            continue
                        self.assertEqual(mine.get(key), theirs.get(key), f"{theirs['patient_id']} {key}")
                    for got, want in ((mine.get('binding'), theirs.get('binding')),
                                      ((mine.get('relaxed_match') or {}).get('binding'), (theirs.get('relaxed_match') or {}).get('binding'))):
                        self.assertEqual(got is None, want is None)
                        if want:
                            self.assertEqual({s: d['id'] for s, d in got.items()}, want)
                    birth = self.births[theirs['patient_id']]
                    for step, t in (theirs.get('witness') or {}).items():  # minutes from birth, exactly
                        self.assertEqual(birth + round(mine['witness'][step] * store_parity.MINUTE), t)

    def test_every_status_and_reason_reaches_the_page(self):
        seen, reasons = set(), set()
        for spec in requests(self.pids).values():
            _, body = self.served.post(spec)
            seen |= {r['status'] for r in body['results']}
            reasons |= {r.get('reason') for r in body['results']}
        self.assertLessEqual({'possible', 'relaxed', 'none', 'not_comparable', 'unresolved', 'excluded'}, seen)
        self.assertLessEqual({'NO_SOURCE_COVERAGE', 'ADMISSION_EXCLUDED_RECORDS', 'NO_INDEX_WINDOW', 'FILTERED'}, reasons)

    def test_bound_events_are_described_from_the_stores_rows(self):
        _, body = self.served.post(requests(self.pids)['value'])
        with es.open_store(self.snapshot) as store:
            rows = {(pid, eid): (kind, slo, unit, value) for pid, eid, kind, slo, unit, value in store.query(
                '''SELECT p.patient_id, e.event_id, e.kind, e.start_lo, s.unit, s.value_lexical FROM events e
                   JOIN patients p USING (patient_idx) LEFT JOIN series s USING (patient_idx, event_id)''')}
        described = 0
        for r in body['results']:
            for d in (r.get('binding') or {}).values():
                kind, slo, unit, value = rows[r['patient_id'], d['id']]
                self.assertEqual((d['kind'], d['unit'], d['value']), (kind, unit, value))
                self.assertEqual(self.births[r['patient_id']] + d['start'][0] * store_parity.MINUTE, slo)
                self.assertEqual(d['record_id'], d['id'].split('/')[1].split('#')[0])
                described += 1
        self.assertGreater(described, 0)
        # A derived step's own point (an Nth occurrence) is no record, and says so.
        _, body = self.served.post(requests(self.pids)['nth'])
        points = [d for r in body['results'] for d in (r.get('binding') or {}).values() if d.get('derived')]
        self.assertTrue(points)
        self.assertTrue(all(d['id'].startswith('derived-step:') for d in points))
        # D38: a gene step's point names the genetic result it comes from and the time of its first report not negative.
        _, body = self.served.post(requests(self.pids)['gene'])
        genes = [(r['patient_id'], r['witness']['B'], d) for r in body['results'] if 'witness' in r
                 for s, d in (r.get('binding') or {}).items() if s == 'B']
        self.assertTrue(genes)
        for pid, at, d in genes:
            self.assertTrue(d['derived'])
            self.assertEqual(d['gene_report']['kind'], 'genetic_result')
            self.assertEqual(d['id'], f"derived-step:B/own@{d['gene_report']['id']}")
            self.assertEqual(d['gene_report']['finding'], at)  # a gene point is exact: its witness is its time

    def test_refusals_are_named(self):
        good = requests(self.pids)['plain']
        window = good['windows'][self.pids[0]]
        cases = {
            'not an object': ([1], 'REQUEST_FIELDS'),
            'an extra field': ({**good, 'detail': 'full'}, 'REQUEST_FIELDS'),
            'a gap as a string': ({**good, 'query': {**good['query'], 'relations': [{**good['query']['relations'][0], 'max': '525600'}]}},
                                  'TIME_MUST_BE_WHOLE_MINUTES'),
            'a gap as a float': ({**good, 'query': {**good['query'], 'relations': [{**good['query']['relations'][0], 'max': 1.5}]}},
                                 'TIME_MUST_BE_WHOLE_MINUTES'),
            'a gap beyond any record': ({**good, 'query': {**good['query'], 'relations': [{**good['query']['relations'][0], 'max': 10 ** 12}]}},
                                        'TIME_OUT_OF_RANGE'),
            'a boolean window end': ({**good, 'windows': {self.pids[0]: {'start': 0, 'end': True}}}, 'TIME_MUST_BE_WHOLE_MINUTES'),
            'an unknown patient': ({**good, 'windows': {'X999': window}}, 'UNKNOWN_WINDOW_PATIENT'),
            'a window with extra keys': ({**good, 'windows': {self.pids[0]: {**window, 'note': 'x'}}}, 'WINDOW_MUST_BE_START_AND_END'),
            'the engine refuses the profile': ({**good, 'query': {**good['query'], 'profile': 'other'}}, 'UNSUPPORTED_PROFILE'),
            'the store refuses a factor of 1': (self.with_factor('1'), 'INVALID_FACTOR'),
            'the store refuses an unknown conditions profile': ({**good, 'conditions': {'profile': 'x', 'steps': {}}}, 'INVALID_CONDITIONS'),
            'a count span as a string': ({**requests(self.pids)['count'], 'conditions': {'profile': 'execution-store-conditions-1.1',
                                          'steps': {'A': {'count': {'at_least': 2, 'within': {'span': '1'}}}}}}, 'TIME_MUST_BE_WHOLE_MINUTES'),
            'a count that is not an object': ({**requests(self.pids)['count'], 'conditions': {'profile': 'execution-store-conditions-1.1',
                                               'steps': {'A': {'count': [2]}}}}, 'COUNT_MUST_BE_AN_OBJECT'),
            'a count span beyond any record': ({**requests(self.pids)['count'], 'conditions': {'profile': 'execution-store-conditions-1.1',
                                                'steps': {'A': {'count': {'at_least': 2, 'within': {'span': -10 ** 10}}}}}}, 'TIME_OUT_OF_RANGE'),
        }
        # Who refused: the server's own check before the store, or the store (or engine) by its code.
        by_store = {'UNSUPPORTED_PROFILE', 'INVALID_FACTOR', 'INVALID_CONDITIONS'}
        for name, (body, code) in cases.items():
            with self.subTest(name):
                status, answer = self.served.post(body)
                self.assertEqual((status, answer), (400, {'error': code, 'by': 'store' if code in by_store else 'server'}))
        self.assertEqual(self.served.post(b'{not json'), (400, {'error': 'MALFORMED_REQUEST'}))
        self.assertEqual(self.served.post(b'{}', 'text/plain'), (415, {'error': 'JSON_REQUIRED'}))

    def with_factor(self, factor):
        spec = json.loads(json.dumps(requests(self.pids)['rise']))
        spec['conditions']['steps']['B']['relative']['factor'] = factor
        del spec['conditions']['relaxations']
        return spec

    def test_an_unverified_match_is_described_like_a_binding(self):
        # A match the store reports beside an order-uncertain `unresolved` (D30) gets the same description as a binding.
        _, body = self.served.post(requests(self.pids)['plain'])
        bound = next(r for r in body['results'] if r.get('binding'))
        pid, step, event = bound['patient_id'], *next(iter(bound['binding'].items()))
        store = self.served.httpd.trajectory_store
        raw = {'patient_id': pid, 'status': 'unresolved', 'reason': 'OCCURRENCE_ORDER_UNCERTAIN', 'uncertain_steps': [step],
               'unverified_match': {'certainty': 'possible', 'binding': {step: event['id']}, 'method': 'm'}, 'complete': False}
        out = store._patient(raw)
        self.assertEqual(out['unverified_match']['binding'][step], event)
        self.assertEqual(out['unverified_match']['certainty'], 'possible')
        self.assertEqual({k: v for k, v in out.items() if k != 'unverified_match'},
                         {k: v for k, v in raw.items() if k != 'unverified_match'})

    def test_the_repository_check_holds_for_other_spellings_and_other_working_trees(self):
        self.assertTrue(store_trajectory.inside_repository(ROOT / 'verification' / 'not-yet'))
        lower = Path(str(ROOT).lower())
        if lower != ROOT and lower.exists():  # a case-insensitive disk (macOS): the same directory, spelled otherwise
            self.assertTrue(store_trajectory.inside_repository(lower / 'x'))
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(store_trajectory.inside_repository(Path(tmp) / 'root'))
            (Path(tmp) / '.git').mkdir()  # another git working tree
            self.assertTrue(store_trajectory.inside_repository(Path(tmp) / 'root' / 'deeper'))

    def test_an_internal_failure_is_named_without_detail(self):
        with mock.patch.object(gc, 'match', side_effect=RuntimeError('C001 secret')):
            self.assertEqual(self.served.post(requests(self.pids)['plain']), (500, {'error': 'INTERNAL_ERROR'}))
        with mock.patch.object(gc, 'match', side_effect=ValueError('no code here')):
            self.assertEqual(self.served.post(requests(self.pids)['plain']), (500, {'error': 'INTERNAL_ERROR'}))

    def test_the_mimic_endpoints_are_unchanged_beside_it(self):
        status, body = self.served.get('/api/store/status')
        self.assertEqual(status, 200)
        self.assertFalse(body['available'])  # no MIMIC store configured here; its rules are tested in test_store_query.py

    def test_only_the_served_synthetic_cohort_is_built_and_the_build_is_removed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            relabelled = tmp / 'cohort.json'
            relabelled.write_text(json.dumps({**self.cohort, 'dataset_id': 'mimic-iv-3.1'}))
            for cohort, root, code in ((relabelled, None, 'NOT_THE_SYNTHETIC_COHORT'), (tmp / 'missing.json', None, 'NO_SYNTHETIC_COHORT'),
                                       (COHORT, ROOT / 'verification', 'STORE_ROOT_INSIDE_REPOSITORY'), (COHORT, ROOT, 'STORE_ROOT_INSIDE_REPOSITORY')):
                with self.subTest(code=code, root=root):
                    store = store_trajectory.TrajectoryStore(cohort, root).start()
                    status = store.status()
                    store.close()
                    self.assertEqual((status['available'], status['error']), (False, code))
            # A store whose patients are not the served cohort's is refused (the cohort with a patient fewer).
            fewer = tmp / 'fewer.json'
            fewer.write_text(json.dumps(self.cohort))
            store = store_trajectory.TrajectoryStore(fewer, tmp / 'root')
            real_build = store_parity.build
            with mock.patch.object(store_parity, 'build', lambda cohort, root: real_build(
                    {**cohort, 'patients': cohort['patients'][:-1]}, root)):
                status = store.start().status()
            store.close()
            self.assertEqual((status['available'], status['error']), (False, 'STORE_NOT_THE_SERVED_COHORT'))
            # Under a chosen root outside the repository: built there, removed on close.
            store = store_trajectory.TrajectoryStore(COHORT, tmp / 'root').start()
            self.assertTrue(store.status()['available'])
            self.assertEqual(len(list((tmp / 'root').iterdir())), 1)
            store.close()
            self.assertEqual(list((tmp / 'root').iterdir()), [])
            self.assertEqual(store.status()['error'], 'STORE_CLOSED')

    def test_the_server_can_leave_the_store_out(self):
        bare = Running(trajectory_store=False)
        try:
            self.assertEqual(bare.get('/api/trajectory-store/status')[1]['error'], 'STORE_NOT_CONFIGURED')
            self.assertEqual(bare.post(requests(self.pids)['plain']), (503, {'error': 'STORE_NOT_CONFIGURED'}))
        finally:
            bare.close()


if __name__ == '__main__':
    unittest.main()
