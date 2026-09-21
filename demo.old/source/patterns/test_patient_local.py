"""Clock isolation, raw/coordinate fidelity and finite-world temporal checks."""
from copy import deepcopy
from datetime import datetime, timedelta
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from rdflib import Graph, Literal, OWL, RDF, URIRef, XSD
from rdflib.compare import isomorphic

from . import bounded_intervals as bt
from . import bounded_cohort as cohort
from . import bounded_rdf as br
from . import exact_intervals as ei
from . import mimic_inputevents as mimic
from . import patient_local as local
from . import semantic_support as semantic
from . import test_bounded_intervals as conformance
from .test_bounded_intervals import constraint, fixture
from .test_bounded_rdf import outcomes

EXAMPLE = ei.ROOT / 'examples/patient-local'


def label(origin, value):
    return (datetime.fromisoformat(origin) + timedelta(microseconds=value)).isoformat(timespec='microseconds')


def localize(source):
    source = deepcopy(source); source['profile'] = local.PROFILE_ID
    for clock in source['clocks']:
        clock.update(origin='2150-01-01T00:00:00', policy=local.POLICY,
                     origin_source_key='synthetic:fixed-calendar-reference')
    clocks = {c['clock_id']: c for c in source['clocks']}
    for variable in source['variables']:
        for side in ('lower', 'upper'):
            variable['local_' + side] = label(clocks[variable['clock_id']]['origin'], variable.pop(side + '_us'))
    return source


