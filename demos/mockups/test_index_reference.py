"""D46: the reference for index periods placed without the generator's truth (index_reference.py), on small cohorts.

The page's own windows are compared with this reference on the page's cohort by test_engine_parity.cjs, and on ten more
seeds by `d46_windows.cjs reference` (D46 rule 3)."""
import copy
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import index_reference as ir  # noqa: E402

GRID = 1440
CONCEPTS = {
    'seizure': {'kind': 'phenotype', 'parents': [], 'classes': [], 'codings': [{'system': 'ICD-10', 'code': 'R56.8'}]},
    'febrile_seizure': {'kind': 'phenotype', 'parents': ['seizure'], 'classes': [],
                        'codings': [{'system': 'ICD-10', 'code': 'R56.0'}, {'system': 'HPO', 'code': 'HP:0002373'}]},
    'valproate': {'kind': 'medication', 'parents': [], 'classes': ['antiseizure'], 'codings': [{'system': 'ATC', 'code': 'N03AG01'}]},
    'antiseizure': {'kind': 'drug_class', 'parents': [], 'classes': [], 'codings': []},
    'dravet': {'kind': 'diagnosis', 'parents': [], 'classes': [], 'codings': [{'system': 'ORPHA', 'code': '33069'}]},
}
CODE = {'seizure': ('ICD-10', 'R56.8'), 'febrile_seizure': ('ICD-10', 'R56.0'), 'febrile_registry': ('HPO', 'HP:0002373'),
        'valproate': ('ATC', 'N03AG01'), 'dravet': ('ORPHA', '33069')}
FIRST_FEBRILE = {'start': {'kind': 'event', 'event': 'febrile_seizure', 'which': 'first', 'n': 1, 'offset': 0, 'unit': 'month'},
                 'end': {'kind': 'span', 'amount': 24, 'unit': 'month'}}
SECOND_SEIZURE = {'start': {'kind': 'event', 'event': 'seizure', 'which': 'nth', 'n': 2, 'offset': 0, 'unit': 'month'},
                  'end': {'kind': 'span', 'amount': 1, 'unit': 'month'}}
SECOND_VALPROATE = {'start': {'kind': 'event', 'event': 'valproate', 'which': 'nth', 'n': 2, 'offset': 0, 'unit': 'month'},
                    'end': {'kind': 'span', 'amount': 1, 'unit': 'month'}}


def record(rid, kind, code, earliest, latest, precision, source='ehr', **extra):
    system, value = CODE[code]
    return {'record_id': rid, 'source': source, 'kind': kind, 'coding': {'system': system, 'code': value},
            'occurred': {'earliest': earliest, 'latest': latest, 'precision': precision},
            'recorded_at': latest, 'available_at': latest, **extra}


def note(rid, day, mentions):
    return {'record_id': rid, 'source': 'letters', 'kind': 'note',
            'occurred': {'earliest': f'{day}T00:00', 'latest': f'{day}T23:59', 'precision': 'day'},
            'recorded_at': f'{day}T12:00', 'available_at': f'{day}T12:00', 'mentions': mentions}


def cohort(records):
    return {'concepts': CONCEPTS, 'patients': [{'patient_id': 'P1', 'birth_date': '2019-01-01',
                                                'follow_up': {'last_contact': '2026-01-01'}, 'records': records}]}


def view(records, quality=()):
    data = cohort(records)
    data['patients'][0]['quality'] = list(quality)
    data = ir.strip(data, keep_quality=True)
    return ir.Patient(ir.Cohort(data), data['patients'][0]).view()


def window(records, rule, free_text='present'):
    return ir.windows(ir.strip(cohort(records)), {'r': rule}, [free_text])['r'][free_text]['P1']


def day_of(iso):
    return ir.Patient(ir.Cohort(cohort([])), cohort([])['patients'][0]).day(iso)


