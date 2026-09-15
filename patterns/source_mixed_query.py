"""Bounded source import, explicit record review, mixed query, and independent SQL comparison."""
import argparse
from collections import Counter
from copy import deepcopy
import csv
import json
from pathlib import Path
import sys
import tempfile
import zlib
from jsonschema import Draft202012Validator
from . import claim_projection as cp, claim_rdf as cr, exact_intervals as ei
from . import mimic_claim_import as inputs, mimic_measurement_import as measurements
from . import mimic_inputevents as source, measurement_claims as points
from . import mixed_record_query as mixed, source_mixed_reference as reference

PROFILE = 'source-mixed-query-1.0'
REVIEW_PROFILE = 'source-mixed-review-1.0'
SCHEMA = ei.ROOT / 'schemas/source-mixed-query.schema.json'
MAX_REVIEW_BYTES = 4 * 1024 * 1024
FILES = tuple(sorted(set(inputs.FILES + measurements.FILES + mixed.FILES + (
    'patterns/source_mixed_query.py', 'patterns/source_mixed_reference.py', 'schemas/source-mixed-query.schema.json'))))


def validate(document, definition):
    schema = json.loads(SCHEMA.read_text())
    validator = Draft202012Validator({**schema, '$ref': '#/$defs/' + definition})
    ei.require(not list(validator.iter_errors(document)), 'INVALID_SOURCE_MIXED_' + definition.upper())


def key(episode):
    return episode['patient_id'], episode['episode_id']


