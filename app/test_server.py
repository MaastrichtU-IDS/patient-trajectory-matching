"""The actual HTTP patient-to-pattern journey and its integrity boundaries."""
import copy
import hashlib
from pathlib import Path
import json
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from app import server
from app.replay import verify
from patterns.patient_similarity import SimilarityEngine
from demo import cohort


class ResearchHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = server.make_server(port=0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def request(self, path, body=None, headers=None):
        request_headers = {'Origin': self.url}
        if body is not None:
            request_headers['Content-Type'] = 'application/json'
        request_headers.update(headers or {})
        raw = body if isinstance(body, bytes) else json.dumps(body).encode() if body is not None else None
        with urlopen(Request(self.url + path, data=raw, headers=request_headers), timeout=10) as response:
            return json.load(response)

    def initial(self, reference='P00', top_k=5):
        return self.request('/api/initial', {'patient_id': reference, 'top_k': top_k})

    def test_complete_two_refinement_http_journey_and_replay(self):
        initial = self.initial(top_k=1)
        weighted = self.request('/api/refine', {'revision_id': initial['revision_id'], 'operation':
            {'type': 'set_weights', 'weights': {'age_band': '1', 'baseline_creatinine': '3', 'clinical_concepts': '1'}}})
        refined = self.request('/api/refine', {'revision_id': weighted['revision_id'], 'operation':
            {'type': 'add_filter', 'predicate': {'component': 'baseline_creatinine', 'operator': 'between', 'value': {'min': '0', 'max': '1.1'}}}})
        self.assertEqual(weighted['parent_revision_id'], initial['revision_id'])
        self.assertEqual(refined['parent_revision_id'], weighted['revision_id'])
        self.assertEqual(weighted['results']['eligible_patient_ids'], initial['results']['eligible_patient_ids'])
        self.assertTrue(set(refined['results']['eligible_patient_ids']) <= set(initial['results']['eligible_patient_ids']))
        self.assertIn('P08', [row['patient_id'] for row in refined['results']['unresolved']])
        comparison = self.request('/api/trajectory', {'revision_id': refined['revision_id'], 'budget': '2'})
        self.assertGreater(comparison['exact']['candidate_count'], len(refined['results']['displayed']))
        self.assertEqual(comparison['exact']['membership']['included'], ['P01', 'P02', 'P03'])
        self.assertEqual(comparison['added'], ['P04', 'P05', 'P06'])
        self.assertEqual(comparison['relaxed']['membership']['unresolved'], ['P10'])
        evidence = self.request('/api/evidence?revision_id=' + refined['revision_id'] + '&patient_id=P03')
        self.assertEqual(evidence['source_rows'][0]['value'], '8.0')
        self.assertEqual(evidence['pro_solid']['bindings']['P03-B']['normalized_value'], '0.80')
        bundle = self.request('/api/export/' + comparison['comparison_id'])
        replay = verify(bundle)
        self.assertTrue(replay['verified'])
        self.assertEqual(replay['revision_count'], 3)
        self.assertEqual(replay['added'], comparison['added'])
        root = Path(__file__).resolve().parents[1]
        artifact_paths = ['app/server.py', 'app/replay.py', 'app/index.html', 'app/app.js',
                          'app/style.css', 'app/test_server.py', 'app/test_ui.cjs',
                          'patterns/patient_similarity.py', 'patterns/pro_solid.py',
                          'demo/cohort.py', 'demo/cohort-data.json', 'reference_oracle.py',
                          'examples/exemplar.pattern.json', 'ontology/toy-taxonomy.json']
        artifact_paths.extend(str(path.relative_to(root)) for path in sorted((root / 'examples/patient-similarity').glob('*.json')))
        report = {
            'profile': 'research-prototype-journey-verification-1.0',
            'passed': True,
            'verified_journey': ['bounded_query_by_example', 'first_refinement', 'second_refinement',
                                 'exact_vs_relaxed_cohort_comparison', 'source_evidence_inspection'],
            'execution': 'actual-loopback-http-and-independent-export-replay',
            'scope': 'authored-synthetic-research-prototype',
            'clinical_validity_claim': False,
            'replay_verified': replay['verified'],
            'dataset_sha256': initial['dataset_sha256'],
            'profile_sha256': initial['profile_sha256'],
            'implementation_sha256': initial['implementation_sha256'],
            'source_dataset_sha256': self.server.workspace.source_hash,
            'projection_sha256': self.server.workspace.dataset['projection_sha256'],
            'counts': {'base_candidates': len(initial['results']['eligible_patient_ids']),
                       'displayed': len(initial['results']['displayed']), 'revisions': replay['revision_count'],
                       'hard_eligible_after_refinement': len(refined['results']['eligible_patient_ids']),
                       'hard_eligibility_unresolved': len(refined['results']['unresolved']),
                       'exact': len(comparison['exact']['membership']['included']),
                       'relaxed_additions': len(comparison['added']),
                       'trajectory_unresolved': len(comparison['relaxed']['membership']['unresolved']),
                       'source_rows_inspected': len(evidence['source_rows'])},
            'artifacts': {path: hashlib.sha256((root / path).read_bytes()).hexdigest() for path in artifact_paths}}
        run = root / 'verification/research-prototype-run/journey.json'
        run.parent.mkdir(parents=True, exist_ok=True)
        run.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
        committed = root / 'verification/research-prototype-journey.json'
        if committed.exists():
            self.assertEqual(report, json.loads(committed.read_text()), 'Committed research journey evidence is stale')

    def test_selected_reference_is_excluded_in_matching_and_replay(self):
        revision = self.initial('P03')
        result = self.request('/api/trajectory', {'revision_id': revision['revision_id'], 'budget': '2'})
        self.assertEqual(result['exact']['reference_excluded'], 'P03')
        self.assertNotIn('P03', result['relaxed']['membership']['included'])
        self.assertIn('P00', result['exact']['membership']['included'])
        self.assertEqual(verify(self.request('/api/export/' + result['comparison_id']))['reference_patient_id'], 'P03')

    def test_export_detects_result_tampering_even_with_rehashed_outer_manifest(self):
        revision = self.initial()
        result = self.request('/api/trajectory', {'revision_id': revision['revision_id'], 'budget': '1'})
        bundle = self.request('/api/export/' + result['comparison_id'])
        bundle['comparison']['added'].append('P07')
        bundle['manifest_sha256'] = cohort.digest({k:v for k,v in bundle.items() if k != 'manifest_sha256'})
        with self.assertRaisesRegex(ValueError, 'differs'):
            verify(bundle)

    def test_internally_consistent_alternate_fixture_export_is_not_admitted(self):
        for changed in ('dataset', 'profile'):
            workspace = server.Workspace()
            initial = workspace.engine.initial('P00')
            manifest = workspace.engine.manifest(initial['revision_id'])
            dataset, profile = manifest['dataset'], manifest['similarity_profile']
            if changed == 'dataset':
                dataset['label'] += ' altered fixture'
            else:
                profile['weights']['age_band'] = '2'
            alternate = SimilarityEngine(dataset, profile)
            revision = alternate.initial('P00')
            workspace.engine = alternate
            comparison = workspace.compare(revision['revision_id'], '2')
            bundle = workspace.export(comparison['comparison_id'])
            with self.assertRaisesRegex(ValueError, 'admitted authored fixture'):
                verify(bundle)

    def test_origin_and_fetch_site_protection(self):
        for headers in ({'Origin': 'https://other.example'}, {'Sec-Fetch-Site': 'cross-site'}, {'Origin': self.url + '/path'}):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/capabilities', headers=headers)
            self.assertEqual(caught.exception.code, 403)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/initial', {'patient_id': 'P00', 'top_k': 5}, {'Origin': 'https://other.example'})
        self.assertEqual(caught.exception.code, 403)

    def test_strict_body_types_keys_duplicates_and_size(self):
        for body in ([1], {'patient_id': 'P00', 'top_k': True}, {'patient_id': 'P00', 'top_k': 0},
                     {'patient_id': 'P00', 'top_k': 5, 'source_dir': '/tmp'},
                     b'{"patient_id":"P00","top_k":1,"top_k":5}', b'{"patient_id":"P00","top_k":NaN}'):
            with self.assertRaises(HTTPError) as caught:
                self.request('/api/initial', body)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/initial', b' ' * (server.MAX_BODY + 1))
        self.assertEqual(caught.exception.code, 413)

    def test_unsupported_refinement_cannot_change_clinical_acceptance(self):
        revision = self.initial()
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/refine', {'revision_id': revision['revision_id'], 'operation': {'type': 'approve_mapping'}})
        self.assertEqual(caught.exception.code, 400)
        again = self.request('/api/revisions/' + revision['revision_id'])
        self.assertEqual(again, revision)

    def test_unsupported_numeric_exponents_return_validation_error(self):
        revision = self.initial()
        with self.assertRaises(HTTPError) as caught:
            self.request('/api/refine', {'revision_id': revision['revision_id'], 'operation': {
                'type': 'set_weights', 'weights': {'age_band': '1e999999999',
                'baseline_creatinine': '1', 'clinical_concepts': '1'}}})
        self.assertEqual(caught.exception.code, 400)

    def test_strict_queries_and_unlisted_paths(self):
        for path in ('/api/capabilities?path=/etc/passwd', '/api/evidence?patient_id=P01&patient_id=P02&revision_id=x'):
            with self.assertRaises(HTTPError) as caught:
                self.request(path)
            self.assertEqual(caught.exception.code, 400)
        with self.assertRaises(HTTPError) as caught:
            self.request('/../demo/cohort-data.json')
        self.assertEqual(caught.exception.code, 404)

    def test_liveness_and_readiness_are_cheap_and_independent(self):
        with patch.object(self.server.workspace.engine, 'initial', side_effect=AssertionError('Must not rank')):
            self.assertEqual(self.request('/healthz'), {'status': 'ok'})
            self.assertEqual(self.request('/readyz'), {'status': 'ok'})
            self.server.workspace.ready = False
            try:
                with self.assertRaises(HTTPError) as caught:
                    self.request('/readyz')
                self.assertEqual(caught.exception.code, 503)
                self.assertEqual(self.request('/healthz'), {'status': 'ok'})
            finally:
                self.server.workspace.ready = True

    def test_source_fingerprint_mismatch_prevents_readiness(self):
        with patch.object(server.SimilarityEngine, 'metadata', return_value={'source_dataset_sha256': 'wrong'}):
            with self.assertRaisesRegex(ValueError, 'fingerprints'):
                server.Workspace()

    def test_bounded_comparisons_and_defensive_copies(self):
        workspace = server.Workspace()
        revision = workspace.engine.initial('P00')
        with patch.object(server, 'MAX_COMPARISONS', 1):
            old = workspace.compare(revision['revision_id'], '0')
            new = workspace.compare(revision['revision_id'], '2')
            self.assertEqual(len(workspace.comparisons), 1)
            with self.assertRaises(KeyError):
                workspace.export(old['comparison_id'])
            new['added'].append('P07')
            self.assertNotIn('P07', workspace.export(new['comparison_id'])['comparison']['added'])

    def test_browser_security_headers_and_assets(self):
        with urlopen(self.url + '/') as response:
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
            self.assertIn(b'Authored synthetic records', response.read())
        for path in ('/app.js', '/style.css'):
            with urlopen(self.url + path) as response:
                self.assertEqual(response.status, 200)
        self.assertIn('authentication', self.request('/api/capabilities')['unsupported'])


if __name__ == '__main__':
    unittest.main()
