"""Load the pinned public MIMIC-IV demo into the execution store and save aggregate evidence only.

The demo goes through the canonical route (`patterns/mimic_store_ingest.py`): admission, one
pending claim per admitted row, a caller-declared accept-all analysis policy, the SULO adapter's
graph route and `ingest_rows`, then the store. The report holds counts, refusals, the snapshot
identifier, timings and a differential over seeded queries on source item classes. It holds no
patient identifiers, rows or times.

The differential runs every query three ways over every patient:
- the store path (`execution_store.match`: candidate filter, engine, coverage and exclusion rules);
- `execution_store_reference.exhaustive_match` over the whole ingest;
- the engine over each patient's adapter rows directly, with times `origin_us + start_us`, the
  rows' own concepts plus the item's dictionary grouping, and neither rule.
A mismatch is any patient whose store status differs from the reference, or whose full result
differs where the store ran the engine; or whose decision differs from the adapter-row engine's
(status, binding, relaxations, cost, certainty), reading the store's `not_comparable` as the
adapter's `none`. The report requires none. Separately, every event's times and recorded time are
checked against its raw source row.

Chartevents (D27). Chartevents joins by route (b): bulk rows from `describe()` claims. On the demo the check is a
census: every chart row through the independent raw-row check, and every claim store through the round trip and
the measurement graph route, whose rows must equal the bulk rows field for field and the rows read back from the
built store. The third differential path then holds every chart event as route (a) gave it. The reference path is
`PreparedReference` (engine inputs built once per patient); `exhaustive_match`, which filters nothing, runs on the
first `EXHAUSTIVE_SEEDS` queries, where the prepared reference and an unfiltered adapter path must agree with it.

Value conditions (D28). `VALUE_SEEDS` further queries carry value conditions on measured steps, drawn around recorded
values (on a value, so a threshold off by one micro-unit changes the answer; in another unit one time in ten), and value
relaxations. The store must equal the prepared reference with the request on every query and `exhaustive_match` on the
first `VALUE_EXHAUSTIVE_SEEDS`, result for result; each relaxation must widen to a tag holding the stricter tag's events;
and planted defects (a threshold off by one, the unit ignored, null micro-unit values read as matching or not) must
produce mismatches where the demo holds a row they affect. The adapter rows carry no values, so they are not a third path.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import random
import sys
import tempfile
import time
import zlib

from . import exact_intervals as ei
from . import execution_store as es
from . import execution_store_reference as ref
from . import mimic_claim_import as importer
from . import mimic_chartevents as chart
from . import mimic_chartevents_check as chart_check
from . import mimic_chartevents_raw_check as raw_check
from . import mimic_store_ingest as msi
from . import prepared_reference as pr
from . import trajectory_graph as tg
from . import verify_mimic_demo as demo

DATASET = 'mimic-iv-demo-2.2'
SEEDS = 500
EXHAUSTIVE_SEEDS = 50
CHART_PIN = ei.ROOT / 'data/clinical-source-demo-pin.json'
STATEMENT = ('Chartevents rows are built from describe() claim identity (route b). On the demo every claim store was '
             'sent through the measurement graph route (a census): its rows equalled the bulk rows field for field, and '
             'the rows read back from the built store. That comparison shares the row mapping with the bulk route, '
             'which the raw-row check verifies independently over every chart row. For full MIMIC-IV the design is a '
             'deterministic sample of claim stores, not a census: rows outside the sample would be tied to the graph '
             'by claim identity only.')
MINUTE = 60_000_000
DAY = 1440 * MINUTE
COSTS = ('0.25', '0.5', '0.75', '1')
MATCHED = ('exact', 'possible', 'relaxed', 'unresolved')
DECISION = ('binding', 'relaxations', 'cost', 'certainty')
REPORT = ei.ROOT / 'verification/execution-store-mimic-demo-report.json'
FILES = tuple(sorted(set(msi.FILES + (
    'patterns/verify_mimic_store_ingest.py', 'patterns/verify_mimic_demo.py', 'patterns/mimic_store_ingest.py',
    'patterns/execution_store_reference.py', 'data/mimic-inputevents-demo-pin.json'))))
CHART_FILES = tuple(sorted(set(FILES + msi.CHART_FILES + (
    'patterns/prepared_reference.py', 'data/clinical-source-demo-pin.json'))))


# ---------------------------------------------------------------- seeded queries over source item classes

def concept_pool(ingest):
    """Item classes and groupings that at least two patients carry."""
    ancestors = ingest['closure']['ancestors']
    seen = defaultdict(set)
    for p in ingest['patients']:
        for e in p['events']:
            for c in e['concepts']:
                for x in {c, *ancestors[c]}:
                    if x.startswith((importer.ITEM_NS, msi.GROUP_NS)):
                        seen[x].add(p['patient_id'])
    return sorted(c for c, ps in seen.items() if len(ps) >= 2)


def random_windows(rng, ingest):
    out = {}
    for p in ingest['patients']:
        if not p['coverage'] or rng.random() < 0.05:
            out[p['patient_id']] = None
            continue
        lo, hi = min(c['lo'] for c in p['coverage']), max(c['hi'] for c in p['coverage'])
        width = max(hi - lo, DAY)
        if rng.random() < 0.08:  # a window after the last ICU stay
            start = hi + rng.randint(1, 30) * DAY
        else:
            start = lo + int(width * rng.uniform(-0.1, 0.6))
        out[p['patient_id']] = {'start': start, 'end': start + int(width * rng.uniform(0.2, 1.0))}
    return out


def random_query(rng, pool, ingest):
    """A 2-4 step query on source item classes, shaped around one anchor patient's events.

    Each step takes the anchor event's item class, its dictionary grouping, or a decoy item with a
    widening back; each gap fits, fits tightly, misses (perhaps with an extend_max) or stays open.
    """
    ancestors = ingest['closure']['ancestors']
    anchor = rng.choice([p for p in ingest['patients'] if len(p['events']) >= 4])
    k = rng.randint(2, 4)
    chosen = rng.sample(anchor['events'], k)
    steps, relaxations = [], []
    for i, e in enumerate(chosen):
        own = next(c for c in e['concepts'] if c.startswith(importer.ITEM_NS))
        concept = rng.choice([own, *ancestors[own]]) if rng.random() < 0.85 else rng.choice(pool)
        step = {'id': f'S{i}', 'concept': concept, 'occurrence': rng.choice(('any', 'any', 'any', 'first', 'last'))}
        if step['occurrence'] != 'any':
            # Items recur within a stay: aim the step at the anchor's first or last such event.
            carrying = [x for x in anchor['events'] if any(concept == c or concept in ancestors[c] for c in x['concepts'])]
            if carrying:
                pick = min if step['occurrence'] == 'first' else max
                chosen[i] = pick(carrying, key=lambda x: (x['start'][0], x.get('end', x['start'])[0], x['event_id']))
        if rng.random() < 0.2:  # decoy: only the widening reaches the anchor's event
            relaxations.append({'id': f'wS{i}', 'kind': 'widen_concept', 'step': step['id'], 'concept': concept,
                                'cost': rng.choice(COSTS)})
            step['concept'] = rng.choice(pool)
        elif rng.random() < 0.3:
            relaxations.append({'id': f'wS{i}', 'kind': 'widen_concept', 'step': step['id'],
                                'concept': rng.choice(sorted(ancestors[concept] or pool)), 'cost': rng.choice(COSTS)})
        steps.append(step)
    relations = []

    def relation(a, b):
        r = {'id': f'R{len(relations) + 1}', 'from': a, 'to': b}
        if rng.random() < 0.1:
            return {**r, 'kind': 'during', 'grace': rng.choice((0, 30, 120)) * MINUTE}
        gap = chosen[int(b[1:])]['start'][0] - chosen[int(a[1:])]['start'][0]
        mode = rng.choice(('fit', 'fit', 'tight', 'miss', 'open'))
        if mode == 'fit':
            r.update(min=gap - rng.randint(0, 180) * MINUTE, max=gap + rng.randint(0, 180) * MINUTE)
        elif mode == 'tight':
            r.update(min=gap, max=gap)
        elif mode == 'miss':
            r.update(min=gap - rng.randint(180, 2880) * MINUTE, max=gap - rng.randint(1, 120) * MINUTE)
            if rng.random() < 0.7:
                relaxations.append({'id': f"x{r['id']}", 'kind': 'extend_max', 'relation': r['id'],
                                    'max': r['max'] + rng.choice((60, 720, 4320)) * MINUTE, 'cost': rng.choice(COSTS)})
        elif rng.random() < 0.5:
            r['min'] = 0
        return {**r, 'kind': 'after'}

    for i in range(1, k):
        a, b = steps[rng.randrange(i)]['id'], steps[i]['id']
        relations.append(relation(*((b, a) if rng.random() < 0.3 else (a, b))))
    if k >= 3 and rng.random() < 0.3:  # a cycle
        relations.append(relation(steps[0]['id'], steps[-1]['id']))
    if rng.random() < 0.15:
        relations.append({'id': f'R{len(relations) + 1}', 'kind': 'after', 'from': 'START',
                          'to': rng.choice(steps)['id'], 'min': 0})
    return {'profile': tg.PROFILE_ID, 'scope': rng.choice(tg.SCOPES), 'steps': steps, 'relations': relations,
            'relaxations': relaxations, 'budget': rng.choice((0, '0.5', '1', '1')),
            'search_budget': rng.choice((1, 40, 300))}


# ---------------------------------------------------------------- the adapter rows as engine input

def adapter_engine_input(rows, items, chart_events=()):
    """{patient: engine events} straight from `ingest_rows`, without the store mapping, and, with chartevents, every
    chart event as route (a) gave it: (patient, event id, item class, start lo, start hi) points (D27).

    Times are the clock origin's coordinate plus the tightened bounds; concepts are the rows' own,
    plus each item class's dictionary grouping (D14), which the adapter does not know.
    """
    out = defaultdict(list)
    for pid, eid, item_class, lo, hi in chart_events:
        concepts = {item_class}
        category = items[item_class[len(importer.ITEM_NS):]]['category']
        if category:
            concepts.add(msi.group_iri(category))
        out[pid].append({'id': eid, 'concepts': sorted(concepts), 'start': [lo, hi]})
    for r in rows:
        origin = msi.micros(r['clock_origin'])
        concepts = {c['concept'] for c in r['concepts']}
        for c in list(concepts):
            if c.startswith(importer.ITEM_NS) and items[c[len(importer.ITEM_NS):]]['category']:
                concepts.add(msi.group_iri(items[c[len(importer.ITEM_NS):]]['category']))
        out[r['patient_id']].append({'id': r['event_id'], 'concepts': sorted(concepts),
                                     'start': [origin + v for v in r['start_us']],
                                     'end': [origin + v for v in r['end_us']],
                                     'start_var': r['start_var'], 'end_var': r['end_var'], 'proper': r['proper']})
    return out


ADMISSION_CASES = ('none_not_comparable', 'first_or_last_displaced', 'relaxed_unresolved', 'possible_incomplete')


def admission_case(g, a):
    """Which excluded-records case turned the admitted-only engine result `a` into the store's `g`."""
    if g.get('reason') == 'ADMISSION_EXCLUDED_RECORDS':
        if g['status'] == 'unresolved':
            return 'relaxed_unresolved'
        return 'none_not_comparable' if a['status'] == 'none' else 'first_or_last_displaced'
    if g['status'] == 'possible' and 'excluded_steps' in g:
        return 'possible_incomplete'
    return None


