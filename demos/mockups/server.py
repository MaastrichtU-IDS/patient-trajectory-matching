"""Serve the mockups and the trajectory-graph engine to them, on one local port.

    python demos/mockups/server.py [--port 8090] [--host 127.0.0.1] [--mimic-store PATH] [--mimic-d-items FILE]
                                   [--trajectory-store-root DIR] [--no-trajectory-store] [--datasets FILE]

Static files come from this directory, as with `python -m http.server`. Two endpoints are
added: `GET /api/health` names the engine profile, and `POST /api/trajectory-graph/match`
takes `{"query": ..., "patients": [...]}` in the trajectory-graph-1.0 form and returns the
engine's results (docs/trajectory-graph-profile.md). A request outside the profile gets
400 with the engine's error code. The trajectory page falls back to matching in the
browser when the endpoint is absent, as it is on static hosting.

The store query page (`store-query/`) uses three more, from `store_query.py`: `GET /api/store/status`,
`GET /api/store/catalogue` and `POST /api/store/match`. They serve a locally built MIMIC-IV demo
store, named by `--mimic-store` or the `PTM_MIMIC_STORE` environment variable (item labels from
`--mimic-d-items` or `PTM_MIMIC_D_ITEMS`), answer with aggregate counts only, and refuse any
snapshot whose patients are not the open demo's (checked by content, not by its label). Counts-only
is not disclosure control (no small-cell suppression; differencing works), so these endpoints are
for the open demo only. With no store configured, status says so and how to build one.

The trajectory page matches in the execution store by default (D29 stage 2, D37), through two more endpoints from
`store_trajectory.py`: `GET /api/trajectory-store/status` and `POST /api/trajectory-store/match`. At startup the
server builds the page's synthetic cohort into a store (about a second, on a background thread), in a temporary
directory removed when the server closes (cleanly, or on SIGTERM, SIGINT or SIGHUP; never on SIGKILL), or under `--trajectory-store-root` (`PTM_TRAJECTORY_STORE_ROOT`), never
inside the repository. These answer per patient, since the data are synthetic and the page already holds every
record; the MIMIC endpoints keep their aggregate-only rules. `--no-trajectory-store` leaves the store out, and the
page then matches as before.

**Datasets and access (D50, docs/hosting-plan.md).** Those stores are datasets in a registry (`datasets.py`), each
with an access policy (public; approved; or a role) and what it may return. The registry comes from `--datasets`
or `PTM_DATASETS`, else from the flags above (the synthetic cohort public, the MIMIC demo `approved`). Each dataset
answers at `/api/datasets/<id>/status`, `.../catalogue` (aggregate kinds) and `.../match`; the routes above are
aliases of the first dataset of each kind. `GET /api/datasets` lists the datasets the caller may use, and
`GET /api/auth-config` tells the page how to sign in. The caller (`access.py`) is a verified realm bearer token
when PTM_OIDC_ISSUER, PTM_OIDC_JWKS_URI and PTM_OIDC_AUDIENCE are set; otherwise, on a loopback bind, the person
at the machine, who is the owner and is served every configured dataset as before; otherwise anonymous, served
public datasets only. On a non-loopback bind the server refuses to start with a non-public dataset and no realm.

`GET /api/ready` answers 200 once every dataset is ready (503 before), while `/api/health` answers from the moment
the socket is bound. PTM_MAX_BODY caps request bodies (default 20 MiB) and PTM_MATCH_CONCURRENCY the matches run at
once (default unlimited). PTM_LOG_FORMAT=json writes one JSON line per API request to stdout: never a body, a token,
a header or an address.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socketserver
import sys
import threading
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from patterns import trajectory_graph as engine  # noqa: E402
import access  # noqa: E402
import datasets  # noqa: E402
import store_query  # noqa: E402
import store_trajectory  # noqa: E402

MAX_BODY = 20 * 1024 * 1024
GATE_WAIT = 5  # seconds a match waits for a free slot under PTM_MATCH_CONCURRENCY before 503 BUSY
SECURITY_HEADERS = (('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                    ('X-Frame-Options', 'DENY'))
DATASET_ROUTE = re.compile(r'/api/datasets/([^/]+)/(status|catalogue|match)')
LOG_LOCK = threading.Lock()


class Handler(SimpleHTTPRequestHandler):
    registry = datasets.Registry([])     # `serve` gives each server its own
    auth = access.Auth()
    unconfigured_store = store_query.StoreService()  # answers the MIMIC routes when no MIMIC dataset is configured
    max_body = MAX_BODY
    match_gate = None
    log_json = False

    # ------------------------------------------------------------ responses and logs

    def end_headers(self):
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        super().end_headers()

    def send_json(self, status, value, headers=()):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for name, header in headers:
            self.send_header(name, header)
        self.end_headers()
        self.wfile.write(body)

    def refuse(self, error):
        """An AuthError as its status and code; a 401 says how to authenticate. The log keeps the caller's kind when it
        is known (a policy refusal) and says `refused` when it is not (an invalid token)."""
        if getattr(self, 'log_caller', None) is None:
            self.log_caller = 'refused'
        headers = ()
        if error.status == 401:
            challenge = 'Bearer realm="ptm"' + (', error="invalid_token"' if error.code == 'TOKEN_INVALID' else '')
            headers = (('WWW-Authenticate', challenge),)
        return self.send_json(error.status, {'error': error.code}, headers)

    def begin(self, route):
        self.started = time.perf_counter()
        self.route, self.log_dataset, self.log_caller, self.log_subject = route, None, None, None

    def log_request(self, code='-', size='-'):
        if not self.path.startswith('/api/'):
            return
        if not self.log_json:
            return super().log_request(code, size)
        self.log_status = int(code) if str(code).isdigit() else None  # written once the response is sent

    def handle_one_request(self):
        self.log_status = None
        super().handle_one_request()
        if self.log_json and self.log_status is not None:
            line = {'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'event': 'request', 'method': self.command,
                    'route': getattr(self, 'route', 'unknown'), 'status': self.log_status,
                    'ms': round((time.perf_counter() - getattr(self, 'started', time.perf_counter())) * 1000, 1),
                    'dataset': getattr(self, 'log_dataset', None), 'auth': getattr(self, 'log_caller', None)}
            if getattr(self, 'log_subject', None):
                line['user'] = self.log_subject
            with LOG_LOCK:
                sys.stdout.write(json.dumps(line) + '\n')
                sys.stdout.flush()

    def log_error(self, format, *args):
        if not self.log_json:
            return super().log_error(format, *args)
        with LOG_LOCK:  # a malformed request: its code only, never the raw request line
            sys.stdout.write(json.dumps({'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'event': 'http_error',
                                         'status': args[0] if args and isinstance(args[0], int) else None}) + '\n')
            sys.stdout.flush()

    def log_message(self, format, *args):
        if self.path.startswith('/api/') and not self.log_json:
            super().log_message(format, *args)

    # ------------------------------------------------------------ who is calling

    def caller(self):
        """The request's Principal, or AuthError."""
        principal = self.auth.principal(self.headers)
        self.log_caller, self.log_subject = principal.kind, principal.subject
        return principal

    # ------------------------------------------------------------ GET

    def do_GET(self):
        path = self.path
        if not path.startswith('/api/'):
            return super().do_GET()
        found = DATASET_ROUTE.fullmatch(path)
        self.begin('/api/datasets/{id}/' + found.group(2) if found else path if path in GET_ROUTES else 'unknown')
        if path == '/api/health':
            return self.send_json(200, {'profile': engine.PROFILE_ID})
        if path == '/api/ready':
            return self.ready()
        if path == '/api/auth-config':
            return self.send_json(200, self.auth.public_config())
        if path == '/api/datasets':
            return self.dataset_list()
        if path in ('/api/store/status', '/api/store/catalogue'):
            dataset = self.registry.first('mimic-demo-aggregate')
            if dataset is None:
                service = self.unconfigured_store
                return self.store_answer(service.status if path.endswith('status') else service.catalogue)
            return self.dataset_request(dataset, path.rsplit('/', 1)[1])
        if path == '/api/trajectory-store/status':
            dataset = self.registry.first('synthetic-trajectory')
            if dataset is None:
                return self.send_json(200, {'profile': store_trajectory.PROFILE, 'available': False,
                                            'error': 'STORE_NOT_CONFIGURED'})
            return self.dataset_request(dataset, 'status')
        if found and found.group(2) != 'match':
            return self.dataset_route(found)
        return self.send_json(404, {'error': 'NOT_FOUND'})

    def ready(self):
        states = [d.warm() for d in self.registry.datasets]
        summary = {state: states.count(state) for state in ('ready', 'pending', 'failed')}
        ready = summary['ready'] == len(states)
        return self.send_json(200 if ready else 503, {'ready': ready, 'datasets': summary})

    def dataset_list(self):
        try:
            principal = self.caller()
        except access.AuthError as error:
            return self.refuse(error)
        return self.send_json(200, {
            'auth': {'enabled': self.auth.verifier is not None},
            'caller': {'signed_in': principal.signed_in, 'approved': principal.approved,
                       'owner': principal.kind == 'owner'},
            'datasets': [d.describe() for d in self.registry.visible(principal)]})

    def dataset_route(self, found):
        dataset = self.registry.get(found.group(1))
        if dataset is None:
            return self.send_json(404, {'error': 'DATASET_NOT_FOUND'})
        if found.group(2) == 'catalogue' and dataset.kind != 'mimic-demo-aggregate':
            return self.send_json(404, {'error': 'NOT_FOUND'})
        return self.dataset_request(dataset, found.group(2))

    def dataset_request(self, dataset, operation):
        """A dataset's status, catalogue or match, after the caller is checked against its policy."""
        self.log_dataset = dataset.id
        try:
            dataset.policy.check(self.caller())
        except access.AuthError as error:
            return self.refuse(error)
        service = dataset.service
        if operation == 'match':
            data, refused = self.read_json()
            if refused:
                return self.send_json(refused[0], {'error': refused[1]})
            answer = self.trajectory_answer if dataset.kind == 'synthetic-trajectory' else self.store_answer
            return self.gated(lambda: answer(lambda: service.match(data)))
        if dataset.kind == 'synthetic-trajectory':
            return self.trajectory_answer(service.status)
        return self.store_answer(service.status if operation == 'status' else service.catalogue)

    # ------------------------------------------------------------ POST

    def read_json(self):
        """(body, None), or (None, (status, error)) when the request is refused before parsing."""
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return None, (415, 'JSON_REQUIRED')
        try:
            size = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return None, (411, 'LENGTH_REQUIRED')
        if not 0 < size <= self.max_body:
            return None, (413, 'BODY_TOO_LARGE')
        try:
            return json.loads(self.rfile.read(size)), None
        except (ValueError, RecursionError):
            return None, (400, 'MALFORMED_REQUEST')

    def do_POST(self):
        path = self.path
        found = DATASET_ROUTE.fullmatch(path)
        self.begin('/api/datasets/{id}/match' if found else path if path in POST_ROUTES else 'unknown')
        if path == '/api/store/match':
            dataset = self.registry.first('mimic-demo-aggregate')
            if dataset is None:
                data, refused = self.read_json()
                if refused:
                    return self.send_json(refused[0], {'error': refused[1]})
                return self.store_answer(lambda: self.unconfigured_store.match(data))
            return self.dataset_request(dataset, 'match')
        if path == '/api/trajectory-store/match':
            dataset = self.registry.first('synthetic-trajectory')
            if dataset is None:
                data, refused = self.read_json()
                if refused:
                    return self.send_json(refused[0], {'error': refused[1]})
                return self.send_json(503, {'error': 'STORE_NOT_CONFIGURED'})
            return self.dataset_request(dataset, 'match')
        if found and found.group(2) == 'match':
            return self.dataset_route(found)
        if path != '/api/trajectory-graph/match':
            return self.send_json(404, {'error': 'NOT_FOUND'})
        data, refused = self.read_json()
        if refused:
            return self.send_json(refused[0], {'error': refused[1]})
        return self.gated(lambda: self.engine_match(data))

    def engine_match(self, data):
        """The engine on the patients the page sent: no stored data, so no dataset and no policy."""
        try:
            if not isinstance(data, dict) or set(data) != {'query', 'patients'} or not isinstance(data['patients'], list):
                raise engine.TrajectoryError('REQUEST_FIELDS_MUST_BE_QUERY_AND_PATIENTS')
            return self.send_json(200, engine.run(data['query'], data['patients']))
        except (ValueError, TypeError, KeyError) as error:
            # TrajectoryError is a ValueError.
            code = str(error) if isinstance(error, engine.TrajectoryError) else 'MALFORMED_REQUEST'
            return self.send_json(400, {'error': code})

    def gated(self, run):
        """`run` under PTM_MATCH_CONCURRENCY: a match waits up to GATE_WAIT seconds for a slot, then gets 503 BUSY."""
        gate = self.match_gate
        if gate is None:
            return run()
        if not gate.acquire(timeout=GATE_WAIT):
            return self.send_json(503, {'error': 'BUSY'}, (('Retry-After', '2'),))
        try:
            return run()
        finally:
            gate.release()

    def store_answer(self, answer):
        """A store endpoint's answer, or a named error; never a traceback or a dropped connection."""
        try:
            return self.send_json(200, answer())
        except store_query.QueryError as error:
            return self.send_json(400, {'error': str(error)})
        except store_query.Unavailable as error:
            return self.send_json(503, {'error': str(error)})
        except store_query.AggregateRuleViolation:
            # An answer that is not aggregate counts only is never sent, not even in part.
            return self.send_json(500, {'error': 'AGGREGATE_RULE_VIOLATION'})
        except Exception:  # noqa: BLE001 - an internal failure is named, with no detail that could carry data
            return self.send_json(500, {'error': 'INTERNAL_ERROR'})

    def trajectory_answer(self, answer):
        """The trajectory store's answer (D37), or a named error; never a traceback."""
        try:
            return self.send_json(200, answer())
        except store_trajectory.RequestError as error:  # `by`: the server's own check, or the store's refusal
            return self.send_json(400, {'error': str(error), 'by': error.by})
        except store_trajectory.Unavailable as error:
            return self.send_json(503, {'error': str(error)})
        except Exception:  # noqa: BLE001 - an internal failure is named, with no detail
            return self.send_json(500, {'error': 'INTERNAL_ERROR'})


