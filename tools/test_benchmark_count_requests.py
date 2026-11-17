"""The count-request latency tool (D43b, `tools/benchmark_count_requests.py`): its requests are valid and carry a count
(but the two listed without one), its statistics pair the runs, its verdict applies the committed bars and is met only
when the protocol was followed and the post-pass did its work in every run (the review's M1 and S2), its movement check
reads only the results the reference leaves STEP_CONDITION_UNCERTAIN, and a side's pool workers import that side's
code (S1). Nothing here times anything."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import benchmark_count_requests as bc  # noqa: E402

NAMES = list(bc.REQUESTS)
TALLY = {'refined': 7, 'skipped_mismatch': 0}
MET = {'met': True}


def run(seconds, side='candidate', tally=TALLY):
    def tallies(name):
        return [None if side == 'reference' or name in bc.NO_COUNT_REQUESTS else tally] * len(seconds[name])
    return {'requests': {name: {'seconds': list(s), 'digests': ['d' if name in bc.NO_COUNT_REQUESTS else side],
                                'tallies': tallies(name)} for name, s in seconds.items()},
            'parent_peak_rss_bytes': 1, 'worker_peak_rss_bytes': 2, 'pool_code': [side]}


def records(slow, plain=1.0, names=NAMES, tally=TALLY):
    out = []
    for repeat in range(4):
        order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
        ref = run({n: (5.0, 5.2, 5.1) for n in names}, 'reference')
        factor = lambda n: plain if n in bc.NO_COUNT_REQUESTS else slow
        cand = run({n: (5.0 * factor(n), 5.2 * factor(n), 5.1 * factor(n)) for n in names}, tally=tally)
        out.append({'workers': 14, 'repeat': repeat, 'order': order, 'reference': ref, 'candidate': cand})
    return out


def moved(names=NAMES, only=True):
    return {n: {'moved': 0 if n in bc.NO_COUNT_REQUESTS else 3, 'other_results_differing': 0 if only else 1,
                'only_uncertain_moved': only} for n in names}


class RequestTests(unittest.TestCase):
    def test_every_request_is_valid_and_has_a_count_unless_listed_without(self):
        from patterns import store_conditions as sc
        from patterns import store_conditions_counts as scn
        profiles = set()
        self.assertEqual(set(bc.NO_COUNT_REQUESTS), {'1.1 sequence', '1.3 coded only'})
        self.assertEqual(len(bc.COUNT_REQUESTS), 4)
        for name, (query, conditions) in bc.REQUESTS.items():
            self.assertEqual(scn.has_counts(conditions), name not in bc.NO_COUNT_REQUESTS, name)
            module = sc._module(conditions)
            module.Request(query, conditions)  # refuses an invalid request
            profiles.add(conditions['profile'])
            self.assertEqual(conditions.get('exclude_kinds'), ['mention'] if name.startswith('1.3') else None)
        self.assertEqual(profiles, {'execution-store-conditions-1.1', 'execution-store-conditions-1.3'})
        self.assertIn(bc.ABSOLUTE_BAR['request'], bc.REQUESTS)


class VerdictTests(unittest.TestCase):
    def verdict(self, recs, **kw):
        return bc.verdict_of(bc.statistics_of(recs, NAMES), NAMES, protocol=kw.get('protocol', MET),
                             answers=kw.get('answers', MET))

    def test_the_bars(self):
        stats = bc.statistics_of(records(1.5), NAMES)
        at = stats['14']['requests']['1.3 event frequency']
        self.assertAlmostEqual(at['ratio_by_repeat'][0], 1.5)
        self.assertEqual(at['candidate']['p95_seconds'], 5.2 * 1.5)  # the maximum of 12 runs
        self.assertEqual(len(at['ratio_by_order']['reference']), 2)
        self.assertTrue(self.verdict(records(1.5))['met'])
        slow = self.verdict(records(2.1))
        self.assertFalse(slow['met'])
        self.assertFalse(slow['rule_4_1']['ratios']['1.1 event frequency']['every_pair_at_most'])
        self.assertNotIn('1.1 sequence', slow['rule_4_1']['ratios'])
        self.assertTrue(slow['rule_4_2b']['met'])

    def test_the_absolute_bar(self):
        verdict = self.verdict(records(1.95))
        self.assertTrue(all(r['every_pair_at_most'] for r in verdict['rule_4_1']['ratios'].values()))
        self.assertAlmostEqual(verdict['rule_4_1']['absolute']['candidate_p95_seconds'], 5.2 * 1.95)  # over 10 s
        self.assertFalse(verdict['rule_4_1']['absolute']['met'])
        self.assertFalse(verdict['met'])

    def test_the_bar_without_counts_is_the_pooled_median(self):
        stats = bc.statistics_of(records(1.5, plain=1.04), NAMES)
        self.assertAlmostEqual(stats['14']['requests']['1.1 sequence']['ratio_median_pooled'], 1.04)
        self.assertTrue(self.verdict(records(1.5, plain=1.04))['rule_4_2b']['met'])
        verdict = self.verdict(records(1.5, plain=1.06))
        self.assertTrue(verdict['rule_4_1']['met'])
        self.assertFalse(verdict['rule_4_2b']['met'])
        self.assertFalse(verdict['met'])

    def test_no_verdict_without_the_protocol(self):
        self.assertTrue(bc.protocol_check([14, 8], NAMES, 4, 3)['met'])
        for workers, names, repeats, runs in (([8], NAMES, 4, 3), ([14], NAMES[:2], 4, 3), ([14], NAMES, 3, 3),
                                              ([14], NAMES, 2, 3), ([14], NAMES, 4, 2)):
            protocol = bc.protocol_check(workers, names, repeats, runs)
            self.assertFalse(protocol['met'], (workers, names, repeats, runs))
            self.assertFalse(self.verdict(records(1.5), protocol=protocol)['met'])
        self.assertFalse(bc.verdict_of(bc.statistics_of(records(1.5), NAMES), NAMES)['met'])  # neither check given


class AnswersTests(unittest.TestCase):
    def test_a_post_pass_that_did_its_work(self):
        self.assertTrue(bc.answers_check(records(1.5), moved(), NAMES)['met'])

    def test_a_post_pass_that_refined_nothing_or_skipped_fails(self):
        for tally in ({'refined': 0, 'skipped_mismatch': 0}, {'refined': 7, 'skipped_mismatch': 2}, None):
            out = bc.answers_check(records(1.0, tally=tally), moved(), NAMES)
            self.assertFalse(out['met'], tally)
            self.assertFalse(out['requests']['1.3 event frequency']['met'])
            self.assertTrue(out['requests']['1.1 sequence']['met'])

    def test_tallies_that_differ_between_runs_or_digests_that_differ_fail(self):
        recs = records(1.5)
        recs[2]['candidate']['requests']['1.1 nth and count']['tallies'][1] = {'refined': 6, 'skipped_mismatch': 0}
        self.assertFalse(bc.answers_check(recs, moved(), NAMES)['requests']['1.1 nth and count']['tally_identical'])
        recs = records(1.5)
        recs[1]['reference']['requests']['1.3 nth and count']['digests'] = ['other']
        self.assertFalse(bc.answers_check(recs, moved(), NAMES)['requests']['1.3 nth and count']['met'])
        recs = records(1.5)
        recs[3]['candidate']['requests']['1.3 coded only']['digests'] = ['changed']
        self.assertFalse(bc.answers_check(recs, moved(), NAMES)['requests']['1.3 coded only']['met'])
        self.assertFalse(bc.answers_check(records(1.5), moved(only=False), NAMES)['met'])


class MovementTests(unittest.TestCase):
    def test_only_uncertain_results_may_move(self):
        uncertain = {'patient_id': 'b', 'status': 'unresolved', 'reason': 'STEP_CONDITION_UNCERTAIN'}
        ref = [{'patient_id': 'a', 'status': 'exact'}, uncertain]
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / 'a', Path(tmp) / 'b'
            a.mkdir(), b.mkdir()
            stem = '1.3_event_frequency.json'
            (a / stem).write_text(json.dumps({'results': ref}))
            (b / stem).write_text(json.dumps({'results': [ref[0], {'patient_id': 'b', 'status': 'possible'}]}))
            out = bc.movement(a, b, ['1.3 event frequency'])['1.3 event frequency']
            self.assertEqual((out['moved'], out['moved_to'], out['only_uncertain_moved']), (1, {'possible': 1}, True))
            (b / stem).write_text(json.dumps({'results': [{'patient_id': 'a', 'status': 'none'}, uncertain]}))
            out = bc.movement(a, b, ['1.3 event frequency'])['1.3 event frequency']
            self.assertEqual((out['moved'], out['only_uncertain_moved']), (0, False))


class PoolCodeTests(unittest.TestCase):
    def test_a_sides_pool_workers_import_that_sides_code(self):
        """The review's S1: the reference side's spawned pool workers import the reference's `execution_store`, not
        this tree's (the script header must not put this tree in front of the side's)."""
        from patterns import execution_store as es
        from patterns import test_derived_steps as tds
        from tools.benchmark_matcher import extract_ref
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            snapshot = es.build(tds.derived_ingest(31), tmp / 'store')
            extract_ref('HEAD', tmp / 'reference')
            args = argparse.Namespace(snapshot_path=str(snapshot), runs=1, memory_limit='1GB', requests=[],
                                      budget_seconds=600)
            for root in (tmp / 'reference', ROOT):
                out = bc.run_side(root, args, 2)
                self.assertTrue(out['pool_code'])
                for path in out['pool_code']:
                    self.assertTrue(Path(path).resolve().is_relative_to(root.resolve()), (root, path))
                self.assertTrue(Path(out['code']).resolve().is_relative_to(root.resolve()))


if __name__ == '__main__':
    unittest.main()