def agrees(g, a, case):
    """(whether the store's result `g` is the admitted-only engine result `a` under the store's rules,
    whether a binding was compared). Decisions are compared field by field where a binding survives."""
    if case == 'first_or_last_displaced':
        return a['status'] in MATCHED, False
    if case == 'relaxed_unresolved':
        return a['status'] == 'relaxed' and all(g['relaxed_match'][k] == a[k] for k in DECISION), True
    if g['status'] == 'not_comparable':
        return a['status'] == 'none', False
    same = g['status'] == a['status']
    if same and 'binding' in a:
        same = all(g.get(k) == a.get(k) for k in DECISION)
    if same and 'relaxed_match' in a:
        same = all(g['relaxed_match'].get(k) == a['relaxed_match'].get(k) for k in DECISION)
    return same, 'binding' in a


def _filtered(events, index, wanted):
    """The engine events carrying a wanted concept, in order (what any step could bind)."""
    return [events[i] for i in sorted({i for c in wanted for i in index.get(c, ())})]


def differential(store, ingest, engine_input, seeds=SEEDS, first_seed=0, exhaustive_seeds=EXHAUSTIVE_SEEDS):
    """The store path against exhaustive matching and against the adapter rows, over every patient.

    Against the reference, every status must agree, and every result the store's engine decided must
    agree in full. Against the adapter rows, the decision must agree once the store's comparability
    rules are allowed for (`agrees`): status, binding, relaxations, cost and certainty. Returns counts,
    the status mix, `not_comparable` by reason, the excluded-records cases, relaxed results by
    relaxation kind, and the first few mismatches (seed and status only) for diagnosis.
    """
    pool = concept_pool(ingest)
    ids = {i: p for i, p, *_ in store.rows("patients")}
    prepared = pr.PreparedReference(ingest)
    index, linked = {}, {}
    for pid, events in engine_input.items():
        index[pid] = {}
        for k, e in enumerate(events):
            for c in e['concepts']:
                index[pid].setdefault(c, []).append(k)
        linked[pid] = ref.shares_variables({'events': [{'event_id': e['id'], 'start_var': e.get('start_var', e['id'] + '.s'),
                                                         **({'end': e['end'], 'end_var': e.get('end_var', e['id'] + '.f')}
                                                            if 'end' in e else {})} for e in events]})
    slice_results, prepared_mismatches = 0, 0
    mix, adapter_mix, reasons, relaxed_by_kind = Counter(), Counter(), Counter(), Counter()
    bindings_compared, cases = 0, Counter()
    mismatches, adapter_mismatches, dropped, filtered, examples = 0, 0, 0, 0, []
    for seed in range(first_seed, first_seed + seeds):
        rng = random.Random(seed)
        query, windows = random_query(rng, pool, ingest), random_windows(rng, ingest)
        kinds = {x['id']: x['kind'] for x in query['relaxations']}
        kept = {ids[i] for i in es.candidates(store, query)['patient_idx']}
        got = es.match(store, query, windows)['results']
        exhaustive = seed - first_seed < exhaustive_seeds
        want = prepared.match(query, windows)
        if exhaustive:  # the reference that filters nothing, for the event-filter argument the others share
            full = ref.exhaustive_match(ingest, query, windows)
            slice_results += len(full)
            prepared_mismatches += sum(a != b for a, b in zip(want, full, strict=True))
            want = full
        wanted = set().union(*ref.step_concepts(query).values())
        for g, w in zip(got, want, strict=True):
            assert g['patient_id'] == w['patient_id'], (seed, g['patient_id'], w['patient_id'])
            pid = w['patient_id']
            mix[w['status']] += 1
            if g['status'] == 'not_comparable':
                reasons[g['reason']] += 1
            used = g.get('relaxations') or g.get('relaxed_match', {}).get('relaxations') or []
            for i in used:
                relaxed_by_kind[f"{g['status']}:{kinds[i]}"] += 1
            if pid not in kept:
                filtered += 1
                dropped += w['status'] in MATCHED
            if g['status'] != w['status'] or (g.get('reason') != 'FILTERED' and g != w):
                mismatches += 1
                examples.append({'seed': seed, 'store': g['status'], 'reference': w['status']})
            events = engine_input.get(pid, [])
            if not exhaustive and not linked.get(pid):
                events = _filtered(events, index.get(pid, {}), wanted)
            a = tg.match(query, {'patient_id': pid, 'window': windows.get(pid), 'events': events})
            adapter_mix[a['status']] += 1
            case = admission_case(g, a)
            if case:
                cases[case] += 1
            same, compared = agrees(g, a, case)
            bindings_compared += compared
            if not same:
                adapter_mismatches += 1
                examples.append({'seed': seed, 'store': g['status'], 'adapter_rows': a['status']})
    return {'queries': seeds, 'first_seed': first_seed, 'patient_results': sum(mix.values()),
            'reference': ('prepared_reference (engine inputs once per patient) on every query; exhaustive_match and an '
                          'unfiltered adapter path on the first queries'),
            'exhaustive_slice': {'queries': min(seeds, exhaustive_seeds), 'patient_results': slice_results,
                                 'prepared_reference_mismatches': prepared_mismatches},
            'mismatches': mismatches, 'adapter_row_mismatches': adapter_mismatches, 'dropped_matches': dropped,
            'filtered_by_candidate_filter': filtered, 'status_mix': dict(sorted(mix.items())),
            'adapter_row_status_mix': dict(sorted(adapter_mix.items())),
            'adapter_row_bindings_compared': bindings_compared,
            'not_comparable_by_reason': dict(sorted(reasons.items())),
            'admission_exclusion_cases': {k: cases[k] for k in ADMISSION_CASES},
            'relaxations_used_by_status_and_kind': dict(sorted(relaxed_by_kind.items())),
            'mismatch_examples': examples[:5]}


