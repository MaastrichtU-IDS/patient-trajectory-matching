#!/usr/bin/env python3
"""Reproduce the unchanged v2 OWL checker; retain logs in a fresh output directory."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--robot-jar', required=True, type=Path,
                        help='ROBOT v1.9.10 release JAR (SHA-256 verified)')
    parser.add_argument('--output', type=Path, default=HERE / 'results/local-run',
                        help='new directory; existing evidence is never overwritten')
    args = parser.parse_args()
    provenance = json.loads((HERE / 'provenance.json').read_text())
    checks = json.loads((HERE / 'checks.json').read_text())
    jar = args.robot_jar.resolve()
    if not jar.is_file() or digest(jar) != provenance['tools']['robot']['sha256']:
        parser.error('ROBOT JAR is missing or differs from the pinned v1.9.10 release')
    for name, expected in provenance['sha256'].items():
        path = ROOT / name
        if not path.is_file() or digest(path) != expected:
            parser.error(f'original artifact missing or changed: {name}')
    for tool in ('java', 'javac'):
        if shutil.which(tool) is None:
            parser.error(f'{tool} is required; install a JDK (tested with OpenJDK 21)')
    output = args.output.resolve()
    if output.exists():
        parser.error(f'output already exists: {output}; choose a fresh directory')
    output.mkdir(parents=True)
    metadata = {
        'run_kind': 'rerun of unchanged original checker and OWL inputs',
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'python': sys.version, 'robot_sha256': digest(jar),
        'input_sha256': provenance['sha256'],
        'runner_sha256': digest(Path(__file__)),
        'inventory_sha256': digest(HERE / 'checks.json'),
        'commands': [],
        'result_values': 'Recovered from successful boolean assertions in the original checker; see raw logs.',
        'validation_scope': 'Standalone OWL core/example; no SULO import alignment or temporal query engine.',
    }

    def run(command, stem, cwd):
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True)
        (output / f'{stem}.stdout.txt').write_text(result.stdout)
        (output / f'{stem}.stderr.txt').write_text(result.stderr)
        metadata['commands'].append({'argv': command, 'cwd': str(cwd),
                                     'returncode': result.returncode,
                                     'stdout': f'{stem}.stdout.txt',
                                     'stderr': f'{stem}.stderr.txt'})
        return result

    success = False
    with tempfile.TemporaryDirectory(prefix='temporal-kg-v2-') as scratch:
        work = Path(scratch)
        original_layout = work / 'formal-definition-v2'
        original_layout.mkdir()
        for name in ('temporal-core.ofn', 'example.ofn'):
            shutil.copyfile(HERE.parent / 'ontology' / name, original_layout / name)
        # Keep the original checker arguments so its transcript can be compared byte-for-byte.
        java = shutil.which('java')
        javac = shutil.which('javac')
        run([java, '-version'], 'java-version', work)
        run([javac, '-version'], 'javac-version', work)
        run([java, '-jar', str(jar), '--version'], 'robot-version', work)
        profile = run([java, '-jar', str(jar), 'validate-profile', '--input',
                       'formal-definition-v2/temporal-core.ofn', '--profile', 'DL',
                       '--output', str(output / 'owl2dl-profile.txt')], 'profile', work)
        classes = work / 'classes'
        classes.mkdir()
        compile_result = run([javac, '-cp', str(jar), '-d', str(classes),
                              str(HERE / 'ValidateTemporal.java')], 'compile', work)
        checker = None
        if compile_result.returncode == 0:
            checker = run([java, '-cp', str(classes) + os.pathsep + str(jar),
                           'ValidateTemporal', 'formal-definition-v2/temporal-core.ofn',
                           'formal-definition-v2/example.ofn'], 'checker', work)
        passed = [] if checker is None else [
            line.removeprefix('PASS: ') for line in checker.stdout.splitlines()
            if line.startswith('PASS: ')]
        labels = [check['label'] for check in checks]
        transcript_matches = passed == labels
        # A PASS is emitted only after the exact assertion in the unchanged source succeeds.
        with (output / 'checks.tsv').open('w', newline='') as stream:
            writer = csv.writer(stream, delimiter='\t', lineterminator='\n')
            writer.writerow(['id', 'input', 'expression', 'expected', 'actual', 'outcome', 'evidence'])
            for index, check in enumerate(checks):
                observed = index < len(passed) and passed[index] == check['label']
                failed = checker is not None and f'java.lang.AssertionError: {check["label"]}\n' in checker.stderr
                expected = str(check['expected']).lower()
                actual = expected if observed else str(not check['expected']).lower() if failed else ''
                outcome = 'PASS' if observed else 'FAIL' if failed else 'NOT_OBSERVED'
                writer.writerow([check['id'], check['input'], check['expression'], expected,
                                 actual, outcome, f'checker.stdout.txt:{index + 1}' if observed else 'checker.stderr.txt'])
        success = (profile.returncode == 0 and checker is not None
                   and checker.returncode == 0 and transcript_matches and len(checks) == 18)
        metadata['passed'] = success
        metadata['checks_passed'] = len(passed)
        metadata['original_transcript_byte_match'] = checker is not None and (
            (output / 'checker.stdout.txt').read_bytes()
            == (HERE / 'results/original-2026-09-15/reasoner-checks.txt').read_bytes())
        metadata['original_profile_byte_match'] = (output / 'owl2dl-profile.txt').is_file() and (
            (output / 'owl2dl-profile.txt').read_bytes()
            == (HERE / 'results/original-2026-09-15/owl2dl-profile.txt').read_bytes())
        metadata['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
        (output / 'run.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(f'{"PASS" if success else "FAIL"}: {len(passed)}/18 checks; evidence: {output}')
    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
