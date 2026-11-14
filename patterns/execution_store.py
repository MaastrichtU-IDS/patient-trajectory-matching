"""Execution store: immutable Parquet snapshots on embedded DuckDB, built by streaming one patient
at a time, and a relaxation-sound candidate filter in front of the trajectory-graph matcher.

`StoreBuilder(root, header)` takes the ingest header (profile, dataset id, closure), then one
patient per `add()`, then the provenance in `finish()`. Each `add()` validates the patient against
the closure, appends its rows to the staging files of its bucket's current part (a run of whole
patients of one bucket) and its digest to a staged patient list, so Python holds one patient at a
time. `finish()` derives ten tables in a file-backed DuckDB database under a memory limit, one part
at a time and bucket by bucket, so the derive's working set does not grow with the cohort, and
writes them as Parquet under `root/<snapshot_id>/`, with a `manifest.json` that
`schemas/execution-store-manifest.schema.json` validates. `build(ingest, root)` does the same for a
whole ingest document. Docs: docs/execution-store.md; decisions D19, D20, D23 and D26.

Identity is content, never Parquet bytes. The input digest hashes the header and a sorted list of
per-patient digests. Each table's content digest hashes its per-bucket digests, and each bucket
digest hashes the bucket's rows in key order, streamed from DuckDB in batches, so no table is ever
held in Python. The snapshot identifier hashes the input digest, the builder's artifacts and every
table's count and content digest. Integrity is bytes: the manifest records each Parquet file's
sha256. Opening checks those (`verify='files'`, the default); `verify='content'` re-derives the
content digests; `finish()` always checks content before it publishes.

`candidates(store, query)` intersects concept postings across a trajectory-graph query's
steps, rarest first. Each step's postings are the union over its concept and every concept a
`widen_concept` relaxation within the budget could put in its place, so a patient the
engine could match under any tried relaxation set is never dropped. `match(store, query,
windows)` runs `trajectory_graph.match` on the survivors, except those the postings' start and end
summaries prove `none` (time pruning, D25 and D26: the engine's own `none`, its events never loaded),
reports the rest as `none` with reason `FILTERED`, and turns `none` into `not_comparable` when, for some step, no source
that records the step's concepts (table `source_concepts`) covers the patient's window, or
when the patient holds excluded records (table `excluded_records`) a step could have bound. Its
output carries the snapshot's labels and a `context` (snapshot, query and window digests,
engine artifact digests, rules, verification level, labels) and that context's digest, `context_id`.

A match request may carry value `conditions` (D28): per step, a comparison or range on the values of the step's
concept's `series` rows in one literal unit, and value relaxations that widen one. Each is compiled into a per-query
tag concept (`Request`), which the engine sees as the step's concept; the engine is unchanged, and the candidate
filter, time pruning, loading, coverage and excluded records each read tags as docs/execution-store.md says.

`patterns/execution_store_reference.py` builds the same tables and digests in pure Python, and
`patterns/test_execution_store.py` compares them table by table and bucket by bucket. DuckDB is
imported lazily, here only, so nothing else in the engine needs it.
"""
from __future__ import annotations

import argparse
import atexit
import copy
import hashlib
import json
import os
from pathlib import Path
import multiprocessing
import re
import shutil
import sys
import tempfile
import time

from jsonschema import Draft202012Validator

from . import trajectory_graph as tg
from ._resources import ROOT

# 2.1 (D24): events hold their time variables and `proper`, and patients whether their events share variables.
# 3.0 (D26): the D5 record fields are typed event columns, free-form evidence is its own table (`event_evidence`),
# and postings keep each concept's latest end (`max_end_hi`).
# 3.1 (D27): a `series` table holds measurement values (exact decimal text, micro-unit integer, literal unit) for
# point events, beside `events`; a document without series gives the same rows in every other table.
PROFILE = 'execution-store-3.1'
# The ingest profile is unchanged: the event fields 2.1 and 3.0 read (start_var, end_var, proper and the record
# fields) are optional, so a document without them is read, and digested, exactly as before (D25, D26).
INGEST_PROFILE = 'execution-store-ingest-2.0'
MATCH_PROFILE = 'execution-store-match-2.0'
SCHEMA_PATH = 'schemas/execution-store-manifest.schema.json'
MODULE_PATH = 'patterns/execution_store.py'
ORIGIN = '0001-01-01T00:00:00'
MAX_TIME = 315_537_897_599_999_999  # 9999-12-31T23:59:59.999999 from ORIGIN, in microseconds
BUCKETS = 8
# The label of the one bucket an unpartitioned table has.
WHOLE = 'all'
# Rows fetched from DuckDB per batch while digesting; any size gives the same digests.
FETCH_ROWS = 10_000
# Staged rows (events, event concepts, coverage and excluded records) in one derive part: a part is a
# run of whole patients of one bucket, and `finish()` derives one part at a time, so the derive's
# working set depends on this and not on the cohort. Any size gives the same tables (D23).
PART_ROWS = 2_000_000
DEFAULT_MEMORY_LIMIT = '4GB'
# Patient indices one ordered query of a partitioned table's bucket covers when digesting (`content_digests`):
# about 12,500 patients of the bucket, so a sort's size depends on this and not on the cohort (D24). Any span
# gives the same digests.
DIGEST_SPAN = 100_000
CANONICAL_FORM = ('per table and bucket (an unpartitioned table is the one bucket "all"; a partitioned one has buckets '
                  '0..7, empty ones included): sha256 over its rows ordered by the table key, then every column, '
                  'each row a compact UTF-8 JSON array of its values in column order followed by a newline; the table '
                  'digest is sha256 over "<bucket>\\t<bucket digest>\\n" for its buckets in order')
INPUT_FORM = ('sha256 over the canonical JSON of {profile, dataset_id, closure, provenance}, a newline, then '
              '"<patient_id>\\t<sha256 of the patient\'s canonical JSON>\\n" for each patient in code-point order of '
              'patient_id; a patient is canonical with events sorted by event_id, each event\'s concepts sorted, '
              'coverage and excluded records sorted, and keys sorted, and each event without proper: false, a '
              'start_var equal to "<event_id>.s" or an end_var equal to "<event_id>.f"; series rows, when given, '
              'sorted by event_id, and an empty series left out')

# Logical tables: column names and DuckDB types, in canonical column order.
TABLES = {
    'patients': (('patient_idx', 'INTEGER'), ('patient_id', 'VARCHAR'), ('bucket', 'INTEGER'),
                 ('shared_variables', 'BOOLEAN')),
    'events': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('kind', 'VARCHAR'), ('source', 'VARCHAR'),
               ('clock_id', 'VARCHAR'), ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('end_lo', 'BIGINT'),
               ('end_hi', 'BIGINT'), ('ongoing', 'BOOLEAN'), ('start_var', 'VARCHAR'), ('end_var', 'VARCHAR'),
               ('proper', 'BOOLEAN'), ('status', 'VARCHAR'), ('selection_state', 'VARCHAR'), ('recorded_at', 'BIGINT'),
               ('available_at', 'BIGINT'), ('claim_id', 'VARCHAR'), ('claim_sha256', 'VARCHAR'),
               ('source_table', 'VARCHAR'), ('record_sha256', 'VARCHAR'), ('file_sha256', 'VARCHAR')),
    'event_evidence': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('evidence', 'VARCHAR')),
    'event_concepts': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('concept', 'VARCHAR'),
                       ('basis', 'VARCHAR')),
    'concept_closure': (('concept', 'VARCHAR'), ('ancestor', 'VARCHAR'), ('basis', 'VARCHAR'), ('version', 'VARCHAR'),
                        ('concept_basis', 'VARCHAR'), ('ancestor_basis', 'VARCHAR')),
    'concept_postings': (('concept', 'VARCHAR'), ('patient_idx', 'INTEGER'), ('n', 'BIGINT'),
                         ('min_start_lo', 'BIGINT'), ('max_start_hi', 'BIGINT'), ('max_end_hi', 'BIGINT')),
    'coverage': (('patient_idx', 'INTEGER'), ('source', 'VARCHAR'), ('clock_id', 'VARCHAR'), ('lo', 'BIGINT'),
                 ('hi', 'BIGINT'), ('basis', 'VARCHAR')),
    'source_concepts': (('source', 'VARCHAR'), ('concept', 'VARCHAR'), ('basis', 'VARCHAR')),
    'excluded_records': (('patient_idx', 'INTEGER'), ('source', 'VARCHAR'), ('concept', 'VARCHAR'),
                         ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('reason', 'VARCHAR'), ('n', 'BIGINT')),
    # D27: one row per measured point event; the time is the event's, copied so a series scan needs no join.
    'series': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('item_class', 'VARCHAR'), ('start_lo', 'BIGINT'),
               ('start_hi', 'BIGINT'), ('value_lexical', 'VARCHAR'), ('value_decimal', 'VARCHAR'),
               ('value_micro', 'BIGINT'), ('unit', 'VARCHAR')),
}
# The only column types a table may have: no floats or decimals, whose text forms are not canonical.
ALLOWED_TYPES = ('INTEGER', 'BIGINT', 'BOOLEAN', 'VARCHAR')
# Each table's key: unique within the table, so ordering by it is total. Every column follows as a
# tie-break, so that even a file with a repeated key digests the same in any row order.
KEYS = {'patients': ('patient_idx',), 'events': ('patient_idx', 'event_id'),
        'event_evidence': ('patient_idx', 'event_id'), 'event_concepts': ('patient_idx', 'event_id', 'concept'),
        'concept_closure': ('concept', 'ancestor'),
        'concept_postings': ('concept', 'patient_idx'), 'coverage': ('patient_idx', 'source', 'lo', 'hi', 'basis'),
        'source_concepts': ('source', 'concept'),
        'excluded_records': ('patient_idx', 'source', 'concept', 'start_lo', 'start_hi', 'reason'),
        'series': ('patient_idx', 'item_class', 'start_lo', 'start_hi', 'event_id')}
# Patient-keyed tables are written as hive-style bucket partitions; the others as one file.
PARTITIONED = ('events', 'event_evidence', 'event_concepts', 'coverage', 'excluded_records', 'series')
# Row order inside the Parquet files, for reads by patient or concept. Identity never depends on it.
SORT = {'patients': 'patient_idx', 'events': 'patient_idx, start_lo, event_id',
        'event_evidence': 'patient_idx, event_id', 'event_concepts': 'patient_idx, concept, event_id',
        'concept_closure': 'concept, ancestor',
        'concept_postings': 'concept, patient_idx', 'coverage': 'patient_idx, source, lo',
        'source_concepts': 'concept, source', 'excluded_records': 'patient_idx, concept, start_lo, reason',
        'series': 'patient_idx, item_class, start_lo, event_id'}
VERIFY_LEVELS = ('files', 'content', False)
# The basis a concept has when the ingest's closure does not name one.
DEFAULT_CONCEPT_BASIS = 'declared'
# How `match` turns `none` into `not_comparable` (docs/execution-store.md, "Comparability").
COVERAGE_RULE = 'window_overlap_source_concepts_v1'
# How `match` turns a remaining `none` into `not_comparable` when the source held records of a step's
# concepts that the ingest left out (docs/execution-store.md, "Excluded records").
EXCLUSION_RULE = 'excluded_records_scope_v1'
ENGINE_ARTIFACTS = ('patterns/execution_store.py', 'patterns/trajectory_graph.py', 'patterns/temporal_stn.py')
_MEMORY = re.compile(r'^[0-9]+(\.[0-9]+)?\s*(B|KB|MB|GB|TB|KiB|MiB|GiB|TiB)$')
# Characters a patient identifier may not hold: they delimit the input digest's lines.
_ID_DELIMITERS = ('\t', '\n', '\r')
_SNAPSHOT_NAME = re.compile(r'[0-9a-f]{64}')


class IngestError(ValueError):
    """Ingest outside the contract. `code` is stable; the message adds where."""

    def __init__(self, code, where=''):
        super().__init__(f'{code}: {where}' if where else code)
        self.code = code


class StoreError(ValueError):
    """A snapshot, query or window the store refuses. `code` is stable."""

    def __init__(self, code, where=''):
        super().__init__(f'{code}: {where}' if where else code)
        self.code = code


def _duckdb():
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - exercised only without the store extra
        raise StoreError('DUCKDB_UNAVAILABLE', 'pip install -r patterns/requirements-store.lock.txt') from exc
    return duckdb


def _require(condition, code, where=''):
    if not condition:
        raise IngestError(code, where)


def _time(value, where):
    _require(type(value) is int, 'INVALID_TIME', where)
    _require(0 <= value <= MAX_TIME, 'TIME_OUT_OF_RANGE', where)
    return value


# Validation: the header, the provenance and one patient at a time.

def validate_header(header):
    """The ingest header: profile, dataset id and closure, refused by name."""
    _require(isinstance(header, dict) and header.get('profile') == INGEST_PROFILE, 'UNSUPPORTED_INGEST_PROFILE')
    _require(set(header) == {'profile', 'dataset_id', 'closure'}, 'INVALID_INGEST_FIELDS')
    _require(type(header['dataset_id']) is str and header['dataset_id'], 'INVALID_DATASET_ID')
    closure = header['closure']
    _require(isinstance(closure, dict) and {'version', 'ancestors'} <= set(closure) <= {'version', 'ancestors', 'bases'},
             'INVALID_CLOSURE')
    _require(type(closure['version']) is str and closure['version'], 'INVALID_CLOSURE_VERSION')
    ancestors = closure['ancestors']
    _require(isinstance(ancestors, dict) and ancestors, 'INVALID_CLOSURE')
    for concept, above in ancestors.items():
        _require(type(concept) is str and concept and isinstance(above, list)
                 and all(type(a) is str for a in above), 'INVALID_CLOSURE', concept)
        _require(len(above) == len(set(above)), 'DUPLICATE_ANCESTOR', concept)
        _require(concept not in above, 'CLOSURE_CYCLE', concept)
        for a in above:
            _require(a in ancestors, 'CLOSURE_UNKNOWN_ANCESTOR', f'{concept} -> {a}')
            _require(set(ancestors[a]) <= set(above), 'CLOSURE_NOT_TRANSITIVE', f'{concept} -> {a}')
    bases = closure.get('bases')
    _require(bases is None or (isinstance(bases, dict) and set(bases) == set(ancestors)
                               and all(type(b) is str and b for b in bases.values())), 'INVALID_CONCEPT_BASES')
    return header


def validate_provenance(provenance):
    """Free-form provenance, except `selection` and `labels`, which the manifest copies."""
    _require(isinstance(provenance, dict), 'INVALID_PROVENANCE')
    selection = provenance.get('selection')
    _require(selection is None or (isinstance(selection, dict)
                                   and all(type(selection.get(k)) is str for k in ('mode', 'version', 'available_at'))),
             'INVALID_SELECTION')
    labels = provenance.get('labels')
    _require(labels is None or (isinstance(labels, dict) and labels
                                and all(type(k) is str and type(v) in (str, bool) for k, v in labels.items())),
             'INVALID_LABELS')
    try:
        _json(provenance)
    except (TypeError, ValueError):
        raise IngestError('INVALID_PROVENANCE') from None
    return provenance


# The D5 record fields (D26): optional, typed, and stored as event columns, not in the free-form evidence. Each
# maps to its kind: a non-empty string, a time on the patient's clock (microseconds from ORIGIN), or a sha256.
RECORD_FIELDS = {'status': 'text', 'selection_state': 'text', 'recorded_at': 'time', 'available_at': 'time',
                 'claim_id': 'text', 'claim_sha256': 'sha256', 'source_table': 'text', 'record_sha256': 'sha256',
                 'file_sha256': 'sha256'}
EVENT_FIELDS = {'event_id', 'kind', 'source', 'concepts', 'start', 'end', 'evidence', 'start_var', 'end_var',
                'proper'} | set(RECORD_FIELDS)
_SHA256 = re.compile(r'[0-9a-f]{64}')


def engine_event(event_id, concepts, start, end, start_var, end_var, proper):
    """An engine event: the stored times, and the variable names and `proper` only where the ingest gave
    them (an absent name is the engine's default, `<id>.s` or `<id>.f`)."""
    out = {'id': event_id, 'concepts': concepts, 'start': start}
    if start_var is not None:
        out['start_var'] = start_var
    if end is not None:
        out['end'] = end
        if end_var is not None:
            out['end_var'] = end_var
        if proper:
            out['proper'] = True
    return out


def engine_events(events):
    """A patient's ingest events as engine events (concepts as given)."""
    return [engine_event(e['event_id'], e['concepts'], list(e['start']), list(e['end']) if 'end' in e else None,
                         e.get('start_var'), e.get('end_var'), e.get('proper', False)) for e in events]


def shares_variables(events):
    """Whether two of these ingest events name one time variable, default names included (as the engine
    names them): then the events are matched together, never filtered by concept (`patients.shared_variables`)."""
    if not any('start_var' in e or 'end_var' in e for e in events):
        return False  # default names are unique, since event ids are
    seen = set()
    for e in events:
        names = {e.get('start_var', e['event_id'] + '.s')}  # a point names its start only
        if 'end' in e:
            names.add(e.get('end_var', e['event_id'] + '.f'))
        if not names.isdisjoint(seen):
            return True
        seen |= names
    return False


def validate_patient(p, ancestors):
    """One patient against the closure's concepts. Duplicate patient ids are a whole-stream check."""
    _require(isinstance(p, dict) and {'patient_id', 'clock_id', 'events', 'coverage'} <= set(p)
             and set(p) <= {'patient_id', 'clock_id', 'events', 'coverage', 'box_representable', 'excluded', 'series'},
             'INVALID_PATIENT')
    pid = p['patient_id']
    _require(type(pid) is str and pid and not any(c in pid for c in _ID_DELIMITERS), 'INVALID_PATIENT_ID')
    # The store holds each time as bounds, and events may name shared time variables (an equality
    # between events) and be proper; the engine models both exactly (D24). A source whose other
    # constraints between events go beyond that cannot be stored without losing soundness.
    _require(type(p.get('box_representable', True)) is bool, 'INVALID_PATIENT', pid)
    _require(p.get('box_representable', True), 'INGEST_PATIENT_NOT_BOX_REPRESENTABLE', pid)
    _require(type(p['clock_id']) is str and p['clock_id'], 'INVALID_CLOCK_ID', pid)
    _require(isinstance(p['events'], list), 'INVALID_EVENTS', pid)
    ids, named = set(), False
    for e in p['events']:
        _require(isinstance(e, dict) and {'event_id', 'kind', 'source', 'concepts', 'start', 'evidence'} <= set(e)
                 and set(e) <= EVENT_FIELDS, 'INVALID_EVENT', pid)
        eid = e['event_id']
        where = f'{pid}/{eid}'
        _require(type(eid) is str and eid, 'INVALID_EVENT_ID', pid)
        _require(eid not in ids, 'DUPLICATE_EVENT_ID', where)
        ids.add(eid)
        _require(type(e['kind']) is str and e['kind'], 'INVALID_EVENT_KIND', where)
        _require(type(e['source']) is str and e['source'], 'INVALID_EVENT_SOURCE', where)
        concepts = e['concepts']
        _require(isinstance(concepts, list) and concepts and all(type(c) is str for c in concepts),
                 'INVALID_EVENT_CONCEPTS', where)
        _require(len(concepts) == len(set(concepts)), 'DUPLICATE_EVENT_CONCEPT', where)
        for c in concepts:
            _require(c in ancestors, 'UNKNOWN_CONCEPT', f'{where}: {c}')
        start = e['start']
        _require(isinstance(start, list) and len(start) == 2, 'INVALID_TIME', where)
        lo, hi = (_time(v, where) for v in start)
        _require(lo <= hi, 'INVERTED_BOUNDS', f'{where} start')
        if 'end' in e:
            end = e['end']
            _require(isinstance(end, list) and len(end) == 2, 'INVALID_TIME', where)
            end_lo = _time(end[0], where)
            _require(end_lo >= lo, 'END_BEFORE_START', where)
            if end[1] is not None:
                _require(_time(end[1], where) >= end_lo, 'INVERTED_BOUNDS', f'{where} end')
        # Time variables, as the engine names them (a point has no end variable), and `proper`, an
        # end strictly after the start, which a point cannot have.
        for field in ('start_var', 'end_var'):
            _require(field not in e or (type(e[field]) is str and e[field]), 'INVALID_EVENT_VARIABLE', where)
        _require('end_var' not in e or 'end' in e, 'INVALID_EVENT_VARIABLE', where)
        _require('proper' not in e or (type(e['proper']) is bool and 'end' in e), 'INVALID_EVENT_PROPER', where)
        named = named or 'start_var' in e or 'end_var' in e or 'proper' in e
        # The record fields (D26): present only with a value of their kind; an unknown value is left out.
        for field, kind in RECORD_FIELDS.items():
            if field in e:
                value = e[field]
                if kind == 'time':
                    _time(value, f'{where} {field}')
                else:
                    _require(type(value) is str and value and (kind == 'text' or _SHA256.fullmatch(value)),
                             'INVALID_RECORD_FIELD', f'{where} {field}')
        _require(isinstance(e['evidence'], dict), 'INVALID_EVIDENCE', where)
        try:
            json.dumps(e['evidence'], allow_nan=False)
        except (TypeError, ValueError):
            raise IngestError('INVALID_EVIDENCE', where) from None
    if named:
        # The engine solves the patient's network when it matches; refuse here what it would refuse
        # then (variables no time satisfies, a linked group beyond its limit), so `match` never meets it.
        try:
            tg.patient_times({'patient_id': pid, 'window': None, 'events': engine_events(p['events'])}, trusted=True)
        except tg.TrajectoryError as exc:
            raise IngestError(str(exc), pid) from None
    _require(isinstance(p['coverage'], list), 'INVALID_COVERAGE', pid)
    periods = set()
    for c in p['coverage']:
        _require(isinstance(c, dict) and set(c) == {'source', 'lo', 'hi', 'basis'}, 'INVALID_COVERAGE', pid)
        _require(type(c['source']) is str and c['source'] and type(c['basis']) is str and c['basis'],
                 'INVALID_COVERAGE', pid)
        lo, hi = _time(c['lo'], pid), _time(c['hi'], pid)
        _require(lo <= hi, 'INVERTED_BOUNDS', f'{pid} coverage {c["source"]}')
        key = (c['source'], lo, hi, c['basis'])
        _require(key not in periods, 'DUPLICATE_COVERAGE', f'{pid} {c["source"]}')
        periods.add(key)
    # Records the source held that the ingest left out: a concept and a start, never an event.
    _require(isinstance(p.get('excluded', []), list), 'INVALID_EXCLUDED', pid)
    for x in p.get('excluded', []):
        _require(isinstance(x, dict) and set(x) == {'source', 'concept', 'start', 'reason'}
                 and all(type(x[k]) is str and x[k] for k in ('source', 'concept', 'reason')), 'INVALID_EXCLUDED', pid)
        _require(x['concept'] in ancestors, 'UNKNOWN_CONCEPT', f'{pid} excluded: {x["concept"]}')
        _require(isinstance(x['start'], list) and len(x['start']) == 2, 'INVALID_TIME', f'{pid} excluded')
        lo, hi = (_time(v, f'{pid} excluded') for v in x['start'])
        _require(lo <= hi, 'INVERTED_BOUNDS', f'{pid} excluded')
    validate_series(p)
    return p


SERIES_FIELDS = {'event_id', 'item_class', 'value_lexical', 'value_decimal', 'value_micro', 'unit'}


