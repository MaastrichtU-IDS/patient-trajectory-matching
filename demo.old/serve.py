"""Local demonstration server. Core matching needs Python 3.10+, no packages."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse, copy, json, subprocess, sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'source'))
from reference_oracle import evaluate

DATA = json.loads((ROOT / 'demo-data.json').read_text())

class Handler(BaseHTTPRequestHandler):
    def send(self, value, status=200, mime='application/json'):
        body = value if isinstance(value, bytes) else json.dumps(value).encode()
        self.send_response(status)
        self.send_header('Content-Type', mime + '; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ('/', '/index.html'):
            return self.send((ROOT / 'Patient_Trajectory_Demo.html').read_bytes(), mime='text/html')
        if url.path == '/api/match':
            args = parse_qs(url.query)
            budget = args.get('budget', ['0'])[0]
            count = args.get('count', ['2'])[0]
            complete = args.get('complete', ['1'])[0]
            if budget not in ('0', '0.5', '1', '1.5', '2') or count not in ('0', '1', '2') or complete not in ('0', '1'):
                return self.send({'error': 'Unsupported demo controls'}, 400)
            results = []
            for case in DATA['cases']:
                c = copy.deepcopy(case)
                c['budget_override'] = {'max_total_cost': budget, 'max_relaxed_constraints': int(count)}
                c['source_search_complete'] = complete == '1'
                results.append(evaluate(c, DATA['pattern'], DATA['taxonomy']))
            return self.send({'engine': 'repository Python oracle', 'results': results})
        self.send({'error': 'Not found'}, 404)

    def do_POST(self):
        if self.path not in ('/api/semantic', '/api/graph'):
            return self.send({'error': 'Not found'}, 404)
        # No input payload or user-provided paths are accepted by these fixed demo routes.
        try:
            if self.path == '/api/semantic':
                module, output = 'patterns.semantic_support', ROOT / 'evidence/semantic-live'
            else:
                module, output = 'patterns.pro_solid', ROOT / 'evidence/pro-solid-live'
            result = subprocess.run([sys.executable, '-m', module, '--output', str(output)],
                                    cwd=ROOT / 'source', capture_output=True, text=True, timeout=45)
            if result.returncode:
                return self.send({'error': 'The optional pipeline could not run. Install source/patterns/requirements-semantic.lock.txt in the Python environment running this server.', 'detail': result.stderr[-1500:]}, 503)
            name = 'result.json' if self.path == '/api/semantic' else 'match.json'
            return self.send({'engine': 'live repository pipeline', 'result': json.loads((output / name).read_text())})
        except subprocess.TimeoutExpired:
            return self.send({'error': 'Pipeline exceeded the 45-second demonstration limit.'}, 503)

    def log_message(self, fmt, *args):
        print(fmt % args)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(f'Patient Trajectory Matching demo: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
