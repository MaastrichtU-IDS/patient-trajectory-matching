"""Value conditions in the execution store's match request (D28, chartevents stage 2).

A request may carry conditions on the values of a step's concept's `series` rows, in one literal unit, and value
relaxations; the store compiles each into a per-query tag concept and the engine is unchanged. These tests compare the
store with the reference, which reads conditions independently (`execution_store_reference.ValueQuery`), on synthetic
cohorts made for it: values on and next to every threshold, several units per item, micro-unit values that are null
(finer than 10**-6, or beyond BIGINT), value-less events, interval events under a measured grouping, excluded records,
partial coverage and shared variables. Planted defects in the store must fail the differential.

Needs DuckDB (`patterns/requirements-store.lock.txt`); importing this module does not.
"""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch

from . import execution_store as es
from . import execution_store_reference as ref
from . import prepared_reference as pr
from . import series_values as sv
from . import trajectory_graph as tg

HOUR = 3_600_000_000
T0 = 63_712_000_000_000_000  # about 2020 on the store's clock
COSTS = ('0.25', '0.5', '1')
CP = es.CONDITIONS_PROFILE
ANCESTORS = {'g:vitals': [], 'g:labs': [], 'g:drugs': [],
             'i:hr': ['g:vitals'], 'i:map': ['g:vitals'], 'i:temp': ['g:vitals'], 'i:fluid': ['g:vitals'],
             'i:lact': ['g:labs'], 'i:k': ['g:labs'], 'i:odd': [], 'd:pressor': ['g:drugs']}
MEASURED = {'i:hr': ('bpm', 'beats/min'), 'i:map': ('mmHg',), 'i:temp': ('C', 'F'), 'i:lact': ('mmol/L',),
            'i:k': ('mEq/L', 'mmol/L'), 'i:odd': ('u',)}
# Values clustered on thresholds, with their neighbours one micro-unit away, finer than a micro-unit (a null micro-unit
# value), beyond BIGINT (null too), signed zeros and other spellings of one value.
THRESHOLDS = ('65', '100', '2.5', '0', '-3', '36.6')
NEAR = ('{t}', '{t}.0', '{t}.000001', '{t}.0000001', '{t}.0000009', '{t}e0')
EXTRA = ('64.999999', '99.9999999', '-0', '+0', '.5', '5.', '1e-7', '-1e-7', '6.5e1', '650e-1', '0.0000005',
         '9223372036854.775807', '9223372036854.775808', '-9223372036854.775809', '1e30', '-1e30', '1e-300', '2.4999999')


def values_pool():
    out = set(EXTRA)
    for t in THRESHOLDS:
        for shape in NEAR:
            text = shape.format(t=t)
            if text.count('.') <= 1:
                out.add(text)
    out = {v for v in out if sv.decimal_of(v) is not None}
    return sorted(out)


VALUES = values_pool()


def series_row(eid, item, text, unit):
    decimal, micro = sv.columns(text)
    return {'event_id': eid, 'item_class': item, 'value_lexical': text, 'value_decimal': decimal, 'value_micro': micro,
            'unit': unit}


