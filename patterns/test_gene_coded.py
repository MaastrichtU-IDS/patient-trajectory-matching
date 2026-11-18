"""Gene steps under "coded only" (D42): the joint profile `execution-store-conditions-gene-coded-1.0`, answered by
`store_conditions_gene` through D39's hooks in `store_conditions_coded`.

- Small cases (`gene_coded_cases`): tiny snapshots answered through the dispatcher, against the worlds check (kept events,
  excluded records as events of unknown end since D44, and the gene step's true findings in every reading of an unknown
  classification; no carve-out since D44) and the independent reference (D39's removal of left-out events, then D38's
  gene reference); the store against itself on the snapshot built without mentions; pruning on and off; the four
  planted defects of the D42 rules; D42's development case judged in full (`CarveOutRemovedTests`, D44).
- Snapshots: D38's gene fixtures (letter mentions of a gene and of a plain concept, undetermined records, rises, shared
  variables) and the committed cohort built by the wrapper, with D38's gene requests made joint: the store against the
  reference, at 1 and 2 workers, both detail levels, pruning on and off; the store against itself without mentions.
- The contract: refusals by name, the output's `excluded_kinds`, the context, the dispatcher's route.

`adhoc` runs the ruled soundness measurements of the decision log (D42, rule 1) at their sizes.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from . import coded_only_cases as co
from . import execution_store as es
from . import gene_coded_cases as gcc
from . import store_conditions as sc
from . import store_conditions_coded as cc
from . import store_conditions_counts as scn
from . import store_conditions_gene as gc
from . import store_conditions_relative as rc
from . import synthetic_ingest as si
from . import test_gene_steps as tgs
from . import test_relative_conditions as trc
from . import trajectory_graph as tg

ROOT = Path(__file__).resolve().parents[1]
DAY = trc.DAY
T0 = trc.T0
MINUTE = 60_000_000
GENE = dict(gc.GENE_SPEC)
KINDS = list(gcc.KINDS)

_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='gene-coded-')
    tmp = Path(_STATE['tmp'].name)
    for name, seed, options in (('fixture', 51, {}), ('shared', 52, {'shared': 0.7})):
        ingest = tgs.gene_ingest(seed, **options)
        _STATE[name] = (ingest, es.open_store(es.build(ingest, tmp / name), verify='content'), tgs.FIXTURE_POOL)
    subprocess.run([sys.executable, str(ROOT / 'demos/mockups/synthetic/generate.py'), '--output', str(tmp / 'cohort.json')],
                   check=True, capture_output=True, cwd=ROOT)
    cohort = json.loads((tmp / 'cohort.json').read_text(encoding='utf-8'))
    _STATE['cohort_json'] = cohort
    ingest = gc.synthetic_ingest(cohort, series=True)
    _STATE['cohort'] = (ingest, es.open_store(es.build(ingest, tmp / 'cohort'), verify='content'), tgs.COHORT_POOL)
    for name in ('fixture', 'shared', 'cohort'):  # the same snapshots without letter mentions, for the store-store check
        _STATE[name + '/bare'] = es.open_store(es.build(co.without_kinds(_STATE[name][0], KINDS), tmp / f'{name}-bare'))
        _STATE[name + '/reference'] = gcc.Reference(_STATE[name][0])


def tearDownModule():
    for name in ('fixture', 'shared', 'cohort'):
        if name in _STATE:
            _STATE[name][1].close()
        if name + '/bare' in _STATE:
            _STATE[name + '/bare'].close()
    es.close_pools()
    _STATE['tmp'].cleanup()


def case(seed, name):
    """(query, joint conditions, windows): D38's gene request on this snapshot, made joint."""
    ingest, _, pool = _STATE[name]
    rng = random.Random(f'{name}/gene-coded/{seed}')
    for _ in range(50):
        query, conditions, windows = tgs.gene_request(rng, ingest, pool)
        conditions = gcc.joint(conditions)
        try:
            gc.Request(query, conditions)
        except es.StoreError:
            continue
        return query, conditions, windows
    raise AssertionError(seed)


