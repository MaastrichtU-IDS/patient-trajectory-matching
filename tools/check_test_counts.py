#!/usr/bin/env python3
"""Generate and check the contract-check totals recorded in docs/status.md.

Test cases are counted with the unittest loader; nothing is executed. Exit 0 means
the recorded totals match the suites that load today, not that any test passes.
The per-increment totals elsewhere in docs/ are historical and are not checked.
"""
from __future__ import annotations
import argparse
import difflib
import importlib
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUS = 'docs/status.md'
CASES = 'examples/cases.json'
ORACLE_REPORT = 'verification/reference-report.json'
REPORT = 'verification/test-count-report.json'
START, END = '<!-- test-counts:start -->', '<!-- test-counts:end -->'
TOTAL_RE = re.compile(r'<!-- test-counts:total -->\d+<!-- /test-counts:total -->')
ROW_RE = re.compile(r'^\| (?P<label>[^|]+?) \| (?P<count>\d+) passed \|$')
TOTAL_ROW_RE = re.compile(r'^\| \*\*Total\*\* \| \*\*\d+ contract checks\*\* \|$')
SENTENCE_RE = re.compile(r'The total is \d+ suite tests plus \d+ oracle cases and [\w-]+ oracle properties')
ADJACENT_RE = re.compile(r'the demo suite has \d+ tests, the research HTTP suite has \d+, and the completion checker has \d+')
NODE_RE = re.compile(r'\b(?:[A-Z][a-z]+|\d+) Node DOM-state suites\b')
WORDS = ['zero', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine']

# Table order. A module missing from here fails the check: that reminder is the point.
LABELS = {
    'test_pro_solid': 'PRO/SOLID acceptance tests',
    'test_exact_intervals': 'Exact-interval conformance tests',
    'test_interval_cohort': 'Interval cohort tests',
    'test_extended_interval_query': 'Extended interval query tests',
    'test_bounded_intervals': 'Bounded uncertainty tests',
    'test_bounded_rdf': 'Bounded RDF ingestion tests',
    'test_extended_relaxation': 'Extended metric relaxation tests',
    'test_robust_relaxation': 'Robust costed relaxation tests',
    'test_evidence_selection': 'Evidence selection tests',
    'test_temporal_interface': 'Temporal interface conformance tests',
    'test_semantic_support': 'Checked Rust semantic support tests (optional dependency, separate CI job)',
    'test_joint_evidence': 'Joint temporal/semantic selection tests',
    'test_use_case_conformance': 'Reproducible GEN-01/COH-01 use-case tests',
    'test_mimic_inputevents': 'MIMIC inputevents admission tests',
    'test_patient_local': 'Patient-local clock and RDF tests',
    'test_mimic_record_query': 'End-to-end MIMIC record-query tests',
    'test_claim_projection': 'Structured claim/projection tests',
    'test_state_validity': 'State validity and coverage tests',
    'test_local_claim_projection': 'Patient-local claim projection tests',
    'test_mimic_claim_import': 'MIMIC pending claim import tests',
    'test_measurement_claims': 'Measurement claims and chartevents import tests',
    'test_mixed_record_query': 'Mixed record query tests',
    'test_source_mixed_query': 'Source mixed-query and SQL comparison tests',
    'test_clinical_source_preflight': 'Clinical source preflight tests',
    'test_indexed_source_windows': 'Indexed source-window tests',
    'test_partitioned_window_query': 'Partitioned window execution tests',
    'test_unique_claim_review': 'Unique-claim review tests',
    'test_reviewed_source_query': 'Source-fidelity audit and reviewed execution tests',
    'test_reviewed_pressure_strata': 'Separate pressure-stratum provenance and comparison tests',
    'test_pressure_overlap': 'Pressure-cohort overlap and provenance tests',
    'test_reviewed_record_mappings': 'Reviewed record mapping tests',
    'test_source_record_catalogue': 'Source record catalogue tests',
    'test_reviewed_measurement_mappings': 'Reviewed measurement selector tests',
    'test_measurement_source_catalogue': 'Measurement source catalogue and audit tests',
    'test_prepared_measurement_session': 'Prepared measurement session tests',
    'test_patient_similarity': 'Pre-index similarity and immutable refinement tests',
}


class CountError(Exception):
    """The recorded totals cannot be derived; never a statement that a test failed."""


def count_suites(root=ROOT, labels=LABELS, package='patterns', exhaustive=True):
    """Count loadable test cases per module. Import failures raise; they are never counted as one.

    `exhaustive` requires labels to name every test module in the package, so a new suite
    fails the check until it is recorded. Pass False to count named modules only.
    """
    root = Path(root)
    modules = sorted(p.stem for p in (root / package).glob('test_*.py'))
    if exhaustive:
        unlabelled = [m for m in modules if m not in labels]
        if unlabelled:
            raise CountError('Add a table label in LABELS for: ' + ', '.join(unlabelled))
    missing = [m for m in labels if m not in modules]
    if missing:
        raise CountError(f'{package}: labelled suites that no longer exist: ' + ', '.join(missing))
    # Import against this root only. Snapshot the package namespace so a `patterns`
    # already cached from another root (or by the caller) cannot shadow it, and put
    # everything back afterwards so counting leaves the process as it found it.
    def namespace():
        return {k: v for k, v in sys.modules.items() if k == package or k.startswith(package + '.')}
    saved = namespace()
    for key in saved:
        del sys.modules[key]
    sys.path.insert(0, str(root))
    loader = unittest.TestLoader()
    counts = {}
    try:
        for module in labels:
            try:
                imported = importlib.import_module(f'{package}.{module}')
            except Exception as exc:  # a broken import must not silently count as one failed test
                raise CountError(f'{package}.{module} failed to import: {exc!r}') from exc
            counts[module] = loader.loadTestsFromModule(imported).countTestCases()
    finally:
        sys.path.remove(str(root))
        for key in namespace():
            del sys.modules[key]
        sys.modules.update(saved)
    return counts


def adjacent_counts(root=ROOT):
    """Counts for the suites the status sentence names beside the contract total.

    These are not contract checks: the demo, research-HTTP and completion-audit suites
    exercise the application and tooling, not the matching contracts. They were hand-typed
    and drifted, so they are derived here too. Node suites are counted as files because
    they are driven by `node`, not the unittest loader.
    """
    demo_modules = {p.stem: p.stem for p in sorted((Path(root) / 'demo').glob('test_*.py'))}
    demo = sum(count_suites(root, demo_modules, package='demo').values())
    server = count_suites(root, {'test_server': 'test_server'}, package='app',
                          exhaustive=False)['test_server']
    completion = count_suites(root, {'test_check_completion': 'test_check_completion'},
                              package='tools', exhaustive=False)['test_check_completion']
    node = sorted(str(q.relative_to(root)) for d in ('app', 'demo')
                  for q in (Path(root) / d).glob('test_*.cjs'))
    return {'demo_suite': demo, 'research_http_suite': server,
            'completion_checker': completion, 'node_dom_suites': len(node), 'node_suite_files': node}


def oracle_counts(root=ROOT):
    cases = json.loads((Path(root) / CASES).read_text())
    properties = json.loads((Path(root) / ORACLE_REPORT).read_text())['properties']
    return len(cases), len(properties)


def _number(n):
    return WORDS[n] if 0 <= n < len(WORDS) else str(n)


def render_block(block, counts, cases, properties, labels=LABELS, adjacent=None):
    """Regenerate suite rows, the total row and the total sentence; preserve everything else."""
    suite_total = sum(counts.values())
    contract_total = suite_total + cases + properties
    by_label = {labels[m]: counts[m] for m in counts}
    generated = [f'| {labels[m]} | {counts[m]} passed |' for m in labels]
    generated.append(f'| **Total** | **{contract_total} contract checks** |')
    out, emitted, saw_sentence, seen = [], False, False, set()
    for line in block.split('\n'):  # not splitlines(): keep the block's exact line breaks
        row = ROW_RE.match(line)
        if (row and row.group('label') in by_label) or TOTAL_ROW_RE.match(line):
            if not emitted:
                out.extend(generated)
                emitted = True
            continue
        if SENTENCE_RE.search(line):
            line = SENTENCE_RE.sub(
                f'The total is {suite_total} suite tests plus {cases} oracle cases and '
                f'{_number(properties)} oracle properties', line)
            saw_sentence = True
        if adjacent and ADJACENT_RE.search(line):
            line = ADJACENT_RE.sub(
                f"the demo suite has {adjacent['demo_suite']} tests, the research HTTP suite has "
                f"{adjacent['research_http_suite']}, and the completion checker has "
                f"{adjacent['completion_checker']}", line)
            seen.add('adjacent')
        if adjacent and NODE_RE.search(line):
            line = NODE_RE.sub(f"{_number(adjacent['node_dom_suites']).capitalize()} Node DOM-state suites"
                               if NODE_RE.search(line).group(0)[0].isupper()
                               else f"{_number(adjacent['node_dom_suites'])} Node DOM-state suites", line)
            seen.add('node')
        out.append(line)
    if not emitted:
        raise CountError('No recognised suite rows or total row inside the marked block')
    if not saw_sentence:
        raise CountError('The marked block has no "The total is ... suite tests" sentence')
    for name in (('adjacent', 'node') if adjacent else ()):
        if name not in seen:
            raise CountError(f'The marked block has no {name} suite-count phrase to regenerate')
    return '\n'.join(out), contract_total


def render_status(text, counts, cases, properties, labels=LABELS, adjacent=None):
    if text.count(START) != 1 or text.count(END) != 1:
        raise CountError(f'{STATUS} must contain exactly one {START} and one {END}')
    if len(TOTAL_RE.findall(text)) != 1:
        raise CountError(f'{STATUS} must contain exactly one inline total marker')
    head, rest = text.split(START, 1)
    block, tail = rest.split(END, 1)
    new_block, contract_total = render_block(block, counts, cases, properties, labels, adjacent)
    text = head + START + new_block + END + tail
    text = TOTAL_RE.sub(f'<!-- test-counts:total -->{contract_total}<!-- /test-counts:total -->', text)
    return text, contract_total


def report(counts, cases, properties, contract_total, adjacent=None):
    return {'profile': 'test-count-report-1.0', 'method': 'unittest loader; no tests executed',
            'suites': counts, 'suite_tests': sum(counts.values()),
            'oracle_cases': cases, 'oracle_properties': properties,
            'contract_checks': contract_total,
            'adjacent_suites': {k: v for k, v in (adjacent or {}).items() if k != 'node_suite_files'},
            'node_suite_files': (adjacent or {}).get('node_suite_files', [])}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--write', action='store_true', help='rewrite docs/status.md and the report')
    args = parser.parse_args(argv)
    status = args.root / STATUS
    try:
        counts = count_suites(args.root, LABELS)
        cases, properties = oracle_counts(args.root)
        adjacent = adjacent_counts(args.root)
        current = status.read_text()
        rendered, contract_total = render_status(current, counts, cases, properties, LABELS, adjacent)
    except CountError as exc:
        print(f'test-counts: {exc}', file=sys.stderr)
        return 2
    summary = report(counts, cases, properties, contract_total, adjacent)
    if args.write:
        status.write_text(rendered)
        (args.root / REPORT).write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
        print(json.dumps({'written': STATUS, 'contract_checks': contract_total,
                          'suite_tests': summary['suite_tests']}))
        return 0
    if rendered != current:
        print(f'test-counts: {STATUS} is stale; run: python tools/check_test_counts.py --write',
              file=sys.stderr)
        sys.stderr.writelines(difflib.unified_diff(
            current.splitlines(keepends=True), rendered.splitlines(keepends=True),
            fromfile=f'{STATUS} (recorded)', tofile=f'{STATUS} (loaded suites)', n=1))
        return 1
    print(json.dumps({'consistent': True, 'contract_checks': contract_total,
                      'suite_tests': summary['suite_tests']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
