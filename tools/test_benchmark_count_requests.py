"""The count-request latency tool (D43b, `tools/benchmark_count_requests.py`): its requests are valid and carry a count
(but the two listed without one),
its statistics pair the runs, its verdict applies the committed bars, and its movement check reads only the results
the reference leaves STEP_CONDITION_UNCERTAIN. Nothing here times anything."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import benchmark_count_requests as bc  # noqa: E402


def run(seconds):
    return {'requests': {name: {'seconds': list(s), 'digests': ['d']} for name, s in seconds.items()},
            'parent_peak_rss_bytes': 1, 'worker_peak_rss_bytes': 2}


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
    NAMES = ['1.3 event frequency', '1.1 event frequency', '1.1 sequence']

    def records(self, slow, plain=1.0):
        out = []
        for repeat in range(4):
            order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
            ref = run({n: (5.0, 5.2, 5.1) for n in self.NAMES})
            factor = lambda n: plain if n in bc.NO_COUNT_REQUESTS else slow
            cand = run({n: (5.0 * factor(n), 5.2 * factor(n), 5.1 * factor(n)) for n in self.NAMES})
            out.append({'workers': 14, 'repeat': repeat, 'order': order, 'reference': ref, 'candidate': cand})
        return out

    def test_the_bars(self):
        stats = bc.statistics_of(self.records(1.5), self.NAMES)
        at = stats['14']['requests']['1.3 event frequency']
        self.assertAlmostEqual(at['ratio_by_repeat'][0], 1.5)
        self.assertEqual(at['candidate']['p95_seconds'], 5.2 * 1.5)
        self.assertEqual(len(at['ratio_by_order']['reference']), 2)
        self.assertTrue(bc.verdict_of(stats, self.NAMES)['met'])
        slow = bc.verdict_of(bc.statistics_of(self.records(2.1), self.NAMES), self.NAMES)
        self.assertFalse(slow['met'])
        self.assertFalse(slow['rule_4_1']['ratios']['1.1 event frequency']['every_pair_at_most'])
        self.assertNotIn('1.1 sequence', slow['rule_4_1']['ratios'])
        self.assertTrue(slow['rule_4_2b']['met'])

    def test_the_absolute_bar(self):
        verdict = bc.verdict_of(bc.statistics_of(self.records(1.95), self.NAMES), self.NAMES)
        self.assertTrue(all(r['every_pair_at_most'] for r in verdict['rule_4_1']['ratios'].values()))
        self.assertAlmostEqual(verdict['rule_4_1']['absolute']['candidate_p95_seconds'], 5.2 * 1.95)  # over 10 s
        self.assertFalse(verdict['rule_4_1']['absolute']['met'])
        self.assertFalse(verdict['met'])

    def test_the_bar_without_counts_is_the_pooled_median(self):
        stats = bc.statistics_of(self.records(1.5, plain=1.04), self.NAMES)
        self.assertAlmostEqual(stats['14']['requests']['1.1 sequence']['ratio_median_pooled'], 1.04)
        self.assertTrue(bc.verdict_of(stats, self.NAMES)['rule_4_2b']['met'])
        verdict = bc.verdict_of(bc.statistics_of(self.records(1.5, plain=1.06), self.NAMES), self.NAMES)
        self.assertTrue(verdict['rule_4_1']['met'])
        self.assertFalse(verdict['rule_4_2b']['met'])
        self.assertFalse(verdict['met'])


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


if __name__ == '__main__':
    unittest.main()
