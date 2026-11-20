"""D48's tools (decision log, D48): the latency verdict normalises each searching query's ratio by the controls' geometric
mean in the same pair; the structural check fails when a control calls any counted function or the hull check runs, and
reads D48's two site changes back as MAIN's; the micro bound charges each searching query's calls at the measured
per-call costs. Nothing here times anything or opens a store."""
import ast
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

import d48_measure as dm  # noqa: E402


def run(times, digest='d'):
    return [{'seed': s, 'match_seconds': t, 'digest': digest + str(s)} for s, t in times.items()]


def record(repeat, workers, reference, candidate, digest='d'):
    order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
    return {'workers': workers, 'repeat': repeat, 'order': order,
            'reference': {'runs': [run(reference)] * 3, 'peak_rss_bytes': 1},
            'candidate': {'runs': [run(candidate, digest)] * 3, 'peak_rss_bytes': 1}}


def counts(meter=0, spend=0, refutes=0, backtrack=0):
    return {'_meter': meter, '_HullBudget.spend': spend, '_hull_refutes': refutes, '_backtrack': backtrack}


class D48ToolTests(unittest.TestCase):

    def test_a_searching_query_is_normalised_by_the_controls_in_its_pair(self):
        reference = {0: 1.0, 1: 2.0, 2: 4.0}
        candidate = {0: 1.1, 1: 2.2, 2: 4.4 * 1.04}  # every query 10% slower (drift), the searching one 4% more
        by_workers, identity = dm.paired_of([record(r, 14, reference, candidate) for r in range(4)], [2], [0, 1])
        pair = by_workers['14'][0]
        self.assertAlmostEqual(pair['control_geometric_mean'], 1.1)
        self.assertAlmostEqual(pair['normalised'][2], 1.04)
        self.assertAlmostEqual(pair['raw'][2], 1.1 * 1.04)
        self.assertEqual(identity['identical'], 3)
        _, identity = dm.paired_of([record(0, 14, reference, candidate, digest='x')], [2], [0, 1])
        self.assertEqual(identity['mismatching_seeds'], [0, 1, 2])

    def test_the_structural_check_fails_on_a_control_that_runs_counted_code_or_any_hull_check(self):
        ok = {'met': True}
        rows = [{'seed': 0, 'calls': counts()}, {'seed': 1, 'calls': counts(meter=2, spend=9)},
                {'seed': 2, 'calls': counts(backtrack=1)}]
        s = dm.structural_of(rows, ok)
        self.assertTrue(s['met'])
        self.assertEqual((s['searching'], s['controls'], s['building_a_meter']), ([1, 2], [0], [1]))
        self.assertFalse(dm.structural_of(rows + [{'seed': 3, 'calls': counts(refutes=1, meter=1)}], ok)['met'])
        self.assertFalse(dm.structural_of(rows, {'met': False})['met'])
        self.assertFalse(dm.structural_of([{'seed': 1, 'calls': counts(meter=1)}], ok)['met'])  # no control at all

    def test_the_site_check_reads_the_meter_and_the_mark_back_as_main(self):
        main = ast.parse('def decide(budget):\n    meter = Budget(budget)\n    found = _backtrack(meter)\n    out = {}\n')
        mine = ast.parse('def decide(budget):\n    meter = _meter(budget, lambda: _hull_refutes(1))\n'
                         '    found = _backtrack(meter)\n    out = {}\n    if getattr(meter, \'refuted\', False):\n'
                         '        out[\'hull_refuted\'] = True\n')
        self.assertEqual(dm._normalise_site(mine.body[0]), ast.dump(main.body[0]))
        other = ast.parse('def decide(budget):\n    meter = _meter(budget, lambda: _hull_refutes(1))\n'
                          '    found = _backtrack(meter, 2)\n    out = {}\n')
        self.assertNotEqual(dm._normalise_site(other.body[0]), ast.dump(main.body[0]))

    def test_the_micro_bound_charges_calls_at_the_measured_costs(self):
        rows = [{'seed': 5, 'calls': counts(meter=10, spend=1_000_000), 'hull_seconds': 0.001},
                {'seed': 6, 'calls': counts(), 'hull_seconds': 0.0}]
        costs = {'spend_extra_seconds': 1e-8, 'site_extra_seconds': 1e-7}
        m = dm.micro_of(rows, costs, {5: 2.0})
        (row,) = m['per_query']
        self.assertAlmostEqual(row['bound_seconds'], 0.01 + 1e-6 + 0.001)
        self.assertTrue(m['met'])
        self.assertFalse(dm.micro_of(rows, costs, {5: 1.0})['met'])  # 1.1% of 1 s

    def test_failures_are_classified_by_their_check(self):
        self.assertEqual(dm.classify('worlds: none, but the set [] is possible'), 'worlds_violation')
        self.assertEqual(dm.classify("decided ('none', None, None) but the truth is ('possible', None, None)"),
                         'truth_mismatch')
        self.assertEqual(dm.classify("a decided result changed: {} -> {}"), 'decided_changed')
        self.assertEqual(dm.classify("at a budget of 40,000 the result differs from the switch off's"),
                         'large_budget_differs')


if __name__ == '__main__':
    unittest.main()
