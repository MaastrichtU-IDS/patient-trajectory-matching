"""Coded records only, by request, in the execution store's match request: profile `execution-store-conditions-1.3`
(D39).

A 1.3 request is a 1.2 request (D36: value conditions, Nth occurrences, counts, rises and falls) with a request-level
`exclude_kinds`, which it requires: a non-empty list of distinct event kinds to leave out of every step. Only
`"mention"` is accepted (the converter's letter mentions; `EXCLUDE_KIND_UNSUPPORTED` otherwise). `steps` may be empty.

The semantics (`EXCLUDE_RULE`). A 1.3 request is answered as the same request as profile 1.2, without `exclude_kinds`,
would be answered on the snapshot with every event of an excluded kind removed, with its concepts, evidence, series rows
and time variables; the patients, coverage periods, excluded records, closure and labels are unchanged. So:
- every step binds, ranks, counts and reads kept events only (D32's Nth and counts already did);
- coverage: a step is recorded by the sources holding a kept event of its concepts (a value tag: a series row of a kept
  event in its unit), so a source that records a concept only through left-out events does not cover it;
- D18 is unchanged: excluded records still reach, displace and weaken results, and a left-out event is *not* an excluded
  record: it never makes a result not comparable, unresolved or incomplete;
- candidates: the store's filter (postings over every event, a superset), refined over its survivors to patients with
  kept events for every step (`_refine`), so `candidates` and the FILTERED results are those of the filtered snapshot;
  the output's `plan` still counts each step's postings over every event;
- pruning reads the posting summaries over every event, which bound the kept events, so it is sound unchanged.

Why a module of its own (D39, as D36). The scaling report's builds are reused when only matching changed (D34), and
`execution_store.py`, `store_conditions_relative.py` and the converter are bound by reports, so D39 changes none of them.
No build loads this module. Its `match` and `candidates` hand every request other than a 1.3 one to
`store_conditions_relative`, which hands every request other than a 1.2 one to `execution_store`.

What is the store's own code, called as it is: loading (`_load_events`, its value-tag form), the relative readings,
derivation and the two-pass rule (`store_conditions_relative.derive_patient`, `match_derived`), pruning, the
excluded-records rule and the worker pools. Left-out events are dropped after loading, per patient by event id, from one
query of the chunk's events of an excluded kind (`_left_out`), and their series rows from the relative readings.
What is new: `Request` (1.3 over 1.2), `_refine`, `_comparability` (the store's, with the sources restricted to kept
kinds), `match` (`store_conditions_relative.match` with these hooks), the worker unit and the context.
"""
from __future__ import annotations

import copy
import hashlib
import os

from . import execution_store as es
from . import store_conditions_relative as rc
from . import trajectory_graph as tg
from ._resources import ROOT

MODULE_PATH = 'patterns/store_conditions_coded.py'
CONDITIONS_PROFILE_1_3 = 'execution-store-conditions-1.3'  # D39: exclude_kinds over 1.2
CONDITIONS_PROFILES = (*rc.CONDITIONS_PROFILES, CONDITIONS_PROFILE_1_3)
SUPPORTED_KINDS = (es.MENTION_KIND,)
EXCLUDE_RULE = ('exclude_kinds_v1: the request is answered as the same request as profile 1.2 would be on the snapshot '
                'with every event of an excluded kind removed, with its concepts, evidence, series rows and time '
                'variables; patients, coverage periods, excluded records, closure and labels unchanged. A step is '
                'recorded, for coverage, by the sources holding a kept event of its concepts (a value tag: a series row '
                'of a kept event in its unit). A left-out event is not an excluded record. The plan counts each step\'s '
                'postings over every event; candidates are refined to kept events')
# Planted defects for the tests (D39 rules); None in every real run.
_D39_DEFECT = None


def is_coded_request(conditions):
    """Whether a match request's conditions are of profile 1.3 (which `store_conditions_relative` alone refuses)."""
    return isinstance(conditions, dict) and conditions.get('profile') == CONDITIONS_PROFILE_1_3