def validate_series(p):
    """A patient's measurement values (D27): at most one row per point event, for one of its direct concepts, with
    the exact decimal text and micro-unit integer `series_values` gives for the spelling, and a literal unit."""
    from . import series_values as sv
    pid = p['patient_id']
    _require(isinstance(p.get('series', []), list), 'INVALID_SERIES', pid)
    if not p.get('series'):
        return
    events = {e['event_id']: e for e in p['events']}
    seen = set()
    for r in p['series']:
        _require(isinstance(r, dict) and set(r) == SERIES_FIELDS, 'INVALID_SERIES', pid)
        # Before it is hashed or looked up (D28, a D27 follow-up): an unhashable or non-string id is refused by name.
        _require(type(r['event_id']) is str and r['event_id'], 'INVALID_SERIES', pid)
        where = f"{pid}/{r['event_id']}"
        event = events.get(r['event_id'])
        _require(event is not None, 'SERIES_UNKNOWN_EVENT', where)
        _require('end' not in event, 'SERIES_EVENT_NOT_POINT', where)
        _require(r['event_id'] not in seen, 'DUPLICATE_SERIES_EVENT', where)
        seen.add(r['event_id'])
        _require(type(r['item_class']) is str and r['item_class'] in event['concepts'], 'SERIES_ITEM_CLASS_MISMATCH', where)
        try:
            value = sv.decimal_of(r['value_lexical'])
        except sv.SeriesValueError:
            raise IngestError('INVALID_SERIES_VALUE', where) from None
        micro = r['value_micro']
        _require(r['value_decimal'] == sv.decimal_text(value)
                 and (micro is None or type(micro) is int) and micro == sv.micro_value(value), 'INVALID_SERIES_VALUE', where)
        _require(type(r['unit']) is str and r['unit'] and r['unit'].strip() == r['unit'], 'INVALID_SERIES_UNIT', where)


def _validate_document(ingest):
    """The parts of a whole ingest document outside the header and the patients."""
    _require(isinstance(ingest, dict) and ingest.get('profile') == INGEST_PROFILE, 'UNSUPPORTED_INGEST_PROFILE')
    _require(set(ingest) <= {'profile', 'dataset_id', 'provenance', 'closure', 'patients'}
             and {'dataset_id', 'closure', 'patients'} <= set(ingest), 'INVALID_INGEST_FIELDS')
    validate_provenance(ingest.get('provenance', {}))
    _require(isinstance(ingest['patients'], list) and ingest['patients'], 'NO_PATIENTS')
    return {k: ingest[k] for k in ('profile', 'dataset_id', 'closure')}


def validate_ingest(ingest):
    """Refuse a whole ingest document outside the contract, by name, in memory."""
    header = validate_header(_validate_document(ingest))
    seen = set()
    for p in ingest['patients']:
        validate_patient(p, header['closure']['ancestors'])
        _require(p['patient_id'] not in seen, 'DUPLICATE_PATIENT_ID', p['patient_id'])
        seen.add(p['patient_id'])
    return ingest


# Canonical forms.

def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def canonical_closure(closure):
    out = {'version': closure['version'], 'ancestors': {c: sorted(a) for c, a in sorted(closure['ancestors'].items())}}
    if 'bases' in closure:
        out['bases'] = dict(sorted(closure['bases'].items()))
    return out


def normal_event(e):
    """An event without what the engine reads as its default (D25): `proper: false`, a `start_var` equal to
    `<event_id>.s` and an `end_var` equal to `<event_id>.f`. The engine decides the two forms alike, so they
    have one digest and one set of rows."""
    own = {'start_var': e['event_id'] + '.s', 'end_var': e['event_id'] + '.f'}
    drop = [k for k in own if e.get(k) == own[k]] + (['proper'] if e.get('proper') is False else [])
    return {k: v for k, v in e.items() if k not in drop} if drop else e


def canonical_patient(p):
    """Order-free, normal form of one patient: the same content in any order, or with the engine's defaults
    spelled out (`normal_event`), has the same digest and the same rows."""
    out = {**p, 'events': [{**normal_event(e), 'concepts': sorted(e['concepts'])}
                           for e in sorted(p['events'], key=lambda e: e['event_id'])],
           'coverage': sorted(p['coverage'], key=lambda c: (c['source'], c['lo'], c['hi'], c['basis']))}
    if 'excluded' in p:
        out['excluded'] = sorted(p['excluded'], key=lambda x: (x['source'], x['concept'], x['start'], x['reason']))
    if p.get('series'):
        out['series'] = sorted(p['series'], key=lambda r: r['event_id'])
    else:  # `series: []` says what an absent `series` says, so the two have one digest (D28, a D27 follow-up)
        out.pop('series', None)
    return out


def header_line(header, provenance):
    """The first line of the input digest's text: see INPUT_FORM."""
    return _json({'profile': header['profile'], 'dataset_id': header['dataset_id'],
                  'closure': canonical_closure(header['closure']), 'provenance': provenance})


def row_line(row):
    """One row of the canonical content: a compact JSON array of its values, newline-terminated."""
    return json.dumps(list(row), ensure_ascii=False, separators=(',', ':')) + '\n'


# The string escaper `json.dumps(..., ensure_ascii=False)` itself uses (C when available): `rows_text` builds the same
# text as `row_line` from it, column by column (D28).
_ENCODE_STRING = json.encoder.encode_basestring
_CONSTANT_TEXT = {True: 'true', False: 'false', None: 'null'}
_NONE_TYPE = type(None)
# D28, part 3: whether `content_digests` builds a batch's canonical lines column by column (`rows_text`) or row by row
# (`row_line`, the definition). Both give the same bytes (the tests compare them); `rows_text` makes digesting faster.
_FAST_ROWS = True


def _column_text(column):
    """Each value of one column as `json.dumps` writes it inside an array, or None for a type it is not meant for."""
    kinds = set(map(type, column))
    if kinds == {str}:
        return list(map(_ENCODE_STRING, column))
    if kinds == {int}:
        return list(map(int.__repr__, column))
    if kinds <= {bool, _NONE_TYPE}:
        return list(map(_CONSTANT_TEXT.__getitem__, column))
    if kinds == {str, _NONE_TYPE}:
        return ['null' if v is None else _ENCODE_STRING(v) for v in column]
    if kinds == {int, _NONE_TYPE}:
        return ['null' if v is None else int.__repr__(v) for v in column]
    if kinds <= {str, int, bool, _NONE_TYPE}:
        return [_CONSTANT_TEXT[v] if v is None or type(v) is bool else int.__repr__(v) if type(v) is int
                else _ENCODE_STRING(v) for v in column]
    return None


def rows_text(rows):
    """`''.join(row_line(r) for r in rows)`, built a column at a time: the table types (INTEGER, BIGINT, BOOLEAN,
    VARCHAR and null) are written as `json.dumps(ensure_ascii=False)` writes them (`int.__repr__`, `true`, `false`,
    `null`, and its own string escaper), so the bytes are the same. A batch holding any other type is written row by
    row."""
    if not _FAST_ROWS or not rows:
        return ''.join(row_line(r) for r in rows)
    columns = [_column_text(c) for c in zip(*rows)]
    if not columns or any(c is None for c in columns):
        return ''.join(row_line(r) for r in rows)
    return '[' + ']\n['.join(map(','.join, zip(*columns))) + ']\n'


def bucket_labels(name):
    return [str(b) for b in range(BUCKETS)] if name in PARTITIONED else [WHOLE]


def bucket_of(patient_id):
    """A patient's bucket: the first 32 bits of sha256(patient_id) modulo BUCKETS, as the derive's SQL computes it."""
    return int(hashlib.sha256(patient_id.encode('utf-8')).hexdigest()[:8], 16) % BUCKETS


def table_digest(buckets):
    """A table's content digest from its bucket digests, in bucket order: see CANONICAL_FORM."""
    return _sha256(''.join(f'{label}\t{buckets[label]["content_digest"]}\n' for label in bucket_labels_of(buckets)))


def bucket_labels_of(buckets):
    return sorted(buckets, key=lambda label: (label == WHOLE, int(label) if label != WHOLE else 0))


def _closure_rows(closure):
    """concept_closure rows: each concept is its own ancestor (basis self), then its declared ones.

    Each row also carries what kind of concept each side is (`closure.bases`, default 'declared'),
    so that a row read alone says whether it is, say, a source item class or a clinical concept.
    """
    bases = closure.get('bases') or {}
    kind = lambda c: bases.get(c, DEFAULT_CONCEPT_BASIS)
    rows = []
    for concept, above in closure['ancestors'].items():
        rows.append({'concept': concept, 'ancestor': concept, 'basis': 'self', 'version': closure['version'],
                     'concept_basis': kind(concept), 'ancestor_basis': kind(concept)})
        rows.extend({'concept': concept, 'ancestor': a, 'basis': 'declared', 'version': closure['version'],
                     'concept_basis': kind(concept), 'ancestor_basis': kind(a)} for a in above)
    return rows


# DuckDB: staging, derivation, Parquet and streamed digests.

def _lit(path):
    return "'" + str(path).replace("'", "''") + "'"


def _read_json(paths, columns):
    spec = '{' + ', '.join(f"'{k}': '{v}'" for k, v in columns) + '}'
    return f"read_json([{', '.join(_lit(p) for p in paths)}], format='newline_delimited', columns={spec})"


def _staged_sql(staging, paths, columns):
    """A relation over staged files of one staged table, with its columns in STAGED order and types: ndjson lines
    read with those columns (a missing key is null), or typed Parquet files (D26); no files is no rows."""
    if not paths:
        return '(SELECT ' + ', '.join(f'NULL::{t} AS {c}' for c, t in columns) + ' WHERE false)'
    if staging == 'ndjson':
        return _read_json(paths, columns)
    return (f"(SELECT {', '.join(c for c, _ in columns)} FROM read_parquet([{', '.join(_lit(p) for p in paths)}]))")


# How `add()` stages rows (D26): typed Parquet files written through DuckDB, or newline-delimited JSON. Either gives
# the same tables and digests; see `StoreBuilder`.
STAGINGS = ('parquet', 'ndjson')
STAGING = 'parquet'
# Rows one Parquet staging file holds: each staged table of each open part buffers this many rows in Python, as typed
# columns, before they are written (the last file of a part may hold fewer). Any number gives the same tables.
STAGE_ROWS = 10_000
# The evidence stage is also flushed once its buffered evidence text reaches this many characters (D27; a D26 review
# follow-up): evidence is free-form, several KB an event for MIMIC rows, and the buffer is Python's, outside DuckDB's
# memory limit. Like `stage_rows`, it changes how rows are cut into files, never the tables.
STAGE_EVIDENCE_CHARS = 32 * 2 ** 20


class _ParquetStage:
    """One staged table's rows for one part (or the whole build), buffered as typed columns and written to Parquet
    files of `rows` rows each through DuckDB (`COPY (SELECT unnest(?), ...)`), so no Python object outlives a file."""

    def __init__(self, db, stem, columns, rows, measure=None, max_chars=None):
        self.db, self.stem, self.columns, self.rows = db, stem, columns, rows
        self.names = [c for c, _ in columns]
        self.select = ', '.join(f'unnest(CAST(? AS {t}[])) AS {c}' for c, t in columns)
        self.paths, self.n, self.chars = [], 0, 0
        self.measure, self.max_chars = measure, max_chars  # a text column whose buffered length also bounds a file
        self.data = [[] for _ in columns]

    def write(self, row):
        for column, name in zip(self.data, self.names):
            column.append(row.get(name))
        self.n += 1
        if self.measure is not None:
            self.chars += len(row[self.measure])
        if self.n >= self.rows or (self.measure is not None and self.chars >= self.max_chars):
            self.flush()

    def flush(self):
        if self.n:
            path = f'{self.stem}.f{len(self.paths):05d}.parquet'
            self.db.execute(f'COPY (SELECT {self.select}) TO {_lit(path)} (FORMAT parquet)', self.data)
            self.paths.append(path)
            self.n, self.chars, self.data = 0, 0, [[] for _ in self.columns]

    def close(self):
        self.flush()


STAGED = {
    # A staged line leaves out `shared_variables`, `start_var`, `end_var`, `proper` and the record fields when they
    # are false or absent; the reader takes a missing key as null.
    'patients': [('patient_id', 'VARCHAR'), ('clock_id', 'VARCHAR'), ('digest', 'VARCHAR'),
                 ('shared_variables', 'BOOLEAN')],
    'events': [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('kind', 'VARCHAR'), ('source', 'VARCHAR'),
               ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('end_lo', 'BIGINT'), ('end_hi', 'BIGINT'),
               ('start_var', 'VARCHAR'), ('end_var', 'VARCHAR'), ('proper', 'BOOLEAN')]
              + [(f, 'BIGINT' if kind == 'time' else 'VARCHAR') for f, kind in RECORD_FIELDS.items()],
    'evidence': [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('evidence', 'VARCHAR')],
    'concepts': [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('concept', 'VARCHAR')],
    'closure': [('concept', 'VARCHAR'), ('ancestor', 'VARCHAR'), ('basis', 'VARCHAR'), ('version', 'VARCHAR'),
                ('concept_basis', 'VARCHAR'), ('ancestor_basis', 'VARCHAR')],
    'coverage': [('patient_id', 'VARCHAR'), ('source', 'VARCHAR'), ('lo', 'BIGINT'), ('hi', 'BIGINT'), ('basis', 'VARCHAR')],
    'excluded': [('patient_id', 'VARCHAR'), ('source', 'VARCHAR'), ('concept', 'VARCHAR'), ('start_lo', 'BIGINT'),
                 ('start_hi', 'BIGINT'), ('reason', 'VARCHAR')],
    'series': [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('item_class', 'VARCHAR'), ('start_lo', 'BIGINT'),
               ('start_hi', 'BIGINT'), ('value_lexical', 'VARCHAR'), ('value_decimal', 'VARCHAR'),
               ('value_micro', 'BIGINT'), ('unit', 'VARCHAR')],
}


def _connect(path, threads, memory_limit, temp_directory):
    """A DuckDB connection whose results carry order only where a query says ORDER BY, and whose
    string order is binary (code point order), which the digests rely on."""
    duckdb = _duckdb()
    config = {'threads': threads, 'preserve_insertion_order': False, 'temp_directory': str(temp_directory)}
    if memory_limit is not None:
        config['memory_limit'] = memory_limit
    db = duckdb.connect(str(path), config=config)
    collation = db.execute("SELECT current_setting('default_collation')").fetchone()[0]
    if collation != '':
        db.close()
        raise StoreError('DUCKDB_COLLATION_NOT_BINARY', collation)
    return db


# Staged per derive part (a run of whole patients of one bucket); the patient list and the closure are one file each.
PART_STAGED = ('events', 'evidence', 'concepts', 'coverage', 'excluded', 'series')


def part_file(stage, name, part, suffix='.ndjson'):
    """A part's staging file of one staged table (its stem, with suffix '', for Parquet's numbered files)."""
    bucket, index = part
    return Path(stage) / f'{name}.b{bucket}.p{index:05d}{suffix}'


def _load_patients(db, relation):
    db.execute(f"CREATE TABLE s_patients AS SELECT * FROM {relation('patients')}")
    duplicate = db.execute('SELECT patient_id FROM s_patients GROUP BY patient_id HAVING count(*) > 1 '
                           'ORDER BY patient_id LIMIT 1').fetchone()
    _require(duplicate is None, 'DUPLICATE_PATIENT_ID', duplicate[0] if duplicate else '')


class _Statements:
    """Runs the derive's statements, timing each kind and reading DuckDB's own memory accounting
    (`duckdb_memory()`) after each, for `on_stage`."""

    def __init__(self, db):
        self.db = db
        self.reset()

    def reset(self):
        self.seconds, self.memory, self.temporary, self.count = {}, 0, 0, 0

    def __call__(self, kind, sql):
        started = time.perf_counter()
        self.db.execute(sql)
        self.seconds[kind] = self.seconds.get(kind, 0.0) + time.perf_counter() - started
        self.count += 1
        used, temporary = self.db.execute(
            'SELECT sum(memory_usage_bytes), sum(temporary_storage_bytes) FROM duckdb_memory()').fetchone()
        self.memory, self.temporary = max(self.memory, int(used or 0)), max(self.temporary, int(temporary or 0))

    def report(self):
        out = {'statements': self.count, 'statement_seconds': {k: round(v, 3) for k, v in self.seconds.items()},
               'duckdb_memory_max_bytes': self.memory, 'duckdb_temporary_max_bytes': self.temporary}
        try:
            out['process_peak_rss_bytes'] = _peak_rss()
        except ImportError:  # pragma: no cover - no `resource` module (Windows)
            pass
        self.reset()
        return out


def _create_table(db, name, columns):
    db.execute(f'CREATE TABLE {name} ({", ".join(f"{c} {t}" for c, t in columns)})')


def _derive(db, relation, parts, on_stage=None):
    """Create the ten logical tables (plus a bucket column on partitioned ones) from the staging files.

    Patient-keyed rows are derived one part at a time, bucket by bucket (D23). Every row of
    `events`, `event_evidence`, `event_concepts`, `coverage`, `excluded_records` and `concept_postings` depends on
    one patient's staged rows (and the closure and the patient index), and every patient is in
    exactly one part, so the union of the parts' rows is the table a single statement over all
    the staging gives; `source_concepts` is the distinct union of each part's distinct pairs.
    `on_stage('derived:bucket=k', info)` reports each bucket's statement times and memory. `relation(name,
    parts)` is the SQL relation over the staged rows of one staged table, for the given parts (`StoreBuilder.relation`).
    """
    run = _Statements(db)
    staged = lambda name, part=None: relation(name, () if part is None else (part,))
    # Dense index by sorted identifier; bucket from the identifier alone, so it never moves.
    run('patient_index', f"""CREATE TABLE p AS SELECT
        (row_number() OVER (ORDER BY patient_id ASC) - 1)::INTEGER AS patient_idx, patient_id,
        (('0x' || substr(sha256(patient_id), 1, 8))::UBIGINT % {BUCKETS})::INTEGER AS bucket, clock_id,
        coalesce(shared_variables, false) AS shared_variables
        FROM s_patients""")
    run('patients', 'CREATE TABLE patients AS SELECT patient_idx, patient_id, bucket, shared_variables FROM p')
    run('concept_closure', f'''CREATE TABLE concept_closure AS SELECT concept, ancestor, basis, version, concept_basis,
        ancestor_basis FROM {staged('closure')}''')
    for name in PARTITIONED:
        _create_table(db, name, TABLES[name] + (('bucket', 'INTEGER'),))
    _create_table(db, 'concept_postings', TABLES['concept_postings'])  # max_end_hi: null when some event is ongoing
    _create_table(db, 'part_source_concepts', (('source', 'VARCHAR'), ('concept', 'VARCHAR')))
    if on_stage:
        on_stage('derived:whole', run.report())
    # Rows inserted bucket by bucket keep each bucket's rows together, so `WHERE bucket = k` skips the rest.
    for bucket in range(BUCKETS):
        mine = [part for part in parts if part[0] == bucket]
        for part in mine:
            run('events', f"""CREATE TEMP TABLE part_events AS SELECT p.patient_idx, e.event_id, e.kind, e.source,
                p.clock_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi,
                (e.end_lo IS NOT NULL AND e.end_hi IS NULL) AS ongoing, e.start_var, e.end_var,
                coalesce(e.proper, false) AS proper, {', '.join('e.' + f for f in RECORD_FIELDS)}, p.bucket
                FROM {staged('events', part)} e JOIN p USING (patient_id)""")
            run('event_concepts', f"""CREATE TEMP TABLE part_concepts AS SELECT p.patient_idx, c.event_id, c.concept,
                'direct' AS basis, p.bucket FROM {staged('concepts', part)} c JOIN p USING (patient_id)""")
            run('concept_postings', """INSERT INTO concept_postings
                WITH expanded AS (SELECT DISTINCT ec.patient_idx, ec.event_id, cc.ancestor AS concept
                                  FROM part_concepts ec JOIN concept_closure cc ON cc.concept = ec.concept)
                SELECT x.concept, x.patient_idx, count(*)::BIGINT AS n, min(e.start_lo) AS min_start_lo,
                       max(e.start_hi) AS max_start_hi,
                       CASE WHEN bool_or(e.ongoing) THEN NULL ELSE max(coalesce(e.end_hi, e.start_hi)) END AS max_end_hi
                FROM expanded x JOIN part_events e USING (patient_idx, event_id) GROUP BY x.concept, x.patient_idx""")
            # Which sources record each concept (through the closure), fixed at build time so that a
            # comparability answer never depends on which patients a query happens to see.
            run('source_concepts', """INSERT INTO part_source_concepts SELECT DISTINCT e.source, cc.ancestor
                FROM part_events e JOIN part_concepts ec USING (patient_idx, event_id)
                JOIN concept_closure cc ON cc.concept = ec.concept""")
            run('series', f"""CREATE TEMP TABLE part_series AS SELECT p.patient_idx, s.event_id, s.item_class, s.start_lo,
                s.start_hi, s.value_lexical, s.value_decimal, s.value_micro, s.unit, p.bucket
                FROM {staged('series', part)} s JOIN p USING (patient_id)""")
            # Each series row is of a point event of the part, at that event's time, for one of its direct concepts.
            # The ingest's checks and the staging make it so. This guards the event link only (the row's event, its
            # time and its item class): a value or unit changed between ingest and table is not caught here, but by
            # the store-against-reference equality in the tests and, on the demo, by the graph rows against the store.
            orphan = db.execute("""SELECT count(*) FROM part_series s LEFT JOIN part_events e USING (patient_idx, event_id)
                LEFT JOIN part_concepts c ON c.patient_idx = s.patient_idx AND c.event_id = s.event_id
                     AND c.concept = s.item_class
                WHERE e.event_id IS NULL OR c.concept IS NULL OR e.end_lo IS NOT NULL
                   OR e.start_lo <> s.start_lo OR e.start_hi <> s.start_hi""").fetchone()[0]
            if orphan:
                raise StoreError('SERIES_ROWS_NOT_OF_THEIR_EVENTS', str(orphan))
            run('series', 'INSERT INTO series SELECT * FROM part_series')
            run('series', 'DROP TABLE part_series')
            run('events', 'INSERT INTO events SELECT * FROM part_events')
            run('event_concepts', 'INSERT INTO event_concepts SELECT * FROM part_concepts')
            run('events', 'DROP TABLE part_events')
            run('event_concepts', 'DROP TABLE part_concepts')
            run('event_evidence', f"""INSERT INTO event_evidence SELECT p.patient_idx, x.event_id, x.evidence, p.bucket
                FROM {staged('evidence', part)} x JOIN p USING (patient_id)""")
            run('coverage', f"""INSERT INTO coverage SELECT p.patient_idx, c.source, p.clock_id, c.lo, c.hi, c.basis, p.bucket
                FROM {staged('coverage', part)} c JOIN p USING (patient_id)""")
            # Identical excluded records (same patient, source, concept, start and reason) are one row with a count.
            run('excluded_records', f"""INSERT INTO excluded_records SELECT p.patient_idx, x.source, x.concept,
                x.start_lo, x.start_hi, x.reason, count(*)::BIGINT AS n, p.bucket
                FROM {staged('excluded', part)} x JOIN p USING (patient_id)
                GROUP BY p.patient_idx, x.source, x.concept, x.start_lo, x.start_hi, x.reason, p.bucket""")
        if on_stage:
            on_stage(f'derived:bucket={bucket}', {'parts': len(mine), **run.report()})
    run('source_concepts', """CREATE TABLE source_concepts AS SELECT DISTINCT source, concept,
        'inferred_from_records' AS basis FROM part_source_concepts""")
    run('source_concepts', 'DROP TABLE part_source_concepts')
    run('patient_index', 'DROP TABLE p')
    if on_stage:
        on_stage('derived:source_concepts', run.report())
    _check_types(db)


def _check_types(db):
    """Every table holds exactly its declared columns, each of an allowed type."""
    for name, columns in TABLES.items():
        found = [(c, t) for c, t, *_ in db.execute(f'DESCRIBE {name}').fetchall()]
        if name in PARTITIONED:
            found = [(c, t) for c, t in found if c != 'bucket']
        if found != list(columns) or any(t not in ALLOWED_TYPES for _, t in found):
            raise StoreError('TABLE_TYPES_NOT_ALLOWED', f'{name}: {found}')


