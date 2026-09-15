"""Joint source-history selection, scope isolation and Rust replay acceptance."""
from copy import deepcopy
import json
import random
import unittest
from unittest.mock import patch

from . import bounded_cohort as bc
from . import evidence_selection as es
from . import exact_intervals as ei
from . import joint_evidence as joint
from . import semantic_support as ss

N = 'https://example.org/trajectory/semantic/'


def stamp(day):
    return f'2026-01-{day:02d}T00:00:00Z'


def fixture():
    folder = ei.ROOT / 'examples/joint-evidence'
    return [json.loads((folder / (key + '.json')).read_text()) for key in ('archive', 'request', 'policy', 'query')]


class JointEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.archive, self.request, self.policy, self.query = fixture()

    def run_query(self):
        return joint.execute(self.archive, self.request, self.policy, self.query)

    def after(self):
        self.request['patient_cutoffs']['P1'] = stamp(4)

    def revised(self):
        return self.archive['assertions'][2]

    def add_withdrawal(self, parent='classification_admin', day=5):
        entry = {**self.archive['entries'][2], 'id': 'withdraw_' + str(day), 'operation': 'withdraw',
                 'assertion_id': None, 'supersedes': parent, 'source_available_at': stamp(day)}
        self.archive['entries'].append(entry)
        return entry

    def test_as_known_excludes_later_classification_despite_old_recorded_date(self):
        result = self.run_query()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['matching']['possible_patient_ids'], [])
        self.assertNotIn('admin_classified', result['selection']['selected_assertion_ids'])
        self.assertNotIn(N + 'Antibiotic', [a['class'] for a in result['semantic_module']['class_assertions']])
        self.assertEqual(self.archive['entries'][2]['source_recorded_at'], stamp(1))
        self.assertFalse(result['selection']['later_evidence_used'])

    def test_inclusive_cutoff_changes_class_support_and_match(self):
        old = self.run_query()
        self.request['patient_cutoffs']['P1'] = '2026-01-04T01:00:00+01:00'
        new = self.run_query()
        self.assertEqual(new['matching']['certain_patient_ids'], ['P1'])
        self.assertNotEqual(old['context_id'], new['context_id'])
        self.assertNotIn('admin', new['selection']['selected_assertion_ids'])
        self.assertIn('admin_classified', new['selection']['selected_assertion_ids'])
        self.assertFalse(new['selection']['later_evidence_used'])

    def test_retrospective_includes_later_evidence_with_explicit_label(self):
        self.request['mode'] = 'retrospective'
        result = self.run_query()
        self.assertEqual(result['matching']['certain_patient_ids'], ['P1'])
        self.assertTrue(result['selection']['later_evidence_used'])
        self.assertFalse(result['historical_ontology_replay'])
        self.assertEqual(result['context']['semantic_policy'], 'current-pinned-retrospective')

    def test_atomic_revision_changes_temporal_and_semantic_facts_together(self):
        self.after()
        old = self.run_query()
        revised = deepcopy(self.revised()); revised['id'] = 'admin_corrected'
        # A remains proper but no longer precedes B. Replace the drug class
        # in the SAME support revision; neither old assertion survives.
        revised['bundle']['variables'][1].update(lower_us=4, upper_us=4)
        revised['bundle']['semantic_facts'][-1]['class_iri'] = N + 'NonAntibiotic'
        self.archive['assertions'].append(revised)
        self.archive['entries'].append({**self.archive['entries'][2], 'id': 'correction', 'assertion_id': revised['id'],
                                       'supersedes': 'classification_admin', 'source_available_at': stamp(5)})
        self.request['patient_cutoffs']['P1'] = stamp(5)
        result = self.run_query()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['matching']['possible_patient_ids'], [])
        self.assertEqual([v['lower_us'] for v in result['selection']['source']['variables'] if v['id'] == 'Ae'], [4])
        for key in ('variables:Ae', 'semantic_facts:drug_class'):
            self.assertEqual(result['selection']['row_supports'][key][0]['support_ids'], ['correction'])
        self.assertNotIn(N + 'Antibiotic', [a['class'] for a in result['semantic_module']['class_assertions']])
        old_slot = old['matching']['trajectories'][0]['bindings'][0]['slots']['a']
        new_witness = result['semantic_run']['matching']['evidence']['bindings']['A']
        for key in ('process', 'patient_role', 'patient_bearer'):
            self.assertEqual(old_slot[key], new_witness[key])

    def test_withdrawal_does_not_resurrect_ancestor(self):
        self.add_withdrawal(); self.request['patient_cutoffs']['P1'] = stamp(5)
        result = self.run_query()
        self.assertEqual(result['selection']['selected_assertion_ids'], ['collection_original'])
        self.assertNotIn('A', [e['id'] for e in result['selection']['source']['events']])
        self.assertEqual(result['semantic_module']['class_assertions'], [])
        self.assertEqual(result['matching']['possible_patient_ids'], [])

    def test_independent_support_survives_withdrawal(self):
        self.archive['entries'].append({**self.archive['entries'][2], 'id': 'second_support', 'source_id': 'second', 'supersedes': None})
        self.request['admissible_source_ids'].append('second')
        self.add_withdrawal(); self.request['patient_cutoffs']['P1'] = stamp(5)
        result = self.run_query()
        self.assertEqual(result['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(result['selection']['row_supports']['semantic_facts:drug_class'][0]['support_ids'], ['second_support'])

    def test_duplicate_axioms_preserve_all_fact_supports(self):
        self.after()
        assertion = {'id': 'duplicate_fact', 'patient_id': 'P1', 'episode_id': 'E1',
                     'bundle': {k: [] for k in (*es.ROW_KINDS, 'semantic_facts')}}
        assertion['bundle']['semantic_facts'] = [{**deepcopy(self.revised()['bundle']['semantic_facts'][-1]), 'id': 'drug_class_also'}]
        self.archive['assertions'].append(assertion)
        self.archive['entries'].append({**self.archive['entries'][2], 'id': 'duplicate_support', 'assertion_id': assertion['id'], 'supersedes': None})
        result = self.run_query()
        entry = next(x for x in result['axiom_supports'] if x['axiom'].get('class') == N + 'Antibiotic')
        self.assertEqual({f['fact_id'] for f in entry['facts']}, {'drug_class', 'drug_class_also'})
        self.assertEqual(result['matching']['certain_patient_ids'], ['P1'])

    def test_unknown_semantic_availability_blocks_before_reasoning(self):
        self.archive['entries'][2]['source_available_at'] = None
        with patch.object(ss, 'execute', side_effect=AssertionError('must not reason')):
            for mode in ('source_as_known', 'retrospective'):
                self.request['mode'] = mode
                result = self.run_query()
                self.assertEqual(result['status'], 'BLOCKED_EVIDENCE')
                self.assertEqual(result['selection']['replay_coverage'], 'PARTIAL')
                self.assertIsNone(result['matching'])
                self.assertIsNone(result['semantic_module'])

    def test_incomplete_history_blocks_both_layers(self):
        for coverage in ('partial', 'unavailable'):
            self.archive['history_coverage'] = coverage
            result = self.run_query()
            self.assertEqual(result['status'], 'BLOCKED_EVIDENCE')
            self.assertIsNone(result['semantic_run'])
            self.assertIsNone(result['matching'])

    def test_excluded_unknown_source_does_not_contribute_or_block(self):
        self.archive['entries'][2].update(source_id='excluded', supersedes=None, source_available_at=None)
        result = self.run_query()
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['matching']['possible_patient_ids'], [])

    def test_conflicting_fact_id_blocks_before_rust(self):
        self.after()
        assertion = deepcopy(self.revised()); assertion['id'] = 'conflict'
        assertion['bundle']['semantic_facts'][-1]['class_iri'] = N + 'NonAntibiotic'
        self.archive['assertions'].append(assertion)
        self.archive['entries'].append({**self.archive['entries'][2], 'id': 'other', 'assertion_id': 'conflict', 'supersedes': None})
        with patch.object(ss, 'execute', side_effect=AssertionError('must not reason')):
            result = self.run_query()
        self.assertEqual(result['status'], 'BLOCKED_EVIDENCE')
        self.assertTrue(any(b.get('row') == 'semantic_facts:drug_class' for b in result['selection']['blockers']))

    def test_distinct_conflicting_claims_reach_ontology_inconsistency(self):
        self.after()
        fact = {**self.revised()['bundle']['semantic_facts'][-1], 'id': 'conflicting_class', 'class_iri': N + 'NonAntibiotic'}
        self.revised()['bundle']['semantic_facts'].append(fact)
        result = self.run_query()
        self.assertEqual(result['status'], 'INCONSISTENT_ONTOLOGY')
        self.assertIsNone(result['matching'])
        self.assertTrue(result['semantic_run']['semantic_support']['reference']['clashes'])

    def test_missing_selected_event_blocks_instead_of_dropping_fact(self):
        self.after()
        self.revised()['bundle']['semantic_facts'][2]['subject'] = {'event_id': 'missing'}
        result = self.run_query()
        self.assertEqual(result['status'], 'INVALID_SELECTED_SEMANTICS')
        self.assertIn('MISSING_SELECTED_SEMANTIC_EVENT', result['validation']['reason'])
        self.assertIsNone(result['matching'])

    def test_missing_selected_local_declaration_blocks(self):
        self.after()
        self.revised()['bundle']['semantic_facts'] = [f for f in self.revised()['bundle']['semantic_facts'] if f['id'] != 'drug_declaration']
        result = self.run_query()
        self.assertEqual(result['status'], 'INVALID_SELECTED_SEMANTICS')
        self.assertIn('MISSING_SELECTED_SEMANTIC_INDIVIDUAL', result['validation']['reason'])

    def test_later_malformed_reference_does_not_leak_into_earlier_selection(self):
        self.revised()['bundle']['semantic_facts'][2]['subject'] = {'event_id': 'missing'}
        self.assertEqual(self.run_query()['status'], 'READY')
        self.after()
        self.assertEqual(self.run_query()['status'], 'INVALID_SELECTED_SEMANTICS')

    def test_fact_scope_and_raw_iri_references_are_rejected(self):
        self.revised()['bundle']['semantic_facts'][0]['patient_id'] = 'P2'
        with self.assertRaisesRegex(ei.ContractError, 'SEMANTIC_ASSERTION_SCOPE_MISMATCH'): self.run_query()
        self.setUp()
        self.revised()['bundle']['semantic_facts'][2]['subject'] = {'iri': 'https://foreign/process'}
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_JOINT_ARCHIVE_SCHEMA'): self.run_query()

    def test_cross_episode_event_reference_blocks(self):
        self.after()
        # B and its temporal coordinates move together to E2, but a selected
        # semantic fact in E1 is made to target it.
        other = self.archive['assertions'][1]
        other['episode_id'] = 'E2'; self.archive['entries'][1]['episode_id'] = 'E2'
        for kind in ('events', 'variables'):
            for row in other['bundle'][kind]: row['episode_id'] = 'E2'
        self.revised()['bundle']['semantic_facts'][2]['subject'] = {'event_id': 'B'}
        result = self.run_query()
        self.assertEqual(result['status'], 'INVALID_SELECTED_SEMANTICS')
        self.assertIn('CROSS_SCOPE_SEMANTIC_REFERENCE', result['validation']['reason'])

    def test_patient_cutoffs_and_local_names_are_isolated(self):
        def second(value):
            if isinstance(value, list): return [second(v) for v in value]
            if not isinstance(value, dict): return value
            result = {}
            for key, val in value.items():
                if key == 'patient_id': val = 'P2'
                elif key in ('id', 'record_id', 'start_var', 'end_var', 'event_id', 'left_var', 'right_var', 'assertion_id', 'supersedes') and val is not None:
                    val += '_P2'
                else: val = second(val)
                result[key] = val
            return result
        self.archive['assertions'] += second(deepcopy(self.archive['assertions']))
        self.archive['entries'] += second(deepcopy(self.archive['entries']))
        self.request['patient_cutoffs']['P2'] = stamp(4)
        result = self.run_query()
        self.assertEqual(result['matching']['certain_patient_ids'], ['P2'])
        individuals = result['semantic_module']['individuals']
        self.assertEqual(len(individuals), 4)
        self.assertTrue(any('/P1/E1/drug' in i for i in individuals))
        self.assertTrue(any('/P2/E1/drug' in i for i in individuals))

    def test_semantic_only_packet_joins_selected_temporal_evidence(self):
        fact = deepcopy(self.revised()['bundle']['semantic_facts'][-1])
        self.revised()['bundle'] = {**{k: [] for k in es.ROW_KINDS}, 'semantic_facts': [fact]}
        self.archive['entries'][2]['supersedes'] = None
        self.after()
        result = self.run_query()
        self.assertEqual(result['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(result['selection']['row_supports']['events:A'][0]['support_ids'], ['support_admin'])
        self.assertEqual(result['selection']['row_supports']['semantic_facts:drug_class'][0]['support_ids'], ['classification_admin'])

    def test_dangling_fact_after_event_withdrawal_blocks(self):
        fact = deepcopy(self.revised()['bundle']['semantic_facts'][2])
        self.revised()['bundle'] = {**{k: [] for k in es.ROW_KINDS}, 'semantic_facts': [fact]}
        self.archive['entries'][2]['supersedes'] = None
        self.add_withdrawal(parent='support_admin'); self.request['patient_cutoffs']['P1'] = stamp(5)
        result = self.run_query()
        self.assertEqual(result['status'], 'INVALID_SELECTED_SEMANTICS')
        self.assertIsNone(result['matching'])

    def test_temporal_contradiction_blocks_before_semantic_reasoning(self):
        self.after()
        self.revised()['bundle']['variables'][1].update(lower_us=0, upper_us=0)
        with patch.object(ss, 'execute', side_effect=AssertionError('must not reason')):
            result = self.run_query()
        self.assertEqual(result['status'], 'TEMPORAL_INCONSISTENCY')
        self.assertIsNone(result['matching'])

    def test_revision_fork_cycle_and_cross_source_guards_reused(self):
        self.after()
        self.archive['entries'].append({**self.archive['entries'][2], 'id': 'fork'})
        self.assertEqual(self.run_query()['status'], 'BLOCKED_EVIDENCE')
        self.setUp(); self.archive['entries'][0]['supersedes'] = 'classification_admin'
        self.archive['entries'][2]['source_available_at'] = stamp(1)
        with self.assertRaisesRegex(ei.ContractError, 'REVISION_CYCLE'): self.run_query()
        self.setUp(); self.archive['entries'][2]['source_id'] = 'other'
        with self.assertRaisesRegex(ei.ContractError, 'CROSS_SOURCE_OR_SCOPE_REVISION'): self.run_query()

    def test_no_semantic_facts_matches_temporal_entry_point_for_builtin_selectors(self):
        self.after()
        with self.assertRaisesRegex(ei.ContractError, 'INVALID_ARCHIVE_SCHEMA'):
            es.select(self.archive, self.request)
        for a in self.archive['assertions']: a['bundle']['semantic_facts'] = []
        self.query['slots'][0]['class_iri'] = 'https://example.org/trajectory/bounded/Infusion'
        current = self.run_query()
        archive = deepcopy(self.archive); archive['profile'] = 'evidence-archive-1.0'
        for a in archive['assertions']: del a['bundle']['semantic_facts']
        query = deepcopy(self.query); query['profile'] = 'bounded-interval-query-1.0'
        old = es.execute(archive, self.request, query)
        self.assertEqual(current['matching']['certain_patient_ids'], old['matching']['certain_patient_ids'])
        self.assertEqual(current['selection']['active_support_ids'], old['selection']['active_support_ids'])
        self.assertEqual(current['selection']['decisions'], old['selection']['decisions'])

    def test_policy_contains_no_patient_assertions_or_historical_replay_switch(self):
        self.policy['class_assertions'] = []
        with self.assertRaises(ei.ContractError): self.run_query()
        self.setUp(); self.request['semantic_policy'] = 'historical-ontology'
        with self.assertRaises(ei.ContractError): self.run_query()

    def test_current_policy_change_changes_context_and_answers(self):
        self.after(); old = self.run_query()
        self.policy['id'] = 'empty-rule-policy'; self.policy['rules'] = []
        new = self.run_query()
        self.assertEqual(new['matching']['certain_patient_ids'], [])
        self.assertNotEqual(old['context_id'], new['context_id'])
        self.assertFalse(new['historical_ontology_replay'])

    def test_semantic_failure_propagates_without_cohort_answer(self):
        self.after()
        with patch.object(ss, 'run_backend', return_value={'status': 'BACKEND_TIMEOUT'}), patch.object(bc, '_execute_supported', side_effect=AssertionError('must not join')):
            result = self.run_query()
        self.assertEqual(result['status'], 'UNRESOLVED_SEMANTICS')
        self.assertIsNone(result['matching'])
        self.assertTrue(result['axiom_supports'])

    def test_every_asserted_semantic_fact_traces_to_active_source_support(self):
        self.after(); result = self.run_query()
        active = set(result['selection']['active_support_ids'])
        for item in result['axiom_supports']:
            for fact in item['facts']:
                for support in fact['support']:
                    self.assertTrue(set(support['support_ids']) <= active)
                    self.assertIn(support['assertion_id'], result['selection']['selected_assertion_ids'])
        self.assertEqual(result['semantic_module']['source_sha256'], ei.digest(ei.canonical(result['selection']['source'])))
        self.assertEqual(result['matching']['context']['semantic_context_id'], result['semantic_run']['context_id'])

    def test_seeded_histories_against_independent_latest_eligible_chain(self):
        rng = random.Random(771)
        for case in range(24):
            self.setUp()
            # Keep stable temporal/role witnesses. Only this independent
            # source chain revises the drug's classification.
            self.archive['assertions'] = self.archive['assertions'][:2]
            self.archive['entries'] = self.archive['entries'][:2]
            cutoff = rng.randint(2, 6); self.request['patient_cutoffs']['P1'] = stamp(cutoff)
            self.request['admissible_source_ids'].append('classification')
            chain = []; parent = None
            for day in range(2, 7):
                operation = 'assert' if parent is None or rng.random() < .65 else 'withdraw'
                eid = f'rev_{day}'; aid = None; kind = None
                if operation == 'assert':
                    aid = f'claim_{day}'; kind = rng.choice(['Antibiotic', 'NonAntibiotic'])
                    fact = {'id': 'drug_class', 'patient_id': 'P1', 'episode_id': 'E1', 'kind': 'class',
                            'subject': {'local_id': 'drug'}, 'class_iri': N + kind}
                    self.archive['assertions'].append({'id': aid, 'patient_id': 'P1', 'episode_id': 'E1',
                        'bundle': {**{k: [] for k in es.ROW_KINDS}, 'semantic_facts': [fact]}})
                self.archive['entries'].append({'id': eid, 'source_id': 'classification', 'patient_id': 'P1',
                    'episode_id': 'E1', 'operation': operation, 'assertion_id': aid, 'supersedes': parent,
                    'source_available_at': stamp(day), 'source_recorded_at': stamp(1), 'archived_at': stamp(6)})
                chain.append((day, operation, eid, aid, kind)); parent = eid
            latest = max(row for row in chain if row[0] <= cutoff)
            expected = ['P1'] if latest[1] == 'assert' and latest[4] == 'Antibiotic' else []
            with self.subTest(case=case, cutoff=cutoff):
                result = self.run_query()
                self.assertEqual(result['status'], 'READY')
                self.assertEqual(result['matching']['certain_patient_ids'], expected)
                selected = set(result['selection']['active_support_ids']) - {'support_admin', 'support_collection'}
                self.assertEqual(selected, {latest[2]} if latest[1] == 'assert' else set())


if __name__ == '__main__':
    unittest.main(verbosity=2)
