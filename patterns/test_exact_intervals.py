"""Conformance tests for exact-interval-1.0; run independently of the v2.4 suite."""
from copy import deepcopy
from dataclasses import replace
from itertools import product
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdflib import BNode, Graph, Literal, OWL, RDF, URIRef, XSD
from rdflib.compare import isomorphic

from . import exact_intervals as ei
from . import pro_solid as base

ROOT, S, EX, EI, D = ei.ROOT, ei.S, ei.EX, ei.EI, ei.DATA
EXAMPLE = ROOT / 'examples/exact-interval'


class IntervalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source_template = json.loads((EXAMPLE / 'source-rows.json').read_text())
        cls.manifest = json.loads((EXAMPLE / 'manifest.json').read_text())
        cls.queries = json.loads((EXAMPLE / 'queries.json').read_text())
        cls.good_graph = ei.build_graph(cls.source_template)
        cls.records, cls.evidence = ei.project(cls.good_graph, cls.manifest)
        cls.by_id = {r.event_id: r for r in cls.records}

    def source(self):
        return deepcopy(self.source_template)

    def graph(self):
        g = Graph()
        for triple in self.good_graph:
            g.add(triple)
        return g

    def project(self, graph):
        return ei.project(graph, self.manifest)

    def bad_graph(self, graph, code):
        with self.assertRaisesRegex(ei.ContractError, code):
            self.project(graph)

    def bad_source(self, source, code):
        with self.assertRaisesRegex(ei.ContractError, code):
            self.project(ei.build_graph(source))

    def test_end_to_end_expected_comparisons(self):
        results = ei.run_queries(self.records, self.queries)
        expected = [('contact', 'SATISFIED', 'meets', 0),
                    ('contact_is_not_before', 'NOT_SATISFIED', 'meets', 0),
                    ('before', 'SATISFIED', 'before', 900000000),
                    ('overlap', 'SATISFIED', 'overlaps', -600000000),
                    ('reverse_overlap', 'NOT_SATISFIED', 'overlapped_by', -2400000000),
                    ('ten_minute_gap', 'SATISFIED', 'before', 600000000),
                    ('zero_gap', 'SATISFIED', 'meets', 0),
                    ('gap_too_large', 'NOT_SATISFIED', 'before', 900000000),
                    ('overlap_has_no_nonnegative_gap', 'NOT_SATISFIED', 'overlaps', -600000000)]
        self.assertEqual([(r['id'], r['status'], r['relation'], r['signed_gap_us']) for r in results], expected)
        for result in results:
            for ref in result['evidence_ids']:
                self.assertIn(ref, self.evidence['bindings'])

    def test_offset_equivalence_and_contextual_boundary_identity(self):
        a, b = self.by_id['A'], self.by_id['B']
        self.assertEqual(a.end_us, b.start_us)
        ae, be = (self.evidence['bindings'][r.evidence_id] for r in (a, b))
        self.assertNotEqual(ae['end']['datum'], be['start']['datum'])
        self.assertEqual(be['start']['value'], '2026-09-02T15:30:00+01:00')
        self.assertFalse(list(self.good_graph.triples((None, OWL.sameAs, None))))

    def test_pro_witness_and_source_binding_preserved(self):
        self.assertEqual(len({r.patient_bearer for r in self.records}), 1)
        self.assertEqual(len({r.patient_role for r in self.records}), 4)
        for r in self.records:
            binding = self.evidence['bindings'][r.evidence_id]
            self.assertIn((URIRef(r.process), S.hasParticipant, URIRef(r.patient_role)), self.good_graph)
            self.assertIn((URIRef(r.patient_role), S.isFeatureOf, URIRef(r.patient_bearer)), self.good_graph)
            self.assertEqual(binding['source_metadata']['event_id'], r.event_id)
            self.assertIn((URIRef(binding['source_record']), S.refersTo, URIRef(r.process)), self.good_graph)

    def test_optional_duration_is_computed_without_fabricating_datum(self):
        binding = self.evidence['bindings'][self.by_id['D'].evidence_id]
        self.assertIsNone(binding['recorded_duration'])
        self.assertEqual(binding['duration_us'], 900000000)

    def test_second_minute_hour_units_are_exact(self):
        for value, unit in [('1800', 'second'), ('30', 'minute'), ('0.5', 'hour')]:
            source = self.source(); source['events'][0]['duration'] = {'value': value, 'unit': unit}
            records, evidence = self.project(ei.build_graph(source))
            a = next(r for r in records if r.event_id == 'A')
            self.assertEqual(evidence['bindings'][a.evidence_id]['recorded_duration']['normalized_us'], 1800000000)

    def test_microseconds_and_duration_do_not_round(self):
        source = self.source()
        source['events'][0].update(start='2026-09-02T14:00:00.000001Z', end='2026-09-02T14:00:00.000002Z',
                                   duration={'value': '0.000001', 'unit': 'second'})
        records, _ = self.project(ei.build_graph(source))
        a = next(r for r in records if r.event_id == 'A')
        self.assertEqual(a.end_us - a.start_us, 1)
        with self.assertRaisesRegex(ei.ContractError, 'EXACT_MICROSECONDS'):
            ei.duration_us('0.00000100000000000000000000000000000000000001', 'second')

    def test_class_only_profile_and_solid_literals(self):
        profile = Graph().parse(ei.PROFILE)
        for typ in (OWL.ObjectProperty, OWL.DatatypeProperty):
            self.assertFalse(list(profile.subjects(RDF.type, typ)))
        for _, predicate, obj in self.good_graph:
            if isinstance(obj, Literal):
                self.assertEqual(predicate, S.hasValue)

    def test_graph_roundtrip_preserves_projection_and_evidence(self):
        parsed = Graph().parse(data=ei.turtle_text(self.good_graph), format='turtle')
        self.assertTrue(isomorphic(self.good_graph, parsed))
        self.assertEqual(self.project(parsed), (self.records, self.evidence))

    def test_committed_graph_matches_source_and_projection(self):
        graph = Graph().parse(EXAMPLE / 'graph.ttl', format='turtle')
        self.assertTrue(isomorphic(self.good_graph, graph))
        self.assertEqual(self.project(graph), (self.records, self.evidence))

    def test_graph_order_does_not_change_evidence(self):
        g = Graph()
        for triple in reversed(sorted(self.good_graph, key=str)):
            g.add(triple)
        self.assertEqual(self.project(g), (self.records, self.evidence))

    def test_decimal_spelling_and_multiline_metadata_survive_export(self):
        source = self.source()
        source['events'][0]['duration']['value'] = '+030.0000'
        source['events'][0]['source_key'] = 'quoted "source"\nsecond line'
        graph = ei.build_graph(source)
        parsed = Graph().parse(data=ei.turtle_text(graph), format='turtle')
        self.assertTrue(isomorphic(graph, parsed))
        self.assertEqual(self.project(graph), self.project(parsed))

    def test_source_and_snapshot_changes_change_context(self):
        source = self.source(); source['events'][0]['source_key'] = 'different-source'
        changed, _ = self.project(ei.build_graph(source))
        self.assertNotEqual(changed[0].evidence_id, self.records[0].evidence_id)
        other, _ = ei.project(self.good_graph, {**self.manifest, 'snapshot_id': 'other'})
        result = ei.evaluate(self.by_id['A'], next(r for r in other if r.event_id == 'B'), 'meets')
        self.assertEqual(result['status'], 'INCOMPARABLE')
        self.assertIn('CONTEXT_MISMATCH', result['reason_codes'])

    def test_missing_end_rejected(self):
        g = self.graph(); g.remove((D['events/A/interval'], S.hasDirectPart, D['events/A/end']))
        self.bad_graph(g, 'SHACL')

    def test_duplicate_core_start_rejected(self):
        g = self.graph(); extra = D['extra-start']
        g.add((D['events/A/interval'], S.hasDirectPart, extra)); g.add((extra, RDF.type, S.StartTime))
        self.bad_graph(g, 'SHACL')

    def test_multiple_values_rejected(self):
        g = self.graph(); g.add((D['events/A/start'], S.hasValue,
                               Literal('2026-09-02T13:00:00Z', datatype=XSD.dateTimeStamp)))
        self.bad_graph(g, 'SHACL')

    def test_interval_scalar_rejected(self):
        g = self.graph(); g.add((D['events/A/interval'], S.hasValue, Literal('ambiguous')))
        self.bad_graph(g, 'SHACL')

    def test_start_end_class_merge_rejected(self):
        g = self.graph(); g.add((D['events/A/start'], RDF.type, EI.ExactEndTime))
        self.bad_graph(g, 'DISJOINT')

    def test_equal_or_reversed_endpoints_rejected(self):
        for end in ('2026-09-02T14:00:00Z', '2026-09-02T13:59:59Z'):
            source = self.source(); source['events'][0]['end'] = end
            self.bad_source(source, 'PROPER_INTERVAL')

    def test_unzoned_unknown_offset_and_excess_precision_rejected(self):
        for start in ('2026-09-02T14:00:00', '2026-09-02T14:00:00-00:00',
                      '2026-09-02T14:00:00.0000001Z', '2026-09-02T14:00:00+14:01',
                      '2026-09-02T14:00:60Z'):
            source = self.source(); source['events'][0]['start'] = start
            self.bad_source(source, 'SHACL|TIME|DATETIME')

    def test_wrong_endpoint_datatype_rejected(self):
        g = self.graph(); g.set((D['events/A/start'], S.hasValue, Literal('2026-09-02T14:00:00Z')))
        self.bad_graph(g, 'SHACL')

    def test_duration_disagreement_rejected(self):
        source = self.source(); source['events'][0]['duration']['value'] = '31'
        self.bad_source(source, 'DURATION_DISAGREEMENT')

    def test_invalid_duration_values_rejected(self):
        for value in ('-1', 'NaN', 'Infinity', '1e3', '0', '0.0000001', 30, 30.0):
            source = self.source(); source['events'][0]['duration'] = {'value': value, 'unit': 'second'}
            self.bad_source(source, 'DURATION')

    def test_unsupported_duration_and_multiple_units_rejected(self):
        source = self.source(); source['events'][0]['duration']['unit'] = 'month'
        self.bad_source(source, 'DURATION_UNIT')
        g = self.graph(); g.add((D['events/A/duration'], S.hasDirectPart, EI.Second))
        self.bad_graph(g, 'SHACL')

    def test_unit_access_uses_selected_scalar_not_interval_descendants(self):
        binding = self.evidence['bindings'][self.by_id['A'].evidence_id]
        self.assertEqual(binding['start']['unit'], str(EI.Second))
        self.assertEqual(binding['recorded_duration']['unit'], str(EI.Minute))
        g = self.graph(); g.add((D['events/A/interval'], S.hasPart, EI.Minute))
        self.bad_graph(g, 'UNSUPPORTED_OBJECT_PROPERTY')

    def test_missing_or_multiple_clock_bindings_rejected(self):
        for add in (False, True):
            g = self.graph()
            if add: g.add((D['events/A/interval'], S.hasDirectPart, D['events/B/clock-binding']))
            else: g.remove((D['events/A/interval'], S.hasDirectPart, D['events/A/clock-binding']))
            self.bad_graph(g, 'SHACL')

    def test_different_clocks_are_incomparable_even_with_equal_coordinates(self):
        source = self.source()
        source['clocks'].append({**source['clocks'][0], 'clock_id': 'other-clock'})
        source['events'][1]['clock_id'] = 'other-clock'
        records, _ = self.project(ei.build_graph(source)); by_id = {r.event_id: r for r in records}
        result = ei.evaluate(by_id['A'], by_id['B'], 'meets')
        self.assertEqual(result['status'], 'INCOMPARABLE')
        self.assertEqual(result['reason_codes'], ['CLOCK_MISMATCH'])
        self.assertIsNone(result['relation']); self.assertIsNone(result['signed_gap_us'])

    def test_clock_patient_scope_enforced(self):
        source = self.source(); source['clocks'][0]['scope'] = 'patient:P2'
        self.bad_source(source, 'CLOCK_PATIENT_SCOPE_MISMATCH')

    def test_unsupported_clock_policy_or_scope_rejected(self):
        for field, value in [('policy', 'calendar-years'), ('scope', 'unspecified')]:
            source = self.source(); source['clocks'][0][field] = value
            self.bad_source(source, 'UNSUPPORTED_CLOCK')

    def test_duplicate_clock_identity_in_existing_graph_rejected(self):
        source = self.source(); source['clocks'].append({**source['clocks'][0], 'clock_id': 'other'})
        g = ei.build_graph(source)
        g.set((D['clocks/other/clock_id'], S.hasValue, Literal('fixture-clock', datatype=XSD.string)))
        self.bad_graph(g, 'DUPLICATE_CLOCK_IDENTIFIER')

    def test_missing_clock_metadata_rejected(self):
        g = self.graph(); g.remove((D['clocks/fixture-clock'], S.hasDirectPart, D['clocks/fixture-clock/policy']))
        self.bad_graph(g, 'SHACL')

    def test_global_clock_does_not_bypass_patient_or_episode_join(self):
        for field, value in [('patient_id', 'P2'), ('episode_id', 'E2')]:
            source = self.source(); source['clocks'][0]['scope'] = 'global'; source['events'][1][field] = value
            records, _ = self.project(ei.build_graph(source)); by_id = {r.event_id: r for r in records}
            result = ei.evaluate(by_id['A'], by_id['B'], 'meets')
            self.assertEqual(result['status'], 'INCOMPARABLE')
            self.assertIn('TRAJECTORY_SCOPE_MISMATCH', result['reason_codes'])

    def test_generic_participation_is_not_patient_role_evidence(self):
        g = self.graph(); g.remove((D['events/A'], S.hasParticipant, D['events/A/patient-role']))
        g.add((D['events/A'], S.hasParticipant, D['persons/P1']))
        self.bad_graph(g, 'SHACL')

    def test_care_provider_is_not_patient(self):
        g = self.graph(); g.remove((D['events/A/patient-role'], RDF.type, EX.PatientRole))
        g.add((D['events/A/patient-role'], RDF.type, EX.CareProviderRole))
        self.bad_graph(g, 'SHACL')

    def test_inverse_feature_path_supported(self):
        g = self.graph(); g.remove((D['events/A/patient-role'], S.isFeatureOf, D['persons/P1']))
        g.add((D['persons/P1'], S.hasFeature, D['events/A/patient-role']))
        records, _ = self.project(g)
        self.assertEqual(next(r for r in records if r.event_id == 'A').patient_bearer, str(D['persons/P1']))

    def test_shared_patient_role_rejected(self):
        g = self.graph(); g.set((D['events/B'], S.hasParticipant, D['events/A/patient-role']))
        self.bad_graph(g, 'ROLE_REUSED')

    def test_ambiguous_patient_bearer_rejected(self):
        g = self.graph(); g.add((D['persons/P2'], RDF.type, EX.Person))
        g.add((D['events/A/patient-role'], S.isFeatureOf, D['persons/P2']))
        self.bad_graph(g, 'SHACL')

    def test_same_identifier_on_different_people_rejected(self):
        source = self.source(); source['clocks'][0]['scope'] = 'global'; source['events'][1]['patient_id'] = 'P2'
        g = ei.build_graph(source); g.set((D['persons/P2/identifier'], S.hasValue, Literal('P1', datatype=XSD.string)))
        self.bad_graph(g, 'PATIENT_IDENTIFIER_COLLISION')

    def test_missing_source_record_rejected(self):
        g = self.graph(); g.remove((D['records/R-A'], S.refersTo, D['events/A']))
        self.bad_graph(g, 'SHACL')

    def test_duplicate_record_identifier_rejected_in_rdf(self):
        g = self.graph(); g.set((D['records/R-B/record_id'], S.hasValue, Literal('R-A', datatype=XSD.string)))
        self.bad_graph(g, 'DUPLICATE_SOURCE_IDENTIFIER')

    def test_invalid_source_hash_rejected(self):
        g = self.graph(); g.set((D['records/R-A/source_hash'], S.hasValue, Literal('not-a-hash', datatype=XSD.string)))
        self.bad_graph(g, 'INVALID_SOURCE_HASH')

    def test_reused_interval_rejected(self):
        g = self.graph(); g.set((D['events/B'], S.atTime, D['events/A/interval']))
        self.bad_graph(g, 'REUSED_TEMPORAL_DESCRIPTOR')

    def test_prohibited_predicates_and_blank_nodes_rejected(self):
        mutations = [(D['events/A'], EX.hasPatient, D['persons/P1']),
                     (D['events/A'], EX.start, Literal('2026-09-02')),
                     (BNode(), RDF.type, EX.Person)]
        for triple in mutations:
            g = self.graph(); g.add(triple)
            self.bad_graph(g, 'UNSUPPORTED_OBJECT_PROPERTY|SOLID_LITERAL_PROPERTY|NAMED_INSTANCE_REQUIRED')

    def test_not_performed_records_rejected(self):
        for status in ('not-given', 'planned', 'prescribed'):
            source = self.source(); source['events'][0]['status'] = status
            self.bad_source(source, 'UNSUPPORTED_RECORD_STATUS')

    def test_missing_extra_or_duplicate_source_fields_rejected(self):
        source = self.source(); del source['events'][0]['end']; self.bad_source(source, 'SOURCE_FIELDS')
        source = self.source(); source['events'][0]['guessed_time'] = True; self.bad_source(source, 'SOURCE_FIELDS')
        source = self.source(); source['events'].append(source['events'][0]); self.bad_source(source, 'DUPLICATE_SOURCE')

    def test_unsupported_manifest_profile_rejected(self):
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_PROFILE'):
            ei.project(self.good_graph, {**self.manifest, 'profile': 'pro-solid-2.4'})

    def test_old_point_profile_is_not_silently_accepted(self):
        old = Graph().parse(ROOT / 'examples/pro-solid/graph.ttl')
        self.bad_graph(old, 'UNSUPPORTED_OBJECT_PROPERTY|EMPTY_OR_MIXED_PROFILE')
        with self.assertRaises(base.ContractError):
            base.project(self.good_graph, json.loads((ROOT / 'examples/pro-solid/manifest.json').read_text()))

    def test_bad_operators_gap_bounds_and_queries_rejected(self):
        a, b = self.by_id['A'], self.by_id['B']
        for kwargs in ({'operator': 'immediatelyPrecedes'}, {'operator': 'gap'},
                       {'operator': 'gap', 'min_gap_us': -1, 'max_gap_us': 1},
                       {'operator': 'gap', 'min_gap_us': 2, 'max_gap_us': 1},
                       {'operator': 'gap', 'min_gap_us': False, 'max_gap_us': 1},
                       {'operator': 'gap', 'min_gap_us': 0, 'max_gap_us': 1.0},
                       {'operator': 'before', 'max_gap_us': 1}):
            with self.assertRaises(ei.ContractError): ei.evaluate(a, b, **kwargs)
        with self.assertRaisesRegex(ei.ContractError, 'UNKNOWN_EVENT'):
            ei.run_queries(self.records, [{'id': 'bad', 'left_event': 'missing', 'right_event': 'B', 'operator': 'before'}])

    def test_self_comparison_is_equals_without_strict_precedence(self):
        a = self.by_id['A']
        result = ei.evaluate(a, a, 'before')
        self.assertEqual((result['relation'], result['status']), ('equals', 'NOT_SATISFIED'))

    def test_allen_endpoint_order_oracle_441_pairs(self):
        # Independently specified weak endpoint orders (sA, eA, sB, eB).
        orders = {(0,1,2,3): 'before', (0,1,1,2): 'meets', (0,2,1,3): 'overlaps',
                  (0,1,0,2): 'starts', (1,2,0,3): 'during', (1,2,0,2): 'finishes',
                  (0,1,0,1): 'equals', (2,3,0,1): 'after', (1,2,0,1): 'met_by',
                  (1,3,0,2): 'overlapped_by', (0,2,0,1): 'started_by',
                  (0,3,1,2): 'contains', (0,2,1,2): 'finished_by'}
        intervals = [(s, e) for s in range(7) for e in range(s+1, 7)]
        observed = set()
        for a, b in product(intervals, repeat=2):
            ranks = {v: i for i, v in enumerate(sorted(set(a+b)))}
            expected = orders[tuple(ranks[v] for v in a+b)]
            self.assertEqual(ei.allen_relation(a, b), expected)
            observed.add(expected)
        self.assertEqual(len(observed), 13)
        self.assertEqual(len(intervals)**2, 441)

    def test_interval_geometry_and_integer_range_rejected(self):
        for endpoints in ((0, 0), (1, 0), (False, 1), (0, 1.0), (0, 2**63)):
            with self.assertRaises(ei.ContractError): ei.allen_relation(endpoints, (2, 3))
        with self.assertRaises(ei.ContractError): replace(self.by_id['A'], end_us=self.by_id['A'].start_us)

    def test_cli_source_and_graph_paths_agree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, flags in [('source', []), ('graph', ['--graph', str(EXAMPLE / 'graph.ttl')])]:
                run = subprocess.run([sys.executable, '-m', 'patterns.exact_intervals', '--output', str(root/name), *flags],
                                     cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
            for file in ('intervals.json', 'evidence.json', 'comparisons.json'):
                self.assertEqual(json.loads((root/'source'/file).read_text()), json.loads((root/'graph'/file).read_text()))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(IntervalTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {'profile': ei.PROFILE_ID, 'tests_run': result.testsRun, 'failures': len(result.failures),
              'errors': len(result.errors), 'passed': result.wasSuccessful(),
              'scope': 'synthetic exact occurrence intervals and finite endpoint comparisons only',
              'production_readiness_claim': False}
    (ROOT / 'verification/exact-interval-report.json').write_text(json.dumps(report, indent=2) + '\n')
    sys.exit(0 if result.wasSuccessful() else 1)
