"""The synthetic cohort in an execution store, and trajectory queries matched there: the store side of
`test_store_parity.cjs`.

    python demos/mockups/store_parity.py build COHORT.json ROOT     # prints the snapshot directory
    python demos/mockups/store_parity.py match SNAPSHOT < cases.json  # per-patient results, as JSON

`build` converts the cohort with `patterns/synthetic_ingest.py` emitting `series` (D32's option): each measured
record with a number and a unit gets a series row (D27), the value spelled as the cohort's JSON writes it, and a measured
record without a unit becomes an excluded record. Before D32 this module added the rows itself (`with_series`, kept to
show the converter's rows are the same for every record with a unit).

`match` reads `{"cases": [{"name", "query", "conditions", "windows"}]}`, times in whole minutes from each patient's
birth (the trajectory page's engine grid), and answers `{name: {patient_id: {status, certainty?, relaxations?,
reason?}}}`. A minute from birth is `micros(birth_date) + minutes * 60,000,000` on the store's clock, exactly,
because the synthetic converter counts each record's patient-local wall-clock time in microseconds from
0001-01-01T00:00 and the page counts the same wall-clock time in days from the birth date's midnight.

A case may also name `candidates`, {key: {concept, condition?, relative?}} for each step and relaxed alternative. The
answer then adds `<name>#candidates`, each patient's events per key (for explaining a difference from the page), and
`<name>#replayed`, the engine's status on exactly those events (`replay`), which the test requires to equal the
store's own result up to its comparability rule. For a key with a relative condition (D33) the events are the readings
carrying the store's relative tag, each with its strength ('certain' or 'possible'), and `<name>#inexact` lists the
patients for whom some relative tag was decided inexactly.
"""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from patterns import execution_store as es  # noqa: E402
from patterns import series_values  # noqa: E402
from patterns import synthetic_ingest as si  # noqa: E402

MINUTE = 60_000_000
MEASURED = ('measurement', 'diary_summary')


