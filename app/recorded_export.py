"""Portable completed recorded-query evidence, replayed only on admitted local inputs.

The report is a deterministic comparison record, not an authenticated signature or
an input source. In particular, a report never selects a filesystem configuration.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import time

from app.temporal import digest
from patterns.claim_rdf import digest as context_digest

FORMAT = 'recorded-journey-export-1'
MAX_BYTES = 8 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = (
    'app/recorded_export.py', 'app/recorded_journey.py',
    'app/recorded_similarity.py', 'app/recorded_selectors.py',
    'app/temporal_replay.py', 'demo/pressure.py', 'demo/mapped_pressure.py',
    'demo/pressure_cache.py', 'patterns/pressure_service_config.py',
)
INTERPRETATION = (
    'Exact recomputation on the same admitted local sources, reviews and implementation; '
    'the digest does not authenticate the report or its publisher. Recorded observations '
    'are not treatment effects. Clinical interpretation remains unverified.'
)


def _encoded(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError('Invalid recorded export JSON') from error
    if len(raw) > MAX_BYTES:
        raise ValueError('Recorded export exceeds 8 MiB')
    return raw


def _artifacts():
    return {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ARTIFACTS}


def _validate_snapshot(snapshot):
    """Reject partial or internally stale evidence before offering an export."""
    try:
        result = snapshot['temporal_result']
        anchors = result['anchors']
        details = snapshot['details']
        tokens = [a['token'] for a in anchors]
        if (result['status'] != 'COMPLETED'
                or type(result['anchors_verified']) is not int
                or type(result['anchors_total']) is not int
                or result['anchors_verified'] != result['anchors_total']
                or result['anchors_total'] != len(anchors)
                or len(tokens) != len(set(tokens))
                or len(details) != len(anchors)
                or {d['anchor']['token'] for d in details} != set(tokens)
                or any(a['status'] not in ('MATCH', 'NO_SELECTED_MATCH') for a in anchors)
                or result['clinical_mapping_verified'] is not False
                or context_digest(snapshot['source_context']) != snapshot['session_context_id']
                or context_digest(snapshot['query_context']) != snapshot['query_context_id']
                or result['context'] != snapshot['query_context']
                or result['context_id'] != snapshot['query_context_id']
                or result['context']['session_id'] != snapshot['session_context_id']):
            raise ValueError('Recorded export requires complete, consistent anchor evidence')
        by_token = {a['token']: a for a in anchors}
        for detail in details:
            if (detail['anchor'] != by_token[detail['anchor']['token']]
                    or detail['session_context_id'] != snapshot['session_context_id']
                    or detail['query_context_id'] != snapshot['query_context_id']
                    or detail['sql_agreement'] is not True
                    or detail['clinical_mapping_verified'] is not False):
                raise ValueError('Recorded export contains stale or unverified anchor evidence')
    except (KeyError, TypeError) as error:
        raise ValueError('Recorded export requires complete, consistent anchor evidence') from error


def _comparison(snapshot, reference_token, top_k):
    if reference_token is None:
        if top_k is not None:
            raise ValueError('top_k must be null when no reference is selected')
        return None
    from app.recorded_similarity import compare
    return {'reference_token': reference_token, 'top_k': top_k,
            'result': compare(snapshot, reference_token, top_k)}


def export_recorded(workspace, request):
    """Export retained job evidence without reopening changed sources or reviews."""
    if not isinstance(request, dict) or set(request) != {
            'profile', 'job_id', 'reference_token', 'top_k'}:
        raise ValueError('Recorded export fields must be profile, job_id, reference_token, top_k')
    snapshot = deepcopy(workspace.snapshot(request['profile'], request['job_id']))
    _validate_snapshot(snapshot)
    # Operational IDs and execution/cache timings are deliberately outside the
    # portable claim. Stable source-derived anchor tokens remain meaningful.
    snapshot.pop('job_id', None)
    result = snapshot['temporal_result']
    result.pop('elapsed_seconds', None)
    query_request = {'profile': request['profile'],
                     'stratum': result['stratum'],
                     'controls': deepcopy(snapshot['query_context']['controls'])}
    bundle = {
        'format': FORMAT,
        'source_mode': result['source_mode'],
        'request': query_request,
        'snapshot': snapshot,
        'comparison': _comparison(snapshot, request['reference_token'], request['top_k']),
        'artifacts': _artifacts(),
        'interpretation': INTERPRETATION,
    }
    bundle['report_id'] = digest(bundle)
    # Normalize tuples and other JSON-compatible values before equality/replay.
    return json.loads(_encoded(bundle))


def verify_recorded(bundle, *, config=None):
    """Replay against authored defaults or an explicitly supplied local config."""
    _encoded(bundle)
    if not isinstance(bundle, dict) or set(bundle) != {
            'format', 'source_mode', 'request', 'snapshot', 'comparison',
            'artifacts', 'interpretation', 'report_id'} or bundle['format'] != FORMAT:
        raise ValueError('Unsupported recorded export')
    if bundle['report_id'] != digest({k: v for k, v in bundle.items() if k != 'report_id'}):
        raise ValueError('Recorded export fingerprint differs')
    if bundle['artifacts'] != _artifacts():
        raise ValueError('Recorded export implementation differs from this checkout')
    _validate_snapshot(bundle['snapshot'])
    request = bundle['request']
    if not isinstance(request, dict) or set(request) != {'profile', 'stratum', 'controls'}:
        raise ValueError('Invalid recorded export request')
    mode = bundle['source_mode']
    if mode == 'configured-records':
        if config is None:
            raise ValueError('Configured recorded replay requires explicit --recorded-config')
        if request['profile'] != 'reviewed' or request['stratum'] != 'configured':
            raise ValueError('Invalid configured recorded export profile')
    elif mode == 'synthetic':
        if config is not None:
            raise ValueError('Authored recorded replay does not accept a configured source')
        if request['profile'] not in ('literal', 'reviewed') or request['stratum'] != 'synthetic':
            raise ValueError('Invalid authored recorded export profile')
    else:
        raise ValueError('Unsupported recorded export source mode')
    comparison = bundle['comparison']
    if comparison is None:
        reference_token = top_k = None
    elif isinstance(comparison, dict) and set(comparison) == {'reference_token', 'top_k', 'result'}:
        reference_token, top_k = comparison['reference_token'], comparison['top_k']
        if reference_token is None:
            raise ValueError('Recorded comparison must identify a reference')
    else:
        raise ValueError('Invalid recorded export comparison')
    from app.recorded_journey import RecordedJourneyWorkspace
    workspace = RecordedJourneyWorkspace(config=config)
    try:
        job = workspace.start(deepcopy(request))
        deadline = time.monotonic() + 120
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.01)
            job = workspace.get(request['profile'], job['id'])
        if job['status'] != 'COMPLETED':
            raise ValueError('Recorded replay did not complete: ' + str(job.get('error', job['status'])))
        expected = export_recorded(workspace, {
            'profile': request['profile'], 'job_id': job['id'],
            'reference_token': reference_token, 'top_k': top_k,
        })
        if _encoded(expected) != _encoded(bundle):
            raise ValueError('Recorded export differs from replay on admitted local sources and reviews')
        return {'verified': True, 'report_id': expected['report_id'],
                'source_mode': mode, 'profile': request['profile'],
                'session_context_id': expected['snapshot']['session_context_id'],
                'query_context_id': expected['snapshot']['query_context_id'],
                'metrics': expected['snapshot']['temporal_result']['metrics'],
                'comparison_verified': comparison is not None,
                'clinical_mapping_verified': False,
                'interpretation': INTERPRETATION}
    finally:
        workspace.close()
