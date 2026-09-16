"""Reviewed measurement item selection, with checked class rules and separate strata."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import tempfile

from jsonschema import Draft202012Validator
from . import claim_rdf as cr, exact_intervals as ei, reviewed_record_mappings as mappings
from . import mixed_record_query as mixed, semantic_support as semantic

PROFILE = 'reviewed-measurement-mappings-1.0'
SELECTOR_PROFILE = 'reviewed-measurement-selector-1.0'
ITEM_NS = 'https://example.org/trajectory/measurement-record-item/'
PROBE_NS = 'https://example.org/trajectory/measurement-catalogue-probe/'
SCHEMA = ei.ROOT / 'schemas/reviewed-measurement-selector.schema.json'
FILES = tuple(sorted(set(mappings.FILES + mixed.FILES + (
    'patterns/reviewed_measurement_mappings.py', 'schemas/reviewed-measurement-selector.schema.json'))))
INPUTS = mappings.NAMES + ('selector', 'interval-store', 'interval-policy', 'semantic-policy',
                         'measurement-store', 'measurement-policy', 'alignment', 'query')


def plan(catalogue, terminology, proposals, review, selector, *, timeout_seconds=20):
    catalogue, terminology, proposals, review, selector = deepcopy((catalogue, terminology, proposals, review, selector))
    compiled = mappings.compile_policy(catalogue, terminology, proposals, review)
    result = {'status': 'BLOCKED_MAPPING_REVIEW', 'mapping': compiled, 'semantic_run': None,
              'item_outcomes': [], 'supported_item_ids': None}
    if compiled['semantic_policy'] is None: return result
    ei.require(len(cr.canonical(selector).encode()) <= cr.MAX_BYTES, 'MEASUREMENT_SELECTOR_LIMIT')
    ei.require(not list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(selector)),
               'INVALID_REVIEWED_MEASUREMENT_SELECTOR')
    semantic.iri(selector['class_iri'])
    ei.require(selector['mapping_context_id'] == compiled['context_id'], 'STALE_MEASUREMENT_MAPPING_SELECTOR')
    entries = {row['code']: row for row in catalogue['entries']}
    ei.require(set(selector['source_item_ids']) <= set(entries), 'UNKNOWN_MEASUREMENT_SOURCE_ITEM')
    targets = {row['record_class_iri'] for row in terminology['terms']}
    ei.require(selector['class_iri'] in targets, 'MEASUREMENT_REQUIRES_RECORD_SELECTOR')
    for code, row in entries.items():
        ei.require(row['source_class_iri'] == ITEM_NS + code, 'MEASUREMENT_ITEM_CLASS_MISMATCH')
    # Synthetic catalogue witnesses check the compiled implication rules. These are
    # not patient measurement individuals and are never added to a claim graph.
    policy = compiled['semantic_policy']
    model = {'classes': policy['classes'], 'rules': policy['rules'], 'disjoint': policy['disjoint'],
             'individuals': [PROBE_NS + code for code in sorted(entries)], 'property_assertions': [],
             'class_assertions': [{'individual': PROBE_NS + code, 'class': entries[code]['source_class_iri']}
                                  for code in sorted(entries)]}
    checked = semantic.check(model, [selector['class_iri']], timeout_seconds=timeout_seconds)
    result['semantic_run'] = checked
    if checked['status'] != 'READY':
        result['status'] = 'BLOCKED_MEASUREMENT_MAPPING_SEMANTICS'; return result
    supported = set(checked['memberships'][selector['class_iri']])
    evidence = {row['source']['code']: row for row in compiled['rule_evidence']}
    for code in sorted(selector['source_item_ids']):
        result['item_outcomes'].append({'item_id': code,
            'status': 'SUPPORTED_RECORD_SELECTOR' if PROBE_NS + code in supported else 'NOT_ENTAILED_BY_MAPPING',
            'catalogue_probe_iri': PROBE_NS + code, 'mapping_evidence': evidence[code]})
    result['supported_item_ids'] = [row['item_id'] for row in result['item_outcomes'] if row['status'] == 'SUPPORTED_RECORD_SELECTOR']
    result['status'] = 'READY_MEASUREMENT_SELECTOR'
    return result


def execute(catalogue, terminology, proposals, review, selector, interval_store, interval_policy,
            semantic_policy, measurement_store, measurement_policy, alignment, query, *, timeout_seconds=20):
    values = deepcopy((catalogue, terminology, proposals, review, selector, interval_store, interval_policy,
                       semantic_policy, measurement_store, measurement_policy, alignment, query))
    catalogue, terminology, proposals, review, selector, interval_store, interval_policy, semantic_policy, measurement_store, measurement_policy, alignment, query = values
    context = {'profile': PROFILE, 'input_sha256': {name: cr.digest(value) for name, value in zip(INPUTS, values)},
               'artifacts': {p: ei.digest((ei.ROOT / p).read_text()) for p in FILES}, 'timeout_seconds': timeout_seconds,
               'scope': 'reviewed_item_class_selection_then_separate_same_item_same_literal_unit_queries',
               'measurement_class_reasoning_scope': 'synthetic_catalogue_witnesses_not_patient_measurement_graph',
               'clinical_mapping_verified': False, 'catalogue_source_fidelity_verified': False,
               'cross_item_value_comparison_performed': False, 'unit_conversion_performed': False}
    result = {'profile': PROFILE, 'context': context, 'context_id': cr.digest(context), 'selection_plan': None,
              'strata': [], 'certain_patient_ids': None, 'possible_patient_ids': None,
              'membership_semantics': 'union_of_separate_item_query_memberships', 'search_complete_over_requested_strata': False}
    selected = plan(catalogue, terminology, proposals, review, selector, timeout_seconds=timeout_seconds)
    result['selection_plan'] = selected
    if selected['status'] != 'READY_MEASUREMENT_SELECTOR':
        result['status'] = selected['status']; return result
    mixed.validate_query(query)
    mixed.intervals.validate_store(interval_store); mixed.points.validate_store(measurement_store)
    ei.require(catalogue['dataset_id'] == measurement_store['dataset_id'], 'MEASUREMENT_CATALOGUE_DATASET_MISMATCH')
    mixed.validate_alignment(alignment, interval_store, measurement_store)
    # Both original policies must validate even when no catalogue item supports the selector.
    mixed.cp.validate_policy(interval_store, interval_policy, semantic_policy)
    mixed.cp.validate_policy(measurement_store, measurement_policy, mixed.points.SEMANTIC_POLICY)
    for side in ('baseline', 'followup'):
        ei.require(set(query[side]['item_ids']) == set(selector['source_item_ids']), 'MEASUREMENT_QUERY_SCOPE_MISMATCH')
        ei.require(query[side]['unit_lexical'] == selector['unit_lexical'], 'MEASUREMENT_QUERY_UNIT_MISMATCH')
    if not selected['supported_item_ids']:
        result['status'] = 'NO_SUPPORTED_MEASUREMENT_ITEMS'; return result
    certain, possible = set(), set()
    for item in selected['supported_item_ids']:
        item_query = deepcopy(query)
        for side in ('baseline', 'followup'): item_query[side]['item_ids'] = [item]
        run = mixed.execute(interval_store, interval_policy, semantic_policy, measurement_store,
                            measurement_policy, alignment, item_query, timeout_seconds=timeout_seconds)
        result['strata'].append({'item_id': item, 'unit_lexical': selector['unit_lexical'],
                                'query': item_query, 'result': run})
        if run['status'] == 'COMPLETED_RECORD_QUERY':
            certain.update(run['certain_patient_ids']); possible.update(run['possible_patient_ids'])
    if any(row['result']['status'] != 'COMPLETED_RECORD_QUERY' for row in result['strata']):
        result['status'] = 'BLOCKED_INCOMPLETE_MEASUREMENT_STRATA'; return result
    result.update(status='COMPLETED_REVIEWED_MEASUREMENT_QUERY', certain_patient_ids=sorted(certain),
                  possible_patient_ids=sorted(possible), search_complete_over_requested_strata=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    folder = ei.ROOT / 'examples/reviewed-measurement-mappings'
    for name in INPUTS: parser.add_argument('--' + name, type=Path, default=folder / (name + '.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv); temporary = None
    try:
        paths = [getattr(args, name.replace('-', '_')) for name in INPUTS]
        ei.require(args.output.resolve() not in {p.resolve() for p in paths}, 'OUTPUT_OVERWRITES_INPUT')
        for path in paths: ei.require(path.stat().st_size <= cr.MAX_BYTES, 'INPUT_FILE_LIMIT')
        result = execute(*(json.loads(path.read_text()) for path in paths))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=args.output.parent, delete=False) as handle:
            temporary = Path(handle.name); json.dump(result, handle, indent=2); handle.write('\n')
        temporary.replace(args.output)
    except (ValueError, OSError, RecursionError) as error:
        parser.exit(2, str(error) + '\n')
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    print(json.dumps({'status': result['status'], 'context_id': result['context_id']}))
    return 0 if result['status'] == 'COMPLETED_REVIEWED_MEASUREMENT_QUERY' else 2


if __name__ == '__main__': raise SystemExit(main())
