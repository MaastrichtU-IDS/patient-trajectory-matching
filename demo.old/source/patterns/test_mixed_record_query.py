"""Mixed geometry, explicit clock alignment, fixed witnesses and follow-up independence."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import product
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import mixed_record_query as mixed, claim_rdf as cr, exact_intervals as ei
from . import measurement_claims as points, local_claim_projection as intervals, semantic_support as semantic

NAMES = ('interval-store', 'interval-policy', 'semantic-policy', 'measurement-store', 'measurement-policy', 'alignment', 'query')


def reference(result, baseline_id, followup_id=None):
    """Independent finite-world oracle: raw dates, direct gap/membership arithmetic.

    Does not use the STN, query-edge compiler, rebased coordinates or classify().
    """
    source = result['interval_view']['source']; query = result['context']['query']
    record_map = {r['event']['id']: r for r in result['measurement_view']['records']}
    baseline = record_map[baseline_id]; scope = (baseline['event']['patient_id'], baseline['event']['episode_id'])
    event = next(e for e in source['events'] if (e['patient_id'], e['episode_id']) == scope)
    variables = {}
    def scope_of(v): return v['patient_id'], v['episode_id']
    def number(label):
        d = datetime.fromisoformat(label) - datetime(2000, 1, 1)
        return (d.days * 86400 + d.seconds) * 1000000 + d.microseconds
    for ns, rows in [('i', source['variables']), ('m', result['measurement_view']['selection']['selected']['variables'])]:
        for v in rows:
            if scope_of(v) == scope: variables[ns + ':' + v['id']] = range(number(v['local_lower']), number(v['local_upper']) + 1)
    count = 1
    for r in variables.values(): count *= len(r)
    assert count <= 8192, 'Oracle deliberately bounds exhaustive worlds'
    outcomes = []
    for values in product(*variables.values()):
        a = dict(zip(variables, values))
        if any(a['i:' + e['start_var']] >= a['i:' + e['end_var']] for e in source['events'] if scope_of(e) == scope): continue
        if any(a['i:' + c['left_var']] - a['i:' + c['right_var']] > c['upper_us']
               for c in source['constraints'] if 'i:' + c['left_var'] in a): continue
        start, end = a['i:' + event['start_var']], a['i:' + event['end_var']]
        b = a['m:' + baseline['event']['time_var']]; base = query['baseline']
        holds = base['min_before_start_us'] <= start - b <= base['max_before_start_us']
        if followup_id:
            f = a['m:' + record_map[followup_id]['event']['time_var']]; follow = query['followup']
            anchor = start if follow['anchor'] == 'start' else end
            holds = holds and follow['min_after_anchor_us'] <= f - anchor <= follow['max_after_anchor_us']
            if follow['within_interval']: holds = holds and start <= f < end
        outcomes.append(holds)
    assert outcomes
    return 'CERTAIN' if all(outcomes) else 'POSSIBLE' if any(outcomes) else 'IMPOSSIBLE'


class MixedTests(unittest.TestCase):
    def setUp(self):
        folder = ei.ROOT / 'examples/mixed-record-query'
        self.values = {name: json.loads((folder / (name + '.json')).read_text()) for name in NAMES}

    def run_query(self):
        return mixed.execute(*(self.values[n] for n in NAMES))

    def rebind(self):
        for store_name, policy_name, sem in [('interval-store', 'interval-policy', self.values['semantic-policy']),
                                           ('measurement-store', 'measurement-policy', points.SEMANTIC_POLICY)]:
            store = self.values[store_name]; policy = self.values[policy_name]
            policy.update(store_sha256=cr.digest(store), semantic_policy_sha256=cr.digest(sem),
                decisions=[{'id': 'd_' + c['id'], 'claim_id': c['id'], 'claim_sha256': cr.digest(c), 'action': 'accept',
                            'supersedes': None, 'reason': 'Synthetic test selection'} for c in store['claims']])
        self.values['alignment'].update(interval_store_sha256=cr.digest(self.values['interval-store']),
                                        measurement_store_sha256=cr.digest(self.values['measurement-store']))

    def tiny(self):
        for key in ('interval-store', 'measurement-store'):
            store = self.values[key]
            store['claims'] = [c for c in store['claims'] if c['patient_id'] == 'P1' and c['id'] != 'claim_followup2_P1']
            store['clocks'] = [c for c in store['clocks'] if c['scope'] == 'patient:P1']
        self.values['alignment']['bindings'] = self.values['alignment']['bindings'][:1]
        self.rebind()

    def variable(self, store_name, vid):
        return next(v for c in self.values[store_name]['claims'] for v in c['bundle']['variables'] if v['id'] == vid)

    def bounds(self, store_name, vid, lo, hi=None):
        origin = datetime(2150, 1, 1, 10)
        v = self.variable(store_name, vid)
        v.update(local_lower=(origin + timedelta(microseconds=lo)).isoformat(),
                 local_upper=(origin + timedelta(microseconds=hi if hi is not None else lo)).isoformat())

    def geometry(self, start=2, end=5, baseline=0, followup=4):
        self.tiny()
        for key, vid, value in [('interval-store','treatment_P1_start',start),('interval-store','treatment_P1_end',end),
                               ('measurement-store','baseline_P1_time',baseline),('measurement-store','followup1_P1_time',followup)]:
            self.bounds(key, vid, *(value if isinstance(value, tuple) else (value,)))
        self.values['query']['baseline'].update(min_before_start_us=1, max_before_start_us=4)
        self.values['query']['followup'].update(min_after_anchor_us=0, max_after_anchor_us=2)
        self.rebind()

    def baseline(self, result, bid='baseline_P1'):
        return next(b for b in result['baseline_bindings'] if b['baseline_id'] == bid)

    def test_real_rust_treatment_support_and_three_distinct_followup_outcomes(self):
        result = self.run_query()
        self.assertEqual(result['status'], 'COMPLETED_RECORD_QUERY')
        self.assertEqual(result['certain_patient_ids'], ['P1','P2','P3'])
        p1, p2, p3 = [self.baseline(result, 'baseline_' + p) for p in ('P1','P2','P3')]
        self.assertEqual(p1['treatment']['semantic_support']['status'], 'ENTAILED')
        self.assertEqual([f['value_change']['delta'] for f in p1['followup']], ['10','14'])
        self.assertEqual(p2['followup_status'], 'NO_SELECTED_FOLLOWUP_IN_WINDOW')
        self.assertEqual(p3['followup'][0]['value_change']['delta'], '-7')
        self.assertTrue(p1['treatment']['patient_role']); self.assertTrue(p1['treatment']['patient_bearer'])

    def test_missing_followup_never_excludes_eligible_patient_or_proves_failure(self):
        result = self.run_query(); row = self.baseline(result, 'baseline_P2')
        self.assertEqual(row['eligibility']['status'], 'CERTAIN'); self.assertEqual(row['followup'], [])
        self.assertEqual(row['clinical_response_status'], 'UNKNOWN')
        self.assertIn('P2', result['certain_patient_ids'])

    def test_changing_followup_values_does_not_condition_eligibility_on_improvement(self):
        first = self.run_query()
        for c in self.values['measurement-store']['claims']:
            if 'followup' in c['id']: c['bundle']['events'][0]['value_lexical'] = '1'
        self.rebind(); second = self.run_query()
        self.assertEqual(first['certain_patient_ids'], second['certain_patient_ids'])
        self.assertEqual(first['possible_patient_ids'], second['possible_patient_ids'])
        self.assertEqual(self.baseline(second)['followup'][0]['value_change']['delta'], '-57')

    def test_no_alignment_is_incomparable_even_with_identical_clock_ids(self):
        self.values['alignment']['bindings'] = []; result = self.run_query()
        self.assertEqual(result['certain_patient_ids'], [])
        self.assertEqual(self.baseline(result)['eligibility']['status'], 'INCOMPARABLE')
        self.assertEqual(self.baseline(result)['followup_status'], 'UNKNOWN_BASELINE_ALIGNMENT')

    def test_stale_cross_dataset_cross_patient_and_unknown_alignment_rejected(self):
        original = deepcopy(self.values)
        for mutation, message in [('stale','STALE_CLOCK_ALIGNMENT'),('dataset','CROSS_DATASET'),
                                   ('patient','ALIGNMENT_PATIENT'),('unknown','UNKNOWN_ALIGNMENT_CLOCK'),('duplicate','DUPLICATE_CLOCK_ALIGNMENT')]:
            self.values = deepcopy(original)
            if mutation == 'stale': self.values['alignment']['interval_store_sha256'] = '0' * 64
            elif mutation == 'dataset': self.values['measurement-store']['dataset_id'] = 'other'; self.rebind()
            elif mutation == 'patient': self.values['alignment']['bindings'][0]['measurement_clock_id'] = 'clock_P2'
            elif mutation == 'unknown': self.values['alignment']['bindings'][0]['interval_clock_id'] = 'missing'
            else: self.values['alignment']['bindings'].append(dict(self.values['alignment']['bindings'][0]))
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ei.ContractError,message): self.run_query()

    def test_independent_origin_rebasing_preserves_results_and_compiled_network(self):
        original = self.run_query()
        for key, time in [('interval-store','08:00:00'),('measurement-store','11:00:00')]:
            for clock in self.values[key]['clocks']: clock['origin'] = '2150-01-01T' + time
        self.rebind(); rebased = self.run_query()
        self.assertEqual(original['source_networks'], rebased['source_networks'])
        self.assertEqual(original['certain_patient_ids'], rebased['certain_patient_ids'])
        self.assertNotEqual(original['context_id'], rebased['context_id'])
        self.assertEqual([f['value_change'] for f in self.baseline(original)['followup']],
                         [f['value_change'] for f in self.baseline(rebased)['followup']])

    def test_same_calendar_shift_preserves_answers(self):
        before = self.run_query()
        for key in ('interval-store','measurement-store'):
            for clock in self.values[key]['clocks']: clock['origin'] = (datetime.fromisoformat(clock['origin']) + timedelta(days=10)).isoformat()
            for c in self.values[key]['claims']:
                for v in c['bundle']['variables']:
                    for side in ('local_lower','local_upper'): v[side] = (datetime.fromisoformat(v[side]) + timedelta(days=10)).isoformat()
        self.rebind(); after = self.run_query()
        self.assertEqual(before['certain_patient_ids'], after['certain_patient_ids'])
        self.assertEqual([b['followup_status'] for b in before['baseline_bindings']], [b['followup_status'] for b in after['baseline_bindings']])

    def test_baseline_lower_and_upper_bounds_are_inclusive_and_strictness_is_explicit(self):
        self.geometry()
        for before, expected in [(0,'IMPOSSIBLE'),(1,'CERTAIN'),(4,'CERTAIN'),(5,'IMPOSSIBLE')]:
            self.bounds('measurement-store','baseline_P1_time',2-before);self.rebind()
            result=self.run_query();self.assertEqual(self.baseline(result)['eligibility']['status'],expected)
        self.values['query']['baseline']['min_before_start_us']=0
        self.bounds('measurement-store','baseline_P1_time',2);self.rebind()
        self.assertEqual(self.baseline(self.run_query())['eligibility']['status'],'CERTAIN')

    def test_followup_gap_inclusive_bounds_and_start_vs_end_anchor(self):
        self.geometry()
        for f, expected in [(1,'IMPOSSIBLE'),(2,'CERTAIN'),(4,'CERTAIN'),(5,'IMPOSSIBLE')]:
            self.bounds('measurement-store','followup1_P1_time',f);self.rebind();result=self.run_query()
            self.assertEqual(self.baseline(result)['followup'][0]['joint_binding']['status'],expected)
        self.values['query']['followup']['anchor']='end'
        self.bounds('measurement-store','followup1_P1_time',5);self.rebind()
        self.assertEqual(self.baseline(self.run_query())['followup'][0]['joint_binding']['status'],'CERTAIN')

    def test_half_open_interval_contains_start_and_excludes_end(self):
        self.geometry();self.values['query']['followup'].update(within_interval=True,max_after_anchor_us=10)
        for f, expected in [(2,'CERTAIN'),(4,'CERTAIN'),(5,'IMPOSSIBLE')]:
            self.bounds('measurement-store','followup1_P1_time',f);self.rebind();r=self.run_query()
            self.assertEqual(self.baseline(r)['followup'][0]['joint_binding']['status'],expected)
            self.assertEqual(reference(r,'baseline_P1','followup1_P1'),expected)

    def test_exhaustive_uncertain_worlds_agree_with_fixed_witness_classification(self):
        for start,baseline,followup,inside in [((2,4),(0,3),(3,6),False),((2,3),0,(3,4),False),
                                               ((2,4),1,(4,6),True),(2,0,(1,2),False)]:
            self.geometry(start=start,baseline=baseline,followup=followup)
            self.values['query']['followup']['within_interval']=inside
            r=self.run_query();row=self.baseline(r)
            self.assertEqual(row['eligibility']['status'],reference(r,'baseline_P1'))
            if row['followup']:
                self.assertEqual(row['followup'][0]['joint_binding']['status'],reference(r,'baseline_P1','followup1_P1'))

    def test_possible_baseline_does_not_become_certain_after_conditioning(self):
        self.geometry(start=1,end=4,baseline=(0,2),followup=3)
        self.values['query']['followup']['max_after_anchor_us']=10
        r=self.run_query();row=self.baseline(r)
        self.assertEqual(row['eligibility']['status'],'POSSIBLE')
        self.assertEqual(row['followup'][0]['joint_binding']['status'],'POSSIBLE')
        self.assertEqual(reference(r,'baseline_P1','followup1_P1'),'POSSIBLE')
        self.assertEqual(r['certain_patient_ids'],[]);self.assertEqual(r['possible_patient_ids'],['P1'])

    def test_source_constraints_survive_composition_and_change_certainty(self):
        self.geometry(start=(2,4),end=5,baseline=3,followup=5)
        r=self.run_query();self.assertEqual(self.baseline(r)['eligibility']['status'],'POSSIBLE')
        claim=self.values['interval-store']['claims'][0]
        claim['bundle']['constraints']=[{'id':'fix_start','left_var':'treatment_P1_end','right_var':'treatment_P1_start','upper_us':1,'source_key':'synthetic:correlation'}]
        self.rebind();r=self.run_query()
        self.assertEqual(self.baseline(r)['eligibility']['status'],'CERTAIN')
        self.assertEqual(reference(r,'baseline_P1'),'CERTAIN')
        self.assertIn('source:i:fix_start',r['edge_evidence'])

    def test_proofs_and_witnesses_reference_retained_source_edges(self):
        r=self.run_query();row=self.baseline(r);proof=row['eligibility']
        self.assertEqual(proof['status'],'CERTAIN')
        for p in proof['entailment_proofs']:
            self.assertTrue(p['source_path'])
            self.assertTrue(set(p['source_path']) <= set(r['edge_evidence']))
        self.assertIn('m:baseline_P1_time',proof['possible_witness'])
        self.assertTrue(any(e['kind']=='proper_interval' for e in r['edge_evidence'].values()))
        self.assertFalse(any(k.startswith('proper:m:') for k in r['edge_evidence']))

    def test_units_and_item_identity_are_required_for_numeric_difference(self):
        self.tiny();follow=self.values['measurement-store']['claims'][1]['bundle']['events'][0]
        follow['item_id']='other_pressure';self.values['query']['followup']['item_ids'].append('other_pressure')
        self.rebind();r=self.run_query();change=self.baseline(r)['followup'][0]['value_change']
        self.assertEqual(change['status'],'INCOMPARABLE_ITEM_OR_UNIT');self.assertIsNone(change['delta'])
        follow['unit_lexical']='kPa';self.rebind();r=self.run_query()
        self.assertEqual(self.baseline(r)['followup_status'],'NO_SELECTED_FOLLOWUP_IN_WINDOW')

    def test_exact_decimal_predicates_and_subtraction_do_not_round(self):
        self.tiny();events=[c['bundle']['events'][0] for c in self.values['measurement-store']['claims']]
        events[0]['value_lexical']='12345678901234567890.12345678901234567890'
        events[1]['value_lexical']='12345678901234567890.12345678901234567891'
        self.values['query']['baseline'].update(operator='eq',value_lexical=events[0]['value_lexical'])
        self.rebind();r=self.run_query();delta=self.baseline(r)['followup'][0]['value_change']['delta']
        self.assertEqual(Decimal(delta),Decimal('0.00000000000000000001'))

    def test_all_scalar_predicate_operators(self):
        self.tiny()
        for op,threshold,expected in [('lt','58',False),('le','58',True),('eq','58',True),('ge','58',True),('gt','58',False)]:
            self.values['query']['baseline'].update(operator=op,value_lexical=threshold)
            result=self.run_query()
            self.assertEqual('baseline_P1' in result['measurement_filter']['baseline_candidate_ids'],expected)

    def test_patient_episode_binding_prevents_borrowing_followup(self):
        r=self.run_query()
        self.assertEqual(self.baseline(r,'baseline_P2')['followup'],[])
        for row in r['baseline_bindings']:
            self.assertTrue(all(f['measurement_id'].endswith(row['patient_id']) for f in row['followup']))
        self.tiny();claim=self.values['measurement-store']['claims'][1]
        claim['episode_id']='other_stay'
        for kind in ('variables','events'):
            for item in claim['bundle'][kind]:item['episode_id']='other_stay'
        self.rebind();self.assertEqual(self.baseline(self.run_query())['followup'],[])

    def test_baseline_record_cannot_be_reused_as_followup(self):
        self.tiny();self.values['measurement-store']['claims']=self.values['measurement-store']['claims'][:1]
        self.bounds('measurement-store','baseline_P1_time',0);self.bounds('interval-store','treatment_P1_start',0)
        self.values['query']['baseline']['min_before_start_us']=0;self.rebind()
        self.assertEqual(self.baseline(self.run_query())['followup'],[])

    def test_followup_clock_unknown_does_not_remove_eligible_baseline(self):
        self.tiny();store=self.values['measurement-store'];clock=deepcopy(store['clocks'][0]);clock['clock_id']='other_clock';store['clocks'].append(clock)
        store['claims'][1]['bundle']['variables'][0]['clock_id']='other_clock';self.rebind()
        r=self.run_query();row=self.baseline(r)
        self.assertEqual(row['eligibility']['status'],'CERTAIN')
        self.assertEqual(row['followup_status'],'UNKNOWN_CLOCK_ALIGNMENT')
        self.assertEqual(row['followup'][0]['joint_binding']['status'],'INCOMPARABLE')

    def test_pending_measurements_are_not_used_for_baseline_or_followup(self):
        self.values['measurement-policy']['decisions']=[];r=self.run_query()
        self.assertEqual(r['baseline_bindings'],[]);self.assertEqual(r['certain_patient_ids'],[])
        self.assertEqual(r['clinical_knowledge_status'],'UNKNOWN')

    def test_pending_treatments_yield_no_eligible_binding(self):
        self.values['interval-policy']['decisions']=[];r=self.run_query()
        self.assertEqual(r['baseline_bindings'],[]);self.assertEqual(r['status'],'COMPLETED_RECORD_QUERY')

    def test_withdrawing_followup_preserves_baseline_eligibility(self):
        self.tiny();policy=self.values['measurement-policy'];old=policy['decisions'][1]
        policy['decisions'].append({**old,'id':'withdraw','action':'withdraw','supersedes':old['id']})
        r=self.run_query();self.assertEqual(r['certain_patient_ids'],['P1'])
        self.assertEqual(self.baseline(r)['followup_status'],'NO_SELECTED_FOLLOWUP_IN_WINDOW')

    def test_missing_time_dependency_blocks_authoritative_query(self):
        self.tiny();self.values['measurement-store']['claims'][0]['bundle']['variables']=[];self.rebind()
        r=self.run_query();self.assertEqual(r['status'],'BLOCKED_MEASUREMENT_VIEW')
        self.assertIsNone(r['certain_patient_ids']);self.assertIsNone(r['baseline_bindings'])
        self.assertFalse(r['search_complete_over_selected_records'])

    def test_backend_failure_is_not_a_negative_cohort_result(self):
        with patch.object(semantic,'run_backend',return_value={'status':'BACKEND_TIMEOUT'}):r=self.run_query()
        self.assertEqual(r['status'],'BLOCKED_INTERVAL_VIEW');self.assertIsNone(r['certain_patient_ids'])

    def test_inconsistent_interval_source_blocks_query(self):
        self.tiny();self.bounds('interval-store','treatment_P1_end',-1);self.rebind()
        r=self.run_query();self.assertEqual(r['status'],'BLOCKED_INTERVAL_VIEW');self.assertIsNone(r['baseline_bindings'])

    def test_variable_baseline_and_followup_limits_do_not_truncate(self):
        for name in ('MAX_VARIABLES','MAX_BASELINE_TESTS','MAX_FOLLOWUP_TESTS'):
            with self.subTest(name=name),patch.object(mixed,name,0):r=self.run_query()
            self.assertEqual(r['status'],'BLOCKED_MIXED_SOURCE');self.assertIsNone(r['certain_patient_ids'])
            self.assertIsNone(r['baseline_bindings']);self.assertFalse(r['search_complete_over_selected_records'])

    def test_invalid_query_numbers_units_and_profiles_rejected(self):
        original=deepcopy(self.values['query'])
        for mutation in ('float','reverse','unit','extra','profile','decimal'):
            q=deepcopy(original)
            if mutation=='float':q['baseline']['min_before_start_us']=1.0
            elif mutation=='reverse':q['baseline']['max_before_start_us']=0
            elif mutation=='unit':q['followup']['unit_lexical']=' mmHg'
            elif mutation=='extra':q['unknown']=True
            elif mutation=='profile':q['profile']='other'
            else:q['baseline']['value_lexical']='NaN'
            with self.subTest(mutation=mutation),self.assertRaises(ei.ContractError):mixed.validate_query(q)
        self.values['interval-store']['profile']=intervals.STORE_PROFILE
        with self.assertRaises(ei.ContractError):self.run_query()

    def test_inputs_not_mutated_and_context_binds_query_alignment_and_limits(self):
        original=deepcopy(self.values);a=self.run_query();self.assertEqual(original,self.values)
        self.values['alignment']['bindings'][0]['reason']='Another explicit synthetic rationale'
        b=self.run_query();self.assertNotEqual(a['context_id'],b['context_id'])
        self.assertEqual(a['certain_patient_ids'],b['certain_patient_ids'])
        self.assertEqual(a['context_id'],cr.digest(a['context']))

    def test_flags_never_claim_physical_clinical_causal_or_full_owl_verification(self):
        r=self.run_query()
        for key in ('clinical_mapping_verified','physical_elapsed_time_verified','source_history_verified',
                    'causal_effect_estimated','full_mixed_owl_reasoning_verified'):self.assertFalse(r[key])
        self.assertEqual(r['measurement_view']['accepted_graph_turtle'],None)
        self.assertTrue(r['interval_view']['accepted_graph_turtle'])

    def test_cli_success_invalid_input_and_output_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            folder=Path(folder);args=[]
            for name,value in self.values.items():
                path=folder/(name+'.json');path.write_text(json.dumps(value));args += ['--'+name,str(path)]
            output=folder/'result.json';args += ['--output',str(output)]
            with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
                self.assertEqual(mixed.main(args),0);previous=output.read_bytes()
                q=folder/'query.json';q.write_text('{}')
                with self.assertRaises(SystemExit):mixed.main(args)
                self.assertEqual(output.read_bytes(),previous)
                with self.assertRaises(SystemExit):mixed.main(args[:-1]+[str(q)])
                self.assertEqual(q.read_text(),'{}')

    def test_each_world_can_have_a_different_baseline_without_a_certain_named_binding(self):
        self.geometry(start=(1,2),end=3,baseline=0,followup=3)
        store=self.values['measurement-store'];extra=deepcopy(store['claims'][0])
        extra.update(id='claim_alternative',source_record_id='record_alternative')
        extra['bundle']['variables'][0]['id']='alternative_time'
        event=extra['bundle']['events'][0]
        event.update(id='alternative',record_id='record_alternative',time_var='alternative_time')
        store['claims'].append(extra);self.bounds('measurement-store','alternative_time',1)
        self.values['query']['baseline'].update(min_before_start_us=1,max_before_start_us=1)
        self.rebind();r=self.run_query()
        self.assertEqual(r['certain_patient_ids'],[]);self.assertEqual(r['possible_patient_ids'],['P1'])
        self.assertEqual(self.baseline(r)['eligibility']['status'],'POSSIBLE')
        self.assertEqual(self.baseline(r,'alternative')['eligibility']['status'],'POSSIBLE')

    def test_identical_variable_names_in_separate_stores_do_not_alias(self):
        self.tiny();claim=self.values['measurement-store']['claims'][0]
        claim['bundle']['variables'][0]['id']='treatment_P1_start'
        claim['bundle']['events'][0]['time_var']='treatment_P1_start';self.rebind()
        r=self.run_query();self.assertEqual(r['certain_patient_ids'],['P1'])
        witness=self.baseline(r)['eligibility']['possible_witness']
        self.assertNotEqual(witness['i:treatment_P1_start'],witness['m:treatment_P1_start'])

    def test_committed_synthetic_report_reproduces_and_declares_its_limits(self):
        from . import verify_mixed_record_query as verifier
        report=json.loads((ei.ROOT/'verification/mixed-record-query-report.json').read_text())
        self.assertEqual(report,verifier.verify())
        self.assertTrue(report['synthetic_only']);self.assertFalse(report['public_demo_mixed_query_analyzed'])
        self.assertEqual(report['interval_semantic_status'],'READY')
        self.assertEqual(report['certain_synthetic_patient_ids'],['P1','P2','P3'])
        self.assertFalse(report['full_mixed_owl_reasoning_verified'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