class Request(rc.Request):
    """`store_conditions_relative.Request` for a 1.3 request: the request without `exclude_kinds`, compiled as profile
    1.2, with `exclude` the kinds left out, and `profile` and `conditions` the 1.3 request's own. With no conditioned
    step, the query is compiled as a request without tags or derived steps."""

    def __init__(self, query, conditions):
        tg.validate_query(query)
        if not (isinstance(conditions, dict) and conditions.get('profile') == CONDITIONS_PROFILE_1_3
                and set(conditions) <= {'profile', 'steps', 'relaxations', 'exclude_kinds'}
                and {'steps', 'exclude_kinds'} <= set(conditions)):
            raise es.StoreError('INVALID_CONDITIONS', 'profile, steps, relaxations and exclude_kinds')
        kinds = conditions['exclude_kinds']
        if not (isinstance(kinds, list) and kinds and all(type(k) is str and k for k in kinds)
                and len(set(kinds)) == len(kinds)):
            raise es.StoreError('INVALID_EXCLUDE_KINDS', repr(kinds)[:120])
        unsupported = sorted(k for k in kinds if k not in SUPPORTED_KINDS)
        if unsupported:
            raise es.StoreError('EXCLUDE_KIND_UNSUPPORTED', ', '.join(unsupported))
        if not isinstance(conditions['steps'], dict):
            raise es.StoreError('INVALID_CONDITIONS', 'steps')
        inner = {k: v for k, v in conditions.items() if k != 'exclude_kinds'}
        if conditions['steps']:
            super().__init__(query, {**inner, 'profile': rc.CONDITIONS_PROFILE_1_2})
        else:
            if conditions.get('relaxations', []) != []:
                raise es.StoreError('INVALID_CONDITIONS', 'relaxations without a conditioned step')
            self.tags, self.derived, self.kinds, self.step_n = {}, {}, {}, {}
            self.query = tg.validate_query(copy.deepcopy(query))
            per_step = es._step_concepts(self.query)
            self.load = {sid: list(cs) for sid, cs in per_step.items()}
            self.base = {sid: list(cs) for sid, cs in per_step.items()}
            self.used, self.derived_used = {}, {}
        self.profile = CONDITIONS_PROFILE_1_3
        self.conditions = conditions
        self.exclude = tuple(kinds)

    def describe_excluded(self):
        """The kinds left out, for the match output (D39)."""
        return {'kinds': list(self.exclude), 'rule': EXCLUDE_RULE}


# Left-out events.

def _left_out(store, idxs, kinds):
    """{patient_idx: ids of its events whose kind is one of `kinds`} for these patients. Event ids are unique within a
    patient only, so the sets are per patient (`_D39_DEFECT == 'coded_dropped'` pools them across patients)."""
    out = {}
    if idxs:
        try:
            es._temp(store, '_m_left', [('idx', 'INTEGER')], sorted(idxs))
            for idx, eid in store.db.execute(
                    f'SELECT e.patient_idx, e.event_id FROM _m_left c JOIN events e ON e.patient_idx = c.idx '
                    f'WHERE e.patient_idx BETWEEN ? AND ? AND e.kind IN {es._in(kinds)}',
                    [min(idxs), max(idxs)] + list(kinds)).fetchall():
                out.setdefault(idx, set()).add(eid)
        finally:
            es._drop(store, '_m_left')
    if _D39_DEFECT == 'coded_dropped':
        pooled = set().union(*out.values()) if out else set()
        out = {idx: pooled for idx in idxs}
    return out


def _load_kept(store, idxs, per_step, linked, tags, kinds, derived=False):
    """(`execution_store._load_events` with every left-out event dropped, {patient_idx: left-out ids}). A patient
    sharing variables keeps every kept event, as the store loads it (D24). `_D39_DEFECT == 'mention_read'` leaves the
    left-out events in on the path without derived steps."""
    events = es._load_events(store, idxs, per_step, linked, tags) if tags else es._load_events(store, idxs, per_step, linked)
    gone = _left_out(store, idxs, kinds)
    if _D39_DEFECT == 'mention_read' and not derived:
        return events, gone
    out = {}
    for idx, mine in events.items():
        drop = gone.get(idx, ())
        kept = [e for e in mine if e['id'] not in drop]
        if kept:
            out[idx] = kept
    return out, gone


