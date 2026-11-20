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
answers at `/api/datasets/<id>/status`, `.../catalogue` (aggregate kinds) and `.../match`, and is a 404 to a caller
who may not use it; the routes above are aliases of the first dataset of each kind the caller may use.
`GET /api/datasets` lists the datasets the caller may use, and `GET /api/auth-config` tells the page how to sign
in. The caller (`access.py`) is a verified realm bearer token when PTM_OIDC_ISSUER, PTM_OIDC_JWKS_URI and
PTM_OIDC_AUDIENCE are set; otherwise, on a loopback bind and for a request addressed to localhost, the person at
the machine, who is the owner and is served every configured dataset as before; otherwise anonymous, served public
datasets only. On a non-loopback bind the server refuses to start with a non-public dataset and no realm.

**Hosted** (any non-loopback bind): the anonymous engine route is off (the page uses the store), static files are
limited to page asset types with no directory listings, MIMIC stores open at startup rather than at first use, and
each synthetic-store match runs in a child process (`match_worker.py`) killed after PTM_MATCH_TIME_LIMIT seconds
(default 2), with its search budget clamped to 1,000 and lower caps on steps, relations and relaxations
(`GET /api/limits` says so). A realm then needs PTM_OIDC_CLIENT_ID.
Everywhere: `GET /api/ready` answers `{"ready": ...}` (200 once every started dataset is ready, else 503; the owner
also gets the counts), while `/api/health` answers from the moment the socket is bound. Requests are bounded:
PTM_MAX_BODY (default 20 MiB), PTM_MATCH_CONCURRENCY (matches at once, taken before a body is read; default
unlimited locally, 4 hosted), PTM_MAX_THREADS (requests in flight, default 64; beyond it, 4 reserve threads for the
probes, then 503 BUSY at once), a
30 s socket timeout, and caps on patients, events, steps and relations in a match. PTM_LOG_FORMAT=json writes one
JSON line per API request to stdout: never a body, a token, a header or an address. Every response carries a
Content-Security-Policy (with the hashes of the pages' inline scripts) and the usual hardening headers.
"""
import argparse
import base64
from fractions import Fraction
from functools import partial
import hashlib
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import re
import signal
import socketserver
import sys
import threading
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from patterns import trajectory_graph as engine  # noqa: E402
import access  # noqa: E402
import datasets  # noqa: E402
import match_worker  # noqa: E402
import store_query  # noqa: E402
import store_trajectory  # noqa: E402

MAX_BODY = 20 * 1024 * 1024
GATE_WAIT = 3            # seconds a match may wait in all (for a match slot, then for the hosted match worker) before
                         # 503 BUSY: a legitimate match is answered or refused within GATE_WAIT + the time limit
HOSTED_MATCH_CONCURRENCY = 4
MAX_THREADS = 64         # requests in flight; one more gets 503 BUSY without a thread
REQUEST_TIMEOUT = 30     # seconds a connection may sit idle mid-request
MAX_PATIENTS = 500       # in an engine request (the page sends the 48-patient cohort's candidates)
MAX_EVENTS = 50_000      # in an engine request, summed over its patients
MAX_STEPS = 12           # in any match request's query
MAX_RELATIONS = 40       # relations, and separately relaxations, in any match request's query
# Hosted bounds (security re-check of D50, M1), measured on the synthetic store at one worker (docs/hosting-plan.md,
# "The security review"): the page's own requests use at most 5 steps, 4 relations, 4 relaxations, a relaxation
# cost budget of 1 and a search budget of 40,000, and none of their 317 answers changes at a search budget of 500.
HOSTED_MAX_STEPS = 6
HOSTED_MAX_RELATIONS = 8
HOSTED_MAX_RELAXATIONS = 6
HOSTED_COST_BUDGET = Fraction(1)
HOSTED_SEARCH_BUDGET = 1000      # clamped server-side; a search that reaches it is the engine's own `unresolved`
HOSTED_MATCH_TIME_LIMIT = 2.0    # seconds; a store match still running is killed (match_worker.py)
MAX_MATCH_TIME_LIMIT = 30.0      # PTM_MATCH_TIME_LIMIT above this (or not a finite number) is refused at startup
HOSTED_WORKER_MAX_MIB = 400      # the match worker's address-space limit (RLIMIT_AS), PTM_MATCH_WORKER_MAX_MIB
PROBE_RESERVE = 4                # threads beyond PTM_MAX_THREADS for /api/health and /api/ready only
PROBE_ROUTES = frozenset({'/api/health', '/api/ready'})
SECURITY_HEADERS = (('X-Content-Type-Options', 'nosniff'), ('Referrer-Policy', 'no-referrer'),
                    ('X-Frame-Options', 'DENY'))
STATIC_TYPES = frozenset({'.html', '.js', '.css', '.json', '.svg', '.png', '.ico', '.txt', '.woff2'})
DATASET_ROUTE = re.compile(r'/api/datasets/([^/]+)/(status|catalogue|match)')
INLINE_SCRIPT = re.compile(r'<script>(.*?)</script>', re.S)
LOG_LOCK = threading.Lock()
_THREAD = threading.local()  # .probe_only: a request on a reserve thread, which serves the probes and nothing else


def inline_script_hashes(root=HERE):
    """CSP hashes of every inline <script> in the pages served from `root` (computed at startup, so a page edit
    needs no second list to keep in step)."""
    hashes = set()
    for page in root.glob('**/*.html'):
        for script in INLINE_SCRIPT.findall(page.read_text(encoding='utf-8')):
            hashes.add("'sha256-" + base64.b64encode(hashlib.sha256(script.encode('utf-8')).digest()).decode() + "'")
    return sorted(hashes)


def content_security_policy(issuer=None, root=HERE):
    connect = "'self'"
    if issuer:
        parts = urlsplit(issuer)
        connect += f' {parts.scheme}://{parts.netloc}'  # the realm's token, refresh and revocation endpoints
    scripts = ' '.join(["'self'"] + inline_script_hashes(root))
    # Styles stay 'unsafe-inline': the pages use <style> blocks and style attributes in rendered markup; scripts,
    # the dangerous half, are limited to files from this origin and the hashed inline scripts.
    return (f"default-src 'self'; script-src {scripts}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            f"connect-src {connect}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'")


class Handler(SimpleHTTPRequestHandler):
    registry = datasets.Registry([])     # `serve` gives each server its own
    auth = access.Auth()
    unconfigured_store = store_query.StoreService()  # answers the MIMIC routes when no MIMIC dataset is configured
    max_body = MAX_BODY
    match_gate = None
    log_json = False
    hosted = False
    csp = content_security_policy()
    timeout = REQUEST_TIMEOUT
    server_version = 'ptm'
    match_time_limit = 0

    def version_string(self):
        return self.server_version  # no Python or library version in the Server header

    # ------------------------------------------------------------ responses and logs

    def end_headers(self):
        for name, value in SECURITY_HEADERS:
            self.send_header(name, value)
        self.send_header('Content-Security-Policy', self.csp)
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
        is known and says `refused` when it is not (an invalid token)."""
        if getattr(self, 'log_caller', None) is None:
            self.log_caller = 'refused'
        headers = ()
        if error.status == 401:
            challenge = 'Bearer realm="ptm"' + (', error="invalid_token"' if error.code == 'TOKEN_INVALID' else '')
            headers = (('WWW-Authenticate', challenge),)
        return self.send_json(error.status, {'error': error.code}, headers)

    def not_found(self, code='NOT_FOUND'):
        return self.send_json(404, {'error': code})

    def begin(self, route):
        self.started = time.perf_counter()
        self.route, self.log_dataset, self.log_caller, self.log_subject = route, None, None, None

    def is_api(self):
        return getattr(self, 'path', '').startswith('/api/')

    def log_request(self, code='-', size='-'):
        if not self.is_api():
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
        if self.is_api() and not self.log_json:
            super().log_message(format, *args)

    # ------------------------------------------------------------ who is calling

    def caller(self):
        """The request's Principal, or AuthError."""
        principal = self.auth.principal(self.headers)
        self.log_caller, self.log_subject = principal.kind, principal.subject
        return principal

    # ------------------------------------------------------------ static files

    def list_directory(self, path):
        if self.hosted:  # no directory listings on hosting
            self.send_error(404)
            return None
        return super().list_directory(path)

    def static_allowed(self):
        """Hosted: page asset types only (no .py sources, READMEs or anything else in the directory)."""
        if not self.hosted:
            return True
        target = Path(self.translate_path(self.path))
        if target.is_dir():
            target = target / 'index.html'
        return target.suffix.lower() in STATIC_TYPES

    def refused_early(self):
        """A reserve thread serves only the probes (503 BUSY otherwise); a path holding a NUL byte is a 404."""
        path = getattr(self, 'path', '')
        if getattr(_THREAD, 'probe_only', False) and path not in PROBE_ROUTES:
            self.send_json(503, {'error': 'BUSY'}, (('Retry-After', '2'),))
            return True
        if '\x00' in path or '%00' in path.lower():
            self.send_error(404)
            return True
        return False

    def do_HEAD(self):
        if self.refused_early():
            return
        if self.is_api() or not self.static_allowed():
            self.send_error(404)
            return
        super().do_HEAD()

    # ------------------------------------------------------------ GET

    def do_GET(self):
        if self.refused_early():
            return
        path = self.path
        if not path.startswith('/api/'):
            try:
                allowed = self.static_allowed()
            except ValueError:  # a path the filesystem cannot hold
                allowed = False
            if not allowed:
                return self.send_error(404)
            return super().do_GET()
        found = DATASET_ROUTE.fullmatch(path)
        self.begin('/api/datasets/{id}/' + found.group(2) if found else path if path in GET_ROUTES else 'unknown')
        if path == '/api/health':
            return self.send_json(200, {'profile': engine.PROFILE_ID})
        if path == '/api/ready':
            return self.ready()
        if path == '/api/auth-config':
            return self.send_json(200, self.auth.public_config())
        if path == '/api/limits':
            return self.send_json(200, self.limits())
        if path == '/api/datasets':
            return self.dataset_list()
        if path in ('/api/store/status', '/api/store/catalogue'):
            return self.alias('mimic-demo-aggregate', path.rsplit('/', 1)[1])
        if path == '/api/trajectory-store/status':
            return self.alias('synthetic-trajectory', 'status')
        if found and found.group(2) != 'match':
            return self.dataset_route(found)
        return self.not_found()

    def limits(self):
        """What bounds a search here, for the page to say so: the hosted search budget and time limit."""
        if not self.hosted:
            return {'hosted': False}
        return {'hosted': True, 'search_budget': HOSTED_SEARCH_BUDGET, 'match_time_limit_s': self.match_time_limit or None,
                'max_steps': HOSTED_MAX_STEPS, 'max_relaxations': HOSTED_MAX_RELAXATIONS}

    def ready(self):
        """{"ready": bool}: 200 once every started dataset is ready. Never opens a store (hosted, MIMIC stores open at
        startup; locally one not yet used is idle and does not hold readiness back). The owner also gets the counts."""
        states = [d.state() for d in self.registry.datasets]
        ready = all(state in ('ready', 'idle') for state in states)
        value = {'ready': ready}
        try:
            if self.caller().kind == 'owner':
                value['datasets'] = {state: states.count(state) for state in ('ready', 'pending', 'failed', 'idle')}
        except access.AuthError:
            pass
        return self.send_json(200 if ready else 503, value)

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

    def alias(self, kind, operation):
        """An old route: the first dataset of `kind` the caller may use, else the answer of no such store, so the
        route says nothing about datasets the caller cannot see."""
        try:
            principal = self.caller()
        except access.AuthError as error:
            return self.refuse(error)
        dataset = next((d for d in self.registry.visible(principal) if d.kind == kind), None)
        if dataset is not None:
            return self.dataset_operation(dataset, operation)
        if kind == 'synthetic-trajectory':
            if operation == 'match':
                return self.send_json(503, {'error': 'STORE_NOT_CONFIGURED'})
            return self.send_json(200, {'profile': store_trajectory.PROFILE, 'available': False,
                                        'error': 'STORE_NOT_CONFIGURED'})
        service = self.unconfigured_store
        if operation == 'match':
            return self.store_answer(lambda: service.match({}))
        return self.store_answer(service.status if operation == 'status' else service.catalogue)

    def dataset_route(self, found):
        """/api/datasets/<id>/<operation>: a 404 for an unknown dataset and for one the caller may not use alike."""
        try:
            principal = self.caller()
        except access.AuthError as error:
            return self.refuse(error)
        dataset = self.registry.get(found.group(1))
        if dataset is None or not dataset.policy.permits(principal):
            return self.not_found('DATASET_NOT_FOUND')
        if found.group(2) == 'catalogue' and dataset.kind != 'mimic-demo-aggregate':
            return self.not_found()
        return self.dataset_operation(dataset, found.group(2))

    def dataset_operation(self, dataset, operation):
        """A permitted dataset's status, catalogue or match."""
        self.log_dataset = dataset.id
        service = dataset.service
        if operation == 'match':
            hosted = self.hosted
            if dataset.kind == 'synthetic-trajectory':
                matcher = dataset.matcher
                if matcher is not None:  # hosted: a child process with a time limit, waited for until the deadline
                    return self.gated_body(lambda data: self.trajectory_answer(
                        lambda: matcher.match(hosted_request(check_size(data, hosted)), wait=self.queue_left())))
                return self.gated_body(lambda data: self.trajectory_answer(
                    lambda: service.match(hosted_request(check_size(data, hosted)) if hosted else check_size(data))))
            return self.gated_body(lambda data: self.store_answer(lambda: service.match(check_size(data, hosted))))
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

    def gated_body(self, run):
        """`run(body)` with a match slot held from before the body is read until the answer is sent, so at most
        PTM_MATCH_CONCURRENCY bodies are read, parsed and matched at once."""
        def read_then_run():
            data, refused = self.read_json()
            if refused:
                return self.send_json(refused[0], {'error': refused[1]})
            return run(data)
        return self.gated(read_then_run)

    def do_POST(self):
        if self.refused_early():
            return
        path = self.path
        found = DATASET_ROUTE.fullmatch(path)
        self.begin('/api/datasets/{id}/match' if found else path if path in POST_ROUTES else 'unknown')
        if path == '/api/store/match':
            return self.alias('mimic-demo-aggregate', 'match')
        if path == '/api/trajectory-store/match':
            return self.alias('synthetic-trajectory', 'match')
        if found and found.group(2) == 'match':
            return self.dataset_route(found)
        if path != '/api/trajectory-graph/match' or self.hosted:
            # Hosted, the engine on caller-sent patients is off: it costs CPU per byte for anyone, and the page
            # matches in the store (it falls back to the browser if the engine is chosen).
            return self.not_found()
        return self.gated_body(self.engine_match)

    def engine_match(self, data):
        """The engine on the patients the page sent: no stored data, so no dataset and no policy."""
        try:
            if not isinstance(data, dict) or set(data) != {'query', 'patients'} or not isinstance(data['patients'], list):
                raise engine.TrajectoryError('REQUEST_FIELDS_MUST_BE_QUERY_AND_PATIENTS')
            check_size(data)
            patients = data['patients']
            if len(patients) > MAX_PATIENTS:
                raise engine.TrajectoryError('TOO_MANY_PATIENTS')
            events = sum(len(p['events']) for p in patients if isinstance(p, dict) and isinstance(p.get('events'), list))
            if events > MAX_EVENTS:
                raise engine.TrajectoryError('TOO_MANY_EVENTS')
            return self.send_json(200, engine.run(data['query'], patients))
        except (ValueError, TypeError, KeyError) as error:
            # TrajectoryError is a ValueError.
            code = str(error) if isinstance(error, (engine.TrajectoryError, SizeError)) else 'MALFORMED_REQUEST'
            return self.send_json(400, {'error': code})

    def queue_left(self):
        """Seconds left of this match's GATE_WAIT deadline (at least a moment, so a free worker is still taken)."""
        return max(0.05, getattr(self, 'queue_deadline', time.monotonic() + GATE_WAIT) - time.monotonic())

    def gated(self, run):
        """`run` under PTM_MATCH_CONCURRENCY: a match waits up to GATE_WAIT seconds for a slot, then gets 503 BUSY.
        The same deadline then bounds its wait for the hosted match worker (`queue_left`)."""
        self.queue_deadline = time.monotonic() + GATE_WAIT
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
        except SizeError as error:
            return self.send_json(400, {'error': str(error)})
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
        except SizeError as error:
            return self.send_json(400, {'error': str(error), 'by': 'server'})
        except store_trajectory.RequestError as error:  # `by`: the server's own check, or the store's refusal
            return self.send_json(400, {'error': str(error), 'by': error.by})
        except store_trajectory.Unavailable as error:
            return self.send_json(503, {'error': str(error)})
        except Exception:  # noqa: BLE001 - an internal failure is named, with no detail
            return self.send_json(500, {'error': 'INTERNAL_ERROR'})


class SizeError(ValueError):
    """A match request above the server's caps on steps, relations or relaxations (400, before any work)."""


def check_size(data, hosted=False):
    """`data`, after checking that a match request's query is within the step, relation and relaxation caps (lower
    when hosted). The stores and the engine check shape; this bounds the work any one request can ask for."""
    steps, relations, relaxations = ((HOSTED_MAX_STEPS, HOSTED_MAX_RELATIONS, HOSTED_MAX_RELAXATIONS) if hosted
                                     else (MAX_STEPS, MAX_RELATIONS, MAX_RELATIONS))
    query = data.get('query') if isinstance(data, dict) else None
    if isinstance(query, dict):
        if isinstance(query.get('steps'), list) and len(query['steps']) > steps:
            raise SizeError('TOO_MANY_STEPS')
        for key, cap in (('relations', relations), ('relaxations', relaxations)):
            if isinstance(query.get(key), list) and len(query[key]) > cap:
                raise SizeError('TOO_MANY_' + key.upper())
    return data


def hosted_request(data):
    """A hosted synthetic-store request: the relaxation cost budget at most 1 (the page's own maximum; 400 above it)
    and the search budget clamped to HOSTED_SEARCH_BUDGET, so a search the clamp stops is the engine's own
    `unresolved` (SEARCH_BUDGET_EXHAUSTED) rather than minutes of work. The caller's dict is not changed."""
    query = data.get('query') if isinstance(data, dict) else None
    if not isinstance(query, dict):
        return data  # the store refuses it by name
    cost = query.get('budget', '0')
    try:
        too_costly = Fraction(cost) > HOSTED_COST_BUDGET
    except (TypeError, ValueError, ZeroDivisionError):
        too_costly = False  # malformed: the engine refuses it by name
    if too_costly:
        raise SizeError('RELAXATION_BUDGET_ABOVE_HOSTED_MAX')
    budget = query.get('search_budget', HOSTED_SEARCH_BUDGET)
    if type(budget) is int and budget > HOSTED_SEARCH_BUDGET or 'search_budget' not in query:
        budget = HOSTED_SEARCH_BUDGET
    return {**data, 'query': {**query, 'search_budget': budget}}


GET_ROUTES = frozenset({'/api/health', '/api/ready', '/api/auth-config', '/api/limits', '/api/datasets', '/api/store/status',
                        '/api/store/catalogue', '/api/trajectory-store/status'})
POST_ROUTES = frozenset({'/api/trajectory-graph/match', '/api/store/match', '/api/trajectory-store/match'})
BUSY = b'{"error": "BUSY"}'


class Server(ThreadingHTTPServer):
    registry = datasets.Registry([])
    store = None
    trajectory_store = None
    max_threads = MAX_THREADS

    def server_bind(self):
        # http.server's own server_bind also calls socket.getfqdn() for a name nothing here uses; on a machine
        # whose reverse DNS is slow that held the socket closed for 35 s at startup.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = host, port

    def process_request(self, request, client_address):
        """A thread per request, at most `max_threads` at once. Beyond that, PROBE_RESERVE more threads serve only
        /api/health and /api/ready (so a flood cannot fail the pod's probes); beyond those, 503 BUSY without a thread
        or a read."""
        slots = self.__dict__.setdefault('_slots', threading.BoundedSemaphore(self.max_threads))
        reserve = self.__dict__.setdefault('_reserve', threading.BoundedSemaphore(PROBE_RESERVE))
        if not slots.acquire(blocking=False):
            if reserve.acquire(blocking=False):
                threading.Thread(target=self._probe_thread, args=(request, client_address), daemon=True).start()
                return
            try:
                request.sendall(b'HTTP/1.0 503 Service Unavailable\r\nContent-Type: application/json\r\n'
                                b'Retry-After: 2\r\nConnection: close\r\nContent-Length: '
                                + str(len(BUSY)).encode() + b'\r\n\r\n' + BUSY)
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def _probe_thread(self, request, client_address):
        _THREAD.probe_only = True
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self._reserve.release()

    def handle_error(self, request, client_address):
        """An unexpected failure in a request: its exception class only, never the client's address or a traceback."""
        with LOG_LOCK:
            sys.stderr.write(json.dumps({'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'event': 'server_error',
                                         'error': type(sys.exc_info()[1]).__name__}) + '\n')
            sys.stderr.flush()

    def server_close(self):
        super().server_close()
        for dataset in self.registry.datasets:
            if dataset.kind == 'synthetic-trajectory':
                dataset.service.close()
            if dataset.matcher is not None:
                dataset.matcher.close()


def log_event(event, **fields):
    """A server event (not a request) as one JSON line on stdout: its name and named fields, never a body or address."""
    with LOG_LOCK:
        sys.stdout.write(json.dumps({'ts': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'event': event, **fields}) + '\n')
        sys.stdout.flush()


def match_time_limit_from(value, hosted):
    """The match time limit in seconds: `value`, else PTM_MATCH_TIME_LIMIT, else 2 hosted and 0 (none) locally.
    Hosted it must be a finite number with 0 < x <= 30: `inf` would hang matching for good and 0 would silently
    remove the bound, so either refuses startup (ValueError). Locally 0 means in-process matching, as before."""
    if value is None:
        raw = os.environ.get('PTM_MATCH_TIME_LIMIT')
        if raw in (None, ''):
            return HOSTED_MATCH_TIME_LIMIT if hosted else 0
        try:
            value = float(raw)
        except ValueError:
            raise ValueError('PTM_MATCH_TIME_LIMIT_MUST_BE_A_NUMBER_OF_SECONDS') from None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) \
            or value < 0 or value > MAX_MATCH_TIME_LIMIT or (hosted and value == 0):
        raise ValueError('PTM_MATCH_TIME_LIMIT_MUST_BE_ABOVE_0_AND_AT_MOST_30_S')
    return value


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
          verifier=None, max_body=None, match_concurrency=None, log_format=None, max_threads=None, hosted=False,
          match_time_limit=None):
    """The server. Datasets come from `datasets_file` or PTM_DATASETS, else from the flags: the synthetic cohort
    (`trajectory_cohort`, by default the page's) unless `trajectory_store` is false, built on a background thread
    under `trajectory_store_root` or PTM_TRAJECTORY_STORE_ROOT (else a temporary directory; `server_close()` removes
    it), and the MIMIC demo store from `mimic_store` or PTM_MIMIC_STORE (labels from `mimic_d_items` or
    PTM_MIMIC_D_ITEMS). `demo_pin` replaces the open demo's (patient count, subject digest) for tests on the
    synthetic fixture only. `verifier` (an access.OidcVerifier) or the PTM_OIDC_* variables configure the realm.
    Refuses (ValueError) a non-public dataset on a non-loopback bind without a realm. Any non-loopback bind is hosted;
    `hosted=True` applies the hosted rules on loopback too (tests, or a local reverse proxy). It can only add them."""
    mimic_store = mimic_store or os.environ.get('PTM_MIMIC_STORE') or None
    mimic_d_items = mimic_d_items or os.environ.get('PTM_MIMIC_D_ITEMS') or None
    root = trajectory_store_root or os.environ.get('PTM_TRAJECTORY_STORE_ROOT')
    datasets_file = datasets_file or os.environ.get('PTM_DATASETS') or None
    entries = datasets.load(datasets_file) if datasets_file else datasets.from_flags(trajectory_store, mimic_store)
    verifier = verifier if verifier is not None else access.verifier_from_env(os.environ)
    hosted = bool(hosted) or not access.is_loopback(host)
    if hosted and verifier is None and any(e.policy.access != 'public' for e in entries):
        raise ValueError('NON_PUBLIC_DATASET_NEEDS_OIDC_ON_A_NON_LOOPBACK_BIND')
    if hosted and verifier is not None and not verifier.client_id:
        raise ValueError('HOSTED_REALM_NEEDS_PTM_OIDC_CLIENT_ID')  # so that `azp` is always checked when hosted
    match_time_limit = match_time_limit_from(match_time_limit, hosted)
    worker_mib = _int_env('PTM_MATCH_WORKER_MAX_MIB', HOSTED_WORKER_MAX_MIB if hosted else 0)
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
    default_gate = HOSTED_MATCH_CONCURRENCY if hosted else 0
    gate_size = match_concurrency if match_concurrency is not None else _int_env('PTM_MATCH_CONCURRENCY', default_gate)
    log_format = log_format or os.environ.get('PTM_LOG_FORMAT') or 'text'
    handler = type('ConfiguredHandler', (Handler,), {
        'registry': registry,
        'auth': access.Auth(verifier, local_owner=not hosted),
        'unconfigured_store': store_query.StoreService(),
        'max_body': max_body if max_body is not None else _int_env('PTM_MAX_BODY', MAX_BODY),
        'match_gate': threading.BoundedSemaphore(gate_size) if gate_size else None,
        'log_json': log_format == 'json',
        'hosted': hosted,
        'match_time_limit': match_time_limit,
        'csp': content_security_policy(verifier.issuer if verifier else None)})
    httpd = Server((host, port), partial(handler, directory=str(HERE)))
    httpd.max_threads = max_threads if max_threads is not None else _int_env('PTM_MAX_THREADS', MAX_THREADS)
    httpd.registry, httpd.auth = registry, handler.auth
    first_mimic = registry.first('mimic-demo-aggregate')
    first_synthetic = registry.first('synthetic-trajectory')
    httpd.store = first_mimic.service if first_mimic else handler.unconfigured_store
    for dataset in registry.datasets:
        if dataset.kind == 'synthetic-trajectory':
            dataset.service.start()      # built now, on its own thread
            if match_time_limit:         # hosted: matches in a child process with a hard time limit
                dataset.matcher = match_worker.IsolatedMatcher(
                    dataset.service.cohort_path, root, match_time_limit, memory_mib=worker_mib, on_event=log_event).start()
        elif hosted:
            dataset.open_in_background()  # hosted: opened now, so readiness covers it; locally at first use
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
