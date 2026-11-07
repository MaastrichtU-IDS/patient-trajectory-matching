"""Execution store: immutable Parquet snapshots on embedded DuckDB, built by streaming one patient
at a time, and a relaxation-sound candidate filter in front of the trajectory-graph matcher.

`StoreBuilder(root, header)` takes the ingest header (profile, dataset id, closure), then one
patient per `add()`, then the provenance in `finish()`. Each `add()` validates the patient against
the closure, appends its rows to staging files and its digest to a staged patient list, so Python
holds one patient at a time. `finish()` derives eight tables in a file-backed DuckDB database under
a memory limit and writes them as Parquet under `root/<snapshot_id>/`, with a `manifest.json` that
`schemas/execution-store-manifest.schema.json` validates. `build(ingest, root)` does the same for a
whole ingest document. Docs: docs/execution-store.md; decisions D19 and D20.

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
windows)` runs `trajectory_graph.match` on the survivors, reports the rest as `none` with
reason `FILTERED`, and turns `none` into `not_comparable` when, for some step, no source
that records the step's concepts (table `source_concepts`) covers the patient's window, or
when the patient holds excluded records (table `excluded_records`) a step could have bound. Its
output carries the snapshot's labels and a `context` (snapshot, query and window digests,
engine artifact digests, rules, verification level, labels) and that context's digest, `context_id`.

`patterns/execution_store_reference.py` builds the same tables and digests in pure Python, and
`patterns/test_execution_store.py` compares them table by table and bucket by bucket. DuckDB is
imported lazily, here only, so nothing else in the engine needs it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

from jsonschema import Draft202012Validator

from . import trajectory_graph as tg
from ._resources import ROOT

PROFILE = 'execution-store-2.0'
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
DEFAULT_MEMORY_LIMIT = '4GB'
CANONICAL_FORM = ('per table and bucket (an unpartitioned table is the one bucket "all"; a partitioned one has buckets '
                  '0..7, empty ones included): sha256 over its rows ordered by the table key, then every column, '
                  'each row a compact UTF-8 JSON array of its values in column order followed by a newline; the table '
                  'digest is sha256 over "<bucket>\\t<bucket digest>\\n" for its buckets in order')
INPUT_FORM = ('sha256 over the canonical JSON of {profile, dataset_id, closure, provenance}, a newline, then '
              '"<patient_id>\\t<sha256 of the patient\'s canonical JSON>\\n" for each patient in code-point order of '
              'patient_id; a patient is canonical with events sorted by event_id, each event\'s concepts sorted, '
              'coverage and excluded records sorted, and keys sorted')

# Logical tables: column names and DuckDB types, in canonical column order.
TABLES = {
    'patients': (('patient_idx', 'INTEGER'), ('patient_id', 'VARCHAR'), ('bucket', 'INTEGER')),
    'events': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('kind', 'VARCHAR'), ('source', 'VARCHAR'),
               ('clock_id', 'VARCHAR'), ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('end_lo', 'BIGINT'),
               ('end_hi', 'BIGINT'), ('ongoing', 'BOOLEAN'), ('evidence', 'VARCHAR')),
    'event_concepts': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('concept', 'VARCHAR'),
                       ('basis', 'VARCHAR')),
    'concept_closure': (('concept', 'VARCHAR'), ('ancestor', 'VARCHAR'), ('basis', 'VARCHAR'), ('version', 'VARCHAR'),
                        ('concept_basis', 'VARCHAR'), ('ancestor_basis', 'VARCHAR')),
    'concept_postings': (('concept', 'VARCHAR'), ('patient_idx', 'INTEGER'), ('n', 'BIGINT'),
                         ('min_start_lo', 'BIGINT'), ('max_start_hi', 'BIGINT')),
    'coverage': (('patient_idx', 'INTEGER'), ('source', 'VARCHAR'), ('clock_id', 'VARCHAR'), ('lo', 'BIGINT'),
                 ('hi', 'BIGINT'), ('basis', 'VARCHAR')),
    'source_concepts': (('source', 'VARCHAR'), ('concept', 'VARCHAR'), ('basis', 'VARCHAR')),
    'excluded_records': (('patient_idx', 'INTEGER'), ('source', 'VARCHAR'), ('concept', 'VARCHAR'),
                         ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('reason', 'VARCHAR'), ('n', 'BIGINT')),
}
# The only column types a table may have: no floats or decimals, whose text forms are not canonical.
ALLOWED_TYPES = ('INTEGER', 'BIGINT', 'BOOLEAN', 'VARCHAR')
# Each table's key: unique within the table, so ordering by it is total. Every column follows as a
# tie-break, so that even a file with a repeated key digests the same in any row order.
KEYS = {'patients': ('patient_idx',), 'events': ('patient_idx', 'event_id'),
        'event_concepts': ('patient_idx', 'event_id', 'concept'), 'concept_closure': ('concept', 'ancestor'),
        'concept_postings': ('concept', 'patient_idx'), 'coverage': ('patient_idx', 'source', 'lo', 'hi', 'basis'),
        'source_concepts': ('source', 'concept'),
        'excluded_records': ('patient_idx', 'source', 'concept', 'start_lo', 'start_hi', 'reason')}
# Patient-keyed tables are written as hive-style bucket partitions; the others as one file.
PARTITIONED = ('events', 'event_concepts', 'coverage', 'excluded_records')
# Row order inside the Parquet files, for reads by patient or concept. Identity never depends on it.
SORT = {'patients': 'patient_idx', 'events': 'patient_idx, start_lo, event_id',
        'event_concepts': 'patient_idx, concept, event_id', 'concept_closure': 'concept, ancestor',
        'concept_postings': 'concept, patient_idx', 'coverage': 'patient_idx, source, lo',
        'source_concepts': 'concept, source', 'excluded_records': 'patient_idx, concept, start_lo, reason'}
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


def validate_patient(p, ancestors):
    """One patient against the closure's concepts. Duplicate patient ids are a whole-stream check."""
    _require(isinstance(p, dict) and {'patient_id', 'clock_id', 'events', 'coverage'} <= set(p)
             and set(p) <= {'patient_id', 'clock_id', 'events', 'coverage', 'box_representable', 'excluded'},
             'INVALID_PATIENT')
    pid = p['patient_id']
    _require(type(pid) is str and pid and not any(c in pid for c in _ID_DELIMITERS), 'INVALID_PATIENT_ID')
    # The store holds boxes (independent bounds per time). A source whose constraints go beyond
    # them, or share variables between events, cannot be stored without losing soundness.
    _require(type(p.get('box_representable', True)) is bool, 'INVALID_PATIENT', pid)
    _require(p.get('box_representable', True), 'INGEST_PATIENT_NOT_BOX_REPRESENTABLE', pid)
    _require(type(p['clock_id']) is str and p['clock_id'], 'INVALID_CLOCK_ID', pid)
    _require(isinstance(p['events'], list), 'INVALID_EVENTS', pid)
    ids = set()
    for e in p['events']:
        _require(isinstance(e, dict) and {'event_id', 'kind', 'source', 'concepts', 'start', 'evidence'} <= set(e)
                 and set(e) <= {'event_id', 'kind', 'source', 'concepts', 'start', 'end', 'evidence'},
                 'INVALID_EVENT', pid)
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
        _require(isinstance(e['evidence'], dict), 'INVALID_EVIDENCE', where)
        try:
            json.dumps(e['evidence'], allow_nan=False)
        except (TypeError, ValueError):
            raise IngestError('INVALID_EVIDENCE', where) from None
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
    return p


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