def _write(db, snapshot):
    for name, columns in TABLES.items():
        cols = ', '.join(c for c, _ in columns)
        if name in PARTITIONED:
            target = snapshot / name
            target.mkdir()
            filled = [b for b, in db.execute(f'SELECT DISTINCT bucket FROM {name} ORDER BY bucket').fetchall()]
            if not filled:
                db.execute(f"COPY (SELECT {cols}, bucket FROM {name}) TO {_lit(target / 'empty.parquet')} (FORMAT parquet)")
            # One bucket at a time, in the layout PARTITION_BY (bucket) writes, so each sort holds one bucket.
            for bucket in filled:
                (target / f'bucket={bucket}').mkdir()
                db.execute(f"""COPY (SELECT {cols}, bucket FROM {name} WHERE bucket = {int(bucket)} ORDER BY {SORT[name]})
                               TO {_lit(target / f'bucket={bucket}' / 'data_0.parquet')} (FORMAT parquet)""")
        else:
            db.execute(f"COPY (SELECT {cols} FROM {name} ORDER BY {SORT[name]}) TO {_lit(snapshot / (name + '.parquet'))} (FORMAT parquet)")


def _attach(db, snapshot):
    """Views over a snapshot's Parquet files, one per logical table: the logical columns, and the
    bucket column on partitioned tables."""
    for name, columns in TABLES.items():
        cols = ', '.join(c for c, _ in columns)
        if name in PARTITIONED:
            files, cols = snapshot / name / '**' / '*.parquet', cols + ', bucket'
        else:
            files = snapshot / f'{name}.parquet'
        db.execute(f'CREATE OR REPLACE VIEW {name} AS SELECT {cols} FROM read_parquet({_lit(files)}, hive_partitioning = false)')


def _order(name):
    """ORDER BY of the canonical content: the table key, then every column, nulls last, strings binary."""
    columns = [c for c, _ in TABLES[name]]
    ordered = list(KEYS[name]) + [c for c in columns if c not in KEYS[name]]
    return ', '.join(f'{c} ASC NULLS LAST' for c in ordered)


def _slices(db, name, label, span):
    """The WHERE clauses, in canonical order, whose ordered rows concatenated are one bucket's ordered rows.

    An unpartitioned table is one query. A partitioned table's key starts with `patient_idx` (nulls last),
    so its bucket is cut into consecutive `patient_idx` ranges of `span` indices, the first open below and
    the last open above, then the rows with a null index: together they cover every row the file could
    hold, whatever its content, and each ordered query sorts one range, not the bucket (D24). A bucket
    whose largest index is below `span` is one query over the bucket, as before."""
    if name not in PARTITIONED:
        return [('', [])]
    bucket = int(label)
    top = db.execute(f'SELECT max(patient_idx) FROM {name} WHERE bucket = ?', [bucket]).fetchone()[0]
    if top is None or top < span:
        return [(' WHERE bucket = ?', [bucket])]
    cuts = list(range(span, top + 1, span))
    out = [(' WHERE bucket = ? AND patient_idx < ?', [bucket, cuts[0]])]
    out += [(' WHERE bucket = ? AND patient_idx >= ? AND patient_idx < ?', [bucket, lo, hi])
            for lo, hi in zip(cuts, cuts[1:])]
    out += [(' WHERE bucket = ? AND patient_idx >= ?', [bucket, cuts[-1]]),
            (' WHERE bucket = ? AND patient_idx IS NULL', [bucket])]
    return out


def content_digests(db, fetch_rows=FETCH_ROWS, on_table=None, span=None):
    """{table: {rows, content_digest, buckets: {label: {rows, content_digest}}}}, streamed from `db`.

    Each bucket is read by ordered queries (one, or one per `patient_idx` range of `span` indices for a
    large partitioned bucket; `_slices`) whose rows are fetched `fetch_rows` at a time and hashed as
    they arrive (CANONICAL_FORM), so Python holds one batch, never a table, and DuckDB sorts one range
    at a time. Any `fetch_rows` and any `span` give the same digests.
    """
    span = DIGEST_SPAN if span is None else span
    out = {}
    for name, columns in TABLES.items():
        cols = ', '.join(c for c, _ in columns)
        buckets = {}
        for label in bucket_labels(name):
            h, n = hashlib.sha256(), 0
            for where, params in _slices(db, name, label, span):
                cursor = db.execute(f'SELECT {cols} FROM {name}{where} ORDER BY {_order(name)}', params)
                while True:
                    rows = cursor.fetchmany(fetch_rows)
                    if not rows:
                        break
                    n += len(rows)
                    h.update(rows_text(rows).encode('utf-8'))
            buckets[label] = {'rows': n, 'content_digest': h.hexdigest()}
        out[name] = {'rows': sum(b['rows'] for b in buckets.values()), 'content_digest': table_digest(buckets),
                     'buckets': buckets}
        if on_table:
            on_table(name)
    return out


def _input_digest(db, header, provenance, fetch_rows):
    """INPUT_FORM, with the staged patient digests sorted by DuckDB and streamed."""
    h = hashlib.sha256((header_line(header, provenance) + '\n').encode('utf-8'))
    cursor = db.execute('SELECT patient_id, digest FROM s_patients ORDER BY patient_id ASC')
    while True:
        rows = cursor.fetchmany(fetch_rows)
        if not rows:
            break
        h.update(''.join(f'{pid}\t{d}\n' for pid, d in rows).encode('utf-8'))
    return h.hexdigest()


def file_digests(snapshot):
    """{relative path: sha256} of every Parquet file under a snapshot, each hashed as a stream."""
    snapshot = Path(snapshot)
    out = {}
    for path in sorted(snapshot.rglob('*.parquet')):
        with open(path, 'rb') as f:
            out[path.relative_to(snapshot).as_posix()] = hashlib.file_digest(f, 'sha256').hexdigest()
    return out


def builder_artifacts():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (MODULE_PATH, SCHEMA_PATH)}


def snapshot_identity(manifest, tables=None):
    """The digest that names a snapshot: canonical inputs, builder artifacts, per-table counts and content.

    `tables`, when given, replaces the manifest's table digests (to name what the files now hold)."""
    tables = manifest['tables'] if tables is None else tables
    identity = {'profile': manifest['profile'], 'input_digest': manifest['input_digest'],
                'closure_version': manifest['closure_version'], 'builder_artifacts': manifest['builder']['artifacts'],
                'tables': {n: {'rows': t['rows'], 'content_digest': t['content_digest']}
                           for n, t in sorted(tables.items())}}
    return _sha256(_json(identity))


def validate_manifest(manifest):
    schema = json.loads((ROOT / SCHEMA_PATH).read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: list(e.path))
    if errors:
        raise StoreError('INVALID_MANIFEST', errors[0].message)
    for name, table in manifest['tables'].items():
        if sorted(table['buckets']) != sorted(bucket_labels(name)) or table_digest(table['buckets']) != table['content_digest'] \
                or sum(b['rows'] for b in table['buckets'].values()) != table['rows']:
            raise StoreError('BUCKET_DIGEST_MISMATCH', name)
    if snapshot_identity(manifest) != manifest['snapshot_id']:
        raise StoreError('SNAPSHOT_ID_MISMATCH', manifest['snapshot_id'])
    return manifest


def _memory_limit(value):
    if value is not None and not (isinstance(value, str) and _MEMORY.match(value)):
        raise StoreError('INVALID_MEMORY_LIMIT', repr(value))
    return value


