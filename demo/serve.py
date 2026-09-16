"""Local demonstration server. Core matching needs Python 3.10+, no packages."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse, copy, json, platform, shlex, subprocess, sys, tempfile
from importlib.metadata import PackageNotFoundError, version

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent
sys.path.insert(0, str(SOURCE))
from reference_oracle import evaluate

DATA = json.loads((ROOT / 'demo-data.json').read_text())

def environment():
    packages = {}
    for name in ('rdflib', 'pyshacl', 'jsonschema', 'rustdl'):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = 'NOT INSTALLED'
    args = [sys.executable, '-m', 'pip', 'install', '-r',
            str(SOURCE / 'patterns/requirements-semantic.lock.txt')]
    command = subprocess.list2cmdline(args) if sys.platform == 'win32' else shlex.join(args)
    return {'python_executable': sys.executable, 'python_version': platform.python_version(),
            'platform': platform.platform(), 'architecture': platform.machine(),
            'packages': packages, 'install_command': command,
            'restart': 'Stop this server with Ctrl+C, then run it again with the same Python executable.'}


def run_pipeline(kind):
    """Keep each run isolated and preserve the actual pipeline failure."""
    module = 'patterns.semantic_support' if kind == 'semantic' else 'patterns.pro_solid'
    try:
        with tempfile.TemporaryDirectory(prefix='trajectory-demo-') as directory:
            output = Path(directory)
            completed = subprocess.run([sys.executable, '-m', module, '--output', str(output)],
                                       cwd=SOURCE, capture_output=True, text=True, timeout=45)
            report_path = output / ('result.json' if kind == 'semantic' else 'match.json')
            report = json.loads(report_path.read_text()) if report_path.exists() else None
            if completed.returncode or report is None or (kind == 'semantic' and report.get('status') != 'READY'):
                backend = (report or {}).get('semantic_support', {}).get('backend_result')
                status = (report or {}).get('status')
                detail = '\n'.join(part for part in [completed.stderr.strip(), completed.stdout.strip(),
                                      json.dumps(backend, indent=2) if backend else ''] if part)
                message = f'{"Rust reasoning" if kind == "semantic" else "Graph"} pipeline failed'
                message += f' ({status}).' if status else f' (exit code {completed.returncode}).'
                return {'error': message, 'detail': detail[-8000:] or 'No result report was produced.',
                        'environment': environment()}, 503
            return {'engine': 'live repository pipeline', 'result': report}, 200
    except subprocess.TimeoutExpired:
        return {'error': 'Pipeline exceeded the 45-second demonstration limit.',
                'environment': environment()}, 503
    except (OSError, ValueError) as error:
        return {'error': 'The pipeline could not produce a readable result.',
                'detail': f'{type(error).__name__}: {error}', 'environment': environment()}, 503

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
        if url.path == '/api/environment':
            return self.send(environment())
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
        result, status = run_pipeline(self.path.rsplit('/', 1)[-1])
        self.send(result, status)

    def log_message(self, fmt, *args):
        print(fmt % args)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    print(f'Patient Trajectory Matching demo: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
