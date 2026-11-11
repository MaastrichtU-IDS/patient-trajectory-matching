"""Serve the mockups and the trajectory-graph engine to them, on one local port.

    python demos/mockups/server.py [--port 8090] [--host 127.0.0.1] [--mimic-store PATH] [--mimic-d-items FILE]

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
snapshot that is not the open demo. With no store configured, status says so and how to build one.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
sys.path.insert(0, str(HERE))
from patterns import trajectory_graph as engine  # noqa: E402
import store_query  # noqa: E402

MAX_BODY = 20 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
    store = store_query.StoreService()  # `serve` gives each server its configured store

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
            return self.send_json(200, self.store.status())
        if self.path == '/api/store/catalogue':
            try:
                return self.send_json(200, self.store.catalogue())
            except store_query.Unavailable as error:
                return self.send_json(503, {'error': str(error)})
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
        except ValueError:
            return None, (400, 'MALFORMED_REQUEST')

    def do_POST(self):
        if self.path == '/api/store/match':
            return self.store_match()
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
        try:
            return self.send_json(200, self.store.match(data))
        except store_query.QueryError as error:
            return self.send_json(400, {'error': str(error)})
        except store_query.Unavailable as error:
            return self.send_json(503, {'error': str(error)})
        except store_query.AggregateRuleViolation:
            # An answer that is not aggregate counts only is never sent, not even in part.
            return self.send_json(500, {'error': 'AGGREGATE_RULE_VIOLATION'})

    def log_message(self, format, *args):
        if self.path.startswith('/api/'):
            super().log_message(format, *args)


def serve(host='127.0.0.1', port=8090, mimic_store=None, mimic_d_items=None):
    """The server; the MIMIC demo store comes from the arguments, else from PTM_MIMIC_STORE and PTM_MIMIC_D_ITEMS."""
    service = store_query.StoreService(mimic_store or os.environ.get('PTM_MIMIC_STORE'),
                                       mimic_d_items or os.environ.get('PTM_MIMIC_D_ITEMS'))
    handler = type('ConfiguredHandler', (Handler,), {'store': service})
    httpd = ThreadingHTTPServer((host, port), partial(handler, directory=str(HERE)))
    httpd.store = service
    return httpd


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8090)
    parser.add_argument('--mimic-store', help='a locally built MIMIC-IV demo snapshot, or the root holding it '
                        '(default: $PTM_MIMIC_STORE); only the open demo is served')
    parser.add_argument('--mimic-d-items', help="the demo's d_items file, for item labels (default: $PTM_MIMIC_D_ITEMS)")
    args = parser.parse_args()
    server = serve(args.host, args.port, args.mimic_store, args.mimic_d_items)
    print(f'mockups and {engine.PROFILE_ID} on http://{args.host}:{server.server_port}/', flush=True)
    status = server.store.status()
    print(f"store query page: {'MIMIC-IV demo store ' + status['snapshot_id'][:12] if status['available'] else status['error']}",
          flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
