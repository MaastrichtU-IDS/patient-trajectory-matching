"""H0 cutoff, coverage, immutable refinement and replay guarantees."""
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
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

    @staticmethod
    def ranking(revision):
        """Results without the IC population's snapshot digest. The tests that use this change
        the dataset in ways similarity must ignore, so that digest is all that may differ."""
        results = deepcopy(revision['results'])
        results.get('concept_measure', {}).get('ic_population', {}).pop('dataset_sha256', None)
        return results

    def test_default_population_and_provenance_match_guided_source(self):
        original = json.loads((p.ROOT/'examples/guided-cohort/cohort-data.json').read_text())
        self.assertEqual([x['patient_id'] for x in self.dataset['patients']], [x['patient_id'] for x in original['patients']])
        self.assertEqual(self.engine.metadata()['source_dataset_sha256'], sha256((p.ROOT/'examples/guided-cohort/cohort-data.json').read_bytes()).hexdigest())
        inspected = self.engine.inspect(self.revision()['revision_id'], 'P03')
        evidence = inspected['candidate']['features']['baseline_creatinine']['evidence'][0]
        self.assertEqual(evidence['value']['hasValue'], '0.80')
        self.assertEqual(evidence['source']['original_unit'], 'mg/L')
        self.assertEqual(evidence['source']['binding']['normalized_value'], '0.80')
        self.assertEqual(evidence['claim']['refersTo'], evidence['role']['id'])
        # sulo:hasParticipant would assert the process the overlay only reports on.
        self.assertNotIn('process', evidence)
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
        self.assertEqual(self.ranking(modified),self.ranking(self.revision()))
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
        self.assertEqual(self.ranking(actual),self.ranking({'results': baseline_results}))

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
        pair = self.engine.inspect(self.engine.initial('P00', 2, 'ancestor_jaccard')['revision_id'],'P02')
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


