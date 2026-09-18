"""Pre-index ranking boundaries and actual recorded-service integration."""
from copy import deepcopy
import time
import unittest

from app.recorded_journey import RecordedJourneyWorkspace
from app.recorded_similarity import compare_snapshot, references, _distance
from decimal import Decimal


def measurement(event, value, *, time='2150-01-01T09:55:00', item='2000', unit='mmHg'):
    return {'event_id': event, 'value': value, 'time': time, 'item_id': item, 'unit': unit,
            'source': {'table': 'chartevents', 'record_number': 1},
            'claim_id': 'claim-' + event, 'claim_sha256': 'retained-source-digest'}


def anchor(patient, episode, token, measurements, status='NO_SELECTED_MATCH'):
    return {'anchor': {'patient_id': patient, 'episode_id': episode, 'token': token,
                       'start': '2150-01-01T10:00:00', 'end': '2150-01-01T11:00:00',
                       'status': status}, 'measurements': measurements}


def snapshot(details, extra_roster=()):
    roster = [{'patient_id': detail['anchor']['patient_id'], 'episode_id': detail['anchor']['episode_id'],
               'status': detail['anchor']['status']} for detail in details]
    return {'profile': 'literal', 'job_id': 'a' * 32, 'source_context': {'source_files': {'chartevents': 'captured'}},
            'query_context': {'query': {'baseline': {'item_ids': ['2000'], 'unit_lexical': 'mmHg',
                                                   'max_before_start_us': 30 * 60000000,
                                                   'value_lexical': '65'}}},
            'details': details, 'temporal_result': {'roster': roster + list(extra_roster),
                                                   'metrics': {'patients': len(roster)},
                                                   'anchors': [deepcopy(detail['anchor']) for detail in details]}}


