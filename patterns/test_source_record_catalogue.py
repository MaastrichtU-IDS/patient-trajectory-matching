"""Pinned source correspondence before reviewed mapping execution."""
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
from . import source_record_catalogue as s, verify_source_record_catalogue as verify


class SourceCatalogueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original, cls.prepared = verify.load_example()

    def setUp(self):
        self.values = deepcopy(self.original)
        self.store = deepcopy(self.prepared['interval_import']['episodes'][0]['store'])
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        for path in verify.SOURCE.glob('*.csv'): shutil.copy(path, self.folder / path.name)

    def build(self): return s.build(self.folder, self.values['request'])
    def audit(self): return s.audit_store(self.folder, self.values['request'], self.values['catalogue'], self.store)

    def edit_dictionary(self, change):
        path = self.folder / 'd_items.csv'
        with path.open() as handle:
            reader = csv.DictReader(handle); fields = reader.fieldnames; rows = list(reader)
        change(rows)
        with path.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        self.values['request']['source_sha256']['d_items'] = s.inputs.admission.digest(path.read_bytes())

    def test_dictionary_catalogue_and_complete_selected_row_accounting(self):
        result = self.build()
        self.assertEqual(result['catalogue'], self.values['catalogue'])
        self.assertEqual(result['selected_item_outcomes'], {'1000': {'DUPLICATE_ROW':1, 'STAGED_SEGMENT':3, 'UNSUPPORTED_ROW':1}})
        self.assertEqual(result['input_rows'], 5); self.assertTrue(result['reconciliation_complete'])
        self.assertEqual(result['dictionary_evidence'][0]['dictionary_row']['label'], 'Synthetic input A')
        self.assertEqual(result['dictionary_evidence'][0]['source']['record_number'], 1)
        self.assertEqual(result['source_claims_accepted'], 0); self.assertFalse(result['mapped_query_executed'])
        self.assertNotIn('patient_id', json.dumps(result)); self.assertNotIn('claim_id', json.dumps(result))

    def test_closed_request_limits_and_pin_shape(self):
        for change in [lambda r:r.update(extra=True), lambda r:r.update(itemids=[]),
                       lambda r:r.update(itemids=['1000','1000']), lambda r:r.update(itemids=['x']),
                       lambda r:r.update(itemids=[str(i) for i in range(1,18)]),
                       lambda r:r.update(release='  '), lambda r:r['source_sha256'].pop('icustays')]:
            self.values = deepcopy(self.original); change(self.values['request'])
            with self.assertRaisesRegex(ValueError, 'INVALID_SOURCE_CATALOGUE_REQUEST'): self.build()

    def test_each_source_pin_rejects_changed_bytes(self):
        for table in ('inputevents', 'icustays', 'd_items'):
            self.values = deepcopy(self.original); self.values['request']['source_sha256'][table] = '0'*64
            with self.assertRaisesRegex(ValueError, 'CATALOGUE_SOURCE_HASH_MISMATCH:' + table): self.build()

    def test_unknown_or_measurement_item_cannot_enter_interval_catalogue(self):
        for code in ('9999','2000'):
            self.values['request']['itemids'] = [code]
            with self.assertRaisesRegex(ValueError, 'CATALOGUE_(UNKNOWN_ITEM|REQUIRES_INPUT_ITEM)'): self.build()

    def test_duplicate_dictionary_item_rejected(self):
        self.edit_dictionary(lambda rows: rows.append(deepcopy(rows[0])))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_DIMENSION_ID'): self.build()

    def test_revised_dictionary_needs_new_catalogue_and_mapping_review(self):
        self.edit_dictionary(lambda rows: rows[0].update(label='Changed source label'))
        result = self.build()
        self.assertEqual(result['catalogue']['entries'][0]['label'], 'Changed source label')
        with self.assertRaisesRegex(ValueError, 'SOURCE_CATALOGUE_MISMATCH'): self.audit()
        with self.assertRaisesRegex(ValueError, 'STALE_SOURCE_CATALOGUE'):
            s.mappings.compile_policy(result['catalogue'], *(self.values[n] for n in ('terminology','mappings','review')))

    def test_source_class_and_dataset_identity_cannot_be_forged(self):
        for key,value in [('label','Invented label'), ('source_class_iri','https://example.org/forged')]:
            self.values = deepcopy(self.original); self.values['catalogue']['entries'][0][key] = value
            with self.assertRaisesRegex(ValueError, 'SOURCE_CATALOGUE_MISMATCH'): self.audit()
        self.values = deepcopy(self.original); self.store['dataset_id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'SOURCE_STORE_DATASET_MISMATCH'): self.audit()

    def test_claim_time_type_and_source_identity_must_reproduce(self):
        original = deepcopy(self.store)
        changes = [lambda c:c.update(source_sha256='0'*64),
                   lambda c:c['bundle']['variables'][0].update(local_lower='2150-01-01 00:00:00'),
                   lambda c:c['bundle']['semantic_facts'][0].update(class_iri='https://example.org/wrong'),
                   lambda c:c['bundle']['events'][0].update(source_key='forged'),
                   lambda c:c.update(id='forged_claim')]
        for change in changes:
            self.store = deepcopy(original); change(self.store['claims'][0])
            with self.assertRaises(ValueError): self.audit()

    def test_clock_origin_and_origin_evidence_must_reproduce(self):
        original = deepcopy(self.store)
        for key,value in [('origin','2150-01-01T00:00:00'), ('origin_source_key','forged')]:
            self.store = deepcopy(original); self.store['clocks'][0][key] = value
            with self.assertRaisesRegex(ValueError, 'SOURCE_CLOCK_MISMATCH'): self.audit()

    def test_occurrence_store_is_not_admitted(self):
        self.store['profile'] = 'local-claim-store-1.0'
        with self.assertRaisesRegex(ValueError, 'SOURCE_REQUIRES_RECORDED_STORE'): self.audit()

    def test_subset_fidelity_never_claims_complete_coverage_or_acceptance(self):
        result = self.audit()
        self.assertEqual(result['checked_claims'], 1)
        self.assertTrue(result['claim_row_fidelity_verified']); self.assertTrue(result['recorded_clock_origin_verified'])
        for key in ('complete_source_coverage_verified', 'clinical_mapping_verified', 'source_acceptance_verified',
                    'measurement_fidelity_verified', 'clock_alignment_verified'): self.assertFalse(result[key])

    def test_mapping_execution_preserves_explicit_source_policy(self):
        before = deepcopy(self.values['source-review'])
        result = verify.execute_episode(self.values, self.prepared, 0)
        self.assertEqual(result['status'], 'COMPLETED_RECORD_QUERY')
        self.assertEqual(result['query_result']['certain_patient_ids'], ['1'])
        self.assertEqual(before, self.values['source-review'])
        self.values['source-review']['episodes'][0]['interval_policy']['semantic_policy_sha256'] = '0'*64
        with self.assertRaisesRegex(ValueError, 'STALE_CLAIM_SEMANTIC_POLICY'):
            verify.execute_episode(self.values, self.prepared, 0)

    def test_pending_mapping_blocks_backend_despite_valid_source(self):
        self.values['review']['decisions'] = []
        with patch.object(s.mappings.mixed, 'execute', side_effect=AssertionError('pending mapping')):
            result = verify.execute_episode(self.values, self.prepared, 0)
        self.assertEqual(result['status'], 'BLOCKED_MAPPING_REVIEW')
        self.assertEqual(result['source_audit']['status'], 'VERIFIED_SOURCE_STORE')
        self.assertIsNone(result['query_result'])

    def test_source_audit_does_not_mutate_inputs(self):
        before = deepcopy((self.values, self.store)); result = self.audit()
        result['catalogue_report']['catalogue']['entries'][0]['label'] = 'edited output'
        self.assertEqual(before, (self.values, self.store))
        self.assertEqual(self.audit()['catalogue_report']['catalogue'], self.values['catalogue'])

    def test_compressed_sources_are_pinned_and_ambiguous_files_rejected(self):
        path = self.folder / 'inputevents.csv'; zipped = path.with_suffix('.csv.gz')
        zipped.write_bytes(gzip.compress(path.read_bytes(), mtime=0))
        with self.assertRaisesRegex(ValueError, 'MISSING_OR_AMBIGUOUS_TABLE'): self.build()
        path.unlink(); self.values['request']['source_sha256']['inputevents'] = s.inputs.admission.digest(zipped.read_bytes())
        self.assertEqual(self.build()['catalogue'], self.values['catalogue'])
        with self.assertRaisesRegex(ValueError, 'SOURCE_CLAIM_MISMATCH'): self.audit()

    def test_cli_atomic_output_and_input_protection(self):
        request = self.folder / 'request.json'; request.write_text(json.dumps(self.values['request']))
        output = self.folder / 'output.json'
        args = ['--input-dir',str(self.folder),'--request',str(request),'--output',str(output)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(s.main(args), 0); original = output.read_bytes()
            for protected in (request, self.folder/'d_items.csv'):
                saved = protected.read_bytes()
                with self.assertRaises(SystemExit): s.main(args[:-1]+[str(protected)])
                self.assertEqual(saved, protected.read_bytes())
            request.write_text('{}')
            with self.assertRaises(SystemExit): s.main(args)
            self.assertEqual(original, output.read_bytes())

    def test_committed_synthetic_report_actual_rust_and_sql_reproduce(self):
        report = json.loads((s.ei.ROOT/'verification/source-record-catalogue-synthetic-report.json').read_text())
        self.assertEqual(report, verify.verify())
        self.assertEqual(report['certain_synthetic_patient_ids'], ['1','2','3'])
        self.assertTrue(report['all_sql_bindings_equal']); self.assertTrue(report['pro_witnesses_preserved'])

    def test_public_catalogue_report_pins_and_pending_clinical_scope(self):
        root=s.ei.ROOT
        report=json.loads((root/'verification/source-record-catalogue-demo-report.json').read_text())
        pin=json.loads((root/'data/clinical-source-demo-pin.json').read_text())
        request=json.loads((root/'data/clinical-source-catalogue-request.json').read_text())
        self.assertEqual(report['context']['request'], request)
        self.assertEqual(report['context_id'], s.cr.digest(report['context']))
        self.assertEqual(report['context']['catalogue_sha256'], s.cr.digest(report['catalogue']))
        self.assertEqual(set(report['context']['artifacts']), set(s.FILES))
        for path,digest in report['context']['artifacts'].items(): self.assertEqual(digest,s.ei.digest((root/path).read_text()),path)
        for manifest in report['context']['source_files']:
            self.assertEqual(manifest['file_sha256'], pin['files'][manifest['table']]['file_sha256'])
            self.assertEqual(manifest['rows'], pin['files'][manifest['table']]['rows'])
        self.assertEqual(report['catalogue']['entries'], [{'code':'221906','label':'Norepinephrine','source_class_iri':s.inputs.ITEM_NS+'221906'}])
        self.assertFalse(report['clinical_mapping_verified']); self.assertFalse(report['mapped_query_executed'])
        self.assertEqual(report['source_claims_accepted'], 0)
        self.assertNotIn('patient_id',json.dumps(report)); self.assertNotIn('claim_id',json.dumps(report))
        self.assertEqual(report['catalogue'], json.loads((root/'data/terminology/mimic-demo-2.2-pending/catalogue.json').read_text()))


if __name__ == '__main__': unittest.main()