def value_ingest(seed, patients=48, shared=0.2, excluded=True, covered=(0.9, 0.7)):
    """A synthetic cohort with measured points (see the module docstring); `shared` is the share of patients in which
    a measurement starts when an infusion ends (a shared time variable)."""
    rng = random.Random(seed)
    out = []
    for n in range(patients):
        pid = f'V{seed}-{n:03d}'
        events, series, dropped = [], [], []
        span = rng.randint(24, 24 * 20)
        for k in range(rng.randint(3, 14)):
            item = rng.choice(sorted(MEASURED))
            eid = f'm{k}'
            lo = T0 + rng.randint(0, span) * HOUR
            hi = lo + (0 if rng.random() < 0.8 else rng.randint(1, 30) * HOUR)
            events.append({'event_id': eid, 'kind': 'measurement', 'source': 'chart', 'concepts': [item],
                           'start': [lo, hi], 'evidence': {'k': k}})
            if rng.random() < 0.93:  # some measured events carry no value
                unit = rng.choice(MEASURED[item]) if rng.random() < 0.9 else rng.choice(['bpm', 'u', 'mmHg'])
                series.append(series_row(eid, item, rng.choice(VALUES), unit))
        for k in range(rng.randint(0, 3)):
            item = rng.choice(('i:fluid', 'd:pressor'))
            lo = T0 + rng.randint(0, span) * HOUR
            end = lo + rng.randint(1, 48) * HOUR
            events.append({'event_id': f'x{k}', 'kind': 'exposure', 'source': 'pump', 'concepts': [item],
                           'start': [lo, lo], 'end': [end, end], 'proper': True, 'evidence': {}})
        if rng.random() < shared:
            infusion = next((e for e in events if 'end' in e), None)
            point = next((e for e in events if 'end' not in e), None)
            if infusion and point:
                point['start'] = list(infusion['end'])
                point['start_var'] = infusion['event_id'] + '.f'
        for _ in range(rng.choice((0, 0, 1, 2, 4)) if excluded else 0):
            lo = T0 + rng.randint(0, span) * HOUR
            dropped.append({'source': 'chart', 'concept': rng.choice(sorted(MEASURED)), 'start': [lo, lo],
                            'reason': 'MISSING_UNIT'})
        coverage = []
        if rng.random() < covered[0]:
            coverage.append({'source': 'chart', 'lo': T0 - HOUR * rng.randint(0, 48),
                             'hi': T0 + HOUR * (span + rng.randint(-48, 48)), 'basis': 'declared'})
        if rng.random() < covered[1]:
            coverage.append({'source': 'pump', 'lo': T0, 'hi': T0 + HOUR * span, 'basis': 'declared'})
        patient = {'patient_id': pid, 'clock_id': f'patient_{pid}', 'events': events, 'coverage': coverage}
        if series:
            patient['series'] = series
        if dropped:
            patient['excluded'] = dropped
        out.append(patient)
    return {'profile': es.INGEST_PROFILE, 'dataset_id': f'value-fixture-{seed}',
            'closure': {'version': 'value-fixture-1', 'ancestors': ANCESTORS}, 'patients': out}


def random_windows(rng, ingest):
    out = {}
    for p in ingest['patients']:
        if rng.random() < 0.05:
            out[p['patient_id']] = None
            continue
        start = T0 + rng.randint(-48, 24 * 10) * HOUR
        out[p['patient_id']] = {'start': start, 'end': start + rng.randint(6, 24 * 15) * HOUR}
    return out


def _bounds_around(rng, text):
    """A condition's bounds drawn around a value: on it, one micro-unit off, finer, or wide."""
    op = rng.choice(('ge', 'gt', 'le', 'lt', 'eq', 'range', 'range'))
    near = rng.choice((text, text, rng.choice(VALUES)))
    if op == 'eq':
        return {'eq': near}
    if op != 'range':
        return {op: near}
    a, b = sorted((near, rng.choice(VALUES)), key=sv.decimal_of)
    if sv.decimal_of(a) == sv.decimal_of(b):
        return {'ge': a, 'le': b}
    return {rng.choice(('ge', 'gt')): a, rng.choice(('le', 'lt')): b}


def _wider(rng, condition):
    """A condition containing `condition`: each bound kept, loosened, dropped or moved out to a value below or above."""
    out = {'unit': condition['unit']}
    if 'eq' in condition:
        lower, upper = ('ge', condition['eq']), ('le', condition['eq'])
    else:
        lower = next(((k, condition[k]) for k in ('gt', 'ge') if k in condition), None)
        upper = next(((k, condition[k]) for k in ('lt', 'le') if k in condition), None)
    for side in (lower, upper):
        if side is None:
            continue
        key, text = side
        mode = rng.choice(('keep', 'loosen', 'drop', 'move'))
        if mode == 'drop' and out.keys() == {'unit'} and side is upper and lower is not None:
            mode = 'keep'
        if mode == 'drop':
            continue
        if mode == 'loosen':
            key = {'gt': 'ge', 'lt': 'le'}.get(key, key)
        elif mode == 'move':
            v = sv.decimal_of(text)
            pool = [x for x in VALUES if (sv.decimal_of(x) < v if key in ('gt', 'ge') else sv.decimal_of(x) > v)]
            if pool:
                text = rng.choice(pool)
        out[key] = text
    if out.keys() == {'unit'}:  # a condition needs a bound: one beyond every value of the cohort, on the open side
        out['le' if lower is None else 'ge'] = '1e31' if lower is None else '-1e31'
    return out


