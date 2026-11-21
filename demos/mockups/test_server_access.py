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
    # Say why: each dataset's state, and a hosted match worker's last start error.
    why = [(d.id, d.state(), getattr(d.matcher, '_last_error', None)) for d in client.httpd.registry.datasets]
    raise AssertionError(f'not ready: {why}')


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
        # A dataset the caller may not use is a 404, exactly like one that does not exist.
        for path in ('/api/datasets/synthetic-approved/status', '/api/datasets/synthetic-credentialed/status',
                     '/api/datasets/mimic-iv-demo/status', '/api/datasets/mimic-iv-demo/catalogue',
                     '/api/datasets/synthetic-approved/match', '/api/datasets/mimic-iv-demo/match'):
            body = mimic_query() if path.endswith('match') else None
            self.assertEqual(self.call(path, body=body)[:2], (404, {'error': 'DATASET_NOT_FOUND'}), path)
        # The old MIMIC routes answer as if no store were configured: they say nothing about a hidden dataset.
        status, body, _ = self.call('/api/store/status')
        self.assertEqual((status, body['configured'], body['error']), (200, False, 'STORE_NOT_CONFIGURED'))
        self.assertEqual(self.call('/api/store/catalogue')[:2], (503, {'error': 'STORE_NOT_CONFIGURED'}))
        self.assertEqual(self.call('/api/store/match', body=mimic_query())[:2], (503, {'error': 'STORE_NOT_CONFIGURED'}))

    def test_each_tier_by_token(self):
        expected = {  # (public, approved, role, mimic demo)
            'signed_in': (200, 404, 404, 404),
            'approved': (200, 200, 404, 200),
            'credentialed': (200, 200, 200, 200),
        }
        paths = ('/api/datasets/synthetic-paediatric-rare/status', '/api/datasets/synthetic-approved/status',
                 '/api/datasets/synthetic-credentialed/status', '/api/datasets/mimic-iv-demo/status')
        for who, statuses in expected.items():
            got = tuple(self.call(p, self.tokens[who])[0] for p in paths)
            self.assertEqual(got, statuses, who)
        self.assertEqual(self.call(paths[1], self.tokens['signed_in'])[1], {'error': 'DATASET_NOT_FOUND'})
        self.assertEqual(self.call(paths[2], self.tokens['approved'])[1], {'error': 'DATASET_NOT_FOUND'})
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
        self.assertEqual(self.call(path, self.tokens['approved'], body={})[:2], (404, {'error': 'DATASET_NOT_FOUND'}))
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
        self.assertEqual(self.call('/api/ready')[:2], (200, {'ready': True}))  # no detail for anyone but the owner

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
            # A line is written just after its response is sent, so an earlier test's last line can land here: wait
            # for a sentinel request's own line, then start from an empty log.
            self.call('/api/health')
            deadline = time.time() + 5
            while '"route": "/api/health"' not in out.getvalue() and time.time() < deadline:
                time.sleep(0.02)
            out.seek(0)
            out.truncate()
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
            ('/api/datasets/{id}/status', 200, 'bearer'), ('/api/datasets/{id}/status', 404, 'bearer'),
            ('/api/datasets/{id}/match', 200, 'bearer'), ('/api/datasets', 401, 'refused')])
        self.assertEqual(lines[0]['user'], 'f3c1e8a0-user')
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
                self.assertEqual(client.call('/api/ready')[:2],  # the owner (loopback) also gets the counts
                                 (503, {'ready': False, 'datasets': {'ready': 0, 'pending': 1, 'failed': 0, 'idle': 0}}))
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


