"""Exact ranking and leakage boundaries of explicit recorded feature profiles."""
from copy import deepcopy
from fractions import Fraction
import unittest

from app import feature_profiles as fp
from app.recorded_similarity import compare, references
from app.test_recorded_similarity import anchor, measurement, snapshot


def profile(*entries):
    return {'schema': fp.SCHEMA, 'features': [
        {'id': entry, 'weight': '1', 'scale': '1'} if isinstance(entry, str) else entry
        for entry in entries]}


def history(prefix, first, last, *, early='2150-01-01T09:40:00', late='2150-01-01T09:55:00'):
    return [measurement(prefix + '-first', first, time=early), measurement(prefix + '-last', last, time=late)]


def source():
    return snapshot([anchor('1', '10', 'a' * 24, history('ref', '58', '60')),
                     anchor('2', '20', 'b' * 24, history('peer', '55', '59')),
                     anchor('3', '30', 'c' * 24, history('other', '61', '62'))])


class FeatureProfileTests(unittest.TestCase):
    def test_opt_in_single_latest_preserves_ranking_and_default_contract(self):
        captured = source()
        legacy = compare(captured, 'a' * 24, 1)
        explicit = compare(captured, 'a' * 24, 1, profile('latest_value'))
        self.assertNotIn('features', legacy['reference'])
        self.assertNotIn('sha256', legacy['feature_profile'])
        self.assertEqual([(r['patient_id'], r['distance']) for r in legacy['ranked_patients']],
                         [(r['patient_id'], r['distance']) for r in explicit['ranked_patients']])
        self.assertEqual(legacy, compare(captured, 'a' * 24, 1, None))
        self.assertEqual(legacy['temporal_result'], explicit['temporal_result'])

    def test_all_features_values_evidence_and_normalized_contributions(self):
        requested = profile('latest_value', 'value_change', 'measurement_count', 'latest_recency')
        requested['features'][0].update(weight='2', scale='10')
        captured = source()
        before = deepcopy(captured)
        result = compare(captured, 'a' * 24, 1, requested)
        features = result['reference']['features']
        self.assertEqual([f['value'] for f in features], ['60', '2', '2', '5'])
        self.assertEqual(features[1]['evidence'][0]['event_id'], 'ref-first')
        self.assertEqual(features[1]['evidence'][1]['event_id'], 'ref-last')
        self.assertEqual(features[2]['unit'], 'count')
        self.assertEqual(features[3]['unit'], 'minutes')
        rows = {r['patient_id']: r for r in result['ranked_patients']}
        self.assertEqual(rows['2']['distance'], '0.44')  # (2*1/10 + 2)/5
        self.assertEqual(rows['3']['distance'], '0.28')  # (2*2/10 + 1)/5
        self.assertEqual(rows['2']['distance_exact'], {'numerator': '11', 'denominator': '25'})
        self.assertEqual(sum(fp.restore(c['contribution_exact']) for c in rows['2']['feature_contributions']), Fraction(11, 25))
        self.assertEqual(captured, before)

    def test_missing_change_requires_distinct_times_and_missing_never_zero(self):
        captured = snapshot([anchor('1', '10', 'a' * 24, history('ref', '58', '60')),
                             anchor('2', '20', 'b' * 24, [measurement('one', '60'), measurement('two', '61')]),
                             anchor('3', '30', 'c' * 24, [])],
                            [{'patient_id': '4', 'episode_id': '40', 'status': 'NO_ADMITTED_ANCHOR'}])
        requested = profile('latest_value', 'value_change', 'measurement_count')
        result = compare(captured, 'a' * 24, 1, requested)
        self.assertEqual(result['ranked_patients'], [])
        partial = result['unresolved_patients'][0]['feature_coverage'][0]
        self.assertEqual(partial['coverage'], {'available': 2, 'requested': 3, 'complete': False})
        self.assertEqual(partial['missing_feature_ids'], ['value_change'])
        self.assertEqual(partial['features'][1]['reason'], 'NEEDS_TWO_DISTINCT_PREINDEX_TIMES')
        self.assertEqual(result['unresolved_patients'][1]['feature_coverage'][0]['features'][2]['value'], None)
        self.assertEqual(result['unresolved_patients'][2]['reason'], 'NO_ADMITTED_ANCHOR')
        with self.assertRaisesRegex(ValueError, 'every requested'):
            compare(captured, 'b' * 24, 1, requested)

    def test_no_future_threshold_status_or_segment_end_leakage(self):
        captured = source()
        requested = profile('latest_value', 'value_change', 'measurement_count', 'latest_recency')
        first = compare(captured, 'a' * 24, 1, requested)
        captured['query_context']['query']['baseline']['value_lexical'] = '999'
        for detail in captured['details']:
            detail['anchor']['end'] = '2151-01-01T10:00:00'
            detail['anchor']['status'] = 'MATCH'
            detail['measurements'].extend([
                measurement('future', '-99999', time='2150-01-01T10:01:00'),
                measurement('at-index', '99999', time='2150-01-01T10:00:00'),
                measurement('too-early', '99999', time='2150-01-01T09:29:59.999999'),
                measurement('wrong-unit', '1', unit='kPa'),
                measurement('wrong-item', '1', item='different'),
                measurement('bad', 'NaN')])
        second = compare(captured, 'a' * 24, 1, requested)
        self.assertEqual(first['reference']['features'], second['reference']['features'])
        for before, after in zip(first['ranked_patients'], second['ranked_patients']):
            self.assertEqual(before['patient_id'], after['patient_id'])
            self.assertEqual(before['distance_exact'], after['distance_exact'])
            self.assertEqual(before['feature_contributions'], after['feature_contributions'])

    def test_profile_definition_hash_canonical_and_binds_source_scope(self):
        first = profile({'id': 'value_change', 'weight': '1.000', 'scale': '2.00'}, 'latest_value')
        second = profile('latest_value', {'id': 'value_change', 'weight': '1', 'scale': '2'})
        captured = source()
        result = references(captured, first)['feature_profile']
        self.assertEqual(result, references(captured, second)['feature_profile'])
        self.assertEqual(len(result['sha256']), 64)
        changed = deepcopy(second)
        changed['features'][0]['scale'] = '2'
        self.assertNotEqual(result['sha256'], references(captured, changed)['feature_profile']['sha256'])
        captured['query_context']['query']['baseline']['max_before_start_us'] += 1
        self.assertNotEqual(result['sha256'], references(captured, second)['feature_profile']['sha256'])

    def test_validation_rejects_units_unknown_features_nonpositive_and_unsafe_numbers(self):
        for value in ('0', '-1', '1e2', '01', '1.0000001', '1000001', 'NaN', 1, True, None):
            for field in ('weight', 'scale'):
                request = profile('latest_value')
                request['features'][0][field] = value
                with self.subTest(value=value, field=field), self.assertRaises(ValueError):
                    references(source(), request)
        for request in ({}, profile(), profile('unknown'), profile('latest_value', 'latest_value'),
                        {**profile('latest_value'), 'missing_policy': 'zero'},
                        profile({'id': 'latest_value', 'weight': '1', 'scale': '1', 'unit': 'kPa'})):
            with self.subTest(request=request), self.assertRaises(ValueError):
                references(source(), request)
        references(source(), profile({'id': 'latest_value', 'weight': '0.000001', 'scale': '1000000'}))

    def test_recency_exact_microseconds_and_ranking_before_display_rounding(self):
        captured = snapshot([
            anchor('ref', 'r', 'a' * 24, [measurement('ref', '0')]),
            anchor('z', 'z', 'b' * 24, [measurement('z', '0.0000000000001')]),
            anchor('a', 'a', 'c' * 24, [measurement('a', '0.0000000000002')])])
        rows = compare(captured, 'a' * 24, 1, profile('latest_value'))['ranked_patients']
        self.assertEqual([r['distance'] for r in rows], ['0', '0'])
        self.assertEqual([r['patient_id'] for r in rows], ['z', 'a'])
        self.assertNotEqual(rows[0]['distance_exact'], rows[1]['distance_exact'])
        captured['details'][0]['measurements'][0]['time'] = '2150-01-01T09:59:59.999999'
        feature = next(a for a in references(captured, profile('latest_recency'))['anchors'] if a['patient_id'] == 'ref')['features'][0]
        self.assertEqual(feature['value_exact'], {'numerator': '1', 'denominator': '60000000'})

    def test_pattern_authored_baseline_upper_bound_is_respected_and_hashed(self):
        captured = source()
        requested = profile('latest_value', 'value_change')
        original_hash = references(captured, requested)['feature_profile']['sha256']
        captured['query_context']['query']['baseline']['min_before_start_us'] = 15 * 60000000
        legacy = references(captured)
        self.assertEqual(legacy['anchors'][0]['feature']['event_id'], 'ref-first')
        result = references(captured, requested)
        self.assertNotEqual(original_hash, result['feature_profile']['sha256'])
        self.assertEqual(result['anchors'][0]['features'][0]['value'], '58')
        self.assertEqual(result['anchors'][0]['features'][1]['status'], 'MISSING')

    def test_half_even_rounding_and_negative_change(self):
        self.assertEqual(fp.display(Fraction(1, 3)), '0.333333333333')
        self.assertEqual(fp.display(Fraction(1, 2 * 10 ** 12)), '0')
        self.assertEqual(fp.display(Fraction(3, 2 * 10 ** 12)), '0.000000000002')
        captured = source()
        captured['details'][0]['measurements'][0]['value'] = '65'
        result = references(captured, profile('value_change'))
        self.assertEqual(result['anchors'][0]['features'][0]['value'], '-5')

    def test_patient_wide_exclusion_closest_anchor_and_topk_highlights(self):
        captured = source()
        captured['details'].append(anchor('1', '11', 'd' * 24, history('ref-other', '1', '1')))
        captured['temporal_result']['roster'].append({'patient_id': '1', 'episode_id': '11', 'status': 'MATCH'})
        captured['details'].append(anchor('2', '21', 'e' * 24, history('near', '58', '60')))
        requested = profile('latest_value', 'value_change')
        result = compare(captured, 'a' * 24, 1, requested)
        self.assertEqual(result['eligible_patient_ids'], ['2', '3'])
        self.assertEqual(result['ranked_patients'][0]['selected_anchor']['token'], 'e' * 24)
        self.assertEqual(result['ranked_patients'][0]['distance'], '0')
        self.assertEqual([r['highlighted'] for r in result['ranked_patients']], [True, False])
        larger = compare(captured, 'a' * 24, 2, requested)
        result['top_k'] = 2
        result['ranked_patients'][1]['highlighted'] = True
        self.assertEqual(result, larger)


if __name__ == '__main__':
    unittest.main()
