"""Actual HTTP acceptance for recorded reference ranking and evidence exports."""
from copy import deepcopy
import json
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server
from app.temporal import digest
from app.temporal_replay import verify

BASE = '/api/journey/recorded'


class RecordedQueryByExampleHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server.make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, body=None, headers=None, attachment=False):
        values = {'Origin': self.url}
        if body is not None:
            values['Content-Type'] = 'application/json'
        values.update(headers or {})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        with urlopen(Request(self.url + path, data=raw, headers=values), timeout=30) as response:
            if attachment:
                self.assertTrue(response.headers.get('Content-Disposition', '').startswith('attachment;'))
                self.assertIn('application/json', response.headers['Content-Type'])
            return json.load(response)

    def completed(self, profile='reviewed', **controls):
        payload = {'profile': profile, 'stratum': 'synthetic', 'controls': {
            'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120, **controls}}
        job = self.request(BASE + '/jobs', payload)
        path = f"{BASE}/{profile}/jobs/{job['id']}"
        deadline = time.monotonic() + 45
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.02)
            job = self.request(path)
        self.assertEqual(job['status'], 'COMPLETED', job.get('error', job['status']))
        return job, path

    def comparison(self, job, path, patient='2', top_k=1):
        references = self.request(path + '/references')
        anchor = next(a for a in references['anchors'] if a['patient_id'] == patient)
        payload = {'profile': job['profile'], 'job_id': job['id'],
                   'reference_token': anchor['token'], 'top_k': top_k}
        return self.request(BASE + '/compare', payload), references, payload

    def test_actual_profiles_rank_preindex_features_and_preserve_entire_cohort(self):
        for profile in ('literal', 'reviewed'):
            with self.subTest(profile=profile):
                job, path = self.completed(profile)
                result, references, payload = self.comparison(job, path)
                self.assertEqual(references['schema'], 'recorded-reference-options-1')
                self.assertEqual(len(references['roster']), 5)
                self.assertEqual({a['patient_id'] for a in references['anchors']}, {'1', '2', '3'})
                self.assertEqual(result['reference']['feature']['value'], '60')
                self.assertEqual(result['reference']['feature']['unit'], 'mmHg')
                self.assertTrue(result['reference']['feature']['source'])
                self.assertTrue(result['reference']['feature']['claim_sha256'])
                self.assertEqual([(r['patient_id'], r['rank'], r['distance'], r['highlighted'])
                                  for r in result['ranked_patients']], [('1', 1, '2', True), ('3', 2, '2', False)])
                self.assertEqual(result['eligible_patient_ids'], ['1', '3', '4'])
                self.assertEqual(result['unresolved_patients'][0]['patient_id'], '4')
                self.assertEqual(result['unresolved_patients'][0]['reason'], 'NO_ADMITTED_ANCHOR')
                self.assertEqual(result['temporal_result'],
                                 {k: v for k, v in job['summary'].items() if k != 'elapsed_seconds'})
                self.assertNotIn('2', [r['patient_id'] for r in result['ranked_patients']])
                more = self.request(BASE + '/compare', {**payload, 'top_k': 20})
                self.assertEqual(more['temporal_result'], result['temporal_result'])
                self.assertEqual(more['eligible_patient_ids'], result['eligible_patient_ids'])
                self.assertEqual([r['patient_id'] for r in more['ranked_patients']], ['1', '3'])
                self.assertTrue(all(r['highlighted'] for r in more['ranked_patients']))

    def test_threshold_and_followup_changes_do_not_enter_feature_ranking(self):
        first, first_path = self.completed('literal')
        before, references, _ = self.comparison(first, first_path)
        changed, changed_path = self.completed('literal', threshold='59', followup_minutes=0)
        after, changed_references, _ = self.comparison(changed, changed_path)
        self.assertNotEqual(before['temporal_result']['metrics'], after['temporal_result']['metrics'])
        self.assertEqual(before['feature_profile'], after['feature_profile'])
        self.assertEqual(before['reference']['feature'], after['reference']['feature'])
        self.assertEqual([(r['patient_id'], r['distance'], r['selected_anchor']['feature'])
                          for r in before['ranked_patients']],
                         [(r['patient_id'], r['distance'], r['selected_anchor']['feature'])
                          for r in after['ranked_patients']])
        self.assertEqual({a['patient_id'] for a in changed_references['anchors']}, {'1', '2', '3'})
        self.assertEqual(self.request(first_path + '/references'), references)

    def test_download_retains_evidence_replays_and_rejects_rehashed_changes(self):
        job, path = self.completed('reviewed')
        comparison, _, payload = self.comparison(job, path)
        bundle = self.request(BASE + '/export', payload, attachment=True)
        self.assertEqual(bundle['format'], 'recorded-journey-export-1')
        self.assertEqual(bundle['request']['profile'], 'reviewed')
        self.assertEqual(bundle['snapshot']['temporal_result']['metrics'], job['summary']['metrics'])
        self.assertTrue(bundle['snapshot']['source_context'])
        self.assertTrue(bundle['snapshot']['session_context_id'])
        self.assertTrue(bundle['snapshot']['query_context_id'])
        self.assertEqual(len(bundle['snapshot']['details']), len(job['summary']['anchors']))
        self.assertEqual(bundle['comparison']['result']['ranked_patients'], comparison['ranked_patients'])
        detail = next(d for d in bundle['snapshot']['details'] if d['anchor']['patient_id'] == '2')
        self.assertEqual(len(detail['bindings']), 1)
        self.assertIsNone(detail['bindings'][0][4])
        self.assertIsNone(detail['bindings'][0][5])
        baseline = next(m for m in detail['measurements'] if m['event_id'] == detail['bindings'][0][3])
        self.assertEqual(baseline['value'], '60')
        self.assertEqual(detail['measurement_mapping']['selection_plan']['semantic_run']
                         ['backend_result']['backend']['name'], 'rustdl')
        self.assertEqual(self.request(BASE + '/export', payload, attachment=True), bundle)
        checked = verify(bundle)
        self.assertTrue(checked['verified'])
        self.assertTrue(checked['comparison_verified'])
        self.assertEqual(checked['report_id'], bundle['report_id'])
        tampered = deepcopy(bundle)
        tampered['comparison']['result']['ranked_patients'][0]['distance'] = '0'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            verify(tampered)
        tampered['report_id'] = digest({k: v for k, v in tampered.items() if k != 'report_id'})
        with self.assertRaisesRegex(ValueError, 'differs from replay'):
            verify(tampered)
        plain = self.request(BASE + '/export', {**payload, 'reference_token': None, 'top_k': None},
                             attachment=True)
        self.assertIsNone(plain['comparison'])
        self.assertEqual(plain['snapshot'], bundle['snapshot'])
        self.assertFalse(verify(plain)['comparison_verified'])

    def test_strict_comparison_export_admission_and_reference_scope(self):
        job, path = self.completed('literal')
        _, _, payload = self.comparison(job, path)
        invalid = [[], {}, {**payload, 'top_k': True}, {**payload, 'top_k': 0},
                   {**payload, 'top_k': 21}, {**payload, 'top_k': '1'},
                   {**payload, 'top_k': 1.0}, {**payload, 'reference_token': None},
                   {**payload, 'reference_token': 'patient4'},
                   {**payload, 'reference_token': 'f' * 24},
                   {**payload, 'job_id': '../source'}, {**payload, 'profile': 'unknown'},
                   {**payload, 'source_dir': '/tmp'}, {**payload, 'feature_value': '60'},
                   {**payload, 'unit': 'mm[Hg]'}, b'{', b'{"profile":"literal","profile":"reviewed"}']
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(HTTPError) as caught:
                self.request(BASE + '/compare', body)
            self.assertEqual(caught.exception.code, 400)
        for body in ({**payload, 'reference_token': None}, {**payload, 'top_k': None},
                     {**payload, 'source_dir': '/tmp'}, {**payload, 'top_k': True}):
            with self.subTest(export=body), self.assertRaises(HTTPError) as caught:
                self.request(BASE + '/export', body)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.request(BASE + '/compare', {**payload, 'profile': 'reviewed'})
        self.assertEqual(caught.exception.code, 404)
        for route, body in ((path + '/references', None), (BASE + '/compare', payload),
                            (BASE + '/export', payload)):
            with self.subTest(route=route), self.assertRaises(HTTPError) as caught:
                self.request(route, body, headers={'Origin': 'https://other.example'})
            self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.request(BASE + '/compare', payload, headers={'Content-Type': 'text/plain'})
        self.assertEqual(caught.exception.code, 415)
        with self.assertRaises(HTTPError) as caught:
            self.request(BASE + '/compare', b' ' * (server.MAX_BODY + 1))
        self.assertEqual(caught.exception.code, 413)


if __name__ == '__main__':
    unittest.main()