# ---------------------------------------------------------------- value conditions (D28, chartevents stage 2)

VALUE_SEEDS = 200
VALUE_EXHAUSTIVE_SEEDS = 20
VALUE_DEFECT_SEEDS = 40
VALUE_FIRST_SEED = 100_000  # value queries draw from their own seeds, apart from the 500 above


def series_pool(ingest):
    """{concept: [(unit, spelling)]}: every series row's unit and value under its item class and the item's grouping,
    the values a condition is drawn around."""
    ancestors = ingest['closure']['ancestors']
    out = defaultdict(list)
    for p in ingest['patients']:
        for r in p.get('series', []):
            for c in (r['item_class'], *ancestors[r['item_class']]):
                out[c].append((r['unit'], r['value_lexical']))
    return {c: sorted(v) for c, v in out.items()}


def _value_bounds(rng, values, text):
    """Bounds around a value: on it (so a threshold off by one micro-unit moves the answer), or a range to another."""
    from . import series_values as sv
    op = rng.choice(('ge', 'gt', 'le', 'lt', 'eq', 'range', 'range'))
    if op == 'eq':
        return {'eq': text}
    if op != 'range':
        return {op: text}
    a, b = sorted((text, rng.choice(values)), key=sv.decimal_of)
    if sv.decimal_of(a) == sv.decimal_of(b):
        return {'ge': a, 'le': b}
    return {rng.choice(('ge', 'gt')): a, rng.choice(('le', 'lt')): b}