def spelling(value):
    """The record's value as the cohort's JSON spells it, or None if it is not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return repr(value)


def with_series(patient, records):
    """The converted patient with a series row for each measured point event whose record has a number and a unit."""
    rows = []
    for event in patient['events']:
        if event['kind'] not in MEASURED or 'end' in event:
            continue
        record = records[event['evidence']['record_id']]
        text, unit = spelling(record.get('value')), record.get('unit')
        if text is None or not unit:
            continue
        decimal, micro = series_values.columns(text)
        rows.append({'event_id': event['event_id'], 'item_class': event['concepts'][0], 'value_lexical': text,
                     'value_decimal': decimal, 'value_micro': micro, 'unit': unit})
    return {**patient, 'series': rows} if rows else patient


def build(cohort, root):
    """The cohort built by the converter itself emitting series (D32: `synthetic_ingest`'s option `series`)."""
    skipped = {}
    with es.StoreBuilder(root, si.synthetic_header(cohort)) as builder:
        for patient in si.iter_patients(cohort, skipped, series=True):
            builder.add(patient)
        return builder.finish(si.synthetic_provenance(cohort, skipped, series=True))


def birth_micros(cohort):
    return {p['patient_id']: si.micros(p['birth_date'] + 'T00:00') for p in cohort['patients']}


def to_micros(query):
    """The page's query, minutes to microseconds: relation bounds and the relaxations' limits."""
    q = json.loads(json.dumps(query))
    for r in q['relations']:
        for k in ('min', 'max', 'grace'):
            if k in r:
                r[k] *= MINUTE
    for x in q.get('relaxations', []):
        for k in ('max', 'grace'):
            if k in x:
                x[k] *= MINUTE
    return q


def conditions_micros(conditions):
    """The page's conditions, minutes to microseconds: a count's span (D32)."""
    if conditions is None:
        return None
    c = json.loads(json.dumps(conditions))
    for raw in c['steps'].values():
        within = raw.get('count', {}).get('within')
        if isinstance(within, dict):
            within['span'] *= MINUTE
        if 'relative' in raw:  # D33: the baseline's window
            raw['relative']['baseline']['within'] *= MINUTE
    return c


def match(snapshot, cases, cohort):
    births = birth_micros(cohort)
    out = {}
    with es.open_store(snapshot) as store:
        for case in cases:
            windows = {pid: None if w is None else {'start': births[pid] + w['start'] * MINUTE,
                                                    'end': births[pid] + w['end'] * MINUTE}
                       for pid, w in case['windows'].items()}
            result = es.match(store, to_micros(case['query']), windows, conditions=conditions_micros(case.get('conditions')))
            mine = out[case['name']] = {}
            # The patients with a candidate and an index window (D33's usefulness bar is a share of these).
            found = es.candidates(store, to_micros(case['query']), conditions_micros(case.get('conditions')))
            ids = dict(store.query('SELECT patient_idx, patient_id FROM patients'))
            out[case['name'] + '#with_candidate'] = sorted(ids[i] for i in found['patient_idx'] if windows.get(ids[i]))
            for r in result['results']:
                keep = {k: r[k] for k in ('status', 'certainty', 'relaxations', 'reason') if k in r}
                if r['status'] == 'unresolved' and 'relaxed_match' in r:
                    keep['relaxations'] = r['relaxed_match']['relaxations']
                mine[r['patient_id']] = keep
            if case.get('candidates'):
                found = out[case['name'] + '#candidates'] = candidates(store, case['candidates'], births)
                inexact = relative_candidates(store, case['candidates'], births, found)
                out[case['name'] + '#inexact'] = sorted(inexact)
                out[case['name'] + '#replayed'] = replay(case, found, inexact)
    return out


def relative_candidates(store, wanted, births, found):
    """D33: for each key with a relative condition, the readings carrying the store's relative tag of its concept, each
    with its strength, put into `found` in place of every event of the concept; the patients for whom a tag was decided
    inexactly. The tag is the store's own (`execution_store._derived_chunk`, a one-step request), which reads no window."""
    from patterns import trajectory_graph as tg
    pids = dict(store.query('SELECT patient_idx, patient_id FROM patients'))
    idxs = sorted(pids)
    linked = [i for i, in store.query('SELECT patient_idx FROM patients WHERE shared_variables')]
    starts = {(pid, eid): (slo, shi, kind) for pid, eid, slo, shi, kind in store.query(
        'SELECT p.patient_id, e.event_id, e.start_lo, e.start_hi, e.kind FROM events e JOIN patients p USING (patient_idx)')}
    inexact = set()
    for key, spec in wanted.items():
        if 'relative' not in spec:
            continue
        relative = json.loads(json.dumps(spec['relative']))
        relative['baseline']['within'] *= MINUTE
        query = {'profile': tg.PROFILE_ID, 'steps': [{'id': 'A', 'concept': spec['concept']}], 'relations': []}
        request = es.Request(query, {'profile': es.CONDITIONS_PROFILE_1_2, 'steps': {'A': {'relative': relative}}})
        for pid in found:
            found[pid].pop(key, None)
        windows = [{'start': 0, 'end': 0}] * len(idxs)
        for idx, d in es._derived_chunk(store, request, idxs, [pids[i] for i in idxs], windows, linked):
            pid = pids[idx]
            certain = {e['id'] for e in d.first['events'] if 'derived-step:A/own' in e['concepts']}
            possible = {e['id'] for e in (d.second or d.first)['events'] if 'derived-step:A/own' in e['concepts']}
            if d.inexact:
                inexact.add(pid)
            minutes = lambda t: (t - births[pid]) / MINUTE
            for eid in sorted(possible):
                slo, shi, kind = starts[pid, eid]
                found.setdefault(pid, {}).setdefault(key, []).append(
                    [minutes(slo), minutes(shi), None, None, eid, kind, 'certain' if eid in certain else 'possible'])
    return inexact


def replay(case, found, inexact=()):
    """The engine alone on the candidate events `candidates` lists, each listing the keys it is a candidate for, with the
    query's steps and relaxations renamed to those keys: {patient: status}. If this equals the store's result, up to
    the store's comparability rule, then the candidate lists are exactly what the store's engine saw, and a
    difference from the page is a difference of candidates (or that rule), never of the query or the windows.

    A relative condition (D33) is replayed from the tagged readings listed, by their strengths: the store's two-pass
    rule (`execution_store.match_derived`) over a pass of the certain readings and a pass of every listed one."""
    from patterns import trajectory_graph as tg
    steps = (conditions_micros(case.get('conditions')) or {}).get('steps') or {}
    if any('relative' in raw for raw in steps.values()):
        return relative_replay(case, found, inexact)
    q = to_micros(case['query'])
    q['steps'] = [{**s, 'concept': 'step:' + s['id']} for s in q['steps']]
    q['relaxations'] = [{**x, 'concept': x['id']} if x['kind'] == 'widen_concept' else x for x in q['relaxations']]
    for x in (case.get('conditions') or {}).get('relaxations', []):
        if x['kind'] == 'widen_value':
            q['relaxations'].append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                     'concept': x['id']})
    # D32: a derived step is derived again from the listed events, by the store's own rules, over the keys: its own
    # key for the step and for a `widen_count` that keeps the concept, the relaxation's key for one naming another.
    derived = {sid: {k: v for k, v in raw.items() if k in ('occurrence', 'count')}
               for sid, raw in ((conditions_micros(case.get('conditions')) or {}).get('steps') or {}).items()}
    derived = {sid: raw for sid, raw in derived.items() if raw}
    request = None
    if derived:
        relax = [dict(x, concept=x['id']) if 'concept' in x else x
                 for x in (case.get('conditions') or {}).get('relaxations', []) if x['kind'] == 'widen_count']
        request = es.Request(q, {'profile': es.CONDITIONS_PROFILE_1_1, 'steps': derived, 'relaxations': relax})
        pq = tg.PreparedQuery(request.query)
    out = {}
    for pid, w in case['windows'].items():  # minutes from birth, as the candidates are
        events, mentions = {}, set()
        for key, rows in found.get(pid, {}).items():
            for slo, shi, elo, ehi, eid, kind in rows:
                e = events.setdefault(eid, {'id': eid, 'concepts': [], 'start': [round(slo * MINUTE), round(shi * MINUTE)]})
                if elo is not None:
                    e['end'] = [round(elo * MINUTE), None if ehi is None else round(ehi * MINUTE)]
                e['concepts'].append(key)
                if kind == es.MENTION_KIND:
                    mentions.add(eid)
        window = None if w is None else {'start': w['start'] * MINUTE, 'end': w['end'] * MINUTE}
        patient = {'patient_id': pid, 'window': window, 'events': list(events.values())}
        if request is not None and window is not None:
            d = es.derive_patient(request, pid, window, patient['events'], mentions)
            out[pid] = es.match_derived(pq, request, d)['status']
        else:
            out[pid] = tg.match(q, patient)['status']
    return out


