"""Private SQLite retention for admitted recorded jobs; not a source of new claims.

A completed snapshot must pass the same completeness checks as an export. Hashes
catch accidental corruption, not a malicious writer with database access.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import fcntl
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading

SCHEMA_VERSION = 1
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
_KEY = re.compile(r'[0-9a-f]{32}\Z')
_LABEL = re.compile(r'[A-Za-z][A-Za-z0-9_.:-]{0,63}\Z')
TERMINAL = ('completed', 'failed', 'interrupted')


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def _encode(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise ValueError('Invalid durable job JSON') from error
    if len(raw) > MAX_PAYLOAD_BYTES:
        raise ValueError('Durable job exceeds 8 MiB')
    return raw


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _key(value):
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ValueError('Invalid durable job identifier')
    return value


def _label(value):
    if not isinstance(value, str) or not _LABEL.fullmatch(value):
        raise ValueError('Audit metadata must be a short identifier, not free text')
    return value


class RecordedStore:
    """One configuration per database, with atomic job/evidence/audit transitions.

    `max_bytes` bounds retained canonical job payloads, not SQLite page overhead.
    Old terminal jobs and audit records are pruned explicitly at configured limits.
    Interrupted intents may be resumed while retained; no result is inferred.
    """
    def __init__(self, path, configuration, *, max_jobs=100,
                 max_bytes=64 * 1024 * 1024, max_audit=10000):
        for value in (max_jobs, max_bytes, max_audit):
            if type(value) is not int or value < 1:
                raise ValueError('Store retention limits must be positive integers')
        if not isinstance(configuration, dict):
            raise ValueError('Store configuration must be a JSON object')
        self.configuration_id = _hash(_encode(configuration))
        self.max_jobs, self.max_bytes, self.max_audit = max_jobs, max_bytes, max_audit
        self._lock = threading.RLock()
        self._closed = False
        self._lease = None
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError('Durable database must not be a symbolic link')
        if self.path.parent.is_symlink():
            raise ValueError('Durable state directory must not be a symbolic link')
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = self.path.parent.stat()
        if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != os.geteuid()
                or directory.st_mode & 0o077):
            raise ValueError('Durable state directory must be owner-only (mode 0700)')
        if self.path.exists():
            existing = self.path.stat()
            if (not stat.S_ISREG(existing.st_mode) or existing.st_uid != os.geteuid()
                    or existing.st_mode & 0o077):
                raise ValueError('Durable database must be an owner-only regular file')
        # Create privately before sqlite opens it; never temporarily world readable.
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        os.close(fd)
        os.chmod(self.path, 0o600)
        self._db = sqlite3.connect(str(self.path), timeout=10, isolation_level=None,
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        try:
            lease_path = str(self.path) + '.lock'
            self._lease = os.open(lease_path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
            lease_stat = os.fstat(self._lease)
            if (not stat.S_ISREG(lease_stat.st_mode) or lease_stat.st_uid != os.geteuid()
                    or lease_stat.st_mode & 0o077):
                raise ValueError('Durable lock must be an owner-only regular file')
            try:
                fcntl.flock(self._lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError('Durable database is already owned by a running instance') from error
            self._db.execute('PRAGMA foreign_keys=ON')
            self._db.execute('PRAGMA journal_mode=DELETE')
            self._db.execute('PRAGMA synchronous=FULL')
            # FULL auto-vacuum prevents deleted retained blobs accumulating on disk.
            self._db.execute('PRAGMA auto_vacuum=FULL')
            self._initialize()
            if self._db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise ValueError('Durable database integrity check failed')
        except Exception:
            self._db.close()
            if self._lease is not None:
                os.close(self._lease)
                self._lease = None
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            self._db.execute('BEGIN IMMEDIATE')
            try:
                yield
                self._db.execute('COMMIT')
            except BaseException:
                self._db.execute('ROLLBACK')
                raise

    def _initialize(self):
        with self._transaction():
            names = {r[0] for r in self._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if names:
                if not {'metadata', 'jobs', 'audit'} <= names:
                    raise ValueError('Unknown durable database schema')
                metadata = dict(self._db.execute('SELECT key,value FROM metadata'))
                if metadata.get('schema_version') != str(SCHEMA_VERSION):
                    raise ValueError('Unsupported durable database schema version')
                if metadata.get('configuration_id') != self.configuration_id:
                    raise ValueError('Durable database belongs to a different configuration')
                return
            self._db.execute('CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            self._db.executemany('INSERT INTO metadata VALUES(?,?)', [
                ('schema_version', str(SCHEMA_VERSION)), ('configuration_id', self.configuration_id)])
            self._db.execute('''CREATE TABLE jobs(
                id TEXT PRIMARY KEY, state TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, payload BLOB NOT NULL, sha256 TEXT NOT NULL,
                payload_bytes INTEGER NOT NULL)''')
            self._db.execute('''CREATE TABLE audit(
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                actor TEXT NOT NULL, action TEXT NOT NULL, job_id TEXT, status TEXT NOT NULL)''')

    def _audit(self, actor, action, job_id, status):
        actor, action, status = _label(actor), _label(action), _label(status)
        if job_id is not None:
            _key(job_id)
        self._db.execute('INSERT INTO audit(timestamp,actor,action,job_id,status) VALUES(?,?,?,?,?)',
                         (_now(), actor, action, job_id, status))
        self._db.execute('DELETE FROM audit WHERE sequence NOT IN '
                         '(SELECT sequence FROM audit ORDER BY sequence DESC LIMIT ?)', (self.max_audit,))

    def audit(self, actor, action, job_id=None, status='recorded'):
        with self._transaction():
            self._audit(actor, action, job_id, status)

    def _read(self, key):
        row = self._db.execute('SELECT * FROM jobs WHERE id=?', (_key(key),)).fetchone()
        if row is None:
            raise KeyError('Unknown or expired durable job')
        raw = bytes(row['payload'])
        if len(raw) != row['payload_bytes'] or _hash(raw) != row['sha256']:
            raise ValueError('Durable job integrity check failed')
        try:
            record = json.loads(raw)
            if (record['id'] != row['id'] or record['state'] != row['state']
                    or record['created_at'] != row['created_at']
                    or record['updated_at'] != row['updated_at']
                    or record['configuration_id'] != self.configuration_id
                    or record['schema_version'] != SCHEMA_VERSION):
                raise ValueError('Durable job metadata integrity check failed')
        except (KeyError, TypeError, UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('Durable job integrity check failed') from error
        if record['state'] == 'completed':
            self._validate_complete(record, record['job'], record['snapshot'])
        return record

    def _write(self, record):
        raw = _encode(record)
        if len(raw) > self.max_bytes:
            raise ValueError('Durable job exceeds configured retention capacity')
        self._db.execute('''INSERT INTO jobs VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at,
            payload=excluded.payload, sha256=excluded.sha256, payload_bytes=excluded.payload_bytes''',
            (record['id'], record['state'], record['created_at'], record['updated_at'],
             raw, _hash(raw), len(raw)))
        while True:
            count, size = self._db.execute('SELECT COUNT(*),COALESCE(SUM(payload_bytes),0) FROM jobs').fetchone()
            if count <= self.max_jobs and size <= self.max_bytes:
                break
            victim = self._db.execute("SELECT id FROM jobs WHERE state IN ('completed','failed','interrupted') "
                                      'AND id != ? ORDER BY updated_at,id LIMIT 1', (record['id'],)).fetchone()
            if victim is None:
                raise ValueError('Durable retention capacity is occupied by active jobs')
            self._db.execute('DELETE FROM jobs WHERE id=?', (victim['id'],))
            self._audit('system', 'evicted', victim['id'], 'expired')

    def record_started(self, key, request, *, service_id=None, actor='owner', source_context=None):
        _key(key)
        if not isinstance(request, dict):
            raise ValueError('Durable request must be an object')
        if service_id is not None:
            _key(service_id)
        now = _now()
        record = {'schema_version': SCHEMA_VERSION, 'configuration_id': self.configuration_id,
                  'id': key, 'state': 'queued', 'request': request, 'service_id': service_id,
                  'created_at': now, 'updated_at': now, 'source_context': source_context,
                  'job': None, 'snapshot': None, 'error_code': None}
        with self._transaction():
            if self._db.execute('SELECT 1 FROM jobs WHERE id=?', (key,)).fetchone():
                raise ValueError('Durable job identifier already exists')
            self._write(record)
            self._audit(actor, 'accepted', key, 'queued')
        return self.get(key)

    def record_running(self, key, service_id, *, actor='owner'):
        _key(service_id)
        with self._transaction():
            record = self._read(key)
            if record['state'] not in ('queued', 'interrupted'):
                raise ValueError('Only a queued or interrupted job can start')
            record.update(state='running', service_id=service_id, updated_at=_now(), error_code=None)
            self._write(record)
            self._audit(actor, 'started', key, 'running')
        return self.get(key)

    @staticmethod
    def _validate_complete(record, job, snapshot):
        from app.recorded_export import _validate_snapshot
        if not isinstance(job, dict) or job.get('status') != 'COMPLETED':
            raise ValueError('Only completed service jobs may be retained as evidence')
        if (not isinstance(snapshot, dict)
                or snapshot.get('profile') != record['request'].get('profile')
                or snapshot.get('job_id') not in (record['id'], record['service_id'])):
            raise ValueError('Completed evidence belongs to another durable job')
        if job.get('id') not in (record['id'], record['service_id']):
            raise ValueError('Completed service job identifier differs')
        if record['source_context'] is not None and record['source_context'] != snapshot.get('source_context'):
            raise ValueError('Completed evidence source context differs from the accepted job')
        _validate_snapshot(snapshot)
        summary = deepcopy(job.get('summary'))
        if not isinstance(summary, dict):
            raise ValueError('Completed service job must retain its summary')
        summary.pop('elapsed_seconds', None)
        if summary != snapshot['temporal_result']:
            raise ValueError('Completed service summary differs from retained evidence')
        if job.get('request') != snapshot.get('request'):
            raise ValueError('Completed service request differs from retained evidence')

    def record_complete(self, key, job, snapshot, *, actor='owner'):
        with self._transaction():
            record = self._read(key)
            if record['state'] not in ('queued', 'running'):
                raise ValueError('Only an active job can complete')
            self._validate_complete(record, job, snapshot)
            record.update(state='completed', job=job, snapshot=snapshot,
                          source_context=snapshot['source_context'], updated_at=_now())
            self._write(record)
            self._audit(actor, 'completed', key, 'completed')
        return self.get(key)

    def record_failure(self, key, code='execution_failed', *, actor='owner'):
        _label(code)
        with self._transaction():
            record = self._read(key)
            if record['state'] not in ('queued', 'running', 'interrupted'):
                raise ValueError('Only an unfinished job can fail')
            record.update(state='failed', error_code=code, updated_at=_now())
            self._write(record)
            self._audit(actor, 'failed', key, 'failed')
        return self.get(key)

    def get(self, key):
        with self._lock:
            return self._read(key)

    def list_jobs(self):
        with self._lock:
            return [self._read(row['id']) for row in self._db.execute(
                'SELECT id FROM jobs ORDER BY created_at DESC,id').fetchall()]

    def resumable(self):
        return [record for record in self.list_jobs() if record['state'] in ('queued', 'interrupted')]

    def recover(self):
        """Call once at exclusive instance startup, never while workers are live."""
        with self._transaction():
            keys = [row['id'] for row in self._db.execute(
                "SELECT id FROM jobs WHERE state IN ('queued','running')").fetchall()]
            for key in keys:
                record = self._read(key)
                record.update(state='interrupted', service_id=None, updated_at=_now())
                self._write(record)
                self._audit('system', 'recovered', key, 'interrupted')
        return self.resumable()

    def audit_events(self):
        with self._lock:
            return [dict(row) for row in self._db.execute('SELECT * FROM audit ORDER BY sequence')]

    def close(self):
        with self._lock:
            if not self._closed:
                self._db.close()
                if self._lease is not None:
                    os.close(self._lease)
                    self._lease = None
                self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