def compare(name, seeds, itself=True, **options):
    """(mismatches against the reference, mismatches against the store without mentions, status mix)."""
    _, store, _ = _STATE[name]
    reference = _STATE[name + '/reference']
    wrong, apart, mix = [], [], Counter()
    for seed in seeds:
        query, conditions, windows = case(seed, name)
        got = sc.match(store, query, windows, conditions=conditions, **options)['results']
        want = reference.match(query, windows, conditions)
        if options.get('detail') == 'decided':
            listed = {r['patient_id'] for r in got}
            want = [w for w in want if w['patient_id'] in listed]
        for g, w in zip(got, want, strict=True):
            if json.dumps(co.unfiltered(g)) != json.dumps(w):
                wrong.append((seed, g, w))
        mix.update(g['status'] for g in got)
        if itself:
            bare = _STATE[name + '/bare']
            plain = gcc.gene_conditions(conditions)
            other = sc.match(bare, query, windows, conditions=plain, **options)['results']
            if (json.dumps(other) != json.dumps(got) or sc.candidates(bare, query, plain)['patient_idx']
                    != sc.candidates(store, query, conditions)['patient_idx']):
                apart.append(seed)
    return wrong, apart, mix


class SmallCaseTests(unittest.TestCase):
    """The joint profile is sound on tiny snapshots and agrees with the reference and with itself without mentions."""

    def test_no_claim_is_false_and_the_reference_agrees(self):
        with tempfile.TemporaryDirectory() as tmp:
            for generator in gcc.GENERATORS:
                violations, mismatches, mix, cases = gcc.run_small(Path(tmp), generator, 1, first=0, itself=True)
                self.assertEqual(violations, [], generator)
                self.assertEqual(mismatches, [], generator)
                self.assertEqual(cases, 1000)
                self.assertTrue({'exact', 'none', 'not_comparable'} <= set(mix), (generator, mix))

    def test_each_planted_defect_is_caught(self):
        with tempfile.TemporaryDirectory() as tmp:
            for defect in gcc.DEFECTS:
                found = 0
                for generator in gcc.GENERATORS:
                    violations, mismatches, _, _ = gcc.run_small(Path(tmp), generator, 1, first=100, defect=defect,
                                                                 pruning=False)
                    found += len(violations) + len(mismatches)
                self.assertGreater(found, 0, defect)


class CarveOutRemovedTests(unittest.TestCase):
    """D44: D42's development case, judged in full. Its relaxed match beside STEP_CONDITION_UNCERTAIN is `possible` over
    the known records, and the undetermined genetic result g1 makes it exact in some reading. D42 carved the worlds
    check's report out; the excluded-records rule now marks the match, so the check passes with no carve-out, and fails
    under the planted defect that leaves the mark off (main's behaviour)."""

    def test_the_d42_development_case(self):
        case = json.loads((ROOT / 'patterns/fixtures/d44_d42_development_case.json').read_text())
        p = case['ingest']['patients'][0]
        windows = {p['patient_id']: case['window']}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(case['ingest'], Path(tmp) / 's')) as store:
            reference = gcc.Reference(case['ingest']).match(case['query'], windows, case['conditions'])[0]
            for defect, post in ((None, False), (None, True), ('beside_unmarked', False), ('beside_unmarked', True)):
                with self.subTest(defect=defect, post_pass=post), patch.object(es, '_D44_DEFECT', defect), \
                        patch.object(sc, 'COUNT_POST_PASS', post):
                    out = sc.match(store, case['query'], windows, conditions=case['conditions'])
                    got = out['results'][0]
                    patient, reports = gcc.kept_patient(p, case['window'])
                    wrong = gcc.check_result(got, case['query'], case['conditions'], patient, reports)
                    if defect is None:
                        self.assertEqual(wrong, [])
                        self.assertEqual(got['relaxed_match']['note'], es.MAY_MAKE_EXACT)
                        self.assertTrue(gcc.beside_marked(got))
                        if not post:
                            self.assertEqual(got['reason'], 'STEP_CONDITION_UNCERTAIN')
                            self.assertEqual(json.dumps(co.unfiltered(got)), json.dumps(reference))
                        else:  # the post-pass still refines it (its comparison reads the answer without the mark)
                            self.assertEqual(out['derived_steps']['count_refinement']['refined'], 1)
                            self.assertEqual(out['derived_steps']['count_refinement']['skipped_mismatch'], 0)
                    else:
                        self.assertEqual(wrong, ['the relaxed match beside unresolved does not reach its certainty'])


