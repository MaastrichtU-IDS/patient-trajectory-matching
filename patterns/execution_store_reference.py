"""Pure-Python reference for the execution store: the same logical tables, and an exhaustive
matcher over every patient.

It shares no code with `patterns/execution_store.py`. The tables are built from the ingest
document with dictionaries and loops, not SQL; the input digest, the per-bucket and per-table
content digests, the bucket and the coverage rule are written again from the specification in
docs/execution-store.md. It reads only the ingest and calls the trajectory-graph engine, never
the store. It holds everything in memory, which a reference may do.

`patterns/test_execution_store.py` compares every table row for row with the store's, the
input digest and every table and bucket digest with the manifest's, and the store's match
results with `exhaustive_match`.
"""
from __future__ import annotations

from decimal import Decimal
import hashlib
import json

from . import trajectory_graph as tg

BUCKET_COUNT = 8
# The record fields an event may carry (D26), each its own column, null where the event does not say.
RECORD = ['status', 'selection_state', 'recorded_at', 'available_at', 'claim_id', 'claim_sha256', 'source_table',
          'record_sha256', 'file_sha256']
COLUMNS = {
    'patients': ['patient_idx', 'patient_id', 'bucket', 'shared_variables'],
    'events': ['patient_idx', 'event_id', 'kind', 'source', 'clock_id', 'start_lo', 'start_hi', 'end_lo', 'end_hi',
               'ongoing', 'start_var', 'end_var', 'proper', 'status', 'selection_state', 'recorded_at', 'available_at',
               'claim_id', 'claim_sha256', 'source_table', 'record_sha256', 'file_sha256'],
    'event_evidence': ['patient_idx', 'event_id', 'evidence'],
    'event_concepts': ['patient_idx', 'event_id', 'concept', 'basis'],
    'concept_closure': ['concept', 'ancestor', 'basis', 'version', 'concept_basis', 'ancestor_basis'],
    'concept_postings': ['concept', 'patient_idx', 'n', 'min_start_lo', 'max_start_hi', 'max_end_hi'],
    'coverage': ['patient_idx', 'source', 'clock_id', 'lo', 'hi', 'basis'],
    'source_concepts': ['source', 'concept', 'basis'],
    'excluded_records': ['patient_idx', 'source', 'concept', 'start_lo', 'start_hi', 'reason', 'n'],
    'series': ['patient_idx', 'event_id', 'item_class', 'start_lo', 'start_hi', 'value_lexical', 'value_decimal',
               'value_micro', 'unit'],
}


def bucket_of(patient_id):
    """First 32 bits of the identifier's sha256, modulo the bucket count."""
    return int.from_bytes(hashlib.sha256(patient_id.encode('utf-8')).digest()[:4], 'big') % BUCKET_COUNT