def _derived_chunk(store, request, idxs, pids, windows, linked):
    """`store_conditions_relative._derived_chunk` over kept events: each candidate's `Derived`; a relative condition's
    readings lose the series rows of left-out events."""
    events, gone = _load_kept(store, idxs, request.load, linked, request.used, request.exclude, derived=True)
    mentions = es._mentions(store, idxs)
    readings = rc._readings(store, idxs, request) if request.relative else {}
    for idx, r in readings.items():
        for eid in gone.get(idx, ()):
            r['rows'].pop(eid, None)
    return [(idx, rc.derive_patient(request, pid, w, events.get(idx, []), mentions.get(idx, set()), readings.get(idx)))
            for idx, pid, w in zip(idxs, pids, windows)]


def _match_derived_chunk(store, pq, request, idxs, pids, windows, linked):
    return [(idx, rc.match_derived(pq, request, derived))
            for idx, derived in _derived_chunk(store, request, idxs, pids, windows, linked)]


def _match_plain_chunk(store, pq, request, per_step, idxs, pids, windows, linked):
    tags = request.used
    events, _ = _load_kept(store, idxs, per_step, linked, tags, request.exclude)
    return [(idx, tg.match_prepared(pq, {'patient_id': pid, 'window': w, 'events': events.get(idx, [])}, trusted=True))
            for idx, pid, w in zip(idxs, pids, windows)]


# Candidates.

def _kept_counts(store, survivors, plain, tags, kinds):
    """{(patient_idx, concept or tag): number of kept events carrying it} over `survivors`: plain concepts through the
    closure, value tags through their series rows (exactly, as `execution_store._tag_counts` reads them)."""
    out = {}
    if not survivors:
        return out
    try:
        es._temp(store, '_m_kept', [('idx', 'INTEGER')], survivors)
        if plain:
            for idx, concept, n in store.db.execute(f"""
                    SELECT e.patient_idx, cc.ancestor, count(DISTINCT e.event_id)
                    FROM _m_kept c JOIN events e ON e.patient_idx = c.idx
                    JOIN event_concepts ec USING (patient_idx, event_id)
                    JOIN concept_closure cc ON cc.concept = ec.concept
                    WHERE e.patient_idx BETWEEN ? AND ? AND cc.ancestor IN {es._in(plain)} AND e.kind NOT IN {es._in(kinds)}
                    GROUP BY e.patient_idx, cc.ancestor""", [survivors[0], survivors[-1]] + list(plain) + list(kinds)).fetchall():
                out[idx, concept] = n
        join = ('JOIN _m_kept c ON c.idx = s.patient_idx '
                'JOIN events e ON e.bucket = s.bucket AND e.patient_idx = s.patient_idx AND e.event_id = s.event_id')
        for name, spec in tags.items():
            exact, loose = es._tag_rows(store, spec, 's.patient_idx, count(DISTINCT s.event_id)', 's.patient_idx, s.event_id',
                                        join, f'AND e.kind NOT IN {es._in(kinds)}', list(kinds), 'GROUP BY s.patient_idx')
            for idx, n in exact:
                out[idx, name] = n
            seen = set()
            for idx, eid in loose:
                if (idx, eid) not in seen:
                    seen.add((idx, eid))
                    out[idx, name] = out.get((idx, name), 0) + 1
    finally:
        es._drop(store, '_m_kept')
    return out


