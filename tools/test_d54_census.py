"""D54's census tool (`tools/d54_census.py`): its comparison of a patient under main's generator and this tree's accepts
an id, a link or a dangling removal, and catches anything else. Constructed patients only; no DuckDB."""
import copy
from pathlib import Path
import sys
import types
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import d54_census as dc  # noqa: E402


def patient():
    """Main's form: T002 names a seizure and a weight (the reuse), Q001 and Q002 share an id, Q003 names a removed record."""
    return {'patient_id': 'C001', 'sex': 'F',
            'truth': {'condition_group': 'dravet', 'events': [
                {'event_id': 'C001-T001', 'concept': 'seizure', 'kind': 'phenotype', 'day': 10},
                {'event_id': 'C001-T002', 'concept': 'dravet', 'kind': 'diagnosis', 'day': 40},
                {'event_id': 'C001-T002', 'concept': 'weight', 'kind': 'measurement', 'day': 50, 'value': 4.0}]},
            'records': [
                {'record_id': 'C001-R0001', 'source': 'ehr', 'truth_event_id': 'C001-T002', 'value': 4.0},
                {'record_id': 'C001-R0002', 'source': 'letters', 'truth_event_id': None,
                 'mentions': [{'concept': 'seizure', 'truth_event_id': 'C001-T001'}]}],
            'quality': [
                {'case_id': 'C001-Q001', 'kind': 'missing-unit', 'record_ids': ['C001-R0001'], 'truth_event_id': 'C001-T002'},
                {'case_id': 'C001-Q001', 'kind': 'free-text-only', 'record_ids': ['C001-R0002'], 'truth_event_id': 'C001-T001'},
                {'case_id': 'C001-Q003', 'kind': 'recorded-late', 'record_ids': ['C001-R0009'], 'truth_event_id': 'C001-T001'}]}


def unique(p):
    """`patient()` from main's generator with unique truth ids (the census's strict reference): each link names its event."""
    q = copy.deepcopy(p)
    for i, e in enumerate(q['truth']['events'], 1):
        e['event_id'] = f'C001-U{i:04d}'
    q['records'][0]['truth_event_id'] = 'C001-U0003'  # the weight's measurement
    q['records'][1]['mentions'][0]['truth_event_id'] = 'C001-U0001'
    for case, link in zip(q['quality'], ('C001-U0003', 'C001-U0001', 'C001-U0001')):
        case['truth_event_id'] = link
    return q


def fixed(p):
    """This tree's form of `patient()`: the weight renumbered (and its record's link), the second case renumbered, the
    dangling case dropped."""
    q = copy.deepcopy(p)
    q['truth']['events'][2]['event_id'] = 'C001-T003'
    q['records'][0]['truth_event_id'] = 'C001-T003'
    q['quality'][0]['truth_event_id'] = 'C001-T003'
    q['quality'][1]['case_id'] = 'C001-Q002'
    del q['quality'][2]
    return q


class CompareTests(unittest.TestCase):
    def test_the_fix_is_accepted_and_counted(self):
        p = patient()
        problems, counts = dc.compare_patient(p, fixed(p), unique(p))
        self.assertEqual(problems, [])
        self.assertEqual((counts['truth_ids_changed'], counts['case_ids_changed'], counts['cases_dropped']), (1, 1, 1))
        self.assertEqual(counts['links_checked'], 5)  # 2 record links, 1 mention, 2 cases
        defects = dc.defects(p)
        self.assertEqual((defects['truth'], defects['case'], defects['dangling_case_records']),
                         ({'C001-T002'}, {'C001-Q001'}, ['C001-Q003']))
        self.assertEqual(dc.defects(fixed(p))['truth'], set())

    def test_any_other_difference_is_caught(self):
        p = patient()
        plants = {
            'a truth field': lambda q: q['truth']['events'][0].update(day=11),
            'a record field': lambda q: q['records'][0].update(value=4.1),
            'a mention field': lambda q: q['records'][1]['mentions'][0].update(concept='febrile_seizure'),
            'a link to another event': lambda q: q['records'][1]['mentions'][0].update(truth_event_id='C001-T003'),
            'a unique link moved': lambda q: q['quality'][1].update(truth_event_id='C001-T003'),
            'a link removed': lambda q: q['quality'][1].update(truth_event_id=None),
            'a case field': lambda q: q['quality'][1].update(kind='misspelling'),
            'a case dropped that named a present record': lambda q: q['quality'].pop(0),
            'a patient field': lambda q: q.update(sex='M'),
            'a record removed': lambda q: q['records'].pop(),
        }
        for name, plant in plants.items():
            q = fixed(p)
            plant(q)
            with self.subTest(plant=name):
                self.assertNotEqual(dc.compare_patient(p, q, unique(p))[0], [])

    def test_a_link_on_a_reused_id_must_name_the_same_event(self):
        """Strict (the review's S1): main's T002 named the diagnosis and the weight; the measurement's record named the
        weight (main's generator with unique ids says so), so naming the diagnosis here is caught."""
        p = patient()
        q = fixed(p)
        q['records'][0]['truth_event_id'] = 'C001-T002'  # the diagnosis, which also held T002 on main
        self.assertNotEqual(dc.compare_patient(p, q, unique(p))[0], [])
        q['records'][0]['truth_event_id'] = 'C001-T003'
        self.assertEqual(dc.compare_patient(p, q, unique(p))[0], [])


