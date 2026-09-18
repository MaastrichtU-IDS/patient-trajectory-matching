"""Independent source extraction boundaries and exact distinct-variable oracle."""
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import prepare_demo_clinical_features as prep
from tools import evaluate_demo_clinical_features as evaluate
from app import clinical_features as cf


class DemoClinicalExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        example = prep.ROOT / 'examples/source-mixed-query'
        for name in ('inputevents', 'icustays', 'd_items'):
            text = (example / (name + '.csv')).read_text()
            if name in ('inputevents', 'd_items'):
                # Replace only a CSV item field, never patient or episode IDs.
                rows = list(csv.DictReader(text.splitlines()))
                fields = list(rows[0])
                for row in rows:
                    field = 'itemid'
                    if field in row and row[field] == '1000':
                        row[field] = '221906'
                if name == 'd_items':
                    for _, item, label, unit in prep.VARIABLES:
                        row = {key: '' for key in fields}
                        row.update(itemid=item, label=label, abbreviation=label, linksto='chartevents',
                                   category='Routine Vital Signs', unitname=unit, param_type='Numeric')
                        rows.append(row)
                with (self.source / (name+'.csv')).open('w', newline='') as handle:
                    writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
                    writer.writeheader(); writer.writerows(rows)
            else:
                (self.source / (name+'.csv')).write_text(text)
        self.rows = [
            '1,10,100,,2150-01-01 09:45:00,2150-01-02 12:00:00,220045,100,100,bpm,0',
            '1,10,100,,2150-01-01 09:46:00,2150-01-02 12:00:00,220210,24,24,insp/min,0',
            '1,10,100,,2150-01-01 10:00:00,2150-01-02 12:00:00,220045,90,90,bpm,0',
            '1,10,101,,2150-01-01 09:59:00,2150-01-02 12:00:00,220045,1,1,bpm,0',
            '1,10,100,,2150-01-01 09:59:00,2150-01-02 12:00:00,220045,80,80,bpm,1',
            '1,10,100,,2150-01-01 09:58:00,2150-01-02 12:00:00,220045,80,80,hertz,0',
        ]
        self.rows.append(self.rows[0])
        self.pin = self.root / 'pin.json'
        self.save()

    def save(self):
        header = 'subject_id,hadm_id,stay_id,caregiver_id,charttime,storetime,itemid,value,valuenum,valueuom,warning'
        (self.source/'chartevents.csv').write_text(header+'\n'+'\n'.join(self.rows)+'\n')
        pins = {p.stem: {'file_sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in self.source.glob('*.csv')}
        self.pin.write_text(json.dumps({'dataset_id':'mimic-iv-demo-2.2','files':pins}))

    def test_actual_csv_admission_original_identity_and_private_output(self):
        out = self.root / 'output'
        summary = prep.prepare(self.source, out, pin_path=self.pin)
        pack = cf.ClinicalFeaturePack(out/'pack.json')
        self.assertEqual(summary['selected_rows'], 2)
        self.assertEqual(summary['counts']['duplicate_rows'], 1)
        self.assertEqual(summary['counts']['wrong_unit_rows'], 1)
        self.assertEqual(summary['counts']['unsupported_row'], 1)
        self.assertEqual([r['event_id'].rsplit(':', 1)[1] for r in pack.rows], ['1','2'])
        self.assertEqual(out.stat().st_mode & 0o777, 0o700)
        self.assertEqual((out/'measurements.csv').stat().st_mode & 0o777, 0o600)
        self.assertEqual(pack.definition['review']['clinical_status'], 'PENDING')
        checked, count = evaluate.raw_source_check(self.source, out/'pack.json')
        self.assertEqual(count, 2)
        self.assertEqual(checked.definition, pack.definition)

    def test_changed_pin_and_dictionary_rejected(self):
        (self.source/'chartevents.csv').write_text('changed')
        with self.assertRaisesRegex(ValueError, 'pin'):
            prep.prepare(self.source, self.root/'out', pin_path=self.pin)
        self.save()
        dictionary = self.source/'d_items.csv'
        dictionary.write_text(dictionary.read_text().replace(',bpm,', ',hertz,'))
        self.save()
        with self.assertRaisesRegex(ValueError, 'dictionary'):
            prep.prepare(self.source, self.root/'out', pin_path=self.pin)

    def test_no_row_limit_truncation(self):
        with patch.object(cf, 'MAX_ROWS', 1), self.assertRaisesRegex(ValueError, 'no cohort truncation'):
            prep.prepare(self.source, self.root/'out', pin_path=self.pin)
        self.assertFalse((self.root/'out'/'measurements.csv').exists())

    def test_nonprivate_output_symlink_and_repository_output_rejected(self):
        out = self.root/'out'; out.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, 'private permissions'):
            prep.prepare(self.source, out, pin_path=self.pin)
        self.assertFalse((out/'measurements.csv').exists())
        link = self.root/'linked'; link.symlink_to(out, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            prep.prepare(self.source, link, pin_path=self.pin)
        with self.assertRaisesRegex(ValueError, 'outside the repository'):
            prep.prepare(self.source, prep.ROOT/'verification'/'forbidden', pin_path=self.pin)

    def test_window_exact_patient_stay_boundaries(self):
        windows = {('1','100'):[datetime.fromisoformat('2150-01-01T10:00:00')]}
        row = {'subject_id':'1','stay_id':'100','charttime':'2150-01-01 09:30:00'}
        self.assertTrue(prep.within_window(row, windows))
        for changed in [{'charttime':'2150-01-01 09:29:59'}, {'charttime':'2150-01-01 10:00:00'},
                        {'subject_id':'2'}, {'stay_id':'101'}]:
            self.assertFalse(prep.within_window({**row, **changed}, windows))

    def test_distinct_oracle_selects_ascending_id_at_latest_preindex_time(self):
        detail = {'anchor':{'patient_id':'1','episode_id':'100','start':'2150-01-01T10:00:00'}}
        row = {'patient_id':'1','episode_id':'100','item_id':'220045','unit':'bpm','time':'2150-01-01T09:59:00','value':'90'}
        points = [{**row,'event_id':'z'}, {**row,'event_id':'a'},
                  {**row,'event_id':'later','time':'2150-01-01T10:00:00','value':'1'},
                  {**row,'event_id':'other','episode_id':'101','value':'1'}]
        chosen = evaluate.latest(detail, points, '220045','bpm',30*60000000,1)
        self.assertEqual(chosen['event_id'], 'a')


if __name__ == '__main__':
    unittest.main()
