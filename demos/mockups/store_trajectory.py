"""The trajectory page's queries answered by the execution store, on the synthetic cohort (D29 stage 2, D37).

`server.py` serves two endpoints from this module:

- `GET /api/trajectory-store/status`: whether the store is ready, its snapshot and profiles, or why there is none;
- `POST /api/trajectory-store/match`: the page's query as its translator writes it (`trajectory/store_request.js`,
  `storeRequest`): `{"query", "conditions", "windows"}`, every time in whole minutes from each patient's birth. The
  answer is `store_conditions.match`'s (D38, the one entry point), which routes a gene step's request to
  `store_conditions_gene` (under coded only too, the joint profile, D42), a coded-only request (profile 1.3, D39) to
  `store_conditions_coded`, and every other to
  `store_conditions_relative.match`, which hands every request but a rise's (profile 1.2) to `execution_store.match`
  unchanged, with times turned back into minutes from birth and each bound event described from the store's own rows
  (kind, source, record, recorded times, value); a gene step's point by the genetic result it comes from.

**The store.** At startup the cohort the page downloads (`data/synthetic-paediatric-rare.json`) is built into a
store by the converter itself emitting series, with the period label (`store_parity.build`, as the parity test and
D36 build it). The build takes about a second and goes into a fresh temporary directory, removed when the server
closes cleanly or on SIGTERM, SIGINT or SIGHUP (never on SIGKILL, which no process can catch); `root` (the server's
`--trajectory-store-root`) puts that directory somewhere else, never inside this checkout or any git working tree.

**Per-patient answers, for synthetic data only.** Unlike the MIMIC endpoints (`store_query.py`, aggregate counts
only), these answer per patient: the data are synthetic and the page already holds every record of the cohort. A
content check keeps it so. The cohort's dataset id must be the generator's, and the built snapshot's dataset id and
patients must be exactly those of the cohort file the server serves; otherwise nothing is served.

**Named refusals.** A request whose shape is wrong gets a named `RequestError` (`by: server`); one the store or the
engine refuses gets their code (`StoreRefused`, `by: store`). Times must be whole minutes (integers),
so no string or float reaches the conversion to the store's clock.

The windows are the page's (it places each patient's index period by its own rule, over its own reconciled view of
the records). The store takes windows as input; it reads no generator truth itself. Since D52 a period placed by an
event is a hidden step of the query, and the page sends a constant base window with an `index` block naming the step
and its offsets; the server narrows each base window to the hull of the periods the store's records allow
(`index_hull.py`, `index_hull_v1`), which never changes what the request matches, and answers with the narrowed windows
and the reason for each patient it excluded (`index`). Since D52c the narrowed request is simplified before it is matched
(`index_simplify.py`, `index_simplify_v1`: implied order relations dropped, a hidden step merged into an identical user
step, or a `first` hidden step bounded only from above matched as an `any` step), each rewrite giving the same truth value
in every world, and the answer is mapped back (`index.simplify` says what was done); and the narrowing's ranges for the
page's template anchors are computed when the store opens (`prefill_s`), before the server reports ready.
"""
from __future__ import annotations

import atexit
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

PROFILE = 'mockup-trajectory-store-1.0'
COHORT = HERE / 'data' / 'synthetic-paediatric-rare.json'
SYNTHETIC_DATASET = 'synthetic-paediatric-rare-v0'
CODE = re.compile(r'[A-Z][A-Z0-9_]*')
MINUTE = 60_000_000
TIME_LIMIT = 10 ** 9  # minutes, about 1,900 years: far beyond any window or gap the page writes
REQUEST_KEYS = {'query', 'conditions', 'windows', 'index', 'index_anchor'}
WORKERS = 1  # the server matches on one worker; D52's narrowing runs its queries on as many DuckDB threads (review, S3)
BUILD_WAIT = 120  # seconds a request waits for the startup build
HOW_TO_BUILD = ['python demos/mockups/synthetic/generate.py',
                'python -m pip install -r patterns/requirements-store.lock.txt',
                'python demos/mockups/server.py']


class RequestError(ValueError):
    """A request the server refuses by name, before the store sees it; the message is the code."""

    by = 'server'