class RecordedSimilarityUnitTests(unittest.TestCase):
    def test_latest_finite_preindex_same_item_unit_and_stable_event_tie(self):
        records = [measurement('z', '59'), measurement('a', '61'),
                   measurement('old', '10', time='2150-01-01T09:29:59'),
                   measurement('at-index', '12', time='2150-01-01T10:00:00'),
                   measurement('followup', '13', time='2150-01-01T10:01:00'),
                   measurement('wrong-unit', '14', time='2150-01-01T09:59:00', unit='kPa'),
                   measurement('missing-unit', '14', time='2150-01-01T09:59:00', unit=None),
                   measurement('wrong-item', '15', time='2150-01-01T09:59:00', item='other'),
                   measurement('nan', 'NaN', time='2150-01-01T09:59:00'),
                   measurement('infinite', 'Infinity', time='2150-01-01T09:59:00'),
                   measurement('invalid', '<5', time='2150-01-01T09:59:00')]
        source = snapshot([anchor('1', '10', 'a' * 24, records)])
        result = references(source)
        self.assertEqual(result['anchors'][0]['feature']['event_id'], 'a')
        self.assertEqual(result['anchors'][0]['feature']['value'], '61')
        records.reverse()
        self.assertEqual(references(source), result)
        records.append(measurement('latest', '67', time='2150-01-01T09:59:59.999999'))
        self.assertEqual(references(source)['anchors'][0]['feature']['event_id'], 'latest')

    def test_baseline_lower_boundary_is_inclusive_and_selection_ignores_eligibility(self):
        value = measurement('boundary', '99', time='2150-01-01T09:30:00')
        value.update(eligible_baseline=False, selected_followup=False,
                     baseline_exclusions=['NOT_BELOW_THRESHOLD'])
        result = references(snapshot([anchor('1', '10', 'a' * 24, [value])]))
        feature = result['anchors'][0]['feature']
        self.assertEqual(feature['value'], '99')
        self.assertNotIn('eligible_baseline', feature)
        self.assertNotIn('baseline_exclusions', feature)

    def test_all_reference_stays_excluded_patient_best_anchor_and_ties(self):
        source = snapshot([
            anchor('reference', '10', 'a' * 24, [measurement('ref', '60')]),
            anchor('reference', '11', 'b' * 24, [measurement('ref-other-stay', '70')]),
            anchor('2', '20', 'c' * 24, [measurement('far', '80')], status='MATCH'),
            anchor('2', '21', 'd' * 24, [measurement('near', '62')]),
            anchor('2', '21', 'e' * 24, [measurement('equal-anchor', '58')]),
            anchor('3', '30', 'f' * 24, [measurement('tie-patient', '58')]),
        ])
        before = deepcopy(source)
        result = compare_snapshot(source, 'a' * 24, 1)
        self.assertEqual(result['eligible_patient_ids'], ['2', '3'])
        self.assertEqual([row['patient_id'] for row in result['ranked_patients']], ['2', '3'])
        self.assertEqual(result['ranked_patients'][0]['selected_anchor']['token'], 'd' * 24)
        self.assertEqual(result['ranked_patients'][0]['temporal_status'], 'MATCH')
        self.assertEqual(result['ranked_patients'][0]['selected_anchor']['temporal_status'], 'NO_SELECTED_MATCH')
        self.assertEqual([row['distance'] for row in result['ranked_patients']], ['2', '2'])
        self.assertEqual([row['highlighted'] for row in result['ranked_patients']], [True, False])
        self.assertEqual(source, before)
        self.assertEqual(result['temporal_result'], source['temporal_result'])
        self.assertEqual(len(result['temporal_result']['anchors']), 6)

    def test_unresolved_roster_and_missing_anchor_feature_remain_explicit(self):
        source = snapshot([anchor('1', '10', 'a' * 24, [measurement('ref', '60')]),
                           anchor('2', '20', 'b' * 24, [])],
                          [{'patient_id': '3', 'episode_id': '30', 'status': 'NO_ADMITTED_ANCHOR'}])
        result = compare_snapshot(source, 'a' * 24, 1)
        self.assertEqual(result['eligible_patient_ids'], ['2', '3'])
        self.assertEqual(result['ranked_patients'], [])
        self.assertEqual([row['reason'] for row in result['unresolved_patients']],
                         ['MISSING_PREINDEX_FEATURE', 'NO_ADMITTED_ANCHOR'])
        with self.assertRaisesRegex(ValueError, 'no finite'):
            compare_snapshot(source, 'b' * 24, 1)

    def test_exact_decimal_difference_and_zero_format(self):
        source = snapshot([anchor('1', '10', 'a' * 24, [measurement('ref', '60.100000000000000000000000000001')]),
                           anchor('2', '20', 'b' * 24, [measurement('peer', '60.1')]),
                           anchor('3', '30', 'c' * 24, [measurement('equal', '60.100000000000000000000000000001')])])
        rows = compare_snapshot(source, 'a' * 24, 20)['ranked_patients']
        self.assertEqual([row['distance'] for row in rows], ['0', '0.000000000000000000000000000001'])

    def test_topk_only_changes_highlights_and_ephemeral_job_id_optional(self):
        source = snapshot([anchor(str(i), str(i * 10), str(i) * 24, [measurement(str(i), str(60 + i))])
                           for i in range(1, 4)])
        first = compare_snapshot(source, '1' * 24, 1)
        second = compare_snapshot(source, '1' * 24, 20)
        for row in first['ranked_patients']:
            row['highlighted'] = True
        first['top_k'] = 20
        self.assertEqual(first, second)
        del source['job_id']
        self.assertNotIn('job_id', compare_snapshot(source, '1' * 24, 1))

    def test_decimal_precision_covers_both_extreme_exponents(self):
        self.assertEqual(_distance('1' + '0' * 100, '0.' + '0' * 99 + '1'),
                         Decimal('9' * 100 + '.' + '9' * 100))

    def test_changed_followup_values_and_threshold_do_not_enter_distance(self):
        source = snapshot([
            anchor('1', '10', 'a' * 24, [measurement('ref', '60'),
                                       measurement('later-ref', '200', time='2150-01-01T10:15:00')]),
            anchor('2', '20', 'b' * 24, [measurement('peer', '58'),
                                       measurement('later-peer', '1', time='2150-01-01T10:15:00')])])
        first = compare_snapshot(source, 'a' * 24, 1)
        source['details'][0]['measurements'][1]['value'] = '0'
        source['details'][1]['measurements'][1]['value'] = '300'
        source['query_context']['query']['baseline']['value_lexical'] = '1'
        second = compare_snapshot(source, 'a' * 24, 1)
        self.assertEqual(first['reference'], second['reference'])
        self.assertEqual(first['ranked_patients'], second['ranked_patients'])

    def test_invalid_reference_topk_and_feature_scope_are_rejected(self):
        source = snapshot([anchor('1', '10', 'a' * 24, [measurement('ref', '60')])])
        for value in (0, 21, True, 1.0, '1', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                compare_snapshot(source, 'a' * 24, value)
        with self.assertRaises(ValueError):
            compare_snapshot(source, 'b' * 24, 1)
        source['query_context']['query']['baseline']['unit_lexical'] = 'kPa'
        with self.assertRaises(ValueError):
            references(source)


class RecordedSimilarityEngineTests(unittest.TestCase):
    def setUp(self):
        self.workspace = RecordedJourneyWorkspace()
        self.addCleanup(self.workspace.close)

    def job(self, *, profile='literal', threshold='65', followup=120):
        job = self.workspace.start({'profile': profile, 'stratum': 'synthetic',
                                    'controls': {'threshold': threshold, 'baseline_minutes': 30,
                                                 'followup_minutes': followup}})
        deadline = time.monotonic() + 30
        while job['status'] == 'RUNNING' and time.monotonic() < deadline:
            time.sleep(.01)
            job = self.workspace.get(profile, job['id'])
        self.assertEqual(job['status'], 'COMPLETED', job.get('error'))
        return job

    def test_literal_and_reviewed_rank_same_source_values_with_source_evidence(self):
        for profile in ('literal', 'reviewed'):
            with self.subTest(profile=profile):
                job = self.job(profile=profile)
                options = self.workspace.references(profile, job['id'])
                reference = next(row for row in options['anchors'] if row['patient_id'] == '2')
                result = self.workspace.compare({'profile': profile, 'job_id': job['id'],
                                                  'reference_token': reference['token'], 'top_k': 1})
                self.assertEqual(reference['feature']['value'], '60')
                self.assertEqual([row['patient_id'] for row in result['ranked_patients']], ['1', '3'])
                self.assertEqual([row['distance'] for row in result['ranked_patients']], ['2', '2'])
                self.assertEqual(result['reference']['feature']['source']['table'], 'chartevents')
                self.assertIn('claim_sha256', result['reference']['feature'])
                self.assertEqual(result['temporal_result']['metrics'], job['summary']['metrics'])
                if profile == 'reviewed':
                    self.assertIn('measurement_mapping', result['source_context'])

    def test_threshold_and_followup_do_not_select_reference_or_change_distances(self):
        results = []
        for threshold, followup in [('65', 120), ('1', 0), ('300', 120)]:
            job = self.job(threshold=threshold, followup=followup)
            options = self.workspace.references('literal', job['id'])
            reference = next(row for row in options['anchors'] if row['patient_id'] == '2')
            result = self.workspace.compare({'profile': 'literal', 'job_id': job['id'],
                                              'reference_token': reference['token'], 'top_k': 1})
            results.append([(row['patient_id'], row['distance'], row['selected_anchor']['feature'])
                            for row in result['ranked_patients']])
            self.assertEqual(result['reference']['feature']['value'], '60')
            if threshold == '1':
                self.assertEqual(result['temporal_result']['metrics']['patients'], 0)
                self.assertEqual(result['ranked_patients'][0]['temporal_status'], 'NO_SELECTED_MATCH')
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])

    def test_snapshot_is_detached_and_strict_comparison_request(self):
        job = self.job()
        captured = self.workspace.snapshot('literal', job['id'])
        captured['source_context']['source_files'].clear()
        captured['details'][0]['measurements'].clear()
        again = self.workspace.snapshot('literal', job['id'])
        self.assertTrue(again['source_context']['source_files'])
        self.assertTrue(again['details'][0]['measurements'])
        self.assertEqual(again['source_mode'], 'synthetic')
        self.assertNotIn('elapsed_seconds', again['temporal_result'])
        request = {'profile': 'literal', 'job_id': job['id'],
                   'reference_token': again['details'][0]['anchor']['token'], 'top_k': 1}
        for bad in (None, {}, {**request, 'item_id': 'other'}, {**request, 'reference_token': '../record'}):
            with self.subTest(request=bad), self.assertRaises(ValueError):
                self.workspace.compare(bad)
        with self.assertRaises(KeyError):
            self.workspace.references('reviewed', job['id'])


if __name__ == '__main__':
    unittest.main()
