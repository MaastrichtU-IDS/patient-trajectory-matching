"""Verify a running research application using only the Python standard library."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def get(base: str, path: str) -> tuple[str, bytes]:
    with urllib.request.urlopen(base.rstrip('/') + path, timeout=3) as response:
        if response.status != 200:
            raise AssertionError(f'{path}: HTTP {response.status}')
        return response.headers.get_content_type(), response.read(1_048_577)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8080')
    parser.add_argument('--timeout', type=float, default=60)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    deadline = time.monotonic() + args.timeout
    while True:
        try:
            for path in ('/healthz', '/readyz'):
                mime, body = get(args.url, path)
                if mime != 'application/json' or not isinstance(json.loads(body), dict):
                    raise AssertionError(f'{path}: expected a JSON object')
            break
        except (OSError, ValueError, AssertionError) as exc:
            if time.monotonic() >= deadline:
                raise SystemExit(f'Application did not become ready: {exc}') from exc
            time.sleep(0.25)
    mime, body = get(args.url, '/api/capabilities')
    if mime != 'application/json' or not isinstance(json.loads(body), dict):
        raise SystemExit('Capabilities endpoint did not return a JSON object')
    mime, body = get(args.url, '/')
    if mime != 'text/html' or b'<html' not in body.lower():
        raise SystemExit('Application page did not return HTML')
    mime, body = get(args.url, '/temporal')
    if mime != 'text/html' or b'Temporal uncertainty' not in body:
        raise SystemExit('Temporal page did not return expected HTML')
    request = urllib.request.Request(args.url.rstrip('/') + '/api/temporal/run',
                                     data=b'{"budget":"1.25"}',
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        report = json.load(response)
    if report['result']['robust_patient_ids'] != ['T01', 'T02'] or report['added_robust_patient_ids'] != ['T02']:
        raise SystemExit('Temporal evaluation differs from the authored example')
    mime, body = get(args.url, '/api/temporal/export/' + report['report_id'])
    if mime != 'application/json' or json.loads(body) != report:
        raise SystemExit('Temporal export differs from the completed evaluation')
    mime, body = get(args.url, '/temporal/editor')
    if mime != 'text/html' or b'Edit interval constraints' not in body:
        raise SystemExit('Interval editor page did not return expected HTML')
    _, body = get(args.url, '/api/editor')
    controls = json.loads(body)['default_controls']
    request = urllib.request.Request(args.url.rstrip('/') + '/api/editor/run',
                                     data=json.dumps(controls).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        report = json.load(response)
    if report['result']['certain_patient_ids'] != ['T01'] or report['result']['possible_patient_ids'] != ['T01', 'T02']:
        raise SystemExit('Interval editor differs from the authored example')
    _, body = get(args.url, '/api/editor/export/' + report['report_id'])
    if json.loads(body) != report:
        raise SystemExit('Interval editor export differs from execution')
    print(json.dumps({'status': 'passed', 'checks': [
        '/healthz', '/readyz', '/api/capabilities', '/', '/temporal',
        '/api/temporal/run', '/api/temporal/export/<report_id>', '/temporal/editor',
        '/api/editor', '/api/editor/run', '/api/editor/export/<report_id>'
    ]}))


if __name__ == '__main__':
    main()
