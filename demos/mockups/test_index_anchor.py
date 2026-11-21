"""D52: the index anchor as a hidden step. The rewrite's reference (`index_anchor_reference.py`), the server's narrowing
(`index_hull.py`), the server's request check, and the worlds harness on a few snapshots (`tools/d52_measure.py`); D52c:
the server's simplification (`index_simplify.py`) and its pre-fill.

Tests that build a store skip without DuckDB.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

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

    def test_the_point_reasons_in_their_order(self):
        concepts = {'gene:SCN1A': {'kind': 'gene'}}
        span = SPAN(12)
        self.assertEqual(ref.point_reason({'start': ev('@any_diagnosis'), 'end': span}, [], 'off', 'store', concepts), 'INDEX_ANCHOR_ANY_UNSUPPORTED')
        self.assertEqual(ref.point_reason({'start': ev('gene:SCN1A'), 'end': span}, [], 'off', 'store', concepts), 'INDEX_ANCHOR_GENE_UNSUPPORTED')
        self.assertEqual(ref.point_reason({'start': ev('seizure', 'nth', 2), 'end': span}, [], 'present', 'store', concepts), 'FREE_TEXT_COUNT_UNSUPPORTED')
        self.assertIsNone(ref.point_reason({'start': ev('seizure', 'nth', 2), 'end': span}, [], 'off', 'store', concepts))
        count = [{'id': 'A', 'occ': 'count', 'within': 'period'}]
        self.assertEqual(ref.point_reason({'start': ev('seizure'), 'end': span}, count, 'off', 'store', concepts), 'INDEX_PERIOD_COUNT_UNSUPPORTED')
        self.assertEqual(ref.point_reason({'start': {'kind': 'date', 'date': '2020-01-01'}, 'end': ev('seizure')}, [], 'off', 'store', concepts),
                         'INDEX_DATE_START_WITH_EVENT_END_UNSUPPORTED')
        self.assertEqual(ref.point_reason({'start': ev('seizure'), 'end': span}, [], 'off', None, concepts), 'FREE_TEXT_CODED_ONLY_UNSUPPORTED')
        self.assertIsNone(ref.point_reason({'start': {'kind': 'birth'}, 'end': {'kind': 'age', 'amount': 6, 'unit': 'year'}}, count, 'off', None, concepts))

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
        # A point period (D52 review, S2): its reason, alone.
        point = {'query': q, 'windows': {}, 'index_anchor': {'mode': 'point', 'reason': 'INDEX_ANCHOR_ANY_UNSUPPORTED'}}
        st.check_request(point, {})
        for spec, code in [({**point, 'index_anchor': {'mode': 'hull', 'reason': 'X'}}, 'INDEX_ANCHOR_MUST_BE_A_POINT_AND_A_REASON'),
                           ({**point, 'index': good['index']}, 'INDEX_ANCHOR_AND_INDEX_TOGETHER')]:
            with self.assertRaises(st.RequestError) as caught:
                st.check_request(spec, {})
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

    def test_the_narrowing_cache_reuses_a_range_per_snapshot_and_key_and_equals_the_uncached_narrowing(self):
        # D52b: a range is computed once per snapshot and key (concept, k, from the end, kinds left out).
        import random
        import d52_measure as m
        import index_hull as hull
        from patterns import execution_store as es
        rng = random.Random('d52b-cache')
        a, statics = m.tiny_ingest(rng, 'ca', 20)
        b, _ = m.tiny_ingest(rng, 'cb', 20)
        query = {'steps': [{'id': '@index_start', 'concept': 'z', 'occurrence': 'first'}]}
        nth = {'steps': [{'id': '@index_start', 'concept': 'z', 'occurrence': 'any'}]}
        coded = {'steps': {}, 'exclude_kinds': ['mention']}
        index = {'start': {'step': '@index_start', 'offset': -1}, 'end': {'step': '@index_start', 'offset': 3}}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(a, Path(tmp) / 'a')) as sa, es.open_store(es.build(b, Path(tmp) / 'b')) as sb:
            wa = {pid: {'start': 0, 'end': s['last']} for pid, s in statics.items()}
            wb = {p['patient_id']: {'start': 0, 'end': 12} for p in b['patients']}
            q0, h0 = hull.CALLS['range_queries'], hull.CALLS['range_hits']
            first = hull.narrow(sa, query, None, index, wa)
            again = hull.narrow(sa, query, None, index, wa)
            self.assertEqual((hull.CALLS['range_queries'] - q0, hull.CALLS['range_hits'] - h0), (1, 1))
            self.assertEqual(first, again)
            self.assertEqual(first, hull.narrow(sa, query, None, index, wa, cache=False))
            # Another key (coded only; an Nth) is computed, then reused; the cache=False path neither reads nor fills it.
            for q, c in ((query, coded), (nth, {'steps': {'@index_start': {'occurrence': {'nth': 2}}}})):
                q1 = hull.CALLS['range_queries']
                one = hull.narrow(sa, q, c, index, wa)
                self.assertEqual(hull.CALLS['range_queries'], q1 + 1)
                self.assertEqual(one, hull.narrow(sa, q, c, index, wa))
                self.assertEqual(hull.CALLS['range_queries'], q1 + 1)
                self.assertEqual(one, hull.narrow(sa, q, c, index, wa, cache=False))
            self.assertNotEqual(first[2], hull.narrow(sa, query, coded, index, wa)[2]) if any(e['kind'] == 'mention' for p in a['patients'] for e in p['events']) else None
            # Another snapshot empties the cache: its ranges are its own.
            q2 = hull.CALLS['range_queries']
            other = hull.narrow(sb, query, None, index, wb)
            self.assertEqual(hull.CALLS['range_queries'], q2 + 1)
            self.assertEqual(other, hull.narrow(sb, query, None, index, wb, cache=False))
            self.assertEqual(hull._CACHE['snapshot'][0], sb.snapshot_id)

    def tiny_store(self, tmp):
        import d52_measure as m
        from patterns import execution_store as es
        p = {'patient_id': 'P1', 'clock_id': 'clock_P1', 'coverage': [{'source': 'ehr', 'lo': 0, 'hi': 60, 'basis': 'declared'}],
             'events': [{'event_id': f'e{i}', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [10 * i, 10 * i], 'evidence': {}}
                        for i in range(1, 4)]}
        ingest = {'profile': es.INGEST_PROFILE, 'dataset_id': 'd52b-m1', 'closure': {'version': 'm1', 'ancestors': m.ANCESTORS}, 'patients': [p]}
        return es.open_store(es.build(ingest, Path(tmp) / 's'))

    def test_the_cache_is_bounded_and_takes_only_validated_keys_and_malformed_input_is_refused_by_name(self):
        # D52b review, M1, the reviewer's repro (/tmp/d52b-review/grow.py): crafted concepts, N and kinds.
        import index_hull as hull
        index = {'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0}, 'end': None}
        windows = {'P1': {'start': 0, 'end': 50}}
        step = lambda concept='x', occ='first': {'steps': [{'id': '@index_start', 'concept': concept, 'occurrence': occ}]}
        nth = lambda k, which='nth': {'steps': {'@index_start': {'occurrence': {which: k}}}}
        with tempfile.TemporaryDirectory() as tmp, self.tiny_store(tmp) as store:
            hull.narrow(store, step(), None, index, windows)
            queries = hull.CALLS['range_queries']
            refusals = [
                (step('unknown-' + 'x' * 100), None, 'INDEX_CONCEPT_UNKNOWN'),
                (step('x' * 1_000_000), None, 'INDEX_STEP_MALFORMED'),  # a 1 MB concept name
                (step(occ='any'), nth(51), 'INDEX_OCCURRENCE_UNSUPPORTED'),
                (step(occ='any'), nth(1), 'INDEX_OCCURRENCE_UNSUPPORTED'),
                (step(occ='any'), nth('3'), 'INDEX_OCCURRENCE_UNSUPPORTED'),
                (step(occ='any'), nth(3, 'third'), 'INDEX_OCCURRENCE_UNSUPPORTED'),
                (step(occ='any'), None, 'INDEX_OCCURRENCE_UNSUPPORTED'),
                (step(occ='any'), {'steps': {'@index_start': {'occurrence': [3]}}}, 'INDEX_OCCURRENCE_UNSUPPORTED'),
                (step(occ='every'), None, 'INDEX_OCCURRENCE_UNSUPPORTED'),
                (step(), {'steps': {}, 'exclude_kinds': ['letters-of-another-kind']}, 'INDEX_EXCLUDE_KINDS_UNKNOWN'),
                (step(), {'steps': {}, 'exclude_kinds': 'mention'}, 'INDEX_EXCLUDE_KINDS_UNKNOWN'),
                (step(), {'steps': []}, 'INDEX_STEP_MALFORMED'),
                ({'steps': 'x'}, None, 'INDEX_STEP_MALFORMED'),
            ]
            for query, conditions, code in refusals:
                with self.assertRaises(hull.IndexRefused) as caught:
                    hull.narrow(store, query, conditions, index, windows)
                self.assertEqual(caught.exception.code, code, (query['steps'] if isinstance(query['steps'], str) else query['steps'][0]['concept'][:20], conditions))
            with self.assertRaises(hull.IndexRefused):
                hull.narrow(store, step(), None, {'start': {'step': '@other', 'offset': 0}}, windows)
            self.assertEqual(hull.CALLS['range_queries'], queries, 'no SQL for a refused request')
            self.assertEqual(len(hull._CACHE['ranges']), 1)
            # The repro's second loop: every valid N, 2 to 50, is a key of its own; the cache keeps at most its cap.
            with mock.patch.object(hull, 'CACHE_MAX_KEYS', 8):
                for k in range(2, hull.MAX_K + 1):
                    hull.narrow(store, step(occ='any'), nth(k), index, windows)
                self.assertEqual(len(hull._CACHE['ranges']), 8)
                self.assertEqual(sorted(key[1] for key in hull._CACHE['ranges']), list(range(hull.MAX_K - 7, hull.MAX_K + 1)))
                # The least recently used goes first: reading k = 43 keeps it past the next insertion.
                hull.narrow(store, step(occ='any'), nth(43), index, windows)
                hull.narrow(store, step(), None, index, windows)
                self.assertIn(('x', 43, False, ('mention',)), hull._CACHE['ranges'])
                self.assertNotIn(('x', 44, False, ('mention',)), hull._CACHE['ranges'])
            # The row cap: a key whose ranges pass it alone is computed but not kept.
            with mock.patch.object(hull, 'CACHE_MAX_ROWS', 0):
                before = dict(hull._CACHE['ranges'])
                hull.narrow(store, step(occ='last'), None, index, windows)
                self.assertNotIn(('x', 1, True, ()), hull._CACHE['ranges'])
            self.assertLessEqual(hull._CACHE['rows'], hull.CACHE_MAX_ROWS)
            self.assertGreater(hull.cache_bytes(), 0)
            del before

    def test_a_range_query_reads_nothing_but_its_key(self):
        # D52b review, S2: the identity of cached and uncached narrowings shows no staleness or mutation; the key's
        # completeness rests on `_range_query` reading only the key. Two requests that share a key, and differ in every
        # other field (offsets, windows, other steps, relations, an end step), send the same SQL with the same parameters.
        import inspect
        import index_hull as hull
        self.assertEqual(list(inspect.signature(hull._range_query).parameters), ['store', 'key'])
        sent = []
        class Recorder:
            snapshot_id = 'recorder'
            db = None
            def query(self, sql, params=()):
                if 'concept_closure' in sql and 'DISTINCT concept' in sql:
                    return [('x',), ('y',), ('z',)]
                if 'DISTINCT kind' in sql:
                    return [('condition',), ('mention',)]
                sent.append((sql, list(params)))
                return []
        one = {'steps': [{'id': 'A', 'concept': 'y', 'occurrence': 'any'}, {'id': '@index_start', 'concept': 'x', 'occurrence': 'first'}],
               'relations': [{'id': 'r'}]}
        two = {'steps': [{'id': '@index_start', 'concept': 'x', 'occurrence': 'first'}, {'id': '@index_end', 'concept': 'z', 'occurrence': 'last'}]}
        i1 = {'start': {'step': '@index_start', 'offset': -5}, 'end': {'step': '@index_start', 'offset': 9}}
        i2 = {'start': {'step': '@index_start', 'offset': 0}, 'end': {'step': '@index_end', 'offset': 1}}
        hull.narrow(Recorder(), one, {'steps': {}, 'exclude_kinds': ['mention']}, i1, {'P': {'start': 0, 'end': 1}}, cache=False)
        hull.narrow(Recorder(), two, {'steps': {}, 'exclude_kinds': ['mention']}, i2, {'Q': {'start': 3, 'end': 4}}, cache=False)
        x_queries = [q for q in sent if 'WITH f AS' in q[0] and q[1][0] == 'x']  # the range queries (D52c also reads facts)
        self.assertEqual(len(x_queries), 2)
        self.assertEqual(x_queries[0], x_queries[1])
        self.assertEqual(x_queries[0][1], ['x', 'mention'])

    def test_an_nth_event_never_ranks_a_letter_mention_in_the_narrowing(self):
        # The review's repro (D52 review, M1): from the first x to the 3rd x, profile 1.1 with no exclusion (codedOnly
        # 'derived'). The 3rd coded x is at 30; a mention of x (0 to 5) must not make the narrowing's 3rd x 20.
        import d52_measure as m
        import index_hull as hull
        from patterns import execution_store as es
        from patterns import store_conditions as sc
        ev = lambda eid, kind, source, concept, lo, hi: {'event_id': eid, 'kind': kind, 'source': source, 'concepts': [concept],
                                                         'start': [lo, hi], 'evidence': {}}
        p = {'patient_id': 'P1', 'clock_id': 'clock_P1', 'coverage': [{'source': s, 'lo': 0, 'hi': 60, 'basis': 'declared'} for s in ('ehr', 'letters')],
             'events': [ev('e1', 'condition', 'ehr', 'x', 10, 10), ev('e2', 'condition', 'ehr', 'x', 20, 20), ev('e3', 'condition', 'ehr', 'x', 30, 30),
                        ev('m1', 'mention', 'letters', 'x', 0, 5), ev('f1', 'condition', 'ehr', 'y', 25, 25)]}
        ingest = {'profile': es.INGEST_PROFILE, 'dataset_id': 'd52-m1', 'closure': {'version': 'm1', 'ancestors': m.ANCESTORS}, 'patients': [p]}
        steps = [{'id': '@index_start', 'bound': {'event': 'x'}, 'occurrence': 'first', 'derived': None, 'concept': 'x'},
                 {'id': '@index_end', 'bound': {'event': 'x'}, 'occurrence': 'any', 'derived': {'nth': 3}, 'concept': 'x'}]
        query = {'profile': 'trajectory-graph-1.0', 'scope': 'within', 'steps': [{'id': 'A', 'concept': 'y', 'occurrence': 'any'}],
                 'relations': [], 'relaxations': [], 'budget': 0, 'search_budget': 40000}
        bases = {'P1': {'start': 0, 'end': 50}}
        out = ref.rewrite_sides({'query': query, 'conditions': {'profile': 'execution-store-conditions-1.1', 'steps': {}}, 'windows': bases},
                                {'step': '@index_start', 'offset': 0}, {'step': '@index_end', 'offset': 0}, steps, [{'id': 'A'}], bases)
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, Path(tmp) / 's')) as store:
            h1 = sc.match(store, out['query'], out['windows'], conditions=out['conditions'])['results'][0]
            narrowed, reasons, _ = hull.narrow(store, out['query'], out['conditions'], out['index'], out['windows'])
            self.assertEqual(narrowed['P1'], {'start': 0, 'end': 30})  # the first x may be the mention; the 3rd never is
            h3 = sc.match(store, out['query'], narrowed, conditions=out['conditions'])['results'][0]
        self.assertEqual((h1['status'], h3['status']), ('exact', 'exact'))
        self.assertEqual(ref.anchor_range(p['events'], 'x', m.ANCESTORS, 'any', {'nth': 3}), (30, 30, 3))

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
        self.assertEqual(clean['h3_h3s_mismatches'], 0, clean['h3_h3s_examples'])  # D52c
        planted = m.worlds(1200, 'unit-test-defect', defect='eligibility_dropped', quiet=True)
        self.assertGreater(planted['failing_cases'], 0)


@unittest.skipUnless(HAVE_DUCKDB, 'needs DuckDB')
class SimplifyTests(unittest.TestCase):
    """D52c: `index_simplify.py` (index_simplify_v1) on tiny snapshots: what each rewrite applies to and refuses, the mapping
    back, the server's path and its pre-fill."""

    @classmethod
    def setUpClass(cls):
        import random
        import d52_measure as m
        from patterns import execution_store as es
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ingest, cls.statics = m.tiny_ingest(random.Random('d52c-unit'), 'unit', 20)
        cls.store = es.open_store(es.build(cls.ingest, Path(cls.tmp.name) / 's'))

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        cls.tmp.cleanup()

    def simplify(self, steps, relations, index=None, relaxations=(), conditions=None, scope='any'):
        import index_hull as hull
        import index_simplify as simp
        query = {'profile': 'trajectory-graph-1.0', 'scope': scope, 'steps': steps, 'relations': relations,
                 'relaxations': list(relaxations), 'budget': '1', 'search_budget': 40000}
        index = index or {'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0}, 'end': {'step': '@index_start', 'offset': 3}}
        windows = {pid: {'start': 0, 'end': s['last']} for pid, s in self.statics.items()}
        narrowed, _, found = hull.narrow(self.store, query, conditions, index, windows)
        return simp.simplify(self.store, query, conditions, index, narrowed, found), query, narrowed

    H = {'id': '@index_start', 'concept': 'x', 'occurrence': 'first'}
    FOLLOWED = {'id': 'index:followed', 'kind': 'after', 'from': '@index_start', 'to': 'END', 'min': 3}
    BORN = {'id': 'index:born', 'kind': 'after', 'from': 'START', 'to': '@index_start', 'min': -3}

    def scope_start(self, step, lo=0, hi=None):
        r = {'id': f'index:scope-start:{step}', 'kind': 'after', 'from': '@index_start', 'to': step, 'min': lo}
        return {**r, 'max': hi} if hi is not None else r

    def test_the_templates_shapes_each_get_their_rewrites(self):
        # Blank, Tree, Graph: a first x like the anchor; c1 drops the implied relation, c2 merges the anchor into it.
        s, _, _ = self.simplify([{'id': 'A', 'concept': 'x', 'occurrence': 'first'}, self.H],
                                [self.scope_start('A'), self.FOLLOWED, self.BORN])
        self.assertEqual((s['record']['dropped'], s['record']['merged'], s['record']['existential']),
                         (['index:scope-start:A'], {'@index_start': 'A'}, None))
        self.assertEqual([st['id'] for st in s['query']['steps']], ['A'])
        self.assertEqual([(r['id'], r['from'], r['to']) for r in s['query']['relations']],
                         [('index:followed', 'A', 'END'), ('index:born', 'START', 'A')])
        # Event frequency: any x (c1) and a first y bounding the anchor from above: c3, the born relation implied.
        s, _, _ = self.simplify([{'id': 'A', 'concept': 'x', 'occurrence': 'any'}, {'id': 'B', 'concept': 'y', 'occurrence': 'first'}, self.H],
                                [self.scope_start('A'), self.scope_start('B'), self.FOLLOWED, self.BORN])
        self.assertEqual(s['record']['dropped'], ['index:scope-start:A'])
        self.assertEqual((s['record']['existential'], s['record']['implied']), ('@index_start', ['index:born']))
        self.assertEqual(next(st for st in s['query']['steps'] if st['id'] == '@index_start')['occurrence'], 'any')
        # A step whose concept is the anchor's ancestor is not implied (z can be y); one below it is.
        s, _, _ = self.simplify([{'id': 'A', 'concept': 'z', 'occurrence': 'any'}, {**self.H, 'concept': 'z'}],
                                [self.scope_start('A'), self.FOLLOWED, self.BORN])
        self.assertEqual(s['record']['dropped'], ['index:scope-start:A'])  # z is below z
        s, _, _ = self.simplify([{'id': 'A', 'concept': 'z', 'occurrence': 'any'}, self.H], [self.scope_start('A'), self.FOLLOWED, self.BORN])
        self.assertEqual(s['record']['dropped'], [])

    def test_what_each_rewrite_leaves_alone(self):
        A = {'id': 'A', 'concept': 'y', 'occurrence': 'any'}
        # c1: a positive minimum, a maximum, or a relaxation widening the step out of the anchor's family.
        for rel, relax in [(self.scope_start('A', 1), ()), (self.scope_start('A', 0, 5), ()),
                           (self.scope_start('A'), [{'id': 'node:A', 'kind': 'widen_concept', 'step': 'A', 'concept': 'z', 'cost': '0.5'}])]:
            s, _, _ = self.simplify([{**A, 'concept': 'x'}, self.H], [rel, self.FOLLOWED, self.BORN], relaxations=relax)
            self.assertEqual(s['record']['dropped'], [], rel)
        # c2: an X -> X relation that does not hold at one time (the period starts after its event), or a relaxation naming X.
        s, _, _ = self.simplify([{'id': 'A', 'concept': 'x', 'occurrence': 'first'}, self.H], [self.scope_start('A', 1), self.FOLLOWED, self.BORN])
        self.assertEqual(s['record']['merged'], {})
        # c3: a lower bound from a user step (`within`: a maximum), two hidden steps, an Nth event, a scope not `any`.
        s, _, _ = self.simplify([A, self.H], [self.scope_start('A', 0, 3), self.FOLLOWED, self.BORN])
        self.assertIsNone(s['record']['existential'])
        two = {'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0}, 'end': {'step': '@index_end', 'offset': 0}}
        s, _, _ = self.simplify([A, self.H, {'id': '@index_end', 'concept': 'y', 'occurrence': 'first'}],
                                [self.scope_start('A'), {'id': 'index:ordered', 'kind': 'after', 'from': '@index_start', 'to': '@index_end', 'min': 1}],
                                index=two)
        self.assertIsNone(s['record']['existential'])
        s, _, _ = self.simplify([A, {**self.H, 'occurrence': 'any'}], [self.scope_start('A'), self.FOLLOWED],
                                conditions={'profile': 'execution-store-conditions-1.1', 'steps': {'@index_start': {'occurrence': {'nth': 2}}}})
        self.assertIsNone(s['record']['existential'])
        s, _, _ = self.simplify([A, self.H], [self.scope_start('A'), self.FOLLOWED, self.BORN], scope='from_start')
        self.assertIsNone(s['record']['existential'])

    def test_the_answer_is_mapped_back(self):
        import index_simplify as simp
        merged = {'rule': simp.RULE, 'dropped': [], 'merged': {'@index_start': 'A'}, 'existential': None, 'implied': []}
        r = {'patient_id': 'p', 'status': 'not_comparable', 'binding': {'A': 'e1'}, 'witness': {'A': 5}, 'excluded_steps': ['A']}
        out = simp.map_result(r, merged, ['A', '@index_start'], frozenset())
        self.assertEqual((out['binding'], out['witness'], out['excluded_steps']),
                         ({'A': 'e1', '@index_start': 'e1'}, {'A': 5, '@index_start': 5}, ['A', '@index_start']))
        exist = {**merged, 'merged': {}, 'existential': '@index_start'}
        r = {'patient_id': 'p', 'status': 'possible', 'certainty': 'possible', 'binding': {'A': 'e1', '@index_start': 'e2'},
             'witness': {'A': 5, '@index_start': 4}}
        self.assertEqual(simp.map_result(r, exist, ['A', '@index_start'], frozenset())['binding'], {'A': 'e1'})
        weak = simp.map_result(r, exist, ['A', '@index_start'], frozenset({'p'}))
        self.assertEqual((weak['note'], weak['witness']), (simp.NOTE, {'A': 5}))
        relaxed = simp.map_result({'patient_id': 'p', 'status': 'relaxed', 'certainty': 'possible', 'binding': {}, 'complete': True},
                                  exist, ['A', '@index_start'], frozenset({'p'}))
        self.assertEqual((relaxed['complete'], relaxed['note']), (False, simp.NOTE))

    def test_the_server_simplifies_maps_back_and_fills_its_cache_at_start(self):
        import json
        import index_hull as hull
        import store_trajectory as st
        cohort = json.loads(st.COHORT.read_text())
        pids = [p['patient_id'] for p in cohort['patients']]
        query = {'profile': 'trajectory-graph-1.0', 'scope': 'any',
                 'steps': [{'id': 'A', 'concept': 'seizure', 'occurrence': 'first'}, {'id': '@index_start', 'concept': 'seizure', 'occurrence': 'first'}],
                 'relations': [{'id': 'index:scope-start:A', 'kind': 'after', 'from': '@index_start', 'to': 'A', 'min': 0},
                               {'id': 'index:followed', 'kind': 'after', 'from': '@index_start', 'to': 'END', 'min': 1440}],
                 'relaxations': [], 'budget': '0', 'search_budget': 40000}
        index = {'rule': 'index_anchor_v1', 'start': {'step': '@index_start', 'offset': 0}, 'end': {'step': '@index_start', 'offset': 1440}}
        windows = {pid: {'start': 0, 'end': 10 ** 7} for pid in pids}
        with tempfile.TemporaryDirectory() as tmp:
            server = st.TrajectoryStore(root=tmp).start()
            try:
                status = server.status()
                self.assertIsInstance(status['prefill_s'], float)
                self.assertIn(['seizure', 1, False, ('mention',)], status['prefill_keys'])
                hits = hull.CALLS['range_hits']
                answer = server.match({'query': query, 'conditions': {'profile': 'execution-store-conditions-1.3', 'steps': {},
                                                                       'exclude_kinds': ['mention']}, 'windows': windows, 'index': index})
                self.assertGreater(hull.CALLS['range_hits'], hits, 'the first request finds its range cached')
                self.assertEqual(answer['index']['simplify']['merged'], {'@index_start': 'A'})
                for r in answer['results']:
                    if 'binding' in r:
                        self.assertEqual(r['binding']['@index_start'], r['binding']['A'])
                # The existential form: the hidden step's binding is dropped.
                q3 = {**query, 'steps': [{'id': 'A', 'concept': 'seizure', 'occurrence': 'any'}, query['steps'][1]]}
                answer = server.match({'query': q3, 'windows': windows, 'index': index})
                self.assertEqual(answer['index']['simplify']['existential'], '@index_start')
                self.assertTrue(all('@index_start' not in r.get('binding', {}) for r in answer['results']))
            finally:
                server.close()

class ReviewTests(unittest.TestCase):
    """D52c review: rule 3b's classes refuse contradictions (M1, S3); the shared note (nit)."""

    REC = {'rule': 'index_simplify_v1', 'dropped': ['index:scope-start:A'], 'merged': {}, 'existential': '@index_start', 'implied': []}

    def classify(self, u, s, weak=frozenset({'p'}), excluded=None, h1=None):
        import index_simplify as simp
        return simp.classify({'patient_id': 'p', **u}, {'patient_id': 'p', **s}, self.REC, weak,
                             {'p': (0, 0, 1)} if excluded is None else excluded, h1 and {'patient_id': 'p', **h1})

    def test_the_reviewers_three_contradictions_are_flagged(self):
        import index_simplify as simp
        note = {'note': simp.NOTE}
        # A complete `none` against a `possible` with the note: not a withdrawn claim, a contradiction.
        self.assertEqual(self.classify({'status': 'none', 'complete': True}, {'status': 'possible', **note}), [])
        # `exact` against `none` where excluded records exist: not D18's difference.
        self.assertEqual(self.classify({'status': 'exact'}, {'status': 'none', 'complete': True}), [])
        # `unresolved` against `exact`, where the unsimplified request over the base windows says `none`: not `decided`.
        self.assertEqual(self.classify({'status': 'unresolved'}, {'status': 'exact'}, h1={'status': 'none', 'complete': True}), [])

    def test_the_classes_still_explain_what_they_should(self):
        import index_simplify as simp
        self.assertEqual(self.classify({'status': 'unresolved'}, {'status': 'exact'}, h1={'status': 'unresolved'}), ['decided'])
        self.assertEqual(self.classify({'status': 'possible'}, {'status': 'possible', 'note': simp.NOTE}), ['claim_withdrawn'])
        self.assertEqual(self.classify({'status': 'relaxed', 'relaxations': ['x'], 'cost': '1', 'certainty': 'possible', 'complete': True},
                                       {'status': 'relaxed', 'relaxations': ['x'], 'cost': '1', 'certainty': 'possible', 'complete': False,
                                        'note': simp.NOTE}), ['claim_withdrawn'])
        adm = {'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS', 'excluded_steps': ['@index_start']}
        self.assertEqual(self.classify(adm, {'status': 'exact'}, weak=frozenset()), ['admission'])
        # a withdrawn claim that also changes another field is not explained
        self.assertEqual(self.classify({'status': 'possible'}, {'status': 'possible', 'note': simp.NOTE, 'reason': 'X'}), [])
        # matches beside two `unresolved` results must agree
        beside = lambda c: {'status': 'unresolved', 'relaxed_match': {'relaxations': ['x'], 'cost': '1', 'certainty': c}}
        self.assertEqual(self.classify(beside('possible'), beside('exact'), weak=frozenset()), [])
        self.assertIn('undecided_both', self.classify({'status': 'unresolved'}, beside('exact'), weak=frozenset()))

    def test_a_merge_alone_may_decide_where_the_unsimplified_form_did_not(self):
        # D52d: D52c's census found 20 results that c2 alone made more decided, each compatible with both unsimplified
        # forms; `decided` is granted for c2 as for c1 and c3, under the same compatibility checks.
        import index_simplify as simp
        merged = {'rule': simp.RULE, 'dropped': [], 'merged': {'@index_start': 'A'}, 'existential': None, 'implied': []}
        u, s = {'patient_id': 'p', 'status': 'unresolved'}, {'patient_id': 'p', 'status': 'possible'}
        self.assertEqual(simp.classify(u, s, merged, frozenset(), {}, {'patient_id': 'p', 'status': 'unresolved'}), ['decided'])
        self.assertEqual(simp.classify(u, s, merged, frozenset(), {}, {'patient_id': 'p', 'status': 'none', 'complete': True}), [])
        none = {'patient_id': 'p', 'status': 'none', 'complete': False}
        relaxed = {'patient_id': 'p', 'status': 'relaxed', 'relaxations': ['node:B'], 'cost': '0.5', 'certainty': 'possible'}
        self.assertEqual(simp.classify(none, relaxed, merged, frozenset(), {}, none), ['decided'])

    def test_every_checker_honours_the_note(self):
        import index_simplify as simp
        from patterns import trajectory_graph_worlds as tw
        self.assertEqual(simp.NOTE, tw.ANCHOR_ORDER_MAY_MAKE_EXACT)
        self.assertFalse(tw.claims_no_exact({'status': 'possible', 'note': simp.NOTE}, False))
        self.assertTrue(tw.claims_no_exact({'status': 'possible'}, False))


@unittest.skipUnless(HAVE_DUCKDB, 'needs DuckDB')
class ExcludedRecordsNarrowingTests(unittest.TestCase):
    """D52c review, M2: the narrowing never excludes a patient an excluded record of a hidden step could place; S1: a
    failed pre-fill takes nothing down."""

    def test_a_patient_with_only_an_excluded_record_is_matched_and_not_comparable(self):
        import d52_measure as m
        p = {'patient_id': 'p0', 'clock_id': 'c', 'coverage': [{'source': 'ehr', 'lo': 0, 'hi': 12, 'basis': 'declared'}],
             'events': [{'event_id': 'e0', 'kind': 'condition', 'source': 'ehr', 'concepts': ['y'], 'start': [4, 4], 'evidence': {}}],
             'excluded': [{'source': 'ehr', 'concept': 'x', 'start': [2, 2], 'reason': 'NOT_ADMITTED'}]}
        steps = [{'id': '@index_start', 'concept': 'x', 'occurrence': 'first', 'derived': None, 'bound': {'event': 'x'}}]
        spec = ({'step': '@index_start', 'offset': 0}, {'step': '@index_start', 'offset': 3}, steps)
        q = {'profile': 'trajectory-graph-1.0', 'scope': 'from_start', 'steps': [{'id': 'A', 'concept': 'y', 'occurrence': 'any'}],
             'relations': [], 'relaxations': [], 'budget': '0', 'search_budget': 40000}
        # Another patient's x in the same source, so the coverage rule finds the source records x and D18's rule answers
        # (with no source recording x at all, the coverage rule's NO_SOURCE_RECORDS_CONCEPT comes first: also not comparable).
        other = {'patient_id': 'p1', 'clock_id': 'c1', 'coverage': p['coverage'],
                 'events': [{'event_id': 'e0', 'kind': 'condition', 'source': 'ehr', 'concepts': ['x'], 'start': [3, 3], 'evidence': {}}]}
        statics = {'p0': {'last': 12, 'c_end': 0, 'c_start': 0}, 'p1': {'last': 12, 'c_end': 0, 'c_start': 0}}
        with tempfile.TemporaryDirectory() as tmp:
            alone, _ = m.one_case(tmp + '/c', [p], {'p0': statics['p0']}, spec, q, None)
            self.assertEqual((alone['p0']['H3s'][0]['status'], alone['p0']['H3s'][0]['reason']), ('not_comparable', 'NO_SOURCE_RECORDS_CONCEPT'))
            res, _ = m.one_case(tmp + '/a', [p, other], statics, spec, q, None)
            r, kept, whole = res['p0']['H3s']
            self.assertEqual((r['status'], r.get('reason')), ('not_comparable', 'ADMISSION_EXCLUDED_RECORDS'))
            self.assertIn('@index_start', r['excluded_steps'])
            self.assertEqual(kept + whole, [])
            # the harness's shape (D52c development, 142 of 12,000 cases): excluded on kept records alone, a period over
            # the whole source; the planted defect `excluded_ignored` brings it back, and the whole-source check fails it.
            res, _ = m.one_case(tmp + '/b', [p, other], statics, spec, q, None, 'excluded_ignored')
            r, kept, whole = res['p0']['H3']
            self.assertEqual(r['status'], 'excluded')
            self.assertTrue(whole)

    def test_the_server_offers_the_store_when_its_prefill_fails(self):
        import index_hull as hull
        import store_trajectory as st
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hull, 'prefill', side_effect=RuntimeError('boom')):
            server = st.TrajectoryStore(root=tmp).start()
            try:
                status = server.status()
                self.assertTrue(status['available'])
                self.assertEqual((status['prefill_error'], status['prefill_keys']), ('PREFILL_FAILED', []))
            finally:
                server.close()

if __name__ == '__main__':
    unittest.main()
