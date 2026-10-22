"""Paired fixture comparisons and explicit boundaries of the SULO mapping."""
from copy import deepcopy
import hashlib
import json
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, OWL, RDF, URIRef, XSD

from . import bounded_cohort as cohort
from . import bounded_intervals as bt
from . import bounded_rdf as rdf
from . import evidence_selection as selection
from . import exact_intervals as ei
from . import temporal_interface as ti


def load(name='ordered'):
    folder = ti.EXAMPLES / name
    return (Graph().parse(folder / 'formal.ttl'), Graph().parse(folder / 'sulo.ttl'),
            json.loads((folder / 'interface.json').read_text()), json.loads((folder / 'queries.json').read_text()))


def set_coordinate(data, graph, variable, value):
    row = next(v for v in data['variables'] if v['id'] == variable)
    row['lower_us'] = row['upper_us'] = value
    for side in ('lower', 'upper'):
        node = bt.D['variables/' + variable + '/' + side]
        graph.set((node, ei.S.hasValue, Literal(value, datatype=XSD.integer)))


class MappingTests(unittest.TestCase):
    def test_paired_corpus(self):
        cases = json.loads((ti.EXAMPLES / 'cases.json').read_text())
        self.assertEqual(len(cases), 8)
        comparisons = 0
        for case in cases:
            with self.subTest(case=case['id']):
                result = ti.compare(ti.EXAMPLES / case['id'])
                comparisons += len(result['checks'])
                check = next(c for c in result['checks'] if c['query_id'] == case['expected_query'])
                binding = next(b for b in check['answers']['bindings'] if b['slots'] == case['expected_slots'])
                self.assertEqual(binding['status'], case['expected_status'])
                self.assertFalse(result['full_owl_mapping_verified'])
        self.assertEqual(comparisons, 33)

    def test_formal_reference_does_not_use_production_compiler(self):
        formal, _, interface, queries = load('correlated')
        with (patch.object(bt, 'compile_source', side_effect=AssertionError('production compiler')),
              patch.object(cohort, 'classify', side_effect=AssertionError('production classifier'))):
            source = ti.formal_view(formal, interface)['view']
            actual = ti.formal_answers(source, queries[0])
        self.assertEqual(actual['certain_patient_ids'], ['P1'])

    def test_frozen_sulo_inputs_do_not_use_graph_writer(self):
        with patch.object(bt, 'build_graph', side_effect=AssertionError('graph writer')):
            result = ti.compare(ti.EXAMPLES / 'ordered')
        self.assertEqual(result['checks'][0]['answers']['certain_patient_ids'], ['P1'])

    def test_changed_sulo_coordinate_is_detected(self):
        formal, sulo, interface, _ = load()
        changed = deepcopy(interface)
        set_coordinate(changed, sulo, 'Bs', 2)
        self.assertNotEqual(ti.formal_view(formal, interface)['view'], ti.sulo_view(sulo)[0]['view'])

    def test_missing_named_endpoint_is_not_an_anonymous_witness(self):
        formal, sulo, interface, _ = load()
        formal.remove((ti.F.intervalA, ti.F.hasBeginning, None))
        sulo.remove((bt.D['events/A/start'], ei.S.refersTo, None))
        with self.assertRaisesRegex(ei.ContractError, 'FORMAL_UNIQUE_NAMED_LINK'):
            ti.formal_view(formal, interface)
        with self.assertRaisesRegex(ei.ContractError, 'RDF_CARDINALITY'):
            ti.sulo_view(sulo)

    def test_unknown_complete_extent_is_unsupported(self):
        formal, sulo, interface, _ = load()
        formal.remove((ti.F.A, ti.F.exactExtent, None))
        sulo.remove((bt.D['events/A'], ei.S.atTime, None))
        with self.assertRaises(ei.ContractError): ti.formal_view(formal, interface)
        with self.assertRaises(ei.ContractError): ti.sulo_view(sulo)

    def test_point_occurrences_are_outside_this_interval_interface(self):
        formal, sulo, interface, _ = load()
        formal.set((ti.F.A, ti.F.exactExtent, ti.F.As))
        sulo.set((bt.D['events/A'], ei.S.atTime, bt.D['events/A/start']))
        with self.assertRaisesRegex(ei.ContractError, 'FORMAL_EXPLICIT_TYPE_REQUIRED'):
            ti.formal_view(formal, interface)
        with self.assertRaises(ei.ContractError): ti.sulo_view(sulo)

    def test_multiple_extents_do_not_make_at_time_functional(self):
        formal, sulo, interface, _ = load()
        formal.add((ti.F.A, ti.F.exactExtent, ti.F.intervalB))
        sulo.add((bt.D['events/A'], ei.S.atTime, bt.D['events/B/interval']))
        with self.assertRaises(ei.ContractError): ti.formal_view(formal, interface)
        with self.assertRaises(ei.ContractError): ti.sulo_view(sulo)
        ontology = ei.base.ontology()
        self.assertNotIn((ei.S.atTime, RDF.type, OWL.FunctionalProperty), ontology)

    def test_duplicate_endpoints_do_not_trigger_identity_merging(self):
        formal, sulo, interface, _ = load()
        formal.add((ti.F.intervalA, ti.F.hasBeginning, ti.F.Bs))
        extra = URIRef('urn:extra-start-description')
        sulo.add((bt.D['events/A/interval'], ei.S.hasDirectPart, extra))
        sulo.add((extra, RDF.type, bt.BT.StartDescriptor))
        sulo.add((extra, ei.S.refersTo, bt.D['variables/Bs']))
        with self.assertRaises(ei.ContractError): ti.formal_view(formal, interface)
        with self.assertRaises(ei.ContractError): ti.sulo_view(sulo)

    def test_zero_duration_with_distinct_points_is_temporally_inconsistent(self):
        formal, sulo, interface, queries = load()
        set_coordinate(interface, sulo, 'As', 1)
        data = ti.formal_view(formal, interface)
        self.assertNotEqual(data['entities']['A']['start_point'], data['entities']['A']['end_point'])
        with self.assertRaisesRegex(ei.ContractError, 'FORMAL_TEMPORAL_INCONSISTENCY'):
            ti.formal_answers(data['view'], queries[0])
        with self.assertRaises(bt.InconsistentSource): ti.sulo_view(sulo)

    def test_reversed_extent_requires_temporal_validation(self):
        formal, sulo, interface, queries = load()
        set_coordinate(interface, sulo, 'As', 2)
        data = ti.formal_view(formal, interface)
        with self.assertRaisesRegex(ei.ContractError, 'FORMAL_TEMPORAL_INCONSISTENCY'):
            ti.formal_answers(data['view'], queries[0])
        with self.assertRaises(bt.InconsistentSource): ti.sulo_view(sulo)

    def test_identical_formal_endpoint_and_shared_sulo_variable_differ(self):
        formal, sulo, interface, _ = load()
        formal.set((ti.F.intervalA, ti.F.hasEnd, ti.F.As))
        sulo.set((bt.D['events/A/end'], ei.S.refersTo, bt.D['variables/As']))
        with self.assertRaisesRegex(ei.ContractError, 'FORMAL_IDENTICAL_ENDPOINTS'):
            ti.formal_view(formal, interface)
        # SULO endpoint descriptions stay different, but the interval has zero duration.
        with self.assertRaises(bt.InconsistentSource): ti.sulo_view(sulo)

    def test_coincidence_preserves_declared_difference(self):
        formal, sulo, interface, _ = load('coincident_boundaries')
        data = ti.formal_view(formal, interface)
        self.assertIn([str(ti.F.Ae), str(ti.F.Bs)], data['declared_different_points'])
        a, b = data['entities']['A'], data['entities']['B']
        self.assertNotEqual(a['end_point'], b['start_point'])
        mapped, _ = ti.sulo_view(sulo)
        self.assertNotEqual(mapped['entities']['A']['end_descriptor'], mapped['entities']['B']['start_descriptor'])
        self.assertNotEqual(mapped['entities']['A']['end_variable'], mapped['entities']['B']['start_variable'])
        self.assertEqual(data['view'], mapped['view'])

    def test_shared_variable_does_not_merge_start_and_end_descriptions(self):
        formal, sulo, interface, _ = load('shared_boundary')
        f = ti.formal_view(formal, interface); s, _ = ti.sulo_view(sulo)
        self.assertEqual(f['entities']['A']['end_point'], f['entities']['B']['start_point'])
        self.assertEqual(s['entities']['A']['end_variable'], s['entities']['B']['start_variable'])
        self.assertNotEqual(s['entities']['A']['end_descriptor'], s['entities']['B']['start_descriptor'])
        self.assertEqual(f['view'], s['view'])

    def test_qualitative_owl_facts_are_not_silently_compiled(self):
        formal, sulo, interface, _ = load()
        formal.add((ti.F.As, ti.F.pointBefore, ti.F.Bs))
        sulo.add((bt.D['events/A'], ei.S.precedes, bt.D['events/B']))
        with self.assertRaisesRegex(ei.ContractError, 'FORMAL_UNCONSUMED_TRIPLES'):
            ti.formal_view(formal, interface)
        with self.assertRaisesRegex(ei.ContractError, 'UNCONSUMED_RDF_TRIPLES'):
            ti.sulo_view(sulo)

    def test_bearer_participation_cannot_replace_the_role(self):
        formal, sulo, interface, _ = load()
        formal.set((ti.F.A, ti.F.hasParticipant, ti.F.patient))
        sulo.set((bt.D['events/A'], ei.S.hasParticipant, bt.D['persons/P1']))
        with self.assertRaises(ei.ContractError): ti.formal_view(formal, interface)
        with self.assertRaises(ei.ContractError): ti.sulo_view(sulo)

    def test_selector_and_rational_domain_limits_are_explicit(self):
        formal, _, interface, queries = load()
        query = deepcopy(queries[0]); query['slots'][0]['class_iri'] = str(bt.BT.Infusion)
        with self.assertRaisesRegex(ei.ContractError, 'COMMON_SELECTOR_IS_PROCESS_ONLY'):
            ti.formal_answers(ti.formal_view(formal, interface)['view'], query)
        interface['variables'][0]['lower_us'] = 0.5
        with self.assertRaisesRegex(ei.ContractError, 'INTEGER_MICROSECONDS'):
            ti.formal_view(formal, interface)
        _, _, interface, _ = load()
        interface['clocks'].append({**interface['clocks'][0], 'clock_id': 'unused', 'scope': 'unknown'})
        with self.assertRaisesRegex(ei.ContractError, 'FORMAL_CLOCK_SCOPE'):
            ti.formal_view(formal, interface)

    def test_reference_resource_limit_is_not_a_complete_result(self):
        formal, _, interface, queries = load()
        interface['variables'][0]['upper_us'] = 100001
        with self.assertRaisesRegex(ValueError, 'REFERENCE_ENUMERATION_LIMIT'):
            ti.formal_answers(ti.formal_view(formal, interface)['view'], queries[0])

    def test_renamed_instance_iris_preserve_common_view(self):
        formal, sulo, interface, _ = load()
        original = ti.formal_view(formal, interface)['view']
        def rename(graph, protected=()):
            names = {s: URIRef('urn:renamed:' + str(i)) for i, s in enumerate(sorted(set(graph.subjects()))) if s not in protected}
            changed = Graph()
            for a, b, c in graph: changed.add((names.get(a, a), b, names.get(c, c)))
            return changed, names
        formal, names = rename(formal)
        for p in interface['points']: p['point'] = str(names[URIRef(p['point'])])
        for p in interface['processes']: p['process'] = str(names[URIRef(p['process'])])
        sulo, _ = rename(sulo, (bt.BT.Microsecond, ei.EI.Second))
        self.assertEqual(ti.formal_view(formal, interface)['view'], original)
        self.assertEqual(ti.sulo_view(sulo)[0]['view'], original)

    def test_temporal_type_conflicts_are_rejected_by_the_profile(self):
        formal, sulo, interface, _ = load()
        formal.add((ti.F.intervalA, RDF.type, ti.F.TimePoint))
        sulo.add((bt.D['events/A/interval'], RDF.type, ei.S.TimeInstant))
        with self.assertRaises(ei.ContractError): ti.formal_view(formal, interface)
        with self.assertRaises(ei.ContractError): ti.sulo_view(sulo)

    def test_correction_updates_coordinates_preserving_process_bindings(self):
        root = ei.ROOT / 'examples/evidence-selection'
        archive = json.loads((root / 'archive.json').read_text()); request = json.loads((root / 'request.json').read_text())
        formal, _, interface, queries = load()
        first = None
        for mode in ('source_as_known', 'retrospective'):
            request['mode'] = mode
            selected = selection.select(archive, request)
            self.assertEqual(selected['status'], 'READY')
            mapped, snapshot = ti.sulo_view(rdf.export_source(selected['source']))
            independent = deepcopy(interface)
            independent['clocks'] = [{**c, 'clock_id': 'clinical'} for c in independent['clocks']]
            for v in independent['variables']:
                v['clock_id'] = 'clinical'
                if mode == 'retrospective' and v['id'] in ('Bs', 'Be'):
                    v['lower_us'] = v['upper_us'] = 0 if v['id'] == 'Bs' else 1
            expected = ti.formal_view(formal, independent)
            self.assertEqual(mapped['view'], expected['view'])
            self.assertEqual(ti.matching_answers(cohort.execute(snapshot, queries[0])), ti.formal_answers(expected['view'], queries[0]))
            if first is None: first = mapped
            else:
                self.assertEqual(mapped['entities'], first['entities'])
                self.assertNotEqual(mapped['view']['variables'], first['view']['variables'])

    def test_register_covers_original_checks_without_claiming_full_owl(self):
        register = json.loads((ei.ROOT / 'docs/temporal-kg/sulo-conformance.json').read_text())
        original = json.loads((ei.ROOT / 'docs/temporal-kg/validation/checks.json').read_text())
        self.assertEqual([r['original_check_id'] for r in register['checks']], [c['id'] for c in original])
        self.assertFalse(register['full_owl_mapping_verified'])
        self.assertEqual(register['original_inventory_sha256'], hashlib.sha256(
            (ei.ROOT / 'docs/temporal-kg/validation/checks.json').read_bytes()).hexdigest())
        for row, check in zip(register['checks'], original):
            self.assertEqual(row['original_label'], check['label'])
            self.assertEqual(row['original_expected'], check['expected'])
            for test in row['tests']: self.assertTrue(hasattr(self, test), test)
        self.assertEqual(register['common_selectors'], [str(ei.S.Process)])