class SnapshotTests(unittest.TestCase):
    """The store against the reference, and against itself without mentions, on D38's fixtures and the cohort."""

    def test_the_store_equals_the_reference_and_itself(self):
        for name, n in (('fixture', 30), ('shared', 15), ('cohort', 15)):
            wrong, apart, mix = compare(name, range(n))
            self.assertEqual(wrong[:2], [], name)
            self.assertEqual(apart, [], name)
            self.assertTrue({'exact', 'none'} <= set(mix) or name != 'fixture', (name, mix))

    def test_workers_detail_and_pruning_change_nothing(self):
        for name in ('fixture', 'cohort'):
            _, store, _ = _STATE[name]
            for seed in range(0, 12, 2):
                query, conditions, windows = case(seed, name)
                base = sc.match(store, query, windows, conditions=conditions)
                two = sc.match(store, query, windows, conditions=conditions, workers=2, chunk=7)
                self.assertEqual(json.dumps(base), json.dumps(two), (name, seed))
                decided = sc.match(store, query, windows, conditions=conditions, detail='decided')
                listed = {r['patient_id']: r for r in decided['results']}
                for r in base['results']:
                    if r['patient_id'] in listed:
                        self.assertEqual(listed[r['patient_id']], r)
                with patch.object(es, '_PRUNE', False):
                    off = sc.match(store, query, windows, conditions=conditions)
                self.assertEqual(json.dumps(base), json.dumps(off), (name, seed))

    def test_leaving_mentions_out_changes_answers(self):
        """The fixtures' letter mentions of a plain concept move some result between the gene profile and the joint
        profile (else the joint path would be untested), and never a gene point."""
        _, store, _ = _STATE['fixture']
        moved = 0
        for seed in range(30):
            query, conditions, windows = case(seed, 'fixture')
            a = sc.match(store, query, windows, conditions=conditions)
            b = sc.match(store, query, windows, conditions=gcc.gene_conditions(conditions))
            moved += sum(1 for x, y in zip(a['results'], b['results'], strict=True) if x != y)
            for r in a['results']:
                for m in (r, r.get('relaxed_match') or {}):
                    for step, eid in (m.get('binding') or {}).items():
                        self.assertFalse(eid.startswith('n'), (seed, r))  # the fixture's mentions are n0, n1
        self.assertGreater(moved, 0)

    def test_letters_cover_no_step(self):
        """Letters record seizures and genes only through mentions: under the joint profile they record neither the gene
        step nor a seizure step (under the gene profile they record the seizure step). C002, C003 and C016, with letters
        and no genetics coverage, are not comparable on the gene step under both; the reference agrees."""
        ingest, store, _ = _STATE['cohort']
        cohort = _STATE['cohort_json']
        q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'B', 'concept': 'gene:SCN1A'},
                                                                 {'id': 'S', 'concept': 'seizure'}],
             'relations': [], 'relaxations': [], 'budget': '0'}
        c = gcc.joint({'profile': gc.GENE_PROFILE, 'steps': {'B': {'gene': dict(GENE)}}})
        births = {p['patient_id']: si.micros(p['birth_date'] + 'T00:00') for p in cohort['patients']}
        windows = {pid: {'start': b, 'end': b + 3653 * 1440 * MINUTE} for pid, b in births.items()}
        by = {r['patient_id']: r for r in sc.match(store, q, windows, conditions=c)['results']}
        plain = {r['patient_id']: r for r in sc.match(store, q, windows, conditions=gcc.gene_conditions(c))['results']}
        for pid in ('C002', 'C003', 'C016'):
            self.assertEqual(by[pid]['status'], 'not_comparable', pid)
            self.assertIn('B', by[pid]['uncovered_steps'], pid)
            self.assertIn('B', plain[pid]['uncovered_steps'], pid)
        self.assertIn(('letters', 'seizure'), store.query('SELECT source, concept FROM source_concepts'))
        self.assertNotIn('letters', cc._sources(store, {'S': ['seizure']}, {}, tuple(KINDS))['seizure'])
        want = _STATE['cohort/reference'].match(q, windows, c)
        self.assertEqual([json.dumps(co.unfiltered(r)) for r in by.values()], [json.dumps(r) for r in want])


