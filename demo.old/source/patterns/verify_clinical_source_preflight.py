"""Verify pinned demo source bytes and publish only the aggregate preflight report."""
import argparse
import json
from pathlib import Path
from . import clinical_source_preflight as preflight, exact_intervals as ei

PIN = ei.ROOT / 'data/clinical-source-demo-pin.json'
REQUEST = ei.ROOT / 'examples/clinical-source-preflight/demo-request.json'


def verify_result(result):
    pin = json.loads(PIN.read_text())
    ei.require(result['status'] == 'COMPLETED_SOURCE_PREFLIGHT' and result['row_accounting_complete'], 'INCOMPLETE_DEMO_PREFLIGHT')
    ei.require(result['context']['request'] == json.loads(REQUEST.read_text()), 'DEMO_REQUEST_MISMATCH')
    manifests = {m['table']: m for m in result['context']['source_files']}
    ei.require(set(manifests) == set(pin['files']), 'DEMO_TABLE_SET_MISMATCH')
    for table, expected in pin['files'].items():
        for field, value in expected.items():
            ei.require(manifests[table][field] == value, 'DEMO_PIN_MISMATCH:' + table + ':' + field)
    ei.require(result['context_id'] == preflight.cr.digest(result['context']), 'DEMO_CONTEXT_DIGEST_MISMATCH')
    for table, total in result['input_rows'].items():
        ei.require(total == manifests[table]['rows'] and sum(result['row_outcomes'][table].values()) == total,
                   'DEMO_ACCOUNTING_MISMATCH:' + table)
    return {'profile': 'clinical-source-preflight-verification-1.0',
        'source_url': pin['source_url'], 'published_checksum_url': pin['checksum_url'],
        'source_pin_sha256': ei.digest(PIN.read_text()), 'request_sha256': ei.digest(REQUEST.read_text()),
        'verifier_sha256': ei.digest(Path(__file__).read_text()), 'published_file_hashes_verified': True,
        'scope': 'Public-demo item coverage and capacity only; no mixed query or clinical cohort evaluation.',
        'result': result}


def verify(folder):
    return verify_result(preflight.run(folder, json.loads(REQUEST.read_text())))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.input_dir)
    (ei.ROOT / 'verification/clinical-source-preflight-demo-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['result']['status'], 'input_rows': report['result']['input_rows']}))