class HardeningTests(unittest.TestCase):
    """The security review's findings (D50): bounded work per request, the hosted surface, malformed requests, the
    owner's Host check, readiness that opens nothing, and the Content-Security-Policy."""

    def engine_body(self, copies):
        query = json.loads((ROOT / 'examples/trajectory-graph/query.json').read_text())
        patients = json.loads((ROOT / 'examples/trajectory-graph/patients.json').read_text())
        many = []
        for i in range(copies):
            for p in patients:
                q = json.loads(json.dumps(p))
                q['patient_id'] = f"{p['patient_id']}x{i}"
                many.append(q)
        return {'query': query, 'patients': many}

    def test_the_reviewers_flood_scaled_down_is_bounded(self):
        # The review: valid engine requests near the body cap, many at once, each read and parsed before any gate,
        # on unbounded threads. Scaled down: 14 at once against 2 match slots and 6 request threads.
        client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, match_concurrency=2, max_threads=6))
        body = json.dumps(self.engine_body(60)).encode()   # 300 patients: valid, under MAX_PATIENTS
        lock, release = threading.Lock(), threading.Event()
        parsed, peak, codes = [0], [0], []
        real_read = server.Handler.read_json

        def counting_read(handler):
            result = real_read(handler)
            with lock:
                parsed[0] += 1
                peak[0] = max(peak[0], parsed[0])
            return result

        def blocked(query, patients):
            release.wait(20)
            with lock:
                parsed[0] -= 1
            return {'profile': 'trajectory-graph-1.0', 'results': []}

        def post():
            try:
                with urlopen(Request(client.url + '/api/trajectory-graph/match', data=body,
                                     headers={'Content-Type': 'application/json'}), timeout=30) as response:
                    codes.append(response.status)
            except HTTPError as error:
                codes.append(error.code)
            except OSError:
                codes.append('dropped')
        try:
            with mock.patch.object(server.Handler, 'read_json', counting_read), \
                    mock.patch.object(server.engine, 'run', blocked), mock.patch.object(server, 'GATE_WAIT', 0.5):
                threads = [threading.Thread(target=post) for _ in range(14)]
                for t in threads:
                    t.start()
                time.sleep(1.5)  # the gate's waiters have given up; two matches still hold their slots
                self.assertEqual(client.call('/api/health')[0], 200, 'the server answers during the flood')
                self.assertEqual(peak[0], 2, 'bodies are read only inside a match slot')
                release.set()
                for t in threads:
                    t.join(30)
            # Two were answered and the rest refused BUSY; on a slow machine a request that only arrives after the
            # release is answered too, which is fine: the property is that no more than two bodies were ever parsed.
            self.assertEqual(len(codes), 14)
            self.assertEqual(set(codes) - {200, 503}, set(), codes)
            self.assertGreaterEqual(codes.count(200), 2, codes)
            self.assertGreaterEqual(codes.count(503), 8, codes)
            self.assertEqual(peak[0], 2)
        finally:
            release.set()
            client.close()

    def test_requests_above_the_caps_are_refused_before_any_work(self):
        client = Client(server.serve('127.0.0.1', 0))
        try:
            too_many = self.engine_body(server.MAX_PATIENTS // 5 + 1)
            self.assertEqual(client.call('/api/trajectory-graph/match', body=too_many)[:2],
                             (400, {'error': 'TOO_MANY_PATIENTS'}))
            body = self.engine_body(1)
            body['patients'] = body['patients'][:1]
            body['patients'][0]['events'] = [{'id': f'e{i}'} for i in range(server.MAX_EVENTS + 1)]
            self.assertEqual(client.call('/api/trajectory-graph/match', body=body)[:2],
                             (400, {'error': 'TOO_MANY_EVENTS'}))
            wide = {'query': {'steps': [{}] * (server.MAX_STEPS + 1)}, 'windows': {}}
            self.assertEqual(client.call('/api/trajectory-store/match', body=wide)[:2],
                             (400, {'error': 'TOO_MANY_STEPS', 'by': 'server'}))
            many = {'query': {'steps': [], 'relations': [{}] * (server.MAX_RELATIONS + 1)}, 'windows': {}}
            self.assertEqual(client.call('/api/trajectory-store/match', body=many)[:2],
                             (400, {'error': 'TOO_MANY_RELATIONS', 'by': 'server'}))
        finally:
            client.close()

    def test_the_request_thread_cap_keeps_a_reserve_for_the_probes(self):
        client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, max_threads=1))
        import socket
        port = client.httpd.server_port
        idle = [socket.create_connection(('127.0.0.1', port))]  # holds the only ordinary slot
        try:
            time.sleep(0.2)
            # Beyond the cap, a reserve thread still answers the probes, and nothing else.
            self.assertEqual(client.call('/api/health')[:2], (200, {'profile': 'trajectory-graph-1.0'}))
            self.assertEqual(client.call('/api/ready')[0], 200)
            status, body, headers = client.call('/api/datasets')
            self.assertEqual((status, body, headers['Retry-After']), (503, {'error': 'BUSY'}, '2'))
            # With the reserve held too, 503 BUSY at once, without a thread.
            idle += [socket.create_connection(('127.0.0.1', port)) for _ in range(server.PROBE_RESERVE)]
            time.sleep(0.2)
            status, body, headers = client.call('/api/health')
            self.assertEqual((status, body, headers['Retry-After']), (503, {'error': 'BUSY'}, '2'))
        finally:
            for sock in idle:
                sock.close()
        deadline = time.time() + 5
        while client.call('/api/datasets')[0] != 200 and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(client.call('/api/datasets')[0], 200)
        self.assertEqual(server.Handler.timeout, 30)  # an idle connection holds its slot for 30 s at most
        client.close()

    def test_a_nul_byte_in_a_path_is_a_404(self):
        import socket
        client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, hosted=True))
        err = io.StringIO()
        try:
            with mock.patch.object(sys, 'stderr', err):
                for raw in (b'GET /index%00.html HTTP/1.0\r\n\r\n', b'GET /shared/\x00core.js HTTP/1.0\r\n\r\n',
                            b'GET /api/health%00 HTTP/1.0\r\n\r\n'):
                    with socket.create_connection(('127.0.0.1', client.httpd.server_port), timeout=5) as sock:
                        sock.sendall(raw)
                        reply = sock.recv(256)
                    self.assertTrue(reply.startswith(b'HTTP/1.0 404'), (raw, reply[:40]))
                time.sleep(0.2)
            self.assertNotIn('Traceback', err.getvalue())
            self.assertNotIn('ValueError', err.getvalue())
        finally:
            client.close()

    def test_the_hosted_surface(self):
        client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, hosted=True))
        try:
            for path in ('/server.py', '/access.py', '/README.md', '/shared/', '/test_access.py', '/results/build.py'):
                with self.assertRaises(HTTPError, msg=path) as caught:
                    urlopen(client.url + path, timeout=10)
                self.assertEqual(caught.exception.code, 404, path)
            for path in ('/', '/index.html', '/trajectory/', '/shared/core.js', '/shared/auth.js'):
                with urlopen(client.url + path, timeout=10) as response:
                    self.assertEqual(response.status, 200, path)
                    self.assertEqual(response.headers['Server'], 'ptm')
            self.assertEqual(client.call('/api/trajectory-graph/match', body=self.engine_body(1))[:2],
                             (404, {'error': 'NOT_FOUND'}))  # the anonymous engine route is off when hosted
            self.assertFalse(client.call('/api/datasets')[1]['caller']['owner'])  # no loopback owner when hosted
        finally:
            client.close()

    def test_a_malformed_request_line_logs_no_address_or_traceback(self):
        import socket
        for log_format in ('text', 'json'):
            client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, log_format=log_format))
            err, out = io.StringIO(), io.StringIO()
            try:
                with mock.patch.object(sys, 'stderr', err), mock.patch.object(sys, 'stdout', out):
                    for raw in (b'GET / HTTP/9.9.9\r\n\r\n', b'GET /api/health HTTP/1.1 extra\r\n\r\n'):
                        with socket.create_connection(('127.0.0.1', client.httpd.server_port), timeout=5) as sock:
                            sock.sendall(raw)
                            reply = sock.recv(1024)
                        self.assertTrue(reply, raw)  # an error page (HTTP/0.9 style for a bad version), not a hang
                    time.sleep(0.2)
                logged = err.getvalue() + out.getvalue()
                self.assertNotIn('Traceback', logged)
                self.assertNotIn('127.0.0.1', logged) if log_format == 'json' else None
                self.assertEqual(client.call('/api/health')[0], 200)
            finally:
                client.close()

    def test_the_owner_is_only_a_request_addressed_to_localhost(self):
        client = Client(server.serve('127.0.0.1', 0, trajectory_store=False))
        try:
            self.assertTrue(client.call('/api/datasets')[1]['caller']['owner'])  # Host: 127.0.0.1:<port>
            for host in ('rebind.attacker.example', f'rebind.attacker.example:{client.httpd.server_port}',
                         'tunnel.example.org'):
                caller = client.call('/api/datasets', headers={'Host': host})[1]['caller']
                self.assertFalse(caller['owner'], host)
        finally:
            client.close()

    def test_readiness_opens_nothing_and_details_only_for_the_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, mimic_store=tmp))
            try:
                anonymous = {'Host': 'elsewhere.example'}
                self.assertEqual(client.call('/api/ready', headers=anonymous)[:2], (200, {'ready': True}))
                self.assertEqual(client.call('/api/ready')[1],
                                 {'ready': True, 'datasets': {'ready': 0, 'pending': 0, 'failed': 0, 'idle': 1}})
                self.assertFalse(client.httpd.store.opened, 'a readiness probe never opens a store')
            finally:
                client.close()

    def test_the_content_security_policy_covers_every_page(self):
        realm = access.OidcVerifier(ISSUER, 'http://127.0.0.1:9/certs', AUDIENCE)
        client = Client(server.serve('127.0.0.1', 0, trajectory_store=False, verifier=realm))
        try:
            with urlopen(client.url + '/index.html', timeout=10) as response:
                csp = response.headers['Content-Security-Policy']
        finally:
            client.close()
        directives = dict(d.strip().split(' ', 1) for d in csp.split(';'))
        self.assertEqual(directives['default-src'], "'self'")
        self.assertEqual(directives['frame-ancestors'], "'none'")
        self.assertEqual(directives['connect-src'], "'self' https://keycloak.example.test")
        self.assertNotIn('unsafe-inline', directives['script-src'])
        self.assertNotIn('unsafe-eval', directives['script-src'])
        import base64
        import hashlib
        import re
        pages = sorted(HERE.glob('**/*.html'))
        self.assertGreaterEqual(len(pages), 6)
        for page in pages:
            text = page.read_text(encoding='utf-8')
            for script in re.findall(r'<script>(.*?)</script>', text, re.S):
                digest = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
                self.assertIn(f"'sha256-{digest}'", directives['script-src'], page)
            self.assertIsNone(re.search(r'<[^>]+\son[a-z]+\s*=', text), f'inline event handler in {page}')
            self.assertNotIn('javascript:', text, page)
            self.assertIsNone(re.search(r'<(script|link|img)[^>]+(src|href)="(https?:)?//', text), f'external resource in {page}')
        for script in sorted(HERE.glob('**/*.js')):
            text = script.read_text(encoding='utf-8')
            self.assertIsNone(re.search(r'\son[a-z]+=\\?["\']', text), f'inline handler in markup built by {script}')
            self.assertNotIn('eval(', text, script)
            self.assertNotIn('new Function', text, script)


