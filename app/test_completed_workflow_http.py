"""Integrated HTTP acceptance: typed patterns, explicit features and durable evidence."""
from base64 import b64encode
from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server
from app.temporal_replay import verify

BASE = '/api/journey/recorded'
FEATURES = {'schema': 'recorded-similarity-features-1', 'features': [
    {'id': 'latest_value', 'weight': '2', 'scale': '10'},
    {'id': 'measurement_count', 'weight': '1', 'scale': '2'},
    {'id': 'latest_recency', 'weight': '1', 'scale': '5'},
]}


class CompletedWorkflowHTTPTests(unittest.TestCase):
    @contextmanager
    def running(self, **options):
        instance = server.make_server(port=0, **options)
        thread = threading.Thread(target=instance.serve_forever, daemon=True)
        thread.start()
        try:
            yield instance, f'http://127.0.0.1:{instance.server_port}'
        finally:
            instance.shutdown()
            instance.server_close()
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive())

    def request(self, url, path, body=None, authorization=None, raw=False):
        headers = {'Origin': url}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        if authorization is not None:
            headers['Authorization'] = authorization
        data = json.dumps(body).encode() if body is not None else None
        with urlopen(Request(url + path, data=data, headers=headers), timeout=10) as response:
            return response.read() if raw else json.load(response)

    def completed(self, url, authorization=None):
        job = self.request(url, BASE + '/jobs', {
            'profile': 'reviewed', 'stratum': 'synthetic',
            'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}},
            authorization)
        deadline = time.monotonic() + 45
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.02)
            job = self.request(url, self.path(job), authorization=authorization)
        self.assertEqual(job['status'], 'COMPLETED', job)
        return job

    @staticmethod
    def path(job):
        return f"{BASE}/{job['profile']}/jobs/{job['id']}"

    def test_typed_revision_weighted_comparison_export_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            state_dir = Path(folder) / 'state'
            with self.running(state_dir=state_dir) as (instance, url):
                original = self.completed(url)
                original_path = self.path(original)
                descriptor = self.request(url, original_path + '/pattern')
                self.assertEqual(descriptor['shape'], [
                    'baseline_point', 'treatment_interval', 'optional_followup_point'])
                form = deepcopy(descriptor['default_pattern'])
                form['baseline']['operator'] = 'ge'
                form['baseline']['value_lexical'] = '60'
                revised = self.request(url, BASE + '/pattern', {
                    'profile': 'reviewed', 'job_id': original['id'], 'pattern': form})
                self.assertEqual(revised['status'], 'COMPLETED', revised)
                self.assertNotEqual(revised['id'], original['id'])
                self.assertEqual(self.request(url, original_path), original)
                first_revision = deepcopy(revised)
                form['baseline']['value_lexical'] = '61'
                revised = self.request(url, BASE + '/pattern', {
                    'profile': 'reviewed', 'job_id': first_revision['id'], 'pattern': form})
                self.assertEqual(revised['status'], 'COMPLETED', revised)
                self.assertEqual(revised['parent_job_id'], first_revision['id'])
                self.assertEqual(self.request(url, self.path(first_revision)), first_revision)
                path = self.path(revised)
                refs = self.request(url, path + '/references')
                anchor = next(a for a in refs['anchors'] if a['patient_id'] == '2')
                payload = {'profile': 'reviewed', 'job_id': revised['id'],
                           'reference_token': anchor['token'], 'top_k': 1,
                           'feature_profile': FEATURES}
                comparison = self.request(url, BASE + '/compare', payload)
                self.assertNotIn('2', comparison['eligible_patient_ids'])
                self.assertEqual(comparison['feature_profile']['definition'], FEATURES)
                self.assertTrue(comparison['ranked_patients'])
                for peer in comparison['ranked_patients']:
                    exact = lambda value: Fraction(int(value['numerator']), int(value['denominator']))
                    self.assertEqual(sum((exact(c['contribution_exact']) for c in peer['feature_contributions']), Fraction()),
                                     exact(peer['distance_exact']))
                    self.assertTrue(peer['selected_anchor']['coverage']['complete'])
                    self.assertTrue(all(feature['evidence'] for feature in peer['selected_anchor']['features']))
                self.assertFalse(revised['summary']['clinical_mapping_verified'])
                self.assertEqual(comparison['temporal_result']['roster'], revised['summary']['roster'])
                self.assertEqual({r['patient_id'] for r in revised['summary']['roster']},
                                 {r['patient_id'] for r in original['summary']['roster']})
                bundle = self.request(url, BASE + '/export', payload)
                self.assertTrue(verify(bundle)['verified'])
                invalid = deepcopy(form)
                invalid['baseline']['maximum_offset_us'] = 0
                with self.assertRaises(HTTPError) as caught:
                    self.request(url, BASE + '/pattern', {
                        'profile': 'reviewed', 'job_id': original['id'], 'pattern': invalid})
                self.assertEqual(caught.exception.code, 400)
                invalid = deepcopy(form)
                invalid['followup']['maximum_offset_us'] = descriptor['parent_query']['followup']['max_after_anchor_us'] + 1
                with self.assertRaises(HTTPError) as caught:
                    self.request(url, BASE + '/pattern', {
                        'profile': 'reviewed', 'job_id': original['id'], 'pattern': invalid})
                self.assertEqual(caught.exception.code, 400)
                interrupted_id = 'e' * 32
                instance.workspace.recorded.store.record_started(interrupted_id, {
                    'profile': 'reviewed', 'stratum': 'synthetic',
                    'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
            with self.running(state_dir=state_dir) as (instance, url):
                self.assertEqual(self.request(url, self.path(original)), original)
                self.assertEqual(self.request(url, path), revised)
                self.assertEqual(self.request(url, BASE + '/compare', payload), comparison)
                self.assertEqual(self.request(url, BASE + '/export', payload), bundle)
                history = self.request(url, BASE + '/history')
                self.assertIn(original['id'], json.dumps(history))
                self.assertIn(revised['id'], json.dumps(history))
                interrupted = next(j for j in history['jobs'] if j['id'] == interrupted_id)
                self.assertEqual(interrupted['status'], 'INTERRUPTED')
                self.assertTrue(interrupted['resumable'])
                resumed = self.request(url, BASE + '/resume', {'job_id': interrupted_id})
                deadline = time.monotonic() + 45
                while resumed['status'] == 'RUNNING' and time.monotonic() < deadline:
                    time.sleep(.02)
                    resumed = self.request(url, self.path(resumed))
                self.assertEqual(resumed['status'], 'COMPLETED', resumed)
                self.assertEqual(resumed['id'], interrupted_id)
                self.assertEqual(resumed['summary']['metrics'], original['summary']['metrics'])
                with self.assertRaises(HTTPError) as caught:
                    self.request(url, BASE + '/resume', {'job_id': interrupted_id})
                self.assertEqual(caught.exception.code, 400)

    def test_basic_owner_access_guards_ui_api_and_keeps_audit_metadata_only(self):
        with tempfile.TemporaryDirectory() as folder:
            auth_file = Path(folder) / 'owner.txt'
            secret = 'acceptance-test-credential-1234567890'
            auth_file.write_text('owner:' + secret + '\n')
            auth_file.chmod(0o600)
            authorization = 'Basic ' + b64encode(('owner:' + secret).encode()).decode()
            wrong = 'Basic ' + b64encode(b'owner:incorrect-credential').decode()
            with self.running(state_dir=Path(folder) / 'state', auth_file=auth_file) as (instance, url):
                self.assertEqual(self.request(url, '/healthz')['status'], 'ok')
                for path in ('/journey', '/journey.js', BASE + '/config', BASE + '/history'):
                    for header in (None, wrong):
                        with self.subTest(path=path, authorized=header is not None), self.assertRaises(HTTPError) as caught:
                            self.request(url, path, authorization=header, raw=True)
                        self.assertEqual(caught.exception.code, 401)
                        self.assertIn('Basic', caught.exception.headers.get('WWW-Authenticate', ''))
                for header in (None, wrong):
                    with self.assertRaises(HTTPError) as caught:
                        self.request(url, BASE + '/jobs', {}, authorization=header)
                    self.assertEqual(caught.exception.code, 401)
                page = self.request(url, '/journey', authorization=authorization, raw=True)
                self.assertIn(b'<html', page)
                job = self.completed(url, authorization)
                refs = self.request(url, self.path(job) + '/references', authorization=authorization)
                payload = {'profile': 'reviewed', 'job_id': job['id'],
                           'reference_token': refs['anchors'][0]['token'], 'top_k': 1,
                           'feature_profile': FEATURES}
                self.request(url, BASE + '/compare', payload, authorization)
                self.request(url, BASE + '/export', payload, authorization)
                events = instance.workspace.recorded.store.audit_events()
                self.assertTrue(events)
                for event in events:
                    self.assertEqual(set(event), {'sequence', 'timestamp', 'actor', 'action', 'job_id', 'status'})
                    self.assertIn(event['actor'], ('owner', 'system', 'anonymous'))
                    self.assertNotIn(secret, json.dumps(event))
                    self.assertNotIn(authorization, json.dumps(event))
                self.assertTrue(any(e['action'] == 'completed' and e['job_id'] == job['id'] for e in events))


if __name__ == '__main__':
    unittest.main()