class RequestTests(unittest.TestCase):
    """The joint profile's contract: refusals by name, output, context and route."""

    def setUp(self):
        self.ingest, self.store, _ = _STATE['fixture']
        self.pid = self.ingest['patients'][0]['patient_id']
        self.windows = {self.pid: {'start': T0, 'end': T0 + 900 * DAY}}
        self.one = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'G', 'concept': 'g:SCN1A'}, {'id': 'A', 'concept': 'c:febrile'}],
                    'relations': []}

    def test_refusals(self):
        joint = gc.GENE_CODED_PROFILE
        cases = [
            ({'profile': joint, 'steps': {'G': {'gene': GENE}}}, 'INVALID_CONDITIONS'),  # exclude_kinds is required
            ({'profile': joint, 'steps': {'G': {'gene': GENE}}, 'exclude_kinds': KINDS, 'other': 1}, 'INVALID_CONDITIONS'),
            ({'profile': joint, 'steps': {'G': {'gene': GENE}}, 'exclude_kinds': []}, 'INVALID_EXCLUDE_KINDS'),
            ({'profile': joint, 'steps': {'G': {'gene': GENE}}, 'exclude_kinds': ['mention', 'mention']},
             'INVALID_EXCLUDE_KINDS'),
            ({'profile': joint, 'steps': {'G': {'gene': GENE}}, 'exclude_kinds': 'mention'}, 'INVALID_EXCLUDE_KINDS'),
            ({'profile': joint, 'steps': {'G': {'gene': GENE}}, 'exclude_kinds': ['genetic_result']},
             'EXCLUDE_KIND_UNSUPPORTED'),
            ({'profile': joint, 'steps': {'A': {'occurrence': {'nth': 2}}}, 'exclude_kinds': KINDS}, 'GENE_STEP_REQUIRED'),
            ({'profile': joint, 'steps': {}, 'exclude_kinds': KINDS}, 'INVALID_CONDITIONS'),
            ({'profile': joint, 'steps': {'G': {'gene': GENE, 'unit': 'kg'}}, 'exclude_kinds': KINDS},
             'GENE_WITH_OTHER_CONDITION'),
            ({'profile': joint, 'steps': {'G': {'gene': {'report': 'first'}}}, 'exclude_kinds': KINDS}, 'INVALID_GENE'),
        ]
        for conditions, code in cases:
            with self.assertRaises(es.StoreError) as raised:
                gc.Request(self.one, conditions)
            self.assertEqual(raised.exception.code, code, conditions)
        good = {'profile': joint, 'steps': {'G': {'gene': GENE}}, 'exclude_kinds': KINDS}
        for module in (rc, cc):  # the joint profile is the gene module's alone
            with self.assertRaises(es.StoreError) as raised:
                module.Request(self.one, good)
            self.assertIn(raised.exception.code, ('INVALID_CONDITIONS', 'INVALID_CONDITION'), module.__name__)
        with self.assertRaises(es.StoreError) as raised:
            gc.Request({**self.one, 'steps': [{'id': 'G', 'concept': 'g:SCN1A', 'occurrence': 'first'}]}, good)
        self.assertEqual(raised.exception.code, 'OCCURRENCE_ON_GENE_STEP')

    def test_a_snapshot_declaring_mentions_gene_reports_refuses_by_name(self):
        ingest = json.loads(json.dumps(self.ingest))
        ingest['provenance']['labels'][gc.GENE_LABEL] = 'genetic_result,mention'
        good = {'profile': gc.GENE_CODED_PROFILE, 'steps': {'G': {'gene': GENE}}, 'exclude_kinds': KINDS}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, Path(tmp) / 's')) as store:
            for call in (lambda: sc.match(store, self.one, self.windows, conditions=good),
                         lambda: sc.candidates(store, self.one, good)):
                with self.assertRaises(es.StoreError) as raised:
                    call()
                self.assertEqual(raised.exception.code, 'GENE_REPORT_KIND_EXCLUDED')

    def test_output_and_context(self):
        q = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'G', 'concept': 'g:SCN1A'}, {'id': 'A', 'concept': 'm:freq'}],
             'relations': []}
        c = {'profile': gc.GENE_CODED_PROFILE, 'steps': {'G': {'gene': GENE}, 'A': trc.rel(unit='/month', within=75 * DAY)},
             'exclude_kinds': KINDS}
        out = sc.match(self.store, q, self.windows, conditions=c)
        self.assertEqual(out['excluded_kinds'], {'kinds': KINDS, 'rule': gc.GENE_CODED_RULE})
        self.assertEqual(out['derived_steps']['profile'], gc.GENE_CODED_PROFILE)
        self.assertEqual(out['derived_steps']['steps'], {'A': {'relative': 'rise'}, 'G': {'gene': 'first_not_negative'}})
        for path in (gc.MODULE_PATH, rc.MODULE_PATH, cc.MODULE_PATH):
            self.assertEqual(out['context']['artifacts'][path], hashlib.sha256((ROOT / path).read_bytes()).hexdigest())
        self.assertEqual(out['context']['exclude_rule'], es._digest(gc.GENE_CODED_RULE))
        self.assertEqual(out['context']['gene_rules'], es._digest(gc.GENE_RULES))
        plain = sc.match(self.store, q, self.windows, conditions=gcc.gene_conditions(c))
        self.assertNotIn('excluded_kinds', plain)
        self.assertNotIn('exclude_rule', plain['context'])
        self.assertNotEqual(out['context_id'], plain['context_id'])

    def test_the_dispatcher_routes_the_joint_profile_to_the_gene_module(self):
        self.assertEqual(sc.GENE_CODED_PROFILE, gc.GENE_CODED_PROFILE)
        self.assertEqual(sc.profiles(), [*rc.CONDITIONS_PROFILES, gc.GENE_PROFILE, sc.CODED_PROFILE, gc.GENE_CODED_PROFILE])
        for seed in range(3):
            query, conditions, windows = case(seed, 'fixture')
            for workers in (1, 2):
                self.assertEqual(json.dumps(sc.match(self.store, query, windows, conditions=conditions, workers=workers)),
                                 json.dumps(gc.match(self.store, query, windows, conditions=conditions, workers=workers)))
            self.assertEqual(sc.candidates(self.store, query, conditions), gc.candidates(self.store, query, conditions))
            self.assertEqual(sc.match_context(self.store, query, windows, conditions),
                             gc.match_context(self.store, query, windows, conditions))