def _value_wider(rng, values, condition):
    """A condition containing `condition`: each bound kept, loosened from strict to inclusive, or moved outwards to
    another value of the concept; a bound dropped leaves the other, and a condition left with none is widened instead."""
    from . import series_values as sv
    if 'eq' in condition:
        sides = [('ge', condition['eq']), ('le', condition['eq'])]
    else:
        sides = [(k, condition[k]) for k in ('gt', 'ge', 'lt', 'le') if k in condition]
    out = {'unit': condition['unit']}
    for key, text in sides:
        mode = rng.choice(('keep', 'loosen', 'move', 'move'))
        if mode == 'loosen':
            key = {'gt': 'ge', 'lt': 'le'}.get(key, key)
        elif mode == 'move':
            v = sv.decimal_of(text)
            lower = key in ('gt', 'ge')
            pool = [x for x in values if (sv.decimal_of(x) < v if lower else sv.decimal_of(x) > v)]
            if pool:
                text = rng.choice(pool)
        out[key] = text
    return out


def random_value_request(rng, pool, ingest, values):
    """A seeded query (`random_query`) with value conditions on the steps whose concept has measured values: each drawn
    around one of the concept's recorded values, in that value's unit or, one time in ten, in another unit of the demo;
    one time in three with a value relaxation. Returns (query, conditions or None, windows)."""
    query, windows = random_query(rng, pool, ingest), random_windows(rng, ingest)
    units = sorted({u for rows in values.values() for u, _ in rows})
    steps, relaxations = {}, []
    for step in query['steps']:
        rows = values.get(step['concept'])
        if not rows or rng.random() < 0.15:
            continue
        unit, text = rng.choice(rows)
        if rng.random() < 0.1:
            unit = rng.choice(units)
        mine = [t for u, t in rows if u == unit] or [text]
        condition = {'unit': unit, **_value_bounds(rng, mine, text)}
        steps[step['id']] = condition
        if rng.random() < 0.33:
            relaxations.append({'id': f"v{step['id']}", 'kind': 'widen_value', 'step': step['id'],
                                'cost': rng.choice(COSTS), 'condition': _value_wider(rng, mine, condition)})
    if not steps:
        return query, None, windows
    conditions = {'profile': es.CONDITIONS_PROFILE, 'steps': steps}
    if relaxations:
        conditions['relaxations'] = relaxations
    return query, conditions, windows


