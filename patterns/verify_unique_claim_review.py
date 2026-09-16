"""Verify complete pending review packages on pinned demo sources; publish aggregate counts only."""
import argparse
import json
from pathlib import Path
from . import unique_claim_review as u, verify_indexed_source_windows as prior


def verify(folder):
    strata = {}
    for name in prior.NAMES:
        request = json.loads((u.p.ei.ROOT/'examples/indexed-source-windows'/(name+'-request.json')).read_text())
        selection = u.p.windows.run(folder, request); prior.validate_summary(selection['summary'], name)
        planned = u.p.plan(selection); package = u.prepare(selection, planned)
        compiled = u._compile(selection, planned, package, u.review_template(package))
        summary = package['summary']
        u.p.ei.require(summary['unique_claims_by_kind'] == {'interval': len(planned['anchors']),
            'measurement': selection['summary']['unique_records_in_windows']}, 'UNIQUE_PACKAGE_SOURCE_COVERAGE')
        u.p.ei.require(compiled['status'] == 'PENDING_REVIEW' and compiled['summary']['unique_accepted_claims'] == 0,
                       'UNEXPECTED_DEMO_REVIEW_DECISION')
        u.p.ei.require(compiled['summary']['batches_validated'] == len(planned['batches']), 'INCOMPLETE_DEMO_REVIEW_VALIDATION')
        # Keep source identifiers, claim content and batch/anchor rosters out of committed evidence.
        strata[name] = {'source_selection_context': selection['summary']['context'],
            'selection_context_id': selection['summary']['context_id'], 'plan_context_id': planned['context_id'],
            'package_context_id': package['context_id'], 'compiled_review_sha256': compiled['compiled_review_sha256'],
            'package_summary': summary, 'compilation_summary': compiled['summary'], 'status': compiled['status']}
    return {'profile': 'unique-claim-review-verification-1.0',
        'source_pin_sha256': u.p.ei.digest(prior.PIN.read_text()), 'verifier_sha256': u.p.ei.digest(Path(__file__).read_text()),
        'artifacts': {f: u.p.ei.digest((u.p.ei.ROOT/f).read_text()) for f in u.FILES},
        'patient_rows_or_identifiers_included': False, 'real_source_claims_accepted': 0,
        'real_source_mixed_query_executed': False, 'scope': 'Complete unique claim packages and pending policy validation only.',
        'strata': strata}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--input-dir', type=Path, required=True)
    args = parser.parse_args(); result = verify(args.input_dir)
    (u.p.ei.ROOT/'verification/unique-claim-review-demo-report.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps({name: row['package_summary'] for name, row in result['strata'].items()}))