class InformationContentTests(unittest.TestCase):
    """IC-weighted concept measures are an explicit option; the default is unchanged.

    In the authored snapshot P00-P09 have complete concept sets and P10 does not, so the
    IC population is ten patients: eight carry ex:DrugA (P02 through its ancestor), one
    ex:DrugAChild and two ex:DrugB. With p = (count+1)/11 the values below follow.
    """
    IC = {'ex:DrugA': '0.200670695462', 'ex:DrugAChild': '1.704748092238', 'ex:DrugB': '1.29928298413'}

    def setUp(self):
        self.dataset = json.loads((p.EXAMPLE/'dataset.json').read_text())
        self.profile = json.loads((p.EXAMPLE/'profile.json').read_text())
        self.engine = p.SimilarityEngine(self.dataset, self.profile)

    def concepts(self, pid, dataset=None):
        patient = next(x for x in (dataset or self.dataset)['patients'] if x['patient_id'] == pid)
        return next(r for r in patient['records'] if r['component'] == 'clinical_concepts')

    def component(self, engine, reference, candidate, measure):
        revision = engine.initial(reference, 2, measure)
        return engine.inspect(revision['revision_id'], candidate)['comparison']['components'][2]

    @staticmethod
    def exact(value):
        with localcontext() as context:
            context.prec = 28
            return p.lexical(Decimal(value.numerator) / Decimal(value.denominator))

    def test_ic_values_on_the_hand_computed_population(self):
        table = self.engine.information_content()
        self.assertEqual({c: v['ic'] for c, v in table['concepts'].items()}, self.IC)
        self.assertEqual({c: v['count'] for c, v in table['concepts'].items()},
                         {'ex:DrugA': 8, 'ex:DrugAChild': 1, 'ex:DrugB': 2})
        population = table['population']
        self.assertEqual((population['patients'], population['unobserved_patients'], population['roots']), (10, 1, []))
        self.assertEqual(population['counts_sha256'], p.digest({'ex:DrugA': 8, 'ex:DrugAChild': 1, 'ex:DrugB': 2}))
        self.assertEqual(population['dataset_sha256'], self.engine.metadata()['dataset_sha256'])
        self.assertEqual(population['smoothing'], '(count+1)/(patients+1)')

    def test_simgic_weights_the_same_sets_jaccard_counts(self):
        """P00 {DrugA} against P02 {DrugA, DrugAChild}: Jaccard 0.5, simGIC far less similar."""
        self.assertEqual(self.component(self.engine, 'P00', 'P02', 'ancestor_jaccard')['distance'], '0.5')
        component = self.component(self.engine, 'P00', 'P02', 'simgic')
        shared, union = Fraction(self.IC['ex:DrugA']), Fraction(self.IC['ex:DrugA']) + Fraction(self.IC['ex:DrugAChild'])
        self.assertEqual(component['distance'], self.exact(1 - shared/union))
        self.assertEqual(component['measure'], 'simgic')
        self.assertEqual(component['explanation']['terms'],
                         [{'concept': 'ex:DrugA', 'ic': self.IC['ex:DrugA'], 'in': 'both'},
                          {'concept': 'ex:DrugAChild', 'ic': self.IC['ex:DrugAChild'], 'in': 'candidate'}])
        # simGIC is symmetric: the same union and intersection from either side.
        self.assertEqual(self.component(self.engine, 'P02', 'P00', 'simgic')['distance'], component['distance'])

    def test_an_uninformative_shared_root_counts_for_jaccard_but_not_simgic(self):
        self.profile['concept_ancestors'] = {'ex:DrugA': ['ex:Medication'], 'ex:DrugAChild': ['ex:DrugA', 'ex:Medication'],
                                             'ex:DrugB': ['ex:Medication']}
        engine = p.SimilarityEngine(self.dataset, self.profile)
        self.assertEqual(self.component(engine, 'P00', 'P04', 'ancestor_jaccard')['distance'], '0.6666666666666666666666666667')
        self.assertEqual(self.component(engine, 'P00', 'P04', 'simgic')['distance'], '1')

    def test_best_match_resnik_is_reference_to_candidate(self):
        forward = self.component(self.engine, 'P00', 'P02', 'resnik_bma')
        backward = self.component(self.engine, 'P02', 'P00', 'resnik_bma')
        # P02's DrugAChild explains all of P00's DrugA; P00's DrugA explains little of DrugAChild.
        self.assertEqual(forward['distance'], '0')
        self.assertEqual(backward['distance'], self.exact(1 - Fraction(self.IC['ex:DrugA'])/Fraction(self.IC['ex:DrugAChild'])))
        self.assertEqual(backward['explanation']['terms'],
                         [{'reference_concept': 'ex:DrugAChild', 'candidate_concept': 'ex:DrugA', 'shared_ancestor': 'ex:DrugA',
                           'ic': self.IC['ex:DrugA'], 'reference_ic': self.IC['ex:DrugAChild']}])
        unrelated = self.component(self.engine, 'P00', 'P04', 'resnik_bma')
        self.assertEqual(unrelated['distance'], '1')
        self.assertEqual(unrelated['explanation']['terms'][0]['shared_ancestor'], None)

    def test_smoothing_bounds_unseen_terms_and_the_root_is_zero(self):
        """An unseen concept gets ln(N+1); the root is IC 0 even when a declared empty set lacks it."""
        self.profile['concept_ancestors'] = {'ex:DrugA': ['ex:Medication'], 'ex:DrugAChild': ['ex:DrugA', 'ex:Medication'],
                                             'ex:DrugB': ['ex:Medication'], 'ex:DrugC': ['ex:Medication']}
        record = self.concepts('P01'); record['value'] = []
        table = p.SimilarityEngine(self.dataset, self.profile).information_content()
        self.assertEqual(table['population']['patients'], 10)
        self.assertEqual(table['population']['roots'], ['ex:Medication'])
        self.assertEqual(table['concepts']['ex:DrugC'], {'count': 0, 'ic': '2.397895272798'})
        # Nine of ten observed patients carry the root; unforced it would be ln(11/10).
        self.assertEqual(table['concepts']['ex:Medication'], {'count': 9, 'ic': '0'})
        self.assertTrue(all(Decimal(v['ic']) <= Decimal('2.397895272798') for v in table['concepts'].values()))

    def test_an_empty_reference_is_not_a_perfect_match_under_resnik_bma(self):
        """With nothing to explain, every candidate would score 0. The component is missing
        instead, so the reference ranks on its other components with reduced coverage, and
        the concept axis cannot favour one candidate over another."""
        record = self.concepts('P00'); record['value'] = []; record['coverage_complete'] = True
        engine = p.SimilarityEngine(self.dataset, self.profile)
        revision = engine.initial('P00', 10, 'resnik_bma')
        for candidate in ('P01', 'P02', 'P05'):
            with self.subTest(candidate=candidate):
                comparison = engine.inspect(revision['revision_id'], candidate)['comparison']
                concept = comparison['components'][2]
                self.assertIsNone(concept['distance'])
                self.assertTrue(concept['missing'])
                self.assertEqual(concept['explanation'], {'undefined': 'EMPTY_REFERENCE_SET', 'terms': []})
                self.assertLess(Decimal(comparison['coverage']), 1)
        # The other measures still compare the empty set, and the reverse direction is defined.
        self.assertEqual(self.component(engine, 'P00', 'P01', 'simgic')['distance'], '1')
        self.assertEqual(self.component(engine, 'P01', 'P00', 'resnik_bma')['distance'], '1')

    def test_empty_population_and_ic_zero_union_are_defined(self):
        empty = deepcopy(self.dataset)
        for patient in empty['patients']:
            self.concepts(patient['patient_id'], empty)['coverage_complete'] = False
        engine = p.SimilarityEngine(empty, self.profile)
        table = engine.information_content()
        self.assertEqual((table['population']['patients'], table['population']['unobserved_patients']), (0, 11))
        self.assertEqual({v['ic'] for v in table['concepts'].values()}, {'0'})
        self.assertIsNone(self.component(engine, 'P00', 'P01', 'simgic')['distance'])
        # Everyone carries DrugA, so it is IC 0 and the union carries no information.
        for patient in self.dataset['patients']:
            record = self.concepts(patient['patient_id']); record['value'] = ['ex:DrugA']; record['coverage_complete'] = True
        engine = p.SimilarityEngine(self.dataset, self.profile)
        self.assertEqual(engine.information_content()['concepts']['ex:DrugA']['ic'], '0')
        for measure in ('simgic', 'resnik_bma'):
            with self.subTest(measure=measure):
                self.assertEqual(self.component(engine, 'P00', 'P01', measure)['distance'], '0')
        self.assertEqual(self.component(engine, 'P00', 'P01', 'simgic')['explanation']['union_ic'], '0')

    def test_a_query_without_a_measure_is_byte_identical_to_before(self):
        """Digests taken from the engine before the IC measures existed. A query recorded
        then has no measure key and must still mean ancestor-Jaccard."""
        ids = [x['patient_id'] for x in self.engine.metadata()['patients']]
        revisions = {pid: self.engine._initial(pid, 10, None) for pid in ids}
        self.assertEqual(p.digest([revisions[pid]['results'] for pid in ids]),
                         '29fd2e45f3dac2c942a724da3a2e354171e7b0b6f61d495b9f6dcd4e1200b191')
        self.assertEqual(p.digest([self.engine.inspect(revisions[pid]['revision_id'], c)['comparison'] for pid in ids for c in ids if c != pid]),
                         'a40fc5a0a5ffe82d261310cd645abf9565c058ccb9c52eac182fa14d0996a91d')
        # Everything in the revision but the module digest, which any edit changes.
        self.assertEqual(p.digest({k: v for k, v in revisions['P00'].items() if k not in ('implementation_sha256', 'revision_id')}),
                         'e52fde9bbe8fa3c69eb8a9bde2db46267a3076ca75e82f778fffc37cb8fe7534')
        self.assertNotIn('concept_measure', revisions['P00']['query'])
        replayed = p.SimilarityEngine.replay(self.engine.manifest(revisions['P00']['revision_id']))
        self.assertEqual(replayed.get_revision(revisions['P00']['revision_id']), revisions['P00'])
        # Naming ancestor-Jaccard explicitly ranks identically and records the measure.
        explicit = self.engine.initial('P00', 10, 'ancestor_jaccard')
        self.assertEqual(explicit['results'].pop('concept_measure'), {'measure': 'ancestor_jaccard'})
        self.assertEqual(explicit['results'], revisions['P00']['results'])
        weighted = self.engine.refine(revisions['P00']['revision_id'], {'type': 'set_concept_measure', 'concept_measure': 'simgic'})
        back = self.engine.refine(weighted['revision_id'], {'type': 'set_concept_measure', 'concept_measure': 'ancestor_jaccard'})
        self.assertEqual(back['query']['concept_measure'], 'ancestor_jaccard')
        self.assertEqual(back['results']['ranked'], revisions['P00']['results']['ranked'])

    def test_new_queries_default_to_simgic_and_name_it(self):
        self.assertEqual(self.engine.metadata()['default_concept_measure'], 'simgic')
        self.assertEqual(self.engine.metadata()['legacy_concept_measure'], 'ancestor_jaccard')
        revision = self.engine.initial('P00', 10)
        self.assertEqual(revision['query']['concept_measure'], 'simgic')
        self.assertEqual(revision['results']['concept_measure']['measure'], 'simgic')
        self.assertIn('ic_population', revision['results']['concept_measure'])
        self.assertEqual(revision, self.engine.initial('P00', 10, 'simgic'))

    def test_the_concept_measure_option_is_validated(self):
        for measure in ('jaccard', 'SIMGIC', None, ['simgic'], 1):
            with self.subTest(measure=measure), self.assertRaisesRegex(ValueError, 'UNSUPPORTED_CONCEPT_MEASURE'):
                self.engine.initial('P00', 2, measure)
        revision = self.engine.initial('P00', 2)
        for operation in ({'type': 'set_concept_measure', 'concept_measure': 'cosine'},
                          {'type': 'set_concept_measure'},
                          {'type': 'set_concept_measure', 'concept_measure': 'simgic', 'population': 'other'}):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.engine.refine(revision['revision_id'], operation)
        manifest = self.engine.manifest(self.engine.initial('P00', 2, 'simgic')['revision_id'])
        manifest['revisions'][0]['query']['concept_measure'] = 'cosine'
        with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_CONCEPT_MEASURE'):
            p.SimilarityEngine.replay(manifest)

    def test_an_ic_ranking_records_its_population_and_replays(self):
        initial = self.engine.initial('P00', 2, 'simgic')
        self.assertEqual(initial['query']['concept_measure'], 'simgic')
        recorded = initial['results']['concept_measure']
        self.assertEqual(recorded, {'measure': 'simgic', 'ic_population': self.engine.information_content()['population']})
        self.assertEqual(initial['result_sha256'], p.digest(initial['results']))
        inspected = self.engine.inspect(initial['revision_id'], 'P10')
        self.assertEqual(inspected['concept_measure'], recorded)
        # A partial concept set is still missing, not scored.
        self.assertIsNone(inspected['comparison']['components'][2]['distance'])
        self.assertIsNone(inspected['comparison']['components'][2]['explanation'])
        resnik = self.engine.refine(initial['revision_id'], {'type': 'set_concept_measure', 'concept_measure': 'resnik_bma'})
        filtered = self.engine.refine(resnik['revision_id'], {'type': 'add_filter', 'predicate': {'component': 'clinical_concepts', 'operator': 'contains', 'value': 'ex:DrugA'}})
        # The population is the snapshot, not the filtered pool or the reference's neighbours.
        self.assertEqual(filtered['results']['concept_measure']['ic_population'], recorded['ic_population'])
        self.assertEqual(filtered['changes']['removed'], ['P04', 'P06'])
        replayed = p.SimilarityEngine.replay(self.engine.manifest(filtered['revision_id']))
        self.assertEqual(replayed.get_revision(filtered['revision_id']), filtered)

    def test_decimal_context_cannot_change_information_content(self):
        expected = self.engine.information_content(), self.engine.initial('P02', 5, 'resnik_bma')['results']
        with localcontext() as context:
            context.prec = 7
            engine = p.SimilarityEngine(self.dataset, self.profile)
            self.assertEqual((engine.information_content(), engine.initial('P02', 5, 'resnik_bma')['results']), expected)