class CensusTests(unittest.TestCase):
    """The census as a whole, over fake generators (the review's M1): all met only with no problem and every seed counted."""

    def run_census(self, old, new):
        fake = lambda c: types.SimpleNamespace(build=lambda seed: None, serialise=lambda children, seed: copy.deepcopy(c))
        real = dc.generators, dc.synthetic_ingest
        dc.generators = lambda base: {'main': fake(old), 'unique': fake(self.uniq), 'here': fake(new), 'digests': {}}
        # As the real converter: it reads records, not truth links or cases.
        dc.synthetic_ingest = lambda cohort: [[(r['record_id'], r.get('value')) for r in p['records']] for p in cohort['patients']]
        try:
            return dc.census([1, 2], 'base')
        finally:
            dc.generators, dc.synthetic_ingest = real

    def setUp(self):
        p = patient()
        self.old, self.new, self.uniq = ({'schema': 's', 'patients': [x]} for x in (p, fixed(p), unique(p)))

    def test_the_fix_is_all_met(self):
        r = self.run_census(self.old, self.new)
        self.assertTrue(r['all_met'], r['problems'])
        self.assertEqual((r['seeds'], r['totals']['patients_changed'], r['totals']['converter_output_identical']), (2, 2, 2))

    def test_an_extra_case_is_not_all_met(self):
        new = copy.deepcopy(self.new)
        new['patients'][0]['quality'].append({'case_id': 'C001-Q004', 'kind': 'k', 'record_ids': [], 'truth_event_id': None})
        r = self.run_census(self.old, new)
        self.assertFalse(r['all_met'])
        self.assertFalse(r['met']['2.3 every difference an id, a link naming the same event, or a dangling removal'])

    def test_other_patients_are_not_all_met_and_the_seed_still_counts(self):
        new = copy.deepcopy(self.new)
        new['patients'][0]['patient_id'] = 'C002'
        r = self.run_census(self.old, new)
        self.assertFalse(r['all_met'])
        self.assertEqual((r['seeds'], r['totals']['converter_output_identical']), (2, 2))

    def test_a_different_converter_output_is_not_all_met(self):
        new = copy.deepcopy(self.new)
        new['patients'][0]['records'][0]['value'] = 4.1
        r = self.run_census(self.old, new)
        self.assertFalse(r['all_met'])
        self.assertFalse(r['met']['2.5 converter output identical'])

    def test_rule_5_classes(self):
        c = dc.classify
        self.assertEqual(c('d52d-anchor-usefulness-report', ('per_seed', '6121', 'cohort_sha256')), 'cohort_digest')
        self.assertEqual(c('d52d-anchor-usefulness-report', ('per_seed', '6121', 'snapshot_id')), 'other')
        self.assertEqual(c('d46-window-census-report', ('artifacts', 'demos/mockups/synthetic/generate.py')), 'artifact_digest')
        self.assertEqual(c('d47-page-census-report', ('view_cost', 'main', 'median_ms_all_patients')), 'timing')
        self.assertEqual(c('d47-page-census-report', ('results', 'changed')), 'other')  # the page's census: all equal
        self.assertEqual(c('d46-window-usefulness-report', ('templates', 'sequence, stated findings @main', 'candidates')),
                         'truth_or_case_reading')
        self.assertEqual(c('d46-window-usefulness-report', ('templates', 'sequence, stated findings @here', 'candidates')), 'other')
        self.assertEqual(c('d47-page-usefulness-report', ('rule_decided', 'sequence, stated findings', 'main_types')),
                         'truth_or_case_reading')
        self.assertEqual(c('d47-page-usefulness-report', ('rule_decided', 'sequence, stated findings', 'met')), 'other')
        self.assertEqual(c('d47-page-reference-report', ('defects', 'case_truth', 'patients_moved_by_removing_truth')),
                         'truth_or_case_reading')
        self.assertEqual(c('d47-page-reference-report', ('defects', 'case_truth', 'caught')), 'other')
        self.assertEqual(c('d47-page-reference-report', ('defects', 'tau30', 'mismatches')), 'other')
        scaling = 'execution-store-scaling-report'
        self.assertEqual(c(scaling, ('sizes', '[4]', 'snapshot_id')), 'other')
        self.assertEqual(c(scaling, ('sizes', '[4]', 'queries', 'status_mix', 'none')), 'other')
        self.assertEqual(c(scaling, ('sizes', '[4]', 'queries', 'per_query', '[0]', 'counts', 'none')), 'other')
        self.assertEqual(c(scaling, ('sizes', '[4]', 'queries', 'per_query', '[0]', 'match_seconds')), 'timing')
        self.assertEqual(c(scaling, ('sizes', '[4]', 'build_seconds')), 'timing')
        self.assertEqual(c(scaling, ('sizes', '[4]', 'profile', 'match_cprofile', 'calls')), 'build_or_run_figure')
        self.assertEqual(c(scaling, ('commit', 'head')), 'build_or_run_figure')

    def test_seed_specs(self):
        self.assertEqual(dc.seeds_of('3-5,9'), [3, 4, 5, 9])
        self.assertIn(dc.PAGE_SEED, dc.seeds_of(dc.CENSUS_SEEDS))
        self.assertEqual(len(dc.seeds_of(dc.TIMING_SEEDS)), 209)


if __name__ == '__main__':
    unittest.main()
