"""E0's measuring tool (`tools/e0_measure.py`): the contradiction rule between nested arms, the owner's counting rule,
the mention-truth oracle, the seed lists, and E0b's naming of a mention's truth event among events sharing its id (two
of E0's four misses rebuilt, the refusals). No store is built (the arms are exercised by the ruled run)."""
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

    def test_decided_claims_and_their_types(self):
        self.assertEqual(em.decided(r('exact')), ('exact',))
        self.assertEqual(em.decided(r('none', complete=True)), ('none',))
        self.assertIsNone(em.decided(r('none', complete=False)))
        self.assertEqual(em.decided(r('relaxed', certainty='exact', relaxations=['a'], complete=True)), ('relaxed', ('a',)))
        self.assertIsNone(em.decided(r('relaxed', certainty='possible', relaxations=['a'], complete=True)))
        for status in ('possible', 'unresolved', 'not_comparable', 'excluded'):
            self.assertIsNone(em.decided(r(status)))
        self.assertEqual([em.claim_type(x) for x in (r('exact'), r('none', complete=True), r('none', complete=False),
                                                     r('relaxed', certainty='possible'), r('possible'))],
                         ['exact', 'complete_none', None, 'relaxed', None])

    def test_movement_and_contradictions(self):
        runs = self.runs()
        m = em.compare(runs, 'x', 'G', 'GT')
        self.assertEqual((m['contradictions'], m['became_exact_among_candidates']), (0, 1))
        self.assertEqual((m['toward_decided'], m['away_from_decided']), (1, 0))
        self.assertEqual(m['claims_checked'], {'complete_none': 1})
        self.assertEqual(m['all_transitions']['none -> none'], 1)
        # From GT to T, C003 moves from possible to a complete none (toward decided); C001 stays exact.
        self.assertEqual(em.compare(runs, 'x', 'GT', 'T')['claims_checked'], {'complete_none': 1, 'exact': 1})
        away = {1: {**runs[1], 'arms': {'G': runs[1]['arms']['GT'], 'GT': runs[1]['arms']['G']}}}
        self.assertEqual(em.compare(away, 'x', 'G', 'GT')['away_from_decided'], 1)
        self.assertEqual(m['candidate_transitions'], {'possible -> exact': 1, 'possible -> possible': 1,
                                                      'unresolved -> unresolved': 1})
        m = em.compare(runs, 'x', 'GT', 'T')
        # C004 (not a candidate) was `none` and became `exact`: a contradiction, counted over every patient.
        self.assertEqual(m['contradictions'], 1)
        self.assertEqual(m['examples'][0]['patient_id'], 'C004')


CONCEPTS = {cid: {'label': cid, 'kind': kind, 'parents': parents, 'classes': [],
                   'codings': [{'system': 'local', 'code': cid.upper(), 'display': cid}]}
            for cid, kind, parents in (('seizure', 'phenotype', []), ('febrile_seizure', 'phenotype', ['seizure']),
                                       ('rett', 'diagnosis', []), ('tsc', 'diagnosis', []), ('height', 'measurement', []),
                                       ('weight', 'measurement', []), ('motor_stereotypy', 'phenotype', []),
                                       ('epileptic_spasm', 'phenotype', []), ('vigabatrin', 'medication', []),
                                       ('genetic_test', 'genetic', []), ('gene:MECP2', 'gene', []))}


def note(birth, record_id, day, text, mentions):
    """A clinic letter of the synthetic source on the patient's day `day`, its mentions [(text, concept, truth id)]."""
    from datetime import date, timedelta
    d = (date.fromisoformat(birth) + timedelta(days=day)).isoformat()
    spans = [{'span': [text.index(t), text.index(t) + len(t)], 'text': t, 'concept': c, 'assertion': 'present',
              'experiencer': 'patient', 'truth_event_id': tid} for t, c, tid in mentions]
    return {'record_id': record_id, 'source': 'letters', 'kind': 'note', 'text': text, 'language': 'en',
            'occurred': {'earliest': f'{d}T00:00', 'latest': f'{d}T23:59', 'precision': 'day', 'as_written': d},
            'recorded_at': f'{d}T10:00', 'available_at': f'{d}T16:00', 'mentions': spans}


