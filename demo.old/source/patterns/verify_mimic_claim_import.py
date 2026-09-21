"""Verify the pinned public-demo claim import and publish aggregate evidence only."""
import argparse
import csv
import json
from pathlib import Path
import tempfile
import zlib

from . import exact_intervals as ei
from . import mimic_claim_import as pipeline
from . import verify_mimic_demo as demo

REQUEST = ei.ROOT / 'examples/mimic-claim-import/demo-request.json'


def verify(input_dir):
    result = pipeline.run(input_dir, json.loads(REQUEST.read_text()))
    admission = demo.verify_summary(result['admission']['summary'])
    summary = result['summary']
    ei.require(summary['status'] == 'COMPLETED_PENDING_IMPORT' and summary['accepted_claims'] == 0
               and summary['reconciliation_complete'], 'DEMO_IMPORT_NOT_VERIFIED')
    certificates = [e['isolation'] for e in result['episodes'] if e['store'] is not None]
    ei.require(all(c['status'] == 'VERIFIED_EMPTY_PROCESS_MODEL' for c in certificates), 'DEMO_ISOLATION_FAILED')
    return {'profile': 'mimic-claim-import-verification-1.0',
        'scope': 'Public-demo source descriptions only; no acceptance or clinical cohort claim.',
        'full_mimic_analyzed': False, 'patient_rows_included': False,
        'request_sha256': ei.digest(REQUEST.read_text()), 'verifier_sha256': ei.digest(Path(__file__).read_text()),
        'admission': admission, 'import_summary': summary,
        'isolation_summary': {'verified_stores': len(certificates),
            'logical_axioms_per_store': sorted({c['logical_axioms_checked'] for c in certificates}),
            'total_description_triples_checked': sum(c['assertion_triples_checked'] for c in certificates),
            'max_claims_per_store': max((e['selected_records'] for e in result['episodes']), default=0),
            'all_models_have_empty_process_and_time': all(c['process_extension_size'] == 0
                and c['temporal_extension_size'] == 0 for c in certificates)}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/mimic-claim-import-demo-report.json')
    args = parser.parse_args(); temporary = None
    try:
        protected = {p.resolve() for p in pipeline.source_paths(args.input_dir).values()} | {REQUEST.resolve()}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        report = verify(args.input_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(report, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps(report['isolation_summary'], sort_keys=True))


if __name__ == '__main__':
    main()
