"""Reproduce synthetic baseline eligibility and mixed recorded follow-up evidence."""
import json
from pathlib import Path
from . import mixed_record_query as mixed, exact_intervals as ei

NAMES = ('interval-store', 'interval-policy', 'semantic-policy', 'measurement-store', 'measurement-policy', 'alignment', 'query')


def verify():
    folder = ei.ROOT / 'examples/mixed-record-query'
    result = mixed.execute(*(json.loads((folder / (name + '.json')).read_text()) for name in NAMES))
    ei.require(result['status'] == 'COMPLETED_RECORD_QUERY', 'SYNTHETIC_MIXED_QUERY_FAILED')
    return {'profile': 'mixed-record-query-verification-1.0', 'synthetic_only': True,
        'patient_data_included': False, 'public_demo_mixed_query_analyzed': False,
        'verifier_sha256': ei.digest(Path(__file__).read_text()),
        'fixture_sha256': {str(p.relative_to(ei.ROOT)): ei.digest(p.read_text()) for p in sorted(folder.iterdir())},
        'status': result['status'], 'context': result['context'], 'context_id': result['context_id'],
        'certain_synthetic_patient_ids': result['certain_patient_ids'],
        'possible_synthetic_patient_ids': result['possible_patient_ids'],
        'interval_semantic_status': result['interval_view']['semantic_run']['status'],
        'interval_isolation_axioms': result['interval_view']['claim_isolation']['logical_axioms_checked'],
        'measurement_isolation_axioms': result['measurement_view']['isolation']['logical_axioms_checked'],
        'search_complete_over_selected_records': result['search_complete_over_selected_records'],
        'clinical_mapping_verified': False, 'physical_elapsed_time_verified': False,
        'causal_effect_estimated': False, 'full_mixed_owl_reasoning_verified': False,
        'bindings': [{'patient_id': b['patient_id'], 'baseline_id': b['baseline_id'],
            'eligibility': b['eligibility']['status'], 'followup_status': b['followup_status'],
            'clinical_response_status': b['clinical_response_status'],
            'followup': [{'measurement_id': f['measurement_id'], 'joint_status': f['joint_binding']['status'],
                         'value_change': f['value_change']} for f in b['followup']]} for b in result['baseline_bindings']]}


if __name__ == '__main__':
    report = verify()
    (ei.ROOT / 'verification/mixed-record-query-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'certain_synthetic_patient_ids': report['certain_synthetic_patient_ids']}))