def value_request(rng, ingest):
    """A 1-3 step query shaped around one anchor patient's events, with value conditions on most steps, as the store
    request (query, conditions, windows)."""
    ancestors = ingest['closure']['ancestors']
    anchor = rng.choice([p for p in ingest['patients'] if len(p['events']) >= 3])
    values = {r['event_id']: r for r in anchor.get('series', [])}
    chosen = rng.sample(anchor['events'], rng.randint(1, min(3, len(anchor['events']))))
    steps, relaxations, conds, value_relaxations = [], [], {}, []
    for i, e in enumerate(chosen):
        own = e['concepts'][0]
        concept = rng.choice([own, *ancestors[own]]) if rng.random() < 0.85 else rng.choice(sorted(ancestors))
        sid = f'S{i}'
        steps.append({'id': sid, 'concept': concept, 'occurrence': rng.choice(('any', 'any', 'first', 'last'))})
        if rng.random() < 0.8:
            row = values.get(e['event_id'])
            unit = row['unit'] if row and rng.random() < 0.9 else rng.choice(['bpm', 'mmHg', 'C', 'u', 'mmol/L'])
            cond = {'unit': unit, **_bounds_around(rng, row['value_lexical'] if row else rng.choice(VALUES))}
            conds[sid] = cond
            if rng.random() < 0.35:
                value_relaxations.append({'id': f'v{sid}', 'kind': 'widen_value', 'step': sid,
                                          'cost': rng.choice(COSTS), 'condition': _wider(rng, cond)})
        if rng.random() < 0.2:
            relaxations.append({'id': f'w{sid}', 'kind': 'widen_concept', 'step': sid,
                                'concept': rng.choice(sorted(ancestors[concept]) or sorted(ancestors)),
                                'cost': rng.choice(COSTS)})
    relations = []
    for i in range(1, len(steps)):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        r = {'id': f'R{i}', 'from': a, 'to': b}
        if rng.random() < 0.15:
            relations.append({**r, 'kind': 'during', 'grace': rng.choice((0, 2, 12)) * HOUR})
            continue
        gap = chosen[int(b[1:])]['start'][0] - chosen[int(a[1:])]['start'][0]
        mode = rng.choice(('fit', 'tight', 'miss', 'open'))
        if mode == 'fit':
            r.update(min=gap - rng.randint(0, 12) * HOUR, max=gap + rng.randint(0, 12) * HOUR)
        elif mode == 'tight':
            r.update(min=gap, max=gap)
        elif mode == 'miss':
            r.update(min=gap - rng.randint(13, 100) * HOUR, max=gap - rng.randint(1, 12) * HOUR)
            if rng.random() < 0.6:
                relaxations.append({'id': f'x{r["id"]}', 'kind': 'extend_max', 'relation': r['id'],
                                    'max': r['max'] + rng.choice((6, 48)) * HOUR, 'cost': rng.choice(COSTS)})
        relations.append({**r, 'kind': 'after'})
    if rng.random() < 0.15:
        relations.append({'id': 'RS', 'kind': 'after', 'from': 'START', 'to': steps[0]['id'], 'min': 0})
    query = {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
             'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1', '2')),
             'search_budget': rng.choice((1, 40, 300))}
    conditions = None
    if conds:
        conditions = {'profile': CP, 'steps': conds}
        if value_relaxations:
            conditions['relaxations'] = value_relaxations
    return query, conditions, random_windows(rng, ingest)


_STATE = {}


