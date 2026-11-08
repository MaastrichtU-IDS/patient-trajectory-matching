"""Smoke test of the execution-store scaling benchmark: the report's structure, not its numbers.

Needs DuckDB (`patterns/requirements-store.lock.txt`); skipped without it.
"""
import json
from pathlib import Path
import tempfile
import unittest

try:
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover - the store extra is optional
    duckdb = None

from tools import benchmark_execution_store as bench


class PureHelperTests(unittest.TestCase):
    def test_memory_limits_are_read_as_duckdb_reads_them(self):
        self.assertEqual(bench.limit_bytes('4GB'), 4e9)
        self.assertEqual(bench.limit_bytes('256 MiB'), 256 * 2**20)

    def test_percentiles_are_nearest_rank(self):
        self.assertEqual(bench.summary([5, 1, 3, 2, 4]), {'p50': 3, 'p95': 5, 'max': 5})
        self.assertEqual(bench.summary([]), {'p50': None, 'p95': None, 'max': None})

    def test_patient_references_and_shifts_are_whole_days(self):
        self.assertEqual(bench.patient_ref('0001-C002~r7'), ('0001-C002', 7))
        self.assertEqual(bench.patient_ref('0001-C002'), ('0001-C002', 0))
        for k in range(1, 50):
            self.assertEqual(bench.shift_for(k) % bench.DAY, 0)
            self.assertGreater(bench.shift_for(k), 0)


