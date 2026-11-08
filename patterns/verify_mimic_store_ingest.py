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
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import random
import tempfile
import time
import zlib

from . import exact_intervals as ei
from . import execution_store as es
from . import execution_store_reference as ref
from . import mimic_claim_import as importer
from . import mimic_store_ingest as msi
from . import trajectory_graph as tg
from . import verify_mimic_demo as demo

DATASET = 'mimic-iv-demo-2.2'
SEEDS = 500
MINUTE = 60_000_000
DAY = 1440 * MINUTE
COSTS = ('0.25', '0.5', '0.75', '1')
MATCHED = ('exact', 'possible', 'relaxed', 'unresolved')
DECISION = ('binding', 'relaxations', 'cost', 'certainty')
REPORT = ei.ROOT / 'verification/execution-store-mimic-demo-report.json'
FILES = tuple(sorted(set(msi.FILES + (
    'patterns/verify_mimic_store_ingest.py', 'patterns/verify_mimic_demo.py', 'patterns/mimic_store_ingest.py',
    'patterns/execution_store_reference.py', 'data/mimic-inputevents-demo-pin.json'))))


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
                chosen[i] = pick(carrying, key=lambda x: (x['start'][0], x['end'][0], x['event_id']))
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

def adapter_engine_input(rows, items):
    """{patient: engine events} straight from `ingest_rows`, without the store mapping.

    Times are the clock origin's coordinate plus the tightened bounds; concepts are the rows' own,
    plus each item class's dictionary grouping (D14), which the adapter does not know.
    """
    out = defaultdict(list)
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


def differential(store, ingest, engine_input, seeds=SEEDS, first_seed=0):
    """The store path against exhaustive matching and against the adapter rows, over every patient.

    Against the reference, every status must agree, and every result the store's engine decided must
    agree in full. Against the adapter rows, the decision must agree once the store's comparability
    rules are allowed for (`agrees`): status, binding, relaxations, cost and certainty. Returns counts,
    the status mix, `not_comparable` by reason, the excluded-records cases, relaxed results by
    relaxation kind, and the first few mismatches (seed and status only) for diagnosis.
    """
    pool = concept_pool(ingest)
    ids = {i: p for i, p, *_ in store.rows("patients")}
    mix, adapter_mix, reasons, relaxed_by_kind = Counter(), Counter(), Counter(), Counter()
    bindings_compared, cases = 0, Counter()
    mismatches, adapter_mismatches, dropped, filtered, examples = 0, 0, 0, 0, []
    for seed in range(first_seed, first_seed + seeds):
        rng = random.Random(seed)
        query, windows = random_query(rng, pool, ingest), random_windows(rng, ingest)
        kinds = {x['id']: x['kind'] for x in query['relaxations']}
        kept = {ids[i] for i in es.candidates(store, query)['patient_idx']}
        got = es.match(store, query, windows)['results']
        want = ref.exhaustive_match(ingest, query, windows)
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
            a = tg.match(query, {'patient_id': pid, 'window': windows.get(pid), 'events': engine_input.get(pid, [])})
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
            'mismatches': mismatches, 'adapter_row_mismatches': adapter_mismatches, 'dropped_matches': dropped,
            'filtered_by_candidate_filter': filtered, 'status_mix': dict(sorted(mix.items())),
            'adapter_row_status_mix': dict(sorted(adapter_mix.items())),
            'adapter_row_bindings_compared': bindings_compared,
            'not_comparable_by_reason': dict(sorted(reasons.items())),
            'admission_exclusion_cases': {k: cases[k] for k in ADMISSION_CASES},
            'relaxations_used_by_status_and_kind': dict(sorted(relaxed_by_kind.items())),
            'mismatch_examples': examples[:5]}


def raw_time_check(ingest, segments):
    """Every event's times against the raw source row: start, end and recorded time, to the microsecond."""
    by_key = {s['id']: s for s in segments}
    checked = mismatched = 0
    for p in ingest['patients']:
        for e in p['events']:
            segment = by_key[e['evidence']['source_key']]
            checked += 1
            start, end = msi.micros(segment['start']['raw_value']), msi.micros(segment['end']['raw_value'])
            recorded = segment['recorded_at']['raw_value']
            ok = (e['start'] == [start, start] and e['end'] == [end, end]
                  and e['evidence']['recorded_at_us'] == (msi.micros(recorded) if recorded else None)
                  and e['evidence']['source_row_sha256'] == segment['source']['row_sha256'])
            mismatched += not ok
    return {'events_checked': checked, 'mismatches': mismatched}


# ---------------------------------------------------------------- the report

def verify(input_dir, seeds=SEEDS, workers=1, root=None):
    started = time.perf_counter()
    result = msi.convert(input_dir, DATASET, workers=workers)
    admission = demo.verify_summary(result['prepared']['admission']['summary'])
    timings = dict(result['timings_seconds'])
    with tempfile.TemporaryDirectory() as tmp:
        t = time.perf_counter()
        snapshot = es.build(result['ingest'], Path(root or tmp))
        timings['store_build'] = time.perf_counter() - t
        t = time.perf_counter()
        with es.open_store(snapshot) as store:
            timings['store_open_and_verify'] = time.perf_counter() - t
            manifest = store.manifest
            t = time.perf_counter()
            engine_input = adapter_engine_input(result['rows'], result['prepared']['items'])
            check = differential(store, result['ingest'], engine_input, seeds)
            timings['differential'] = time.perf_counter() - t
    times = raw_time_check(result['ingest'], result['prepared']['admission']['segments'])
    timings['total'] = time.perf_counter() - started
    ei.require(check['mismatches'] == 0 and check['adapter_row_mismatches'] == 0 and check['dropped_matches'] == 0,
               'DEMO_STORE_DIFFERENTIAL_MISMATCH')
    ei.require(times['mismatches'] == 0 and times['events_checked'] == result['counts']['events'],
               'DEMO_RAW_TIME_MISMATCH')
    return {
        'profile': 'execution-store-mimic-demo-verification-1.0',
        'scope': ('Public-demo inputevents through the SULO adapter into the execution store. Concepts are source item '
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
        'artifacts': msi.artifacts(FILES),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--input-dir', type=Path, required=True,
                        help='directory holding the pinned inputevents, icustays and d_items csv.gz files')
    parser.add_argument('--output', type=Path, default=REPORT)
    parser.add_argument('--seeds', type=int, default=SEEDS)
    parser.add_argument('--workers', type=int, default=1)
    parser.add_argument('--store-root', type=Path, help='keep the snapshot here (default: a temporary directory)')
    args = parser.parse_args(argv)
    try:
        inputs = {p.resolve() for p in importer.source_paths(args.input_dir).values()}
        ei.require(args.output.resolve() not in inputs, 'OUTPUT_OVERWRITES_INPUT')
        report = verify(args.input_dir, args.seeds, args.workers, args.store_root)
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
