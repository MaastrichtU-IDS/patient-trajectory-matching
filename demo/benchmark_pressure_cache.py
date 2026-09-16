"""Reproduce cold/warm queries and cache hits; export aggregate evidence only."""
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
from patterns import reviewed_pressure_session as engine

FILES = tuple(sorted(set(engine.FILES + (
    'demo/pressure.py', 'demo/pressure_cache.py', 'demo/serve.py',
    'demo/pressure.js', 'demo/benchmark_pressure_cache.py', 'data/clinical-source-demo-pin.json'))))
DEFAULT = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}


def artifacts():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES}


def comparable(result):
    value = deepcopy(result)
    value.pop('elapsed_seconds')
    return value


def benchmark(folder=None, stratum='arterial'):
    service = pressure.PressureService(folder, synthetic=folder is None)
    serve.PRESSURE = service
    serve.Handler.log_message = lambda *args: None
    server = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    request = {'stratum': stratum if folder else 'synthetic', 'controls': DEFAULT}
    def get(path):
        with urlopen(base + path, timeout=30) as response:
            return json.load(response)
    def run():
        with urlopen(Request(base + '/api/pressure/jobs', data=json.dumps(request).encode(),
                             headers={'Content-Type': 'application/json'}), timeout=30) as response:
            job = json.load(response)
        deadline = time.monotonic() + 1800
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.05)
            job = get('/api/pressure/jobs/' + job['id'])
        if job['status'] != 'COMPLETED':
            raise ValueError(job.get('error', 'Query did not complete'))
        return job, service.jobs[job['id']]['_result']
    try:
        before = artifacts()
        first, reference = run()
        reference = deepcopy(reference)
        session = service.session
        probes = {}
        for token, detail in reference['details'].items():
            if detail['bindings']:
                probes.setdefault('eligible', token)
            else:
                probes.setdefault('no_eligible_pair', token)
            if any(row[4] is None for row in detail['bindings']):
                probes.setdefault('missing_followup', token)
        # Synthetic default has only eligible anchors; public arterial includes all three cases.
        reference_inspections = {t: session.inspect(reference, t) for t in reference['details']}
        runs = []
        def record(label, job, result):
            assert comparable(result) == comparable(reference), 'Full result/evidence changed'
            assert all(service.inspect(job['id'], t) == d for t, d in reference_inspections.items())
            for token in probes.values():
                assert get('/api/pressure/jobs/' + job['id'] + '/anchors/' + token) == reference_inspections[token]
            runs.append({'label': label, 'mode': job['execution']['mode'], 'timing': job['timing'],
                         'full_result_equal': True, 'all_inspections_equal': True,
                         'http_inspector_cases': sorted(probes)})
            print(json.dumps({'run': label, 'mode': job['execution']['mode'], 'timing': job['timing']}), flush=True)
        record('cold_preparation_and_query', first, reference)
        # A second real graph/SQL execution over the same prepared source measures warm query cost.
        service.result_cache.clear()
        warm, result = run()
        assert warm['execution']['mode'] == 'fresh_query'
        record('warm_preparation_fresh_query', warm, result)
        for number in range(1, 4):
            job, result = run()
            assert job['execution']['mode'] == 'cached_complete_result'
            record(f'repeated_query_{number}', job, result)
        assert artifacts() == before, 'Implementation changed during benchmark'
        session.check_current()
        return {'profile': 'pressure-result-cache-benchmark-1.0', 'status': 'VERIFIED',
                'source_mode': service.metadata()['source_mode'], 'stratum': request['stratum'],
                'controls': DEFAULT, 'python_version': platform.python_version(),
                'artifacts': before, 'source_files': session.context['source_files'],
                'session_context_id': session.id, 'query_context_id': reference['context_id'],
                'review_sha256': session.context['review_sha256'],
                'metrics': reference['metrics'], 'anchors_verified': reference['anchors_verified'],
                'stays_retained': len(reference['roster']), 'runs': runs,
                'cache_limits': {'entries': service.result_cache.max_entries,
                                 'serialized_bytes': service.result_cache.max_bytes},
                'patient_rows_or_identifiers_included': False, 'clinical_mapping_verified': False,
                'timing_scope': 'Server job wall time before response assembly; one process, one local sample sequence; no latency guarantee',
                'evidence_scope': 'All result fields except elapsed_seconds and all anchor inspections compared exactly; cached hits do not rerun graph or SQL'}
    finally:
        server.shutdown()
        server.server_close()
        service.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mimic-dir', type=Path, help='Original pinned public MIMIC-IV demo ICU files; omit for synthetic')
    parser.add_argument('--stratum', choices=tuple(pressure.STRATA), default='arterial')
    parser.add_argument('--output', type=Path, required=True, help='Aggregate JSON report, without source records or identifiers')
    args = parser.parse_args()
    report = benchmark(args.mimic_dir, args.stratum)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + '.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(args.output)
