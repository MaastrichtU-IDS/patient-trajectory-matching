"""D51's analysis (decision log, D51): the decision rule's three outcomes and their edges, the normalised ratio pair by
pair from a count-latency report, no decision without the protocol or the answers, and the variance diagnosis. Nothing
here times anything or opens a store."""
from pathlib import Path
import json
import math
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

import benchmark_count_requests as bcr  # noqa: E402
import d51_count_investigation as d51  # noqa: E402


def side(times):
    """A process's output: {request: 3 runs' seconds}."""
    return {'requests': {name: {'seconds': list(times[name])} for name in d51.REQUESTS},
            'parent_peak_rss_bytes': 1, 'worker_peak_rss_bytes': 1}


def report(count_ratios, control_ratios=None, runs=None, repeats=None, answers_met=True, moved=False):
    """A count-latency report as the tool writes it, with the count request's candidate at `count_ratios[i]` of the
    reference in pair i and each control at `control_ratios[i]` (default 1)."""
    control_ratios = control_ratios or [1.0] * len(count_ratios)
    base = {d51.REQUEST: 7.0, '1.1 sequence': 3.2, '1.3 coded only': 3.5}
    runs = runs or (lambda name, s, i: [s, s, s])
    records = []
    for i, (c, g) in enumerate(zip(count_ratios, control_ratios)):
        ref = {n: runs(n, base[n], i) for n in d51.REQUESTS}
        cand = {n: [t * (c if n == d51.REQUEST else g) for t in ref[n]] for n in d51.REQUESTS}
        records.append({'workers': 14, 'repeat': i, 'order': ('reference', 'candidate') if i % 2 == 0
                        else ('candidate', 'reference'), 'reference': side(ref), 'candidate': side(cand)})
    rows = {n: {'met': True, 'nothing_moved': not moved} for n in d51.REQUESTS}
    return {'repeats': repeats or len(count_ratios), 'runs_per_request': 3, 'workers': [14],
            'requests': {n: {} for n in d51.REQUESTS}, 'size': 300000, 'snapshot_id': 's',
            'reference': {'git_ref': 'f9e087e', 'git_commit': 'f9e087e8'},
            'by_workers': bcr.statistics_of(records, list(d51.REQUESTS)),
            'answers': {'check': {'met': answers_met, 'requests': rows}}}


class DecisionRuleTests(unittest.TestCase):

    def test_no_slowdown_at_the_edges(self):
        self.assertEqual(d51.decide([1.02] * 8)['outcome'], 'i')
        self.assertEqual(d51.decide([1.0] * 7 + [1.2])['outcome'], 'i')  # one pair above 1.05 is allowed

    def test_two_pairs_above_the_bar_is_not_no_slowdown(self):
        out = d51.decide([1.0] * 6 + [1.06, 1.06])
        self.assertEqual((out['outcome'], out['pairs_over_bar']), ('iii', 2))

    def test_a_median_just_above_1_02_is_inconclusive(self):
        self.assertEqual(d51.decide([1.021] * 8)['outcome'], 'iii')
        self.assertEqual(d51.decide([1.05] * 8)['outcome'], 'iii')  # 1.05 is not above 1.05

    def test_a_median_above_1_05_is_a_slowdown(self):
        self.assertEqual(d51.decide([1.051] * 8)['outcome'], 'ii')

    def test_the_median_of_eight_is_the_mean_of_the_middle_two(self):
        out = d51.decide([1.0, 1.0, 1.0, 1.01, 1.03, 1.1, 1.1, 1.1])
        self.assertAlmostEqual(out['median_normalised'], 1.02)
        self.assertEqual(out['outcome'], 'iii')  # median 1.02, but 3 pairs above 1.05


