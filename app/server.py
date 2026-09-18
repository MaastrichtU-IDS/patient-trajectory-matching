"""Serve the patient-to-cohort workspace and startup-configured recorded evidence."""
import argparse
import copy
import hashlib
import json
import re
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs

from demo import cohort
from patterns.patient_similarity import SimilarityEngine
from app.temporal import TemporalWorkspace
from app.interval_editor import IntervalEditor
from app.journey import JourneyWorkspace
from app.pattern_builder import compile_pattern, decompile_query
from app.relaxation_catalogue import compile_catalogue
from app.recorded_journey import RecordedJourneyWorkspace
from app.recorded_export import export_recorded
from app.access_control import OwnerAccess

ROOT = Path(__file__).resolve().parent
MAX_BODY = 8192
MAX_COMPARISONS = 64
ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')


def exact_keys(value, required):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError('Request fields must be: ' + ', '.join(required))


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError('Invalid identifier')
    return value


class Workspace:
    def __init__(self, recorded_config=None, state_dir=None, access=None):
        self.engine = SimilarityEngine()
        source_bytes = (ROOT.parent / 'demo/cohort-data.json').read_bytes()
        self.dataset = json.loads(source_bytes)
        self.source_hash = hashlib.sha256(source_bytes).hexdigest()
        if self.engine.metadata().get('source_dataset_sha256') != self.source_hash:
            raise ValueError('Similarity and trajectory source fingerprints differ')
        self.comparisons = OrderedDict()
        self.temporal = TemporalWorkspace()
        self.interval_editor = IntervalEditor()
        self.journey = JourneyWorkspace()
        self.recorded = RecordedJourneyWorkspace(config=recorded_config, state_dir=state_dir)
        self.access = access
        self.lock = threading.RLock()
        self.ready = True

    def capabilities(self):
        return {'status': 'ready' if self.ready else 'not-ready',
                'scope': ('research-prototype-with-configured-recorded-sources' if self.recorded.config is not None
                          else 'authored-synthetic-research-prototype'),
                'supported': ['pre-index-similarity', 'bounded-refinements',
                              'exact-and-declared-relaxed-trajectories', 'source-evidence', 'replay-export',
                              'bounded-temporal-demonstration', 'bounded-interval-query-editor',
                              'guided-patient-temporal-journey', 'configurable-three-event-patterns',
                              'custom-relaxation-catalogues', 'guided-recorded-treatment-evidence',
                              'reviewed-ontology-measurement-selection', 'recorded-query-by-example',
                              'recorded-query-export-replay', 'recorded-pattern-revisions',
                              'explicit-recorded-feature-profiles'] +
                             (['single-owner-authentication'] if self.access else []) +
                             (['durable-recorded-jobs'] if self.recorded.store else []),
                'unsupported': ['clinical-validation', 'clinical-mapping-approval', 'uploads',
                                'multi-user-isolation', 'restricted-patient-data',
                                'all-pairs-search', 'production-deployment'] +
                               (['authentication'] if self.access is None else []),
                'max_request_bytes': MAX_BODY, 'max_saved_comparisons': MAX_COMPARISONS,
                'metadata': self.engine.metadata()}

    def compare(self, revision_id, budget):
        if isinstance(budget, bool) or budget not in ('0', '1', '2'):
            raise ValueError('Budget must be the string 0, 1 or 2')
        revision = self.engine.get_revision(identifier(revision_id))
        population = set(revision['results']['eligible_patient_ids'])
        reference = revision['reference_patient_id']
        known = {p['patient_id'] for p in self.dataset['patients']}
        if not population <= known or reference not in known:
            raise ValueError('Similarity and trajectory snapshots have different patient populations')
        data = copy.deepcopy(self.dataset)
        data['reference_patient_id'] = reference
        data['patients'] = [p for p in data['patients'] if p['patient_id'] in population | {reference}]
        exact = cohort.run_cohort(data, '0')
        relaxed = cohort.run_cohort(data, budget)
        result = {'revision_id': revision_id, 'budget': budget,
                  'population_scope': 'entire-hard-filter-eligible-population-not-displayed-top-k',
                  'eligible_patient_ids': sorted(population),
                  'eligibility_unresolved': copy.deepcopy(revision['results']['unresolved']),
                  'exact': exact, 'relaxed': relaxed,
                  'added': sorted(set(relaxed['membership']['included']) - set(exact['membership']['included'])),
                  'interpretation': 'Authored exposure-associated trajectory; no causal or clinical acceptance claim.',
                  'query_scope': 'Fixed authored kidney trajectory applied after pre-index eligibility refinements.'}
        result['comparison_id'] = cohort.digest(result)
        self.comparisons[result['comparison_id']] = (copy.deepcopy(result), data)
        self.comparisons.move_to_end(result['comparison_id'])
        while len(self.comparisons) > MAX_COMPARISONS:
            self.comparisons.popitem(last=False)
        return copy.deepcopy(result)

    def export(self, comparison_id):
        result, data = self.comparisons[identifier(comparison_id)]
        manifest = {'format': 'research-workspace-export-1',
                    'scope': 'authored-synthetic-research-prototype',
                    'similarity': self.engine.manifest(result['revision_id']),
                    'comparison': copy.deepcopy(result),
                    'exact_replay': cohort.export_cohort(copy.deepcopy(data), copy.deepcopy(result['exact'])),
                    'relaxed_replay': cohort.export_cohort(copy.deepcopy(data), copy.deepcopy(result['relaxed']))}
        manifest['manifest_sha256'] = cohort.digest(manifest)
        return manifest

    def evidence(self, revision_id, patient_id):
        revision_id, patient_id = identifier(revision_id), identifier(patient_id)
        evidence = self.engine.inspect(revision_id, patient_id)
        source = next((p for p in self.dataset['patients'] if p['patient_id'] == patient_id), None)
        if source is None:
            raise KeyError(patient_id)
        return {'similarity': evidence, 'source_rows': copy.deepcopy(source['source_rows']),
                'pro_solid': copy.deepcopy(source['evidence']),
                'notice': 'Similarity uses eligible pre-index features only. Source rows also show the separate trajectory follow-up.'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'ResearchWorkspace/1'

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *_):
        pass

    @property
    def workspace(self):
        return self.server.workspace

    def authorized(self):
        access = self.workspace.access
        if access is None:
            return True
        headers = self.headers.get_all('Authorization', [])
        accepted = len(headers) == 1 and access.accepts(headers[0])
        self.workspace.recorded.audit('owner' if accepted else 'anonymous',
                                      'access', status='accepted' if accepted else 'denied')
        if not accepted:
            self.send(401, {'error': 'Owner authentication required'}, challenge=True)
        return accepted

    def send(self, status, value, mime='application/json; charset=utf-8', attachment=False, challenge=False):
        if attachment == 'recorded':
            body = json.dumps(value, allow_nan=False, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        else:
            body = json.dumps(value, allow_nan=False).encode() if mime.startswith('application/json') else value
        if getattr(self, '_audit_action', None):
            self.workspace.recorded.audit('owner' if self.workspace.access else 'local',
                                          self._audit_action, status='http_' + str(status))
        self.send_response(status)
        if challenge:
            self.send_header('WWW-Authenticate', 'Basic realm="Research workspace", charset="UTF-8"')
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        if attachment:
            filename = {'temporal': 'temporal-analysis.json', 'journey': 'patient-journey.json',
                        'recorded': 'recorded-journey.json'}.get(attachment, 'research-revision.json')
            self.send_header('Content-Disposition', 'attachment; filename="' + filename + '"')
        self.end_headers()
        self.wfile.write(body)

    def origin_valid(self):
        host = self.headers.get('Host', '')
        if not host or any(c in host for c in '/\\@, \r\n'):
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return False
        origin = self.headers.get('Origin')
        if origin:
            parsed = urlsplit(origin)
            return parsed.scheme in ('http', 'https') and parsed.netloc == host and not parsed.path and not parsed.query and not parsed.fragment
        return True

    def do_GET(self):
        try:
            if not self.origin_valid():
                return self.send(403, {'error': 'Same-origin access required'})
            parsed = urlsplit(self.path)
            if parsed.fragment:
                raise ValueError('Fragments are unsupported')
            query = parse_qs(parsed.query, keep_blank_values=True)
            if any(len(v) != 1 for v in query.values()):
                raise ValueError('Repeated query parameters are unsupported')
            path = parsed.path
            if path in ('/healthz', '/readyz'):
                if query:
                    raise ValueError('Unexpected query parameters')
                ready = path == '/healthz' or self.workspace.ready
                return self.send(200 if ready else 503, {'status': 'ok' if ready else 'not-ready'})
            if not self.authorized():
                return
            if path.startswith('/api/journey/recorded/'):
                self._audit_action = 'read'
            if path == '/api/evidence':
                exact_keys(query, ('revision_id', 'patient_id'))
                with self.workspace.lock:
                    return self.send(200, self.workspace.evidence(query['revision_id'][0], query['patient_id'][0]))
            if query:
                raise ValueError('Unexpected query parameters')
            with self.workspace.lock:
                if path == '/api/capabilities':
                    return self.send(200, self.workspace.capabilities())
                if path == '/api/temporal':
                    return self.send(200, self.workspace.temporal.metadata())
                if path == '/api/editor':
                    return self.send(200, self.workspace.interval_editor.metadata())
                if path == '/api/journey':
                    return self.send(200, self.workspace.journey.metadata())
                if path == '/api/journey/recorded/config':
                    return self.send(200, self.workspace.recorded.metadata())
                if path == '/api/journey/recorded/history':
                    return self.send(200, self.workspace.recorded.history())
                recorded_pattern = re.fullmatch(r'/api/journey/recorded/([A-Za-z0-9_-]+)/jobs/([A-Za-z0-9_-]+)/pattern', path)
                if recorded_pattern:
                    return self.send(200, self.workspace.recorded.pattern_metadata(*recorded_pattern.groups()))
                recorded_references = re.fullmatch(r'/api/journey/recorded/([A-Za-z0-9_-]+)/jobs/([A-Za-z0-9_-]+)/references', path)
                if recorded_references:
                    return self.send(200, self.workspace.recorded.references(*recorded_references.groups()))
                recorded_job = re.fullmatch(r'/api/journey/recorded/([A-Za-z0-9_-]+)/jobs/([A-Za-z0-9_-]+)(?:/anchors/([A-Za-z0-9_.:-]+))?', path)
                if recorded_job:
                    profile, job_id, token = recorded_job.groups()
                    value = (self.workspace.recorded.inspect(profile, job_id, token) if token else
                             self.workspace.recorded.get(profile, job_id))
                    return self.send(200, value)
                if path.startswith('/api/journey/export/'):
                    return self.send(200, self.workspace.journey.export(identifier(path.rsplit('/', 1)[1])), attachment='journey')
                if path.startswith('/api/editor/export/'):
                    return self.send(200, self.workspace.interval_editor.export(identifier(path.rsplit('/', 1)[1])), attachment='temporal')
                if path.startswith('/api/temporal/export/'):
                    return self.send(200, self.workspace.temporal.export(identifier(path.rsplit('/', 1)[1])), attachment='temporal')
                if path.startswith('/api/revisions/'):
                    return self.send(200, self.workspace.engine.get_revision(identifier(path.rsplit('/', 1)[1])))
                if path.startswith('/api/export/'):
                    return self.send(200, self.workspace.export(path.rsplit('/', 1)[1]), attachment=True)
            static = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/temporal': ('temporal.html', 'text/html; charset=utf-8'),
                      '/temporal.js': ('temporal.js', 'text/javascript; charset=utf-8'),
                      '/temporal/editor': ('editor.html', 'text/html; charset=utf-8'),
                      '/editor.js': ('editor.js', 'text/javascript; charset=utf-8'),
                      '/journey': ('journey.html', 'text/html; charset=utf-8'),
                      '/journey.js': ('journey.js', 'text/javascript; charset=utf-8'),
                      '/recorded_journey.js': ('recorded_journey.js', 'text/javascript; charset=utf-8'),
                      '/journey.css': ('journey.css', 'text/css; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path in static:
                name, mime = static[path]
                return self.send(200, (ROOT / name).read_bytes(), mime)
            self.send(404, {'error': 'Route not found'})
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            self.send(400, {'error': str(exc)})
        except KeyError:
            self.send(404, {'error': 'Revision, comparison or patient not found; start a new search if it expired'})
        except Exception:
            self.send(500, {'error': 'Evaluation failed; no completed result is available'})

    def do_POST(self):
        try:
            if not self.origin_valid():
                return self.send(403, {'error': 'Same-origin access required'})
            if not self.authorized():
                return
            if self.path.startswith('/api/journey/recorded/'):
                self._audit_action = 'export' if self.path.endswith('/export') else 'request'
            if self.headers.get('Transfer-Encoding'):
                raise ValueError('Transfer encoding is unsupported')
            if self.headers.get_content_type() != 'application/json':
                return self.send(415, {'error': 'Use application/json'})
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise ValueError('One Content-Length header is required')
            size = int(lengths[0])
            if size > MAX_BODY:
                return self.send(413, {'error': 'Request body exceeds 8192 bytes'})
            def pairs(items):
                value = {}
                for key, val in items:
                    if key in value:
                        raise ValueError('Duplicate JSON field: ' + key)
                    value[key] = val
                return value
            data = json.loads(self.rfile.read(size), object_pairs_hook=pairs,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Non-finite numbers are unsupported')))
            with self.workspace.lock:
                if self.path == '/api/initial':
                    exact_keys(data, ('patient_id', 'top_k'))
                    if type(data['top_k']) is not int or not 1 <= data['top_k'] <= 20:
                        raise ValueError('top_k must be an integer from 1 through 20')
                    result = self.workspace.engine.initial(identifier(data['patient_id']), data['top_k'])
                elif self.path == '/api/refine':
                    exact_keys(data, ('revision_id', 'operation'))
                    result = self.workspace.engine.refine(identifier(data['revision_id']), data['operation'])
                elif self.path == '/api/trajectory':
                    exact_keys(data, ('revision_id', 'budget'))
                    result = self.workspace.compare(identifier(data['revision_id']), data['budget'])
                elif self.path == '/api/temporal/run':
                    exact_keys(data, ('budget',))
                    result = self.workspace.temporal.run(data['budget'])
                elif self.path == '/api/editor/run':
                    result = self.workspace.interval_editor.run(data)
                elif self.path == '/api/journey/recorded/compare':
                    result = self.workspace.recorded.compare(data)
                elif self.path == '/api/journey/recorded/export':
                    return self.send(200, export_recorded(self.workspace.recorded, data), attachment='recorded')
                elif self.path == '/api/journey/recorded/pattern':
                    result = self.workspace.recorded.pattern(data)
                elif self.path == '/api/journey/recorded/resume':
                    result = self.workspace.recorded.resume(data)
                elif self.path == '/api/journey/recorded/jobs':
                    result = self.workspace.recorded.start(data)
                elif self.path == '/api/journey/run':
                    result = self.workspace.journey.run(data)
                elif self.path == '/api/journey/compile':
                    exact_keys(data, ('pattern',))
                    query = compile_pattern(data['pattern'])
                    result = {'pattern': decompile_query(query), 'query': query}
                elif self.path == '/api/journey/decompile':
                    exact_keys(data, ('query',))
                    pattern = decompile_query(data['query'])
                    result = {'pattern': pattern, 'query': compile_pattern(pattern)}
                elif self.path == '/api/journey/catalogue':
                    exact_keys(data, ('pattern', 'catalogue', 'budget'))
                    query = compile_pattern(data['pattern'])
                    result = {'query': query, 'policy': compile_catalogue(query, data['catalogue'], data['budget'])}
                else:
                    return self.send(404, {'error': 'Route not found'})
                self.send(200, result)
        except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
            self.send(400, {'error': str(exc)})
        except RuntimeError as exc:
            self.send(409, {'error': str(exc)})
        except KeyError:
            self.send(404, {'error': 'Revision or patient not found; start a new search if it expired'})
        except Exception:
            self.send(500, {'error': 'Evaluation failed; no completed result is available'})


class ResearchServer(ThreadingHTTPServer):
    def server_close(self):
        try:
            super().server_close()
        finally:
            if hasattr(self, 'workspace'):
                self.workspace.recorded.close()


def make_server(host='127.0.0.1', port=8080, *, recorded_config=None, state_dir=None, auth_file=None):
    if recorded_config is not None and host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Configured recorded sources require a loopback host')
    if auth_file is not None and host not in ('127.0.0.1', 'localhost'):
        raise ValueError('Owner authentication requires a loopback host')
    access = OwnerAccess(auth_file) if auth_file is not None else None
    workspace = Workspace(recorded_config=recorded_config, state_dir=state_dir, access=access)
    try:
        server = ResearchServer((host, port), Handler)
    except Exception:
        workspace.recorded.close()
        raise
    server.workspace = workspace
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', default=8080, type=int)
    parser.add_argument('--recorded-config', type=Path, help='Startup-only reviewed source and mapping configuration for the recorded journey')
    parser.add_argument('--state-dir', type=Path, help='Private local directory for durable recorded jobs and audit metadata')
    parser.add_argument('--auth-file', type=Path, help='Private owner:<secret> credential file; loopback only')
    args = parser.parse_args()
    server = make_server(args.host, args.port, recorded_config=args.recorded_config, state_dir=args.state_dir, auth_file=args.auth_file)
    print(f'Research workspace: http://{args.host}:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
