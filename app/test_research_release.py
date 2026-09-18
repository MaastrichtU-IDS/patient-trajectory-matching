"""Actual HTTP acceptance of separately admitted clinical variables and replay."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib.error import HTTPError

from app import server
from app import test_completed_workflow_http as http_acceptance
from app.temporal import digest
from app.temporal_replay import verify

ROOT = Path(__file__).resolve().parents[1]
BASE = '/api/journey/recorded'
PROFILE = {'schema': 'recorded-clinical-features-1', 'features': [
    {'id': 'latest_value', 'weight': '1', 'scale': '10'},
    {'id': 'heart_rate', 'weight': '2', 'scale': '20'},
    {'id': 'respiratory_rate', 'weight': '1', 'scale': '4'},
]}


class ResearchReleaseTests(unittest.TestCase):
    running = http_acceptance.CompletedWorkflowHTTPTests.running
    request = http_acceptance.CompletedWorkflowHTTPTests.request
    completed = http_acceptance.CompletedWorkflowHTTPTests.completed
    path = staticmethod(http_acceptance.CompletedWorkflowHTTPTests.path)

    def test_distinct_variable_http_replay_and_retained_evidence_after_source_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            pack_dir = Path(folder) / 'pack'
            shutil.copytree(ROOT / 'examples/clinical-features', pack_dir)
            pack = pack_dir / 'authored-pack.json'
            options = {'clinical_features_path': pack, 'state_dir': Path(folder) / 'state'}
            with self.running(**options) as (_, url):
                job = self.completed(url)
                refs = self.request(url, BASE + '/references', {
                    'profile': 'reviewed', 'job_id': job['id'], 'feature_profile': PROFILE})
                reference = next(a for a in refs['anchors'] if a['patient_id'] == '2')
                self.assertEqual(reference['feature_status'], 'AVAILABLE')
                request = {'profile': 'reviewed', 'job_id': job['id'],
                           'reference_token': reference['token'], 'top_k': 2, 'feature_profile': PROFILE}
                compared = self.request(url, BASE + '/compare', request)
                self.assertEqual([r['patient_id'] for r in compared['ranked_patients']], ['3', '1'])
                self.assertEqual({r['unit'] for r in compared['ranked_patients'][0]['feature_contributions']},
                                 {'mmHg', 'beats/min', 'breaths/min'})
                bundle = self.request(url, BASE + '/export', request)
                self.assertTrue(verify(bundle, clinical_features_path=pack)['verified'])
                with self.assertRaisesRegex(ValueError, 'clinical-features'):
                    verify(bundle)
                changed = deepcopy(bundle)
                changed['comparison']['feature_profile']['features'][1]['scale'] = '40'
                changed['report_id'] = digest({k: v for k, v in changed.items() if k != 'report_id'})
                with self.assertRaisesRegex(ValueError, 'differs from replay'):
                    verify(changed, clinical_features_path=pack)
            source = pack_dir / 'authored-measurements.csv'
            source.write_text(source.read_text().replace(',100\n', ',101\n'))
            with self.running(**options) as (_, url):
                self.assertEqual(self.request(url, self.path(job)), job)
                self.assertEqual(self.request(url, BASE + '/compare', request), compared)
                self.assertEqual(self.request(url, BASE + '/export', request), bundle)
                with self.assertRaises(HTTPError) as caught:
                    self.request(url, BASE + '/jobs', {'profile': 'reviewed', 'stratum': 'synthetic',
                                 'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
                self.assertEqual(caught.exception.code, 400)
            with self.assertRaises(ValueError):
                verify(bundle, clinical_features_path=pack)

    def test_startup_sources_are_exclusive_and_loopback_only(self):
        with self.assertRaisesRegex(ValueError, 'one recorded source'):
            server.make_server(port=0, recorded_config='unused', public_demo_dir='unused')
        for option in ({'public_demo_dir': 'unused'}, {'clinical_features_path': 'unused'}):
            with self.subTest(option=option), self.assertRaisesRegex(ValueError, 'loopback'):
                server.make_server(host='0.0.0.0', port=0, **option)
        with self.running(public_demo_dir=ROOT / 'examples/source-mixed-query') as (_, url):
            metadata = self.request(url, BASE + '/config')
            self.assertEqual(metadata['default_profile'], 'literal')
            self.assertEqual(set(metadata['profiles']), {'literal'})
            self.assertEqual(self.request(url, '/api/capabilities')['scope'], 'public-demo-research-prototype')
            with self.assertRaises(HTTPError) as caught:
                self.request(url, BASE + '/jobs', {'profile': 'reviewed', 'stratum': 'synthetic',
                             'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
            self.assertEqual(caught.exception.code, 400)
