"""Synthetic admission boundaries; no redistributed patient rows."""
from contextlib import redirect_stderr, redirect_stdout
import csv
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from . import mimic_inputevents as mi
from . import verify_mimic_demo as demo


def row(**updates):
    result = dict.fromkeys(mi.HEADERS['inputevents'], '')
    result.update(subject_id='1', hadm_id='10', stay_id='100', itemid='1000',
                  orderid='200', linkorderid='200', starttime='2150-01-01 10:00:00',
                  endtime='2150-01-01 11:00:00', storetime='2150-01-02 12:00:00',
                  amount='10', amountuom='mL', rate='10', rateuom='mL/hour',
                  statusdescription='FinishedRunning', ordercategorydescription='Continuous IV',
                  ordercomponenttypedescription='Main order parameter')
    result.update(updates)
    return result


def dimension(table, **updates):
    return {**dict.fromkeys(mi.HEADERS[table], ''), **updates}


def fixtures():
    return {'inputevents': [row()],
            'icustays': [dimension('icustays', subject_id='1', hadm_id='10', stay_id='100')],
            'd_items': [dimension('d_items', itemid='1000', label='Synthetic input A', linksto='inputevents')]}


def write_tables(folder, tables):
    for table, rows in tables.items():
        with (folder / (table + '.csv')).open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=mi.HEADERS[table])
            writer.writeheader(); writer.writerows(rows)


class MimicAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.tables = fixtures()

    def run_stage(self, dataset_id='synthetic-mimic-demo-2.2'):
        write_tables(self.folder, self.tables)
        return mi.stage(*(self.folder / (t + '.csv') for t in mi.HEADERS), dataset_id=dataset_id)

    def test_valid_segment_preserves_unresolved_times_and_handoff_blockers(self):
        result = self.run_stage(); segment = result['segments'][0]
        self.assertEqual(result['summary']['outcome_counts']['STAGED_SEGMENT'], 1)
        self.assertFalse(result['summary']['matcher_ready'])
        self.assertIsNone(result['summary']['matching'])
        self.assertEqual(result['summary']['history_coverage'], 'unavailable')
        self.assertEqual(len(result['summary']['matcher_blockers']), 5)
        for field in ('start', 'end', 'recorded_at'):
            self.assertIsNone(segment[field]['timezone'])
            self.assertNotIn('Z', segment[field]['local_datetime'])
            self.assertEqual(segment[field]['normalization_status'], 'UNRESOLVED')
        self.assertIsNone(segment['source_available_at'])
        self.assertIsNone(segment['clinical_class'])
        self.assertEqual(segment['mapping_status'], 'UNREVIEWED')

    def test_provenance_recomputes_from_exact_files_and_rows(self):
        result = self.run_stage(); source = result['reconciliation'][0]['source']
        self.assertEqual(source['row_sha256'], mi.digest(mi.canonical(self.tables['inputevents'][0])))
        self.assertEqual(source['file_sha256'], mi.digest((self.folder / 'inputevents.csv').read_bytes()))
        for table, name in [('icustays', 'stay_source'), ('d_items', 'item_source')]:
            self.assertEqual(result['segments'][0][name]['row_sha256'], mi.digest(mi.canonical(self.tables[table][0])))

    def test_identical_row_reconciled_without_duplicate_segment(self):
        self.tables['inputevents'] *= 2
        result = self.run_stage()
        self.assertEqual(len(result['segments']), 1)
        self.assertEqual(result['reconciliation'][1]['duplicate_of'], result['reconciliation'][0]['record_id'])
        self.assertNotEqual(result['reconciliation'][0]['record_id'], result['reconciliation'][1]['record_id'])

    def test_shared_order_keeps_components_and_rate_segments_distinct(self):
        self.tables['d_items'].append(dimension('d_items', itemid='1001', linksto='inputevents'))
        self.tables['inputevents'] += [row(itemid='1001', ordercomponenttypedescription='Mixed solution'),
            row(starttime='2150-01-01 11:00:00', endtime='2150-01-01 12:00:00', orderid='201', rate='20')]
        result = self.run_stage()
        self.assertEqual(len(result['segments']), 3)
        self.assertEqual(len({s['id'] for s in result['segments']}), 3)
        self.assertEqual({s['linkorderid'] for s in result['segments']}, {'200'})

    def test_supported_end_statuses_do_not_construct_new_events(self):
        self.tables['inputevents'] = [row(statusdescription=s) for s in sorted(mi.STATUSES)]
        self.assertEqual(len(self.run_stage()['segments']), 4)

    def test_unknown_status_is_not_performed(self):
        for status in ('Rewritten', 'Changed', 'Bolus', 'Flushed', '', 'future-status'):
            with self.subTest(status=status):
                self.tables['inputevents'] = [row(statusdescription=status)]
                result = self.run_stage()
                self.assertEqual(result['segments'], [])
                self.assertIn('UNSUPPORTED_STATUS', result['reconciliation'][0]['reasons'])

    def test_bolus_push_non_iv_and_unknown_categories_excluded(self):
        for category in ('Drug Push', 'Bolus', 'Non Iv Meds', 'unknown'):
            with self.subTest(category=category):
                self.tables['inputevents'] = [row(ordercategorydescription=category)]
                self.assertEqual(self.run_stage()['reconciliation'][0]['outcome'], 'UNSUPPORTED_ROW')

    def test_one_minute_continuous_record_ambiguous_but_shorter_not_called_bolus(self):
        self.tables['inputevents'] = [row(endtime='2150-01-01 10:01:00'), row(endtime='2150-01-01 10:00:30')]
        result = self.run_stage()
        self.assertEqual(result['reconciliation'][0]['reasons'], ['ONE_MINUTE_BOLUS_AMBIGUITY'])
        self.assertEqual(len(result['segments']), 1)
        self.assertEqual(result['segments'][0]['end']['occurrence_precision'], 'unverified')

    def test_absent_and_earlier_storetime_never_certify_availability(self):
        for value in ('', '2149-12-31 00:00:00'):
            with self.subTest(value=value):
                self.tables['inputevents'] = [row(storetime=value)]
                result = self.run_stage()
                self.assertEqual(len(result['segments']), 1)
                self.assertIsNone(result['segments'][0]['source_available_at'])
                self.assertFalse(result['summary']['availability_verified'])

    def test_malformed_missing_offset_fraction_and_impossible_dates_rejected(self):
        for value in ('', '2150-01-01', '2150-01-01 10:00:00Z', '2150-01-01 10:00:00.0',
                      '2150-02-29 10:00:00', '2150-01-01 10:00:00+01:00'):
            with self.subTest(value=value):
                self.tables['inputevents'] = [row(starttime=value)]
                self.assertIn('INVALID_LOCAL_TIMESTAMP:starttime', self.run_stage()['reconciliation'][0]['reasons'])
        self.tables['inputevents'] = [row(storetime='bad')]
        self.assertEqual(self.run_stage()['reconciliation'][0]['outcome'], 'INVALID_ROW')

    def test_nonpositive_intervals_invalid(self):
        for end in ('2150-01-01 10:00:00', '2150-01-01 09:00:00'):
            self.tables['inputevents'] = [row(endtime=end)]
            self.assertIn('NON_POSITIVE_RECORDED_INTERVAL', self.run_stage()['reconciliation'][0]['reasons'])

    def test_stay_identity_unknown_item_and_wrong_table_rejected(self):
        for updates, code in [({'subject_id': '2'}, 'STAY_IDENTITY_MISMATCH'),
                              ({'hadm_id': '20'}, 'STAY_IDENTITY_MISMATCH'),
                              ({'stay_id': '200'}, 'UNKNOWN_STAY'), ({'itemid': '2000'}, 'UNKNOWN_ITEM'),
                              ({'orderid': '-1'}, 'INVALID_ID:orderid')]:
            with self.subTest(updates=updates):
                self.tables['inputevents'] = [row(**updates)]
                self.assertIn(code, self.run_stage()['reconciliation'][0]['reasons'])
        self.tables['inputevents'] = [row()]
        self.tables['d_items'][0]['linksto'] = 'chartevents'
        self.assertIn('ITEM_TABLE_MISMATCH', self.run_stage()['reconciliation'][0]['reasons'])

    def test_missing_nonpositive_and_nonfinite_quantities_separate(self):
        for value, outcome in [('', 'UNSUPPORTED_ROW'), ('0', 'UNSUPPORTED_ROW'), ('-1', 'UNSUPPORTED_ROW'),
                               ('NaN', 'INVALID_ROW'), ('Infinity', 'INVALID_ROW'), ('bad', 'INVALID_ROW'), ('1_0', 'INVALID_ROW'), (' ', 'UNSUPPORTED_ROW')]:
            for field in ('amount', 'rate'):
                with self.subTest(value=value, field=field):
                    self.tables['inputevents'] = [row(**{field: value})]
                    self.assertEqual(self.run_stage()['reconciliation'][0]['outcome'], outcome)
        for unit in ('', ' '):
            self.tables['inputevents'] = [row(rateuom=unit)]
            self.assertEqual(self.run_stage()['reconciliation'][0]['outcome'], 'UNSUPPORTED_ROW')

    def test_arbitrary_labels_units_and_numbers_are_not_clinical_mappings(self):
        self.tables['d_items'][0]['label'] = 'Antibiotic norepinephrine synthetic text'
        self.tables['inputevents'] = [row(rate='1.234567890123456789', rateuom='unreviewed-unit')]
        segment = self.run_stage()['segments'][0]
        self.assertIsNone(segment['clinical_class'])
        self.assertEqual(segment['rate'], {'raw_value': '1.234567890123456789', 'raw_unit': 'unreviewed-unit'})

    def test_patient_clocks_stable_across_stays_but_separate_across_patients_and_datasets(self):
        self.tables['icustays'] += [dimension('icustays', subject_id='1', hadm_id='11', stay_id='101'),
                                    dimension('icustays', subject_id='2', hadm_id='20', stay_id='200')]
        self.tables['inputevents'] += [row(hadm_id='11', stay_id='101'), row(subject_id='2', hadm_id='20', stay_id='200')]
        clocks = [s['clock'] for s in self.run_stage()['segments']]
        self.assertEqual(clocks[0], clocks[1]); self.assertNotEqual(clocks[0], clocks[2])
        self.assertFalse(clocks[0]['cross_patient_comparable']); self.assertIsNone(clocks[0]['origin'])
        other = self.run_stage('other-extract')['segments'][0]
        self.assertNotEqual(clocks[0], other['clock'])
        self.assertNotEqual(self.run_stage()['segments'][0]['id'], other['id'])

    def test_no_clipping_to_stay_or_invented_boundary(self):
        self.tables['icustays'][0].update(intime='2150-01-01 10:30:00', outtime='2150-01-01 10:45:00')
        segment = self.run_stage()['segments'][0]
        self.assertEqual(segment['start']['raw_value'], row()['starttime'])
        self.assertEqual(segment['end']['raw_value'], row()['endtime'])

    def test_every_row_has_one_outcome_all_diagnostics_retained(self):
        self.tables['inputevents'] = [row(), row(), row(statusdescription='unknown'),
            row(subject_id='bad', starttime='bad', statusdescription='unknown')]
        result = self.run_stage(); summary = result['summary']
        self.assertEqual(summary['outcome_counts'], dict.fromkeys(mi.OUTCOMES, 1))
        self.assertTrue(summary['reconciliation_complete'])
        self.assertEqual(sum(summary['outcome_counts'].values()), 4)
        self.assertGreater(len(result['reconciliation'][-1]['reasons']), 2)
        self.assertNotIn('reconciliation', summary)
        self.assertNotIn('patient_id', json.dumps(summary))

    def test_dimension_duplicates_and_conflicting_admissions_fail_whole_run(self):
        for table in ('icustays', 'd_items'):
            self.tables = fixtures(); self.tables[table] *= 2
            with self.assertRaisesRegex(mi.AdmissionError, 'DUPLICATE_DIMENSION_ID'):
                self.run_stage()
        self.tables = fixtures()
        self.tables['icustays'].append(dimension('icustays', subject_id='2', hadm_id='10', stay_id='101'))
        with self.assertRaisesRegex(mi.AdmissionError, 'CONFLICTING_ADMISSION_PATIENT'):
            self.run_stage()

    def test_changed_row_and_dimension_change_context_deterministically(self):
        first = self.run_stage()
        self.assertEqual(first, self.run_stage())
        self.tables['d_items'][0]['label'] = 'Changed label'
        second = self.run_stage()
        self.assertNotEqual(first['summary']['context_id'], second['summary']['context_id'])
        self.assertEqual(first['segments'][0]['id'], second['segments'][0]['id'])
        self.tables['inputevents'][0]['rate'] = '11'
        third = self.run_stage()
        self.assertNotEqual(first['segments'][0]['id'], third['segments'][0]['id'])

    def test_gzip_and_plain_csv_share_csv_and_record_identity(self):
        first = self.run_stage()
        path = self.folder / 'inputevents.csv.gz'
        path.write_bytes(gzip.compress((self.folder / 'inputevents.csv').read_bytes(), mtime=0))
        second = mi.stage(path, self.folder / 'icustays.csv', self.folder / 'd_items.csv', dataset_id='synthetic-mimic-demo-2.2')
        self.assertEqual(first['segments'][0]['id'], second['segments'][0]['id'])
        self.assertEqual(first['segments'][0]['source']['csv_sha256'], second['segments'][0]['source']['csv_sha256'])
        self.assertNotEqual(first['summary']['context_id'], second['summary']['context_id'])

    def test_quoted_newline_is_one_csv_record_not_two_lines(self):
        self.tables['inputevents'][0]['ordercategoryname'] = 'Synthetic, quoted\nlabel'
        result = self.run_stage()
        self.assertEqual(result['summary']['input_rows'], 1)
        self.assertEqual(result['reconciliation'][0]['source']['record_number'], 1)
        self.assertEqual(result['reconciliation'][0]['raw'], self.tables['inputevents'][0])

    def test_header_drift_duplicate_columns_and_malformed_records_fail(self):
        self.run_stage()
        path = self.folder / 'inputevents.csv'
        original = path.read_text()
        for broken in (original.replace('subject_id,', 'new_column,', 1),
                       original.replace('hadm_id,', 'subject_id,', 1), original + '\n', original + 'one,two\n'):
            path.write_text(broken)
            with self.assertRaises(mi.AdmissionError):
                mi.read_table(path, 'inputevents')

    def test_resource_limits_and_corrupt_gzip_fail(self):
        self.run_stage()
        path = self.folder / 'inputevents.csv'
        with patch.object(mi, 'MAX_ROWS', 0), self.assertRaisesRegex(mi.AdmissionError, 'ROW_LIMIT'):
            mi.read_table(path, 'inputevents')
        with patch.object(mi, 'MAX_BYTES', 20), self.assertRaisesRegex(mi.AdmissionError, 'FILE_SIZE_LIMIT'):
            mi.read_table(path, 'inputevents')
        zipped = path.with_suffix('.csv.gz'); zipped.write_bytes(gzip.compress(path.read_bytes()))
        with patch.object(mi, 'MAX_BYTES', len(zipped.read_bytes())), self.assertRaisesRegex(mi.AdmissionError, 'EXPANDED_SIZE_LIMIT'):
            mi.read_table(zipped, 'inputevents')
        zipped.write_bytes(b'broken')
        with self.assertRaises(OSError):
            mi.read_table(zipped, 'inputevents')

    def test_empty_table_is_complete_staging_with_no_match_answer(self):
        self.tables['inputevents'] = []
        result = self.run_stage()
        self.assertEqual(result['summary']['input_rows'], 0)
        self.assertTrue(result['summary']['reconciliation_complete'])
        self.assertFalse(result['summary']['matcher_ready']); self.assertIsNone(result['summary']['matching'])

    def test_cli_writes_complete_bundle_and_preserves_output_on_invalid_input(self):
        self.run_stage(); output = self.folder / 'result.json'
        args = ['--input-dir', str(self.folder), '--dataset-id', 'synthetic', '--output', str(output)]
        with redirect_stdout(io.StringIO()):
            self.assertEqual(mi.main(args), 0)
        previous = output.read_bytes()
        self.assertEqual(json.loads(previous)['summary']['status'], 'STAGED_NOT_MATCHER_READY')
        (self.folder / 'inputevents.csv').write_text('bad')
        with redirect_stderr(io.StringIO()):
            self.assertEqual(mi.main(args), 2)
        self.assertEqual(output.read_bytes(), previous)

    def test_demo_verifier_rejects_synthetic_substitution(self):
        result = self.run_stage(mi.DATASET)
        with self.assertRaisesRegex(mi.AdmissionError, 'DEMO_PIN_MISMATCH'):
            demo.verify_summary(result['summary'])

    def test_demo_pin_and_committed_report_bind_current_implementation(self):
        report = json.loads((mi.ROOT / 'verification/mimic-demo-inputevents-report.json').read_text())
        self.assertEqual(demo.verify_summary(report['summary']), report)
        self.assertEqual(report['summary']['context']['implementation_sha256'], mi.digest(Path(mi.__file__).read_bytes()))
        self.assertEqual(report['summary']['outcome_counts'], {'STAGED_SEGMENT': 10980, 'UNSUPPORTED_ROW': 9424, 'INVALID_ROW': 0, 'DUPLICATE_ROW': 0})
        self.assertFalse(report['full_mimic_analyzed'])

    def test_cli_rejects_ambiguous_files_and_bad_dataset_id(self):
        self.run_stage()
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(mi.main(['--input-dir', str(self.folder)]), 2)
        self.assertIn('DATASET_ID_REQUIRED', err.getvalue())
        (self.folder / 'inputevents.csv.gz').write_bytes(b'unused')
        with redirect_stderr(io.StringIO()):
            self.assertEqual(mi.main(['--input-dir', str(self.folder), '--dataset-id', 'synthetic', '--output', str(self.folder/'out.json')]), 2)
        with self.assertRaisesRegex(mi.AdmissionError, 'INVALID_DATASET_ID'):
            self.run_stage('../bad')


if __name__ == '__main__':
    unittest.main(verbosity=2)
