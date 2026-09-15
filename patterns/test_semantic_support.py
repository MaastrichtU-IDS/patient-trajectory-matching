"""Rust/finite-model differential tests and semantic-to-temporal gate regressions."""
from copy import deepcopy
import itertools
import json
import random
import subprocess
import unittest
from unittest.mock import patch

from . import bounded_cohort as bc
from . import bounded_intervals as bt
from . import exact_intervals as ei
from . import semantic_support as ss

N = 'https://example.org/trajectory/semantic/'
C = lambda value: {'class': N + value}


def fixture():
    root = ei.ROOT / 'examples'
    source = json.loads((root / 'bounded-interval/source.json').read_text())
    module = json.loads((root / 'semantic-support/module.json').read_text())
    query = json.loads((root / 'semantic-support/query.json').read_text())
    return bt.prepare(source), module, query


def small_model():
    return {'classes': [N + x for x in ('A', 'B', 'C')], 'individuals': [N + 'x', N + 'y'],
            'class_assertions': [{'individual': N + 'x', 'class': N + 'A'}],
            'property_assertions': [], 'rules': [{'id': 'a_b', 'if': C('A'), 'then': N + 'B'}],
            'disjoint': []}


def normalize(result):
    return [(t['patient_id'], t['status'], [(b['status'], sorted((s, x['event_id']) for s, x in b['slots'].items()))
                                           for b in t['bindings']]) for t in result['trajectories']]


class SemanticSupportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot, cls.module, cls.query = fixture()
        cls.baseline = ss.execute(cls.snapshot, cls.query, cls.module)

    def test_actual_rust_inferred_class_and_temporal_answers(self):
        result = self.baseline
        self.assertEqual(result['status'], 'READY')
        self.assertFalse(result['full_owl_mapping_verified'])
        self.assertEqual(result['semantic_support']['backend_result']['backend']['version'], '0.4.28')
        matching = result['matching']
        self.assertEqual(matching['certain_patient_ids'], ['P1'])
        self.assertEqual(matching['possible_patient_ids'], ['P1', 'P2'])
        self.assertEqual([t['status'] for t in matching['trajectories']],
                         ['CERTAIN_MATCH', 'POSSIBLE_MATCH', 'NO_RECORDED_MATCH', 'INCOMPARABLE'])
        self.assertNotIn(N + 'AntibioticAdministration', [a['class'] for a in self.module['class_assertions']])
        self.assertTrue(result['semantic_support']['reference']['derivations'])

    def test_same_answers_as_existing_profile_for_existing_selectors(self):
        query = deepcopy(self.query)
        query['slots'][0]['class_iri'] = str(bt.BT.Infusion)
        current = ss.execute(self.snapshot, query, self.module)['matching']
        query['profile'] = bt.QUERY_PROFILE
        previous = bc.execute(self.snapshot, query)
        self.assertEqual(normalize(current), normalize(previous))

    def test_original_pro_bindings_and_temporal_proofs_survive(self):
        for trajectory in self.baseline['matching']['trajectories']:
            for binding in trajectory['bindings']:
                self.assertIn('query_edges', binding)
                for item in binding['slots'].values():
                    original = self.snapshot.evidence[item['event_id']]
                    for key in ('process', 'patient_role', 'patient_bearer', 'evidence_id'):
                        self.assertEqual(item[key], original[key])
                    self.assertEqual(item['semantic_support']['status'], 'ENTAILED')
        context = self.baseline['matching']['context']
        self.assertEqual(context['semantic_context_id'], self.baseline['context_id'])

    def test_missing_role_type_cannot_be_replaced_by_generic_participation(self):
        module = deepcopy(self.module)
        module['class_assertions'] = [a for a in module['class_assertions'] if a['class'] != N + 'AdministeredDrugRole']
        result = ss.execute(self.snapshot, self.query, module)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['matching']['possible_patient_ids'], [])

    def test_existential_conditions_share_one_role_witness(self):
        module = deepcopy(self.module)
        # Retain typed roles on each process, but move antibiotic bearers to
        # separate, untyped roles that the processes also participate through.
        for row in list(module['property_assertions']):
            if row['property'] == str(ei.S.isFeatureOf):
                other = row['subject'] + '-other'
                module['individuals'].append(other)
                process = next(a['subject'] for a in module['property_assertions']
                               if a['object'] == row['subject'])
                row['subject'] = other
                module['property_assertions'].append({'subject': process, 'property': str(ei.S.hasParticipant), 'object': other})
        result = ss.execute(self.snapshot, self.query, module)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['matching']['possible_patient_ids'], [])

    def test_rust_and_reference_agree_on_cycles_conjunction_and_nested_existentials(self):
        model = small_model()
        model['property_assertions'] = [{'subject': N + 'y', 'property': str(ei.S.hasParticipant), 'object': N + 'x'}]
        model['rules'].extend([
            {'id': 'b_a', 'if': C('B'), 'then': N + 'A'},
            {'id': 'both', 'if': {'all': [C('A'), C('B')]}, 'then': N + 'C'},
            {'id': 'via_role', 'if': {'some': {'property': str(ei.S.hasParticipant), 'filler': C('C')}}, 'then': N + 'C'}])
        result = ss.check(model, model['classes'])
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['memberships'][N + 'C'], [N + 'x', N + 'y'])

    def test_reference_against_all_finite_interpretations(self):
        # Enumerate ALL class and property extensions (including unasserted
        # edges): countermodels, not a closed-world database test.
        rng = random.Random(814)
        for _ in range(10):
            model = small_model()
            model['classes'] = [N + 'A', N + 'B']
            prop = str(ei.S.hasParticipant)
            model['rules'] = [{'id': 'rule', 'if': {'some': {'property': prop, 'filler': C('A')}}, 'then': N + 'B'}]
            if rng.choice([True, False]):
                model['property_assertions'] = [{'subject': N + 'y', 'property': prop, 'object': N + 'x'}]
            if rng.choice([True, False]):
                model['disjoint'] = [{'id': 'd', 'classes': model['classes']}]
            if rng.choice([True, False]):
                model['class_assertions'].append({'individual': N + 'x', 'class': N + 'B'})
            pairs = list(itertools.product(model['individuals'], model['classes']))
            edges = list(itertools.product(model['individuals'], model['individuals']))
            valid = []
            for bits in itertools.product([False, True], repeat=len(pairs) + len(edges)):
                types = {p for p, bit in zip(pairs, bits[:len(pairs)]) if bit}
                relation = {p for p, bit in zip(edges, bits[len(pairs):]) if bit}
                if any((a['individual'], a['class']) not in types for a in model['class_assertions']): continue
                if any((a['subject'], a['object']) not in relation for a in model['property_assertions']): continue
                if any((v, N + 'A') in types and (u, N + 'B') not in types for u, v in relation): continue
                if model['disjoint'] and any((i, N + 'A') in types and (i, N + 'B') in types for i in model['individuals']): continue
                valid.append(types)
            result = ss.check(model, model['classes'])
            self.assertEqual(result['status'], 'READY' if valid else 'INCONSISTENT_ONTOLOGY')
            if valid:
                certain = set.intersection(*valid)
                observed = {(i, c) for c, people in result['memberships'].items() for i in people}
                self.assertEqual(observed, certain)

    def test_inconsistent_module_blocks_matcher_before_explosion(self):
        module = deepcopy(self.module)
        drug = next(a['individual'] for a in module['class_assertions'] if a['class'] == N + 'Antibiotic')
        module['class_assertions'].append({'individual': drug, 'class': N + 'NonAntibiotic'})
        with patch.object(bc, '_execute_supported', side_effect=AssertionError('must not join')):
            result = ss.execute(self.snapshot, self.query, module)
        self.assertEqual(result['status'], 'INCONSISTENT_ONTOLOGY')
        self.assertIsNone(result['matching'])
        self.assertTrue(result['semantic_support']['reference']['clashes'])

    def test_uninstantiated_unsatisfiable_class_is_not_inconsistent(self):
        model = small_model()
        model['rules'].extend([{'id': 'c_a', 'if': C('C'), 'then': N + 'A'}, {'id': 'c_b', 'if': C('C'), 'then': N + 'B'}])
        model['disjoint'] = [{'id': 'd', 'classes': [N + 'A', N + 'C']}]
        result = ss.check(model, model['classes'])
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['memberships'][N + 'C'], [])

    def test_no_unique_name_or_equality_inference_claim(self):
        model = small_model()
        result = ss.check(model, model['classes'])
        self.assertEqual(result['memberships'][N + 'B'], [N + 'x'])
        # Neither owl:sameAs nor owl:differentFrom is emitted.
        self.assertNotIn('SameIndividual', result['ontology_ofn'])
        self.assertNotIn('DifferentIndividuals', result['ontology_ofn'])

    def test_partial_or_false_positive_support_blocks_all_matching(self):
        actual = self.baseline['semantic_support']['backend_result']
        for extra in (False, True):
            broken = deepcopy(actual)
            cls = N + 'AntibioticAdministration'
            if extra: broken['memberships'][cls].append(self.snapshot.evidence['P3a']['process'])
            else: broken['memberships'][cls].pop()
            with self.subTest(extra=extra), patch.object(ss, 'run_backend', return_value=broken), patch.object(bc, '_execute_supported', side_effect=AssertionError('must not join')):
                result = ss.execute(self.snapshot, self.query, self.module)
                self.assertEqual(result['status'], 'UNRESOLVED_SEMANTICS')
                self.assertFalse(result['semantic_support_complete'])
                self.assertIsNone(result['matching'])

    def test_consistency_disagreement_blocks(self):
        broken = deepcopy(self.baseline['semantic_support']['backend_result'])
        broken['consistent'] = False
        broken['memberships'] = {}
        with patch.object(ss, 'run_backend', return_value=broken):
            result = ss.execute(self.snapshot, self.query, self.module)
        self.assertEqual(result['semantic_support']['reason_code'], 'CONSISTENCY_DISAGREEMENT')

    def test_drops_warnings_stderr_missing_metadata_and_future_flags_block(self):
        actual = self.baseline['semantic_support']['backend_result']
        mutations = [{'dropped': {'HasKey': 1}}, {'warnings': ['incomplete']}, {'stderr': 'warning'},
                     {'incomplete': True}, {'consistent': 1}, {'backend': {}}, {'backend': []}, {'memberships': []}]
        for change in mutations:
            with self.subTest(change=change), patch.object(ss, 'run_backend', return_value={**actual, **change}):
                self.assertEqual(ss.execute(self.snapshot, self.query, self.module)['status'], 'UNRESOLVED_SEMANTICS')

    def test_worker_errors_and_unavailable_version_do_not_become_no_match(self):
        for status in ('BACKEND_TIMEOUT', 'BACKEND_ERROR', 'UNSUPPORTED_BACKEND_VERSION', 'DROPPED_AXIOMS', 'MALFORMED_BACKEND_RESPONSE'):
            with self.subTest(status=status), patch.object(ss, 'run_backend', return_value={'status': status}):
                result = ss.execute(self.snapshot, self.query, self.module)
                self.assertEqual(result['semantic_support']['reason_code'], status)
                self.assertIsNone(result['matching'])

    def test_subprocess_timeout_malformed_json_and_configuration_isolation(self):
        with patch.object(subprocess, 'run', side_effect=subprocess.TimeoutExpired('worker', 1)):
            self.assertEqual(ss.run_backend('Ontology()', [], 1)['status'], 'BACKEND_TIMEOUT')
        with patch.dict('os.environ', {'RUSTDL_MAX_NODES': '1'}), patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'nonsense', '')) as run:
            self.assertEqual(ss.run_backend('Ontology()', [], 1)['status'], 'MALFORMED_BACKEND_RESPONSE')
            self.assertNotIn('RUSTDL_MAX_NODES', run.call_args.kwargs['env'])
            self.assertEqual(run.call_args.kwargs['timeout'], 1)

    def test_source_correction_requires_new_semantic_selection(self):
        source = deepcopy(self.snapshot.source)
        source['variables'][0]['upper_us'] += 1
        snapshot = bt.prepare(source)
        with self.assertRaisesRegex(ei.ContractError, 'STALE_SEMANTIC_SOURCE'):
            ss.execute(snapshot, self.query, self.module)

    def test_changed_rule_changes_context_and_answers(self):
        module = deepcopy(self.module)
        module['rules'] = []
        result = ss.execute(self.snapshot, self.query, module)
        self.assertNotEqual(result['context_id'], self.baseline['context_id'])
        self.assertEqual(result['matching']['possible_patient_ids'], [])
        self.assertEqual(result['semantic_support']['memberships'][N + 'AntibioticAdministration'], [])

    def test_unsupported_owl_constructs_properties_and_imports_rejected(self):
        for change in ({'imports': []}, {'sameAs': []}, {'property_characteristics': []}):
            with self.subTest(change=change), self.assertRaises(ei.ContractError):
                ss.build_model(self.snapshot, {**self.module, **change})
        for expression in ({'not': C('A')}, {'oneOf': [N + 'x']}, {'min': 2}, {'all': []}):
            model = small_model(); model['rules'][0]['if'] = expression
            with self.subTest(expression=expression), self.assertRaises(ei.ContractError): ss.ofn(model)
        model = small_model(); model['rules'][0]['then'] = {'some': C('A')}
        with self.assertRaises(ei.ContractError): ss.ofn(model)
        model = small_model(); model['property_assertions'] = [{'subject': N + 'x', 'property': N + 'hasPatient', 'object': N + 'y'}]
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_PROPERTY'): ss.ofn(model)

    def test_iri_injection_reserved_names_and_undeclared_entities_rejected(self):
        for value in ('http://bad/> ) Import(<http://bad', 'http://bad/\nX', 'http://www.w3.org/2002/07/owl#Thing'):
            with self.subTest(value=value), self.assertRaises(ei.ContractError): ss.iri(value)
        model = small_model(); model['class_assertions'][0]['individual'] = N + 'undeclared'
        with self.assertRaisesRegex(ei.ContractError, 'UNDECLARED_INDIVIDUAL'): ss.ofn(model)

    def test_finite_limits_and_duplicate_rule_ids_rejected(self):
        model = small_model(); model['individuals'] += [N + str(i) for i in range(64)]
        with self.assertRaisesRegex(ei.ContractError, 'SEMANTIC_LIMIT'): ss.ofn(model)
        model = small_model(); model['rules'] *= 2
        with self.assertRaisesRegex(ei.ContractError, 'DUPLICATE_RULE_ID'): ss.ofn(model)
        model = small_model(); expr = C('A')
        for _ in range(6): expr = {'some': {'property': str(ei.S.hasParticipant), 'filler': expr}}
        model['rules'][0]['if'] = expr
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_RULE_EXPRESSION'): ss.ofn(model)

    def test_unsupported_selector_and_old_api_do_not_silently_accept_query(self):
        query = deepcopy(self.query); query['slots'][0]['class_iri'] = N + 'Unknown'
        with self.assertRaisesRegex(ei.ContractError, 'UNDECLARED_SELECTOR'): ss.execute(self.snapshot, query, self.module)
        with self.assertRaises(ei.ContractError): bc.execute(self.snapshot, self.query)

    def test_ofn_has_no_imports_new_properties_or_identity_axioms(self):
        ofn = self.baseline['semantic_support']['ontology_ofn']
        self.assertIn('ObjectSomeValuesFrom(<https://w3id.org/sulo/hasParticipant>', ofn)
        for forbidden in ('Import(', 'DatatypeProperty(', 'SameIndividual(', 'FunctionalObjectProperty('):
            self.assertNotIn(forbidden, ofn)
        self.assertEqual(ofn.count('Declaration(ObjectProperty('), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