def satisfies(condition, row):
    """Whether a series row meets a condition: the unit letter for letter, the value compared as an exact Decimal."""
    if row['unit'] != condition['unit']:
        return False
    v = Decimal(row['value_lexical'])
    tests = {'ge': lambda t: v >= t, 'gt': lambda t: v > t, 'le': lambda t: v <= t, 'lt': lambda t: v < t,
             'eq': lambda t: v == t}
    return all(tests[op](Decimal(t)) for op, t in condition.items() if op != 'unit')


def candidates(store, wanted, births):
    """For explaining differences: {patient: {key: [[start lo, start hi, end lo, end hi, event id, kind], ...]}}, in
    minutes from birth, of the events carrying each key's concept (through the closure) and meeting its condition."""
    rows = store.query('''SELECT p.patient_id, e.event_id, e.kind, e.start_lo, e.start_hi, e.end_lo, e.end_hi,
                                 list(DISTINCT cc.ancestor)
                          FROM events e JOIN patients p USING (patient_idx)
                          JOIN event_concepts ec USING (patient_idx, event_id)
                          JOIN concept_closure cc ON cc.concept = ec.concept GROUP BY ALL''')
    series = {(pid, eid): {'unit': unit, 'value_lexical': text} for pid, eid, unit, text in store.query(
        'SELECT p.patient_id, s.event_id, s.unit, s.value_lexical FROM series s JOIN patients p USING (patient_idx)')}
    out = {}
    for pid, eid, kind, slo, shi, elo, ehi, ancestors in rows:
        minutes = lambda t: None if t is None else (t - births[pid]) / MINUTE
        for key, spec in wanted.items():
            if spec['concept'] not in ancestors:
                continue
            if spec.get('condition') and not ((pid, eid) in series and satisfies(spec['condition'], series[pid, eid])):
                continue
            out.setdefault(pid, {}).setdefault(key, []).append(
                [minutes(slo), minutes(shi), minutes(elo), minutes(ehi), eid, kind])
    return out


