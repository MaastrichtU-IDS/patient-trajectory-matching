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
        facts = ({'P': 100 * b.MINUTE, 'Q': 5 * b.MINUTE}, {'P': 0, 'Q': 0},
                 {('seizure', 1, False): {'P': 10 * b.MINUTE, 'Q': 1 * b.MINUTE}, ('seizure', 3, False): {},
                  ('valproate', 1, True): {'P': 50 * b.MINUTE}})
        index = {'start': {'step': '@index_start', 'offset': 0}, 'end': {'step': '@index_start', 'offset': 20}}
        q = lambda occ, concept='seizure': {'steps': [{'id': '@index_start', 'concept': concept, 'occurrence': occ}]}
        spec = {'candidate': {'query': q('first'), 'conditions': None, 'index': index}}
        self.assertEqual(b.reference_windows(spec, facts), {'P': {'start': 10 * b.MINUTE, 'end': 30 * b.MINUTE}, 'Q': None})
        nth = {'candidate': {'query': q('any'), 'conditions': {'steps': {'@index_start': {'occurrence': {'nth': 3}}}}, 'index': index}}
        self.assertEqual(b.reference_windows(nth, facts), {'P': None, 'Q': None})
        # D52c: the last valproate start (review S2's cold anchor), by the greatest earliest start.
        last = {'candidate': {'query': q('last', 'valproate'), 'conditions': None,
                              'index': {'start': {'step': '@index_start', 'offset': -10}, 'end': {'step': '@index_start', 'offset': 0}}}}
        self.assertEqual(b.reference_windows(last, facts), {'P': {'start': 40 * b.MINUTE, 'end': 50 * b.MINUTE}, 'Q': None})
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


class SimplifyPlantedCaseTests(unittest.TestCase):
    """D52c: three planted defects of `index_simplify` too rare for the random harness at its ruled size, each shown on a
    constructed case: the simplification is sound on it, and the defect makes the worlds check fail it."""

    def setUp(self):
        try:
            import duckdb  # noqa: F401
        except ImportError:
            self.skipTest('the store needs DuckDB')
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.coverage = [{'source': s, 'lo': 0, 'hi': 12, 'basis': 'declared'} for s in ('ehr', 'reg')]

    def patient(self, events, excluded=None):
        p = {'patient_id': 'p0', 'clock_id': 'clock_p0', 'coverage': self.coverage,
             'events': [{'event_id': f'e{k}', 'kind': 'condition', 'source': 'ehr', 'evidence': {}, **e} for k, e in enumerate(events)]}
        if excluded:
            p['excluded'] = excluded
        return p

    def spec(self, end_offset, start_offset=0):
        steps = [{'id': '@index_start', 'concept': 'x', 'occurrence': 'first', 'derived': None, 'bound': {'event': 'x'}}]
        return ({'step': '@index_start', 'offset': start_offset}, {'step': '@index_start', 'offset': end_offset}, steps)

    def query(self, steps, relations=(), relaxations=(), scope='from_start'):
        return {'profile': 'trajectory-graph-1.0', 'scope': scope, 'steps': list(steps), 'relations': list(relations),
                'relaxations': list(relaxations), 'budget': '1', 'search_budget': 40000}

    def run_case(self, patient, last, spec, query, defect=None):
        res, record = m.one_case(self.tmp.name + ('/' + (defect or 'sound')), [patient], {'p0': {'last': last, 'c_end': 0, 'c_start': 0}},
                                 spec, query, None, defect)
        return res['p0'], record

    def test_c3_withdraws_the_possible_claim_where_the_first_event_is_exact_through_different_events(self):
        # A (y) binds e0, which is also an x; the first x is at most e0 and at most 3 (e1) in every world, so the query is
        # exact with A = e0, though no single x is: an `any` step answers `possible`, whose "no exact" claim is false.
        p = self.patient([{'concepts': ['x', 'y'], 'start': [2, 5]}, {'concepts': ['x'], 'start': [1, 3]}])
        q = self.query([{'id': 'A', 'concept': 'y', 'occurrence': 'any'}])
        sound, record = self.run_case(p, 8, self.spec(5), q)
        self.assertEqual(record['existential'], '@index_start')
        result, wrong, _ = sound['H3s']
        self.assertEqual(result['status'], 'possible')
        self.assertIn('ANCHOR_ORDER_MAY_MAKE_EXACT', result['note'])
        self.assertEqual(wrong, [])
        planted, _ = self.run_case(p, 8, self.spec(5), q, 'exist_claims_unguarded')
        self.assertTrue(planted['H3s'][1], 'unguarded, the possible claim is false')

    def test_c3_reads_the_excluded_records_in_its_born_check(self):
        # The period [x - 5, x - 3]. An excluded x at 1 may be the first x, ending the period before birth: c3 must not apply.
        p = self.patient([{'concepts': ['x'], 'start': [8, 8]}, {'concepts': ['y'], 'start': [4, 4]}],
                         excluded=[{'source': 'ehr', 'concept': 'x', 'start': [1, 1], 'reason': 'NOT_ADMITTED'}])
        q = self.query([{'id': 'A', 'concept': 'y', 'occurrence': 'any'}])
        sound, record = self.run_case(p, 12, self.spec(-3, -5), q)
        self.assertIsNone(record['existential'])
        self.assertEqual(sound['H3s'][1] + sound['H3s'][2], [])
        planted, record = self.run_case(p, 12, self.spec(-3, -5), q, 'exist_excluded_unchecked')
        self.assertEqual(record['existential'], '@index_start')
        self.assertEqual(planted['H3s'][0]['status'], 'exact')
        self.assertTrue(planted['H3s'][2], 'over the whole source the exact claim is false')

    def test_c1_reads_the_concepts_a_relaxation_widens_a_step_to(self):
        # A (x) widened to z may bind the y at 4 before the first x (3 to 5): the relation from the anchor is not implied
        # under the relaxation, so c1 keeps it; dropped, the relaxed match is claimed where no world has it.
        p = self.patient([{'concepts': ['x'], 'start': [3, 5]}, {'concepts': ['y'], 'start': [4, 4]}])
        q = self.query([{'id': 'A', 'concept': 'x', 'occurrence': 'any'}],
                       [{'id': 'edge:E', 'kind': 'after', 'from': 'END', 'to': 'A', 'min': -8, 'max': -6}],
                       [{'id': 'node:A', 'kind': 'widen_concept', 'step': 'A', 'concept': 'z', 'cost': '0.5'}])
        sound, record = self.run_case(p, 12, self.spec(5), q)
        self.assertEqual(record['dropped'], [])
        self.assertEqual(sound['H3s'][1], [])
        planted, record = self.run_case(p, 12, self.spec(5), q, 'drop_wider_concept')
        self.assertEqual(record['dropped'], ['index:scope-start:A'])
        self.assertTrue(planted['H3s'][1], 'the relaxed match is claimed where no world has it')

if __name__ == '__main__':
    unittest.main()