def e0_cases():
    """Two of E0's four misses, rebuilt (seed 5005's C030 and seed 5024's C040): each letter confirms a diagnosis whose
    truth id the generator also gave a measurement; the letters and truth events are the generator's, cut down."""
    c030 = {'patient_id': 'C030', 'birth_date': '2021-11-19', 'records': [
        note('2021-11-19', 'C030-R0032', 890, 'Paediatric neurology review at 2 years 5 months. ', []),
        note('2021-11-19', 'C030-R0033', 981,
             'Paediatric neurology review at 2 years 8 months. Since the last review: hand-wringing stereotypies (about '
             '8 weeks ago). This confirms Rett syndrome. Single-gene test result: pathogenic MECP2 variant (c.741C>T).',
             [('hand-wringing stereotypies', 'motor_stereotypy', 'C030-T003'), ('Rett syndrome', 'rett', 'C030-T007'),
              ('pathogenic MECP2 variant', 'gene:MECP2', 'C030-T006')])],
        'truth': {'events': [
            {'event_id': 'C030-T003', 'concept': 'motor_stereotypy', 'kind': 'phenotype', 'day': 927},
            {'event_id': 'C030-T006', 'concept': 'genetic_test', 'kind': 'genetic', 'day': 878, 'gene': 'MECP2'},
            {'event_id': 'C030-T007', 'concept': 'rett', 'kind': 'diagnosis', 'day': 958},
            {'event_id': 'C030-T006', 'concept': 'weight', 'kind': 'measurement', 'day': 817},
            {'event_id': 'C030-T007', 'concept': 'height', 'kind': 'measurement', 'day': 817}]}}
    c040 = {'patient_id': 'C040', 'birth_date': '2022-12-15', 'records': [
        note('2022-12-15', 'C040-R0100', 104, 'Paediatric neurology review at 3 months. ', []),
        note('2022-12-15', 'C040-R0101', 172,
             'Paediatric neurology review at 6 months. This confirms Tuberous sclerosis complex. Also clusters of '
             'epileptic spasms (about 5 weeks ago). Vigabatrin was started. Single-gene test sent (TSC1). Current '
             'medication: vigabatrin.',
             [('Tuberous sclerosis complex', 'tsc', 'C040-T047'), ('clusters of epileptic spasms', 'epileptic_spasm',
                                                                    'C040-T004'),
              ('Vigabatrin', 'vigabatrin', 'C040-T005')])],
        'truth': {'events': [
            {'event_id': 'C040-T004', 'concept': 'epileptic_spasm', 'kind': 'phenotype', 'day': 137},
            {'event_id': 'C040-T005', 'concept': 'vigabatrin', 'kind': 'medication', 'day': 140},
            {'event_id': 'C040-T047', 'concept': 'tsc', 'kind': 'diagnosis', 'day': 131},
            {'event_id': 'C040-T047', 'concept': 'weight', 'kind': 'measurement', 'day': 13}]}}
    return {'concepts': CONCEPTS, 'patients': [c030, c040]}


class TruthEventTests(unittest.TestCase):
    def test_e0s_misses_are_named_by_concept_and_are_inside(self):
        cohort = e0_cases()
        ancestors = si.ancestor_closure(cohort['concepts'])
        c030, c040 = cohort['patients']
        self.assertEqual(em.truth_event(c030, c030['records'][1], 1, ancestors)['concept'], 'rett')
        self.assertEqual(em.truth_event(c030, c030['records'][1], 2, ancestors)['concept'], 'genetic_test')  # gene:MECP2
        self.assertEqual(em.truth_event(c040, c040['records'][1], 0, ancestors)['concept'], 'tsc')
        summary, misses, _, excluded = em.score_cohort(cohort, 'e0-cases')
        self.assertEqual((misses, excluded), ([], []))
        self.assertEqual(summary['narrowed_by_rule'], {'since_previous_letter': 3, 'weeks_ago': 2})
        # E0's reading (the last event with the id) took the measurements, which lie outside the narrowed ranges.
        by_id = {e['event_id']: e for e in c030['truth']['events']}
        self.assertEqual(by_id['C030-T007']['concept'], 'height')

    def test_refusals_by_name(self):
        cohort = e0_cases()
        ancestors = si.ancestor_closure(cohort['concepts'])
        c030 = cohort['patients'][0]
        record = c030['records'][1]
        cases = {'TRUTH_EVENT_MISSING': [e for e in c030['truth']['events'] if e['event_id'] != 'C030-T007'],
                 'TRUTH_EVENT_CONCEPT_NONE': [e for e in c030['truth']['events'] if e['concept'] != 'rett'] + [
                     {'event_id': 'C030-T007', 'concept': 'tsc', 'kind': 'diagnosis', 'day': 20}],
                 'TRUTH_EVENT_AMBIGUOUS': c030['truth']['events'] + [
                     {'event_id': 'C030-T007', 'concept': 'rett', 'kind': 'diagnosis', 'day': 20}]}
        for code, events in cases.items():
            patient = {**c030, 'truth': {'events': events}}
            with self.assertRaises(em.Refused) as raised:
                em.truth_event(patient, record, 1, ancestors)
            self.assertEqual(raised.exception.code, code)
        self.assertEqual(set(cases), set(em.REFUSALS))

    def test_an_id_naming_one_event_names_it_whatever_its_concept(self):
        cohort = e0_cases()
        c030 = cohort['patients'][0]
        patient = {**c030, 'truth': {'events': [e for e in c030['truth']['events'] if e['concept'] != 'rett']}}
        self.assertEqual(em.truth_event(patient, c030['records'][1], 1, {})['concept'], 'height')

    def test_an_ancestor_carries_a_concept(self):
        ancestors = si.ancestor_closure(CONCEPTS)
        self.assertTrue(em.carries({'concept': 'seizure'}, 'febrile_seizure', ancestors))
        self.assertFalse(em.carries({'concept': 'febrile_seizure'}, 'seizure', ancestors))
        self.assertFalse(em.carries({'concept': 'genetic_test', 'kind': 'genetic', 'gene': 'SCN1A'}, 'gene:MECP2', ancestors))

    def test_a_refused_narrowed_mention_is_listed_and_left_out(self):
        cohort = e0_cases()
        cohort['patients'][0]['truth']['events'].append(
            {'event_id': 'C030-T007', 'concept': 'rett', 'kind': 'diagnosis', 'day': 20})
        summary, misses, _, excluded = em.score_cohort(cohort, 'e0-cases')
        self.assertEqual(misses, [])
        self.assertEqual(summary['refused_TRUTH_EVENT_AMBIGUOUS'], 1)
        self.assertEqual(summary['excluded_narrowed'], 1)
        self.assertEqual([(x['event_id'], x['refusal'], [c['meets'] for c in x['candidates']]) for x in excluded],
                         [('letters/C030-R0033#m1', 'TRUTH_EVENT_AMBIGUOUS', [True, False])])


