"""Scalar/point descriptions, source admission, acceptance and profile boundaries."""
from copy import deepcopy
from contextlib import redirect_stderr, redirect_stdout
import csv
from decimal import Decimal
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from rdflib import Graph, OWL, RDF, URIRef

from . import measurement_claims as mc, mimic_measurement_import as mi
from . import claim_rdf as cr, claim_isolation as isolation, exact_intervals as ei
from . import claim_projection as cp, local_claim_projection as lc, patient_local as local
from .test_mimic_inputevents import dimension


def row(**changes):
    return dict({'subject_id': '1', 'hadm_id': '10', 'stay_id': '100', 'caregiver_id': '',
        'charttime': '2150-01-01 09:40:00', 'storetime': '2150-01-02 12:00:00',
        'itemid': '1000', 'value': '58.00', 'valuenum': '58.00', 'valueuom': 'mmHg', 'warning': '0'}, **changes)


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.rows = [row()]
        self.stays = [dimension('icustays', subject_id='1', hadm_id='10', stay_id='100')]
        self.items = [dimension('d_items', itemid='1000', label='Synthetic pressure', linksto='chartevents', unitname='mmHg')]
        self.request = json.loads((ei.ROOT / 'examples/mimic-measurement/request.json').read_text())

    def write(self):
        for table, rows, header in [('chartevents', self.rows, mi.HEADER), ('icustays', self.stays, mi.source.HEADERS['icustays']),
                                    ('d_items', self.items, mi.source.HEADERS['d_items'])]:
            with (self.folder / (table + '.csv')).open('w', newline='') as handle:
                writer = csv.DictWriter(handle, fieldnames=header); writer.writeheader(); writer.writerows(rows)

    def run_import(self):
        self.write(); return mi.run(self.folder, self.request)

    def fixture(self):
        episode = self.run_import()['episodes'][0]
        return episode['store'], episode['acceptance_policy']

    def accept(self, store, policy):
        policy = deepcopy(policy)
        policy['decisions'] = [{'id': 'decision_' + str(i), 'claim_id': c['id'], 'claim_sha256': cr.digest(c),
            'action': 'accept', 'supersedes': None, 'reason': 'Synthetic selection only'} for i, c in enumerate(store['claims'])]
        return policy

    def test_import_is_pending_and_single_point_has_no_duration(self):
        store, policy = self.fixture(); claim = store['claims'][0]; event = claim['bundle']['events'][0]
        self.assertEqual(policy['decisions'], [])
        self.assertEqual(len(claim['bundle']['variables']), 1)
        self.assertIn('time_var', event); self.assertNotIn('start_var', event); self.assertNotIn('end_var', event)
        self.assertEqual(event['value_lexical'], '58.00'); self.assertEqual(event['unit_lexical'], 'mmHg')
        self.assertEqual(mc.select(store, policy)['status'], 'EMPTY_SELECTED_RECORDS')

    def test_rdf_roundtrip_and_141_axiom_isolation(self):
        store, _ = self.fixture(); graph = cr.encode(store, fields=cr.MEASUREMENT_FIELDS)
        self.assertEqual(cr.decode(graph, fields=cr.MEASUREMENT_FIELDS), store)
        report = isolation.check(graph, local=True, measurement=True)
        self.assertEqual(report['logical_axioms_checked'], 141)
        self.assertEqual(report['process_extension_size'], 0); self.assertEqual(report['temporal_extension_size'], 0)
        self.assertIn('ontology/measurement-claim-description-profile.ttl', report['extension_sha256'])
        self.assertFalse(list(graph.triples((None, ei.S.atTime, None))))
        self.assertFalse(list(graph.triples((None, ei.S.hasParticipant, None))))

    def test_old_claim_readers_and_interval_routes_reject_measurements(self):
        store, policy = self.fixture(); graph = cr.encode(store, fields=cr.MEASUREMENT_FIELDS)
        for action in (lambda: cr.decode(graph), lambda: cr.decode(graph, fields=cr.LOCAL_FIELDS),
                       lambda: cp.validate_store(store), lambda: lc.validate_store(store),
                       lambda: local.prepare(store), lambda: isolation.check(graph, local=True)):
            with self.assertRaises(ei.ContractError): action()

    def test_explicit_acceptance_preserves_raw_and_numeric_evidence_without_reasoning(self):
        store, policy = self.fixture()
        with patch.object(local, 'execute_semantic', side_effect=AssertionError('No reasoning')):
            result = mc.select(store, self.accept(store, policy))
        self.assertEqual(result['status'], 'SELECTED_MEASUREMENT_RECORDS')
        record = result['records'][0]
        self.assertEqual(record['numeric_value'], '58.00'); self.assertEqual(record['event']['value_lexical'], '58.00')
        self.assertEqual(record['lower_us'], 0); self.assertEqual(record['upper_us'], 0)
        self.assertFalse(result['temporal_query_supported']); self.assertIsNone(result['matching'])
        self.assertIsNone(result['accepted_graph_turtle'])
        self.assertTrue(record['event_supports']); self.assertTrue(record['time_supports'])

    def test_selection_rdf_has_same_context_and_result(self):
        store, policy = self.fixture(); policy = self.accept(store, policy)
        a = mc.select(store, policy); b = mc.select(cr.encode(store, fields=cr.MEASUREMENT_FIELDS), policy)
        self.assertEqual(a, b)

    def test_withdrawal_leaves_no_selected_measurement(self):
        store, policy = self.fixture(); policy = self.accept(store, policy)
        policy['decisions'].append({**policy['decisions'][0], 'id': 'withdraw', 'action': 'withdraw', 'supersedes': 'decision_0'})
        result = mc.select(store, policy)
        self.assertEqual(result['status'], 'EMPTY_SELECTED_RECORDS'); self.assertEqual(result['records'], [])

    def test_independent_support_survives_withdrawal(self):
        store, _ = self.fixture(); extra = deepcopy(store['claims'][0])
        extra.update(id='independent', source_id='another_source'); store['claims'].append(extra)
        policy = self.accept(store, mc.pending_policy(store))
        policy['decisions'].append({**policy['decisions'][0], 'id': 'withdraw', 'action': 'withdraw', 'supersedes': 'decision_0'})
        result = mc.select(store, policy)
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['event_supports'][0]['assertion_id'], 'independent')

    def test_conflicting_accepted_values_block_whole_view(self):
        store, _ = self.fixture(); extra = deepcopy(store['claims'][0]); extra.update(id='conflict', source_id='other')
        extra['bundle']['events'][0]['value_lexical'] = '72'; store['claims'].append(extra)
        result = mc.select(store, self.accept(store, mc.pending_policy(store)))
        self.assertEqual(result['status'], 'BLOCKED_ACCEPTED_CONFLICT'); self.assertIsNone(result['records'])

    def test_correction_chain_replaces_value_without_mutating_original(self):
        store, _ = self.fixture(); original = deepcopy(store['claims'][0]); extra = deepcopy(original); extra['id'] = 'corrected'
        extra['bundle']['events'][0]['value_lexical'] = '72'; store['claims'].append(extra)
        policy = self.accept(store, mc.pending_policy(store)); policy['decisions'][1]['supersedes'] = 'decision_0'
        result = mc.select(store, policy)
        self.assertEqual(result['records'][0]['numeric_value'], '72'); self.assertEqual(store['claims'][0], original)

    def test_missing_selected_time_dependency_blocks_without_partial_records(self):
        store, _ = self.fixture(); claim = store['claims'][0]; clock_claim = deepcopy(claim)
        clock_claim.update(id='clock_support', source_id='clock_source'); clock_claim['bundle']['events'] = []
        claim['bundle']['variables'] = []; store['claims'].append(clock_claim)
        policy = self.accept(store, mc.pending_policy(store)); policy['decisions'] = policy['decisions'][:1]
        result = mc.select(store, policy)
        self.assertEqual(result['status'], 'INVALID_SELECTED_MEASUREMENTS')
        self.assertEqual(result['reason'], 'MISSING_SELECTED_MEASUREMENT_TIME'); self.assertIsNone(result['records'])

    def test_cross_patient_time_reference_is_rejected_after_selection(self):
        self.stays.append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.rows.append(row(subject_id='2', hadm_id='20', stay_id='200'))
        result = self.run_import(); store = deepcopy(result['episodes'][0]['store']); other = result['episodes'][1]['store']
        store['clocks'] += other['clocks']; store['claims'] += other['claims']
        store['claims'][0]['bundle']['events'][0]['time_var'] = store['claims'][1]['bundle']['variables'][0]['id']
        selected = mc.select(store, self.accept(store, mc.pending_policy(store)))
        self.assertEqual(selected['reason'], 'MEASUREMENT_TIME_SCOPE_MISMATCH')

    def test_bounded_point_time_survives_without_interval_conversion(self):
        store, _ = self.fixture(); variable = store['claims'][0]['bundle']['variables'][0]
        variable.update(local_lower='2150-01-01 09:39:59', local_upper='2150-01-01 09:40:01')
        result = mc.select(store, self.accept(store, mc.pending_policy(store)))
        self.assertEqual((result['records'][0]['lower_us'], result['records'][0]['upper_us']), (-1000000, 1000000))
        self.assertNotIn('interval', result['records'][0])

    def test_rebased_origin_changes_coordinate_preserving_raw_time(self):
        store, _ = self.fixture(); store['clocks'][0]['origin'] = '2150-01-01T09:00:00'
        result = mc.select(store, self.accept(store, mc.pending_policy(store)))
        self.assertEqual(result['records'][0]['lower_us'], 2400000000)
        self.assertEqual(result['records'][0]['time_variable']['local_lower'], '2150-01-01 09:40:00')

    def test_exact_decimals_no_float_rounding_and_lexical_retention(self):
        for value in ('-0', '0', '-3.25', '1e-10', '12345678901234567890.12345678901234567890'):
            with self.subTest(value=value): self.assertEqual(mc.decimal_value(value), Decimal(value))
        for value in ('NaN', 'Infinity', '1e309', '1e-309', ' 1', '1,5', '<5', '', 1.0, True):
            with self.subTest(value=value), self.assertRaises(ei.ContractError): mc.decimal_value(value)

    def test_equal_values_keep_distinct_source_and_result_identities(self):
        self.rows.append(row(charttime='2150-01-01 10:30:00'))
        store, policy = self.fixture(); result = mc.select(store, self.accept(store, policy))
        self.assertEqual(len(result['records']), 2)
        self.assertEqual(len({r['event']['id'] for r in result['records']}), 2)
        self.assertEqual({r['numeric_value'] for r in result['records']}, {'58.00'})

    def test_units_are_never_filled_from_dictionary_or_converted(self):
        self.rows[0]['valueuom'] = ''; result = self.run_import()
        self.assertIn('MISSING_UNIT', result['reconciliation'][0]['reasons'])
        self.rows[0]['valueuom'] = 'kPa'; store, policy = self.fixture()
        record = mc.select(store, self.accept(store, policy))['records'][0]
        self.assertEqual(record['event']['unit_lexical'], 'kPa'); self.assertEqual(record['numeric_value'], '58.00')

    def test_qualified_text_and_numeric_disagreement_never_become_exact_scalars(self):
        for text, numeric, code in [('<5', '5', 'TEXTUAL_OR_QUALIFIED_VALUE'),
                                   ('15 Alert', '15', 'TEXTUAL_OR_QUALIFIED_VALUE'),
                                   ('58', '59', 'VALUE_NUMERIC_DISAGREEMENT')]:
            self.rows = [row(value=text, valuenum=numeric)]
            result = self.run_import(); self.assertEqual(result['summary']['described_claims'], 0)
            self.assertIn(code, result['reconciliation'][0]['reasons'])
        self.rows = [row(value='58', valuenum='58.00')]
        self.assertEqual(self.run_import()['summary']['described_claims'], 1)

    def test_warning_states_have_explicit_admission_outcomes(self):
        for warning, code in [('1', 'SOURCE_WARNING_FLAGGED'), ('', 'SOURCE_WARNING_UNKNOWN'), ('2', 'INVALID_WARNING_FLAG')]:
            self.rows = [row(warning=warning)]; result = self.run_import()
            self.assertEqual(result['summary']['described_claims'], 0)
            self.assertIn(code, result['reconciliation'][0]['reasons'])

    def test_missing_values_and_bad_times_are_accounted(self):
        for updates, code in [({'valuenum': ''}, 'MISSING_NUMERIC_VALUE'), ({'value': ''}, 'MISSING_VALUE_TEXT'),
                              ({'charttime': '2150-01-01'}, 'INVALID_LOCAL_TIMESTAMP:charttime'),
                              ({'storetime': 'invalid'}, 'INVALID_LOCAL_TIMESTAMP:storetime'),
                              ({'valuenum': 'NaN'}, 'INVALID_NUMERIC_VALUE')]:
            self.rows = [row(**updates)]; result = self.run_import()
            self.assertIn(code, result['reconciliation'][0]['reasons'])
            self.assertTrue(result['summary']['reconciliation_complete'])

    def test_earlier_or_missing_storetime_does_not_define_source_availability(self):
        for storetime in ('', '2150-01-01 08:00:00'):
            self.rows = [row(storetime=storetime)]; ep = self.run_import()['episodes'][0]
            evidence = next(iter(ep['claim_provenance'].values()))
            self.assertIsNone(evidence['source_available_at'])
            self.assertEqual(evidence['recorded_at']['raw_value'], storetime or None)

    def test_file_row_dimension_and_origin_hashes_recompute(self):
        result = self.run_import(); ep = result['episodes'][0]; claim = ep['store']['claims'][0]
        provenance = ep['claim_provenance'][claim['id']]
        for table, name, row_data in [('chartevents', 'source', self.rows[0]), ('icustays', 'stay_source', self.stays[0]),
                                     ('d_items', 'item_source', self.items[0])]:
            self.assertEqual(provenance[name]['file_sha256'], mi.source.digest((self.folder / (table + '.csv')).read_bytes()))
            self.assertEqual(provenance[name]['row_sha256'], mi.source.digest(mi.source.canonical(row_data)))
        self.assertEqual(provenance['origin_source'], provenance['source'])
        self.assertEqual(claim['source_sha256'], provenance['source']['file_sha256'])
        self.assertEqual(provenance['claim_sha256'], cr.digest(claim))

    def test_full_ledger_and_empty_roster_stays(self):
        self.stays.append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.items.append(dimension('d_items', itemid='1001', linksto='chartevents'))
        self.rows += [row(), row(warning='1'), row(subject_id='9'), row(itemid='1001')]
        result = self.run_import()
        self.assertEqual(result['summary']['import_outcome_counts'], {'NOT_ADMITTED': 3, 'OUTSIDE_ITEM_SCOPE': 1, 'PENDING_CLAIM': 1})
        self.assertEqual(len({r['record_id'] for r in result['reconciliation']}), 5)
        self.assertEqual(result['reconciliation'][1]['duplicate_of'], result['reconciliation'][0]['record_id'])
        self.assertEqual(result['episodes'][1]['status'], 'NO_SELECTED_ADMITTED_RECORDS')
        self.assertEqual(result['summary']['represented_icu_stays'], 2)

    def test_patient_filter_and_stay_identity(self):
        self.stays.append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.rows += [row(subject_id='2', hadm_id='20', stay_id='200'), row(subject_id='2')]
        self.request['patient_ids'] = ['1']; result = self.run_import()
        self.assertEqual(result['reconciliation'][1]['import_outcome'], 'OUTSIDE_PATIENT_SCOPE')
        self.assertIn('STAY_IDENTITY_MISMATCH', result['reconciliation'][2]['reasons'])
        self.assertEqual(len(result['episodes']), 1)

    def test_origin_before_item_filter_shared_across_stays(self):
        self.items.append(dimension('d_items', itemid='1001', linksto='chartevents'))
        self.stays.append(dimension('icustays', subject_id='1', hadm_id='11', stay_id='101'))
        self.rows += [row(itemid='1001', charttime='2150-01-01 08:00:00'), row(hadm_id='11', stay_id='101')]
        result = self.run_import(); clocks = [e['store']['clocks'][0] for e in result['episodes']]
        self.assertEqual(clocks[0], clocks[1]); self.assertEqual(clocks[0]['origin'], '2150-01-01T08:00:00')
        self.assertEqual(result['reconciliation'][1]['import_outcome'], 'OUTSIDE_ITEM_SCOPE')

    def test_32_claim_limit_and_33rd_record_block_without_truncation(self):
        self.rows = [row(caregiver_id=str(i+1)) for i in range(32)]
        self.assertEqual(self.run_import()['summary']['described_claims'], 32)
        self.rows.append(row(caregiver_id='33')); result = self.run_import()
        self.assertEqual(result['summary']['status'], 'BLOCKED_INCOMPLETE_IMPORT')
        self.assertEqual(result['summary']['import_outcome_counts'], {'BLOCKED_RESOURCE_LIMIT': 33})
        self.assertTrue(result['summary']['reconciliation_complete']); self.assertIsNone(result['episodes'][0]['store'])

    def test_one_blocked_stay_retains_other_pending_store(self):
        self.stays.append(dimension('icustays', subject_id='2', hadm_id='20', stay_id='200'))
        self.rows += [row(caregiver_id='1'), row(subject_id='2', hadm_id='20', stay_id='200')]
        with patch.object(mi, 'MAX_CLAIMS', 1): result = self.run_import()
        self.assertEqual(result['summary']['described_claims'], 1)
        self.assertFalse(result['summary']['description_complete_over_selected_records'])
        self.assertEqual(result['summary']['blocked_stays'], 1)

    def test_file_row_roster_and_graph_limits_fail_explicitly(self):
        for name in ('MAX_ROWS', 'MAX_BYTES', 'MAX_EPISODES'):
            with patch.object(mi, name, 0), self.assertRaises(ei.ContractError): self.run_import()
        with patch.object(cr, 'MAX_BYTES', 1): result = self.run_import()
        self.assertEqual(result['episodes'][0]['reason'], 'CLAIM_DOCUMENT_LIMIT')

    def test_compressed_input_hash_and_record_content(self):
        self.write(); plain = self.folder / 'chartevents.csv'; raw = plain.read_bytes()
        compressed = self.folder / 'chartevents.csv.gz'; compressed.write_bytes(gzip.compress(raw, mtime=0)); plain.unlink()
        result = mi.run(self.folder, self.request); evidence = result['reconciliation'][0]['source']
        self.assertEqual(evidence['file_sha256'], mi.source.digest(compressed.read_bytes()))
        self.assertEqual(evidence['csv_sha256'], mi.source.digest(raw))

    def test_schema_identity_and_scope_failures(self):
        for change in ({'mode': 'source_as_known'}, {'time_policy': 'clinical-exact'}, {'itemids': []},
                       {'itemids': ['1000','1000']}, {'patient_ids': ['1','1']}, {'extra': True}):
            self.write()
            with self.subTest(change=change), self.assertRaises(ei.ContractError): mi.run(self.folder, {**self.request, **change})
        self.request['itemids'] = ['9999']
        with self.assertRaisesRegex(ei.ContractError, 'UNKNOWN_CHART_ITEM_SELECTOR'): self.run_import()
        self.request['itemids'] = ['1000']; self.request['patient_ids'] = ['9']
        with self.assertRaisesRegex(ei.ContractError, 'UNKNOWN_REQUESTED_PATIENT'): self.run_import()

    def test_duplicate_dimension_or_conflicting_admission_invalidates_run(self):
        self.stays.append(dict(self.stays[0]))
        with self.assertRaisesRegex(ValueError, 'DUPLICATE_DIMENSION_ID'): self.run_import()
        self.stays[1].update(subject_id='2', stay_id='200')
        with self.assertRaisesRegex(ValueError, 'CONFLICTING_ADMISSION_PATIENT'): self.run_import()

    def test_stale_acceptance_and_changed_dictionary_are_detected(self):
        store, policy = self.fixture(); old_store_hash = cr.digest(store)
        store['claims'][0]['bundle']['events'][0]['value_lexical'] = '59'
        with self.assertRaisesRegex(ei.ContractError, 'STALE_CLAIM_STORE'): mc.select(store, policy)
        self.items[0]['label'] = 'Changed label'; changed, _ = self.fixture()
        self.assertNotEqual(cr.digest(changed), old_store_hash)

    def test_measurement_module_tampering_and_live_assertions_fail_isolation(self):
        store, _ = self.fixture(); graph = cr.encode(store, fields=cr.MEASUREMENT_FIELDS)
        graph.add((URIRef('https://example.org/extra'), RDF.type, ei.S.Process))
        with self.assertRaises(ei.ContractError): isolation.check(graph, local=True, measurement=True)
        original = Graph.parse
        def tampered(g, source=None, *args, **kwargs):
            result = original(g, source, *args, **kwargs)
            if str(source).endswith('/measurement-claim-description-profile.ttl'):
                result.add((cr.CP.Field_time_var, OWL.equivalentClass, ei.S.Process))
            return result
        graph = cr.encode(store, fields=cr.MEASUREMENT_FIELDS)
        with patch.object(Graph, 'parse', tampered), self.assertRaisesRegex(ei.ContractError, 'UNREVIEWED_MEASUREMENT_CLAIM_CLASS_MODULE'):
            isolation.check(graph, local=True, measurement=True)

    def test_selection_does_not_mutate_input_or_global_policy(self):
        store, policy = self.fixture(); before = deepcopy((store, policy, mc.SEMANTIC_POLICY))
        result = mc.select(store, self.accept(store, policy)); result['context']['semantic_policy']['id'] = 'mutated'
        self.assertEqual((store, policy, mc.SEMANTIC_POLICY), before)

    def test_cli_atomic_failure_and_input_protection(self):
        self.write(); request = self.folder/'request.json'; output = self.folder/'result.json'
        request.write_text(json.dumps(self.request)); args=['--input-dir',str(self.folder),'--request',str(request),'--output',str(output)]
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(mi.main(args),0); previous=output.read_bytes()
            request.write_text('{}'); self.assertEqual(mi.main(args),2); self.assertEqual(output.read_bytes(),previous)
            request.write_text(json.dumps(self.request)); original=request.read_bytes()
            self.assertEqual(mi.main(args[:-1]+[str(request)]),2); self.assertEqual(request.read_bytes(),original)
            (self.folder/'chartevents.csv').write_text('wrong,header\n')
            self.assertEqual(mi.main(args),2); self.assertEqual(output.read_bytes(),previous)

    def test_committed_synthetic_report_is_reproducible_and_explicit_about_scope(self):
        from . import verify_measurement_claims as verifier
        report = json.loads((ei.ROOT / 'verification/measurement-claim-report.json').read_text())
        self.assertEqual(report, verifier.verify())
        self.assertTrue(report['synthetic_only']); self.assertFalse(report['public_demo_chartevents_analyzed'])
        self.assertFalse(report['temporal_query_supported']); self.assertFalse(report['accepted_occurrence_graph_generated'])
        self.assertEqual(report['selected_synthetic_values'], ['58', '68', '72'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