def costly_request(windows, steps=6, relaxations=4, search_budget=10_000_000):
    """The costliest shape measured (D50): seizure steps cycling first, last and any, chained by `after` 1 to 2 days,
    with tiny-cost relaxations so every subset of them is tried. About 6.5 s in-process at any search budget."""
    day = 1440
    occ = ('first', 'last', 'any')
    return {'query': {'profile': 'trajectory-graph-1.0', 'scope': 'any',
                      'steps': [{'id': f'S{i}', 'concept': 'seizure', 'occurrence': occ[i % 3]} for i in range(steps)],
                      'relations': [{'id': f'edge:{i}', 'kind': 'after', 'from': f'S{i}', 'to': f'S{i + 1}',
                                     'min': day, 'max': 2 * day} for i in range(steps - 1)],
                      'relaxations': [{'id': f'gap:{i}', 'kind': 'extend_max', 'relation': f'edge:{i}',
                                       'max': 5 * day, 'cost': '0.01'} for i in range(relaxations)],
                      'budget': '1', 'search_budget': search_budget},
            'conditions': None, 'windows': windows}


@unittest.skipUnless(store_trajectory.COHORT.is_file(), 'run demos/mockups/synthetic/generate.py first')
class HostedStoreBoundTests(unittest.TestCase):
    """The security re-check's M1 residual: one small request must not hold the synthetic store for long. Hosted, the
    search budget is clamped, the caps are lower, and every match runs in a child process killed at the time limit."""

    @classmethod
    def setUpClass(cls):
        from test_store_trajectory import requests
        cls.client = Client(server.serve('127.0.0.1', 0, hosted=True, match_time_limit=1.0))
        wait_ready(cls.client)
        pids = sorted(json.loads(store_trajectory.COHORT.read_text())['patients'], key=lambda p: p['patient_id'])
        cls.pids = [p['patient_id'] for p in pids]
        cls.page_like = requests(cls.pids)
        cls.windows = {pid: {'start': 0, 'end': 3653 * 10 * 1440} for pid in cls.pids}

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def adversarial(self, steps=6, relaxations=4, search_budget=10_000_000):
        return costly_request(self.windows, steps, relaxations, search_budget)

    def test_a_costly_request_is_cut_at_the_time_limit_and_the_store_recovers(self):
        started = time.perf_counter()
        status, body, _ = self.client.call('/api/trajectory-store/match', body=self.adversarial())
        elapsed = time.perf_counter() - started
        self.assertEqual((status, body), (503, {'error': 'MATCH_TIME_LIMIT'}))
        self.assertLess(elapsed, 4, 'cut near the 1 s limit (plus waiting for the child to be ready)')
        # The child is started again; the page's own kind of request is answered.
        for name, request in list(self.page_like.items())[:2]:
            status, body, _ = self.client.call('/api/trajectory-store/match', body=request)
            self.assertEqual(status, 200, (name, body))

    def test_hosted_answers_equal_the_in_process_store(self):
        # Through the child, at the clamped budget, the page's requests get the in-process store's answers at 40,000.
        local = Client(server.serve('127.0.0.1', 0))
        try:
            wait_ready(local)
            for name, request in self.page_like.items():
                hosted = self.client.call('/api/trajectory-store/match', body=request)
                direct = local.call('/api/trajectory-store/match', body=request)
                self.assertEqual(hosted[0], direct[0], name)
                if hosted[0] == 200:
                    # the context id digests the request, search budget included, so it differs by design
                    strip = lambda b: {k: v for k, v in b.items() if k not in ('elapsed_ms', 'context_id')}
                    self.assertEqual(strip(hosted[1]), strip(direct[1]), name)
        finally:
            local.close()

    def test_hosted_caps_and_the_budget_clamp(self):
        call = lambda body: self.client.call('/api/trajectory-store/match', body=body)[:2]
        self.assertEqual(call(self.adversarial(steps=server.HOSTED_MAX_STEPS + 1, relaxations=0)),
                         (400, {'error': 'TOO_MANY_STEPS', 'by': 'server'}))
        too_relaxed = self.adversarial(relaxations=0)
        too_relaxed['query']['relaxations'] = [{'id': f'concept:S{i % 6}', 'kind': 'widen_concept', 'step': f'S{i % 6}',
                                                'concept': 'seizure', 'cost': '0.01'} for i in range(server.HOSTED_MAX_RELAXATIONS + 1)]
        self.assertEqual(call(too_relaxed), (400, {'error': 'TOO_MANY_RELAXATIONS', 'by': 'server'}))
        costly = self.adversarial(relaxations=1)
        costly['query']['budget'] = '1.5'
        self.assertEqual(call(costly), (400, {'error': 'RELAXATION_BUDGET_ABOVE_HOSTED_MAX', 'by': 'server'}))
        original = self.adversarial()
        clamped = server.hosted_request(original)
        self.assertEqual(clamped['query']['search_budget'], server.HOSTED_SEARCH_BUDGET)
        self.assertEqual(original['query']['search_budget'], 10_000_000, "the caller's request is not changed")
        lower = self.adversarial(search_budget=200)
        self.assertEqual(server.hosted_request(lower)['query']['search_budget'], 200)
        missing = self.adversarial()
        del missing['query']['search_budget']
        self.assertEqual(server.hosted_request(missing)['query']['search_budget'], server.HOSTED_SEARCH_BUDGET)
        self.assertEqual(self.client.call('/api/limits')[1],
                         {'hosted': True, 'search_budget': server.HOSTED_SEARCH_BUDGET, 'match_time_limit_s': 1.0,
                          'max_steps': server.HOSTED_MAX_STEPS, 'max_relaxations': server.HOSTED_MAX_RELAXATIONS})

    def test_a_hosted_realm_needs_the_client_id(self):
        realm = access.OidcVerifier(ISSUER, 'http://127.0.0.1:9/certs', AUDIENCE)  # no client id
        with self.assertRaises(ValueError) as caught:
            server.serve('127.0.0.1', 0, trajectory_store=False, hosted=True, verifier=realm)
        self.assertEqual(str(caught.exception), 'HOSTED_REALM_NEEDS_PTM_OIDC_CLIENT_ID')


