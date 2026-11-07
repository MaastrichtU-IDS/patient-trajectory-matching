"""Pure-Python reference for the execution store: the same logical tables, and an exhaustive
matcher over every patient.

It shares no code with `patterns/execution_store.py`. The tables are built from the ingest
document with dictionaries and loops, not SQL; the canonical-content digest, the bucket and
the coverage rule are written again from the specification in docs/execution-store.md. It
reads only the ingest and calls the trajectory-graph engine, never the store.

`patterns/test_execution_store.py` compares every table row for row with the store's, the
table digests with the manifest's, and the store's match results with `exhaustive_match`.
"""
from __future__ import annotations

import hashlib
import json

from . import trajectory_graph as tg

BUCKET_COUNT = 8
COLUMNS = {
    'patients': ['patient_idx', 'patient_id', 'bucket'],
    'events': ['patient_idx', 'event_id', 'kind', 'source', 'clock_id', 'start_lo', 'start_hi', 'end_lo', 'end_hi',
               'ongoing', 'evidence'],
    'event_concepts': ['patient_idx', 'event_id', 'concept', 'basis'],
    'concept_closure': ['concept', 'ancestor', 'basis', 'version', 'concept_basis', 'ancestor_basis'],
    'concept_postings': ['concept', 'patient_idx', 'n', 'min_start_lo', 'max_start_hi'],
    'coverage': ['patient_idx', 'source', 'clock_id', 'lo', 'hi', 'basis'],
    'source_concepts': ['source', 'concept', 'basis'],
    'excluded_records': ['patient_idx', 'source', 'concept', 'start_lo', 'start_hi', 'reason', 'n'],
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


def build_tables(ingest):
    """{table: [row tuples]} in the store's column order."""
    ancestors = ingest['closure']['ancestors']
    version = ingest['closure']['version']
    ids = sorted(p['patient_id'] for p in ingest['patients'])
    index = {pid: i for i, pid in enumerate(ids)}
    tables = {name: [] for name in COLUMNS}
    for pid in ids:
        tables['patients'].append((index[pid], pid, bucket_of(pid)))
    kinds = ingest['closure'].get('bases') or {}
    kind = lambda c: kinds[c] if c in kinds else 'declared'
    for concept in ancestors:
        tables['concept_closure'].append((concept, concept, 'self', version, kind(concept), kind(concept)))
        for a in ancestors[concept]:
            tables['concept_closure'].append((concept, a, 'declared', version, kind(concept), kind(a)))
    recorded = set()
    for p in ingest['patients']:
        i, clock = index[p['patient_id']], p['clock_id']
        postings = {}
        for e in p['events']:
            lo, hi = e['start']
            if 'end' in e:
                end_lo, end_hi = e['end']
            else:
                end_lo = end_hi = None
            tables['events'].append((i, e['event_id'], e['kind'], e['source'], clock, lo, hi, end_lo, end_hi,
                                     end_lo is not None and end_hi is None, evidence_text(e['evidence'])))
            for c in e['concepts']:
                tables['event_concepts'].append((i, e['event_id'], c, 'direct'))
            for c in expanded(e['concepts'], ancestors):
                n, first, last = postings.get(c, (0, lo, hi))
                postings[c] = (n + 1, min(first, lo), max(last, hi))
                recorded.add((e['source'], c))
        for c, (n, first, last) in postings.items():
            tables['concept_postings'].append((c, i, n, first, last))
        for period in p['coverage']:
            tables['coverage'].append((i, period['source'], clock, period['lo'], period['hi'], period['basis']))
        excluded = {}
        for x in p.get('excluded', []):
            key = (x['source'], x['concept'], x['start'][0], x['start'][1], x['reason'])
            excluded[key] = excluded.get(key, 0) + 1
        for (source, concept, lo, hi, reason), n in excluded.items():
            tables['excluded_records'].append((i, source, concept, lo, hi, reason, n))
    tables['source_concepts'] = [(source, concept, 'inferred_from_records') for source, concept in recorded]
    return {name: sorted(rows, key=lambda r: json.dumps(list(r), ensure_ascii=False)) for name, rows in tables.items()}


def digest(rows):
    """Canonical-content digest: compact JSON array per row, sorted as UTF-8 bytes, newline-terminated."""
    encoded = sorted(json.dumps([*row], separators=(',', ':'), ensure_ascii=False).encode('utf-8') for row in rows)
    return hashlib.sha256(b''.join(line + b'\n' for line in encoded)).hexdigest()


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

    Steps reach an excluded record through its concept's closure and the scope. Then, in order: a
    first or last step whose target an excluded record could displace makes the result not
    comparable; a `none` is not comparable; a `relaxed` is unresolved with the match beside it; a
    `possible` is incomplete.
    """
    scope = query.get('scope', 'any')
    reach = {}
    for step in query['steps']:
        hits = [tuple(x['start']) for x in patient.get('excluded', [])
                if expanded([x['concept']], ancestors) & per_step[step['id']] and start_possible(scope, window, *x['start'])]
        if hits:
            reach[step['id']] = hits
    if not reach:
        return result
    status, pid = result['status'], result['patient_id']
    named = [step['id'] for step in query['steps'] if step['id'] in reach]
    begins = {e['id']: e['start'] for e in events}
    bound = result.get('binding')
    if bound is None and 'relaxed_match' in result:
        bound = result['relaxed_match']['binding']
    if bound is not None:
        displaced = []
        for step in query['steps']:
            sid, how = step['id'], step.get('occurrence', 'any')
            if sid not in reach:
                continue
            lo, hi = begins[bound[sid]]
            if how == 'first' and min(x for x, _ in reach[sid]) <= hi:
                displaced.append(sid)
            elif how == 'last' and max(y for _, y in reach[sid]) >= lo:
                displaced.append(sid)
        if displaced:
            return {'patient_id': pid, 'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                    'excluded_steps': displaced}
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


def exhaustive_match(ingest, query, windows):
    """The engine on every patient's full, closure-expanded events; then the coverage rule on `none`,
    then the excluded-records rule on whatever the engine decided."""
    ancestors = ingest['closure']['ancestors']
    recorders = {}
    for p in ingest['patients']:
        for e in p['events']:
            for c in expanded(e['concepts'], ancestors):
                recorders.setdefault(c, set()).add(e['source'])
    per_step = step_concepts(query)
    results = []
    for p in sorted(ingest['patients'], key=lambda p: p['patient_id']):
        window = windows.get(p['patient_id'])
        events = []
        for e in p['events']:
            event = {'id': e['event_id'], 'concepts': sorted(expanded(e['concepts'], ancestors)), 'start': list(e['start'])}
            if 'end' in e:
                event['end'] = list(e['end'])
            events.append(event)
        result = tg.match(query, {'patient_id': p['patient_id'], 'window': window, 'events': events})
        if result['status'] == 'none':
            uncovered, unrecorded = [], []
            for step in (s['id'] for s in query['steps']):
                able = set()
                for c in per_step[step]:
                    able |= recorders.get(c, set())
                if not able:
                    unrecorded.append(step)
                covered = any(period['source'] in able and period['lo'] <= window['end'] and period['hi'] >= window['start']
                              for period in p['coverage'])
                if not covered:
                    uncovered.append(step)
            if uncovered:
                result = {'patient_id': p['patient_id'], 'status': 'not_comparable',
                          'reason': 'NO_SOURCE_RECORDS_CONCEPT' if unrecorded else 'NO_SOURCE_COVERAGE',
                          'uncovered_steps': uncovered}
                if unrecorded:
                    result['unrecorded_steps'] = unrecorded
        if window is not None and result['status'] != 'not_comparable':
            result = admission_check(query, per_step, result, p, events, ancestors, window)
        results.append(result)
    return results
