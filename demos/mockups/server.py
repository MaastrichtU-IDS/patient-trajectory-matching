"""Serve the mockups and the trajectory-graph engine to them, on one local port.

    python demos/mockups/server.py [--port 8090] [--host 127.0.0.1]

Static files come from this directory, as with `python -m http.server`. Two endpoints are
added: `GET /api/health` names the engine profile, and `POST /api/trajectory-graph/match`
takes `{"query": ..., "patients": [...]}` in the trajectory-graph-1.0 form and returns the
engine's results (docs/trajectory-graph-profile.md). A request outside the profile gets
400 with the engine's error code. The trajectory page falls back to matching in the
browser when the endpoint is absent, as it is on static hosting.
"""
import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from patterns import trajectory_graph as engine  # noqa: E402

MAX_BODY = 20 * 1024 * 1024


class Handler(SimpleHTTPRequestHandler):
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
        return super().do_GET()

    def do_POST(self):
        if self.path != '/api/trajectory-graph/match':
            return self.send_json(404, {'error': 'NOT_FOUND'})
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            return self.send_json(415, {'error': 'JSON_REQUIRED'})
        try:
            size = int(self.headers.get('Content-Length', ''))
        except ValueError:
            return self.send_json(411, {'error': 'LENGTH_REQUIRED'})
        if not 0 < size <= MAX_BODY:
            return self.send_json(413, {'error': 'BODY_TOO_LARGE'})
        try:
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict) or set(data) != {'query', 'patients'} or not isinstance(data['patients'], list):
                raise engine.TrajectoryError('REQUEST_FIELDS_MUST_BE_QUERY_AND_PATIENTS')
            return self.send_json(200, engine.run(data['query'], data['patients']))
        except (ValueError, TypeError, KeyError) as error:
            # TrajectoryError is a ValueError; a malformed body is one too.
            code = str(error) if isinstance(error, engine.TrajectoryError) else 'MALFORMED_REQUEST'
            return self.send_json(400, {'error': code})

    def log_message(self, format, *args):
        if self.path.startswith('/api/'):
            super().log_message(format, *args)


def serve(host='127.0.0.1', port=8090):
    return ThreadingHTTPServer((host, port), partial(Handler, directory=str(HERE)))


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8090)
    args = parser.parse_args()
    server = serve(args.host, args.port)
    print(f'mockups and {engine.PROFILE_ID} on http://{args.host}:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