def main(argv):
    if argv[:1] == ['build'] and len(argv) == 3:
        print(build(json.loads(Path(argv[1]).read_text()), Path(argv[2])))
        return 0
    if argv[:1] == ['match'] and len(argv) == 2:
        data = json.load(sys.stdin)
        cohort = json.loads(Path(data['cohort']).read_text())
        print(json.dumps(match(Path(argv[1]), data['cases'], cohort)))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


def relative_replay(case, found, inexact):
    """`replay` for a case with a relative condition (D33): each listed reading carries its key's relative tag in pass 1
    when certain and in pass 2 when certain or possible; the store's two-pass rule then decides."""
    from patterns import trajectory_graph as tg
    q = to_micros(case['query'])
    q['steps'] = [{**s, 'concept': 'step:' + s['id']} for s in q['steps']]
    q['relaxations'] = [{**x, 'concept': x['id']} if x['kind'] == 'widen_concept' else x for x in q['relaxations']]
    conditions = conditions_micros(case.get('conditions'))
    steps = {sid: {'relative': raw['relative']} for sid, raw in conditions['steps'].items() if 'relative' in raw}
    factors = [x for x in conditions.get('relaxations', []) if x['kind'] == 'widen_factor']
    for x in conditions.get('relaxations', []):
        if x['kind'] == 'widen_value':
            q['relaxations'].append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                     'concept': x['id']})
    request = es.Request(q, {'profile': es.CONDITIONS_PROFILE_1_2, 'steps': steps, 'relaxations': factors})
    pq = tg.PreparedQuery(request.query)
    tag_of = {'step:' + sid: f'{es._DERIVED_PREFIX}{sid}/own' for sid in steps}
    tag_of.update({x['id']: f"{es._DERIVED_PREFIX}{x['step']}/{x['id']}" for x in factors})
    out = {}
    for pid, w in case['windows'].items():
        events, certain, possible = {}, {}, {}
        for key, rows in found.get(pid, {}).items():
            for row in rows:
                slo, shi, elo, ehi, eid, kind = row[:6]
                e = events.setdefault(eid, {'id': eid, 'concepts': [], 'start': [round(slo * MINUTE), round(shi * MINUTE)]})
                if elo is not None:
                    e['end'] = [round(elo * MINUTE), None if ehi is None else round(ehi * MINUTE)]
                if key in tag_of:
                    possible.setdefault(eid, set()).add(tag_of[key])
                    if row[6] == 'certain':
                        certain.setdefault(eid, set()).add(tag_of[key])
                else:
                    e['concepts'].append(key)
        if w is None:
            out[pid] = 'excluded'
            continue
        window = {'start': w['start'] * MINUTE, 'end': w['end'] * MINUTE}

        def doc(tags):
            return {'patient_id': pid, 'window': window,
                    'events': [{**e, 'concepts': sorted(set(e['concepts']) | tags.get(e['id'], set()))}
                               for e in events.values() if e['concepts'] or tags.get(e['id'])]}
        d = es.Derived()
        d.first = doc(certain)
        tried = set(request.derived_used)
        second = any(name in tried for eid, names in possible.items() for name in names - certain.get(eid, set()))
        d.second = doc(possible) if second else None
        d.families = {name: {eid for eid, names in possible.items() if name in names} for name in request.derived}
        d.linked, d.points, d.inexact = set(), {}, pid in inexact
        d.concepts = {e['id']: frozenset(e['concepts']) for e in d.first['events']}
        out[pid] = es.match_derived(pq, request, d)['status']
    return out


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
