"""Compare a changed pressure query over reused preparation with full graph/SQL execution."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
import sys
import threading
import time
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pressure
import serve
from patterns import reviewed_pressure_session as engine, prepared_mixed_query as prepared

DEFAULT = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}
CHANGED = {'threshold': '60', 'baseline_minutes': 25, 'followup_minutes': 60}
FILES = tuple(sorted(set(engine.FILES + prepared.ARTIFACTS + (
    'demo/pressure.py', 'demo/pressure_cache.py', 'demo/pressure.js', 'demo/serve.py',
    'demo/benchmark_prepared_pressure.py', 'data/clinical-source-demo-pin.json'))))


def artifacts():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}


def comparable(result):
    result = deepcopy(result)
    result.pop('elapsed_seconds')
    return result


def benchmark(folder=None):
    service = pressure.PressureService(folder, synthetic=folder is None)
    serve.PRESSURE = service
    serve.Handler.log_message = lambda *args: None
    server = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    stratum = 'arterial' if folder else 'synthetic'
    def get(path):
        with urlopen(base + path, timeout=30) as response: return json.load(response)
    def run(controls):
        body = {'stratum': stratum, 'controls': controls}
        with urlopen(Request(base + '/api/pressure/jobs', data=json.dumps(body).encode(),
                             headers={'Content-Type': 'application/json'})) as response:
            job = json.load(response)
        deadline = time.monotonic() + 1800
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.1); job = get('/api/pressure/jobs/' + job['id'])
        assert job['status'] == 'COMPLETED', job.get('error', 'Incomplete query')
        return job, service.jobs[job['id']]['_result']
    try:
        before = artifacts()
        cold, original = run(DEFAULT)
        session = service.session
        cases = {}
        for token, detail in original['details'].items():
            case = 'eligible' if detail['bindings'] else 'no_eligible_pair'
            cases.setdefault(case, token)
            if any(row[4] is None for row in detail['bindings']): cases.setdefault('missing_followup', token)
        checked = {}
        for case, token in cases.items():
            evidence = get('/api/pressure/jobs/' + cold['id'] + '/anchors/' + token)
            assert evidence == session.inspect(original, token)
            assert evidence['sql_agreement'] and evidence['treatment']['decisions']
            checked[case] = True
        live = {'profile': 'live-pressure-http-verification-1.0', 'status': 'VERIFIED', 'stratum': stratum,
                'controls': DEFAULT, 'session_context': session.context, 'session_context_id': session.id,
                'query_context': original['context'], 'query_context_id': original['context_id'],
                'metrics': original['metrics'], 'anchors_verified': original['anchors_verified'],
                'stays_retained': len(original['roster']), 'http_inspector_cases': checked,
                'patient_rows_or_identifiers_included': False, 'clinical_mapping_verified': False,
                'visual_browser_inspection_verified': False}
        print(json.dumps({'stage': 'cold', 'timing': cold['timing'], 'preparation': cold['execution']['batch_preparation']}), flush=True)
        changed, actual = run(CHANGED)
        assert changed['execution']['mode'] == 'fresh_query'
        assert changed['execution']['batch_preparation']['reused_batches'] > 0
        print(json.dumps({'stage': 'changed_prepared', 'timing': changed['timing'], 'preparation': changed['execution']['batch_preparation']}), flush=True)
        start = time.monotonic()
        reference = session.execute(CHANGED)  # Original graph/semantic/SQL route, no executor callback.
        reference_seconds = time.monotonic() - start
        assert comparable(actual) == comparable(reference), 'Changed-query result differs from full execution'
        inspections = 0
        for token in actual['details']:
            assert service.inspect(changed['id'], token) == session.inspect(reference, token)
            inspections += 1
        assert artifacts() == before
        session.check_current()
        report = {'profile': 'prepared-pressure-benchmark-1.0', 'status': 'VERIFIED',
                  'source_mode': service.metadata()['source_mode'], 'stratum': stratum,
                  'python_version': platform.python_version(), 'artifacts': before,
                  'source_files': session.context['source_files'], 'session_context_id': session.id,
                  'original_query_context_id': original['context_id'], 'changed_query_context_id': actual['context_id'],
                  'original_controls': DEFAULT, 'changed_controls': CHANGED,
                  'original_metrics': original['metrics'], 'changed_metrics': actual['metrics'],
                  'anchors_verified': actual['anchors_verified'], 'stays_retained': len(actual['roster']),
                  'cold_job_timing': cold['timing'], 'changed_job_timing': changed['timing'],
                  'full_changed_execution_seconds': round(reference_seconds, 6),
                  'cold_preparation': cold['execution']['batch_preparation'],
                  'changed_preparation': changed['execution']['batch_preparation'],
                  'all_result_fields_except_duration_equal': True, 'anchor_inspections_equal': inspections,
                  'http_inspector_cases': checked, 'patient_rows_or_identifiers_included': False,
                  'clinical_mapping_verified': False,
                  'timing_scope': 'Single local sequence; server jobs exclude response assembly/polling/rendering; full reference is Session.execute wall time'}
        print(json.dumps({'stage': 'verified', 'changed_metrics': actual['metrics'], 'full_changed_execution_seconds': reference_seconds}), flush=True)
        return report, live
    finally:
        server.shutdown(); server.server_close(); service.close()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n'); temporary.replace(path)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mimic-dir', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--live-report', type=Path)
    args = parser.parse_args()
    report, live = benchmark(args.mimic_dir)
    save(args.output, report)
    if args.live_report: save(args.live_report, live)
