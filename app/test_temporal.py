"""Actual HTTP temporal journey, certificate arithmetic and admission boundaries."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server, temporal
from app.temporal_replay import verify


class TemporalHTTPTests(unittest.TestCase):
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

    def request(self, path, body=None, origin=None):
        headers = {'Origin': origin or self.url}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        with urlopen(Request(self.url + path, data=raw, headers=headers), timeout=10) as response:
            return json.load(response)

    def test_http_journey_certificates_export_and_replay(self):
        meta = self.request('/api/temporal')
        original = self.request('/api/temporal/run', {'budget': '0'})
        self.assertEqual(original['result']['robust_patient_ids'], ['T01'])
        self.assertEqual(original['result']['possible_patient_ids'], ['T01', 'T02'])
        self.assertEqual(original['result']['excluded_by_budget'], ['widen-to-50-minutes'])
        self.assertEqual([r['original_status'] for r in original['patients']],
                         ['CERTAIN_MATCH', 'POSSIBLE_MATCH', 'NO_RECORDED_MATCH', 'INCOMPARABLE'])
        widening = self.request('/api/temporal/run', {'budget': '1.25'})
        self.assertEqual(widening['added_robust_patient_ids'], ['T02'])
        self.assertEqual(widening['result']['robust_patient_ids'], ['T01', 'T02'])
        self.assertEqual(widening['patients'][0]['selected_option'], 'original')
        self.assertEqual(widening['patients'][1]['selected_cost'], '1.25')
        self.assertEqual(widening['inputs']['source'], original['inputs']['source'])
        self.assertEqual(widening['inputs']['query'], original['inputs']['query'])
        evaluations = widening['result']['evaluations']
        first, second = [e['result'] for e in evaluations]
        self.assertEqual(first['context']['source_context_id'], second['context']['source_context_id'])
        t02 = next(t for t in first['trajectories'] if t['patient_id'] == 'T02')['bindings'][0]
        # Independently check returned numerical assignments against source bounds
        # and the user's minute window, not just the executor's status strings.
        for key in ('possible_witness', 'counterexample'):
            witness = t02[key]
            for v in original['inputs']['source']['variables']:
                if v['patient_id'] == 'T02':
                    self.assertLessEqual(v['lower_us'], witness[v['id']])
                    self.assertLessEqual(witness[v['id']], v['upper_us'])
            gap = witness['T02-b-start'] - witness['T02-a-end']
            if key == 'possible_witness':
                self.assertTrue(0 < gap <= 48 * 60000000)
            else:
                self.assertFalse(0 < gap <= 48 * 60000000)
        self.assertEqual(next(t for t in second['trajectories'] if t['patient_id'] == 'T02')['status'], 'CERTAIN_MATCH')
        self.assertTrue(next(t for t in first['trajectories'] if t['patient_id'] == 'T03')['bindings'][0]['negative_cycle'])
        t04 = next(t for t in first['trajectories'] if t['patient_id'] == 'T04')['bindings'][0]
        self.assertEqual(t04['reason_codes'], ['CLOCK_MISMATCH'])
        self.assertIsNone(t04['possible'])
        self.assertEqual(meta['workload']['candidate_bindings_per_evaluation'], 4)
        self.assertEqual(meta['source_sha256'], temporal.digest(original['inputs']['source']))
        for report in (original, widening):
            bundle = self.request('/api/temporal/export/' + report['report_id'])
            self.assertEqual(bundle, report)
            self.assertTrue(verify(bundle)['verified'])
            with urlopen(self.url + '/api/temporal/export/' + report['report_id']) as response:
                self.assertIn('temporal-analysis.json', response.headers['Content-Disposition'])
        summary = {'profile': 'temporal-workspace-journey-verification-1', 'passed': True,
                   'execution': 'actual-loopback-http-and-local-replay',
                   'scope': original['scope'], 'original_counts': original['original_counts'],
                   'added_robust_patient_ids': widening['added_robust_patient_ids'],
                   'report_ids': [r['report_id'] for r in (original, widening)],
                   'source_sha256': meta['source_sha256'], 'limits': meta['limits'],
                   'witness_and_counterexample_arithmetic_checked': True,
                   'clinical_validity_claim': False,
                   'artifacts': {p: hashlib.sha256((temporal.ROOT / p).read_bytes()).hexdigest()
                                 for p in (*temporal.ARTIFACTS, 'app/server.py', 'app/test_temporal.py', 'app/test_temporal_ui.cjs')}}
        path = temporal.ROOT / 'verification/research-prototype-run/temporal-journey.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')

    def test_strict_temporal_requests_and_origin(self):
        for body in ({}, {'budget': 0}, {'budget': True}, {'budget': '2'}, {'budget': '1.250'},
                     {'budget': 'NaN'}, {'budget': '0', 'source': {}}, {'budget': '0', 'query': {}},
                     b'{"budget":"0","budget":"1.25"}'):
            with self.subTest(body=body), self.assertRaises(HTTPError) as caught:
                self.request('/api/temporal/run', body)
            self.assertEqual(caught.exception.code, 400)
        for path, body in (('/api/temporal', None), ('/api/temporal/run', {'budget': '0'})):
            with self.assertRaises(HTTPError) as caught:
                self.request(path, body, origin='https://other.example')
            self.assertEqual(caught.exception.code, 403)

    def test_incomplete_execution_cannot_publish_result(self):
        before = deepcopy(self.server.workspace.temporal._reports)
        with patch.object(temporal.relax, 'execute', return_value={'status': 'BLOCKED', 'search_complete': False}):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/temporal/run', {'budget': '0'})
            self.assertEqual(caught.exception.code, 400)
        self.assertEqual(self.server.workspace.temporal._reports, before)

    def test_assets_metadata_and_unknown_export(self):
        for path, content_type in (('/temporal', 'text/html'), ('/temporal.js', 'text/javascript')):
            with urlopen(self.url + path) as response:
                self.assertEqual(response.headers.get_content_type(), content_type)
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/temporal/export/not-retained')
        self.assertEqual(caught.exception.code, 404)
        with patch.object(temporal.relax, 'execute', side_effect=AssertionError('Metadata must not execute')):
            self.request('/api/temporal')
            self.request('/readyz')


class TemporalAdmissionTests(unittest.TestCase):
    def test_size_and_binding_limits_precede_executor(self):
        workspace = temporal.TemporalWorkspace()
        for key in ('variables', 'events', 'clocks', 'patients', 'episodes', 'slots',
                    'query_constraints', 'candidate_bindings_per_evaluation', 'evaluations', 'catalogue_options'):
            limits = {**temporal.LIMITS, key: 0}
            with self.subTest(key=key), patch.object(temporal, 'LIMITS', limits), \
                    patch.object(temporal.relax, 'execute', side_effect=AssertionError('Must not execute')):
                with self.assertRaisesRegex(ValueError, 'execution limits'):
                    workspace.run('1.25')

    def test_tampered_rehashed_export_and_alternate_fixture_rejected(self):
        bundle = temporal.TemporalWorkspace().run('1.25')
        for field in ('patients', 'inputs', 'artifacts', 'result'):
            changed = deepcopy(bundle)
            if field == 'patients': changed[field][1]['original_status'] = 'CERTAIN_MATCH'
            elif field == 'inputs': changed[field]['source']['snapshot_id'] = 'different'
            elif field == 'artifacts': changed[field]['app/temporal.py'] = 'wrong'
            else: changed[field]['robust_patient_ids'].append('T04')
            changed['report_id'] = temporal.digest({k: v for k, v in changed.items() if k != 'report_id'})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'differs from replay'):
                verify(changed)
        bundle['budget'] = '0'
        with self.assertRaisesRegex(ValueError, 'fingerprint'):
            verify(bundle)

    def test_copies_and_bounded_retention(self):
        workspace = temporal.TemporalWorkspace()
        metadata = workspace.metadata()
        metadata['query']['slots'].clear()
        report = workspace.run('0')
        report_id = report['report_id']
        report['inputs']['source']['events'].clear()
        self.assertEqual(len(workspace.export(report_id)['inputs']['source']['events']), 8)
        exported = workspace.export(report_id)
        exported['patients'].clear()
        self.assertEqual(len(workspace.export(report_id)['patients']), 4)
        with patch.dict(temporal.LIMITS, saved_results=1):
            workspace.run('1.25')
        with self.assertRaises(KeyError):
            workspace.export(report_id)


if __name__ == '__main__':
    unittest.main()