class RecordedPublicDemoScopeTests(unittest.TestCase):
    """The engine admits one non-authored scope, and only one.

    `docs/patient-similarity.md` states the constructor is not an arbitrary clinical
    data loader. `recorded_public_demo` keeps that true by construction: the dataset
    must carry the digests of the committed open-access demo pin, and they must equal
    it exactly, so the only non-authored data admitted is the pinned public release.
    """
    def setUp(self):
        self.dataset = json.loads((p.EXAMPLE / 'dataset.json').read_text())
        self.profile = json.loads((p.EXAMPLE / 'profile.json').read_text())
        pin = json.loads((p.ROOT / 'data/clinical-source-demo-pin.json').read_bytes())
        self.pin = {name: meta['file_sha256'] for name, meta in pin['files'].items()}
        demographics = json.loads((p.ROOT / 'data/clinical-demographics-demo-pin.json').read_bytes())
        self.demographics = {name: meta['file_sha256'] for name, meta in demographics['files'].items()}

    def recorded(self, **overrides):
        dataset = dict(self.dataset, scope='recorded_public_demo', source_pin=dict(self.pin))
        dataset.update(overrides)
        return dataset

    def profile_for(self, **overrides):
        return dict(self.profile, mapping_review='pinned_public_demo_source_fidelity', **overrides)

    def test_pinned_demo_dataset_is_admitted(self):
        engine = p.SimilarityEngine(self.recorded(), self.profile_for())
        self.assertEqual(engine.metadata()['scope'], 'recorded_public_demo')

    def test_a_cohort_carrying_demographics_is_admitted(self):
        """Deriving age needs hosp/patients.csv.gz, which the ICU pin may not name.

        `data/clinical-source-demo-pin.json` is the ICU source preflight manifest: the
        preflight verifier asserts its table set equals the preflight's source set, and
        the pressure benchmarks bind its digest. Demographics therefore carry their own
        pin, and a recorded dataset may declare the ICU set alone or the ICU set plus
        demographics -- those two, and nothing else.
        """
        engine = p.SimilarityEngine(self.recorded(source_pin=dict(self.pin, **self.demographics)),
                                    self.profile_for())
        self.assertEqual(engine.metadata()['scope'], 'recorded_public_demo')

    def test_a_partial_demographic_pin_is_refused(self):
        for broken in (dict(self.pin, patients='0' * 64),
                       dict(self.demographics),
                       {name: value for name, value in self.pin.items() if name != 'chartevents'}):
            with self.subTest(declared=sorted(broken)), \
                    self.assertRaisesRegex(ValueError, 'SOURCE_PIN_DIFFERS_FROM_COMMITTED_DEMO'):
                p.SimilarityEngine(self.recorded(source_pin=broken), self.profile_for())

    def test_unpinned_source_is_refused(self):
        wrong = dict(self.pin, chartevents='0' * 64)
        with self.assertRaisesRegex(ValueError, 'SOURCE_PIN_DIFFERS_FROM_COMMITTED_DEMO'):
            p.SimilarityEngine(self.recorded(source_pin=wrong), self.profile_for())

    def test_recorded_scope_without_a_pin_is_refused(self):
        dataset = self.recorded()
        del dataset['source_pin']
        with self.assertRaisesRegex(ValueError, 'RECORDED_DEMO_REQUIRES_SOURCE_PIN'):
            p.SimilarityEngine(dataset, self.profile_for())

    def test_authored_dataset_may_not_carry_a_pin(self):
        with self.assertRaisesRegex(ValueError, 'SOURCE_PIN_LIMITED_TO_RECORDED_DEMO'):
            p.SimilarityEngine(dict(self.dataset, source_pin=dict(self.pin)), self.profile)

    def test_each_scope_requires_its_own_mapping_review(self):
        with self.assertRaisesRegex(ValueError, 'MAPPING_REVIEW_MUST_MATCH_SCOPE'):
            p.SimilarityEngine(self.recorded(), self.profile)
        with self.assertRaisesRegex(ValueError, 'MAPPING_REVIEW_MUST_MATCH_SCOPE'):
            p.SimilarityEngine(self.dataset, self.profile_for())

    def test_other_scopes_are_still_refused(self):
        for scope in ('recorded_clinical', 'production', 'mimic_iv_full'):
            with self.subTest(scope=scope), self.assertRaisesRegex(ValueError, 'UNPERMITTED_DATASET_SCOPE'):
                p.SimilarityEngine(dict(self.dataset, scope=scope), self.profile)

    def test_the_revision_record_names_the_scope_it_ranked(self):
        """The revision is the exported, replayable record of a ranking.

        It is read away from the running server, so a fixed authored string in it
        describes pinned recorded stays as synthetic to every later reader. The
        scope is derived from the admitted dataset so the two cannot disagree.
        """
        authored = p.SimilarityEngine(self.dataset, self.profile)
        recorded = p.SimilarityEngine(self.recorded(), self.profile_for())
        for engine, expected in ((authored, 'bounded_authored_synthetic_exhaustive_ranking'),
                                 (recorded, 'bounded_recorded_public_demo_exhaustive_ranking')):
            revision = engine.initial(engine.metadata()['patients'][0]['patient_id'], 5)
            with self.subTest(scope=engine.metadata()['scope']):
                self.assertEqual(revision['scope'], expected)
                self.assertIn(engine.metadata()['scope'], revision['scope'])

    def test_a_clinical_validity_claim_is_refused_in_either_scope(self):
        for dataset in (dict(self.dataset, clinical_validity_claim=True),
                        self.recorded(clinical_validity_claim=True)):
            with self.subTest(scope=dataset['scope']), self.assertRaisesRegex(ValueError, 'UNPERMITTED_DATASET_SCOPE'):
                p.SimilarityEngine(dataset, self.profile)

if __name__=='__main__':unittest.main()
