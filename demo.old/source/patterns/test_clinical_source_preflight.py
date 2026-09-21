"""Complete streaming coverage, refusal on truncation, aggregate outputs and unchanged admission rules."""
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
from . import clinical_source_preflight as p, exact_intervals as ei, mimic_measurement_import as importer
from . import verify_clinical_source_preflight as verify

FOLDER = ei.ROOT / 'examples/source-mixed-query'
REQUEST = ei.ROOT / 'examples/clinical-source-preflight/synthetic-request.json'


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.request=json.loads(REQUEST.read_text())
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'source';shutil.copytree(FOLDER,self.folder)

    def run_scan(self):return p.run(self.folder,self.request)

    def rewrite(self,change):
        path=self.folder/'chartevents.csv'
        with path.open() as f:
            reader=csv.DictReader(f);header=reader.fieldnames;rows=list(reader)
        change(rows)
        with path.open('w',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=header,lineterminator='\n');writer.writeheader();writer.writerows(rows)

    def test_complete_aggregate_counts(self):
        r=self.run_scan()
        self.assertEqual(r['input_rows'],{'inputevents':5,'chartevents':9})
        self.assertEqual(r['row_outcomes']['chartevents'],{'ADMITTED_MEASUREMENT':6,'DUPLICATE_ROW':1,'UNSUPPORTED_ROW':2})
        self.assertEqual(r['roster'],{'patients':4,'stays':5,'stays_with_admitted_treatment':3,'stays_with_admitted_measurement':3,'stays_without_either_admitted_type':2})
        self.assertTrue(r['row_accounting_complete'])
        self.assertEqual(r['capacity_screen']['within_conservative_count_bounds'],3)

    def test_no_acceptance_alignment_query_or_patient_identifiers(self):
        r=self.run_scan()
        for k in ('clinical_mapping_verified','clock_alignment_declared','mixed_query_executed','patient_rows_or_identifiers_included','full_mimic_analyzed'):
            self.assertFalse(r[k])
        self.assertEqual(r['claims_created'],0);self.assertEqual(r['accepted_claims'],0);self.assertIsNone(r['cohort_membership'])
        def visit(value):
            if isinstance(value,dict):
                self.assertFalse(set(value)&{'subject_id','patient_id','stay_id','episode_id','raw','value','valuenum','charttime','starttime'})
                for v in value.values():visit(v)
            elif isinstance(value,list):
                for v in value:visit(v)
        # Source column names are strings in manifests, never row-valued output keys.
        visit(r)

    def test_gzip_matches_full_csv_manifest(self):
        original=self.run_scan();path=self.folder/'chartevents.csv'
        payload=path.read_bytes();path.with_suffix('.csv.gz').write_bytes(gzip.compress(payload));path.unlink()
        compressed=self.run_scan()
        self.assertEqual(original['row_outcomes'],compressed['row_outcomes'])
        manifests=[next(m for m in r['context']['source_files'] if m['table']=='chartevents') for r in (original,compressed)]
        self.assertEqual(manifests[0]['csv_sha256'],manifests[1]['csv_sha256'])
        self.assertNotEqual(manifests[0]['file_sha256'],manifests[1]['file_sha256'])

    def test_streaming_and_existing_importer_admission_agree(self):
        r=self.run_scan()
        request={'profile':importer.PROFILE,'id':'test','dataset_id':self.request['dataset_id'],'mode':'retrospective_source_records',
                 'time_policy':'recorded-point-label-exact-v1','patient_ids':'all','itemids':['2000']}
        old=importer.run(self.folder,request)
        self.assertEqual(r['row_outcomes']['chartevents'],old['summary']['admission_outcome_counts'])

    def test_multiline_field_keeps_csv_record_count(self):
        self.rewrite(lambda rows:rows[0].update(value='text\nwith newline'))
        r=self.run_scan();self.assertEqual(r['input_rows']['chartevents'],9)
        self.assertEqual(r['items'][1]['reason_counts']['TEXTUAL_OR_QUALIFIED_VALUE'],2)

    def test_outside_item_rows_counted_without_clinical_admission(self):
        self.rewrite(lambda rows:rows.append({**rows[0],'itemid':'999999','valuenum':'notnumeric'}))
        r=self.run_scan();self.assertEqual(r['input_rows']['chartevents'],10)
        self.assertEqual(r['row_outcomes']['chartevents']['OUTSIDE_ITEM_SCOPE'],1)
        self.assertEqual(sum(r['row_outcomes']['chartevents'].values()),10)

    def test_unknown_stay_and_unit_warning_buckets_do_not_leak_values(self):
        self.rewrite(lambda rows:rows[0].update(stay_id='999',valueuom='UNTRUSTED_UNIT_TEXT',warning='odd'))
        r=self.run_scan();item=r['items'][1]
        self.assertEqual(item['admission_outcomes']['INVALID_ROW'],1)
        self.assertEqual(item['unit_buckets']['OTHER'],1);self.assertEqual(item['warning_buckets']['OTHER'],1)
        self.assertNotIn('UNTRUSTED_UNIT_TEXT',json.dumps(r));self.assertNotIn('odd',json.dumps(r))

    def test_warning_missing_or_flagged_remains_unsupported(self):
        self.rewrite(lambda rows:rows[0].update(warning=''))
        r=self.run_scan();self.assertEqual(r['items'][1]['reason_counts']['SOURCE_WARNING_UNKNOWN'],1)
        self.assertEqual(r['items'][1]['reason_counts']['SOURCE_WARNING_FLAGGED'],1)

    def test_resource_limits_refuse_complete_report(self):
        for name,limit in [('MAX_RAW_BYTES',1),('MAX_EXPANDED_BYTES',1),('MAX_LINE_BYTES',5),('MAX_CHART_ROWS',8),('MAX_SELECTED_ROWS',8)]:
            with self.subTest(name=name),patch.object(p,name,limit),self.assertRaises(ei.ContractError):self.run_scan()

    def test_source_change_during_stream_invalidates_manifest(self):
        real=p.file_digest;calls=0
        def changed(path):
            nonlocal calls
            calls+=1;value=real(path)
            return ('0'*64,value[1]) if calls==2 else value
        with patch.object(p,'file_digest',side_effect=changed),self.assertRaisesRegex(ei.ContractError,'SOURCE_CHANGED'):
            self.run_scan()

    def test_bad_header_and_malformed_record_rejected(self):
        path=self.folder/'chartevents.csv';original=path.read_text()
        for payload in [original.replace('caregiver_id','wrong',1),original+'one,two\n']:
            path.write_text(payload)
            with self.assertRaises(ei.ContractError):self.run_scan()

    def test_truncated_gzip_refused(self):
        path=self.folder/'chartevents.csv';compressed=gzip.compress(path.read_bytes())
        path.with_suffix('.csv.gz').write_bytes(compressed[:-8]);path.unlink()
        with self.assertRaises(EOFError):self.run_scan()

    def test_count_screen_is_conservative_not_query_readiness(self):
        with patch.object(p.mixed,'MAX_FOLLOWUP_TESTS',0):r=self.run_scan()
        self.assertEqual(r['capacity_screen']['exceed_conservative_count_bounds'],2)
        self.assertEqual(r['capacity_screen']['within_conservative_count_bounds'],1)
        self.assertFalse(r['capacity_screen']['query_readiness_established'])
        self.assertFalse(r['mixed_query_executed'])

    def test_direct_file_gate_is_independent_of_item_filter(self):
        with patch.object(importer,'MAX_ROWS',8):r=self.run_scan()
        self.assertFalse(r['capacity_screen']['direct_chartevents_file_within_import_limits'])
        self.assertEqual(r['capacity_screen']['direct_import_blockers'],['CHARTEVENTS_IMPORT_ROW_LIMIT'])
        self.assertEqual(r['status'],'COMPLETED_SOURCE_PREFLIGHT')

    def test_stay_count_bound_and_wrong_item_table_refused(self):
        with patch.object(p.intervals,'MAX_EPISODES',4),self.assertRaisesRegex(ei.ContractError,'STAY_LIMIT'):self.run_scan()
        self.request['treatment_itemids']=['2000']
        with self.assertRaisesRegex(ei.ContractError,'WRONG_TABLE'):self.run_scan()

    def test_invalid_request_and_missing_or_ambiguous_source(self):
        self.request['extra']=True
        with self.assertRaisesRegex(ei.ContractError,'INVALID_PREFLIGHT_REQUEST'):self.run_scan()
        del self.request['extra'];path=self.folder/'chartevents.csv'
        path.with_suffix('.csv.gz').write_bytes(gzip.compress(path.read_bytes()))
        with self.assertRaisesRegex(ei.ContractError,'AMBIGUOUS_TABLE'):self.run_scan()

    def test_input_immutable_context_tracks_request(self):
        before=deepcopy(self.request);a=self.run_scan();self.assertEqual(self.request,before)
        self.request['id']='different';b=self.run_scan()
        self.assertNotEqual(a['context_id'],b['context_id']);self.assertEqual(a['row_outcomes'],b['row_outcomes'])

    def test_cli_atomic_output_and_input_protection(self):
        output=Path(self.temp.name)/'result.json'
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(p.main(['--input-dir',str(self.folder),'--output',str(output)]),0)
            before=output.read_bytes()
            with patch.object(p,'MAX_CHART_ROWS',1):self.assertEqual(p.main(['--input-dir',str(self.folder),'--output',str(output)]),2)
            self.assertEqual(output.read_bytes(),before)
            self.assertEqual(p.main(['--input-dir',str(self.folder),'--output',str(self.folder/'chartevents.csv')]),2)

    def test_demo_report_pins_and_aggregate_arithmetic(self):
        report=json.loads((ei.ROOT/'verification/clinical-source-preflight-demo-report.json').read_text())
        self.assertEqual(verify.verify_result(report['result']),report)
        r=report['result'];self.assertEqual(r['input_rows'],{'inputevents':20404,'chartevents':668862})
        for table,n in r['input_rows'].items():self.assertEqual(sum(r['row_outcomes'][table].values()),n)
        self.assertEqual(sum(x['admission_outcomes'].get('ADMITTED_MEASUREMENT',0) for x in r['items']),14349)
        self.assertEqual(next(x for x in r['items'] if x['itemid']=='221906')['admission_outcomes']['STAGED_SEGMENT'],944)
        for file in r['context']['source_files']:
            bad=deepcopy(r);next(m for m in bad['context']['source_files'] if m['table']==file['table'])['file_sha256']='0'*64
            with self.assertRaisesRegex(ei.ContractError,'PIN_MISMATCH'):verify.verify_result(bad)

    def test_demo_artifact_hashes_match_current_code(self):
        report=json.loads((ei.ROOT/'verification/clinical-source-preflight-demo-report.json').read_text())
        for path,digest in report['result']['context']['artifacts'].items():
            self.assertEqual(ei.digest((ei.ROOT/path).read_text()),digest,path)


if __name__=='__main__':unittest.main()
