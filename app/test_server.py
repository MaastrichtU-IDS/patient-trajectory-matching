"""The actual HTTP patient-to-pattern journey and its integrity boundaries."""
import copy
import hashlib
import re
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
from patterns import robust_relaxation
from app import interval_editor


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

    def test_each_workflow_names_its_cost_model_and_no_response_carries_both(self):
        """Decision D's second adoption item, decided: the two models may not share a view.

        The acceptance cases in `patterns/test_relaxation_contract.py` established that at one
        stated budget the two surfaces return different cohorts, and that under recorded
        uncertainty one admits where the other refuses. So a reader who meets both numbers
        must be told which model produced each, and must not meet them in one view where the
        numbers read as one control.

        Both halves are checked here. Each response names its own model, and neither response
        mentions the other's identifier anywhere in its payload -- which is what makes the
        separation a property of the data rather than of the page that happens to render it.
        """
        oracle, catalogue = cohort.RELAXATION_PROFILE, robust_relaxation.PROFILE
        self.assertNotEqual(oracle, catalogue, 'the guard below is vacuous if they coincide')

        revision = self.initial()
        trajectory = self.request('/api/trajectory', {'revision_id': revision['revision_id'], 'budget': '2'})
        for part in ('exact', 'relaxed'):
            self.assertEqual(trajectory[part]['relaxation_profile'], oracle, part)

        priced = {'trajectory': trajectory}
        priced['journey'] = self.request('/api/journey/run', {
            'reference_patient_id': 'T03', 'top_k': 1, 'maximum_baseline': None,
            'question': 'overlap', 'budget': '1.25'})
        priced['temporal'] = self.request('/api/temporal/run', {'budget': '1.25'})
        priced['editor'] = self.request('/api/editor/run', interval_editor.IntervalEditor().metadata()['default_controls'])
        # The three catalogue surfaces nest the executed policy differently; each must still
        # name its model somewhere a reader's page can reach.
        for name, policy in (('journey', priced['journey'].get('policy')),
                             ('temporal', priced['temporal'].get('inputs', {}).get('policy')),
                             ('editor', priced['editor'].get('policy'))):
            self.assertIsNotNone(policy, f'{name} response carries no executed policy')
            self.assertEqual(policy['profile'], catalogue, name)

        for name, response, own, other in (('trajectory', trajectory, oracle, catalogue),
                                           ('journey', priced['journey'], catalogue, oracle),
                                           ('temporal', priced['temporal'], catalogue, oracle),
                                           ('editor', priced['editor'], catalogue, oracle)):
            serialized = json.dumps(response)
            self.assertIn(own, serialized, f'{name} does not name the model that priced it')
            self.assertNotIn(other, serialized,
                             f'{name} carries both cost-model identifiers; decision D requires '
                             f'that a reader never meets the two budgets as one control')

    def test_every_workflow_declares_what_its_budget_bounds(self):
        """The identifier alone does not help a reader who has not read the decision note.

        Each page states, beside its own control, what its number bounds and that it is not
        the other page's number. The two statements have to disagree about summing, because
        that is the difference: the oracle sums component costs across relaxed targets and the
        catalogue never sums across options.
        """
        pages = {}
        for path in ('/', '/journey', '/temporal', '/temporal/editor'):
            with urlopen(Request(self.url + path, headers={'Origin': self.url}), timeout=10) as response:
                pages[path] = response.read().decode()
            self.assertIn('id="priced-by"', pages[path], f'{path} has nowhere to name its model')
            self.assertIn('not one control', pages[path], f'{path} does not warn against the comparison')
        # The oracle page is the only one that sums. Every catalogue page must say the opposite,
        # or a reader moving between them meets two numbers described the same way.
        self.assertIn('<strong>sum</strong> of component costs across every relaxed target', pages['/'])
        self.assertNotIn('<strong>single</strong> option applied', pages['/'])
        for path in ('/journey', '/temporal', '/temporal/editor'):
            self.assertIn('<strong>single</strong> option applied', pages[path], path)
            self.assertNotIn('<strong>sum</strong> of component costs across every relaxed target', pages[path], path)

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

    def test_workspace_grid_items_may_shrink_below_their_content_width(self):
        """UI-012 reflow at 320 CSS px.

        The 850px breakpoint collapses .workspace to a single `1fr` column, but `1fr` is
        `minmax(auto,1fr)` and that `auto` minimum is min-content, so a control panel keeps
        its natural width and the page scrolls sideways anyway. `min-width:0` removes that
        floor. #recorded-workflow already carried the same guard locally.

        CI has no browser, so this pins the guard rather than measuring layout. The reflow
        itself was verified in Chrome: /journey 426->320, /temporal 361->320 and
        /temporal/editor 419->320 against a 320px viewport.
        """
        css = (Path(server.__file__).resolve().parent / 'style.css').read_text()
        self.assertIn('.workspace{display:grid', css)
        self.assertIn('.workspace>*{min-width:0}', css)



    def test_a_weight_moves_only_the_order_and_a_requirement_only_the_eligible_set(self):
        """UI-002's two effects, at the engine the page reports them from.

        The page offers each feature as Must match, Prefer similar or Ignore, and tells the
        reader which of eligibility and ranking it changed. That report is only honest if
        the engine keeps the two apart, so this pins the separation rather than the wording:
        a weight never moves anyone into or out of the eligible set, and a requirement does.

        It also pins why Must match contributes weight zero. If a required feature kept a
        weight, one setting would move both effects at once and the page could not report
        them separately without attributing a reordering to the wrong cause.
        """
        engine = self.server.workspace.engine
        initial = engine.initial('P00', 5)
        order = lambda revision: [row['patient_id'] for row in revision['results']['displayed']]
        eligible = lambda revision: revision['results']['eligible_patient_ids']

        weighted = engine.refine(initial['revision_id'], {'type': 'set_weights', 'weights': {
            'age_band': '0', 'baseline_creatinine': '1', 'clinical_concepts': '0'}})
        self.assertEqual(eligible(weighted), eligible(initial), 'a weight is not a criterion')
        self.assertNotEqual(order(weighted), order(initial), 'this weight should reorder the display')

        required = {'component': 'baseline_creatinine', 'operator': 'between',
                    'value': {'min': '0', 'max': '1.1'}}
        filtered = engine.refine(initial['revision_id'], {'type': 'add_filter', 'predicate': required})
        self.assertNotEqual(eligible(filtered), eligible(initial), 'a criterion changes who is eligible')
        self.assertNotIn('P08', eligible(filtered))
        self.assertIn('P08', eligible(initial))

        # Must match as the page sends it: weight zero first, then the requirement. The
        # eligible set must land where the requirement alone lands, whatever the weight was.
        zeroed = engine.refine(initial['revision_id'], {'type': 'set_weights', 'weights': {
            'age_band': '1', 'baseline_creatinine': '0', 'clinical_concepts': '1'}})
        applied = engine.refine(zeroed['revision_id'], {'type': 'add_filter', 'predicate': required})
        self.assertEqual(eligible(applied), eligible(filtered))

    def test_a_ranking_needs_something_to_rank_on(self):
        """Ignoring every feature is refused, which is why the page keeps one preferred.

        The page blocks this before sending anything, but the engine is where the rule
        lives: a zero total weight has no score to divide, and silently substituting a
        default would rank patients by a criterion nobody chose.
        """
        engine = self.server.workspace.engine
        initial = engine.initial('P00', 5)
        with self.assertRaises(ValueError) as refusal:
            engine.refine(initial['revision_id'], {'type': 'set_weights', 'weights': {
                'age_band': '0', 'baseline_creatinine': '0', 'clinical_concepts': '0'}})
        self.assertIn('INVALID_WEIGHTS', str(refusal.exception))

    def test_research_navigation_links_declare_a_twenty_four_pixel_target(self):
        """UI-012 target size for the research navigation, as a declaration.

        The four research pages share one `<nav aria-label="Research workflows">` of text
        links. They carried no rule of their own, so each anchor was an inline box whose
        height was whatever the inherited font produced -- around 19px on the three pages
        that load only style.css, and 23.4px on /journey, where journey.css sets 13px type
        at a 1.8 line-height. Both are under the 24px of WCAG 2.2 SC 2.5.8, and the
        shortfall also set how far apart the rows sit once the nav wraps on a narrow
        viewport, which is where the spacing exception would otherwise have covered it.

        `min-height` is what makes the floor independent of the inherited font size, and it
        needs a non-inline box to apply, so the display declaration is part of the
        guarantee rather than decoration. CI has no browser and no layout engine here --
        the DOM suites run against a hand-written element stub -- so this pins the
        declared geometry and does not measure a rendered target. Measuring one is still
        listed as remaining under UI-012.
        """
        directory = Path(server.__file__).resolve().parent
        def declarations(css, selector):
            match = re.search(re.escape(selector) + r'\{([^}]*)\}', css)
            self.assertIsNotNone(match, f'{selector} is not declared')
            return dict(part.split(':', 1) for part in match.group(1).split(';') if part)

        css = (directory / 'style.css').read_text()
        rule = declarations(css, 'nav a')
        self.assertIn(rule.get('display'), ('inline-block', 'block', 'flex'),
                      'min-height does not apply to an inline box')
        # Under border-box the declared minimum is the whole box, padding included, so the
        # floor is the min-height alone. Without that reset it would be content height and
        # the arithmetic below would be wrong rather than conservative.
        self.assertIn('*{box-sizing:border-box}', css)
        self.assertGreaterEqual(int(rule.get('min-height', '0px').removesuffix('px')), 24)
        # /journey sets 13px type on the nav. That is what the floor exists to survive, so
        # the smaller font must not arrive with its own box declarations for these anchors.
        journey = (directory / 'journey.css').read_text()
        smaller = int(declarations(journey, 'nav')['font-size'].removesuffix('px'))
        self.assertLess(smaller, 16, 'this page is why the floor cannot be derived from type size')
        self.assertNotIn('nav a{', journey, 'a second rule here could undo the floor silently')
        # A page that forgot the stylesheet would not be covered by any of the above.
        for page in ('index.html', 'journey.html', 'editor.html', 'temporal.html'):
            markup = (directory / page).read_text()
            navigation = re.search(r'<nav[^>]*aria-label="Research workflows">(.*?)</nav>', markup)
            self.assertIsNotNone(navigation, f'{page} has no research navigation')
            self.assertGreater(navigation.group(1).count('<a '), 1)
            self.assertIn('href="/style.css"', markup, f'{page} is not covered by the nav rule')

    def test_cross_site_top_level_navigation_is_admitted_but_cross_site_reads_and_writes_are_not(self):
        """A link on another site arrives as a cross-site GET navigation with no Origin header.
        That is how a browser reaches this page at all; refusing it shows the JSON error in the tab."""
        def raw(path, method='GET', headers=None, body=None):
            try:
                with urlopen(Request(self.url + path, data=body, headers=headers or {}, method=method), timeout=10) as response:
                    return response.status
            except HTTPError as caught:
                return caught.code
        navigation = {'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'navigate', 'Sec-Fetch-Dest': 'document',
                      'Sec-Fetch-User': '?1', 'Referer': 'http://other.test/'}  # as Chrome sends for a link click
        self.assertEqual(raw('/journey', headers=navigation), 200)
        self.assertEqual(raw('/', headers=navigation), 200)
        # Embedding vectors, cross-site fetch reads and every cross-site write stay refused.
        self.assertEqual(raw('/journey', headers={**navigation, 'Sec-Fetch-Dest': 'object'}), 403)
        self.assertEqual(raw('/journey', headers={**navigation, 'Sec-Fetch-Dest': 'embed'}), 403)
        self.assertEqual(raw('/api/capabilities', headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'cors',
                                                          'Sec-Fetch-Dest': 'empty'}), 403)
        json_post = {**navigation, 'Content-Type': 'application/json'}
        self.assertEqual(raw('/api/initial', 'POST', {**json_post, 'Origin': 'https://other.example'}, b'{}'), 403)
        self.assertEqual(raw('/api/initial', 'POST', json_post, b'{}'), 403)

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
