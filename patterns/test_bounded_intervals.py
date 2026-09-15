"""Finite-world, certificate, representation, and cohort conformance checks."""
from copy import deepcopy
from dataclasses import asdict
from itertools import product
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from rdflib import Graph, Literal, OWL, RDF, RDFS, URIRef
from rdflib.compare import isomorphic

from . import bounded_intervals as bt
from . import bounded_cohort as cohort
from . import bounded_reference as reference
from . import exact_intervals as ei
from .temporal_stn import Edge, Network, ZERO, satisfies

EXAMPLE = ei.ROOT / 'examples/bounded-interval'


def fixture(events, constraints=()):
    """Small single-patient source. Events: (id, kind, start bounds, end bounds)."""
    source = {'profile': bt.PROFILE_ID, 'dataset_id': 'test', 'snapshot_id': 'one',
              'clocks': [{'clock_id': 'c', 'origin': '2026-09-02T00:00:00Z', 'scope': 'patient:P', 'policy': ei.POLICY}],
              'variables': [], 'events': [], 'constraints': list(constraints)}
    for eid, kind, starts, ends in events:
        for suffix, (lower, upper) in [('s', starts), ('e', ends)]:
            source['variables'].append({'id': eid + suffix, 'lower_us': lower, 'upper_us': upper,
                                         'patient_id': 'P', 'episode_id': 'E', 'clock_id': 'c', 'source_key': 'test:' + eid + suffix})
        source['events'].append({'id': eid, 'record_id': 'R-' + eid, 'patient_id': 'P', 'episode_id': 'E',
                                 'event_kind': kind, 'status': 'performed', 'start_var': eid + 's', 'end_var': eid + 'e', 'source_key': 'test:' + eid})
    return source


def constraint(cid, left, right, upper):
    return {'id': cid, 'left_var': left, 'right_var': right, 'upper_us': upper, 'source_key': 'test:' + cid}


class BoundedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((EXAMPLE / 'source.json').read_text())
        cls.query = json.loads((EXAMPLE / 'query.json').read_text())
        cls.snapshot = bt.prepare(cls.source)

    def verify_path(self, path, edges, left=None, right=None, upper=None, cycle=False):
        by_id = {e.id: e for e in edges}
        if not path:
            self.assertFalse(cycle)
            self.assertEqual(left, right)
            self.assertGreaterEqual(upper, 0)
            return
        walked = [by_id[key] for key in path]
        for a, b in zip(walked, walked[1:]):
            self.assertEqual(a.left, b.right)
        weight = sum(e.upper for e in walked)
        if cycle:
            self.assertEqual(walked[0].right, walked[-1].left)
            self.assertLess(weight, 0)
        else:
            self.assertEqual(walked[0].right, right)
            self.assertEqual(walked[-1].left, left)
            self.assertEqual(weight, upper)

    def verify_result(self, snapshot, query, result):
        for trajectory in result['trajectories']:
            scope = trajectory['patient_id'], trajectory['episode_id']
            worlds = reference.worlds(snapshot.source, scope)
            source_edges = snapshot.networks[scope].edges
            for answer in trajectory['bindings']:
                binding = {name: snapshot.events[slot['event_id']] for name, slot in answer['slots'].items()}
                self.assertEqual(answer['status'], reference.classify(snapshot.source, binding, query['constraints'], worlds))
                query_edges = [Edge(**e) for e in answer['query_edges']]
                if answer['status'] == 'IMPOSSIBLE':
                    self.verify_path(answer['negative_cycle'], list(source_edges) + query_edges, cycle=True)
                else:
                    witness = answer.get('possible_witness', answer.get('comparable_part_witness'))
                    self.assertIn(witness, worlds)
                    self.assertTrue(all(satisfies(witness, e) for e in query_edges))
                if answer['status'] == 'CERTAIN':
                    for proof in answer['entailment_proofs']:
                        edge = next(e for e in query_edges if e.id == proof['query_edge_id'])
                        self.verify_path(proof['source_path'], source_edges, edge.left, edge.right, proof['entailed_upper_us'])
                        self.assertLessEqual(proof['entailed_upper_us'], edge.upper)
                if answer['status'] == 'POSSIBLE':
                    self.assertIn(answer['counterexample'], worlds)
                    self.assertFalse(all(reference.holds(binding, c, answer['counterexample']) for c in query['constraints']))

    def test_four_independent_outcomes_and_certificates(self):
        result = cohort.execute(self.snapshot, self.query)
        self.assertEqual(result['certain_patient_ids'], ['P1'])
        self.assertEqual(result['possible_patient_ids'], ['P1', 'P2'])
        self.assertEqual([t['status'] for t in result['trajectories']],
                         ['CERTAIN_MATCH', 'POSSIBLE_MATCH', 'NO_RECORDED_MATCH', 'INCOMPARABLE'])
        self.verify_result(self.snapshot, self.query, result)

    def test_shared_anchor_proves_gap_that_marginals_do_not(self):
        query = deepcopy(self.query)
        query['constraints'][0].update(operator='gap', min_gap_us=2, max_gap_us=2)
        result = cohort.execute(self.snapshot, query)
        self.assertEqual(result['trajectories'][0]['bindings'][0]['status'], 'CERTAIN')
        source = deepcopy(self.source); source['constraints'] = []
        uncorrelated = cohort.execute(bt.prepare(source), query)
        self.assertEqual(uncorrelated['trajectories'][0]['bindings'][0]['status'], 'POSSIBLE')
        self.verify_result(self.snapshot, query, result)

    def test_individually_possible_atoms_jointly_impossible(self):
        source = fixture([('a', 'infusion', (0, 0), (1, 1)), ('b', 'specimen_collection', (1, 2), (3, 3))])
        snapshot = bt.prepare(source)
        query = deepcopy(self.query)
        for op in ('before', 'meets'):
            query['constraints'][0]['operator'] = op
            self.assertEqual(cohort.execute(snapshot, query)['trajectories'][0]['bindings'][0]['status'], 'POSSIBLE')
        query['constraints'].append({'id': 'also_before', 'left': 'a', 'right': 'b', 'operator': 'before'})
        result = cohort.execute(snapshot, query)
        self.assertEqual(result['trajectories'][0]['bindings'][0]['status'], 'IMPOSSIBLE')
        self.verify_result(snapshot, query, result)

    def test_fixed_witness_quantifier_order(self):
        source = fixture([('a1', 'infusion', (0, 0), (1, 1)), ('a2', 'infusion', (1, 1), (2, 2)),
                          ('b', 'specimen_collection', (1, 2), (2, 3))],
                         [constraint('duration-upper', 'be', 'bs', 1), constraint('duration-lower', 'bs', 'be', -1)])
        snapshot = bt.prepare(source)
        query = deepcopy(self.query); query['constraints'][0]['operator'] = 'meets'
        result = cohort.execute(snapshot, query)
        self.assertEqual(result['certain_patient_ids'], [])
        self.assertEqual(result['possible_patient_ids'], ['P'])
        self.assertEqual([b['status'] for b in result['trajectories'][0]['bindings']], ['POSSIBLE', 'POSSIBLE'])
        worlds = reference.worlds(source, ('P', 'E'))
        self.assertTrue(all(any(reference.holds({'a': snapshot.events[e], 'b': snapshot.events['b']}, query['constraints'][0], w)
                                for e in ('a1', 'a2')) for w in worlds))
        self.verify_result(snapshot, query, result)

    def test_three_pairwise_possible_edges_form_impossible_cycle(self):
        source = fixture([(name, 'infusion', (0, 3), (1, 4)) for name in ('a', 'b', 'c')])
        snapshot = bt.prepare(source)
        edges = [{'id': a + b, 'left': a, 'right': b, 'operator': 'before'}
                 for a, b in [('a', 'b'), ('b', 'c'), ('c', 'a')]]
        network = snapshot.networks[('P', 'E')]
        for edge in edges:
            compiled, unknown = cohort.query_edges(snapshot.events, [edge], snapshot.variables)
            self.assertEqual(cohort.classify(network, compiled, unknown)['status'], 'POSSIBLE')
        compiled, unknown = cohort.query_edges(snapshot.events, edges, snapshot.variables)
        answer = cohort.classify(network, compiled, unknown)
        self.assertEqual(answer['status'], 'IMPOSSIBLE')
        self.verify_path(answer['negative_cycle'], list(network.edges) + compiled, cycle=True)

    def test_unconstrained_required_distinct_slots_with_subclass_selection(self):
        source = fixture([(name, 'infusion', (0, 1), (2, 3)) for name in ('a', 'b', 'c')])
        query = deepcopy(self.query); query['constraints'] = []
        for slot in query['slots']: slot['class_iri'] = str(ei.S.Process)
        snapshot = bt.prepare(source)
        result = cohort.execute(snapshot, query)
        self.assertEqual(len(result['trajectories'][0]['bindings']), 6)
        self.assertTrue(all(b['status'] == 'CERTAIN' for b in result['trajectories'][0]['bindings']))
        self.verify_result(snapshot, query, result)

    def test_wide_correlated_domains_do_not_require_world_enumeration(self):
        source = deepcopy(self.source)
        for variable in source['variables']:
            if variable['patient_id'] == 'P1': variable['upper_us'] += 10**15
        result = cohort.execute(bt.prepare(source), self.query)
        self.assertEqual(result['trajectories'][0]['bindings'][0]['status'], 'CERTAIN')

    def test_inconsistent_source_is_not_vacuous_certainty(self):
        source = deepcopy(self.source)
        source['constraints'].append(constraint('contradiction', 'P1ae', 'P1as', 0))
        with self.assertRaises(bt.InconsistentSource) as caught:
            bt.prepare(source)
        details = caught.exception.details
        self.verify_path(details['negative_cycle'], [Edge(**e) for e in details['edges']], cycle=True)
        self.assertEqual(details['status'], 'INCONSISTENT_SOURCE')

    def test_same_variable_cannot_form_proper_interval(self):
        source = deepcopy(self.source)
        source['events'][0]['end_var'] = source['events'][0]['start_var']
        with self.assertRaises(bt.InconsistentSource): bt.prepare(source)

    def test_shared_variable_preserves_identity_across_descriptors(self):
        source = fixture([('a', 'infusion', (0, 1), (2, 3)), ('b', 'specimen_collection', (2, 3), (4, 5))])
        source['events'][1]['start_var'] = 'ae'
        snapshot = bt.prepare(source)
        query = deepcopy(self.query); query['constraints'][0]['operator'] = 'meets'
        result = cohort.execute(snapshot, query)
        self.assertEqual(result['trajectories'][0]['bindings'][0]['status'], 'CERTAIN')
        self.assertNotEqual(snapshot.evidence['a']['end_descriptor'], snapshot.evidence['b']['start_descriptor'])
        self.assertEqual(snapshot.evidence['a']['end_variable'], snapshot.evidence['b']['start_variable'])
        self.verify_result(snapshot, query, result)

    def test_singleton_domains_agree_with_exact_evaluator(self):
        clock = ei.Clock('urn:clock', 'c', '2026-09-02T00:00:00Z', 'global', ei.POLICY)
        for s, e, u, v in product(range(4), repeat=4):
            if s >= e or u >= v: continue
            source = fixture([('a', 'infusion', (s, s), (e, e)), ('b', 'specimen_collection', (u, u), (v, v))])
            snapshot = bt.prepare(source)
            a = ei.ExactInterval('a', 'urn:a', 'urn:ar', 'urn:p', 'P', 'E', s, e, clock, 'ctx', 'ae')
            b = ei.ExactInterval('b', 'urn:b', 'urn:br', 'urn:p', 'P', 'E', u, v, clock, 'ctx', 'be')
            for op in ('before', 'meets', 'overlaps', 'gap'):
                query = deepcopy(self.query); query['constraints'][0]['operator'] = op
                bounds = {'min_gap_us': 0, 'max_gap_us': 2} if op == 'gap' else {}
                query['constraints'][0].update(bounds)
                edges, unknown = cohort.query_edges({'a': snapshot.events['a'], 'b': snapshot.events['b']}, query['constraints'], snapshot.variables)
                actual = cohort.classify(snapshot.networks[('P', 'E')], edges, unknown)['status']
                expected = 'CERTAIN' if ei.evaluate(a, b, op, **bounds)['status'] == 'SATISFIED' else 'IMPOSSIBLE'
                self.assertEqual(actual, expected)

    def test_graph_uses_pro_solid_and_no_sampled_boundaries(self):
        graph = self.snapshot.graph
        ontology = ei.base.ontology() + Graph().parse(ei.PROFILE) + Graph().parse(ei.ROOT / 'ontology/bounded-interval-profile.ttl')
        extension = Graph().parse(ei.ROOT / 'ontology/bounded-interval-profile.ttl')
        self.assertFalse(list(extension.subjects(RDF.type, OWL.ObjectProperty)))
        self.assertFalse(list(extension.subjects(RDF.type, OWL.DatatypeProperty)))
        approved = {RDF.type, ei.S.hasDirectPart, ei.S.refersTo, ei.S.hasParticipant, ei.S.isFeatureOf, ei.S.hasFeature, ei.S.atTime, ei.S.hasValue}
        for s, p, o in graph:
            self.assertIsInstance(s, URIRef)
            self.assertIn(p, approved)
            if isinstance(o, Literal): self.assertEqual(p, ei.S.hasValue)
            else: self.assertIsInstance(o, URIRef)
            if p == RDF.type: self.assertIn((o, RDF.type, OWL.Class), ontology)
        for item in self.snapshot.evidence.values():
            self.assertIn((URIRef(item['process']), ei.S.hasParticipant, URIRef(item['patient_role'])), graph)
            self.assertIn((URIRef(item['patient_role']), ei.S.isFeatureOf, URIRef(item['patient_bearer'])), graph)
            for key in ('start_descriptor', 'end_descriptor', 'interval'):
                self.assertFalse(list(graph.objects(URIRef(item[key]), ei.S.hasValue)))
        serialized = ei.turtle_text(graph)
        self.assertTrue(isomorphic(graph, Graph().parse(data=serialized, format='turtle')))

    def test_source_and_query_validation(self):
        for field, value in [('lower_us', 0.0), ('lower_us', True), ('upper_us', 2**63), ('clock_id', 'missing')]:
            source = deepcopy(self.source); source['variables'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ei.ContractError): bt.prepare(source)
        for category, row in [('variables', self.source['variables'][0]), ('events', self.source['events'][0]), ('clocks', self.source['clocks'][0])]:
            source = deepcopy(self.source); source[category].append(row)
            with self.assertRaises(ei.ContractError): bt.prepare(source)
        for change in ({'start_var': 'missing'}, {'patient_id': 'different'}, {'status': 'planned'}):
            source = deepcopy(self.source); source['events'][0].update(change)
            with self.assertRaises(ei.ContractError): bt.prepare(source)
        for field, value in [('profile', 'interval-cohort-1.0'), ('limit', 2)]:
            query = deepcopy(self.query); query[field] = value
            with self.assertRaises(ei.ContractError): bt.validate(query, query=True)
        query = deepcopy(self.query); query['slots'][1]['id'] = 'a'
        with self.assertRaises(ei.ContractError): bt.validate(query, query=True)
        query = deepcopy(self.query); query['constraints'][0].update(operator='gap', min_gap_us=2, max_gap_us=1)
        with self.assertRaises(ei.ContractError): bt.validate(query, query=True)
        Draft202012Validator.check_schema(json.loads(bt.SCHEMA.read_text()))

    def test_cross_scope_source_constraints_rejected(self):
        for right in ('P2as', 'P4bs'):
            source = deepcopy(self.source)
            source['constraints'].append(constraint('cross', 'P4as', right, 0))
            with self.assertRaisesRegex(ei.ContractError, 'CROSS_SCOPE_SOURCE_CONSTRAINT'): bt.prepare(source)

    def test_no_cross_episode_binding(self):
        source = fixture([('a', 'infusion', (0, 0), (1, 1)), ('b', 'specimen_collection', (3, 3), (4, 4))])
        source['events'][1]['episode_id'] = 'other'
        for variable in source['variables'][2:]: variable['episode_id'] = 'other'
        result = cohort.execute(bt.prepare(source), self.query)
        self.assertEqual(result['possible_patient_ids'], [])
        self.assertEqual(len(result['trajectories']), 2)
        self.assertTrue(all(t['status'] == 'NO_RECORDED_MATCH' for t in result['trajectories']))

    def test_known_impossibility_dominates_incomparability(self):
        source = deepcopy(self.source)
        source['events'].append({**source['events'][-2], 'id': 'P4extra', 'record_id': 'R-P4extra'})
        query = deepcopy(self.query)
        query['slots'].append({'id': 'c', 'class_iri': str(bt.BT.Infusion)})
        query['constraints'].append({'id': 'impossible', 'left': 'a', 'right': 'c', 'operator': 'before'})
        result = cohort.execute(bt.prepare(source), query)
        self.assertEqual(result['trajectories'][-1]['status'], 'NO_RECORDED_MATCH')
        self.assertTrue(all(b['status'] == 'IMPOSSIBLE' for b in result['trajectories'][-1]['bindings']))

    def test_large_exact_integer_bounds_and_grid(self):
        for origin in (-2**63, 2**63 - 5):
            source = fixture([('a', 'infusion', (origin, origin), (origin + 1, origin + 1)),
                              ('b', 'specimen_collection', (origin + 2, origin + 2), (origin + 3, origin + 3))])
            result = cohort.execute(bt.prepare(source), self.query)
            self.assertEqual(result['trajectories'][0]['bindings'][0]['status'], 'CERTAIN')
        source = deepcopy(self.source); source['variables'][0]['lower_us'] = -.5
        with self.assertRaises(ei.ContractError): bt.prepare(source)

    def test_reference_cap_is_failure_not_partial_result(self):
        source = deepcopy(self.source); source['variables'][0]['upper_us'] = 1000000
        with self.assertRaisesRegex(ValueError, 'REFERENCE_ENUMERATION_LIMIT'): reference.worlds(source, ('P1', 'E1'))

    def test_context_changes_with_source_and_query(self):
        source = deepcopy(self.source); source['snapshot_id'] = 'changed'
        self.assertNotEqual(self.snapshot.context_id, bt.prepare(source).context_id)
        source = deepcopy(self.source); source['constraints'][0]['source_key'] = 'changed'
        self.assertNotEqual(self.snapshot.context_id, bt.prepare(source).context_id)
        first = cohort.execute(self.snapshot, self.query)
        self.assertEqual(first, cohort.execute(bt.prepare(deepcopy(self.source)), deepcopy(self.query)))
        query = deepcopy(self.query); query['constraints'][0]['operator'] = 'meets'
        self.assertNotEqual(first['context_id'], cohort.execute(self.snapshot, query)['context_id'])

    def test_randomized_networks_against_finite_worlds(self):
        rng = random.Random(2701)
        for trial in range(250):
            names = ['x', 'y', 'z']
            edges = [e for name in names for e in (Edge(name + '-upper', name, ZERO, 2), Edge(name + '-lower', ZERO, name, 0))]
            for i in range(rng.randrange(1, 8)):
                a, b = rng.choices(names, k=2)
                edges.append(Edge('r' + str(i), a, b, rng.randrange(-3, 4)))
            worlds = [dict(zip(names, values)) for values in product(range(3), repeat=3)
                      if all(({**dict(zip(names, values)), ZERO: 0}[e.left] - {**dict(zip(names, values)), ZERO: 0}[e.right]) <= e.upper for e in edges)]
            network = Network(names, edges)
            with self.subTest(trial=trial):
                self.assertEqual(network.feasible, bool(worlds))
                if not worlds:
                    self.verify_path(network.negative_cycle, edges, cycle=True)
                else:
                    self.assertIn(network.witness(), worlds)
                    for a, b in product(names, repeat=2):
                        bound, path = network.bound(a, b)
                        self.assertEqual(bound, max(w[a] - w[b] for w in worlds))
                        self.verify_path(path, edges, a, b, bound)

    def test_randomized_query_conjunctions_against_worlds(self):
        rng = random.Random(3349)
        source = fixture([('a', 'infusion', (0, 2), (1, 3)), ('b', 'specimen_collection', (0, 2), (1, 3)),
                          ('c', 'infusion', (0, 2), (1, 3))])
        snapshot = bt.prepare(source)
        binding = snapshot.events
        worlds = reference.worlds(source, ('P', 'E'))
        for trial in range(200):
            constraints = []
            for i in range(rng.randrange(1, 5)):
                a, b = rng.sample(['a', 'b', 'c'], 2)
                op = rng.choice(['before', 'meets', 'overlaps', 'gap'])
                c = {'id': str(i), 'left': a, 'right': b, 'operator': op}
                if op == 'gap': c.update(min_gap_us=0, max_gap_us=rng.randrange(3))
                constraints.append(c)
            edges, unknown = cohort.query_edges(binding, constraints, snapshot.variables)
            actual = cohort.classify(snapshot.networks[('P', 'E')], edges, unknown)
            with self.subTest(trial=trial):
                self.assertEqual(actual['status'], reference.classify(source, binding, constraints, worlds))
                if actual['status'] == 'IMPOSSIBLE':
                    self.verify_path(actual['negative_cycle'], list(snapshot.networks[('P', 'E')].edges) + edges, cycle=True)
                else:
                    self.assertIn(actual['possible_witness'], worlds)
                    self.assertTrue(all(reference.holds(binding, c, actual['possible_witness']) for c in constraints))
                    if actual['status'] == 'POSSIBLE':
                        self.assertIn(actual['counterexample'], worlds)
                        self.assertFalse(all(reference.holds(binding, c, actual['counterexample']) for c in constraints))

    def test_cli_success_and_inconsistent_source(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / 'run'
            result = subprocess.run([sys.executable, '-m', 'patterns.bounded_cohort', '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((output / 'result.json').read_text())['certain_patient_ids'], ['P1'])
            self.assertTrue(isomorphic(self.snapshot.graph, Graph().parse(output / 'graph.ttl')))
            source = deepcopy(self.source); source['events'][0]['end_var'] = source['events'][0]['start_var']
            path = Path(folder) / 'bad.json'; path.write_text(json.dumps(source))
            result = subprocess.run([sys.executable, '-m', 'patterns.bounded_cohort', '--source', str(path), '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(result.stderr)['status'], 'INCONSISTENT_SOURCE')


if __name__ == '__main__':
    unittest.main()
