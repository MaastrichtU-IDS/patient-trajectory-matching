"""Live HTTP acceptance for the bounded reference-to-temporal-cohort journey."""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server
from app.temporal_replay import verify


class JourneyHTTPTests(unittest.TestCase):
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

    def request(self, path, body=None, headers=None):
        request_headers = {'Origin': self.url}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        with urlopen(Request(self.url + path, data=raw, headers=request_headers), timeout=10) as response:
            return json.load(response)

    def controls(self, **changes):
        return {'reference_patient_id': 'T03', 'top_k': 1, 'maximum_baseline': None,
                'question': 'overlap', 'budget': '0', **changes}

    def test_replay_cli_reports_a_refused_export_as_a_result_not_a_traceback(self):
        """A refusal is the tool working: the export does not match this implementation.

        verify() raises, and several test modules rely on that, so its contract is unchanged.
        The CLI must translate the refusal into this repository's contract-failure convention
        -- structured stdout and exit 2 -- rather than let the traceback escape.
        """
        root = Path(server.__file__).resolve().parents[1]
        run = self.request('/api/journey/run', self.controls(budget='1.25'))
        export = self.request('/api/journey/export/' + run['report_id'])

        def replay(bundle_or_text, name):
            with tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / f'{name}.json'
                path.write_text(bundle_or_text if isinstance(bundle_or_text, str) else json.dumps(bundle_or_text))
                return subprocess.run([sys.executable, '-m', 'app.temporal_replay', str(path)],
                                      cwd=root, capture_output=True, text=True, timeout=120)

        ok = replay(export, 'valid')
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(json.loads(ok.stdout)['verified'])

        for name, bundle in (('tampered', {**export, 'request': dict(export['request'], budget='0')}),
                             ('unsupported', {'format': 'no-such-export-1'}),
                             ('malformed', '{not json')):
            with self.subTest(case=name):
                refused = replay(bundle, name)
                self.assertEqual(refused.returncode, 2, refused.stderr)
                self.assertNotIn('Traceback', refused.stderr)
                payload = json.loads(refused.stdout)
                self.assertIs(payload['verified'], False)
                self.assertTrue(payload['reason'])

    def test_reference_to_cohort_preserves_original_and_evaluates_beyond_preview(self):
        metadata = self.request('/api/journey')
        self.assertEqual({row['patient_id'] for row in metadata['patients']}, {'T01','T02','T03','T04'})
        original = self.request('/api/journey/run', self.controls())
        relaxed = self.request('/api/journey/run', self.controls(budget='1.25'))
        self.assertEqual(relaxed['eligibility']['eligible_patient_ids'], ['T01','T02','T04'])
        self.assertEqual({row['patient_id'] for row in relaxed['patients']}, {'T01','T02','T04'})
        self.assertEqual(len(relaxed['ranking']['displayed_patient_ids']), 1)
        self.assertNotIn('T01', relaxed['ranking']['displayed_patient_ids'])
        self.assertEqual(relaxed['added_robust_patient_ids'], ['T01'])
        self.assertEqual(original['result'], relaxed['result'])
        self.assertEqual(original['source'], relaxed['source'])
        self.assertEqual(original['query'], relaxed['query'])
        self.assertEqual(original['relaxation']['robust_patient_ids'], [])
        self.assertEqual(relaxed['relaxation']['robust_patient_ids'], ['T01'])
        patients = {row['patient_id']: row for row in relaxed['patients']}
        self.assertEqual(patients['T01']['original_status'], 'POSSIBLE')
        self.assertEqual(patients['T01']['option_status'], 'CERTAIN')
        self.assertEqual(patients['T04']['original_status'], 'INCOMPARABLE')
        self.assertEqual(patients['T04']['option_status'], 'INCOMPARABLE')
        self.assertTrue(patients['T01']['explanation']['original']['summary'])
        self.assertTrue(patients['T01']['evidence']['treatment'])
        self.assertTrue(patients['T01']['evidence']['observation'])
        self.assertIn('not', str(patients['T01']['evidence']['clinical_outcome']).lower())
        # An independent unit calculation checks an actual solver witness and counterexample.
        trajectory = next(row for row in relaxed['result']['trajectories'] if row['patient_id'] == 'T01')
        binding = trajectory['bindings'][0]
        def shared_minutes(world):
            a,b,c,d = (world['T01-'+key] for key in ('a-start','a-end','b-start','b-end'))
            self.assertTrue(a < c < d < b)
            self.assertEqual((b-a) / 60000000, 10)
            return (min(b,d)-max(a,c)) / 60000000
        self.assertGreaterEqual(shared_minutes(binding['possible_witness']), 3)
        self.assertLess(shared_minutes(binding['counterexample']), 3)
        self.assertGreaterEqual(shared_minutes(binding['counterexample']), 2)
        for report in (original, relaxed):
            with urlopen(self.url + '/api/journey/export/' + report['report_id']) as response:
                self.assertIn('attachment', response.headers['Content-Disposition'])
                exported = json.load(response)
            self.assertEqual(exported, report)
            self.assertEqual(exported['format'], 'patient-journey-export-1')
            self.assertTrue(verify(exported)['verified'])

    def test_routes_and_request_boundaries(self):
        for path, mime in (('/journey','text/html'),('/journey.js','text/javascript'),('/journey.css','text/css')):
            with self.subTest(path=path), urlopen(self.url + path) as response:
                self.assertEqual(response.headers.get_content_type(), mime)
                self.assertIn("script-src 'self'", response.headers['Content-Security-Policy'])
        invalid = [b'{', b'{"budget":"0","budget":"1.25"}', [],
                   self.controls(reference_patient_id='P00'), self.controls(top_k=True),
                   self.controls(budget=1.25), self.controls(question='unknown'),
                   self.controls(maximum_baseline='NaN'), {**self.controls(), 'source': {}}]
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(HTTPError) as caught:
                self.request('/api/journey/run', body)
            self.assertEqual(caught.exception.code, 400)
        for path, body in (('/api/journey',None),('/api/journey/run',self.controls())):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(path, body, {'Origin':'https://other.example'})
            self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/journey/run', b' ' * (server.MAX_BODY + 1))
        self.assertEqual(caught.exception.code, 413)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/journey/run', self.controls(), {'Content-Type':'text/plain'})
        self.assertEqual(caught.exception.code, 415)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/journey/export/not-retained')
        self.assertEqual(caught.exception.code, 404)

    def test_changing_question_keeps_the_same_patient_records(self):
        overlap = self.request('/api/journey/run', self.controls())
        sequential = self.request('/api/journey/run', self.controls(question='sequential'))
        self.assertNotEqual(overlap['query'], sequential['query'])
        self.assertEqual(overlap['source'], sequential['source'])
        self.assertEqual(overlap['admitted_source_sha256'], sequential['admitted_source_sha256'])
        self.assertEqual(overlap['baseline_source'], sequential['baseline_source'])
        self.assertEqual(overlap['eligibility'], sequential['eligibility'])


if __name__ == '__main__':
    unittest.main()
