"""Canonical builder conversion and whole-conjunction execution regressions."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from app import journey, pattern_builder as builder


class PatternCompilerTests(unittest.TestCase):
    def setUp(self):
        self.pattern = deepcopy(builder.DEFAULT_PATTERN)

    def test_all_operators_roundtrip(self):
        for op in builder.OPERATORS:
            constraint = {'id': 'test', 'operator': op, 'left': 'infusion', 'right': 'collection'}
            if op == 'duration':
                constraint = {'id': 'test', 'operator': op, 'slot': 'infusion'}
            if op in ('gap', 'duration'):
                constraint.update(minimum_minutes='0.000001', maximum_minutes='1.250000')
            elif op == 'minimum_overlap':
                constraint['minimum_minutes'] = '0.000001'
            pattern = {**self.pattern, 'constraints': [constraint]}
            with self.subTest(operator=op):
                query = builder.compile_pattern(pattern)
                restored = builder.decompile_query(query)
                self.assertEqual(builder.compile_pattern(restored), query)
                if op in ('gap', 'duration'):
                    self.assertEqual(restored['constraints'][0]['maximum_minutes'], '1.25')

    def test_exact_signed_minutes_and_negative_zero(self):
        self.pattern['constraints'][1].update(minimum_minutes='-0.000001', maximum_minutes='-0.000000')
        query = builder.compile_pattern(self.pattern)
        self.assertEqual((query['constraints'][1]['min_gap_us'], query['constraints'][1]['max_gap_us']), (-60, 0))
        self.assertEqual(builder.decompile_query(query)['constraints'][1]['maximum_minutes'], '0')

    def test_slot_reordering_removal_and_type_changes(self):
        original = builder.compile_pattern(self.pattern)
        self.pattern['slots'].reverse()
        reordered = builder.compile_pattern(self.pattern)
        self.assertEqual(original['constraints'], reordered['constraints'])
        self.assertEqual(original['slots'], list(reversed(reordered['slots'])))
        self.pattern['slots'] = self.pattern['slots'][1:]
        self.pattern['constraints'] = self.pattern['constraints'][:1]
        self.pattern['slots'][0]['event_kind'] = 'recorded_input_segment'
        query = builder.compile_pattern(self.pattern)
        self.assertEqual(len(query['slots']), 2)
        self.assertIn('RecordedInputSegment', query['slots'][0]['class_iri'])

    def test_invalid_controls(self):
        invalid = []
        for slots in ([], self.pattern['slots'][:1], self.pattern['slots'] * 2):
            invalid.append({**self.pattern, 'slots': slots})
        for constraints in ([], self.pattern['constraints'] * 4):
            invalid.append({**self.pattern, 'constraints': constraints})
        for field, value in [('id', '<script>'), ('id', 'x'*33), ('event_kind', 'arbitrary'), ('id', [])]:
            candidate = deepcopy(self.pattern); candidate['slots'][0][field] = value; invalid.append(candidate)
        for field, value in [('left', 'missing'), ('right', 'infusion'), ('operator', []), ('id', 'followup-gap')]:
            candidate = deepcopy(self.pattern); candidate['constraints'][0][field] = value; invalid.append(candidate)
        candidate = deepcopy(self.pattern); candidate['slots'][1]['id'] = 'infusion'; invalid.append(candidate)
        for value in (True, 1.2, '1e2', '0.0000001', '1441', None):
            candidate = deepcopy(self.pattern); candidate['constraints'][1]['minimum_minutes'] = value; invalid.append(candidate)
        for candidate in (None, [], {**self.pattern, 'source': {}}, *invalid):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                builder.compile_pattern(candidate)

    def test_positive_duration_overlap_and_ordered_bounds(self):
        for op in ('duration', 'gap', 'minimum_overlap'):
            item = {'id': 'metric', 'operator': op, 'minimum_minutes': '0'}
            item.update({'slot': 'infusion'} if op == 'duration' else {'left': 'infusion', 'right': 'collection'})
            if op != 'minimum_overlap': item['maximum_minutes'] = '-1' if op == 'gap' else '10'
            with self.subTest(op=op), self.assertRaises(ValueError):
                builder.compile_pattern({**self.pattern, 'constraints': [item]})

    def test_import_rejects_lossy_or_unsupported_ast(self):
        query = builder.compile_pattern(self.pattern)
        invalid = []
        for field, value in [('id', 'different'), ('profile', 'different'), ('source', {}), ('slots', None)]:
            invalid.append({**query, field: value})
        for value in (1, True, 1.0, '60', -86400000060):
            candidate = deepcopy(query); candidate['constraints'][1]['min_gap_us'] = value; invalid.append(candidate)
        candidate = deepcopy(query); candidate['constraints'][0]['extra'] = True; invalid.append(candidate)
        for candidate in invalid:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                builder.decompile_query(candidate)


class ConfigurableJourneyTests(unittest.TestCase):
    def setUp(self):
        self.workspace = journey.JourneyWorkspace()
        self.request = {**journey.DEFAULT_REQUEST, 'question': 'custom', 'budget': '0',
                        'pattern': deepcopy(builder.DEFAULT_PATTERN)}

    def test_three_event_distinct_bindings_export_and_replay(self):
        report = self.workspace.run(self.request)
        self.assertEqual({p['patient_id']: p['original_status'] for p in report['patients']},
                         {'T01': 'CERTAIN', 'T02': 'POSSIBLE', 'T04': 'INCOMPARABLE'})
        self.assertEqual(report['query'], builder.compile_pattern(report['pattern']))
        self.assertEqual(report['workload']['candidate_bindings_per_evaluation'], 12)
        self.assertEqual(len(report['relaxation']['evaluations']), 1)
        for trajectory in report['result']['trajectories']:
            self.assertEqual(len(trajectory['bindings']), 2)
            for binding in trajectory['bindings']:
                ids = [row['event_id'] for row in binding['slots'].values()]
                self.assertEqual(len(set(ids)), 3)
        self.assertTrue(journey.verify(self.workspace.export(report['report_id']))['verified'])

    def test_whole_conjunction_cannot_switch_named_binding(self):
        # There is a collection during the infusion and another after it, but no
        # one collection satisfies both; existential witnesses cannot be mixed.
        self.request['pattern']['slots'] = self.request['pattern']['slots'][:2]
        self.request['pattern']['constraints'] = [
            {'id': 'inside', 'operator': 'contains', 'left': 'infusion', 'right': 'collection'},
            {'id': 'later', 'operator': 'before', 'left': 'infusion', 'right': 'collection'}]
        report = self.workspace.run(self.request)
        self.assertEqual(report['result']['possible_patient_ids'], [])
        t01 = next(t for t in report['result']['trajectories'] if t['patient_id'] == 'T01')
        self.assertTrue(all(b['status'] == 'IMPOSSIBLE' for b in t01['bindings']))

    def test_edit_source_invariance_top_k_and_exclusion(self):
        original = self.workspace.run(self.request)
        self.request['top_k'] = 3
        reordered = deepcopy(self.request['pattern']); reordered['slots'].reverse()
        self.request['pattern'] = reordered
        changed = self.workspace.run(self.request)
        self.assertEqual(original['source'], changed['source'])
        self.assertEqual(original['admitted_source_sha256'], changed['admitted_source_sha256'])
        self.assertEqual(original['patients'], changed['patients'])
        self.request['maximum_baseline'] = 50
        filtered = self.workspace.run(self.request)
        self.assertEqual([r['patient_id'] for r in filtered['patients']], ['T02'])
        self.assertEqual(filtered['admitted_source_sha256'], original['admitted_source_sha256'])

    def test_absent_type_and_impossible_distinct_bindings_are_empty_matches(self):
        for kind in ('recorded_input_segment', 'specimen_collection'):
            self.request['pattern']['slots'][0]['event_kind'] = kind
            report = self.workspace.run(self.request)
            self.assertEqual(report['result']['possible_patient_ids'], [])
            self.assertEqual([r['original_status'] for r in report['patients']], ['NO_RECORDED_MATCH']*3)
            self.assertTrue(all(not row['bindings'] for row in report['result']['trajectories']))

    def test_invalid_request_or_workload_rejected_before_prepare(self):
        for update in ({'budget': '1.25'}, {'pattern': None}, {'question': 'overlap'}):
            with patch.object(journey.bt, 'prepare', side_effect=AssertionError('must validate first')):
                with self.subTest(update=update), self.assertRaises(ValueError):
                    self.workspace.run({**self.request, **update})
        self.request['pattern']['constraints'][0]['left'] = 'missing'
        with patch.object(journey.bt, 'prepare', side_effect=AssertionError('must validate first')):
            with self.assertRaises(ValueError): self.workspace.run(self.request)
        self.request['pattern'] = deepcopy(builder.DEFAULT_PATTERN)
        with patch.dict(journey.LIMITS, {'events': 1}), patch.object(journey.bt, 'prepare', side_effect=AssertionError('must reject bounds first')):
            with self.assertRaisesRegex(ValueError, 'limits'): self.workspace.run(self.request)

    def test_existing_fixture_is_extended_without_rewriting_evidence(self):
        original = journey.json.loads((journey.ROOT / 'examples/interval-editor/overlap-source.json').read_text())
        for field in ('events', 'variables', 'clocks'):
            self.assertTrue(all(item in self.workspace._source[field] for item in original[field]))
        self.assertEqual(len(self.workspace._source['events']), 12)
        self.assertEqual(len(self.workspace._source['variables']), 24)


if __name__ == '__main__':
    unittest.main()