def prepare(folder, request):
    validate(request, 'request'); mixed.validate_query(request['query'])
    request = deepcopy(request)
    i = inputs.run(folder, request['interval_import'])
    m = measurements.run(folder, request['measurement_import'])
    ei.require(request['interval_import']['dataset_id'] == request['measurement_import']['dataset_id'], 'DATASET_MISMATCH')
    ei.require([key(e) for e in i['episodes']] == [key(e) for e in m['episodes']], 'ROSTER_MISMATCH')
    manifests = [{f['table']: f for f in r['summary']['context']['source_files']} for r in (i, m)]
    for table in ('icustays', 'd_items'):
        ei.require(manifests[0][table] == manifests[1][table], 'SHARED_SOURCE_CHANGED:' + table)
    treatment_item = request['query']['treatment_class_iri'].removeprefix(inputs.ITEM_NS)
    ei.require(request['query']['treatment_class_iri'] == inputs.ITEM_NS + treatment_item and
               treatment_item in request['interval_import']['itemids'], 'LITERAL_TREATMENT_ITEM_REQUIRED')
    selected_points = set(request['measurement_import']['itemids'])
    ei.require(set(request['query']['baseline']['item_ids'] + request['query']['followup']['item_ids']) <= selected_points,
               'MEASUREMENT_SELECTOR_NOT_IMPORTED')
    context = {'profile': PROFILE, 'request': request, 'interval_import_context_id': i['summary']['context_id'],
        'measurement_import_context_id': m['summary']['context_id'],
        'scope': 'explicitly_selected_exact_source_labels_within_icu_stay',
        'reference_scope': 'independent_sql_temporal_and_scalar_joins_shared_admission_and_policy_selection',
        'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}}
    context_id = cr.digest(context)
    reviews = []
    for ie, me in zip(i['episodes'], m['episodes']):
        alignment = None
        if ie['store'] is not None and me['store'] is not None:
            alignment = {'profile': 'patient-local-clock-alignment-1.0', 'id': 'alignment_' + ie['episode_id'],
                'interval_store_sha256': ie['store_sha256'], 'measurement_store_sha256': me['store_sha256'], 'bindings': []}
        reviews.append({'patient_id': ie['patient_id'], 'episode_id': ie['episode_id'],
            'interval_policy': deepcopy(ie['acceptance_policy']), 'measurement_policy': deepcopy(me['acceptance_policy']),
            'alignment': alignment})
    return {'status': 'PREPARED_PENDING_REVIEW', 'context': context, 'context_id': context_id,
        'interval_import': i, 'measurement_import': m,
        'review_template': {'profile': REVIEW_PROFILE, 'preparation_context_id': context_id, 'episodes': reviews}}


def selected(episode, policy, semantic_policy):
    if episode['store'] is None:
        ei.require(policy is None, 'POLICY_WITHOUT_STORE')
        return []
    ei.require(policy is not None, 'MISSING_STORE_POLICY')
    selection = cp._select_validated(episode['store'], policy, semantic_policy)
    ei.require(not selection['blockers'], 'SELECTED_SOURCE_CONFLICT')
    return selection['accepted_claim_ids']


def raw_rows(prepared, folder):
    """Read the same hashed CSV bytes independently of projected claim values."""
    result = {}
    paths = {**inputs.source_paths(folder), **measurements.paths_for(folder)}
    for table, imported in [('inputevents', prepared['interval_import']), ('chartevents', prepared['measurement_import'])]:
        rows, manifest = (source.read_table(paths[table], table) if table == 'inputevents'
                          else measurements.read_observations(paths[table]))
        expected = next(f for f in imported['summary']['context']['source_files'] if f['table'] == table)
        ei.require(manifest == expected, 'REFERENCE_SOURCE_CHANGED:' + table)
        result[table] = rows
    return result


def reference_rows(episode, selected_claims, rows, prefix):
    answer = []
    for cid in selected_claims:
        evidence = episode['claim_provenance'][cid]['source']
        number = evidence['record_number']
        answer.append({**rows[number - 1], 'id': prefix + evidence['csv_sha256'] + '_' + str(number)})
    return answer


def graph_bindings(result):
    """Normalize exact positive graph bindings; uncertainty cannot pass an exact reference gate."""
    rows = []
    for b in result['baseline_bindings']:
        ei.require(b['eligibility']['status'] in ('CERTAIN', 'IMPOSSIBLE'), 'NON_EXACT_OR_UNALIGNED_BASELINE')
        if b['eligibility']['status'] != 'CERTAIN': continue
        identity = [b['patient_id'], b['episode_id'], b['treatment']['event_id'], b['baseline_id']]
        found = []
        for f in b['followup']:
            ei.require(f['joint_binding']['status'] in ('CERTAIN', 'IMPOSSIBLE'), 'NON_EXACT_OR_UNALIGNED_FOLLOWUP')
            if f['joint_binding']['status'] == 'CERTAIN':
                change = f['value_change']
                found.append(identity + [f['measurement_id'], change['delta'], change.get('unit_lexical')])
        ei.require(b['followup_status'] == ('RECORDED_FOLLOWUP' if found else 'NO_SELECTED_FOLLOWUP_IN_WINDOW'),
                   'FOLLOWUP_STATUS_DISAGREEMENT')
        rows.extend(found or [identity + [None, None, None]])
    return sorted(rows, key=lambda row: tuple('' if v is None else v for v in row))


def evaluate(prepared, review, folder, *, timeout_seconds=20):
    """Internal handoff from prepare(); public run() always reimports and verifies review hashes."""
    validate(review, 'review')
    ei.require(review['preparation_context_id'] == prepared['context_id'], 'STALE_PREPARATION_REVIEW')
    i, m = prepared['interval_import'], prepared['measurement_import']
    ei.require([key(e) for e in review['episodes']] == [key(e) for e in i['episodes']], 'REVIEW_ROSTER_MISMATCH')
    context = {'profile': PROFILE, 'preparation_context': prepared['context'], 'preparation_context_id': prepared['context_id'],
               'review': deepcopy(review), 'timeout_seconds': timeout_seconds}
    ei.require(type(timeout_seconds) in (int, float) and 0 < timeout_seconds <= 60, 'INVALID_TIMEOUT')
    result = {'profile': PROFILE, 'context': context, 'context_id': cr.digest(context),
        'interval_import': i, 'measurement_import': m, 'episodes': [], 'certain_patient_ids': None,
        'possible_patient_ids': None, 'eligible_stays': None, 'search_complete_over_selected_records': False,
        'clinical_mapping_verified': False, 'clinical_knowledge_status': 'UNKNOWN', 'causal_effect_estimated': False,
        'source_history_verified': False, 'physical_elapsed_time_verified': False, 'full_mixed_owl_reasoning_verified': False}
    raw = raw_rows(prepared, folder); query = prepared['context']['request']['query']
    treatment_item = query['treatment_class_iri'][len(inputs.ITEM_NS):]
    for ie, me, re in zip(i['episodes'], m['episodes'], review['episodes']):
        selected_i = selected(ie, re['interval_policy'], i['semantic_policy'])
        selected_m = selected(me, re['measurement_policy'], points.SEMANTIC_POLICY)
        episode = {'patient_id': ie['patient_id'], 'episode_id': ie['episode_id'],
            'selected_interval_claim_ids': selected_i, 'selected_measurement_claim_ids': selected_m,
            'mixed_result': None, 'reference_bindings': None, 'comparison_passed': False}
        result['episodes'].append(episode)
        if ie['store'] is not None and me['store'] is not None:
            ei.require(re['alignment'] is not None, 'MISSING_ALIGNMENT_DOCUMENT')
            mixed.validate_alignment(re['alignment'], ie['store'], me['store'])
        else:
            ei.require(re['alignment'] is None, 'ALIGNMENT_WITHOUT_STORES')
        if 'BLOCKED_RESOURCE_LIMIT' in (ie['status'], me['status']):
            episode.update(status='BLOCKED_IMPORT'); continue
        if ie['store'] is None or me['store'] is None:
            # Missing imported side has zero selected records; no clock or empty synthetic store is invented.
            observed = []
        else:
            run = mixed.execute(ie['store'], re['interval_policy'], i['semantic_policy'], me['store'],
                                re['measurement_policy'], re['alignment'], query, timeout_seconds=timeout_seconds)
            episode['mixed_result'] = run
            if run['status'] != 'COMPLETED_RECORD_QUERY':
                episode.update(status='BLOCKED_MIXED_QUERY'); continue
            try: observed = graph_bindings(run)
            except ei.ContractError as error:
                episode.update(status='BLOCKED_COMPARISON', reason=str(error)); continue
            eligible = sorted({r[0] for r in observed})
            if run['certain_patient_ids'] != eligible or run['possible_patient_ids'] != eligible:
                episode.update(status='BLOCKED_REFERENCE_DISAGREEMENT', reason='PATIENT_SET_DISAGREEMENT'); continue
        # Run the reference only after the mixed work bounds and backend gates pass.
        expected = reference.execute(reference_rows(ie, selected_i, raw['inputevents'], 'input_'),
            reference_rows(me, selected_m, raw['chartevents'], 'chart_'), treatment_item, query)
        episode['reference_bindings'] = expected
        if observed != expected:
            episode.update(status='BLOCKED_REFERENCE_DISAGREEMENT', observed_bindings=observed); continue
        episode.update(status='VERIFIED_RECORD_MATCH' if expected else 'VERIFIED_NO_SELECTED_RECORD_MATCH', comparison_passed=True)
    complete = (all(e['comparison_passed'] for e in result['episodes']) and
                all(r['summary']['description_complete_over_selected_records'] and r['summary']['reconciliation_complete'] for r in (i, m)))
    result.update(status='COMPLETED_VERIFIED_SOURCE_QUERY' if complete else 'BLOCKED_INCOMPLETE_SOURCE_QUERY',
        episode_outcome_counts=dict(sorted(Counter(e['status'] for e in result['episodes']).items())),
        search_complete_over_selected_records=complete,
        selected_claim_counts={'interval': sum(len(e['selected_interval_claim_ids']) for e in result['episodes']),
                               'measurement': sum(len(e['selected_measurement_claim_ids']) for e in result['episodes'])})
    if complete:
        stays = [{'patient_id': e['patient_id'], 'episode_id': e['episode_id']} for e in result['episodes'] if e['reference_bindings']]
        patients = sorted({e['patient_id'] for e in stays})
        result.update(certain_patient_ids=patients, possible_patient_ids=patients, eligible_stays=stays)
    return result


def run(folder, request, review=None, *, timeout_seconds=20):
    prepared = prepare(folder, request)
    return prepared if review is None else evaluate(prepared, review, folder, timeout_seconds=timeout_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    folder = ei.ROOT / 'examples/source-mixed-query'
    parser.add_argument('--input-dir', type=Path, default=folder)
    parser.add_argument('--request', type=Path, default=folder / 'request.json')
    parser.add_argument('--review', type=Path, help='Explicit review; omit to prepare pending claims and an undecided template')
    parser.add_argument('--output', type=Path, default=ei.ROOT / 'verification/source-mixed-query-run/result.json')
    args = parser.parse_args(argv); temporary = None
    try:
        protected = {p.resolve() for p in [*inputs.source_paths(args.input_dir).values(),
            *measurements.paths_for(args.input_dir).values(), args.request] + ([args.review] if args.review else [])}
        ei.require(args.output.resolve() not in protected, 'OUTPUT_OVERWRITES_INPUT')
        ei.require(args.request.stat().st_size <= cr.MAX_BYTES, 'REQUEST_SIZE_LIMIT')
        if args.review: ei.require(args.review.stat().st_size <= MAX_REVIEW_BYTES, 'REVIEW_SIZE_LIMIT')
        result = run(args.input_dir, json.loads(args.request.read_text()), json.loads(args.review.read_text()) if args.review else None)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, EOFError, csv.Error, zlib.error, RecursionError) as error:
        print(json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}), file=sys.stderr); return 2
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status': result['status'], 'certain_patient_ids': result.get('certain_patient_ids')}))
    return 0 if result['status'] in ('PREPARED_PENDING_REVIEW', 'COMPLETED_VERIFIED_SOURCE_QUERY') else 2


if __name__ == '__main__':
    raise SystemExit(main())
