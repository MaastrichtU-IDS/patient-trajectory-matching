"""Cache safety and full-result equivalence using the actual reviewed synthetic engine."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pressure
from pressure_cache import ResultCache
from patterns import reviewed_pressure_session as engine

DEFAULT = {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}


class PressureCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'source'
        shutil.copytree(engine.p.ei.ROOT / 'examples/source-mixed-query', self.folder)
        self.service = pressure.PressureService(synthetic=True)
        self.service.folder = self.folder
        self.addCleanup(self.service.close)

    def run_query(self, options=None):
        s = self.service
        jid = s.start({'stratum': 'synthetic', 'controls': options or DEFAULT})['id']
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            job = s.get(jid)
            if job['status'] != 'RUNNING':
                return job
            time.sleep(.01)
        self.fail('Query did not finish')

    def assert_failed(self, job, reason):
        self.assertEqual(job['status'], 'FAILED')
        self.assertIn(reason, job['error'])
        self.assertNotIn('summary', job)
        self.assertEqual(len(self.service.result_cache.entries), 0)

    def test_repeat_preserves_every_result_and_inspector_field_without_rerunning_matcher(self):
        first = self.run_query()
        s = self.service
        self.assertEqual(first['status'], 'COMPLETED')
        original = deepcopy(s.jobs[first['id']]['_result'])
        evidence = {t: s.inspect(first['id'], t) for t in original['details']}
        with (patch.object(engine.p.mixed, 'execute', side_effect=AssertionError('No fresh graph query')),
              patch.object(engine.p.reference, 'execute', side_effect=AssertionError('No fresh SQL query'))):
            second = self.run_query()
        self.assertEqual(second['status'], 'COMPLETED')
        self.assertEqual(second['execution']['mode'], 'cached_complete_result')
        self.assertEqual(second['execution']['origin_job_id'], first['id'])
        self.assertEqual(second['timing']['execute_seconds'], 0)
        self.assertEqual(original, s.jobs[second['id']]['_result'])
        self.assertEqual(evidence, {t: s.inspect(second['id'], t) for t in original['details']})
        # Public replies are copies, including nested source decisions and witnesses.
        second['summary']['metrics']['patients'] = 999
        next(iter(evidence.values()))['treatment']['decisions'].clear()
        third = self.run_query()
        self.assertEqual(third['summary']['metrics']['patients'], 3)
        self.assertTrue(s.inspect(third['id'], next(iter(original['details'])))['treatment']['decisions'])

    def test_each_control_and_literal_context_change_is_a_fresh_query(self):
        first = self.run_query()
        for change in [{'threshold': '59'}, {'baseline_minutes': 10}, {'followup_minutes': 0}, {'threshold': '65.0'}]:
            with self.subTest(change=change):
                job = self.run_query({**DEFAULT, **change})
                self.assertEqual(job['status'], 'COMPLETED')
                self.assertEqual(job['execution']['mode'], 'fresh_query')
                self.assertNotEqual(job['summary']['context_id'], first['summary']['context_id'])
                repeated = self.run_query({**DEFAULT, **change})
                self.assertEqual(repeated['execution']['mode'], 'cached_complete_result')
                self.assertEqual(job['summary'], repeated['summary'])
        self.assertEqual(len(self.service.result_cache.entries), 3)
        self.assertEqual(self.run_query()['execution']['mode'], 'fresh_query')

    def test_changed_source_refuses_hit_and_retains_previous_completed_job(self):
        first = self.run_query()
        path = self.folder / 'chartevents.csv'
        path.write_bytes(path.read_bytes() + b'\n')
        self.assert_failed(self.run_query(), 'SOURCE_CHANGED')
        self.assertEqual(self.service.get(first['id'])['summary'], first['summary'])

    def test_changed_review_or_parent_request_refuses_hit(self):
        for field in ['review', 'request']:
            self.run_query()
            request, declaration = self.service._inputs('synthetic')
            if field == 'review':
                declaration['reviewer'] = 'changed'
            else:
                request['query']['id'] = 'changed'
            with patch.object(self.service, '_inputs', return_value=(request, declaration)):
                self.assert_failed(self.run_query(), 'REVIEW_CHANGED')

    def test_cache_implementation_change_requires_restart(self):
        self.run_query()
        with patch.object(pressure, 'implementation_stamp', return_value={'changed': 'hash'}):
            self.assert_failed(self.run_query(), 'IMPLEMENTATION_CHANGED')

    def test_engine_artifact_change_refuses_hit(self):
        self.run_query()
        original = Path.read_text
        target = engine.p.ei.ROOT / 'patterns/mixed_record_query.py'
        def changed(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            return value + '\n' if path == target else value
        with patch.object(Path, 'read_text', changed):
            self.assert_failed(self.run_query(), 'IMPLEMENTATION_CHANGED')

    def test_source_review_and_implementation_changes_during_hit_are_refused(self):
        for kind in ['source', 'review', 'implementation']:
            self.run_query()
            cache = self.service.result_cache
            get = cache.get
            path = self.folder / 'chartevents.csv'
            before = path.read_bytes()
            request, declaration = self.service._inputs('synthetic')
            changed_declaration = deepcopy(declaration)
            changed_declaration['reviewer'] = 'changed during hit'
            def interrupted(key):
                entry = get(key)
                self.assertIsNotNone(entry)
                if kind == 'source':
                    path.write_bytes(before + b'\n')
                elif kind == 'review':
                    self.service._inputs = lambda name: (request, changed_declaration)
                else:
                    self.service.implementation = {'changed': 'hash'}
                return entry
            try:
                with patch.object(cache, 'get', side_effect=interrupted):
                    self.assert_failed(self.run_query(), {'source': 'SOURCE_CHANGED', 'review': 'REVIEW_CHANGED', 'implementation': 'IMPLEMENTATION_CHANGED'}[kind])
            finally:
                path.write_bytes(before)
                self.service.__dict__.pop('_inputs', None)
                self.service.implementation = pressure.implementation_stamp()

    def test_blocked_or_failed_execution_is_never_reused(self):
        self.run_query()
        with patch.object(engine.p.mixed, 'execute', side_effect=ValueError('backend blocked')):
            blocked = self.run_query({**DEFAULT, 'threshold': '59'})
        self.assertEqual(blocked['status'], 'BLOCKED')
        self.assertNotIn('summary', blocked)
        self.assertEqual(len(self.service.result_cache.entries), 0)
        self.assertEqual(self.run_query()['execution']['mode'], 'fresh_query')
        with patch.object(self.service.session, 'execute', side_effect=RuntimeError('backend failed')):
            self.assert_failed(self.run_query({**DEFAULT, 'threshold': '59'}), 'backend failed')

    def test_switching_preparation_drops_result_entries(self):
        self.run_query()
        self.assertEqual(len(self.service.result_cache.entries), 1)
        # An alternate preparation name exercises the switch without public source data.
        self.service._prepare('alternate-synthetic', lambda **kw: None)
        self.assertEqual(len(self.service.result_cache.entries), 0)
        self.assertEqual(self.run_query()['execution']['mode'], 'fresh_query')

    def test_committed_benchmarks_bind_current_implementation_and_aggregate_evidence(self):
        from benchmark_pressure_cache import artifacts
        for name, patients, anchors, stays in [('synthetic', 3, 3, 5), ('arterial', 13, 944, 140)]:
            with self.subTest(source=name):
                report = json.loads((engine.p.ei.ROOT / f'verification/pressure-cache-{name}-report.json').read_text())
                self.assertEqual(report['status'], 'VERIFIED')
                self.assertEqual(report['artifacts'], artifacts())
                self.assertEqual(report['metrics']['patients'], patients)
                self.assertEqual(report['anchors_verified'], anchors)
                self.assertEqual(report['stays_retained'], stays)
                self.assertEqual([r['mode'] for r in report['runs']],
                                 ['fresh_query'] * 2 + ['cached_complete_result'] * 3)
                for run in report['runs']:
                    self.assertTrue(run['full_result_equal'])
                    self.assertTrue(run['all_inspections_equal'])
                    self.assertTrue(all(v >= 0 for v in run['timing'].values()))
                    if run['mode'] == 'cached_complete_result':
                        self.assertEqual(run['timing']['execute_seconds'], 0)
                self.assertFalse(report['patient_rows_or_identifiers_included'])
                self.assertFalse(report['clinical_mapping_verified'])
                self.assertNotIn('bindings', report)
                self.assertNotIn('anchors', report)
                self.assertNotIn('roster', report)
                if name == 'arterial':
                    pin = json.loads((engine.p.ei.ROOT / 'data/clinical-source-demo-pin.json').read_text())
                    self.assertEqual(report['source_files'], {t: f['file_sha256'] for t, f in pin['files'].items()})
                    prior = json.loads((engine.p.ei.ROOT / 'verification/live-pressure-demo-report.json').read_text())
                    self.assertEqual(report['session_context_id'], prior['session_context_id'])
                    self.assertEqual(report['query_context_id'], prior['query_context_id'])
                    self.assertEqual(report['metrics'], prior['metrics'])
                    self.assertEqual(report['runs'][0]['http_inspector_cases'],
                                     ['eligible', 'missing_followup', 'no_eligible_pair'])

    def test_lru_serialized_size_budget_and_snapshot_isolation(self):
        cache = ResultCache(max_entries=2, max_bytes=2000)
        result = {'status': 'COMPLETED', 'details': {'a': [1]}}
        for key in ['a', 'b']:
            self.assertTrue(cache.put(key, result, key))
        result['details']['a'].append(2)
        cached = cache.get('a')
        self.assertEqual(cached['result']['details']['a'], [1])
        cached['result']['details']['a'].append(3)
        self.assertTrue(cache.put('c', result, 'c'))
        self.assertIsNone(cache.get('b'))
        self.assertEqual(cache.get('a')['result']['details']['a'], [1])
        self.assertFalse(cache.put('large', {'status': 'COMPLETED', 'value': 'x' * 3000}, 'large'))
        self.assertFalse(cache.put('blocked', {'status': 'BLOCKED'}, 'blocked'))
        self.assertLessEqual(cache.bytes, 2000)
        # Force eviction by bytes even while below the entry limit.
        small = ResultCache(max_entries=10, max_bytes=100)
        self.assertTrue(small.put('a', {'status': 'COMPLETED'}, 'a'))
        self.assertTrue(small.put('b', {'status': 'COMPLETED'}, 'b'))
        self.assertIsNone(small.get('a'))
        cache.clear()
        self.assertEqual(cache.bytes, 0)


if __name__ == '__main__':
    unittest.main()
