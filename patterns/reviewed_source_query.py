"""Execute an explicitly declared source-fidelity review and publish aggregate query evidence."""
import argparse
import csv
import sqlite3
import zlib
from collections import Counter
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
from jsonschema import Draft202012Validator
from . import source_fidelity_audit as audit

u = audit.u
p = audit.p
SCHEMA = p.ei.ROOT/'schemas/source-fidelity-review-declaration.schema.json'
FILES = tuple(sorted(set(audit.FILES + ('patterns/reviewed_source_query.py', 'schemas/source-fidelity-review-declaration.schema.json'))))


def prepare(folder, request):
    selection = p.windows.run(folder, request); planned = p.plan(selection); package = u.prepare(selection, planned)
    checked = audit.audit(folder, package)
    return selection, planned, package, checked


def materialize_review(package, checked, declaration):
    """Explicit bounded review rule only; an audit by itself never creates acceptance or alignment."""
    p.ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(declaration)), 'INVALID_FIDELITY_DECLARATION')
    p.ei.require(package['context_id'] == p.cr.digest(package['context']) and checked['context_id'] == p.cr.digest(checked['context']), 'INVALID_FIDELITY_CONTEXT_HASH')
    p.ei.require(declaration['package_context_id'] == package['context_id'] and declaration['audit_context_id'] == checked['context_id'], 'STALE_FIDELITY_DECLARATION')
    p.ei.require(checked['context']['package_context_id'] == package['context_id'] and checked['status'] == 'VERIFIED_SOURCE_FIDELITY' and
        checked['summary']['claims_verified'] == package['summary']['unique_claims'] and checked['summary']['claims_failed'] == 0, 'FIDELITY_REVIEW_FAILED')
    p.ei.require(declaration['unique_claims'] == package['summary']['unique_claims'] and
        declaration['calendar_reviews'] == package['summary']['calendar_reviews'], 'DECLARED_REVIEW_COVERAGE_MISMATCH')
    p.ei.require(all(declaration[k].strip() for k in ('reviewer','record_reason','calendar_reason')), 'EMPTY_DECLARATION_REASON')
    review = u.review_template(package); review['reviewer'] = declaration['reviewer']
    did = p.cr.digest(declaration)
    for c in review['claims']:
        c['decisions'] = [{'id': 'fidelity_' + p.cr.digest([did,c['claim_id']]), 'action': 'accept', 'supersedes': None,
            'reason': declaration['record_reason']}]
    for c in review['calendars']:
        c.update(decision='same_patient_calendar', reason=declaration['calendar_reason'])
    return review


def aggregate(package, checked, declaration, compiled, result):
    complete = result['status'] == 'COMPLETED_PARTITIONED_QUERY' and result['search_complete_over_selected_records']
    bindings = result['bindings'] if complete else None
    metrics = None
    if complete:
        baseline_pairs = {tuple(r[:4]) for r in bindings}
        followups = [r for r in bindings if r[4] is not None]
        signs = Counter('positive' if Decimal(r[5]) > 0 else 'negative' if Decimal(r[5]) < 0 else 'zero'
                        for r in followups if r[5] is not None)
        metrics = {'matched_patients': len(result['certain_patient_ids']),
            'eligible_treatment_baseline_pairs': len(baseline_pairs), 'followup_bindings': len(followups),
            'eligible_pairs_without_selected_followup': sum(r[4] is None for r in bindings),
            'delta_signs_per_binding': dict(sorted(signs.items()))}
    query = package['context']['plan_context']['selection_context']['request']
    return {'profile': 'reviewed-source-query-verification-1.0', 'request': query,
        'package_context_id': package['context_id'], 'audit_context_id': checked['context_id'],
        'review_declaration': deepcopy(declaration), 'review_declaration_sha256': p.cr.digest(declaration),
        'unique_review_sha256': compiled['review_sha256'], 'compiled_review_sha256': compiled['compiled_review_sha256'],
        'execution_context_id': result['context_id'], 'artifacts': {f: p.ei.digest((p.ei.ROOT/f).read_text()) for f in FILES},
        'source_selection_summary': package['context']['source_selection_summary'], 'partition_plan_summary': package['context']['partition_plan_summary'],
        'package_summary': package['summary'], 'audit_summary': checked['summary'], 'review_summary': compiled['summary'],
        'status': result['status'], 'batch_outcomes': result['batch_outcomes'], 'anchor_outcomes': result['anchor_outcomes'],
        'stay_outcomes': dict(sorted(Counter(s['status'] for s in result['roster']).items())),
        'anchors_verified_against_unpartitioned_sql': sum(a['status'].startswith('VERIFIED_') for a in result['anchors']),
        'search_complete_over_selected_records': complete, 'metrics': metrics,
        'real_source_mixed_query_executed': query['dataset_id'] == 'mimic-iv-demo-2.2',
        'mixed_query_executed': True, 'patient_rows_or_identifiers_included': False,
        'clinical_mapping_verified': False, 'reviewer_identity_verified': False,
        'source_history_verified': False, 'physical_elapsed_time_verified': False,
        'causal_effect_estimated': False, 'full_mixed_owl_reasoning_verified': False,
        'interpretation': 'Exact reviewed source-record query; binding counts are dependent observations, not independent treatment outcomes.'}


def execute_prepared(selection, planned, package, checked, declaration):
    review = materialize_review(package, checked, declaration)
    compiled = u._compile(selection, planned, package, review)
    p.ei.require(compiled['summary']['review_complete'], 'INCOMPLETE_DECLARED_REVIEW')
    result = p.execute(selection, planned, compiled['compiled_review'])
    return aggregate(package, checked, declaration, compiled, result), review, result


def run(folder, request, declaration):
    return execute_prepared(*prepare(folder,request), declaration)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir',type=Path,required=True);parser.add_argument('--request',type=Path,required=True)
    parser.add_argument('--declaration',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    try:
        protected={f.resolve() for f in [*p.windows.inputs.source_paths(args.input_dir).values(),*p.windows.measurements.paths_for(args.input_dir).values(),args.request,args.declaration]}
        p.ei.require(args.output.resolve() not in protected,'OUTPUT_OVERWRITES_INPUT')
        p.ei.require(args.request.stat().st_size <= p.cr.MAX_BYTES and args.declaration.stat().st_size <= p.cr.MAX_BYTES,'REVIEW_INPUT_SIZE_LIMIT')
        report,_,_=run(args.input_dir,json.loads(args.request.read_text()),json.loads(args.declaration.read_text()))
        # Only this aggregate report is written by the public CLI; detailed records stay in memory.
        import tempfile
        args.output.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=args.output.parent,delete=False) as handle:
            temporary=Path(handle.name)
            try:json.dump(report,handle,indent=2);handle.write('\n')
            except BaseException:temporary.unlink(missing_ok=True);raise
        try:temporary.replace(args.output)
        finally:temporary.unlink(missing_ok=True)
    except (ValueError,OSError,EOFError,csv.Error,zlib.error,sqlite3.Error,RecursionError) as error:
        print(json.dumps({'status':'INVALID_REVIEWED_SOURCE_INPUT','reason':str(error)}),file=sys.stderr);return 2
    print(json.dumps({'status':report['status'],'metrics':report['metrics']}))
    return 0 if report['status']=='COMPLETED_PARTITIONED_QUERY' else 2


if __name__=='__main__':raise SystemExit(main())