class PatientLocalTests(unittest.TestCase):
    def setUp(self):
        self.source = json.loads((EXAMPLE / 'source.json').read_text())
        self.query = json.loads((EXAMPLE / 'query.json').read_text())

    def test_coordinate_microseconds_negative_fraction_and_year_boundaries(self):
        for value, origin, expected in [
            ('2150-01-01 00:00:00.000001', '2150-01-01T00:00:00', 1),
            ('2149-12-31T23:59:59.999999', '2150-01-01T00:00:00', -1),
            ('2000-03-01T00:00:00', '2000-02-28T00:00:00', 172800000000),
            ('2100-03-01T00:00:00', '2100-02-28T00:00:00', 86400000000),
            ('2150-01-01T00:00:00.12', '2150-01-01T00:00:00.1', 20000)]:
            with self.subTest(value=value): self.assertEqual(local.coordinate(value, origin), expected)
        self.assertEqual(local.coordinate('0001-01-01T00:00:00', '9999-12-31T23:59:59.999999'), -315537897599999999)

    def test_timestamp_failures_do_not_infer_offsets_precision_or_dates(self):
        for value in ('', '2150-01-01', '2150-01-01T00:00:00Z', '2150-01-01T00:00:00+01:00',
                      '2150-01-01T00:00:00-00:00', '2150-02-29T00:00:00', '2150-01-01T00:00:60',
                      '2150-01-01T00:00:00.0000001', '2150-01-01T24:00:00', 123):
            with self.subTest(value=value), self.assertRaisesRegex(ei.ContractError, 'INVALID_LOCAL_DATETIME'):
                local.coordinate(value, '2150-01-01T00:00:00')
        with self.assertRaises(ei.ContractError): local.coordinate('2150-01-01T00:00:00', '2150-01-01 00:00:00')

    def test_local_day_arithmetic_does_not_apply_dst_or_assert_physical_elapsed_time(self):
        # These labels require no resolution against Europe's real-world DST transitions.
        self.assertEqual(local.coordinate('2026-03-29T03:30:00', '2026-03-29T01:30:00'), 7200000000)
        self.assertEqual(local.coordinate('2026-10-25T02:30:00', '2026-10-25T00:00:00'), 9000000000)
        answer = local.execute(local.prepare(self.source), self.query)
        self.assertFalse(answer['physical_elapsed_time_verified'])
        self.assertEqual(answer['context']['gap_semantics'], 'local_calendar_coordinate_difference')
        self.assertEqual(answer['context_id'], ei.digest(ei.canonical(answer['context'])))

    def test_four_outcomes_and_original_source_certificates(self):
        snapshot = local.prepare(self.source); answer = local.execute(snapshot, self.query)
        self.assertEqual(answer['certain_patient_ids'], ['P1'])
        self.assertEqual(answer['possible_patient_ids'], ['P1', 'P2'])
        self.assertEqual([t['status'] for t in answer['trajectories']],
                         ['CERTAIN_MATCH', 'POSSIBLE_MATCH', 'NO_RECORDED_MATCH', 'INCOMPARABLE'])
        conformance.BoundedTests().verify_result(snapshot, self.query, answer)

    def test_origin_rebase_preserves_relations_and_constraints(self):
        original = local.prepare(self.source)
        for clock in self.source['clocks']:
            clock['origin'] = label(clock['origin'], 1000000)
        rebased = local.prepare(self.source)
        self.assertEqual(outcomes(local.execute(original, self.query)), outcomes(local.execute(rebased, self.query)))
        for vid in original.variables:
            self.assertEqual(rebased.variables[vid]['lower_us'], original.variables[vid]['lower_us'] - 1000000)
        self.assertEqual(original.source['constraints'], rebased.source['constraints'])
        self.assertNotEqual(original.context_id, rebased.context_id)

    def test_whole_patient_date_shift_preserves_coordinates_and_answers(self):
        original = local.prepare(self.source)
        for clock in self.source['clocks']:
            clock['origin'] = (datetime.fromisoformat(clock['origin']) + timedelta(days=123)).isoformat()
        for variable in self.source['variables']:
            for side in ('lower', 'upper'):
                variable['local_' + side] = (datetime.fromisoformat(variable['local_' + side]) + timedelta(days=123)).isoformat(timespec='microseconds')
        shifted = local.prepare(self.source)
        for vid in original.variables:
            for side in ('lower_us', 'upper_us'):
                self.assertEqual(original.variables[vid][side], shifted.variables[vid][side])
        self.assertEqual(outcomes(local.execute(original, self.query)), outcomes(local.execute(shifted, self.query)))
        self.assertNotEqual(original.context_id, shifted.context_id)

    def test_patient_scope_global_clocks_and_mixed_policies_rejected(self):
        for key, value in [('scope', 'global'), ('policy', ei.POLICY), ('origin', '2150-01-01T00:00:00Z')]:
            source = deepcopy(self.source); source['clocks'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ei.ContractError): local.prepare(source)
        self.source['clocks'][0]['scope'] = 'patient:P2'
        with self.assertRaisesRegex(ei.ContractError, 'CLOCK_PATIENT_SCOPE_MISMATCH'): local.prepare(self.source)

    def test_cross_patient_or_cross_episode_source_edges_rejected(self):
        self.source['constraints'].append(constraint('bad', 'P1as', 'P2as', 0))
        with self.assertRaisesRegex(ei.ContractError, 'CROSS_SCOPE_SOURCE_CONSTRAINT'): local.prepare(self.source)
        self.source['constraints'].pop()
        self.source['variables'][0]['episode_id'] = 'E2'
        with self.assertRaisesRegex(ei.ContractError, 'CROSS_SCOPE_SOURCE_CONSTRAINT|EVENT_VARIABLE_SCOPE_MISMATCH'): local.prepare(self.source)

    def test_equal_labels_on_distinct_clocks_remain_incomparable(self):
        answer = local.execute(local.prepare(self.source), self.query)
        p4 = next(t for t in answer['trajectories'] if t['patient_id'] == 'P4')
        self.assertEqual(p4['status'], 'INCOMPARABLE')
        for c in self.source['clocks'][-2:]: self.assertEqual(c['origin'], '2180-01-01T00:00:00')
        self.source['constraints'].append(constraint('bad', 'P4as', 'P4bs', 0))
        with self.assertRaisesRegex(ei.ContractError, 'CROSS_SCOPE_SOURCE_CONSTRAINT'): local.prepare(self.source)

    def test_missing_bounds_origin_key_and_unknown_clock_rejected(self):
        for target, key in [('variables', 'local_lower'), ('clocks', 'origin_source_key')]:
            source = deepcopy(self.source); del source[target][0][key]
            with self.subTest(key=key), self.assertRaises(ei.ContractError): local.prepare(source)
        self.source['variables'][0]['clock_id'] = 'unknown'
        with self.assertRaisesRegex(ei.ContractError, 'UNKNOWN_CLOCK'): local.prepare(self.source)

    def test_reversed_bounds_and_inconsistent_intervals_are_distinct(self):
        self.source['variables'][0]['local_lower'] = '2150-01-02T00:00:00'
        with self.assertRaisesRegex(ei.ContractError, 'REVERSED_VARIABLE_BOUNDS'): local.prepare(self.source)
        source = localize(fixture([('a', 'infusion', (2, 2), (1, 1))]))
        with self.assertRaises(bt.InconsistentSource) as caught: local.prepare(source)
        self.assertTrue(caught.exception.details['negative_cycle'])

    def test_shared_anchor_correlation_survives_conversion(self):
        self.query['constraints'][0].update(operator='gap', min_gap_us=2, max_gap_us=2)
        snapshot = local.prepare(self.source)
        answer = local.execute(snapshot, self.query)
        self.assertEqual(answer['trajectories'][0]['bindings'][0]['status'], 'CERTAIN')
        self.source['constraints'] = []
        uncorrelated = local.execute(local.prepare(self.source), self.query)
        self.assertEqual(uncorrelated['trajectories'][0]['bindings'][0]['status'], 'POSSIBLE')
        conformance.BoundedTests().verify_result(snapshot, self.query, answer)

    def test_joint_possibility_not_atomwise_possibility(self):
        source = localize(fixture([('a', 'infusion', (0, 0), (1, 1)), ('b', 'specimen_collection', (1, 2), (3, 3))]))
        snapshot = local.prepare(source)
        self.query['constraints'][0]['operator'] = 'meets'
        self.assertEqual(local.execute(snapshot, self.query)['trajectories'][0]['bindings'][0]['status'], 'POSSIBLE')
        self.query['constraints'].append({'id': 'strict', 'left': 'a', 'right': 'b', 'operator': 'before'})
        answer = local.execute(snapshot, self.query)
        self.assertEqual(answer['trajectories'][0]['bindings'][0]['status'], 'IMPOSSIBLE')
        conformance.BoundedTests().verify_result(snapshot, self.query, answer)

    def test_fixed_named_witness_not_a_different_binding_in_each_world(self):
        source = localize(fixture([('a1', 'infusion', (0, 0), (1, 1)), ('a2', 'infusion', (1, 1), (2, 2)),
                                  ('b', 'specimen_collection', (1, 2), (2, 3))],
                                 [constraint('up', 'be', 'bs', 1), constraint('down', 'bs', 'be', -1)]))
        self.query['constraints'][0]['operator'] = 'meets'
        snapshot = local.prepare(source); answer = local.execute(snapshot, self.query)
        self.assertEqual(answer['certain_patient_ids'], [])
        self.assertEqual(answer['possible_patient_ids'], ['P'])
        conformance.BoundedTests().verify_result(snapshot, self.query, answer)

    def test_seeded_small_domains_agree_with_independent_finite_worlds(self):
        randomizer = random.Random(913)
        for iteration in range(24):
            rows = []
            for name, kind in [('a', 'infusion'), ('b', 'specimen_collection')]:
                lo = randomizer.randint(-2, 2)
                rows.append((name, kind, (lo, lo + 1), (lo + 2, lo + 3)))
            snapshot = local.prepare(localize(fixture(rows)))
            for operator in ('before', 'meets', 'overlaps', 'gap'):
                query = deepcopy(self.query)
                query['constraints'][0]['operator'] = operator
                if operator == 'gap': query['constraints'][0].update(min_gap_us=0, max_gap_us=3)
                with self.subTest(iteration=iteration, operator=operator):
                    conformance.BoundedTests().verify_result(snapshot, query, local.execute(snapshot, query))

    def test_raw_spelling_and_closed_rdf_roundtrip(self):
        self.source['variables'][0]['local_lower'] = '2150-01-01 00:00:00'
        graph = local.export_source(self.source)
        first = local.prepare_graph(graph)
        second = local.prepare_graph(Graph().parse(data=ei.turtle_text(graph), format='turtle'))
        self.assertEqual(local.execute(first, self.query), local.execute(second, self.query))
        self.assertTrue(isomorphic(graph, second.graph))
        self.assertEqual(first.variables['P1as']['local_lower'], '2150-01-01 00:00:00')
        self.assertEqual(outcomes(local.execute(local.prepare(self.source), self.query)), outcomes(local.execute(first, self.query)))

    def test_rdf_uses_naive_datetime_origin_and_only_sulo_literal_property(self):
        graph = local.export_source(self.source)
        for _, predicate, value in graph:
            if isinstance(value, Literal): self.assertEqual(predicate, ei.S.hasValue)
        origin = next(graph.objects(bt.D['clocks/P1_common/origin'], ei.S.hasValue))
        self.assertEqual(origin.datatype, XSD.dateTime)
        self.assertEqual(str(origin), self.source['clocks'][0]['origin'])
        ontology = Graph().parse(local.ONTOLOGY)
        self.assertEqual(list(ontology.subjects(RDF.type, OWL.ObjectProperty)), [])
        self.assertEqual(list(ontology.subjects(RDF.type, OWL.DatatypeProperty)), [])

    def test_rdf_numeric_or_lexical_tampering_rejected(self):
        for node, literal in [
            (bt.D['variables/P1as/lower'], Literal(-1, datatype=XSD.integer)),
            (bt.D['variables/P1as/local-lower'], Literal('2150-01-02T00:00:00', datatype=XSD.string)),
            (bt.D['clocks/P1_common/origin'], Literal('2150-01-02T00:00:00', datatype=XSD.dateTime))]:
            graph = local.export_source(self.source); graph.set((node, ei.S.hasValue, literal))
            with self.subTest(node=node), self.assertRaisesRegex(ei.ContractError, 'LOCAL_COORDINATE_MISMATCH'):
                local.prepare_graph(graph)

    def test_rdf_timezone_datatype_policy_and_profile_mixing_rejected(self):
        for node, literal in [
            (bt.D['clocks/P1_common/origin'], Literal('2150-01-01T00:00:00Z', datatype=XSD.dateTime)),
            (bt.D['clocks/P1_common/origin'], Literal('2150-01-01T00:00:00', datatype=XSD.dateTimeStamp)),
            (bt.D['clocks/P1_common/policy'], Literal(ei.POLICY, datatype=XSD.string)),
            (bt.D['snapshot/rdf-identifier'], Literal(br.PROFILE_ID, datatype=XSD.string))]:
            graph = local.export_source(self.source); graph.set((node, ei.S.hasValue, literal))
            with self.subTest(node=node), self.assertRaises(ei.ContractError): local.prepare_graph(graph)

    def test_rdf_requires_raw_bounds_and_origin_evidence(self):
        for path in ('variables/P1as/local-lower', 'clocks/P1_common/origin-source'):
            graph = local.export_source(self.source); graph.remove((bt.D[path], ei.S.hasValue, None))
            with self.subTest(path=path), self.assertRaises(ei.ContractError): local.prepare_graph(graph)
        graph = local.export_source(self.source)
        graph.add((bt.D.snapshot, URIRef('urn:unsupported'), bt.D.snapshot))
        with self.assertRaisesRegex(ei.ContractError, 'UNCONSUMED_RDF_TRIPLES'): local.prepare_graph(graph)

    def test_arbitrary_rdf_instance_names_keep_pro_role_witnesses(self):
        graph = local.export_source(self.source)
        replacements = {n: URIRef('urn:renamed:' + str(i)) for i, n in enumerate(sorted({s for s in graph.subjects() if str(s).startswith(str(bt.D))}))}
        renamed = Graph()
        for s, p, o in graph: renamed.add((replacements.get(s, s), p, replacements.get(o, o)))
        snapshot = local.prepare_graph(renamed)
        self.assertEqual(local.execute(snapshot, self.query)['certain_patient_ids'], ['P1'])
        for evidence in snapshot.evidence.values():
            self.assertIn((URIRef(evidence['process']), ei.S.hasParticipant, URIRef(evidence['patient_role'])), renamed)
            self.assertIn((URIRef(evidence['patient_role']), ei.S.isFeatureOf, URIRef(evidence['patient_bearer'])), renamed)
        graph = local.export_source(self.source)
        graph.set((bt.D['events/P1a'], ei.S.hasParticipant, bt.D['persons/P1']))
        with self.assertRaises(ei.ContractError): local.prepare_graph(graph)

    def test_rdf_inconsistency_keeps_negative_cycle_and_source_evidence(self):
        graph = local.export_source(self.source)
        graph.set((bt.D['constraints/ae-upper/upper'], ei.S.hasValue, Literal(-1, datatype=XSD.integer)))
        with self.assertRaises(bt.InconsistentSource) as caught: local.prepare_graph(graph)
        details = caught.exception.details
        self.assertEqual(details['rdf_context']['profile'], local.RDF_PROFILE)
        self.assertTrue(details['negative_cycle'])
        self.assertTrue(all('rdf' in item for item in details['edge_evidence'].values()))

    def test_original_profiles_do_not_accept_local_data_or_queries(self):
        snapshot = local.prepare(self.source)
        with self.assertRaises(ei.ContractError): bt.prepare(self.source)
        with self.assertRaises(ei.ContractError): br.prepare_graph(local.export_source(self.source))
        query = deepcopy(self.query); query['profile'] = bt.QUERY_PROFILE
        with self.assertRaises(ei.ContractError): local.execute(snapshot, query)
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_SNAPSHOT_PROFILE'): cohort.execute(snapshot, query)
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_SNAPSHOT_PROFILE'): semantic.execute(snapshot, {}, {})
        old = bt.prepare(fixture([('a', 'infusion', (0, 0), (1, 1))]))
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_SNAPSHOT_PROFILE'): local.execute(old, self.query)

    def test_invalid_query_does_not_become_an_empty_result(self):
        for change in ('float', 'reversed', 'unknown', 'extra'):
            query = deepcopy(self.query)
            query['constraints'][0].update(operator='gap', min_gap_us=0, max_gap_us=1)
            if change == 'float': query['constraints'][0]['max_gap_us'] = 1.0
            if change == 'reversed': query['constraints'][0]['min_gap_us'] = 2
            if change == 'unknown': query['constraints'][0]['right'] = 'unknown'
            if change == 'extra': query['timezone'] = 'UTC'
            with self.subTest(change=change), self.assertRaises(ei.ContractError): local.validate_query(query)

    def test_snapshot_owns_input_and_provenance_changes_context(self):
        snapshot = local.prepare(self.source)
        original = deepcopy(snapshot.source)
        self.source['clocks'][0]['origin_source_key'] = 'different-source'
        other = local.prepare(self.source)
        self.assertEqual(snapshot.source, original)
        self.assertNotEqual(snapshot.context_id, other.context_id)
        self.assertEqual(outcomes(local.execute(snapshot, self.query)), outcomes(local.execute(other, self.query)))

    def test_staged_mimic_label_can_be_located_without_promoting_clinical_precision(self):
        folder = ei.ROOT / 'examples/mimic-inputevents'
        staged = mimic.stage(*(folder / (table + '.csv') for table in mimic.HEADERS), dataset_id='synthetic')
        original = deepcopy(staged)
        segment = staged['segments'][0]
        self.assertEqual(local.coordinate(segment['start']['raw_value'], '2150-01-01T00:00:00'), 36000000000)
        self.assertEqual(staged, original)
        self.assertFalse(staged['summary']['matcher_ready'])
        self.assertIsNone(segment['source_available_at'])
        self.assertEqual(segment['start']['occurrence_precision'], 'unverified')

    def test_cli_roundtrip_and_failure_preserve_prior_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            command = [sys.executable, '-m', 'patterns.patient_local', '--output', str(output)]
            subprocess.run(command, check=True, capture_output=True)
            first = (output / 'result.json').read_bytes()
            subprocess.run(command + ['--graph', str(output / 'graph.ttl')], check=True, capture_output=True)
            self.assertEqual(first, (output / 'result.json').read_bytes())
            bad = Path(temp) / 'bad.json'; bad.write_text('{}')
            result = subprocess.run(command + ['--source', str(bad)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertIn('INVALID_INPUT', result.stderr)
            self.assertEqual(first, (output / 'result.json').read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
