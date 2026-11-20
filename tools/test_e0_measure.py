"""E0's measuring tool (`tools/e0_measure.py`): the contradiction rule between nested arms, the owner's counting rule,
the mention-truth oracle and the seed lists. No store is built (the arms are exercised by the ruled run)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

import e0_measure as em  # noqa: E402
from patterns import mention_time as mt  # noqa: E402
from patterns import synthetic_ingest as si  # noqa: E402


def r(status, **kw):
    return {'status': status, **kw}


class TransitionTests(unittest.TestCase):
    def test_a_decided_claim_must_hold_in_the_smaller_arm(self):
        ok, bad = em.transition_ok, lambda a, b: not em.transition_ok(a, b)
        self.assertTrue(ok(r('exact', complete=True), r('exact', complete=True)))
        self.assertTrue(bad(r('exact', complete=True), r('possible', complete=True)))
        self.assertTrue(bad(r('exact'), r('none', complete=True)))
        self.assertTrue(ok(r('none', complete=True), r('none', complete=True)))
        self.assertTrue(bad(r('none', complete=True), r('relaxed', certainty='possible', relaxations=['x'], cost='0.5')))
        self.assertTrue(ok(r('none', complete=False, note='RELAXATION_ORDER_UNCERTAIN'),
                           r('relaxed', certainty='possible', relaxations=['x'], cost='0.5', complete=True)))
        self.assertTrue(bad(r('none', complete=True), r('possible')))

    def test_relaxed_claims(self):
        ok, bad = em.transition_ok, lambda a, b: not em.transition_ok(a, b)
        exact = r('relaxed', certainty='exact', relaxations=['a'], cost='0.5', complete=True)
        self.assertTrue(ok(exact, dict(exact)))
        self.assertTrue(bad(exact, r('relaxed', certainty='possible', relaxations=['a'], cost='0.5', complete=True)))
        self.assertTrue(bad(exact, r('none', complete=True)))
        self.assertTrue(bad(exact, r('possible')))
        possible = r('relaxed', certainty='possible', relaxations=['a'], cost='0.5', complete=True)
        self.assertTrue(ok(possible, r('none', complete=True)))
        self.assertTrue(ok(possible, r('relaxed', certainty='exact', relaxations=['b'], cost='1', complete=True)))
        self.assertTrue(bad(possible, r('relaxed', certainty='exact', relaxations=['c'], cost='0.25', complete=True)))
        self.assertTrue(bad(possible, r('exact')))
        incomplete = r('relaxed', certainty='exact', relaxations=['a'], cost='0.5', complete=False)
        self.assertTrue(ok(incomplete, r('relaxed', certainty='possible', relaxations=['z'], cost='0.25')))
        self.assertTrue(bad(incomplete, r('exact')))

    def test_undecided_results_and_windows(self):
        ok, bad = em.transition_ok, lambda a, b: not em.transition_ok(a, b)
        for small in ('unresolved', 'not_comparable'):
            self.assertTrue(ok(r('exact'), r(small)))
            self.assertTrue(ok(r('none', complete=True), r(small)))
        for big in ('unresolved', 'not_comparable', 'possible'):
            for small in ('exact', 'none', 'possible'):
                self.assertTrue(ok(r(big), r(small)))
        self.assertTrue(ok(r('excluded'), r('excluded')))
        self.assertTrue(bad(r('excluded'), r('none')))
        self.assertTrue(bad(r('possible'), r('excluded')))


def run(keys, arms):
    return {'keys': keys, 'arms': arms}


def case(results, with_candidate, seconds=0.5):
    return {'results': results, 'with_candidate': with_candidate, 'seconds': seconds}


class CountingTests(unittest.TestCase):
    def runs(self):
        keys = {'C001': 'reference', 'C002': 'dravet-1', 'C003': 'dravet-1', 'C004': 'gefs-0'}
        g = case({'C001': r('possible'), 'C002': r('unresolved'), 'C003': r('possible'), 'C004': r('none', complete=True)},
                 ['C001', 'C002', 'C003'])
        gt = case({'C001': r('exact'), 'C002': r('unresolved'), 'C003': r('possible'), 'C004': r('none', complete=True)},
                  ['C001', 'C002', 'C003'])
        t = case({'C001': r('exact'), 'C002': r('exact'), 'C003': r('none', complete=True), 'C004': r('exact')},
                 ['C001', 'C002', 'C003'])
        return {1: run(keys, {'G': {'x': g}, 'GT': {'x': gt}, 'T': {'x': t}})}

    def test_the_owners_counting_rule(self):
        t = em.tally(self.runs(), 'x', 'GT')
        self.assertEqual((t['candidates'], t['unresolved'], t['exact']), (3, 1, 1))
        self.assertEqual(t['distinct_child_types'], {'types': 2, 'types_unresolved': 1})
        self.assertEqual(t['without_C001'], {'candidates': 2, 'unresolved': 1})
        self.assertEqual(t['statuses'], {'exact': 1, 'possible': 1, 'unresolved': 1})

    def test_movement_and_contradictions(self):
        runs = self.runs()
        m = em.compare(runs, 'x', 'G', 'GT')
        self.assertEqual((m['contradictions'], m['became_exact_among_candidates']), (0, 1))
        self.assertEqual(m['candidate_transitions'], {'possible -> exact': 1, 'possible -> possible': 1,
                                                      'unresolved -> unresolved': 1})
        m = em.compare(runs, 'x', 'GT', 'T')
        # C004 (not a candidate) was `none` and became `exact`: a contradiction, counted over every patient.
        self.assertEqual(m['contradictions'], 1)
        self.assertEqual(m['examples'][0]['patient_id'], 'C004')


class OracleTests(unittest.TestCase):
    def patient(self):
        return {'patient_id': 'C001', 'birth_date': '2019-03-10',
                'truth': {'events': [{'event_id': 'T1', 'day': 100}]},
                'records': [{'record_id': 'R1', 'source': 'letters', 'kind': 'note',
                             'mentions': [{'truth_event_id': 'T1'}]}]}

    def test_the_oracle_puts_each_mention_at_its_true_day_within_its_range(self):
        p = self.patient()
        lo = si.micros('2019-06-18T00:00')
        self.assertEqual(em.truth_day(p, p['records'][0], 0), (lo, lo + em.DAY - 1))
        mention = {'event_id': 'letters/R1#m0', 'kind': 'mention', 'start': [lo - 5 * em.DAY, lo + 12 * em.MINUTE]}
        other = {'event_id': 'ehr/R2', 'kind': 'condition', 'start': [0, 1]}
        out = em.oracle_patients({'patients': [p]}, [{'patient_id': 'C001', 'events': [mention, other]}])
        self.assertEqual(out[0]['events'], [{**mention, 'start': [lo, lo + 12 * em.MINUTE]}, other])
        missed = {**mention, 'start': [lo + 2 * em.DAY, lo + 3 * em.DAY]}
        out = em.oracle_patients({'patients': [p]}, [{'patient_id': 'C001', 'events': [missed]}])
        self.assertEqual(out[0]['events'][0]['start'], [lo, lo + em.DAY - 1])


class ToolTests(unittest.TestCase):
    def test_seed_lists(self):
        self.assertEqual(em.seeds_of('5001-5003,5010'), (5001, 5002, 5003, 5010))
        self.assertEqual((em.ARM_SEEDS[0], em.ARM_SEEDS[-1], em.ARM_EXTEND[-1]), (5001, 5030, 5100))
        self.assertEqual(em.RULED_DEFECTS, mt.DEFECTS)

    def test_the_defect_switch_is_restored(self):
        with em.defect('naive_years'):
            self.assertEqual(mt._DEFECT, 'naive_years')
        self.assertIsNone(mt._DEFECT)

    def test_days_covered(self):
        lo = si.micros('2020-01-01T00:00')
        self.assertEqual(em.days_covered([lo, si.micros('2020-01-31T23:59')]), 31.0)


if __name__ == '__main__':
    unittest.main()
