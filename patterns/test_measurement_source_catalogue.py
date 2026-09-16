"""Source correspondence, review separation and raw-CSV query control checks."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import csv
import gzip
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from . import measurement_source_catalogue as s, verify_measurement_source_catalogue as verify


class MeasurementSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.original, cls.prepared = verify.load_example()

    def setUp(self):
        self.values = deepcopy(self.original)
        self.store = deepcopy(self.prepared['measurement_import']['episodes'][0]['store'])
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for path in verify.SOURCE.glob('*.csv'): shutil.copy(path, self.folder / path.name)

    def build(self): return s.build(self.folder, self.values['request'])
    def audit(self): return s.audit_store(self.folder, self.values['request'], self.values['catalogue'], self.store)

    def edit(self, table, change):
        path = self.folder / (table + '.csv')
        with path.open(newline='') as handle:
            reader = csv.DictReader(handle); fields = reader.fieldnames; rows = list(reader)
        change(rows)
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        self.values['request']['source_sha256'][table] = s.inputs.source.digest(path.read_bytes())

    def fresh_store(self):
        request = self.prepared['context']['request']['measurement_import']
        self.store = s.inputs.run(self.folder, request)['episodes'][0]['store']

    def test_catalogue_dictionary_evidence_and_complete_row_accounting(self):
        result = self.build()
        self.assertEqual(result['catalogue'], self.values['catalogue'])
        self.assertEqual(result['selected_item_outcomes'], {'2000': {'ADMITTED_MEASUREMENT':6, 'DUPLICATE_ROW':1, 'UNSUPPORTED_ROW':2}})
        self.assertEqual(result['input_rows'], 9); self.assertTrue(result['reconciliation_complete'])
        self.assertEqual(result['dictionary_evidence'][0]['dictionary_row']['unitname'], 'mmHg')
        self.assertEqual(result['dictionary_evidence'][0]['source']['record_number'], 2)
        self.assertNotIn('patient_id', json.dumps(result)); self.assertNotIn('claim_id', json.dumps(result))
        for field in ('clinical_mapping_verified', 'source_publisher_authenticated', 'mapped_query_executed', 'unit_conversion_performed'):
            self.assertFalse(result[field])
        self.assertEqual(result['source_claims_accepted'], 0)

    def test_closed_request_scope_and_limits(self):
        for change in (lambda r:r.update(extra=True), lambda r:r.update(itemids=[]),
                       lambda r:r.update(itemids=['2000','2000']), lambda r:r.update(itemids=['x']),
                       lambda r:r.update(itemids=[str(i) for i in range(1,18)]),
                       lambda r:r.update(release='  '), lambda r:r['source_sha256'].pop('icustays')):
            self.values = deepcopy(self.original); change(self.values['request'])
            with self.assertRaisesRegex(ValueError, 'INVALID_MEASUREMENT_CATALOGUE_REQUEST'): self.build()

    def test_all_three_pins_are_required(self):
        for table in ('chartevents', 'icustays', 'd_items'):
            self.values = deepcopy(self.original); self.values['request']['source_sha256'][table] = '0'*64
            with self.assertRaisesRegex(ValueError, 'CATALOGUE_SOURCE_HASH_MISMATCH:' + table): self.build()

    def test_unknown_or_interval_item_rejected(self):
        for code in ('9999', '1000'):
            self.values['request']['itemids'] = [code]
            with self.assertRaisesRegex(ValueError, 'CATALOGUE_(UNKNOWN_ITEM|REQUIRES_CHART_ITEM)'): self.build()

    def test_duplicate_and_conflicting_dimensions_rejected(self):
        self.edit('d_items', lambda rows:rows.append(deepcopy(rows[0])))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_DIMENSION_ID'): self.build()
        shutil.copy(verify.SOURCE/'d_items.csv', self.folder/'d_items.csv')
        self.values['request'] = deepcopy(self.original['request'])
        self.edit('icustays', lambda rows:rows[1].update(hadm_id=rows[0]['hadm_id']))
        with self.assertRaisesRegex(ValueError, 'CONFLICTING_ADMISSION_PATIENT'): self.build()

    def test_dictionary_revision_requires_new_catalogue_and_review(self):
        self.edit('d_items', lambda rows:rows[1].update(label='Revised synthetic label'))
        result = self.build()
        with self.assertRaisesRegex(ValueError, 'SOURCE_CATALOGUE_MISMATCH'): self.audit()
        with self.assertRaisesRegex(ValueError, 'STALE_SOURCE_CATALOGUE'):
            s.mappings.mappings.compile_policy(result['catalogue'], *(self.values[n] for n in ('terminology','mappings','review')))

    def test_catalogue_and_dataset_cannot_be_forged(self):
        for field, value in [('label','Invented'), ('source_class_iri','https://example.org/forged')]:
            self.values = deepcopy(self.original); self.values['catalogue']['entries'][0][field] = value
            with self.assertRaisesRegex(ValueError, 'SOURCE_CATALOGUE_MISMATCH'): self.audit()
        self.values = deepcopy(self.original); self.store['dataset_id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'SOURCE_STORE_DATASET_MISMATCH'): self.audit()

    def test_every_scalar_time_and_source_field_must_reproduce(self):
        changes = [lambda c:c.update(source_sha256='0'*64), lambda c:c.update(source_record_id='forged'),
                   lambda c:c['bundle']['events'][0].update(value_lexical='58.0'),
                   lambda c:c['bundle']['events'][0].update(unit_lexical='mm[Hg]'),
                   lambda c:c['bundle']['events'][0].update(item_id='2001'),
                   lambda c:c['bundle']['events'][0].update(source_key='forged'),
                   lambda c:c['bundle']['variables'][0].update(local_lower='2150-01-01 09:00:00')]
        for change in changes:
            self.store = deepcopy(self.prepared['measurement_import']['episodes'][0]['store'])
            change(self.store['claims'][0])
            with self.assertRaisesRegex(ValueError, 'SOURCE_MEASUREMENT_CLAIM_MISMATCH'): self.audit()

    def test_rejected_or_duplicate_rows_cannot_be_fabricated_as_claims(self):
        rows, manifest = s.inputs.read_observations(self.folder/'chartevents.csv')
        for number in (7, 8, 9):
            evidence = s.inputs.source.evidence('chartevents', number, rows[number-1], manifest)
            record_id = 'chartevents:' + self.values['request']['dataset_id'] + ':' + manifest['csv_sha256'] + ':' + str(number)
            claim = s.inputs.describe({'raw':rows[number-1], 'source':evidence, 'record_id':record_id}, self.values['request']['dataset_id'])
            self.store['claims'] = [claim]
            with self.assertRaisesRegex(ValueError, 'SOURCE_MEASUREMENT_CLAIM_MISMATCH'): self.audit()

    def test_clock_origin_precedes_item_filter_and_is_bound_to_evidence(self):
        self.edit('d_items', lambda rows:rows.append({**rows[1], 'itemid':'2001', 'label':'Other synthetic measurement'}))
        self.edit('chartevents', lambda rows:rows.append({**rows[0], 'itemid':'2001', 'charttime':'2150-01-01 08:00:00'}))
        self.fresh_store()
        self.assertEqual(self.store['clocks'][0]['origin'], '2150-01-01T08:00:00')
        self.assertTrue(self.audit()['recorded_clock_origin_verified'])
        original = deepcopy(self.store['clocks'][0])
        for key, value in [('origin','2150-01-01T09:40:00'), ('origin_source_key','forged')]:
            self.store['clocks'][0] = {**original, key:value}
            with self.assertRaisesRegex(ValueError, 'SOURCE_MEASUREMENT_CLOCK_MISMATCH'): self.audit()

    def test_subset_and_snapshot_are_not_coverage_or_acceptance_certificates(self):
        self.store['claims'] = self.store['claims'][:1]; self.store['snapshot_id'] = 'authored_subset'
        result = self.audit(); self.assertEqual(result['checked_claims'], 1)
        for key in ('complete_source_coverage_verified','source_acceptance_verified','clinical_mapping_verified',
                    'interval_fidelity_verified','clock_alignment_verified','source_history_verified','physical_elapsed_time_verified'):
            self.assertFalse(result[key])
        self.store['claims'] = []
        with self.assertRaisesRegex(ValueError, 'INVALID_CLAIM_CLOCK_SCOPE'): self.audit()

    def test_dictionary_unit_is_evidence_not_conversion_or_admission_override(self):
        self.edit('d_items', lambda rows:rows[1].update(unitname='cm[H2O]'))
        result = self.audit()
        self.assertEqual(result['catalogue_report']['dictionary_evidence'][0]['dictionary_row']['unitname'], 'cm[H2O]')
        self.assertEqual(self.store['claims'][0]['bundle']['events'][0]['unit_lexical'], 'mmHg')
        self.assertFalse(result['unit_conversion_performed'])

    def test_gzip_bytes_and_ambiguous_input_paths(self):
        path = self.folder/'chartevents.csv'; zipped = path.with_suffix('.csv.gz')
        zipped.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
        with self.assertRaisesRegex(ValueError, 'MISSING_OR_AMBIGUOUS_TABLE'): self.build()
        path.unlink()
        with self.assertRaisesRegex(ValueError, 'CATALOGUE_SOURCE_HASH_MISMATCH:chartevents'): self.build()
        self.values['request']['source_sha256']['chartevents'] = s.inputs.source.digest(zipped.read_bytes())
        self.fresh_store(); self.assertEqual(self.audit()['checked_claims'], 3)

    def test_row_byte_and_document_resource_limits(self):
        with patch.object(s.inputs, 'MAX_ROWS', 2):
            with self.assertRaisesRegex(ValueError, 'ROW_LIMIT:chartevents'): self.build()
        with patch.object(s.inputs, 'MAX_BYTES', 16):
            with self.assertRaisesRegex(ValueError, 'FILE_SIZE_LIMIT:chartevents'): self.build()
        self.values['request']['release'] = 'x'*(s.cr.MAX_BYTES+1)
        with self.assertRaisesRegex(ValueError, 'CATALOGUE_REQUEST_LIMIT'): self.build()

    def test_catalogue_can_account_for_more_rows_than_a_single_store(self):
        self.edit('chartevents', lambda rows:rows.extend({**rows[0], 'value':str(i), 'valuenum':str(i)} for i in range(100,140)))
        self.assertEqual(self.build()['selected_item_outcomes']['2000']['ADMITTED_MEASUREMENT'], 46)
        self.assertEqual(s.inputs.run(self.folder, self.prepared['context']['request']['measurement_import'])['episodes'][0]['status'], 'BLOCKED_RESOURCE_LIMIT')

    def test_mapping_and_source_policy_separation_and_input_immutability(self):
        before = deepcopy(self.values)
        result = verify.execute_episode(self.values, self.prepared, 0, self.folder)
        self.assertEqual(result['status'], 'COMPLETED_REVIEWED_MEASUREMENT_QUERY')
        self.assertEqual(result['query_result']['certain_patient_ids'], ['1'])
        self.assertEqual(result['context']['source_audit_context_id'], result['source_audit']['context_id'])
        self.assertEqual(result['context']['query_context_id'], result['query_result']['context_id'])
        self.assertEqual(self.values, before)
        self.values['source-review']['episodes'][0]['measurement_policy']['store_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'STALE_CLAIM_STORE'):
            verify.execute_episode(self.values, self.prepared, 0, self.folder)

    def test_pending_mapping_blocks_backend_after_successful_source_audit(self):
        self.values['review']['decisions'] = []
        with patch.object(s.mappings.semantic, 'check', side_effect=AssertionError('pending backend')):
            result = verify.execute_episode(self.values, self.prepared, 0, self.folder)
        self.assertEqual(result['status'], 'BLOCKED_MAPPING_REVIEW')
        self.assertEqual(result['source_audit']['status'], 'VERIFIED_MEASUREMENT_SOURCE_STORE')
        self.assertIsNone(result['query_result']['certain_patient_ids'])

    def test_tampered_unaccepted_claim_blocks_before_mapping_backend(self):
        prepared = deepcopy(self.prepared)
        prepared['measurement_import']['episodes'][0]['store']['claims'][0]['bundle']['events'][0]['value_lexical'] = '999'
        self.values['source-review']['episodes'][0]['measurement_policy']['decisions'] = []
        with patch.object(s.mappings, 'execute', side_effect=AssertionError('audit must run first')):
            with self.assertRaisesRegex(ValueError, 'SOURCE_MEASUREMENT_CLAIM_MISMATCH'):
                verify.execute_episode(self.values, prepared, 0, self.folder)

    def test_cli_build_audit_atomic_failure_and_input_guards(self):
        request=self.folder/'request.json'; catalogue=self.folder/'catalogue.json'; store=self.folder/'store.json'; output=self.folder/'report.json'
        for path, value in [(request,self.values['request']), (catalogue,self.values['catalogue']), (store,self.store)]: path.write_text(json.dumps(value))
        base=['--input-dir',str(self.folder),'--request',str(request),'--output',str(output)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(s.main(base), 0)
            self.assertEqual(s.main(base+['--catalogue',str(catalogue),'--store',str(store)]), 0)
            self.assertEqual(json.loads(output.read_text())['checked_claims'], 3)
            old=output.read_bytes(); request.write_text('{}')
            with self.assertRaises(SystemExit) as error: s.main(base)
            self.assertEqual(error.exception.code, 2); self.assertEqual(output.read_bytes(), old)
            with self.assertRaises(SystemExit): s.main(base+['--store',str(store)])
            with self.assertRaises(SystemExit): s.main(base[:-1]+[str(store),'--store',str(store),'--catalogue',str(catalogue)])
            self.assertEqual(json.loads(store.read_text()), self.store)

    def test_committed_raw_csv_rust_literal_and_sql_report_reproduces(self):
        report = verify.verify()
        self.assertEqual(report, json.loads((s.ei.ROOT/'verification/measurement-source-catalogue-report.json').read_text()))
        self.assertEqual(report['direct_csv_measurement_claims_checked'], 6)
        self.assertEqual(report['certain_synthetic_patient_ids'], ['1','2','3'])


if __name__ == '__main__': unittest.main()