def evidence_text(evidence):
    return json.dumps(evidence, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def expanded(concepts, ancestors):
    """A set of direct concepts with every ancestor added."""
    out = set()
    for c in concepts:
        out.add(c)
        out.update(ancestors[c])
    return out


def normalised(event):
    """The event as the store keeps it (D25): a field that only restates the engine's default is left out, that
    is `proper` when false, and a start or end variable named `<event_id>.s` or `<event_id>.f` respectively."""
    def restates_default(key, value):
        if key == 'proper':
            return value is False
        if key == 'start_var':
            return value == event['event_id'] + '.s'
        if key == 'end_var':
            return value == event['event_id'] + '.f'
        return False
    return {k: v for k, v in event.items() if not restates_default(k, v)}


def time_names(event):
    """The time variables an event names, as the engine profile defines them: its start, and its end
    unless it is a point; `<id>.s` and `<id>.f` where the event names none."""
    names = [event.get('start_var', event['event_id'] + '.s')]
    if 'end' in event:
        names.append(event.get('end_var', event['event_id'] + '.f'))
    return set(names)


def shares_variables(patient):
    """Whether some time variable is named by two of the patient's events."""
    count = {}
    for e in patient['events']:
        for name in time_names(e):
            count[name] = count.get(name, 0) + 1
    return any(n > 1 for n in count.values())


def build_tables(ingest):
    """{table: [row tuples]} in the store's column order."""
    ancestors = ingest['closure']['ancestors']
    version = ingest['closure']['version']
    ids = sorted(p['patient_id'] for p in ingest['patients'])
    index = {pid: i for i, pid in enumerate(ids)}
    tables = {name: [] for name in COLUMNS}
    by_id = {p['patient_id']: {**p, 'events': [normalised(e) for e in p['events']]} for p in ingest['patients']}
    for pid in ids:
        tables['patients'].append((index[pid], pid, bucket_of(pid), shares_variables(by_id[pid])))
    kinds = ingest['closure'].get('bases') or {}
    kind = lambda c: kinds[c] if c in kinds else 'declared'
    for concept in ancestors:
        tables['concept_closure'].append((concept, concept, 'self', version, kind(concept), kind(concept)))
        for a in ancestors[concept]:
            tables['concept_closure'].append((concept, a, 'declared', version, kind(concept), kind(a)))
    recorded = set()
    for p in by_id.values():
        i, clock = index[p['patient_id']], p['clock_id']
        postings = {}
        for e in p['events']:
            lo, hi = e['start']
            if 'end' in e:
                end_lo, end_hi = e['end']
            else:
                end_lo = end_hi = None
            ongoing = end_lo is not None and end_hi is None
            tables['events'].append((i, e['event_id'], e['kind'], e['source'], clock, lo, hi, end_lo, end_hi, ongoing,
                                     e.get('start_var'), e.get('end_var'), bool(e.get('proper', False)),
                                     *(e.get(f) for f in RECORD)))
            tables['event_evidence'].append((i, e['event_id'], evidence_text(e['evidence'])))
            for c in e['concepts']:
                tables['event_concepts'].append((i, e['event_id'], c, 'direct'))
            # The latest the event can end: a point ends when it starts; an ongoing exposure has no bound (None).
            finish = None if ongoing else (hi if end_lo is None else end_hi)
            for c in expanded(e['concepts'], ancestors):
                n, first, last, latest = postings.get(c, (0, lo, hi, finish))
                postings[c] = (n + 1, min(first, lo), max(last, hi),
                               None if latest is None or finish is None else max(latest, finish))
                recorded.add((e['source'], c))
        for c, (n, first, last, latest) in postings.items():
            tables['concept_postings'].append((c, i, n, first, last, latest))
        for period in p['coverage']:
            tables['coverage'].append((i, period['source'], clock, period['lo'], period['hi'], period['basis']))
        excluded = {}
        for x in p.get('excluded', []):
            key = (x['source'], x['concept'], x['start'][0], x['start'][1], x['reason'])
            excluded[key] = excluded.get(key, 0) + 1
        for (source, concept, lo, hi, reason), n in excluded.items():
            tables['excluded_records'].append((i, source, concept, lo, hi, reason, n))
        # Measurement values (D27), each at its event's time.
        starts = {e['event_id']: e['start'] for e in p['events']}
        for r in p.get('series', []):
            lo, hi = starts[r['event_id']]
            tables['series'].append((i, r['event_id'], r['item_class'], lo, hi, r['value_lexical'], r['value_decimal'],
                                     r['value_micro'], r['unit']))
    tables['source_concepts'] = [(source, concept, 'inferred_from_records') for source, concept in recorded]
    return {name: sorted(rows, key=lambda r: json.dumps(list(r), ensure_ascii=False)) for name, rows in tables.items()}


# Which tables are split by bucket, and the columns that order each table's rows (unique in it).
SPLIT_BY_BUCKET = {'events', 'event_evidence', 'event_concepts', 'coverage', 'excluded_records', 'series'}
ORDER = {'patients': ['patient_idx'], 'events': ['patient_idx', 'event_id'], 'event_evidence': ['patient_idx', 'event_id'],
         'event_concepts': ['patient_idx', 'event_id', 'concept'], 'concept_closure': ['concept', 'ancestor'],
         'concept_postings': ['concept', 'patient_idx'], 'coverage': ['patient_idx', 'source', 'lo', 'hi', 'basis'],
         'source_concepts': ['source', 'concept'],
         'excluded_records': ['patient_idx', 'source', 'concept', 'start_lo', 'start_hi', 'reason'],
         'series': ['patient_idx', 'item_class', 'start_lo', 'start_hi', 'event_id']}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def compact(value, sort=False):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, sort_keys=sort)


