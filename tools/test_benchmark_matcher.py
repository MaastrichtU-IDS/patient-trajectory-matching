"""Smoke test of the matcher comparison: the report's structure and the byte-identity check, not its numbers.

Needs DuckDB (`patterns/requirements-store.lock.txt`) and git; skipped without them. Arm A here is the
committed tree (`HEAD`), so on a clean checkout both arms are the same code and must agree.
"""
import functools
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch
from unittest import mock

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

    def test_paired_statistics_give_per_pair_ratios_the_candidates_target_and_the_digests(self):
        """D31, second attempt: the paired, interleaved confirmation's figures from hand-made runs."""
        def run(times, digests, peak=2**30):
            return {'rows': [{'seed': i, 'match_seconds': t, 'digest': d} for i, (t, d) in enumerate(zip(times, digests))],
                    'peak_rss_bytes': peak}
        same = ['a', 'b', 'c']
        records = [{'workers': 14, 'repeat': 0, 'order': ('reference', 'candidate'),
                    'reference': run([1.0, 2.0, 10.0], same), 'candidate': run([1.0, 1.0, 8.0], same)},
                   {'workers': 14, 'repeat': 1, 'order': ('candidate', 'reference'),
                    'reference': run([1.0, 2.0, 12.0], same), 'candidate': run([2.0, 2.0, 9.0], same, 5 * 2**30)}]
        got = matcher.paired_statistics(records)
        w = got['by_workers']['14']
        self.assertEqual(w['ratio_p95_by_repeat'], [0.8, 0.75])
        self.assertEqual(w['ratio_total_by_repeat'], [10.0 / 13.0, 13.0 / 15.0])
        self.assertEqual((w['ratio_p95_max'], w['ratio_p95_min']), (0.8, 0.75))
        self.assertEqual(w['ratio_p95_by_order'], {'reference': [0.8], 'candidate': [0.75]})
        self.assertEqual(w['pairs'][1]['order'], ['candidate', 'reference'])
        self.assertEqual(w['pairs'][0]['per_query'][2], {'seed': 2, 'reference_seconds': 10.0, 'candidate_seconds': 8.0,
                                                          'ratio': 0.8})
        check = w['candidate_target_check']
        self.assertEqual((check['p95_seconds_all_runs'], check['max_seconds_all_runs']), (9.0, 9.0))
        self.assertEqual(check['p95_seconds_by_repeat'], [8.0, 9.0])
        self.assertEqual((check['p95_met_all_runs'], check['max_met_all_runs'], check['memory_met']), (True, True, False))
        self.assertEqual(w['reference_absolute']['p95_seconds_all_runs'], 12.0)
        self.assertEqual(got['byte_identity'], {'compared': 3, 'identical': 3, 'mismatching_seeds': [],
                                                'each_code_stable_across_its_runs': {'reference': True, 'candidate': True}})
        # A change meant to move results: every per-query digest of each code is listed.
        records[1]['candidate'] = run([2.0, 2.0, 9.0], ['a', 'x', 'c'])
        records[0]['candidate'] = run([1.0, 1.0, 8.0], ['a', 'x', 'c'])
        ident = matcher.paired_statistics(records)['byte_identity']
        self.assertEqual((ident['identical'], ident['mismatching_seeds']), (2, [1]))
        self.assertEqual(ident['per_query_digests'][1], {'seed': 1, 'reference': ['b'], 'candidate': ['x']})
        # A code whose digests differ between its own runs is reported as unstable.
        records[1]['candidate'] = run([2.0, 2.0, 9.0], ['a', 'y', 'c'])
        ident = matcher.paired_statistics(records)['byte_identity']
        self.assertFalse(ident['each_code_stable_across_its_runs']['candidate'])
        self.assertEqual(ident['per_query_digests'][1]['candidate'], ['x', 'y'])


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
        # D31, second attempt: the paired confirmation, the committed tree against itself (both codes the same).
        matcher.main(['--paired-confirm', '--snapshots', f'96={cls.snapshot}', '--paired-reference', 'HEAD',
                      '--large-queries', '3', '--paired-repeats', '2', '--paired-workers', '2', '1',
                      '--output', str(cls.output)])
        cls.paired = json.loads(cls.output.read_text())['paired_confirmation']

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_paired_confirmation_alternates_the_order_and_compares_the_digests(self):
        p = self.paired
        self.assertEqual((p['repeats'], p['workers'], p['queries']), (2, [2, 1], 3))
        self.assertEqual(p['reference']['git_ref'], 'HEAD')
        self.assertEqual(p['candidate']['git_ref'], 'this tree')
        for n in ('1', '2'):
            pairs = p['by_workers'][n]['pairs']
            self.assertEqual([x['order'] for x in pairs], [['reference', 'candidate'], ['candidate', 'reference']])
            self.assertTrue(all(len(x['per_query']) == 3 and x['ratio_p95'] > 0 for x in pairs))
            self.assertEqual(len(p['by_workers'][n]['ratio_p95_by_repeat']), 2)
            self.assertIn('p95_met_all_runs', p['by_workers'][n]['candidate_target_check'])
        self.assertEqual(p['byte_identity']['identical'], 3)
        self.assertNotIn('per_query_digests', p['byte_identity'])
        self.assertEqual(self.report['sizes'], json.loads(self.output.read_text())['sizes'])  # the rest is kept

    def test_each_arm_reports_cost_latency_and_memory_and_later_arms_are_placeholders(self):
        entry = self.report['sizes'][0]
        for arm in ('A', 'B', 'B_unpruned', 'B_decided'):
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
        # Time pruning off (D25) gives the same bytes.
        self.assertEqual(entry['byte_identity_B_against_B_unpruned'], {'compared': 3, 'identical': 3, 'mismatching_seeds': []})
        self.assertEqual(entry['byte_identity_C_against_B'],
                         {w: {'compared': 3, 'identical': 3, 'mismatching_seeds': []} for w in ('C_w1', 'C_w2')})
        self.assertEqual(set(entry['target_check_arm_C']), {'C_w1', 'C_w2'})
        # D28 review: every arm's per-query result digests are kept in the report, so arms and runs can be compared
        # query by query from the report alone.
        digests = {arm: [q['digest'] for q in entry['arms'][arm]['per_query']] for arm in ('A', 'B', 'B_unpruned', 'C_w2')}
        self.assertTrue(all(len(d) == 3 and all(len(x) == 64 for x in d) for d in digests.values()))
        self.assertEqual(len({tuple(d) for d in digests.values()}), 1)
        self.assertIsNone(entry['arms']['B_decided']['per_query'][0]['digest'])
        for path, sha in self.report['artifacts'].items():
            self.assertEqual(sha, matcher.digest(path), path)
        self.assertFalse(self.report['contains_patient_level_data'])
        self.assertTrue(self.report['limitations'])
        self.assertNotIn('arm_a_snapshot', entry)  # the same profile: arm A reads the snapshot as it is

    def test_pruned_candidates_and_pruning_time_are_recorded_per_query(self):
        """D25 review, done in D26: the pruned share is bound evidence, with what the start summaries alone prune."""
        entry = self.report['sizes'][0]
        b = entry['arms']['B']
        pruning = b['pruning']
        self.assertEqual(pruning['pruned'], sum(q['pruned'] for q in b['per_query']))
        self.assertEqual(pruning['candidates_with_window'], sum(q['candidates_with_window'] for q in b['per_query']))
        self.assertLessEqual(pruning['pruned_by_start_summaries_alone'], pruning['pruned'])
        self.assertEqual(pruning['pruned_only_by_end_summaries'],
                         pruning['pruned'] - pruning['pruned_by_start_summaries_alone'])
        self.assertTrue(all(q['prune_seconds'] >= 0 and q['candidates_with_window'] <= q['candidates']
                            for q in b['per_query']))
        self.assertNotIn('pruning', entry['arms']['B_unpruned'])  # pruning off: nothing to count
        self.assertEqual(entry['arms']['C_w2']['pruning']['pruned'], pruning['pruned'])  # the parent prunes


