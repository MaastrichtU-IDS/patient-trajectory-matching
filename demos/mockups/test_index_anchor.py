"""D52: the index anchor as a hidden step. The rewrite's reference (`index_anchor_reference.py`), the server's narrowing
(`index_hull.py`), the server's request check, and the worlds harness on a few snapshots (`tools/d52_measure.py`).

Tests that build a store skip without DuckDB.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
for p in (str(ROOT), str(HERE), str(ROOT / 'tools')):
    if p not in sys.path:
        sys.path.insert(0, p)

import index_anchor_reference as ref  # noqa: E402

HAVE_DUCKDB = importlib.util.find_spec('duckdb') is not None
DAY = 1440


def ev(event, which='first', n=1, offset=0, unit='month'):
    return {'kind': 'event', 'event': event, 'which': which, 'n': n, 'offset': offset, 'unit': unit}


SPAN = lambda amount, unit='month': {'kind': 'span', 'amount': amount, 'unit': unit}  # noqa: E731


def base(scope='from_start', relations=(), relaxations=()):
    return {'query': {'profile': 'trajectory-graph-1.0', 'scope': scope, 'steps': [{'id': 'A', 'concept': 'seizure', 'occurrence': 'any'}],
                      'relations': list(relations), 'relaxations': list(relaxations), 'budget': '1', 'search_budget': 40000},
            'conditions': None, 'windows': {'P': {'start': 1, 'end': 2}}}


PATIENT = {'P': {'birth_date': '2019-01-01', 'follow_up': {'last_contact': '2024-01-01'}}}
LAST = (5 * 365 + 1) * DAY  # 2019-01-01 to 2024-01-01


class RewriteTests(unittest.TestCase):
    def rel(self, out):
        return [(r['id'], r['from'], r['to'], r.get('min'), r.get('max')) for r in out['query']['relations']]

    def test_the_default_period_is_one_hidden_step_with_scope_and_eligibility_relations(self):
        out = ref.rewrite(base(), {'start': ev('seizure'), 'end': SPAN(24)}, [{'id': 'A'}], PATIENT)
        k = ref.to_grid(24 * 30.44)
        self.assertEqual(out['query']['scope'], 'any')
        self.assertEqual([s['id'] for s in out['query']['steps']], ['A', '@index_start'])
        self.assertEqual(self.rel(out), [('index:scope:A', 'START', 'A', 0, None), ('index:scope-start:A', '@index_start', 'A', 0, None),
                                         ('index:followed', '@index_start', 'END', k, None), ('index:born', 'START', '@index_start', -k, None)])
        self.assertEqual(out['windows'], {'P': {'start': 0, 'end': LAST}})
        self.assertEqual(out['index'], {'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0},
                                        'end': {'step': '@index_start', 'offset': k}})

    def test_within_reads_both_sides_of_one_step_and_after_reads_the_end(self):
        within = ref.rewrite(base('within'), {'start': ev('seizure', offset=-1), 'end': SPAN(2)}, [{'id': 'A'}], PATIENT)
        a, b = ref.to_grid(-30.44), ref.to_grid(-30.44 + 2 * 30.44)
        self.assertIn(('index:scope-start:A', '@index_start', 'A', a, b), self.rel(within))
        after = ref.rewrite(base('after'), {'start': SPAN(12), 'end': ev('seizure', 'last')}, [{'id': 'A'}], PATIENT)
        self.assertIn(('index:scope-end:A', '@index_end', 'A', 0, None), self.rel(after))
        self.assertNotIn('index:scope:A', [r[0] for r in self.rel(after)])

    def test_relations_from_start_and_end_shift_with_their_relaxations(self):
        b = base(relations=[{'id': 'edge:0', 'kind': 'after', 'from': 'START', 'to': 'A', 'min': 0, 'max': 10},
                            {'id': 'edge:1', 'kind': 'after', 'from': 'END', 'to': 'A', 'min': -5, 'max': 5}],
                 relaxations=[{'id': 'edge:0', 'kind': 'extend_max', 'cost': '0.25', 'relation': 'edge:0', 'max': 15}])
        out = ref.rewrite(b, {'start': ev('seizure', offset=1, unit='day'), 'end': SPAN(1, 'year')}, [{'id': 'A'}], PATIENT)
        rels = {r[0]: r for r in self.rel(out)}
        self.assertEqual(rels['edge:0'], ('edge:0', '@index_start', 'A', DAY, DAY + 10))
        # the end: the offset of 1 day plus a year, rounded once
        self.assertEqual(rels['edge:1'][3:], (ref.to_grid(1 + 365.25) - 5, ref.to_grid(1 + 365.25) + 5))
        self.assertEqual(out['query']['relaxations'][0]['max'], DAY + 15)

    def test_two_events_are_two_steps_ordered_and_one_event_twice_is_one(self):
        two = ref.rewrite(base(), {'start': ev('seizure'), 'end': ev('antiseizure')}, [{'id': 'A'}], PATIENT)
        self.assertEqual([s['id'] for s in two['query']['steps']][1:], ['@index_start', '@index_end'])
        self.assertIn(('index:ordered', '@index_start', '@index_end', 1, None), self.rel(two))
        once = ref.rewrite(base(), {'start': ev('seizure'), 'end': ev('seizure', 'nth', 1, 6)}, [{'id': 'A'}], PATIENT)
        self.assertEqual([s['id'] for s in once['query']['steps']][1:], ['@index_start'])
        backwards = ref.rewrite(base('within'), {'start': ev('seizure', offset=2), 'end': ev('seizure', offset=1)}, [{'id': 'A'}], PATIENT)
        self.assertEqual(backwards['windows'], {'P': None})  # no period can be placed
        self.assertNotIn('index:scope-start:A', [r[0] for r in self.rel(backwards) if r[3] is not None and r[4] is not None])

    def test_constant_sides(self):
        m1 = ref.rewrite(base('after'), {'start': ev('seizure'), 'end': {'kind': 'age', 'amount': 3, 'unit': 'year'}}, [{'id': 'A'}], PATIENT)
        self.assertIn(('index:ordered', '@index_start', 'END', 1, None), self.rel(m1))
        self.assertIn(('index:scope-end:A', 'END', 'A', 0, None), self.rel(m1))
        self.assertEqual(m1['windows']['P'], {'start': 0, 'end': ref.to_grid(3 * 365.25)})
        late = ref.rewrite(base(), {'start': ev('seizure'), 'end': {'kind': 'age', 'amount': 9, 'unit': 'year'}}, [{'id': 'A'}], PATIENT)
        self.assertIsNone(late['windows']['P'])  # past last contact whatever the event's time
        m2 = ref.rewrite(base(), {'start': {'kind': 'age', 'amount': 1, 'unit': 'year'}, 'end': ev('seizure', 'last')}, [{'id': 'A'}], PATIENT)
        self.assertIn(('index:ordered', 'START', '@index_end', 1, None), self.rel(m2))
        self.assertEqual(m2['windows']['P'], {'start': ref.to_grid(365.25), 'end': LAST})

    def test_an_nth_event_is_a_derived_occurrence(self):
        out = ref.rewrite(base(), {'start': ev('seizure', 'nth', 3), 'end': SPAN(12)}, [{'id': 'A'}], PATIENT)
        self.assertEqual(out['query']['steps'][-1]['occurrence'], 'any')
        self.assertEqual(out['conditions'], {'profile': 'execution-store-conditions-1.1', 'steps': {'@index_start': {'occurrence': {'nth': 3}}}})

    def test_the_refusals_in_their_order(self):
        concepts = {'gene:SCN1A': {'kind': 'gene'}}
        span = SPAN(12)
        self.assertEqual(ref.refusal({'start': ev('@any_diagnosis'), 'end': span}, [], 'off', 'store', concepts), 'INDEX_ANCHOR_ANY_UNSUPPORTED')
        self.assertEqual(ref.refusal({'start': ev('gene:SCN1A'), 'end': span}, [], 'off', 'store', concepts), 'INDEX_ANCHOR_GENE_UNSUPPORTED')
        self.assertEqual(ref.refusal({'start': ev('seizure', 'nth', 2), 'end': span}, [], 'present', 'store', concepts), 'FREE_TEXT_COUNT_UNSUPPORTED')
        self.assertIsNone(ref.refusal({'start': ev('seizure', 'nth', 2), 'end': span}, [], 'off', 'store', concepts))
        count = [{'id': 'A', 'occ': 'count', 'within': 'period'}]
        self.assertEqual(ref.refusal({'start': ev('seizure'), 'end': span}, count, 'off', 'store', concepts), 'INDEX_PERIOD_COUNT_UNSUPPORTED')
        self.assertEqual(ref.refusal({'start': {'kind': 'date', 'date': '2020-01-01'}, 'end': ev('seizure')}, [], 'off', 'store', concepts),
                         'INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED')
        self.assertEqual(ref.refusal({'start': ev('seizure'), 'end': span}, [], 'off', None, concepts), 'FREE_TEXT_CODED_ONLY_UNSUPPORTED')
        self.assertIsNone(ref.refusal({'start': {'kind': 'birth'}, 'end': {'kind': 'age', 'amount': 6, 'unit': 'year'}}, count, 'off', None, concepts))

    def test_the_narrowing_without_sql(self):
        events = [{'kind': 'condition', 'concepts': ['x'], 'start': [5, 7]}, {'kind': 'mention', 'concepts': ['x'], 'start': [0, 9]},
                  {'kind': 'condition', 'concepts': ['y'], 'start': [3, 3]}]
        anc = {'x': ['z'], 'y': ['z'], 'z': []}
        self.assertEqual(ref.anchor_range(events, 'z', anc, 'first'), (0, 3, 3))
        self.assertEqual(ref.anchor_range(events, 'z', anc, 'first', None, ('mention',)), (3, 3, 2))
        self.assertEqual(ref.anchor_range(events, 'x', anc, 'last', None, ('mention',)), (5, 7, 1))
        self.assertEqual(ref.anchor_range(events, 'z', anc, 'any', {'nth': 2}, ('mention',)), (5, 7, 2))
        self.assertEqual(ref.anchor_range(events, 'z', anc, 'any', {'nth': 3}, ('mention',)), (None, None, 2))
        index = {'start': {'step': 'S', 'offset': -1}, 'end': {'step': 'S', 'offset': 4}}
        self.assertEqual(ref.narrow({'start': 0, 'end': 20}, index, {'S': (3, 5, 2)}), ({'start': 2, 'end': 9}, None))
        self.assertEqual(ref.narrow({'start': 0, 'end': 20}, index, {'S': (None, None, 0)}), (None, 'INDEX_ANCHOR_NOT_RECORDED'))
        self.assertEqual(ref.narrow({'start': 0, 'end': 1}, index, {'S': (3, 5, 2)}), (None, 'INDEX_NO_POSSIBLE_PERIOD'))


class RequestCheckTests(unittest.TestCase):
    def test_the_server_takes_an_index_block_naming_a_step_and_whole_minutes(self):
        import store_trajectory as st
        q = {'steps': [{'id': 'A'}, {'id': '@index_start'}], 'relations': []}
        good = {'query': q, 'windows': {}, 'index': {'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0}, 'end': None}}
        st.check_request(good, {})
        for index, code in [({'rule': 'other', 'start': None, 'end': None}, 'INDEX_MUST_NAME_ITS_RULE_AND_SIDES'),
                            ({'rule': 'index_anchor_v1', 'start': {'step': 'X', 'offset': 0}, 'end': None}, 'INDEX_SIDE_MUST_NAME_A_STEP'),
                            ({'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0.5}, 'end': None}, 'TIME_MUST_BE_WHOLE_MINUTES'),
                            ({'rule': 'index_anchor_v1', 'start': None, 'end': None}, 'INDEX_SIDE_MUST_NAME_A_STEP')]:
            with self.assertRaises(st.RequestError) as caught:
                st.check_request({**good, 'index': index}, {})
            self.assertEqual(str(caught.exception), code)


@unittest.skipUnless(HAVE_DUCKDB, 'needs DuckDB')
class StoreTests(unittest.TestCase):
    def test_the_sql_narrowing_equals_the_reference_and_counts_its_calls(self):
        import random
        import d52_measure as m
        import index_hull as hull
        from patterns import execution_store as es
        rng = random.Random('d52-unit')
        ingest, statics = m.tiny_ingest(rng, 'unit', 30)
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, Path(tmp) / 's')) as store:
            for occ, derived, kinds in [('first', None, ()), ('last', None, ('mention',)), ('any', {'nth': 2}, ('mention',)),
                                        ('any', {'nth_last': 2}, ())]:
                query = {'steps': [{'id': '@index_start', 'concept': 'z', 'occurrence': occ}]}
                conditions = {'steps': {'@index_start': {'occurrence': derived}} if derived else {}, 'exclude_kinds': list(kinds)}
                index = {'start': {'step': '@index_start', 'offset': -1}, 'end': {'step': '@index_start', 'offset': 3}}
                windows = {pid: {'start': 0, 'end': s['last']} for pid, s in statics.items()}
                before = hull.CALLS['narrow']
                got, reasons, _ = hull.narrow(store, query, conditions, index, windows, threads=2)
                self.assertEqual(hull.CALLS['narrow'], before + 1)
                self.assertEqual(store.db.execute("SELECT current_setting('threads')").fetchone()[0], 1)
                for p in ingest['patients']:
                    rng_ = ref.anchor_range(p['events'], 'z', m.ANCESTORS, occ, derived, kinds)
                    self.assertEqual((got[p['patient_id']], reasons.get(p['patient_id'])),
                                     ref.narrow(windows[p['patient_id']], index, {'@index_start': rng_}), (occ, derived, p['patient_id']))

    def test_the_server_narrows_an_indexed_request_and_leaves_any_other_alone(self):
        import json
        import index_hull as hull
        import store_trajectory as st
        cohort = json.loads(st.COHORT.read_text())
        pid = cohort['patients'][0]['patient_id']
        query = {'profile': 'trajectory-graph-1.0', 'scope': 'any', 'steps': [{'id': 'A', 'concept': 'seizure', 'occurrence': 'any'},
                 {'id': '@index_start', 'concept': 'seizure', 'occurrence': 'first'}],
                 'relations': [{'id': 'index:scope-start:A', 'kind': 'after', 'from': '@index_start', 'to': 'A', 'min': 0},
                               {'id': 'index:followed', 'kind': 'after', 'from': '@index_start', 'to': 'END', 'min': 1440}],
                 'relaxations': [], 'budget': '0', 'search_budget': 40000}
        index = {'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0}, 'end': {'step': '@index_start', 'offset': 1440}}
        with tempfile.TemporaryDirectory() as tmp:
            server = st.TrajectoryStore(root=tmp).start()
            try:
                before = hull.CALLS['narrow']
                plain = server.match({'query': query, 'windows': {pid: {'start': 0, 'end': 10 ** 7}}})
                self.assertEqual(hull.CALLS['narrow'], before)
                self.assertNotIn('index', plain)
                answer = server.match({'query': query, 'windows': {pid: {'start': 0, 'end': 10 ** 7}}, 'index': index})
                self.assertEqual(hull.CALLS['narrow'], before + 1)
                self.assertEqual(answer['index']['rule'], 'index_hull_v1')
                w = answer['index']['windows'][pid]
                self.assertTrue(w is None or (0 <= w[0] <= w[1] <= 10 ** 7))
            finally:
                server.close()

    def test_the_worlds_harness_finds_no_failing_case_and_catches_a_planted_defect(self):
        import d52_measure as m
        clean = m.worlds(400, 'unit-test', quiet=True)
        self.assertEqual(clean['failing_cases'], 0, clean['examples'])
        self.assertEqual(clean['h1_h3_mismatches'], 0, clean['mismatch_examples'])
        planted = m.worlds(1200, 'unit-test-defect', defect='eligibility_dropped', quiet=True)
        self.assertGreater(planted['failing_cases'], 0)


if __name__ == '__main__':
    unittest.main()