GET_ROUTES = frozenset({'/api/health', '/api/ready', '/api/auth-config', '/api/datasets', '/api/store/status',
                        '/api/store/catalogue', '/api/trajectory-store/status'})
POST_ROUTES = frozenset({'/api/trajectory-graph/match', '/api/store/match', '/api/trajectory-store/match'})


class Server(ThreadingHTTPServer):
    registry = datasets.Registry([])
    store = None
    trajectory_store = None

    def server_bind(self):
        # http.server's own server_bind also calls socket.getfqdn() for a name nothing here uses; on a machine
        # whose reverse DNS is slow that held the socket closed for 35 s at startup.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port

    def server_close(self):
        super().server_close()
        for dataset in self.registry.datasets:
            if dataset.kind == 'synthetic-trajectory':
                dataset.service.close()


def _int_env(name, default):
    value = os.environ.get(name)
    if value in (None, ''):
        return default
    number = int(value)
    if number < 0:
        raise ValueError(f'{name} must not be negative')
    return number


def serve(host='127.0.0.1', port=8090, mimic_store=None, mimic_d_items=None, demo_pin=None,
          trajectory_store=True, trajectory_store_root=None, trajectory_cohort=None, *, datasets_file=None,
          verifier=None, max_body=None, match_concurrency=None, log_format=None):
    """The server. Datasets come from `datasets_file` or PTM_DATASETS, else from the flags: the synthetic cohort
    (`trajectory_cohort`, by default the page's) unless `trajectory_store` is false, built on a background thread
    under `trajectory_store_root` or PTM_TRAJECTORY_STORE_ROOT (else a temporary directory; `server_close()` removes
    it), and the MIMIC demo store from `mimic_store` or PTM_MIMIC_STORE (labels from `mimic_d_items` or
    PTM_MIMIC_D_ITEMS). `demo_pin` replaces the open demo's (patient count, subject digest) for tests on the
    synthetic fixture only. `verifier` (an access.OidcVerifier) or the PTM_OIDC_* variables configure the realm.
    Refuses (ValueError) a non-public dataset on a non-loopback bind without a realm."""
    mimic_store = mimic_store or os.environ.get('PTM_MIMIC_STORE') or None
    mimic_d_items = mimic_d_items or os.environ.get('PTM_MIMIC_D_ITEMS') or None
    root = trajectory_store_root or os.environ.get('PTM_TRAJECTORY_STORE_ROOT')
    datasets_file = datasets_file or os.environ.get('PTM_DATASETS') or None
    entries = datasets.load(datasets_file) if datasets_file else datasets.from_flags(trajectory_store, mimic_store)
    verifier = verifier if verifier is not None else access.verifier_from_env(os.environ)
    if not access.is_loopback(host) and verifier is None and any(e.policy.access != 'public' for e in entries):
        raise ValueError('NON_PUBLIC_DATASET_NEEDS_OIDC_ON_A_NON_LOOPBACK_BIND')
    pin = {'pin': demo_pin} if demo_pin else {}
    built = []
    for entry in entries:
        if entry.kind == 'synthetic-trajectory':
            cohort = entry.settings.get('cohort') or trajectory_cohort or store_trajectory.COHORT
            service = store_trajectory.TrajectoryStore(cohort, root)
        else:
            service = store_query.StoreService(entry.settings['store'],
                                               entry.settings.get('d_items') or (mimic_d_items if not datasets_file else None),
                                               **pin)
        built.append(datasets.Dataset(entry, service))
    registry = datasets.Registry(built)
    gate_size = match_concurrency if match_concurrency is not None else _int_env('PTM_MATCH_CONCURRENCY', 0)
    log_format = log_format or os.environ.get('PTM_LOG_FORMAT') or 'text'
    handler = type('ConfiguredHandler', (Handler,), {
        'registry': registry,
        'auth': access.Auth(verifier, local_owner=access.is_loopback(host)),
        'unconfigured_store': store_query.StoreService(),
        'max_body': max_body if max_body is not None else _int_env('PTM_MAX_BODY', MAX_BODY),
        'match_gate': threading.BoundedSemaphore(gate_size) if gate_size else None,
        'log_json': log_format == 'json'})
    httpd = Server((host, port), partial(handler, directory=str(HERE)))
    httpd.registry, httpd.auth = registry, handler.auth
    first_mimic = registry.first('mimic-demo-aggregate')
    first_synthetic = registry.first('synthetic-trajectory')
    httpd.store = first_mimic.service if first_mimic else handler.unconfigured_store
    for dataset in registry.datasets:  # the synthetic builds start now; a MIMIC store opens at its first use or probe
        if dataset.kind == 'synthetic-trajectory':
            dataset.service.start()
    httpd.trajectory_store = first_synthetic.service if first_synthetic else None
    return httpd


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--mimic-store', help='a locally built MIMIC-IV demo snapshot, or the root holding it '
                        '(default: $PTM_MIMIC_STORE); only the open demo is served')
    parser.add_argument('--mimic-d-items', help="the demo's d_items file, for item labels (default: $PTM_MIMIC_D_ITEMS)")
    parser.add_argument('--trajectory-store-root', help="where to build the synthetic cohort's store for the trajectory "
                        'page (default: $PTM_TRAJECTORY_STORE_ROOT, else a temporary directory); never inside a git working tree')
    parser.add_argument('--no-trajectory-store', action='store_true',
                        help='build no store; the trajectory page then matches with the engine or in the browser')
    parser.add_argument('--datasets', help='a dataset registry file (default: $PTM_DATASETS); replaces the store flags')
    args = parser.parse_args()

    def stop(signum, frame):  # a terminated server closes too, so the store's build is removed (D37); SIGKILL cannot be caught
        raise KeyboardInterrupt
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        signal.signal(getattr(signal, name), stop)
    try:
        server = serve(args.host, args.port, args.mimic_store, args.mimic_d_items,
                       trajectory_store=not args.no_trajectory_store, trajectory_store_root=args.trajectory_store_root,
                       datasets_file=args.datasets)
    except ValueError as error:  # a refused configuration: named, never a traceback
        print(f'not started: {error}', file=sys.stderr, flush=True)
        sys.exit(2)
    auth = server.auth
    realm = (auth.verifier.issuer if auth.verifier else
             'none (loopback: the operator is the owner)' if auth.local_owner else 'none (public datasets only)')
    print(f'mockups and {engine.PROFILE_ID} on http://{args.host}:{server.server_port}/', flush=True)
    print(f'realm: {realm}; datasets: ' + (', '.join(f'{d.id} ({d.policy.access})' for d in server.registry.datasets)
                                           or 'none'), flush=True)

    def report():  # the stores' states, once known, without holding up requests (/api/health answers meanwhile)
        status = server.store.status()
        print(f"store query page: {'MIMIC-IV demo store ' + status['snapshot_id'][:12] if status['available'] else status['error']}",
              flush=True)
        if server.trajectory_store is not None:
            status = server.trajectory_store.status()
            built = f"synthetic store {status['snapshot_id'][:12]} (built in {status['built_s']} s)" if status['available'] else status['error']
            print(f'trajectory page: {built}', flush=True)
    threading.Thread(target=report, name='startup-report', daemon=True).start()
    try:  # a signal while the store builds closes the server too (the build also has an atexit hook)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
