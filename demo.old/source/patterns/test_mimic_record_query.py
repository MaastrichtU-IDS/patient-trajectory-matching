"""Source-to-RDF-to-Rust-to-temporal query acceptance and failure gates."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, RDF, URIRef

from . import bounded_intervals as bt
from . import exact_intervals as ei
from . import mimic_record_query as pipeline
from . import patient_local as local
from . import semantic_support as semantic
from . import verify_mimic_record_query as verifier
from .test_mimic_inputevents import dimension, fixtures, row, write_tables


class RecordQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.tables = fixtures()
        self.tables['inputevents'].append(row(starttime='2150-01-01 11:00:00', endtime='2150-01-01 12:00:00', orderid='201'))
        self.request = json.loads((ei.ROOT / 'examples/mimic-record-query/synthetic-request.json').read_text())

    def run_query(self):
        write_tables(self.folder, self.tables)
        return pipeline.run(self.folder, self.request)

    def episode_parts(self):
        result = self.run_query()
        episode = result['episodes'][0]
        graph = Graph().parse(data=episode['graph_turtle'], format='turtle')
        return local.prepare_graph(graph), episode['run']['context']['query'], episode['semantic_module']

    def test_end_to_end_real_rust_with_inferred_item_groups_and_pro_evidence(self):
        result = self.run_query()
        self.assertEqual(result['summary']['status'], 'COMPLETED_RECORDED_QUERY')
        self.assertEqual(result['matched_patient_ids'], ['1'])
        self.assertTrue(result['summary']['reference_verified'])
        episode = result['episodes'][0]; run = episode['run']
        self.assertNotIn('constructed://', episode['graph_turtle'])
        self.assertIn('derived-from:sha256:', episode['graph_turtle'])
        self.assertEqual(run['status'], 'READY')
        self.assertEqual(run['profile'], local.SEMANTIC_PROFILE)
        groups = {s['class_iri'] for s in run['context']['query']['slots']}
        self.assertTrue(groups.isdisjoint(a['class'] for a in episode['semantic_module']['class_assertions']))
        graph = Graph().parse(data=episode['graph_turtle'], format='turtle')
        binding = next(b for b in run['matching']['trajectories'][0]['bindings'] if b['status'] == 'CERTAIN')
        for witness in binding['slots'].values():
            self.assertEqual(witness['semantic_support']['status'], 'ENTAILED')
            self.assertIn(witness['event_id'], episode['row_evidence'])
            self.assertIn((URIRef(witness['process']), ei.S.hasParticipant, URIRef(witness['patient_role'])), graph)
            self.assertIn((URIRef(witness['patient_role']), ei.S.isFeatureOf, URIRef(witness['patient_bearer'])), graph)

    def test_recorded_source_times_never_become_verified_clinical_times(self):
        result = self.run_query(); summary = result['summary']
        for key in ('clinical_mapping_verified', 'source_history_verified', 'physical_elapsed_time_verified'):
            self.assertFalse(summary[key]); self.assertFalse(result['episodes'][0]['run'][key])
        self.assertEqual(summary['context']['time_semantics'], 'recorded_source_intervals')
        source = result['episodes'][0]['run']['matching']['evidence']['source']
        self.assertTrue(all(e['status'] == 'recorded' and e['event_kind'] == 'recorded_input_segment' for e in source['events']))
        for evidence in result['episodes'][0]['row_evidence'].values():
            self.assertIsNone(evidence['source_available_at']); self.assertEqual(evidence['clinical_precision'], 'unverified')
        self.assertFalse(result['admission']['summary']['matcher_ready'])

    def test_record_class_does_not_entail_infusion(self):
        snapshot, _, _ = self.episode_parts()
        query = {'profile': local.RECORD_QUERY_PROFILE, 'id': 'clinical',
                 'slots': [{'id': 'a', 'class_iri': str(bt.BT.Infusion)}], 'constraints': []}
        self.assertEqual(local.execute(snapshot, query)['possible_patient_ids'], [])
        source = deepcopy(snapshot.source); source['events'][0]['status'] = 'performed'
        for v in source['variables']: del v['lower_us'], v['upper_us']
        with self.assertRaises(ei.ContractError): local.prepare(source)
        source['profile'] = bt.PROFILE_ID
        with self.assertRaises(ei.ContractError): bt.prepare(source)

    def test_all_rows_have_admission_and_query_outcomes(self):
        self.tables['inputevents'] += [row(), row(statusdescription='Rewritten'), row(subject_id='2')]
        self.tables['d_items'].append(dimension('d_items', itemid='1001', label='Other', linksto='inputevents'))
        self.tables['inputevents'].append(row(itemid='1001'))
        result = self.run_query()
        self.assertEqual(sum(result['summary']['query_outcome_counts'].values()), 6)
        self.assertEqual(len({r['record_id'] for r in result['query_reconciliation']}), 6)
        self.assertEqual(result['summary']['query_outcome_counts'], {'NOT_ADMITTED': 3, 'OUTSIDE_ITEM_SCOPE': 1, 'SELECTED_RECORD': 2})
        self.assertEqual(result['episodes'][0]['selected_records'], 2)

    def test_roster_preserves_empty_and_nonmatching_episodes(self):
        self.tables['icustays'].append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        result = self.run_query()
        self.assertEqual(len(result['episodes']), 2)
        self.assertEqual(result['episodes'][1]['status'], 'NO_ADMITTED_RECORD_MATCH')
        self.assertIsNone(result['episodes'][1]['run'])
        self.assertEqual(result['summary']['represented_patients'], 2)

    def test_explicit_patient_scope_and_unknown_patient_validation(self):
        self.tables['icustays'].append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.tables['inputevents'].append(row(subject_id='2', hadm_id='20', stay_id='200'))
        self.request['patient_ids'] = ['1']
        result = self.run_query()
        self.assertEqual(result['summary']['query_outcome_counts']['OUTSIDE_PATIENT_SCOPE'], 1)
        self.assertEqual(len(result['episodes']), 1)
        self.request['patient_ids'] = ['3']
        with self.assertRaisesRegex(ei.ContractError, 'UNKNOWN_REQUESTED_PATIENT'): self.run_query()

    def test_origin_uses_full_staging_before_item_filtering(self):
        self.tables['d_items'].append(dimension('d_items', itemid='1001', linksto='inputevents'))
        self.tables['inputevents'].append(row(itemid='1001', starttime='2149-12-31 10:00:00', endtime='2149-12-31 11:00:00'))
        result = self.run_query()
        self.assertEqual(result['origins']['1']['label'], '2149-12-31 10:00:00')
        self.assertNotIn(result['origins']['1']['source_record_id'], [r['record_id'] for r in result['query_reconciliation'] if r['query_outcome'] == 'SELECTED_RECORD'])
        self.assertEqual(result['matched_patient_ids'], ['1'])

    def test_clock_origin_consistent_across_patient_stays_without_cross_stay_binding(self):
        self.tables['icustays'].append(dimension('icustays', subject_id='1', hadm_id='11', stay_id='101'))
        self.tables['inputevents'][1].update(hadm_id='11', stay_id='101')
        result = self.run_query()
        self.assertEqual(result['matched_patient_ids'], [])
        origins = [e['run']['matching']['evidence']['source']['clocks'][0] for e in result['episodes']]
        self.assertEqual(origins[0], origins[1])

    def test_item_group_union_is_explicit_and_not_label_inference(self):
        self.tables['d_items'][0]['label'] = 'Antibiotic-looking arbitrary label'
        self.tables['d_items'].append(dimension('d_items', itemid='1001', label='Same arbitrary label', linksto='inputevents'))
        self.tables['inputevents'][1]['itemid'] = '1001'
        self.request['slots'][1]['itemids'] = ['1000', '1001']
        result = self.run_query()
        self.assertEqual(result['matched_patient_ids'], ['1'])
        self.tables['d_items'].append(dimension('d_items', itemid='1002', linksto='inputevents'))
        self.request['slots'][1]['itemids'] = ['1002']
        self.assertEqual(self.run_query()['matched_patient_ids'], [])
        self.request['slots'][1]['itemids'] = ['9999']
        with self.assertRaisesRegex(ei.ContractError, 'UNKNOWN_INPUT_ITEM_SELECTOR'): self.run_query()

    def test_all_operators_and_inclusive_gap_agree_with_reference(self):
        for op, start in [('before', '2150-01-01 11:00:01'), ('meets', '2150-01-01 11:00:00'),
                          ('overlaps', '2150-01-01 10:30:00'), ('gap', '2150-01-01 11:00:01')]:
            self.tables['inputevents'][1]['starttime'] = start
            self.request['constraints'] = [{'id': 'c', 'operator': op, 'left': 'a', 'right': 'b'}]
            if op == 'gap': self.request['constraints'][0].update(min_gap_us=1000000, max_gap_us=1000000)
            with self.subTest(op=op):
                result = self.run_query()
                self.assertTrue(result['summary']['reference_verified'])
                self.assertEqual(result['matched_patient_ids'], ['1'])

    def test_required_distinct_records_not_a_self_match(self):
        self.tables['inputevents'] = [row()]
        self.request['constraints'] = []
        self.assertEqual(self.run_query()['matched_patient_ids'], [])

    def test_empty_selected_source_is_a_record_scope_no_match(self):
        self.tables['d_items'].append(dimension('d_items', itemid='1002', linksto='inputevents'))
        self.request['slots'][1]['itemids'] = ['1002']
        with patch.object(local, 'execute_semantic', side_effect=AssertionError('No backend required')):
            result = self.run_query()
        self.assertEqual(result['matched_patient_ids'], [])
        self.assertEqual(result['episodes'][0]['source_coverage'], 'ADMITTED_RECORDS_ONLY')
        self.assertEqual(result['summary']['clinical_knowledge_status'], 'UNKNOWN')

    def test_rejected_matching_row_does_not_become_a_clinical_absence_claim(self):
        self.tables['inputevents'][1]['statusdescription'] = 'Rewritten'
        result = self.run_query()
        self.assertEqual(result['matched_patient_ids'], [])
        self.assertEqual(result['summary']['query_outcome_counts']['NOT_ADMITTED'], 1)
        self.assertEqual(result['summary']['clinical_knowledge_status'], 'UNKNOWN')

    def test_event_limit_blocks_instead_of_truncating(self):
        with patch.object(pipeline, 'MAX_EVENTS', 1): result = self.run_query()
        self.assertEqual(result['summary']['status'], 'BLOCKED_INCOMPLETE_EXECUTION')
        self.assertIsNone(result['matched_patient_ids']); self.assertIsNone(result['summary']['matched_patients'])
        self.assertEqual(result['episodes'][0]['status'], 'BLOCKED_RESOURCE_LIMIT')
        self.assertEqual(result['episodes'][0]['selected_records'], 2)

    def test_binding_and_roster_limits_enforced(self):
        with patch.object(pipeline, 'MAX_BINDINGS', 1): result = self.run_query()
        self.assertFalse(result['summary']['search_complete_over_admitted_records'])
        with patch.object(pipeline, 'MAX_EPISODES', 0), self.assertRaisesRegex(ei.ContractError, 'EPISODE_LIMIT'):
            self.run_query()

    def test_backend_failure_blocks_the_authoritative_cohort(self):
        with patch.object(semantic, 'run_backend', return_value={'status': 'BACKEND_TIMEOUT'}):
            result = self.run_query()
        self.assertEqual(result['episodes'][0]['status'], 'BLOCKED_SEMANTICS')
        self.assertIsNone(result['matched_patient_ids'])
        self.assertFalse(result['summary']['reference_verified'])

    def test_reference_disagreement_blocks_the_authoritative_cohort(self):
        with patch.object(pipeline, 'reference_bindings', return_value=set()): result = self.run_query()
        self.assertEqual(result['episodes'][0]['status'], 'BLOCKED_REFERENCE_DISAGREEMENT')
        self.assertIsNone(result['matched_patient_ids'])

    def test_mode_policy_selector_and_scope_errors_rejected(self):
        with self.assertRaises(ei.ContractError): local.prepare([])
        with self.assertRaises(ei.ContractError): local.validate_query([])
        changes = [({'mode': 'source_as_known'}), ({'time_policy': 'clinical-exact'}),
                   ({'patient_ids': ['1', '1']}), ({'extra': True})]
        for change in changes:
            request = {**self.request, **change}
            with self.subTest(change=change), self.assertRaises(ei.ContractError): pipeline.validate_request(request)
        for mutate in ('duplicate-slot', 'duplicate-item', 'unknown-reference', 'float-gap'):
            request = deepcopy(self.request)
            if mutate == 'duplicate-slot': request['slots'][1]['id'] = 'a'
            if mutate == 'duplicate-item': request['slots'][0]['itemids'] *= 2
            if mutate == 'unknown-reference': request['constraints'][0]['right'] = 'absent'
            if mutate == 'float-gap': request['constraints'][0].update(operator='gap', min_gap_us=0, max_gap_us=1.0)
            with self.subTest(mutate=mutate), self.assertRaises(ei.ContractError): pipeline.validate_request(request)

    def test_local_semantic_gate_stale_module_inconsistency_and_profile_errors(self):
        snapshot, query, module = self.episode_parts()
        stale = deepcopy(module); stale['source_sha256'] = '0' * 64
        with self.assertRaisesRegex(ei.ContractError, 'STALE_SEMANTIC_SOURCE'): local.execute_semantic(snapshot, query, stale)
        bad = deepcopy(query); bad['profile'] = semantic.QUERY_PROFILE
        with self.assertRaisesRegex(ei.ContractError, 'UNSUPPORTED_SEMANTIC_QUERY_PROFILE'): local.execute_semantic(snapshot, bad, module)
        inconsistent = deepcopy(module)
        inconsistent['classes'].append(str(bt.BT.RecordedInputSegment))
        inconsistent['disjoint'] = [{'id': 'clash', 'classes': [pipeline.N + 'item_1000', str(bt.BT.RecordedInputSegment)]}]
        result = local.execute_semantic(snapshot, query, inconsistent)
        self.assertEqual(result['status'], 'INCONSISTENT_ONTOLOGY'); self.assertIsNone(result['matching'])

    def test_local_semantic_module_supports_role_scoped_existential_rules(self):
        snapshot, query, module = self.episode_parts()
        target = pipeline.N + 'PatientCapacityRecord'
        module['classes'] += [target, str(ei.EX.PatientRole)]
        module['rules'].append({'id': 'patient-capacity', 'if': {'some': {'property': str(ei.S.hasParticipant),
            'filler': {'class': str(ei.EX.PatientRole)}}}, 'then': target})
        query['slots'][0]['class_iri'] = target
        result = local.execute_semantic(snapshot, query, module)
        self.assertEqual(result['status'], 'READY')
        self.assertEqual(result['matching']['certain_patient_ids'], ['1'])
        self.assertFalse(result['matching']['physical_elapsed_time_verified'])

    def test_local_rust_semantics_preserves_bounded_certainty_and_possibility(self):
        source = json.loads((ei.ROOT / 'examples/patient-local/source.json').read_text())
        snapshot = local.prepare(source)
        group = pipeline.N + 'SelectedInfusion'
        module = {'profile': semantic.PROFILE, 'source_sha256': ei.digest(ei.canonical(snapshot.source)),
            'classes': [str(bt.BT.Infusion), str(bt.BT.SpecimenCollection), group],
            'individuals': [], 'class_assertions': [], 'property_assertions': [], 'disjoint': [],
            'rules': [{'id': 'select', 'if': {'class': str(bt.BT.Infusion)}, 'then': group}]}
        query = json.loads((ei.ROOT / 'examples/patient-local/query.json').read_text())
        query['profile'] = local.SEMANTIC_QUERY_PROFILE
        query['slots'][0]['class_iri'] = group
        answer = local.execute_semantic(snapshot, query, module)
        self.assertEqual(answer['status'], 'READY')
        self.assertEqual(answer['matching']['certain_patient_ids'], ['P1'])
        self.assertEqual(answer['matching']['possible_patient_ids'], ['P1', 'P2'])
        self.assertEqual(answer['matching']['context']['gap_semantics'], 'local_calendar_coordinate_difference')

    def test_summary_and_row_evidence_bind_request_files_and_mapping(self):
        first = self.run_query()
        self.assertEqual(first['summary']['context_id'], ei.digest(ei.canonical(first['summary']['context'])))
        self.request['constraints'][0]['operator'] = 'before'
        second = self.run_query()
        self.assertNotEqual(first['summary']['context_id'], second['summary']['context_id'])
        for episode in first['episodes']:
            for evidence in episode['row_evidence'].values():
                original = next(r for r in first['admission']['reconciliation'] if r['record_id'] == evidence['record_id'])
                self.assertEqual(evidence['source'], original['source'])

    def test_demo_aggregate_pin_binds_current_code_without_patient_data(self):
        report = json.loads((ei.ROOT / 'verification/mimic-record-query-demo-report.json').read_text())
        self.assertFalse(report['patient_rows_included']); self.assertFalse(report['full_mimic_analyzed'])
        self.assertEqual(verifier.demo.verify_summary(report['admission']['summary']), report['admission'])
        self.assertEqual(report['request_sha256'], ei.digest(verifier.REQUEST.read_text()))
        self.assertEqual(report['verifier_sha256'], ei.digest(Path(verifier.__file__).read_text()))
        for path, sha in report['query_summary']['context']['artifacts'].items():
            self.assertEqual(sha, ei.digest((ei.ROOT / path).read_text()), path)
        self.assertEqual(report['query_summary']['status'], 'COMPLETED_RECORDED_QUERY')
        self.assertEqual(report['query_summary']['represented_icu_stays'], 140)
        self.assertEqual(report['query_summary']['query_outcome_counts']['SELECTED_RECORD'], 290)
        self.assertNotIn('episodes', report); self.assertNotIn('matched_patient_ids', report)

    def test_cli_success_and_invalid_input_preserve_previous_output(self):
        write_tables(self.folder, self.tables)
        request = self.folder / 'request.json'; request.write_text(json.dumps(self.request))
        output = self.folder / 'result.json'
        command = [sys.executable, '-m', 'patterns.mimic_record_query', '--input-dir', str(self.folder),
                   '--request', str(request), '--output', str(output)]
        subprocess.run(command, check=True, capture_output=True)
        previous = output.read_bytes()
        request.write_text('{}')
        result = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('INVALID_INPUT', result.stderr)
        self.assertEqual(previous, output.read_bytes())
        request.write_text(json.dumps(self.request))
        (self.folder / 'inputevents.csv').write_text(chr(34) + 'unterminated')
        malformed = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(malformed.returncode, 2)
        self.assertIn('INVALID_INPUT', malformed.stderr)
        self.assertEqual(previous, output.read_bytes())


if __name__ == '__main__':
    unittest.main(verbosity=2)
