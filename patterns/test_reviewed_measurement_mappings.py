"""Reviewed item-class selection, separate measurement strata and failure gates."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from . import reviewed_measurement_mappings as m, verify_reviewed_measurement_mappings as verify


class MeasurementMappingTests(unittest.TestCase):
    def setUp(self): self.values=verify.load_values()
    def run_query(self): return verify.execute(self.values)
    def plan(self): return m.plan(*(self.values[n] for n in ('catalogue','terminology','mappings','review','selector')))

    def author_changed_measurement_fixture(self):
        """Explicit test-data acceptance, never runtime propagation of a review."""
        store=self.values['measurement-store'];policy=self.values['measurement-policy']
        policy['store_sha256']=m.cr.digest(store)
        claims={c['id']:c for c in store['claims']}
        for d in policy['decisions']:
            d['claim_sha256']=m.cr.digest(claims[d['claim_id']]);d['reason']='Explicit authored mutation in synthetic test'
        self.values['alignment']['measurement_store_sha256']=m.cr.digest(store)

    def test_actual_rust_selects_catalogue_items_and_preserves_strata(self):
        result=self.run_query()
        self.assertEqual(result['status'],'COMPLETED_REVIEWED_MEASUREMENT_QUERY')
        self.assertEqual(result['certain_patient_ids'],['P1','P2'])
        self.assertEqual(result['possible_patient_ids'],['P1','P2'])
        self.assertEqual([s['item_id'] for s in result['strata']],['pressure_a','pressure_b'])
        p=result['selection_plan'];self.assertEqual(p['semantic_run']['status'],'READY')
        self.assertEqual(p['item_outcomes'][2]['status'],'NOT_ENTAILED_BY_MAPPING')
        for row in p['item_outcomes']:self.assertTrue(row['mapping_evidence']['decision_id'])
        self.assertTrue(result['search_complete_over_requested_strata'])

    def test_cross_item_followup_cannot_become_a_measurement_pair(self):
        result=self.run_query();a=result['strata'][0]['result']
        self.assertEqual([f['measurement_id'] for f in a['baseline_bindings'][0]['followup']],['followup1_P1'])
        self.assertNotIn('followup2_P1',a['measurement_filter']['followup_candidate_ids'])
        b=result['strata'][1]['result']
        self.assertIn('followup2_P1',b['measurement_filter']['followup_candidate_ids'])
        self.assertEqual(b['baseline_bindings'][0]['followup_status'],'NO_SELECTED_FOLLOWUP_IN_WINDOW')
        self.assertEqual(b['certain_patient_ids'],['P2'])

    def test_catalogue_probe_is_not_a_patient_or_concept_assertion(self):
        result=self.plan();ofn=result['semantic_run']['ontology_ofn']
        self.assertIn(m.PROBE_NS,ofn)
        self.assertNotIn('baseline_P1',ofn);self.assertNotIn('PatientRole',ofn)
        for term in self.values['terminology']['terms']:self.assertNotIn(term['concept_iri'],ofn)
        for claim in self.values['measurement-store']['claims']:self.assertEqual(claim['bundle']['semantic_facts'],[])

    def test_pending_or_withdrawn_mapping_blocks_all_backend_work(self):
        original=deepcopy(self.values['review'])
        for action in ('pending','withdraw'):
            self.values['review']=deepcopy(original)
            if action=='pending':self.values['review']['decisions'].pop()
            else:
                d=self.values['review']['decisions'][0]
                self.values['review']['decisions'].append({**d,'id':'withdraw-a','action':'withdraw','supersedes':d['id']})
            with patch.object(m.semantic,'check',side_effect=AssertionError('pending semantics')):
                result=self.run_query()
            self.assertEqual(result['status'],'BLOCKED_MAPPING_REVIEW')
            self.assertIsNone(result['certain_patient_ids']);self.assertEqual(result['strata'],[])

    def test_reaccepted_mapping_requires_new_selector_context(self):
        d=self.values['review']['decisions'][0]
        self.values['review']['decisions'].append({**d,'id':'reaffirm-a','supersedes':d['id']})
        with self.assertRaisesRegex(ValueError,'STALE_MEASUREMENT_MAPPING_SELECTOR'):self.run_query()
        compiled=m.mappings.compile_policy(*(self.values[n] for n in m.mappings.NAMES))
        self.values['selector']['mapping_context_id']=compiled['context_id']
        self.assertEqual(self.run_query()['status'],'COMPLETED_REVIEWED_MEASUREMENT_QUERY')

    def test_closed_selector_unknown_code_and_concept_selector_rejected(self):
        original=deepcopy(self.values['selector'])
        changes=[lambda s:s.update(extra=True),lambda s:s.update(source_item_ids=[]),
                 lambda s:s.update(source_item_ids=['unknown']),lambda s:s.update(source_item_ids=['pressure_a','pressure_a']),
                 lambda s:s.update(class_iri=self.values['terminology']['terms'][0]['concept_iri']),
                 lambda s:s.update(unit_lexical=' mmHg')]
        for change in changes:
            self.values['selector']=deepcopy(original);change(self.values['selector'])
            with self.assertRaises(ValueError):self.plan()

    def test_query_cannot_widen_scope_or_change_unit(self):
        original=deepcopy(self.values['query'])
        for side in ('baseline','followup'):
            for key,value in [('item_ids',['pressure_a']),('unit_lexical','kPa')]:
                self.values['query']=deepcopy(original);self.values['query'][side][key]=value
                with self.assertRaisesRegex(ValueError,'MEASUREMENT_QUERY_(SCOPE|UNIT)_MISMATCH'):self.run_query()

    def test_dataset_and_source_policies_are_separate_and_never_rewritten(self):
        self.values['catalogue']['dataset_id']='wrong'
        prop=self.values['mappings'];prop['catalogue_sha256']=m.cr.digest(self.values['catalogue'])
        self.values['review']['mappings_sha256']=m.cr.digest(prop)
        compiled=m.mappings.compile_policy(*(self.values[n] for n in m.mappings.NAMES))
        self.values['selector']['mapping_context_id']=compiled['context_id']
        with self.assertRaisesRegex(ValueError,'MEASUREMENT_CATALOGUE_DATASET_MISMATCH'):self.run_query()
        self.setUp();original=deepcopy(self.values)
        for name in ('measurement-policy','interval-policy'):
            self.values=deepcopy(original);self.values[name]['semantic_policy_sha256']='0'*64
            before=deepcopy(self.values[name])
            with self.assertRaisesRegex(ValueError,'STALE_CLAIM_SEMANTIC_POLICY'):self.run_query()
            self.assertEqual(before,self.values[name])

    def test_backend_timeout_or_disagreement_cannot_fall_back_to_literal_items(self):
        for response in ({'status':'BACKEND_TIMEOUT'},{'status':'OK','consistent':True,'memberships':{},'dropped':{},'warnings':[]}):
            with patch.object(m.semantic,'run_backend',return_value=response),patch.object(m.mixed,'execute',side_effect=AssertionError('No fallback')):
                result=self.run_query()
            self.assertEqual(result['status'],'BLOCKED_MEASUREMENT_MAPPING_SEMANTICS')
            self.assertIsNone(result['certain_patient_ids']);self.assertEqual(result['strata'],[])

    def test_failed_stratum_suppresses_combined_membership(self):
        real=m.mixed.execute;count=0
        def run(*args,**kwargs):
            nonlocal count
            count+=1
            return real(*args,**kwargs) if count==1 else {'status':'BLOCKED_MIXED_SOURCE'}
        with patch.object(m.mixed,'execute',side_effect=run):result=self.run_query()
        self.assertEqual(len(result['strata']),2)
        self.assertEqual(result['strata'][0]['result']['certain_patient_ids'],['P1'])
        self.assertEqual(result['status'],'BLOCKED_INCOMPLETE_MEASUREMENT_STRATA')
        self.assertIsNone(result['certain_patient_ids']);self.assertIsNone(result['possible_patient_ids'])
        self.assertFalse(result['search_complete_over_requested_strata'])

    def test_unit_spelling_is_exact_without_implicit_conversion(self):
        claim=next(c for c in self.values['measurement-store']['claims'] if c['id']=='claim_followup1_P1')
        claim['bundle']['events'][0]['unit_lexical']='mm[Hg]';self.author_changed_measurement_fixture()
        result=self.run_query();row=result['strata'][0]['result']['baseline_bindings'][0]
        self.assertEqual(row['followup_status'],'NO_SELECTED_FOLLOWUP_IN_WINDOW')
        self.assertEqual(result['certain_patient_ids'],['P1','P2'])
        self.assertFalse(result['context']['unit_conversion_performed'])

    def test_uncertain_measurement_keeps_possible_vs_certain_semantics(self):
        claim=next(c for c in self.values['measurement-store']['claims'] if c['id']=='claim_baseline_P2')
        v=claim['bundle']['variables'][0];v.update(local_lower='2150-01-01 09:20:00',local_upper='2150-01-01 10:20:00')
        self.author_changed_measurement_fixture();result=self.run_query()
        self.assertEqual(result['certain_patient_ids'],['P1']);self.assertEqual(result['possible_patient_ids'],['P1','P2'])
        self.assertEqual(result['strata'][1]['result']['baseline_bindings'][0]['eligibility']['status'],'POSSIBLE')

    def test_source_acceptance_and_alignment_still_control_eligibility(self):
        self.values['measurement-policy']['decisions']=[]
        result=self.run_query();self.assertEqual(result['certain_patient_ids'],[])
        self.setUp();self.values['alignment']['bindings']=[];result=self.run_query()
        self.assertEqual(result['certain_patient_ids'],[])
        self.assertEqual(result['strata'][0]['result']['baseline_bindings'][0]['eligibility']['status'],'INCOMPARABLE')

    def test_no_supported_item_is_not_a_completed_cohort_answer(self):
        self.values['selector']['source_item_ids']=['pressure_c']
        for side in ('baseline','followup'):self.values['query'][side]['item_ids']=['pressure_c']
        result=self.run_query()
        self.assertEqual(result['status'],'NO_SUPPORTED_MEASUREMENT_ITEMS')
        self.assertIsNone(result['certain_patient_ids']);self.assertEqual(result['strata'],[])
        self.values['measurement-policy']['semantic_policy_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'STALE_CLAIM_SEMANTIC_POLICY'):self.run_query()

    def test_inputs_and_source_claim_graph_are_immutable(self):
        before=deepcopy(self.values);result=self.run_query()
        self.assertEqual(before,self.values)
        result['strata'][0]['query']['baseline']['item_ids'].clear()
        result['selection_plan']['mapping']['rule_evidence'][0]['source']['label']='edited'
        self.assertEqual(before,self.values)
        self.assertFalse(result['context']['clinical_mapping_verified'])

    def test_cli_complete_blocked_invalid_and_input_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);args=[]
            for name in m.INPUTS:
                path=folder/(name+'.json');path.write_text(json.dumps(self.values[name]));args+=['--'+name,str(path)]
            out=folder/'output.json';args+=['--output',str(out)]
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(m.main(args),0)
                review=folder/'review.json';v=deepcopy(self.values['review']);v['decisions']=[];review.write_text(json.dumps(v))
                self.assertEqual(m.main(args),2);self.assertIsNone(json.loads(out.read_text())['certain_patient_ids'])
                previous=out.read_bytes();review.write_text('{}')
                with self.assertRaises(SystemExit):m.main(args)
                self.assertEqual(previous,out.read_bytes())
                with self.assertRaises(SystemExit):m.main(args[:-1]+[str(review)])
                self.assertEqual(review.read_text(),'{}')

    def test_committed_report_reproduces_with_actual_rust_literal_and_sql(self):
        report=json.loads((m.ei.ROOT/'verification/reviewed-measurement-mappings-report.json').read_text())
        self.assertEqual(report,verify.verify())
        self.assertTrue(report['all_literal_and_sql_bindings_equal']);self.assertTrue(report['cross_item_followup_excluded'])
        self.assertTrue(report['pro_witnesses_preserved'])

    def test_clinical_candidates_are_still_unaccepted(self):
        root=m.ei.ROOT
        pending=json.loads((root/'data/clinical-measurement-mapping-candidates.json').read_text())
        self.assertFalse(pending['executable_mapping_pack']);self.assertFalse(pending['clinical_mapping_verified'])
        self.assertTrue(all(e['decision'] is None and e['target_release_version'] is None for e in pending['entries']))
        self.assertEqual(json.loads((root/'data/terminology/mimic-demo-2.2-pending/review.json').read_text())['decisions'],[])


if __name__=='__main__':unittest.main()