def canonical_patient(p):
    """Order-free form of one patient: the same content in any order has the same digest."""
    out = {**p, 'events': [{**e, 'concepts': sorted(e['concepts'])} for e in sorted(p['events'], key=lambda e: e['event_id'])],
           'coverage': sorted(p['coverage'], key=lambda c: (c['source'], c['lo'], c['hi'], c['basis']))}
    if 'excluded' in p:
        out['excluded'] = sorted(p['excluded'], key=lambda x: (x['source'], x['concept'], x['start'], x['reason']))
    return out


def header_line(header, provenance):
    """The first line of the input digest's text: see INPUT_FORM."""
    return _json({'profile': header['profile'], 'dataset_id': header['dataset_id'],
                  'closure': canonical_closure(header['closure']), 'provenance': provenance})


def row_line(row):
    """One row of the canonical content: a compact JSON array of its values, newline-terminated."""
    return json.dumps(list(row), ensure_ascii=False, separators=(',', ':')) + '\n'


def bucket_labels(name):
    return [str(b) for b in range(BUCKETS)] if name in PARTITIONED else [WHOLE]


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


def _read_json(path, columns):
    spec = '{' + ', '.join(f"'{k}': '{v}'" for k, v in columns) + '}'
    return f"read_json({_lit(path)}, format='newline_delimited', columns={spec})"


