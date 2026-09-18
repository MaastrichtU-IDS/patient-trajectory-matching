"""Finite-world checks for metric relaxation with protected Allen predicates."""
from copy import deepcopy
from itertools import product
import json
import subprocess
import sys
import unittest
from unittest.mock import patch
from jsonschema import ValidationError

from . import robust_relaxation as relax, extended_interval_query as extended, exact_intervals as ei
from .test_bounded_intervals import fixture


def example():
    root = ei.ROOT / 'examples/extended-relaxation'
    return tuple(json.loads((root / (name + '.json')).read_text()) for name in ('source','query','policy'))


class ExtendedRelaxationTests(unittest.TestCase):
    def test_combined_catalogue_option_and_finite_world_reference(self):
        source, query, policy = example()
        before = deepcopy((source, query, policy))
        result = relax.execute(source, query, policy)
        self.assertEqual((source, query, policy), before)
        self.assertEqual(result['robust_patient_ids'], ['P'])
        self.assertEqual(result['best_robust_matches'][0]['option_id'], 'both-metrics')
        self.assertEqual(result['best_robust_matches'][0]['cost'], '1.25')
        contexts = set()
        for evaluation in result['evaluations']:
            constraints = {c['id']:c for c in evaluation['option']['query']['constraints']}
            # Independent endpoint arithmetic on ALL four feasible integer worlds.
            def holds(start, end):
                duration = constraints['duration']
                return (0 < start and end < 10
                        and duration['minimum_us'] <= 10 <= duration['maximum_us']
                        and min(10,end) - max(0,start) >= constraints['shared']['minimum_us'])
            answers = [holds(start,end) for start,end in product((3,4),(6,7))]
            binding = evaluation['result']['trajectories'][0]['bindings'][0]
            self.assertEqual(binding['possible'], any(answers))
            self.assertEqual(binding['certain'], all(answers))
            if binding['status'] == 'POSSIBLE':
                for name, expected in (('possible_witness',True),('counterexample',False)):
                    world = binding[name]
                    b = next(e for e in source['events'] if e['id']=='b')
                    self.assertEqual(holds(world[b['start_var']],world[b['end_var']]), expected)
            self.assertEqual(constraints['containment'], query['constraints'][0])
            self.assertEqual(evaluation['option']['query']['slots'], query['slots'])
            contexts.add(evaluation['result']['context']['source_context_id'])
        self.assertEqual(len(contexts),1)
        self.assertIn('patterns/extended_interval_query.py',result['context']['artifacts'])

    def test_no_implicit_option_composition_and_exact_budgets(self):
        source,query,policy = example()
        policy['options'] = policy['options'][:2]
        self.assertEqual(relax.execute(source,query,policy)['robust_patient_ids'],[])
        for updates in ({'max_cost':'1.2499999999999999999999'}, {'max_changed_targets':1}):
            source,query,policy = example();policy.update(updates)
            result=relax.execute(source,query,policy)
            self.assertEqual(result['robust_patient_ids'],[])
            self.assertEqual(result['possible_patient_ids'],['P'])
            self.assertIn('both-metrics',result['excluded_by_budget'])

    def test_duration_lower_bound_and_overlap_remain_positive(self):
        for field,value in (('lower_us',0),('lower_us',-1),('minimum_us',0),('minimum_us',-1),('minimum_us',3),('minimum_us',4)):
            source,query,policy=example()
            change=policy['options'][-1]['changes'][1 if field=='minimum_us' else 0]
            change[field]=value
            with self.subTest(field=field,value=value), self.assertRaises((ei.ContractError,ValidationError)):
                relax.execute(source,query,policy)

    def test_allen_selectors_source_and_change_shapes_are_protected(self):
        for mutation in ('allen','selector','shape','boolean','contraction','unknown','excluded-invalid'):
            source,query,policy=example()
            if mutation=='allen':policy['relaxable_targets'].append('containment')
            elif mutation=='selector':policy['options'][0]['changes'][0]['class_iri']='other'
            elif mutation=='shape':policy['options'][1]['changes'][0]={'target':'shared','lower_us':1,'upper_us':2}
            elif mutation=='boolean':policy['options'][1]['changes'][0]['minimum_us']=True
            elif mutation=='contraction':policy['options'][0]['changes'][0]['lower_us']=10
            elif mutation=='unknown':policy['options'][0]['changes'][0]['target']='missing'
            else:
                policy['max_cost']='0';policy['options'][0]['changes'][0]['lower_us']=10
            with self.subTest(mutation=mutation), self.assertRaises((ei.ContractError,ValidationError)):
                relax.execute(source,query,policy)

    def test_hard_allen_constraint_is_still_required(self):
        source,query,policy=example();query['constraints'][0]['operator']='before'
        result=relax.execute(source,query,policy)
        self.assertEqual(result['robust_patient_ids'],[])
        self.assertEqual(result['possible_patient_ids'],[])

    def test_signed_gap_widening(self):
        source,query,policy=example()
        query['constraints']=[{'id':'g','left':'a','right':'b','operator':'gap','min_gap_us':-6,'max_gap_us':-6}]
        policy.update(relaxable_targets=['g'],options=[{'id':'widen','cost':'0.125','changes':[{'target':'g','lower_us':-7,'upper_us':-6}]}])
        result=relax.execute(source,query,policy)
        self.assertEqual(result['evaluations'][0]['result']['certain_patient_ids'],[])
        self.assertEqual(result['robust_patient_ids'],['P'])

    def test_lowering_duration_minimum_is_a_relaxation(self):
        source,query,policy=example()
        query['constraints']=[{'id':'d','slot':'a','operator':'duration','minimum_us':11,'maximum_us':12}]
        policy.update(relaxable_targets=['d'],options=[{'id':'lower','cost':'1','changes':[{'target':'d','lower_us':10,'upper_us':12}]}])
        result=relax.execute(source,query,policy)
        self.assertEqual(result['robust_patient_ids'],['P'])

    def test_original_first_zero_cost_tie(self):
        source,query,policy=example()
        query['constraints'][1].update(minimum_us=10,maximum_us=10)
        query['constraints'][2]['minimum_us']=2
        policy['options']=[{'id':'aaa','cost':'0','changes':[{'target':'duration','lower_us':9,'upper_us':10},{'target':'shared','minimum_us':1}]}]
        self.assertEqual(relax.execute(source,query,policy)['best_robust_matches'][0]['option_id'],'original')

    def test_clock_mismatch_is_not_repaired_by_metric_relaxation(self):
        source,query,policy=example()
        source['clocks'].append({**source['clocks'][0],'clock_id':'other'})
        b=next(e for e in source['events'] if e['id']=='b')
        for v in source['variables']:
            if v['id'] in (b['start_var'],b['end_var']):v['clock_id']='other'
        result=relax.execute(source,query,policy)
        self.assertEqual(result['robust_patient_ids'],[])
        final=result['evaluations'][-1]['result']['trajectories'][0]['bindings'][0]
        self.assertEqual(final['status'],'INCOMPARABLE')

    def test_no_world_dependent_binding_or_option(self):
        source,query,policy=example()
        source=fixture([('a1','infusion',(0,0),(1,1)),('a2','infusion',(1,1),(2,2)),('b','specimen_collection',(1,2),(3,3))])
        query['constraints']=[{'id':'meet','left':'a','right':'b','operator':'meets'}]
        policy.update(relaxable_targets=[],options=[])
        result=relax.execute(source,query,policy)
        self.assertEqual(result['robust_patient_ids'],[])
        self.assertEqual(result['possible_patient_ids'],['P'])
        source=fixture([('a','infusion',(0,0),(1,1)),('b','specimen_collection',(1,3),(4,4))])
        query['constraints']=[{'id':'gap','left':'a','right':'b','operator':'gap','min_gap_us':1,'max_gap_us':1}]
        policy.update(relaxable_targets=['gap'],options=[{'id':'early','cost':'1','changes':[{'target':'gap','lower_us':0,'upper_us':1}]},{'id':'late','cost':'1','changes':[{'target':'gap','lower_us':1,'upper_us':2}]}])
        result=relax.execute(source,query,policy)
        self.assertEqual(result['robust_patient_ids'],[])
        self.assertEqual(result['possible_patient_ids'],['P'])

    def test_incomplete_or_inconsistent_execution_blocks_aggregate(self):
        source,query,policy=example()
        complete=extended.execute(relax.bt.prepare(source),query)
        with patch.object(extended,'execute',side_effect=[complete,{'search_complete':False}]):
            result=relax.execute(source,query,policy)
        self.assertEqual(result['status'],'BLOCKED')
        self.assertIsNone(result['robust_patient_ids'])
        self.assertIsNone(result['possible_patient_ids'])
        source['variables'][0]['lower_us']=100
        self.assertEqual(relax.execute(source,query,policy)['status'],'BLOCKED')

    def test_legacy_profiles_reject_overlap_change_shape(self):
        from .test_robust_relaxation import example as bounded_example
        source,query,policy=bounded_example()
        policy['options'][0]['changes']=[{'target':'gap','minimum_us':1}]
        with self.assertRaises(ei.ContractError):relax.execute(source,query,policy)

    def test_cli_example(self):
        args=[sys.executable,'-m','patterns.robust_relaxation']
        for name in ('source','query','policy'):args += ['--'+name,'examples/extended-relaxation/'+name+'.json']
        result=subprocess.run(args,cwd=ei.ROOT,text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(result.stdout)['best_robust_matches'][0]['option_id'],'both-metrics')


if __name__=='__main__':unittest.main()
