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
import math
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
    if has_derived(conditions):
        return derived_exhaustive_match(ingest, query, windows, conditions)
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


# Derived steps (D32), written again from docs/execution-store.md ("Derived steps") and the decision log, sharing no code
# with the store's derivation: the request is read here, envelopes are order statistics computed by sorting, count
# strengths are decided by evaluating each pair's possible positions at every point where they can change, each
# relaxation set is decided by the engine on the query with that set applied and no relaxations, and the two-pass rule,
# coupling and the excluded-records rule are applied from their written statements.

DERIVED = 'derived-step:'
LATER = 'execution-store-conditions-1.1'
VALUE_KEYS = ('unit', 'eq', 'gt', 'ge', 'lt', 'le')


class DerivedQuery:
    """A 1.1 request read independently: value conditions through `ValueQuery`, and per step an Nth occurrence or a
    count, each named `derived-step:<step>/<own or relaxation id>`."""

    def __init__(self, ingest, query, conditions):
        own = {sid: {k: v for k, v in raw.items() if k in VALUE_KEYS} for sid, raw in conditions['steps'].items()}
        own = {sid: c for sid, c in own.items() if c}
        self.spec = {}  # step -> ('nth', which, n) or ('count', n, within)
        for sid, raw in conditions['steps'].items():
            if 'occurrence' in raw:
                (which, n), = raw['occurrence'].items()
                self.spec[sid] = ('nth', which, n)
            elif 'count' in raw:
                within = raw['count']['within']
                self.spec[sid] = ('count', raw['count']['at_least'], within if within == 'window' else within['span'])
        counts = [x for x in conditions.get('relaxations', []) if x['kind'] == 'widen_count']
        values = [x for x in conditions.get('relaxations', []) if x['kind'] == 'widen_value']
        # Value tags: a widen_count naming a wider concept on a conditioned step counts that concept under the step's
        # condition, which ValueQuery tags as it tags a widen_concept of the step.
        extra = [{'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'concept': x['concept'], 'cost': x['cost']}
                 for x in counts if 'concept' in x and x['step'] in own]
        probe = dict(query, relaxations=list(query.get('relaxations', [])) + extra)
        self.value = ValueQuery(ingest, probe, {'profile': 'execution-store-conditions-1.0', 'steps': own,
                                                'relaxations': values}) if own else None
        valued = self.value.query if self.value else dict(query)
        concept_of = {s['id']: s['concept'] for s in valued['steps']}
        widened = {x['id']: x['concept'] for x in valued.get('relaxations', []) if x['kind'] == 'widen_concept'}
        self.counted = {}  # derived name -> (kind, concept or value tag, which or within, n)
        steps = []
        for s in valued['steps']:
            if s['id'] in self.spec:
                s = dict(s, concept=self.name(s['id'], 'own', concept_of[s['id']]))
            steps.append(s)
        relaxations = []
        extra_ids = {x['id'] for x in extra}
        for x in valued.get('relaxations', []):
            if x['id'] in extra_ids:
                continue
            if x['kind'] == 'widen_concept' and x['step'] in self.spec:
                x = dict(x, concept=self.name(x['step'], x['id'], x['concept']))
            relaxations.append(x)
        for x in counts:
            base = widened[x['id']] if x['id'] in extra_ids else x.get('concept', concept_of[x['step']])
            relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                'concept': self.name(x['step'], x['id'], base, x['at_least'])})
        self.query = dict(valued, steps=steps)
        if relaxations or 'relaxations' in valued:
            self.query['relaxations'] = relaxations
        self.per_step = step_concepts(self.query)
        # What each derived tag stands for, for loading, coverage and the excluded-records rule.
        self.loads = {sid: {self.counted[c][1] if c in self.counted else c for c in cs} for sid, cs in self.per_step.items()}
        vdefs = self.value.defs if self.value else {}
        self.base = {sid: {vdefs[c][0] if c in vdefs else c for c in cs} for sid, cs in self.loads.items()}
        self.tried = {c for cs in self.per_step.values() for c in cs if c in self.counted}

    def name(self, sid, variant, concept, n=None):
        spec = self.spec[sid]
        name = f'{DERIVED}{sid}/{variant}'
        if spec[0] == 'nth':
            self.counted[name] = ('nth', concept, spec[1], spec[2])
        else:
            self.counted[name] = ('count', concept, spec[2], spec[1] if n is None else n)
        return name


