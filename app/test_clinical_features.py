"""Distinct-stream admission, leakage boundaries, exact ranking and retention."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app import clinical_features as cf
from app.recorded_similarity import compare, references
from app.test_recorded_similarity import anchor, measurement, snapshot

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples/clinical-features/authored-pack.json'


def profile(*ids):
    return {'schema': cf.SCHEMA, 'features': [{'id': i, 'weight': '1', 'scale': '1'} for i in ids]}


def source():
    result = snapshot([anchor('1', '100', 'a' * 24, [measurement('pressure-1', '58')]),
                       anchor('2', '200', 'b' * 24, [measurement('pressure-2', '60')]),
                       anchor('3', '300', 'c' * 24, [measurement('pressure-3', '62')])])
    result['source_context']['source_files'] = json.loads(EXAMPLE.read_text())['source_files']
    result['query_context_id'] = 'query-id'
    result['temporal_result']['source_mode'] = 'synthetic'
    return result


class ClinicalFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'pack.json'
        self.csv = Path(self.temp.name) / 'measurements.csv'
        self.definition = json.loads(EXAMPLE.read_text())
        self.definition['csv_path'] = 'measurements.csv'
        self.raw = (EXAMPLE.parent / 'authored-measurements.csv').read_text()
        self.save()

    def save(self):
        self.csv.write_text(self.raw)
        self.definition['csv_sha256'] = hashlib.sha256(self.csv.read_bytes()).hexdigest()
        self.path.write_text(json.dumps(self.definition))

    def bound(self, original=None):
        return cf.ClinicalFeaturePack(self.path).bind(source() if original is None else original)

    def test_distinct_variables_exact_scale_contributions_and_legacy_unchanged(self):
        original = source()
        bound = self.bound(original)
        self.assertNotIn('clinical_features', original)
        self.assertEqual(compare(original, 'a' * 24, 1), compare(bound, 'a' * 24, 1))
        selected = profile('latest_value', 'heart_rate', 'respiratory_rate')
        selected['features'][1].update(weight='2', scale='10')
        selected['features'][2].update(scale='4')
        result = compare(bound, 'a' * 24, 2, selected)
        self.assertEqual([r['patient_id'] for r in result['ranked_patients']], ['3', '2'])
        rows = result['ranked_patients']
        self.assertEqual([r['distance'] for r in rows], ['1.75', '2'])
        self.assertEqual(rows[0]['distance_exact'], {'numerator': '7', 'denominator': '4'})
        self.assertEqual([r['contribution'] for r in rows[0]['feature_contributions']], ['1', '0.5', '0.25'])
        self.assertEqual(result['feature_profile']['review']['clinical_status'], 'PENDING')
        hr = next(f for f in result['reference']['features'] if f['id'] == 'heart_rate')
        self.assertEqual(hr['evidence'][0]['patient_id'], '1')
        self.assertEqual(hr['evidence'][0]['episode_id'], '100')
        self.assertIn('file_sha256', hr['evidence'][0]['source'])

    def test_followup_and_other_episode_are_excluded(self):
        bound = self.bound()
        options = references(bound, profile('heart_rate'))
        self.assertEqual(options['anchors'][0]['features'][0]['value'], '100')
        retained = bound['clinical_features']['anchors'][0]['measurements']
        self.assertEqual({r['event_id'] for r in retained}, {'hr-1', 'rr-1'})

    def test_lower_boundary_inclusive_upper_strict_and_timestamp_ties_stable(self):
        self.raw += ('hr-boundary,1,100,3000,beats/min,2150-01-01T09:30:00,90\n'
                     'hr-before-boundary,1,100,3000,beats/min,2150-01-01T09:29:59.999999,1\n'
                     'hr-index,1,100,3000,beats/min,2150-01-01T10:00:00,2\n'
                     'a-last,1,100,3000,beats/min,2150-01-01T09:59:59.999999,88\n'
                     'z-last,1,100,3000,beats/min,2150-01-01T09:59:59.999999,99\n')
        self.save()
        bound = self.bound()
        evidence = bound['clinical_features']['anchors'][0]['measurements']
        self.assertIn('hr-boundary', {r['event_id'] for r in evidence})
        self.assertNotIn('hr-before-boundary', {r['event_id'] for r in evidence})
        self.assertNotIn('hr-index', {r['event_id'] for r in evidence})
        self.assertEqual(references(bound, profile('heart_rate'))['anchors'][0]['features'][0]['value'], '88')

    def test_variable_window_and_narrowed_query_both_apply(self):
        self.definition['variables'][0]['lookback_minutes'] = 10
        self.save()
        original = source()
        original['query_context']['query']['baseline']['max_before_start_us'] = 5 * 60000000
        bound = self.bound(original)
        options = references(bound, profile('heart_rate', 'respiratory_rate'))
        self.assertEqual(options['anchors'][0]['missing_feature_ids'], ['heart_rate', 'respiratory_rate'])
        original = source()
        original['query_context']['query']['baseline']['min_before_start_us'] = 16 * 60000000
        bound = self.bound(original)
        self.assertEqual(references(bound, profile('respiratory_rate'))['anchors'][0]['feature_status'], 'MISSING_PREINDEX_FEATURE')

    def test_nonfinite_wrong_unit_wrong_item_and_timezone_do_not_enter_features(self):
        self.raw += ('bad-unit,1,100,3000,hertz,2150-01-01T09:59:00,1\n'
                     'bad-item,1,100,9999,beats/min,2150-01-01T09:59:00,1\n'
                     'bad-nan,1,100,3000,beats/min,2150-01-01T09:59:00,NaN\n'
                     'bad-inf,1,100,3000,beats/min,2150-01-01T09:59:00,Infinity\n'
                     'bad-zone,1,100,3000,beats/min,2150-01-01T09:59:00+00:00,1\n')
        self.save()
        bound = self.bound()
        self.assertEqual(references(bound, profile('heart_rate'))['anchors'][0]['features'][0]['value'], '100')

    def test_unbounded_numeric_exponents_rejected_before_fraction_arithmetic(self):
        self.raw += 'bad-large,1,100,3000,beats/min,2150-01-01T09:59:00,1e999999999\n'
        self.save()
        with self.assertRaisesRegex(ValueError, 'plain decimals'):
            self.bound()

    def test_lazy_startup_preserves_namespace_and_metadata_when_source_unavailable(self):
        admitted = cf.ClinicalFeaturePack(self.path)
        bound = admitted.bind(source())
        self.csv.unlink()
        lazy = cf.ClinicalFeaturePack(self.path, lazy=True)
        self.assertEqual(lazy.configuration(), admitted.configuration())
        self.assertEqual(lazy.metadata(), admitted.metadata())
        compare(bound, 'a' * 24, 1, profile('heart_rate'))
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            lazy.bind(source())

    def test_wrong_patient_for_admitted_episode_rejected(self):
        self.raw += 'wrong-patient,2,100,3000,beats/min,2150-01-01T09:59:00,1\n'
        self.save()
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.bound()

    def test_missing_variable_excludes_peer_without_imputation(self):
        self.raw = self.raw.replace('rr-3,3,300,3001,breaths/min,2150-01-01T09:46:00,20\n', '')
        self.save()
        result = compare(self.bound(), 'a' * 24, 2, profile('heart_rate', 'respiratory_rate'))
        self.assertEqual([r['patient_id'] for r in result['ranked_patients']], ['2'])
        missing = result['unresolved_patients'][0]
        self.assertEqual(missing['patient_id'], '3')
        self.assertEqual(missing['feature_coverage'][0]['missing_feature_ids'], ['respiratory_rate'])

    def test_supplemental_only_reference_does_not_require_pressure(self):
        original = source()
        original['details'][0]['measurements'] = []
        result = compare(self.bound(original), 'a' * 24, 2, profile('heart_rate'))
        self.assertIsNone(result['reference']['feature'])
        self.assertEqual(result['reference']['feature_status'], 'AVAILABLE')

    def test_source_drift_blocks_new_binding_but_retained_snapshot_usable(self):
        pack = cf.ClinicalFeaturePack(self.path)
        bound = pack.bind(source())
        expected = compare(bound, 'a' * 24, 1, profile('heart_rate'))
        self.csv.write_text(self.raw + 'new,1,100,3000,beats/min,2150-01-01T09:58:00,81\n')
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            pack.bind(source())
        self.assertEqual(compare(bound, 'a' * 24, 1, profile('heart_rate')), expected)
        self.path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'CHANGED'):
            pack.check_current()

    def test_parent_source_and_source_kind_binding(self):
        changed = source()
        changed['source_context']['source_files']['chartevents'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'PARENT_SOURCE'):
            self.bound(changed)
        changed = source()
        changed['temporal_result']['source_mode'] = 'public-demo'
        with self.assertRaisesRegex(ValueError, 'kind'):
            self.bound(changed)
        changed['temporal_result']['source_mode'] = 'configured-records'
        self.bound(changed)

    def test_review_is_explicit_and_clinical_approval_cannot_be_fabricated(self):
        self.definition['review']['status'] = 'PENDING'
        self.save()
        with self.assertRaisesRegex(ValueError, 'review'):
            cf.ClinicalFeaturePack(self.path)
        self.definition['review']['status'] = 'TECHNICAL_ACCEPTED'
        self.definition['review']['clinical_status'] = 'ACCEPTED'
        self.save()
        with self.assertRaisesRegex(ValueError, 'clinical acceptance'):
            cf.ClinicalFeaturePack(self.path)

    def test_unknown_duplicate_unbound_and_rawdata_profile_fields_rejected(self):
        bound = self.bound()
        for selected in [profile('other'), profile('heart_rate', 'heart_rate'),
                         {**profile('heart_rate'), 'measurements': []},
                         {'schema': cf.SCHEMA, 'features': [{'id': 'heart_rate', 'weight': '1', 'scale': '0'}]}]:
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                references(bound, selected)
        with self.assertRaisesRegex(ValueError, 'startup-admitted'):
            references(source(), profile('heart_rate'))

    def test_retained_context_and_patient_clock_unit_time_tampering_rejected(self):
        bound = self.bound()
        for mutate in [lambda e: e['context'].update(query_context_id='other'),
                       lambda e: e['anchors'][0].update(patient_id='other'),
                       lambda e: e['anchors'].pop()]:
            changed = deepcopy(bound)
            mutate(changed['clinical_features'])
            with self.assertRaises(ValueError):
                cf.validate_snapshot(changed)
        for field, value in [('patient_id', '2'), ('episode_id', '200'), ('clock', 'utc'),
                             ('unit', 'other'), ('time', '2150-01-01T10:00:00'), ('value', 'NaN')]:
            changed = deepcopy(bound)
            row = changed['clinical_features']['anchors'][0]['measurements'][0]
            row[field] = value
            row['claim_sha256'] = cf.digest({k:v for k,v in row.items() if k != 'claim_sha256'})
            with self.subTest(field=field), self.assertRaises(ValueError):
                cf.validate_snapshot(changed)

    def test_duplicate_csv_identity_and_duplicate_item_selector_rejected(self):
        self.raw += 'hr-1,1,100,3000,beats/min,2150-01-01T09:45:00,100\n'
        self.save()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            cf.ClinicalFeaturePack(self.path)
        self.definition['variables'][1]['item_id'] = '3000'
        self.save()
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            cf.ClinicalFeaturePack(self.path)

    def test_hash_mismatch_and_missing_source_pin_rejected(self):
        self.definition['csv_sha256'] = 'b' * 64
        self.path.write_text(json.dumps(self.definition))
        with self.assertRaisesRegex(ValueError, 'hash'):
            cf.ClinicalFeaturePack(self.path)
        self.definition['source_files'] = {}
        self.save()
        with self.assertRaisesRegex(ValueError, 'parent source hashes'):
            cf.ClinicalFeaturePack(self.path)

    def test_feature_hash_binds_review_evidence_and_profile(self):
        bound = self.bound()
        first = references(bound, profile('heart_rate'))['feature_profile']['sha256']
        changed = profile('heart_rate')
        changed['features'][0]['scale'] = '2'
        self.assertNotEqual(first, references(bound, changed)['feature_profile']['sha256'])
        self.definition['review']['rationale'] += ' Additional explicit source review.'
        self.save()
        self.assertNotEqual(first, references(self.bound(), profile('heart_rate'))['feature_profile']['sha256'])


if __name__ == '__main__':
    unittest.main()