class NoChangeTests(unittest.TestCase):
    """D42, rule 2, inside the suite: every request without the joint profile is the answer of the module that answered
    it before (through D43's post-pass for a count step, D43b), at 1 and 2 workers, and the shared hooks without kinds
    are the store's own reads. (D38's and D39's
    boundary tests check the same for their modules; the differential against main's code, `d42_no_change.py`, checks
    gene-1.0 and 1.3 answers byte for byte against the code before D42.)"""

    def test_every_request_without_the_joint_profile_is_its_previous_modules_answer(self):
        ingest, store, _ = _STATE['fixture']
        q = {'profile': tg.PROFILE_ID, 'scope': 'any', 'steps': [{'id': 'A', 'concept': 'm:freq'},
                                                                 {'id': 'B', 'concept': 'c:febrile', 'occurrence': 'first'}],
             'relations': [{'id': 'R', 'kind': 'after', 'from': 'B', 'to': 'A', 'min': 0, 'max': 400 * DAY}]}
        windows = tgs.windows_for(random.Random('d42/boundary'), ingest)
        before = [(rc, None), (rc, {'profile': es.CONDITIONS_PROFILE, 'steps': {'A': {'unit': '/month', 'ge': '2'}}}),
                  (rc, {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': {'A': {'count': {'at_least': 2, 'within': 'window'}}}}),
                  (rc, {'profile': rc.CONDITIONS_PROFILE_1_2, 'steps': {'A': trc.rel(unit='/month', within=75 * DAY)}}),
                  (cc, {'profile': cc.CONDITIONS_PROFILE_1_3, 'exclude_kinds': KINDS,
                        'steps': {'A': {'count': {'at_least': 2, 'within': 'window'}}}})]
        for previous, conditions in before:
            for workers in (1, 2):
                answer = previous.match(store, q, windows, conditions=conditions, workers=workers)
                want = json.dumps(answer)
                # D42's comparison, byte for byte, on the routed module's own answer (the post-pass off, D43b).
                with patch.object(sc, 'COUNT_POST_PASS', False):
                    self.assertEqual(json.dumps(sc.match(store, q, windows, conditions=conditions, workers=workers)),
                                     want)
                # With it on, a count request is that answer through D43's post-pass (D43b).
                dispatched = (scn.post_pass(store, q, windows, es.CHUNK_PATIENTS, conditions, answer, workers)
                              if scn.has_counts(conditions) else answer)
                self.assertEqual(json.dumps(sc.match(store, q, windows, conditions=conditions, workers=workers)),
                                 json.dumps(dispatched))
                if previous is rc:  # the gene module hands every request it does not answer to the relative module
                    self.assertEqual(json.dumps(gc.match(store, q, windows, conditions=conditions, workers=workers)),
                                     want)
            self.assertEqual(sc.candidates(store, q, conditions), previous.candidates(store, q, conditions))

    def test_the_hooks_without_kinds_are_the_stores_own_reads(self):
        """`derived_inputs` with no kinds loads what `store_conditions_relative._derived_chunk` loads, and `_comparability`
        with no kinds and no recorders is `execution_store._comparability`: the gene profile's path is the old one."""
        ingest, store, _ = _STATE['fixture']
        ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
        linked = {i for i, in store.query('SELECT patient_idx FROM patients WHERE shared_variables')}
        idxs = sorted(ids.values())
        for seed in range(10):
            query, conditions, windows = case(seed, 'fixture')
            request = gc.Request(query, gcc.gene_conditions(conditions))
            tags = request.used
            events = (es._load_events(store, idxs, request.load, sorted(linked), tags) if tags
                      else es._load_events(store, idxs, request.load, sorted(linked)))
            want = (events, es._mentions(store, idxs), rc._readings(store, idxs, request) if request.relative else {})
            self.assertEqual(cc.derived_inputs(store, request, idxs, sorted(linked), ()), want)
            by_idx = {ids[p]: w for p, w in windows.items()}
            nones = sorted(i for i, w in by_idx.items() if w is not None)
            plain = {sid: cs for sid, cs in request.load.items() if sid not in request.genes}
            self.assertEqual(list(cc._comparability(store, plain, nones, by_idx, (), tags=tags)),
                             list(es._comparability(store, plain, nones, by_idx, tags=tags)), seed)


def adhoc(root, small=20, first=42_000, defects=1, snapshots=(('fixture', 150), ('shared', 100), ('cohort', 150))):
    """The ruled soundness measurements (decision log, D42, rule 1), after `setUpModule()`: per generator `small`
    snapshots of 1,000 cases from seed `first` (worlds check, reference, pruning off, the store against itself); each
    planted defect on `defects` snapshots per generator at 2,000 cases each pair and on 40 fixture requests; the
    snapshot requests at 1 and 2 workers, at `decided` and with pruning off, and against the store without mentions.
    Returns a summary of counts."""
    out = {'small': {}, 'defects': {}, 'snapshots': {}}
    for generator in gcc.GENERATORS:
        violations, mismatches, mix, cases = gcc.run_small(Path(root), generator, small, first=first, itself=True)
        out['small'][generator] = {'cases': cases, 'violations': len(violations), 'mismatches': len(mismatches),
                                   'mix': dict(mix), 'first_violations': violations[:3], 'first_mismatches': mismatches[:3]}
    for defect, (module, switch, value) in gcc.DEFECTS.items():
        mine = out['defects'][defect] = {}
        for generator in gcc.GENERATORS:
            violations, mismatches, _, cases = gcc.run_small(Path(root), generator, 2 * defects, first=first + 1000,
                                                             defect=defect, pruning=False)
            mine[generator] = {'cases': cases, 'violations': len(violations), 'mismatches': len(mismatches)}
        with patch.object(module, switch, value):
            wrong, _, _ = compare('fixture', range(40), itself=False)
        mine['fixture_requests'] = {'requests': 40, 'mismatching_results': len(wrong)}
    for name, n in snapshots:
        wrong, apart, mix = compare(name, range(n))
        decided, _, _ = compare(name, range(n), itself=False, detail='decided')
        _, store, _ = _STATE[name]
        two = unpruned = pruned = 0
        for seed in range(n):
            query, conditions, windows = case(seed, name)
            base = sc.match(store, query, windows, conditions=conditions)
            two += json.dumps(sc.match(store, query, windows, conditions=conditions, workers=2, chunk=9)) != json.dumps(base)
            with patch.object(es, '_PRUNE', False):
                unpruned += json.dumps(sc.match(store, query, windows, conditions=conditions)) != json.dumps(base)
            request = gc.Request(query, conditions)
            pq = tg.PreparedQuery(request.query)
            ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
            by_idx = {ids[p]: w for p, w in windows.items()}
            live = [i for i in gc._candidates(store, pq, request)['patient_idx'] if by_idx[i] is not None]
            pruned += len(gc._prune(store, pq, live, by_idx, request, tags=request.used, derived=request.derived_used))
        out['snapshots'][name] = {'requests': n, 'results': sum(mix.values()), 'reference_mismatches': len(wrong),
                                  'decided_mismatches': len(decided), 'two_workers_differ': two,
                                  'pruning_off_differs': unpruned, 'candidates_pruned': pruned,
                                  'store_without_mentions_differs': len(apart), 'mix': dict(mix)}
    return out


if __name__ == '__main__':
    unittest.main()
