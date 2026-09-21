"""Reproduce the pinned public-demo record query and save aggregate evidence only."""
import argparse
import csv
import zlib
import importlib.metadata
import json
from pathlib import Path

from . import exact_intervals as ei
from . import mimic_record_query as pipeline
from . import verify_mimic_demo as demo

REQUEST = ei.ROOT / 'examples/mimic-record-query/demo-request.json'


def verify(input_dir):
    request = json.loads(REQUEST.read_text())
    result = pipeline.run(input_dir, request)
    admission = demo.verify_summary(result['admission']['summary'])
    summary = result['summary']
    ei.require(summary['status'] == 'COMPLETED_RECORDED_QUERY' and summary['reference_verified'], 'DEMO_QUERY_NOT_VERIFIED')
    return {'profile': 'mimic-record-query-verification-1.0',
            'scope': 'Public-demo recorded source intervals only; no clinical cohort claim.',
            'full_mimic_analyzed': False, 'patient_rows_included': False,
            'request_sha256': ei.digest(REQUEST.read_text()),
            'verifier_sha256': ei.digest(Path(__file__).read_text()),
            'rustdl_version': importlib.metadata.version('rustdl'),
            'admission': admission, 'query_summary': summary}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/mimic-record-query-demo-report.json')
    args = parser.parse_args()
    try:
        protected = {(args.input_dir / (table + suffix)).resolve()
                     for table in pipeline.admission.HEADERS for suffix in ('.csv', '.csv.gz')}
        ei.require(args.output.resolve() not in protected | {REQUEST.resolve()}, 'OUTPUT_OVERWRITES_INPUT')
        report = verify(args.input_dir)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n'); temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, importlib.metadata.PackageNotFoundError) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    print(json.dumps({key: report['query_summary'][key] for key in
                     ('status', 'episode_outcome_counts', 'matched_patients', 'query_outcome_counts')}))


if __name__ == '__main__':
    main()
