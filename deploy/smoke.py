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
    mime, body = get(args.url, '/journey')
    if mime != 'text/html' or b'<html' not in body.lower():
        raise SystemExit('Patient journey page did not return HTML')
    _, body = get(args.url, '/api/journey')
    controls = json.loads(body)['default_request']
    controls.update(reference_patient_id='T03', top_k=1, maximum_baseline=None,
                    question='overlap', budget='1.25')
    request = urllib.request.Request(args.url.rstrip('/') + '/api/journey/run',
                                     data=json.dumps(controls).encode(),
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=10) as response:
        report = json.load(response)
    if (report['eligibility']['eligible_patient_ids'] != ['T01', 'T02', 'T04']
            or report['result']['certain_patient_ids'] != []
            or report['relaxation']['robust_patient_ids'] != ['T01']
            or report['added_robust_patient_ids'] != ['T01']
            or 'T01' in report['ranking']['displayed_patient_ids']):
        raise SystemExit('Patient journey differs from the authored full-pool example')
    mime, body = get(args.url, '/api/journey/export/' + report['report_id'])
    if mime != 'application/json' or json.loads(body) != report:
        raise SystemExit('Patient journey export differs from execution')
    preset_source = report['source']
    pattern = {
        'slots': [{'id': 'infusion', 'event_kind': 'infusion'},
                  {'id': 'collection', 'event_kind': 'specimen_collection'},
                  {'id': 'followup', 'event_kind': 'specimen_collection'}],
        'constraints': [
            {'id': 'c1', 'operator': 'contains', 'left': 'infusion', 'right': 'collection'},
            {'id': 'c2', 'operator': 'gap', 'left': 'collection', 'right': 'followup',
             'minimum_minutes': '0', 'maximum_minutes': '30'}]}
    def journey_post(path, payload):
        request = urllib.request.Request(args.url.rstrip('/') + path,
                                         data=json.dumps(payload).encode(),
                                         headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    compiled = journey_post('/api/journey/compile', {'pattern': pattern})
    controls.update(question='custom', budget='0', pattern=compiled['pattern'])
    report = journey_post('/api/journey/run', controls)
    if (report['query'] != compiled['query'] or report['pattern'] != compiled['pattern']
            or report['source'] != preset_source
            or report['eligibility']['eligible_patient_ids'] != ['T01', 'T02', 'T04']
            or report['result']['certain_patient_ids'] != ['T01']
            or report['result']['possible_patient_ids'] != ['T01', 'T02']
            or len(report['ranking']['displayed_patient_ids']) != 1
            or 'T01' in report['ranking']['displayed_patient_ids']
            or report['policy']['options']):
        raise SystemExit('Three-event builder differs from the authored full-pool example')
    restored = journey_post('/api/journey/decompile', {'query': report['query']})
    if restored != compiled:
        raise SystemExit('Three-event builder query does not round trip')
    mime, body = get(args.url, '/api/journey/export/' + report['report_id'])
    if mime != 'application/json' or json.loads(body) != report:
        raise SystemExit('Three-event builder export differs from execution')
    original_custom_report = report
    pattern['constraints'].extend([
        {'id': 'duration', 'operator': 'duration', 'slot': 'infusion',
         'minimum_minutes': '9', 'maximum_minutes': '9'},
        {'id': 'shared', 'operator': 'minimum_overlap', 'left': 'infusion',
         'right': 'collection', 'minimum_minutes': '3'}])
    duration_change = {'target': 'duration', 'minimum_minutes': '9', 'maximum_minutes': '10'}
    overlap_change = {'target': 'shared', 'minimum_minutes': '2'}
    catalogue = {
        'relaxable_targets': ['duration', 'shared'], 'max_changed_targets': 2,
        'options': [
            {'id': 'duration_only', 'cost': '0.5', 'changes': [duration_change]},
            {'id': 'overlap_only', 'cost': '0.5', 'changes': [overlap_change]},
            {'id': 'combined', 'cost': '1.25', 'changes': [duration_change, overlap_change]}]}
    validated = journey_post('/api/journey/catalogue', {
        'pattern': pattern, 'catalogue': catalogue, 'budget': '1.25'})
    controls.update(pattern=pattern, catalogue=catalogue, budget='1')
    affordable = journey_post('/api/journey/run', controls)
    controls['budget'] = '1.25'
    report = journey_post('/api/journey/run', controls)
    if (report['query'] != validated['query'] or report['policy'] != validated['policy']
            or report['source'] != preset_source or report['result'] != affordable['result']
            or affordable['relaxation']['robust_patient_ids'] != []
            or report['relaxation']['robust_patient_ids'] != ['T01']
            or report['added_robust_patient_ids'] != ['T01']
            or len(report['relaxation']['evaluations']) != 4):
        raise SystemExit('Custom catalogue differs from the authored full-pool example')
    for evaluated, combined_status in ((affordable, None), (report, 'CERTAIN')):
        patient = next(p for p in evaluated['patients'] if p['patient_id'] == 'T01')
        options = {o['option_id']: o for o in patient['option_results']}
        if (patient['original_status'] != 'NO_RECORDED_MATCH'
                or set(options) != {'duration_only', 'overlap_only', 'combined'}
                or options['duration_only']['status'] != 'POSSIBLE'
                or options['overlap_only']['status'] != 'NO_RECORDED_MATCH'
                or options['combined']['status'] != combined_status
                or options['combined']['excluded_by_budget'] != (combined_status is None)
                or options['combined']['selected'] != (combined_status == 'CERTAIN')
                or patient['selected_option'] != ('combined' if combined_status else None)):
            raise SystemExit('Custom catalogue option outcomes differ from the declared options')
    restored = journey_post('/api/journey/decompile', {'query': report['query']})
    if journey_post('/api/journey/compile', {'pattern': restored['pattern']}) != restored:
        raise SystemExit('Custom catalogue original query does not round trip')
    for retained in (original_custom_report, affordable, report):
        mime, body = get(args.url, '/api/journey/export/' + retained['report_id'])
        if mime != 'application/json' or json.loads(body) != retained:
            raise SystemExit('Retained journey export changed after catalogue execution')
    print(json.dumps({'status': 'passed', 'checks': [
        '/healthz', '/readyz', '/api/capabilities', '/', '/temporal',
        '/api/temporal/run', '/api/temporal/export/<report_id>', '/temporal/editor',
        '/api/editor', '/api/editor/run', '/api/editor/export/<report_id>',
        '/journey', '/api/journey', '/api/journey/run', '/api/journey/export/<report_id>',
        '/api/journey/compile', '/api/journey/decompile',
        '/api/journey/run (three-event custom)', '/api/journey/export/<report_id> (three-event custom)',
        '/api/journey/catalogue', '/api/journey/run (custom option outcomes and budget)',
        '/api/journey/export/<report_id> (retained custom catalogue reports)'
    ]}))


if __name__ == '__main__':
    main()
