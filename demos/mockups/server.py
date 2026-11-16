"""Serve the mockups and the trajectory-graph engine to them, on one local port.

    python demos/mockups/server.py [--port 8090] [--host 127.0.0.1] [--mimic-store PATH] [--mimic-d-items FILE]
                                   [--trajectory-store-root DIR] [--no-trajectory-store]

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
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from patterns import trajectory_graph as engine  # noqa: E402
import store_query  # noqa: E402
import store_trajectory  # noqa: E402

MAX_BODY = 20 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
    store = store_query.StoreService()  # `serve` gives each server its configured store
    trajectory_store = None  # `serve` gives each server its synthetic store (D37), or none

    def send_json(self, status, value):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == '/api/health':
            return self.send_json(200, {'profile': engine.PROFILE_ID})
        if self.path == '/api/store/status':
            return self.store_answer(self.store.status)
        if self.path == '/api/store/catalogue':
            return self.store_answer(self.store.catalogue)
        if self.path == '/api/trajectory-store/status':
            if self.trajectory_store is None:
                return self.send_json(200, {'profile': store_trajectory.PROFILE, 'available': False,
                                            'error': 'STORE_NOT_CONFIGURED'})
            return self.trajectory_answer(self.trajectory_store.status)
        if self.path.startswith('/api/'):
            return self.send_json(404, {'error': 'NOT_FOUND'})
        return super().do_GET()

    def read_json(self):
        """(body, None), or (None, (status, error)) when the request is refused before parsing."""
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return None, (415, 'JSON_REQUIRED')
        try:
            size = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return None, (411, 'LENGTH_REQUIRED')
        if not 0 < size <= MAX_BODY:
            return None, (413, 'BODY_TOO_LARGE')
        try:
            return json.loads(self.rfile.read(size)), None
        except (ValueError, RecursionError):
            return None, (400, 'MALFORMED_REQUEST')

    def do_POST(self):
        if self.path == '/api/store/match':
            return self.store_match()
        if self.path == '/api/trajectory-store/match':
            return self.trajectory_match()
        if self.path != '/api/trajectory-graph/match':
            return self.send_json(404, {'error': 'NOT_FOUND'})
        data, refused = self.read_json()
        if refused:
            return self.send_json(refused[0], {'error': refused[1]})
        try:
            if not isinstance(data, dict) or set(data) != {'query', 'patients'} or not isinstance(data['patients'], list):
                raise engine.TrajectoryError('REQUEST_FIELDS_MUST_BE_QUERY_AND_PATIENTS')
            return self.send_json(200, engine.run(data['query'], data['patients']))
        except (ValueError, TypeError, KeyError) as error:
            # TrajectoryError is a ValueError.
            code = str(error) if isinstance(error, engine.TrajectoryError) else 'MALFORMED_REQUEST'
            return self.send_json(400, {'error': code})

    def store_match(self):
        data, refused = self.read_json()
        if refused:
            return self.send_json(refused[0], {'error': refused[1]})
        return self.store_answer(lambda: self.store.match(data))

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

    def trajectory_match(self):
        data, refused = self.read_json()
        if refused:
            return self.send_json(refused[0], {'error': refused[1]})
        if self.trajectory_store is None:
            return self.send_json(503, {'error': 'STORE_NOT_CONFIGURED'})
        return self.trajectory_answer(lambda: self.trajectory_store.match(data))

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

    def log_message(self, format, *args):
        if self.path.startswith('/api/'):
            super().log_message(format, *args)


class Server(ThreadingHTTPServer):
    trajectory_store = None

    def server_close(self):
        super().server_close()
        if self.trajectory_store is not None:
            self.trajectory_store.close()


def serve(host='127.0.0.1', port=8090, mimic_store=None, mimic_d_items=None, demo_pin=None,
          trajectory_store=True, trajectory_store_root=None, trajectory_cohort=None):
    """The server; the MIMIC demo store comes from the arguments, else from PTM_MIMIC_STORE and PTM_MIMIC_D_ITEMS.
    `demo_pin` replaces the open demo's (patient count, subject digest) for tests on the synthetic fixture only.
    With `trajectory_store` (D37), the synthetic cohort (`trajectory_cohort`, by default the page's) is built into a
    store on a background thread, under `trajectory_store_root` or PTM_TRAJECTORY_STORE_ROOT, else in a temporary
    directory; `server_close()` closes it and removes the build."""
    service = store_query.StoreService(mimic_store or os.environ.get('PTM_MIMIC_STORE'),
                                       mimic_d_items or os.environ.get('PTM_MIMIC_D_ITEMS'),
                                       **({'pin': demo_pin} if demo_pin else {}))
    synthetic = None
    if trajectory_store:
        synthetic = store_trajectory.TrajectoryStore(trajectory_cohort or store_trajectory.COHORT,
                                                     trajectory_store_root or os.environ.get('PTM_TRAJECTORY_STORE_ROOT'))
    handler = type('ConfiguredHandler', (Handler,), {'store': service, 'trajectory_store': synthetic})
    httpd = Server((host, port), partial(handler, directory=str(HERE)))
    httpd.store = service
    httpd.trajectory_store = synthetic.start() if synthetic else None
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
    args = parser.parse_args()

    def stop(signum, frame):  # a terminated server closes too, so the store's build is removed (D37); SIGKILL cannot be caught
        raise KeyboardInterrupt
    for name in ('SIGTERM', 'SIGINT', 'SIGHUP'):
        signal.signal(getattr(signal, name), stop)
    server = serve(args.host, args.port, args.mimic_store, args.mimic_d_items,
                   trajectory_store=not args.no_trajectory_store, trajectory_store_root=args.trajectory_store_root)
    try:  # a signal while the store builds closes the server too (the build also has an atexit hook)
        print(f'mockups and {engine.PROFILE_ID} on http://{args.host}:{server.server_port}/', flush=True)
        status = server.store.status()
        print(f"store query page: {'MIMIC-IV demo store ' + status['snapshot_id'][:12] if status['available'] else status['error']}",
              flush=True)
        if server.trajectory_store is not None:
            status = server.trajectory_store.status()
            built = f"synthetic store {status['snapshot_id'][:12]} (built in {status['built_s']} s)" if status['available'] else status['error']
            print(f'trajectory page: {built}', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