def setUpModule():
    _STATE['tmp'] = tempfile.TemporaryDirectory(prefix='value-conditions-')
    tmp = Path(_STATE['tmp'].name)
    for name, seed, options in (('plain', 11, {'shared': 0.0}), ('shared', 12, {'shared': 0.5}),
                                ('clean', 13, {'shared': 0.2, 'excluded': False, 'covered': (1.0, 1.0)})):
        ingest = value_ingest(seed, **options)
        store = es.open_store(es.build(ingest, tmp / name), verify='content')
        _STATE[name] = (ingest, store, pr.PreparedReference(ingest))


def tearDownModule():
    for name in ('plain', 'shared', 'clean'):
        if name in _STATE:
            _STATE[name][1].close()
    _STATE['tmp'].cleanup()


def case(seed, cohort='plain'):
    ingest, store, prepared = _STATE[cohort]
    rng = random.Random(seed)
    for _ in range(20):  # a request with conditions (most draws have one)
        query, conditions, windows = value_request(rng, ingest)
        if conditions is not None:
            return query, conditions, windows
    raise AssertionError(seed)


def unfiltered(result):
    """A store result as the reference writes it: a patient the candidate filter proved `none` (reason FILTERED) is,
    for the reference, the engine's own complete `none`, which must be exactly what it found."""
    if result.get('reason') == 'FILTERED':
        return {k: v for k, v in result.items() if k != 'reason'}
    return result


def compare(store, ingest, prepared, seeds, cohort, exhaustive_every=4, **options):
    """(mismatched results, status mix): the store against the prepared reference for every seed, and against
    `exhaustive_match` for every `exhaustive_every`-th, result for result as `json.dumps` writes them."""
    wrong, mix = [], Counter()
    for seed in seeds:
        query, conditions, windows = case(seed, cohort)
        got = es.match(store, query, windows, conditions=conditions, **options)['results']
        wants = [prepared.match(query, windows, conditions)]
        if seed % exhaustive_every == 0:
            wants.append(ref.exhaustive_match(ingest, query, windows, conditions))
        for want in wants:
            if options.get('detail') == 'decided':
                listed = {r['patient_id'] for r in got}
                want = [w for w in want if w['patient_id'] in listed]
            for g, w in zip(got, want, strict=True):
                if json.dumps(unfiltered(g)) != json.dumps(w):
                    wrong.append((seed, g, w))
        mix.update(g['status'] for g in got)
    return wrong, mix


