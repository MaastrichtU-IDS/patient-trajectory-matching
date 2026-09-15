"""Verify supplied public-demo 2.2 files and save aggregate admission evidence only."""
import argparse
import csv
import json
from pathlib import Path
import sys
import zlib

from . import mimic_inputevents as mi

PIN = mi.ROOT / 'data/mimic-inputevents-demo-pin.json'


def verify_summary(summary):
    pin = json.loads(PIN.read_text())
    mi.require(summary['context']['dataset_id'] == pin['dataset_id'], 'DEMO_DATASET_MISMATCH')
    files = {f['table']: f for f in summary['context']['files']}
    mi.require(files.keys() == pin['files'].keys(), 'DEMO_FILE_SET_MISMATCH')
    for table, expected in pin['files'].items():
        for key, value in expected.items():
            mi.require(files[table][key] == value, f'DEMO_PIN_MISMATCH:{table}:{key}')
    return {'source_url': pin['source_url'], 'published_checksum_url': pin['checksum_url'],
            'published_file_hashes_verified': True, 'source_pin_sha256': mi.digest(PIN.read_bytes()),
            'verifier_sha256': mi.digest(Path(__file__).read_bytes()),
            'scope': 'Public demo inputevents staging only; no patient-level data in this report.',
            'full_mimic_analyzed': False, 'summary': summary}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, required=True, help='Directory containing the three original ICU csv.gz files')
    parser.add_argument('--output', type=Path, default=mi.ROOT / 'verification/mimic-demo-inputevents-report.json')
    args = parser.parse_args(argv)
    try:
        result = mi.stage(*(args.input_dir / (table + '.csv.gz') for table in mi.HEADERS))
        report = verify_summary(result['summary'])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        temporary.replace(args.output)
    except (mi.AdmissionError, OSError, EOFError, UnicodeError, csv.Error, zlib.error) as exc:
        print(json.dumps({'status': 'INVALID_INPUT', 'error': str(exc)}), file=sys.stderr)
        return 2
    print(json.dumps(report['summary']['outcome_counts'], sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
