"""Actual HTTP admission, matching and source evidence for recorded journeys."""
from decimal import Decimal
import json
from pathlib import Path
import threading
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server

ROOT = Path(__file__).resolve().parents[1]
BASE = '/api/journey/recorded'


class RecordedJourneyHTTPTests(unittest.TestCase):
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

    def request(self, path, body=None, headers=None, url=None):
        url = url or self.url
        values = {'Origin': url}
        if body is not None:
            values['Content-Type'] = 'application/json'
        values.update(headers or {})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        with urlopen(Request(url + path, data=raw, headers=values), timeout=15) as response:
            return json.load(response)

    def payload(self, profile='reviewed', stratum='synthetic', **controls):
        return {'profile': profile, 'stratum': stratum,
                'controls': {'threshold': '65', 'baseline_minutes': 30,
                             'followup_minutes': 120, **controls}}

    def completed(self, payload, url=None):
        job = self.request(BASE + '/jobs', payload, url=url)
        path = f"{BASE}/{payload['profile']}/jobs/{job['id']}"
        deadline = time.monotonic() + 45
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.02)
            job = self.request(path, url=url)
        self.assertEqual(job['status'], 'COMPLETED', job.get('error', job['status']))
        return job, path

    def test_authored_profiles_preserve_recorded_values_and_missing_followup(self):
        metadata = self.request(BASE + '/config')
        self.assertEqual(set(metadata['profiles']), {'literal', 'reviewed'})
        self.assertEqual(metadata['default_profile'], 'reviewed')
        summaries = []
        for profile in ('literal', 'reviewed'):
            with self.subTest(profile=profile):
                selector = metadata['profiles'][profile]['selectors']['synthetic']
                self.assertEqual(selector['source_item_ids'], ['2000'])
                self.assertEqual(selector['unit_lexical'], 'mmHg')
                self.assertFalse(selector['clinical_mapping_verified'])
                job, path = self.completed(self.payload(profile))
                summary = job['summary']
                self.assertEqual(summary['metrics'], {
                    'patients': 3, 'stays': 3, 'segments': 3, 'eligible_pairs': 3,
                    'followup_bindings': 3, 'pairs_without_followup': 1})
                self.assertEqual(len(summary['roster']), 5)
                self.assertEqual(sum(r['status'] == 'NO_ADMITTED_ANCHOR' for r in summary['roster']), 2)
                self.assertFalse(summary['clinical_mapping_verified'])
                evidence = {}
                for anchor in summary['anchors']:
                    detail = self.request(path + '/anchors/' + anchor['token'])
                    evidence[anchor['patient_id']] = detail
                    self.assertTrue(detail['sql_agreement'])
                    self.assertEqual(detail['treatment']['item_id'], '1000')
                    self.assertTrue(detail['treatment']['source'])
                    self.assertTrue(detail['treatment']['claim_sha256'])
                    for observation in detail['observations']:
                        baseline = observation['baseline']
                        self.assertEqual(baseline['unit'], 'mmHg')
                        self.assertTrue(baseline['source'])
                        self.assertTrue(baseline['decisions'])
                        self.assertTrue(baseline['claim_sha256'])
                        if observation['followup'] is not None:
                            followup = observation['followup']
                            self.assertEqual(observation['followup_status'], 'RECORDED')
                            self.assertEqual(Decimal(observation['delta']),
                                             Decimal(followup['value']) - Decimal(baseline['value']))
                            self.assertEqual(followup['unit'], baseline['unit'])
                            self.assertTrue(followup['source'])
                observed = evidence['1']['observations']
                self.assertEqual([(o['baseline']['value'], o['followup']['value'], o['delta'])
                                  for o in observed], [('58', '68', '10'), ('58', '72', '14')])
                missing = evidence['2']['observations']
                self.assertEqual(len(missing), 1)
                self.assertEqual(missing[0]['baseline']['value'], '60')
                self.assertEqual(missing[0]['followup_status'], 'MISSING_FOLLOWUP')
                self.assertIsNone(missing[0]['followup'])
                self.assertIsNone(missing[0]['delta'])
                if profile == 'reviewed':
                    plan = evidence['1']['measurement_mapping']['selection_plan']
                    self.assertEqual(plan['status'], 'READY_MEASUREMENT_SELECTOR')
                    self.assertEqual(plan['supported_item_ids'], ['2000'])
                    self.assertEqual(plan['semantic_run']['status'], 'READY')
                    self.assertEqual(plan['semantic_run']['backend_result']['backend']['name'], 'rustdl')
                    self.assertEqual(plan['mapping']['outcomes'][0]['status'], 'ACCEPTED')
                    self.assertEqual(selector['mapping_context_id'], plan['mapping']['context_id'])
                    self.assertTrue(selector['review_evidence'])
                else:
                    self.assertIsNone(selector['mapping_context_id'])
                # A new window query changes selected observations, not historical evidence.
                changed, _ = self.completed(self.payload(profile, followup_minutes=0))
                self.assertEqual(changed['summary']['metrics']['followup_bindings'], 0)
                self.assertEqual(changed['summary']['metrics']['pairs_without_followup'], 3)
                self.assertEqual(self.request(path), job)
                first = summary['anchors'][0]
                self.assertEqual(self.request(path + '/anchors/' + first['token']), evidence[first['patient_id']])
                summaries.append(summary)
        self.assertEqual(summaries[0]['metrics'], summaries[1]['metrics'])
        self.assertEqual(summaries[0]['roster'], summaries[1]['roster'])

    def test_request_boundaries_and_unknown_evidence(self):
        invalid = [b'{', b'{"profile":"literal","profile":"reviewed"}', [],
                   self.payload(profile='unknown'), self.payload(stratum='unknown'),
                   self.payload(threshold=True), self.payload(baseline_minutes=True),
                   self.payload(baseline_minutes=31), self.payload(followup_minutes=121),
                   {**self.payload(), 'source_dir': '/tmp'},
                   {**self.payload(), 'class_iri': 'https://unreviewed.example/measurement'},
                   {**self.payload(), 'controls': {**self.payload()['controls'], 'unit': 'mm[Hg]'}}]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(HTTPError) as caught:
                self.request(BASE + '/jobs', body)
            self.assertEqual(caught.exception.code, 400)
        for path, body in ((BASE + '/config', None), (BASE + '/jobs', self.payload())):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(path, body, {'Origin': 'https://other.example'})
            self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.request(BASE + '/jobs', b' ' * (server.MAX_BODY + 1))
        self.assertEqual(caught.exception.code, 413)
        with self.assertRaises(HTTPError) as caught:
            self.request(BASE + '/jobs', self.payload(), {'Content-Type': 'text/plain'})
        self.assertEqual(caught.exception.code, 415)
        with self.assertRaises(HTTPError) as caught:
            self.request(BASE + '/reviewed/jobs/' + '0' * 32)
        self.assertEqual(caught.exception.code, 404)

    def test_configured_profile_uses_supplied_source_and_its_reviewed_windows(self):
        configured = server.make_server(port=0, recorded_config=ROOT / 'examples/configured-pressure-service/config.json')
        thread = threading.Thread(target=configured.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{configured.server_port}'
        try:
            metadata = self.request(BASE + '/config', url=url)
            self.assertEqual(set(metadata['profiles']), {'reviewed'})
            reviewed = metadata['profiles']['reviewed']
            self.assertEqual(reviewed['source_mode'], 'configured-records')
            self.assertEqual(reviewed['selectors']['configured']['source_item_ids'], ['2001'])
            payload = {'profile': 'reviewed', 'stratum': 'configured', 'controls': reviewed['defaults']}
            job, path = self.completed(payload, url=url)
            self.assertEqual(job['summary']['metrics']['patients'], 1)
            self.assertEqual(len(job['summary']['roster']), 5)
            self.assertEqual(job['summary']['measurement_mapping']['selected_item_ids'], ['2001'])
            for anchor in job['summary']['anchors']:
                detail = self.request(path + '/anchors/' + anchor['token'], url=url)
                self.assertTrue(detail['sql_agreement'])
                self.assertTrue(all(o['baseline']['item_id'] == '2001' for o in detail['observations']))
            for invalid in (self.payload(), {**payload, 'controls': {**reviewed['defaults'], 'baseline_minutes': 16}}):
                with self.assertRaises(HTTPError) as caught:
                    self.request(BASE + '/jobs', invalid, url=url)
                self.assertEqual(caught.exception.code, 400)
        finally:
            configured.shutdown()
            configured.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