class ReconcileTests(unittest.TestCase):
    def test_a_registry_month_and_the_ehr_day_are_one_happening_timed_by_the_ehr(self):
        recs = [record('R1', 'registry_entry', 'febrile_registry', '2019-10-01T00:00', '2019-10-31T23:59', 'month', source='registry'),
                record('R2', 'condition', 'febrile_seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day')]
        self.assertEqual(window(recs, FIRST_FEBRILE)['start'], round(day_of('2019-10-19T00:00') * GRID))

    def test_ranges_that_do_not_meet_stay_apart(self):
        recs = [record('R1', 'condition', 'seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day'),
                record('R2', 'condition', 'seizure', '2019-10-21T00:00', '2019-10-21T23:59', 'day')]
        self.assertEqual(window(recs, SECOND_SEIZURE)['start'], round(day_of('2019-10-21T00:00') * GRID))

    def test_a_less_specific_record_of_the_same_day_is_the_same_happening(self):
        recs = [record('R1', 'condition', 'febrile_seizure', '2019-10-19T08:10', '2019-10-19T08:10', 'minute'),
                record('R2', 'condition', 'seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day')]
        self.assertIn('reason', window(recs, SECOND_SEIZURE))

    def test_a_mention_attaches_by_concept_and_wording_time(self):
        coded = record('R1', 'condition', 'seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day')
        near = note('N1', '2019-11-01', [{'concept': 'seizure', 'assertion': 'present', 'experiencer': 'patient',
                                          'time_expression': 'about 2 weeks ago'}])
        self.assertIn('reason', window([coded, near], SECOND_SEIZURE))
        far = note('N2', '2019-11-01', [{'concept': 'seizure', 'assertion': 'present', 'experiencer': 'patient',
                                         'time_expression': 'this week'}])
        self.assertNotIn('reason', window([coded, far], SECOND_SEIZURE))
        self.assertIn('reason', window([coded, far], SECOND_SEIZURE, 'off'))  # coded only: a mention-only happening does not count

    def test_a_hedged_or_negated_mention_alone_places_no_period_under_stated_findings(self):
        coded = record('R1', 'condition', 'seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day')
        hedged = note('N1', '2019-11-01', [{'concept': 'seizure', 'assertion': 'possible', 'experiencer': 'patient', 'time_expression': 'this week'},
                                           {'concept': 'seizure', 'assertion': 'absent', 'experiencer': 'patient', 'time_expression': 'this week'}])
        self.assertIn('reason', window([coded, hedged], SECOND_SEIZURE))
        self.assertNotIn('reason', window([coded, hedged], SECOND_SEIZURE, 'possible'))

    def test_dispensings_within_supply_and_45_days_are_one_course(self):
        disp = lambda rid, day: record(rid, 'dispensing', 'valproate', f'{day}T00:00', f'{day}T23:59', 'day', source='pharmacy',  # noqa: E731
                                       attributes={'supply_days': 30})
        one = [disp('D1', '2020-01-01'), disp('D2', '2020-02-10'), disp('D3', '2020-04-25')]  # 40 and 75 days apart
        self.assertIn('reason', window(one, SECOND_VALPROATE))
        two = one + [disp('D4', '2020-08-01')]  # 98 days after D3: a second course
        self.assertEqual(window(two, SECOND_VALPROATE)['start'], round(day_of('2020-08-01T00:00') * GRID))

    def test_an_order_joins_the_course_its_dispensings_opened_and_times_it(self):
        recs = [record('D1', 'dispensing', 'valproate', '2020-01-01T00:00', '2020-01-01T23:59', 'day', source='pharmacy'),
                record('O1', 'medication_order', 'valproate', '2020-01-10T00:00', '2020-01-10T23:59', 'day', status='active')]
        first = {**SECOND_VALPROATE, 'start': {**SECOND_VALPROATE['start'], 'which': 'first'}}
        self.assertEqual(window(recs, first)['start'], round(day_of('2020-01-10T00:00') * GRID))
        self.assertIn('reason', window(recs, SECOND_VALPROATE))

    def test_a_course_with_only_a_stop_has_no_start_under_coded_only(self):
        # D53: a letter dates the course under stated findings; under coded only no note is read, so it is no anchor,
        # and an item's other fields come from its records alone.
        stop = record('O1', 'medication_order', 'valproate', '2020-03-01T00:00', '2020-03-01T23:59', 'day', status='stopped')
        letter = note('N1', '2020-03-05', [{'concept': 'valproate', 'assertion': 'present', 'experiencer': 'patient',
                                            'time_expression': 'this week'}])
        first = {**SECOND_VALPROATE, 'start': {**SECOND_VALPROATE['start'], 'which': 'first'}}
        self.assertNotIn('reason', window([stop, letter], first))
        self.assertIn('reason', window([stop, letter], first, 'off'))
        self.assertIn('reason', window([stop], first))
        order = record('O0', 'medication_order', 'valproate', '2020-01-10T00:00', '2020-01-10T23:59', 'day', status='active')
        self.assertEqual(window([order, stop, letter], first, 'off')['start'], round(day_of('2020-01-10T00:00') * GRID))

    def test_the_truth_link_is_never_read(self):
        recs = [record('R1', 'condition', 'seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day', truth_event_id='T1'),
                record('R2', 'condition', 'seizure', '2019-12-01T00:00', '2019-12-01T23:59', 'day', truth_event_id='T1')]
        data = cohort(recs)
        data['patients'][0]['truth'] = {'events': []}
        data['patients'][0]['quality'] = [{'truth_event_id': 'T1'}]
        stripped = ir.strip(data)
        self.assertNotIn('truth', stripped['patients'][0])
        self.assertTrue(all('truth_event_id' not in r for r in stripped['patients'][0]['records']))
        self.assertEqual(data['patients'][0]['records'][0]['truth_event_id'], 'T1')  # the input is not changed
        self.assertNotIn('reason', ir.windows(stripped, {'r': SECOND_SEIZURE}, ['present'])['r']['present']['P1'])

    def test_compare_reports_a_moved_window(self):
        recs = [record('R1', 'condition', 'seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day'),
                record('R2', 'condition', 'seizure', '2019-12-01T00:00', '2019-12-01T23:59', 'day')]
        want = window(recs, SECOND_SEIZURE)
        page = {'rules': {'r': SECOND_SEIZURE}, 'windows': {'r': {'present': {'P1': copy.deepcopy(want)}}}}
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            c, w = pathlib.Path(d, 'c.json'), pathlib.Path(d, 'w.json')
            c.write_text(json.dumps(cohort(recs)))
            w.write_text(json.dumps(page))
            self.assertEqual(ir.compare(c, w)['mismatches'], 0)
            page['windows']['r']['present']['P1']['start'] += 1
            w.write_text(json.dumps(page))
            self.assertEqual(ir.compare(c, w)['mismatches'], 1)


