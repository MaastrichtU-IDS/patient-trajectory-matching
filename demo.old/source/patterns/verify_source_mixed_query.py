"""Reproduce the fabricated CSV-to-claims-to-query SQL comparison; no patient data."""
import json
from pathlib import Path
from . import source_mixed_query as pipeline, exact_intervals as ei


def verify():
    folder = ei.ROOT / 'examples/source-mixed-query'
    result = pipeline.run(folder, json.loads((folder / 'request.json').read_text()),
                          json.loads((folder / 'synthetic-review.json').read_text()))
    ei.require(result['status'] == 'COMPLETED_VERIFIED_SOURCE_QUERY', 'SYNTHETIC_SOURCE_QUERY_FAILED')
    return {'profile': 'source-mixed-query-verification-1.0', 'synthetic_only': True,
        'patient_data_included': False, 'public_demo_mixed_query_analyzed': False,
        'verifier_sha256': ei.digest(Path(__file__).read_text()),
        'fixture_sha256': {str(p.relative_to(ei.ROOT)): ei.digest(p.read_text()) for p in sorted(folder.iterdir())},
        'context': result['context'], 'context_id': result['context_id'], 'status': result['status'],
        'certain_synthetic_patient_ids': result['certain_patient_ids'], 'eligible_synthetic_stays': result['eligible_stays'],
        'selected_claim_counts': result['selected_claim_counts'],
        'clinical_mapping_verified': False, 'physical_elapsed_time_verified': False, 'causal_effect_estimated': False,
        'source_history_verified': False, 'full_mixed_owl_reasoning_verified': False,
        'imports': {k: {f: result[k]['summary'][f] for f in ('input_rows', 'import_outcome_counts',
            'reconciliation_complete', 'description_complete_over_selected_records')} for k in ('interval_import', 'measurement_import')},
        'episodes': [{'patient_id': e['patient_id'], 'episode_id': e['episode_id'], 'status': e['status'],
            'comparison_passed': e['comparison_passed'], 'bindings': e['reference_bindings']} for e in result['episodes']]}


if __name__ == '__main__':
    report = verify()
    (ei.ROOT / 'verification/source-mixed-query-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'certain_synthetic_patient_ids': report['certain_synthetic_patient_ids']}))
