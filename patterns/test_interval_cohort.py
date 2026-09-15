"""Independent fixtures and differential search checks for interval-cohort-1.0."""
from copy import deepcopy
from dataclasses import replace
import json
import random
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator
from rdflib import Graph

from . import interval_cohort as cohort
from . import exact_intervals as ei

EXAMPLE = ei.ROOT / 'examples/interval-cohort'


def semantic(result):
    return {key: value for key, value in result.items() if key != 'execution'}


def keys(found):
    return sorted((tuple(sorted((name, r.event_id) for name, r in binding.items())), status)
                  for binding, status in found)


class CohortTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = json.loads((EXAMPLE / 'source-rows.json').read_text())
        cls.manifest = json.loads((EXAMPLE / 'manifest.json').read_text())
        cls.query = json.loads((EXAMPLE / 'query.json').read_text())
        cls.graph = cohort.build_graph(cls.source)
        cls.snapshot = cohort.prepare(cls.graph, cls.manifest)

    def run_query(self, query=None, snapshot=None):
        query, snapshot = query or self.query, snapshot or self.snapshot
        indexed = cohort.execute(snapshot, query)
        reference = cohort.execute(snapshot, query, engine='reference')
        self.assertEqual(semantic(indexed), semantic(reference))
        return indexed

    def test_independent_cohort_expectations(self):
        result = self.run_query()
        self.assertEqual(result['matched_patient_ids'], ['P1'])
        self.assertTrue(result['search_complete'])
        self.assertEqual([t['status'] for t in result['trajectories']], ['MATCH', 'NO_RECORDED_MATCH', 'INCOMPARABLE'])
        matches = result['trajectories'][0]['matches']
        self.assertEqual({tuple(m['slots'][s]['event_id'] for s in ('a', 'b')) for m in matches}, {('A', 'D'), ('C', 'D')})
        unresolved = result['trajectories'][2]['unresolved_bindings']
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0]['constraints'][0]['reason_codes'], ['CLOCK_MISMATCH'])

    def test_evidence_contains_pro_and_source_witnesses(self):
        result = self.run_query()
        for trajectory in result['trajectories']:
            for match in trajectory['matches'] + trajectory['unresolved_bindings']:
                for slot in match['slots'].values():
                    evidence = result['evidence']['bindings'][slot['evidence_id']]
                    for key in ('process', 'patient_role', 'patient_bearer'):
                        self.assertEqual(slot[key], evidence[key])
                    self.assertTrue(evidence['source_record'])
                    self.assertEqual(evidence['source_metadata']['status'], 'performed')
                    self.assertEqual(evidence['source_metadata']['source_file'],
                                     'constructed://interval-cohort/source-rows.json')

    def test_contact_and_gap_boundaries(self):
        for op, bounds, expected in [('meets', {}, {('A', 'B')}),
                                     ('gap', {'min_gap_us': 0, 'max_gap_us': 0}, {('A', 'B')}),
                                     ('gap', {'min_gap_us': 300000000, 'max_gap_us': 900000000}, {('A', 'D'), ('C', 'D')})]:
            query = deepcopy(self.query)
            query['constraints'][0].update(operator=op, **bounds)
            matches = self.run_query(query)['trajectories'][0]['matches']
            self.assertEqual({tuple(m['slots'][s]['event_id'] for s in ('a', 'b')) for m in matches}, expected)

    def test_directional_overlap_and_named_subclass_selection(self):
        query = deepcopy(self.query)
        query['slots'][1]['class_iri'] = str(ei.EI.IntervalProcess)
        query['constraints'][0]['operator'] = 'overlaps'
        matches = self.run_query(query)['trajectories'][0]['matches']
        # C contains B; containment is not directional Allen overlap.
        self.assertEqual({tuple(m['slots'][s]['event_id'] for s in ('a', 'b')) for m in matches}, {('A', 'C')})

    def test_unconstrained_slots_are_required_and_distinct(self):
        query = deepcopy(self.query)
        query['constraints'] = []
        for slot in query['slots']:
            slot['class_iri'] = str(ei.S.Process)
        result = self.run_query(query)
        self.assertEqual([len(t['matches']) for t in result['trajectories']], [12, 2, 2])
        # No clock comparison is needed without a temporal edge.
        self.assertEqual(result['matched_patient_ids'], ['P1', 'P2', 'P3'])

    def test_episode_separation_and_missing_class(self):
        source = deepcopy(self.source)
        for row in source['events']:
            row['episode_id'] = 'infusions' if row['event_kind'] == 'infusion' else 'collections'
        result = self.run_query(snapshot=cohort.prepare(ei.build_graph(source), self.manifest))
        self.assertEqual(len(result['trajectories']), 6)
        self.assertTrue(all(t['status'] == 'NO_RECORDED_MATCH' for t in result['trajectories']))

    def test_three_slot_named_witness(self):
        query = deepcopy(self.query)
        query['slots'].append({'id': 'c', 'class_iri': str(ei.EI.Infusion)})
        query['constraints'].append({'id': 'overlap', 'left': 'c', 'right': 'a', 'operator': 'overlaps'})
        result = self.run_query(query)
        self.assertEqual(result['matched_patient_ids'], ['P1'])
        matches = result['trajectories'][0]['matches']
        self.assertEqual(len(matches), 1)
        self.assertEqual({slot: value['event_id'] for slot, value in matches[0]['slots'].items()},
                         {'a': 'C', 'b': 'D', 'c': 'A'})

    def test_single_slot_has_no_implied_temporal_comparison(self):
        query = deepcopy(self.query)
        query['slots'] = query['slots'][:1]
        query['constraints'] = []
        result = self.run_query(query)
        self.assertEqual([len(t['matches']) for t in result['trajectories']], [2, 1, 1])
        self.assertTrue(all(t['status'] == 'MATCH' for t in result['trajectories']))

    def test_known_failure_dominates_incomparable_edge(self):
        query = deepcopy(self.query)
        query['slots'].append({'id': 'c', 'class_iri': str(ei.EI.IntervalProcess)})
        query['constraints'].append({'id': 'reverse', 'left': 'b', 'right': 'a', 'operator': 'before'})
        result = self.run_query(query)
        self.assertEqual(result['matched_patient_ids'], [])
        self.assertEqual(result['trajectories'][0]['status'], 'NO_RECORDED_MATCH')
        # Exercise false AND unknown with a third record under a separate clock.
        a, b = self.snapshot.intervals[:2]
        c = replace(a, event_id='third', process='urn:third', clock=replace(a.clock, clock_id='elsewhere'))
        candidates = {'a': [a], 'b': [b], 'c': [c]}
        constraints = [{'id': 'unknown', 'left': 'a', 'right': 'c', 'operator': 'before'},
                       {'id': 'false', 'left': 'b', 'right': 'a', 'operator': 'before'}]
        self.assertEqual(cohort.reference.search(candidates, constraints)[0], [])
        self.assertEqual(cohort.indexed_search(candidates, constraints)[0], [])

    def test_match_and_unresolved_can_coexist(self):
        source = deepcopy(self.source)
        row = next(r.copy() for r in source['events'] if r['event_id'] == 'D')
        row.update(event_id='extra', record_id='R-extra', clock_id='other-clock')
        source['events'].append(row)
        result = self.run_query(snapshot=cohort.prepare(ei.build_graph(source), self.manifest))
        p1 = result['trajectories'][0]
        self.assertEqual(p1['status'], 'MATCH')
        self.assertEqual(len(p1['matches']), 2)
        self.assertEqual(len(p1['unresolved_bindings']), 2)

    def test_invalid_queries(self):
        invalid = []
        q = deepcopy(self.query); q['profile'] = 'other'; invalid.append(q)
        q = deepcopy(self.query); q['limit'] = 1; invalid.append(q)
        q = deepcopy(self.query); q['slots'][0]['optional'] = True; invalid.append(q)
        q = deepcopy(self.query); q['slots'][0]['class_iri'] = 'urn:unknown'; invalid.append(q)
        q = deepcopy(self.query); q['slots'][1]['id'] = 'a'; invalid.append(q)
        q = deepcopy(self.query); q['constraints'][0]['left'] = 'missing'; invalid.append(q)
        q = deepcopy(self.query); q['constraints'][0]['right'] = 'a'; invalid.append(q)
        q = deepcopy(self.query); q['constraints'] *= 2; invalid.append(q)
        q = deepcopy(self.query); q['constraints'][0]['operator'] = 'during'; invalid.append(q)
        q = deepcopy(self.query); q['constraints'][0]['min_gap_us'] = 0; invalid.append(q)
        for low, high in [(0, None), (True, 1), (0.0, 1), (2, 1), (-1, 1), (0, 2**63)]:
            q = deepcopy(self.query)
            q['constraints'][0].update(operator='gap', min_gap_us=low, max_gap_us=high)
            invalid.append(q)
        for q in invalid:
            with self.subTest(query=q), self.assertRaises(ei.ContractError):
                cohort.validate_query(q)

    def test_schema_is_valid(self):
        Draft202012Validator.check_schema(json.loads(cohort.SCHEMA.read_text()))

    def test_rdf_roundtrip_and_determinism(self):
        rdf = Graph().parse(data=ei.turtle_text(self.graph), format='turtle')
        original = self.run_query()
        self.assertEqual(original, self.run_query(snapshot=cohort.prepare(rdf, self.manifest)))
        reversed_snapshot = cohort.PreparedSnapshot(list(reversed(self.snapshot.intervals)),
                                                   self.snapshot.evidence, self.snapshot.types)
        self.assertEqual(original, self.run_query(snapshot=reversed_snapshot))

    def test_context_tracks_query_and_snapshot(self):
        original = self.run_query()['context_id']
        q = deepcopy(self.query); q['constraints'][0]['operator'] = 'meets'
        self.assertNotEqual(original, self.run_query(q)['context_id'])
        manifest = {**self.manifest, 'snapshot_id': 'changed'}
        self.assertNotEqual(original, self.run_query(snapshot=cohort.prepare(self.graph, manifest))['context_id'])

    def test_span_overflow_fails_both_engines_before_search(self):
        records = list(self.snapshot.intervals)
        records[0] = replace(records[0], start_us=-2**63, end_us=-2**63 + 1)
        snapshot = cohort.PreparedSnapshot(records, self.snapshot.evidence, self.snapshot.types)
        for engine in ('reference', 'indexed'):
            with self.assertRaisesRegex(ei.ContractError, 'INTEGER_MICROSECONDS_REQUIRED'):
                cohort.execute(snapshot, self.query, engine=engine)

    def test_randomized_three_slot_differential(self):
        rng = random.Random(14329)
        template = self.snapshot.intervals[0]
        for trial in range(300):
            records = []
            for i in range(8):
                start = rng.randrange(-8, 9)
                records.append(replace(template, event_id=str(i), process='urn:event:' + str(i),
                                       start_us=start, end_us=start + rng.randrange(1, 8),
                                       clock=replace(template.clock, clock_id=str(rng.randrange(2)))))
            candidates = {name: [r for r in records if rng.random() < .7] for name in ('a', 'b', 'c')}
            constraints = []
            for i in range(rng.randrange(1, 5)):
                left, right = rng.sample(['a', 'b', 'c'], 2)
                op = rng.choice(['before', 'meets', 'overlaps', 'gap'])
                c = {'id': str(i), 'left': left, 'right': right, 'operator': op}
                if op == 'gap': c.update(min_gap_us=0, max_gap_us=rng.randrange(5))
                constraints.append(c)
            with self.subTest(trial=trial):
                self.assertEqual(keys(cohort.indexed_search(candidates, constraints)[0]),
                                 keys(cohort.reference.search(candidates, constraints)[0]))

    def test_range_index_reduces_search(self):
        template = self.snapshot.intervals[0]
        candidates = {name: [replace(template, event_id=name + str(i), process='urn:' + name + str(i),
                                     start_us=i * 10 + offset, end_us=i * 10 + offset + 1)
                             for i in range(100)] for name, offset in [('a', 0), ('b', 1)]}
        constraints = [{'id': 'touch', 'left': 'a', 'right': 'b', 'operator': 'meets'}]
        indexed, ix = cohort.indexed_search(candidates, constraints)
        reference, ref = cohort.reference.search(candidates, constraints)
        self.assertEqual(keys(indexed), keys(reference))
        self.assertEqual(len(indexed), 100)
        self.assertEqual(ref['complete_bindings_examined'], 10000)
        self.assertEqual(ix['complete_bindings_examined'], 100)
        self.assertEqual(ix['candidate_extensions'], 200)

    def test_cli_both_engines_and_invalid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            output = ei.ROOT / folder / 'result.json'
            results = []
            for engine in ('indexed', 'reference'):
                completed = subprocess.run([sys.executable, '-m', 'patterns.interval_cohort', '--engine', engine,
                                            '--output', str(output)], capture_output=True, text=True)
                self.assertEqual(completed.returncode, 0, completed.stderr)
                results.append(semantic(json.loads(output.read_text())))
            self.assertEqual(*results)
            rdf = output.with_name('source.ttl'); rdf.write_text(ei.turtle_text(self.graph))
            completed = subprocess.run([sys.executable, '-m', 'patterns.interval_cohort', '--graph', str(rdf),
                                        '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(results[0], semantic(json.loads(output.read_text())))
            bad = output.with_name('bad.json'); bad.write_text('{')
            completed = subprocess.run([sys.executable, '-m', 'patterns.interval_cohort', '--query', str(bad),
                                        '--output', str(output)], capture_output=True, text=True)
            self.assertEqual(completed.returncode, 2)
            self.assertEqual(json.loads(completed.stderr)['status'], 'INVALID_INPUT')


if __name__ == '__main__':
    unittest.main()
