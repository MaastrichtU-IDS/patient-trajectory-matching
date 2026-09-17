"""HTTP acceptance for explicitly declared custom metric relaxation options."""
from copy import deepcopy
import json
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server
from app.temporal_replay import verify


def catalogue_example():
    pattern = {
        'slots': [{'id': 'infusion', 'event_kind': 'infusion'},
                  {'id': 'collection', 'event_kind': 'specimen_collection'}],
        'constraints': [
            {'id': 'contains', 'operator': 'contains', 'left': 'infusion', 'right': 'collection'},
            {'id': 'duration', 'operator': 'duration', 'slot': 'infusion',
             'minimum_minutes': '9', 'maximum_minutes': '9'},
            {'id': 'shared', 'operator': 'minimum_overlap', 'left': 'infusion',
             'right': 'collection', 'minimum_minutes': '3'}],
    }
    duration = {'target': 'duration', 'minimum_minutes': '9', 'maximum_minutes': '10'}
    shared = {'target': 'shared', 'minimum_minutes': '2'}
    catalogue = {
        'relaxable_targets': ['duration', 'shared'], 'max_changed_targets': 2,
        'options': [
            {'id': 'duration_only', 'cost': '0.5', 'changes': [deepcopy(duration)]},
            {'id': 'overlap_only', 'cost': '0.5', 'changes': [deepcopy(shared)]},
            {'id': 'combined', 'cost': '1.25', 'changes': [duration, shared]}],
    }
    return pattern, catalogue


