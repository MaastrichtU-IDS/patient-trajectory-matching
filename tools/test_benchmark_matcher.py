"""Smoke test of the matcher comparison: the report's structure and the byte-identity check, not its numbers.

Needs DuckDB (`patterns/requirements-store.lock.txt`) and git; skipped without them. Arm A here is the
committed tree (`HEAD`), so on a clean checkout both arms are the same code and must agree.
"""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

try:
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover - the store extra is optional
    duckdb = None

from tools import benchmark_execution_store as bench
from tools import benchmark_matcher as matcher


class PureHelperTests(unittest.TestCase):
    def test_percentiles_and_the_identity_comparison(self):
        self.assertEqual(matcher.summary([5, 1, 3, 2, 4]), {'p50': 3, 'p95': 5, 'max': 5})
        a = [{'seed': 0, 'digest': 'x'}, {'seed': 1, 'digest': 'y'}]
        b = [{'seed': 0, 'digest': 'x'}, {'seed': 1, 'digest': 'z'}]
        self.assertEqual(matcher.identity(a, b), {'compared': 2, 'identical': 1, 'mismatching_seeds': [1]})

    def test_the_result_digest_depends_on_every_decided_field_and_nothing_else(self):
        base = {'plan': [], 'candidates': 1, 'counts': {'none': 1}, 'context': 'ignored',
                'results': [{'patient_id': 'P', 'status': 'none'}]}
        same = {**base, 'context': 'other'}
        changed = {**base, 'results': [{'patient_id': 'P', 'status': 'exact'}]}
        self.assertEqual(matcher.result_digest(base), matcher.result_digest(same))
        self.assertNotEqual(matcher.result_digest(base), matcher.result_digest(changed))

    def test_the_target_check_reads_the_arm_asked_for(self):
        entry = {'size': 300_000, 'arms': {'B': {'match_seconds': {'p50': 1, 'p95': 9.0, 'max': 31.0},
                                                'query_process': {'peak_rss_bytes': 2**31}}}}
        got = matcher.target_check(entry)
        self.assertEqual((got['p95_met'], got['max_met'], got['memory_met']), (True, False, True))
        self.assertIsNone(matcher.target_check({'size': 1, 'arms': {}}))


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from patterns import execution_store as es
        cls.tmp = tempfile.TemporaryDirectory(prefix='matcher-test-')
        tmp = Path(cls.tmp.name)
        cls.snapshot = es.build(bench.replicate(bench.pool_ingest(96)[0], 96), tmp / 'store')
        cls.output = tmp / 'report.json'
        try:
            matcher.main(['--snapshots', f'96={cls.snapshot}', '--arm-a-ref', 'HEAD', '--queries', '3', '--repeats', '2',
                          '--workers', '1', '2', '--output', str(cls.output)])
        except (OSError, matcher.subprocess.CalledProcessError) as exc:  # no git, or not a checkout
            cls.tmp.cleanup()
            raise unittest.SkipTest(f'git archive unavailable: {exc}')
        cls.report = json.loads(cls.output.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_arm_reports_cost_latency_and_memory_and_later_arms_are_placeholders(self):
        entry = self.report['sizes'][0]
        for arm in ('A', 'B', 'B_decided'):
            got = entry['arms'][arm]
            self.assertEqual(got['queries'], 3)
            self.assertEqual(set(got['match_seconds']), {'p50', 'p95', 'max'})
            self.assertEqual(set(got['per_candidate_microseconds']), {'end_to_end', 'engine_only', 'event_loading'})
            self.assertGreater(got['query_process']['peak_rss_bytes'], 0)
        for arm in ("D", "D'"):
            self.assertEqual(entry['arms'][arm]['status'], 'not_run')
            self.assertEqual(self.report['arms'][arm]['status'], 'not_run')
        one, two = entry['arms']['C_w1'], entry['arms']['C_w2']
        self.assertEqual((one['workers'], two['workers']), (1, 2))
        self.assertGreater(two['worker_start_seconds_cold'], 0)
        self.assertGreater(two['largest_worker_peak_rss_bytes'], 0)
        self.assertNotIn('C_w4', entry['arms'])
        self.assertEqual(entry['arms']['B_decided']['detail'], 'decided')

    def test_the_arms_agree_byte_for_byte_and_the_report_binds_its_files(self):
        entry = self.report['sizes'][0]
        self.assertEqual(entry['byte_identity_B_against_A'], {'compared': 3, 'identical': 3, 'mismatching_seeds': []})
        self.assertEqual(entry['byte_identity_C_against_B'],
                         {w: {'compared': 3, 'identical': 3, 'mismatching_seeds': []} for w in ('C_w1', 'C_w2')})
        self.assertEqual(set(entry['target_check_arm_C']), {'C_w1', 'C_w2'})
        for path, sha in self.report['artifacts'].items():
            self.assertEqual(sha, matcher.digest(path), path)
        self.assertFalse(self.report['contains_patient_level_data'])
        self.assertTrue(self.report['limitations'])
        self.assertNotIn('snapshot_view', entry['arms']['A'])  # the same profile: the snapshot as it is


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class OlderArmTests(unittest.TestCase):
    """Arm A's code may read an older store profile than the snapshots' (2.0 against 2.1, D24): it opens the
    same files under a relabelled manifest and must decide byte for byte as this tree does."""
    REF = '0a18b9a'  # main before D24: execution store 2.0

    def test_an_older_arm_reads_the_snapshot_through_a_relabelled_view_and_agrees(self):
        from patterns import execution_store as es
        with tempfile.TemporaryDirectory(prefix='matcher-older-') as tmp:
            snapshot = es.build(bench.replicate(bench.pool_ingest(96)[0], 96), Path(tmp) / 'store')
            output = Path(tmp) / 'report.json'
            try:
                matcher.main(['--snapshots', f'96={snapshot}', '--arm-a-ref', self.REF, '--arms', 'A', 'B',
                              '--queries', '4', '--repeats', '1', '--workers', '1', '--output', str(output)])
            except (OSError, matcher.subprocess.CalledProcessError) as exc:  # no git, or a clone without the ref
                raise unittest.SkipTest(f'git archive {self.REF} unavailable: {exc}')
            entry = json.loads(output.read_text())['sizes'][0]
        self.assertEqual(entry['byte_identity_B_against_A'], {'compared': 4, 'identical': 4, 'mismatching_seeds': []})
        self.assertEqual(entry['arms']['A']['snapshot_view']['snapshot_profile'], es.PROFILE)
        self.assertEqual(entry['arms']['A']['snapshot_view']['read_as_profile'], 'execution-store-2.0')
        self.assertNotIn('snapshot_view', entry['arms']['B'])


if __name__ == '__main__':
    unittest.main()
