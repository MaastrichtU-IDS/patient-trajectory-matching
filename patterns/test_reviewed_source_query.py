"""Independent source fidelity, explicit declaration and aggregate execution contract."""
from contextlib import redirect_stdout,redirect_stderr
from copy import deepcopy
import gzip
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from . import reviewed_source_query as r

p=r.p


def declare(package, audit):
    return {'profile':'source-fidelity-review-declaration-1.0','package_context_id':package['context_id'],
        'audit_context_id':audit['context_id'],'unique_claims':package['summary']['unique_claims'],
        'calendar_reviews':package['summary']['calendar_reviews'],'reviewer':'Fabricated source test reviewer',
        'record_decision':'accept_all_fidelity_verified_claims','record_reason':'Exact constructed source fidelity checked',
        'calendar_decision':'same_patient_calendar','calendar_reason':'Constructed common patient calendar',
        'clinical_mapping_verified':False,'reviewer_identity_verified':False}


class ReviewedSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)/'source';shutil.copytree(p.ei.ROOT/'examples/source-mixed-query',self.folder)
        self.request=json.loads((p.ei.ROOT/'examples/indexed-source-windows/synthetic-request.json').read_text())

    def prepare(self):return r.prepare(self.folder,self.request)

    def test_independent_audit_verifies_all_claims_without_acceptance(self):
        s,plan,package,audit=self.prepare()
        self.assertEqual(audit['summary']['claims_verified'],9);self.assertEqual(audit['summary']['claims_failed'],0)
        self.assertEqual(audit['summary']['acceptance_decisions_created'],0)
        self.assertFalse(audit['summary']['calendar_compatibility_inferred'])
        self.assertTrue(all(all(c['checks'].values()) for c in audit['context']['claims']))

    def test_audit_does_not_call_importer_describe_functions(self):
        s,plan,package,audit=self.prepare()
        with patch.object(p.windows.inputs,'describe',side_effect=AssertionError('No importer oracle')),patch.object(p.windows.measurements,'describe',side_effect=AssertionError('No importer oracle')):
            observed=r.audit.audit(self.folder,package)
        self.assertEqual(audit,observed)

    def test_changed_claim_fields_and_extra_facts_fail_fidelity(self):
        s,plan,package,audit=self.prepare()
        for mode in ('value','time','patient','extra_fact'):
            changed=deepcopy(package);c=next(c for c in changed['context']['claims'] if c['kind']=='measurement');claim=c['claim']
            if mode=='value':claim['bundle']['events'][0]['value_lexical']='999'
            if mode=='time':claim['bundle']['variables'][0]['local_lower']='2150-01-01 00:00:00'
            if mode=='patient':claim['patient_id']='999'
            if mode=='extra_fact':claim['bundle']['semantic_facts'].append({'invented':True})
            c['claim_sha256']=p.cr.digest(claim);changed['context_id']=p.cr.digest(changed['context'])
            checked=r.audit.audit(self.folder,changed)
            self.assertEqual(checked['summary']['claims_failed'],1)
            with self.subTest(mode=mode),self.assertRaisesRegex(p.ei.ContractError,'FIDELITY_REVIEW_FAILED'):
                r.materialize_review(changed,checked,declare(changed,checked))

    def test_displayed_row_hash_and_origin_tampering_detected(self):
        s,plan,package,audit=self.prepare()
        for mode in ('row','hash','origin'):
            changed=deepcopy(package);c=next(c for c in changed['context']['claims'] if c['kind']=='measurement')
            if mode=='row':c['source_record']['value']='999'
            if mode=='hash':c['source_evidence']['row_sha256']='0'*64
            if mode=='origin':c['clock']['origin']='2149-01-01T00:00:00'
            checked=r.audit.audit(self.folder,changed)
            with self.subTest(mode=mode):self.assertEqual(checked['summary']['claims_failed'],1)

    def test_changed_original_file_refuses_audit(self):
        s,plan,package,audit=self.prepare();path=self.folder/'chartevents.csv';path.write_text(path.read_text().replace(',58,58,',',57,57,'))
        with self.assertRaisesRegex(p.ei.ContractError,'AUDIT_SOURCE_CHANGED'):r.audit.audit(self.folder,package)

    def test_parser_counts_full_file_and_preserves_original_positions(self):
        rows,manifest=r.audit.read_original(self.folder/'chartevents.csv','chartevents',{2})
        self.assertEqual(list(rows),[2]);self.assertEqual(manifest['rows'],9);self.assertEqual(rows[2]['valuenum'],'68')
        src=self.folder/'chartevents.csv';gz=Path(self.temp.name)/'copy.csv.gz';gz.write_bytes(gzip.compress(src.read_bytes(),mtime=0))
        zipped,zmanifest=r.audit.read_original(gz,'chartevents',{2})
        self.assertEqual(rows,zipped);self.assertEqual(manifest['csv_sha256'],zmanifest['csv_sha256'])
        self.assertNotEqual(manifest['file_sha256'],zmanifest['file_sha256'])

    def test_parser_rejects_malformed_csv_and_resource_limits(self):
        f=Path(self.temp.name)/'bad.csv';f.write_text('a,b\n1\n')
        with self.assertRaisesRegex(p.ei.ContractError,'AUDIT_MALFORMED_RECORD'):r.audit.read_original(f,'bad')
        f.write_text('a,a\n1,2\n')
        with self.assertRaisesRegex(p.ei.ContractError,'AUDIT_INVALID_HEADER'):r.audit.read_original(f,'bad')
        for name in ('MAX_RAW_BYTES','MAX_CSV_BYTES'):
            with self.subTest(name=name),patch.object(r.audit,name,1),self.assertRaises(p.ei.ContractError):r.audit.read_original(self.folder/'chartevents.csv','chartevents')

    def test_explicit_declaration_materializes_hash_bound_review(self):
        s,plan,package,audit=self.prepare();declaration=declare(package,audit)
        review=r.materialize_review(package,audit,declaration);compiled=r.u._compile(s,plan,package,review)
        self.assertEqual(compiled['summary']['unique_accepted_claims'],9)
        self.assertTrue(compiled['summary']['review_complete'])
        self.assertEqual(review['reviewer'],declaration['reviewer'])
        self.assertTrue(all(c['decision']=='same_patient_calendar' for c in review['calendars']))

    def test_absent_stale_incomplete_or_clinical_declarations_refused(self):
        s,plan,package,audit=self.prepare()
        for mode in ('absent','stale','count','calendar','clinical','blank','extra'):
            d=declare(package,audit)
            if mode=='absent':d={}
            if mode=='stale':d['audit_context_id']='0'*64
            if mode=='count':d['unique_claims']-=1
            if mode=='calendar':d['calendar_reviews']-=1
            if mode=='clinical':d['clinical_mapping_verified']=True
            if mode=='blank':d['record_reason']='   '
            if mode=='extra':d['approve_everything']=True
            with self.subTest(mode=mode),self.assertRaises(p.ei.ContractError):r.materialize_review(package,audit,d)

    def test_tampered_context_cannot_be_authorized(self):
        s,plan,package,audit=self.prepare();d=declare(package,audit)
        for field in ('package','audit'):
            altered=deepcopy(package if field=='package' else audit);altered['context']['tampered']=True
            with self.subTest(field=field),self.assertRaisesRegex(p.ei.ContractError,'INVALID_FIDELITY_CONTEXT_HASH'):
                r.materialize_review(altered if field=='package' else package,altered if field=='audit' else audit,d)

    def test_actual_synthetic_execution_and_aggregate_counts(self):
        s,plan,package,audit=self.prepare();report,review,result=r.execute_prepared(s,plan,package,audit,declare(package,audit))
        self.assertEqual(report['status'],'COMPLETED_PARTITIONED_QUERY')
        self.assertEqual(report['metrics'],{'matched_patients':3,'eligible_treatment_baseline_pairs':3,'followup_bindings':3,
            'eligible_pairs_without_selected_followup':1,'delta_signs_per_binding':{'negative':1,'positive':2}})
        self.assertEqual(report['anchors_verified_against_unpartitioned_sql'],3)
        self.assertEqual(sum(report['stay_outcomes'].values()),5)
        self.assertNotIn('bindings',report);self.assertNotIn('certain_patient_ids',report)
        for c in package['context']['claims']:self.assertNotIn(c['claim_id'],json.dumps(report))
        self.assertFalse(report['real_source_mixed_query_executed']);self.assertFalse(report['clinical_mapping_verified'])

    def test_backend_failure_suppresses_aggregate_membership(self):
        s,plan,package,audit=self.prepare()
        with patch.object(p.mixed,'execute',side_effect=p.ei.ContractError('RESOURCE_LIMIT')):
            report,_,_=r.execute_prepared(s,plan,package,audit,declare(package,audit))
        self.assertEqual(report['status'],'BLOCKED_INCOMPLETE_PARTITIONED_QUERY');self.assertIsNone(report['metrics'])
        self.assertEqual(report['batch_outcomes'],{'BLOCKED_BATCH':3})

    def test_inputs_are_immutable_and_review_is_deterministic(self):
        s,plan,package,audit=self.prepare();d=declare(package,audit);before=p.cr.digest([package,audit,d])
        self.assertEqual(r.materialize_review(package,audit,d),r.materialize_review(package,audit,d))
        self.assertEqual(before,p.cr.digest([package,audit,d]))

    def test_cli_executes_explicit_review_and_protects_inputs(self):
        s,plan,package,audit=self.prepare();d=Path(self.temp.name)/'declaration.json';d.write_text(json.dumps(declare(package,audit)))
        req=Path(self.temp.name)/'request.json';req.write_text(json.dumps(self.request));out=Path(self.temp.name)/'result.json'
        args=['--input-dir',str(self.folder),'--request',str(req),'--declaration',str(d)]
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(r.main(args+['--output',str(out)]),0);before=out.read_bytes()
            for target in (d,req,self.folder/'chartevents.csv'):
                saved=target.read_bytes();self.assertEqual(r.main(args+['--output',str(target)]),2);self.assertEqual(saved,target.read_bytes())
            d.write_text('{}');self.assertEqual(r.main(args+['--output',str(out)]),2);self.assertEqual(before,out.read_bytes())


    def test_committed_demo_declaration_and_aggregate_provenance(self):
        report=json.loads((p.ei.ROOT/'verification/reviewed-arterial-demo-report.json').read_text())
        declaration=json.loads((p.ei.ROOT/'data/arterial-source-fidelity-review.json').read_text())
        pin=json.loads((p.ei.ROOT/'data/clinical-source-demo-pin.json').read_text())
        self.assertEqual(report['review_declaration'],declaration)
        self.assertEqual(report['review_declaration_sha256'],p.cr.digest(declaration))
        self.assertEqual(report['package_context_id'],declaration['package_context_id'])
        self.assertEqual(report['audit_context_id'],declaration['audit_context_id'])
        self.assertEqual(set(report['artifacts']),set(r.FILES))
        for path,digest in report['artifacts'].items():self.assertEqual(digest,p.ei.digest((p.ei.ROOT/path).read_text()),path)
        for manifest in report['source_selection_summary']['context']['source_files']:
            self.assertEqual(manifest['file_sha256'],pin['files'][manifest['table']]['file_sha256'])
            self.assertEqual(manifest['rows'],pin['files'][manifest['table']]['rows'])
        self.assertEqual(report['status'],'COMPLETED_PARTITIONED_QUERY')
        self.assertEqual(report['batch_outcomes'],{'COMPLETED_BATCH':964})
        self.assertEqual(report['anchors_verified_against_unpartitioned_sql'],944)
        self.assertEqual(sum(report['anchor_outcomes'].values()),944);self.assertEqual(sum(report['stay_outcomes'].values()),140)
        self.assertEqual(report['audit_summary']['claims_verified'],2022);self.assertEqual(report['review_summary']['unique_accepted_claims'],2022)
        self.assertTrue(report['real_source_mixed_query_executed']);self.assertTrue(report['search_complete_over_selected_records'])
        self.assertFalse(report['clinical_mapping_verified']);self.assertFalse(report['patient_rows_or_identifiers_included'])


if __name__=='__main__':unittest.main()