def patient_digest(patient):
    """sha256 of one patient's order-free JSON: events by id with sorted concepts, coverage and
    excluded records sorted, every object's keys sorted."""
    p = dict(patient)
    p['events'] = sorted(({**normalised(e), 'concepts': sorted(e['concepts'])} for e in patient['events']),
                         key=lambda e: e['event_id'])
    p['coverage'] = sorted(patient['coverage'], key=lambda c: [c['source'], c['lo'], c['hi'], c['basis']])
    if 'excluded' in patient:
        p['excluded'] = sorted(patient['excluded'], key=lambda x: [x['source'], x['concept'], x['start'], x['reason']])
    if patient.get('series'):
        p['series'] = sorted(patient['series'], key=lambda r: r['event_id'])
    else:  # an empty series is no series (D28)
        p.pop('series', None)
    return sha(compact(p, sort=True).encode('utf-8'))


def input_digest(ingest):
    """The header (profile, dataset, closure with sorted ancestor lists, provenance) as sorted-key
    JSON and a newline, then one `patient_id TAB patient digest NEWLINE` per patient by id."""
    closure = {'version': ingest['closure']['version'],
               'ancestors': {c: sorted(a) for c, a in ingest['closure']['ancestors'].items()}}
    if 'bases' in ingest['closure']:
        closure['bases'] = ingest['closure']['bases']
    head = {'profile': ingest['profile'], 'dataset_id': ingest['dataset_id'], 'closure': closure,
            'provenance': ingest.get('provenance', {})}
    lines = sorted((p['patient_id'], patient_digest(p)) for p in ingest['patients'])
    text = compact(head, sort=True) + '\n' + ''.join(pid + '\t' + d + '\n' for pid, d in lines)
    return sha(text.encode('utf-8'))


def bucket_digest(rows, table):
    """Rows ordered by the table's ordering columns, one compact JSON array per line."""
    at = [COLUMNS[table].index(c) for c in ORDER[table]]
    key = lambda row: tuple(row[i] for i in at)
    ordered = sorted(rows, key=key)
    if len({key(r) for r in ordered}) != len(ordered):
        raise ValueError(f'{table}: ordering columns are not unique')
    return sha(''.join(compact(list(r)) + '\n' for r in ordered).encode('utf-8'))


def table_digests(tables):
    """{table: {rows, content_digest, buckets: {label: {rows, content_digest}}}} from `build_tables`.

    Patient-keyed tables have the eight buckets "0" to "7" (empty ones included), each holding the
    rows of the patients in that bucket; the others have one bucket, "all". The table digest is the
    sha256 of one `label TAB bucket digest NEWLINE` per bucket, in that order."""
    bucket_by_idx = {row[0]: row[2] for row in tables["patients"]}
    out = {}
    for name, rows in tables.items():
        if name in SPLIT_BY_BUCKET:
            groups = {str(b): [] for b in range(BUCKET_COUNT)}
            for r in rows:
                groups[str(bucket_by_idx[r[0]])].append(r)
        else:
            groups = {'all': list(rows)}
        buckets = {label: {'rows': len(g), 'content_digest': bucket_digest(g, name)} for label, g in groups.items()}
        text = ''.join(f"{label}\t{b['content_digest']}\n" for label, b in buckets.items())
        out[name] = {'rows': len(rows), 'content_digest': sha(text.encode('utf-8')), 'buckets': buckets}
    return out


def step_concepts(query):
    """Each step's concept and the widenings in any relaxation set the engine would try."""
    concepts = {s['id']: {s['concept']} for s in query['steps']}
    for _, chosen in tg.relaxation_sets(query):
        for x in chosen:
            if x['kind'] == 'widen_concept':
                concepts[x['step']].add(x['concept'])
    return concepts


def start_possible(scope, window, lo, hi):
    """Could a step start in [lo, hi] under the query's scope? Written again from the specification."""
    return {'within': lambda: hi >= window['start'] and lo <= window['end'],
            'from_start': lambda: hi >= window['start'],
            'after': lambda: hi >= window['end']}.get(scope, lambda: True)()