class StoreBuilder:
    """Build one snapshot from a stream of patients: `add()` each, then `finish(provenance)`.

    Python holds one patient at a time. Duplicate patient ids, an empty stream and anything
    `validate_patient` refuses are refused by name, and nothing is written under `root` on any
    refusal. Used as a context manager, leaving the block unfinished discards the build.
    `on_stage(name, info)`, when given, is called after each stage of `finish()`, and after each
    bucket of the derive with its statement times and DuckDB's memory accounting.

    `part_rows` bounds the staged rows of one derive part; it changes how the work is cut, never
    the snapshot. `work_dir`, when given, holds the staging files, the build database and DuckDB's spill files
    (for example on another disk); the snapshot itself is always written under `root`, so that
    publishing it is one rename. Each build works in `.building-*` directories, removed when the
    build ends however it ends; a killed process leaves them behind, and `sweep_stale_builds`
    removes old ones.

    `staging` (D26) is how `add()` stages rows: `'parquet'` (the default, `STAGING`) buffers each staged table's rows
    as typed columns, `stage_rows` at a time, and writes each batch as a Parquet file through an in-memory DuckDB
    connection; `'ndjson'` writes one JSON line per row. Both give the same tables, digests and snapshot.
    """

    def __init__(self, root, header, threads=1, memory_limit=DEFAULT_MEMORY_LIMIT, fetch_rows=FETCH_ROWS,
                 on_stage=None, work_dir=None, part_rows=PART_ROWS, staging=None, stage_rows=STAGE_ROWS,
                 stage_evidence_chars=STAGE_EVIDENCE_CHARS):
        self.header = validate_header(header)  # before anything is written
        self.ancestors = header['closure']['ancestors']
        self.threads, self.memory_limit, self.fetch_rows = threads, _memory_limit(memory_limit), fetch_rows
        if type(fetch_rows) is not int or fetch_rows < 1:
            raise StoreError('INVALID_FETCH_ROWS', repr(fetch_rows))
        if type(part_rows) is not int or part_rows < 1:
            raise StoreError('INVALID_PART_ROWS', repr(part_rows))
        self.part_rows = part_rows
        self.staging = STAGING if staging is None else staging
        if self.staging not in STAGINGS:
            raise StoreError('INVALID_STAGING', repr(staging))
        if type(stage_rows) is not int or stage_rows < 1:
            raise StoreError('INVALID_STAGE_ROWS', repr(stage_rows))
        self.stage_rows = stage_rows
        if type(stage_evidence_chars) is not int or stage_evidence_chars < 1:
            raise StoreError('INVALID_STAGE_EVIDENCE_CHARS', repr(stage_evidence_chars))
        self.stage_evidence_chars = stage_evidence_chars
        self.on_stage = on_stage
        self.root = Path(root)
        # Directories this builder creates, so that a refusal leaves no trace of them.
        self._created = [d for d in (self.root, *self.root.parents) if not d.exists()]
        self.root.mkdir(parents=True, exist_ok=True)
        # The snapshot is written here, beside its final place; everything else goes to `work`.
        self.publish = Path(tempfile.mkdtemp(prefix='.building-', dir=self.root))
        if work_dir is None:
            self.work = self.publish
        else:
            Path(work_dir).mkdir(parents=True, exist_ok=True)
            self.work = Path(tempfile.mkdtemp(prefix='.building-', dir=work_dir))
        self.stage = self.work / 'stage'
        self.stage.mkdir()
        # Parquet staging writes through its own small in-memory connection; the build database opens at finish().
        self._stage_db = None
        if self.staging != 'ndjson':
            (self.work / 'stage-spill').mkdir()
            self._stage_db = _connect(':memory:', 1, '256MB', self.work / 'stage-spill')
        self._files = {name: self._writer(name, self.stage / name) for name in STAGED if name not in PART_STAGED}
        # Every part staged so far, in order, and each bucket's open part: (part, {name: file}, staged rows).
        self.parts, self._current = [], {}
        self._stages = {}  # Parquet staging: (name, part) -> its stage, which lists its files
        self.patients = 0
        self.events = 0
        self._open = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self._open:
            self.abort()

    def _check_open(self):
        if not self._open:
            raise StoreError('BUILDER_CLOSED', str(self.root))

    def _writer(self, name, stem):
        """Where one staged table's rows go: an ndjson file, or a Parquet stage of numbered files."""
        if self.staging == 'ndjson':
            return open(f'{stem}.ndjson', 'w', encoding='utf-8')
        if name == 'evidence':
            return _ParquetStage(self._stage_db, stem, STAGED[name], self.stage_rows, 'evidence', self.stage_evidence_chars)
        return _ParquetStage(self._stage_db, stem, STAGED[name], self.stage_rows)

    def _put(self, name, row, files=None):
        target = (files or self._files)[name]
        if self.staging == 'ndjson':
            target.write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
        else:
            target.write(row)

    def staged_paths(self, name, parts=()):
        """The staging files of one staged table: the whole build's for the patient list and the closure, the given
        parts' for the others. Writers must be closed first."""
        if self.staging == 'ndjson':
            if name not in PART_STAGED:
                return [self.stage / f'{name}.ndjson']
            return [part_file(self.stage, name, part) for part in parts]
        if name not in PART_STAGED:
            return list(self._files[name].paths)
        return [path for part in parts for path in self._stages[name, part].paths]

    def relation(self, name, parts=()):
        """The SQL relation over one staged table's rows (`staged_paths`), with its STAGED columns."""
        return _staged_sql(self.staging, self.staged_paths(name, parts), STAGED[name])

    def _part(self, bucket):
        """The bucket's open part, opened when the bucket has none."""
        if bucket not in self._current:
            part = (bucket, sum(1 for b, _ in self.parts if b == bucket))
            self.parts.append(part)
            writers = {name: self._writer(name, part_file(self.stage, name, part, '')) for name in PART_STAGED}
            self._current[bucket] = [part, writers, 0]
            if self.staging != 'ndjson':
                self._stages.update({(name, part): w for name, w in writers.items()})
        return self._current[bucket]

    def _close_part(self, bucket):
        for f in self._current.pop(bucket)[1].values():
            f.close()

    def add(self, patient):
        """Validate one patient, stage its rows and its digest. Refusal discards the whole build."""
        self._check_open()
        try:
            validate_patient(patient, self.ancestors)
            canonical = canonical_patient(patient)
            pid = canonical['patient_id']
            staged = {'patient_id': pid, 'clock_id': canonical['clock_id'], 'digest': _sha256(_json(canonical))}
            if shares_variables(canonical['events']):
                staged['shared_variables'] = True
            self._put('patients', staged)
            bucket = bucket_of(pid)
            current = self._part(bucket)
            files, rows = current[1], 0
            for e in canonical['events']:
                end = e.get('end') or [None, None]
                row = {'patient_id': pid, 'event_id': e['event_id'], 'kind': e['kind'], 'source': e['source'],
                       'start_lo': e['start'][0], 'start_hi': e['start'][1], 'end_lo': end[0], 'end_hi': end[1]}
                for field in ('start_var', 'end_var', *RECORD_FIELDS):
                    if field in e:
                        row[field] = e[field]
                if e.get('proper'):
                    row['proper'] = True
                self._put('events', row, files)
                self._put('evidence', {'patient_id': pid, 'event_id': e['event_id'], 'evidence': _json(e['evidence'])},
                          files)
                for c in e['concepts']:
                    self._put('concepts', {'patient_id': pid, 'event_id': e['event_id'], 'concept': c}, files)
                rows += 1 + len(e['concepts'])  # an event and its evidence count as one row, as before D26
            if canonical.get('series'):
                starts = {e['event_id']: e['start'] for e in canonical['events']}
                for r in canonical['series']:
                    lo, hi = starts[r['event_id']]
                    self._put('series', {'patient_id': pid, 'event_id': r['event_id'], 'item_class': r['item_class'],
                                         'start_lo': lo, 'start_hi': hi, 'value_lexical': r['value_lexical'],
                                         'value_decimal': r['value_decimal'], 'value_micro': r['value_micro'],
                                         'unit': r['unit']}, files)
                rows += len(canonical['series'])
            for c in canonical['coverage']:
                self._put('coverage', {'patient_id': pid, **c}, files)
            for x in canonical.get('excluded', []):
                self._put('excluded', {'patient_id': pid, 'source': x['source'], 'concept': x['concept'],
                                       'start_lo': x['start'][0], 'start_hi': x['start'][1], 'reason': x['reason']},
                          files)
            current[2] += rows + len(canonical['coverage']) + len(canonical.get('excluded', []))
            if current[2] >= self.part_rows:  # parts end between patients: a patient is in exactly one part
                self._close_part(bucket)
            self.patients += 1
            self.events += len(canonical['events'])
        except BaseException:
            self.abort()
            raise

    def _stage_done(self, name, **info):
        if self.on_stage:
            self.on_stage(name, info)

    def finish(self, provenance=None):
        """Derive, digest, write, check content and publish; return the snapshot directory.

        An existing snapshot with the same identifier is left untouched and returned, once its
        files are shown by content to still hold it."""
        self._check_open()
        provenance = {} if provenance is None else provenance
        try:
            validate_provenance(provenance)
            _require(self.patients > 0, 'NO_PATIENTS')
            for row in _closure_rows(self.header['closure']):
                self._put('closure', row)
            for f in self._files.values():
                f.close()
            for bucket in list(self._current):
                self._close_part(bucket)
            if self._stage_db is not None:
                self._stage_db.close()
                self._stage_db = None
            self._stage_done('staged', patients=self.patients, events=self.events, parts=len(self.parts),
                             staged_bytes=sum(f.stat().st_size for f in self.stage.iterdir()),
                             staged_files=sum(1 for _ in self.stage.iterdir()), staging=self.staging)
            spill = self.work / 'spill'
            spill.mkdir()
            duckdb = _duckdb()
            db = _connect(self.work / 'build.duckdb', self.threads, self.memory_limit, spill)
            try:
                _load_patients(db, self.relation)
                input_digest = _input_digest(db, self.header, provenance, self.fetch_rows)
                self._stage_done('patients_loaded_and_input_digest')
                _derive(db, self.relation, self.parts, self.on_stage)
                db.execute('DROP TABLE s_patients')
                shutil.rmtree(self.stage)
                self._stage_done('derived', database_bytes=(self.work / 'build.duckdb').stat().st_size)
                tables = content_digests(db, self.fetch_rows, lambda name: self._stage_done(f'content_digest:{name}'))
                snapshot = self.publish / 'snapshot'
                snapshot.mkdir()
                _write(db, snapshot)
            except duckdb.Error as exc:
                raise StoreError('DUCKDB_BUILD_FAILED', str(exc).splitlines()[0]) from exc
            finally:
                db.close()
            files = file_digests(snapshot)
            self._stage_done('parquet_written_and_hashed', snapshot_bytes=sum((snapshot / p).stat().st_size for p in files))
            manifest = {'profile': PROFILE, 'snapshot_id': '', 'dataset_id': self.header['dataset_id'],
                        'input_digest': input_digest, 'closure_version': self.header['closure']['version'],
                        'time': {'unit': 'microsecond', 'origin': ORIGIN, 'calendar': 'patient_local'},
                        'buckets': BUCKETS, 'canonical_form': CANONICAL_FORM, 'input_form': INPUT_FORM,
                        'selection': provenance.get('selection'), 'labels': provenance.get('labels'),
                        'builder': {'artifacts': builder_artifacts(), 'engine': 'duckdb',
                                    'engine_version': duckdb.__version__, 'memory_limit': self.memory_limit},
                        'tables': {name: {**tables[name], 'columns': [{'name': c, 'type': t} for c, t in columns],
                                          'path': name if name in PARTITIONED else f'{name}.parquet',
                                          'partitioned_by': 'bucket' if name in PARTITIONED else None}
                                   for name, columns in TABLES.items()},
                        'files': files}
            manifest['snapshot_id'] = snapshot_identity(manifest)
            validate_manifest(manifest)
            (snapshot / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
            # The files as written must hold what was derived, before anyone can open them.
            Store(snapshot, self.threads, 'content', self.memory_limit, self.fetch_rows).close()
            self._stage_done('content_verified')
            target = self.root / manifest['snapshot_id']
            if target.exists():
                # Reuse only a snapshot whose files still hold its identifier's content.
                with Store(target, self.threads, 'content', self.memory_limit, self.fetch_rows) as existing:
                    if existing.snapshot_id != manifest['snapshot_id']:
                        raise StoreError('SNAPSHOT_DIRECTORY_CONFLICT', str(target))
            else:
                os.replace(snapshot, target)
            self._stage_done('published')
            self._close(keep_root=True)
            return target
        except BaseException:
            self.abort()
            raise

    def _close(self, keep_root):
        self._open = False
        if self.staging == 'ndjson':
            for f in self._files.values():
                f.close()
            for bucket in list(self._current):
                self._close_part(bucket)
        else:  # a Parquet stage's buffered rows are dropped, not written
            self._current.clear()
            if self._stage_db is not None:
                self._stage_db.close()
                self._stage_db = None
        shutil.rmtree(self.work, ignore_errors=True)
        shutil.rmtree(self.publish, ignore_errors=True)
        if not keep_root:
            for d in self._created:  # deepest first
                try:
                    d.rmdir()
                except OSError:
                    break

    def abort(self):
        """Discard the build: staging, database and any unpublished snapshot."""
        if self._open:
            self._close(keep_root=False)


def build(ingest, root, threads=1, memory_limit=DEFAULT_MEMORY_LIMIT, fetch_rows=FETCH_ROWS, on_stage=None,
          work_dir=None, part_rows=PART_ROWS, staging=None, stage_rows=STAGE_ROWS,
          stage_evidence_chars=STAGE_EVIDENCE_CHARS):
    """Validate, derive and write one immutable snapshot of a whole ingest document under `root`;
    return its directory. A wrapper over `StoreBuilder`: the patients are streamed from the list."""
    header = _validate_document(ingest)
    with StoreBuilder(root, header, threads, memory_limit, fetch_rows, on_stage, work_dir, part_rows, staging,
                      stage_rows, stage_evidence_chars) as builder:
        for p in ingest['patients']:
            builder.add(p)
        return builder.finish(ingest.get('provenance'))


def sweep_stale_builds(directory, older_than_seconds=24 * 3600, now=None):
    """Remove `.building-*` directories under `directory` (a store root or a `work_dir`) last
    modified more than `older_than_seconds` ago: what a killed build leaves behind. A live build
    touches its directory as it works, but one stalled longer than the threshold would be removed
    too, so choose a threshold longer than any build. Returns the paths removed."""
    import time
    now = time.time() if now is None else now
    removed = []
    for path in sorted(Path(directory).glob('.building-*')):
        if path.is_dir() and now - path.stat().st_mtime > older_than_seconds:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed


class Store:
    """A read-only view of one snapshot.

    Opening validates the manifest (its identifier must equal its own digest) and then verifies
    at the level asked: `'files'` (the default) checks that the Parquet files are exactly those the
    manifest lists, each with its recorded sha256; `'content'` re-derives every table's per-bucket
    content digests from the files, streamed, and compares them with the manifest's; `False` skips
    both, explicitly. `match` reports the level (`verified`).

    File digests are outside the identity, so the `files` level catches corruption, partial copies
    and edited files, but not a coordinated edit of a Parquet file and its manifest entry. Three
    checks cover that: `content`; `expected_snapshot_id`, which refuses any other identifier; and
    the directory name, which, when it is a snapshot identifier, must be this one.
    """

    def __init__(self, path, threads=1, verify='files', memory_limit=None, fetch_rows=FETCH_ROWS,
                 expected_snapshot_id=None):
        if not (verify is False or verify in ('files', 'content')):
            raise StoreError('INVALID_VERIFY_LEVEL', repr(verify))
        self.path = Path(path)
        self.fetch_rows = fetch_rows
        self.memory_limit = memory_limit
        try:
            manifest = json.loads((self.path / 'manifest.json').read_text())
        except (OSError, ValueError) as exc:
            raise StoreError('SNAPSHOT_UNREADABLE', str(self.path)) from exc
        self.manifest = validate_manifest(manifest)
        self.snapshot_id = self.manifest['snapshot_id']
        if _SNAPSHOT_NAME.fullmatch(self.path.name) and self.path.name != self.snapshot_id:
            raise StoreError('SNAPSHOT_DIRECTORY_MISMATCH', f'{self.path.name} holds {self.snapshot_id}')
        if expected_snapshot_id is not None and expected_snapshot_id != self.snapshot_id:
            raise StoreError('SNAPSHOT_ID_NOT_EXPECTED', f'expected {expected_snapshot_id}, found {self.snapshot_id}')
        # Which exact files this view reads: recorded in every match context.
        self.files_digest = _sha256(_json(self.manifest['files']))
        self.verified = False
        if verify == 'files':  # before DuckDB reads any file, so a damaged one is named as such
            self.verify_files()
        duckdb = _duckdb()
        self._spill = Path(tempfile.mkdtemp(prefix='execution-store-spill-'))
        self.db = _connect(':memory:', threads, _memory_limit(memory_limit), self._spill)
        try:
            _attach(self.db, self.path)
            if verify == 'content':
                self.verify_content()
            self.verified = verify
        except duckdb.Error as exc:
            self.close()
            raise StoreError('SNAPSHOT_UNREADABLE', str(exc).splitlines()[0]) from exc
        except StoreError:
            self.close()
            raise

    def close(self):
        self.db.close()
        shutil.rmtree(self._spill, ignore_errors=True)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def rows(self, table):
        """Every row of a logical table in column order, sorted. Holds the table: for tests."""
        cols = ', '.join(c for c, _ in TABLES[table])
        return sorted(self.db.execute(f'SELECT {cols} FROM {table}').fetchall(),
                      key=lambda r: json.dumps(list(r), ensure_ascii=False))

    def verify_files(self):
        """The Parquet files are exactly the manifest's, byte for byte (integrity, not identity)."""
        recorded, found = self.manifest['files'], file_digests(self.path)
        for path in sorted(set(recorded) | set(found)):
            if recorded.get(path) != found.get(path):
                raise StoreError('SNAPSHOT_FILE_MISMATCH', path)
        return True

    def derived_tables(self):
        """Each table's counts and content digests as the files now hold them, streamed."""
        return content_digests(self.db, self.fetch_rows)

    def derived_snapshot_id(self):
        """The identifier the files' content would give, beside the manifest's inputs and builder."""
        return snapshot_identity(self.manifest, self.derived_tables())

    def verify_content(self):
        """Re-derive every table's per-bucket digests from the files; refuse on any drift.

        A store opened without a memory limit verifies under the build's (`builder.memory_limit` in the
        manifest), and then returns to DuckDB's default, so content verification never takes DuckDB's
        default of most of the machine's memory (D24)."""
        limit = None if self.memory_limit is not None else self.manifest['builder'].get('memory_limit')
        if limit is not None:
            self.db.execute(f"SET memory_limit = '{_memory_limit(limit)}'")
        try:
            derived = self.derived_tables()
        finally:
            if limit is not None:
                self.db.execute('RESET memory_limit')
        for name, recorded in self.manifest['tables'].items():
            mine = derived[name]
            for label, bucket in recorded['buckets'].items():
                if mine['buckets'][label] != bucket:
                    raise StoreError('SNAPSHOT_CONTENT_MISMATCH', f'{name}/{label}')
        if snapshot_identity(self.manifest, derived) != self.snapshot_id:  # pragma: no cover - implied by the above
            raise StoreError('SNAPSHOT_ID_MISMATCH', self.snapshot_id)
        return True

    def verify(self, level='content'):
        """Verify at `level` ('files' or 'content'); record it in `verified` and return True."""
        if level not in ('files', 'content'):
            raise StoreError('INVALID_VERIFY_LEVEL', repr(level))
        self.verify_content() if level == 'content' else self.verify_files()
        self.verified = level
        return True

    def query(self, sql, params=()):
        return self.db.execute(sql, list(params)).fetchall()

    def evidence(self, patient_id):
        """{event_id: evidence} of one patient's events, each the object the ingest gave, read from `event_evidence`
        (D26), which matching never reads. An unknown patient has none."""
        found = self.query('SELECT patient_idx, bucket FROM patients WHERE patient_id = ?', [patient_id])
        if not found:
            return {}
        (idx, bucket), = found
        return {eid: json.loads(text) for eid, text in self.query(
            'SELECT event_id, evidence FROM event_evidence WHERE bucket = ? AND patient_idx = ? ORDER BY event_id',
            [bucket, idx])}


    def series(self, patient_id, event_ids=None):
        """One patient's series rows (D27) as dicts in key order (item class, time, event), all of them or those of
        the given events; for display and audit. Matching reads the table only for value conditions (D28)."""
        found = self.query('SELECT patient_idx, bucket FROM patients WHERE patient_id = ?', [patient_id])
        if not found:
            return []
        (idx, bucket), = found
        names = [c for c, _ in TABLES['series']][1:]
        rows = self.query(f"SELECT {', '.join(names)} FROM series WHERE bucket = ? AND patient_idx = ? "
                          f"ORDER BY item_class, start_lo, start_hi, event_id", [bucket, idx])
        wanted = None if event_ids is None else set(event_ids)
        return [dict(zip(names, r)) for r in rows if wanted is None or r[0] in wanted]


def open_store(path, threads=1, verify='files', memory_limit=None, fetch_rows=FETCH_ROWS, expected_snapshot_id=None):
    return Store(path, threads, verify, memory_limit, fetch_rows, expected_snapshot_id)


# Matching path.

def _step_concepts(query):
    """{step: concepts the engine may bind it by}: its own and every widening in a tried relaxation set."""
    out = {s['id']: [s['concept']] for s in query['steps']}
    for _, chosen in tg.relaxation_sets(query):
        for x in chosen:
            if x['kind'] == 'widen_concept' and x['concept'] not in out[x['step']]:
                out[x['step']].append(x['concept'])
    return out


def _in(values):
    return '(' + ', '.join('?' for _ in values) + ')'


def _known(store, concepts):
    found = {r[0] for r in store.query(f'SELECT DISTINCT concept FROM concept_closure WHERE concept IN {_in(concepts)}',
                                        concepts)}
    unknown = sorted(set(concepts) - found)
    if unknown:
        raise StoreError('UNKNOWN_QUERY_CONCEPT', ', '.join(unknown))


def _visible_to_exclusions(store, concepts):
    """Refuse structural query concepts on a snapshot holding excluded records.

    An excluded record carries a source concept and that concept's closure, but not the structural
    classes (labels `structural_concept_basis`) that its events would have had. A query on such a
    class could not see the excluded records, so its `none` would rest on what it could not see.
    """
    basis = (store.manifest.get('labels') or {}).get('structural_concept_basis')
    if basis is None or not store.query('SELECT count(*) FROM excluded_records')[0][0]:
        return
    structural = sorted(r[0] for r in store.query(
        f"SELECT concept FROM concept_closure WHERE basis = 'self' AND concept_basis = ? AND concept IN {_in(concepts)}",
        [basis] + list(concepts)))
    if structural:
        raise StoreError('STRUCTURAL_CONCEPT_EXCLUSION_UNKNOWN', ', '.join(structural))


# Value conditions (D28, chartevents stage 2). A store match request may carry `conditions`: per step, a comparison or
# a range on the values of the step's concept's `series` rows, in one literal unit, and value relaxations that widen
# one. Each condition is compiled into a per-query tag concept: an event carries the tag when it has a series row whose
# item class the step's concept covers (reflexively, through the closure), whose unit equals the condition's unit
# exactly, and whose value satisfies the condition, compared exactly. The engine receives the tag as the step's
# concept and is unchanged; tags are not in any table, so they are outside the snapshot identity.

CONDITIONS_PROFILE = 'execution-store-conditions-1.0'
CONDITIONS_PROFILE_1_1 = 'execution-store-conditions-1.1'  # D32: Nth occurrence and counts
CONDITIONS_PROFILE_1_2 = 'execution-store-conditions-1.2'  # D33: rises and falls (relative conditions)
CONDITIONS_PROFILES = (CONDITIONS_PROFILE, CONDITIONS_PROFILE_1_1, CONDITIONS_PROFILE_1_2)
_VALUE_KEYS = {'unit', 'eq', 'gt', 'ge', 'lt', 'le'}
VALUE_RULE = 'series_unit_exact_value_v1'
_LOWER, _UPPER = ('gt', 'ge'), ('lt', 'le')
_TAG_PREFIX = 'value-condition:'


def _bound_value(text, where):
    from . import series_values as sv
    try:
        return sv.decimal_of(text)
    except sv.SeriesValueError:
        raise StoreError('INVALID_CONDITION_VALUE', where) from None


def _condition(raw, where):
    """(unit, lower, upper) of a condition, each bound (Decimal, closed) or None, refused by name when malformed or
    empty. A condition is `{unit, eq}` or `{unit}` with at most one of `gt`, `ge` and at most one of `lt`, `le`, at least
    one bound in all; values are exact decimal strings in the measurement decimal rule (`series_values.decimal_of`)."""
    if not (isinstance(raw, dict) and 'unit' in raw and set(raw) <= {'unit', 'eq', *_LOWER, *_UPPER}):
        raise StoreError('INVALID_CONDITION', where)
    unit = raw['unit']
    if not (type(unit) is str and unit and unit.strip() == unit):
        raise StoreError('INVALID_CONDITION', f'{where}: unit')
    lows, highs = [k for k in _LOWER if k in raw], [k for k in _UPPER if k in raw]
    if 'eq' in raw:
        if lows or highs:
            raise StoreError('INVALID_CONDITION', f'{where}: eq with another bound')
        value = _bound_value(raw['eq'], where)
        return unit, (value, True), (value, True)
    if len(lows) > 1 or len(highs) > 1 or not (lows or highs):
        raise StoreError('INVALID_CONDITION', f'{where}: bounds')
    lower = (_bound_value(raw[lows[0]], where), lows[0] == 'ge') if lows else None
    upper = (_bound_value(raw[highs[0]], where), highs[0] == 'le') if highs else None
    if lower and upper and (lower[0] > upper[0] or (lower[0] == upper[0] and not (lower[1] and upper[1]))):
        raise StoreError('EMPTY_CONDITION', where)
    return unit, lower, upper


def _decimal_text(value):
    """A value's shortest exact decimal text: trailing zeros stripped from its digits, then `format(..., 'f')`, which
    does not round. `Decimal.normalize` would round to the context's 28 digits, while a condition's value may have 64,
    so two different conditions could share one tag (D28 review). Every zero is '0'."""
    from decimal import Decimal
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    if digits == [0]:
        return '0'
    return format(Decimal((sign, tuple(digits), exponent)), 'f')


def condition_form(condition):
    """A compiled condition as a normal request condition: `eq` for a point, else `ge`/`gt` and `le`/`lt`, with each
    value in its shortest exact decimal form."""
    unit, lower, upper = condition
    if lower and upper and lower == upper:
        return {'unit': unit, 'eq': _decimal_text(lower[0])}
    out = {'unit': unit}
    if lower:
        out['ge' if lower[1] else 'gt'] = _decimal_text(lower[0])
    if upper:
        out['le' if upper[1] else 'lt'] = _decimal_text(upper[0])
    return out


def contains(outer, inner):
    """Whether every value `inner` admits, `outer` admits too, in the same unit: so the tag of `outer` holds every event
    the tag of `inner` holds, for one concept (the containment a value relaxation needs)."""
    if outer[0] != inner[0]:
        return False
    (_, olo, ohi), (_, ilo, ihi) = outer, inner
    lower_ok = olo is None or (ilo is not None and (olo[0] < ilo[0] or (olo[0] == ilo[0] and (olo[1] or not ilo[1]))))
    upper_ok = ohi is None or (ihi is not None and (ohi[0] > ihi[0] or (ohi[0] == ihi[0] and (ohi[1] or not ihi[1]))))
    return lower_ok and upper_ok


def tag_name(concept, condition):
    """A tag's concept name: a digest of the concept and the condition's normal form, so equal conditions share one."""
    return _TAG_PREFIX + _sha256(_json([concept, condition_form(condition)]))


class Request:
    """A store match request compiled: the engine query, with each conditioned step's concept and widenings replaced by
    tags and each value relaxation by a `widen_concept` to a tag; `tags` maps each tag to (concept, condition);
    `base` maps each step to the concepts the engine may bind it by, read as concepts (a tag by its concept), which
    the excluded-records rule and the concept checks use; `conditions` is the request's own, for the context.

    Profile `execution-store-conditions-1.1` (D32) adds, per step, an Nth or Nth-from-last occurrence (`occurrence:
    {nth: N}` or `{nth_last: N}`) or a count (`count: {at_least: N, within: {span: micros} | "window"}`), beside or
    without a value condition, and the relaxation `widen_count`. Each is compiled into a derived tag
    (`derived-step:<step>/<own or relaxation id>`), whose events the store derives per patient after loading (`derive_patient`):
    `derived` maps each derived tag to its spec, `kinds` each derived step to ('nth', which) or ('count', within),
    `load` each step to the concepts its events are loaded by (a derived tag by the concept or value tag it counts),
    and `derived_used` the derived tags some relaxation set the engine would try names. A 1.0 request is compiled as
    before D32, key for key."""

    def __init__(self, query, conditions):
        tg.validate_query(query)
        if not (isinstance(conditions, dict) and conditions.get('profile') in CONDITIONS_PROFILES
                and set(conditions) <= {'profile', 'steps', 'relaxations'} and 'steps' in conditions):
            raise StoreError('INVALID_CONDITIONS', 'profile, steps and relaxations')
        self.profile = conditions['profile']
        later = self.profile != CONDITIONS_PROFILE
        steps = {s['id']: s for s in query['steps']}
        given = conditions['steps']
        if not (isinstance(given, dict) and given):
            raise StoreError('INVALID_CONDITIONS', 'steps')
        unknown = sorted(str(k) for k in set(given) - set(steps))
        if unknown:
            raise StoreError('UNKNOWN_CONDITION_STEP', ', '.join(unknown))
        own, derived_of = {}, {}
        relative_ok = self.profile == CONDITIONS_PROFILE_1_2
        allowed = _VALUE_KEYS | {'occurrence', 'count'} | ({'relative'} if relative_ok else set())
        for sid, raw in given.items():
            if not later:
                own[sid] = _condition(raw, sid)
                continue
            if not (isinstance(raw, dict) and raw and set(raw) <= allowed):
                raise StoreError('INVALID_CONDITION', sid)
            value = {k: v for k, v in raw.items() if k in _VALUE_KEYS}
            if 'relative' in raw:  # D33
                derived_of[sid] = _relative_spec(raw, sid, steps[sid])
                continue
            if value:
                own[sid] = _condition(value, sid)
            if 'occurrence' in raw or 'count' in raw:
                derived_of[sid] = _derived_spec(raw, sid, steps[sid], query)
        # D33: a relative step reads the events with a series row of its concept in the condition's unit, which the
        # value tag of the unit alone (no bound) holds exactly; it is the step's concept for loading, candidates,
        # pruning and coverage, as a value tag is for a counted value condition.
        units = {sid: (spec[1]['unit'], None, None) for sid, spec in derived_of.items() if spec[0] == 'relative'}
        self.tags = {}

        def tag(concept, condition):
            name = tag_name(concept, condition)
            self.tags[name] = (concept, condition)
            return name
        compiled = copy.deepcopy(query)
        for step in compiled['steps']:
            if step['id'] in own:
                step['concept'] = tag(steps[step['id']]['concept'], own[step['id']])
            elif step['id'] in units:
                step['concept'] = tag(steps[step['id']]['concept'], units[step['id']])
        relaxations = []
        for x in compiled.get('relaxations', []):
            if x['kind'] == 'widen_concept' and x['step'] in own:
                x = {**x, 'concept': tag(x['concept'], own[x['step']])}  # the step's condition on the wider concept
            elif x['kind'] == 'widen_concept' and x['step'] in units:
                x = {**x, 'concept': tag(x['concept'], units[x['step']])}
            relaxations.append(x)
        ids = {x['id'] for x in relaxations}
        given = conditions.get('relaxations', [])
        if not isinstance(given, list):
            raise StoreError('INVALID_CONDITIONS', 'relaxations')
        counts, factors = [], []
        for x in given:
            if relative_ok and isinstance(x, dict) and x.get('kind') == 'widen_factor':  # D33
                if not (set(x) == {'id', 'kind', 'step', 'cost', 'factor'} and type(x['id']) is str and x['id']
                        and type(x['step']) is str):
                    raise StoreError('INVALID_FACTOR_RELAXATION', repr(x)[:120])
                if x['id'] in ids:
                    raise StoreError('DUPLICATE_RELAXATION', x['id'])
                ids.add(x['id'])
                spec = derived_of.get(x['step'])
                if spec is None or spec[0] != 'relative':
                    raise StoreError('FACTOR_RELAXATION_STEP_NOT_RELATIVE', str(x['step']))
                factor = _factor(x['factor'], x['id'])
                # A smaller factor (still above 1) admits every reading the step's factor admits, at both strengths;
                # a larger one would not, and is refused (`_D33_DEFECT == 'relaxation_not_containing'` skips this).
                if not factor < _decimal_of(spec[1]['factor']) and _D33_DEFECT != 'relaxation_not_containing':
                    raise StoreError('FACTOR_RELAXATION_MUST_WIDEN', x['id'])
                factors.append({**x, 'factor': _decimal_text(factor)})
                continue
            if later and isinstance(x, dict) and x.get('kind') == 'widen_count':
                if not (set(x) <= {'id', 'kind', 'step', 'cost', 'at_least', 'concept'}
                        and {'id', 'kind', 'step', 'cost', 'at_least'} <= set(x) and type(x['id']) is str and x['id']
                        and type(x['step']) is str and ('concept' not in x or (type(x['concept']) is str and x['concept']))):
                    raise StoreError('INVALID_COUNT_RELAXATION', repr(x)[:120])
                if x['id'] in ids:
                    raise StoreError('DUPLICATE_RELAXATION', x['id'])
                ids.add(x['id'])
                spec = derived_of.get(x['step'])
                if spec is None or spec[0] != 'count':
                    raise StoreError('COUNT_RELAXATION_STEP_NOT_COUNTED', str(x['step']))
                n = x['at_least']
                if not (type(n) is int and 1 <= n < spec[1]):
                    raise StoreError('COUNT_RELAXATION_MUST_NARROW', x['id'])
                counts.append(x)
                continue
            if not (isinstance(x, dict) and set(x) == {'id', 'kind', 'step', 'cost', 'condition'}
                    and x['kind'] == 'widen_value' and type(x['id']) is str and x['id'] and type(x['step']) is str):
                raise StoreError('INVALID_VALUE_RELAXATION', repr(x)[:120])
            if x['id'] in ids:
                raise StoreError('DUPLICATE_RELAXATION', x['id'])
            ids.add(x['id'])
            if x['step'] not in own:
                raise StoreError('VALUE_RELAXATION_STEP_NOT_CONDITIONED', str(x['step']))
            wider = _condition(x['condition'], x['id'])
            if not contains(wider, own[x['step']]):
                raise StoreError('VALUE_RELAXATION_MUST_WIDEN', x['id'])
            relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                'concept': tag(steps[x['step']]['concept'], wider)})
        # D32: each derived step's concept, and each widening of it, becomes the derived tag of that concept (or value
        # tag) under the step's occurrence or count; a `widen_count` becomes a widening to the count tag of the smaller
        # N, on the step's concept or on the wider concept it names (one relaxation per step, as the engine allows).
        self.derived, self.kinds, self.step_n = {}, {}, {}

        def derived_tag(sid, concept, variant, n=None, factor=None):
            # The tag's name, which is also its Nth point's event id, names the step and what introduced the variant
            # (`own`, or the relaxation's id), so a reader reproduces it from the request alone.
            spec = derived_of[sid]
            if spec[0] == 'nth':
                form = {'kind': 'nth', 'concept': concept, 'which': spec[1], 'n': spec[2]}
            elif spec[0] == 'relative':  # D33: `concept` is the unit tag of the step's (or the wider) concept
                form = {'kind': 'relative', 'concept': concept,
                        'relative': {**spec[1], 'factor': spec[1]['factor'] if factor is None else factor}}
            else:
                form = {'kind': 'count', 'concept': concept, 'n': spec[1] if n is None else n, 'within': spec[2]}
            name = f'{_DERIVED_PREFIX}{sid}/{variant}'
            # D32 review: a relaxation named `own`, or ids holding '/' across steps (step A with relaxation `r/own`,
            # step `A/r` with its own tag), would give two tags one name and the later would silently replace the
            # earlier. Own tags are named first, so this one check refuses every such request.
            if name in self.derived:
                raise StoreError('DERIVED_TAG_NAME_COLLISION', name)
            self.derived[name] = form
            self.step_n[name] = spec[2] if spec[0] == 'nth' else 1 if spec[0] == 'relative' else spec[1]
            return name
        if derived_of:
            for sid, spec in derived_of.items():
                self.kinds[sid] = (('nth', spec[1]) if spec[0] == 'nth' else ('relative', spec[1]['kind'])
                                   if spec[0] == 'relative' else ('count', spec[2]))
            for step in compiled['steps']:
                if step['id'] in derived_of:
                    step['concept'] = derived_tag(step['id'], step['concept'], 'own')
            relaxations = [{**x, 'concept': derived_tag(x['step'], x['concept'], x['id'])}
                           if x['kind'] == 'widen_concept' and x['step'] in derived_of else x for x in relaxations]
            for x in counts:
                concept = x.get('concept', steps[x['step']]['concept'])
                if x['step'] in own:
                    concept = tag(concept, own[x['step']])
                relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                    'concept': derived_tag(x['step'], concept, x['id'], x['at_least'])})
            for x in factors:  # D33: the relative tag of the step's concept under the smaller factor
                concept = tag(steps[x['step']]['concept'], units[x['step']])
                relaxations.append({'id': x['id'], 'kind': 'widen_concept', 'step': x['step'], 'cost': x['cost'],
                                    'concept': derived_tag(x['step'], concept, x['id'], factor=x['factor'])})
        if relaxations or 'relaxations' in compiled:
            compiled['relaxations'] = relaxations
        self.query = tg.validate_query(compiled)
        self.conditions = conditions
        per_step = _step_concepts(self.query)
        # What each engine concept is loaded by: a derived tag by the concept or value tag it counts.
        self.load = {sid: list(dict.fromkeys(self.derived[c]['concept'] if c in self.derived else c for c in cs))
                     for sid, cs in per_step.items()}
        self.base = {sid: list(dict.fromkeys(self.tags[c][0] if c in self.tags else c for c in cs))
                     for sid, cs in self.load.items()}
        # The tags some relaxation set the engine would try names (a relaxation beyond the budget is never tried).
        used = {c for cs in self.load.values() for c in cs}
        self.used = {name: spec for name, spec in self.tags.items() if name in used}
        self.derived_used = {name: self.derived[name] for name in dict.fromkeys(c for cs in per_step.values() for c in cs)
                             if name in self.derived}

    def describe(self):
        """The tags, for the match output: each tag's concept and condition in normal form."""
        return {'profile': self.profile, 'rule': VALUE_RULE,
                'tags': {name: {'concept': c, 'condition': condition_form(k)} for name, (c, k) in sorted(self.tags.items())}}

    def describe_derived(self):
        """The derived steps (D32), for the match output: the rules and each derived tag's spec; with a relative
        condition (D33), also the relative rules."""
        out = {'profile': self.profile, 'rules': DERIVED_RULES,
               'steps': {sid: {'occurrence': kind[1]} if kind[0] == 'nth' else {'relative': kind[1]}
                         if kind[0] == 'relative' else {'count_within': kind[1]}
                         for sid, kind in sorted(self.kinds.items())},
               'tags': {name: spec for name, spec in sorted(self.derived.items())}}
        if self.relative:
            out['relative_rules'] = RELATIVE_RULES
        return out

    @property
    def relative(self):
        """Whether some step carries a relative condition (D33)."""
        return any(kind[0] == 'relative' for kind in self.kinds.values())


def _decimal_of(text):
    from . import series_values as sv
    return sv.decimal_of(text)


def _factor(text, where):
    """A factor: a decimal string, in the measurement decimal rule, greater than 1 (D33)."""
    from . import series_values as sv
    try:
        value = sv.decimal_of(text)
    except sv.SeriesValueError:
        raise StoreError('INVALID_FACTOR', where) from None
    if not value > 1:
        raise StoreError('INVALID_FACTOR', where)
    return value


