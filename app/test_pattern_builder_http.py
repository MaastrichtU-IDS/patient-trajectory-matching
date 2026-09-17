"""HTTP acceptance for configurable two- and three-event journey patterns."""
from copy import deepcopy
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server
from app.temporal_replay import verify


def three_event_pattern():
    return {
        'slots': [
            {'id': 'infusion', 'event_kind': 'infusion'},
            {'id': 'collection', 'event_kind': 'specimen_collection'},
            {'id': 'followup', 'event_kind': 'specimen_collection'},
        ],
        'constraints': [
            {'id': 'c1', 'operator': 'contains', 'left': 'infusion', 'right': 'collection'},
            {'id': 'c2', 'operator': 'gap', 'left': 'collection', 'right': 'followup',
             'minimum_minutes': '0', 'maximum_minutes': '30'},
        ],
    }


class PatternBuilderHTTPTests(unittest.TestCase):
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
                'question': 'custom', 'budget': '0', 'pattern': three_event_pattern(), **changes}

    def test_three_events_edit_cohort_and_replay_full_pool(self):
        compiled = self.request('/api/journey/compile', {'pattern': three_event_pattern()})
        original = self.request('/api/journey/run', self.controls(pattern=compiled['pattern']))
        self.assertEqual(original['query'], compiled['query'])
        self.assertEqual(original['pattern'], compiled['pattern'])
        self.assertEqual(original['request']['pattern'], compiled['pattern'])
        self.assertEqual(original['eligibility']['eligible_patient_ids'], ['T01', 'T02', 'T04'])
        self.assertEqual({p['patient_id'] for p in original['patients']}, {'T01', 'T02', 'T04'})
        self.assertEqual(original['result']['certain_patient_ids'], ['T01'])
        self.assertEqual(original['result']['possible_patient_ids'], ['T01', 'T02'])
        self.assertEqual(len(original['ranking']['displayed_patient_ids']), 1)
        self.assertNotIn('T01', original['ranking']['displayed_patient_ids'])
        self.assertNotIn('T03', {e['patient_id'] for e in original['source']['events']})
        self.assertEqual(original['policy']['options'], [])
        self.assertEqual(len(original['relaxation']['evaluations']), 1)
        self.assertEqual(original['added_robust_patient_ids'], [])
        statuses = {p['patient_id']: p['original_status'] for p in original['patients']}
        self.assertEqual(statuses, {'T01': 'CERTAIN', 'T02': 'POSSIBLE', 'T04': 'INCOMPARABLE'})
        # The two collection slots select distinct recorded events, including the new follow-up.
        t01 = next(t for t in original['result']['trajectories'] if t['patient_id'] == 'T01')
        certain = next(binding for binding in t01['bindings'] if binding['certain'])
        event_ids = [slot['event_id'] for slot in certain['slots'].values()]
        self.assertEqual(len(set(event_ids)), 3)
        records = {e['id']: e for e in original['source']['events']}
        self.assertEqual(records[certain['slots']['followup']['event_id']]['event_kind'], 'specimen_collection')

        edited_pattern = deepcopy(compiled['pattern'])
        edited_pattern['constraints'][1]['maximum_minutes'] = '1'
        edited = self.request('/api/journey/run', self.controls(pattern=edited_pattern))
        self.assertEqual(edited['result']['certain_patient_ids'], [])
        self.assertEqual(edited['result']['possible_patient_ids'], [])
        self.assertEqual(next(p['original_status'] for p in edited['patients']
                              if p['patient_id'] == 'T04'), 'INCOMPARABLE')
        self.assertNotEqual(edited['query'], original['query'])
        self.assertEqual(edited['source'], original['source'])
        self.assertEqual(edited['admitted_source_sha256'], original['admitted_source_sha256'])
        self.assertEqual(edited['eligibility'], original['eligibility'])
        for report in (original, edited):
            with urlopen(self.url + '/api/journey/export/' + report['report_id']) as response:
                self.assertIn('attachment', response.headers['Content-Disposition'])
                exported = json.load(response)
            self.assertEqual(exported, report)
            self.assertTrue(verify(exported)['verified'])
            restored = self.request('/api/journey/decompile', {'query': exported['query']})
            self.assertEqual(restored, {'pattern': exported['pattern'], 'query': exported['query']})
            self.assertEqual(self.request('/api/journey/compile', {'pattern': restored['pattern']}), restored)

    def test_question_and_preview_changes_preserve_source_and_temporal_answer(self):
        custom = self.request('/api/journey/run', self.controls())
        larger_preview = self.request('/api/journey/run', self.controls(top_k=3))
        self.assertEqual(custom['result'], larger_preview['result'])
        self.assertEqual(custom['source'], larger_preview['source'])
        self.assertNotEqual(custom['ranking']['displayed_patient_ids'], larger_preview['ranking']['displayed_patient_ids'])
        for question in ('overlap', 'sequential'):
            controls = self.controls(question=question)
            del controls['pattern']
            preset = self.request('/api/journey/run', controls)
            self.assertEqual(preset['source'], custom['source'])
            self.assertEqual(preset['admitted_source_sha256'], custom['admitted_source_sha256'])
            self.assertEqual(preset['baseline_source'], custom['baseline_source'])
            self.assertEqual(preset['eligibility'], custom['eligibility'])

    def test_two_slot_metric_form_survives_http_query_roundtrip(self):
        pattern = three_event_pattern()
        pattern['slots'].pop()
        pattern['constraints'] = [pattern['constraints'][0],
            {'id': 'duration', 'operator': 'duration', 'slot': 'infusion',
             'minimum_minutes': '10', 'maximum_minutes': '10'},
            {'id': 'shared', 'operator': 'minimum_overlap', 'left': 'infusion',
             'right': 'collection', 'minimum_minutes': '2.5'}]
        compiled = self.request('/api/journey/compile', {'pattern': pattern})
        self.assertEqual(compiled['query']['profile'], 'extended-interval-query-1.0')
        self.assertEqual(compiled['query']['constraints'][2]['minimum_us'], 150000000)
        self.assertEqual(self.request('/api/journey/decompile', {'query': compiled['query']}), compiled)
        report = self.request('/api/journey/run', self.controls(pattern=compiled['pattern']))
        self.assertEqual(report['query'], compiled['query'])
        self.assertTrue(verify(report)['verified'])

    def test_invalid_forms_queries_and_custom_controls_are_http_400(self):
        pattern = three_event_pattern()
        compiled = self.request('/api/journey/compile', {'pattern': pattern})
        invalid_patterns = []
        for mutate in (
            lambda p: p['slots'].append({'id': 'fourth', 'event_kind': 'infusion'}),
            lambda p: p['slots'][1].update(id='infusion'),
            lambda p: p['slots'][0].update(event_kind='unknown'),
            lambda p: p['constraints'][0].update(right='absent'),
            lambda p: p['constraints'][1].update(maximum_minutes=30),
            lambda p: p['constraints'][1].update(minimum_minutes='31'),
            lambda p: p['constraints'][1].update(maximum_minutes='NaN'),
            lambda p: p.update(constraints=[]),
        ):
            invalid = deepcopy(pattern)
            mutate(invalid)
            invalid_patterns.append(invalid)
        invalid_queries = []
        for mutate in (
            lambda q: q.update(profile='unrecognized-profile'),
            lambda q: q['slots'][0].update(class_iri='urn:unadmitted:event'),
            lambda q: q['constraints'][0].update(right='absent'),
            lambda q: q['constraints'][1].update(max_gap_us=True),
            lambda q: q.update(extra='unrecognized'),
        ):
            invalid = deepcopy(compiled['query'])
            mutate(invalid)
            invalid_queries.append(invalid)
        invalid_requests = [self.controls(budget='1.25'), self.controls(budget=0),
                            self.controls(top_k=True), self.controls(reference_patient_id='P00'),
                            self.controls(source={}), self.controls(query=compiled['query'])]
        missing = self.controls()
        del missing['pattern']
        invalid_requests.append(missing)
        requests = ([('/api/journey/compile', {'pattern': p}) for p in invalid_patterns]
                    + [('/api/journey/decompile', {'query': q}) for q in invalid_queries]
                    + [('/api/journey/run', body) for body in invalid_requests]
                    + [('/api/journey/compile', b'{"pattern":{},"pattern":{}}'),
                       ('/api/journey/decompile', []), ('/api/journey/compile', {})])
        for path, body in requests:
            with self.subTest(path=path, body=body), self.assertRaises(HTTPError) as caught:
                self.request(path, body)
            self.assertEqual(caught.exception.code, 400)
        for path, body in (('/api/journey/compile', {'pattern': pattern}),
                           ('/api/journey/decompile', {'query': compiled['query']})):
            with self.subTest(path=path), self.assertRaises(HTTPError) as caught:
                self.request(path, body, {'Origin': 'https://other.example'})
            self.assertEqual(caught.exception.code, 403)


if __name__ == '__main__':
    unittest.main()