def admission_check(query, per_step, result, patient, events, ancestors, window):
    """The excluded-records rule, written again from docs/execution-store.md ("Excluded records").

    A step carries an excluded record through its concept's closure, and reaches it if the scope also
    admits its start. Then, in order: a first or last step whose target a carried record could
    displace makes the result not comparable; if some step reaches a record, a `none` is not
    comparable, a `relaxed` is unresolved with the match beside it and a `possible` is incomplete.
    """
    scope = query.get('scope', 'any')
    reach, carrying = {}, {}
    for step in query['steps']:
        mine = [tuple(x['start']) for x in patient.get('excluded', [])
                if expanded([x['concept']], ancestors) & per_step[step['id']]]
        if mine:
            # Any of them can be the true first or last such record; the scope is applied after that choice.
            carrying[step['id']] = mine
        hits = [(lo, hi) for lo, hi in mine if start_possible(scope, window, lo, hi)]
        if hits:
            reach[step['id']] = hits
    if not carrying:
        return result
    status, pid = result['status'], result['patient_id']
    named = [step['id'] for step in query['steps'] if step['id'] in reach]
    begins = {e['id']: e['start'] for e in events}
    bound = result.get('binding')
    if bound is None and 'relaxed_match' in result:
        bound = result['relaxed_match']['binding']
    if bound is None and 'unverified_match' in result:  # D30: a match beside an order-uncertain status
        bound = result['unverified_match']['binding']
    if bound is not None:
        displaced = []
        for step in query['steps']:
            sid, how = step['id'], step.get('occurrence', 'any')
            if sid not in carrying:
                continue
            lo, hi = begins[bound[sid]]
            if how == 'first' and min(x for x, _ in carrying[sid]) <= hi:
                displaced.append(sid)
            elif how == 'last' and max(y for _, y in carrying[sid]) >= lo:
                displaced.append(sid)
        if displaced:
            return {'patient_id': pid, 'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                    'excluded_steps': displaced}
    if not reach:
        return result
    if status == 'none':
        return {'patient_id': pid, 'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                'excluded_steps': named}
    if status == 'relaxed':
        beside = {k: result[k] for k in ('relaxations', 'cost', 'certainty', 'binding', 'method')}
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                'relaxed_match': beside, 'excluded_steps': named, 'complete': False}
    if status == 'possible':
        out = dict(result)
        out.update(complete=False, excluded_steps=named,
                   note=(result['note'] + '; ' if 'note' in result else '') + 'EXCLUDED_RECORDS_MAY_MAKE_EXACT')
        return out
    return result


def coverage_rule(query, per_step, recorders, coverage, window, result):
    """The coverage rule on an engine `none`, written again from docs/execution-store.md: a step none of whose
    concepts any source records, or whose recording sources do not cover the window, makes it not comparable.
    The one copy, used by `exhaustive_match` and `prepared_reference`."""
    uncovered, unrecorded = [], []
    for step in (s['id'] for s in query['steps']):
        able = set()
        for c in per_step[step]:
            able |= recorders.get(c, set())
        if not able:
            unrecorded.append(step)
        covered = any(period['source'] in able and period['lo'] <= window['end'] and period['hi'] >= window['start']
                      for period in coverage)
        if not covered:
            uncovered.append(step)
    if not uncovered:
        return result
    out = {'patient_id': result['patient_id'], 'status': 'not_comparable',
           'reason': 'NO_SOURCE_RECORDS_CONCEPT' if unrecorded else 'NO_SOURCE_COVERAGE', 'uncovered_steps': uncovered}
    if unrecorded:
        out['unrecorded_steps'] = unrecorded
    return out


# Value conditions (D28), written again from docs/execution-store.md ("Value conditions"), sharing no code with the
# store: the store compiles conditions to tags it names by digest and tests micro-unit integers in SQL; this reads
# each series row's spelling as a Decimal and tests it in Python, and names its tags by step and relaxation.

def _bound(text):
    return Decimal(text)


def _admits(condition, unit, lexical):
    """Whether a value spelled `lexical`, in `unit`, satisfies a request condition: the unit equal, letter for letter,
    and the value compared exactly (Decimal comparisons do not round)."""
    if unit != condition['unit']:
        return False
    v = Decimal(lexical)
    if 'eq' in condition:
        return v == _bound(condition['eq'])
    checks = {'gt': lambda b: v > b, 'ge': lambda b: v >= b, 'lt': lambda b: v < b, 'le': lambda b: v <= b}
    return all(checks[k](_bound(condition[k])) for k in checks if k in condition)


class ValueQuery:
    """A value request read independently: the engine query with a tag per conditioned step and per relaxation of
    one, which events carry which tag, the concepts each step stands for, and which sources record each tag."""

    def __init__(self, ingest, query, conditions):
        ancestors = ingest['closure']['ancestors']
        own = conditions['steps']
        self.defs = {}  # tag -> (concept, condition)
        concept_of = {s['id']: s['concept'] for s in query['steps']}
        steps = []
        for s in query['steps']:
            if s['id'] in own:
                name = f"#ref-value/{s['id']}/own"
                self.defs[name] = (s['concept'], own[s['id']])
                s = dict(s, concept=name)
            steps.append(s)
        relaxations = []
        for x in query.get('relaxations', []):
            if x['kind'] == 'widen_concept' and x['step'] in own:
                name = f"#ref-value/{x['step']}/{x['id']}"
                self.defs[name] = (x['concept'], own[x['step']])
                x = dict(x, concept=name)
            relaxations.append(x)
        for x in conditions.get('relaxations', []):
            name = f"#ref-value/{x['step']}/{x['id']}"
            self.defs[name] = (concept_of[x['step']], x['condition'])
            relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'concept': name,
                                'cost': x['cost']})
        self.query = dict(query, steps=steps)
        if relaxations or 'relaxations' in query:
            self.query['relaxations'] = relaxations
        # Which (patient, event) carries each tag, and which sources record it.
        self.carried, self.recorders = {}, {name: set() for name in self.defs}
        for p in ingest['patients']:
            source = {e['event_id']: e['source'] for e in p['events']}
            for r in p.get('series', []):
                covers = expanded([r['item_class']], ancestors)
                for name, (concept, condition) in self.defs.items():
                    if concept not in covers:
                        continue
                    if r['unit'] == condition['unit']:
                        self.recorders[name].add(source[r['event_id']])
                    if _admits(condition, r['unit'], r['value_lexical']):
                        self.carried.setdefault(p['patient_id'], {}).setdefault(r['event_id'], set()).add(name)
        # Each step's engine concepts, read as concepts: what its excluded records are matched by.
        self.per_step = step_concepts(self.query)
        self.base = {sid: {self.defs[c][0] if c in self.defs else c for c in cs} for sid, cs in self.per_step.items()}

    def tags_of(self, patient_id, event_id):
        return self.carried.get(patient_id, {}).get(event_id, set())


