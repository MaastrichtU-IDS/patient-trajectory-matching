"""Robust catalogue search: quantifiers, budgets, hard constraints and mixed records."""
from copy import deepcopy
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from jsonschema import ValidationError
from . import robust_relaxation as relax, bounded_intervals as bt, exact_intervals as ei
from .test_bounded_intervals import fixture
from . import test_mixed_record_query as mixed_tests


def example():
    minute=60000000
    source=fixture([('a','infusion',(0,0),(minute,minute)),
                    ('b','specimen_collection',(48*minute,50*minute),(51*minute,51*minute))])
    query={'profile':bt.QUERY_PROFILE,'id':'gap','slots':[
        {'id':'a','class_iri':str(bt.KINDS['infusion'])},
        {'id':'b','class_iri':str(bt.KINDS['specimen_collection'])}],
        'constraints':[{'id':'gap','left':'a','right':'b','operator':'gap','min_gap_us':0,'max_gap_us':48*minute}]}
    policy={'profile':relax.PROFILE,'kind':'bounded','relaxable_targets':['gap'],'max_cost':'2',
            'max_changed_targets':1,'options':[{'id':'widen','cost':'1.25','changes':[
                {'target':'gap','lower_us':0,'upper_us':50*minute}]}]}
    return source,query,policy