@unittest.skipUnless(store_trajectory.COHORT.is_file(), 'run demos/mockups/synthetic/generate.py first')
class HostedWorkerFloodTests(unittest.TestCase):
    """The final check's medium-low: a client sending costly matches back to back kept the worker's queue full, and
    legitimate matches waited 12 to 16 s. Now a spare worker takes over at once after a kill (the rebuild is off the
    request path), a match waits at most GATE_WAIT for the worker (then 503 BUSY), and each kill is logged."""

    def test_the_reviewers_flood_scaled_down_keeps_legitimate_matches_bounded(self):
        from test_store_trajectory import requests
        out = io.StringIO()
        gate_wait = mock.patch.object(server, 'GATE_WAIT', 1.0)
        gate_wait.start()
        self.addCleanup(gate_wait.stop)
        with mock.patch.object(sys, 'stdout', out):
            client = Client(server.serve('127.0.0.1', 0, hosted=True, match_time_limit=0.5, log_format='json'))
        try:
            wait_ready(client)
            pids = sorted(p['patient_id'] for p in json.loads(store_trajectory.COHORT.read_text())['patients'])
            light = list(requests(pids).values())[0]
            heavy = costly_request({pid: {'start': 0, 'end': 3653 * 10 * 1440} for pid in pids})
            stop = time.time() + 4
            attack, user = [], []

            def attacker():  # faster than the time limit: four costly matches a second
                while time.time() < stop:
                    threading.Thread(target=lambda: attack.append(
                        client.call('/api/trajectory-store/match', body=heavy)[:2])).start()
                    time.sleep(0.25)

            def legitimate():
                while time.time() < stop:
                    started = time.perf_counter()
                    status, body, _ = client.call('/api/trajectory-store/match', body=light)
                    user.append((status, body.get('error'), round(time.perf_counter() - started, 2)))
                    time.sleep(0.3)
            with mock.patch.object(sys, 'stdout', out):
                threads = [threading.Thread(target=attacker), threading.Thread(target=legitimate)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(30)
                time.sleep(3)  # the attacker's last requests finish
            self.assertTrue(user and attack)
            # Every legitimate match is answered or refused BUSY within GATE_WAIT plus one time limit (and a margin).
            self.assertLess(max(seconds for _, _, seconds in user), 1.0 + 0.5 + 1.0, user)
            self.assertTrue(all(status == 200 or (status, error) == (503, 'BUSY') for status, error, _ in user), user)
            self.assertGreater(sum(1 for status, _, _ in user if status == 200), 0, user)
            self.assertTrue(all(code in (503,) for code, _ in attack), attack)
            kills = [json.loads(line) for line in out.getvalue().splitlines() if '"match_killed"' in line]
            self.assertGreater(len(kills), 0)
            self.assertEqual(set(kills[0]), {'ts', 'event', 'reason'})  # no body, no address
            # After the flood, once a worker is built again (off any request's path; a few seconds on a slow
            # machine), a legitimate match is answered quickly.
            deadline = time.time() + 15
            while client.call('/api/ready')[0] != 200 and time.time() < deadline:
                time.sleep(0.2)
            started = time.perf_counter()
            self.assertEqual(client.call('/api/trajectory-store/match', body=light)[0], 200)
            self.assertLess(time.perf_counter() - started, 1.0)
        finally:
            client.close()

    def test_one_client_within_the_front_doors_bounds_cannot_starve_others(self):
        # Traefik now lets one client have one match in flight and about one per 3 s (ingress-match.yaml). Scaled down
        # in the time limit only (0.5 s; still one costly match per 3 s, one at a time, as the spare worker's rebuild
        # takes about a second whatever the limit), every legitimate match is answered, after waiting at most for the
        # one costly match ahead of it.
        from test_store_trajectory import requests
        client = Client(server.serve('127.0.0.1', 0, hosted=True, match_time_limit=0.5))
        try:
            wait_ready(client)
            pids = sorted(p['patient_id'] for p in json.loads(store_trajectory.COHORT.read_text())['patients'])
            light = list(requests(pids).values())[0]
            heavy = costly_request({pid: {'start': 0, 'end': 3653 * 10 * 1440} for pid in pids})
            stop = time.time() + 7
            attack, user = [], []

            def attacker():
                while time.time() < stop:
                    started = time.time()
                    attack.append(client.call('/api/trajectory-store/match', body=heavy)[0])
                    time.sleep(max(0, 3 - (time.time() - started)))

            def legitimate():
                while time.time() < stop:
                    started = time.perf_counter()
                    status = client.call('/api/trajectory-store/match', body=light)[0]
                    user.append((status, round(time.perf_counter() - started, 2)))
                    time.sleep(0.3)
            threads = [threading.Thread(target=attacker), threading.Thread(target=legitimate)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            self.assertTrue(attack and all(code == 503 for code in attack), attack)
            # Each legitimate match is answered after at most the one costly match ahead of it, or, on a machine too
            # slow to rebuild the spare between two costly matches, refused at once (BUSY): never left waiting.
            self.assertTrue(all(status in (200, 503) for status, _ in user), user)
            self.assertGreaterEqual(sum(1 for status, _ in user if status == 200), 0.8 * len(user), user)
            self.assertLess(max(seconds for _, seconds in user), 0.5 + 1.0, user)
        finally:
            client.close()

    def test_a_worker_that_answers_internal_is_killed_and_never_reused(self):
        # D52b's review: a child that answered `internal` (a MemoryError, say) was reused, and stayed broken until a
        # time limit. Now it is killed like a timed-out one, the kill is logged, and the request is a plain 500.
        import match_worker
        from test_store_trajectory import requests
        events = []
        out = io.StringIO()
        with mock.patch.object(sys, 'stdout', out):
            client = Client(server.serve('127.0.0.1', 0, hosted=True, match_time_limit=5, log_format='json'))
        try:
            wait_ready(client)
            matcher = client.httpd.registry.first('synthetic-trajectory').matcher
            matcher.on_event = lambda event, **fields: events.append((event, fields))
            pids = sorted(p['patient_id'] for p in json.loads(store_trajectory.COHORT.read_text())['patients'])
            light = list(requests(pids).values())[0]
            child = matcher._active
            real_read = match_worker._Child.read
            with mock.patch.object(match_worker._Child, 'read',
                                   lambda self, timeout: (real_read(self, timeout), {'internal': True})[1]):
                status, body, _ = client.call('/api/trajectory-store/match', body=light)
            self.assertEqual((status, body), (500, {'error': 'INTERNAL_ERROR'}))
            self.assertIsNotNone(child.process.poll(), 'the worker that answered internal is killed')
            self.assertIsNot(matcher._active, child, 'and never reused')
            self.assertEqual(events[0], ('match_killed', {'reason': 'MATCH_WORKER_INTERNAL'}))
            self.assertEqual(matcher.kills, 1)
            deadline = time.time() + 15
            while client.call('/api/ready')[0] != 200 and time.time() < deadline:
                time.sleep(0.2)
            self.assertEqual(client.call('/api/trajectory-store/match', body=light)[0], 200)  # the spare answers
        finally:
            client.close()

    def test_a_failure_after_the_write_kills_the_worker_and_is_named(self):
        import match_worker
        matcher = match_worker.IsolatedMatcher(store_trajectory.COHORT, time_limit=5).start()
        try:
            pids = sorted(p['patient_id'] for p in json.loads(store_trajectory.COHORT.read_text())['patients'])
            from test_store_trajectory import requests
            light = list(requests(pids).values())[0]
            self.assertIn('results', matcher.match(light))
            for failure in (EOFError('closed'), ValueError('garbage')):
                child = matcher._active
                with mock.patch.object(match_worker._Child, 'read', side_effect=failure):
                    with self.assertRaises(store_trajectory.Unavailable) as caught:
                        matcher.match(light)
                self.assertEqual(str(caught.exception), 'MATCH_WORKER_FAILED')  # a crash is not a time limit
                self.assertIsNotNone(child.process.poll(), 'the worker is killed, never reused')
                deadline = time.time() + 10
                while matcher._spare is None and time.time() < deadline:
                    time.sleep(0.1)
                self.assertIn('results', matcher.match(light))
            self.assertEqual(matcher.kills, 2)
        finally:
            matcher.close()

    def test_the_worker_confines_duckdbs_threads(self):
        # DuckDB 2.0 starts 4 async threads per CPU it sees; the worker opens every database with one worker thread and
        # one async thread, so its address space (and RLIMIT_AS) does not depend on the host's CPUs. In a subprocess,
        # as the worker does it, so this test process's duckdb is left alone.
        import subprocess
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import match_worker; match_worker.confine_duckdb(); '
                'import duckdb; db = duckdb.connect(":memory:", config={"preserve_insertion_order": False}); '
                'print(db.execute("select current_setting(\'threads\'), current_setting(\'async_threads\')").fetchall())')
        out = subprocess.run([sys.executable, '-I', '-c', code, str(HERE)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.strip(), '[(1, 1)]', out.stderr[-500:])

    def test_the_time_limit_must_be_finite_and_bounded_when_hosted(self):
        for bad in (float('inf'), float('nan'), 0, -1, 31, True):
            with self.assertRaises(ValueError, msg=bad):
                server.match_time_limit_from(bad, hosted=True)
        for raw in ('inf', '0', '', 'abc', '45'):
            with mock.patch.dict(os.environ, {'PTM_MATCH_TIME_LIMIT': raw}):
                if raw == '':
                    self.assertEqual(server.match_time_limit_from(None, hosted=True), server.HOSTED_MATCH_TIME_LIMIT)
                else:
                    with self.assertRaises(ValueError, msg=raw):
                        server.match_time_limit_from(None, hosted=True)
        self.assertEqual(server.match_time_limit_from(None, hosted=False), 0)  # locally: in-process, as before
        self.assertEqual(server.match_time_limit_from(0, hosted=False), 0)
        self.assertEqual(server.match_time_limit_from(30, hosted=True), 30)
        with self.assertRaises(ValueError):
            server.serve('127.0.0.1', 0, trajectory_store=False, hosted=True, match_time_limit=float('inf'))


if __name__ == '__main__':
    unittest.main()
