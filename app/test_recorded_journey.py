"""Actual engine checks for the guided recorded-treatment/measurement adapter."""
from copy import deepcopy
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from unittest.mock import patch

from app.recorded_journey import RecordedJourneyWorkspace

ROOT = Path(__file__).resolve().parents[1]
CONTROLS = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}


class RecordedJourneyTests(unittest.TestCase):
    def workspace(self, **kwargs):
        workspace = RecordedJourneyWorkspace(**kwargs)
        self.addCleanup(workspace.close)
        return workspace

    def run_job(self, workspace, profile='literal', stratum='synthetic', controls=None):
        job = workspace.start({'profile': profile, 'stratum': stratum,
                               'controls': deepcopy(controls or CONTROLS)})
        deadline = time.monotonic() + 30
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.01)
            job = workspace.get(profile, job['id'])
        self.assertNotEqual(job['status'], 'RUNNING', 'Recorded query did not finish')
        return job

    def test_literal_and_reviewed_preserve_membership_observations_and_source_evidence(self):
        workspace = self.workspace()
        outputs = {}
        for profile in ('literal', 'reviewed'):
            job = self.run_job(workspace, profile)
            self.assertEqual(job['status'], 'COMPLETED', job.get('error'))
            self.assertEqual(job['summary']['metrics'], {
                'patients': 3, 'stays': 3, 'segments': 3, 'eligible_pairs': 3,
                'followup_bindings': 3, 'pairs_without_followup': 1})
            details = {a['patient_id']: workspace.inspect(profile, job['id'], a['token'])
                       for a in job['summary']['anchors']}
            first = details['1']
            self.assertEqual([(o['baseline']['value'], o['followup']['value'], o['delta'])
                              for o in first['observations']], [('58', '68', '10'), ('58', '72', '14')])
            self.assertEqual(details['3']['observations'][0]['delta'], '-7')
            missing = details['2']['observations'][0]
            self.assertEqual(missing['baseline']['value'], '60')
            self.assertEqual(missing['followup_status'], 'MISSING_FOLLOWUP')
            self.assertIsNone(missing['followup'])
            self.assertIsNone(missing['delta'])
            self.assertEqual(first['treatment']['source']['table'], 'inputevents')
            self.assertEqual(first['observations'][0]['baseline']['source']['record_number'], 1)
            witness = next(iter(first['witnesses'].values()))
            self.assertTrue(witness['treatment']['patient_role'].endswith('/patient-role'))
            self.assertEqual(witness['treatment']['semantic_support']['status'], 'ENTAILED')
            self.assertFalse(first['clinical_mapping_verified'])
            self.assertTrue(first['sql_agreement'])
            if profile == 'reviewed':
                self.assertEqual(first['measurement_mapping']['selection_plan']['status'], 'READY_MEASUREMENT_SELECTOR')
            else:
                self.assertNotIn('measurement_mapping', first)
            outputs[profile] = job['summary']['metrics']
        self.assertEqual(outputs['literal'], outputs['reviewed'])

    def test_refinement_does_not_change_prior_evidence_or_mutable_responses(self):
        workspace = self.workspace()
        first = self.run_job(workspace)
        token = first['summary']['anchors'][0]['token']
        before = workspace.inspect('literal', first['id'], token)
        second = self.run_job(workspace, controls={**CONTROLS, 'followup_minutes': 0})
        self.assertEqual(second['summary']['metrics']['patients'], 3)
        self.assertEqual(second['summary']['metrics']['pairs_without_followup'], 3)
        self.assertEqual(second['summary']['metrics']['followup_bindings'], 0)
        self.assertEqual(workspace.inspect('literal', first['id'], token), before)
        before['observations'][0]['baseline']['value'] = 'changed'
        first['summary']['metrics']['patients'] = 999
        self.assertEqual(workspace.inspect('literal', first['id'], token)['observations'][0]['baseline']['value'], '58')
        self.assertEqual(workspace.get('literal', first['id'])['summary']['metrics']['patients'], 3)

    def test_profile_ids_never_cross_source_services(self):
        workspace = self.workspace()
        literal = self.run_job(workspace)
        with self.assertRaises(KeyError):
            workspace.get('reviewed', literal['id'])
        with self.assertRaises(KeyError):
            workspace.inspect('reviewed', literal['id'], literal['summary']['anchors'][0]['token'])

    def test_strict_requests_and_identifiers_reject_paths_and_unsupported_controls(self):
        workspace = self.workspace()
        good = {'profile': 'literal', 'stratum': 'synthetic', 'controls': CONTROLS}
        bad = [None, {}, {**good, 'source': '/tmp/source'}, {**good, 'class_iri': 'https://example.org/X'},
               {**good, 'profile': ['literal']}, {**good, 'stratum': '../reviewed'},
               {**good, 'controls': {**CONTROLS, 'followup_minutes': 121}},
               {**good, 'controls': {**CONTROLS, 'baseline_minutes': True}},
               {**good, 'controls': {**CONTROLS, 'threshold': 65}}]
        for request in bad:
            with self.subTest(request=request), self.assertRaises(ValueError):
                workspace.start(request)
        for job_id in ('../source', '', {}, 'f' * 33):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                workspace.get('literal', job_id)
        with self.assertRaises(ValueError):
            workspace.inspect('literal', 'f' * 32, '../source')

    def test_configured_source_is_explicit_and_never_falls_back_to_authored(self):
        workspace = self.workspace(config=ROOT / 'examples/configured-pressure-service/config.json')
        metadata = workspace.metadata()
        self.assertEqual(set(metadata['profiles']), {'reviewed'})
        profile = metadata['profiles']['reviewed']
        self.assertTrue(profile['available'], profile.get('error'))
        self.assertEqual(profile['source_mode'], 'configured-records')
        self.assertEqual(profile['selectors']['configured']['source_item_ids'], ['2001'])
        self.assertEqual(profile['defaults'], {'threshold': '65', 'baseline_minutes': 15, 'followup_minutes': 60})
        with self.assertRaises(ValueError):
            self.run_job(workspace, 'literal')
        with self.assertRaises(ValueError):
            self.run_job(workspace, 'reviewed', 'configured', CONTROLS)
        job = self.run_job(workspace, 'reviewed', 'configured', profile['defaults'])
        self.assertEqual(job['status'], 'COMPLETED', job.get('error'))
        self.assertEqual(job['summary']['measurement_mapping']['selected_item_ids'], ['2001'])
        with self.assertRaises((ValueError, OSError)):
            self.workspace(config=ROOT / 'does-not-exist.json')

    def test_changed_source_blocks_new_query_and_retains_historical_inspection(self):
        from demo.pressure import PressureService
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp) / 'records'
            shutil.copytree(ROOT / 'examples/source-mixed-query', folder)
            service = PressureService(synthetic=True)
            service.folder = folder
            workspace = self.workspace(literal_service=service)
            first = self.run_job(workspace)
            self.assertEqual(first['status'], 'COMPLETED')
            token = first['summary']['anchors'][0]['token']
            before = workspace.inspect('literal', first['id'], token)
            path = folder / 'chartevents.csv'
            path.write_bytes(path.read_bytes() + b'\n')
            failed = self.run_job(workspace)
            self.assertEqual(failed['status'], 'FAILED')
            self.assertNotIn('summary', failed)
            self.assertIn('SOURCE_CHANGED', failed['error'])
            self.assertEqual(workspace.inspect('literal', first['id'], token), before)

    def test_selector_failure_is_explicit_and_does_not_select_literal(self):
        workspace = self.workspace()
        from app.recorded_selectors import describe_selector
        def unavailable(service, stratum, mode):
            if mode == 'reviewed':
                raise ValueError('UNREVIEWED_MAPPING')
            return describe_selector(service, stratum, mode)
        with patch('app.recorded_selectors.describe_selector', side_effect=unavailable):
            metadata = workspace.metadata()
        self.assertEqual(metadata['default_profile'], 'reviewed')
        self.assertFalse(metadata['profiles']['reviewed']['available'])
        self.assertIn('UNREVIEWED_MAPPING', metadata['profiles']['reviewed']['error'])
        self.assertTrue(metadata['profiles']['literal']['available'])

    def test_authored_startup_is_lazy_and_close_is_idempotent(self):
        workspace = self.workspace()
        self.assertIsNone(workspace._services)
        workspace.close()
        workspace.close()
        with self.assertRaises(RuntimeError):
            workspace.metadata()


if __name__ == '__main__':
    unittest.main()
