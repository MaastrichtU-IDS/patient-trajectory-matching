"""H0 cutoff, coverage, immutable refinement and replay guarantees."""
from copy import deepcopy
from decimal import Decimal, localcontext
from hashlib import sha256
import importlib.util
import json
import unittest
from . import patient_similarity as p


class PatientSimilarityTests(unittest.TestCase):
    def setUp(self):
        self.dataset = json.loads((p.EXAMPLE/'dataset.json').read_text())
        self.profile = json.loads((p.EXAMPLE/'profile.json').read_text())
        self.engine = p.SimilarityEngine(self.dataset, self.profile)

    def patient(self, pid):
        return next(x for x in self.dataset['patients'] if x['patient_id'] == pid)

    def revision(self):
        return self.engine.initial('P00', top_k=2)

    def test_default_population_and_provenance_match_guided_source(self):
        original = json.loads((p.ROOT/'demo/cohort-data.json').read_text())
        self.assertEqual([x['patient_id'] for x in self.dataset['patients']], [x['patient_id'] for x in original['patients']])
        self.assertEqual(self.engine.metadata()['source_dataset_sha256'], sha256((p.ROOT/'demo/cohort-data.json').read_bytes()).hexdigest())
        inspected = self.engine.inspect(self.revision()['revision_id'], 'P03')
        evidence = inspected['candidate']['features']['baseline_creatinine']['evidence'][0]
        self.assertEqual(evidence['value']['hasValue'], '0.80')
        self.assertEqual(evidence['source']['original_unit'], 'mg/L')
        self.assertEqual(evidence['source']['binding']['normalized_value'], '0.80')
        self.assertEqual(evidence['process']['hasParticipant'], evidence['role']['id'])
        self.assertEqual(evidence['role']['isFeatureOf'], 'https://example.org/trajectory/data/person-P03')

    def test_builder_reproduces_committed_inputs(self):
        spec = importlib.util.spec_from_file_location('qbe_fixture',p.EXAMPLE/'build_fixture.py')
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        dataset, profile = module.build()
        self.assertEqual(dataset,self.dataset); self.assertEqual(profile,self.profile)

    def test_initial_ranking_uses_full_pool_and_patient_id_ties(self):
        revision = self.revision()
        self.assertEqual(revision['results']['base_pool_count'],10)
        self.assertEqual([x['patient_id'] for x in revision['results']['displayed']],['P01','P05'])
        self.assertEqual([x['patient_id'] for x in revision['results']['ranked']][:5],['P01','P05','P07','P09','P03'])
        self.assertNotIn('P00',revision['results']['eligible_patient_ids'])
        self.assertTrue(revision['results']['search_complete'])

    def test_index_event_is_never_a_similarity_feature(self):
        for patient in self.dataset['patients']:
            followup = next(x for x in patient['records'] if x['id'].endswith('-F'))
            followup['value']['hasValue'] = '999'
        modified = p.SimilarityEngine(self.dataset,self.profile).initial('P00',2)
        self.assertEqual(modified['results'],self.revision()['results'])
        inspected = self.engine.inspect(self.revision()['revision_id'],'P01')
        self.assertEqual(inspected['candidate']['ignored'][0]['record_id'],'P01-F')
        self.assertIn('AT_OR_AFTER_INDEX',inspected['candidate']['ignored'][0]['reasons'])

    def test_future_availability_and_unknown_availability_are_excluded(self):
        for availability in (1, None):
            with self.subTest(availability=availability):
                data = deepcopy(self.dataset)
                baseline = next(x for x in data['patients'][1]['records'] if x['id']=='P01-B')
                baseline['available_at_us'] = availability
                engine = p.SimilarityEngine(data,self.profile)
                evidence = engine.inspect(engine.initial('P00')['revision_id'],'P01')
                self.assertFalse(evidence['candidate']['features']['baseline_creatinine']['observed'])
                self.assertIn('AVAILABLE_AFTER_INDEX' if availability else 'UNKNOWN_AVAILABILITY', evidence['candidate']['ignored'][0]['reasons'])

    def test_cutoff_uses_each_patients_own_clock_and_index(self):
        baseline_results = self.revision()['results']
        for n,patient in enumerate(self.dataset['patients']):
            shift = (n+1)*24*p.HOUR
            patient['index_us'] += shift
            for record in patient['records']:
                record['occurrence_us'] += shift
                record['available_at_us'] += shift
        actual = p.SimilarityEngine(self.dataset,self.profile).initial('P00',2)
        self.assertEqual(actual['results'],baseline_results)

    def test_creatinine_minimum_and_inclusive_48_hour_boundary(self):
        inspection = self.engine.inspect(self.revision()['revision_id'],'P02')
        self.assertEqual(inspection['candidate']['features']['baseline_creatinine']['value'],'1')
        baseline = next(x for x in self.patient('P02')['records'] if x['id']=='P02-B')
        baseline['occurrence_us'] -= 1
        engine = p.SimilarityEngine(self.dataset,self.profile)
        self.assertFalse(engine.inspect(engine.initial('P00')['revision_id'],'P02')['candidate']['features']['baseline_creatinine']['observed'])

    def test_minimum_selection_does_not_use_latest_or_outcome(self):
        patient = self.patient('P01')
        second = deepcopy(patient['records'][0]); second['id']='P01-extra'; second['occurrence_us']=-p.HOUR
        second['value']['hasValue']='1.5'; patient['records'].append(second)
        engine = p.SimilarityEngine(self.dataset,self.profile)
        selected = engine.inspect(engine.initial('P00')['revision_id'],'P01')['candidate']['features']['baseline_creatinine']
        self.assertEqual(selected['value'],'1'); self.assertEqual([x['id'] for x in selected['evidence']],['P01-B'])

    def test_reviewed_ancestor_expansion_retains_nonzero_jaccard_distance(self):
        pair = self.engine.inspect(self.revision()['revision_id'],'P02')
        concepts = pair['candidate']['features']['clinical_concepts']['value']
        self.assertEqual(concepts,['ex:DrugA','ex:DrugAChild'])
        component = next(x for x in pair['comparison']['components'] if x['component']=='clinical_concepts')
        self.assertEqual(component['distance'],'0.5')

    def test_missingness_cannot_improve_rank_distance(self):
        before = self.engine.inspect(self.revision()['revision_id'],'P04')['comparison']
        self.patient('P04')['records'] = [r for r in self.patient('P04')['records'] if r['component']!='clinical_concepts']
        engine = p.SimilarityEngine(self.dataset,self.profile)
        after = engine.inspect(engine.initial('P00')['revision_id'],'P04')['comparison']
        self.assertGreaterEqual(Decimal(after['ranking_distance']),Decimal(before['ranking_distance']))
        self.assertLess(Decimal(after['coverage']),Decimal(before['coverage']))
        self.assertEqual(after['observed_distance'],'0')

    def test_no_observed_pairs_is_unrankable_not_zero_distance(self):
        self.patient('P01')['records'] = []
        result = p.SimilarityEngine(self.dataset,self.profile).initial('P00')['results']
        row = next(x for x in result['unrankable'] if x['patient_id']=='P01')
        self.assertIsNone(row['ranking_distance']); self.assertEqual(row['coverage'],'0')
        self.assertNotIn('P01',[x['patient_id'] for x in result['ranked']])
        self.assertIn('P01',result['eligible_patient_ids'])

    def test_empty_concept_set_requires_explicit_coverage(self):
        for pid in ('P00','P01'):
            record = next(x for x in self.patient(pid)['records'] if x['component']=='clinical_concepts')
            record['value'] = []; record['coverage_complete'] = True
        engine = p.SimilarityEngine(self.dataset,self.profile)
        component = engine.inspect(engine.initial('P00')['revision_id'],'P01')['comparison']['components'][2]
        self.assertEqual(component['distance'],'0')
        next(x for x in self.patient('P01')['records'] if x['component']=='clinical_concepts')['coverage_complete'] = False
        engine = p.SimilarityEngine(self.dataset,self.profile)
        component = engine.inspect(engine.initial('P00')['revision_id'],'P01')['comparison']['components'][2]
        self.assertIsNone(component['distance'])

    def test_conflicting_equal_time_age_declarations_are_missing(self):
        patient=self.patient('P01'); second=deepcopy(patient['records'][-1]); second['id']='conflicting-age'; second['value']='70-79'
        patient['records'].append(second)
        engine=p.SimilarityEngine(self.dataset,self.profile)
        feature=engine.inspect(engine.initial('P00')['revision_id'],'P01')['candidate']['features']['age_band']
        self.assertEqual(feature['missing_reason'],'CONFLICTING_AGE_BANDS')

    def test_two_refinements_bind_parent_and_preserve_base_pool(self):
        initial=self.revision()
        weighted=self.engine.refine(initial['revision_id'],{'type':'set_weights','weights':{'age_band':'1','baseline_creatinine':'1','clinical_concepts':'4'}})
        filtered=self.engine.refine(weighted['revision_id'],{'type':'add_filter','predicate':{'component':'clinical_concepts','operator':'contains','value':'ex:DrugA'}})
        self.assertEqual(weighted['parent_revision_id'],initial['revision_id'])
        self.assertEqual(filtered['parent_revision_id'],weighted['revision_id'])
        self.assertEqual(weighted['changes']['removed'],[])
        self.assertEqual(filtered['changes']['removed'],['P04','P06'])
        self.assertEqual(filtered['query']['base_pool'],initial['query']['base_pool'])
        self.assertIn('P02',filtered['results']['eligible_patient_ids']) # Not in initial top two.
        self.assertEqual(self.engine.get_revision(initial['revision_id']),initial)
        self.assertEqual(filtered['query_sha256'],p.digest(filtered['query']))
        self.assertEqual(filtered['result_sha256'],p.digest(filtered['results']))

    def test_hard_filter_unknown_is_reported_not_silent_exclusion(self):
        result=self.engine.refine(self.revision()['revision_id'],{'type':'add_filter','predicate':{'component':'baseline_creatinine','operator':'between','value':{'min':'0','max':'1.1'}}})
        self.assertEqual(result['changes']['unresolved'],['P08'])
        self.assertEqual(result['changes']['removed'],['P08'])
        self.assertEqual(result['results']['unresolved'][0]['reasons'][0]['reason'],'MISSING_ELIGIBLE_FEATURE')

    def test_known_false_conjunction_dominates_other_unknown(self):
        first=self.engine.refine(self.revision()['revision_id'],{'type':'add_filter','predicate':{'component':'baseline_creatinine','operator':'between','value':{'min':'0','max':'1.1'}}})
        second=self.engine.refine(first['revision_id'],{'type':'add_filter','predicate':{'component':'clinical_concepts','operator':'contains','value':'ex:DrugB'}})
        self.assertIn('P08',[x['patient_id'] for x in second['results']['excluded']])
        self.assertNotIn('P08',second['changes']['unresolved'])

    def test_branching_from_old_revision_does_not_mutate_descendant(self):
        initial=self.revision()
        child=self.engine.refine(initial['revision_id'],{'type':'add_filter','predicate':{'component':'clinical_concepts','operator':'contains','value':'ex:DrugA'}})
        branch=self.engine.refine(initial['revision_id'],{'type':'add_filter','predicate':{'component':'clinical_concepts','operator':'contains','value':'ex:DrugB'}})
        self.assertEqual(branch['results']['eligible_patient_ids'],['P04','P06'])
        self.assertEqual(self.engine.get_revision(child['revision_id']),child)

    def test_inputs_outputs_and_exports_are_defensive_copies(self):
        initial=self.revision(); expected=deepcopy(initial)
        self.dataset['patients'].clear(); self.profile['weights']['age_band']='99'
        initial['query']['base_pool'].clear(); initial['results']['ranked'].clear()
        exported=self.engine.manifest(expected['revision_id']); exported['dataset']['patients'].clear()
        inspected=self.engine.inspect(expected['revision_id'],'P01'); inspected['candidate']['features'].clear()
        self.assertEqual(self.engine.get_revision(expected['revision_id']),expected)
        self.assertEqual(self.revision(),expected)

    def test_replay_two_refinements_recomputes_identical_results(self):
        initial=self.revision()
        first=self.engine.refine(initial['revision_id'],{'type':'set_weights','weights':{'age_band':'0','baseline_creatinine':'1','clinical_concepts':'2'}})
        second=self.engine.refine(first['revision_id'],{'type':'add_filter','predicate':{'component':'clinical_concepts','operator':'contains','value':'ex:DrugA'}})
        replayed=p.SimilarityEngine.replay(self.engine.manifest(second['revision_id']))
        self.assertEqual(replayed.get_revision(second['revision_id']),second)

    def test_replay_rejects_dataset_query_result_and_parent_tampering(self):
        initial=self.revision()
        child=self.engine.refine(initial['revision_id'],{'type':'add_filter','predicate':{'component':'age_band','operator':'eq','value':'50-59'}})
        manifest=self.engine.manifest(child['revision_id'])
        for mutation in ('dataset','query','result','parent','implementation'):
            with self.subTest(mutation=mutation):
                value=deepcopy(manifest)
                if mutation=='dataset': value['dataset']['label']='changed'
                if mutation=='query': value['revisions'][-1]['query']['weights']['age_band']='99'
                if mutation=='result': value['revisions'][-1]['results']['eligible_patient_ids']=[]
                if mutation=='parent': value['revisions'][-1]['parent_revision_id']=None
                if mutation=='implementation': value['implementation_sha256']='0'*64
                with self.assertRaises(ValueError): p.SimilarityEngine.replay(value)

    def test_decimal_context_cannot_change_ranking(self):
        expected=self.revision()['results']
        with localcontext() as context:
            context.prec=7
            actual=self.revision()['results']
        self.assertEqual(actual,expected)

    def test_incompatible_clock_unit_index_and_pro_fail_admission(self):
        for mutation in ('clock','unit','index','pro','concept','clinical'):
            with self.subTest(mutation=mutation):
                value=deepcopy(self.dataset); record=value['patients'][1]['records'][0]
                if mutation=='clock':record['clock']='other'
                if mutation=='unit':record['unit']='umol/L'
                if mutation=='index':value['patients'][1]['index_rule']='another rule'
                if mutation=='pro':record['role']['isFeatureOf']='P99'
                if mutation=='concept':value['patients'][1]['records'][1]['value']=['unknown:Concept']
                if mutation=='clinical':value['scope']='clinical'
                with self.assertRaises(ValueError):p.SimilarityEngine(value,self.profile)

    def test_unsupported_filters_and_nonfinite_weights_are_rejected(self):
        revision=self.revision()
        for operation in ({'type':'set_weights','weights':{'age_band':'NaN','baseline_creatinine':'1','clinical_concepts':'1'}},
                          {'type':'add_filter','predicate':{'component':'outcome','operator':'eq','value':'alive'}},
                          {'type':'add_filter','predicate':{'component':'baseline_creatinine','operator':'between','value':{'min':'2','max':'1'}}},
                          {'type':'set_weights','weights':{'age_band':'0','baseline_creatinine':'0','clinical_concepts':'0'}}):
            with self.subTest(operation=operation):
                with self.assertRaises(ValueError):self.engine.refine(revision['revision_id'],operation)

    def test_exact_fraction_order_survives_equal_display_rounding(self):
        self.dataset['patients'] = self.dataset['patients'][:3]
        self.profile['creatinine_scale'] = '1000000'
        self.profile['weights']['baseline_creatinine'] = '0.000000000001'
        for pid, value in (('P00','0'), ('P01','0.000000000002'), ('P02','0.000000000001')):
            records = self.patient(pid)['records']
            next(r for r in records if r['id']==pid+'-B')['value']['hasValue'] = value
            if pid != 'P00':
                next(r for r in records if r['component']=='clinical_concepts')['value'] = ['ex:DrugB']
        rows = p.SimilarityEngine(self.dataset,self.profile).initial('P00')['results']['ranked']
        self.assertEqual(rows[0]['ranking_distance'], rows[1]['ranking_distance'])
        self.assertNotEqual(rows[0]['ranking_fraction'], rows[1]['ranking_fraction'])
        self.assertEqual([r['patient_id'] for r in rows], ['P02','P01'])

    def test_extreme_decimal_exponents_fail_without_overflow(self):
        for value in ('1e999999999', '0e999999999', '-1e999999999', '1e-999999999', 'Infinity', 'sNaN'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError): p.decimal(value)

    def test_incomplete_concept_set_reduces_coverage_but_positive_filter_witness_survives(self):
        initial = self.revision()
        pair = self.engine.inspect(initial['revision_id'], 'P10')
        component = pair['candidate']['features']['clinical_concepts']
        self.assertFalse(component['observed'])
        self.assertEqual(component['missing_reason'], 'INCOMPLETE_CONCEPT_SET')
        self.assertLess(Decimal(pair['comparison']['coverage']), 1)
        present = self.engine.refine(initial['revision_id'], {'type':'add_filter','predicate':{'component':'clinical_concepts','operator':'contains','value':'ex:DrugA'}})
        absent = self.engine.refine(initial['revision_id'], {'type':'add_filter','predicate':{'component':'clinical_concepts','operator':'contains','value':'ex:DrugB'}})
        self.assertIn('P10', present['results']['eligible_patient_ids'])
        self.assertIn('P10', absent['changes']['unresolved'])

    def test_file_admission_reads_no_more_than_limit_plus_one(self):
        from unittest.mock import mock_open, patch
        reader = mock_open(read_data=b'x'*(8*1024*1024+1))
        with patch.object(p.Path, 'open', reader):
            with self.assertRaisesRegex(ValueError, 'SNAPSHOT_SIZE_LIMIT'):
                p.SimilarityEngine('oversized.json',self.profile)
        reader().read.assert_called_once_with(8*1024*1024+1)

    def test_snapshot_scope_and_resource_bounds_are_explicit(self):
        value=deepcopy(self.dataset);value['patients']=value['patients'][:1]
        with self.assertRaisesRegex(ValueError,'PATIENT_BOUND'):p.SimilarityEngine(value,self.profile)
        with self.assertRaisesRegex(ValueError,'INVALID_TOP_K'):self.engine.initial('P00',False)
        with self.assertRaisesRegex(ValueError,'UNKNOWN_PATIENT'):self.engine.initial('missing')
        with self.assertRaisesRegex(ValueError,'PATIENT_OUTSIDE'):self.engine.inspect(self.revision()['revision_id'],'P00')
        self.assertFalse(self.revision()['clinical_validity_claim'])
        self.assertFalse(self.revision()['outcomes_used_for_matching'])

if __name__=='__main__':unittest.main()
