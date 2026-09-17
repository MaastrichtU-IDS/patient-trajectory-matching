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

    def relaxed_controls(self):
        return {**self.controls(), 'duration': {'minimum_minutes':'10','maximum_minutes':'10'},
                'minimum_overlap_minutes': '3',
                'relaxation': {'max_cost':'1.25', 'gap':None, 'duration':None, 'minimum_overlap_minutes':'2'}}

    def test_http_relaxation_preserves_original_and_exports_each_certificate(self):
        controls = self.relaxed_controls()
        report = self.request('/api/editor/run', controls)
        first = report['patients'][0]
        self.assertEqual(first['status'], 'POSSIBLE')
        self.assertEqual(first['option_status'], 'CERTAIN')
        self.assertEqual((first['selected_option'], first['selected_cost']), ('edited-option','1.25'))
        self.assertEqual(report['patients'][3]['option_status'], 'INCOMPARABLE')
        self.assertEqual(report['source'], editor.IntervalEditor().run(self.controls())['source'])
        self.assertEqual(report['workload']['evaluations'], 2)
        self.assertEqual(report['relaxation']['robust_patient_ids'], ['T01'])
        option = report['relaxation']['evaluations'][1]
        self.assertEqual(option['option']['query']['constraints'][:2], report['query']['constraints'][:2])
        witness = option['result']['trajectories'][0]['bindings'][0]['possible_witness']
        a,b,c,d = (witness['T01-'+key] for key in ('a-start','a-end','b-start','b-end'))
        self.assertTrue(a < c and d < b and b-a == 600000000 and min(b,d)-max(a,c) >= 120000000)
        exported = self.request('/api/editor/export/' + report['report_id'])
        self.assertEqual(exported, report)
        self.assertTrue(verify(exported)['verified'])
        for field in ('policy','relaxation','patients'):
            altered = deepcopy(report)
            if field == 'policy': altered[field]['max_cost'] = '0'
            elif field == 'relaxation': altered[field]['evaluations'][1]['option']['cost'] = '0'
            else: altered[field][0]['option_status'] = 'POSSIBLE'
            altered['report_id'] = editor.digest({k:v for k,v in altered.items() if k != 'report_id'})
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'differs from replay'):
                verify(altered)

    def test_budget_exclusion_gap_widening_and_original_first(self):
        controls = self.relaxed_controls()
        controls['relaxation']['max_cost'] = '0'
        report = self.request('/api/editor/run', controls)
        self.assertEqual(report['relaxation']['excluded_by_budget'], ['edited-option'])
        self.assertEqual(len(report['relaxation']['evaluations']), 1)
        self.assertIsNone(report['patients'][0]['option_status'])
        self.assertIsNone(report['patients'][0]['selected_option'])
        self.assertTrue(verify(report)['verified'])
        controls.update(fixture='sequential', relation='gap', gap={'minimum_minutes':'0','maximum_minutes':'48'},
                        duration=None, minimum_overlap_minutes=None,
                        relaxation={'max_cost':'1.25','gap':{'minimum_minutes':'0','maximum_minutes':'50'},
                                    'duration':None,'minimum_overlap_minutes':None})
        report = self.request('/api/editor/run', controls)
        self.assertEqual(report['patients'][0]['selected_option'], 'original')
        self.assertEqual(report['patients'][0]['selected_cost'], '0')
        self.assertEqual(report['patients'][1]['status'], 'POSSIBLE')
        self.assertEqual(report['patients'][1]['option_status'], 'CERTAIN')

    def test_combined_option_is_one_evaluation_and_duration_stays_positive(self):
        controls = self.relaxed_controls()
        controls['duration'] = {'minimum_minutes':'9','maximum_minutes':'9'}
        controls['relaxation']['duration'] = {'minimum_minutes':'9','maximum_minutes':'10'}
        report = self.request('/api/editor/run', controls)
        self.assertEqual(report['patients'][0]['status'], 'NO_RECORDED_MATCH')
        self.assertEqual(report['patients'][0]['option_status'], 'CERTAIN')
        self.assertEqual(len(report['policy']['options']), 1)
        self.assertEqual(len(report['policy']['options'][0]['changes']), 2)
        self.assertEqual(len(report['relaxation']['evaluations']), 2)
        # Either change alone is insufficient for certainty.
        for field in ('duration','minimum_overlap_minutes'):
            partial = deepcopy(controls)
            partial['relaxation'][field] = None
            self.assertNotIn('T01', self.request('/api/editor/run', partial)['relaxation']['robust_patient_ids'])

    def test_relaxation_validation_before_budget_and_solver(self):
        base = self.relaxed_controls()
        invalid = [None, True, 0, '0', '3', '4', 'NaN', '0.0000001']
        requests = []
        for value in invalid:
            c = deepcopy(base); c['relaxation']['minimum_overlap_minutes'] = value; requests.append(c)
        for value in ('2', 1.25, True):
            c = deepcopy(base); c['relaxation']['max_cost'] = value; requests.append(c)
        for low,high in (('0','10'),('10','10'),('11','12'),('11','9')):
            c = deepcopy(base); c['relaxation']['duration'] = {'minimum_minutes':low,'maximum_minutes':high}; requests.append(c)
        c = deepcopy(base); c['relaxation']['gap'] = {'minimum_minutes':'0','maximum_minutes':'50'}; requests.append(c)
        c = deepcopy(base); c['minimum_overlap_minutes'] = None; requests.append(c)
        c = deepcopy(base); c['relaxation']['relation'] = 'before'; requests.append(c)
        c = deepcopy(base); c['relaxation'] = []; requests.append(c)
        for candidate in requests:
            for budget in ('0','1.25'):
                request = deepcopy(candidate)
                if type(request['relaxation']) is dict and request['relaxation']['max_cost'] == '1.25':
                    request['relaxation']['max_cost'] = budget
                with self.subTest(request=request), patch.object(editor.bt, 'prepare', side_effect=AssertionError('No source preparation')), self.assertRaises(HTTPError) as caught:
                    self.request('/api/editor/run', request)
                self.assertEqual(caught.exception.code, 400)

    def test_incomplete_relaxed_evaluation_and_option_limit_never_save(self):
        workspace = editor.IntervalEditor()
        execute = editor.extended.execute
        count = 0
        def incomplete_option(*args):
            nonlocal count
            count += 1
            return execute(*args) if count == 1 else {'search_complete':False}
        with patch.object(editor.extended, 'execute', side_effect=incomplete_option):
            with self.assertRaisesRegex(ValueError, 'Incomplete'):
                workspace.run(self.relaxed_controls())
        self.assertEqual(count, 2)
        self.assertFalse(workspace._reports)
        with patch.dict(editor.LIMITS, catalogue_options=0), patch.object(editor.bt, 'prepare', side_effect=AssertionError('No source preparation')):
            with self.assertRaisesRegex(ValueError, 'execution limits'):
                workspace.run(self.relaxed_controls())

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