@unittest.skipIf(duckdb is None, 'DuckDB is not installed')
class OlderArmTests(unittest.TestCase):
    """Arm A's code may read an older store profile than the snapshots' (2.0 against 3.0, D26, which moved columns): it
    reads its own snapshot of the same cohort, built by its own builder, which must hold the same rows in every column
    matching reads, and decides byte for byte as this tree does."""
    REF = '0a18b9a'  # main before D24: execution store 2.0

    def test_an_older_arm_reads_its_own_snapshot_of_the_same_cohort_and_agrees(self):
        import io
        from contextlib import redirect_stdout
        from patterns import execution_store as es
        from patterns import synthetic_ingest as si
        with tempfile.TemporaryDirectory(prefix='matcher-older-') as tmp:
            # Arm A's converter times letter mentions by their letter; D30 changed this tree's. The cohort the other
            # arms read is converted the same way here, so that the two snapshots hold the same rows.
            with patch.object(si, 'iter_patients', functools.partial(si.iter_patients, mention_time='letter')), \
                    patch.object(si, 'synthetic_provenance', functools.partial(si.synthetic_provenance, mention_time='letter')):
                legacy = bench.replicate(bench.pool_ingest(96)[0], 96)
            snapshot = es.build(legacy, Path(tmp) / 'store')
            output = Path(tmp) / 'report.json'
            try:
                with redirect_stdout(io.StringIO()) as printed:
                    matcher.main(['--build-arm-a', f'96={Path(tmp) / "arm-a"}', '--arm-a-ref', self.REF])
            except (OSError, matcher.subprocess.CalledProcessError) as exc:  # no git, or a clone without the ref
                raise unittest.SkipTest(f'git archive {self.REF} unavailable: {exc}')
            size, _, own = printed.getvalue().strip().splitlines()[-1].partition('=')
            self.assertEqual(json.loads((Path(own) / 'manifest.json').read_text())['profile'], 'execution-store-2.0')
            matcher.main(['--snapshots', f'96={snapshot}', '--arm-a-snapshots', f'96={own}', '--arm-a-ref', self.REF,
                          '--arms', 'A', 'B', '--queries', '4', '--repeats', '1', '--workers', '1',
                          '--output', str(output)])
            report = json.loads(output.read_text())
            # Without its own snapshot, arm A is not run on a snapshot of another profile.
            matcher.main(['--snapshots', f'96={snapshot}', '--arm-a-ref', self.REF, '--arms', 'A', '--queries', '1',
                          '--repeats', '1', '--workers', '1', '--output', str(output)])
            alone = json.loads(output.read_text())['sizes'][0]
        entry = report['sizes'][0]
        self.assertEqual(size, '96')
        # D30 changed `last` and decides uncertain first and last targets differently: a query may differ from arm A's
        # only if it has a first or last step.
        identity = entry['byte_identity_B_against_A']
        self.assertEqual(identity['compared'], 4)
        ordered = {seed for seed, q in bench.query_inputs(4)[0] if any(s.get('occurrence', 'any') != 'any' for s in q['steps'])}
        self.assertLessEqual(set(identity['mismatching_seeds']), ordered)
        same = entry['arm_a_snapshot']['same_rows']
        self.assertTrue(same['all_equal'], same)
        self.assertEqual(entry['arm_a_snapshot']['columns_2_1'],
                         {'rows_set': {'start_var': 0, 'end_var': 0, 'proper': 0, 'shared_variables': 0}, 'all_zero': True})
        self.assertEqual(set(same['tables']), set(matcher.READ_COLUMNS))
        self.assertGreater(same['tables']['events']['rows'], 0)
        self.assertEqual(entry['arm_a_snapshot']['profile'], 'execution-store-2.0')
        self.assertEqual(report['arms']['A']['store_profile'], 'execution-store-2.0')
        self.assertEqual((entry['arms']['A']['store_profile'], entry['arms']['B']['store_profile']),
                         ('execution-store-2.0', es.PROFILE))
        self.assertEqual(alone['arms']['A']['status'], 'not_run')
        self.assertIn('ARM_A_SNAPSHOT_PROFILE', alone['arms']['A']['reason'])

    def test_different_rows_are_reported(self):
        """The rows check compares the columns matching reads: a snapshot with one event moved by a microsecond
        differs in `events` and the postings, and in nothing else."""
        from patterns import execution_store as es
        ingest = bench.replicate(bench.pool_ingest(96)[0], 96)
        moved = json.loads(json.dumps(ingest))
        e = moved['patients'][3]['events'][0]
        e['start'] = [e['start'][0], e['start'][1] + 1]
        with tempfile.TemporaryDirectory(prefix='matcher-rows-') as tmp:
            a, b = es.build(ingest, Path(tmp) / 'a'), es.build(moved, Path(tmp) / 'b')
            self.assertTrue(matcher.same_rows(a, a, '1GB')['all_equal'])
            got = matcher.same_rows(a, b, '1GB')
        self.assertFalse(got['all_equal'])
        self.assertEqual({n for n, t in got['tables'].items() if not t['equal']}, {'events', 'concept_postings'})

    def test_the_digest_reader_leaves_no_spill_directory(self):
        """D28 (a D26 follow-up): `read_digests` removes the spill directory it creates, also when it fails."""
        from patterns import execution_store as es
        with tempfile.TemporaryDirectory(prefix='matcher-spill-') as tmp, \
                mock.patch.object(matcher.tempfile, 'tempdir', tmp):
            snapshot = es.build(bench.replicate(bench.pool_ingest(48)[0], 48), Path(tmp) / 'store')
            before = set(Path(tmp).iterdir())
            matcher.read_digests(snapshot, '1GB')
            matcher.columns_2_1(snapshot, '1GB')
            with self.assertRaises(Exception):
                matcher.read_digests(Path(tmp) / 'absent', '1GB')
            self.assertEqual(set(Path(tmp).iterdir()), before)

    def test_the_2_1_columns_are_counted_and_must_be_unset_on_synthetic_cohorts(self):
        """D28 (a D26 review follow-up): the rows check covers the columns arm A reads; B's 2.1 columns are counted
        beside it, and each is 0 on a synthetic cohort and counted when set."""
        from patterns import execution_store as es
        ingest = bench.replicate(bench.pool_ingest(48)[0], 48)
        named = json.loads(json.dumps(ingest))
        p, exposure = next((p, e) for p in named['patients'] for e in p['events']
                           if 'end' in e and e['end'][1] is not None and e['end'][0] > e['start'][1])
        other = next(e for e in p['events'] if e is not exposure)
        exposure.update(proper=True, start_var='shared.t')
        other.update(start_var='shared.t', start=list(exposure['start']))
        with tempfile.TemporaryDirectory(prefix='matcher-columns-') as tmp:
            plain = matcher.columns_2_1(es.build(ingest, Path(tmp) / 'a'), '1GB')
            set_ = matcher.columns_2_1(es.build(named, Path(tmp) / 'b'), '1GB')
        self.assertEqual(plain, {'rows_set': {'start_var': 0, 'end_var': 0, 'proper': 0, 'shared_variables': 0},
                                 'all_zero': True})
        self.assertFalse(set_['all_zero'])
        self.assertEqual((set_['rows_set']['start_var'], set_['rows_set']['proper'], set_['rows_set']['shared_variables']),
                         (2, 1, 1))


if __name__ == '__main__':
    unittest.main()