class RelaxationCatalogueHTTPTests(unittest.TestCase):
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
        pattern, catalogue = catalogue_example()
        return {'reference_patient_id': 'T03', 'top_k': 1, 'maximum_baseline': None,
                'question': 'custom', 'budget': '1.25', 'pattern': pattern,
                'catalogue': catalogue, **changes}

    def test_explicit_options_budget_full_pool_evidence_and_retained_replay(self):
        controls = self.controls()
        compiled = self.request('/api/journey/catalogue', {
            key: controls[key] for key in ('pattern', 'catalogue', 'budget')})
        affordable = self.request('/api/journey/run', self.controls(budget='1'))
        complete = self.request('/api/journey/run', controls)
        self.assertEqual(complete['query'], compiled['query'])
        self.assertEqual(complete['policy'], compiled['policy'])
        self.assertEqual(complete['request']['catalogue'], controls['catalogue'])
        self.assertEqual(complete['eligibility']['eligible_patient_ids'], ['T01', 'T02', 'T04'])
        self.assertEqual({p['patient_id'] for p in complete['patients']}, {'T01', 'T02', 'T04'})
        self.assertNotIn('T01', complete['ranking']['displayed_patient_ids'])
        self.assertEqual(len(complete['ranking']['displayed_patient_ids']), 1)
        self.assertEqual(affordable['relaxation']['robust_patient_ids'], [])
        self.assertEqual(complete['relaxation']['robust_patient_ids'], ['T01'])
        self.assertEqual(complete['added_robust_patient_ids'], ['T01'])
        self.assertEqual(affordable['result'], complete['result'])
        self.assertEqual(affordable['source'], complete['source'])
        self.assertEqual(len(affordable['relaxation']['evaluations']), 3)
        self.assertEqual(len(complete['relaxation']['evaluations']), 4)
        for report in (affordable, complete):
            t01 = next(p for p in report['patients'] if p['patient_id'] == 'T01')
            self.assertEqual(t01['original_status'], 'NO_RECORDED_MATCH')
            options = {o['option_id']: o for o in t01['option_results']}
            self.assertEqual(set(options), {'duration_only', 'overlap_only', 'combined'})
            self.assertEqual(options['duration_only']['status'], 'POSSIBLE')
            self.assertEqual(options['overlap_only']['status'], 'NO_RECORDED_MATCH')
            self.assertFalse(options['duration_only']['selected'])
            self.assertFalse(options['overlap_only']['selected'])
            excluded = report is affordable
            self.assertEqual(options['combined']['excluded_by_budget'], excluded)
            self.assertEqual(options['combined']['selected'], not excluded)
            self.assertEqual(options['combined']['status'], None if excluded else 'CERTAIN')
            self.assertEqual(options['combined']['cost'], '1.25')
            self.assertEqual(t01['selected_option'], None if excluded else 'combined')
            self.assertEqual(t01['selected_cost'], None if excluded else '1.25')
            self.assertTrue(t01['evidence']['records'])
            self.assertTrue(t01['explanation']['original']['summary'])
            t04 = next(p for p in report['patients'] if p['patient_id'] == 'T04')
            self.assertEqual(t04['original_status'], 'NO_RECORDED_MATCH')
            t04_options = {o['option_id']: o['status'] for o in t04['option_results']}
            self.assertEqual(t04_options, {'duration_only': 'INCOMPARABLE',
                                          'overlap_only': 'NO_RECORDED_MATCH',
                                          'combined': None if excluded else 'INCOMPARABLE'})
            with urlopen(self.url + '/api/journey/export/' + report['report_id']) as response:
                self.assertIn('attachment', response.headers['Content-Disposition'])
                exported = json.load(response)
            self.assertEqual(exported, report)
            self.assertTrue(verify(exported)['verified'])
            restored = self.request('/api/journey/decompile', {'query': exported['query']})
            self.assertEqual(self.request('/api/journey/compile', {'pattern': restored['pattern']}), restored)
        # Neither cheaper option is robust, even when both fit together in the budget.
        # The combined change is evaluated only because it has its own declared option.
        undeclared = deepcopy(controls)
        undeclared['catalogue']['options'].pop()
        self.assertEqual(self.request('/api/journey/run', undeclared)['relaxation']['robust_patient_ids'], [])

    def test_no_catalogue_old_custom_and_presets_remain_valid(self):
        original_controls = self.controls(budget='0')
        del original_controls['catalogue']
        original = self.request('/api/journey/run', original_controls)
        declared = self.request('/api/journey/run', self.controls(budget='0'))
        self.assertEqual(original['result'], declared['result'])
        self.assertEqual(original['source'], declared['source'])
        self.assertEqual(original['policy']['options'], [])
        self.assertEqual(len(original['relaxation']['evaluations']), 1)
        self.assertEqual(len(declared['relaxation']['evaluations']), 1)
        for patient in declared['patients']:
            self.assertEqual(len(patient['option_results']), 3)
            self.assertTrue(all(o['excluded_by_budget'] and o['status'] is None
                                for o in patient['option_results']))
        for question in ('overlap', 'sequential'):
            preset = {k: v for k, v in original_controls.items() if k != 'pattern'}
            preset.update(question=question, budget='1.25')
            report = self.request('/api/journey/run', preset)
            self.assertEqual(report['source'], original['source'])
            self.assertEqual(report['admitted_source_sha256'], original['admitted_source_sha256'])
            self.assertEqual(report['baseline_source'], original['baseline_source'])

    def test_invalid_catalogues_fail_before_zero_budget_exclusion(self):
        mutations = [
            lambda c: c.update(relaxable_targets=['contains', 'duration', 'shared']),
            lambda c: c.update(relaxable_targets=['duration']),
            lambda c: c.update(max_changed_targets=True),
            lambda c: c.update(max_changed_targets=7),
            lambda c: c['options'][0]['changes'][0].update(target='unknown'),
            lambda c: c['options'][0]['changes'][0].update(maximum_minutes='9'),
            lambda c: c['options'][0]['changes'][0].update(minimum_minutes='9.5'),
            lambda c: c['options'][1]['changes'][0].update(minimum_minutes='0'),
            lambda c: c['options'][1]['changes'][0].update(minimum_minutes='4'),
            lambda c: c['options'][1]['changes'][0].update(maximum_minutes='3'),
            lambda c: c['options'][0].update(cost=0.5),
            lambda c: c['options'][0].update(cost='NaN'),
            lambda c: c['options'][0].update(cost='0.0000001'),
            lambda c: c['options'][0].update(cost='100.000001'),
            lambda c: c['options'][0].update(id='original'),
            lambda c: c['options'][1].update(id='duration_only'),
            lambda c: c['options'].append(deepcopy(c['options'][0])),
            lambda c: c['options'][0]['changes'].append(deepcopy(c['options'][0]['changes'][0])),
            lambda c: c.update(source={}),
            lambda c: c.update(path='/tmp/unadmitted.json'),
        ]
        for mutate in mutations:
            controls = self.controls(budget='0')
            mutate(controls['catalogue'])
            preview = {k: controls[k] for k in ('pattern', 'catalogue', 'budget')}
            for path, body in (('/api/journey/catalogue', preview), ('/api/journey/run', controls)):
                with self.subTest(path=path, catalogue=controls['catalogue']), self.assertRaises(HTTPError) as caught:
                    self.request(path, body)
                self.assertEqual(caught.exception.code, 400)

    def test_changed_target_cap_and_empty_catalogue_allow_original_only(self):
        controls = self.controls()
        controls['catalogue']['max_changed_targets'] = 0
        capped = self.request('/api/journey/run', controls)
        self.assertEqual(len(capped['relaxation']['evaluations']), 1)
        self.assertEqual(capped['relaxation']['robust_patient_ids'], [])
        self.assertTrue(all(o['excluded_by_budget'] and o['status'] is None
                            for p in capped['patients'] for o in p['option_results']))
        controls['catalogue'] = {'relaxable_targets': [], 'max_changed_targets': 0, 'options': []}
        empty = self.request('/api/journey/run', controls)
        self.assertEqual(empty['result'], capped['result'])
        self.assertEqual(empty['policy']['options'], [])
        self.assertEqual(len(empty['relaxation']['evaluations']), 1)

    def test_catalogue_http_request_boundaries(self):
        controls = self.controls()
        preview = {k: controls[k] for k in ('pattern', 'catalogue', 'budget')}
        invalid = [{}, [], {**preview, 'source': {}}, {**preview, 'budget': 0},
                   {**preview, 'budget': '-1'}, {**preview, 'budget': '101'},
                   b'{"pattern":{},"pattern":{}}']
        for body in invalid:
            with self.subTest(body=body), self.assertRaises(HTTPError) as caught:
                self.request('/api/journey/catalogue', body)
            self.assertEqual(caught.exception.code, 400)
        for headers, code in (({'Origin': 'https://other.example'}, 403),
                              ({'Content-Type': 'text/plain'}, 415)):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/journey/catalogue', preview, headers)
            self.assertEqual(caught.exception.code, code)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/journey/catalogue', b' ' * (server.MAX_BODY + 1))
        self.assertEqual(caught.exception.code, 413)
        preset = self.controls(question='overlap')
        del preset['pattern']
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/journey/run', preset)
        self.assertEqual(caught.exception.code, 400)


if __name__ == '__main__':
    unittest.main()
