"""Acceptance checks for distinct-patient selection and reproducible evidence."""
import copy
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
import cohort
import serve


class GuidedCohortTests(unittest.TestCase):
    def test_expected_membership_and_fixed_boundaries(self):
        expected = {'0': ['P01', 'P02', 'P03'],
                    '1': ['P01', 'P02', 'P03', 'P04', 'P05'],
                    '2': ['P01', 'P02', 'P03', 'P04', 'P05', 'P06']}
        for budget, included in expected.items():
            result = cohort.run_cohort(serve.COHORT, budget)
            self.assertEqual(result['candidate_count'], 10)
            self.assertEqual(result['membership']['included'], included)
            self.assertEqual(result['membership']['exact'], ['P01', 'P02', 'P03'])
            self.assertEqual(result['membership']['unresolved'], ['P10'])
            self.assertNotIn('P00', [r['patient_id'] for r in result['results']])
            by_id = {r['patient_id']: r for r in result['results']}
            self.assertIn('HARD_VALUE_FAILURE', by_id['P07']['reason_codes'])
            self.assertEqual(by_id['P08']['accepted_as'], 'NONE')
            self.assertIn('HARD_TEMPORAL_FAILURE', by_id['P09']['reason_codes'])
            self.assertEqual(by_id['P02']['total_cost'], '0')
            if budget == '2':
                self.assertEqual(by_id['P06']['components'], {'exposure': '1', 'exposure_window': '1'})
                self.assertEqual(by_id['P06']['total_cost'], '2')

    def test_every_history_rebuilds_from_validated_pro_solid(self):
        from patterns.pro_solid import build_graph, project
        ids, role_ids = set(), set()
        for patient in serve.COHORT['patients']:
            case, evidence = project(build_graph(patient['source_rows']), patient['manifest'])
            case.update(case_id=patient['case']['case_id'], description=patient['case']['description'])
            self.assertEqual(case, patient['case'])
            self.assertEqual(evidence, patient['evidence'])
            bearers = set()
            for event in case['events']:
                self.assertNotIn(event['event_id'], ids)
                ids.add(event['event_id'])
                binding = evidence['bindings'][event['event_id']]
                self.assertNotIn(binding['patient_role'], role_ids)
                role_ids.add(binding['patient_role'])
                bearers.add(binding['patient_bearer'])
                self.assertEqual(event['provenance']['source_record_sha256'],
                                 binding['source_record_sha256'])
            self.assertEqual(len(bearers), 1)
        self.assertEqual(len(ids), 33)
        p03 = next(p for p in serve.COHORT['patients'] if p['patient_id'] == 'P03')
        baseline = p03['evidence']['bindings']['P03-B']
        self.assertEqual(baseline['original_value'], '8.0')
        self.assertEqual(baseline['original_unit'], 'mg/L')
        self.assertEqual(float(baseline['normalized_value']), 0.8)

    def test_export_reproduces_and_detects_changes(self):
        for budget in ('0', '1', '2'):
            result = cohort.run_cohort(serve.COHORT, budget)
            bundle = json.loads(json.dumps(cohort.export_cohort(serve.COHORT, result)))
            self.assertEqual(cohort.verify_export(bundle), result)
        for target in ('dataset', 'result', 'query', 'oracle'):
            changed = copy.deepcopy(bundle)
            if target == 'dataset':
                changed['dataset']['patients'][0]['case']['age'] = 9
            elif target == 'result':
                changed['evaluation']['membership']['included'].append('P07')
            elif target == 'query':
                changed['evaluation']['query']['constraints'][0]['op'] = 'after'
            else:
                changed['evaluation']['oracle_sha256'] = 'not-this-implementation'
            with self.assertRaises(ValueError, msg=target):
                cohort.verify_export(changed)

    def test_no_graph_packages_needed_to_match_the_committed_projection(self):
        completed = subprocess.run([sys.executable, '-S', '-c',
            "import json, cohort; data=json.load(open('cohort-data.json')); "
            "print(cohort.run_cohort(data,'2')['membership']['included'])"],
            cwd=serve.ROOT, capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("'P06'", completed.stdout)

    def test_standalone_is_current_and_all_replays_are_actual_results(self):
        replays = {}
        for budget in ('0', '1', '2'):
            replays[budget] = cohort.run_cohort(serve.COHORT, budget)
            replays[budget]['execution_mode'] = 'recorded-replay'
        payload = {'dataset': serve.COHORT, 'replays': replays,
                   'story': json.loads((serve.ROOT / 'story.json').read_text())}
        html = (serve.ROOT / 'guided.html').read_text()
        html = html.replace('/*GUIDED_CSS*/', (serve.ROOT / 'guided.css').read_text())
        html = html.replace('/*GUIDED_DATA*/null', json.dumps(payload, separators=(',', ':')).replace('</', '<\\/'))
        html = html.replace('/*GUIDED_JS*/', (serve.ROOT / 'guided.js').read_text())
        self.assertEqual(html, (serve.ROOT / 'Guided_Cohort_Demo.html').read_text())
        self.assertEqual(len(payload['story']), 5)
        self.assertEqual(serve.COHORT['projection_sha256'], cohort.file_digest(serve.SOURCE / 'patterns/pro_solid.py'))

    def test_live_journey_http_export_and_invalid_requests(self):
        with patch.object(serve.Handler, 'log_message'):
            server = serve.ThreadingHTTPServer(('127.0.0.1', 0), serve.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                root = f'http://127.0.0.1:{server.server_port}'
                for budget, count in [('0', 3), ('1', 5), ('2', 6)]:
                    with urlopen(root + f'/api/cohort?budget={budget}') as response:
                        result = json.load(response)
                    self.assertEqual(len(result['membership']['included']), count)
                    self.assertEqual(result['execution_mode'], 'live-python')
                    with urlopen(root + f'/api/cohort/export?budget={budget}') as response:
                        bundle = json.load(response)
                    self.assertEqual(bundle['evaluation'], result)
                    self.assertEqual(cohort.verify_export(bundle), result)
                for query in ('budget=3', 'budget=-1', 'budget=', 'budget=0&budget=2', 'patient=P01'):
                    with self.assertRaises(HTTPError) as caught:
                        urlopen(root + '/api/cohort?' + query)
                    self.assertEqual(caught.exception.code, 400)
                for path, marker in [('/', b'From one patient to a cohort'), ('/lab', b'16 constructed cases')]:
                    with urlopen(root + path) as response:
                        self.assertIn(marker, response.read())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


if __name__ == '__main__':
    unittest.main()