def _relative_spec(raw, sid, step):
    """('relative', {kind, factor, unit, floor, previous, within}) for a 1.2 step entry, each decimal in its shortest
    exact form, refused by name when malformed or in conflict with the step (D33)."""
    from . import series_values as sv
    if any(k in raw for k in _VALUE_KEYS):
        raise StoreError('RELATIVE_WITH_ABSOLUTE', sid)
    # A first, last or Nth over a relative condition's events would pick a different reading in each pass.
    if 'occurrence' in raw or 'count' in raw or step.get('occurrence', 'any') != 'any':
        raise StoreError('OCCURRENCE_ON_DERIVED_STEP', sid)
    rel = raw['relative']
    if not (isinstance(rel, dict) and set(rel) == {'kind', 'factor', 'unit', 'floor', 'baseline'}
            and rel['kind'] in ('rise', 'fall')):
        raise StoreError('INVALID_RELATIVE', sid)
    unit, base = rel['unit'], rel['baseline']
    if not (type(unit) is str and unit and unit.strip() == unit):
        raise StoreError('INVALID_RELATIVE', f'{sid}: unit')
    if not (isinstance(base, dict) and set(base) == {'previous', 'within'} and type(base['previous']) is int
            and 1 <= base['previous'] <= RELATIVE_MAX_PREVIOUS and type(base['within']) is int and base['within'] >= 1):
        raise StoreError('INVALID_RELATIVE', f'{sid}: baseline')
    factor = _factor(rel['factor'], sid)
    try:
        floor = sv.decimal_of(rel['floor'])
    except sv.SeriesValueError:
        raise StoreError('INVALID_RELATIVE', f'{sid}: floor') from None
    if not floor > 0:
        raise StoreError('INVALID_RELATIVE', f'{sid}: floor')
    return ('relative', {'kind': rel['kind'], 'factor': _decimal_text(factor), 'unit': unit,
                         'floor': _decimal_text(floor), 'previous': base['previous'], 'within': base['within']})


def _derived_spec(raw, sid, step, query):
    """('nth', 'nth' or 'nth_last', N) or ('count', N, span in microseconds or 'window') for a 1.1 step entry, refused
    by name when malformed or in conflict with the step (D32)."""
    if 'occurrence' in raw and 'count' in raw:
        raise StoreError('OCCURRENCE_ON_DERIVED_STEP', sid)
    # A first, last or Nth over a count's events would pick a different record in each pass of the two-pass rule.
    if step.get('occurrence', 'any') != 'any' and not (_D32_DEFECT == 'occurrence_on_derived' and 'count' in raw):
        raise StoreError('OCCURRENCE_CONFLICT' if 'occurrence' in raw else 'OCCURRENCE_ON_DERIVED_STEP', sid)
    if 'occurrence' in raw:
        occ = raw['occurrence']
        if not (isinstance(occ, dict) and len(occ) == 1 and next(iter(occ)) in ('nth', 'nth_last')):
            raise StoreError('INVALID_OCCURRENCE', sid)
        which, n = next(iter(occ.items()))
        if not (type(n) is int and n >= 1):
            raise StoreError('INVALID_OCCURRENCE', sid)
        if any(r['kind'] == 'during' and r['from'] == sid for r in query.get('relations', [])):
            raise StoreError('NTH_STEP_HAS_NO_END', sid)
        return ('nth', which, n)
    count = raw['count']
    if not (isinstance(count, dict) and set(count) == {'at_least', 'within'}):
        raise StoreError('INVALID_COUNT', sid)
    n, within = count['at_least'], count['within']
    if not (type(n) is int and n >= 1):
        raise StoreError('INVALID_COUNT', sid)
    if within != 'window':
        if not (isinstance(within, dict) and set(within) == {'span'} and type(within['span']) is int
                and within['span'] >= 0):
            raise StoreError('INVALID_COUNT', sid)
        within = within['span']
    return ('count', n, within)


def _micro_bounds(condition):
    """The integer bounds [lo, hi] (None where open) that a series row with a micro-unit value m must meet, m an exact
    integer: m >= ceil(t * 10**6) for `ge t`, m >= floor(t * 10**6) + 1 for `gt t`, m <= floor(t * 10**6) for `le t`
    and m <= ceil(t * 10**6) - 1 for `lt t`, by exact rational arithmetic; clamped to just outside BIGINT, which every
    stored micro-unit value lies inside, so the bounds are SQL parameters of any size."""
    from fractions import Fraction
    import math
    from . import series_values as sv
    _, lower, upper = condition
    clamp = lambda v: max(sv.BIGINT_MIN - 1, min(sv.BIGINT_MAX + 1, v))
    lo = hi = None
    if lower:
        scaled = Fraction(lower[0]) * 10 ** 6
        lo = clamp(math.ceil(scaled) if lower[1] else math.floor(scaled) + 1)
    if upper:
        scaled = Fraction(upper[0]) * 10 ** 6
        hi = clamp(math.floor(scaled) if upper[1] else math.ceil(scaled) - 1)
    return lo, hi


def _decimal_satisfies(condition, text):
    """Whether an exact decimal (a series row's `value_decimal`) satisfies a condition: Decimal comparisons are exact,
    whatever the context's precision."""
    from decimal import Decimal
    value = Decimal(text)
    _, lower, upper = condition
    if lower and not (value > lower[0] or (lower[1] and value == lower[0])):
        return False
    if upper and not (value < upper[0] or (upper[1] and value == upper[0])):
        return False
    return True


def _tag_where(spec):
    """The SQL test (over `series s` joined to `concept_closure cc` on its item class) of a tag's concept and unit."""
    concept, (unit, _, _) = spec
    return 'cc.ancestor = ? AND s.unit = ?', [concept, unit]


def _tag_rows(store, spec, columns, loose_columns=None, join='', where='', params=(), group=''):
    """The series rows carrying the tag `spec` = (concept, condition), in two parts: rows whose micro-unit value is
    exact, tested in SQL against `_micro_bounds`, as `columns` (over `series s`, grouped by `group` if given), and rows
    whose micro-unit value is null (finer than 10**-6, or beyond BIGINT), each tested on its exact decimal by
    `_decimal_satisfies`, as `loose_columns` (default `columns`; never grouped: the caller merges them). Returns (exact
    rows, null rows). `join` takes no parameters; `params` are `where`'s."""
    test, test_params = _tag_where(spec)
    lo, hi = _micro_bounds(spec[1])
    bounds = (' AND s.value_micro >= ?' if lo is not None else '') + (' AND s.value_micro <= ?' if hi is not None else '')
    head = f'FROM series s JOIN concept_closure cc ON cc.concept = s.item_class {join} WHERE {test} {where}'
    exact = store.db.execute(f'SELECT {columns} {head} AND s.value_micro IS NOT NULL{bounds} {group}',
                             test_params + list(params) + [b for b in (lo, hi) if b is not None]).fetchall()
    loose = [row[:-1] for row in store.db.execute(
        f'SELECT {loose_columns or columns}, s.value_decimal {head} AND s.value_micro IS NULL', test_params + list(params)).fetchall()
        if _decimal_satisfies(spec[1], row[-1])]
    return exact, loose


def _tag_patients(store, spec):
    """The patients holding some event that carries the tag: its candidates, exactly."""
    exact, loose = _tag_rows(store, spec, 's.patient_idx', group='GROUP BY s.patient_idx')
    return {r[0] for r in exact} | {r[0] for r in loose}


def _tag_summaries(store, spec, part):
    """{patient_idx: (min start_lo, max start_hi, max end)} over the tag's events of the candidates in `part` (sorted;
    the temporary table `_m_live` holds them): the posting summaries a tag would have. Every tagged event is a point
    (series rows are of points only), so its latest end is its latest start."""
    exact, loose = _tag_rows(store, spec, 's.patient_idx, min(s.start_lo), max(s.start_hi)',
                             's.patient_idx, s.start_lo, s.start_hi', 'JOIN _m_live l ON l.idx = s.patient_idx',
                             'AND s.patient_idx BETWEEN ? AND ?', [part[0], part[-1]], 'GROUP BY s.patient_idx')
    out = {idx: (lo, hi) for idx, lo, hi in exact}
    for idx, lo, hi in loose:
        have = out.get(idx)
        out[idx] = (lo, hi) if have is None else (min(have[0], lo), max(have[1], hi))
    return {idx: (lo, hi, hi) for idx, (lo, hi) in out.items()}


def _tag_sources(store, spec):
    """The sources holding, anywhere in the snapshot, a series row of an item class the tag's concept covers, in the
    tag's unit, whatever its value: the sources that record the tag, for the coverage rule (as `source_concepts` is for
    a concept)."""
    test, params = _tag_where(spec)
    return {r[0] for r in store.query(
        f'''SELECT DISTINCT e.source FROM series s JOIN concept_closure cc ON cc.concept = s.item_class
            JOIN events e ON e.bucket = s.bucket AND e.patient_idx = s.patient_idx AND e.event_id = s.event_id
            WHERE {test}''', params)}


def _check_tag_names(store, request):
    if request is not None and request.tags:
        taken = sorted(r[0] for r in store.query(
            f'SELECT DISTINCT concept FROM concept_closure WHERE concept IN {_in(list(request.tags))}', list(request.tags)))
        if taken:
            raise StoreError('VALUE_TAG_NAME_TAKEN', ', '.join(taken))
    if request is not None and request.derived:  # D32: derived tags live in a namespace no concept may take
        names = list(request.derived)
        taken = sorted(r[0] for r in store.query(
            f'SELECT DISTINCT concept FROM concept_closure WHERE concept IN {_in(names)}', names))
        if taken:
            raise StoreError('DERIVED_TAG_NAME_TAKEN', ', '.join(taken))


def _tag_counts(store, spec):
    """{patient_idx: the number of events carrying the tag}, read from `series` exactly (D32: an Nth or count step on a
    value tag needs at least N of them)."""
    exact, loose = _tag_rows(store, spec, 's.patient_idx, count(DISTINCT s.event_id)', 's.patient_idx, s.event_id',
                             group='GROUP BY s.patient_idx')
    out = {idx: n for idx, n in exact}
    seen = set()
    for idx, eid in loose:
        if (idx, eid) not in seen:
            seen.add((idx, eid))
            out[idx] = out.get(idx, 0) + 1
    return out


def _derived_patients(store, name, request):
    """The patients with at least N events of a derived tag's concept or value tag (D32): a sound superset of the
    patients holding an event of the tag, since a count of N needs N events and an Nth occurrence N. A posting's `n`
    counts every event carrying the concept, letter mentions included, which the derived tag leaves out, so over coded
    records only the filter is a superset, not exact. `_D32_DEFECT == 'filter_n'` (a planted defect) reads a
    `widen_count`'s tag at the step's own N."""
    spec, tags = request.derived[name], request.tags
    if spec['kind'] == 'relative':  # D33: a relative tag's events are events of its unit tag
        n = 1
    else:
        n = spec['n'] if _D32_DEFECT != 'filter_n' else request.step_n[name]
    concept = spec['concept']
    if concept in tags:
        return {idx for idx, k in _tag_counts(store, tags[concept]).items() if k >= n}
    return {r[0] for r in store.query('SELECT patient_idx FROM concept_postings WHERE concept = ? AND n >= ?', [concept, n])}


# Derived steps (D32): Nth and Nth-from-last occurrence, and "at least N times within a span or the index period", in a
# 1.1 match request. Each is compiled into a derived tag (`Request`); after a chunk's events are loaded, the store
# derives the tag's events per patient (`derive_patient`) and runs the engine, unchanged, over them (`match_derived`):
# - an Nth step's tag is carried by one derived point event, with its own variable, whose start ranges from the Nth
#   smallest lower bound to the Nth smallest upper bound of the starts of the patient's coded events of the concept,
#   over `Times.dom` (Nth from last: the Nth largest), which is exactly the set of times the Nth occurrence can take
#   when those events are independent (the envelope lemma, plan section 1.2);
# - a count step's tag is carried by the recorded events at which the count is reached, *certain* (in every choice of
#   the patient's times) or only *possible* (in some), decided exactly by a sweep when the counted events share no
#   variable, and by a sound pairwise test on the patient's distances otherwise; the two-pass rule combines a pass over
#   the certain events with one over the certain and possible ones.
# Counts and Nth run over coded records only: an event whose kind is `mention` is never counted or ranked. Results are
# counts of recorded events, never of real episodes (a happening recorded twice counts twice).

_DERIVED_PREFIX = 'derived-step:'
MENTION_KIND = 'mention'
DERIVED_RULES = {
    'records': 'coded_records_v1: counts and Nth occurrences read only events whose kind is not "mention", and count '
               'recorded events, not happenings',
    'nth': 'nth_envelope_dom_v1: the Nth occurrence time ranges from the Nth smallest lower to the Nth smallest upper '
           'start bound over Times.dom (Nth from last: the Nth largest); unresolved (OCCURRENCE_TIME_COUPLED) where '
           'possible would rest on a step that can bind a counted event, or on shared variables',
    'count': 'count_sweep_v1 (exact, no shared variable among the counted events) or count_pairwise_linked_v1 '
             '(sound pairwise test on the patient\'s distances): at least N - 1 other counted events in '
             '[t(e) - span, t(e)], or in [window start, t(e)] with t(e) in the window; certain at e when it holds in '
             'every choice of times, possible when in some',
    'two_pass': 'two_pass_v1: pass 1 over certain count events; pass 2 over certain and possible ones when pass 1 is '
                'neither exact, possible nor unresolved and some tag a tried relaxation set uses has a possible-only '
                'event; differing passes give unresolved (STEP_CONDITION_UNCERTAIN)',
    'exclusions': 'excluded_records_derived_v1: an Nth step is displaced like first (Nth from last like last), on its '
                  'envelope; Nth and span-count steps reach every excluded record of their concepts, period counts '
                  'those overlapping the window',
}
# Planted defects for the tests (D32 rules): a module global, like the engine's switches; None in every real run.
_D32_DEFECT = None

# Relative conditions (D33): "rises to at least x f" and "falls to at most / f" against a baseline of up to k previous
# readings within W, in a 1.2 match request. A relative step's tag is carried by the readings (events with a series row
# of the step's concept in the condition's unit) whose condition holds, certain or possible, decided exactly from the
# readings' times (`_relative_strengths`); the two-pass rule of D32 then applies unchanged.
PERIOD_LABEL = 'period_reading_kinds'  # snapshot label: the event kinds whose series rows summarise a whole period
RELATIVE_MAX_PREVIOUS = 3
RELATIVE_UNCERTAIN_LIMIT = 6
RELATIVE_RULES = {
    'readings': 'relative_previous_k_v1: the readings of an item class are its series rows, in any unit, and its '
                'excluded records; a reading whose event kind the snapshot label period_reading_kinds lists is a period '
                'summary, timed exactly at its recorded start\'s lower bound (the period\'s start); any other series '
                'reading is timed by its start over Times.dom, an excluded record by its recorded start range',
    'baseline': 'with the reading at tau: the k most recent readings of its item class with tau - W <= t <= tau - 1, '
                'by time, at equal times excluded records first, then event id ascending; none: no baseline (false); '
                'an excluded record or a reading in another unit among them: undetermined; otherwise their mean, exact',
    'comparison': 'rise: value >= factor * max(floor, baseline); fall: value <= max(floor, baseline) / factor; exact '
                  'rationals over value_decimal; the reading must be in the condition\'s unit',
    'strength': 'certain when the condition holds in every choice of the readings\' times, possible when it holds or '
                'is undetermined in some; exact when no two point readings of the item class have linked start '
                'variables and at most 6 readings of uncertain time could precede the reading within W; otherwise '
                'sound (certain under, possible over) and marked inexact',
    'exclusions': 'excluded records that could be among the previous k make the baseline undetermined, inside the '
                  'tag; an excluded record that could be the rising reading is reached by the step\'s scope (D18)',
}
# Planted defects for the tests (D33 rules); None in every real run.
_D33_DEFECT = None


def _interval_counter(intervals):
    """count(tau): how many of the closed intervals (lo, hi) contain tau (hi may be infinite); empty ones are left out."""
    from bisect import bisect_left, bisect_right
    kept = [(lo, hi) for lo, hi in intervals if lo <= hi]
    los, his = sorted(lo for lo, _ in kept), sorted(hi for _, hi in kept)
    return lambda tau: bisect_right(los, tau) - bisect_left(his, tau), los, his


def _extreme(counter, lo, hi, own, best):
    """The least (`best` = min) or greatest (max) of counter(tau) - [tau in own] over integer tau in [lo, hi]. The
    function is constant between the points where an interval or `own` begins (its lo) or ends (its hi + 1), so it is
    evaluated at lo and at each such point inside (lo, hi]."""
    from bisect import bisect_right
    count, los, his = counter
    points = {lo}
    points.update(los[bisect_right(los, lo):bisect_right(los, hi)])
    points.update(h + 1 for h in his[bisect_right(his, lo - 1):bisect_right(his, hi - 1)])
    for v in (own[0], own[1] + 1):
        if lo < v <= hi:
            points.add(v)
    return best(count(t) - (1 if own[0] <= t <= own[1] else 0) for t in points)


def _count_strengths(times, family, n, within, window):
    """({event id: 'certain' or 'possible'}, exact) for a count of at least `n` over `family` (kernel records of the
    patient's coded events carrying the concept), within `within` (a span in microseconds, or 'window'). An event e
    reaches the count in a choice of times t when at least n - 1 *other* events x of the family have
    t(e) - span <= t(x) <= t(e) (for the window: window start <= t(x) <= t(e) <= window end). Simultaneous events count.

    With no variable shared among the family's events (and with none outside it), each start ranges independently over
    its tightened range [a, b] (`Times.dom`), so with tau = t(e):
    - forced(tau) = #{x : b_x <= tau and a_x >= tau - span} other events are in the span in every choice, and
      allowed(tau) = #{x : a_x <= tau and b_x >= tau - span} in some (each independently);
    - e is certain when min over tau of forced(tau) >= n - 1, possible when max of allowed(tau) >= n - 1 (count_sweep_v1,
      exact). For the window, tau ranges over e's range cut to the window for possible, while certain needs e's whole
      range inside the window.
    Otherwise each pair (e, x) is read from the patient's distances: given t(e) = tau, x's start ranges exactly over
    [max(a_x, tau - gap(x, e)), min(b_x, tau + gap(e, x))] (a minimal network is decomposable), so counting x as
    forced where that interval lies inside the span, and as allowed where it meets it, gives a sound under-approximation
    of certain and over-approximation of possible (count_pairwise_linked_v1); `exact` is then False."""
    out = {}
    if len(family) < n:
        return out, True
    need = n - 1
    defect = _D32_DEFECT
    if defect == 'own_witness':
        need = max(0, n - 2)
    span = None if within == 'window' else within + (1 if defect == 'span_off_by_one' else 0)
    ws, we = (window['start'], window['end']) if within == 'window' else (None, None)
    exact = all(r[tg._KEYS] is None for r in family)
    ab = [(r[tg._SLO], r[tg._SHI]) for r in family]
    if defect == 'recorded_bounds':
        ab = [r[tg._EVENT].s for r in family]

    def allowed_iv(a, b):
        return (a, b + span) if span is not None else ((a, tg.INF) if b >= ws else (1, 0))

    def forced_iv(a, b):
        return (b, a + span) if span is not None else ((b, tg.INF) if a >= ws else (1, 0))
    if exact:
        allowed = _interval_counter([allowed_iv(a, b) for a, b in ab])
        forced = _interval_counter([forced_iv(a, b) for a, b in ab])
    for k, r in enumerate(family):
        a, b = ab[k]
        if exact:
            own_allowed, own_forced = allowed_iv(a, b), forced_iv(a, b)
            al, fo = allowed, forced
        else:
            e = r[tg._EVENT]
            aiv, fiv = [], []
            for j, x in enumerate(family):
                if j == k:
                    continue
                ax, bx = ab[j]
                g_ex, g_xe = times.gap(e.sv, x[tg._EVENT].sv), times.gap(x[tg._EVENT].sv, e.sv)
                if span is not None:
                    aiv.append((ax + max(0, -g_ex), bx + min(span, g_xe)) if g_ex >= -span and g_xe >= 0 else (1, 0))
                    fiv.append((-tg.INF if g_ex <= 0 else bx, tg.INF if g_xe <= span else ax + span))
                else:
                    aiv.append((max(ax, ax - g_ex, ws - g_ex), bx + g_xe) if bx >= ws and g_xe >= 0 else (1, 0))
                    fiv.append((max(-tg.INF if g_ex <= 0 else bx, -tg.INF if ax >= ws else ws + g_xe), tg.INF))
            al, fo = _interval_counter(aiv), _interval_counter(fiv)
            own_allowed = own_forced = (1, 0)
        # possible: tau over e's range (cut to the window for a period count)
        lo, hi = (a, b) if span is not None else (max(a, ws), min(b, we))
        if defect == 'forced_allowed_swapped':
            al, fo, own_allowed, own_forced = fo, al, own_forced, own_allowed
        possible = lo <= hi and _extreme(al, lo, hi, own_allowed, max) >= need
        # certain: tau over e's whole range, which for a period count must lie inside the window
        if span is None:
            inside = (a <= we and b >= ws) if defect == 'period_overlap' else (a >= ws and b <= we)
            clo, chi = (max(a, ws), min(b, we)) if defect in ('tau_cut', 'period_overlap') else (a, b)
            certain = (inside or defect == 'tau_cut') and clo <= chi and _extreme(fo, clo, chi, own_forced, min) >= need
        else:
            certain = _extreme(fo, a, b, own_forced, min) >= need
        if certain:
            out[r[tg._EVENT].id] = 'certain'
        elif possible:
            out[r[tg._EVENT].id] = 'possible'
    return out, exact


class _Reading:
    """One reading of an item class (D33): a series row (`eid` its event, `value` a Fraction, `unit`) or an excluded
    record (`eid`, `value` and `unit` None); [a, b] the range of its time; `link` the component of its start variable
    in the patient's network (None for a period reading, an excluded record or an event not loaded); `rank` the
    tie order at equal times, larger first (excluded records, then event ids ascending)."""
    __slots__ = ('eid', 'item', 'a', 'b', 'value', 'unit', 'link', 'rank')

    def __init__(self, eid, item, a, b, value, unit, link, rank):
        self.eid, self.item, self.a, self.b, self.value, self.unit, self.link, self.rank = (
            eid, item, a, b, value, unit, link, rank)


def _patient_readings(readings, times):
    """({item class: [_Reading] sorted by a}, {item class: whether two of its point readings are linked}) for one
    patient. `readings` holds `rows` {event id: (item class, unit, value_decimal, kind, recorded start)}, `excluded`
    {item class: [recorded start]}, `period` (the event kinds the snapshot declares period summaries) and `covered`
    {concept: item classes it covers}."""
    from decimal import Decimal
    from fractions import Fraction
    period = readings['period']
    loaded = {e.id: e for e in times.events}
    by_class = {}
    for r, (eid, (item, unit, text, kind, start)) in enumerate(sorted(readings['rows'].items())):
        e = loaded.get(eid)
        if kind in period:  # a period summary: its period's start, exactly
            a = b = start[0]
            link = None
        elif e is not None:
            a, b = times.dom[e.sv]
            link = times.component[e.sv]
        else:  # an event no step loads, in a patient without shared variables: its recorded range is its range
            a, b = start
            link = None
        by_class.setdefault(item, []).append(_Reading(eid, item, a, b, Fraction(Decimal(text)), unit, link, (0, -r)))
    for item, starts in readings['excluded'].items():
        for j, (lo, hi) in enumerate(sorted(starts)):
            by_class.setdefault(item, []).append(_Reading(None, item, lo, hi, None, None, None, (1, -j)))
    linked = {}
    for item, members in by_class.items():
        members.sort(key=lambda x: (x.a, x.b))
        links = [x.link for x in members if x.link is not None]
        linked[item] = len(links) != len(set(links))
    return by_class, linked


