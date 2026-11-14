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
        build peak is at most 10% above the 300,000-patient one."""
        report = json.loads((bench.ROOT / 'verification/execution-store-scaling-report.json').read_text())
        sizes = {s['size']: s for s in report['sizes']}
        big, mid = sizes[1_000_000], sizes[300_000]
        self.assertEqual(big['status'], 'completed')
        self.assertEqual(big['tables']['patients'], 1_000_000)
        self.assertTrue(big['profile']['verify_stages']['content_agrees'])
        self.assertEqual(big['memory_limit'], '4GB')
        stages = {s['stage'] for s in big['build_stages']['stages']}
        self.assertTrue({f'derived:bucket={b}' for b in range(8)} <= stages)
        # The bound was 1.05x until D31's second attempt; the owner widened it to 1.10x. The build peak varies by at
        # least 4% between identical runs (8.61 and 8.28 GiB at 1,000,000 with the builder unchanged, against 8.02 GiB
        # at 300,000), so 5% failed on noise. The gate exists to catch memory growing with patient count (before D23
        # the 1,000,000 build did not finish under 4 GB), and 10% still catches that.
        self.assertLessEqual(big['build_phase']['peak_rss_bytes'], 1.10 * mid['build_phase']['peak_rss_bytes'])


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
            self.assertEqual((b['recorded_at'], b['available_at']), (a['recorded_at'] + delta, a['available_at'] + delta))


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class ReuseTests(unittest.TestCase):
    """D34: a run may query the snapshots an earlier run kept instead of building them, only when a small build of this
    tree records exactly what that run's builds recorded, its build parameters and environment equal this run's and each
    kept snapshot still holds its identifier; a reused report names the run that first built the snapshots (path,
    digest, commit, time, machine) through any chain of reuses."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix='scaling-reuse-')
        cls.work = Path(cls.tmp.name) / 'work'
        cls.first, cls.second, cls.third = (Path(cls.tmp.name) / f'{n}.json' for n in ('first', 'second', 'third'))
        common = ['--sizes', '100', '--queries', '3', '--repeats', '1', '--exhaustive', '1', '--exhaustive-max-size',
                  '100', '--profile-max-size', '100', '--work-dir', str(cls.work), '--keep']
        bench.main(common + ['--output', str(cls.first)])
        bench.main(common + ['--output', str(cls.second), '--reuse-builds', str(cls.first)])
        bench.main(common + ['--output', str(cls.third), '--reuse-builds', str(cls.second)])
        cls.old, cls.new, cls.chained = (json.loads(p.read_text()) for p in (cls.first, cls.second, cls.third))
        cls.parameters = cls.old['parameters']

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_build_runs_isolated_and_loads_no_matching_code(self):
        record = self.old['build_provenance']
        self.assertEqual(record['rule'], 'runtime_record_v1')
        self.assertIn('patterns/execution_store.py', record['modules'])
        self.assertIn('patterns/trajectory_times.py', record['modules'])
        self.assertNotIn('patterns/trajectory_graph.py', record['modules'])
        self.assertNotIn('tools/benchmark_execution_store.py', record['modules'])
        self.assertEqual(sorted(record['files']), sorted(bench.BUILD_FILES))
        self.assertEqual(record['root'], 'checkout')
        with self.assertRaises(SystemExit):  # the benchmark itself no longer runs a build worker
            bench.main(['--worker', 'build', '--worker-output', str(Path(self.tmp.name) / 'x.json')])

    def test_the_report_records_when_where_and_what(self):
        self.assertTrue(self.old['generated_at'])
        self.assertEqual(set(self.old['commit']), {'head', 'dirty'})
        self.assertNotIn('build_origin', self.old)
        self.assertTrue(self.old['build_covers'])
        from tools import check_report_provenance as gate
        self.assertEqual(gate.record_problems(self.old['build_provenance']), [])
        self.assertIn(self.old['build_provenance']['modules'], gate.artifact_maps(self.old, []))

    def test_a_reused_build_gives_the_same_tables_candidates_and_statuses(self):
        old, new = self.old['sizes'][0], self.new['sizes'][0]
        self.assertNotIn('build_reused', old)
        self.assertEqual(new['build_reused']['snapshot_id'], old['snapshot_id'])
        self.assertEqual(self.new['build_provenance'], self.old['build_provenance'])
        for key in ('snapshot_id', 'tables', 'build_seconds', 'build_stages', 'build_phase'):
            self.assertEqual(new[key], old[key], key)
        for key in ('candidate_count', 'status_mix', 'incomplete_none_by_note'):
            self.assertEqual(new['queries'][key], old['queries'][key], key)
        self.assertEqual(new['exhaustive_check']['mismatches'], 0)

    def test_the_origin_is_the_first_build_through_a_chain_of_reuses(self):
        import hashlib
        first = hashlib.sha256(self.first.read_bytes()).hexdigest()
        for report, reused_from in ((self.new, self.first), (self.chained, self.second)):
            origin = report['build_origin']
            self.assertEqual(origin['first_built']['sha256'], first)
            self.assertEqual(origin['first_built']['path'], str(self.first.resolve()))
            self.assertEqual(origin['first_built']['generated_at'], self.old['generated_at'])
            self.assertEqual(origin['first_built']['commit'], self.old['commit'])
            self.assertEqual(origin['first_built']['environment'], self.old['environment'])
            self.assertEqual(origin['reused_from']['sha256'], hashlib.sha256(reused_from.read_bytes()).hexdigest())
            self.assertEqual(origin['sizes'], [100])
            self.assertEqual(report['sizes'][0]['build_seconds'], self.old['sizes'][0]['build_seconds'])

    def test_a_build_field_changed_after_the_build_fails_the_run(self):
        from unittest.mock import patch
        real = bench._query_size

        def rewriting(size, args, scratch, entry, built, started, keep):
            out = real(size, args, scratch, entry, built, started, keep)
            out['build_seconds'] = 0.0
            return out
        with patch.object(bench, '_query_size', rewriting), self.assertRaises(bench.BuildFieldsChanged):
            bench.main(['--sizes', '100', '--queries', '1', '--repeats', '1', '--exhaustive', '0', '--profile-max-size',
                        '100', '--work-dir', str(self.work), '--keep', '--reuse-builds', str(self.first),
                        '--output', str(Path(self.tmp.name) / 'never.json')])

    def refused(self, old, sizes=(100,), record=None):
        with self.assertRaises(bench.ReuseRefused) as raised:
            bench.reuse_plan(old, list(sizes), self.work, self.parameters, record=record or self.old['build_provenance'])
        return raised.exception.code

    def test_reuse_is_refused_by_name_when_anything_the_build_depends_on_differs(self):
        record = self.old['build_provenance']
        self.assertEqual(sorted(bench.reuse_plan(self.old, [100], self.work, self.parameters)), [100])  # a real probe
        copy = lambda: json.loads(json.dumps(self.old))
        old = copy()
        del old['build_provenance']
        self.assertEqual(self.refused(old), 'NO_BUILD_PROVENANCE')
        for change in (lambda r: r['modules'].__setitem__('patterns/execution_store.py', '0' * 64),
                       lambda r: r['modules'].__setitem__('patterns/new_module.py', '0' * 64),
                       lambda r: r['files'].__setitem__('demos/mockups/synthetic/generate.py', '0' * 64),
                       lambda r: r['versions'].__setitem__('duckdb', '0.0.1'),
                       lambda r: r.__setitem__('root', 'installed')):
            old = copy()
            change(old['build_provenance'])
            self.assertEqual(self.refused(old, record=record), 'BUILD_RECORD_CHANGED')
        for key, value in (('memory_limit', '2GB'), ('budget_seconds_per_size', 1.0), ('rss_limit_bytes', 1),
                           ('staging', 'ndjson'), ('threads', 2)):
            old = copy()
            old['parameters'][key] = value
            self.assertEqual(self.refused(old), 'BUILD_PARAMETERS_CHANGED', key)
        old = copy()
        old['profile'] = 'execution-store-scaling-1.0'
        self.assertEqual(self.refused(old), 'BUILD_PARAMETERS_CHANGED')
        for key in ('cpu', 'platform', 'memory_bytes', 'python'):
            old = copy()
            old['environment'][key] = 'elsewhere'
            self.assertEqual(self.refused(old), 'ENVIRONMENT_CHANGED', key)
        self.assertEqual(self.refused(self.old, (100, 200)), 'SIZE_NOT_IN_REPORT')
        old = copy()
        old['sizes'][0]['snapshot_id'] = 'f' * 64
        self.assertEqual(self.refused(old), 'SNAPSHOT_MISSING')
        kept = self.work / 'size-100' / 'store' / self.old['sizes'][0]['snapshot_id']
        other = kept.parent / ('e' * 64)
        kept.rename(other)
        try:
            old['sizes'][0]['snapshot_id'] = 'e' * 64
            self.assertEqual(self.refused(old), 'SNAPSHOT_IDENTITY_CHANGED')
        finally:
            other.rename(kept)


if __name__ == '__main__':
    unittest.main()
