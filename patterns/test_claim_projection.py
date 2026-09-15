"""Claim isolation, explicit acceptance, support replacement and checked matching."""
from copy import deepcopy
import json
import random
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, RDF, OWL, URIRef, XSD, BNode
from . import claim_projection as cp
from . import claim_rdf as cr
from . import claim_isolation as isolation
from . import exact_intervals as ei
from . import semantic_support as ss


def fixture():
    base = ei.ROOT / 'examples/claim-projection'
    return [json.loads((base / (name + '.json')).read_text())
            for name in ('store', 'policy', 'semantic-policy', 'query')]


class ClaimProjectionTests(unittest.TestCase):
    def setUp(self):
        self.store, self.policy, self.semantic, self.query = fixture()

    def bind(self):
        self.policy['store_sha256'] = cr.digest(self.store)
        self.policy['semantic_policy_sha256'] = cr.digest(self.semantic)
        claims = {c['id']: c for c in self.store['claims']}
        for d in self.policy['decisions']: d['claim_sha256'] = cr.digest(claims[d['claim_id']])

    def run_query(self):
        return cp.execute(self.store, self.policy, self.semantic, self.query)

    def claim(self, cid='admin_classified'):
        return next(c for c in self.store['claims'] if c['id'] == cid)

    def decide(self, cid, action='accept', parent=None, name='new_decision'):
        d = {'id': name, 'claim_id': cid, 'claim_sha256': cr.digest(self.claim(cid)),
             'action': action, 'supersedes': parent, 'reason': 'Synthetic test decision.'}
        self.policy['decisions'].append(d)
        self.bind()
        return d

    def test_accepted_pipeline_preserves_role_and_temporal_witnesses(self):
        r = self.run_query()
        self.assertEqual(r['status'], 'READY')
        self.assertEqual(r['matching']['certain_patient_ids'], ['P1'])
        g = Graph().parse(data=r['accepted_graph_turtle'], format='turtle')
        process = URIRef('https://example.org/trajectory/bounded-data/events/A')
        role = URIRef(str(process) + '/patient-role')
        self.assertIn((process, ei.S.hasParticipant, role), g)
        self.assertTrue(list(g.objects(role, ei.S.isFeatureOf)))
        self.assertTrue(r['axiom_supports'])
        self.assertFalse(r['clinical_mapping_verified'])
        self.assertFalse(r['accepted_view_full_owl_verified'])
        self.assertEqual(r['clinical_knowledge_status'], 'UNKNOWN')

    def test_all_pending_has_no_occurrence_view_and_no_backend_call(self):
        self.policy['decisions'] = []
        with patch.object(ss, 'execute', side_effect=AssertionError('backend must not run')):
            r = self.run_query()
        self.assertEqual(r['status'], 'EMPTY_ACCEPTED_VIEW')
        self.assertIsNone(r['accepted_graph_turtle'])
        self.assertIsNone(r['matching'])
        self.assertEqual({c['state'] for c in r['selection']['claims']}, {'PENDING'})
        self.assertEqual(r['claim_isolation']['process_extension_size'], 0)

    def test_rejected_claims_do_not_enter_view(self):
        for d in self.policy['decisions']: d['action'] = 'reject'
        self.assertEqual(self.run_query()['status'], 'EMPTY_ACCEPTED_VIEW')

    def test_withdrawal_removes_dependent_semantics_without_resurrecting_ancestor(self):
        # Explicit accepted ancestor, then classified replacement, then withdrawal.
        self.policy['decisions'][1]['supersedes'] = 'original_accept'
        self.decide('admin', name='original_accept')
        self.decide('admin_classified', 'withdraw', 'accept_admin_classified')
        r = self.run_query()
        self.assertEqual(r['status'], 'READY')
        self.assertEqual(r['selection']['accepted_claim_ids'], ['collection_original'])
        self.assertEqual(r['matching']['certain_patient_ids'], [])
        self.assertEqual(r['semantic_module']['class_assertions'], [])
        self.assertNotIn('/events/A>', r['accepted_graph_turtle'])

    def test_independent_support_survives_withdrawal(self):
        other = deepcopy(self.claim()); other.update(id='independent', source_id='second_source')
        self.store['claims'].append(other); self.decide('independent', name='accept_independent')
        self.decide('admin_classified', 'withdraw', 'accept_admin_classified')
        r = self.run_query()
        self.assertEqual(r['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(r['selection']['row_supports']['events:A'][0]['assertion_id'], 'independent')

    def test_identical_rows_retain_both_source_supports(self):
        other = deepcopy(self.claim()); other.update(id='independent', source_id='second_source')
        self.store['claims'].append(other); self.decide('independent')
        r = self.run_query()
        self.assertEqual(len(r['selection']['row_supports']['events:A']), 2)
        self.assertEqual(len(r['source']['events']), 2)

    def test_correction_replaces_temporal_and_semantic_claim_atomically(self):
        revised = deepcopy(self.claim()); revised['id'] = 'corrected'
        revised['bundle']['variables'][1].update(lower_us=4, upper_us=4)
        revised['bundle']['semantic_facts'][-1]['class_iri'] = 'https://example.org/trajectory/semantic/NonAntibiotic'
        self.store['claims'].append(revised)
        self.decide('corrected', parent='accept_admin_classified')
        r = self.run_query()
        self.assertEqual(r['status'], 'READY')
        self.assertEqual(r['matching']['certain_patient_ids'], [])
        self.assertEqual(next(v for v in r['source']['variables'] if v['id'] == 'Ae')['lower_us'], 4)
        self.assertNotIn('admin_classified', r['selection']['accepted_claim_ids'])

    def test_rejected_replacement_does_not_reactivate_ancestor(self):
        self.decide('admin', 'reject', 'accept_admin_classified')
        self.assertEqual(self.run_query()['selection']['accepted_claim_ids'], ['collection_original'])

    def test_accepted_conflict_blocks_entire_view(self):
        other = deepcopy(self.claim()); other.update(id='conflict', source_id='other')
        other['bundle']['variables'][1].update(lower_us=2, upper_us=2)
        self.store['claims'].append(other); self.decide('conflict')
        with patch.object(ss, 'execute', side_effect=AssertionError('backend must not run')):
            r = self.run_query()
        self.assertEqual(r['status'], 'BLOCKED_ACCEPTED_CONFLICT')
        self.assertIsNone(r['accepted_graph_turtle'])
        self.assertIsNone(r['matching'])
        self.assertEqual(r['selection']['blockers'][0]['row'], 'variables:Ae')

    def test_pending_conflicting_content_is_preserved_without_poisoning_view(self):
        self.claim('admin')['bundle']['variables'][1].update(lower_us=99, upper_us=99)
        self.bind()
        r = self.run_query()
        self.assertEqual(r['matching']['certain_patient_ids'], ['P1'])
        g = Graph().parse(data=r['claim_graph_turtle'], format='turtle')
        self.assertIn(99, [int(v) for v in g.objects(None, ei.S.hasValue) if v.datatype == XSD.integer])

    def test_orphan_semantic_support_blocks_when_target_claim_not_accepted(self):
        semantic = deepcopy(self.claim()); semantic.update(id='semantic_only', source_id='other')
        for k in ('events', 'variables', 'constraints'): semantic['bundle'][k] = []
        self.store['claims'].append(semantic)
        self.policy['decisions'][1]['action'] = 'reject'
        self.decide('semantic_only')
        r = self.run_query()
        self.assertEqual(r['status'], 'INVALID_ACCEPTED_VIEW')
        self.assertIsNone(r['accepted_graph_turtle'])
        self.assertIn('MISSING_SELECTED_SEMANTIC_EVENT', r['validation']['reason'])

    def test_joint_constraint_survives_projection(self):
        c = deepcopy(self.claim()); c.update(id='constraint_claim', source_id='constraint_source')
        c['bundle'] = {k: [] for k in cp.ROW_KINDS}
        c['bundle']['constraints'] = [{'id':'joint', 'left_var':'Ae','right_var':'Bs','upper_us':-2,'source_key':'joint_source'}]
        self.store['claims'].append(c); self.decide(c['id'])
        r = self.run_query()
        self.assertEqual(r['source']['constraints'], c['bundle']['constraints'])
        self.assertEqual(r['selection']['row_supports']['constraints:joint'][0]['assertion_id'], c['id'])

    def test_inconsistent_accepted_constraints_block_before_backend(self):
        self.claim()['bundle']['constraints'] = [{'id':'bad','left_var':'Bs','right_var':'Ae','upper_us':0,'source_key':'bad'}]
        self.bind()
        r = self.run_query()
        self.assertEqual(r['status'], 'INCONSISTENT_ACCEPTED_TIME')
        self.assertIsNone(r['accepted_graph_turtle'])

    def test_decision_fork_rejected(self):
        self.decide('admin_classified', 'withdraw', 'accept_admin_classified', 'w1')
        self.decide('admin_classified', 'withdraw', 'accept_admin_classified', 'w2')
        with self.assertRaisesRegex(ei.ContractError, 'DECISION_FORK'): self.run_query()

    def test_duplicate_roots_for_same_source_record_rejected(self):
        self.decide('admin')
        with self.assertRaisesRegex(ei.ContractError, 'MULTIPLE_DECISION_ROOTS'): self.run_query()

    def test_cross_source_revision_rejected(self):
        self.policy['decisions'][1]['supersedes'] = 'accept_collection_original'
        with self.assertRaisesRegex(ei.ContractError, 'CROSS_SOURCE_OR_SCOPE'): self.run_query()

    def test_decision_cycle_rejected(self):
        d = self.decide('admin', parent='accept_admin_classified')
        self.policy['decisions'][1]['supersedes'] = d['id']
        with self.assertRaisesRegex(ei.ContractError, 'DECISION_CYCLE'): self.run_query()

    def test_withdrawal_needs_matching_parent(self):
        self.policy['decisions'][1]['action'] = 'withdraw'
        with self.assertRaisesRegex(ei.ContractError, 'WITHDRAWAL_WITHOUT_PARENT'): self.run_query()

    def test_store_hash_prevents_stale_projection(self):
        self.store['snapshot_id'] = 'changed'
        with self.assertRaisesRegex(ei.ContractError, 'STALE_CLAIM_STORE'): self.run_query()

    def test_claim_hash_prevents_silent_reapproval(self):
        self.claim()['source_sha256'] = '0' * 64
        self.policy['store_sha256'] = cr.digest(self.store)
        with self.assertRaisesRegex(ei.ContractError, 'STALE_DECISION_CLAIM'): self.run_query()

    def test_semantic_policy_hash_prevents_unreviewed_rule_change(self):
        self.semantic['id'] = 'changed'
        with self.assertRaisesRegex(ei.ContractError, 'STALE_CLAIM_SEMANTIC_POLICY'): self.run_query()

    def test_unknown_fields_on_pending_claim_are_not_silently_dropped(self):
        self.claim('admin')['hasPatient'] = 'P1'
        self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_STORE_SCHEMA'): self.run_query()

    def test_numeric_bounds_reject_float_and_boolean(self):
        for value in (1.0, True):
            with self.subTest(value=value):
                self.claim()['bundle']['variables'][0]['lower_us'] = value
                self.bind()
                with self.assertRaisesRegex(ei.ContractError, 'CLAIM_JSON_VALUE'): self.run_query()

    def test_cross_patient_bundle_rejected(self):
        self.claim()['bundle']['events'][0]['patient_id'] = 'P2'; self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_SCOPE_MISMATCH'): self.run_query()

    def test_recovered_rdf_drives_same_matching(self):
        g = cr.encode(self.store)
        round_trip = Graph().parse(data=g.serialize(format='turtle'), format='turtle')
        r = cp.execute(round_trip, self.policy, self.semantic, self.query)
        direct = self.run_query()
        self.assertEqual(r['source'], direct['source'])
        self.assertEqual(r['matching']['certain_patient_ids'], direct['matching']['certain_patient_ids'])
        self.assertEqual(cr.decode(round_trip), self.store)

    def test_descriptions_have_no_occurrence_predicates_or_clinical_types(self):
        g = cr.encode(self.store)
        self.assertEqual(set(g.predicates()), {RDF.type, ei.S.hasDirectPart, ei.S.refersTo, ei.S.hasValue})
        self.assertTrue(all(str(c).startswith(str(cr.CP)) for c in g.objects(None, RDF.type)))
        # Mentioning the predicate as a string is content, not a property assertion.
        self.assertIn(Literal(str(ei.S.hasParticipant), datatype=XSD.string), set(g.objects(None, ei.S.hasValue)))
        self.assertFalse(list(g.triples((None, ei.S.hasParticipant, None))))

    def test_model_checks_pinned_sulo_with_empty_process_extension(self):
        report = isolation.check(cr.encode(self.store))
        self.assertEqual(report['status'], 'VERIFIED_EMPTY_PROCESS_MODEL')
        self.assertEqual(report['logical_axioms_checked'], 134)
        self.assertEqual(report['process_extension_size'], 0)
        self.assertFalse(report['general_owl_reasoner'])

    def test_unreviewed_sulo_pin_blocks_model_claim(self):
        with patch.object(isolation, 'PIN', '0' * 64):
            with self.assertRaisesRegex(ei.ContractError, 'UNREVIEWED_SULO_PIN'):
                isolation.check(cr.encode(self.store))

    def test_occurrence_edge_injection_rejected(self):
        g = cr.encode(self.store)
        root = next(g.subjects(RDF.type, cr.CP.Document))
        g.add((root, ei.S.hasParticipant, URIRef('urn:patient')))
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_GRAPH_EXTRA'): cr.decode(g)

    def test_added_ontology_axiom_rejected(self):
        g = cr.encode(self.store)
        g.add((cr.CP.ObjectDescription, OWL.equivalentClass, ei.S.Process))
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_GRAPH_EXTRA'): cr.decode(g)

    def test_multiple_values_rejected(self):
        g = cr.encode(self.store)
        node = next(g.subjects(RDF.type, cr.CP.StringDatum))
        g.add((node, ei.S.hasValue, Literal('another', datatype=XSD.string)))
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_FIELD_CARDINALITY'): cr.decode(g)

    def test_blank_node_injection_rejected(self):
        g = cr.encode(self.store); g.add((BNode(), RDF.type, cr.CP.Document))
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_GRAPH_NAMED_NODES'): cr.decode(g)

    def test_changed_content_requires_new_graph_identity_and_acceptance(self):
        g = cr.encode(self.store)
        triple = next(t for t in g if t[1] == ei.S.hasValue and t[2] == Literal('claims_1', datatype=XSD.string))
        g.remove(triple); g.add((triple[0], triple[1], Literal('claims_2', datatype=XSD.string)))
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_GRAPH_EXTRA_OR_CHANGED'): cr.decode(g)

    def test_backend_disagreement_suppresses_accepted_view(self):
        with patch.object(ss, 'run_backend', return_value={'status': 'BACKEND_TIMEOUT'}):
            r = self.run_query()
        self.assertEqual(r['status'], 'UNRESOLVED_SEMANTICS')
        self.assertIsNone(r['accepted_graph_turtle'])
        self.assertIsNone(r['matching'])
        self.assertEqual(r['claim_isolation']['process_extension_size'], 0)

    def test_invalid_query_is_rejected_even_for_empty_view(self):
        self.policy['decisions'] = []; self.query['slots'][0]['class_iri'] = 'urn:unknown'
        with self.assertRaises(ei.ContractError): self.run_query()

    def test_execution_does_not_mutate_supplied_inputs(self):
        before = deepcopy((self.store, self.policy, self.semantic, self.query))
        self.run_query()
        self.assertEqual(before, (self.store, self.policy, self.semantic, self.query))

    def test_policy_revision_changes_context_and_retains_reason(self):
        old = self.run_query(); self.policy['decisions'][0]['reason'] = 'New explicitly stated rationale.'
        new = self.run_query()
        self.assertNotEqual(old['context_id'], new['context_id'])
        self.assertIn('New explicitly stated rationale.', [d['reason'] for d in new['selection']['decisions']])

    def test_seeded_revision_chains_agree_with_last_decision_reference(self):
        rng = random.Random(17)
        for _ in range(50):
            store, policy, semantic, _ = fixture()
            policy['decisions'] = []
            accepted = []
            for claim in store['claims'][1:]:
                parent = None
                for revision in range(rng.randint(1, 8)):
                    action = rng.choice(['accept', 'reject'] if parent is None else ['accept', 'reject', 'withdraw'])
                    did = claim['id'] + '_' + str(revision)
                    policy['decisions'].append({'id':did,'claim_id':claim['id'],
                        'claim_sha256':cr.digest(claim),'action':action,'supersedes':parent,'reason':'Synthetic.'})
                    parent = did
                if action == 'accept': accepted.append(claim['id'])
            rng.shuffle(policy['decisions'])
            self.assertEqual(cp.select(store, policy, semantic)['accepted_claim_ids'], sorted(accepted))

    def test_unreviewed_claim_class_module_blocks_model_check(self):
        original = Graph.parse
        def changed(graph, source=None, *args, **kwargs):
            result = original(graph, source, *args, **kwargs)
            if str(source).endswith('claim-description-profile.ttl'):
                result.add((cr.CP.ObjectDescription, OWL.equivalentClass, ei.S.Process))
            return result
        with patch.object(Graph, 'parse', changed):
            with self.assertRaisesRegex(ei.ContractError, 'UNREVIEWED_CLAIM_CLASS_MODULE'):
                isolation.check(cr.encode(self.store))

    def test_model_supports_negative_integer_claim_values(self):
        self.claim()['bundle']['variables'][0].update(lower_us=-3, upper_us=-1)
        report = isolation.check(cr.encode(self.store))
        self.assertEqual(report['process_extension_size'], 0)

    def test_timeout_is_validated_even_for_empty_view(self):
        self.policy['decisions'] = []
        for value in (0, 61, True, float('nan')):
            with self.assertRaisesRegex(ei.ContractError, 'INVALID_BACKEND_TIMEOUT'):
                cp.execute(self.store, self.policy, self.semantic, self.query, timeout_seconds=value)

    def test_claim_store_limit_rejected_before_projection(self):
        self.store['claims'] = [deepcopy(self.claim()) for _ in range(33)]
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_STORE_SCHEMA'):
            cp.validate_store(self.store)

    def test_rdf_array_order_is_preserved_and_index_gaps_rejected(self):
        g = cr.encode(self.store)
        index = next(g.subjects(RDF.type, cr.CP.IndexDatum))
        g.set((index, ei.S.hasValue, Literal(1000, datatype=XSD.integer)))
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_INDEX_GAP'):
            cr.decode(g)

    def test_cli_rejects_malformed_turtle_and_input_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.ttl'; path.write_text('This is not Turtle {')
            out = Path(directory) / 'result.json'
            run = subprocess.run([sys.executable, '-m', 'patterns.claim_projection', '--graph', str(path),
                                  '--output', str(out)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2, run.stderr)
            self.assertFalse(out.exists())
            before = path.read_bytes()
            run = subprocess.run([sys.executable, '-m', 'patterns.claim_projection', '--graph', str(path),
                                  '--output', str(path)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(path.read_bytes(), before)

    def test_cli_atomically_replaces_result_and_preserves_on_invalid_input(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'result.json'
            args = [sys.executable, '-m', 'patterns.claim_projection', '--output', str(out)]
            run = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(run.returncode, 0, run.stderr)
            original = out.read_bytes()
            bad = Path(directory) / 'bad.json'; bad.write_text('{}')
            run = subprocess.run(args + ['--store', str(bad)], capture_output=True, text=True)
            self.assertEqual(run.returncode, 2)
            self.assertEqual(out.read_bytes(), original)
            self.assertEqual(set(Path(directory).iterdir()), {out, bad})


if __name__ == '__main__':
    unittest.main()
