"""Verify authored terminology grouping through actual Rust and mixed temporal matching."""
import json
from pathlib import Path
from . import reviewed_record_mappings as r

EXAMPLE = r.ei.ROOT / 'examples/reviewed-record-mappings'
EXTRA = ('interval-store', 'interval-policy', 'measurement-store', 'measurement-policy', 'alignment', 'query')


def verify():
    values = {p.stem: json.loads(p.read_text()) for p in sorted(EXAMPLE.glob('*.json'))}
    result = r.execute(*(values[n] for n in r.NAMES + EXTRA))
    query = result['query_result']
    r.ei.require(result['status'] == 'COMPLETED_RECORD_QUERY', 'MAPPING_DEMO_FAILED')
    r.ei.require(query['certain_patient_ids'] == query['possible_patient_ids'] == ['P1', 'P2', 'P3'], 'MAPPING_DEMO_MEMBERSHIP')
    control = r.mixed.execute(values['interval-store'], values['control-interval-policy'], values['control-semantic-policy'],
                              values['measurement-store'], values['measurement-policy'], values['alignment'], values['query'])
    r.ei.require(control['status'] == 'COMPLETED_RECORD_QUERY' and control['certain_patient_ids'] == [], 'UNMAPPED_CONTROL_FAILED')
    target = values['query']['treatment_class_iri']
    r.ei.require(all(f['class_iri'] != target for c in values['interval-store']['claims'] for f in c['bundle']['semantic_facts']), 'TARGET_MUST_BE_DERIVED')
    r.ei.require(all(b['treatment']['patient_role'] and b['treatment']['patient_bearer'] and
                    b['treatment']['semantic_support']['status'] == 'ENTAILED' for b in query['baseline_bindings']), 'MISSING_PRO_OR_SEMANTIC_SUPPORT')
    return {'profile': 'reviewed-record-mapping-verification-1.0', 'status': 'VERIFIED',
            'synthetic_only': True, 'public_demo_mapping_executed': False, 'clinical_mapping_verified': False,
            'verifier_sha256': r.ei.digest(Path(__file__).read_text()),
            'fixture_sha256': {str(p.relative_to(r.ei.ROOT)): r.ei.digest(p.read_text()) for p in sorted(EXAMPLE.glob('*.json'))},
            'mapping_context': result['mapping']['context'], 'mapping_context_id': result['mapping']['context_id'],
            'rule_evidence': result['mapping']['rule_evidence'],
            'query_context': query['context'], 'query_context_id': query['context_id'],
            'certain_synthetic_patient_ids': query['certain_patient_ids'],
            'control_certain_synthetic_patient_ids': control['certain_patient_ids'],
            'record_selector_derived_not_asserted': True, 'pro_witnesses_preserved': True,
            'rust_semantic_status': query['interval_view']['semantic_run']['status'],
            'followup_statuses': [b['followup_status'] for b in query['baseline_bindings']],
            'scope': 'Authored record-class implications; no clinical vocabulary equivalence, occurrence inference or identity verification'}


if __name__ == '__main__':
    report = verify()
    (r.ei.ROOT / 'verification/reviewed-record-mappings-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'synthetic_patients': report['certain_synthetic_patient_ids']}))
