"""The hosted MIMIC-IV demo fetch (mimic_demo_fetch.py) and its dataset in the server, offline.

A loopback HTTP server stands in for PhysioNet, serving small stand-in files with test pins: never the demo itself.
The server tests replace the fetch-and-build step, so no download and no store build happen here; the store that
"arrives" is the synthetic MIMIC fixture, served with its own pin as in test_store_query.py.
"""
import hashlib
import json
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import mimic_demo_fetch as fetcher  # noqa: E402
import server  # noqa: E402
import store_query  # noqa: E402
import store_trajectory  # noqa: E402
from test_server_access import Client, wait_ready  # noqa: E402

FILES = {'icu/chartevents.csv.gz': b'chart', 'icu/d_items.csv.gz': b'items', 'icu/icustays.csv.gz': b'stays',
         'icu/inputevents.csv.gz': b'inputs'}
PINS = {path: hashlib.sha256(data).hexdigest() for path, data in FILES.items()}


class StandIn:
    """A loopback server with the four files and a SHA256SUMS.txt, whose contents a test may change."""

    def __init__(self):
        self.files = dict(FILES)
        self.sums = ''.join(f'{d} {p}\n' for p, d in PINS.items()) + 'deadbeef hosp/patients.csv.gz\n'
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = self.path.lstrip('/')
                body = outer.sums.encode() if path == 'SHA256SUMS.txt' else outer.files.get(path)
                self.send_response(200 if body is not None else 404)
                self.send_header('Content-Length', str(len(body or b'')))
                self.end_headers()
                self.wfile.write(body or b'')

            def log_message(self, *args):
                pass

        class Quick(HTTPServer):
            def server_bind(self):
                socketserver.TCPServer.server_bind(self)
        self.httpd = Quick(('127.0.0.1', 0), Handler)
        self.base = f'http://127.0.0.1:{self.httpd.server_address[1]}/'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


class FetchTests(unittest.TestCase):
    def setUp(self):
        self.standin = StandIn()
        self.tmp = tempfile.TemporaryDirectory()
        self.built = []

    def tearDown(self):
        self.standin.close()
        self.tmp.cleanup()

    def fake_build(self, icu, output):
        self.built.append(sorted(p.name for p in icu.iterdir()))
        store = Path(output) / 'store' / 'snap'
        store.mkdir(parents=True)
        (store / 'manifest.json').write_text('{}')
        return Path(output) / 'store'

    def run_fetch(self):
        return fetcher.fetch_and_build(self.tmp.name, base=self.standin.base, pins=PINS, build_store=self.fake_build,
                                       allow_http=True)

    def failure(self):
        with self.assertRaises(fetcher.FetchError) as caught:
            self.run_fetch()
        self.assertFalse((Path(self.tmp.name) / 'build').exists(), 'a failure leaves no store behind')
        self.assertEqual(self.built, [], 'nothing unchecked is built')
        return str(caught.exception)

    def test_checked_files_are_built_and_the_downloads_removed(self):
        store = self.run_fetch()
        self.assertEqual(store, Path(self.tmp.name) / 'build' / 'store')
        self.assertEqual(self.built, [['chartevents.csv.gz', 'd_items.csv.gz', 'icustays.csv.gz', 'inputevents.csv.gz']])
        self.assertEqual((Path(self.tmp.name) / 'd_items.csv.gz').read_bytes(), b'items')
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ['build', 'd_items.csv.gz'])

    def test_a_file_that_does_not_match_its_pin_is_refused(self):
        self.standin.files['icu/chartevents.csv.gz'] = b'tampered'
        self.assertEqual(self.failure(), 'CHECKSUM_MISMATCH')

    def test_published_sums_that_disagree_with_the_pins_are_refused(self):
        self.standin.sums = self.standin.sums.replace(PINS['icu/icustays.csv.gz'], '0' * 64)
        self.assertEqual(self.failure(), 'PHYSIONET_SUMS_DISAGREE')

    def test_an_unreachable_source_is_named(self):
        self.standin.close()
        self.assertEqual(self.failure(), 'PHYSIONET_UNREACHABLE')

    def test_only_https_unless_a_test_says_otherwise(self):
        with self.assertRaises(fetcher.FetchError) as caught:
            fetcher.get('http://physionet.org/files/mimic-iv-demo/2.2/SHA256SUMS.txt')
        self.assertEqual(str(caught.exception), 'NOT_HTTPS')
        self.assertTrue(fetcher.BASE.startswith('https://physionet.org/'))
        self.assertEqual(set(fetcher.PINS), set(FILES))
        pinned = json.loads((ROOT / 'data/mimic-inputevents-demo-pin.json').read_text())['files']
        self.assertEqual(fetcher.PINS['icu/inputevents.csv.gz'], pinned['inputevents']['file_sha256'])
        self.assertEqual(fetcher.PINS['icu/icustays.csv.gz'], pinned['icustays']['file_sha256'])
        self.assertEqual(fetcher.PINS['icu/d_items.csv.gz'], pinned['d_items']['file_sha256'])

    def test_a_build_failure_is_named(self):
        def failing(icu, output):
            raise fetcher.FetchError('STORE_BUILD_FAILED')
        with self.assertRaises(fetcher.FetchError):
            fetcher.fetch_and_build(self.tmp.name, base=self.standin.base, pins=PINS, build_store=failing,
                                    allow_http=True)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), [])