class AnalysisTests(unittest.TestCase):

    def test_the_count_request_is_normalised_by_the_controls_in_its_pair(self):
        out = d51.analyse_report(report([1.10] * 8, [1.10] * 8))  # everything 10% slower: drift, not the request
        for p in out['pairs']:
            self.assertAlmostEqual(p['raw'], 1.10)
            self.assertAlmostEqual(p['controls_geometric_mean'], 1.10)
            self.assertAlmostEqual(p['normalised'], 1.0)
        self.assertEqual(out['decision']['outcome'], 'i')

    def test_one_slow_pair_of_eight_is_no_slowdown_but_two_are_not(self):
        self.assertEqual(d51.analyse_report(report([1.0] * 7 + [1.078]))['decision']['outcome'], 'i')
        self.assertEqual(d51.analyse_report(report([1.0] * 6 + [1.078] * 2))['decision']['outcome'], 'iii')

    def test_a_steady_slowdown_is_a_slowdown(self):
        out = d51.analyse_report(report([1.08] * 8))
        self.assertEqual(out['decision']['outcome'], 'ii')
        self.assertEqual(out['ratio_by_order']['reference'], [out['pairs'][i]['normalised'] for i in (0, 2, 4, 6)])

    def test_no_decision_without_the_protocol_or_the_answers(self):
        self.assertIsNone(d51.analyse_report(report([1.0] * 4))['decision']['outcome'])  # 4 pairs, not 8
        self.assertIsNone(d51.analyse_report(report([1.0] * 8, answers_met=False))['decision']['outcome'])
        self.assertIsNone(d51.analyse_report(report([1.0] * 8, moved=True))['decision']['outcome'])

    def test_within_process_spread_by_request(self):
        # the count request's runs spread 10% in every process, the controls' not at all
        runs = (lambda name, s, i: [s * 0.95, s, s * 1.05] if name == d51.REQUEST else [s, s, s])
        within = d51.analyse_report(report([1.0] * 8, runs=runs))['variance']['within_process']
        self.assertAlmostEqual(within[d51.REQUEST]['relative_range_median'], 0.10)
        self.assertEqual(len(within[d51.REQUEST]['processes']), 16)
        self.assertEqual(within['1.1 sequence']['relative_range_max'], 0.0)
        self.assertAlmostEqual(within[d51.REQUEST]['by_side']['candidate']['relative_range_max'], 0.10)

    def test_between_pair_spread_and_correlation(self):
        count = [1.0, 1.1, 1.0, 1.1, 1.0, 1.1, 1.0, 1.1]
        between = d51.analyse_report(report(count, count))['variance']['between_pairs']
        self.assertAlmostEqual(between['sd_log_ratio']['normalised'], 0.0)
        self.assertAlmostEqual(between['correlation_count_with_controls'], 1.0)
        self.assertAlmostEqual(between['sd_log_ratio'][d51.REQUEST],
                               math.sqrt(8 / 7) * math.log(1.1) / 2)
        between = d51.analyse_report(report(count))['variance']['between_pairs']
        self.assertIsNone(between['correlation_count_with_controls'])  # the controls do not move

    def test_write_embeds_the_tools_report_and_binds_both_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = d51.write(report([1.0] * 8), Path(tmp) / 'r.json', command=['x'])
            self.assertEqual(json.loads((Path(tmp) / 'r.json').read_text())['analysis']['decision']['outcome'], 'i')
        self.assertEqual(sorted(out['artifacts']), sorted(d51.ARTIFACTS))
        self.assertIn('by_workers', out['count_latency_report'])


class SubsetVerdictTests(unittest.TestCase):
    """D51's first attempt lost its report: the count tool's `verdict_of` reads D44b's absolute request, which a subset
    of requests does not run."""

    def test_the_tools_verdict_fails_on_the_subset_and_the_wrapper_does_not(self):
        stats = report([1.0] * 8)['by_workers']
        with self.assertRaises(KeyError):
            bcr.verdict_of(stats, list(d51.REQUESTS), 14, ratio_bar=1.05, normalised=True)
        out = d51.subset_verdict(bcr.verdict_of)(stats, list(d51.REQUESTS), 14, ratio_bar=1.05, normalised=True,
                                                  protocol={'met': True}, answers={'met': True}, identity='not ruled')
        absolute = out['rule_4_1']['absolute']
        self.assertEqual((absolute['request'], absolute['met']), (d51.REQUEST, True))
        self.assertIn('not applied', absolute['note'])
        self.assertFalse(out['met'])  # no micro, no control check: the tool's verdict is not D51's rule

    def test_the_wrapper_leaves_a_run_with_the_absolute_request_alone(self):
        seen = {}
        d51.subset_verdict(lambda stats, requests, *a, **k: seen.update(k))({}, ['1.3 event frequency'])
        self.assertNotIn('absolute', seen)

    def test_run_tool_puts_the_tools_verdict_back(self):
        real, main = bcr.verdict_of, bcr.main
        seen = []
        bcr.main = lambda argv: seen.append((argv, bcr.verdict_of is real)) or 0
        try:
            d51.run_tool(['--x'])
        finally:
            bcr.main = main
        self.assertEqual(seen, [(['--x'], False)])
        self.assertIs(bcr.verdict_of, real)


if __name__ == '__main__':
    unittest.main()