def value_cases(ingest, seeds, first_seed=VALUE_FIRST_SEED):
    """The seeded value requests, one per seed (a seed whose query has no measured step draws again)."""
    pool, values = concept_pool(ingest), series_pool(ingest)
    for seed in range(first_seed, first_seed + seeds):
        rng = random.Random(seed)
        while True:
            query, conditions, windows = random_value_request(rng, pool, ingest, values)
            if conditions is not None:
                yield seed, query, conditions, windows
                break


def _unfiltered(result):
    """A store result as the reference writes it: the candidate filter's `none` is the engine's complete `none`."""
    return {k: v for k, v in result.items() if k != 'reason'} if result.get('reason') == 'FILTERED' else result


def _value_mismatches(store, prepared, cases, ingest=None, exhaustive_seeds=0):
    wrong, mix, compared, examples = 0, Counter(), 0, []
    for k, (seed, query, conditions, windows) in enumerate(cases):
        got = es.match(store, query, windows, conditions=conditions)['results']
        wants = [prepared.match(query, windows, conditions)]
        if k < exhaustive_seeds:
            wants.append(ref.exhaustive_match(ingest, query, windows, conditions))
        for want in wants:
            for g, w in zip(got, want, strict=True):
                assert g['patient_id'] == w['patient_id'], (seed, g['patient_id'], w['patient_id'])
                compared += 1
                if json.dumps(_unfiltered(g)) != json.dumps(w):
                    wrong += 1
                    examples.append({'seed': seed, 'store': g['status'], 'reference': w['status']})
        mix.update(g['status'] for g in got)
    return wrong, mix, compared, examples


def value_planted_defects():
    """The planted defects of the value conditions (as in `patterns/test_value_conditions.py`), as patches."""
    real = es._micro_bounds

    def off_by_one(condition):  # `ge t` read as `gt t` on exact micro-unit values
        lo, hi = real(condition)
        return (None if lo is None else lo + 1 if condition[1][1] else lo), hi
    return {'threshold_off_by_one': ('_micro_bounds', off_by_one),
            'unit_ignored': ('_tag_where', lambda spec: ('cc.ancestor = ? AND ? IS NOT NULL', [spec[0], spec[1][0]])),
            'null_micro_value_as_matching': ('_decimal_satisfies', lambda condition, text: True),
            'null_micro_value_as_not_matching': ('_decimal_satisfies', lambda condition, text: False)}


