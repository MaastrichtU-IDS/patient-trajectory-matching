"""Execution store, stage 1: immutable Parquet snapshots on embedded DuckDB, and a
relaxation-sound candidate filter in front of the trajectory-graph matcher.

`build(ingest, root)` validates an ingest document (docs/execution-store.md, "Ingest
contract"), derives six tables in DuckDB and writes them as Parquet under
`root/<snapshot_id>/`, with a `manifest.json` that `schemas/execution-store-manifest.schema.json`
validates. The snapshot identifier is a digest of the canonical inputs and of each table's
row count and canonical-content digest, computed over sorted canonical rows and never over
Parquet bytes, so it does not depend on the writer or its thread count.

`candidates(store, query)` intersects concept postings across a trajectory-graph query's
steps, rarest first. Each step's postings are the union over its concept and every concept a
`widen_concept` relaxation within the budget could put in its place, so a patient the
engine could match under any tried relaxation set is never dropped. `match(store, query,
windows)` runs `trajectory_graph.match` on the survivors, reports the rest as `none` with
reason `FILTERED`, and turns `none` into `not_comparable` when, for some step, no source
that records the step's concepts covers the patient's window.

`patterns/execution_store_reference.py` builds the same tables in pure Python, and
`patterns/test_execution_store.py` compares them row for row. DuckDB is imported lazily,
here only, so nothing else in the engine needs it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile

from jsonschema import Draft202012Validator

from . import trajectory_graph as tg
from .pro_solid import ROOT

PROFILE = 'execution-store-1.0'
INGEST_PROFILE = 'execution-store-ingest-1.0'
MATCH_PROFILE = 'execution-store-match-1.0'
SCHEMA_PATH = 'schemas/execution-store-manifest.schema.json'
MODULE_PATH = 'patterns/execution_store.py'
ORIGIN = '0001-01-01T00:00:00'
MAX_TIME = 315_537_897_599_999_999  # 9999-12-31T23:59:59.999999 from ORIGIN, in microseconds
BUCKETS = 8
CANONICAL_FORM = ('sha256 over each row as a compact UTF-8 JSON array of its values in column order, '
                  'lines sorted by code point and each terminated by a newline')

# Logical tables: column names and DuckDB types, in canonical column order.
TABLES = {
    'patients': (('patient_idx', 'INTEGER'), ('patient_id', 'VARCHAR'), ('bucket', 'INTEGER')),
    'events': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('kind', 'VARCHAR'), ('source', 'VARCHAR'),
               ('clock_id', 'VARCHAR'), ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('end_lo', 'BIGINT'),
               ('end_hi', 'BIGINT'), ('ongoing', 'BOOLEAN'), ('evidence', 'VARCHAR')),
    'event_concepts': (('patient_idx', 'INTEGER'), ('event_id', 'VARCHAR'), ('concept', 'VARCHAR'),
                       ('basis', 'VARCHAR')),
    'concept_closure': (('concept', 'VARCHAR'), ('ancestor', 'VARCHAR'), ('basis', 'VARCHAR'), ('version', 'VARCHAR')),
    'concept_postings': (('concept', 'VARCHAR'), ('patient_idx', 'INTEGER'), ('n', 'BIGINT'),
                         ('min_start_lo', 'BIGINT'), ('max_start_hi', 'BIGINT')),
    'coverage': (('patient_idx', 'INTEGER'), ('source', 'VARCHAR'), ('clock_id', 'VARCHAR'), ('lo', 'BIGINT'),
                 ('hi', 'BIGINT'), ('basis', 'VARCHAR')),
}
# Patient-keyed tables are written as hive-style bucket partitions; the others as one file.
PARTITIONED = ('events', 'event_concepts', 'coverage')
SORT = {'patients': 'patient_idx', 'events': 'patient_idx, start_lo, event_id',
        'event_concepts': 'patient_idx, concept, event_id', 'concept_closure': 'concept, ancestor',
        'concept_postings': 'concept, patient_idx', 'coverage': 'patient_idx, source, lo'}


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


def validate_ingest(ingest):
    """Refuse anything outside the ingest contract, by name."""
    _require(isinstance(ingest, dict) and ingest.get('profile') == INGEST_PROFILE, 'UNSUPPORTED_INGEST_PROFILE')
    _require(set(ingest) <= {'profile', 'dataset_id', 'provenance', 'closure', 'patients'}
             and {'dataset_id', 'closure', 'patients'} <= set(ingest), 'INVALID_INGEST_FIELDS')
    _require(type(ingest['dataset_id']) is str and ingest['dataset_id'], 'INVALID_DATASET_ID')
    closure = ingest['closure']
    _require(isinstance(closure, dict) and set(closure) == {'version', 'ancestors'}, 'INVALID_CLOSURE')
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
    patients = ingest['patients']
    _require(isinstance(patients, list) and patients, 'NO_PATIENTS')
    seen = set()
    for p in patients:
        _require(isinstance(p, dict) and set(p) == {'patient_id', 'clock_id', 'events', 'coverage'}, 'INVALID_PATIENT')
        pid = p['patient_id']
        _require(type(pid) is str and pid, 'INVALID_PATIENT_ID')
        _require(pid not in seen, 'DUPLICATE_PATIENT_ID', pid)
        seen.add(pid)
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
    return ingest


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def canonical_ingest(ingest):
    """Order-free form of an ingest: the same content in any order has the same digest."""
    patients = []
    for p in sorted(ingest['patients'], key=lambda p: p['patient_id']):
        events = [{**e, 'concepts': sorted(e['concepts'])} for e in sorted(p['events'], key=lambda e: e['event_id'])]
        coverage = sorted(p['coverage'], key=lambda c: (c['source'], c['lo'], c['hi'], c['basis']))
        patients.append({**p, 'events': events, 'coverage': coverage})
    closure = {'version': ingest['closure']['version'],
               'ancestors': {c: sorted(a) for c, a in sorted(ingest['closure']['ancestors'].items())}}
    return {**ingest, 'closure': closure, 'patients': patients}


def _closure_rows(closure):
    """concept_closure rows: each concept is its own ancestor (basis self), then its declared ones."""
    rows = []
    for concept, above in closure['ancestors'].items():
        rows.append({'concept': concept, 'ancestor': concept, 'basis': 'self', 'version': closure['version']})
        rows.extend({'concept': concept, 'ancestor': a, 'basis': 'declared', 'version': closure['version']}
                    for a in above)
    return rows


def _stage(ingest, directory):
    """Flatten the ingest into newline-delimited JSON files DuckDB reads with fixed column types."""
    files = {name: open(directory / f'{name}.ndjson', 'w', encoding='utf-8')
             for name in ('patients', 'events', 'concepts', 'closure', 'coverage')}
    def put(name, row):
        files[name].write(json.dumps(row, ensure_ascii=False, separators=(',', ':')) + '\n')
    try:
        for p in ingest['patients']:
            pid = p['patient_id']
            put('patients', {'patient_id': pid, 'clock_id': p['clock_id']})
            for e in p['events']:
                end = e.get('end') or [None, None]
                put('events', {'patient_id': pid, 'event_id': e['event_id'], 'kind': e['kind'], 'source': e['source'],
                               'start_lo': e['start'][0], 'start_hi': e['start'][1], 'end_lo': end[0],
                               'end_hi': end[1], 'evidence': _json(e['evidence'])})
                for c in e['concepts']:
                    put('concepts', {'patient_id': pid, 'event_id': e['event_id'], 'concept': c})
            for c in p['coverage']:
                put('coverage', {'patient_id': pid, **c})
        for row in _closure_rows(ingest['closure']):
            put('closure', row)
    finally:
        for f in files.values():
            f.close()


def _lit(path):
    return "'" + str(path).replace("'", "''") + "'"


def _read_json(path, columns):
    spec = '{' + ', '.join(f"'{k}': '{v}'" for k, v in columns) + '}'
    return f"read_json({_lit(path)}, format='newline_delimited', columns={spec})"


def _derive(db, stage):
    """Create the six logical tables (plus a bucket column on partitioned ones) from the staging files."""
    db.execute(f"""CREATE TABLE s_patients AS SELECT * FROM {_read_json(stage / 'patients.ndjson',
        [('patient_id', 'VARCHAR'), ('clock_id', 'VARCHAR')])}""")
    db.execute(f"""CREATE TABLE s_events AS SELECT * FROM {_read_json(stage / 'events.ndjson',
        [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('kind', 'VARCHAR'), ('source', 'VARCHAR'),
         ('start_lo', 'BIGINT'), ('start_hi', 'BIGINT'), ('end_lo', 'BIGINT'), ('end_hi', 'BIGINT'),
         ('evidence', 'VARCHAR')])}""")
    db.execute(f"""CREATE TABLE s_concepts AS SELECT * FROM {_read_json(stage / 'concepts.ndjson',
        [('patient_id', 'VARCHAR'), ('event_id', 'VARCHAR'), ('concept', 'VARCHAR')])}""")
    db.execute(f"""CREATE TABLE s_closure AS SELECT * FROM {_read_json(stage / 'closure.ndjson',
        [('concept', 'VARCHAR'), ('ancestor', 'VARCHAR'), ('basis', 'VARCHAR'), ('version', 'VARCHAR')])}""")
    db.execute(f"""CREATE TABLE s_coverage AS SELECT * FROM {_read_json(stage / 'coverage.ndjson',
        [('patient_id', 'VARCHAR'), ('source', 'VARCHAR'), ('lo', 'BIGINT'), ('hi', 'BIGINT'), ('basis', 'VARCHAR')])}""")
    # Dense index by sorted identifier; bucket from the identifier alone, so it never moves.
    db.execute(f"""CREATE TABLE p AS SELECT
        (row_number() OVER (ORDER BY patient_id) - 1)::INTEGER AS patient_idx, patient_id,
        (('0x' || substr(sha256(patient_id), 1, 8))::UBIGINT % {BUCKETS})::INTEGER AS bucket, clock_id
        FROM s_patients""")
    db.execute('CREATE TABLE patients AS SELECT patient_idx, patient_id, bucket FROM p')
    db.execute("""CREATE TABLE events AS SELECT p.patient_idx, e.event_id, e.kind, e.source, p.clock_id,
        e.start_lo, e.start_hi, e.end_lo, e.end_hi, (e.end_lo IS NOT NULL AND e.end_hi IS NULL) AS ongoing,
        e.evidence, p.bucket FROM s_events e JOIN p USING (patient_id)""")
    db.execute("""CREATE TABLE event_concepts AS SELECT p.patient_idx, c.event_id, c.concept, 'direct' AS basis,
        p.bucket FROM s_concepts c JOIN p USING (patient_id)""")
    db.execute('CREATE TABLE concept_closure AS SELECT concept, ancestor, basis, version FROM s_closure')
    db.execute("""CREATE TABLE concept_postings AS
        WITH expanded AS (SELECT DISTINCT ec.patient_idx, ec.event_id, cc.ancestor AS concept
                          FROM event_concepts ec JOIN concept_closure cc ON cc.concept = ec.concept)
        SELECT x.concept, x.patient_idx, count(*)::BIGINT AS n, min(e.start_lo) AS min_start_lo,
               max(e.start_hi) AS max_start_hi
        FROM expanded x JOIN events e USING (patient_idx, event_id) GROUP BY x.concept, x.patient_idx""")
    db.execute("""CREATE TABLE coverage AS SELECT p.patient_idx, c.source, p.clock_id, c.lo, c.hi, c.basis, p.bucket
        FROM s_coverage c JOIN p USING (patient_id)""")


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
    """Views over a snapshot's Parquet files, one per logical table, with the logical columns only."""
    for name, columns in TABLES.items():
        cols = ', '.join(c for c, _ in columns)
        files = snapshot / name / '**' / '*.parquet' if name in PARTITIONED else snapshot / f'{name}.parquet'
        db.execute(f'CREATE OR REPLACE VIEW {name} AS SELECT {cols} FROM read_parquet({_lit(files)}, hive_partitioning = false)')


