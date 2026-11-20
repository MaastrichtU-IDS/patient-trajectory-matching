"""Per-dataset access over HTTP (D50): the server with a realm (keys served on loopback by this test, generated here),
and without one. No network: the realm, its keys and its tokens are all local.

Datasets here: the synthetic cohort as the public dataset, and the same cohort registered twice more behind
`approved` and behind a role (a registry may raise a kind's floor), so every tier is exercised on real stores; plus
the synthetic MIMIC fixture as the MIMIC-IV demo kind (approved, aggregate counts, served with its own pin as in
test_store_query.py). Needs the hosting lock (PyJWT, cryptography) and the store lock (DuckDB).
"""
import io
import json
import os
from pathlib import Path
import socketserver
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))
import access  # noqa: E402
import server  # noqa: E402
import store_query  # noqa: E402
import store_trajectory  # noqa: E402
from test_access import AUDIENCE, ISSUER, Realm  # noqa: E402
from test_store_query import FIXTURE, query as mimic_query  # noqa: E402
from patterns import execution_store as es  # noqa: E402
from patterns import mimic_store_ingest as msi  # noqa: E402

ROLE = 'dataset:test:read'


def registry(mimic_store=None):
    entries = [
        {'id': 'synthetic-paediatric-rare', 'kind': 'synthetic-trajectory', 'title': 'Synthetic', 'access': 'public',
         'results': 'per_patient'},
        {'id': 'synthetic-approved', 'kind': 'synthetic-trajectory', 'title': 'Synthetic, approved users',
         'access': 'approved', 'results': 'per_patient'},
        {'id': 'synthetic-credentialed', 'kind': 'synthetic-trajectory', 'title': 'Synthetic, role holders',
         'access': {'role': ROLE}, 'results': 'per_patient'}]
    if mimic_store:
        entries.append({'id': 'mimic-iv-demo', 'kind': 'mimic-demo-aggregate', 'title': 'MIMIC-IV demo fixture',
                        'access': 'approved', 'results': 'aggregate', 'store': str(mimic_store)})
    return {'datasets': entries}


class Client:
    def __init__(self, httpd):
        self.httpd = httpd
        self.url = f'http://127.0.0.1:{httpd.server_port}'
        threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def call(self, path, token=None, body=None, headers=None):
        """(status, json body, response headers)."""
        headers = dict(headers or {})
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        data = None
        if body is not None:
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            headers.setdefault('Content-Type', 'application/json')
        try:
            with urlopen(Request(self.url + path, data=data, headers=headers), timeout=60) as response:
                return response.status, json.load(response), response.headers
        except HTTPError as error:
            return error.code, json.load(error), error.headers