def value_differential(store, ingest, seeds=VALUE_SEEDS, exhaustive_seeds=VALUE_EXHAUSTIVE_SEEDS,
                       defect_seeds=VALUE_DEFECT_SEEDS):
    """Value requests (D28): the store against the prepared reference on every query and `exhaustive_match` on the
    first, result for result; every value relaxation checked to widen to a tag holding the stricter tag's events; and
    each planted defect run on the first `defect_seeds` queries, where it must produce mismatches unless the demo holds
    no row it could affect (counted)."""
    from unittest.mock import patch
    prepared = pr.PreparedReference(ingest)
    cases = list(value_cases(ingest, seeds))
    wrong, mix, compared, examples = _value_mismatches(store, prepared, cases, ingest, exhaustive_seeds)
    shapes, relaxations, violations = Counter(), 0, 0
    values = series_pool(ingest)
    for seed, query, conditions, _ in cases:
        request = es.Request(query, conditions)
        concept = {s['id']: s['concept'] for s in query['steps']}
        for sid, condition in conditions['steps'].items():
            shapes['conditions'] += 1
            recorded = {t for u, t in values.get(concept[sid], ()) if u == condition['unit']}
            shapes['on_a_recorded_value'] += any(condition.get(k) in recorded for k in ('eq', 'ge', 'gt', 'le', 'lt'))
            shapes['unit_not_recorded_for_concept'] += not recorded
            shapes['grouping'] += concept[sid].startswith(msi.GROUP_NS)
        tags = {s['id']: s['concept'] for s in request.query['steps']}
        for x in conditions.get('relaxations', []):
            wide = next(r['concept'] for r in request.query['relaxations'] if r['id'] == x['id'])
            strict, wider = (set(sum(es._tag_rows(store, request.tags[t], 's.patient_idx, s.event_id'), []))
                             for t in (tags[x['step']], wide))
            relaxations += 1
            violations += not strict <= wider
    nulls = store.query('SELECT count(*) FROM series WHERE value_micro IS NULL')[0][0]
    defects = {}
    for name, (target, replacement) in value_planted_defects().items():
        with patch.object(es, target, replacement):
            found = _value_mismatches(store, prepared, cases[:defect_seeds])[0]
        defects[name] = {'queries': min(defect_seeds, len(cases)), 'mismatches': found,
                         'caught': found > 0}
    return {'queries': len(cases), 'first_seed': VALUE_FIRST_SEED, 'patient_results': sum(mix.values()),
            'results_compared': compared,
            'reference': ('prepared_reference with the request (engine inputs once per patient) on every query; '
                          'exhaustive_match with the request on the first queries; each reads conditions independently '
                          'of the store (execution_store_reference.ValueQuery: Decimal on the spelling, the unit letter '
                          'for letter)'),
            'exhaustive_queries': min(exhaustive_seeds, len(cases)), 'mismatches': wrong,
            'status_mix': dict(sorted(mix.items())), 'condition_shapes': dict(sorted(shapes.items())),
            'value_relaxations_checked': relaxations, 'value_relaxation_containment_violations': violations,
            'series_rows_with_null_micro_value': nulls,
            'planted_defects': defects,
            'note': ('a planted defect in reading null micro-unit values cannot fail on a snapshot holding none; the '
                     'synthetic fixtures of patterns/test_value_conditions.py hold many'),
            'mismatch_examples': examples[:5]}


def raw_time_check(ingest, segments):
    """Every event's times against the raw source row: start, end and recorded time, to the microsecond."""
    by_key = {s['id']: s for s in segments}
    checked = mismatched = 0
    for p in ingest['patients']:
        for e in p['events']:
            if e['source'] != msi.SOURCE:
                continue
            segment = by_key[e['evidence']['source_key']]
            checked += 1
            start, end = msi.micros(segment['start']['raw_value']), msi.micros(segment['end']['raw_value'])
            recorded = segment['recorded_at']['raw_value']
            ok = (e['start'] == [start, start] and e['end'] == [end, end]
                  and e.get('recorded_at') == (msi.micros(recorded) if recorded else None)
                  and e['record_sha256'] == segment['source']['row_sha256'])
            mismatched += not ok
    return {'events_checked': checked, 'mismatches': mismatched}


# ---------------------------------------------------------------- the report

def chartevents_pin(run):
    """The chartevents file against its published pin (D11): byte digest and rows."""
    pin = json.loads(CHART_PIN.read_text())['files']['chartevents']
    ei.require(run.manifest['file_sha256'] == pin['file_sha256'] and run.manifest['rows'] == pin['rows'],
               'CHARTEVENTS_PIN_MISMATCH')
    return {'file_sha256_matches_pin': True, 'rows': run.manifest['rows']}


def store_chart_coverage(store):
    """{patient_id: [(lo, hi, basis)]}: the chartevents coverage rows of a built store."""
    out = defaultdict(list)
    for pid, lo, hi, basis in store.query('SELECT p.patient_id, c.lo, c.hi, c.basis FROM coverage c '
                                          'JOIN patients p USING (patient_idx) WHERE c.source = ?', [chart.SOURCE]):
        out[pid].append((lo, hi, basis))
    return dict(out)


def chart_raw_check(result, store=None):
    """The independent raw-row check over every chart row (D27), with chartevents coverage recomputed from the raw
    rows and icustays and compared with the ingest's and, given a built store, the store's. Rows of patients
    inputevents refused are not in the store, and are left out of the count."""
    checker = raw_check.RawRowCheck()
    for p in result['ingest']['patients']:
        mine = {'patient_id': p['patient_id'], 'events': [e for e in p['events'] if e['source'] == chart.SOURCE],
                'excluded': [x for x in p.get('excluded', []) if x['source'] == chart.SOURCE],
                'coverage': [c for c in p['coverage'] if c['source'] == chart.SOURCE]}
        checker.add(mine, p.get('series', []))
    run = result['chart_run']
    out = checker.verify(run.paths['chartevents'], run.paths['d_items'], run.paths['icustays'],
                         store_chart_coverage(store) if store is not None else None)
    expected_rows = run.manifest['rows'] - result['chart_counts'].get('rows_of_refused_patients', 0)
    ei.require(out['passed'] and out['rows'] == expected_rows, 'CHARTEVENTS_RAW_ROW_CHECK_FAILED')
    return out


