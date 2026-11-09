"""`PreparedReference.match` equals `execution_store_reference.exhaustive_match`, result for result."""
import json
import random
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from . import chartevents_fixture as fx
from . import execution_store_reference as ref
from . import mimic_chartevents as chart
from . import prepared_reference as pr
from . import synthetic_ingest as si
from . import test_execution_store as tes

_STATE = {}


def setUpModule():
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run([sys.executable, str(tes.GENERATOR), '--output', str(Path(tmp) / 'cohort.json')], check=True,
                       capture_output=True, cwd=tes.ROOT)
        cohort = json.loads((Path(tmp) / 'cohort.json').read_text(encoding='utf-8'))
        _STATE['synthetic'] = si.synthetic_ingest(cohort)
        fx.write(Path(tmp) / 'chart')
        run = chart.Run(Path(tmp) / 'chart', 'synthetic-chartevents-2.2')
        patients = [b['patient'] for b in run.batches(Path(tmp), 2)]
        _STATE['chartevents'] = {**run.header(), 'provenance': {}, 'patients': patients}


def compare(test, ingest, seeds, first_seed=0):
    pool = tes.concept_pool(ref.build_tables(ingest))
    prepared = pr.PreparedReference(ingest)
    statuses, seconds = set(), [0.0, 0.0]
    for seed in range(first_seed, first_seed + seeds):
        rng = random.Random(seed)
        query, windows = tes.random_query(rng, pool, ingest), tes.random_windows(rng, ingest)
        t = time.perf_counter()
        want = ref.exhaustive_match(ingest, query, windows)
        seconds[0] += time.perf_counter() - t
        t = time.perf_counter()
        got = prepared.match(query, windows)
        seconds[1] += time.perf_counter() - t
        test.assertEqual(got, want, seed)
        statuses |= {w['status'] for w in want}
        statuses |= {w.get('reason') for w in want if w['status'] == 'not_comparable'}
    return statuses, seconds


class EqualityTests(unittest.TestCase):
    def test_synthetic_cohort(self):
        statuses, _ = compare(self, _STATE['synthetic'], 60)
        self.assertTrue({'exact', 'none', 'not_comparable'} <= statuses, statuses)

    def test_shared_variable_cohort(self):
        # Events no step can bind may link two that can: such patients keep every event.
        ingest = tes.shared_variable_cohort(_STATE['synthetic'], 7)
        self.assertTrue(any(ref.shares_variables(p) for p in ingest['patients']))
        compare(self, ingest, 60, first_seed=1000)

    def test_chartevents_points_and_excluded_records(self):
        statuses, seconds = compare(self, _STATE['chartevents'], 300)
        self.assertIn('ADMISSION_EXCLUDED_RECORDS', statuses)
        self.assertIn('exact', statuses)

    def test_an_unbindable_event_linking_two_steps(self):
        # X ends when Z starts, Y starts when Z (proper) ends, and no step can bind Z: Y starts at least
        # 1 after X ends, so "B exactly 10 after A" is impossible. Without Z, X and Y are unlinked and it
        # is possible, so a reference that filtered this patient by concept would answer wrongly.
        def event(eid, concept, start, end=None, **names):
            out = {'event_id': eid, 'kind': 'k', 'source': 's', 'concepts': [concept], 'start': start,
                   'evidence': {}, **names}
            if end is not None:
                out['end'] = end
            return out
        ingest = {'profile': 'execution-store-ingest-2.0', 'dataset_id': 'link',
                  'closure': {'version': '1', 'ancestors': {'a': [], 'b': [], 'z': []}},
                  'patients': [{'patient_id': 'P', 'clock_id': 'c', 'coverage': [{'source': 's', 'lo': 0, 'hi': 100,
                                                                                  'basis': 'declared'}],
                                'events': [event('X', 'a', [0, 0], [10, 20], end_var='v', proper=True),
                                           event('Z', 'z', [10, 20], [10, 30], start_var='v', end_var='w', proper=True),
                                           event('Y', 'b', [10, 30], start_var='w')]}]}
        query = {'profile': 'trajectory-graph-1.0', 'scope': 'any',
                 'steps': [{'id': 'A', 'concept': 'a'}, {'id': 'B', 'concept': 'b'}],
                 'relations': [{'id': 'R', 'kind': 'after', 'from': 'A', 'to': 'B', 'min': 10, 'max': 10}],
                 'relaxations': [], 'budget': 0}
        windows = {'P': {'start': 0, 'end': 100}}
        want = ref.exhaustive_match(ingest, query, windows)
        self.assertEqual(pr.PreparedReference(ingest).match(query, windows), want)
        self.assertEqual(want[0]['status'], 'none')
        prepared = pr.PreparedReference(ingest)
        unlinked = prepared.engine_events(dict(prepared.patients[0], shares=False), {'a', 'b'})
        self.assertEqual([e['id'] for e in unlinked], ['X', 'Y'])
        self.assertEqual(pr.tg.match(query, {'patient_id': 'P', 'window': windows['P'], 'events': unlinked})['status'],
                         'possible')

    def test_shared_variables_keep_every_event(self):
        ingest = tes.shared_variable_cohort(_STATE['synthetic'], 7)
        prepared = pr.PreparedReference(ingest)
        shared = next(p for p in prepared.patients if p['shares'])
        alone = next(p for p in prepared.patients if not p['shares'] and len(p['events']) > 3)
        wanted = {alone['events'][0]['concepts'][0]}
        self.assertEqual(prepared.engine_events(shared, wanted), shared['events'])
        self.assertLess(len(prepared.engine_events(alone, wanted)), len(alone['events']))


if __name__ == '__main__':
    unittest.main()
