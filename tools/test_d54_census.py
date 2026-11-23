"""D54's census tool (`tools/d54_census.py`): its comparison of a patient under main's generator and this tree's accepts
an id, a link or a dangling removal, and catches anything else. Constructed patients only; no DuckDB."""
import copy
from pathlib import Path
import sys
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
        problems, counts = dc.compare_patient(p, fixed(p))
        self.assertEqual(problems, [])
        self.assertEqual((counts['truth_ids_changed'], counts['case_ids_changed'], counts['cases_dropped']), (1, 1, 1))
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
            'a case field': lambda q: q['quality'][1].update(kind='misspelling'),
            'a case dropped that named a present record': lambda q: q['quality'].pop(0),
            'a patient field': lambda q: q.update(sex='M'),
            'a record removed': lambda q: q['records'].pop(),
        }
        for name, plant in plants.items():
            q = fixed(p)
            plant(q)
            with self.subTest(plant=name):
                self.assertNotEqual(dc.compare_patient(p, q)[0], [])

    def test_a_link_on_a_reused_id_may_name_either_event_and_no_other(self):
        p = patient()
        q = fixed(p)
        q['records'][0]['truth_event_id'] = 'C001-T002'  # the diagnosis, which also held T002 on main
        self.assertEqual(dc.compare_patient(p, q)[0], [])
        q['records'][0]['truth_event_id'] = 'C001-T001'
        self.assertNotEqual(dc.compare_patient(p, q)[0], [])

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
