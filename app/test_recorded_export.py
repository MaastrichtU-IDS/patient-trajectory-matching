"""Durable recorded evidence replay against the actual admitted source engines."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from app.recorded_export import export_recorded, verify_recorded
from app.recorded_journey import RecordedJourneyWorkspace
from app.temporal import digest
from app.temporal_replay import verify

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}


class RecordedExportTests(unittest.TestCase):
    def workspace(self, **kwargs):
        value = RecordedJourneyWorkspace(**kwargs)
        self.addCleanup(value.close)
        return value

    def job(self, workspace, profile='literal', stratum='synthetic', controls=None):
        job = workspace.start({'profile': profile, 'stratum': stratum,
                               'controls': deepcopy(controls or CONTROLS)})
        deadline = time.monotonic() + 30
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.01)
            job = workspace.get(profile, job['id'])
        self.assertEqual(job['status'], 'COMPLETED', job.get('error'))
        return job

    def export(self, workspace, job, profile='literal', reference=None, top_k=None):
        return export_recorded(workspace, {'profile': profile, 'job_id': job['id'],
                                          'reference_token': reference, 'top_k': top_k})

    @staticmethod
    def rehash(bundle):
        bundle['report_id'] = digest({k: v for k, v in bundle.items() if k != 'report_id'})
        return bundle

    def configured(self, temp):
        folder = Path(temp)
        shutil.copytree(ROOT / 'examples/configured-pressure-service', folder / 'configured-pressure-service')
        shutil.copytree(ROOT / 'examples/prepared-measurement-session', folder / 'prepared-measurement-session')
        return folder / 'configured-pressure-service/config.json'

    def test_both_profiles_replay_after_close_and_preserve_all_evidence(self):
        for profile in ('literal', 'reviewed'):
            with self.subTest(profile=profile):
                workspace = self.workspace()
                bundle = self.export(workspace, self.job(workspace, profile), profile)
                workspace.close()
                self.assertEqual(bundle['format'], 'recorded-journey-export-1')
                snapshot = bundle['snapshot']
                self.assertNotIn('job_id', snapshot)
                self.assertNotIn('elapsed_seconds', snapshot['temporal_result'])
                self.assertIsNone(bundle['comparison'])
                self.assertFalse(snapshot['temporal_result']['clinical_mapping_verified'])
                details = {row['anchor']['patient_id']: row for row in snapshot['details']}
                self.assertEqual([row[5] for row in details['1']['bindings']], ['10', '14'])
                self.assertIsNone(details['2']['bindings'][0][4])
                self.assertIsNone(details['2']['bindings'][0][5])
                self.assertEqual(details['1']['treatment']['source']['table'], 'inputevents')
                self.assertTrue(all(d['sql_agreement'] for d in snapshot['details']))
                self.assertEqual(len(snapshot['details']), snapshot['temporal_result']['anchors_total'])
                verified = verify(json.loads(json.dumps(bundle)))
                self.assertTrue(verified['verified'])
                self.assertFalse(verified['comparison_verified'])

    def test_reference_and_display_limit_are_explicit_and_replayable(self):
        workspace = self.workspace()
        job = self.job(workspace)
        reference = job['summary']['anchors'][0]['token']
        bundle = self.export(workspace, job, reference=reference, top_k=1)
        self.assertEqual(bundle['comparison']['reference_token'], reference)
        self.assertEqual(bundle['comparison']['top_k'], 1)
        self.assertNotIn('job_id', bundle['comparison']['result'])
        self.assertTrue(verify_recorded(bundle)['comparison_verified'])
        with self.assertRaises(ValueError):
            self.export(workspace, job, top_k=1)
        with self.assertRaises(ValueError):
            self.export(workspace, job, reference=reference, top_k=True)

    def test_reused_job_has_identical_canonical_export(self):
        workspace = self.workspace()
        first = self.job(workspace)
        saved = self.export(workspace, first)
        second = self.job(workspace)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual(second['execution']['mode'], 'cached_complete_result')
        self.assertEqual(self.export(workspace, second), saved)

    def test_exact_lexical_controls_change_query_context(self):
        workspace = self.workspace()
        first = self.export(workspace, self.job(workspace))
        second = self.export(workspace, self.job(workspace, controls={**CONTROLS, 'threshold': '65.0'}))
        self.assertNotEqual(first['report_id'], second['report_id'])
        self.assertNotEqual(first['snapshot']['query_context_id'], second['snapshot']['query_context_id'])
        self.assertEqual(second['request']['controls']['threshold'], '65.0')
        self.assertTrue(verify_recorded(second)['verified'])

    def test_incomplete_cross_profile_and_extra_request_fields_rejected(self):
        workspace = self.workspace()
        job = self.job(workspace)
        with self.assertRaises(KeyError):
            self.export(workspace, job, 'reviewed')
        with self.assertRaises(ValueError):
            export_recorded(workspace, {'profile': 'literal', 'job_id': job['id'],
                                       'reference_token': None, 'top_k': None, 'source': '/tmp/source'})
        snapshot = workspace.snapshot('literal', job['id'])
        for mutation in ('count', 'clinical', 'details'):
            changed = deepcopy(snapshot)
            if mutation == 'count':
                changed['temporal_result']['anchors_verified'] -= 1
            elif mutation == 'clinical':
                changed['temporal_result']['clinical_mapping_verified'] = True
            else:
                changed['details'].pop()
            with self.subTest(mutation=mutation), patch.object(workspace, 'snapshot', return_value=changed):
                with self.assertRaises(ValueError):
                    self.export(workspace, job)

    def test_rehashing_changed_results_does_not_make_them_replayable(self):
        workspace = self.workspace()
        job = self.job(workspace)
        bundle = self.export(workspace, job, reference=job['summary']['anchors'][0]['token'], top_k=1)
        for mutation in ('binding', 'comparison', 'clinical', 'source', 'path'):
            changed = deepcopy(bundle)
            if mutation == 'binding':
                changed['snapshot']['details'][0]['bindings'][0][5] = '900'
            elif mutation == 'comparison':
                changed['comparison']['result']['top_k'] = 2
            elif mutation == 'clinical':
                changed['snapshot']['temporal_result']['clinical_mapping_verified'] = True
            elif mutation == 'source':
                changed['snapshot']['source_context']['source_files']['chartevents'] = '0' * 64
            else:
                changed['request']['config'] = '/tmp/untrusted.json'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                verify_recorded(self.rehash(changed))

    def test_retained_export_does_not_read_changed_source_or_live_metadata(self):
        from demo.pressure import PressureService
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'records'
            shutil.copytree(ROOT / 'examples/source-mixed-query', folder)
            service = PressureService(synthetic=True)
            service.folder = folder
            workspace = self.workspace(literal_service=service)
            job = self.job(workspace)
            saved = self.export(workspace, job)
            path = folder / 'chartevents.csv'
            path.write_bytes(path.read_bytes() + b'\n')
            with patch.object(service, 'metadata', side_effect=AssertionError('Live metadata reread')):
                self.assertEqual(self.export(workspace, job), saved)

    def test_configured_replay_requires_explicit_local_config_and_no_embedded_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.configured(temp)
            workspace = self.workspace(config=config)
            job = self.job(workspace, 'reviewed', 'configured',
                           {'threshold': '65', 'baseline_minutes': 15, 'followup_minutes': 60})
            bundle = self.export(workspace, job, 'reviewed')
            self.assertNotIn(temp, json.dumps(bundle))
            with self.assertRaisesRegex(ValueError, 'explicit --recorded-config'):
                verify_recorded(bundle)
            self.assertTrue(verify(bundle, recorded_config=config)['verified'])
            self.assertFalse(verify_recorded(bundle, config=config)['clinical_mapping_verified'])
            source = Path(temp) / 'prepared-measurement-session/chartevents.csv'
            source.write_bytes(source.read_bytes() + b'\n')
            with self.assertRaises(ValueError):
                verify_recorded(bundle, config=config)
            self.assertEqual(self.export(workspace, job, 'reviewed'), bundle)

    def test_configured_source_review_drift_blocks_replay_but_preserves_export(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.configured(temp)
            workspace = self.workspace(config=config)
            job = self.job(workspace, 'reviewed', 'configured',
                           {'threshold': '65', 'baseline_minutes': 15, 'followup_minutes': 60})
            bundle = self.export(workspace, job, 'reviewed')
            review = Path(temp) / 'configured-pressure-service/source-review.json'
            value = json.loads(review.read_text())
            value['record_reason'] += ' Review amended.'
            review.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                verify_recorded(bundle, config=config)
            self.assertEqual(self.export(workspace, job, 'reviewed'), bundle)

    def test_mapping_pack_drift_and_unaccepted_mapping_cannot_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            config = self.configured(temp)
            workspace = self.workspace(config=config)
            job = self.job(workspace, 'reviewed', 'configured',
                           {'threshold': '65', 'baseline_minutes': 15, 'followup_minutes': 60})
            bundle = self.export(workspace, job, 'reviewed')
            review = Path(temp) / 'configured-pressure-service/mapping/review.json'
            value = json.loads(review.read_text())
            value['decisions'][1]['reason'] += ' Amended mapping review.'
            review.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                verify_recorded(bundle, config=config)
            value['decisions'][1]['action'] = 'reject'
            review.write_text(json.dumps(value))
            with self.assertRaises(ValueError):
                verify_recorded(bundle, config=config)
            self.assertEqual(self.export(workspace, job, 'reviewed'), bundle)

    def test_implementation_drift_blocks_replay_before_source_execution(self):
        workspace = self.workspace()
        bundle = self.export(workspace, self.job(workspace))
        changed = {**bundle['artifacts'], 'app/recorded_export.py': '0' * 64}
        with patch('app.recorded_export._artifacts', return_value=changed):
            with self.assertRaisesRegex(ValueError, 'implementation differs'):
                verify_recorded(bundle)

    def test_cli_replays_saved_json_and_size_cap_applies_before_engine(self):
        workspace = self.workspace()
        bundle = self.export(workspace, self.job(workspace))
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'recorded-query.json'
            path.write_text(json.dumps(bundle))
            result = subprocess.run([sys.executable, '-m', 'app.temporal_replay', str(path)],
                                    cwd=ROOT, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)['verified'])
        with patch('app.recorded_export.MAX_BYTES', 10):
            with self.assertRaisesRegex(ValueError, 'exceeds 8 MiB'):
                verify_recorded(bundle)
        with self.assertRaisesRegex(ValueError, 'does not accept'):
            verify_recorded(bundle, config='/tmp/never-open-this.json')


if __name__ == '__main__':
    unittest.main()
