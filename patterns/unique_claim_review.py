"""Inspect each exact source claim once and compile explicit review to all partition batches."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import zlib
from jsonschema import Draft202012Validator
from . import partitioned_window_query as p

PROFILE = 'unique-claim-review-package-1.0'
REVIEW_PROFILE = 'unique-claim-review-1.0'
SCHEMA = p.ei.ROOT / 'schemas/unique-claim-review.schema.json'
MAX_OUTPUT_BYTES = 128 * 1024 * 1024
FILES = tuple(sorted(set(p.FILES + ('patterns/unique_claim_review.py', 'schemas/unique-claim-review.schema.json'))))


def prepare(selection, planned):
    """Internal source handoff only. Compilation always regenerates this package."""
    p.ei.require(planned['status'] == 'PLANNED_PENDING_REVIEW', 'INCOMPLETE_REVIEW_PLAN')
    claims = {}; calendars = {}; templates = []
    manifests = {f['table']: f for f in selection['summary']['context']['source_files']}
    for descriptor in planned['batches']:
        batch = p.build_batch(selection, planned, descriptor)
        template = {k: deepcopy(batch[k]) for k in ('batch_id', 'interval_policy', 'measurement_policy', 'alignment')}
        template['claim_ids'] = {}
        for kind in ('interval', 'measurement'):
            store = batch[kind + '_store']; template['claim_ids'][kind] = []
            if store is None: continue
            for claim in store['claims']:
                cid = claim['id']; template['claim_ids'][kind].append(cid)
                if cid not in claims:
                    if kind == 'interval':
                        source_record = deepcopy(selection['interval_segments'][descriptor['anchor_id']])
                        evidence = source_record['source']
                    else:
                        number = int(claim['source_record_id'].rsplit('_', 1)[1])
                        source_record = deepcopy(selection['measurement_records'][str(number)])
                        evidence = p.windows.source.evidence('chartevents', number, source_record, manifests['chartevents'])
                    claims[cid] = {'claim_id': cid, 'claim_sha256': p.cr.digest(claim), 'kind': kind,
                        'claim': deepcopy(claim), 'source_record': source_record, 'source_evidence': evidence,
                        'clock': deepcopy(store['clocks'][0]), 'batch_ids': [], 'anchor_ids': []}
                entry = claims[cid]
                p.ei.require(entry['claim_sha256'] == p.cr.digest(claim) and entry['clock'] == store['clocks'][0],
                             'INCONSISTENT_PACKAGE_CLAIM')
                entry['batch_ids'].append(descriptor['id'])
                if descriptor['anchor_id'] not in entry['anchor_ids']: entry['anchor_ids'].append(descriptor['anchor_id'])
        if batch['measurement_store'] is not None:
            patient = descriptor['patient_id']
            clocks = {kind + '_clock': deepcopy(batch[kind + '_store']['clocks'][0]) for kind in ('interval', 'measurement')}
            if patient not in calendars:
                calendars[patient] = {'patient_id': patient, **clocks, 'batch_ids': []}
            p.ei.require(all(calendars[patient][k] == v for k, v in clocks.items()), 'INCONSISTENT_PACKAGE_CLOCK')
            calendars[patient]['batch_ids'].append(descriptor['id'])
        templates.append(template)
    context = {'profile': PROFILE, 'plan_context_id': planned['context_id'], 'plan_context': planned['context'],
        'source_selection_summary': deepcopy(selection['summary']), 'partition_plan_summary': deepcopy(planned['summary']),
        'claims': [claims[k] for k in sorted(claims)], 'calendars': [calendars[k] for k in sorted(calendars)],
        'batch_templates': templates, 'artifacts': {f: p.ei.digest((p.ei.ROOT/f).read_text()) for f in FILES}}
    occurrences = sum(len(c['batch_ids']) for c in claims.values())
    summary = {'unique_claims': len(claims), 'unique_claims_by_kind': dict(sorted(Counter(c['kind'] for c in claims.values()).items())),
        'batch_claim_occurrences': occurrences, 'repeated_occurrences_saved': occurrences - len(claims),
        'calendar_reviews': len(calendars), 'batches': len(templates), 'anchors': len(planned['anchors']),
        'stays': len(selection['roster']), 'accepted_claims': 0, 'mixed_query_executed': False,
        'clinical_mapping_verified': False, 'cohort_membership': None}
    return {'profile': PROFILE, 'context': context, 'context_id': p.cr.digest(context), 'summary': summary}


def review_template(package):
    return {'profile': REVIEW_PROFILE, 'package_context_id': package['context_id'], 'reviewer': None,
        'claims': [{'claim_id': c['claim_id'], 'claim_sha256': c['claim_sha256'], 'decisions': []}
                   for c in package['context']['claims']],
        'calendars': [{'patient_id': c['patient_id'], 'decision': 'pending', 'reason': None}
                      for c in package['context']['calendars']]}


def _compile(selection, planned, package, review):
    # Private handoff: package must be the fresh result of prepare(), never externally supplied.
    p.ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(review)), 'INVALID_UNIQUE_REVIEW')
    p.ei.require(review['package_context_id'] == package['context_id'], 'STALE_UNIQUE_REVIEW')
    p.ei.require([(c['claim_id'], c['claim_sha256']) for c in review['claims']] ==
                 [(c['claim_id'], c['claim_sha256']) for c in package['context']['claims']], 'UNIQUE_REVIEW_CLAIM_COVERAGE')
    p.ei.require([c['patient_id'] for c in review['calendars']] ==
                 [c['patient_id'] for c in package['context']['calendars']], 'UNIQUE_REVIEW_CALENDAR_COVERAGE')
    acted = any(c['decisions'] for c in review['claims']) or any(c['decision'] != 'pending' for c in review['calendars'])
    p.ei.require(not acted or (review['reviewer'] is not None and review['reviewer'].strip()), 'MISSING_REVIEWER')
    decisions = {}; ids = set()
    for claim in review['claims']:
        own = []
        for d in claim['decisions']:
            p.ei.require(d['id'] not in ids, 'DUPLICATE_UNIQUE_DECISION_ID'); ids.add(d['id'])
            p.ei.require(d['reason'].strip(), 'EMPTY_REVIEW_REASON')
            own.append({**deepcopy(d), 'claim_id': claim['claim_id'], 'claim_sha256': claim['claim_sha256']})
        own_ids = {d['id'] for d in own}
        p.ei.require(all(d['supersedes'] is None or d['supersedes'] in own_ids for d in own), 'CROSS_CLAIM_REVIEW_PARENT')
        decisions[claim['claim_id']] = own
    calendars = {c['patient_id']: c for c in review['calendars']}
    for c in calendars.values():
        p.ei.require((c['decision'] == 'pending' and c['reason'] is None) or
                     (c['decision'] != 'pending' and c['reason'] is not None and c['reason'].strip()), 'INVALID_CALENDAR_REASON')
    review_id = p.cr.digest(review)
    compiled = {'profile': p.REVIEW_PROFILE, 'plan_context_id': planned['context_id'], 'batches': []}
    for descriptor, template in zip(planned['batches'], package['context']['batch_templates']):
        b = {k: deepcopy(template[k]) for k in ('batch_id', 'interval_policy', 'measurement_policy', 'alignment')}
        for kind in ('interval', 'measurement'):
            policy = b[kind + '_policy']
            if policy is not None:
                policy['decisions'] = [deepcopy(d) for cid in template['claim_ids'][kind] for d in decisions[cid]]
                policy['id'] = 'unique_' + p.cr.digest([package['context_id'], review_id, b['batch_id'], kind])
        if b['alignment'] is not None:
            c = calendars[descriptor['patient_id']]
            if c['decision'] == 'same_patient_calendar':
                patient = c['patient_id']
                b['alignment']['bindings'] = [{'patient_id': patient, 'interval_clock_id': 'patient_' + patient,
                    'measurement_clock_id': 'patient_' + patient, 'basis': 'same_dataset_patient_calendar', 'reason': c['reason']}]
        compiled['batches'].append(b)
    p.ei.require(len(p.cr.canonical(compiled).encode('utf-8')) <= p.MAX_REVIEW_BYTES, 'COMPILED_REVIEW_SIZE_LIMIT')
    # Rebuild every store and validate the existing policies, lifecycle, alignment and shared selection before release.
    _, states = p._review(selection, planned, compiled)
    parents = {d['supersedes'] for ds in decisions.values() for d in ds}
    outcomes = Counter()
    for cid, ds in decisions.items():
        leaves = [d for d in ds if d['id'] not in parents]
        outcomes[leaves[0]['action'] if leaves else 'pending'] += 1
    complete = outcomes['pending'] == 0 and all(c['decision'] != 'pending' for c in calendars.values())
    return {'profile': 'compiled-unique-claim-review-1.0', 'package_context_id': package['context_id'],
        'review': deepcopy(review), 'review_sha256': review_id, 'compiled_review': compiled,
        'compiled_review_sha256': p.cr.digest(compiled), 'status': 'REVIEW_COMPLETE' if complete else 'PENDING_REVIEW',
        'summary': {'review_complete': complete, 'unique_claim_outcomes': dict(sorted(outcomes.items())),
            'calendar_outcomes': dict(sorted(Counter(c['decision'] for c in calendars.values()).items())),
            'unique_accepted_claims': sum(v[1] for v in states.values()), 'batches_validated': len(compiled['batches']),
            'mixed_query_executed': False, 'clinical_mapping_verified': False, 'reviewer_identity_verified': False}}


def compile_review(selection, planned, review):
    return _compile(selection, planned, prepare(selection, planned), review)


def run(folder, request, review=None, *, execute=False):
    p.ei.require(not execute or review is not None, 'EXECUTION_REQUIRES_UNIQUE_REVIEW')
    selection = p.windows.run(folder, request); planned = p.plan(selection); package = prepare(selection, planned)
    if review is None: return {'package': package, 'review_template': review_template(package)}
    compiled = _compile(selection, planned, package, review)
    result = {'package': package, 'compilation': compiled}
    if execute:
        p.ei.require(compiled['summary']['review_complete'], 'UNFINISHED_UNIQUE_REVIEW')
        result['execution'] = p.execute(selection, planned, compiled['compiled_review'])
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=p.ei.ROOT/'examples/source-mixed-query')
    parser.add_argument('--request', type=Path, default=p.ei.ROOT/'examples/indexed-source-windows/synthetic-request.json')
    parser.add_argument('--review', type=Path)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--output', type=Path, default=p.ei.ROOT/'verification/unique-claim-review-run/result.json')
    args = parser.parse_args(argv); temporary = None
    try:
        protected = {f.resolve() for f in [*p.windows.inputs.source_paths(args.input_dir).values(),
            *p.windows.measurements.paths_for(args.input_dir).values(), args.request] + ([args.review] if args.review else [])}
        p.ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        p.ei.require(args.request.stat().st_size <= p.cr.MAX_BYTES, 'REQUEST_SIZE_LIMIT')
        if args.review: p.ei.require(args.review.stat().st_size <= p.MAX_REVIEW_BYTES, 'UNIQUE_REVIEW_SIZE_LIMIT')
        result = run(args.input_dir, json.loads(args.request.read_text()),
                     json.loads(args.review.read_text()) if args.review else None, execute=args.execute)
        serialized = json.dumps(result, indent=2) + '\n'
        p.ei.require(len(serialized.encode('utf-8')) <= MAX_OUTPUT_BYTES, 'REVIEW_OUTPUT_SIZE_LIMIT')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); handle.write(serialized)
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, sqlite3.Error, RecursionError) as error:
        print(json.dumps({'status': 'INVALID_UNIQUE_REVIEW_INPUT', 'reason': str(error)}), file=sys.stderr); return 2
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    status = result.get('execution', {}).get('status', result.get('compilation', {}).get('status', 'PENDING_REVIEW'))
    print(json.dumps({'status': status}))
    return 0 if status in ('PENDING_REVIEW', 'REVIEW_COMPLETE', 'COMPLETED_PARTITIONED_QUERY') else 2


if __name__ == '__main__': raise SystemExit(main())