STAGED = {
    'patients': [('patient_id', 'VARCHAR'), ('clock_id', 'VARCHAR'), ('digest', 'VARCHAR')],
    'events': [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('kind', 'VARCHAR'), ('source', 'VARCHAR'),
               ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('end_lo', 'BIGINT'), ('end_hi', 'BIGINT'),
               ('evidence', 'VARCHAR')],
    'concepts': [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('concept', 'VARCHAR')],
    'closure': [('concept', 'VARCHAR'), ('ancestor', 'VARCHAR'), ('basis', 'VARCHAR'), ('version', 'VARCHAR'),
                ('concept_basis', 'VARCHAR'), ('ancestor_basis', 'VARCHAR')],
    'coverage': [('patient_id', 'VARCHAR'), ('source', 'VARCHAR'), ('lo', 'BIGINT'), ('hi', 'BIGINT'), ('basis', 'VARCHAR')],
    'excluded': [('patient_id', 'VARCHAR'), ('source', 'VARCHAR'), ('concept', 'VARCHAR'), ('start_lo', 'BIGINT'),
                 ('start_hi', 'BIGINT'), ('reason', 'VARCHAR')],
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


def _load_patients(db, stage):
    db.execute(f"CREATE TABLE s_patients AS SELECT * FROM {_read_json(stage / 'patients.ndjson', STAGED['patients'])}")
    duplicate = db.execute('SELECT patient_id FROM s_patients GROUP BY patient_id HAVING count(*) > 1 '
                           'ORDER BY patient_id LIMIT 1').fetchone()
    _require(duplicate is None, 'DUPLICATE_PATIENT_ID', duplicate[0] if duplicate else '')


def _derive(db, stage):
    """Create the eight logical tables (plus a bucket column on partitioned ones) from the staging files."""
    staged = lambda name: _read_json(stage / f'{name}.ndjson', STAGED[name])
    # Dense index by sorted identifier; bucket from the identifier alone, so it never moves.
    db.execute(f"""CREATE TABLE p AS SELECT
        (row_number() OVER (ORDER BY patient_id ASC) - 1)::INTEGER AS patient_idx, patient_id,
        (('0x' || substr(sha256(patient_id), 1, 8))::UBIGINT % {BUCKETS})::INTEGER AS bucket, clock_id
        FROM s_patients""")
    db.execute('CREATE TABLE patients AS SELECT patient_idx, patient_id, bucket FROM p')
    db.execute(f"""CREATE TABLE events AS SELECT p.patient_idx, e.event_id, e.kind, e.source, p.clock_id,
        e.start_lo, e.start_hi, e.end_lo, e.end_hi, (e.end_lo IS NOT NULL AND e.end_hi IS NULL) AS ongoing,
        e.evidence, p.bucket FROM {staged('events')} e JOIN p USING (patient_id)""")
    db.execute(f"""CREATE TABLE event_concepts AS SELECT p.patient_idx, c.event_id, c.concept, 'direct' AS basis,
        p.bucket FROM {staged('concepts')} c JOIN p USING (patient_id)""")
    db.execute(f'''CREATE TABLE concept_closure AS SELECT concept, ancestor, basis, version, concept_basis, ancestor_basis
        FROM {staged('closure')}''')
    db.execute("""CREATE TABLE concept_postings AS
        WITH expanded AS (SELECT DISTINCT ec.patient_idx, ec.event_id, cc.ancestor AS concept
                          FROM event_concepts ec JOIN concept_closure cc ON cc.concept = ec.concept)
        SELECT x.concept, x.patient_idx, count(*)::BIGINT AS n, min(e.start_lo) AS min_start_lo,
               max(e.start_hi) AS max_start_hi
        FROM expanded x JOIN events e USING (patient_idx, event_id) GROUP BY x.concept, x.patient_idx""")
    db.execute(f"""CREATE TABLE coverage AS SELECT p.patient_idx, c.source, p.clock_id, c.lo, c.hi, c.basis, p.bucket
        FROM {staged('coverage')} c JOIN p USING (patient_id)""")
    # Which sources record each concept (through the closure), fixed at build time so that a
    # comparability answer never depends on which patients a query happens to see.
    db.execute("""CREATE TABLE source_concepts AS SELECT DISTINCT e.source, cc.ancestor AS concept,
        'inferred_from_records' AS basis
        FROM events e JOIN event_concepts ec USING (patient_idx, event_id)
        JOIN concept_closure cc ON cc.concept = ec.concept""")
    # Identical excluded records (same patient, source, concept, start and reason) are one row with a count.
    db.execute(f"""CREATE TABLE excluded_records AS SELECT p.patient_idx, x.source, x.concept, x.start_lo, x.start_hi,
        x.reason, count(*)::BIGINT AS n, p.bucket
        FROM {staged('excluded')} x JOIN p USING (patient_id)
        GROUP BY p.patient_idx, x.source, x.concept, x.start_lo, x.start_hi, x.reason, p.bucket""")
    db.execute('DROP TABLE p')
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
            if db.execute(f'SELECT count(*) FROM {name}').fetchone()[0] == 0:
                db.execute(f"COPY (SELECT {cols}, bucket FROM {name}) TO {_lit(target / 'empty.parquet')} (FORMAT parquet)")
            else:
                db.execute(f"""COPY (SELECT {cols}, bucket FROM {name} ORDER BY {SORT[name]}) TO {_lit(target)}
                               (FORMAT parquet, PARTITION_BY (bucket), WRITE_PARTITION_COLUMNS true)""")
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


def content_digests(db, fetch_rows=FETCH_ROWS, on_table=None):
    """{table: {rows, content_digest, buckets: {label: {rows, content_digest}}}}, streamed from `db`.

    Each bucket is one ordered query whose rows are fetched `fetch_rows` at a time and hashed as
    they arrive (CANONICAL_FORM), so Python holds one batch, never a table.
    """
    out = {}
    for name, columns in TABLES.items():
        cols = ', '.join(c for c, _ in columns)
        buckets = {}
        for label in bucket_labels(name):
            where, params = (' WHERE bucket = ?', [int(label)]) if name in PARTITIONED else ('', [])
            cursor = db.execute(f'SELECT {cols} FROM {name}{where} ORDER BY {_order(name)}', params)
            h, n = hashlib.sha256(), 0
            while True:
                rows = cursor.fetchmany(fetch_rows)
                if not rows:
                    break
                n += len(rows)
                h.update(''.join(row_line(r) for r in rows).encode('utf-8'))
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
    `on_stage(name, info)`, when given, is called after each stage of `finish()`.

    `work_dir`, when given, holds the staging files, the build database and DuckDB's spill files
    (for example on another disk); the snapshot itself is always written under `root`, so that
    publishing it is one rename. Each build works in `.building-*` directories, removed when the
    build ends however it ends; a killed process leaves them behind, and `sweep_stale_builds`
    removes old ones.
    """

    def __init__(self, root, header, threads=1, memory_limit=DEFAULT_MEMORY_LIMIT, fetch_rows=FETCH_ROWS,
                 on_stage=None, work_dir=None):
        self.header = validate_header(header)  # before anything is written
        self.ancestors = header['closure']['ancestors']
        self.threads, self.memory_limit, self.fetch_rows = threads, _memory_limit(memory_limit), fetch_rows
        if type(fetch_rows) is not int or fetch_rows < 1:
            raise StoreError('INVALID_FETCH_ROWS', repr(fetch_rows))
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
        self._files = {name: open(self.stage / f'{name}.ndjson', 'w', encoding='utf-8') for name in STAGED}
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

    def _put(self, name, row):
        self._files[name].write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')

    def add(self, patient):
        """Validate one patient, stage its rows and its digest. Refusal discards the whole build."""
        self._check_open()
        try:
            validate_patient(patient, self.ancestors)
            canonical = canonical_patient(patient)
            pid = canonical['patient_id']
            self._put('patients', {'patient_id': pid, 'clock_id': canonical['clock_id'],
                                   'digest': _sha256(_json(canonical))})
            for e in canonical['events']:
                end = e.get('end') or [None, None]
                self._put('events', {'patient_id': pid, 'event_id': e['event_id'], 'kind': e['kind'],
                                     'source': e['source'], 'start_lo': e['start'][0], 'start_hi': e['start'][1],
                                     'end_lo': end[0], 'end_hi': end[1], 'evidence': _json(e['evidence'])})
                for c in e['concepts']:
                    self._put('concepts', {'patient_id': pid, 'event_id': e['event_id'], 'concept': c})
            for c in canonical['coverage']:
                self._put('coverage', {'patient_id': pid, **c})
            for x in canonical.get('excluded', []):
                self._put('excluded', {'patient_id': pid, 'source': x['source'], 'concept': x['concept'],
                                       'start_lo': x['start'][0], 'start_hi': x['start'][1], 'reason': x['reason']})
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
            self._stage_done('staged', patients=self.patients, events=self.events,
                             staged_bytes=sum(f.stat().st_size for f in self.stage.iterdir()))
            spill = self.work / 'spill'
            spill.mkdir()
            duckdb = _duckdb()
            db = _connect(self.work / 'build.duckdb', self.threads, self.memory_limit, spill)
            try:
                _load_patients(db, self.stage)
                input_digest = _input_digest(db, self.header, provenance, self.fetch_rows)
                self._stage_done('patients_loaded_and_input_digest')
                _derive(db, self.stage)
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
        for f in self._files.values():
            f.close()
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
          work_dir=None):
    """Validate, derive and write one immutable snapshot of a whole ingest document under `root`;
    return its directory. A wrapper over `StoreBuilder`: the patients are streamed from the list."""
    header = _validate_document(ingest)
    with StoreBuilder(root, header, threads, memory_limit, fetch_rows, on_stage, work_dir) as builder:
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
        """Re-derive every table's per-bucket digests from the files; refuse on any drift."""
        derived = self.derived_tables()
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


def candidates(store, query):
    """Patients who could match under some relaxation set the engine would try.

    Sound, not exact: for each step, the union of postings over its concept and its
    widenings; intersected across steps, rarest first. No time pruning in stage 1.
    """
    return _candidates(store, tg.PreparedQuery(query))


def _candidates(store, pq):
    query = pq.query
    per_step = _step_concepts(query)
    _known(store, sorted({c for cs in per_step.values() for c in cs}))
    _visible_to_exclusions(store, sorted({c for cs in per_step.values() for c in cs}))
    plan = []
    for step, concepts in per_step.items():
        patients = {r[0] for r in store.query(
            f'SELECT DISTINCT patient_idx FROM concept_postings WHERE concept IN {_in(concepts)}', concepts)}
        plan.append({'step': step, 'concepts': concepts, 'patients': patients})
    plan.sort(key=lambda s: (len(s['patients']), s['step']))
    survivors = None
    for s in plan:
        survivors = set(s['patients']) if survivors is None else survivors & s['patients']
        if not survivors:
            break
    return {'patient_idx': sorted(survivors or ()),
            'plan': [{'step': s['step'], 'concepts': s['concepts'], 'patients': len(s['patients'])} for s in plan]}


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


def _load_events(store, idxs, per_step):
    """{patient_idx: [events]} of these candidates' events that carry any step's concept or widening
    through the closure, as engine events listing only those concepts. `idxs` is sorted and meant to
    be one chunk: it goes into a temporary table that the events are joined to, and the query reads
    only the index range it spans."""
    concepts = sorted({c for cs in per_step.values() for c in cs})
    if not idxs:
        return {}
    try:
        _temp(store, '_m_cand', [('idx', 'INTEGER')], list(idxs))
        rows = store.db.execute(f"""
            SELECT e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi,
                   list(DISTINCT cc.ancestor ORDER BY cc.ancestor) AS concepts
            FROM _m_cand c JOIN events e ON e.patient_idx = c.idx
            JOIN event_concepts ec USING (patient_idx, event_id)
            JOIN concept_closure cc ON cc.concept = ec.concept
            WHERE e.patient_idx BETWEEN ? AND ? AND cc.ancestor IN {_in(concepts)}
            GROUP BY e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi
            ORDER BY e.patient_idx, e.event_id""", [min(idxs), max(idxs)] + concepts).fetchall()
    finally:
        _drop(store, '_m_cand')
    out = {}
    for idx, eid, slo, shi, elo, ehi, cs in rows:
        event = {'id': eid, 'concepts': cs, 'start': [slo, shi]}
        if elo is not None:
            event['end'] = [elo, ehi]
        out.setdefault(idx, []).append(event)
    return out


def _comparability(store, per_step, nones, windows_by_idx, chunk=CHUNK_PATIENTS * SET_CHUNK_FACTOR):
    """Yield (patient_idx, reason, uncovered steps, unrecorded steps) for patients in `nones` a `none` cannot stand for.

    Rule COVERAGE_RULE: a step is covered for a patient when some source that records any of the
    step's concepts (table `source_concepts`, fixed at build time) has a coverage period meeting
    the window (lo <= end and hi >= start). A step no source records at all is unrecorded; the
    reason is then NO_SOURCE_RECORDS_CONCEPT, otherwise NO_SOURCE_COVERAGE.

    The overlap test runs in SQL against a temporary table of the windows, `chunk` patients at a time.
    """
    if not nones:
        return
    concepts = sorted({c for cs in per_step.values() for c in cs})
    sources = {}
    for source, concept in store.query(
            f'SELECT source, concept FROM source_concepts WHERE concept IN {_in(concepts)}', concepts):
        sources.setdefault(concept, set()).add(source)
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


def apply_exclusions(query, result, touched, starts, every=None):
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
    Every changed result names the steps (`excluded_steps`) and the reason ADMISSION_EXCLUDED_RECORDS.
    """
    every = touched if every is None else every
    if not every or result['status'] not in ('exact', 'possible', 'relaxed', 'unresolved', 'none'):
        return result
    order = [step['id'] for step in query['steps']]
    occurrence = {step['id']: step.get('occurrence', 'any') for step in query['steps']}
    steps = [step for step in order if step in touched]
    pid = result['patient_id']
    incomparable = lambda which: {'patient_id': pid, 'status': 'not_comparable', 'reason': 'ADMISSION_EXCLUDED_RECORDS',
                                  'excluded_steps': which}
    binding = result.get('binding') or result.get('relaxed_match', {}).get('binding')
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


def match_context(store, query, windows):
    """What a result depends on, and its digest: snapshot, query, windows, engine code, rules, verification
    level and the digest of the manifest's file list (which exact files were read)."""
    context = {'snapshot_id': store.snapshot_id, 'query_digest': _digest(query),
               'windows_digest': _digest(dict(sorted(windows.items()))),
               'artifacts': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in ENGINE_ARTIFACTS},
               'coverage_rule': COVERAGE_RULE, 'exclusion_rule': EXCLUSION_RULE, 'verified': store.verified,
               'files_digest': store.files_digest, 'labels': store.manifest.get('labels')}
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


