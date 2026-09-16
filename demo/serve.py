"""Local demonstration server. Core matching needs Python 3.10+, no packages."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import argparse, copy, json, platform, re, shlex, subprocess, sys, tempfile
from importlib.metadata import PackageNotFoundError, version

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT.parent
sys.path.insert(0, str(SOURCE))
from reference_oracle import evaluate
from cohort import export_cohort, run_cohort
from pressure import PressureService
PRESSURE = PressureService()

DATA = json.loads((ROOT / 'demo-data.json').read_text())
COHORT = json.loads((ROOT / 'cohort-data.json').read_text())

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

    def pressure_origin(self):
        host=self.headers.get('Host','')
        allowed={f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'}
        if host not in allowed or self.headers.get('Origin', 'http://'+host) != 'http://'+host:
            self.send({'error':'Pressure data are available only to this local server origin.'},403)
            return False
        return True

    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/pressure' or url.path.startswith('/api/pressure'):
            if not self.pressure_origin(): return
            if url.query: return self.send({'error':'Unexpected URL parameters'},400)
            if url.path == '/pressure':
                page=(ROOT/'pressure.html').read_text().replace('/*PRESSURE_CSS*/',(ROOT/'pressure.css').read_text()).replace('/*PRESSURE_JS*/',(ROOT/'pressure.js').read_text())
                return self.send(page.encode(),mime='text/html')
            if url.path == '/api/pressure/config': return self.send(PRESSURE.metadata())
            match=re.fullmatch(r'/api/pressure/jobs/([0-9a-f]{32})(?:/anchors/([0-9a-f]{24}))?',url.path)
            if match:
                try:
                    return self.send(PRESSURE.inspect(*match.groups()) if match[2] else PRESSURE.get(match[1]))
                except KeyError: return self.send({'error':'Unknown or expired query/evidence'},404)
                except ValueError as error: return self.send({'error':str(error)},409)
            return self.send({'error':'Not found'},404)
        if url.path in ('/', '/index.html'):
            return self.send((ROOT / 'Guided_Cohort_Demo.html').read_bytes(), mime='text/html')
        if url.path in ('/lab', '/Patient_Trajectory_Demo.html'):
            return self.send((ROOT / 'Patient_Trajectory_Demo.html').read_bytes(), mime='text/html')
        if url.path in ('/api/cohort', '/api/cohort/export'):
            args = parse_qs(url.query, keep_blank_values=True)
            if set(args) - {'budget'} or len(args.get('budget', ['0'])) != 1:
                return self.send({'error': 'Use one cohort budget: 0, 1 or 2.'}, 400)
            try:
                result = run_cohort(COHORT, args.get('budget', ['0'])[0])
            except ValueError as error:
                return self.send({'error': str(error)}, 400)
            return self.send(export_cohort(COHORT, result) if url.path.endswith('/export') else result)
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
        if self.path == '/api/pressure/jobs':
            if not self.pressure_origin(): return
            if self.headers.get('Content-Type','').split(';')[0] != 'application/json' or self.headers.get('Transfer-Encoding'):
                return self.send({'error':'Send a bounded JSON request'},400)
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 0<size<=4096: raise ValueError('Request body must be 1–4096 bytes')
                self.connection.settimeout(10)
                request=json.loads(self.rfile.read(size))
                return self.send(PRESSURE.start(request),202)
            except (ValueError,TypeError,UnicodeError) as error: return self.send({'error':str(error)},400)
            except RuntimeError as error: return self.send({'error':str(error)},409)
            except ImportError: return self.send({'error':'Install the pinned graph/Rust dependencies and restart.','environment':environment()},503)
            except (OSError,TimeoutError): return self.send({'error':'Request could not be read'},400)
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
    sources=parser.add_mutually_exclusive_group()
    sources.add_argument('--mimic-dir',type=Path,help='Original pinned public MIMIC-IV demo 2.2 ICU files, served locally')
    sources.add_argument('--pressure-synthetic',action='store_true',help='Explicitly use authored pressure fixtures')
    args = parser.parse_args()
    PRESSURE = PressureService(args.mimic_dir,args.pressure_synthetic)
    print(f'Patient Trajectory Matching demo: http://127.0.0.1:{args.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()