class StoreRefused(RequestError):
    """A request the store or the engine refuses by name; the message is their code."""

    by = 'store'


class Unavailable(RuntimeError):
    """No store to answer from; the message is the code."""


def require(condition, code):
    if not condition:
        raise RequestError(code)


def _minutes(value, code='TIME_MUST_BE_WHOLE_MINUTES'):
    """A time in whole minutes: an integer (not a bool), within TIME_LIMIT (else TIME_OUT_OF_RANGE)."""
    require(type(value) is int, code)
    require(-TIME_LIMIT <= value <= TIME_LIMIT, 'TIME_OUT_OF_RANGE')
    return value


def inside_repository(path):
    """Whether `path` lies in this checkout or in any git working tree, judged on its nearest existing ancestor by file
    identity (so a path spelled in another case on a case-insensitive disk is still inside)."""
    path = Path(os.path.abspath(os.path.expanduser(str(path))))
    existing = next(p for p in (path, *path.parents) if p.exists())
    for p in (existing, *existing.parents):
        try:
            if os.path.samefile(p, ROOT) or (p / '.git').exists():
                return True
        except OSError:
            continue
    return False


def check_request(spec, patient_ids):
    """The request's shape, before anything is converted: (query, conditions, windows). Only the fields the conversion
    to the store's clock reads are checked here; the store and the engine refuse everything else by name."""
    require(isinstance(spec, dict) and REQUEST_KEYS >= set(spec) and {'query', 'windows'} <= set(spec), 'REQUEST_FIELDS')
    query, conditions, windows = spec['query'], spec.get('conditions'), spec['windows']
    require(isinstance(query, dict), 'QUERY_MUST_BE_AN_OBJECT')
    require(isinstance(query.get('relations', []), list), 'RELATIONS_MUST_BE_A_LIST')
    require(isinstance(query.get('relaxations', []), list), 'RELAXATIONS_MUST_BE_A_LIST')
    for r in query.get('relations', []):
        require(isinstance(r, dict), 'RELATION_MUST_BE_AN_OBJECT')
        for k in ('min', 'max', 'grace'):
            if k in r:
                _minutes(r[k], 'TIME_MUST_BE_WHOLE_MINUTES')
    for x in query.get('relaxations', []):
        require(isinstance(x, dict), 'RELAXATION_MUST_BE_AN_OBJECT')
        for k in ('max', 'grace'):
            if k in x:
                _minutes(x[k], 'TIME_MUST_BE_WHOLE_MINUTES')
    if conditions is not None:
        require(isinstance(conditions, dict), 'CONDITIONS_MUST_BE_AN_OBJECT')
        steps = conditions.get('steps', {})
        require(isinstance(steps, dict) and all(isinstance(s, dict) for s in steps.values()), 'CONDITION_STEPS_MUST_BE_OBJECTS')
        for raw in steps.values():
            count = raw.get('count')
            if count is not None:
                require(isinstance(count, dict), 'COUNT_MUST_BE_AN_OBJECT')
                if isinstance(count.get('within'), dict):
                    _minutes(count['within'].get('span'), 'TIME_MUST_BE_WHOLE_MINUTES')
            relative = raw.get('relative')
            if relative is not None:
                require(isinstance(relative, dict) and isinstance(relative.get('baseline'), dict), 'RELATIVE_MUST_HAVE_A_BASELINE')
                _minutes(relative['baseline'].get('within'), 'TIME_MUST_BE_WHOLE_MINUTES')
    index = spec.get('index')
    if index is not None:  # D52: the hidden steps that place the period, and their offsets (index_hull.py)
        require(isinstance(index, dict) and set(index) == {'rule', 'start', 'end'} and index['rule'] == 'index_anchor_v1',
                'INDEX_MUST_NAME_ITS_RULE_AND_SIDES')
        steps = {s.get('id') for s in query.get('steps', []) if isinstance(s, dict)}
        for side in ('start', 'end'):
            x = index[side]
            if x is not None:
                require(isinstance(x, dict) and set(x) == {'step', 'offset'} and x['step'] in steps, 'INDEX_SIDE_MUST_NAME_A_STEP')
                _minutes(x['offset'], 'TIME_MUST_BE_WHOLE_MINUTES')
        require(index['start'] is not None or index['end'] is not None, 'INDEX_SIDE_MUST_NAME_A_STEP')
    point = spec.get('index_anchor')
    if point is not None:  # D52: a period kept as D49's point window, and why (echoed in the answer for the page)
        require(isinstance(point, dict) and set(point) == {'mode', 'reason'} and point['mode'] == 'point'
                and isinstance(point['reason'], str) and CODE.fullmatch(point['reason']), 'INDEX_ANCHOR_MUST_BE_A_POINT_AND_A_REASON')
        require(index is None, 'INDEX_ANCHOR_AND_INDEX_TOGETHER')
    require(isinstance(windows, dict), 'WINDOWS_MUST_BE_AN_OBJECT')
    for pid, w in windows.items():
        require(pid in patient_ids, 'UNKNOWN_WINDOW_PATIENT')
        if w is not None:
            require(isinstance(w, dict) and set(w) == {'start', 'end'}, 'WINDOW_MUST_BE_START_AND_END')
            _minutes(w['start'], 'TIME_MUST_BE_WHOLE_MINUTES')
            _minutes(w['end'], 'TIME_MUST_BE_WHOLE_MINUTES')
    return query, conditions, windows