def _order_statistic(values, n, from_last):
    ordered = sorted(values)
    return ordered[len(ordered) - n] if from_last else ordered[n - 1]


def _named_twice(events):
    """The variables two events name (an event's own start and end are one name each)."""
    seen = {}
    for e in events:
        names = {e.get('start_var', e['id'] + '.s')}
        if 'end' in e:
            names.add(e.get('end_var', e['id'] + '.f'))
        for v in names:
            seen[v] = seen.get(v, 0) + 1
    return {v for v, k in seen.items() if k > 1}


def _variables(e):
    names = {e.get('start_var', e['id'] + '.s')}
    if 'end' in e:
        names.add(e.get('end_var', e['id'] + '.f'))
    return names


def _strengths(times, family, n, within, window):
    """{event id: 'certain' | 'possible'} and whether it is exact, by evaluating, for each event e and each time tau it
    can take, which other events can (some choice) or must (every choice) lie in the count's interval, from the pair's
    two-variable projection of the patient's network; tau is tried at every point where some pair's answer changes."""
    if len(family) < n:
        return {}, True
    shared = _named_twice(times['events'])
    exact = not any(_variables(e) & shared for e in family)
    dom, gap = times['dom'], times['gap']
    out = {}
    if exact and all(dom[e['sv']][0] == dom[e['sv']][1] for e in family):
        # Every counted time is exact (as on MIMIC): the count at e is a plain count of the others in its interval.
        from bisect import bisect_left, bisect_right
        starts = sorted(dom[e['sv']][0] for e in family)
        for e in family:
            t0 = dom[e['sv']][0]
            if within == 'window':
                if not window['start'] <= t0 <= window['end']:
                    continue
                lo = window['start']
            else:
                lo = t0 - within
            if bisect_right(starts, t0) - bisect_left(starts, lo) - 1 >= n - 1:
                out[e['id']] = 'certain'
        return out, True
    for e in family:
        a, b = dom[e['sv']]
        others = [x for x in family if x is not e]
        pairs = [(dom[x['sv']], gap(e['sv'], x['sv']), gap(x['sv'], e['sv'])) for x in others]

        def window_of(tau):
            if within == 'window':
                return window['start'], tau
            return tau - within, tau

        def can(tau, pair):
            (ax, bx), up, down = pair  # x - e <= up, e - x <= down
            lo, hi = window_of(tau)
            return max(ax, tau - down, lo) <= min(bx, tau + up, hi)

        def must(tau, pair):
            (ax, bx), up, down = pair
            lo, hi = window_of(tau)
            return lo <= max(ax, tau - down) and min(bx, tau + up) <= hi
        marks = {a, b}
        offsets = [0] if within == 'window' else [0, within]
        base_w = [window['start']] if within == 'window' else []
        for (ax, bx), up, down in pairs:
            for v in (ax, bx):
                for o in offsets:
                    for g in (0, up, -up, down, -down):
                        if g not in (math.inf, -math.inf):
                            marks.update({v + o + g, v + o + g + 1, v - o - g, v - o - g + 1})
            for w0 in base_w:
                for g in (0, up, -up, down, -down):
                    if g not in (math.inf, -math.inf):
                        marks.update({w0 + g, w0 + g + 1, w0 - g, w0 - g + 1})
        taus = sorted(t for t in marks if a <= t <= b)
        need = n - 1
        if within == 'window':
            possible_taus = [t for t in taus if window['start'] <= t <= window['end']]
            certain = window['start'] <= a and b <= window['end'] and all(
                sum(must(t, p) for p in pairs) >= need for t in taus)
        else:
            possible_taus = taus
            certain = all(sum(must(t, p) for p in pairs) >= need for t in taus)
        possible = any(sum(can(t, p) for p in pairs) >= need for t in possible_taus)
        if certain:
            out[e['id']] = 'certain'
        elif possible:
            out[e['id']] = 'possible'
    return out, exact


def _times(patient):
    t = tg.patient_times(patient, trusted=True)
    return {'dom': t.dom, 'gap': t.gap, 'events': patient['events']}


