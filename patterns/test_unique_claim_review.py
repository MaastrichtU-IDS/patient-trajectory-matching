"""Unique review, deterministic propagation, revision gates and complete source execution."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from . import unique_claim_review as u
from .test_partitioned_window_query import accept_review

p = u.p


def resolve(package, action='accept'):
    """Explicit decisions for fabricated test sources only."""
    review = u.review_template(package); review['reviewer'] = 'Synthetic fixture reviewer'
    for n, c in enumerate(review['claims']):
        c['decisions'] = [{'id': 'decision_' + str(n), 'action': action, 'supersedes': None,
                           'reason': 'Review of this constructed source record'}]
    for c in review['calendars']:
        c.update(decision='same_patient_calendar', reason='Constructed source calendars agree for this patient')
    return review


class UniqueReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)/'source'; shutil.copytree(p.ei.ROOT/'examples/source-mixed-query', self.folder)
        self.request = json.loads((p.ei.ROOT/'examples/indexed-source-windows/synthetic-request.json').read_text())

    def setup_package(self):
        s = p.windows.run(self.folder, self.request); plan = p.plan(s)
        return s, plan, u.prepare(s, plan)

    def rewrite(self, rows, table='chartevents'):
        path = self.folder/(table + '.csv')
        with path.open() as h: header = csv.DictReader(h).fieldnames
        with path.open('w', newline='') as h:
            w = csv.DictWriter(h, fieldnames=header, lineterminator='\n'); w.writeheader(); w.writerows(rows)

    def large(self):
        with (self.folder/'chartevents.csv').open() as h: base = next(csv.DictReader(h))
        rows = [base] + [{**base, 'charttime':f'2150-01-01 10:{i:02}:00', 'value':'70', 'valuenum':'70'} for i in range(1,38)]
        self.rewrite(rows)
        return self.setup_package()

    def test_package_unique_claims_and_complete_memberships(self):
        s, plan, package = self.large(); summary = package['summary']; claims = package['context']['claims']
        self.assertEqual(summary['unique_claims'], 41)
        self.assertEqual(summary['unique_claims_by_kind'], {'interval':3,'measurement':38})
        self.assertGreater(summary['batch_claim_occurrences'], 41)
        self.assertEqual(summary['repeated_occurrences_saved'], summary['batch_claim_occurrences']-41)
        for descriptor in plan['batches']:
            built = p.build_batch(s, plan, descriptor)
            expected = {c['id'] for k in ('interval','measurement') if built[k+'_store'] for c in built[k+'_store']['claims']}
            observed = {c['claim_id'] for c in claims if descriptor['id'] in c['batch_ids']}
            self.assertEqual(expected, observed)
        self.assertEqual(summary['stays'],5)

    def test_source_evidence_and_clock_origins_preserved(self):
        s, plan, package = self.setup_package()
        for row in package['context']['claims']:
            self.assertEqual(row['claim_sha256'], p.cr.digest(row['claim']))
            self.assertEqual(row['source_evidence']['file_sha256'], row['claim']['source_sha256'])
            self.assertIn('row_sha256', row['source_evidence'])
            self.assertTrue(row['clock']['origin'])
        point = next(c for c in package['context']['claims'] if c['kind']=='measurement')
        self.assertEqual(point['source_record']['valueuom'],'mmHg')

    def test_pending_template_has_no_implicit_acceptance_or_alignment(self):
        s, plan, package = self.setup_package(); review = u.review_template(package)
        self.assertIsNone(review['reviewer']); self.assertTrue(all(not c['decisions'] for c in review['claims']))
        with patch.object(p.mixed,'execute',side_effect=AssertionError('No query during compile')):
            r = u.compile_review(s,plan,review)
        self.assertEqual(r['status'],'PENDING_REVIEW'); self.assertEqual(r['summary']['unique_accepted_claims'],0)
        self.assertEqual(r['summary']['unique_claim_outcomes'],{'pending':9})
        self.assertTrue(all(not b['alignment']['bindings'] for b in r['compiled_review']['batches']))

    def test_complete_synthetic_execution_matches_existing_explicit_review(self):
        s,plan,package=self.setup_package();review=resolve(package)
        r=u.run(self.folder,self.request,review,execute=True)
        expected=p.execute(s,plan,accept_review(s,plan))
        self.assertEqual(r['execution']['bindings'],expected['bindings'])
        self.assertEqual(r['execution']['certain_patient_ids'],['1','2','3'])
        self.assertEqual(r['compilation']['summary']['unique_accepted_claims'],9)
        self.assertEqual(len(r['execution']['roster']),5)
        self.assertTrue(any(row[4] is None for row in r['execution']['bindings']))

    def test_oversized_execution_one_decision_propagates_everywhere(self):
        s,plan,package=self.large();review=resolve(package);c=u.compile_review(s,plan,review)
        r=p.execute(s,plan,c['compiled_review'])
        self.assertEqual(r['status'],'COMPLETED_PARTITIONED_QUERY'); self.assertEqual(len(r['bindings']),37)
        self.assertEqual(r['unique_accepted_claims'],41)
        for claim in review['claims']:
            copies=[d for b in c['compiled_review']['batches'] for k in ('interval','measurement')
                    if b[k+'_policy'] for d in b[k+'_policy']['decisions'] if d['claim_id']==claim['claim_id']]
            self.assertTrue(copies);self.assertEqual(len({p.cr.digest(d) for d in copies}),1)

    def test_withdrawal_retracts_repeated_claim_from_every_batch(self):
        s,plan,package=self.large();review=resolve(package)
        claim=next(c for c in review['claims'] if c['claim_id'].startswith('claim_chart_') and c['claim_id'].endswith('_2'))
        claim['decisions'].append({'id':'withdraw_point','action':'withdraw','supersedes':claim['decisions'][0]['id'],'reason':'Withdraw constructed reading'})
        c=u.compile_review(s,plan,review);_,states=p._review(s,plan,c['compiled_review'])
        self.assertFalse(states[claim['claim_id']][1]);self.assertEqual(c['summary']['unique_accepted_claims'],40)
        self.assertEqual(c['summary']['unique_claim_outcomes']['withdraw'],1)

    def test_reject_then_accept_retains_history(self):
        s,plan,package=self.setup_package();review=resolve(package,'reject');claim=review['claims'][0]
        claim['decisions'].append({'id':'reconsider','action':'accept','supersedes':claim['decisions'][0]['id'],'reason':'Correct reviewed record interpretation'})
        r=u.compile_review(s,plan,review)
        self.assertEqual(r['summary']['unique_accepted_claims'],1);self.assertEqual(r['review'],review)

    def test_complete_rejection_is_explicit_empty_selected_view(self):
        s,plan,package=self.setup_package();review=resolve(package,'reject');r=u.run(self.folder,self.request,review,execute=True)
        self.assertEqual(r['compilation']['status'],'REVIEW_COMPLETE')
        self.assertEqual(r['execution']['certain_patient_ids'],[]);self.assertEqual(r['execution']['clinical_knowledge_status'],'UNKNOWN')

    def test_pending_claim_or_calendar_prevents_workflow_execution(self):
        s,plan,package=self.setup_package()
        for field in ('claim','calendar'):
            review=resolve(package)
            if field=='claim':review['claims'][0]['decisions']=[]
            else:review['calendars'][0].update(decision='pending',reason=None)
            with self.subTest(field=field),patch.object(p,'execute',side_effect=AssertionError('No execution')):
                self.assertEqual(u.compile_review(s,plan,review)['status'],'PENDING_REVIEW')
                with self.assertRaisesRegex(p.ei.ContractError,'UNFINISHED_UNIQUE_REVIEW'):u.run(self.folder,self.request,review,execute=True)

    def test_not_aligned_remains_incomparable_and_blocks_complete_answer(self):
        s,plan,package=self.setup_package();review=resolve(package)
        review['calendars'][0].update(decision='not_aligned',reason='Compatibility could not be established')
        r=u.run(self.folder,self.request,review,execute=True)
        self.assertEqual(r['compilation']['status'],'REVIEW_COMPLETE')
        self.assertEqual(r['execution']['status'],'BLOCKED_INCOMPLETE_PARTITIONED_QUERY')
        self.assertIsNone(r['execution']['certain_patient_ids'])

    def test_changed_source_and_request_invalidate_review(self):
        s,plan,package=self.setup_package();review=resolve(package)
        changed=deepcopy(self.request);changed['query']['baseline']['value_lexical']='64'
        with self.assertRaisesRegex(p.ei.ContractError,'STALE_UNIQUE_REVIEW'):u.run(self.folder,changed,review)
        with (self.folder/'chartevents.csv').open() as h:rows=list(csv.DictReader(h))
        rows[0]['value']=rows[0]['valuenum']='57';self.rewrite(rows)
        with self.assertRaisesRegex(p.ei.ContractError,'STALE_UNIQUE_REVIEW'):u.run(self.folder,self.request,review)

    def test_stale_hash_missing_extra_duplicate_and_reordered_claims_refused(self):
        s,plan,package=self.setup_package();base=resolve(package)
        for mode in ('hash','missing','extra','duplicate','order'):
            r=deepcopy(base)
            if mode=='hash':r['claims'][0]['claim_sha256']='0'*64
            if mode=='missing':r['claims'].pop()
            if mode=='extra':r['claims'].append(deepcopy(r['claims'][0]))
            if mode=='duplicate':r['claims'][1]=deepcopy(r['claims'][0])
            if mode=='order':r['claims'].reverse()
            with self.subTest(mode=mode),self.assertRaisesRegex(p.ei.ContractError,'UNIQUE_REVIEW_CLAIM_COVERAGE'):u.compile_review(s,plan,r)

    def test_calendar_roster_and_reason_validation(self):
        s,plan,package=self.setup_package();base=resolve(package)
        for mode in ('missing','duplicate','foreign','whitespace','pending_reason'):
            r=deepcopy(base)
            if mode=='missing':r['calendars'].pop()
            if mode=='duplicate':r['calendars'][1]=deepcopy(r['calendars'][0])
            if mode=='foreign':r['calendars'][0]['patient_id']='999'
            if mode=='whitespace':r['calendars'][0]['reason']='  '
            if mode=='pending_reason':r['calendars'][0]['decision']='pending'
            with self.subTest(mode=mode),self.assertRaises(p.ei.ContractError):u.compile_review(s,plan,r)

    def test_reviewer_required_for_any_record_or_calendar_decision(self):
        s,plan,package=self.setup_package()
        for action in ('record','calendar'):
            r=u.review_template(package)
            if action=='record':r['claims'][0]['decisions']=resolve(package)['claims'][0]['decisions']
            else:r['calendars'][0].update(decision='not_aligned',reason='Unknown compatibility')
            with self.subTest(action=action),self.assertRaisesRegex(p.ei.ContractError,'MISSING_REVIEWER'):u.compile_review(s,plan,r)

    def test_duplicate_decision_ids_and_cross_claim_parents_refused(self):
        s,plan,package=self.setup_package();base=resolve(package)
        r=deepcopy(base);r['claims'][1]['decisions'][0]['id']=r['claims'][0]['decisions'][0]['id']
        with self.assertRaisesRegex(p.ei.ContractError,'DUPLICATE_UNIQUE_DECISION_ID'):u.compile_review(s,plan,r)
        r=deepcopy(base);r['claims'][1]['decisions'][0]['supersedes']=r['claims'][0]['decisions'][0]['id']
        with self.assertRaisesRegex(p.ei.ContractError,'CROSS_CLAIM_REVIEW_PARENT'):u.compile_review(s,plan,r)

    def test_forks_cycles_multiple_roots_and_unparented_withdrawal_refused(self):
        s,plan,package=self.setup_package()
        for mode in ('fork','cycle','roots','withdraw'):
            r=resolve(package);ds=r['claims'][0]['decisions'];old=ds[0]
            if mode=='withdraw':old['action']='withdraw'
            if mode=='roots':ds.append({**old,'id':'new'})
            if mode=='cycle':old['supersedes']='new';ds.append({**old,'id':'new','supersedes':old['id']})
            if mode=='fork':ds.extend([{**old,'id':k,'supersedes':old['id']} for k in ('child1','child2')])
            with self.subTest(mode=mode),self.assertRaises(p.ei.ContractError):u.compile_review(s,plan,r)

    def test_batch_decision_limit_is_not_relaxed_by_compiler(self):
        s,plan,package=self.large();review=resolve(package)
        for n,c in enumerate(review['claims']):
            for i in range(1,9):c['decisions'].append({'id':f'history_{n}_{i}','action':'accept','supersedes':c['decisions'][-1]['id'],'reason':'Synthetic history'})
        with self.assertRaisesRegex(p.ei.ContractError,'INVALID_CLAIM_POLICY_SCHEMA'):u.compile_review(s,plan,review)
        with patch.object(p,'MAX_REVIEW_BYTES',1),self.assertRaisesRegex(p.ei.ContractError,'COMPILED_REVIEW_SIZE_LIMIT'):
            u.compile_review(s,plan,resolve(package))

    def test_blocked_plan_cannot_be_packaged_as_complete(self):
        s,plan,package=self.setup_package()
        with patch.object(p,'MAX_BATCHES',1):blocked=p.plan(s)
        with self.assertRaisesRegex(p.ei.ContractError,'INCOMPLETE_REVIEW_PLAN'):u.prepare(s,blocked)

    def test_empty_windows_need_no_invented_calendar(self):
        self.rewrite([]);s,plan,package=self.setup_package()
        self.assertEqual(package['summary']['unique_claims'],3);self.assertEqual(package['summary']['calendar_reviews'],0)
        r=u.run(self.folder,self.request,resolve(package),execute=True)
        self.assertEqual(r['execution']['certain_patient_ids'],[]);self.assertEqual(len(r['execution']['roster']),5)

    def test_zero_anchors_retains_stay_roster(self):
        self.rewrite([],table='inputevents');s,plan,package=self.setup_package()
        self.assertEqual(package['summary']['unique_claims'],0);self.assertEqual(package['summary']['stays'],5)
        r=u.run(self.folder,self.request,u.review_template(package),execute=True)
        self.assertEqual(r['execution']['certain_patient_ids'],[]);self.assertEqual(len(r['execution']['roster']),5)

    def test_schema_is_closed_and_reasons_cannot_be_whitespace(self):
        s,plan,package=self.setup_package()
        for mode in ('extra','nested','whitespace'):
            r=resolve(package)
            if mode=='extra':r['approve_all']=True
            if mode=='nested':r['claims'][0]['decisions'][0]['claim_id']='override'
            if mode=='whitespace':r['claims'][0]['decisions'][0]['reason']='  '
            with self.subTest(mode=mode),self.assertRaises(p.ei.ContractError):u.compile_review(s,plan,r)

    def test_determinism_input_immutability_and_audit_hashes(self):
        s,plan,package=self.setup_package();r=resolve(package);before=p.cr.digest([s,plan,package,r])
        a=u.compile_review(s,plan,r);b=u.compile_review(s,plan,r)
        self.assertEqual(a,b);self.assertEqual(before,p.cr.digest([s,plan,package,r]))
        self.assertEqual(a['review_sha256'],p.cr.digest(r));self.assertEqual(a['compiled_review_sha256'],p.cr.digest(a['compiled_review']))
        self.assertEqual(package['context_id'],p.cr.digest(package['context']))
        self.assertFalse(a['summary']['reviewer_identity_verified']);self.assertFalse(a['summary']['clinical_mapping_verified'])

    def test_cli_prepare_compile_execute_and_atomic_failure(self):
        out=Path(self.temp.name)/'output.json';rp=Path(self.temp.name)/'review.json'
        args=['--input-dir',str(self.folder),'--output',str(out)]
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            self.assertEqual(u.main(args),0);prepared=json.loads(out.read_text());rp.write_text(json.dumps(prepared['review_template']))
            self.assertEqual(u.main(args+['--review',str(rp)]),0)
            before=out.read_bytes();self.assertEqual(u.main(args+['--review',str(rp),'--execute']),2);self.assertEqual(before,out.read_bytes())
            rp.write_text(json.dumps(resolve(prepared['package'])))
            self.assertEqual(u.main(args+['--review',str(rp),'--execute']),0)
            self.assertEqual(json.loads(out.read_text())['execution']['status'],'COMPLETED_PARTITIONED_QUERY')
            before=out.read_bytes();rp.write_text('{}');self.assertEqual(u.main(args+['--review',str(rp)]),2);self.assertEqual(before,out.read_bytes())

    def test_cli_protects_source_request_review_and_size_limits(self):
        out=Path(self.temp.name)/'output.json';out.write_text('old');rp=Path(self.temp.name)/'review.json';rp.write_text('{}')
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            for target in (self.folder/'chartevents.csv',self.folder/'inputevents.csv',rp):
                before=target.read_bytes()
                self.assertEqual(u.main(['--input-dir',str(self.folder),'--review',str(rp),'--output',str(target)]),2)
                self.assertEqual(before,target.read_bytes())
            with patch.object(u,'MAX_OUTPUT_BYTES',1):self.assertEqual(u.main(['--input-dir',str(self.folder),'--output',str(out)]),2)
            with patch.object(p,'MAX_REVIEW_BYTES',1):self.assertEqual(u.main(['--input-dir',str(self.folder),'--review',str(rp),'--output',str(out)]),2)
            self.assertEqual(out.read_text(),'old')

    def test_execution_requires_review_and_backend_failure_stays_blocked(self):
        with self.assertRaisesRegex(p.ei.ContractError,'EXECUTION_REQUIRES_UNIQUE_REVIEW'):u.run(self.folder,self.request,execute=True)
        s,plan,package=self.setup_package()
        with patch.object(p.mixed,'execute',side_effect=p.ei.ContractError('RESOURCE_LIMIT')):
            r=u.run(self.folder,self.request,resolve(package),execute=True)
        self.assertEqual(r['execution']['status'],'BLOCKED_INCOMPLETE_PARTITIONED_QUERY');self.assertIsNone(r['execution']['bindings'])


    def test_one_point_review_propagates_across_distinct_anchors(self):
        with (self.folder/'inputevents.csv').open() as h: rows=list(csv.DictReader(h))
        rows.append({**rows[0], 'starttime':'2150-01-01 10:05:00', 'endtime':'2150-01-01 10:50:00', 'orderid':'250', 'linkorderid':'250'})
        self.rewrite(rows,table='inputevents');s,plan,package=self.setup_package()
        point=next(c for c in package['context']['claims'] if c['kind']=='measurement' and c['claim_id'].endswith('_2'))
        self.assertEqual(len(point['anchor_ids']),2);self.assertEqual(len(point['batch_ids']),2)
        self.assertEqual(package['summary']['unique_claims'],10)
        review=resolve(package);c=next(c for c in review['claims'] if c['claim_id']==point['claim_id']);c['decisions'][0]['action']='reject'
        compiled=u.compile_review(s,plan,review);_,states=p._review(s,plan,compiled['compiled_review'])
        self.assertFalse(states[point['claim_id']][1]);self.assertEqual(compiled['summary']['unique_accepted_claims'],9)

    def test_committed_synthetic_review_reproduces_report(self):
        path=p.ei.ROOT/'examples/unique-claim-review/synthetic-review.json'
        review=json.loads(path.read_text());r=u.run(self.folder,self.request,review,execute=True)
        report=json.loads((p.ei.ROOT/'verification/unique-claim-review-synthetic-report.json').read_text())
        self.assertEqual(report['review_file_sha256'],p.ei.digest(path.read_text()))
        self.assertEqual(report['package_context_id'],r['package']['context_id'])
        self.assertEqual(report['compilation_summary'],r['compilation']['summary'])
        self.assertEqual(report['compiled_review_sha256'],r['compilation']['compiled_review_sha256'])
        self.assertEqual(report['bindings'],r['execution']['bindings'])
        self.assertEqual(report['execution_status'],r['execution']['status'])

    def test_demo_report_provenance_and_complete_pending_compilation(self):
        from . import verify_unique_claim_review as verify
        report=json.loads((p.ei.ROOT/'verification/unique-claim-review-demo-report.json').read_text())
        self.assertEqual(report['verifier_sha256'],p.ei.digest(Path(verify.__file__).read_text()))
        self.assertEqual(report['source_pin_sha256'],p.ei.digest(verify.prior.PIN.read_text()))
        self.assertEqual(set(report['artifacts']),set(u.FILES))
        for path,digest in report['artifacts'].items():self.assertEqual(digest,p.ei.digest((p.ei.ROOT/path).read_text()),path)
        self.assertEqual(set(report['strata']),set(verify.prior.NAMES))
        for row in report['strata'].values():
            summary=row['package_summary'];compilation=row['compilation_summary']
            self.assertEqual(summary['anchors'],944);self.assertEqual(summary['stays'],140)
            self.assertEqual(summary['unique_claims_by_kind']['interval'],944)
            self.assertEqual(summary['batch_claim_occurrences']-summary['unique_claims'],summary['repeated_occurrences_saved'])
            self.assertEqual(summary['batches'],compilation['batches_validated'])
            self.assertEqual(compilation['unique_claim_outcomes'],{'pending':summary['unique_claims']})
            self.assertEqual(compilation['unique_accepted_claims'],0);self.assertEqual(row['status'],'PENDING_REVIEW')
        self.assertFalse(report['patient_rows_or_identifiers_included']);self.assertFalse(report['real_source_mixed_query_executed'])


if __name__=='__main__':unittest.main()