@unittest.skipUnless(store_trajectory.COHORT.is_file(), 'run demos/mockups/synthetic/generate.py first')
class ServedTests(unittest.TestCase):
    """The fetched dataset in the server: unavailable while building or when its source fails, never holding back
    readiness or the public dataset; served through the store service's content gate once built."""

    def registry(self, tmp):
        path = Path(tmp) / 'datasets.json'
        path.write_text(json.dumps({'datasets': [
            {'id': 'synthetic-paediatric-rare', 'kind': 'synthetic-trajectory', 'title': 'Synthetic',
             'access': 'public', 'results': 'per_patient'},
            {'id': 'mimic-iv-demo', 'kind': 'mimic-demo-aggregate', 'title': 'MIMIC-IV demo', 'access': 'approved',
             'results': 'aggregate', 'store': str(Path(tmp) / 'demo' / 'build' / 'store'),
             'fetch': 'physionet:mimic-iv-demo-2.2'}]}))
        return path

    def test_an_unreachable_source_leaves_the_dataset_unavailable_and_the_rest_working(self):
        events = []

        def unreachable(dest):
            raise fetcher.FetchError('PHYSIONET_UNREACHABLE')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(server, 'log_event',
                                                                     lambda e, **f: events.append((e, f))):
            client = Client(server.serve('127.0.0.1', 0, datasets_file=self.registry(tmp),
                                         fetch_and_build=unreachable))
            try:
                dataset = client.httpd.registry.get('mimic-iv-demo')
                dataset.start_fetch(unreachable, server.log_event, attempts=(0,))  # one attempt, now
                deadline = time.time() + 10
                while dataset.fetch_state != 'failed' and time.time() < deadline:
                    time.sleep(0.05)
                wait_ready(client)  # readiness does not wait for a fetched dataset
                self.assertEqual(client.call('/api/datasets/mimic-iv-demo/status')[:2],  # the loopback owner
                                 (503, {'error': 'PHYSIONET_UNREACHABLE'}))
                self.assertEqual(client.call('/api/store/match', body={'steps': []})[:2],
                                 (503, {'error': 'PHYSIONET_UNREACHABLE'}))
                self.assertEqual(client.call('/api/datasets/synthetic-paediatric-rare/status')[0], 200)
                listed = {d['id']: d['ready'] for d in client.call('/api/datasets')[1]['datasets']}
                self.assertEqual(listed, {'synthetic-paediatric-rare': True, 'mimic-iv-demo': False})
                self.assertIn(('mimic_demo_fetch', {'dataset': 'mimic-iv-demo', 'ok': False,
                                                    'error': 'PHYSIONET_UNREACHABLE', 'seconds': 0.0}), events)
            finally:
                client.close()

    def test_a_built_store_is_served_through_the_content_gate(self):
        from test_store_query import FIXTURE
        from patterns import execution_store as es
        from patterns import mimic_store_ingest as msi
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'demo' / 'build' / 'store'

            def arrive(dest):  # the synthetic MIMIC fixture stands in for the demo's store
                return msi.stream_build(FIXTURE, 'mimic-iv-demo-2.2', target).parent
            with es.open_store(msi.stream_build(FIXTURE, 'mimic-iv-demo-2.2', Path(tmp) / 'pin')) as store:
                ids = {pid for pid, in store.query('SELECT patient_id FROM patients')}
            pin = (len(ids), store_query.subjects_digest(ids))
            real = store_query.StoreService
            gate = mock.patch.object(server.store_query, 'StoreService',
                                     lambda path=None, d_items=None, **k: real(path, d_items, pin=pin))
            with gate:
                client = Client(server.serve('127.0.0.1', 0, datasets_file=self.registry(tmp), fetch_and_build=arrive))
            try:
                dataset = client.httpd.registry.get('mimic-iv-demo')
                deadline = time.time() + 60
                while dataset.state() != 'ready' and time.time() < deadline:
                    time.sleep(0.1)
                status, body, _ = client.call('/api/datasets/mimic-iv-demo/status')
                self.assertEqual((status, body['available'], body['patients']), (200, True, len(ids)))
            finally:
                client.close()


if __name__ == '__main__':
    unittest.main()