class OracleTests(unittest.TestCase):
    def patient(self):
        return {'patient_id': 'C001', 'birth_date': '2019-03-10',
                'truth': {'events': [{'event_id': 'T1', 'concept': 'seizure', 'kind': 'phenotype', 'day': 100},
                                     {'event_id': 'T1', 'concept': 'weight', 'kind': 'measurement', 'day': 300}]},
                'records': [{'record_id': 'R1', 'source': 'letters', 'kind': 'note',
                             'mentions': [{'truth_event_id': 'T1', 'concept': 'seizure'}]}]}

    def test_the_oracle_puts_each_mention_at_its_true_day_within_its_range(self):
        p = self.patient()
        cohort = {'concepts': CONCEPTS, 'patients': [p]}
        lo = si.micros('2019-06-18T00:00')
        self.assertEqual(em.truth_day(p, p['records'][0], 0, {}), (lo, lo + em.DAY - 1))
        mention = {'event_id': 'letters/R1#m0', 'kind': 'mention', 'start': [lo - 5 * em.DAY, lo + 12 * em.MINUTE]}
        other = {'event_id': 'ehr/R2', 'kind': 'condition', 'start': [0, 1]}
        out = em.oracle_patients(cohort, [{'patient_id': 'C001', 'events': [mention, other]}])
        self.assertEqual(out[0]['events'], [{**mention, 'start': [lo, lo + 12 * em.MINUTE]}, other])
        missed = {**mention, 'start': [lo + 2 * em.DAY, lo + 3 * em.DAY]}
        out = em.oracle_patients(cohort, [{'patient_id': 'C001', 'events': [missed]}])
        self.assertEqual(out[0]['events'][0]['start'], [lo, lo + em.DAY - 1])

    def test_a_refused_mention_keeps_gts_range_in_the_oracle(self):
        p = self.patient()
        p['truth']['events'].append({'event_id': 'T1', 'concept': 'seizure', 'kind': 'phenotype', 'day': 120})
        mention = {'event_id': 'letters/R1#m0', 'kind': 'mention', 'start': [5, 9]}
        refusals = {}
        out = em.oracle_patients({'concepts': CONCEPTS, 'patients': [p]}, [{'patient_id': 'C001', 'events': [mention]}],
                                 refusals)
        self.assertEqual((out[0]['events'], refusals), ([mention], {'TRUTH_EVENT_AMBIGUOUS': 1}))


class ToolTests(unittest.TestCase):
    def test_seed_lists(self):
        self.assertEqual(em.seeds_of('5001-5003,5010'), (5001, 5002, 5003, 5010))
        self.assertEqual((em.ARM_SEEDS[0], em.ARM_SEEDS[-1], em.ARM_EXTEND[-1]), (5201, 5230, 5300))
        self.assertEqual((em.SOUNDNESS_SEEDS[0], em.SOUNDNESS_SEEDS[-1], em.DEFECT_SEEDS[-1]), (5201, 5300, 5230))
        self.assertFalse(set(em.SOUNDNESS_SEEDS) & set(range(5001, 5101)))  # E0's seeds are not reused
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