def built_store_comparison(store, outcomes):
    """Every graph-checked store's route-(a) rows against the rows read back from the built store (D27, §3.6)."""
    by_patient = defaultdict(list)
    for o in outcomes:
        by_patient[o['key'][0]].append(o)
    compared = mismatched = events = 0
    for pid, mine in by_patient.items():
        forms = chart_check.store_forms(store, pid, [eid for o in mine for eid in o['event_ids']])
        for o in mine:
            compared += 1
            events += len(o['event_ids'])
            found = [forms.get(eid) for eid in o['event_ids']]
            mismatched += None in found or chart_check.forms_digest(found) != o['stored_digest']
    return {'stores_compared': compared, 'events_compared': events, 'stores_mismatched': mismatched}


def verify(input_dir, seeds=SEEDS, workers=1, root=None, chartevents=True, census_workers=None,
           exhaustive_seeds=EXHAUSTIVE_SEEDS, value_seeds=VALUE_SEEDS):
    started = time.perf_counter()
    result = msi.convert(input_dir, DATASET, workers=workers, chartevents=chartevents)
    admission = demo.verify_summary(result['prepared']['admission']['summary'])
    timings = dict(result['timings_seconds'])
    chart_report, outcomes, chart_events = None, [], []
    with tempfile.TemporaryDirectory() as tmp:
        if chartevents:
            run = result['chart_run']
            pin = chartevents_pin(run)
            t = time.perf_counter()
            selected = {s['key']: ['census'] for s in result['chart_stores']}
            digests = {s['key']: s['rows_sha256'] for s in result['chart_stores']}
            outcomes = chart_check.check_selected(run, selected, digests, Path(tmp) / 'census', graph=True,
                                                  workers=census_workers or workers)
            census = chart_check.summarise(outcomes)
            timings['chartevents_census'] = time.perf_counter() - t
            ei.require(census['stores_round_tripped'] == len(selected) == census['stores_graph_checked']
                       and census['round_trip_failures'] == 0 and census['mismatched_stores'] == 0,
                       'CHARTEVENTS_GRAPH_CHECK_MISMATCH')
            chart_events = [(o['key'][0], *e) for o in outcomes for e in o['engine_events']]
        t = time.perf_counter()
        snapshot = es.build(result['ingest'], Path(root or tmp) / 'store')
        timings['store_build'] = time.perf_counter() - t
        t = time.perf_counter()
        with es.open_store(snapshot) as store:
            timings['store_open_and_verify'] = time.perf_counter() - t
            manifest = store.manifest
            if chartevents:
                t = time.perf_counter()
                raw = chart_raw_check(result, store)
                timings['chartevents_raw_row_check'] = time.perf_counter() - t
                t = time.perf_counter()
                stored = built_store_comparison(store, outcomes)
                timings['chartevents_built_store_comparison'] = time.perf_counter() - t
                ei.require(stored['stores_mismatched'] == 0 and stored['stores_compared'] == len(outcomes),
                           'CHARTEVENTS_BUILT_STORE_MISMATCH')
                chart_report = {
                    'route': {'choice': 'b', 'check': 'census', 'steps': chart_check.ROUTE_STEPS,
                              'description': ('each admitted chartevents row a point event computed from its describe() '
                                              'claim, with a series row; claim stores of at most 32 claims per ICU '
                                              'stay; rows admission leaves out are excluded records')},
                    'pin': pin, 'counts': result['chart_counts'],
                    'closure': result['ingest']['provenance']['chartevents']['closure'],
                    'raw_row_check': raw, 'census': census, 'built_store_comparison': stored,
                    'sample_design_for_full_mimic': {'rate': chart_check.RATE, 'per_batch': chart_check.PER_BATCH,
                                                     'batch': 'execution-store bucket of the patient',
                                                     'stratified': True},
                    'statement': STATEMENT}
            t = time.perf_counter()
            engine_input = adapter_engine_input(result['rows'], result['prepared']['items'], chart_events)
            check = differential(store, result['ingest'], engine_input, seeds, exhaustive_seeds=exhaustive_seeds)
            timings['differential'] = time.perf_counter() - t
            values = None
            if chartevents and value_seeds:
                t = time.perf_counter()
                values = value_differential(store, result['ingest'], value_seeds)
                timings['value_differential'] = time.perf_counter() - t
    times = raw_time_check(result['ingest'], result['prepared']['admission']['segments'])
    timings['total'] = time.perf_counter() - started
    import resource
    own, children = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    resources = {'cpu_seconds': round(own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime, 1),
                 # ru_maxrss is bytes on macOS and kilobytes on Linux.
                 'peak_rss_bytes_main_process': own.ru_maxrss * (1 if sys.platform == 'darwin' else 1024),
                 'note': 'this run, this machine: CPU time of the verifier and its worker processes'}
    ei.require(check['mismatches'] == 0 and check['adapter_row_mismatches'] == 0 and check['dropped_matches'] == 0
               and check['exhaustive_slice']['prepared_reference_mismatches'] == 0, 'DEMO_STORE_DIFFERENTIAL_MISMATCH')
    ei.require(times['mismatches'] == 0 and times['events_checked'] == result['counts']['events'],
               'DEMO_RAW_TIME_MISMATCH')
    if values is not None:
        defects = values['planted_defects']
        ei.require(values['mismatches'] == 0 and values['value_relaxation_containment_violations'] == 0
                   and defects['threshold_off_by_one']['caught'] and defects['unit_ignored']['caught']
                   and (values['series_rows_with_null_micro_value'] == 0
                        or all(d['caught'] for d in defects.values())), 'DEMO_VALUE_DIFFERENTIAL_MISMATCH')
    report = {
        'profile': 'execution-store-mimic-demo-verification-1.2' if chartevents else
                   'execution-store-mimic-demo-verification-1.0',
        'scope': ('Public-demo inputevents through the SULO adapter and chartevents numeric measurements by route (b) '
                  'into the execution store. Concepts are source item classes with their dictionary groupings, not '
                  'clinical concepts. Aggregates only; no patient rows, identifiers or times.') if chartevents else
                 ('Public-demo inputevents through the SULO adapter into the execution store. Concepts are source item '
                  'classes with their dictionary groupings, not clinical concepts. Aggregates only; no patient rows, '
                  'identifiers or times.'),
        'full_mimic_analyzed': False, 'patient_rows_included': False,
        'clinical_mapping_verified': False, 'concept_basis': msi.CONCEPT_BASIS,
        'route': {'choice': 'a', 'description': ('every admitted row through the canonical route: describe() claims in '
                                                 'record claim stores of at most 32 claims per ICU stay, a '
                                                 'caller-declared accept-all analysis policy, the patient-local graph '
                                                 'route and ingest_rows'),
                  **result['ingest']['provenance']['route'], 'workers': workers},
        'admission': admission,
        'counts': result['counts'],
        'refused': result['refused'],
        'snapshot': {'snapshot_id': manifest['snapshot_id'], 'dataset_id': manifest['dataset_id'],
                     'input_digest': manifest['input_digest'], 'closure_version': manifest['closure_version'],
                     'labels': manifest['labels'], 'selection': manifest['selection'],
                     'ancestor_bases': result['ingest']['provenance']['ancestor_bases'],
                     'tables': {n: t['rows'] for n, t in sorted(manifest['tables'].items())},
                     'duckdb_version': manifest['builder']['engine_version']},
        'raw_source_time_check': times,
        'differential': check,
        'timings_seconds': {k: round(v, 2) for k, v in sorted(timings.items())},
        'resources': resources,
        'artifacts': msi.artifacts(CHART_FILES if chartevents else FILES),
    }
    if chart_report is not None:
        report['chartevents'] = chart_report
        report['route'] = {**report['route'], 'source': msi.SOURCE}
    if values is not None:
        report['value_differential'] = values
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--input-dir', type=Path, required=True,
                        help='directory holding the pinned inputevents, icustays and d_items csv.gz files')
    parser.add_argument('--output', type=Path, default=REPORT)
    parser.add_argument('--seeds', type=int, default=SEEDS)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--store-root', type=Path, help='keep the snapshot here (default: a temporary directory)')
    parser.add_argument('--census-workers', type=int, help='processes for the chartevents census (default: --workers)')
    parser.add_argument('--no-chartevents', action='store_true', help='inputevents only, as before D27')
    parser.add_argument('--value-seeds', type=int, default=VALUE_SEEDS, help='value-condition queries (D28); 0 for none')
    args = parser.parse_args(argv)
    try:
        inputs = {p.resolve() for p in importer.source_paths(args.input_dir).values()}
        ei.require(args.output.resolve() not in inputs, 'OUTPUT_OVERWRITES_INPUT')
        report = verify(args.input_dir, args.seeds, args.workers, args.store_root, chartevents=not args.no_chartevents,
                        census_workers=args.census_workers, value_seeds=args.value_seeds)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + '.tmp')
        temporary.write_text(json.dumps(report, indent=2) + '\n')
        temporary.replace(args.output)
    except (ValueError, LookupError, OSError, EOFError, csv.Error, zlib.error) as error:
        parser.exit(2, json.dumps({'status': 'INVALID_INPUT', 'reason': str(error)}) + '\n')
    print(json.dumps({'snapshot_id': report['snapshot']['snapshot_id'], 'counts': report['counts'],
                      'refused': report['refused'], 'differential': report['differential'],
                      'timings_seconds': report['timings_seconds']}, indent=2))
    return 0


if __name__ == '__main__':
    main()