def _relative_strengths(rel, cands, by_class, linked):
    """({event id: 'certain' or 'possible'}, exact) of a relative condition `rel` (D33, `relative_previous_k_v1`) over
    the readings `cands` (in the condition's unit, of item classes its concept covers).

    With reading c at tau, a reading x of c's item class is *in the window* when tau - W <= t(x) <= tau - 1; the
    baseline is the k window members ranked first by (time, rank), larger first. Each x is forced in (its range lies in
    the window), optional (it can lie in the window or outside it), or out. The baselines realizable at tau are:
    - of fewer than k members: exactly the forced readings plus fewer than k - |forced| optional ones;
    - of k members S: exactly those for which, with each member at its latest time in the window, every non-member
      that can be in the window can also lie outside it or rank below the lowest member at its earliest time there
      (the members' latest times make that lowest rank as high as it can be, since times are independent).
    Every test compares tau - W or tau - 1 with a bound, so the set of baselines is constant between the points where
    one test changes: tau is tried at c's earliest time and at every bound plus 0 to 3 or W - 1 to W + 3 inside c's
    range. Certain when every baseline at every tau is true, possible when one is true or undetermined. Exact when the
    times are independent: unless two point readings of the item class are linked (then sound and inexact), or more
    than RELATIVE_UNCERTAIN_LIMIT readings of uncertain time could precede c within W (then possible, inexact)."""
    from bisect import bisect_left, bisect_right
    from fractions import Fraction
    from heapq import nlargest
    from itertools import combinations
    defect = _D33_DEFECT
    factor, floor = Fraction(_decimal_of(rel['factor'])), Fraction(_decimal_of(rel['floor']))
    k = rel['previous'] + (1 if defect == 'k_plus_one' else 0)
    W = rel['within'] + (1 if defect == 'window_off_by_one' else 0)
    unit, rise = rel['unit'], rel['kind'] == 'rise'
    out, exact = {}, True
    sorted_a = {item: [x.a for x in members] for item, members in by_class.items()}
    widest = {item: max((x.b - x.a for x in members), default=0) for item, members in by_class.items()}
    key = lambda x, t: (t, x.rank)

    def verdict(c, S):  # True, False, or None (undetermined)
        if not S:
            return False
        if any(x.value is None for x in S):
            return None
        if defect != 'unit_mixing' and any(x.unit != unit for x in S):
            return None
        base = sum((x.value for x in S), Fraction(0)) / len(S)
        m = base if defect == 'floor_ignored' else max(floor, base)
        return c.value >= factor * m if rise else c.value * factor <= m
    for c in cands:
        members = by_class[c.item]
        a_c, b_c = c.a, c.b
        arr = sorted_a[c.item]
        window = members[bisect_left(arr, a_c - W - widest[c.item]):bisect_right(arr, b_c - 1)]
        near = [x for x in window if x is not c and x.b >= a_c - W]
        if defect == 'excluded_ignored':
            near = [x for x in near if x.value is not None]
        if linked[c.item]:
            exact = False
        uncertain = sum(1 for x in near if x.a != x.b) + (a_c != b_c)
        if uncertain > RELATIVE_UNCERTAIN_LIMIT:
            out[c.eid] = 'possible'
            exact = False
            continue
        if uncertain == 0:  # every time exact (as on MIMIC, and for period readings): one baseline
            tau = a_c
            S = nlargest(k, (x for x in near if tau - W <= x.a <= tau - 1), key=lambda x: key(x, x.a))
            v = verdict(c, S)
            if v:
                out[c.eid] = 'certain'
            elif v is None:
                out[c.eid] = 'possible'
            continue
        offsets = (0, 1, 2, 3, W - 1, W, W + 1, W + 2, W + 3)
        taus = {a_c} | {p + d for x in near for p in (x.a, x.b) for d in offsets if a_c <= p + d <= b_c}
        holds = undecided = fails = False
        baselines = set()
        for tau in sorted(taus):
            inable = []
            for x in near:
                lo, hi = max(x.a, tau - W), min(x.b, tau - 1)
                if lo <= hi:
                    inable.append((x, lo, hi, x.a <= tau - W - 1 or x.b >= tau))
            forced = [i for i in inable if not i[3]]
            optional = [i for i in inable if i[3]]
            found = []
            if len(forced) < k:
                for z in range(k - len(forced)):
                    found.extend(forced + list(extra) for extra in combinations(optional, z))
            top = [i for i in inable
                   if sum(1 for y, lo, _, _ in forced if y is not i[0] and key(y, lo) > key(i[0], i[2])) < k]
            for S in combinations(top, k):
                theta = min(key(s, hi) for s, _, hi, _ in S)
                chosen = {id(s) for s, _, _, _ in S}
                if all(out_ or key(x, lo) < theta for x, lo, _, out_ in inable if id(x) not in chosen):
                    found.append(list(S))
            for S in found:
                members_ = [s[0] for s in S]
                baselines.add(frozenset(id(x) for x in members_))
                v = verdict(c, members_)
                if v:
                    holds = True
                elif v is None:
                    undecided = True
                else:
                    fails = True
            if (fails or undecided) and (holds or undecided) and defect != 'determined_only':
                break
        certain = holds and not undecided and not fails
        if defect == 'determined_only':
            certain = certain and len(baselines) == 1
        if certain:
            out[c.eid] = 'certain'
        elif holds or undecided:
            out[c.eid] = 'possible'
    return out, exact


_NO_READINGS = {'rows': {}, 'excluded': {}, 'period': frozenset(), 'covered': {}}


class Derived:
    """One patient's derived events (D32): `first` the engine patient of pass 1 (Nth points, and count tags on their
    certain events), `second` that of pass 2 (count tags on certain and possible events) or None when no tag a tried
    set uses has a possible-only event, `families` {derived tag: ids of the coded events it counts}, `linked` the
    concepts whose coded events share a variable, `inexact` whether a count tag a tried set uses was decided by the
    pairwise test, and `points` {point id: start range}."""
    __slots__ = ('first', 'second', 'families', 'linked', 'inexact', 'points', 'concepts')


def derive_patient(request, pid, window, events, mentions, readings=None):
    """The derived events of one patient, from its loaded engine events (concepts already listing the request's plain
    concepts and value tags) and the ids of its events whose kind is `mention`. Raises `DERIVED_EVENT_ID_TAKEN` when an
    event of the patient carries a derived point's identifier. A relative condition (D33) also reads the patient's
    `readings` (`_readings`): the series rows and excluded records of the item classes its concept covers."""
    times = tg.patient_times({'patient_id': pid, 'window': window, 'events': events}, trusted=True)
    defect = _D32_DEFECT
    out = Derived()
    out.families, out.linked, out.points, out.inexact = {}, set(), {}, False
    out.concepts = {e.id: e.concepts for e in times.events}
    certain, possible, second = {}, {}, False
    trigger = request.derived_used if defect != 'pass2_unrelaxed_only' else {
        name: spec for name, spec in request.derived_used.items() if name in {s['concept'] for s in request.query['steps']}}
    points, ids = [], {e.id for e in times.events}
    prepared = None
    for name, spec in request.derived_used.items():
        if spec['kind'] == 'relative':  # D33
            if prepared is None:
                prepared = _patient_readings(readings or _NO_READINGS, times)
            by_class, linked = prepared
            concept, (unit, _, _) = request.tags[spec['concept']]
            cands = [x for item in sorted((readings or _NO_READINGS)['covered'].get(concept, ()))
                     for x in by_class.get(item, ()) if x.eid is not None and x.unit == unit and x.eid in ids]
            out.families[name] = {x.eid for x in cands}
            strengths, exact = _relative_strengths(spec['relative'], cands, by_class, linked)
            if not exact and strengths:
                out.inexact = True
            for eid, strength in strengths.items():
                (certain if strength == 'certain' else possible).setdefault(eid, []).append(name)
                if strength == 'certain':
                    possible.setdefault(eid, []).append(name)
            if name in trigger and any(v == 'possible' for v in strengths.values()):
                second = True
            continue
        family = [r for r in times.candidate_records(spec['concept']) if r[tg._EVENT].id not in mentions]
        out.families[name] = {r[tg._EVENT].id for r in family}
        if any(r[tg._KEYS] is not None for r in family):
            out.linked.add(spec['concept'])
        n = spec['n']
        if spec['kind'] == 'nth':
            if defect == 'nth_off_by_one':
                n += 1
            if len(family) < n:
                continue
            if defect == 'rule_r':
                ranked = sorted(family, key=lambda r: (r[tg._SLO], r[tg._SHI], r[tg._EVENT].id))
                pick = ranked[n - 1] if spec['which'] == 'nth' else ranked[-n]
                lo, hi = pick[tg._SLO], pick[tg._SHI]
            else:
                bounds = [r[tg._EVENT].s if defect == 'recorded_bounds' else (r[tg._SLO], r[tg._SHI]) for r in family]
                lows, highs = sorted(b[0] for b in bounds), sorted(b[1] for b in bounds)
                front = spec['which'] == 'nth' or defect == 'nth_last_from_front'
                lo, hi = (lows[n - 1], highs[n - 1]) if front else (lows[-n], highs[-n])
            if name in ids:
                raise StoreError('DERIVED_EVENT_ID_TAKEN', f'{pid}/{name}')
            points.append({'id': name, 'concepts': [name], 'start': [lo, hi]})
            out.points[name] = [lo, hi]
        else:
            strengths, exact = _count_strengths(times, family, n, spec['within'], window)
            if not exact and strengths:
                out.inexact = True
            for eid, strength in strengths.items():
                (certain if strength == 'certain' else possible).setdefault(eid, []).append(name)
                if strength == 'certain':
                    possible.setdefault(eid, []).append(name)
            if name in trigger and any(v == 'possible' for v in strengths.values()):
                second = True
    def doc(tagged):
        return {'patient_id': pid, 'window': window,
                'events': [{**e, 'concepts': sorted(set(e['concepts']) | set(tagged[e['id']]))} if e['id'] in tagged else e
                           for e in events] + points}
    out.first = doc(certain)
    out.second = doc(possible) if second else None
    return out


def _coupled_steps(pq, request, derived):
    """{relaxation set key: [Nth steps whose possible result is not exact]} (D32). An Nth step on concept K is coupled
    when K's coded events share a variable, or another step can bind one of them: an `any`, `first` or `last` step
    whose concept one of them carries, or a derived step whose counted events meet them. Its envelope point is then
    independent in the engine's model where the true Nth time is not, so `possible` may be wrong both ways; `exact` and
    `none` stay sound, since every true choice of times maps into the model with the point at the true Nth time."""
    out = {}
    if _D32_DEFECT == 'coupling_ignored':
        return out
    for chosen in [()] + [chosen for _, chosen in pq.sets]:
        plan = pq.plan(chosen)
        coupled = []
        for s in pq.step_ids:
            name = plan.concepts[s]
            spec = request.derived.get(name)
            if spec is None or spec['kind'] != 'nth' or name not in derived.points:
                continue
            family = derived.families[name]
            hit = spec['concept'] in derived.linked
            for t in pq.step_ids:
                if hit or t == s:
                    continue
                other = plan.concepts[t]
                if other in request.derived:
                    hit = not derived.families[other].isdisjoint(family)
                else:
                    hit = any(other in derived.concepts[eid] for eid in family)
            if hit:
                coupled.append(s)
        if coupled:
            out[tuple(x['id'] for x in chosen)] = coupled
    return out


def _coupled_match(pq, patient, coupled):
    """`trajectory_graph.match_prepared` for a patient whose Nth steps are coupled in some set (D32): a set in which a
    coupled step's result would be `possible` is undecided, as D30 leaves a set an uncertain `first` or `last` step
    makes undecided, with reason OCCURRENCE_TIME_COUPLED (its binding beside an unresolved status, or the set passed
    over with note RELAXATION_OCCURRENCE_COUPLED or CHEAPER_RELAXATION_OCCURRENCE_COUPLED). Everything else is the
    engine's own `match_prepared`, line for line."""
    query, budget = pq.query, pq.budget
    times = tg.patient_times(patient, True)
    pid = patient['patient_id']

    def decide(chosen):
        r, open_ = tg.decide_ordered(query, times, chosen, budget, pq.plan(chosen))
        reason = 'OCCURRENCE_ORDER_UNCERTAIN'
        steps = coupled.get(tuple(x['id'] for x in chosen))
        if open_ is None and steps and not r['exact']['found'] and r['possible']['found']:
            open_, reason = ('possible', steps), 'OCCURRENCE_TIME_COUPLED'
        return r, open_, reason
    first, undecided, why = decide(())
    if undecided is not None and undecided[0] != 'none':
        kind = undecided[0]
        return {'patient_id': pid, 'status': 'unresolved', 'reason': why, 'uncertain_steps': undecided[1],
                'unverified_match': {'certainty': kind, 'binding': tg._binding_ids(first[kind]['binding']),
                                     'method': first[kind]['method']}, 'complete': False}
    if first['exact']['found']:
        return {'patient_id': pid, 'status': 'exact', 'binding': tg._binding_ids(first['exact']['binding']),
                'method': first['exact']['method'], 'complete': True}
    if first['possible']['found']:
        out = {'patient_id': pid, 'status': 'possible', 'binding': tg._binding_ids(first['possible']['binding']),
               'witness': dict(sorted(first['possible']['witness'].items())), 'method': first['possible']['method'],
               'complete': first['exact']['complete']}
        if not first['exact']['complete']:
            out['note'] = 'EXACT_SEARCH_STOPPED'
        return out
    stopped = not first['possible']['complete']
    unsure = undecided[1] if undecided is not None else None

    def unresolved(**extra):
        if stopped:
            return {'patient_id': pid, 'status': 'unresolved', 'reason': 'SEARCH_BUDGET_EXHAUSTED', **extra,
                    'complete': False}
        return {'patient_id': pid, 'status': 'unresolved', 'reason': why, 'uncertain_steps': unsure, **extra,
                'complete': False}
    skipped = order_skipped = coupled_skipped = False
    for cost, chosen in pq.sets:
        r, open_, reason = decide(chosen)
        if open_ is not None:
            if reason == 'OCCURRENCE_TIME_COUPLED':
                coupled_skipped = True
            else:
                order_skipped = True
            continue
        winner = 'exact' if r['exact']['found'] else 'possible' if r['possible']['found'] else None
        if winner is None:
            skipped = skipped or not r['possible']['complete']
            continue
        relaxed = {'relaxations': [x['id'] for x in chosen], 'cost': tg.decimal_text(cost), 'certainty': winner,
                   'binding': tg._binding_ids(r[winner]['binding']), 'method': r[winner]['method']}
        if stopped or unsure is not None:
            return unresolved(relaxed_match=relaxed)
        out = {'patient_id': pid, 'status': 'relaxed', **relaxed,
               'complete': not skipped and not order_skipped and not coupled_skipped
               and (winner == 'exact' or r['exact']['complete'])}
        if skipped:
            out['note'] = 'CHEAPER_RELAXATION_SEARCH_STOPPED'
        elif order_skipped:
            out['note'] = 'CHEAPER_RELAXATION_ORDER_UNCERTAIN'
        elif coupled_skipped:
            out['note'] = 'CHEAPER_RELAXATION_OCCURRENCE_COUPLED'
        return out
    if stopped or unsure is not None:
        return unresolved()
    out = {'patient_id': pid, 'status': 'none', 'complete': not skipped and not order_skipped and not coupled_skipped}
    if skipped:
        out['note'] = 'RELAXATION_SEARCH_STOPPED'
    elif order_skipped:
        out['note'] = 'RELAXATION_ORDER_UNCERTAIN'
    elif coupled_skipped:
        out['note'] = 'RELAXATION_OCCURRENCE_COUPLED'
    return out


def _one_pass(pq, patient, coupled):
    return _coupled_match(pq, patient, coupled) if coupled else tg.match_prepared(pq, patient, trusted=True)


def _notes(*results):
    notes = []
    for r in results:
        for note in (r.get('note') or '').split('; '):
            if note and note not in notes:
                notes.append(note)
    return '; '.join(notes)


def match_derived(pq, request, derived):
    """One patient's result for a request with derived steps (D32): the engine over pass 1, then the two-pass rule.

    | Pass 1 (certain count events) | Pass 2 (certain and possible) | Result |
    | exact, possible or unresolved | not run | pass 1's |
    | relaxed | relaxed by the same set | relaxed (pass 1's match), `complete` only if both are |
    | relaxed | anything else | unresolved, STEP_CONDITION_UNCERTAIN, pass 1's match beside it |
    | none | none | none, `complete` only if both are |
    | none | anything else | unresolved, STEP_CONDITION_UNCERTAIN, no match beside it |

    Pass 2 runs only when some tag a tried set uses has a possible-only event (else it is pass 1's model). Notes from
    both passes are kept. Where a tried count tag was decided by the pairwise test (`inexact`: its certain events may be
    too few), a claim that no binding is exact rests on nothing, so pass 1's `possible`, a relaxed match whose
    certainty is `possible`, and such a match beside `unresolved` are not reported."""
    coupled = _coupled_steps(pq, request, derived)
    counted = [s for s in pq.step_ids if request.kinds.get(s, ('',))[0] in ('count', 'relative')]
    one = _one_pass(pq, derived.first, coupled)
    pid = derived.first['patient_id']

    def uncertain(other=None, **extra):
        # Pass 2 unresolved for its own reason (an Nth step's coupling, an uncertain first or last target, a stopped
        # search) is reported with that reason (D32 review); otherwise the count's condition left it undecided.
        if other is not None and other['status'] == 'unresolved':
            steps = {'uncertain_steps': other['uncertain_steps']} if 'uncertain_steps' in other else {}
            return {'patient_id': pid, 'status': 'unresolved', 'reason': other['reason'], **steps, **extra,
                    'complete': False}
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'STEP_CONDITION_UNCERTAIN',
                'uncertain_steps': counted, **extra, 'complete': False}
    if derived.inexact:
        if one['status'] == 'possible':
            return uncertain()
        if one['status'] == 'unresolved' and one.get('relaxed_match', {}).get('certainty') == 'possible':
            one = {k: v for k, v in one.items() if k != 'relaxed_match'}
    if derived.second is None or one['status'] in ('exact', 'possible', 'unresolved'):
        if derived.inexact and one['status'] == 'relaxed' and one['certainty'] == 'possible':
            return uncertain()
        return one
    two = _one_pass(pq, derived.second, coupled)
    if one['status'] == 'relaxed':
        if two['status'] == 'relaxed' and two['relaxations'] == one['relaxations']:
            if derived.inexact and one['certainty'] == 'possible':
                return uncertain()
            out = {**{k: v for k, v in one.items() if k not in ('complete', 'note')},
                   'complete': one['complete'] and two['complete']}
            note = _notes(one, two)
            return {**out, 'note': note} if note else out
        match = {k: one[k] for k in ('relaxations', 'cost', 'certainty', 'binding', 'method')}
        if derived.inexact and one['certainty'] == 'possible':
            return uncertain(two)
        return uncertain(two, relaxed_match=match)
    if two['status'] == 'none':
        out = {'patient_id': pid, 'status': 'none', 'complete': one['complete'] and two['complete']}
        note = _notes(one, two)
        return {**out, 'note': note} if note else out
    return uncertain(two)


def candidates(store, query, conditions=None):
    """Patients who could match under some relaxation set the engine would try.

    Sound, not exact: for each step, the union of postings over its concept and its
    widenings; intersected across steps, rarest first. By concept only: `match` then prunes on the
    postings' summaries (`_prune`, D25, D26).

    With value `conditions` (D28) a conditioned step's concepts are tags, which have no postings: a tag's patients are
    those holding an event that carries it, read from `series` exactly (`_tag_patients`).
    """
    request = None if conditions is None else Request(query, conditions)
    _require_series(store, request)
    return _candidates(store, tg.PreparedQuery(query if request is None else request.query), request)


def _require_series(store, request):
    """D32: a snapshot built without series cannot hold any value, so a 1.1 value condition would rest on nothing and
    is refused by name (`NO_SERIES_IN_SNAPSHOT`). A 1.0 request keeps its D28 answer, key for key."""
    if request is not None and request.profile != CONDITIONS_PROFILE and request.tags and not store.manifest[
            'tables']['series']['rows']:
        raise StoreError('NO_SERIES_IN_SNAPSHOT', ', '.join(sorted({c for c, _ in request.tags.values()})))


def _candidates(store, pq, request=None):
    query = pq.query
    per_step = _step_concepts(query)
    base = per_step if request is None else request.base
    _known(store, sorted({c for cs in base.values() for c in cs}))
    _visible_to_exclusions(store, sorted({c for cs in base.values() for c in cs}))
    _check_tag_names(store, request)
    tags = {} if request is None else request.tags
    derived = {} if request is None else request.derived
    plan = []
    for step, concepts in per_step.items():
        plain = [c for c in concepts if c not in tags and c not in derived]
        patients = {r[0] for r in store.query(
            f'SELECT DISTINCT patient_idx FROM concept_postings WHERE concept IN {_in(plain)}', plain)} if plain else set()
        for c in concepts:
            if c in tags:
                patients |= _tag_patients(store, tags[c])
            elif c in derived:
                patients |= _derived_patients(store, c, request)
        plan.append({'step': step, 'concepts': concepts, 'patients': patients})
    plan.sort(key=lambda s: (len(s['patients']), s['step']))
    survivors = None
    for s in plan:
        survivors = set(s['patients']) if survivors is None else survivors & s['patients']
        if not survivors:
            break
    return {'patient_idx': sorted(survivors or ()),
            'plan': [{'step': s['step'], 'concepts': s['concepts'], 'patients': len(s['patients'])} for s in plan]}


# Time pruning on the posting summaries (D25, D26). `match` drops a candidate before loading its events when the
# summaries prove the engine's answer is `none`, complete; `_PRUNE = False` turns it off (the tests compare both), and
# `_PRUNE_ENDS = False` leaves out the tests on ends (D26), which the benchmark uses to count what they add.
_PRUNE = True
_PRUNE_ENDS = True
_NONE = lambda pid: {'patient_id': pid, 'status': 'none', 'complete': True}  # tg.match_prepared's own `none`
# Where a posting summary keeps each range end: (min_start_lo, max_start_hi, max_end_hi). The lower end of an end
# is not stored, so a test needing it is left out.
_SUMMARY = {('s', 'lo'): 0, ('s', 'hi'): 1, ('f', 'hi'): 2}