class PageViewTests(unittest.TestCase):
    """D47: the page's whole view, with data-quality cases attached per record."""

    def test_a_case_attaches_to_each_event_holding_its_records_and_never_through_its_truth_link(self):
        recs = [record('R1', 'condition', 'seizure', '2019-10-19T00:00', '2019-10-19T23:59', 'day'),
                record('R2', 'condition', 'seizure', '2019-12-01T00:00', '2019-12-01T23:59', 'day'),
                note('N1', '2019-12-10', [{'concept': 'seizure', 'assertion': 'present', 'experiencer': 'patient', 'time_expression': 'this week'}])]
        quality = [{'case_id': 'Q1', 'kind': 'duplicate-record', 'record_ids': ['R1', 'R2'], 'truth_event_id': 'T1'},
                   {'case_id': 'Q2', 'kind': 'free-text-only', 'record_ids': ['N1'], 'truth_event_id': 'T2'},
                   {'case_id': 'Q3', 'kind': 'missing-measurement', 'record_ids': [], 'truth_event_id': 'T3'}]
        v = view(recs, quality)
        coded = [i for i in v['items'] if i['records']]
        self.assertEqual([i['cases'] for i in coded], [['Q1'], ['Q1']])
        self.assertEqual([i['cases'] for i in v['items'] if not i['records']], [[]])  # the mention's own event: the case stays with the letter
        self.assertEqual(v['letters'], {'N1': ['Q2']})
        self.assertEqual(v['unattached'], ['Q3'])

    def test_a_record_the_rule_cannot_place_is_listed_with_its_case(self):
        local = {'record_id': 'R9', 'source': 'ehr', 'kind': 'condition', 'coding': {'system': 'local', 'code': 'OBS-X'},
                 'occurred': {'earliest': '2020-01-01T00:00', 'latest': '2020-01-01T23:59', 'precision': 'day'}}
        v = view([local], [{'case_id': 'Q9', 'kind': 'local-code', 'record_ids': ['R9']}])
        self.assertEqual(v['items'], [])
        self.assertEqual(v['unplaced'], {'R9': ['Q9']})
        self.assertEqual(v['unattached'], [])

    def test_each_measured_record_is_its_own_series_reading(self):
        recs = [{'record_id': f'M{i}', 'source': 'ehr', 'kind': 'measurement', 'coding': {'system': 'LOINC', 'code': '29463-7'},
                 'occurred': {'earliest': '2020-01-0%dT10:00' % i, 'latest': '2020-01-0%dT10:00' % i, 'precision': 'minute'},
                 'value': 10 + i, 'unit': 'kg'} for i in (1, 2)]
        cohort_concepts = dict(CONCEPTS, weight={'kind': 'measurement', 'parents': [], 'classes': [], 'codings': [{'system': 'LOINC', 'code': '29463-7'}]})
        data = {'concepts': cohort_concepts, 'patients': [{'patient_id': 'P1', 'birth_date': '2019-01-01', 'follow_up': {'last_contact': '2026-01-01'},
                                                           'records': recs, 'quality': []}]}
        v = ir.Patient(ir.Cohort(data), data['patients'][0]).view()
        self.assertEqual(v['series']['weight'], ['M1', 'M2'])
        self.assertEqual(v['items'], [])

    def test_strip_keeps_the_cases_without_their_truth_links(self):
        data = cohort([])
        data['patients'][0]['quality'] = [{'case_id': 'Q1', 'record_ids': [], 'truth_event_id': 'T1'}]
        self.assertEqual(ir.strip(data, keep_quality=True)['patients'][0]['quality'], [{'case_id': 'Q1', 'record_ids': []}])
        self.assertNotIn('quality', ir.strip(data)['patients'][0])


if __name__ == '__main__':
    unittest.main()