class RobustTests(unittest.TestCase):
    def test_robust_widening_and_immutable_source(self):
        s,q,p=example(); before=deepcopy((s,q,p))
        r=relax.execute(s,q,p)
        self.assertEqual((s,q,p),before)
        self.assertEqual(r['robust_patient_ids'],['P'])
        self.assertEqual(r['best_robust_matches'][0]['cost'],'1.25')
        original=r['evaluations'][0]['result']
        self.assertEqual(original['certain_patient_ids'],[])
        self.assertEqual(original['possible_patient_ids'],['P'])
        self.assertEqual(r['evaluations'][1]['result']['context']['source_context_id'],original['context']['source_context_id'])

    def test_budget_and_exact_cost_order(self):
        s,q,p=example()
        p['max_cost']='1.249999999999999999999999999999'
        self.assertEqual(relax.execute(s,q,p)['robust_patient_ids'],[])
        p['max_cost']='2'
        p['options'].append({**deepcopy(p['options'][0]),'id':'cheaper','cost':'1.249999999999999999999999999999'})
        self.assertEqual(relax.execute(s,q,p)['best_robust_matches'][0]['option_id'],'cheaper')
        p['max_changed_targets']=0
        self.assertEqual(relax.execute(s,q,p)['robust_patient_ids'],[])

    def test_original_zero_cost_preferred(self):
        s,q,p=example(); q['constraints'][0]['max_gap_us']=49*60000000
        # A lexically earlier, equally priced widening must not displace an
        # already-certain original. Catalogue order must not affect the choice.
        p['options'][0].update(id='aaa',cost='0')
        p['options'].append({**deepcopy(p['options'][0]),'id':'zzz','cost':'0.00'})
        self.assertEqual(relax.execute(s,q,p)['best_robust_matches'][0]['option_id'],'original')
        p['options'].reverse()
        self.assertEqual(relax.execute(s,q,p)['best_robust_matches'][0]['option_id'],'original')

    def test_zero_cost_relaxation_when_original_is_not_certain(self):
        s,q,p=example()
        p['options'][0].update(id='aaa',cost='0')
        result=relax.execute(s,q,p)
        self.assertEqual(result['best_robust_matches'][0]['option_id'],'aaa')
        self.assertEqual(result['robust_patient_ids'],['P'])

    def test_no_world_dependent_binding(self):
        s,q,p=example()
        s=fixture([('a1','infusion',(0,0),(1,1)),('a2','infusion',(1,1),(2,2)),
                   ('b','specimen_collection',(1,2),(3,3))])
        q['constraints']=[{'id':'meet','left':'a','right':'b','operator':'meets'}]
        p.update(relaxable_targets=[],options=[])
        r=relax.execute(s,q,p)
        self.assertEqual(r['robust_patient_ids'],[])
        self.assertEqual(r['possible_patient_ids'],['P'])

    def test_no_world_dependent_modification(self):
        s,q,p=example()
        s=fixture([('a','infusion',(0,0),(1,1)),('b','specimen_collection',(1,3),(4,4))])
        q['constraints'][0].update(min_gap_us=1,max_gap_us=1)
        p['options']=[{'id':'early','cost':'1','changes':[{'target':'gap','lower_us':0,'upper_us':1}]},
                      {'id':'late','cost':'1','changes':[{'target':'gap','lower_us':1,'upper_us':2}]}]
        r=relax.execute(s,q,p)
        self.assertEqual(r['robust_patient_ids'],[])
        self.assertEqual(r['possible_patient_ids'],['P'])
        self.assertTrue(all(e['result']['possible_patient_ids']==['P'] for e in r['evaluations']))

    def test_reject_contraction_hard_target_and_mutations(self):
        for mode in ('contraction','hard','duplicate','structural','bool'):
            s,q,p=example(); change=p['options'][0]['changes'][0]
            if mode=='contraction': change['upper_us']=47*60000000
            if mode=='hard': p['relaxable_targets']=[]
            if mode=='duplicate': p['options'].append(deepcopy(p['options'][0]))
            if mode=='structural': change['operator']='before'
            if mode=='bool': change['lower_us']=False
            with self.assertRaises((ei.ContractError,ValidationError)): relax.execute(s,q,p)

    def test_hard_constraint_still_enforced(self):
        s,q,p=example()
        q['constraints'].append({'id':'hard','left':'b','right':'a','operator':'before'})
        self.assertEqual(relax.execute(s,q,p)['possible_patient_ids'],[])

    def test_inconsistent_source_blocks(self):
        s,q,p=example(); s['variables'][0].update(lower_us=60000000,upper_us=60000000)
        r=relax.execute(s,q,p)
        self.assertEqual(r['status'],'BLOCKED'); self.assertIsNone(r['robust_patient_ids'])

    def mixed_input(self):
        f=mixed_tests.MixedTests(); f.setUp(); f.geometry(start=(3,5),end=6,baseline=0,followup=7)
        s={n:f.values[n] for n in relax.MIXED_INPUTS}
        q=f.values['query']; p=example()[2]
        p.update(kind='mixed',relaxable_targets=['baseline'])
        p['options'][0]['changes']=[{'target':'baseline','lower_us':1,'upper_us':5}]
        return s,q,p

    def test_mixed_real_backend_and_followup_independence(self):
        s,q,p=self.mixed_input()
        r=relax.execute(s,q,p)
        self.assertEqual(r['status'],'COMPLETED')
        self.assertEqual(r['evaluations'][0]['result']['certain_patient_ids'],[])
        self.assertEqual(r['robust_patient_ids'],['P1'])
        for e in r['evaluations']:
            self.assertEqual(e['option']['query']['followup'],q['followup'])
            self.assertEqual(e['option']['query']['baseline']['value_lexical'],q['baseline']['value_lexical'])

    def test_incomplete_variant_blocks_all_answers(self):
        s,q,p=self.mixed_input()
        with patch.object(relax.mixed,'execute',return_value={'status':'BLOCKED_INTERVAL_VIEW'}):
            r=relax.execute(s,q,p)
        self.assertFalse(r['search_complete']); self.assertIsNone(r['robust_patient_ids'])

    def test_cli_example(self):
        args=[sys.executable,'-m','patterns.robust_relaxation']
        for n in ('source','query','policy'): args+=['--'+n,'examples/robust-relaxation/'+n+'.json']
        r=subprocess.run(args,cwd=ei.ROOT,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(r.stdout)['robust_patient_ids'],['P'])


if __name__=='__main__': unittest.main()