def _prune_plan(pq, ends=None):
    """For the unrelaxed query and each relaxation set the engine would try, in its order, what a patient must
    have for that set to be possible: per step, its concept and the bounds its unary constraints put on the start
    of an event of that concept; per related step pair, the bounds on the difference of their starts; and, with
    `ends` (default `_PRUNE_ENDS`), the bounds a constraint on an end puts on its difference with a start (D26).

    Each entry is (steps, pairs, gaps): steps as (concept, lows, highs), where lows are (anchor, k) meaning some
    event's latest start >= anchor + k and highs (anchor, k) meaning its earliest start <= anchor + k; pairs as
    (concept of x, concept of y, lo, hi), meaning start(y) - start(x) in [lo, hi] for some pair of events; gaps as
    (concept of x, end of x, concept of y, end of y, k, sense), meaning summary[y][end of y] - summary[x][end of x]
    >= k (sense 1) or <= k (sense -1) for some pair, with ends indexing a summary (`_SUMMARY`). In
    trajectory-graph-1.0 the only constraint on an end is `during`'s grace, start(to) - end(from) <= grace, whose
    gap is min_start_lo(to) - max_end_hi(from) <= grace. A test that would need a summary not stored is left out,
    which only weakens the test."""
    ends = _PRUNE_ENDS if ends is None else ends
    out = []
    for chosen in [()] + [chosen for _, chosen in pq.sets]:
        plan = pq.plan(chosen)
        steps = []
        for s in pq.step_ids:
            lows, highs = [], []
            for c in plan.unary[s]:
                if c.lo > c.hi:  # no difference satisfies it, so no event passes: a bound no start reaches
                    lows.append(('START', tg.INF))
                if c.x[0] in tg.ANCHORS and c.y == (s, 's'):  # start - anchor in [lo, hi]
                    lows += [(c.x[0], c.lo)] if c.lo != -tg.INF else []
                    highs += [(c.x[0], c.hi)] if c.hi != tg.INF else []
                elif c.y[0] in tg.ANCHORS and c.x == (s, 's'):  # anchor - start in [lo, hi]
                    lows += [(c.y[0], -c.hi)] if c.hi != tg.INF else []
                    highs += [(c.y[0], -c.lo)] if c.lo != -tg.INF else []
            steps.append((plan.concepts[s], tuple(lows), tuple(highs)))
        pairs, gaps = [], []
        for cs in plan.binary.values():
            for c in cs:
                cx, cy = plan.concepts[c.x[0]], plan.concepts[c.y[0]]
                if (c.x[1] == 's' and c.y[1] == 's') or c.lo > c.hi:  # an empty constraint is impossible either way
                    pairs.append((cx, cy, c.lo, c.hi))
                    continue
                if not ends:
                    continue
                # `plausible` needs hi(y) - lo(x) >= lo and lo(y) - hi(x) <= hi, over the engine's ranges, which lie
                # inside the summaries' ends; each half is tested only where both summaries are stored.
                if c.lo != -tg.INF and (c.x[1], 'lo') in _SUMMARY and (c.y[1], 'hi') in _SUMMARY:
                    gaps.append((cx, _SUMMARY[c.x[1], 'lo'], cy, _SUMMARY[c.y[1], 'hi'], c.lo, 1))
                if c.hi != tg.INF and (c.x[1], 'hi') in _SUMMARY and (c.y[1], 'lo') in _SUMMARY:
                    gaps.append((cx, _SUMMARY[c.x[1], 'hi'], cy, _SUMMARY[c.y[1], 'lo'], c.hi, -1))
        out.append((tuple(steps), tuple(pairs), tuple(gaps)))
    return out


def _impossible(spec, idx, window):
    """Whether one relaxation set cannot be possible for patient `idx`, given its index window. `spec` is one
    entry of `_prune_plan` with each concept replaced by its posting summaries, {patient_idx: (min_start_lo,
    max_start_hi)}, and each gap's two summary ends by {patient_idx: value} (max_end_hi is None when some event of
    the concept is ongoing).

    Why this is sound. The engine's candidate events for a step are the patient's events carrying the step's
    concept (one of them, under `first` or `last`), which are exactly the events the concept's posting counts, so
    each has start_lo >= min_start_lo and start_hi <= max_start_hi, and its end, a point's being its start, at most
    max_end_hi (unbounded for an ongoing exposure, which the summary records as None, and is then never tested).
    The engine decides each start and end over the tightest range the patient's own facts leave it (`Times.dom`),
    never wider than the recorded bounds; shared variables and `proper` only narrow it. A step's unary constraints
    are on its start, and `plausible`, which `certain` implies, passes an event only if its start can reach each
    bound (latest start >= anchor + lo, earliest start <= anchor + hi). Between two steps, `plausible` needs
    value(y) - value(x) to reach [lo, hi] within the patient's distances, which are at most the bounds' differences
    (zero for one variable): hi(y) - lo(x) >= lo and lo(y) - hi(x) <= hi, for starts and ends alike. So when a step
    has no posting for its concept, or its posting cannot reach a bound, or a pair's summaries cannot reach a gap,
    every candidate domain or arc of that set empties under both `certain` and `plausible`: exact and possible
    are not found, by arc consistency, with `complete: true`, before any search."""
    steps, pairs, gaps = spec
    anchor = {'START': window['start'], 'END': window['end']}
    for postings, lows, highs in steps:
        found = postings.get(idx)
        if found is None:
            return True
        lo, hi = found
        if any(hi < anchor[a] + k for a, k in lows) or any(lo > anchor[a] + k for a, k in highs):
            return True
    for x, y, lo, hi in pairs:
        (xlo, xhi), (ylo, yhi) = x[idx], y[idx]
        if lo > hi or yhi - xlo < lo or ylo - xhi > hi:
            return True
    for x, y, k, sense in gaps:
        a, b = x[idx], y[idx]
        if a is not None and b is not None and (b - a < k if sense == 1 else b - a > k):
            return True
    return False


def _prune(store, pq, live, windows_by_idx, slice_size=None, tags=None, derived=None):
    """The candidates in `live` (sorted, strictly increasing: the summaries are read between the first and the last)
    whose result the posting summaries prove: every set the engine would try is impossible (`_impossible`), so its
    answer is `none` with `complete: true`, byte for byte, whatever the patient's events. The summaries are read for
    these candidates only, one concept and one slice of `slice_size` candidates at a time, so memory is bounded by a
    slice, not the candidate set.

    `tags` (D28) maps each tag of a value request to (concept, condition). A tag has no posting; its summaries are
    computed from the series rows carrying it (`_tag_summaries`), over exactly the events the engine lists under it,
    so the argument of `_impossible` holds for it unchanged.

    `derived` (D32) maps each derived tag to its spec. A count tag's events are events of its concept (or value tag),
    and an Nth tag's point starts inside the range of their starts, over `Times.dom`, which lies inside the recorded
    bounds, and ends when it starts; so the concept's summaries (with the latest start as the point's latest end) bound
    every event the engine lists under the tag, and the argument holds unchanged."""
    tags = tags or {}
    derived = derived or {}
    if not live:
        return set()
    if any(a >= b for a, b in zip(live, live[1:])):
        raise StoreError('PRUNE_CANDIDATES_NOT_SORTED', f'{len(live)} candidates')
    plan = _prune_plan(pq)
    concepts = sorted({c for steps, _, _ in plan for c, _, _ in steps})
    # The summary ends the gaps read, each as its own {patient_idx: value}; none without a constraint on an end.
    ends = sorted({(c, e) for _, _, gaps in plan for x, xe, y, ye, _, _ in gaps for c, e in ((x, xe), (y, ye))})
    columns = 'p.patient_idx, p.min_start_lo, p.max_start_hi' + (', p.max_end_hi' if ends else '')
    slice_size = CHUNK_PATIENTS * SET_CHUNK_FACTOR if slice_size is None else slice_size
    pruned = set()
    for k in range(0, len(live), slice_size):
        part = live[k:k + slice_size]
        summaries = {}
        try:
            _temp(store, '_m_live', [('idx', 'INTEGER')], part)

            def fetch(concept):
                if concept in summaries:
                    return summaries[concept]
                if concept in tags:
                    summaries[concept] = {idx: row if ends else row[:2]
                                          for idx, row in _tag_summaries(store, tags[concept], part).items()}
                    return summaries[concept]
                cursor = store.db.execute(f"""SELECT {columns}
                    FROM concept_postings p JOIN _m_live l ON l.idx = p.patient_idx
                    WHERE p.concept = ? AND p.patient_idx BETWEEN ? AND ?""", [concept, part[0], part[-1]])
                mine = summaries[concept] = {}
                while True:
                    rows = cursor.fetchmany(store.fetch_rows)
                    if not rows:
                        break
                    mine.update({row[0]: row[1:] for row in rows})
                return mine
            for concept in concepts:
                if concept in derived:
                    spec = derived[concept]
                    rows = fetch(spec['concept'])
                    if spec['kind'] == 'nth' and ends:  # a point ends when it starts
                        rows = {idx: (row[0], row[1], row[1]) for idx, row in rows.items()}
                    summaries[concept] = rows
                else:
                    fetch(concept)
        finally:
            _drop(store, '_m_live')
        values = {(c, e): {idx: row[e] for idx, row in summaries[c].items()} for c, e in ends}
        if ends:
            summaries = {c: {idx: row[:2] for idx, row in rows.items()} for c, rows in summaries.items()}
        specs = [([(summaries[c], lows, highs) for c, lows, highs in steps],
                  [(summaries[x], summaries[y], lo, hi) for x, y, lo, hi in pairs],
                  [(values[x, xe], values[y, ye], k, sense) for x, xe, y, ye, k, sense in gaps])
                 for steps, pairs, gaps in plan]
        pruned.update(idx for idx in part if all(_impossible(spec, idx, windows_by_idx[idx]) for spec in specs))
    return pruned


CHUNK_PATIENTS = 2_000  # candidates loaded and matched at a time
SET_CHUNK_FACTOR = 25  # set-based SQL steps (comparability) take this many times as many patients at a time
_BIG = 2 ** 64  # window bounds are clamped to +-_BIG: comparisons with BIGINT columns are unchanged


def _temp(store, name, columns, *arrays):
    """(Re)create temp table `name` with the given columns from parallel Python lists."""
    store.db.execute(f'DROP TABLE IF EXISTS {name}')
    select = ', '.join(f'unnest(CAST(? AS {t}[])) AS {c}' for c, t in columns)
    store.db.execute(f'CREATE TEMP TABLE {name} AS SELECT {select}', list(arrays))


def _drop(store, *names):
    for name in names:
        store.db.execute(f'DROP TABLE IF EXISTS {name}')


def _load_events(store, idxs, per_step, linked=(), tags=None):
    """{patient_idx: [events]} of these candidates' events that carry any step's concept or widening
    through the closure, as engine events listing only those concepts. `idxs` is sorted and meant to
    be one chunk: it goes into a temporary table that the events are joined to, and the query reads
    only the index range it spans.

    `linked` lists the candidates whose events share time variables (`patients.shared_variables`). Their
    events are matched together: an event no step can bind may still link two that can, so each such
    patient gets every event, those carrying no step concept with no concepts (D24). Any other event
    links only its own two variables, so leaving it out changes no decision.

    `tags` (D28) maps each tag of a value request to (concept, condition): see `_load_tagged`."""
    if tags:
        return _load_tagged(store, idxs, per_step, linked, tags)
    concepts = sorted({c for cs in per_step.values() for c in cs})
    if not idxs:
        return {}
    try:
        _temp(store, '_m_cand', [('idx', 'INTEGER')], list(idxs))
        rows = store.db.execute(f"""
            SELECT e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi, e.start_var, e.end_var,
                   e.proper, list(DISTINCT cc.ancestor ORDER BY cc.ancestor) AS concepts
            FROM _m_cand c JOIN events e ON e.patient_idx = c.idx
            JOIN event_concepts ec USING (patient_idx, event_id)
            JOIN concept_closure cc ON cc.concept = ec.concept
            WHERE e.patient_idx BETWEEN ? AND ? AND cc.ancestor IN {_in(concepts)}
            GROUP BY e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi, e.start_var, e.end_var,
                     e.proper
            ORDER BY e.patient_idx, e.event_id""", [min(idxs), max(idxs)] + concepts).fetchall()
    finally:
        _drop(store, '_m_cand')
    if linked:
        try:
            _temp(store, '_m_linked', [('idx', 'INTEGER')], list(linked))
            whole = store.db.execute(f"""
                SELECT e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi, e.start_var, e.end_var,
                       e.proper, list(DISTINCT cc.ancestor ORDER BY cc.ancestor)
                           FILTER (WHERE cc.ancestor IN {_in(concepts)}) AS concepts
                FROM _m_linked l JOIN events e ON e.patient_idx = l.idx
                LEFT JOIN event_concepts ec USING (patient_idx, event_id)
                LEFT JOIN concept_closure cc ON cc.concept = ec.concept
                WHERE e.patient_idx BETWEEN ? AND ?
                GROUP BY e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi, e.start_var,
                         e.end_var, e.proper
                ORDER BY e.patient_idx, e.event_id""", concepts + [min(linked), max(linked)]).fetchall()
        finally:
            _drop(store, '_m_linked')
        mine = set(linked)
        rows = [r for r in rows if r[0] not in mine] + whole
    out = {}
    for idx, eid, slo, shi, elo, ehi, sv, fv, proper, cs in rows:
        if sv is None and fv is None and not proper:  # no names of its own: the event as 2.0 loaded it
            event = {'id': eid, 'concepts': cs or [], 'start': [slo, shi]}
            if elo is not None:
                event['end'] = [elo, ehi]
        else:
            event = engine_event(eid, cs or [], [slo, shi], None if elo is None else [elo, ehi], sv, fv, proper)
        out.setdefault(idx, []).append(event)
    return out


def _load_tagged(store, idxs, per_step, linked, tags):
    """`_load_events` for a value request (D28): each event lists the request's plain concepts it carries through the
    closure and the tags it carries (`_tag_rows` over this chunk's series rows), sorted. An event carrying a tag
    carries the tag's concept (its series row's item class is one of its direct concepts, and the concept covers it),
    so the events are loaded as for the plain concepts and the tags' concepts, and each keeps only the plain concepts
    and tags it carries; an event left with none is dropped, unless its patient shares variables (then every event is
    kept, as in `_load_events`)."""
    if not idxs:
        return {}
    plain = sorted({c for cs in per_step.values() for c in cs if c not in tags})
    loaded = _load_events(store, idxs, {'': sorted(set(plain) | {concept for concept, _ in tags.values()})}, linked)
    carried = {}
    try:
        _temp(store, '_m_tagged', [('idx', 'INTEGER')], list(idxs))
        for name, spec in tags.items():
            exact, loose = _tag_rows(store, spec, 's.patient_idx, s.event_id', join='JOIN _m_tagged c ON c.idx = s.patient_idx',
                                     where='AND s.patient_idx BETWEEN ? AND ?', params=[min(idxs), max(idxs)])
            for idx, eid in exact + loose:
                carried.setdefault((idx, eid), set()).add(name)
    finally:
        _drop(store, '_m_tagged')
    keep, mine = set(plain), set(linked)
    out = {}
    for idx, events in loaded.items():
        for event in events:
            concepts = sorted({c for c in event['concepts'] if c in keep} | carried.get((idx, event['id']), set()))
            if concepts or idx in mine:
                out.setdefault(idx, []).append({**event, 'concepts': concepts})
    return out


def _comparability(store, per_step, nones, windows_by_idx, chunk=CHUNK_PATIENTS * SET_CHUNK_FACTOR, tags=None):
    """Yield (patient_idx, reason, uncovered steps, unrecorded steps) for patients in `nones` a `none` cannot stand for.

    Rule COVERAGE_RULE: a step is covered for a patient when some source that records any of the
    step's concepts (table `source_concepts`, fixed at build time) has a coverage period meeting
    the window (lo <= end and hi >= start). A step no source records at all is unrecorded; the
    reason is then NO_SOURCE_RECORDS_CONCEPT, otherwise NO_SOURCE_COVERAGE.

    The overlap test runs in SQL against a temporary table of the windows, `chunk` patients at a time.

    A tag of a value request (D28; `tags` maps each to (concept, condition)) is recorded by the sources holding a series
    row of its concept in its unit (`_tag_sources`), never more than record its concept.
    """
    if not nones:
        return
    tags = tags or {}
    concepts = sorted({c for cs in per_step.values() for c in cs if c not in tags})
    sources = {}
    for source, concept in store.query(
            f'SELECT source, concept FROM source_concepts WHERE concept IN {_in(concepts)}', concepts) if concepts else ():
        sources.setdefault(concept, set()).add(source)
    for name, spec in tags.items():
        sources[name] = _tag_sources(store, spec)
    able = {step: set().union(*(sources.get(c, set()) for c in cs)) for step, cs in per_step.items()}
    unrecorded = [step for step in per_step if not able[step]]
    reason = 'NO_SOURCE_RECORDS_CONCEPT' if unrecorded else 'NO_SOURCE_COVERAGE'
    pairs = [(step, source) for step in per_step for source in sorted(able[step])]
    clamp = lambda v: max(-_BIG, min(_BIG, v))
    try:
        _temp(store, '_m_able', [('step', 'VARCHAR'), ('source', 'VARCHAR')], [p[0] for p in pairs], [p[1] for p in pairs])
        for k in range(0, len(nones), chunk):
            part = nones[k:k + chunk]
            _temp(store, '_m_none', [('idx', 'INTEGER'), ('ws', 'HUGEINT'), ('we', 'HUGEINT')], part,
                  [clamp(windows_by_idx[i]['start']) for i in part], [clamp(windows_by_idx[i]['end']) for i in part])
            covered = dict(store.db.execute("""
                SELECT n.idx, list(DISTINCT a.step) FROM _m_none n
                JOIN coverage c ON c.patient_idx = n.idx JOIN _m_able a ON a.source = c.source
                WHERE c.lo <= n.we AND c.hi >= n.ws GROUP BY n.idx""").fetchall())
            for idx in part:
                have = covered.get(idx, ())
                missing = [step for step in per_step if step not in have]
                if missing:
                    yield idx, reason, missing, unrecorded
    finally:
        _drop(store, '_m_able', '_m_none')


def _digest(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def admits_start(scope, window, lo, hi):
    """Whether a step under `scope` could start anywhere in [lo, hi], given the index window.

    The query's scope is the only constraint every step carries: `within` keeps a step's start in the
    window, `from_start` after its start, `after` after its end, and `any` nowhere in particular. Other
    constraints only narrow this, so the test is a sound over-approximation.
    """
    if scope == 'within':
        return lo <= window['end'] and hi >= window['start']
    if scope == 'from_start':
        return hi >= window['start']
    if scope == 'after':
        return hi >= window['end']
    return True


def _excluded_starts(store, query, per_step, decided, windows_by_idx):
    """{patient_idx: ({step: starts the scope admits}, {step: all starts})} for each step's excluded records,
    for patients in `decided` (a set).

    A step's excluded records are those whose concept is one of the step's concepts or widenings, or
    a descendant of one (through the closure). It could have bound those whose start the query's
    scope admits (`admits_start`). But the engine picks a `first` or `last` target among every event
    carrying the concept and only then applies the scope, so any of them could displace a target.

    The records are read in one streamed query, in batches of `store.fetch_rows`; only those of
    `decided` patients are kept.
    """
    if not decided:
        return {}
    concepts = sorted({c for cs in per_step.values() for c in cs})
    found = {}
    cursor = store.db.execute(f"""
            SELECT x.patient_idx, cc.ancestor, x.start_lo, x.start_hi FROM excluded_records x
            JOIN concept_closure cc ON cc.concept = x.concept
            WHERE cc.ancestor IN {_in(concepts)}""", concepts)
    while True:
        rows = cursor.fetchmany(store.fetch_rows)
        if not rows:
            break
        for idx, ancestor, lo, hi in rows:
            if idx in decided:
                found.setdefault(idx, set()).add((ancestor, lo, hi))
    scope = query.get('scope', 'any')
    out = {}
    for idx, records in found.items():
        w = windows_by_idx[idx]
        every = {step: sorted({(lo, hi) for c, lo, hi in records if c in cs}) for step, cs in per_step.items()}
        every = {step: starts for step, starts in every.items() if starts}
        admitted = {step: [(lo, hi) for lo, hi in starts if admits_start(scope, w, lo, hi)] for step, starts in every.items()}
        admitted = {step: starts for step, starts in admitted.items() if starts}
        if every:
            out[idx] = (admitted, every)
    return out


def apply_exclusions(query, result, touched, starts, every=None, occurrence=None):
    """Rule EXCLUSION_RULE: what a result may still say when excluded records could have changed it.

    `touched` maps each step to the starts of the excluded records its scope admits, and `every` to
    the starts of all its excluded records (defaulting to `touched`); `starts` maps each of this
    patient's event ids to its start bounds. Adding records only adds bindings, so an `exact` or `possible`
    binding stands, except:
    - a `first` or `last` step's target can move to any of its excluded records, whether or not the
      scope admits it (`start_lo` at or before the bound event's `start_hi` for first, `start_hi` at
      or after its `start_lo` for last), since the engine applies the scope after choosing. The found
      binding may then not be a binding of the query at all, so the result is `not_comparable`,
      whatever the engine said;
    - `none` may hide a binding: `not_comparable`;
    - `relaxed` claims no unrelaxed binding exists, which an excluded record may supply: `unresolved`,
      with the relaxed match beside it, the engine's own convention for an undecided unrelaxed search;
    - `possible` claims no exact binding exists: still possible, but `complete: false`.
    The displacement test reads the binding of the result, of its relaxed match, or of the match an
    order-uncertain `unresolved` reports beside it (D30).
    Every changed result names the steps (`excluded_steps`) and the reason ADMISSION_EXCLUDED_RECORDS.

    `occurrence` (D32) overrides some steps' occurrence for the displacement test: an Nth step reads as `first` and an
    Nth-from-last step as `last`, each on its envelope point's range, which `starts` then holds. An excluded record that
    could start at or before the latest the Nth occurrence can (at or after the earliest the Nth from last can) could
    change which time is the Nth; one that cannot lies after (before) it in every choice of times.
    """
    every = touched if every is None else every
    if not every or result['status'] not in ('exact', 'possible', 'relaxed', 'unresolved', 'none'):
        return result
    order = [step['id'] for step in query['steps']]
    occurrence = {step['id']: step.get('occurrence', 'any') for step in query['steps']} | (occurrence or {})
    steps = [step for step in order if step in touched]
    pid = result['patient_id']
    incomparable = lambda which: {'patient_id': pid, 'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                                  'excluded_steps': which}
    binding = (result.get('binding') or result.get('relaxed_match', {}).get('binding')
               or result.get('unverified_match', {}).get('binding'))
    if binding is not None:
        moved = [step for step in order if step in every
                 and ((occurrence[step] == 'first' and any(lo <= starts[binding[step]][1] for lo, _ in every[step]))
                      or (occurrence[step] == 'last' and any(hi >= starts[binding[step]][0] for _, hi in every[step])))]
        if moved:
            return incomparable(moved)
    if not steps:
        return result
    if result['status'] == 'none':
        return incomparable(steps)
    if result['status'] == 'relaxed':
        relaxed = {k: result[k] for k in ('relaxations', 'cost', 'certainty', 'binding', 'method')}
        return {'patient_id': pid, 'status': 'unresolved', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                'relaxed_match': relaxed, 'excluded_steps': steps, 'complete': False}
    if result['status'] == 'possible':
        note = 'EXCLUDED_RECORDS_MAY_MAKE_EXACT'
        return {**result, 'complete': False, 'excluded_steps': steps,
                'note': result['note'] + '; ' + note if 'note' in result else note}
    return result


# Partition-parallel matching (D21 arm C).
#
# The engine runs on candidates in work units of `chunk` patients, each independent of the others.
# With `workers > 1` the units go to a pool of spawned processes; each worker opens the snapshot
# once (verify=False: the parent verified it, and the worker checks it is the same snapshot and the
# same file list) and keeps its own connection, which is never shared. The parent merges the units'
# results by patient index, so the output is the same for any worker count.

_UNITS = {'B': 1, 'KB': 10**3, 'MB': 10**6, 'GB': 10**9, 'TB': 10**12,
          'KIB': 2**10, 'MIB': 2**20, 'GIB': 2**30, 'TIB': 2**40}
WORKER_MEMORY_FLOOR = 256 * 10**6


def worker_memory_limit(limit, workers):
    """A DuckDB memory limit for each of `workers` processes sharing `limit` (a string like '4GB'; the
    default limit when None), at least 256 MB. Without it each worker would take DuckDB's default of
    80% of the machine's memory."""
    limit = _memory_limit(limit) or DEFAULT_MEMORY_LIMIT
    number, unit = re.fullmatch(r'([0-9.]+)\s*([A-Za-z]+)', limit).groups()
    each = max(WORKER_MEMORY_FLOOR, int(float(number) * _UNITS[unit.upper()] / workers))
    return f'{each // 1024}KiB'


class MatchWorkerError(StoreError):
    """A match worker crashed or raised; the whole `match` fails and returns nothing."""


_W = {}  # per worker process: its store and the prepared query it last ran


def _worker_init(path, snapshot_id, files_digest, memory_limit, barrier):
    store = Store(path, 1, False, memory_limit, expected_snapshot_id=snapshot_id)
    if store.files_digest != files_digest:
        raise StoreError('SNAPSHOT_FILE_LIST_NOT_EXPECTED', str(path))
    # atexit handlers do not run in multiprocessing children; this one removes the worker's spill directory.
    multiprocessing.util.Finalize(None, store.close, exitpriority=10)
    _W.update(store=store, key=None, barrier=barrier)


def _worker_ready():
    _W['barrier'].wait(120)
    return os.getpid()


def _peak_rss():
    """This process's peak resident set size in bytes."""
    import resource
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * (1 if sys.platform == 'darwin' else 1024)


def _worker_unit(unit):
    """Run the engine over one unit: ('ok', [(idx, result)], peak RSS) or ('error', type, message)."""
    key, query, conditions, idxs, pids, windows, linked, fault = unit
    try:
        if fault == 'exit':
            os._exit(3)
        if fault == 'raise':
            raise RuntimeError('injected failure')
        if _W['key'] != key:
            request = None if conditions is None else Request(query, conditions)
            pq = tg.PreparedQuery(query if request is None else request.query)
            _W.update(key=key, pq=pq, per_step=_step_concepts(pq.query), tags=None if request is None else request.used,
                      request=request)
        pq, per_step, store = _W['pq'], _W['per_step'], _W['store']
        if _W['request'] is not None and _W['request'].derived:  # D32
            return ('ok', _match_derived_chunk(store, pq, _W['request'], idxs, pids, windows, linked), _peak_rss())
        tags = _W['tags']  # only a value request passes tags, so a loader of the earlier signature still serves the rest
        events = _load_events(store, idxs, per_step, linked, tags) if tags else _load_events(store, idxs, per_step, linked)
        out = [(idx, tg.match_prepared(pq, {'patient_id': pid, 'window': w, 'events': events.get(idx, [])}, trusted=True))
               for idx, pid, w in zip(idxs, pids, windows)]
        return ('ok', out, _peak_rss())
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc)[:300])


