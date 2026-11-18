"""D44's measurement tools: the movement classification (`tools/d44_measure.py`) and the excluded-records latency
verdict (`tools/benchmark_exclusions.py`). Neither test needs DuckDB."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from patterns import execution_store as es  # noqa: E402
from tools import benchmark_exclusions as bx  # noqa: E402
from tools import d44_measure as dm  # noqa: E402

MATCH = {'relaxations': ['w'], 'cost': '0.5', 'certainty': 'possible', 'binding': {'A': 'a'}, 'method': 'm'}
OLD = {'patient_id': 'P', 'status': 'unresolved', 'reason': 'STEP_CONDITION_UNCERTAIN', 'uncertain_steps': ['A'],
       'relaxed_match': MATCH, 'complete': False}


class ClassifyTests(unittest.TestCase):
    """Rule 4's classification of a call's two answers: equal, D44's mark and nothing else, or other."""

    def test_the_mark_and_nothing_else(self):
        marked = {**OLD, 'relaxed_match': {**MATCH, 'note': es.MAY_MAKE_EXACT}, 'excluded_steps': ['A']}
        self.assertEqual(dm.classify(OLD, OLD), 'equal')
        self.assertEqual(dm.classify(OLD, marked), 'marked')
        self.assertEqual(dm.classify(OLD, es.apply_exclusions({'steps': [{'id': 'A', 'concept': 'X'}]}, OLD,
                                                              {'A': [(1, 1)]}, {'a': (0, 0)})), 'marked')
        noted = {**OLD, 'relaxed_match': {**MATCH, 'note': 'OTHER'}}
        self.assertEqual(dm.classify(noted, {**noted, 'relaxed_match': {**MATCH, 'note': 'OTHER; ' + es.MAY_MAKE_EXACT},
                                             'excluded_steps': ['A']}), 'marked')

    def test_anything_else_is_other(self):
        exact = {**OLD, 'relaxed_match': {**MATCH, 'certainty': 'exact'}}
        others = [
            {**exact, 'relaxed_match': {**exact['relaxed_match'], 'note': es.MAY_MAKE_EXACT}},  # an exact match marked
            {**OLD, 'relaxed_match': {**MATCH, 'note': es.MAY_MAKE_EXACT}, 'reason': 'ADMISSION_EXCLUDED_RECORDS'},
            {**OLD, 'excluded_steps': ['A']},  # steps named without a mark
            {**OLD, 'relaxed_match': {**MATCH, 'note': 'SOMETHING_ELSE'}},
            {**OLD, 'status': 'none'},
        ]
        for new in others:
            with self.subTest(new=json.dumps(new)):
                self.assertEqual(dm.classify(exact if new is others[0] else OLD, new), 'other')
        self.assertEqual(dm.classify({'status': 'possible'}, {'status': 'possible', 'note': 'X'}), 'other')


class ExclusionsVerdictTests(unittest.TestCase):
    """Rule 6.3's verdict: every pair at most 1.05, at least 4 repeats and every request, the rule run on enough
    results with an `unresolved` one among them, nothing changed but the mark."""

    def structure(self, calls=5000, unresolved=40, other=0):
        return {'calls': calls, 'unresolved_calls': unresolved, 'results_marked': 2,
                'movement': {'marked': 3, 'other': other}}

    def records(self, factor):
        out = []
        for repeat in range(4):
            order = ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference')
            out.append({'repeat': repeat, 'order': order, 'reference': {'seconds': [1.0, 2.0, 3.0]},
                        'candidate': {'seconds': [1.0 * factor, 2.0 * factor, 3.0 * factor]}})
        return out

    def test_the_verdict(self):
        groups = ['differential', 'value', 'relative']
        pairs = bx.statistics_of(self.records(1.02), groups)
        self.assertAlmostEqual(pairs[0]['ratio'], 1.02)
        self.assertEqual(sorted(pairs[0]['ratio_by_group']), sorted(groups))
        self.assertTrue(bx.verdict_of(pairs, 4, self.structure())['met'])
        self.assertFalse(bx.verdict_of(bx.statistics_of(self.records(1.06), groups), 4, self.structure())['met'])
        self.assertFalse(bx.verdict_of(pairs, 2, self.structure())['met'])
        self.assertFalse(bx.verdict_of(pairs, 4, self.structure(), limit=40)['met'])
        self.assertFalse(bx.verdict_of(pairs, 4, self.structure(calls=999))['met'])
        self.assertFalse(bx.verdict_of(pairs, 4, self.structure(unresolved=0))['met'])
        self.assertFalse(bx.verdict_of(pairs, 4, self.structure(other=1))['met'])


if __name__ == '__main__':
    unittest.main()
