"""Evidence lifecycle and bounded-matcher integration acceptance cases."""
from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

from . import bounded_reference as reference
from . import evidence_selection as selection
from . import exact_intervals as ei


def stamp(day):
    return f'2026-01-{day:02}T00:00:00Z'


def fixture():
    folder = ei.ROOT / 'examples/evidence-selection'
    return [json.loads((folder / (name + '.json')).read_text()) for name in ('archive', 'request', 'query')]


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.archive, self.request, self.query = fixture()

    def run_match(self):
        return selection.execute(self.archive, self.request, self.query)

    def select(self):
        return selection.select(self.archive, self.request)

    def correction(self):
        return self.archive['entries'][2]

    def test_cutoff_selects_original_and_preserves_repeated_event(self):
        result = self.run_match()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(result['selection']['selected_assertion_ids'], ['admin', 'collection_original'])
        self.assertEqual({r['id'] for r in result['selection']['source']['events']}, {'A', 'B'})
        self.assertEqual(result['selection']['archive'], self.archive)

    def test_retrospective_changes_evidence_and_membership(self):
        old = self.run_match()
        self.request['mode'] = 'retrospective'
        new = self.run_match()
        self.assertEqual(new['matching']['certain_patient_ids'], [])
        self.assertTrue(new['selection']['later_evidence_used'])
        self.assertNotEqual(old['context_id'], new['context_id'])
        self.assertEqual(new['selection']['selected_assertion_ids'], ['admin', 'collection_corrected'])

    def test_availability_equality_is_inclusive_and_offset_normalized(self):
        self.request['patient_cutoffs']['P1'] = '2026-01-04T01:00:00+01:00'
        result = self.select()
        self.assertIn('collection_corrected', result['selected_assertion_ids'])
        self.assertFalse(result['later_evidence_used'])

    def test_product_archive_cannot_contain_later_ingestion(self):
        self.archive['published_at'] = stamp(5)
        with self.assertRaisesRegex(ei.ContractError, 'ENTRY_AFTER_ARCHIVE'):
            self.select()

    def test_unknown_availability_blocks_even_retrospective(self):
        self.archive['entries'][1]['source_available_at'] = None
        for mode in ('source_as_known', 'retrospective'):
            self.request['mode'] = mode
            result = self.run_match()
            self.assertEqual(result['status'], 'BLOCKED_EVIDENCE')
            self.assertEqual(result['selection']['replay_coverage'], 'PARTIAL')
            self.assertIsNone(result['matching'])
            self.assertIsNone(result['selection']['source'])

    def test_excluded_source_does_not_block_with_unknown_availability(self):
        other = deepcopy(self.archive['entries'][0])
        other.update(id='other', source_id='excluded', source_available_at=None)
        self.archive['entries'].append(other)
        self.assertEqual(self.select()['status'], 'READY')

    def test_partial_and_unavailable_history_block(self):
        for coverage in ('partial', 'unavailable'):
            self.archive['history_coverage'] = coverage
            result = self.run_match()
            self.assertEqual(result['status'], 'BLOCKED_EVIDENCE')
            self.assertTrue(result['selection']['selection_complete'])
            self.assertEqual(result['selection']['replay_coverage'], coverage.upper())
            self.assertIsNone(result['matching'])

    def test_duplicate_support_survives_withdrawal_of_one_source(self):
        self.archive['assertions'] = self.archive['assertions'][:2]
        self.archive['entries'] = self.archive['entries'][:2]
        original = self.archive['entries'][1]
        duplicate = {**original, 'id': 'duplicate', 'source_id': 'second_source'}
        withdrawal = {**original, 'id': 'withdrawal', 'operation': 'withdraw', 'assertion_id': None,
                      'supersedes': original['id'], 'source_available_at': stamp(3)}
        self.archive['entries'].extend([duplicate, withdrawal])
        self.request['admissible_source_ids'].append('second_source')
        result = self.run_match()
        self.assertEqual(result['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(result['selection']['row_supports']['events:B'][0]['support_ids'], ['duplicate'])

    def test_identical_rows_coalesce_across_assertion_ids(self):
        assertion = {**deepcopy(self.archive['assertions'][0]), 'id': 'same_claim'}
        support = {**self.archive['entries'][0], 'id': 'other_claim', 'assertion_id': 'same_claim'}
        self.archive['assertions'].append(assertion)
        self.archive['entries'].append(support)
        report = self.select()
        self.assertEqual(len(report['source']['events']), 2)
        self.assertEqual(len(report['row_supports']['events:A']), 2)

    def test_withdrawal_does_not_resurrect_an_ancestor(self):
        withdrawal = {**self.correction(), 'id': 'withdraw', 'operation': 'withdraw', 'assertion_id': None,
                      'supersedes': self.correction()['id'], 'source_available_at': stamp(5)}
        self.archive['entries'].append(withdrawal)
        self.request['patient_cutoffs']['P1'] = stamp(5)
        report = self.select()
        self.assertEqual(report['selected_assertion_ids'], ['admin'])
        self.assertEqual(report['status'], 'READY')
        self.assertEqual([e['id'] for e in report['source']['events']], ['A'])

    def test_late_contradiction_is_not_a_correction(self):
        self.correction()['supersedes'] = None
        self.request['mode'] = 'retrospective'
        report = self.select()
        self.assertEqual(report['status'], 'BLOCKED_EVIDENCE')
        conflicts = [b for b in report['blockers'] if b['reason'] == 'CONFLICTING_ROW']
        self.assertEqual({b['row'] for b in conflicts}, {'variables:Bs', 'variables:Be'})
        self.assertEqual(set(report['selected_assertion_ids']), {'admin', 'collection_original', 'collection_corrected'})

    def test_cross_source_revision_is_rejected(self):
        self.correction()['source_id'] = 'other'
        with self.assertRaisesRegex(ei.ContractError, 'CROSS_SOURCE_OR_SCOPE_REVISION'):
            self.select()

    def test_revision_fork_only_blocks_when_available(self):
        fork = {**self.correction(), 'id': 'fork'}
        self.archive['entries'].append(fork)
        self.assertEqual(self.select()['status'], 'READY')
        self.request['mode'] = 'retrospective'
        self.assertIn('REVISION_FORK', [b['reason'] for b in self.select()['blockers']])

    def test_revision_cycles_and_backdated_successors_fail(self):
        original = deepcopy(self.archive)
        self.archive['entries'][1].update(supersedes='correction_collection', source_available_at=stamp(4))
        with self.assertRaisesRegex(ei.ContractError, 'REVISION_CYCLE'):
            self.select()
        self.archive = original
        self.correction()['source_available_at'] = stamp(1)
        with self.assertRaisesRegex(ei.ContractError, 'REVISION_AVAILABILITY_ORDER'):
            self.select()

    def test_unknown_revision_ancestor_and_orphan_assertions_fail(self):
        original = deepcopy(self.archive)
        self.correction()['supersedes'] = 'missing'
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_REVISION_TARGET'):
            self.select()
        self.archive = original
        self.archive['assertions'].append({**deepcopy(self.archive['assertions'][0]), 'id': 'orphan'})
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_ARCHIVE_ASSERTION'):
            self.select()

    def test_dangling_selected_references_do_not_become_no_match(self):
        self.archive['assertions'][0]['bundle']['events'][0]['start_var'] = 'missing'
        result = self.run_match()
        self.assertEqual(result['status'], 'INVALID_SELECTED_SOURCE')
        self.assertIsNone(result['matching'])

    def test_temporal_inconsistency_keeps_selection_support(self):
        bundle = self.archive['assertions'][0]['bundle']
        bundle['constraints'].append({'id': 'impossible', 'left_var': 'Ae', 'right_var': 'As',
                                      'upper_us': 0, 'source_key': 'constraint_source'})
        result = self.run_match()
        self.assertEqual(result['status'], 'TEMPORAL_INCONSISTENCY')
        self.assertTrue(result['selection']['validation']['negative_cycle'])
        self.assertEqual(result['selection']['row_supports']['constraints:impossible'][0]['assertion_id'], 'admin')
        self.assertIsNone(result['matching'])

    def test_empty_selection_is_explicit(self):
        self.request['patient_cutoffs']['P1'] = '2025-12-31T00:00:00Z'
        result = self.run_match()
        self.assertEqual(result['status'], 'EMPTY_SELECTED_EVIDENCE')
        self.assertIsNone(result['matching'])
        self.assertEqual(result['selection']['selected_assertion_ids'], [])

    def test_input_and_prior_result_are_not_mutated(self):
        archive_before, request_before = deepcopy(self.archive), deepcopy(self.request)
        first = self.run_match()
        first_text = ei.canonical(first)
        self.assertEqual(self.archive, archive_before)
        self.assertEqual(self.request, request_before)
        self.request['mode'] = 'retrospective'
        self.run_match()
        self.archive['entries'][0]['source_id'] = 'changed'
        self.assertEqual(ei.canonical(first), first_text)

    def test_patient_cutoffs_and_constraint_scope(self):
        # Clone a separate patient history with distinct row and assertion identifiers.
        other = deepcopy(self.archive)
        for a in other['assertions']:
            a['id'] = 'p2_' + a['id']; a['patient_id'] = 'P2'
            for kind in selection.ROW_KINDS:
                for row in a['bundle'][kind]:
                    for key in ('id', 'record_id', 'start_var', 'end_var', 'left_var', 'right_var'):
                        if key in row:
                            row[key] = 'p2_' + row[key]
                    if 'patient_id' in row:
                        row['patient_id'] = 'P2'
        for e in other['entries']:
            e['id'] = 'p2_' + e['id']; e['assertion_id'] = 'p2_' + e['assertion_id']; e['patient_id'] = 'P2'
            if e['supersedes']:
                e['supersedes'] = 'p2_' + e['supersedes']
        self.archive['assertions'].extend(other['assertions']); self.archive['entries'].extend(other['entries'])
        self.request['patient_cutoffs']['P2'] = stamp(5)
        self.assertEqual(self.run_match()['matching']['certain_patient_ids'], ['P1'])
        self.archive['assertions'][0]['bundle']['constraints'].append(
            {'id': 'bad_scope', 'left_var': 'p2_As', 'right_var': 'p2_Ae', 'upper_us': 100, 'source_key': 'bad'})
        self.assertIn('CONSTRAINT_ASSERTION_SCOPE', [b['reason'] for b in self.select()['blockers']])

    def test_schema_policy_and_timestamp_errors(self):
        original = deepcopy(self.request)
        for key, value in [('semantic_policy', 'historical'), ('missing_availability_policy', 'include'),
                           ('revision_policy', 'latest-ingested-wins')]:
            self.request = {**original, key: value}
            with self.assertRaises(ei.ContractError):
                self.select()
        self.request = original
        self.request['patient_cutoffs']['P1'] = '2026-01-03T00:00:00'
        with self.assertRaises(ei.ContractError):
            self.select()

    def test_malformed_query_rejected_even_if_selection_empty(self):
        self.request['patient_cutoffs']['P1'] = '2025-12-31T00:00:00Z'
        self.query['slots'][0]['class_iri'] = 'unsupported'
        with self.assertRaises(ei.ContractError):
            self.run_match()

    def test_exact_integer_validation_in_archive(self):
        self.archive['assertions'][0]['bundle']['variables'][0]['lower_us'] = 0.0
        with self.assertRaisesRegex(ei.ContractError, 'INTEGER_MICROSECONDS'):
            self.select()

    def test_selection_permutation_and_repeatability(self):
        before = self.select()
        self.assertEqual(self.select(), before)
        self.archive['entries'].reverse(); self.archive['assertions'].reverse()
        after = self.select()
        self.assertEqual(after['selected_assertion_ids'], before['selected_assertion_ids'])
        self.assertEqual(after['row_supports'], before['row_supports'])
        # Archive hashes preserve array order, even when selection is equivalent.
        self.assertNotEqual(after['context_id'], before['context_id'])

    def test_seeded_linear_histories_against_reference(self):
        rng = random.Random(17)
        for _ in range(60):
            archive, request, _ = fixture()
            archive['assertions'] = archive['assertions'][:1]
            archive['entries'] = []
            chains = []
            for channel in range(3):
                chain = []
                for position in range(rng.randint(1, 5)):
                    eid = f's{channel}_{position}'
                    action = 'assert' if position == 0 else rng.choice(['assert', 'withdraw'])
                    entry = {'id': eid, 'source_id': f's{channel}', 'patient_id': 'P1', 'episode_id': 'E1',
                             'operation': action, 'assertion_id': 'admin' if action == 'assert' else None,
                             'supersedes': chain[-1]['id'] if chain else None, 'source_available_at': stamp(position + 1),
                             'source_recorded_at': None, 'archived_at': stamp(6)}
                    chain.append(entry)
                archive['entries'].extend(chain); chains.append(chain)
            request['admissible_source_ids'] = ['s0', 's1', 's2']
            cutoff = rng.randint(1, 5); request['patient_cutoffs']['P1'] = stamp(cutoff)
            # Independent reference: inspect each chronological chain prefix's last operation.
            expected = []
            for chain in chains:
                final = chain[:cutoff][-1]
                if final['operation'] == 'assert':
                    expected.append(final['id'])
            rng.shuffle(archive['entries'])
            actual = selection.select(archive, request)
            self.assertEqual(actual['active_support_ids'], sorted(expected))
            self.assertEqual(actual['status'], 'READY' if expected else 'EMPTY_SELECTED_EVIDENCE')

    def test_selected_answers_against_finite_world_reference(self):
        for mode in ('source_as_known', 'retrospective'):
            self.request['mode'] = mode
            result = self.run_match()
            source = result['selection']['source']
            events = {e['id']: e for e in source['events']}
            worlds = reference.worlds(source, ('P1', 'E1'))
            binding = {'a': events['A'], 'b': events['B']}
            expected = reference.classify(source, binding, self.query['constraints'], worlds)
            actual = result['matching']['trajectories'][0]['bindings'][0]['status']
            self.assertEqual(actual, expected)

    def test_shared_uncertainty_survives_selection(self):
        self.archive['assertions'] = self.archive['assertions'][:2]
        self.archive['entries'] = self.archive['entries'][:2]
        for assertion in self.archive['assertions']:
            for variable in assertion['bundle']['variables']:
                variable['upper_us'] += 5
        # Marginal endpoint ranges overlap, but the joint gap is always two.
        constraints = self.archive['assertions'][0]['bundle']['constraints']
        for number, (left, right, delta) in enumerate([('Ae', 'As', 1), ('Bs', 'Ae', 2), ('Be', 'Bs', 1)]):
            for suffix, a, b, upper in [('forward', left, right, delta), ('reverse', right, left, -delta)]:
                constraints.append({'id': f'c{number}_{suffix}', 'left_var': a, 'right_var': b,
                                    'upper_us': upper, 'source_key': f'shared_{number}'})
        result = self.run_match()
        self.assertEqual(result['matching']['certain_patient_ids'], ['P1'])
        source = result['selection']['source']
        events = {e['id']: e for e in source['events']}
        expected = reference.classify(source, {'a': events['A'], 'b': events['B']}, self.query['constraints'],
                                      reference.worlds(source, ('P1', 'E1')))
        self.assertEqual(expected, 'CERTAIN')
        self.assertEqual(len(result['selection']['source']['constraints']), 6)

    def test_cutoff_source_and_identifier_scope_validation(self):
        original = deepcopy(self.request)
        for field, value in [('patient_cutoffs', {}), ('patient_cutoffs', {'P1': stamp(7)}),
                             ('admissible_source_ids', ['typo'])]:
            self.request = {**original, field: value}
            with self.assertRaises(ei.ContractError):
                self.select()
        self.request = original
        self.archive['entries'][0]['id'] += '\n'
        with self.assertRaises(ei.ContractError):
            self.select()

    def test_cli_reports_success_and_blocked_status(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            args = [sys.executable, '-m', 'patterns.evidence_selection', '--output', str(folder / 'out')]
            result = subprocess.run(args, cwd=ei.ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads((folder / 'out/result.json').read_text())['status'], 'READY')
            self.archive['history_coverage'] = 'partial'
            (folder / 'archive.json').write_text(json.dumps(self.archive))
            result = subprocess.run(args + ['--archive', str(folder / 'archive.json')], cwd=ei.ROOT,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(json.loads((folder / 'out/result.json').read_text())['status'], 'BLOCKED_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
