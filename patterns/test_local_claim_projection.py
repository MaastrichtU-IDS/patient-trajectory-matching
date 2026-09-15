"""Local-clock claim projection, profile boundaries and dependent evidence."""
from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, RDF, OWL, XSD
from . import claim_projection as cp
from . import claim_rdf as cr
from . import claim_isolation as isolation
from . import exact_intervals as ei
from . import local_claim_projection as lc
from . import patient_local as local
from . import semantic_support as ss


def fixture(prefix=''):
    folder = ei.ROOT / 'examples/local-claim-projection'
    return [json.loads((folder / (prefix + name + '.json')).read_text())
            for name in ('store', 'policy', 'semantic-policy', 'query')]


class LocalClaimTests(unittest.TestCase):
    def setUp(self):
        self.store, self.policy, self.semantic, self.query = fixture()

    def bind(self):
        self.policy['store_sha256'] = cr.digest(self.store)
        self.policy['semantic_policy_sha256'] = cr.digest(self.semantic)
        claims = {c['id']: c for c in self.store['claims']}
        for d in self.policy['decisions']: d['claim_sha256'] = cr.digest(claims[d['claim_id']])

    def run_query(self):
        return lc.execute(self.store, self.policy, self.semantic, self.query)

    def claim(self, cid='admin_classified'):
        return next(c for c in self.store['claims'] if c['id'] == cid)

    def withdraw(self, cid):
        self.policy['decisions'].append({'id': 'withdraw_' + cid, 'claim_id': cid,
            'claim_sha256': cr.digest(self.claim(cid)), 'action': 'withdraw',
            'supersedes': 'accept_' + cid, 'reason': 'Synthetic withdrawal.'})

    def test_local_assertions_match_and_preserve_raw_and_normalized_sources(self):
        r = self.run_query()
        self.assertEqual(r['status'], 'READY')
        self.assertEqual(r['profile'], lc.PROFILE)
        self.assertEqual(r['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(r['normalized_source'], local.normalize(r['source']))
        self.assertNotIn('lower_us', r['source']['variables'][0])
        self.assertEqual(r['semantic_module']['source_sha256'], ei.digest(ei.canonical(r['normalized_source'])))
        graph = Graph().parse(data=r['accepted_graph_turtle'], format='turtle')
        lexical_nodes = list(graph.subjects(RDF.type, local.PL.LocalLowerLexical))
        self.assertEqual(len(lexical_nodes), 4)
        self.assertEqual(len(list(graph.subjects(RDF.type, ei.EX.PatientRole))), 2)

    def test_time_interpretation_is_bound_at_every_execution_layer(self):
        r = self.run_query()
        for scope in (r, r['context'], r['semantic_run'], r['semantic_run']['context'], r['matching'], r['matching']['context']):
            self.assertEqual(scope['time_domain'], local.TIME_DOMAIN)
            self.assertEqual(scope['gap_semantics'], 'local_calendar_coordinate_difference')
            self.assertFalse(scope['physical_elapsed_time_verified'])
        self.assertFalse(r['clinical_mapping_verified'])
        self.assertFalse(r['source_history_verified'])

    def test_recorded_profile_does_not_automatically_assert_infusion(self):
        r = lc.execute(*fixture('record-'))
        self.assertEqual(r['status'], 'READY')
        self.assertEqual(r['profile'], lc.RECORD_PROFILE)
        self.assertEqual(r['source']['profile'], local.RECORD_PROFILE)
        self.assertEqual(r['time_semantics'], 'recorded_source_intervals')
        self.assertEqual(r['matching']['certain_patient_ids'], ['P1'])
        g = Graph().parse(data=r['accepted_graph_turtle'], format='turtle')
        self.assertEqual(len(list(g.subjects(RDF.type, cp.bt.BT.RecordedInputSegment))), 2)
        self.assertFalse(list(g.subjects(RDF.type, cp.bt.BT.Infusion)))
        self.assertFalse(list(g.subjects(RDF.type, ei.EX.DrugAdministration)))

    def test_old_offset_entrypoint_rejects_both_local_store_profiles(self):
        for prefix in ('', 'record-'):
            with self.subTest(prefix=prefix), self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_STORE_SCHEMA'):
                cp.execute(*fixture(prefix))

    def test_local_entrypoint_rejects_offset_store(self):
        folder = ei.ROOT / 'examples/claim-projection'
        offset = json.loads((folder / 'store.json').read_text())
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_LOCAL_CLAIM_STORE_PROFILE'):
            lc.execute(offset, self.policy, self.semantic, self.query)

    def test_recorded_and_occurrence_kinds_cannot_be_mixed(self):
        self.claim()['bundle']['events'][0].update(event_kind='recorded_input_segment', status='recorded')
        self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_STORE_SCHEMA'): self.run_query()
        self.store, self.policy, self.semantic, self.query = fixture('record-')
        self.claim()['bundle']['events'][0].update(event_kind='infusion', status='performed')
        self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_STORE_SCHEMA'): self.run_query()

    def test_local_requires_local_semantic_query_profile(self):
        self.query['profile'] = ss.QUERY_PROFILE
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_SEMANTIC_QUERY_PROFILE'): self.run_query()

    def test_offset_datetime_and_global_clock_are_rejected(self):
        for key, value in (('origin', '2150-01-01T10:00:00Z'), ('scope', 'global')):
            self.setUp(); self.store['clocks'][0][key] = value; self.bind()
            with self.subTest(key=key), self.assertRaises(ei.ContractError): self.run_query()

    def test_origin_provenance_is_required(self):
        del self.store['clocks'][0]['origin_source_key']; self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_STORE_SCHEMA'): self.run_query()

    def test_precomputed_numeric_bound_in_local_claim_is_rejected(self):
        self.claim()['bundle']['variables'][0]['lower_us'] = 0; self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_STORE_SCHEMA'): self.run_query()

    def test_invalid_local_label_is_rejected_even_when_claim_pending(self):
        for value in ('2150-02-30T00:00:00', '2150-01-01', '2150-01-01T10:00:00+01:00'):
            self.claim('admin')['bundle']['variables'][0]['local_lower'] = value; self.bind()
            with self.subTest(value=value), self.assertRaisesRegex(ei.ContractError, 'INVALID_LOCAL_DATETIME'):
                self.run_query()

    def test_reversed_local_bounds_are_rejected(self):
        self.claim()['bundle']['variables'][0]['local_lower'] = '2150-01-01T11:00:00'; self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'REVERSED_LOCAL_CLAIM_BOUNDS'): self.run_query()

    def test_zero_duration_does_not_become_proper_interval(self):
        end = self.claim()['bundle']['variables'][1]
        end['local_lower'] = end['local_upper'] = '2150-01-01T10:00:00'; self.bind()
        r = self.run_query()
        self.assertEqual(r['status'], 'INCONSISTENT_ACCEPTED_TIME')
        self.assertIsNone(r['accepted_graph_turtle'])

    def test_clock_rebasing_preserves_answer_and_changes_coordinates(self):
        original = self.run_query()
        self.store['clocks'][0]['origin'] = '2150-01-01T11:00:00'; self.bind()
        rebased = self.run_query()
        self.assertEqual(rebased['matching']['certain_patient_ids'], original['matching']['certain_patient_ids'])
        self.assertEqual(next(v for v in rebased['normalized_source']['variables'] if v['id'] == 'As')['lower_us'], -3600000000)
        self.assertNotEqual(rebased['context_id'], original['context_id'])

    def test_uniform_calendar_shift_preserves_coordinates_and_answer(self):
        original = self.run_query()
        for c in self.store['clocks']:
            c['origin'] = (local.parse_local(c['origin']) + timedelta(days=100)).isoformat()
        for claim in self.store['claims']:
            for v in claim['bundle']['variables']:
                for key in ('local_lower', 'local_upper'):
                    v[key] = (local.parse_local(v[key]) + timedelta(days=100)).isoformat(timespec='microseconds')
        self.bind(); shifted = self.run_query()
        self.assertEqual(shifted['matching']['certain_patient_ids'], original['matching']['certain_patient_ids'])
        self.assertEqual([v['lower_us'] for v in shifted['normalized_source']['variables']],
                         [v['lower_us'] for v in original['normalized_source']['variables']])

    def test_changed_origin_invalidates_existing_acceptance_policy(self):
        self.store['clocks'][0]['origin'] = '2150-01-01T11:00:00'
        with self.assertRaisesRegex(ei.ContractError, 'STALE_CLAIM_STORE'): self.run_query()

    def test_identical_labels_on_distinct_clocks_remain_incomparable(self):
        other = {**self.store['clocks'][0], 'clock_id': 'other'}
        self.store['clocks'].append(other)
        for v in self.claim('collection_original')['bundle']['variables']: v['clock_id'] = 'other'
        self.bind(); r = self.run_query()
        self.assertEqual(r['matching']['certain_patient_ids'], [])
        self.assertEqual([(t['patient_id'], t['status']) for t in r['matching']['trajectories']], [('P1', 'INCOMPARABLE')])

    def test_clock_cannot_be_borrowed_from_another_patient(self):
        self.store['clocks'][0]['scope'] = 'patient:P2'; self.bind()
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_CLAIM_CLOCK_SCOPE'): self.run_query()

    def test_slots_cannot_join_different_patients(self):
        c = self.claim('collection_original'); c['patient_id'] = 'P2'
        self.store['clocks'].append({**self.store['clocks'][0], 'clock_id':'P2_clock','scope':'patient:P2'})
        for kind in ('variables', 'events'):
            for row in c['bundle'][kind]:
                row['patient_id'] = 'P2'
                if kind == 'variables': row['clock_id'] = 'P2_clock'
        self.bind(); r = self.run_query()
        self.assertEqual(r['status'], 'READY')
        self.assertEqual(r['matching']['certain_patient_ids'], [])
        self.assertEqual(r['matching']['possible_patient_ids'], [])

    def test_correlated_source_constraint_changes_possible_to_certain(self):
        start = self.claim('collection_original')['bundle']['variables'][0]
        start['local_lower'] = '2150-01-01T10:00:00'; self.bind()
        possible = self.run_query()
        self.assertEqual(possible['matching']['possible_patient_ids'], ['P1'])
        self.assertEqual(possible['matching']['certain_patient_ids'], [])
        constraint = {'id':'bound_gap','left_var':'Ae','right_var':'Bs','upper_us':-2,'source_key':'joint_evidence'}
        self.claim()['bundle']['constraints'] = [constraint]; self.bind()
        certain = self.run_query()
        self.assertEqual(certain['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(certain['source']['constraints'], [constraint])
        self.assertEqual(certain['normalized_source']['constraints'], [constraint])

    def test_withdrawn_dependency_is_not_silently_removed_from_constraints(self):
        self.claim()['bundle']['constraints'] = [{'id':'bound_gap','left_var':'Ae','right_var':'Bs','upper_us':-2,'source_key':'joint_evidence'}]
        self.bind(); self.withdraw('collection_original')
        r = self.run_query()
        self.assertEqual(r['status'], 'INVALID_ACCEPTED_VIEW')
        self.assertIn('UNKNOWN_VARIABLE', r['validation']['reason'])
        self.assertIsNone(r['normalized_source'])

    def test_withdrawal_removes_local_process_and_semantic_support(self):
        self.withdraw('admin_classified'); r = self.run_query()
        self.assertEqual(r['status'], 'READY')
        self.assertEqual(r['matching']['certain_patient_ids'], [])
        self.assertEqual({e['id'] for e in r['source']['events']}, {'B'})
        self.assertEqual(r['semantic_module']['class_assertions'], [])

    def test_independent_local_claim_support_survives_withdrawal(self):
        other = deepcopy(self.claim()); other.update(id='independent', source_id='other')
        self.store['claims'].append(other)
        self.policy['decisions'].append({'id':'independent_decision','claim_id':other['id'],
            'claim_sha256':cr.digest(other),'action':'accept','supersedes':None,'reason':'Synthetic independent support.'})
        self.withdraw('admin_classified'); self.bind(); r = self.run_query()
        self.assertEqual(r['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(r['selection']['row_supports']['events:A'][0]['assertion_id'], 'independent')

    def test_local_correction_replaces_raw_bounds_and_result(self):
        c = deepcopy(self.claim()); c['id'] = 'corrected'
        end = c['bundle']['variables'][1]
        end['local_lower'] = end['local_upper'] = '2150-01-01T10:00:00.000004'
        self.store['claims'].append(c)
        self.policy['decisions'].append({'id':'corrected_decision','claim_id':c['id'],
            'claim_sha256':cr.digest(c),'action':'accept','supersedes':'accept_admin_classified','reason':'Synthetic correction.'})
        self.bind(); r = self.run_query()
        self.assertEqual(r['matching']['certain_patient_ids'], [])
        self.assertEqual(next(v for v in r['normalized_source']['variables'] if v['id']=='Ae')['upper_us'], 4)

    def test_all_pending_still_has_local_interpretation_and_no_assertions(self):
        self.policy['decisions'] = []
        with patch.object(local, 'execute_semantic', side_effect=AssertionError('must not reason')):
            r = self.run_query()
        self.assertEqual(r['status'], 'EMPTY_ACCEPTED_VIEW')
        self.assertFalse(r['physical_elapsed_time_verified'])
        self.assertIsNone(r['accepted_graph_turtle'])
        self.assertIsNone(r['normalized_source'])

    def test_closed_local_rdf_round_trip_preserves_matching(self):
        direct = self.run_query()
        graph = Graph().parse(data=direct['claim_graph_turtle'], format='turtle')
        recovered = lc.execute(graph, self.policy, self.semantic, self.query)
        self.assertEqual(recovered['source'], direct['source'])
        self.assertEqual(recovered['normalized_source'], direct['normalized_source'])
        self.assertEqual(recovered['matching']['certain_patient_ids'], direct['matching']['certain_patient_ids'])
        self.assertEqual(cr.decode(graph, fields=cr.LOCAL_FIELDS), self.store)

    def test_original_claim_rdf_reader_rejects_local_field_extension(self):
        graph = cr.encode(self.store, fields=cr.LOCAL_FIELDS)
        with self.assertRaisesRegex(ei.ContractError, 'CLAIM_FIELD_TYPE_OR_DUPLICATE'): cr.decode(graph)

    def test_local_isolation_checks_extension_and_emits_no_temporal_entities(self):
        graph = cr.encode(self.store, fields=cr.LOCAL_FIELDS)
        report = isolation.check(graph, local=True)
        self.assertEqual(report['logical_axioms_checked'], 137)
        self.assertEqual(report['process_extension_size'], 0)
        self.assertEqual(report['temporal_extension_size'], 0)
        self.assertIn('ontology/local-claim-description-profile.ttl', report['extension_sha256'])
        self.assertFalse(list(graph.triples((None, ei.S.atTime, None))))
        self.assertEqual(set(v.datatype for v in graph.objects(None, ei.S.hasValue)), {XSD.string, XSD.integer})

    def test_local_class_extension_cannot_add_an_occurrence_axiom(self):
        original = Graph.parse
        def changed(graph, source=None, *args, **kwargs):
            result = original(graph, source, *args, **kwargs)
            if str(source).endswith('local-claim-description-profile.ttl'):
                result.add((cr.CP.Field_local_lower, OWL.equivalentClass, ei.S.TimeInstant))
            return result
        graph = cr.encode(self.store, fields=cr.LOCAL_FIELDS)
        with patch.object(Graph, 'parse', changed):
            with self.assertRaisesRegex(ei.ContractError, 'UNREVIEWED_LOCAL_CLAIM_CLASS_MODULE'):
                isolation.check(graph, local=True)

    def test_rdf_injected_time_assertion_is_rejected(self):
        graph = cr.encode(self.store, fields=cr.LOCAL_FIELDS)
        root = next(graph.subjects(RDF.type, cr.CP.Document))
        graph.add((root, RDF.type, ei.S.TimeInstant))
        with self.assertRaises(ei.ContractError): lc.execute(graph, self.policy, self.semantic, self.query)

    def test_backend_failure_never_releases_normalized_or_accepted_view(self):
        with patch.object(ss, 'run_backend', return_value={'status':'BACKEND_TIMEOUT'}): r = self.run_query()
        self.assertEqual(r['status'], 'UNRESOLVED_SEMANTICS')
        self.assertIsNone(r['accepted_graph_turtle'])
        self.assertIsNone(r['normalized_source'])
        self.assertIsNone(r['matching'])
        self.assertFalse(r['physical_elapsed_time_verified'])

    def test_local_cli_and_rdf_route(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / 'result.json'
            args = [sys.executable, '-m', 'patterns.local_claim_projection', '--output', str(out)]
            first = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            result = json.loads(out.read_text())
            graph = Path(directory) / 'claims.ttl'; graph.write_text(result['claim_graph_turtle'])
            second = subprocess.run(args + ['--graph', str(graph)], capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(json.loads(out.read_text())['source'], result['source'])


if __name__ == '__main__':
    unittest.main()