class ValueDifferentialTests(unittest.TestCase):
    """The store's value conditions against the independent reference, result for result."""

    def test_the_store_equals_the_reference_on_seeded_value_requests(self):
        for cohort, seeds in (('plain', range(240)), ('shared', range(1000, 1160)), ('clean', range(2000, 2160))):
            ingest, store, prepared = _STATE[cohort]
            wrong, mix = compare(store, ingest, prepared, seeds, cohort)
            self.assertEqual(wrong[:3], [], cohort)
            # The draws reach every outcome the rules can give.
            self.assertTrue({'exact', 'relaxed', 'none', 'not_comparable', 'excluded'} <= set(mix), (cohort, mix))

    def test_workers_and_the_decided_detail_change_nothing(self):
        ingest, store, prepared = _STATE['plain']
        wrong, _ = compare(store, ingest, prepared, range(0, 60, 3), 'plain', workers=2, chunk=5)
        self.assertEqual(wrong[:3], [])
        wrong, _ = compare(store, ingest, prepared, range(1, 60, 3), 'plain', detail='decided')
        self.assertEqual(wrong[:3], [])
        for seed in range(0, 30, 3):
            q, c, w = case(seed)
            one = es.match(store, q, w, conditions=c)
            self.assertEqual(json.dumps(es.match(store, q, w, conditions=c, workers=2, chunk=3)), json.dumps(one))
        es.close_pools()

    def test_time_pruning_stays_exact_on_tagged_steps(self):
        pruned = 0
        for cohort, seeds in (('plain', range(200)), ('shared', range(1000, 1100)), ('clean', range(2000, 2100))):
            ingest, store, _ = _STATE[cohort]
            for seed in seeds:
                q, c, w = case(seed, cohort)
                on = json.dumps(es.match(store, q, w, conditions=c))
                with patch.object(es, '_PRUNE', False):
                    off = json.dumps(es.match(store, q, w, conditions=c))
                self.assertEqual(on, off, (cohort, seed))
                request = es.Request(q, c)
                pq = tg.PreparedQuery(request.query)
                ids = dict(store.query('SELECT patient_id, patient_idx FROM patients'))
                by_idx = {ids[pid]: x for pid, x in w.items()}
                live = [i for i in es._candidates(store, pq, request)['patient_idx'] if by_idx.get(i) is not None]
                pruned += len(es._prune(store, pq, live, by_idx, tags=request.tags))
        self.assertGreater(pruned, 0)

    def test_every_value_relaxation_widens_to_a_tag_holding_the_stricter_tags_events(self):
        checked = 0
        for cohort, seeds in (('plain', range(240)), ('shared', range(1000, 1160)), ('clean', range(2000, 2160))):
            ingest, store, _ = _STATE[cohort]
            for seed in seeds:
                q, c, _ = case(seed, cohort)
                request = es.Request(q, c)
                value = ref.ValueQuery(ingest, q, c)
                steps = {s['id']: s['concept'] for s in request.query['steps']}
                for x in c.get('relaxations', []):
                    wide = next(r['concept'] for r in request.query['relaxations'] if r['id'] == x['id'])
                    strict, wider = (set(sum(es._tag_rows(store, request.tags[t], 's.patient_idx, s.event_id'), []))
                                     for t in (steps[x['step']], wide))
                    self.assertLessEqual(strict, wider, (seed, x))
                    mine = {name: {(p, e) for p, es_ in value.carried.items() for e, tags in es_.items() if name in tags}
                            for name in (f"#ref-value/{x['step']}/own", f"#ref-value/{x['step']}/{x['id']}")}
                    self.assertLessEqual(*mine.values())
                    checked += 1
        self.assertGreater(checked, 50)


class PlantedDefectTests(unittest.TestCase):
    """Each defect in the store's value conditions makes the differential fail."""

    def failures(self, target, replacement, seeds=range(160)):
        ingest, store, prepared = _STATE['plain']
        with patch.object(es, target, replacement):
            wrong, _ = compare(store, ingest, prepared, seeds, 'plain', exhaustive_every=10 ** 9)
        return len(wrong)

    def test_a_threshold_off_by_one_micro_unit_fails(self):
        real = es._micro_bounds

        def off_by_one(condition):  # `ge t` read as `gt t`: the value on the threshold is lost
            lo, hi = real(condition)
            return (None if lo is None else lo + 1 if condition[1][1] else lo), hi
        self.assertGreater(self.failures('_micro_bounds', off_by_one), 0)

    def test_an_ignored_unit_fails(self):
        def no_unit(spec):
            return 'cc.ancestor = ? AND ? IS NOT NULL', [spec[0], spec[1][0]]
        self.assertGreater(self.failures('_tag_where', no_unit), 0)

    def test_a_null_micro_unit_value_read_as_matching_or_not_fails(self):
        self.assertGreater(self.failures('_decimal_satisfies', lambda condition, text: True), 0)
        self.assertGreater(self.failures('_decimal_satisfies', lambda condition, text: False), 0)


def small(patients, closure=None):
    return {'profile': es.INGEST_PROFILE, 'dataset_id': 'value-cases',
            'closure': {'version': 'v', 'ancestors': closure or ANCESTORS}, 'patients': patients}


def point(eid, item, hour, source='chart', **extra):
    return {'event_id': eid, 'kind': 'measurement', 'source': source, 'concepts': [item],
            'start': [T0 + hour * HOUR, T0 + hour * HOUR], 'evidence': {}, **extra}


def patient(pid, events, series, coverage=True, excluded=None):
    out = {'patient_id': pid, 'clock_id': f'c{pid}', 'events': events, 'series': series,
           'coverage': [{'source': 'chart', 'lo': T0 - 100 * HOUR, 'hi': T0 + 100 * HOUR, 'basis': 'declared'},
                        {'source': 'pump', 'lo': T0 - 100 * HOUR, 'hi': T0 + 100 * HOUR, 'basis': 'declared'}]
           if coverage else []}
    if excluded:
        out['excluded'] = excluded
    return out


