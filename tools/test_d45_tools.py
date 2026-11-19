"""D45's tools (decision log, D45): the latency tool's verdict applies the committed bars to control-normalised ratios
and is met only when the protocol was followed and every answer is stable (`tools/benchmark_coded_default.py`); its
structural check counts only exclusion work, fails on a reference or control that does any, on a candidate that does
none, on a by-name import and on a pool entry point that reaches the module; the kept build is checked against this
tree; and rule 1.1's dispatcher harness (`tools/d45_measure.py`) runs and applies D43's movement rule. Nothing here times
anything. Tests that build a store skip without DuckDB."""
import ast
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
for p in (str(ROOT), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    import duckdb  # noqa: F401
except ImportError:  # pragma: no cover - the store extra is optional
    duckdb = None

import benchmark_coded_default as bd  # noqa: E402

TEMPLATES = ['sequence', 'event frequency', 'blank', 'tree', 'graph']
CONTROLS = ['control a', 'control b']


def arm(seconds, digest='d'):
    return {'requests': {name: {'seconds': list(s), 'digests': [digest + name] * (len(s) + 1),
                                'first_run_seconds': s[0]} for name, s in seconds.items()},
            'parent_peak_rss_bytes': 1, 'worker_peak_rss_bytes': 0}


def records(candidate, reference=1.0, control_drift=1.0, repeats=4, runs=20, workers=1, slow=None):
    """ABBA records: each template at `reference` s in the reference arm and `candidate` times that in the candidate
    arm (or `slow[name]` times), the controls at 1 s and `control_drift` s."""
    out = []
    for repeat in range(repeats):
        ref = {**{n: [reference] * runs for n in TEMPLATES}, **{c: [1.0] * runs for c in CONTROLS}}
        cand = {**{n: [reference * (slow or {}).get(n, candidate)] * runs for n in TEMPLATES},
                **{c: [control_drift] * runs for c in CONTROLS}}
        out.append({'workers': workers, 'repeat': repeat,
                    'order': ('reference', 'candidate') if repeat % 2 == 0 else ('candidate', 'reference'),
                    'reference': arm(ref, 'r'), 'candidate': arm(cand, 'c')})
        for c in CONTROLS:  # a control's answer is the same in both arms
            for side in ('reference', 'candidate'):
                out[-1][side]['requests'][c]['digests'] = ['ctl' + c] * (runs + 1)
    return out


def verdict(recs, p95=bd.PAGE_P95_BAR, runs=20, repeats=4, workers=1, **kw):
    return bd.size_verdict(recs, TEMPLATES, CONTROLS, workers, p95, runs, bd.PAGE_RUNS_MIN, repeats, **kw)[1]


class SizeVerdictTests(unittest.TestCase):
    def test_within_the_bar_is_met(self):
        v = verdict(records(1.5))
        self.assertTrue(v['protocol']['met'])
        self.assertTrue(all(r['met'] for r in v['requests'].values()))
        self.assertAlmostEqual(v['requests']['blank']['normalised_by_repeat'][0], 1.5)

    def test_the_ratio_is_normalised_by_the_controls(self):
        # Raw 2.4, the controls 1.25 slower in the candidate's process: normalised 1.92, met.
        v = verdict(records(2.4, control_drift=1.25))
        self.assertAlmostEqual(v['requests']['sequence']['normalised_by_repeat'][1], 2.4 / 1.25)
        self.assertTrue(v['requests']['sequence']['every_pair_at_most'])
        self.assertAlmostEqual(v['controls']['geometric_mean_by_repeat'][0], 1.25)

    def test_above_the_bar_misses(self):
        v = verdict(records(1.0, slow={'event frequency': 2.2}))
        self.assertFalse(v['requests']['event frequency']['met'])
        self.assertTrue(v['requests']['blank']['met'])

    def test_the_absolute_bar(self):
        v = verdict(records(1.5, reference=1.5))  # candidate 2.25 s at the page's size, bar 2 s
        self.assertFalse(v['requests']['tree']['p95_met'])
        self.assertTrue(verdict(records(1.5, reference=1.5), p95=bd.SCALE_P95_BAR)['requests']['tree']['p95_met'])

    def test_the_protocol(self):
        self.assertFalse(verdict(records(1.0, repeats=2), repeats=2)['protocol']['met'])
        self.assertFalse(verdict(records(1.0, runs=5), runs=5)['protocol']['met'])
        recs = records(1.0)
        del recs[0]['candidate']['requests']['blank']
        with self.assertRaises(KeyError):
            verdict(recs)

    def test_the_ruled_worker_count_is_required(self):
        # D45 review, S2: a run without 14 workers fails the protocol, whatever count it read its statistics at.
        self.assertFalse(verdict(records(1.0), ruled_run=False)['protocol']['met'])
        self.assertTrue(verdict(records(1.0), ruled_run=True)['protocol']['met'])

    def test_every_template_must_be_in_the_requests_file(self):
        self.assertTrue(verdict(records(1.0), expected=TEMPLATES)['protocol']['met'])
        self.assertFalse(verdict(records(1.0), expected=[*TEMPLATES, 'another'])['protocol']['met'])
        self.assertEqual(sorted(bd.PAGE_TEMPLATES), sorted(TEMPLATES))
        self.assertEqual(len(bd.SCALE_SHAPES), 5)

    def test_an_unstable_answer_misses(self):
        recs = records(1.0)
        recs[2]['candidate']['requests']['graph']['digests'][3] = 'other'
        v = verdict(recs)
        self.assertFalse(v['requests']['graph']['met'])
        self.assertTrue(v['requests']['tree']['met'])

    def test_a_control_answered_differently_breaks_the_protocol(self):
        recs = records(1.0)
        recs[1]['reference']['requests']['control a']['digests'] = ['x'] * 21
        self.assertFalse(verdict(recs)['protocol']['met'])

    def test_the_profiles(self):
        data = {'page': {n: {} for n in TEMPLATES}}
        page = verdict(records(1.0, slow={'tree': 3.0}))
        scale = verdict(records(1.0), p95=bd.SCALE_P95_BAR)
        out = bd.verdict_of(page, scale, {'met': True}, {'met': True}, data)
        self.assertTrue(out['coded'])
        self.assertFalse(out['joint'])
        self.assertFalse(bd.verdict_of(page, scale, {'met': False}, {'met': True}, data)['coded'])


class StructuralTests(unittest.TestCase):
    DATA = {'page': {'sequence': {}}, 'page_controls': {'c': {}}, 'scale': {'blank': {}}}

    def raw(self, ref=0, ctl=0, cand=5, positive=bd.POSITIVE):
        reading = lambda n, called=(): {'calls': n, 'nonzero': {f: 1 for f in called}}
        return {'targets': ['match'], 'page': {'sequence (reference)': reading(ref), 'c': reading(ctl),
                                               'sequence (candidate)': reading(cand, positive)},
                'scale': {'blank (reference)': {'1': reading(ref), '14': reading(ref)},
                          'blank (candidate)': {'1': reading(cand, positive), '14': reading(cand, ('match',))},
                          '1.1 sequence': {'1': reading(ctl), '14': reading(ctl)},
                          'plain sequence': {'1': reading(0), '14': reading(0)}}}

    def test_met(self):
        self.assertTrue(bd.structural_check(self.raw(), self.DATA, [], [])['met'])

    def test_a_reference_doing_exclusion_work_fails(self):
        self.assertFalse(bd.structural_check(self.raw(ref=1), self.DATA, [], [])['met'])

    def test_a_control_doing_exclusion_work_fails(self):
        self.assertFalse(bd.structural_check(self.raw(ctl=2), self.DATA, [], [])['met'])

    def test_a_candidate_doing_none_fails(self):
        self.assertFalse(bd.structural_check(self.raw(cand=0), self.DATA, [], [])['met'])

    def test_a_candidate_missing_an_entry_point_at_one_worker_fails(self):
        # D45 review, S3: at one worker each candidate calls every POSITIVE entry point, not just any function.
        for gone in bd.POSITIVE:
            raw = self.raw(positive=[f for f in bd.POSITIVE if f != gone])
            check = bd.structural_check(raw, self.DATA, [], [])
            self.assertFalse(check['met'])
            self.assertTrue(any(gone in f for f in check['failures']))

    def test_bypasses_fail(self):
        self.assertFalse(bd.structural_check(self.raw(), self.DATA, ['patterns/x.py imports [match]'], [])['met'])
        self.assertFalse(bd.structural_check(self.raw(), self.DATA, [], ['patterns.execution_store:_worker_unit'])['met'])

    def test_a_missing_reading_fails(self):
        raw = self.raw()
        del raw['scale']['plain sequence']['14']
        self.assertFalse(bd.structural_check(raw, self.DATA, [], [])['met'])

    def test_this_tree_has_no_bypass(self):
        self.assertEqual(bd.bypasses(), ([], []))

    def test_the_alias_detector(self):
        tree = ast.parse('from . import store_conditions_coded as cc\ndef _worker_unit(u):\n    return cc.match(u)\n')
        self.assertEqual(bd._coded_aliases(tree), {'cc'})
        lazy = ast.parse('def f():\n    from . import store_conditions_coded\n')
        self.assertEqual(bd._coded_aliases(lazy.body[0]), {'store_conditions_coded'})
        # The gene module's pool unit does reach it (the joint profile): the detector sees that.
        gene = ast.parse((ROOT / 'patterns' / 'store_conditions_gene.py').read_text())
        self.assertIn('cc', bd._coded_aliases(gene))

    def test_targets(self):
        targets = bd.exclusion_targets()
        self.assertIn('_left_out', targets)
        self.assertIn('Request.__init__', targets)
        self.assertNotIn('is_coded_request', targets)
        self.assertTrue(set(bd.TIMED) <= set(targets))

    def test_doing_exclusion(self):
        def with_kinds(store, idxs, kinds):
            pass

        def with_request(store, found, request):
            pass

        class Req:
            exclude = ()
        self.assertFalse(bd.doing_exclusion(with_kinds, (1, 2, ()), {}))
        self.assertTrue(bd.doing_exclusion(with_kinds, (1, 2), {'kinds': ('mention',)}))
        self.assertFalse(bd.doing_exclusion(with_request, (1, 2, Req()), {}))
        Req.exclude = ('mention',)
        self.assertTrue(bd.doing_exclusion(with_request, (1, 2, Req()), {}))
        self.assertTrue(bd.doing_exclusion(lambda store: None, (1,), {}))


class BuildCheckTests(unittest.TestCase):
    def test_build_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / 'size-1' / 'store' / 'abc'
            snap.mkdir(parents=True)
            import hashlib
            digest = hashlib.sha256((ROOT / 'patterns/execution_store.py').read_bytes()).hexdigest()
            (snap / 'manifest.json').write_text(json.dumps({'snapshot_id': 'abc', 'builder': {
                'artifacts': {'patterns/execution_store.py': digest}}}))
            build = snap.parent.parent / 'build.json'
            build.write_text(json.dumps({'record': {'modules': {'patterns/execution_store.py': digest}, 'files': {}}}))
            self.assertTrue(bd.build_check(snap)['met'])
            build.write_text(json.dumps({'record': {'modules': {'patterns/execution_store.py': '0' * 64}, 'files': {}}}))
            self.assertFalse(bd.build_check(snap)['met'])
            build.unlink()
            self.assertFalse(bd.build_check(snap)['met'])


@unittest.skipIf(duckdb is None, 'needs DuckDB (patterns/requirements-store.lock.txt)')
class StoreTests(unittest.TestCase):
    def test_counters_see_exclusion_work_only(self):
        import random
        from patterns import coded_only_cases as co
        from patterns import execution_store as es
        from patterns import store_conditions as sc
        counters = bd.Counters()
        try:
            rng = random.Random('d45-counters')
            ingest = co.tiny_ingest(rng, 'counters', 12)
            with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, Path(tmp) / 's')) as store:
                for _ in range(6):
                    query, conditions = co.tiny_request(rng, 'coded-derived')
                    windows = co.tiny_windows(rng, ingest)
                    counters.reset()
                    sc.match(store, query, windows, conditions=co.reference_conditions(conditions))
                    self.assertEqual(counters.total(), 0, counters.read())
                    counters.reset()
                    sc.match(store, query, windows, conditions=conditions)
                    self.assertGreater(counters.total(), 0)
        finally:
            for owner, attr, real in counters.undo:
                setattr(owner, attr, real)

    def test_the_dispatcher_harness(self):
        import d45_measure as dm
        with tempfile.TemporaryDirectory() as tmp:
            for generator in dm.GENERATORS:
                out = dm.run_snapshot(Path(tmp), 7, generator, patients=12, requests=4)
                self.assertEqual(out['cases'], 48)
                for key in ('violations', 'mismatches', 'moved_wrongly', 'pruning', 'itself'):
                    self.assertEqual(out[key], [], f'{generator}: {key}')

    def test_the_movement_rule(self):
        import d45_measure as dm
        before = {'patient_id': 'p', 'status': 'unresolved', 'reason': dm.UNCERTAIN}
        self.assertTrue(dm.moved_ok({'patient_id': 'p', 'status': 'possible'}, before))
        self.assertFalse(dm.moved_ok({'patient_id': 'p', 'status': 'possible'},
                                     {'patient_id': 'p', 'status': 'unresolved', 'reason': 'OTHER'}))
        self.assertTrue(dm.moved_ok(before, dict(before)))


if __name__ == '__main__':
    unittest.main()