def derive(dq, pid, window, events, mentions):
    """(pass-1 events, pass-2 events or None, points {id: range}, families {name: ids}, linked concepts, inexact)."""
    plain = events
    t = _times({'patient_id': pid, 'window': window, 'events': plain})
    shared = _named_twice(plain)
    points, families, linked = {}, {}, set()
    certain, possible = {}, {}
    second, inexact = False, False
    for name in sorted(dq.tried):
        kind, concept, detail, n = dq.counted[name]
        family = [dict(e, sv=e.get('start_var', e['id'] + '.s')) for e in plain
                  if concept in e['concepts'] and e['id'] not in mentions]
        families[name] = {e['id'] for e in family}
        if any(_variables(e) & shared for e in family):
            linked.add(concept)
        if kind == 'nth':
            if len(family) >= n:
                lows = [t['dom'][e['sv']][0] for e in family]
                highs = [t['dom'][e['sv']][1] for e in family]
                points[name] = [_order_statistic(lows, n, detail == 'nth_last'), _order_statistic(highs, n, detail == 'nth_last')]
            continue
        found, exact = _strengths(t, family, n, detail, window)
        inexact = inexact or (not exact and bool(found))
        for eid, strength in found.items():
            possible.setdefault(eid, set()).add(name)
            if strength == 'certain':
                certain.setdefault(eid, set()).add(name)
            else:
                second = True
    extra = [{'id': name, 'concepts': [name], 'start': list(r)} for name, r in points.items()]

    def tagged(carried):
        return [dict(e, concepts=sorted(set(e['concepts']) | carried[e['id']])) if e['id'] in carried else e
                for e in plain] + extra
    return tagged(certain), tagged(possible) if second else None, points, families, linked, inexact


def _set_decision(query, chosen, patient):
    """The engine's decision for one relaxation set: the query with the set applied and no relaxations."""
    concepts, relations = tg.applied(query, chosen)
    q = {k: v for k, v in query.items() if k not in ('relaxations', 'budget')}
    q['steps'] = [dict(s, concept=concepts[s['id']]) for s in query['steps']]
    q['relations'] = relations
    r = tg.match(q, patient)
    out = {'winner': None, 'binding': None, 'method': None, 'exact_complete': True, 'open': None, 'stopped': False,
           'uncertain': r.get('uncertain_steps'), 'witness': r.get('witness')}
    if r['status'] in ('exact', 'possible'):
        out.update(winner=r['status'], binding=r['binding'], method=r['method'],
                   exact_complete=r['complete'] if r['status'] == 'possible' else True)
    elif r['status'] == 'unresolved' and r['reason'] == 'SEARCH_BUDGET_EXHAUSTED':
        out['stopped'] = True
    elif r['status'] == 'unresolved':
        m = r.get('unverified_match')
        out.update(open=m['certainty'] if m else 'none', binding=m and m['binding'], method=m and m['method'])
    return out


def _coupled(dq, query, chosen, families, linked, events, points):
    concepts, _ = tg.applied(query, chosen)
    carried = {e['id']: set(e['concepts']) for e in events}
    out = []
    for s in [x['id'] for x in query['steps']]:
        name = concepts[s]
        if name not in dq.counted or dq.counted[name][0] != 'nth' or name not in families or name not in points:
            continue
        mine = families[name]
        hit = dq.counted[name][1] in linked
        for t in [x['id'] for x in query['steps']]:
            if hit or t == s:
                continue
            other = concepts[t]
            hit = bool(families[other] & mine) if other in dq.counted else any(other in carried[i] for i in mine)
        if hit:
            out.append(s)
    return out


