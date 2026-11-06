"""The concept-measure retrieval benchmark: its metrics, its protocol and its committed report."""
from fractions import Fraction
from pathlib import Path
import unittest

from tools import evaluate_concept_measures as bench

ROOT = Path(__file__).resolve().parents[1]
REPORT = 'verification/concept-measure-benchmark.json'


def concept(kind, parents=(), classes=()):
    return {'kind': kind, 'parents': list(parents), 'classes': list(classes),
            'codings': [{'system': 'T', 'code': f'{kind}-{len(parents)}-{len(classes)}'}]}


def patient(pid, group, coded=(), mentioned=(), negated=()):
    records = [{'record_id': f'{pid}-{i}', 'kind': 'condition', 'coding': {'system': 'T', 'code': code}}
               for i, code in enumerate(coded)]
    records.append({'record_id': f'{pid}-note', 'kind': 'note', 'mentions':
                    [{'concept': c, 'assertion': 'present', 'experiencer': 'patient'} for c in mentioned] +
                    [{'concept': c, 'assertion': 'absent', 'experiencer': 'patient'} for c in negated]})
    return {'patient_id': pid, 'truth': {'condition_group': group}, 'records': records}


class MetricTests(unittest.TestCase):
    def test_metrics_on_a_hand_checked_ranking(self):
        # Relevant at ranks 1 and 3 of six: AP = (1/1 + 2/3) / 2.
        m = bench.metrics(['a', 'x', 'b', 'y', 'z', 'w'], {'a', 'b'})
        self.assertEqual(m['average_precision'], Fraction(5, 6))
        self.assertEqual(m['precision_at_5'], Fraction(2, 5))
        self.assertEqual(m['recall_at_10'], 1)
        self.assertEqual(m['reciprocal_rank'], 1)
        self.assertEqual(bench.metrics(['x', 'a'], {'a'})['reciprocal_rank'], Fraction(1, 2))


class ProtocolTests(unittest.TestCase):
    def cohort(self):
        concepts = {'seizure': concept('phenotype'), 'febrile': concept('phenotype', ['seizure']),
                    'ataxia': concept('phenotype'), 'dravet': concept('diagnosis')}
        concepts['febrile']['codings'] = [{'system': 'T', 'code': 'F'}]
        concepts['ataxia']['codings'] = [{'system': 'T', 'code': 'A'}]
        concepts['dravet']['codings'] = [{'system': 'T', 'code': 'D'}]
        patients = [patient('P1', 'g1', coded=['F', 'D']), patient('P2', 'g1', mentioned=['febrile']),
                    patient('P3', 'g2', coded=['A'], negated=['febrile']), patient('P4', 'g2', mentioned=['ataxia'])]
        return {'concepts': concepts, 'patients': patients}

    def test_only_recorded_findings_count_and_diagnoses_stay_hidden(self):
        c = self.cohort()
        codes = {(x['system'], x['code']): cid for cid, v in c['concepts'].items() for x in v['codings']}
        kinds = ('phenotype',)
        self.assertEqual(bench.recorded_concepts(c['patients'][0], c['concepts'], codes, kinds), {'febrile'})
        # A finding a letter denies is not part of the profile.
        self.assertEqual(bench.recorded_concepts(c['patients'][2], c['concepts'], codes, kinds), {'ataxia'})

    def test_every_measure_separates_two_clean_groups(self):
        result = bench.evaluate_cohort(self.cohort(), axes={'findings': ('phenotype',)})['findings']
        self.assertEqual(result['queries'], 4)
        for measure, scores in result['measures'].items():
            with self.subTest(measure=measure):
                self.assertEqual(scores['average_precision'], 1)
        self.assertEqual(result['chance']['precision_at_5'], Fraction(1, 3))


class ReportTests(unittest.TestCase):
    def test_the_committed_report_is_reproduced(self):
        """Regenerates the cohort when absent, as on a fresh checkout."""
        self.assertEqual(bench.main(['--check', str(ROOT / REPORT)]), 0)


if __name__ == '__main__':
    unittest.main()
