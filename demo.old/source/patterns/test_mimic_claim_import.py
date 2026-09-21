"""Source provenance, complete import accounting, and explicit acceptance boundary."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from rdflib import RDF
from . import claim_isolation, claim_rdf as cr, exact_intervals as ei
from . import local_claim_projection as projection, patient_local as local
from . import mimic_claim_import as pipeline, mimic_inputevents as admission
from .test_mimic_inputevents import dimension, fixtures, row, write_tables


class ClaimImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name); self.tables = fixtures()
        self.request = json.loads((ei.ROOT / 'examples/mimic-claim-import/synthetic-request.json').read_text())

    def run_import(self):
        write_tables(self.folder, self.tables)
        return pipeline.run(self.folder, self.request)

    def query(self):
        return {'profile': local.SEMANTIC_QUERY_PROFILE, 'id': 'item',
                'slots': [{'id': 'a', 'class_iri': pipeline.ITEM_NS + '1000'}], 'constraints': []}

    def accept(self, episode):
        policy = deepcopy(episode['acceptance_policy'])
        policy['decisions'] = [{'id': 'd_' + str(i), 'claim_id': c['id'], 'claim_sha256': cr.digest(c),
            'action': 'accept', 'supersedes': None, 'reason': 'Synthetic test analysis selection'}
            for i, c in enumerate(episode['store']['claims'])]
        return policy

    def test_import_never_calls_occurrence_export_or_reasoning(self):
        with patch.object(local, 'export_source', side_effect=AssertionError('occurrence export')), \
             patch.object(local, 'execute_semantic', side_effect=AssertionError('reasoning')):
            result = self.run_import()
        self.assertEqual(result['summary']['status'], 'COMPLETED_PENDING_IMPORT')
        self.assertEqual(result['summary']['accepted_claims'], 0)
        self.assertIsNone(result['summary']['matching'])
        episode = result['episodes'][0]
        self.assertEqual(episode['acceptance_policy']['decisions'], [])
        self.assertEqual(episode['isolation']['logical_axioms_checked'], 137)

    def test_description_rdf_roundtrip_and_empty_process_model(self):
        episode = self.run_import()['episodes'][0]; store = episode['store']
        graph = cr.encode(store, fields=cr.LOCAL_FIELDS)
        self.assertEqual(cr.decode(graph, fields=cr.LOCAL_FIELDS), store)
        self.assertEqual(claim_isolation.check(graph, local=True), episode['isolation'])
        self.assertNotIn(ei.S.Process, graph.objects(None, RDF.type))
        self.assertFalse(list(graph.triples((None, ei.S.hasParticipant, None))))
        self.assertEqual(episode['store_sha256'], cr.digest(store))

    def test_empty_policy_produces_empty_view_even_with_matching_record(self):
        result = self.run_import(); ep = result['episodes'][0]
        run = projection.execute(ep['store'], ep['acceptance_policy'], result['semantic_policy'], self.query())
        self.assertEqual(run['status'], 'EMPTY_ACCEPTED_VIEW')
        self.assertIsNone(run['source']); self.assertIsNone(run['matching'])
        self.assertEqual(run['selection']['claims'][0]['state'], 'PENDING')

    def test_explicit_test_acceptance_connects_to_real_checked_rust(self):
        result = self.run_import(); ep = result['episodes'][0]
        run = projection.execute(ep['store'], self.accept(ep), result['semantic_policy'], self.query())
        self.assertEqual(run['status'], 'READY')
        self.assertEqual(run['matching']['certain_patient_ids'], ['1'])
        self.assertFalse(run['clinical_mapping_verified'])
        self.assertEqual(run['time_semantics'], 'recorded_source_intervals')
        self.assertEqual(run['source']['events'][0]['event_kind'], 'recorded_input_segment')

    def test_withdrawal_removes_previously_accepted_source_claim(self):
        result = self.run_import(); ep = result['episodes'][0]; policy = self.accept(ep)
        policy['decisions'].append({**policy['decisions'][0], 'id': 'withdraw',
                                   'action': 'withdraw', 'supersedes': 'd_0'})
        run = projection.execute(ep['store'], policy, result['semantic_policy'], self.query())
        self.assertEqual(run['status'], 'EMPTY_ACCEPTED_VIEW')

    def test_actual_file_and_row_hashes_bind_all_source_dependencies(self):
        result = self.run_import(); ep = result['episodes'][0]; claim = ep['store']['claims'][0]
        evidence = ep['claim_provenance'][claim['id']]
        self.assertEqual(claim['source_sha256'], admission.digest((self.folder / 'inputevents.csv').read_bytes()))
        for table, field in [('inputevents', 'source'), ('d_items', 'item_source'), ('icustays', 'stay_source')]:
            self.assertEqual(evidence[field]['row_sha256'], admission.digest(admission.canonical(self.tables[table][0])))
            self.assertEqual(evidence[field]['file_sha256'], admission.digest((self.folder / (table + '.csv')).read_bytes()))
        self.assertEqual(evidence['origin_source'], evidence['source'])
        self.assertEqual(evidence['claim_sha256'], cr.digest(claim))
        self.assertEqual(result['import_reconciliation'][0]['claim_id'], claim['id'])

    def test_clock_labels_recording_time_and_unknowns_are_preserved(self):
        result = self.run_import(); ep = result['episodes'][0]
        claim = ep['store']['claims'][0]; evidence = ep['claim_provenance'][claim['id']]
        variable = claim['bundle']['variables'][0]
        self.assertEqual(variable['local_lower'], self.tables['inputevents'][0]['starttime'])
        self.assertEqual(variable['local_upper'], variable['local_lower'])
        self.assertNotIn('lower_us', variable)
        self.assertEqual(evidence['recorded_at']['raw_value'], self.tables['inputevents'][0]['storetime'])
        self.assertIsNone(evidence['source_available_at'])
        for field in ('clinical_mapping_verified', 'source_history_verified', 'physical_elapsed_time_verified'):
            self.assertFalse(result['summary'][field])

    def test_missing_storetime_does_not_invent_availability(self):
        self.tables['inputevents'][0]['storetime'] = ''
        ep = self.run_import()['episodes'][0]
        evidence = next(iter(ep['claim_provenance'].values()))
        self.assertIsNone(evidence['recorded_at']['raw_value']); self.assertIsNone(evidence['source_available_at'])

    def test_complete_ledger_includes_duplicates_invalid_unsupported_and_scope(self):
        self.tables['d_items'].append(dimension('d_items', itemid='1001', linksto='inputevents'))
        self.tables['icustays'].append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.tables['inputevents'] += [row(), row(subject_id='9'), row(statusdescription='Rewritten'),
            row(itemid='1001'), row(subject_id='2', hadm_id='20', stay_id='200')]
        self.request['patient_ids'] = ['1']
        result = self.run_import(); ledger = result['import_reconciliation']
        self.assertEqual(len(ledger), 6); self.assertEqual(len({r['record_id'] for r in ledger}), 6)
        self.assertEqual(result['summary']['import_outcome_counts'],
                         {'NOT_ADMITTED': 3, 'OUTSIDE_ITEM_SCOPE': 1, 'OUTSIDE_PATIENT_SCOPE': 1, 'PENDING_CLAIM': 1})
        self.assertEqual(ledger[1]['duplicate_of'], ledger[0]['record_id'])
        self.assertEqual(ledger[1]['admission_outcome'], 'DUPLICATE_ROW')
        self.assertEqual(result['summary']['selected_records'], 1)

    def test_empty_roster_stay_is_preserved_without_absence_claim(self):
        self.tables['icustays'].append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        result = self.run_import(); ep = result['episodes'][1]
        self.assertEqual(ep['status'], 'NO_SELECTED_ADMITTED_RECORDS')
        self.assertIsNone(ep['store']); self.assertEqual(result['summary']['represented_patients'], 2)
        self.assertEqual(result['summary']['clinical_knowledge_status'], 'UNKNOWN')

    def test_no_admitted_segments_is_complete_empty_import(self):
        self.tables['inputevents'][0]['statusdescription'] = 'Rewritten'
        result = self.run_import()
        self.assertEqual(result['summary']['status'], 'COMPLETED_PENDING_IMPORT')
        self.assertEqual(result['summary']['described_claims'], 0)
        self.assertEqual(result['origins'], {})

    def test_origin_precedes_item_filter_and_is_shared_across_stays(self):
        self.tables['d_items'].append(dimension('d_items', itemid='1001', linksto='inputevents'))
        self.tables['icustays'].append(dimension('icustays', subject_id='1', hadm_id='11', stay_id='101'))
        self.tables['inputevents'] += [row(itemid='1001', starttime='2149-12-31 10:00:00', endtime='2149-12-31 11:00:00'),
                                     row(hadm_id='11', stay_id='101')]
        result = self.run_import(); clocks = [e['store']['clocks'] for e in result['episodes']]
        self.assertEqual(clocks[0], clocks[1]); self.assertEqual(clocks[0][0]['origin'], '2149-12-31T10:00:00')
        self.assertEqual(result['import_reconciliation'][1]['import_outcome'], 'OUTSIDE_ITEM_SCOPE')
        self.assertEqual(clocks[0][0]['origin_source_key'], result['import_reconciliation'][1]['record_id'])

    def test_patient_scopes_never_share_stores_or_clocks(self):
        self.tables['icustays'].append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.tables['inputevents'].append(row(subject_id='2', hadm_id='20', stay_id='200'))
        result = self.run_import()
        for ep in result['episodes']:
            self.assertEqual({c['patient_id'] for c in ep['store']['claims']}, {ep['patient_id']})
            self.assertEqual(ep['store']['clocks'][0]['scope'], 'patient:' + ep['patient_id'])

    def test_limit_blocks_whole_stay_without_truncation(self):
        self.tables['inputevents'].append(row(orderid='201'))
        with patch.object(pipeline, 'MAX_CLAIMS', 1): result = self.run_import()
        self.assertEqual(result['summary']['status'], 'BLOCKED_INCOMPLETE_IMPORT')
        self.assertTrue(result['summary']['reconciliation_complete'])
        self.assertFalse(result['summary']['description_complete_over_selected_records'])
        self.assertEqual(result['summary']['selected_records'], 2)
        self.assertEqual(result['summary']['described_claims'], 0)
        self.assertEqual(result['summary']['import_outcome_counts'], {'BLOCKED_RESOURCE_LIMIT': 2})
        self.assertIsNone(result['episodes'][0]['store'])

    def test_other_stays_survive_one_blocked_stay(self):
        self.tables['icustays'].append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.tables['inputevents'] += [row(orderid='201'), row(subject_id='2', hadm_id='20', stay_id='200')]
        with patch.object(pipeline, 'MAX_CLAIMS', 1): result = self.run_import()
        self.assertEqual(result['summary']['described_claims'], 1)
        self.assertEqual(result['episodes'][1]['status'], 'PENDING_CLAIMS')
        self.assertEqual(result['summary']['status'], 'BLOCKED_INCOMPLETE_IMPORT')

    def test_encoded_document_limit_is_also_accounted(self):
        with patch.object(cr, 'MAX_BYTES', 1): result = self.run_import()
        self.assertEqual(result['episodes'][0]['reason'], 'CLAIM_DOCUMENT_LIMIT')
        self.assertEqual(result['import_reconciliation'][0]['import_outcome'], 'BLOCKED_RESOURCE_LIMIT')

    def test_isolation_failure_is_not_mislabeled_as_resource_limit(self):
        with patch.object(claim_isolation, 'check', side_effect=ei.ContractError('CLAIM_COUNTERMODEL_FAILED')):
            with self.assertRaisesRegex(ei.ContractError, 'CLAIM_COUNTERMODEL_FAILED'): self.run_import()

    def test_dimension_change_during_run_is_rejected(self):
        original = admission.read_table; calls = []
        def changed(path, table):
            rows, manifest = original(path, table); calls.append(table)
            if table == 'icustays' and calls.count(table) == 2: manifest['file_sha256'] = '0' * 64
            return rows, manifest
        with patch.object(admission, 'read_table', side_effect=changed), self.assertRaisesRegex(ei.ContractError, 'SOURCE_CHANGED'):
            self.run_import()

    def test_changing_dictionary_changes_context_and_store_even_when_claim_unchanged(self):
        first = self.run_import(); self.tables['d_items'][0]['label'] = 'A clinical-sounding label'
        second = self.run_import()
        self.assertNotEqual(first['summary']['context_id'], second['summary']['context_id'])
        self.assertNotEqual(first['episodes'][0]['store_sha256'], second['episodes'][0]['store_sha256'])
        self.assertEqual(first['episodes'][0]['store']['claims'], second['episodes'][0]['store']['claims'])
        self.assertEqual(second['semantic_policy']['rules'], [])
        self.assertEqual(second['semantic_policy']['classes'], [pipeline.ITEM_NS + '1000'])

    def test_dataset_namespace_is_stable_and_original_label_retained(self):
        first = self.run_import(); self.assertEqual(first, self.run_import())
        self.assertEqual(first['summary']['context']['request']['dataset_id'], 'synthetic-mimic-demo-2.2')
        self.request['dataset_id'] = 'another-source'
        second = self.run_import()
        self.assertNotEqual(first['episodes'][0]['store']['dataset_id'], second['episodes'][0]['store']['dataset_id'])
        self.assertNotEqual(first['episodes'][0]['store']['claims'][0]['source_id'], second['episodes'][0]['store']['claims'][0]['source_id'])

    def test_request_profile_history_selector_and_extra_fields_rejected(self):
        for change in ({'profile': 'other'}, {'mode': 'source_as_known'}, {'time_policy': 'clinical-exact'},
                       {'itemids': []}, {'itemids': ['1000', '1000']}, {'patient_ids': ['1', '1']}, {'accept': True}):
            with self.subTest(change=change), self.assertRaises(ei.ContractError):
                pipeline.validate_request({**self.request, **change})
        for change, error in [({'itemids': ['9999']}, 'UNKNOWN_INPUT_ITEM'),
                              ({'patient_ids': ['9']}, 'UNKNOWN_REQUESTED_PATIENT')]:
            self.request.update(change)
            with self.assertRaisesRegex(ei.ContractError, error): self.run_import()
            self.request = json.loads((ei.ROOT / 'examples/mimic-claim-import/synthetic-request.json').read_text())

    def test_roster_limit_rejects_entire_run(self):
        with patch.object(pipeline, 'MAX_EPISODES', 0), self.assertRaisesRegex(ei.ContractError, 'EPISODE_LIMIT'):
            self.run_import()

    def test_cli_success_invalid_request_overwrite_and_partial_result(self):
        write_tables(self.folder, self.tables)
        request = self.folder / 'request.json'; output = self.folder / 'result.json'
        request.write_text(json.dumps(self.request))
        args = ['--input-dir', str(self.folder), '--request', str(request), '--output', str(output)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(pipeline.main(args), 0); previous = output.read_bytes()
            request.write_text('{}'); self.assertEqual(pipeline.main(args), 2)
            self.assertEqual(output.read_bytes(), previous)
            request.write_text(json.dumps(self.request))
            original = request.read_bytes()
            self.assertEqual(pipeline.main(args[:-1] + [str(request)]), 2)
            self.assertEqual(request.read_bytes(), original)
            with patch.object(pipeline, 'MAX_CLAIMS', 0): self.assertEqual(pipeline.main(args), 2)
        self.assertEqual(json.loads(output.read_text())['summary']['status'], 'BLOCKED_INCOMPLETE_IMPORT')

    def test_full_32_claim_store_and_next_row_boundary(self):
        self.tables['inputevents'] = [row(orderid=str(200 + i)) for i in range(32)]
        result = self.run_import()
        self.assertEqual(result['summary']['described_claims'], 32)
        self.assertEqual(len(result['episodes'][0]['store']['claims']), 32)
        self.tables['inputevents'].append(row(orderid='999'))
        result = self.run_import()
        self.assertEqual(result['summary']['import_outcome_counts'], {'BLOCKED_RESOURCE_LIMIT': 33})

    def test_committed_public_demo_report_has_only_aggregate_verified_evidence(self):
        from . import verify_mimic_claim_import as verifier
        report = json.loads((ei.ROOT / 'verification/mimic-claim-import-demo-report.json').read_text())
        self.assertEqual(verifier.demo.verify_summary(report['admission']['summary']), report['admission'])
        self.assertFalse(report['patient_rows_included']); self.assertFalse(report['full_mimic_analyzed'])
        self.assertEqual(report['request_sha256'], ei.digest(verifier.REQUEST.read_text()))
        self.assertEqual(report['verifier_sha256'], ei.digest(Path(verifier.__file__).read_text()))
        summary = report['import_summary']
        for path, sha in summary['context']['artifacts'].items():
            self.assertEqual(sha, ei.digest((ei.ROOT / path).read_text()), path)
        self.assertEqual(summary['status'], 'COMPLETED_PENDING_IMPORT')
        self.assertEqual(summary['import_outcome_counts'],
                         {'NOT_ADMITTED': 9424, 'OUTSIDE_ITEM_SCOPE': 10690, 'PENDING_CLAIM': 290})
        self.assertEqual(summary['represented_icu_stays'], 140)
        self.assertEqual(report['isolation_summary']['verified_stores'], 31)
        self.assertEqual(report['isolation_summary']['logical_axioms_per_store'], [137])
        self.assertTrue(report['isolation_summary']['all_models_have_empty_process_and_time'])
        self.assertEqual(summary['accepted_claims'], 0)
        for forbidden in ('episodes', 'origins', 'import_reconciliation', 'claims', 'matched_patient_ids'):
            self.assertNotIn(forbidden, report); self.assertNotIn(forbidden, summary)


if __name__ == '__main__':
    unittest.main(verbosity=2)