def one_step(concept, occurrence='any', scope='any'):
    return {'profile': tg.PROFILE_ID, 'scope': scope, 'steps': [{'id': 'A', 'concept': concept, 'occurrence': occurrence}]}


class SemanticsTests(unittest.TestCase):
    """Constructed cases for each decision of the stage-2 design."""

    def run_case(self, ingest, query, conditions, window=(-50, 50)):
        windows = {p['patient_id']: {'start': T0 + window[0] * HOUR, 'end': T0 + window[1] * HOUR}
                   for p in ingest['patients']}
        with tempfile.TemporaryDirectory() as tmp, es.open_store(es.build(ingest, tmp)) as store:
            out = es.match(store, query, windows, conditions=conditions)
        want = ref.exhaustive_match(ingest, query, windows, conditions)
        self.assertEqual(json.dumps([unfiltered(r) for r in out["results"]]), json.dumps(want))
        return {r['patient_id']: r for r in out['results']}, out

    def test_thresholds_are_exact_at_the_micro_unit_and_below_it(self):
        values = ('65', '65.000001', '65.0000001', '64.9999999', '1e30')
        g = small([patient(f'P{k}', [point('m', 'i:map', 1)], [series_row('m', 'i:map', v, 'mmHg')])
                   for k, v in enumerate(values)])
        for cond, want in ((('ge', '65'), {'P0', 'P1', 'P2', 'P4'}), (('gt', '65'), {'P1', 'P2', 'P4'}),
                           (('gt', '65.0000001'), {'P1', 'P4'}), (('le', '65.0000001'), {'P0', 'P2', 'P3'}),
                           (('lt', '65'), {'P3'}), (('eq', '65.0000001'), {'P2'}), (('eq', '65.0'), {'P0'}),
                           (('ge', '1e29'), {'P4'})):
            got, _ = self.run_case(g, one_step('i:map'), {'profile': CP, 'steps': {'A': {'unit': 'mmHg', cond[0]: cond[1]}}})
            self.assertEqual({pid for pid, r in got.items() if r['status'] == 'exact'}, want, cond)
        self.assertIsNone(sv.micro_value(sv.decimal_of('65.0000001')))  # the case is a null micro-unit value

    def test_the_unit_is_literal_and_an_unrecorded_unit_is_not_comparable(self):
        g = small([patient('P0', [point('m', 'i:hr', 1)], [series_row('m', 'i:hr', '120', 'beats/min')]),
                   patient('P1', [point('m', 'i:hr', 1)], [series_row('m', 'i:hr', '120', 'bpm')])])
        cond = lambda unit: {'profile': CP, 'steps': {'A': {'unit': unit, 'ge': '100'}}}
        got, _ = self.run_case(g, one_step('i:hr'), cond('bpm'))
        self.assertEqual((got['P0']['status'], got['P1']['status']), ('none', 'exact'))
        got, _ = self.run_case(g, one_step('i:hr'), cond('/min'))  # no source records i:hr in this unit
        self.assertEqual({r['reason'] for r in got.values()}, {'NO_SOURCE_RECORDS_CONCEPT'})

    def test_a_grouping_reads_every_item_it_covers_in_the_unit_and_never_an_unmeasured_event(self):
        fluid = {'event_id': 'x', 'kind': 'exposure', 'source': 'pump', 'concepts': ['i:fluid'],
                 'start': [T0, T0], 'end': [T0 + HOUR, T0 + HOUR], 'evidence': {}}
        g = small([patient('P0', [point('m', 'i:map', 1)], [series_row('m', 'i:map', '50', 'mmHg')]),
                   patient('P1', [point('m', 'i:temp', 1)], [series_row('m', 'i:temp', '50', 'C')]),
                   patient('P2', [fluid, point('m', 'i:hr', 2)], [series_row('m', 'i:hr', '50', 'mmHg')]),
                   patient('P3', [fluid], [])])
        got, out = self.run_case(g, one_step('g:vitals'), {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': '65'}}})
        self.assertEqual({p: r['status'] for p, r in got.items()},
                         {'P0': 'exact', 'P1': 'none', 'P2': 'exact', 'P3': 'none'})
        self.assertEqual(got['P2']['binding'], {'A': 'm'})
        self.assertEqual(list(out['value_conditions']['tags'].values()),
                         [{'concept': 'g:vitals', 'condition': {'unit': 'mmHg', 'lt': '65'}}])

    def test_first_and_last_are_the_first_and_last_events_that_satisfy_the_condition(self):
        g = small([patient('P0', [point('a', 'i:map', 1), point('b', 'i:map', 2), point('c', 'i:map', 3)],
                           [series_row('a', 'i:map', '80', 'mmHg'), series_row('b', 'i:map', '60', 'mmHg'),
                            series_row('c', 'i:map', '62', 'mmHg')])])
        cond = {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': '65'}}}
        got, _ = self.run_case(g, one_step('i:map', 'first'), cond)
        self.assertEqual(got['P0']['binding'], {'A': 'b'})
        got, _ = self.run_case(g, one_step('i:map', 'last'), cond)
        self.assertEqual(got['P0']['binding'], {'A': 'c'})

    def test_excluded_records_of_the_item_reach_a_value_step_whatever_their_value(self):
        excluded = [{'source': 'chart', 'concept': 'i:map', 'start': [T0, T0], 'reason': 'MISSING_UNIT'}]
        g = small([patient('P0', [point('m', 'i:map', 1)], [series_row('m', 'i:map', '80', 'mmHg')], excluded=excluded),
                   patient('P1', [point('m', 'i:map', 1)], [series_row('m', 'i:map', '60', 'mmHg')], excluded=excluded),
                   patient('P2', [point('m', 'i:map', 1)], [series_row('m', 'i:map', '60', 'mmHg')])])
        cond = {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': '65'}}}
        got, _ = self.run_case(g, one_step('i:map'), cond)
        # The unitless row might have been below 65: `none` is not comparable; an `any` match stands.
        self.assertEqual((got['P0']['status'], got['P0'].get('reason')), ('not_comparable', 'ADMISSION_EXCLUDED_RECORDS'))
        self.assertEqual((got['P1']['status'], got['P2']['status']), ('exact', 'exact'))
        # The excluded row (hour 0) is earlier than the first satisfying event: it might have been the first.
        got, _ = self.run_case(g, one_step('i:map', 'first'), cond)
        self.assertEqual((got['P1']['status'], got['P2']['status']), ('not_comparable', 'exact'))

    def test_a_value_relaxation_is_tried_by_cost_and_named_in_the_result(self):
        g = small([patient('P0', [point('m', 'i:map', 1)], [series_row('m', 'i:map', '70', 'mmHg')])])
        q = {**one_step('i:map'), 'budget': '1'}
        cond = {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': '65'}},
                'relaxations': [{'id': 'v75', 'kind': 'widen_value', 'step': 'A', 'cost': '0.5',
                                 'condition': {'unit': 'mmHg', 'lt': '75'}}]}
        got, _ = self.run_case(g, q, cond)
        self.assertEqual((got['P0']['status'], got['P0']['relaxations'], got['P0']['cost']), ('relaxed', ['v75'], '0.5'))

    def test_a_request_without_conditions_is_answered_as_before(self):
        ingest, store, _ = _STATE['plain']
        q, c, w = case(3)
        plain = es.match(store, q, w)
        self.assertNotIn('value_conditions', plain)
        self.assertNotIn('conditions_digest', plain['context'])
        self.assertEqual(json.dumps(plain), json.dumps(es.match(store, q, w, conditions=None)))
        tagged = es.match(store, q, w, conditions=c)
        self.assertEqual(tagged['context']['value_rule'], es.VALUE_RULE)
        self.assertEqual(tagged['context']['query_digest'], plain['context']['query_digest'])
        self.assertNotEqual(tagged['context_id'], plain['context_id'])

    def test_requests_outside_the_contract_are_refused_by_name(self):
        q = {**one_step('i:map'), 'budget': '1'}
        good = {'unit': 'mmHg', 'lt': '65'}
        cases = {
            'INVALID_CONDITIONS': [{'steps': {'A': good}}, {'profile': CP}, {'profile': CP, 'steps': {}},
                                   {'profile': CP, 'steps': {'A': good}, 'extra': 1}],
            'UNKNOWN_CONDITION_STEP': [{'profile': CP, 'steps': {'B': good}}],
            'INVALID_CONDITION': [{'profile': CP, 'steps': {'A': {'lt': '65'}}},
                                  {'profile': CP, 'steps': {'A': {'unit': '', 'lt': '65'}}},
                                  {'profile': CP, 'steps': {'A': {'unit': ' mmHg', 'lt': '65'}}},
                                  {'profile': CP, 'steps': {'A': {'unit': 'mmHg'}}},
                                  {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': '65', 'le': '66'}}},
                                  {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'eq': '65', 'le': '66'}}},
                                  {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'ne': '65'}}}],
            'INVALID_CONDITION_VALUE': [{'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': 65}}},
                                        {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': '<65'}}},
                                        {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'lt': 'NaN'}}}],
            'EMPTY_CONDITION': [{'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'gt': '65', 'lt': '65'}}},
                                {'profile': CP, 'steps': {'A': {'unit': 'mmHg', 'ge': '66', 'le': '65'}}}],
            'VALUE_RELAXATION_MUST_WIDEN': [
                {'profile': CP, 'steps': {'A': good}, 'relaxations': [
                    {'id': 'v', 'kind': 'widen_value', 'step': 'A', 'cost': '1', 'condition': c}]}
                for c in ({'unit': 'mmHg', 'lt': '64'}, {'unit': 'mmHg', 'le': '64.9999999'},
                          {'unit': 'kPa', 'lt': '70'}, {'unit': 'mmHg', 'gt': '0'})],
            'VALUE_RELAXATION_STEP_NOT_CONDITIONED': [
                {'profile': CP, 'steps': {'A': good}, 'relaxations': [
                    {'id': 'v', 'kind': 'widen_value', 'step': 'B', 'cost': '1', 'condition': good}]}],
            'INVALID_VALUE_RELAXATION': [
                {'profile': CP, 'steps': {'A': good}, 'relaxations': [{'id': 'v', 'kind': 'widen_concept', 'step': 'A',
                                                                       'cost': '1', 'condition': good}]}],
        }
        for code, requests in cases.items():
            for c in requests:
                with self.subTest(code=code, conditions=c), self.assertRaises(es.StoreError) as caught:
                    es.Request(q, c)
                self.assertEqual(caught.exception.code, code)
        q2 = {**q, 'relaxations': [{'id': 'v', 'kind': 'widen_concept', 'step': 'A', 'concept': 'g:vitals', 'cost': '1'}]}
        with self.assertRaises(es.StoreError) as caught:
            es.Request(q2, {'profile': CP, 'steps': {'A': good}, 'relaxations': [
                {'id': 'v', 'kind': 'widen_value', 'step': 'A', 'cost': '1', 'condition': {'unit': 'mmHg', 'lt': '70'}}]})
        self.assertEqual(caught.exception.code, 'DUPLICATE_RELAXATION')
        # Containment is exact: the same bound loosened from strict to inclusive widens; one micro-unit less does not.
        es.Request(q, {'profile': CP, 'steps': {'A': good}, 'relaxations': [
            {'id': 'v', 'kind': 'widen_value', 'step': 'A', 'cost': '1', 'condition': {'unit': 'mmHg', 'le': '65.0'}}]})
        _, store, _ = _STATE['plain']
        with self.assertRaises(es.StoreError) as caught:
            es.match(store, one_step('nothing'), {}, conditions={'profile': CP, 'steps': {'A': good}})
        self.assertEqual(caught.exception.code, 'UNKNOWN_QUERY_CONCEPT')


if __name__ == '__main__':
    unittest.main()
