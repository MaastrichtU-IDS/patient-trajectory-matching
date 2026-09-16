"""Integration checks for the presentation demo and its failure diagnostics."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import serve


class DemoTests(unittest.TestCase):
    def test_recorded_inputs_and_oracle_are_current(self):
        self.assertEqual(serve.DATA['oracle_sha256'], hashlib.sha256((serve.SOURCE / 'reference_oracle.py').read_bytes()).hexdigest())
        for key, path in [('cases', 'examples/cases.json'), ('pattern', 'examples/exemplar.pattern.json'),
                          ('taxonomy', 'ontology/toy-taxonomy.json')]:
            self.assertEqual(serve.DATA[key], json.loads((serve.SOURCE / path).read_text()))

    def test_all_480_replay_results_match_the_live_oracle(self):
        self.assertEqual(len(serve.DATA['runs']), 30)
        for key, expected in serve.DATA['runs'].items():
            budget, count, complete = key.split(':')
            actual = []
            for fixture in serve.DATA['cases']:
                case = copy.deepcopy(fixture)
                case['budget_override'] = {'max_total_cost': budget, 'max_relaxed_constraints': int(count)}
                case['source_search_complete'] = complete == '1'
                actual.append(serve.evaluate(case, serve.DATA['pattern'], serve.DATA['taxonomy']))
            self.assertEqual(actual, expected, key)

    def test_both_live_pipelines(self):
        for kind in ('graph', 'semantic'):
            result, status = serve.run_pipeline(kind)
            self.assertEqual(status, 200, result)
            if kind == 'graph':
                self.assertEqual(result['result']['accepted_as'], 'EXACT')
            else:
                self.assertEqual(result['result']['status'], 'READY')
                self.assertEqual(result['result']['matching']['certain_patient_ids'], ['P1'])

    def test_missing_dependency_identifies_actual_error_and_interpreter(self):
        failure = subprocess.CompletedProcess([], 1, '', "ModuleNotFoundError: No module named 'rdflib'")
        with patch.object(serve.subprocess, 'run', return_value=failure):
            result, status = serve.run_pipeline('graph')
        self.assertEqual(status, 503)
        self.assertIn('rdflib', result['detail'])
        self.assertEqual(result['environment']['python_executable'], sys.executable)
        self.assertIn('requirements-semantic.lock.txt', result['environment']['install_command'])

    def test_semantic_failure_survives_an_empty_stderr(self):
        def blocked(args, **kwargs):
            output = Path(args[-1])
            (output / 'result.json').write_text(json.dumps({
                'status': 'UNRESOLVED_SEMANTICS', 'semantic_support': {
                    'backend_result': {'status': 'BACKEND_ERROR', 'reason': 'native library unavailable'}}}))
            return subprocess.CompletedProcess(args, 2, '', '')
        with patch.object(serve.subprocess, 'run', side_effect=blocked):
            result, status = serve.run_pipeline('semantic')
        self.assertEqual(status, 503)
        self.assertIn('UNRESOLVED_SEMANTICS', result['error'])
        self.assertIn('native library unavailable', result['detail'])

    def test_timeout_is_not_reported_as_missing_dependencies(self):
        with patch.object(serve.subprocess, 'run', side_effect=subprocess.TimeoutExpired([], 45)):
            result, status = serve.run_pipeline('semantic')
        self.assertEqual(status, 503)
        self.assertIn('45-second', result['error'])
        self.assertNotIn('Install', result['error'])

    def test_http_route_and_invalid_control(self):
        with patch.object(serve.Handler, 'log_message'):
            server = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root = f'http://127.0.0.1:{server.server_port}'
                with urlopen(root + '/api/match?budget=1&count=2&complete=1') as response:
                    self.assertEqual(json.load(response)['results'], serve.DATA['runs']['1:2:1'])
                with urlopen(root) as response:
                    self.assertIn(b'Patient Trajectory Matching', response.read())
                with self.assertRaises(HTTPError) as caught:
                    urlopen(root + '/api/match?budget=arbitrary')
                self.assertEqual(caught.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_embedded_replay_uses_committed_data_and_diagnostics(self):
        html = (serve.ROOT / 'Patient_Trajectory_Demo.html').read_text()
        template = (serve.ROOT / 'ui.html').read_text()
        encoded = json.dumps(serve.DATA, separators=(',', ':')).replace('</', '<\\/')
        self.assertEqual(html, template.replace('/*DEMO_DATA*/null', encoded))
        self.assertIn('diagnostic-text-semantic', html)
        self.assertIn('Displayed results were not updated', html)


if __name__ == '__main__':
    unittest.main()
