"""Describe fixed recorded-query selectors using the existing reviewed Rust plan.

The HTTP choice is a configured service profile and stratum. No client ontology,
class IRI, item list, or mapping decision is admitted by this adapter.
"""
from copy import deepcopy

from patterns import mapped_pressure_session as mapped


def describe_selector(service, stratum, mode):
    """Return checked selection metadata; a blocked review never becomes literal.

    Metadata is not source admission for a job. The selected pressure service
    independently audits sources, reviews, and implementation before executing
    and before returning any membership, including on cached queries.
    """
    if mode not in ('literal', 'reviewed'):
        raise ValueError('UNKNOWN_RECORDED_SELECTOR_MODE')
    if not isinstance(stratum, str) or stratum not in service.metadata()['strata']:
        raise ValueError('UNKNOWN_RECORDED_SELECTOR_STRATUM')
    if (mode == 'reviewed') != hasattr(service, 'mapping_dir'):
        raise ValueError('RECORDED_SELECTOR_SERVICE_MISMATCH')
    service._check_implementation()
    request, declaration = service._inputs(stratum)
    mapped.pressure.p.windows.validate(request)
    query = request['query']
    baseline, followup = query['baseline'], query['followup']
    item_ids = baseline['item_ids']
    mapped.ei.require(len(item_ids) == 1 and followup['item_ids'] == item_ids,
                      'RECORDED_SELECTOR_REQUIRES_ONE_ITEM')
    mapped.ei.require(baseline['unit_lexical'] == followup['unit_lexical'],
                      'RECORDED_SELECTOR_UNIT_MISMATCH')
    value = {
        'mode': mode, 'label': 'Source measurement item ' + item_ids[0],
        'source_item_ids': deepcopy(item_ids), 'unit_lexical': baseline['unit_lexical'],
        'treatment_class_iri': query['treatment_class_iri'],
        'class_iri': None, 'concept_iri': None, 'mapping_context_id': None,
        'review_evidence': [], 'semantic_check': None,
        'clinical_mapping_verified': False,
        'unit_conversion_performed': False, 'cross_item_value_comparison_performed': False,
        'source_admission': 'rechecked_by_selected_service_before_job_results',
    }
    if mode == 'reviewed':
        pack, stamps = mapped.load_pack(service.mapping_dir)
        catalogue = mapped.source.build(service.folder, pack['request'])
        mapped.ei.require(pack['catalogue'] == catalogue['catalogue'],
                          'MAPPED_PRESSURE_SOURCE_CATALOGUE_MISMATCH')
        mapped.ei.require(pack['request']['dataset_id'] == request['dataset_id'],
                          'MAPPED_PRESSURE_DATASET_MISMATCH')
        plan = mapped.source.mappings.plan(
            *(pack[name] for name in mapped.source.mappings.mappings.NAMES), pack['selector'])
        mapped.ei.require(plan['status'] == 'READY_MEASUREMENT_SELECTOR',
                          'RECORDED_SELECTOR_BLOCKED:' + plan['status'])
        selector = pack['selector']
        mapped.ei.require(selector['source_item_ids'] == item_ids == plan['supported_item_ids'],
                          'RECORDED_SELECTOR_ITEM_SCOPE_MISMATCH')
        mapped.ei.require(selector['unit_lexical'] == baseline['unit_lexical'],
                          'RECORDED_SELECTOR_UNIT_MISMATCH')
        term = next(row for row in pack['terminology']['terms']
                    if row['record_class_iri'] == selector['class_iri'])
        entry = next(row for row in pack['catalogue']['entries'] if row['code'] == item_ids[0])
        checked = plan['semantic_run']
        value.update(
            label=term['label'] + ' · ' + entry['label'] + ' · ' + item_ids[0],
            class_iri=selector['class_iri'], concept_iri=term['concept_iri'],
            mapping_context_id=plan['mapping']['context_id'],
            catalogue_context_id=catalogue['context_id'], mapping_files=deepcopy(stamps),
            review_evidence=[deepcopy(row['mapping_evidence']) for row in plan['item_outcomes']],
            semantic_check={
                'status': checked['status'],
                'backend': deepcopy(checked['backend_result']['backend']),
                'ontology_sha256': checked['ontology_sha256'],
                'scope': 'synthetic_catalogue_witnesses_not_patient_measurement_graph',
                'supported_item_ids': deepcopy(plan['supported_item_ids']),
            },
        )
        mapped.ei.require(mapped.load_pack(service.mapping_dir)[1] == stamps,
                          'MAPPING_REVIEW_CHANGED_RESTART_SESSION')
    service._check_implementation()
    mapped.ei.require(service._inputs(stratum) == (request, declaration),
                      'REVIEW_CHANGED_RESTART_SESSION')
    return value
