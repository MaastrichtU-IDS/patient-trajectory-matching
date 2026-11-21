"""D52's tools: the latency verdict's rules (`benchmark_index_anchor.py`) and the worlds harness's truth
(`d52_measure.py`), on constructed inputs."""
from __future__ import annotations

from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'tools'), str(ROOT / 'demos' / 'mockups')):
    if p not in sys.path:
        sys.path.insert(0, p)

import benchmark_index_anchor as b  # noqa: E402
import d52_measure as m  # noqa: E402


def records(ratios, controls=('c1', 'c2'), count_name='event frequency', other='blank', narrow=True):
    """Pairs whose candidate time is `ratios[name][k]` times the reference's (1 s), controls at 1.0."""
    out = []
    for k in range(len(ratios[other])):
        rec = {'workers': 1, 'repeat': k, 'order': ('reference', 'candidate') if k % 2 == 0 else ('candidate', 'reference')}
        for arm in ('reference', 'candidate'):
            reqs = {}
            for name in (count_name, other):
                t = ratios[name][k] if arm == 'candidate' else 1.0
                reqs[name] = {'seconds': [t] * 3, 'digests': ['d'], 'narrow_calls': (3 if arm == 'candidate' and narrow else 0)}
            for c in controls:
                reqs[c] = {'seconds': [1.0] * 3, 'digests': ['c'], 'narrow_calls': 0}
            rec[arm] = {'requests': reqs, 'parent_peak_rss_bytes': 1, 'worker_peak_rss_bytes': 1}
        out.append(rec)
    return out


class VerdictTests(unittest.TestCase):
    def verdict(self, ratios, **kw):
        recs = records(ratios, **kw)
        return b.size_verdict(recs, ['event frequency', 'blank'], ['c1', 'c2'], 1, 10.0, 20, 20, len(recs))[1]

    def test_a_count_request_is_judged_by_its_median_over_pairs_and_another_by_every_pair(self):
        one_slow = [1.2] * 7 + [2.4]
        v = self.verdict({'event frequency': one_slow, 'blank': one_slow})
        self.assertTrue(v['requests']['event frequency']['ratio_met'])  # median 1.2 (D51)
        self.assertFalse(v['requests']['blank']['ratio_met'])  # one pair above 2.0
        self.assertTrue(v['protocol']['met'])

    def test_the_protocol_needs_eight_even_pairs(self):
        v = self.verdict({'event frequency': [1.1] * 6, 'blank': [1.1] * 6})
        self.assertFalse(v['protocol']['repeats_at_least_8_and_even'])
        self.assertFalse(v['protocol']['met'])

    def test_the_narrowing_runs_for_candidates_only(self):
        v = self.verdict({'event frequency': [1.1] * 8, 'blank': [1.1] * 8})
        self.assertTrue(all(x['met'] for x in v['structural_narrowing'].values()))
        v = self.verdict({'event frequency': [1.1] * 8, 'blank': [1.1] * 8}, narrow=False)
        self.assertFalse(v['structural_narrowing']['blank']['met'])
        self.assertTrue(v['structural_narrowing']['c1']['met'])

    def test_the_first_run_is_ruled_apart_against_its_bar_and_the_reference_first_run(self):
        # D52b review, S1: the first run (the narrowing's cache empty) at most 10 s in every pair, and normalised against
        # the reference's first run (controls' first runs in the same pair) at most 2.0 in every pair.
        def recs(cand_first, ref_first=4.0, control=1.0):
            out = []
            for k, t in enumerate(cand_first):
                rec = {'workers': 14, 'repeat': k}
                for arm in ('reference', 'candidate'):
                    rec[arm] = {'requests': {'blank': {'first_run_seconds': t if arm == 'candidate' else ref_first},
                                             'c1': {'first_run_seconds': control}, 'c2': {'first_run_seconds': control}}}
                out.append(rec)
            return out
        self.assertTrue(b.first_run_check(recs([7.0] * 8), ['blank'], ['c1', 'c2'], 14, 10.0)['met'])
        slow = b.first_run_check(recs([7.0] * 7 + [10.5]), ['blank'], ['c1', 'c2'], 14, 10.0)
        self.assertFalse(slow['requests']['blank']['every_first_run_at_most_bar'])
        ratio = b.first_run_check(recs([9.0] * 8), ['blank'], ['c1', 'c2'], 14, 10.0)
        self.assertFalse(ratio['requests']['blank']['every_normalised_at_most_ratio_bar'])  # 9 / 4 = 2.25

    def test_reference_windows_follow_the_page_rule_over_literal_records(self):
        facts = ({'P': 100 * b.MINUTE, 'Q': 5 * b.MINUTE}, {'P': 0, 'Q': 0}, {1: {'P': 10 * b.MINUTE, 'Q': 1 * b.MINUTE}, 3: {}})
        spec = {'candidate': {'conditions': None, 'index': {'start': {'offset': 0}, 'end': {'offset': 20}}}}
        self.assertEqual(b.reference_windows(spec, facts), {'P': {'start': 10 * b.MINUTE, 'end': 30 * b.MINUTE}, 'Q': None})
        nth = {'candidate': {'conditions': {'steps': {'@index_start': {'occurrence': {'nth': 3}}}}, 'index': spec['candidate']['index']}}
        self.assertEqual(b.reference_windows(nth, facts), {'P': None, 'Q': None})
        self.assertEqual(b.base_windows(facts), {'P': {'start': 0, 'end': 100 * b.MINUTE}, 'Q': {'start': 0, 'end': 5 * b.MINUTE}})


