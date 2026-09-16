"""Reviewed mappings, record/concept boundaries and checked mixed-query integration."""
from contextlib import redirect_stdout, redirect_stderr
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from . import reviewed_record_mappings as r, verify_reviewed_record_mappings as verify


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.values = {p.stem: json.loads(p.read_text()) for p in verify.EXAMPLE.glob('*.json')}

    def compile(self): return r.compile_policy(*(self.values[n] for n in r.NAMES))
    def execute(self): return r.execute(*(self.values[n] for n in r.NAMES + verify.EXTRA))

    def author_test_review(self):
        """Explicit authoring of synthetic test decisions, never production review propagation."""
        v = self.values; mappings = v['mappings']
        mappings['catalogue_sha256'] = r.cr.digest(v['catalogue'])
        mappings['terminology_sha256'] = r.cr.digest(v['terminology'])
        v['review'] = {'profile': 'record-mapping-review-1.0', 'mappings_sha256': r.cr.digest(mappings),
                       'decisions': [{'id': 'accept-' + row['id'], 'mapping_id': row['id'],
                                      'mapping_sha256': r.cr.digest(row), 'action': 'accept', 'supersedes': None,
                                      'reviewer': 'Authored test reviewer', 'reason': 'Authored synthetic mutation'}
                                     for row in mappings['entries']]}

    def test_actual_rust_group_query_preserves_pro_and_optional_followup(self):
        result = self.execute(); query = result['query_result']
        self.assertEqual(result['status'], 'COMPLETED_RECORD_QUERY')
        self.assertEqual(query['certain_patient_ids'], ['P1', 'P2', 'P3'])
        self.assertEqual(query['interval_view']['semantic_run']['status'], 'READY')
        p2 = next(b for b in query['baseline_bindings'] if b['baseline_id'] == 'baseline_P2')
        self.assertEqual(p2['followup_status'], 'NO_SELECTED_FOLLOWUP_IN_WINDOW')
        self.assertEqual(p2['eligibility']['status'], 'CERTAIN')
        self.assertTrue(p2['treatment']['patient_role']); self.assertTrue(p2['treatment']['patient_bearer'])
        self.assertFalse(query['clinical_mapping_verified'])

    def test_rules_are_one_way_record_class_implications_with_review_evidence(self):
        result = self.compile(); policy = result['semantic_policy']
        concepts = {t['concept_iri'] for t in self.values['terminology']['terms']}
        self.assertFalse(concepts & set(policy['classes']))
        source_classes = {s['source_class_iri'] for s in self.values['catalogue']['entries']}
        targets = {t['record_class_iri'] for t in self.values['terminology']['terms']}
        for rule, evidence in zip(policy['rules'], result['rule_evidence']):
            self.assertIn(rule['if']['class'], source_classes)
            self.assertIn(rule['then'], targets)
            self.assertEqual(rule['id'], evidence['rule_id'])
            self.assertTrue(evidence['decision_id'])
        self.assertEqual(policy['disjoint'], [])
        self.assertFalse(result['context']['reviewer_identity_verified'])

    def test_pending_review_blocks_all_policy_and_backend_work(self):
        self.values['review']['decisions'].pop()
        with patch.object(r.mixed, 'execute', side_effect=AssertionError('No execution with pending mapping')):
            result = self.execute()
        self.assertEqual(result['status'], 'BLOCKED_MAPPING_REVIEW')
        self.assertIsNone(result['query_result']); self.assertIsNone(result['mapping']['semantic_policy'])
        self.assertEqual(result['mapping']['rule_evidence'], [])

    def test_withdrawal_and_explicit_reacceptance_change_policy_identity(self):
        old = self.compile(); decision = self.values['review']['decisions'][0]
        self.values['review']['decisions'].append({**decision, 'id': 'withdraw-a', 'action': 'withdraw', 'supersedes': decision['id']})
        self.assertEqual(self.compile()['status'], 'BLOCKED_MAPPING_REVIEW')
        self.values['review']['decisions'].append({**decision, 'id': 'reaccept-a', 'supersedes': 'withdraw-a'})
        new = self.compile(); self.assertEqual(new['status'], 'READY_REVIEWED_RECORD_MAPPING')
        self.assertNotEqual(old['context_id'], new['context_id'])
        self.assertNotEqual(old['semantic_policy'], new['semantic_policy'])
        with self.assertRaisesRegex(ValueError, 'STALE_CLAIM_SEMANTIC_POLICY'): self.execute()

    def test_review_journal_rejects_forks_forward_links_and_cross_mapping_links(self):
        original = deepcopy(self.values['review'])
        for parent in ['missing', 'accept-map-b']:
            self.values['review'] = deepcopy(original); d = original['decisions'][0]
            self.values['review']['decisions'].append({**d, 'id': 'withdraw', 'action': 'withdraw', 'supersedes': parent})
            with self.assertRaisesRegex(ValueError, 'INVALID_REVIEW_CHAIN'): self.compile()
        self.values['review'] = deepcopy(original); d = original['decisions'][0]
        self.values['review']['decisions'] += [{**d, 'id': name, 'supersedes': d['id']} for name in ['fork-a', 'fork-b']]
        with self.assertRaisesRegex(ValueError, 'INVALID_REVIEW_CHAIN'): self.compile()

    def test_duplicate_decisions_roots_and_stale_mapping_digests_are_rejected(self):
        original = deepcopy(self.values['review'])
        mutations = [lambda ds: ds.append(deepcopy(ds[0])),
                     lambda ds: ds.append({**ds[0], 'id': 'another-root'}),
                     lambda ds: ds[0].update(mapping_sha256='0' * 64),
                     lambda ds: ds[0].update(action='withdraw')]
        for mutate in mutations:
            self.values['review'] = deepcopy(original); mutate(self.values['review']['decisions'])
            with self.assertRaises(ValueError): self.compile()

    def test_catalogue_terminology_and_mapping_updates_require_new_review(self):
        original = deepcopy(self.values)
        for name, field in [('catalogue', 'release'), ('terminology', 'version'), ('mappings', 'id')]:
            self.values = deepcopy(original); self.values[name][field] = 'changed'
            with self.assertRaisesRegex(ValueError, 'STALE_'): self.compile()
        self.values = deepcopy(original)
        self.values['terminology']['terms'][0]['label'] = 'Changed definition label'
        self.values['mappings']['terminology_sha256'] = r.cr.digest(self.values['terminology'])
        with self.assertRaisesRegex(ValueError, 'STALE_MAPPING_REVIEW'): self.compile()

    def test_incomplete_or_ambiguous_catalogue_mapping_is_rejected(self):
        original = deepcopy(self.values)
        mutations = [lambda v: v['mappings']['entries'].pop(),
                     lambda v: v['mappings']['entries'][1].update(source_code='item-a'),
                     lambda v: v['mappings']['entries'][1].update(target_code='missing'),
                     lambda v: v['catalogue']['entries'][1].update(code='item-a'),
                     lambda v: v['catalogue']['entries'][1].update(source_class_iri=v['catalogue']['entries'][0]['source_class_iri'])]
        for mutate in mutations:
            self.values = deepcopy(original); mutate(self.values); self.author_test_review()
            with self.assertRaises(ValueError): self.compile()

    def test_equivalence_reverse_axioms_and_unrecognized_fields_are_rejected(self):
        for relation in ['equivalent_to', 'target_record_subclass_of_source_record', 'same_as']:
            self.values['mappings']['entries'][0]['relation'] = relation
            self.author_test_review()
            with self.assertRaisesRegex(ValueError, 'INVALID_MAPPING_MAPPINGS'): self.compile()
        self.setUp(); self.values['terminology']['imports'] = ['https://example.org/ontology']
        self.author_test_review()
        with self.assertRaisesRegex(ValueError, 'INVALID_MAPPING_TERMINOLOGY'): self.compile()

    def test_record_wrappers_cannot_alias_concepts_or_reserved_ontology_classes(self):
        original = deepcopy(self.values)
        for mutation in ['concept', 'reserved', 'namespace']:
            self.values = deepcopy(original)
            if mutation == 'concept': self.values['terminology']['terms'][0]['concept_iri'] = self.values['terminology']['terms'][0]['record_class_iri']
            elif mutation == 'reserved': self.values['catalogue']['entries'][0]['source_class_iri'] = 'https://w3id.org/sulo/Process'
            else: self.values['terminology']['terms'][0]['record_class_iri'] = 'https://example.org/wrong'
            self.author_test_review()
            with self.assertRaises(ValueError): self.compile()

    def test_compilation_does_not_mutate_inputs_or_share_output_objects(self):
        before = deepcopy(self.values); result = self.compile()
        self.assertEqual(self.values, before)
        expected = deepcopy(result); result['rule_evidence'][0]['source']['label'] = 'mutated'
        result['semantic_policy']['rules'].clear()
        self.assertEqual(self.compile(), expected)

    def test_source_acceptance_is_separate_and_never_rewritten(self):
        before = deepcopy(self.values['interval-policy'])
        self.values['interval-policy']['semantic_policy_sha256'] = '0' * 64
        altered = deepcopy(self.values['interval-policy'])
        with self.assertRaisesRegex(ValueError, 'STALE_CLAIM_SEMANTIC_POLICY'): self.execute()
        self.assertEqual(self.values['interval-policy'], altered)
        self.values['interval-policy'] = before; self.execute()
        self.assertEqual(self.values['interval-policy'], before)

    def test_missing_unknown_or_asserted_target_source_types_are_rejected(self):
        original = deepcopy(self.values['interval-store'])
        for mutation in ['missing', 'target', 'duplicate', 'unknown-event']:
            self.values['interval-store'] = deepcopy(original)
            facts = self.values['interval-store']['claims'][0]['bundle']['semantic_facts']
            if mutation == 'missing': facts.clear()
            elif mutation == 'target': facts[0]['class_iri'] = self.values['query']['treatment_class_iri']
            elif mutation == 'unknown-event': facts.append({**facts[0], 'id': 'dangling-type', 'subject': {'event_id': 'missing-event'}})
            else: facts.append({**facts[0], 'id': 'extra-type'})
            with self.assertRaises(ValueError): self.execute()

    def test_wrong_dataset_or_concept_query_cannot_execute(self):
        self.values['interval-store']['dataset_id'] = 'other'
        with self.assertRaisesRegex(ValueError, 'MAPPING_DATASET_MISMATCH'): self.execute()
        self.setUp(); self.values['query']['treatment_class_iri'] = self.values['terminology']['terms'][0]['concept_iri']
        with self.assertRaisesRegex(ValueError, 'QUERY_REQUIRES_RECORD_SELECTOR'): self.execute()

    def test_empty_reviewer_or_excessive_mapping_input_is_rejected(self):
        self.values['review']['decisions'][0]['reviewer'] = '   '
        with self.assertRaisesRegex(ValueError, 'INVALID_MAPPING_REVIEW'): self.compile()
        self.setUp(); self.values['review']['decisions'][0]['reason'] = 'x' * (r.cr.MAX_BYTES + 1)
        with self.assertRaisesRegex(ValueError, 'MAPPING_DOCUMENT_LIMIT'): self.compile()

    def test_cli_complete_blocked_invalid_and_input_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder); args = []
            for name in r.NAMES:
                path = folder / (name + '.json'); path.write_text(json.dumps(self.values[name])); args += ['--' + name, str(path)]
            output = folder / 'output.json'; args += ['--output', str(output)]
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(r.main(args), 0)
                review = folder / 'review.json'; value = deepcopy(self.values['review']); value['decisions'] = []
                review.write_text(json.dumps(value)); self.assertEqual(r.main(args), 2)
                self.assertIsNone(json.loads(output.read_text())['semantic_policy'])
                previous = output.read_bytes(); review.write_text('{}')
                with self.assertRaises(SystemExit): r.main(args)
                self.assertEqual(output.read_bytes(), previous)
                with self.assertRaises(SystemExit): r.main(args[:-1] + [str(review)])
                self.assertEqual(review.read_text(), '{}')

    def test_clinical_worksheet_preserves_pending_targets_and_separate_items(self):
        root = r.ei.ROOT
        worksheet = json.loads((root / 'data/clinical-terminology-review.json').read_text())
        plan = json.loads((root / 'data/clinical-candidate-plan.json').read_text())
        pin = json.loads((root / 'data/clinical-source-demo-pin.json').read_text())
        self.assertEqual(worksheet['status'], 'PENDING_DOMAIN_REVIEW')
        self.assertEqual(worksheet['dataset_id'], plan['dataset_id'])
        self.assertEqual(worksheet['source_dictionary_sha256'], pin['files']['d_items']['file_sha256'])
        self.assertFalse(worksheet['clinical_mapping_verified'])
        self.assertFalse(worksheet['executable_mapping_pack'])
        self.assertFalse(worksheet['pool_measurement_items'])
        expected = [plan['treatment']] + plan['measurements']
        self.assertEqual([(e['itemid'], e['dictionary_label']) for e in worksheet['entries']],
                         [(e['itemid'], e['dictionary_label']) for e in expected])
        for entry in worksheet['entries']:
            for key in ('target_system', 'target_version', 'target_code', 'target_concept_iri',
                        'target_record_class_iri', 'mapping_relation', 'reviewer', 'decision'):
                self.assertIsNone(entry[key])
            self.assertEqual(entry['evidence'], [])

    def test_committed_report_reproduces_with_actual_rust_and_unmapped_control(self):
        report = json.loads((r.ei.ROOT / 'verification/reviewed-record-mappings-report.json').read_text())
        self.assertEqual(report, verify.verify())
        self.assertTrue(report['synthetic_only']); self.assertFalse(report['public_demo_mapping_executed'])
        self.assertEqual(report['control_certain_synthetic_patient_ids'], [])
        self.assertTrue(report['record_selector_derived_not_asserted'])


if __name__ == '__main__': unittest.main()