def _one_pass(dq, query, patient, families, linked, points):
    """`match` for one pass, set by set (D32 reference)."""
    pid = patient['patient_id']
    decided = []
    for chosen, cost in [((), 0)] + [(chosen, cost) for cost, chosen in tg.relaxation_sets(query)]:
        d = _set_decision(query, chosen, patient)
        d['reason'] = 'OCCURRENCE_ORDER_UNCERTAIN'
        coupled = _coupled(dq, query, chosen, families, linked, patient['events'], points)
        if d['open'] is None and coupled and d['winner'] == 'possible':
            d.update(open='possible', reason='OCCURRENCE_TIME_COUPLED', uncertain=coupled)
        decided.append((chosen, cost, d))
    _, _, first = decided[0]
    if first['open'] is not None and first['open'] != 'none':
        return {'patient_id': pid, 'status': 'unresolved', 'reason': first['reason'], 'uncertain_steps': first['uncertain'],
                'unverified_match': {'certainty': first['open'], 'binding': first['binding'], 'method': first['method']},
                'complete': False}
    if first['winner'] == 'exact':
        return {'patient_id': pid, 'status': 'exact', 'binding': first['binding'], 'method': first['method'],
                'complete': True}
    if first['winner'] == 'possible':
        out = {'patient_id': pid, 'status': 'possible', 'binding': first['binding'], 'witness': first['witness'],
               'method': first['method'], 'complete': first['exact_complete']}
        if not first['exact_complete']:
            out['note'] = 'EXACT_SEARCH_STOPPED'
        return out
    open_first = first['open'] is not None

    def unresolved(**extra):
        if first['stopped']:
            return {'patient_id': pid, 'status': 'unresolved', 'reason': 'SEARCH_BUDGET_EXHAUSTED', **extra,
                    'complete': False}
        return {'patient_id': pid, 'status': 'unresolved', 'reason': first['reason'],
                'uncertain_steps': first['uncertain'], **extra, 'complete': False}
    skipped = order_skipped = coupled_skipped = False
    for chosen, cost, d in decided[1:]:
        if d['open'] is not None:
            if d['reason'] == 'OCCURRENCE_TIME_COUPLED':
                coupled_skipped = True
            else:
                order_skipped = True
            continue
        if d['winner'] is None:
            skipped = skipped or d['stopped']
            continue
        relaxed = {'relaxations': [x['id'] for x in chosen], 'cost': tg.decimal_text(cost), 'certainty': d['winner'],
                   'binding': d['binding'], 'method': d['method']}
        if first['stopped'] or open_first:
            return unresolved(relaxed_match=relaxed)
        out = {'patient_id': pid, 'status': 'relaxed', **relaxed,
               'complete': not (skipped or order_skipped or coupled_skipped)
               and (d['winner'] == 'exact' or d['exact_complete'])}
        note = ('CHEAPER_RELAXATION_SEARCH_STOPPED' if skipped else 'CHEAPER_RELAXATION_ORDER_UNCERTAIN' if order_skipped
                else 'CHEAPER_RELAXATION_OCCURRENCE_COUPLED' if coupled_skipped else None)
        return {**out, 'note': note} if note else out
    if first['stopped'] or open_first:
        return unresolved()
    out = {'patient_id': pid, 'status': 'none', 'complete': not (skipped or order_skipped or coupled_skipped)}
    note = ('RELAXATION_SEARCH_STOPPED' if skipped else 'RELAXATION_ORDER_UNCERTAIN' if order_skipped
            else 'RELAXATION_OCCURRENCE_COUPLED' if coupled_skipped else None)
    return {**out, 'note': note} if note else out


def derived_result(dq, pid, window, events, mentions):
    """One patient's result for a 1.1 request, before coverage and the excluded-records rule: the two passes, combined
    as the decision log's table states."""
    if window is None:
        return {'patient_id': pid, 'status': 'excluded', 'reason': 'NO_INDEX_WINDOW'}, {}
    first, second, points, families, linked, inexact = derive(dq, pid, window, events, mentions)
    query = dq.query
    counted = [s['id'] for s in query['steps'] if dq.spec.get(s['id'], ('',))[0] == 'count']
    one = _one_pass(dq, query, {'patient_id': pid, 'window': window, 'events': first}, families, linked, points)

    def unsure(**extra):
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'STEP_CONDITION_UNCERTAIN',
                'uncertain_steps': counted, **extra, 'complete': False}
    weak = lambda r: inexact and r.get('certainty') == 'possible'
    if one['status'] in ('exact', 'possible', 'unresolved') or second is None:
        if inexact and one['status'] == 'possible':
            return unsure(), points
        if one['status'] == 'relaxed' and weak(one):
            return unsure(), points
        if one['status'] == 'unresolved' and weak(one.get('relaxed_match', {})):
            one = {k: v for k, v in one.items() if k != 'relaxed_match'}
        return one, points
    two = _one_pass(dq, query, {'patient_id': pid, 'window': window, 'events': second}, families, linked, points)
    notes = []
    for r in (one, two):
        for note in (r.get('note') or '').split('; '):
            if note and note not in notes:
                notes.append(note)
    if one['status'] == 'relaxed':
        if two['status'] == 'relaxed' and two['relaxations'] == one['relaxations'] and not weak(one):
            out = {k: one[k] for k in ('patient_id', 'status', 'relaxations', 'cost', 'certainty', 'binding', 'method')}
            out['complete'] = one['complete'] and two['complete']
            if notes:
                out['note'] = '; '.join(notes)
            return out, points
        if weak(one):
            return unsure(), points
        return unsure(relaxed_match={k: one[k] for k in ('relaxations', 'cost', 'certainty', 'binding', 'method')}), points
    if two['status'] == 'none':
        out = {'patient_id': pid, 'status': 'none', 'complete': one['complete'] and two['complete']}
        if notes:
            out['note'] = '; '.join(notes)
        return out, points
    return unsure(), points