class TruthTests(unittest.TestCase):
    """The harness's per-world period on a constructed patient: a seizure x known only within [2, 4]."""

    def setUp(self):
        self.patient = {'patient_id': 'p', 'window': None, 'events': [
            {'id': 'e0', 'concepts': ['x', 'z'], 'start': [2, 4]}, {'id': 'e1', 'concepts': ['y', 'z'], 'start': [5, 5]}]}
        self.statics = {'last': 10, 'c_end': 8, 'c_start': 0}
        self.query = {'profile': 'trajectory-graph-1.0', 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'y', 'occurrence': 'any'}],
                      'relations': [], 'relaxations': [], 'budget': 0}
        self.Anchored = m.anchored_question()

    def spec(self, span):
        steps = [{'id': '@index_start', 'concept': 'x', 'occurrence': 'first', 'derived': None, 'bound': {'event': 'x'}}]
        return ({'step': '@index_start', 'offset': 0}, {'step': '@index_start', 'offset': span}, steps)

    def judge(self, result, span):
        return m.check(result, self.query, self.patient, self.spec(span), self.statics, self.Anchored, 12)

    def test_an_uncertain_anchor_makes_a_period_hold_in_some_worlds_only(self):
        # The period [x, x + 2] holds y at 5 only when x >= 3: possible, not exact.
        self.assertEqual(self.judge({'status': 'possible', 'binding': {'A': 'e1', '@index_start': 'e0'}}, 2), [])
        self.assertTrue(self.judge({'status': 'exact', 'binding': {'A': 'e1', '@index_start': 'e0'}}, 2))
        self.assertTrue(self.judge({'status': 'none'}, 2))
        # [x, x + 3] holds it in every world.
        self.assertEqual(self.judge({'status': 'exact', 'binding': {'A': 'e1', '@index_start': 'e0'}}, 3), [])

    def test_eligibility_is_part_of_the_match(self):
        self.statics['last'] = 6  # the period [x, x + 3] ends by last contact only when x <= 3
        self.assertTrue(self.judge({'status': 'exact', 'binding': {'A': 'e1', '@index_start': 'e0'}}, 3))
        self.assertEqual(self.judge({'status': 'possible', 'binding': {'A': 'e1', '@index_start': 'e0'}}, 3), [])
        self.statics['last'] = 1  # no world places a period
        self.assertEqual(self.judge({'status': 'excluded'}, 3), [])
        self.statics['last'] = 6
        self.assertTrue(self.judge({'status': 'excluded'}, 3))


if __name__ == '__main__':
    unittest.main()
