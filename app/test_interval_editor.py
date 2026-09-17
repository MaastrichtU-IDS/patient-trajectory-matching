"""Edited constraints through HTTP, exact conversion, replay and execution gates."""
from copy import deepcopy
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server, interval_editor as editor
from app.temporal_replay import verify


class EditorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server.make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def request(self, path, body=None, origin=None):
        headers = {'Origin': origin or self.url}
        if body is not None: headers['Content-Type'] = 'application/json'
        data = body if type(body) is bytes else json.dumps(body).encode() if body is not None else None
        with urlopen(Request(self.url + path, data=data, headers=headers), timeout=10) as response:
            return json.load(response)

    def controls(self):
        return editor.IntervalEditor().metadata()['default_controls']

    def test_edit_conjunction_inspect_certificates_and_replay(self):
        controls = self.controls()
        original = self.request('/api/editor/run', controls)
        self.assertEqual([p['status'] for p in original['patients']], ['CERTAIN','POSSIBLE','NO_RECORDED_MATCH','INCOMPARABLE'])
        controls['duration'] = {'minimum_minutes':'10','maximum_minutes':'10'}
        controls['minimum_overlap_minutes'] = '3'
        changed = self.request('/api/editor/run', controls)
        self.assertEqual([p['status'] for p in changed['patients']], ['POSSIBLE','NO_RECORDED_MATCH','NO_RECORDED_MATCH','INCOMPARABLE'])
        self.assertEqual(changed['source'], original['source'])
        self.assertEqual(len(changed['query']['constraints']), 3)
        self.assertEqual(changed['workload']['candidate_bindings_per_evaluation'], 4)
        binding = changed['result']['trajectories'][0]['bindings'][0]
        def holds(world):
            a,b,c,d=(world['T01-'+key] for key in ('a-start','a-end','b-start','b-end'))
            return a < c and d < b and b-a == 600000000 and min(b,d)-max(a,c) >= 180000000
        self.assertTrue(holds(binding['possible_witness']))
        self.assertFalse(holds(binding['counterexample']))
        for report in (original, changed):
            exported = self.request('/api/editor/export/' + report['report_id'])
            self.assertEqual(exported, report)
            self.assertTrue(verify(exported)['verified'])
        for key in ('query','source','patients'):
            tampered = deepcopy(changed)
            if key == 'query': tampered[key]['constraints'][0]['operator'] = 'before'
            elif key == 'source': tampered[key]['snapshot_id'] = 'tampered'
            else: tampered[key][0]['status'] = 'CERTAIN'
            tampered['report_id'] = editor.digest({k:v for k,v in tampered.items() if k != 'report_id'})
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'differs from replay'):
                verify(tampered)

    def test_all_allen_controls_execute_without_rewriting(self):
        controls = self.controls()
        for relation in editor.extended.ALLEN:
            controls['relation'] = relation
            report = self.request('/api/editor/run', controls)
            self.assertEqual(report['query']['constraints'], [{'id':'relation','left':'infusion','right':'collection','operator':relation}])
            self.assertTrue(report['result']['search_complete'])
            self.assertEqual(len(report['patients']), 4)

    def test_edited_query_accepts_explicit_extended_relaxation_policy(self):
        from patterns import robust_relaxation
        controls = self.controls()
        controls['duration'] = {'minimum_minutes':'10','maximum_minutes':'10'}
        controls['minimum_overlap_minutes'] = '3'
        original = self.request('/api/editor/run', controls)
        policy = {'profile':'robust-temporal-relaxation-1.0','kind':'extended',
                  'relaxable_targets':['shared-time'],'max_cost':'1.25','max_changed_targets':1,
                  'options':[{'id':'less-shared-time','cost':'1.25',
                              'changes':[{'target':'shared-time','minimum_us':120000000}]}]}
        result = robust_relaxation.execute(original['source'], original['query'], policy)
        self.assertEqual(original['result']['certain_patient_ids'], [])
        self.assertEqual(result['robust_patient_ids'], ['T01'])
        self.assertEqual(result['best_robust_matches'][0]['option_id'], 'less-shared-time')
        self.assertEqual(result['evaluations'][1]['option']['query']['constraints'][:2], original['query']['constraints'][:2])
        self.assertTrue(verify(original)['verified'])

    def test_signed_gap_and_exact_decimal_conversion(self):
        controls = self.controls()
        controls.update(fixture='sequential', relation='gap', gap={'minimum_minutes':'0','maximum_minutes':'48'})
        report = self.request('/api/editor/run', controls)
        self.assertEqual([p['status'] for p in report['patients']], ['CERTAIN','POSSIBLE','NO_RECORDED_MATCH','INCOMPARABLE'])
        controls['gap'] = {'minimum_minutes':'-0.000001','maximum_minutes':'0.000001'}
        query = editor.compile_query(controls)
        self.assertEqual(query['constraints'][0]['min_gap_us'], -60)
        self.assertEqual(query['constraints'][0]['max_gap_us'], 60)
        controls.update(fixture='overlap', gap={'minimum_minutes':'-7','maximum_minutes':'-6'})
        self.assertEqual(self.request('/api/editor/run', controls)['patients'][0]['status'], 'CERTAIN')

    def test_invalid_control_shapes_and_numbers_fail_closed(self):
        invalid = []
        base = self.controls()
        for value in (True, 3, '1e3','NaN','Infinity','1441','-1','0','0.0000001','9'*1000):
            invalid.append({**base, 'minimum_overlap_minutes':value})
        invalid += [{**base,'fixture':'/etc/passwd'}, {**base,'relation':'disjunction'},
                    {**base,'duration':{'minimum_minutes':'0','maximum_minutes':'10'}},
                    {**base,'duration':{'minimum_minutes':'0','maximum_minutes':'0'}},
                    {**base,'extra':{}}, {**base,'gap':{'minimum_minutes':'0','maximum_minutes':'1'}},
                    {**base,'duration':{'minimum_minutes':'11','maximum_minutes':'10'}},
                    {**base,'relation':'gap','gap':None},
                    {**base,'duration':{'minimum_minutes':'0','maximum_minutes':'10','slot':'collection'}},
                    {'fixture':'overlap'}, ['contains'], b'{"fixture":"overlap","fixture":"sequential"}']
        for value in invalid:
            with self.subTest(value=str(value)[:100]), self.assertRaises(HTTPError) as caught:
                self.request('/api/editor/run', value)
            self.assertEqual(caught.exception.code, 400)

    def test_limits_and_incomplete_execution_do_not_save_report(self):
        workspace = editor.IntervalEditor()
        with patch.dict(editor.LIMITS, candidate_bindings_per_evaluation=0), \
                patch.object(editor.bt, 'prepare', side_effect=AssertionError('No solver before admission')):
            with self.assertRaisesRegex(ValueError, 'execution limits'):
                workspace.run(self.controls())
        with patch.object(editor.extended, 'execute', return_value={'search_complete':False}):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                workspace.run(self.controls())
        self.assertEqual(len(workspace._reports), 0)

    def test_retention_defensive_copies_and_source_freeze(self):
        workspace = editor.IntervalEditor()
        controls = self.controls()
        original = workspace.run(controls)
        original_id = original['report_id']
        controls['relation'] = 'before'
        original['source']['events'].clear()
        saved = workspace.export(original_id)
        self.assertEqual(len(saved['source']['events']), 8)
        self.assertEqual(saved['controls']['relation'], 'contains')
        saved['patients'].clear()
        self.assertEqual(len(workspace.export(original_id)['patients']), 4)
        with patch.dict(editor.LIMITS, saved_results=1):
            workspace.run(controls)
        with self.assertRaises(KeyError): workspace.export(original_id)

    def test_origin_assets_and_metadata_do_not_execute(self):
        with patch.object(editor.extended, 'execute', side_effect=AssertionError('Metadata is cheap')):
            self.assertEqual(len(self.request('/api/editor')['relations']),14)
        for path in ('/temporal/editor','/editor.js'):
            with urlopen(self.url + path) as response:
                self.assertEqual(response.status,200)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/editor/run',self.controls(),origin='https://other.example')
        self.assertEqual(caught.exception.code,403)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/editor/export/expired')
        self.assertEqual(caught.exception.code,404)


if __name__ == '__main__': unittest.main()
