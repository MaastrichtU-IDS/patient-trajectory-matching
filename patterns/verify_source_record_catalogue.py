"""Reproduce an authored source-to-mapping query and its literal-source SQL control."""
import json
from pathlib import Path
from . import source_record_catalogue as s, source_mixed_query as mixed

EXAMPLE = s.ei.ROOT / 'examples/source-record-catalogue'
SOURCE = s.ei.ROOT / 'examples/source-mixed-query'


def load_example():
    values = {p.stem: json.loads(p.read_text()) for p in EXAMPLE.glob('*.json')}
    prepared = mixed.prepare(SOURCE, json.loads((SOURCE / 'request.json').read_text()))
    return values, prepared


def execute_episode(values, prepared, index):
    ie = prepared['interval_import']['episodes'][index]
    me = prepared['measurement_import']['episodes'][index]
    review = values['source-review']['episodes'][index]
    query = {**prepared['context']['request']['query'],
             'treatment_class_iri': values['terminology']['terms'][0]['record_class_iri']}
    return s.execute(SOURCE, values['request'], *(values[n] for n in s.mappings.NAMES),
                     ie['store'], review['interval_policy'], me['store'], review['measurement_policy'],
                     review['alignment'], query)


def verify():
    values, prepared = load_example()
    mixed.validate(values['source-review'], 'review')
    s.ei.require(values['source-review']['preparation_context_id'] == prepared['context_id'], 'STALE_SYNTHETIC_SOURCE_REVIEW')
    literal = mixed.evaluate(prepared, json.loads((SOURCE / 'synthetic-review.json').read_text()), SOURCE)
    s.ei.require(literal['status'] == 'COMPLETED_VERIFIED_SOURCE_QUERY', 'LITERAL_CONTROL_FAILED')
    patients = set(); audits = []; mapping_contexts = []; checked_bindings = 0
    for index, original in enumerate(literal['episodes']):
        ie = prepared['interval_import']['episodes'][index]
        me = prepared['measurement_import']['episodes'][index]
        if ie['store'] is None or me['store'] is None:
            s.ei.require(original['reference_bindings'] == [], 'EMPTY_EPISODE_CONTROL_FAILED'); continue
        result = execute_episode(values, prepared, index)
        s.ei.require(result['status'] == 'COMPLETED_RECORD_QUERY', 'SOURCE_MAPPING_EXECUTION_FAILED')
        query = result['query_result']; bindings = mixed.graph_bindings(query)
        s.ei.require(bindings == original['reference_bindings'], 'SOURCE_MAPPING_SQL_DISAGREEMENT')
        s.ei.require(query['interval_view']['semantic_run']['status'] == 'READY', 'RUST_REQUIRED')
        s.ei.require(all(b['treatment']['patient_role'] and b['treatment']['patient_bearer'] for b in query['baseline_bindings']), 'PRO_REQUIRED')
        patients.update(query['certain_patient_ids']); checked_bindings += len(bindings)
        audits.append(result['source_audit']['context_id']); mapping_contexts.append(result['mapping']['context_id'])
    s.ei.require(sorted(patients) == literal['certain_patient_ids'] == ['1', '2', '3'], 'SOURCE_MAPPING_MEMBERSHIP')
    artifacts = {p: s.ei.digest((s.ei.ROOT / p).read_text()) for p in set(s.FILES + mixed.FILES + ('patterns/verify_source_record_catalogue.py',))}
    fixture_paths = list(EXAMPLE.glob('*.json')) + list(SOURCE.glob('*.json')) + list(SOURCE.glob('*.csv'))
    return {'profile': 'source-record-catalogue-verification-1.0', 'status': 'VERIFIED',
            'synthetic_only': True, 'clinical_mapping_verified': False, 'public_demo_mapping_executed': False,
            'artifacts': dict(sorted(artifacts.items())),
            'fixture_sha256': {str(p.relative_to(s.ei.ROOT)): s.ei.digest(p.read_text()) for p in sorted(fixture_paths)},
            'source_audit_context_ids': audits, 'mapping_context_ids': mapping_contexts,
            'certain_synthetic_patient_ids': sorted(patients), 'sql_binding_rows_checked': checked_bindings,
            'source_roster_stays': len(literal['episodes']), 'all_sql_bindings_equal': True,
            'pro_witnesses_preserved': True, 'rust_semantic_status': 'READY'}


if __name__ == '__main__':
    report = verify()
    (s.ei.ROOT / 'verification/source-record-catalogue-synthetic-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'synthetic_patients': report['certain_synthetic_patient_ids']}))