def derived_admission(dq, result, patient, events, ancestors, window, points):
    """The excluded-records rule for a 1.1 request (D18 with D32's extensions), written again: Nth steps are displaced
    like first (Nth from last like last) on their envelope point; Nth and span-count steps reach every excluded record
    of their concepts, period counts those overlapping the window, other steps those their scope admits."""
    scope = dq.query.get('scope', 'any')
    reach, carrying = {}, {}
    for step in dq.query['steps']:
        sid = step['id']
        mine = [tuple(x['start']) for x in patient.get('excluded', []) if expanded([x['concept']], ancestors) & dq.base[sid]]
        if not mine:
            continue
        carrying[sid] = mine
        spec = dq.spec.get(sid)
        if spec is not None and (spec[0] == 'nth' or spec[2] != 'window'):
            hits = mine
        elif spec is not None:
            hits = [(lo, hi) for lo, hi in mine if lo <= window['end'] and hi >= window['start']]
        else:
            hits = [(lo, hi) for lo, hi in mine if start_possible(scope, window, lo, hi)]
        if hits:
            reach[sid] = hits
    if not carrying:
        return result
    status, pid = result['status'], result['patient_id']
    named = [s['id'] for s in dq.query['steps'] if s['id'] in reach]
    begins = {e['id']: e['start'] for e in events}
    begins.update(points)
    bound = result.get('binding') or result.get('relaxed_match', {}).get('binding') or \
        result.get('unverified_match', {}).get('binding')
    if bound is not None:
        displaced = []
        for step in dq.query['steps']:
            sid = step['id']
            if sid not in carrying:
                continue
            how = step.get('occurrence', 'any')
            spec = dq.spec.get(sid)
            if spec is not None and spec[0] == 'nth':
                how = 'first' if spec[1] == 'nth' else 'last'
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


def derived_exhaustive_match(ingest, query, windows, conditions):
    """`exhaustive_match` for a 1.1 request with derived steps: every patient's full, closure-expanded events (with
    value tags), the derived passes, then coverage on `none` (a derived step recorded by what it counts), then the
    excluded-records rule."""
    ancestors = ingest['closure']['ancestors']
    dq = DerivedQuery(ingest, query, conditions)
    recorders = {}
    for p in ingest['patients']:
        for e in p['events']:
            for c in expanded(e['concepts'], ancestors):
                recorders.setdefault(c, set()).add(e['source'])
    if dq.value is not None:
        recorders.update(dq.value.recorders)
    results = []
    for p in sorted(ingest['patients'], key=lambda p: p['patient_id']):
        window = windows.get(p['patient_id'])
        events = []
        for e in p['events']:
            concepts = expanded(e['concepts'], ancestors)
            if dq.value is not None:
                concepts = concepts | dq.value.tags_of(p['patient_id'], e['event_id'])
            event = {'id': e['event_id'], 'concepts': sorted(concepts), 'start': list(e['start'])}
            if 'end' in e:
                event['end'] = list(e['end'])
            event.update({k: e[k] for k in ('start_var', 'end_var', 'proper') if k in e})
            events.append(event)
        mentions = {e['event_id'] for e in p['events'] if e['kind'] == 'mention'}
        result, points = derived_result(dq, p['patient_id'], window, events, mentions)
        if result['status'] == 'none':
            result = coverage_rule(dq.query, dq.loads, recorders, p['coverage'], window, result)
        if window is not None and result['status'] != 'not_comparable':
            result = derived_admission(dq, result, p, events, ancestors, window, points)
        results.append(result)
    return results


def has_derived(conditions):
    """Whether a request carries an Nth occurrence or a count (D32)."""
    return conditions is not None and conditions.get('profile') == LATER and any(
        'occurrence' in raw or 'count' in raw for raw in conditions['steps'].values())