def _refine(store, survivors, request):
    """The survivors holding, for every step, a kept event of one of its engine concepts: a plain concept or value tag
    carried by a kept event; an Nth or count tag, at least N kept events of what it counts; a relative tag, one kept
    event of its unit tag (`_D39_DEFECT == 'filter_own_concept'` reads each step's own concept only)."""
    per_step = es._step_concepts(request.query)
    if _D39_DEFECT == 'filter_own_concept':
        per_step = {s['id']: [s['concept']] for s in request.query['steps']}
    need = {}  # engine concept -> (what is counted, at least)
    for cs in per_step.values():
        for c in cs:
            if c in request.derived:
                spec = request.derived[c]
                need[c] = (spec['concept'], spec['n'] if spec['kind'] in ('nth', 'count') else 1)
            else:
                need[c] = (c, 1)
    counted = {what for what, _ in need.values()}
    plain = sorted(c for c in counted if c not in request.tags)
    tags = {name: request.tags[name] for name in sorted(counted) if name in request.tags}
    counts = _kept_counts(store, survivors, plain, tags, request.exclude)
    return {idx for idx in survivors
            if all(any(counts.get((idx, need[c][0]), 0) >= need[c][1] for c in cs) for cs in per_step.values())}


def _candidates(store, pq, request):
    """`store_conditions_relative._candidates`, its survivors refined to kept events (`_refine`); the plan as the store
    gives it (postings over every event)."""
    found = rc._candidates(store, pq, request)
    survivors = found['patient_idx']
    kept = _refine(store, survivors, request)
    return {**found, 'patient_idx': [idx for idx in survivors if idx in kept]}


def candidates(store, query, conditions=None):
    """`store_conditions_relative.candidates`, for a 1.3 request too; any other request is that module's own call."""
    if not is_coded_request(conditions):
        return rc.candidates(store, query, conditions)
    request = Request(query, conditions)
    es._require_series(store, request)
    return _candidates(store, tg.PreparedQuery(request.query), request)


# Coverage.

_SOURCES = {}  # (snapshot, files, concept or tag, kinds) -> its recording sources; a snapshot is immutable
_SOURCES_LIMIT = 4096


def _sources(store, per_step, tags, kinds):
    """{concept or tag: the sources recording it through kept events}: for a plain concept, the sources of the kept
    events carrying it through the closure (`source_concepts` restricted to kept kinds: the store builds that table by
    the same join over every event); for a value tag, the sources of the kept events with a series row of its concept in
    its unit (`execution_store._tag_sources` likewise). With no kinds left out, the store's own tables' answer.
    `_D39_DEFECT == 'coverage_all_sources'` reads the store's own, over every event.

    Each answer is a scan over the events, so it is cached per snapshot (its identifier and file list: a snapshot never
    changes), concept or tag, and kinds (D39 review)."""
    concepts = sorted({c for cs in per_step.values() for c in cs if c not in tags})
    sources = {}
    if _D39_DEFECT == 'coverage_all_sources':
        for source, concept in store.query(
                f'SELECT source, concept FROM source_concepts WHERE concept IN {es._in(concepts)}', concepts) if concepts else ():
            sources.setdefault(concept, set()).add(source)
        for name, spec in tags.items():
            sources[name] = es._tag_sources(store, spec)
        return sources
    kinds = tuple(kinds)
    snapshot = (store.snapshot_id, store.files_digest)
    keep = f' AND e.kind NOT IN {es._in(kinds)}' if kinds else ''
    if len(_SOURCES) > _SOURCES_LIMIT:
        _SOURCES.clear()
    wanted = [c for c in concepts if (snapshot, c, kinds) not in _SOURCES]
    if wanted:
        found = {c: set() for c in wanted}
        for source, concept in store.query(f"""
                SELECT DISTINCT e.source, cc.ancestor FROM events e JOIN event_concepts ec USING (patient_idx, event_id)
                JOIN concept_closure cc ON cc.concept = ec.concept
                WHERE cc.ancestor IN {es._in(wanted)}{keep}""", wanted + list(kinds)):
            found[concept].add(source)
        for c, mine in found.items():
            _SOURCES[snapshot, c, kinds] = frozenset(mine)
    for c in concepts:
        if _SOURCES[snapshot, c, kinds]:
            sources[c] = set(_SOURCES[snapshot, c, kinds])
    for name, spec in tags.items():
        key = (snapshot, ('tag', spec[0], spec[1][0]), kinds)
        if key not in _SOURCES:
            test, params = es._tag_where(spec)
            _SOURCES[key] = frozenset(r[0] for r in store.query(f"""
                SELECT DISTINCT e.source FROM series s JOIN concept_closure cc ON cc.concept = s.item_class
                JOIN events e ON e.bucket = s.bucket AND e.patient_idx = s.patient_idx AND e.event_id = s.event_id
                WHERE {test}{keep}""", params + list(kinds)))
        sources[name] = set(_SOURCES[key])
    return sources


