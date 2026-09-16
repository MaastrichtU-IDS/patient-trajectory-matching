"""Reproduce source-audited measurement selectors and raw-CSV SQL query controls."""
import csv
import json
from pathlib import Path
from . import measurement_source_catalogue as s, source_mixed_query as source

EXAMPLE = s.ei.ROOT / 'examples/measurement-source-catalogue'
SOURCE = s.ei.ROOT / 'examples/source-mixed-query'


def load_example():
    values = {p.stem: json.loads(p.read_text()) for p in EXAMPLE.glob('*.json')}
    prepared = source.prepare(SOURCE, json.loads((SOURCE / 'request.json').read_text()))
    values['source-review'] = json.loads((SOURCE / 'synthetic-review.json').read_text())
    return values, prepared


def execute_episode(values, prepared, index, folder=SOURCE):
    ie = prepared['interval_import']['episodes'][index]
    me = prepared['measurement_import']['episodes'][index]
    re = values['source-review']['episodes'][index]
    return s.execute(folder, values['request'], *(values[n] for n in s.mappings.mappings.NAMES),
                     values['selector'], ie['store'], re['interval_policy'],
                     prepared['interval_import']['semantic_policy'], me['store'],
                     re['measurement_policy'], re['alignment'], prepared['context']['request']['query'])


def verify():
    values, prepared = load_example()
    # This control rereads raw CSV values for SQL rather than using projected claims.
    literal = source.evaluate(prepared, values['source-review'], SOURCE)
    s.ei.require(literal['status'] == 'COMPLETED_VERIFIED_SOURCE_QUERY', 'LITERAL_SOURCE_CONTROL_FAILED')
    with (SOURCE / 'chartevents.csv').open(newline='') as handle: raw = list(csv.DictReader(handle))
    audits, contexts, rows_checked, claims_checked, patients = [], [], 0, 0, set()
    for index, original in enumerate(literal['episodes']):
        me = prepared['measurement_import']['episodes'][index]
        ie = prepared['interval_import']['episodes'][index]
        if ie['store'] is None or me['store'] is None:
            s.ei.require(original['reference_bindings'] == [], 'EMPTY_EPISODE_CONTROL_FAILED'); continue
        # Independent direct field comparison supplements shared importer reconstruction.
        for claim in me['store']['claims']:
            number = me['claim_provenance'][claim['id']]['source']['record_number']; row = raw[number - 1]
            event = claim['bundle']['events'][0]; var = claim['bundle']['variables'][0]
            s.ei.require((event['patient_id'], event['episode_id'], event['item_id'], event['value_lexical'], event['unit_lexical'],
                          var['local_lower'], var['local_upper']) ==
                         (row['subject_id'], row['stay_id'], row['itemid'], row['valuenum'], row['valueuom'], row['charttime'], row['charttime']),
                         'DIRECT_CSV_FIELD_DIFFERENCE')
            claims_checked += 1
        result = execute_episode(values, prepared, index)
        s.ei.require(result['status'] == 'COMPLETED_REVIEWED_MEASUREMENT_QUERY', 'SOURCE_MEASUREMENT_QUERY_FAILED')
        query = result['query_result']; s.ei.require(len(query['strata']) == 1, 'FIXTURE_STRATUM_COUNT')
        run = query['strata'][0]['result']
        s.ei.require(run == original['mixed_result'], 'LITERAL_SOURCE_QUERY_DIFFERENCE')
        bindings = source.graph_bindings(run)
        s.ei.require(bindings == original['reference_bindings'], 'SOURCE_MEASUREMENT_SQL_DIFFERENCE')
        s.ei.require(query['selection_plan']['semantic_run']['status'] == 'READY', 'CATALOGUE_RUST_REQUIRED')
        s.ei.require(run['interval_view']['semantic_run']['status'] == 'READY', 'TREATMENT_RUST_REQUIRED')
        s.ei.require(all(b['treatment']['patient_role'] and b['treatment']['patient_bearer'] for b in run['baseline_bindings']), 'PRO_REQUIRED')
        patients.update(query['certain_patient_ids']); rows_checked += len(bindings)
        audits.append(result['source_audit']['context_id']); contexts.append(result['context_id'])
    s.ei.require(sorted(patients) == literal['certain_patient_ids'] == ['1', '2', '3'], 'SOURCE_MEASUREMENT_MEMBERSHIP')
    paths = sorted(list(EXAMPLE.glob('*.json')) + list(SOURCE.glob('*.json')) + list(SOURCE.glob('*.csv')))
    return {'profile': 'measurement-source-catalogue-verification-1.0', 'status': 'VERIFIED',
            'synthetic_only': True, 'clinical_mapping_verified': False, 'public_demo_mapping_executed': False,
            'artifacts': {p: s.ei.digest((s.ei.ROOT / p).read_text()) for p in sorted(set(s.FILES + source.FILES +
                          ('patterns/verify_measurement_source_catalogue.py',)))},
            'fixture_sha256': {str(p.relative_to(s.ei.ROOT)): s.ei.digest(p.read_text()) for p in paths},
            'source_audit_context_ids': audits, 'audited_query_context_ids': contexts,
            'direct_csv_measurement_claims_checked': claims_checked, 'sql_binding_rows_checked': rows_checked,
            'certain_synthetic_patient_ids': sorted(patients), 'all_literal_and_sql_bindings_equal': True,
            'pro_witnesses_preserved': True, 'rust_semantic_status': 'READY',
            'scope': 'Raw CSV field and SQL checks on authored fixtures; admission and source-policy selection shared'}


if __name__ == '__main__':
    report = verify()
    (s.ei.ROOT / 'verification/measurement-source-catalogue-report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'synthetic_patients': report['certain_synthetic_patient_ids']}))