class MatchPool:
    """Persistent spawned worker processes over one snapshot; reuse it across `match` calls.

    Pools outlive the `Store` they were made from and are shared: `get_pool` (which `match(workers=N)`
    uses) keeps one per (snapshot path, worker count) until `close_pools()`, which callers should call
    when done (it also runs at interpreter exit). Each worker's DuckDB memory limit is the store's (or
    the default) divided by the worker count, at least 256 MB (`worker_memory_limit`, recorded here as
    `memory_limit`, not in the match result, whose bytes must not depend on the worker count).
    `close()` (or leaving a `with` block) stops the workers. `start_seconds` is the time the workers
    took to start and open the snapshot (measured by `warm()`, which `run` calls on first use);
    `worker_peak_rss` is the largest peak resident size any worker has reported."""

    def __init__(self, store, workers, memory_limit=None):
        if not (type(workers) is int and workers >= 1):
            raise StoreError('INVALID_WORKERS', repr(workers))
        self.workers, self.path, self.snapshot_id = workers, store.path.resolve(), store.snapshot_id
        self.files_digest = store.files_digest
        self.worker_peak_rss = 0
        self.start_seconds = None
        self.pids = []
        self._context = multiprocessing.get_context('spawn')
        self._barrier = self._context.Barrier(workers)
        self.memory_limit = worker_memory_limit(memory_limit or store.memory_limit, workers)
        self.fault = None  # test hook: 'raise' or 'exit' makes every unit fail that way
        self._executor = None

    def _pool(self):
        if self._executor is None:
            from concurrent.futures import ProcessPoolExecutor
            self._executor = ProcessPoolExecutor(
                self.workers, mp_context=self._context, initializer=_worker_init,
                initargs=(str(self.path), self.snapshot_id, self.files_digest, self.memory_limit, self._barrier))
        return self._executor

    def warm(self):
        """Start every worker and wait until each has opened the snapshot; the first call times it."""
        if self.start_seconds is not None:
            return self.start_seconds
        started = time.perf_counter()
        try:
            self._barrier.reset()
            futures = [self._pool().submit(_worker_ready) for _ in range(self.workers)]
            self.pids = sorted({f.result(timeout=300) for f in futures})
        except Exception as exc:
            self.close()
            raise MatchWorkerError('MATCH_WORKER_START_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
        self.start_seconds = time.perf_counter() - started
        return self.start_seconds

    def run(self, units):
        """Each unit's results, in unit order; the first failure raises `MatchWorkerError` (after
        cancelling the rest, and discarding the workers if one died) and nothing is returned."""
        self.warm()
        futures = []
        try:
            for unit in units:
                futures.append(self._pool().submit(_worker_unit, (*unit, self.fault)))
        except Exception as exc:
            self.close()
            raise MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
        out = []
        try:
            for future in futures:
                try:
                    reply = future.result()
                except Exception as exc:  # BrokenProcessPool: a worker died
                    self.close()
                    raise MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
                if reply[0] != 'ok':
                    raise MatchWorkerError('MATCH_WORKER_FAILED', f'{reply[1]}: {reply[2]}')
                self.worker_peak_rss = max(self.worker_peak_rss, reply[2])
                out.append(reply[1])
        finally:
            for future in futures:
                future.cancel()
        return out

    def close(self):
        executor, self._executor = self._executor, None
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        self.start_seconds = None
        self.pids = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


_POOLS = {}


def get_pool(store, workers):
    """The module's pool for this snapshot path and worker count, created on first use and kept."""
    key = (str(store.path.resolve()), workers)
    pool = _POOLS.get(key)
    if pool is not None and (pool.snapshot_id, pool.files_digest) != (store.snapshot_id, store.files_digest):
        pool.close()
        pool = None
    if pool is None:
        pool = _POOLS[key] = MatchPool(store, workers)
    return pool


def close_pools():
    """Stop every pool `match(workers=...)` created."""
    while _POOLS:
        _POOLS.popitem()[1].close()


atexit.register(close_pools)


def _mentions(store, idxs):
    """{patient_idx: ids of its events whose kind is `mention`} for these candidates (D32: counts and Nth read coded
    records only)."""
    out = {}
    if idxs:
        try:
            _temp(store, '_m_kind', [('idx', 'INTEGER')], list(idxs))
            for idx, eid in store.db.execute(
                    'SELECT e.patient_idx, e.event_id FROM _m_kind c JOIN events e ON e.patient_idx = c.idx '
                    'WHERE e.patient_idx BETWEEN ? AND ? AND e.kind = ?', [min(idxs), max(idxs), MENTION_KIND]).fetchall():
                out.setdefault(idx, set()).add(eid)
        finally:
            _drop(store, '_m_kind')
    return out


def _readings(store, idxs, request):
    """{patient_idx: readings} for these candidates (D33): for every concept a relative tag of the request reads, the
    series rows of the item classes it covers (event id, item class, unit, exact value, event kind, recorded start, in
    any unit) and the excluded records of those item classes (their recorded starts), with the event kinds the
    snapshot's label `period_reading_kinds` declares period summaries."""
    concepts = sorted({request.tags[spec['concept']][0] for spec in request.derived_used.values()
                       if spec['kind'] == 'relative'})
    if not concepts or not idxs:
        return {}
    label = (store.manifest.get('labels') or {}).get(PERIOD_LABEL)
    period = frozenset(k for k in (label or '').split(',') if k)
    out = {}

    def mine(idx):
        if idx not in out:
            out[idx] = {'rows': {}, 'excluded': {}, 'period': period, 'covered': {}}
        return out[idx]
    try:
        _temp(store, '_m_read', [('idx', 'INTEGER')], list(idxs))
        rows = store.db.execute(f"""
            SELECT s.patient_idx, cc.ancestor, s.event_id, s.item_class, s.unit, s.value_decimal, e.kind, e.start_lo,
                   e.start_hi
            FROM _m_read c JOIN series s ON s.patient_idx = c.idx
            JOIN events e ON e.bucket = s.bucket AND e.patient_idx = s.patient_idx AND e.event_id = s.event_id
            JOIN concept_closure cc ON cc.concept = s.item_class
            WHERE s.patient_idx BETWEEN ? AND ? AND cc.ancestor IN {_in(concepts)}""",
                                [min(idxs), max(idxs)] + concepts).fetchall()
        for idx, concept, eid, item, unit, text, kind, lo, hi in rows:
            r = mine(idx)
            r['rows'][eid] = (item, unit, text, kind, (lo, hi))
            r['covered'].setdefault(concept, set()).add(item)
        dropped = store.db.execute(f"""
            SELECT x.patient_idx, x.concept, x.start_lo, x.start_hi
            FROM _m_read c JOIN excluded_records x ON x.patient_idx = c.idx
            WHERE x.patient_idx BETWEEN ? AND ? AND x.concept IN (
                SELECT concept FROM concept_closure WHERE ancestor IN {_in(concepts)})""",
                                   [min(idxs), max(idxs)] + concepts).fetchall()
        for idx, item, lo, hi in dropped:
            mine(idx)['excluded'].setdefault(item, []).append((lo, hi))
    finally:
        _drop(store, '_m_read')
    return out


def _derived_chunk(store, request, idxs, pids, windows, linked):
    """Each candidate's `Derived` (D32), from its loaded events: the request's plain concepts and value tags, the
    concepts and value tags its derived tags count included; with a relative condition (D33), its readings too."""
    tags = request.used
    events = _load_events(store, idxs, request.load, linked, tags) if tags else _load_events(store, idxs, request.load, linked)
    mentions = _mentions(store, idxs)
    readings = _readings(store, idxs, request) if request.relative else {}
    return [(idx, derive_patient(request, pid, w, events.get(idx, []), mentions.get(idx, set()), readings.get(idx)))
            for idx, pid, w in zip(idxs, pids, windows)]


def _match_derived_chunk(store, pq, request, idxs, pids, windows, linked):
    """[(patient_idx, result)] for one chunk of a request with derived steps (D32)."""
    return [(idx, match_derived(pq, request, derived))
            for idx, derived in _derived_chunk(store, request, idxs, pids, windows, linked)]


def _derived_reach(request, query, touched, windows_by_idx):
    """D18's reach for derived steps (D32), in place: an Nth step and a span-count step reach every excluded record of
    their concepts (an excluded record before the window can shift an in-window event into rank N, and a count's
    witness need not satisfy the scope); a period count reaches those overlapping the window (every witness and the
    bound record lie in it)."""
    for idx, (admitted, every) in touched.items():
        w = windows_by_idx[idx]
        for sid, (kind, detail) in request.kinds.items():
            if sid not in every or kind == 'relative':  # D33: a relative step's reach is its scope
                continue
            if kind == 'nth':
                if _D32_DEFECT != 'scoped_reach':
                    admitted[sid] = every[sid]
            elif detail != 'window':
                admitted[sid] = every[sid]
            else:
                hits = [(lo, hi) for lo, hi in every[sid] if lo <= w['end'] and hi >= w['start']]
                if hits:
                    admitted[sid] = hits
                else:
                    admitted.pop(sid, None)


def match_context(store, query, windows, conditions=None):
    """What a result depends on, and its digest: snapshot, query, windows, engine code, rules, verification
    level and the digest of the manifest's file list (which exact files were read); with value conditions (D28), also
    their digest and the value rule."""
    context = {'snapshot_id': store.snapshot_id, 'query_digest': _digest(query),
               'windows_digest': _digest(dict(sorted(windows.items()))),
               'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ENGINE_ARTIFACTS},
               'coverage_rule': COVERAGE_RULE, 'exclusion_rule': EXCLUSION_RULE, 'verified': store.verified,
               'files_digest': store.files_digest, 'labels': store.manifest.get('labels')}
    if conditions is not None:
        context.update(conditions_digest=_digest(conditions), value_rule=VALUE_RULE)
        if conditions.get('profile') == CONDITIONS_PROFILE_1_1:
            context.update(derived_rules=_digest(DERIVED_RULES))
        elif conditions.get('profile') == CONDITIONS_PROFILE_1_2:  # D33
            context.update(derived_rules=_digest(DERIVED_RULES), relative_rules=_digest(RELATIVE_RULES))
    return context, _digest(context)


DETAILS = ('full', 'decided')
DECIDED = ('exact', 'possible', 'relaxed', 'unresolved', 'none')


def _check_window(pid, w):
    """The refusals `tg.validate_patient` makes of a window, as plain checks with the same codes."""
    if not (type(pid) is str and pid):
        raise tg.TrajectoryError('INVALID_PATIENT_ID')
    if not (isinstance(w, dict) and len(w) == 2 and 'start' in w and 'end' in w):
        raise tg.TrajectoryError('INVALID_WINDOW')
    lo, hi = w['start'], w['end']
    if not (type(lo) is int and type(hi) is int and lo <= hi):
        raise tg.TrajectoryError('INVALID_WINDOW')


def _bound_starts(store, idxs):
    """{patient_idx: {event_id: start bounds}} of these patients' events."""
    out = {}
    if idxs:
        try:
            _temp(store, '_m_start', [('idx', 'INTEGER')], sorted(idxs))
            for idx, eid, lo, hi in store.db.execute(
                    'SELECT e.patient_idx, e.event_id, e.start_lo, e.start_hi FROM _m_start s '
                    'JOIN events e ON e.patient_idx = s.idx').fetchall():
                out.setdefault(idx, {})[eid] = [lo, hi]
        finally:
            _drop(store, '_m_start')
    return out


def match(store, query, windows, detail='full', chunk=CHUNK_PATIENTS, workers=1, conditions=None):
    """Every patient's result: the engine on candidates, `none` (FILTERED) for the rest, and
    `not_comparable` where a `none` rests on a step no covering source could have recorded, or on a
    step whose concepts the source recorded in records the ingest left out (ADMISSION_EXCLUDED_RECORDS).

    `windows` maps patient_id to {start, end} or None; a patient absent or None is excluded.

    Candidates are loaded and matched `chunk` patients at a time and only each compact result is kept,
    so memory beyond the results themselves is bounded by one chunk. The store validated every event
    at ingest, so the engine runs with `trusted=True`; windows are checked here. Before that, a candidate
    whose posting summaries prove the engine's answer `none` under every relaxation set it would try gets
    that answer without its events being loaded (`_prune`, D25); no result changes.

    `detail='full'` (the default) lists every patient in `results`, in patient order. `detail='decided'`
    lists only the patients the engine ran on (every candidate with a window) and those whose status
    comparability or exclusion changed, in patient order; it adds `'detail': 'decided'` and
    `'implicit'`, the numbers of patients left out, by the result `full` would show for them:
    `{'FILTERED': n, 'NO_INDEX_WINDOW': m}` (a `none` the candidate filter proved, and a patient
    with no window). `counts` and every other field are the same in both, and each listed result
    equals its `full` counterpart.

    `workers=1` runs everything in this process. With `workers > 1` the engine's units (`chunk`
    patients each) run in a persistent pool of spawned processes (`get_pool`; `close_pools()` stops
    them), each with its own connection to the same snapshot, opened without re-verifying (`verified`
    in the result is the level this store was opened at). Units merge by patient index, so the
    result equals `workers=1` byte for byte; a worker failure raises `MatchWorkerError`. The pools
    outlive `store` and are shared per (snapshot path, workers): call `close_pools()` when done.

    `conditions` (D28), when given, holds value conditions on steps and value relaxations (`Request`): each is compiled
    into a tag concept for the engine. The output then also carries `value_conditions` (the tags, their concepts and
    conditions) and the context their digest and the value rule; without it, nothing in the output differs.

    With profile `execution-store-conditions-1.1` (D32) `conditions` may also give a step an Nth or Nth-from-last
    occurrence or a count (`Request`), whose events the store derives per patient (`derive_patient`) and matches by
    the two-pass rule (`match_derived`). The output then carries `derived_steps` (the rules and tags) and the context
    the derived rules.
    """
    if not (type(workers) is int and workers >= 1):
        raise StoreError('INVALID_WORKERS', repr(workers))
    if detail not in DETAILS:
        raise StoreError('INVALID_DETAIL', repr(detail))
    request = None if conditions is None else Request(query, conditions)
    tags = None if request is None else request.used
    derived = request is not None and bool(request.derived)
    _require_series(store, request)
    pq = tg.PreparedQuery(query if request is None else request.query)
    given, query = query, pq.query
    patients = store.query('SELECT patient_idx, patient_id FROM patients ORDER BY patient_idx')
    # Patients whose events share time variables: their events are loaded whole (`_load_events`).
    linked = {idx for idx, in store.query('SELECT patient_idx FROM patients WHERE shared_variables')}
    by_id = {pid: idx for idx, pid in patients}
    unknown = sorted(set(windows) - set(by_id))
    if unknown:
        raise StoreError('UNKNOWN_WINDOW_PATIENT', ', '.join(unknown[:5]))
    windows_by_idx = {}
    for idx, pid in patients:
        w = windows.get(pid)
        if w is not None:
            _check_window(pid, w)
        windows_by_idx[idx] = w
    pid_of = dict(patients)
    found = _candidates(store, pq, request)
    per_step = _step_concepts(query) if not derived else request.load  # D32: a derived tag by what it counts
    # The concepts each step's engine concepts stand for (a tag by its concept): what excluded records are matched by.
    base = per_step if request is None else request.base
    live = [i for i in found['patient_idx'] if windows_by_idx[i] is not None]
    results = {}  # the engine's results, and what a rule then changed; patients absent are filtered or windowless
    parts = [live[k:k + chunk] for k in range(0, len(live), chunk)]
    if _PRUNE:
        # Candidates the posting summaries prove `none` get the engine's own `none`, and no events are loaded (D25).
        # Chunks are cut before pruning, so a chunk's index range, which bounds the loader's scan, is never wider
        # than without it; chunks of survivors cut afterwards would be sparse, and one load of 200 survivors
        # spread over 1,000,000 patients held 1.5 GiB.
        if derived:
            pruned = _prune(store, pq, live, windows_by_idx, tags=tags, derived=request.derived_used)
        else:
            pruned = _prune(store, pq, live, windows_by_idx, tags=tags) if tags else _prune(store, pq, live, windows_by_idx)
        for idx in pruned:
            results[idx] = _NONE(pid_of[idx])
        parts = [kept for part in parts if (kept := [i for i in part if i not in pruned])]
    if workers > 1 and parts:
        key = _digest(given if conditions is None else [given, conditions])
        units = [(key, given, conditions, part, [pid_of[i] for i in part], [windows_by_idx[i] for i in part],
                  [i for i in part if i in linked]) for part in parts]
        for unit in get_pool(store, workers).run(units):
            results.update(unit)
    elif derived:
        for part in parts:
            results.update(_match_derived_chunk(store, pq, request, part, [pid_of[i] for i in part],
                                                [windows_by_idx[i] for i in part], [i for i in part if i in linked]))
    else:
        for part in parts:
            mine = [i for i in part if i in linked]
            events = _load_events(store, part, per_step, mine, tags) if tags else _load_events(store, part, per_step, mine)
            for idx in part:
                results[idx] = tg.match_prepared(
                    pq, {'patient_id': pid_of[idx], 'window': windows_by_idx[idx], 'events': events.get(idx, [])}, trusted=True)
            del events
    filtered = [idx for idx, _ in patients if windows_by_idx[idx] is not None and idx not in results]
    filtered_result = lambda idx: {'patient_id': pid_of[idx], 'status': 'none', 'reason': 'FILTERED', 'complete': True}
    nones = sorted(filtered + [idx for idx, r in results.items() if r['status'] == 'none'])
    for idx, reason, missing, unrecorded in _comparability(store, per_step, nones, windows_by_idx,
                                                           chunk * SET_CHUNK_FACTOR, tags):
        results[idx] = {'patient_id': pid_of[idx], 'status': 'not_comparable', 'reason': reason,
                        'uncovered_steps': missing}
        if unrecorded:
            results[idx]['unrecorded_steps'] = unrecorded
    # Records the ingest left out can change any engine decision, not only a `none`.
    decided = {idx for idx, w in windows_by_idx.items()
               if w is not None and (results[idx]['status'] in DECIDED if idx in results else True)}
    touched = _excluded_starts(store, query, base, decided, windows_by_idx)
    # Event ids are unique within a patient only, so each patient's own events give its starts. Every binding the
    # displacement test reads needs them, the match beside an order-uncertain `unresolved` included (D31, second
    # attempt: before, its starts were not read and the test raised KeyError).
    bound = [idx for idx in touched if idx in results and (
        results[idx].get('binding') or results[idx].get('relaxed_match', {}).get('binding')
        or results[idx].get('unverified_match', {}).get('binding'))]
    starts = _bound_starts(store, bound)
    occurrence = None
    if derived:
        # D32: Nth steps reach and displace as the decision log states; a derived point's start range is its envelope.
        _derived_reach(request, query, touched, windows_by_idx)
        occurrence = {sid: 'first' if which == 'nth' else 'last'
                      for sid, (kind, which) in request.kinds.items() if kind == 'nth'}
        nth_bound = [idx for idx in bound if occurrence]
        for k in range(0, len(nth_bound), chunk):
            part = nth_bound[k:k + chunk]
            for idx, d in _derived_chunk(store, request, part, [pid_of[i] for i in part],
                                         [windows_by_idx[i] for i in part], [i for i in part if i in linked]):
                starts.setdefault(idx, {}).update(d.points)
    for idx, (hit, every) in touched.items():
        results[idx] = apply_exclusions(query, results[idx] if idx in results else filtered_result(idx),
                                        hit, starts.get(idx, {}), every, occurrence)
    counts = {}
    for r in results.values():
        counts[r['status']] = counts.get(r['status'], 0) + 1
    implicit_filtered = sum(1 for idx in filtered if idx not in results)
    windowless = sum(1 for w in windows_by_idx.values() if w is None)
    if implicit_filtered:
        counts['none'] = counts.get('none', 0) + implicit_filtered
    if windowless:
        counts['excluded'] = counts.get('excluded', 0) + windowless
    context, context_id = match_context(store, given, windows, conditions)
    out = {'profile': MATCH_PROFILE, 'engine': tg.PROFILE_ID, 'snapshot_id': store.snapshot_id,
           'verified': store.verified, 'coverage_rule': COVERAGE_RULE, 'exclusion_rule': EXCLUSION_RULE,
           'labels': store.manifest.get('labels'), 'context': context, 'context_id': context_id,
           'plan': found['plan'], 'candidates': len(found['patient_idx']), 'counts': dict(sorted(counts.items()))}
    if request is not None and (request.tags or not derived):
        out['value_conditions'] = request.describe()
    if derived:
        out['derived_steps'] = request.describe_derived()
    if detail == 'decided':
        out['detail'] = 'decided'
        out['implicit'] = {'FILTERED': implicit_filtered, 'NO_INDEX_WINDOW': windowless}
        out['results'] = [results[idx] for idx in sorted(results)]
    else:
        out['results'] = [results[idx] if idx in results else
                          filtered_result(idx) if windows_by_idx[idx] is not None else
                          {'patient_id': pid, 'status': 'excluded', 'reason': 'NO_INDEX_WINDOW'}
                          for idx, pid in patients]
    return out


def main(argv=None):
    """`python -m patterns.execution_store build INGEST.json ROOT` or
    `python -m patterns.execution_store synthetic COHORT.json ROOT` prints the snapshot directory.
    The synthetic mode streams the converted patients into the builder one at a time."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('mode', choices=('build', 'synthetic'))
    parser.add_argument('input', type=Path)
    parser.add_argument('root', type=Path)
    parser.add_argument('--threads', type=int, default=1)
    parser.add_argument('--memory-limit', default=DEFAULT_MEMORY_LIMIT, help='DuckDB memory limit, e.g. 4GB')
    args = parser.parse_args(argv)
    data = json.loads(args.input.read_text(encoding='utf-8'))
    if args.mode == 'synthetic':
        from . import synthetic_ingest as si
        skipped = {}
        with StoreBuilder(args.root, si.synthetic_header(data), args.threads, args.memory_limit) as builder:
            for patient in si.iter_patients(data, skipped):
                builder.add(patient)
            print(builder.finish(si.synthetic_provenance(data, skipped)))
        return 0
    print(build(data, args.root, args.threads, args.memory_limit))
    return 0


if __name__ == '__main__':
    sys.exit(main())