class BoundaryIdentityTests(unittest.TestCase):
    """BT_C forbids configurations that OWL and the integer network each accept.

    The specification's `three-boundaries.ofn` is consistent as OWL and satisfiable as
    integers while being impossible under the time theory, so running those two checks
    side by side is not the same as checking the pair. These read the fixture data for
    the configurations the theory rules out.
    """

    def conflicts(self, name, data=None, graph=None):
        formal, sulo, interface, _ = load(name)
        view, _ = ti.sulo_view(graph if graph is not None else sulo)
        return ti.boundary_conflicts(view['entities'], data if data is not None else interface)

    def test_every_committed_fixture_is_free_of_forbidden_configurations(self):
        for folder in sorted(p.name for p in ti.EXAMPLES.iterdir() if p.is_dir()):
            conflicts, _ = self.conflicts(folder)
            self.assertEqual(conflicts, [], folder)

    def test_the_fixtures_with_pinned_coordinates_actually_place_boundaries(self):
        """Without this, the check above passes on a walk that matched nothing. The
        evidence names variables by IRI and the interface by short id, and comparing
        those two forms directly reports every fixture clean.
        """
        placed = {name: self.conflicts(name)[1]
                  for name in sorted(p.name for p in ti.EXAMPLES.iterdir() if p.is_dir())}
        self.assertEqual(placed['ordered'], 4)
        self.assertEqual(placed['coincident_boundaries'], 4)
        self.assertGreaterEqual(sum(1 for n in placed.values() if n), 5)

    def test_fixtures_without_pinned_coordinates_place_nothing(self):
        """Not a gap: an uncertain boundary has no coordinate to coincide at, and
        placing one would assert a time the fixture never gave.
        """
        for name in ('chain_order', 'correlated', 'joint_impossibility'):
            conflicts, placed = self.conflicts(name)
            self.assertEqual((conflicts, placed), ([], 0), name)

    def test_two_distinct_boundaries_of_one_orientation_cannot_coincide(self):
        """C4: coincident boundaries sharing an orientation are the same boundary, so
        two distinct start descriptors at one coordinate is not a near miss.
        """
        _, sulo, interface, _ = load('ordered')
        moved = json.loads(json.dumps(interface))
        set_coordinate(moved, sulo, 'Bs', 0)
        conflicts, placed = self.conflicts('ordered', data=moved)
        self.assertEqual(placed, 4)
        self.assertTrue(any(c.startswith('BTC_C4_DISTINCT_START') for c in conflicts), conflicts)

    def test_a_coincidence_class_cannot_exceed_two_members(self):
        """A22, and the configuration the specification's lemma isolates: OWL accepts
        it and an integer assignment giving all three the same coordinate satisfies the
        network, so only the joint theory rejects it.
        """
        _, sulo, interface, _ = load('ordered')
        crowded = json.loads(json.dumps(interface))
        for variable in ('Ae', 'Bs'):
            set_coordinate(crowded, sulo, variable, 0)
        conflicts, _ = self.conflicts('ordered', data=crowded)
        self.assertTrue(any(c.startswith('BTC_A22_') for c in conflicts), conflicts)

    def test_an_uncertain_boundary_is_never_placed_at_a_coordinate(self):
        _, sulo, interface, _ = load('ordered')
        loosened = json.loads(json.dumps(interface))
        row = next(v for v in loosened['variables'] if v['id'] == 'As')
        row['upper_us'] = row['lower_us'] + 5
        conflicts, placed = self.conflicts('ordered', data=loosened)
        self.assertEqual((conflicts, placed), ([], 3), 'the loosened boundary must drop out')


if __name__ == '__main__':
    unittest.main()