def match(store, query, windows, detail='full', chunk=CHUNK_PATIENTS):
    """Every patient's result: the engine on candidates, `none` (FILTERED) for the rest, and
    `not_comparable` where a `none` rests on a step no covering source could have recorded, or on a
    step whose concepts the source recorded in records the ingest left out (ADMISSION_EXCLUDED_RECORDS).

    `windows` maps patient_id to {start, end} or None; a patient absent or None is excluded.

    Candidates are loaded and matched `chunk` patients at a time and only each compact result is kept,
    so memory beyond the results themselves is bounded by one chunk. The store validated every event
    at ingest, so the engine runs with `trusted=True`; windows are checked here.

    `detail='full'` (the default) lists every patient in `results`, in patient order. `detail='decided'`
    lists only the patients the engine ran on (every candidate with a window) and those whose status
    comparability or exclusion changed, in patient order; it adds `'detail': 'decided'` and
    `'implicit'`, the numbers of patients left out, by the result `full` would show for them:
    `{'FILTERED': n, 'NO_INDEX_WINDOW': m}` (a `none` the candidate filter proved, and a patient
    with no window). `counts` and every other field are the same in both, and each listed result
    equals its `full` counterpart.
    """
    if detail not in DETAILS:
        raise StoreError('INVALID_DETAIL', repr(detail))
    pq = tg.PreparedQuery(query)
    query = pq.query
    patients = store.query('SELECT patient_idx, patient_id FROM patients ORDER BY patient_idx')
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
    found = _candidates(store, pq)
    per_step = _step_concepts(query)
    live = [i for i in found['patient_idx'] if windows_by_idx[i] is not None]
    results = {}  # the engine's results, and what a rule then changed; patients absent are filtered or windowless
    for k in range(0, len(live), chunk):
        part = live[k:k + chunk]
        events = _load_events(store, part, per_step)
        for idx in part:
            results[idx] = tg.match_prepared(
                pq, {'patient_id': pid_of[idx], 'window': windows_by_idx[idx], 'events': events.get(idx, [])}, trusted=True)
        del events
    filtered = [idx for idx, _ in patients if windows_by_idx[idx] is not None and idx not in results]
    filtered_result = lambda idx: {'patient_id': pid_of[idx], 'status': 'none', 'reason': 'FILTERED', 'complete': True}
    nones = sorted(filtered + [idx for idx, r in results.items() if r['status'] == 'none'])
    for idx, reason, missing, unrecorded in _comparability(store, per_step, nones, windows_by_idx,
                                                           chunk * SET_CHUNK_FACTOR):
        results[idx] = {'patient_id': pid_of[idx], 'status': 'not_comparable', 'reason': reason,
                        'uncovered_steps': missing}
        if unrecorded:
            results[idx]['unrecorded_steps'] = unrecorded
    # Records the ingest left out can change any engine decision, not only a `none`.
    decided = {idx for idx, w in windows_by_idx.items()
               if w is not None and (results[idx]['status'] in DECIDED if idx in results else True)}
    touched = _excluded_starts(store, query, per_step, decided, windows_by_idx)
    # Event ids are unique within a patient only, so each patient's own events give its starts.
    starts = _bound_starts(store, [idx for idx in touched if idx in results and (
        results[idx].get('binding') or results[idx].get('relaxed_match', {}).get('binding'))])
    for idx, (hit, every) in touched.items():
        results[idx] = apply_exclusions(query, results[idx] if idx in results else filtered_result(idx),
                                        hit, starts.get(idx, {}), every)
    counts = {}
    for r in results.values():
        counts[r['status']] = counts.get(r['status'], 0) + 1
    implicit_filtered = sum(1 for idx in filtered if idx not in results)
    windowless = sum(1 for w in windows_by_idx.values() if w is None)
    if implicit_filtered:
        counts['none'] = counts.get('none', 0) + implicit_filtered
    if windowless:
        counts['excluded'] = counts.get('excluded', 0) + windowless
    context, context_id = match_context(store, query, windows)
    out = {'profile': MATCH_PROFILE, 'engine': tg.PROFILE_ID, 'snapshot_id': store.snapshot_id,
           'verified': store.verified, 'coverage_rule': COVERAGE_RULE, 'exclusion_rule': EXCLUSION_RULE,
           'labels': store.manifest.get('labels'), 'context': context, 'context_id': context_id,
           'plan': found['plan'], 'candidates': len(found['patient_idx']), 'counts': dict(sorted(counts.items()))}
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
