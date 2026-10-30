"""Actual HTTP acceptance of separately admitted clinical variables and replay."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib.error import HTTPError

from app import server
from app import test_completed_workflow_http as http_acceptance
from app.temporal import digest
from app.temporal_replay import verify

ROOT = Path(__file__).resolve().parents[1]
BASE = '/api/journey/recorded'
PROFILE = {'schema': 'recorded-clinical-features-1', 'features': [
    {'id': 'latest_value', 'weight': '1', 'scale': '10'},
    {'id': 'heart_rate', 'weight': '2', 'scale': '20'},
    {'id': 'respiratory_rate', 'weight': '1', 'scale': '4'},
]}


class ResearchReleaseTests(unittest.TestCase):
    running = http_acceptance.CompletedWorkflowHTTPTests.running
    request = http_acceptance.CompletedWorkflowHTTPTests.request
    completed = http_acceptance.CompletedWorkflowHTTPTests.completed
    path = staticmethod(http_acceptance.CompletedWorkflowHTTPTests.path)

    def test_distinct_variable_http_replay_and_retained_evidence_after_source_drift(self):
        with tempfile.TemporaryDirectory() as folder:
            pack_dir = Path(folder) / 'pack'
            shutil.copytree(ROOT / 'examples/clinical-features', pack_dir)
            pack = pack_dir / 'authored-pack.json'
            options = {'clinical_features_path': pack, 'state_dir': Path(folder) / 'state'}
            with self.running(**options) as (_, url):
                job = self.completed(url)
                refs = self.request(url, BASE + '/references', {
                    'profile': 'reviewed', 'job_id': job['id'], 'feature_profile': PROFILE})
                reference = next(a for a in refs['anchors'] if a['patient_id'] == '2')
                self.assertEqual(reference['feature_status'], 'AVAILABLE')
                request = {'profile': 'reviewed', 'job_id': job['id'],
                           'reference_token': reference['token'], 'top_k': 2, 'feature_profile': PROFILE}
                compared = self.request(url, BASE + '/compare', request)
                self.assertEqual([r['patient_id'] for r in compared['ranked_patients']], ['3', '1'])
                self.assertEqual({r['unit'] for r in compared['ranked_patients'][0]['feature_contributions']},
                                 {'mmHg', 'beats/min', 'breaths/min'})
                bundle = self.request(url, BASE + '/export', request)
                self.assertTrue(verify(bundle, clinical_features_path=pack)['verified'])
                with self.assertRaisesRegex(ValueError, 'clinical-features'):
                    verify(bundle)
                changed = deepcopy(bundle)
                changed['comparison']['feature_profile']['features'][1]['scale'] = '40'
                changed['report_id'] = digest({k: v for k, v in changed.items() if k != 'report_id'})
                with self.assertRaisesRegex(ValueError, 'differs from replay'):
                    verify(changed, clinical_features_path=pack)
            source = pack_dir / 'authored-measurements.csv'
            source.write_text(source.read_text().replace(',100\n', ',101\n'))
            with self.running(**options) as (_, url):
                self.assertEqual(self.request(url, self.path(job)), job)
                self.assertEqual(self.request(url, BASE + '/compare', request), compared)
                self.assertEqual(self.request(url, BASE + '/export', request), bundle)
                with self.assertRaises(HTTPError) as caught:
                    self.request(url, BASE + '/jobs', {'profile': 'reviewed', 'stratum': 'synthetic',
                                 'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
                self.assertEqual(caught.exception.code, 400)
            with self.assertRaises(ValueError):
                verify(bundle, clinical_features_path=pack)

    def test_startup_sources_are_exclusive_and_loopback_only(self):
        with self.assertRaisesRegex(ValueError, 'one recorded source'):
            server.make_server(port=0, recorded_config='unused', public_demo_dir='unused')
        for option in ({'public_demo_dir': 'unused'}, {'clinical_features_path': 'unused'}):
            with self.subTest(option=option), self.assertRaisesRegex(ValueError, 'loopback'):
                server.make_server(host='0.0.0.0', port=0, **option)
        with self.running(public_demo_dir=ROOT / 'examples/source-mixed-query') as (_, url):
            metadata = self.request(url, BASE + '/config')
            self.assertEqual(metadata['default_profile'], 'literal')
            self.assertEqual(set(metadata['profiles']), {'literal'})
            self.assertEqual(self.request(url, '/api/capabilities')['scope'], 'public-demo-research-prototype')
            with self.assertRaises(HTTPError) as caught:
                self.request(url, BASE + '/jobs', {'profile': 'reviewed', 'stratum': 'synthetic',
                             'controls': {'threshold': '65', 'baseline_minutes': 30, 'followup_minutes': 120}})
            self.assertEqual(caught.exception.code, 400)

    def test_capabilities_name_each_surface_source_separately(self):
        """`scope` names the server's recorded configuration, not the cohort the
        workspace ranks. A reader who sees `public-demo-research-prototype` would
        otherwise conclude the similarity surface ranks demo patients; it ranks the
        authored cohort in every mode."""
        with self.running() as (_, url):
            modes = self.request(url, '/api/capabilities')['source_modes']
            self.assertEqual(modes, {'cohort': 'authored-synthetic',
                                     'recorded_journey': 'unconfigured'})
        with self.running(public_demo_dir=ROOT / 'examples/source-mixed-query') as (_, url):
            capabilities = self.request(url, '/api/capabilities')
            self.assertEqual(capabilities['scope'], 'public-demo-research-prototype')
            self.assertEqual(capabilities['source_modes'],
                             {'cohort': 'authored-synthetic', 'recorded_journey': 'public-demo'})

    def demo_cohort(self, folder):
        """A minimal admissible public-demo cohort.

        The records are small and invented; what is under test is the server wiring, not
        the derivation. `tools/build_demo_cohort.py` is what ties rows to the release, by
        digesting every pinned file before reading one.
        """
        pin = json.loads((ROOT / 'data/clinical-source-demo-pin.json').read_bytes())
        rule = 'ICU intime plus 24 hours; every record strictly before it.'
        patients = []
        for index, (stay, value) in enumerate((('100', '1.1'), ('101', '2.4')), start=1):
            record = {'id': f'creatinine-{stay}', 'component': 'baseline_creatinine',
                      'value': {'hasValue': value}, 'unit': 'mg/dL',
                      'occurrence_us': -3600000000 * index, 'available_at_us': -3600000000 * index,
                      'clock': f'stay-{stay}-relative',
                      'source': {'document': 'chartevents.csv.gz', 'row': str(index)},
                      'claim': {'id': f'creatinine-{stay}-claim', 'refersTo': f'creatinine-{stay}-role'},
                      'role': {'id': f'creatinine-{stay}-role', 'isFeatureOf': f'stay-{stay}'}}
            patients.append({'patient_id': f'stay-{stay}', 'label': f'ICU stay {stay}',
                             'episode_id': f'stay-{stay}-icu', 'clock': f'stay-{stay}-relative',
                             'index_us': 0, 'index_rule': rule, 'records': [record]})
        dataset = {'dataset_id': 'demo-cohort-test', 'label': 'Two pinned demo stays',
                   'scope': 'recorded_public_demo', 'clinical_validity_claim': False,
                   'index_rule': rule, 'patients': patients,
                   'source_pin': {name: meta['file_sha256'] for name, meta in pin['files'].items()}}
        profile = {'profile_id': 'demo-cohort-test-1.0', 'clinical_validity_claim': False,
                   'mapping_review': 'pinned_public_demo_source_fidelity',
                   'weights': {'age_band': '0', 'baseline_creatinine': '1', 'clinical_concepts': '1'},
                   'minimum_coverage': '0.5', 'history_us': 24 * 3600000000,
                   'creatinine_window_us': 24 * 3600000000, 'creatinine_scale': '1',
                   'creatinine_unit': 'mg/dL', 'concept_ancestors': {}}
        cohort = Path(folder) / 'cohort'
        cohort.mkdir(parents=True)
        (cohort / 'dataset.json').write_text(json.dumps(dataset))
        (cohort / 'profile.json').write_text(json.dumps(profile))
        return cohort

    def test_public_demo_cohort_replaces_the_authored_pool(self):
        with tempfile.TemporaryDirectory() as folder:
            cohort = self.demo_cohort(folder)
            with self.running(public_demo_cohort=cohort, state_dir=Path(folder) / 'state') as (_, url):
                capabilities = self.request(url, '/api/capabilities')
                self.assertEqual(capabilities['source_modes']['cohort'], 'public-demo')
                self.assertEqual([p['patient_id'] for p in capabilities['metadata']['patients']],
                                 ['stay-100', 'stay-101'])
                self.assertEqual(capabilities['metadata']['scope'], 'recorded_public_demo')
                initial = self.request(url, '/api/initial', {'patient_id': 'stay-100', 'top_k': 5})
                self.assertEqual(initial['results']['eligible_patient_ids'], ['stay-101'])

    def test_the_authored_trajectory_is_withdrawn_not_served_authored_patients(self):
        """The fixed kidney trajectory runs over the authored PRO/SOLID projections. The
        derived cohort has none, so the step must be refused rather than quietly compare
        authored patients while every other surface shows demo stays."""
        with tempfile.TemporaryDirectory() as folder:
            cohort = self.demo_cohort(folder)
            with self.running(public_demo_cohort=cohort, state_dir=Path(folder) / 'state') as (_, url):
                capabilities = self.request(url, '/api/capabilities')
                self.assertNotIn('exact-and-declared-relaxed-trajectories', capabilities['supported'])
                self.assertIn('exact-and-declared-relaxed-trajectories', capabilities['unsupported'])
                self.assertIn('derived-age-band', capabilities['unsupported'])
                initial = self.request(url, '/api/initial', {'patient_id': 'stay-100', 'top_k': 5})
                with self.assertRaises(HTTPError) as caught:
                    self.request(url, '/api/trajectory',
                                 {'revision_id': initial['revision_id'], 'budget': '0'})
                self.assertEqual(caught.exception.code, 400)

    def test_demo_cohort_evidence_names_its_pinned_rows(self):
        with tempfile.TemporaryDirectory() as folder:
            cohort = self.demo_cohort(folder)
            with self.running(public_demo_cohort=cohort, state_dir=Path(folder) / 'state') as (_, url):
                initial = self.request(url, '/api/initial', {'patient_id': 'stay-100', 'top_k': 5})
                evidence = self.request(
                    url, f'/api/evidence?revision_id={initial["revision_id"]}&patient_id=stay-101')
                self.assertIsNone(evidence['pro_solid'])
                self.assertEqual([row['document'] for row in evidence['source_rows']],
                                 ['chartevents.csv.gz'])

    def test_a_cohort_outside_the_recorded_demo_scope_is_refused_at_startup(self):
        with tempfile.TemporaryDirectory() as folder:
            cohort = Path(folder) / 'cohort'
            cohort.mkdir()
            for name in ('dataset.json', 'profile.json'):
                (cohort / name).write_text(((ROOT / 'examples/patient-similarity') / name).read_text())
            with self.assertRaises(ValueError):
                server.make_server(port=0, public_demo_cohort=cohort)
