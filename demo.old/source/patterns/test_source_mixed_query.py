"""Source review boundaries, row accounting, independent SQL agreement and whole-run failure gates."""
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
from . import source_mixed_query as p, source_mixed_reference as sql, exact_intervals as ei, claim_rdf as cr
from . import mimic_claim_import as inputs, mimic_measurement_import as measurements

FOLDER = ei.ROOT / 'examples/source-mixed-query'


def synthetic_review(prepared):
    """Test-only acceptance of fabricated rows, never a production default."""
    review = deepcopy(prepared['review_template'])
    for ie, me, row in zip(prepared['interval_import']['episodes'], prepared['measurement_import']['episodes'], review['episodes']):
        for episode, name in ((ie, 'interval_policy'), (me, 'measurement_policy')):
            if episode['store']:
                row[name]['decisions'] = [{'id': 'test_' + str(n), 'claim_id': c['id'], 'claim_sha256': cr.digest(c),
                    'action': 'accept', 'supersedes': None, 'reason': 'Fabricated test record selection'}
                    for n, c in enumerate(episode['store']['claims'])]
        if row['alignment']:
            row['alignment']['bindings'] = [{'patient_id': ie['patient_id'], 'interval_clock_id': 'patient_' + ie['patient_id'],
                'measurement_clock_id': 'patient_' + ie['patient_id'], 'basis': 'same_dataset_patient_calendar',
                'reason': 'Constructed same-calendar test fixture'}]
    return review


class SourceMixedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.request = json.loads((FOLDER / 'request.json').read_text())
        cls.prepared = p.prepare(FOLDER, cls.request)
        cls.review = synthetic_review(cls.prepared)
        cls.completed = p.evaluate(cls.prepared, cls.review, FOLDER)

    def evaluate(self, review=None, prepared=None, folder=FOLDER):
        return p.evaluate(prepared or self.prepared, review or self.review, folder)

    def changed_request(self, change):
        request = deepcopy(self.request); change(request)
        prepared = p.prepare(FOLDER, request)
        return self.evaluate(synthetic_review(prepared), prepared)

    def test_complete_pipeline_real_rust_and_sql(self):
        r = self.completed
        self.assertEqual(r['status'], 'COMPLETED_VERIFIED_SOURCE_QUERY')
        self.assertEqual(r['certain_patient_ids'], ['1', '2', '3'])
        self.assertEqual(r['certain_patient_ids'], r['possible_patient_ids'])
        self.assertEqual(r['selected_claim_counts'], {'interval': 3, 'measurement': 6})
        for e in r['episodes']:
            self.assertTrue(e['comparison_passed'])
            if e['mixed_result']:
                self.assertEqual(e['mixed_result']['interval_view']['semantic_run']['status'], 'READY')

    def test_empty_stays_and_full_row_accounting(self):
        r = self.completed
        self.assertEqual([(e['patient_id'], e['episode_id']) for e in r['episodes']], [('1','100'),('1','101'),('2','200'),('3','300'),('4','400')])
        self.assertEqual([e['episode_id'] for e in r['episodes'] if not e['reference_bindings']], ['101','400'])
        for name, total, pending in [('interval_import',5,3),('measurement_import',9,6)]:
            s = r[name]['summary']; self.assertEqual(s['input_rows'],total)
            self.assertEqual(sum(s['import_outcome_counts'].values()),total)
            self.assertEqual(s['import_outcome_counts']['PENDING_CLAIM'],pending)
            self.assertTrue(s['reconciliation_complete'])

    def test_missing_and_lower_followup_do_not_remove_eligibility(self):
        episodes = {e['patient_id']: e for e in self.completed['episodes'] if e['reference_bindings']}
        self.assertEqual([r[5] for r in episodes['1']['reference_bindings']], ['10','14'])
        self.assertEqual(episodes['2']['reference_bindings'][0][4:], [None,None,None])
        self.assertEqual(episodes['3']['reference_bindings'][0][5], '-7')

    def test_prepare_never_accepts_or_aligns(self):
        for row in self.prepared['review_template']['episodes']:
            for name in ('interval_policy','measurement_policy'):
                if row[name]: self.assertEqual(row[name]['decisions'], [])
            if row['alignment']: self.assertEqual(row['alignment']['bindings'], [])
        for name in ('interval_import','measurement_import'):
            self.assertEqual(self.prepared[name]['summary']['accepted_claims'],0)

    def test_pending_template_has_no_selected_matches(self):
        r = self.evaluate(self.prepared['review_template'])
        self.assertEqual(r['status'], 'COMPLETED_VERIFIED_SOURCE_QUERY')
        self.assertEqual(r['certain_patient_ids'], [])
        self.assertEqual(r['selected_claim_counts'], {'interval':0,'measurement':0})

    def test_missing_alignment_blocks_whole_cohort(self):
        review=deepcopy(self.review);review['episodes'][0]['alignment']['bindings']=[]
        r=self.evaluate(review)
        self.assertEqual(r['status'],'BLOCKED_INCOMPLETE_SOURCE_QUERY')
        self.assertIsNone(r['certain_patient_ids']);self.assertIsNone(r['eligible_stays'])
        self.assertEqual(r['episodes'][0]['status'],'BLOCKED_COMPARISON')
        self.assertTrue(any(e['comparison_passed'] for e in r['episodes'][1:]))

    def test_stale_review_and_stale_store_decision_rejected(self):
        for mutation in [lambda r:r.update(preparation_context_id='0'*64),
                         lambda r:r['episodes'][0]['interval_policy'].update(store_sha256='0'*64),
                         lambda r:r['episodes'][0]['measurement_policy']['decisions'][0].update(claim_sha256='0'*64)]:
            with self.subTest(mutation=mutation):
                review=deepcopy(self.review);mutation(review)
                with self.assertRaises(ei.ContractError):self.evaluate(review)

    def test_review_roster_must_be_exact(self):
        for episodes in [self.review['episodes'][:-1],self.review['episodes']+self.review['episodes'][:1],list(reversed(self.review['episodes']))]:
            review={**self.review,'episodes':episodes}
            with self.assertRaisesRegex(ei.ContractError,'ROSTER'):self.evaluate(review)

    def test_policy_on_empty_stay_and_absent_policy_rejected(self):
        for index,name,value in [(1,'interval_policy',self.review['episodes'][0]['interval_policy']),(0,'interval_policy',None),(0,'alignment',None)]:
            review=deepcopy(self.review);review['episodes'][index][name]=value
            with self.assertRaises(ei.ContractError):self.evaluate(review)

    def test_withdrawal_removes_only_followup_binding(self):
        review=deepcopy(self.review);policy=review['episodes'][0]['measurement_policy']
        parent=policy['decisions'][1]
        policy['decisions'].append({**parent,'id':'withdraw','action':'withdraw','supersedes':parent['id']})
        r=self.evaluate(review)
        self.assertEqual(r['certain_patient_ids'],['1','2','3'])
        self.assertEqual(len(r['episodes'][0]['reference_bindings']),1)
        self.assertEqual(r['episodes'][0]['reference_bindings'][0][5],'14')

    def test_reject_baseline_does_not_reactivate_it(self):
        review=deepcopy(self.review);review['episodes'][0]['measurement_policy']['decisions'][0]['action']='reject'
        r=self.evaluate(review)
        self.assertEqual(r['certain_patient_ids'],['2','3'])

    def test_sql_disagreement_suppresses_all_authoritative_ids(self):
        with patch.object(sql,'execute',return_value=[]):r=self.evaluate()
        self.assertEqual(r['status'],'BLOCKED_INCOMPLETE_SOURCE_QUERY')
        self.assertIsNone(r['certain_patient_ids']);self.assertIsNone(r['possible_patient_ids'])
        self.assertFalse(r['search_complete_over_selected_records'])

    def test_mixed_backend_failure_is_not_a_negative_match(self):
        with patch.object(p.mixed,'execute',return_value={'status':'BLOCKED_INTERVAL_VIEW'}):r=self.evaluate()
        self.assertEqual(r['episodes'][0]['status'],'BLOCKED_MIXED_QUERY')
        self.assertIsNone(r['eligible_stays'])

    def test_wrong_graph_patient_set_fails_comparison(self):
        real=p.mixed.execute
        def wrong(*a,**kw):
            r=real(*a,**kw);r['certain_patient_ids']=[];return r
        with patch.object(p.mixed,'execute',side_effect=wrong):r=self.evaluate()
        self.assertEqual(r['episodes'][0]['reason'],'PATIENT_SET_DISAGREEMENT')
        self.assertIsNone(r['certain_patient_ids'])

    def test_resource_block_preserves_roster_and_row_ledger(self):
        for module in (inputs,measurements):
            with self.subTest(module=module),patch.object(module,'MAX_CLAIMS',0):
                prepared=p.prepare(FOLDER,self.request)
                r=self.evaluate(synthetic_review(prepared),prepared)
                self.assertEqual(len(r['episodes']),5)
                self.assertIsNone(r['certain_patient_ids'])
                self.assertEqual(r['episodes'][0]['status'],'BLOCKED_IMPORT')

    def test_mixed_work_limit_blocks_without_partial_answer(self):
        with patch.object(p.mixed,'MAX_BASELINE_TESTS',0):r=self.evaluate()
        self.assertEqual(r['episodes'][0]['status'],'BLOCKED_MIXED_QUERY')
        self.assertIsNone(r['certain_patient_ids'])

    def test_changed_source_requires_new_review(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(FOLDER,d,dirs_exist_ok=True)
            path=Path(d)/'chartevents.csv';path.write_text(path.read_text().replace(',58,58,',',57,57,'))
            with self.assertRaisesRegex(ei.ContractError,'STALE_PREPARATION_REVIEW'):p.run(d,self.request,self.review)
            with self.assertRaisesRegex(ei.ContractError,'REFERENCE_SOURCE_CHANGED'):self.evaluate(folder=d)

    def test_importer_projection_mutation_detected_against_raw_source(self):
        prepared=deepcopy(self.prepared)
        e=prepared['measurement_import']['episodes'][0]
        e['store']['claims'][0]['bundle']['events'][0]['value_lexical']='57'
        e['store_sha256']=cr.digest(e['store']);e['acceptance_policy']['store_sha256']=e['store_sha256']
        prepared['review_template']['episodes'][0]['measurement_policy']['store_sha256']=e['store_sha256']
        prepared['review_template']['episodes'][0]['alignment']['measurement_store_sha256']=e['store_sha256']
        r=self.evaluate(synthetic_review(prepared),prepared)
        self.assertEqual(r['episodes'][0]['status'],'BLOCKED_REFERENCE_DISAGREEMENT')

    def test_request_rejects_scope_and_selector_mismatches(self):
        mutations=[lambda r:r['measurement_import'].update(dataset_id='other'),
            lambda r:r['measurement_import'].update(patient_ids=['1']),
            lambda r:r['query'].update(treatment_class_iri='https://example.org/clinical/Drug'),
            lambda r:r['query']['baseline'].update(item_ids=['9999']),lambda r:r.update(extra=True)]
        for mutation in mutations:
            request=deepcopy(self.request);mutation(request)
            with self.subTest(mutation=mutation),self.assertRaises(ei.ContractError):p.prepare(FOLDER,request)

    def test_query_changes_are_bound_to_review(self):
        request=deepcopy(self.request);request['query']['baseline']['value_lexical']='61'
        with self.assertRaisesRegex(ei.ContractError,'STALE_PREPARATION_REVIEW'):p.run(FOLDER,request,self.review)

    def test_baseline_inclusive_window_and_scalar_variants(self):
        for operator,value,expected in [('lt','60',['1']),('le','60',['1','2']),('eq','60',['2']),('ge','60',['2','3']),('gt','62',[])]:
            with self.subTest(operator=operator):
                r=self.changed_request(lambda q:q['query']['baseline'].update(operator=operator,value_lexical=value))
                self.assertEqual(r['certain_patient_ids'],expected)
        r=self.changed_request(lambda q:q['query']['baseline'].update(min_before_start_us=1200000000,max_before_start_us=1200000000))
        self.assertEqual(r['certain_patient_ids'],['1'])

    def test_followup_anchor_window_and_halfopen_interval(self):
        r=self.changed_request(lambda q:q['query']['followup'].update(anchor='end'))
        self.assertEqual([b[5] for b in r['episodes'][0]['reference_bindings']],['14'])
        r=self.changed_request(lambda q:q['query']['followup'].update(within_interval=True))
        self.assertEqual([b[5] for b in r['episodes'][0]['reference_bindings']],['10'])
        self.assertEqual(r['certain_patient_ids'],['1','2','3'])

    def test_exact_decimal_sql_has_no_float_rounding(self):
        self.assertEqual(sql.difference('100000000000000000000.00000000000000000001','100000000000000000000.00000000000000000002'),'0.00000000000000000001')
        self.assertEqual(sql.compare('1.00000000000000000001','gt','1'),1)

    def test_sql_halfopen_end_and_no_self_followup(self):
        q=deepcopy(self.request['query']);q['baseline'].update(min_before_start_us=0,max_before_start_us=0)
        q['followup'].update(min_after_anchor_us=0,max_after_anchor_us=3600000000,within_interval=True)
        interval={'id':'t','subject_id':'1','stay_id':'1','itemid':'1000','starttime':'2150-01-01 10:00:00','endtime':'2150-01-01 11:00:00'}
        b={'id':'b','subject_id':'1','stay_id':'1','itemid':'2000','charttime':'2150-01-01 10:00:00','valuenum':'60','valueuom':'mmHg'}
        f={**b,'id':'f','charttime':interval['endtime'],'valuenum':'70'}
        self.assertEqual(sql.execute([interval],[b,f],'1000',q)[0][4:],[None,None,None])
        q['followup']['within_interval']=False
        self.assertEqual(sql.execute([interval],[b,f],'1000',q)[0][4:],['f','10','mmHg'])

    def test_sql_does_not_join_across_patient_or_stay(self):
        rows=p.raw_rows(self.prepared,FOLDER)
        ie=self.prepared['interval_import']['episodes'][0];me=self.prepared['measurement_import']['episodes'][0]
        ir=p.reference_rows(ie,[c['id'] for c in ie['store']['claims']],rows['inputevents'],'input_')
        mr=p.reference_rows(me,[c['id'] for c in me['store']['claims']],rows['chartevents'],'chart_')
        for field in ('subject_id','stay_id'):
            wrong=deepcopy(mr)
            for r in wrong:r[field]='999'
            self.assertEqual(sql.execute(ir,wrong,'1000',self.request['query']),[])

    def test_inputs_immutable_and_scope_flags_explicit(self):
        before=cr.digest([self.prepared,self.review]);r=self.evaluate()
        self.assertEqual(before,cr.digest([self.prepared,self.review]))
        for k in ('clinical_mapping_verified','causal_effect_estimated','source_history_verified','physical_elapsed_time_verified','full_mixed_owl_reasoning_verified'):
            self.assertFalse(r[k])
        self.assertEqual(r['clinical_knowledge_status'],'UNKNOWN')

    def test_cli_preparation_review_and_atomic_failures(self):
        with tempfile.TemporaryDirectory() as d,redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            output=Path(d)/'result.json'
            self.assertEqual(p.main(['--output',str(output)]),0)
            self.assertEqual(json.loads(output.read_text())['status'],'PREPARED_PENDING_REVIEW')
            self.assertEqual(p.main(['--review',str(FOLDER/'synthetic-review.json'),'--output',str(output)]),0)
            before=output.read_bytes()
            bad=Path(d)/'bad.json';bad.write_text('{}')
            self.assertEqual(p.main(['--review',str(bad),'--output',str(output)]),2)
            self.assertEqual(output.read_bytes(),before)
            self.assertEqual(p.main(['--output',str(FOLDER/'chartevents.csv')]),2)
            self.assertEqual(sorted(x.name for x in Path(d).iterdir()),['bad.json','result.json'])

    def test_committed_report_reproduces(self):
        from .verify_source_mixed_query import verify
        self.assertEqual(verify(),json.loads((ei.ROOT/'verification/source-mixed-query-report.json').read_text()))


if __name__=='__main__':unittest.main()