def exhaustive_match(ingest, query, windows, conditions=None):
    """The engine on every patient's full, closure-expanded events; then the coverage rule on `none`,
    then the excluded-records rule on whatever the engine decided. With value `conditions` (D28) each event also
    lists the tags it carries (`ValueQuery`), the engine gets the tagged query, a tag's recorders are the sources
    holding its concept in its unit, and excluded records are matched by the concepts the tags stand for."""
    ancestors = ingest['closure']['ancestors']
    recorders = {}
    for p in ingest['patients']:
        for e in p['events']:
            for c in expanded(e['concepts'], ancestors):
                recorders.setdefault(c, set()).add(e['source'])
    value = None if conditions is None else ValueQuery(ingest, query, conditions)
    if value is not None:
        query = value.query
        recorders.update(value.recorders)
    per_step = step_concepts(query)
    base = per_step if value is None else value.base
    results = []
    for p in sorted(ingest['patients'], key=lambda p: p['patient_id']):
        window = windows.get(p['patient_id'])
        events = []
        for e in p['events']:
            concepts = expanded(e['concepts'], ancestors)
            if value is not None:
                concepts = concepts | value.tags_of(p['patient_id'], e['event_id'])
            event = {'id': e['event_id'], 'concepts': sorted(concepts), 'start': list(e['start'])}
            if 'end' in e:
                event['end'] = list(e['end'])
            # Every event of the patient, with the time variables and `proper` the ingest gave it.
            event.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
            events.append(event)
        result = tg.match(query, {'patient_id': p['patient_id'], 'window': window, 'events': events})
        if result['status'] == 'none':
            result = coverage_rule(query, per_step, recorders, p['coverage'], window, result)
        if window is not None and result['status'] != 'not_comparable':
            result = admission_check(query, base, result, p, events, ancestors, window)
        results.append(result)
    return results