def _comparability(store, per_step, nones, windows_by_idx, kinds, chunk=es.CHUNK_PATIENTS * es.SET_CHUNK_FACTOR,
                   tags=None):
    """`execution_store._comparability` with each step's recording sources read from kept events only (`_sources`);
    the rule (COVERAGE_RULE) and the overlap test are the store's, line for line."""
    if not nones:
        return
    tags = tags or {}
    sources = _sources(store, per_step, tags, kinds)
    able = {step: set().union(*(sources.get(c, set()) for c in cs)) for step, cs in per_step.items()}
    unrecorded = [step for step in per_step if not able[step]]
    reason = 'NO_SOURCE_RECORDS_CONCEPT' if unrecorded else 'NO_SOURCE_COVERAGE'
    pairs = [(step, source) for step in per_step for source in sorted(able[step])]
    clamp = lambda v: max(-es._BIG, min(es._BIG, v))
    try:
        es._temp(store, '_m_able', [('step', 'VARCHAR'), ('source', 'VARCHAR')], [p[0] for p in pairs], [p[1] for p in pairs])
        for k in range(0, len(nones), chunk):
            part = nones[k:k + chunk]
            es._temp(store, '_m_none', [('idx', 'INTEGER'), ('ws', 'HUGEINT'), ('we', 'HUGEINT')], part,
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
        es._drop(store, '_m_able', '_m_none')


def _as_excluded(store, query, base, decided, windows_by_idx, touched, kinds):
    """The planted defect `mentions_as_excluded` only: left-out events added to the excluded-records rule's input, as
    if they were excluded records (the confusion D39 rules out)."""
    if not decided:
        return
    concepts = sorted({c for cs in base.values() for c in cs})
    rows = store.query(f"""
        SELECT e.patient_idx, cc.ancestor, e.start_lo, e.start_hi FROM events e
        JOIN event_concepts ec USING (patient_idx, event_id) JOIN concept_closure cc ON cc.concept = ec.concept
        WHERE cc.ancestor IN {es._in(concepts)} AND e.kind IN {es._in(kinds)}""", concepts + list(kinds))
    scope = query.get('scope', 'any')
    for idx, ancestor, lo, hi in rows:
        if idx not in decided:
            continue
        admitted, every = touched.setdefault(idx, ({}, {}))
        for step, cs in base.items():
            if ancestor in cs:
                every.setdefault(step, []).append((lo, hi))
                if es.admits_start(scope, windows_by_idx[idx], lo, hi):
                    admitted.setdefault(step, []).append((lo, hi))


def match_context(store, query, windows, conditions=None):
    """`store_conditions_relative.match_context`; for a 1.3 request it binds this module's and the relative module's
    code beside the engine's artifacts, and adds the derived, relative and exclusion rules' digests."""
    if not is_coded_request(conditions):
        return rc.match_context(store, query, windows, conditions)
    context, _ = es.match_context(store, query, windows, conditions)
    context['artifacts'] = {**context['artifacts'],
                            **{p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in (rc.MODULE_PATH, MODULE_PATH)}}
    context.update(derived_rules=es._digest(es.DERIVED_RULES), relative_rules=es._digest(rc.RELATIVE_RULES),
                   exclude_rule=es._digest(EXCLUDE_RULE))
    return context, es._digest(context)


# Workers.

_W = {}  # per worker process: the prepared 1.3 request it last ran (the store is `execution_store._W['store']`)


def _worker_unit(unit):
    """`execution_store._worker_unit` for a 1.3 request, run in the store's own worker processes over the store they
    opened: ('ok', [(idx, result)], peak RSS) or ('error', type, message)."""
    key, query, conditions, idxs, pids, windows, linked, fault = unit
    try:
        if fault == 'exit':
            os._exit(3)
        if fault == 'raise':
            raise RuntimeError('injected failure')
        if _W.get('key') != key:
            request = Request(query, conditions)
            pq = tg.PreparedQuery(request.query)
            _W.update(key=key, pq=pq, per_step=es._step_concepts(pq.query), request=request)
        pq, per_step, request, store = _W['pq'], _W['per_step'], _W['request'], es._W['store']
        if request.derived:
            return ('ok', _match_derived_chunk(store, pq, request, idxs, pids, windows, linked), es._peak_rss())
        return ('ok', _match_plain_chunk(store, pq, request, per_step, idxs, pids, windows, linked), es._peak_rss())
    except Exception as exc:
        return ('error', type(exc).__name__, str(exc)[:300])


def _run(pool, units):
    """`execution_store.MatchPool.run`, each unit run by `_worker_unit` above in the pool's own workers."""
    pool.warm()
    futures = []
    try:
        for unit in units:
            futures.append(pool._pool().submit(_worker_unit, (*unit, pool.fault)))
    except Exception as exc:
        pool.close()
        raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
    out = []
    try:
        for future in futures:
            try:
                reply = future.result()
            except Exception as exc:  # BrokenProcessPool: a worker died
                pool.close()
                raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{type(exc).__name__}: {exc}'[:300]) from exc
            if reply[0] != 'ok':
                raise es.MatchWorkerError('MATCH_WORKER_FAILED', f'{reply[1]}: {reply[2]}')
            pool.worker_peak_rss = max(pool.worker_peak_rss, reply[2])
            out.append(reply[1])
    finally:
        for future in futures:
            future.cancel()
    return out


def match(store, query, windows, detail='full', chunk=es.CHUNK_PATIENTS, workers=1, conditions=None):
    """`store_conditions_relative.match`, for a 1.3 request too (D39): the request is answered over kept events
    (`EXCLUDE_RULE`), with the output's `excluded_kinds` and the context's exclusion rule. Every other request is that
    module's own `match`, called as it is, so its answer is byte-identical to calling it directly. The code below is
    that module's `match`, with this module's request, candidates, loaders, coverage, context and worker units."""
    if not is_coded_request(conditions):
        return rc.match(store, query, windows, detail, chunk, workers, conditions)
    if not (type(workers) is int and workers >= 1):
        raise es.StoreError('INVALID_WORKERS', repr(workers))
    if detail not in es.DETAILS:
        raise es.StoreError('INVALID_DETAIL', repr(detail))
    request = Request(query, conditions)
    tags = request.used
    derived = bool(request.derived)
    es._require_series(store, request)
    pq = tg.PreparedQuery(request.query)
    given, query = query, pq.query
    patients = store.query('SELECT patient_idx, patient_id FROM patients ORDER BY patient_idx')
    # Patients whose events share time variables: their events are loaded whole (`_load_events`), left-out ones dropped.
    linked = {idx for idx, in store.query('SELECT patient_idx FROM patients WHERE shared_variables')}
    by_id = {pid: idx for idx, pid in patients}
    unknown = sorted(set(windows) - set(by_id))
    if unknown:
        raise es.StoreError('UNKNOWN_WINDOW_PATIENT', ', '.join(unknown[:5]))
    windows_by_idx = {}
    for idx, pid in patients:
        w = windows.get(pid)
        if w is not None:
            es._check_window(pid, w)
        windows_by_idx[idx] = w
    pid_of = dict(patients)
    found = _candidates(store, pq, request)
    per_step = es._step_concepts(query) if not derived else request.load  # D32: a derived tag by what it counts
    # The concepts each step's engine concepts stand for (a tag by its concept): what excluded records are matched by.
    base = request.base
    live = [i for i in found['patient_idx'] if windows_by_idx[i] is not None]
    results = {}  # the engine's results, and what a rule then changed; patients absent are filtered or windowless
    parts = [live[k:k + chunk] for k in range(0, len(live), chunk)]
    if es._PRUNE:
        # The summaries cover every event, kept or not, so they bound the kept events: a pruned candidate's `none` is
        # the engine's own over kept events (D25's argument, unchanged).
        if derived:
            pruned = es._prune(store, pq, live, windows_by_idx, tags=tags, derived=request.derived_used)
        else:
            pruned = es._prune(store, pq, live, windows_by_idx, tags=tags) if tags else es._prune(store, pq, live, windows_by_idx)
        for idx in pruned:
            results[idx] = es._NONE(pid_of[idx])
        parts = [kept for part in parts if (kept := [i for i in part if i not in pruned])]
    if workers > 1 and parts:
        key = es._digest([given, conditions])
        units = [(key, given, conditions, part, [pid_of[i] for i in part], [windows_by_idx[i] for i in part],
                  [i for i in part if i in linked]) for part in parts]
        for unit in _run(es.get_pool(store, workers), units):
            results.update(unit)
    else:
        for part in parts:
            args = (part, [pid_of[i] for i in part], [windows_by_idx[i] for i in part], [i for i in part if i in linked])
            results.update(_match_derived_chunk(store, pq, request, *args) if derived
                           else _match_plain_chunk(store, pq, request, per_step, *args))
    filtered = [idx for idx, _ in patients if windows_by_idx[idx] is not None and idx not in results]
    filtered_result = lambda idx: {'patient_id': pid_of[idx], 'status': 'none', 'reason': 'FILTERED', 'complete': True}
    nones = sorted(filtered + [idx for idx, r in results.items() if r['status'] == 'none'])
    for idx, reason, missing, unrecorded in _comparability(store, per_step, nones, windows_by_idx, request.exclude,
                                                           chunk * es.SET_CHUNK_FACTOR, tags):
        results[idx] = {'patient_id': pid_of[idx], 'status': 'not_comparable', 'reason': reason,
                        'uncovered_steps': missing}
        if unrecorded:
            results[idx]['unrecorded_steps'] = unrecorded
    # Records the ingest left out can change any engine decision, not only a `none`; events left out by request cannot.
    decided = {idx for idx, w in windows_by_idx.items()
               if w is not None and (results[idx]['status'] in es.DECIDED if idx in results else True)}
    touched = es._excluded_starts(store, query, base, decided, windows_by_idx)
    if _D39_DEFECT == 'mentions_as_excluded':
        _as_excluded(store, query, base, decided, windows_by_idx, touched, request.exclude)
    bound = [idx for idx in touched if idx in results and (
        results[idx].get('binding') or results[idx].get('relaxed_match', {}).get('binding')
        or results[idx].get('unverified_match', {}).get('binding'))]
    starts = es._bound_starts(store, bound)
    occurrence = None
    if derived:
        # D32: Nth steps reach and displace as the decision log states; a derived point's start range is its envelope.
        # D33: a relative step's reach is its scope.
        rc._derived_reach(request, query, touched, windows_by_idx)
        occurrence = {sid: 'first' if which == 'nth' else 'last'
                      for sid, (kind, which) in request.kinds.items() if kind == 'nth'}
        nth_bound = [idx for idx in bound if occurrence]
        for k in range(0, len(nth_bound), chunk):
            part = nth_bound[k:k + chunk]
            for idx, d in _derived_chunk(store, request, part, [pid_of[i] for i in part],
                                         [windows_by_idx[i] for i in part], [i for i in part if i in linked]):
                starts.setdefault(idx, {}).update(d.points)
    for idx, (hit, every) in touched.items():
        results[idx] = es.apply_exclusions(query, results[idx] if idx in results else filtered_result(idx),
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
    out = {'profile': es.MATCH_PROFILE, 'engine': tg.PROFILE_ID, 'snapshot_id': store.snapshot_id,
           'verified': store.verified, 'coverage_rule': es.COVERAGE_RULE, 'exclusion_rule': es.EXCLUSION_RULE,
           'labels': store.manifest.get('labels'), 'context': context, 'context_id': context_id,
           'plan': found['plan'], 'candidates': len(found['patient_idx']), 'counts': dict(sorted(counts.items()))}
    if request.tags or not derived:
        out['value_conditions'] = request.describe()
    if derived:
        out['derived_steps'] = request.describe_derived()
    out['excluded_kinds'] = request.describe_excluded()
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