class KeyServer:
    """The realm's key set on a loopback port, as Keycloak's certs endpoint would serve it in-cluster."""

    def __init__(self, realm):
        class Keys(BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps(realm.jwks()).encode()
                self.send_response(200 if self.path == '/certs' else 404)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        class Quick(HTTPServer):
            def server_bind(self):  # no reverse-DNS lookup
                socketserver.TCPServer.server_bind(self)
        self.httpd = Quick(('127.0.0.1', 0), Keys)
        self.uri = f'http://127.0.0.1:{self.httpd.server_address[1]}/certs'
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def wait_ready(client, seconds=120):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if client.call('/api/ready')[0] == 200:
            return
        time.sleep(0.2)
    raise AssertionError('not ready')


@unittest.skipUnless(store_trajectory.COHORT.is_file(), 'run demos/mockups/synthetic/generate.py first')
class RealmTests(unittest.TestCase):
    """A server with a realm: tokens decide."""

    @classmethod
    def setUpClass(cls):
        cls.env = mock.patch.dict(os.environ, {'no_proxy': '127.0.0.1,localhost', 'NO_PROXY': '127.0.0.1,localhost'})
        cls.env.start()
        cls.tmp = tempfile.TemporaryDirectory()
        tmp = Path(cls.tmp.name)
        demo = msi.stream_build(FIXTURE, 'mimic-iv-demo-2.2', tmp / 'demo')
        with es.open_store(demo) as store:
            ids = {pid for pid, in store.query('SELECT patient_id FROM patients')}
        pin = (len(ids), store_query.subjects_digest(ids))
        (tmp / 'datasets.json').write_text(json.dumps(registry(demo.parent)))
        cls.realm = Realm()
        cls.keys = KeyServer(cls.realm)
        verifier = access.OidcVerifier(ISSUER, cls.keys.uri, AUDIENCE, client_id='ptm-mockups')
        cls.log = io.StringIO()
        cls.client = Client(server.serve('127.0.0.1', 0, demo_pin=pin, datasets_file=tmp / 'datasets.json',
                                         verifier=verifier, log_format='json'))
        wait_ready(cls.client)
        cls.tokens = {
            'signed_in': cls.realm.token(realm_access={'roles': ['default-roles-ids-apps']}),
            'approved': cls.realm.token(realm_access={'roles': ['approved']}),
            'credentialed': cls.realm.token(realm_access={'roles': ['approved', ROLE]}),
        }

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.keys.close()
        cls.tmp.cleanup()
        cls.env.stop()

    def call(self, *args, **kwargs):
        return self.client.call(*args, **kwargs)

    def test_an_anonymous_caller_reaches_public_datasets_only(self):
        status, body, _ = self.call('/api/datasets')
        self.assertEqual(status, 200)
        self.assertEqual([d['id'] for d in body['datasets']], ['synthetic-paediatric-rare'])
        self.assertEqual(body['caller'], {'signed_in': False, 'approved': False, 'owner': False})
        self.assertEqual(self.call('/api/datasets/synthetic-paediatric-rare/status')[0], 200)
        self.assertEqual(self.call('/api/trajectory-store/status')[0], 200)  # the alias: the first synthetic dataset
        for path in ('/api/datasets/synthetic-approved/status', '/api/datasets/synthetic-credentialed/status',
                     '/api/datasets/mimic-iv-demo/status', '/api/datasets/mimic-iv-demo/catalogue',
                     '/api/store/status', '/api/store/catalogue'):
            status, body, headers = self.call(path)
            self.assertEqual((status, body), (401, {'error': 'LOGIN_REQUIRED'}), path)
            self.assertEqual(headers['WWW-Authenticate'], 'Bearer realm="ptm"')
        for path in ('/api/datasets/synthetic-approved/match', '/api/datasets/synthetic-credentialed/match',
                     '/api/datasets/mimic-iv-demo/match', '/api/store/match'):
            self.assertEqual(self.call(path, body=mimic_query())[:2], (401, {'error': 'LOGIN_REQUIRED'}), path)

    def test_each_tier_by_token(self):
        expected = {  # (public, approved, role, mimic demo)
            'signed_in': (200, 403, 403, 403),
            'approved': (200, 200, 403, 200),
            'credentialed': (200, 200, 200, 200),
        }
        paths = ('/api/datasets/synthetic-paediatric-rare/status', '/api/datasets/synthetic-approved/status',
                 '/api/datasets/synthetic-credentialed/status', '/api/datasets/mimic-iv-demo/status')
        for who, statuses in expected.items():
            got = tuple(self.call(p, self.tokens[who])[0] for p in paths)
            self.assertEqual(got, statuses, who)
        self.assertEqual(self.call(paths[1], self.tokens['signed_in'])[1], {'error': 'NOT_APPROVED'})
        self.assertEqual(self.call(paths[2], self.tokens['approved'])[1], {'error': 'ROLE_REQUIRED'})
        listed = {who: [d['id'] for d in self.call('/api/datasets', token)[1]['datasets']]
                  for who, token in self.tokens.items()}
        self.assertEqual(listed, {
            'signed_in': ['synthetic-paediatric-rare'],
            'approved': ['synthetic-paediatric-rare', 'synthetic-approved', 'mimic-iv-demo'],
            'credentialed': ['synthetic-paediatric-rare', 'synthetic-approved', 'synthetic-credentialed',
                             'mimic-iv-demo']})
        caller = self.call('/api/datasets', self.tokens['signed_in'])[1]['caller']
        self.assertEqual(caller, {'signed_in': True, 'approved': False, 'owner': False})  # "awaiting approval"

    def test_a_match_reaches_the_store_only_for_a_permitted_caller(self):
        # The role dataset's store sees the request only with the role: an empty body is its own named 400.
        path = '/api/datasets/synthetic-credentialed/match'
        self.assertEqual(self.call(path, self.tokens['approved'], body={})[:2], (403, {'error': 'ROLE_REQUIRED'}))
        status, body, _ = self.call(path, self.tokens['credentialed'], body={})
        self.assertEqual((status, body['by']), (400, 'server'))
        # The MIMIC demo kind answers an approved caller with aggregate counts, through both routes.
        for route in ('/api/datasets/mimic-iv-demo/match', '/api/store/match'):
            status, body, _ = self.call(route, self.tokens['approved'], body=mimic_query())
            self.assertEqual(status, 200, body)
            self.assertIn('counts', body)
            self.assertNotIn('results', body)

    def test_tampered_expired_and_misdirected_tokens_are_refused_everywhere(self):
        good = self.tokens['credentialed']
        header, payload, signature = good.split('.')
        now = int(time.time())
        bad = {
            'tampered': '.'.join([header, payload[:-4] + ('AAAA' if payload[-4:] != 'AAAA' else 'BBBB'), signature]),
            'resigned elsewhere': Realm().token(realm_access={'roles': ['approved', ROLE]}),
            'expired': self.realm.token(iat=now - 7200, exp=now - 3600),
            'wrong issuer': self.realm.token(iss='https://keycloak.example.test/realms/ids3'),
            'wrong audience': self.realm.token(aud='account'),
            'id token': self.realm.token(typ='ID'),
        }
        for name, token in bad.items():
            for path in ('/api/datasets', '/api/datasets/synthetic-paediatric-rare/status',
                         '/api/datasets/synthetic-credentialed/status', '/api/store/status'):
                status, body, headers = self.call(path, token)
                self.assertEqual((status, body), (401, {'error': 'TOKEN_INVALID'}), (name, path))
                self.assertEqual(headers['WWW-Authenticate'], 'Bearer realm="ptm", error="invalid_token"')
        status, _, _ = self.call('/api/datasets/synthetic-credentialed/status', headers={'Authorization': 'Basic eDp5'})
        self.assertEqual(status, 401)

    def test_names_routes_and_auth_config(self):
        self.assertEqual(self.call('/api/datasets/nope/status')[:2], (404, {'error': 'DATASET_NOT_FOUND'}))
        self.assertEqual(self.call('/api/datasets/synthetic-paediatric-rare/catalogue')[0], 404)
        self.assertEqual(self.call('/api/auth-config')[1],
                         {'enabled': True, 'issuer': ISSUER, 'client_id': 'ptm-mockups', 'audience': AUDIENCE})
        status, body, _ = self.call('/api/ready')
        self.assertEqual((status, body), (200, {'ready': True, 'datasets': {'ready': 4, 'pending': 0, 'failed': 0}}))

    def test_security_headers(self):
        for path in ('/api/health', '/index.html'):
            with urlopen(self.client.url + path, timeout=10) as response:
                self.assertEqual(response.headers['X-Content-Type-Options'], 'nosniff')
                self.assertEqual(response.headers['X-Frame-Options'], 'DENY')
                self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')

    def test_the_request_log_holds_no_token_body_or_address(self):
        out = io.StringIO()
        token = self.tokens['approved']
        with mock.patch.object(sys, 'stdout', out):
            self.call('/api/datasets/synthetic-approved/status', token)
            self.call('/api/datasets/synthetic-credentialed/status', token)
            self.call('/api/datasets/mimic-iv-demo/match', token, body=mimic_query())
            self.call('/api/datasets', 'x.y.z')
            deadline = time.time() + 5  # a line is written just after its response is sent
            while out.getvalue().count('\n') < 4 and time.time() < deadline:
                time.sleep(0.02)
        text = out.getvalue()
        lines = [json.loads(line) for line in text.splitlines()]
        self.assertEqual([(l['route'], l['status'], l['auth']) for l in lines], [
            ('/api/datasets/{id}/status', 200, 'bearer'), ('/api/datasets/{id}/status', 403, 'bearer'),
            ('/api/datasets/{id}/match', 200, 'bearer'), ('/api/datasets', 401, 'refused')])
        self.assertEqual(lines[0]['user'], 'f3c1e8a0-user')
        self.assertEqual(lines[1]['dataset'], 'synthetic-credentialed')
        for secret in (token, token.split('.')[2], 'Bearer', 'Authorization', '127.0.0.1', '910001', 'x.y.z'):
            self.assertNotIn(secret, text)


@unittest.skipUnless(store_trajectory.COHORT.is_file(), 'run demos/mockups/synthetic/generate.py first')
class WithoutRealmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / 'datasets.json'
        self.file.write_text(json.dumps(registry()))

    def tearDown(self):
        self.tmp.cleanup()

    def test_on_loopback_the_operator_is_the_owner(self):
        client = Client(server.serve('127.0.0.1', 0, datasets_file=self.file, verifier=None))
        try:
            body = client.call('/api/datasets')[1]
            self.assertEqual(len(body['datasets']), 3)
            self.assertEqual(body['caller']['owner'], True)
            self.assertEqual(body['auth'], {'enabled': False})
            self.assertEqual(client.call('/api/datasets/synthetic-credentialed/status')[0], 200)
            self.assertEqual(client.call('/api/auth-config')[1], {'enabled': False})
            self.assertEqual(client.call('/api/datasets', 'a.b.c')[:2], (401, {'error': 'AUTH_NOT_CONFIGURED'}))
        finally:
            client.close()

    def test_a_non_loopback_bind_refuses_a_non_public_dataset_without_a_realm(self):
        for host in ('0.0.0.0', '::', '10.42.0.7'):
            with self.assertRaises(ValueError) as caught:
                server.serve(host, 0, datasets_file=self.file, verifier=None)
            self.assertEqual(str(caught.exception), 'NON_PUBLIC_DATASET_NEEDS_OIDC_ON_A_NON_LOOPBACK_BIND')
        with self.assertRaises(ValueError):  # the flags' MIMIC demo is non-public too
            server.serve('0.0.0.0', 0, mimic_store='/nowhere', verifier=None)
        with mock.patch.dict(os.environ, {'PTM_OIDC_ISSUER': ISSUER}):  # an incomplete realm is refused, not ignored
            with self.assertRaises(ValueError):
                server.serve('127.0.0.1', 0)

    def test_health_answers_while_the_store_builds_and_ready_waits_for_it(self):
        release = threading.Event()
        real_build = store_trajectory.TrajectoryStore._build

        def slow(store):
            release.wait(60)
            return real_build(store)
        with mock.patch.object(store_trajectory.TrajectoryStore, '_build', slow):
            client = Client(server.serve('127.0.0.1', 0))
            try:
                started = time.time()
                self.assertEqual(client.call('/api/health')[:2], (200, {'profile': 'trajectory-graph-1.0'}))
                self.assertLess(time.time() - started, 5)
                self.assertEqual(client.call('/api/ready')[:2],
                                 (503, {'ready': False, 'datasets': {'ready': 0, 'pending': 1, 'failed': 0}}))
                release.set()
                wait_ready(client)
            finally:
                release.set()
                client.close()

    def test_body_limit_and_match_concurrency(self):
        client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, max_body=100, match_concurrency=1))
        entered, release = threading.Event(), threading.Event()

        def blocked(query, patients):
            entered.set()
            release.wait(30)
            return {'profile': 'trajectory-graph-1.0', 'results': []}
        try:
            self.assertEqual(client.call('/api/trajectory-graph/match', body=b'{"query": "' + b'x' * 200 + b'"}')[:2],
                             (413, {'error': 'BODY_TOO_LARGE'}))
            with mock.patch.object(server.engine, 'run', blocked), mock.patch.object(server, 'GATE_WAIT', 0.2):
                first = threading.Thread(target=client.call, args=('/api/trajectory-graph/match',),
                                         kwargs={'body': {'query': {}, 'patients': []}})
                first.start()
                self.assertTrue(entered.wait(10))
                status, body, headers = client.call('/api/trajectory-graph/match', body={'query': {}, 'patients': []})
                self.assertEqual((status, body, headers['Retry-After']), (503, {'error': 'BUSY'}, '2'))
                release.set()
                first.join(10)
        finally:
            release.set()
            client.close()


if __name__ == '__main__':
    unittest.main()
