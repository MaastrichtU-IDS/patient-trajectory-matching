"""Closed RDF ingestion tests: graph identity, evidence, failures and CLI parity."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from rdflib import BNode, Graph, Literal, OWL, RDF, URIRef, XSD
from rdflib.compare import isomorphic

from . import bounded_rdf as br
from . import bounded_intervals as bt
from . import bounded_cohort as cohort
from . import exact_intervals as ei
from . import test_bounded_intervals as conformance

D, S = bt.D, ei.S


def outcomes(result):
    return [(t['patient_id'], t['episode_id'], t['status'],
             [(b['status'], tuple((name, slot['event_id']) for name, slot in sorted(b['slots'].items())))
              for b in t['bindings']]) for t in result['trajectories']]


class RdfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((ei.ROOT / 'examples/bounded-interval/source.json').read_text())
        cls.query = json.loads((ei.ROOT / 'examples/bounded-interval/query.json').read_text())
        cls.original = br.export_source(cls.source)

    def graph(self):
        g = Graph()
        for triple in self.original: g.add(triple)
        return g

    def bad(self, graph, code):
        with self.assertRaisesRegex(ei.ContractError, code): br.prepare_graph(graph)

    def test_source_rdf_equivalence_and_certificates(self):
        expected = cohort.execute(bt.prepare(self.source), self.query)
        snapshot = br.prepare_graph(self.graph())
        actual = cohort.execute(snapshot, self.query)
        self.assertEqual(outcomes(expected), outcomes(actual))
        self.assertEqual(actual['certain_patient_ids'], ['P1'])
        self.assertEqual(actual['possible_patient_ids'], ['P1', 'P2'])
        conformance.BoundedTests().verify_result(snapshot, self.query, actual)

    def test_rdf_roundtrip_same_full_result_and_context(self):
        original = br.prepare_graph(self.graph())
        roundtrip = br.prepare_graph(Graph().parse(data=ei.turtle_text(self.graph()), format='turtle'))
        self.assertEqual(cohort.execute(original, self.query), cohort.execute(roundtrip, self.query))
        self.assertTrue(isomorphic(original.graph, roundtrip.graph))

    def test_arbitrary_instance_iris_do_not_depend_on_uri_suffixes(self):
        g = self.graph()
        nodes = sorted({s for s in g.subjects() if str(s).startswith(str(D))})
        replacements = {node: URIRef('urn:independent:' + str(len(nodes) - i)) for i, node in enumerate(nodes)}
        renamed = Graph()
        for s, p, o in g: renamed.add((replacements.get(s, s), p, replacements.get(o, o)))
        snapshot = br.prepare_graph(renamed)
        self.assertEqual(outcomes(cohort.execute(snapshot, self.query)), outcomes(cohort.execute(br.prepare_graph(g), self.query)))
        for item in snapshot.evidence.values():
            self.assertTrue(item['process'].startswith('urn:independent:'))
            self.assertIn((URIRef(item['process']), S.hasParticipant, URIRef(item['patient_role'])), renamed)
            self.assertIn((URIRef(item['patient_role']), S.isFeatureOf, URIRef(item['patient_bearer'])), renamed)
        self.assertNotEqual(snapshot.context_id, br.prepare_graph(g).context_id)

    def test_integer_lexical_forms_and_source_declarations_retained(self):
        g = self.graph()
        lower = D['variables/P1ae/lower']
        g.set((lower, S.hasValue, Literal('+0001', datatype=XSD.integer, normalize=False)))
        hash_node = D['records/R-P1a/source-hash']
        g.set((hash_node, S.hasValue, Literal('f' * 64, datatype=XSD.string)))
        location = D['records/R-P1a/source-location']
        g.set((location, S.hasValue, Literal('external://study/record-37', datatype=XSD.string)))
        snapshot = br.prepare_graph(g)
        self.assertEqual(snapshot.variables['P1ae']['lower_us'], 1)
        bound = snapshot.edge_evidence['bound:P1ae:lower']['rdf']
        self.assertEqual(bound['lower']['lexical'], '+0001')
        self.assertEqual(bound['lower']['datum'], str(lower))
        evidence = snapshot.evidence['P1a']
        self.assertEqual(evidence['source_hash'], 'f' * 64)
        self.assertEqual(evidence['provenance']['source_location'], 'external://study/record-37')
        self.assertEqual(evidence['provenance']['hash_verification'], 'preserved_declaration_original_rows_not_available')
        parsed = Graph().parse(data=ei.turtle_text(g), format='turtle')
        self.assertEqual(snapshot.context_id, br.prepare_graph(parsed).context_id)

    def test_source_graph_mutation_does_not_mutate_prepared_snapshot(self):
        g = self.graph(); snapshot = br.prepare_graph(g)
        expected = ei.turtle_text(snapshot.graph)
        g.remove((None, None, None))
        self.assertEqual(expected, ei.turtle_text(snapshot.graph))

    def test_inverse_role_feature_and_explicit_named_superclasses(self):
        g = self.graph()
        role, person, process = D['events/P1a/patient-role'], D['persons/P1'], D['events/P1a']
        g.remove((role, S.isFeatureOf, person)); g.add((person, S.hasFeature, role))
        g.add((process, RDF.type, bt.BT.IntervalProcess)); g.add((process, RDF.type, S.Process))
        self.assertEqual(outcomes(cohort.execute(br.prepare_graph(g), self.query)),
                         outcomes(cohort.execute(br.prepare_graph(self.graph()), self.query)))

    def test_missing_or_ambiguous_profile_and_snapshot(self):
        g = self.graph(); g.remove((D['snapshot/rdf-identifier'], S.hasValue, None))
        self.bad(g, 'RDF_CARDINALITY')
        g = self.graph(); g.set((D['snapshot/rdf-identifier'], S.hasValue, Literal('other', datatype=XSD.string)))
        self.bad(g, 'UNSUPPORTED_RDF_PROFILE')
        g = self.graph(); g.add((URIRef('urn:other'), RDF.type, bt.BT.Snapshot))
        self.bad(g, 'SNAPSHOT_CARDINALITY')
        self.bad(bt.prepare(self.source).graph, 'RDF_PART_CARDINALITY')

    def test_explicit_identifiers_required_and_unique(self):
        for a, b, code in [('variables/P1as/rdf-identifier', 'variables/P1ae/rdf-identifier', 'DUPLICATE_VARIABLE'),
                           ('constraints/ae-upper/rdf-identifier', 'constraints/ae-lower/rdf-identifier', 'DUPLICATE_CONSTRAINT'),
                           ('clocks/common/clock_id', 'clocks/other/clock_id', 'DUPLICATE_CLOCK'),
                           ('records/R-P1a/id', 'records/R-P1b/id', 'DUPLICATE_EVENT')]:
            g = self.graph(); g.set((D[b], S.hasValue, next(g.objects(D[a], S.hasValue))))
            with self.subTest(code=code): self.bad(g, code)
        g = self.graph(); g.remove((D['variables/P1as/rdf-identifier'], S.hasValue, None))
        self.bad(g, 'RDF_CARDINALITY')

    def test_generic_participation_does_not_bind_patient(self):
        g = self.graph(); g.set((D['events/P1a'], S.hasParticipant, D['persons/P1']))
        self.bad(g, 'UNSUPPORTED_OR_CONFLICTING_TYPES')
        g = self.graph(); g.set((D['events/P1a/patient-role'], RDF.type, ei.EX.CareProviderRole))
        self.bad(g, 'UNSUPPORTED_OR_CONFLICTING_TYPES')

    def test_role_and_bearer_identity_constraints(self):
        g = self.graph(); g.set((D['events/P1b'], S.hasParticipant, D['events/P1a/patient-role']))
        self.bad(g, 'REUSED_DESCRIPTOR_OR_WITNESS')
        g = self.graph(); g.add((D['events/P1a/patient-role'], S.isFeatureOf, D['persons/P2']))
        self.bad(g, 'PATIENT_BEARER_CARDINALITY')
        g = self.graph(); g.set((D['persons/P2/id'], S.hasValue, Literal('P1', datatype=XSD.string)))
        self.bad(g, 'PATIENT_IDENTIFIER_COLLISION')

    def test_descriptor_and_interval_reuse_rejected(self):
        for parent, predicate, target in [('events/P1b', S.atTime, 'events/P1a/interval'),
                                           ('events/P1b/interval', S.hasDirectPart, 'events/P1a/start')]:
            g = self.graph()
            if predicate == S.hasDirectPart: g.remove((D[parent], predicate, D['events/P1b/start']))
            else: g.remove((D[parent], predicate, None))
            g.add((D[parent], predicate, D[target]))
            self.bad(g, 'REUSED_DESCRIPTOR_OR_WITNESS')

    def test_shared_variable_distinct_boundary_descriptors(self):
        source = conformance.fixture([('a', 'infusion', (0, 1), (2, 3)), ('b', 'specimen_collection', (2, 3), (4, 5))])
        source['events'][1]['start_var'] = 'ae'
        snapshot = br.prepare_graph(br.export_source(source))
        query = deepcopy(self.query); query['constraints'][0]['operator'] = 'meets'
        self.assertEqual(cohort.execute(snapshot, query)['certain_patient_ids'], ['P'])
        self.assertNotEqual(snapshot.evidence['a']['end_descriptor'], snapshot.evidence['b']['start_descriptor'])
        self.assertEqual(snapshot.evidence['a']['end_variable'], snapshot.evidence['b']['start_variable'])

    def test_numeric_types_values_units_and_duplicate_scalar(self):
        with self.assertLogs('rdflib.term', level='WARNING'):
            malformed = Literal('bad', datatype=XSD.integer, normalize=False)
        for literal in [Literal('1.0', datatype=XSD.decimal), malformed,
                        Literal(str(2**63), datatype=XSD.integer, normalize=False)]:
            g = self.graph(); g.set((D['variables/P1ae/lower'], S.hasValue, literal))
            self.bad(g, 'RDF_DATATYPE_MISMATCH|INVALID_INTEGER_LITERAL|INTEGER_MICROSECONDS_REQUIRED')
        g = self.graph(); g.add((D['variables/P1ae/lower'], S.hasValue, Literal('2', datatype=XSD.integer)))
        self.bad(g, 'RDF_CARDINALITY')
        g = self.graph(); g.set((D['variables/P1ae/lower'], S.hasDirectPart, ei.EI.Second))
        self.bad(g, 'UNSUPPORTED_UNIT')
        g = self.graph(); g.remove((D['variables/P1ae/lower'], S.hasDirectPart, None))
        self.bad(g, 'RDF_CARDINALITY')

    def test_missing_or_multiple_endpoints_and_operand_bindings(self):
        g = self.graph(); g.remove((D['events/P1a/interval'], S.hasDirectPart, D['events/P1a/end']))
        self.bad(g, 'RDF_PART_CARDINALITY')
        g = self.graph(); g.add((D['events/P1a/interval'], S.hasDirectPart, D['events/P1b/end']))
        self.bad(g, 'RDF_PART_CARDINALITY')
        g = self.graph(); g.remove((D['constraints/ae-upper/left_var'], S.refersTo, None))
        self.bad(g, 'RDF_CARDINALITY')

    def test_unknown_variables_clocks_and_clock_scope(self):
        for node in ['events/P1a/start', 'constraints/ae-upper/left_var']:
            g = self.graph(); g.set((D[node], S.refersTo, URIRef('urn:missing')))
            self.bad(g, 'UNKNOWN_VARIABLE_REFERENCE')
        g = self.graph(); g.set((D['variables/P1as/clock'], S.refersTo, URIRef('urn:missing')))
        self.bad(g, 'UNKNOWN_CLOCK_REFERENCE')
        g = self.graph(); g.set((D['clocks/common/scope'], S.hasValue, Literal('patient:P1', datatype=XSD.string)))
        self.bad(g, 'CLOCK_PATIENT_SCOPE_MISMATCH')
        g = self.graph(); g.set((D['variables/P1as/clock'], S.refersTo, D['clocks/other']))
        self.bad(g, 'CROSS_SCOPE_SOURCE_CONSTRAINT|EVENT_VARIABLE_SCOPE_MISMATCH')

    def test_missing_records_or_provenance_fail_before_matching(self):
        g = self.graph(); g.remove((D['records/R-P1a'], S.refersTo, None))
        self.bad(g, 'SOURCE_RECORD_CARDINALITY')
        for node in ['records/R-P1a/source-hash', 'variables/P1as/source-hash', 'constraints/ae-upper/source-hash']:
            g = self.graph(); g.set((D[node], S.hasValue, Literal('invalid', datatype=XSD.string)))
            self.bad(g, 'INVALID_SOURCE_HASH')

    def test_unknown_properties_classes_blank_nodes_and_extra_data(self):
        additions = [(D['events/P1a'], URIRef('urn:hasPatient'), D['persons/P1']),
                     (D['events/P1a'], OWL.sameAs, D['events/P1b']),
                     (D['events/P1a'], RDF.type, ei.S.Object),
                     (URIRef('urn:orphan'), S.hasValue, Literal('extra')),
                     (BNode(), RDF.type, bt.BT.TemporalVariable),
                     (D['events/P1a'], URIRef('urn:value'), Literal('extra')),
                     (D['events/P1a/start'], S.hasValue, Literal('2026-09-02T00:00:00Z', datatype=XSD.dateTimeStamp))]
        for triple in additions:
            g = self.graph(); g.add(triple)
            with self.subTest(triple=triple): self.bad(g, 'UNCONSUMED_RDF_TRIPLES|UNSUPPORTED_OR_CONFLICTING_TYPES|NAMED_RDF_REQUIRED|SOLID_LITERAL_PROPERTY')

    def test_snapshot_membership_is_complete(self):
        for node in ['variables/P1as', 'constraints/ae-upper']:
            g = self.graph(); g.remove((D.snapshot, S.hasDirectPart, D[node]))
            self.bad(g, 'SNAPSHOT_MEMBERSHIP')

    def test_inconsistency_certificate_keeps_rdf_evidence(self):
        g = self.graph(); g.set((D['constraints/ae-upper/upper'], S.hasValue, Literal('0', datatype=XSD.integer)))
        with self.assertRaises(bt.InconsistentSource) as caught: br.prepare_graph(g)
        error = caught.exception.details
        self.assertEqual(error['rdf_context']['graph_sha256'], ei.digest(ei.turtle_text(g)))
        self.assertEqual(error['edge_evidence']['source:ae-upper']['rdf']['constraint'], str(D['constraints/ae-upper']))
        conformance.BoundedTests().verify_path(error['negative_cycle'], [cohort.Edge(**e) for e in error['edges']], cycle=True)

    def test_class_only_extension_and_context_changes(self):
        ontology = Graph().parse(br.PROFILE)
        self.assertFalse(list(ontology.subjects(RDF.type, OWL.ObjectProperty)))
        self.assertFalse(list(ontology.subjects(RDF.type, OWL.DatatypeProperty)))
        original = br.prepare_graph(self.graph())
        g = self.graph(); g.set((D['snapshot/snapshot'], S.hasValue, Literal('new-snapshot', datatype=XSD.string)))
        changed = br.prepare_graph(g)
        self.assertNotEqual(original.context_id, changed.context_id)
        self.assertNotEqual(original.evidence['P1a']['evidence_id'], changed.evidence['P1a']['evidence_id'])

    def test_cli_source_graph_parity_and_bad_turtle(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            a, b = output / 'source', output / 'rdf'
            first = subprocess.run([sys.executable, '-m', 'patterns.bounded_rdf', '--output', str(a)], capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr)
            second = subprocess.run([sys.executable, '-m', 'patterns.bounded_rdf', '--graph', str(a / 'graph.ttl'), '--output', str(b)], capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual((a / 'result.json').read_text(), (b / 'result.json').read_text())
            bad = output / 'bad.ttl'; bad.write_text('not valid Turtle')
            third = subprocess.run([sys.executable, '-m', 'patterns.bounded_rdf', '--graph', str(bad), '--output', str(b)], capture_output=True, text=True)
            self.assertEqual(third.returncode, 2)
            self.assertEqual(json.loads(third.stderr)['status'], 'INVALID_INPUT')
            self.assertEqual((a / 'result.json').read_text(), (b / 'result.json').read_text())


if __name__ == '__main__':
    unittest.main()