def refusal_code(error):
    """The stable code of a store or engine refusal, or None when the error carries none."""
    code = getattr(error, 'code', None) or str(error).split(':')[0].strip()
    return code if CODE.fullmatch(code or '') else None


class TrajectoryStore:
    """The synthetic cohort in an execution store, built on a background thread at `start()`."""

    def __init__(self, cohort=COHORT, root=None):
        self.cohort_path = Path(cohort)
        self.root = None if root is None else Path(root).expanduser().resolve()
        self._ready = threading.Event()
        self._lock = threading.Lock()  # one DuckDB connection: one query at a time
        self._error = None
        self._store = None
        self._scratch = None
        self._thread = None
        self.built_s = None
        self.prefill_s = None  # D52c: the narrowing's cache filled for the page's template anchors at the start
        self.prefill_keys = []
        self.prefill_error = None  # D52c review, S1: a failed pre-fill is reported; the store is still offered

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._open, name='trajectory-store-build', daemon=True)
            self._thread.start()
            atexit.register(self.close)
        return self

    def _open(self):
        try:
            self._build()
        except Unavailable as error:
            self._error = str(error)
        except Exception:  # noqa: BLE001 - the status names the failure; no detail leaves the server
            self._error = 'STORE_BUILD_FAILED'
            self._cleanup()
        finally:
            self._ready.set()

    def _build(self):
        started = time.perf_counter()
        try:
            import duckdb  # noqa: F401 - the store's dependency, named when missing
            from patterns import execution_store as es
            import store_parity
        except ImportError:
            raise Unavailable('STORE_DEPENDENCY_MISSING') from None
        if not self.cohort_path.is_file():
            raise Unavailable('NO_SYNTHETIC_COHORT')
        cohort = json.loads(self.cohort_path.read_text())
        if cohort.get('dataset_id') != SYNTHETIC_DATASET:
            raise Unavailable('NOT_THE_SYNTHETIC_COHORT')
        if self.root is not None:
            if inside_repository(self.root):
                raise Unavailable('STORE_ROOT_INSIDE_REPOSITORY')
            self.root.mkdir(parents=True, exist_ok=True)
        self._scratch = Path(tempfile.mkdtemp(prefix='ptm-trajectory-store-', dir=self.root))
        snapshot = store_parity.build(cohort, self._scratch / 'store')
        store = es.open_store(snapshot)
        ids = sorted(pid for pid, in store.query('SELECT patient_id FROM patients'))
        if store.manifest.get('dataset_id') != SYNTHETIC_DATASET or ids != sorted(p['patient_id'] for p in cohort['patients']):
            store.close()
            raise Unavailable('STORE_NOT_THE_SERVED_COHORT')
        self.births = store_parity.birth_micros(cohort)
        self.events = self._describe_all(store)
        self.patients = len(ids)
        # D52c (pre-registered): the narrowing's ranges (and the simplification's facts) for the page's template anchors,
        # before the store is offered, so readiness waits for them and no user request pays them (reported: prefill_s).
        # A failure here takes nothing down (review, S1): it is named in the status (`prefill_error`), the store is
        # offered, and a request computes its ranges and facts on first use, as before D52c.
        filled = time.perf_counter()
        try:
            import index_hull
            import index_simplify
            self.prefill_keys = index_hull.prefill(store, threads=WORKERS)
            index_simplify.prefill(store, sorted({k[0] for k in index_hull.PREFILL}))
        except Exception:  # noqa: BLE001 - the status names the failure; no detail leaves the server
            self.prefill_keys, self.prefill_error = [], 'PREFILL_FAILED'
        self.prefill_s = round(time.perf_counter() - filled, 3)
        self._store = store
        self.built_s = round(time.perf_counter() - started, 2)

    def _describe_all(self, store):
        """{(patient, event id): description}: what the page shows for a bound event, from the store's own rows."""
        rows = store.query('''SELECT p.patient_id, e.event_id, e.kind, e.source, e.start_lo, e.start_hi, e.end_lo, e.end_hi,
                                     e.ongoing, ev.evidence, s.value_lexical, s.unit
                              FROM events e JOIN patients p USING (patient_idx)
                              LEFT JOIN event_evidence ev USING (patient_idx, event_id)
                              LEFT JOIN series s USING (patient_idx, event_id)''')
        from patterns import store_conditions_gene as gc
        out = {}
        for pid, eid, kind, source, slo, shi, elo, ehi, ongoing, evidence, value, unit in rows:
            evidence = json.loads(evidence) if evidence else {}
            mention = evidence.get('mention') or {}
            d = {'id': eid, 'kind': kind, 'source': source, 'record_id': evidence.get('record_id'),
                 'record_kind': evidence.get('record_kind'),
                 'display': (evidence.get('coding') or {}).get('display') or mention.get('text'),
                 'as_written': (evidence.get('occurred') or {}).get('as_written'),
                 'start': [slo, shi]}
            if elo is not None or ongoing:
                d['end'] = [elo, ehi]
            if ongoing:
                d['ongoing'] = True
            if value is not None:
                d['value'], d['unit'] = value, unit
            if 'gene_reports' in evidence:  # D38: the time of its first report that is not negative, if any
                verdict, span = gc.finding(evidence['gene_reports'])
                if verdict == 'found':
                    d['finding'] = span[0]
            out[pid, eid] = d
        return out

    def _wait(self):
        if not self._ready.wait(BUILD_WAIT):
            raise Unavailable('STORE_BUILDING')
        if self._store is None:
            raise Unavailable(self._error or 'STORE_CLOSED')
        return self._store

    def status(self):
        try:
            store = self._wait()
        except Unavailable as error:
            return {'profile': PROFILE, 'available': False, 'error': str(error), 'how_to_build': HOW_TO_BUILD}
        from patterns import store_conditions as sc
        return {'profile': PROFILE, 'available': True, 'snapshot_id': store.snapshot_id,
                'store_profile': store.manifest.get('profile'), 'dataset_id': store.manifest.get('dataset_id'),
                'patients': self.patients, 'conditions_profiles': sc.profiles(),
                'built_s': self.built_s, 'prefill_s': self.prefill_s, 'prefill_keys': self.prefill_keys,
                **({'prefill_error': self.prefill_error} if self.prefill_error else {}),
                'series': True, 'per_patient': True}

    def match(self, spec):
        """The store's answer to the translated page query, per patient, times in minutes from birth."""
        from patterns import store_conditions as sc
        import store_parity
        store = self._wait()
        query, conditions, windows = check_request(spec, self.births)
        micros = {pid: None if w is None else {'start': self.births[pid] + w['start'] * MINUTE,
                                               'end': self.births[pid] + w['end'] * MINUTE}
                  for pid, w in windows.items()}
        started = time.perf_counter()
        index, narrowed = spec.get('index'), None
        try:
            with self._lock:
                sent = store_parity.to_micros(query)
                matched, simplified = sent, None
                if index is not None:  # D52: each base window narrowed to the hull of the periods the records allow
                    import index_hull
                    import index_simplify
                    micros, reasons, found = index_hull.narrow(store, sent, conditions, index, micros, MINUTE, threads=WORKERS)
                    # D52c: the narrowed request simplified (index_simplify_v1), matched, and its answer mapped back.
                    simplified = index_simplify.simplify(store, sent, conditions, index, micros, found)
                    matched = simplified['query']
                    narrowed = {'rule': index_hull.RULE, 'reasons': reasons,
                                'windows': {pid: None if w is None else [self._minutes(pid, w['start']), self._minutes(pid, w['end'])]
                                            for pid, w in micros.items()},
                                'simplify': index_simplify.describe(simplified)}
                result = sc.match(store, matched, micros, workers=WORKERS, conditions=store_parity.conditions_micros(conditions))
                if simplified is not None:
                    result = index_simplify.apply(result, simplified, sent)
        except ValueError as error:  # StoreError and the engine's TrajectoryError are ValueErrors with a code
            code = refusal_code(error)
            if code is None:
                raise
            raise StoreRefused(code) from None
        elapsed = round((time.perf_counter() - started) * 1000)
        out = {'profile': PROFILE, 'snapshot_id': result['snapshot_id'], 'context_id': result['context_id'],
               'match_profile': result['profile'], 'store_profile': store.manifest.get('profile'),
               'conditions_profile': (conditions or {}).get('profile'), 'counts': result['counts'],
               'elapsed_ms': elapsed, 'results': [self._patient(r) for r in result['results']]}
        if narrowed is not None:
            out['index'] = narrowed
        if spec.get('index_anchor') is not None:
            out['index_anchor'] = spec['index_anchor']
        return out

    def _patient(self, r):
        """A store result with its binding described and its times in minutes from birth; every other field as is."""
        pid = r['patient_id']
        out = {k: v for k, v in r.items() if k not in ('binding', 'witness', 'relaxed_match', 'unverified_match')}
        if 'binding' in r:
            out['binding'] = self._binding(pid, r['binding'])
        if 'witness' in r:
            out['witness'] = {s: self._minutes(pid, t) for s, t in r['witness'].items()}
        for key in ('relaxed_match', 'unverified_match'):  # a match reported beside an unresolved status
            if key in r:
                m = dict(r[key])
                if 'binding' in m:
                    m['binding'] = self._binding(pid, m['binding'])
                if 'witness' in m:
                    m['witness'] = {s: self._minutes(pid, t) for s, t in m['witness'].items()}
                out[key] = m
        return out

    def _minutes(self, pid, t):
        if t is None:
            return None
        whole, rest = divmod(t - self.births[pid], MINUTE)
        return whole if not rest else (t - self.births[pid]) / MINUTE

    def _binding(self, pid, binding):
        out = {}
        for step, eid in binding.items():
            d = self.events.get((pid, eid))
            source = self.events.get((pid, eid.split('@', 1)[1])) if '@' in eid and eid.startswith('derived-step:') else None
            if d is None and source is not None and 'finding' in source:  # D38: a gene step's point, from its result
                g = dict(source)
                g['start'] = [self._minutes(pid, t) for t in g['start']]
                g['finding'] = self._minutes(pid, g['finding'])
                out[step] = {'id': eid, 'derived': True, 'gene_report': g}
                continue
            if d is None:  # a derived step's own point (an Nth occurrence), which no record holds
                out[step] = {'id': eid, 'derived': True}
                continue
            d = dict(d)
            d['start'] = [self._minutes(pid, t) for t in d['start']]
            if 'end' in d:
                d['end'] = [self._minutes(pid, t) for t in d['end']]
            out[step] = d
        return out

    def _cleanup(self):
        if self._scratch is not None:
            shutil.rmtree(self._scratch, ignore_errors=True)
            self._scratch = None

    def close(self):
        """Close the store and remove its build."""
        if self._thread is not None:
            self._ready.wait(BUILD_WAIT)
        with self._lock:
            if self._store is not None:
                self._store.close()
                self._store = None
                self._error = self._error or 'STORE_CLOSED'
            self._cleanup()