class CommittedReportTests(unittest.TestCase):
    def test_the_committed_report_builds_and_verifies_a_million_patients_with_flat_memory(self):
        """D23: the regenerated report holds a completed 1,000,000-patient size, verified by content, whose
        build peak is no higher than a few percent above the 300,000-patient one."""
        report = json.loads((bench.ROOT / 'verification/execution-store-scaling-report.json').read_text())
        sizes = {s['size']: s for s in report['sizes']}
        big, mid = sizes[1_000_000], sizes[300_000]
        self.assertEqual(big['status'], 'completed')
        self.assertEqual(big['tables']['patients'], 1_000_000)
        self.assertTrue(big['profile']['verify_stages']['content_agrees'])
        self.assertEqual(big['memory_limit'], '4GB')
        stages = {s['stage'] for s in big['build_stages']['stages']}
        self.assertTrue({f'derived:bucket={b}' for b in range(8)} <= stages)
        self.assertLess(big['build_phase']['peak_rss_bytes'], 1.05 * mid['build_phase']['peak_rss_bytes'])


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class ReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='scaling-test-')
        cls.output = Path(cls.tmp.name) / 'report.json'
        bench.main(['--sizes', '100,200', '--queries', '3', '--repeats', '2', '--exhaustive', '1',
                    '--exhaustive-max-size', '100', '--profile-max-size', '100', '--large-queries', '2',
                    '--large-repeats', '2', '--work-dir', str(Path(cls.tmp.name) / 'work'), '--output', str(cls.output)])
        cls.report = json.loads(cls.output.read_text())

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_each_size_records_build_and_store_measurements(self):
        self.assertEqual([s['size'] for s in self.report['sizes']], [100, 200])
        for entry in self.report['sizes']:
            self.assertEqual(entry['status'], 'completed')
            self.assertEqual(entry['patients'], entry['size'])
            self.assertEqual(entry['tables']['patients'], entry['size'])
            self.assertGreater(entry['tables']['events'], 0)
            for key in ('generate_seconds', 'convert_seconds', 'build_seconds', 'snapshot_bytes'):
                self.assertGreater(entry[key], 0, key)
            self.assertGreater(entry['build_phase']['peak_rss_bytes'], 0)
            self.assertGreater(entry['query_phase']['peak_rss_bytes'], 0)
            self.assertGreater(entry['open_verify_seconds']['cold'], 0)

    def test_queries_record_pruning_latency_and_status_mix(self):
        for entry, n in zip(self.report['sizes'], (3, 2)):
            q = entry['queries']
            self.assertEqual((q['queries'], q['repeats']), (n, 2))
            self.assertEqual(len(q['per_query']), n)
            for name in ('candidates_seconds', 'match_seconds'):
                self.assertTrue(0 < q[name]['p50'] <= q[name]['p95'] <= q[name]['max'], name)
            self.assertTrue(0 <= q['pruning_rate']['p50'] <= 1)
            for row in q['per_query']:
                self.assertLessEqual(row['candidates'], entry['patients'])
            self.assertEqual(sum(q['status_mix'].values()), n * entry['patients'])

    def test_the_small_size_agrees_with_exhaustive_matching(self):
        check = self.report['sizes'][0]['exhaustive_check']
        self.assertEqual((check['queries'], check['mismatches']), (1, 0))
        self.assertNotIn('exhaustive_check', self.report['sizes'][1])

    def test_every_size_profiles_the_streamed_build_and_both_verification_levels(self):
        for entry in self.report['sizes']:
            stages = {s['stage']: s for s in entry['build_stages']['stages']}
            for name in ('pool_on_disk', 'added', 'staged', 'patients_loaded_and_input_digest', 'derived',
                         'content_digest:events', 'parquet_written_and_hashed', 'content_verified', 'published'):
                self.assertGreater(stages[name]['peak_rss_bytes'], 0, name)
            peaks = [s['peak_rss_bytes'] for s in entry['build_stages']['stages']]
            self.assertEqual(peaks, sorted(peaks), 'peak memory only grows')
            self.assertEqual(stages['added']['patients'], entry['size'])
            self.assertEqual(stages['added']['events'], entry['tables']['events'])
            # The derive runs one bucket at a time (D23) and reports each bucket's statements and memory.
            buckets = [stages[f'derived:bucket={b}'] for b in range(8)]
            self.assertEqual(sum(b['parts'] for b in buckets), stages['staged']['parts'])
            for b in buckets:
                self.assertIn('concept_postings', b['statement_seconds'])
                self.assertGreater(b['duckdb_memory_max_bytes'], 0)
                self.assertGreater(b['rss_bytes'], 0)
            self.assertGreater(entry['build_stages']['process_peak_rss_over_memory_limit'], 0)
            self.assertEqual(entry['memory_limit'], bench.DEFAULT_MEMORY_LIMIT)
            verify = entry['profile']['verify_stages']
            self.assertTrue(verify['content_agrees'])
            self.assertIn('verify:content:events', {s['stage'] for s in verify['stages']})
            self.assertGreater(verify['content_rows_per_second'], 0)
            self.assertEqual(entry['open_verify_seconds']['level'], 'files')

    def test_the_small_size_profiles_one_match(self):
        entry, other = self.report['sizes']
        parts = entry['profile']['match_cprofile']
        self.assertGreater(parts['match_seconds_profiled_total'], 0)
        self.assertIn('engine_trajectory_graph_match', parts['parts_seconds'])
        self.assertGreater(entry['events_per_distinct_patient']['mean'], 0)
        self.assertNotIn('match_cprofile', other['profile'])
        self.assertEqual(other['queries']['queries'], 2)  # the larger size runs --large-queries

    def test_report_binds_artifacts_states_limits_and_holds_no_patients(self):
        self.assertEqual(sorted(self.report['artifacts']), sorted(bench.ARTIFACTS))
        for path, value in self.report['artifacts'].items():
            self.assertEqual(value, bench.digest(path))
        self.assertTrue(self.report['limitations'])
        self.assertFalse(self.report['contains_patient_level_data'])
        for key in ('platform', 'cpu', 'python', 'duckdb', 'duckdb_threads', 'duckdb_memory_limit'):
            self.assertIn(key, self.report['environment'])
        self.assertNotIn('0000-C001', self.output.read_text())

    def test_the_pool_streams_from_disk_with_the_same_patients_as_in_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'pool.ndjson'
            header, _, _, _, _, _, events = bench.write_pool(bench.PER_COHORT, path)
            ingest = bench.pool_ingest(bench.PER_COHORT)[0]
            self.assertEqual(header['closure'], ingest['closure'])
            size = 2 * bench.PER_COHORT + 5
            streamed = list(bench.stream_patients(path, len(events), size))
            self.assertEqual(streamed, json.loads(json.dumps(bench.replicate(ingest, size)['patients'])))

    def test_replication_adds_shifted_copies_with_unique_ids(self):
        ingest = bench.pool_ingest(bench.PER_COHORT)[0]
        bigger = bench.replicate(ingest, 2 * bench.PER_COHORT + 5)
        ids = [p['patient_id'] for p in bigger['patients']]
        self.assertEqual(len(ids), len(set(ids)), 'ids are unique')
        self.assertEqual(len(ids), 2 * bench.PER_COHORT + 5)
        source, copy = bigger['patients'][0], bigger['patients'][bench.PER_COHORT]
        self.assertEqual(copy['patient_id'], f"{source['patient_id']}~r1")
        delta = bench.shift_for(1)
        for a, b in zip(source['events'], copy['events']):
            self.assertEqual(b['start'], [t + delta for t in a['start']])
            self.assertEqual(b['concepts'], a['concepts'])


if __name__ == '__main__':
    unittest.main()