def content_digest(rows):
    """Canonical-content digest of a table: see CANONICAL_FORM."""
    lines = sorted(json.dumps(list(r), ensure_ascii=False, separators=(',', ':')) for r in rows)
    h = hashlib.sha256()
    for line in lines:
        h.update(line.encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()


def _table_rows(db, name):
    cols = ', '.join(c for c, _ in TABLES[name])
    return db.execute(f'SELECT {cols} FROM {name}').fetchall()


def builder_artifacts():
    return {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (MODULE_PATH, SCHEMA_PATH)}


def snapshot_identity(manifest):
    """The digest that names a snapshot: canonical inputs, builder artifacts, per-table counts and content."""
    identity = {'profile': manifest['profile'], 'input_digest': manifest['input_digest'],
                'closure_version': manifest['closure_version'], 'builder_artifacts': manifest['builder']['artifacts'],
                'tables': {n: {'rows': t['rows'], 'content_digest': t['content_digest']}
                           for n, t in sorted(manifest['tables'].items())}}
    return hashlib.sha256(_json(identity).encode('utf-8')).hexdigest()


def validate_manifest(manifest):
    schema = json.loads((ROOT / SCHEMA_PATH).read_text())
    errors = sorted(Draft202012Validator(schema).iter_errors(manifest), key=lambda e: list(e.path))
    if errors:
        raise StoreError('INVALID_MANIFEST', errors[0].message)
    if snapshot_identity(manifest) != manifest['snapshot_id']:
        raise StoreError('SNAPSHOT_ID_MISMATCH', manifest['snapshot_id'])
    return manifest


def build(ingest, root, threads=1):
    """Validate, derive and write one immutable snapshot under `root`; return its directory.

    An existing snapshot with the same identifier is left untouched and returned.
    """
    validate_ingest(ingest)
    duckdb = _duckdb()
    canonical = canonical_ingest(ingest)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='.building-', dir=root))
    try:
        stage, snapshot = work / 'stage', work / 'snapshot'
        stage.mkdir()
        snapshot.mkdir()
        _stage(canonical, stage)
        db = duckdb.connect(':memory:', config={'threads': threads})
        try:
            _derive(db, stage)
            _write(db, snapshot)
        finally:
            db.close()
        # Digests come from the files as written, read back through a fresh connection.
        reader = duckdb.connect(':memory:', config={'threads': threads})
        try:
            _attach(reader, snapshot)
            tables = {}
            for name, columns in TABLES.items():
                rows = _table_rows(reader, name)
                tables[name] = {'rows': len(rows), 'content_digest': content_digest(rows),
                                'columns': [{'name': c, 'type': t} for c, t in columns],
                                'path': name if name in PARTITIONED else f'{name}.parquet',
                                'partitioned_by': 'bucket' if name in PARTITIONED else None}
        finally:
            reader.close()
        manifest = {'profile': PROFILE, 'snapshot_id': '', 'dataset_id': ingest['dataset_id'],
                    'input_digest': hashlib.sha256(_json(canonical).encode('utf-8')).hexdigest(),
                    'closure_version': ingest['closure']['version'],
                    'time': {'unit': 'microsecond', 'origin': ORIGIN, 'calendar': 'patient_local'},
                    'buckets': BUCKETS, 'canonical_form': CANONICAL_FORM,
                    'builder': {'artifacts': builder_artifacts(), 'engine': 'duckdb', 'engine_version': duckdb.__version__},
                    'tables': tables}
        manifest['snapshot_id'] = snapshot_identity(manifest)
        validate_manifest(manifest)
        (snapshot / 'manifest.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
        target = root / manifest['snapshot_id']
        if target.exists():
            existing = json.loads((target / 'manifest.json').read_text())
            if existing.get('snapshot_id') != manifest['snapshot_id']:
                raise StoreError('SNAPSHOT_DIRECTORY_CONFLICT', str(target))
        else:
            os.replace(snapshot, target)
        return target
    finally:
        shutil.rmtree(work, ignore_errors=True)


class Store:
    """A read-only view of one snapshot."""

    def __init__(self, path, threads=1):
        self.path = Path(path)
        self.manifest = validate_manifest(json.loads((self.path / 'manifest.json').read_text()))
        self.snapshot_id = self.manifest['snapshot_id']
        self.db = _duckdb().connect(':memory:', config={'threads': threads})
        _attach(self.db, self.path)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def rows(self, table):
        """Every row of a logical table in column order, sorted."""
        return sorted(_table_rows(self.db, table), key=lambda r: json.dumps(list(r), ensure_ascii=False))

    def verify(self):
        """Recompute each table's count and content digest from the files; refuse on any drift."""
        for name, recorded in self.manifest['tables'].items():
            rows = _table_rows(self.db, name)
            if len(rows) != recorded['rows'] or content_digest(rows) != recorded['content_digest']:
                raise StoreError('SNAPSHOT_CONTENT_MISMATCH', name)
        return True

    def query(self, sql, params=()):
        return self.db.execute(sql, list(params)).fetchall()


def open_store(path, threads=1):
    return Store(path, threads)


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


def candidates(store, query):
    """Patients who could match under some relaxation set the engine would try.

    Sound, not exact: for each step, the union of postings over its concept and its
    widenings; intersected across steps, rarest first. No time pruning in stage 1.
    """
    tg.validate_query(query)
    per_step = _step_concepts(query)
    _known(store, sorted({c for cs in per_step.values() for c in cs}))
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


def _load_events(store, idxs, concepts):
    """Candidates' events that carry any query concept through the closure, as engine events."""
    if not idxs:
        return {}
    rows = store.query(f"""
        SELECT e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi,
               list(DISTINCT cc.ancestor ORDER BY cc.ancestor) AS concepts
        FROM events e JOIN event_concepts ec USING (patient_idx, event_id)
        JOIN concept_closure cc ON cc.concept = ec.concept
        WHERE cc.ancestor IN {_in(concepts)} AND e.patient_idx IN {_in(idxs)}
        GROUP BY e.patient_idx, e.event_id, e.start_lo, e.start_hi, e.end_lo, e.end_hi
        ORDER BY e.patient_idx, e.event_id""", list(concepts) + list(idxs))
    out = {}
    for idx, eid, slo, shi, elo, ehi, cs in rows:
        event = {'id': eid, 'concepts': list(cs), 'start': [slo, shi]}
        if elo is not None:
            event['end'] = [elo, ehi]
        out.setdefault(idx, []).append(event)
    return out


def _uncovered(store, per_step, idxs, windows_by_idx):
    """{patient_idx: steps no source covers}: a step is covered when some source that records any of
    its concepts (anywhere in the snapshot) has a coverage period meeting the patient's window."""
    if not idxs:
        return {}
    concepts = sorted({c for cs in per_step.values() for c in cs})
    sources = {}
    for concept, source in store.query(f"""
            SELECT DISTINCT cc.ancestor, e.source FROM events e JOIN event_concepts ec USING (patient_idx, event_id)
            JOIN concept_closure cc ON cc.concept = ec.concept WHERE cc.ancestor IN {_in(concepts)}""", concepts):
        sources.setdefault(concept, set()).add(source)
    periods = {}
    for idx, source, lo, hi in store.query(
            f'SELECT patient_idx, source, lo, hi FROM coverage WHERE patient_idx IN {_in(idxs)}', idxs):
        periods.setdefault(idx, []).append((source, lo, hi))
    out = {}
    for idx in idxs:
        w = windows_by_idx[idx]
        missing = []
        for step, cs in per_step.items():
            able = set().union(*(sources.get(c, set()) for c in cs))
            if not any(s in able and lo <= w['end'] and hi >= w['start'] for s, lo, hi in periods.get(idx, ())):
                missing.append(step)
        if missing:
            out[idx] = missing
    return out


def match(store, query, windows):
    """Every patient's result: the engine on candidates, `none` (FILTERED) for the rest, and
    `not_comparable` where a `none` rests on a step no covering source could have recorded.

    `windows` maps patient_id to {start, end} or None; a patient absent or None is excluded.
    """
    tg.validate_query(query)
    patients = store.query('SELECT patient_idx, patient_id FROM patients ORDER BY patient_idx')
    by_id = {pid: idx for idx, pid in patients}
    unknown = sorted(set(windows) - set(by_id))
    if unknown:
        raise StoreError('UNKNOWN_WINDOW_PATIENT', ', '.join(unknown[:5]))
    windows_by_idx = {}
    for idx, pid in patients:
        w = windows.get(pid)
        if w is not None:
            tg.validate_patient({'patient_id': pid, 'window': w})
        windows_by_idx[idx] = w
    found = candidates(store, query)
    per_step = _step_concepts(query)
    query_concepts = sorted({c for cs in per_step.values() for c in cs})
    live = [i for i in found['patient_idx'] if windows_by_idx[i] is not None]
    events = _load_events(store, live, query_concepts)
    live = set(live)
    results = {}
    for idx, pid in patients:
        if windows_by_idx[idx] is None:
            results[idx] = {'patient_id': pid, 'status': 'excluded', 'reason': 'NO_INDEX_WINDOW'}
        elif idx in live:
            results[idx] = tg.match(query, {'patient_id': pid, 'window': windows_by_idx[idx], 'events': events.get(idx, [])})
        else:
            results[idx] = {'patient_id': pid, 'status': 'none', 'reason': 'FILTERED', 'complete': True}
    nones = [idx for idx, r in results.items() if r['status'] == 'none']
    for idx, steps in _uncovered(store, per_step, nones, windows_by_idx).items():
        results[idx] = {'patient_id': results[idx]['patient_id'], 'status': 'not_comparable',
                        'reason': 'NO_SOURCE_COVERAGE', 'uncovered_steps': steps}
    ordered = [results[idx] for idx, _ in patients]
    counts = {}
    for r in ordered:
        counts[r['status']] = counts.get(r['status'], 0) + 1
    return {'profile': MATCH_PROFILE, 'engine': tg.PROFILE_ID, 'snapshot_id': store.snapshot_id,
            'plan': found['plan'], 'candidates': len(found['patient_idx']), 'counts': dict(sorted(counts.items())),
            'results': ordered}


def main(argv=None):
    """`python -m patterns.execution_store build INGEST.json ROOT` or
    `python -m patterns.execution_store synthetic COHORT.json ROOT` prints the snapshot directory."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument('mode', choices=('build', 'synthetic'))
    parser.add_argument('input', type=Path)
    parser.add_argument('root', type=Path)
    parser.add_argument('--threads', type=int, default=1)
    args = parser.parse_args(argv)
    data = json.loads(args.input.read_text(encoding='utf-8'))
    if args.mode == 'synthetic':
        from .synthetic_ingest import synthetic_ingest
        data = synthetic_ingest(data)
    print(build(data, args.root, args.threads))
    return 0


if __name__ == '__main__':
    sys.exit(main())
