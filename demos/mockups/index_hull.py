"""The server's narrowing of D52's base windows to the hull of the periods the store's records allow (`index_hull_v1`).

    python demos/mockups/index_hull.py narrow SNAPSHOT < cases.json   # the cases with their windows narrowed

D52 writes an event-placed index period as a hidden step of the query (`trajectory/store_request.js`, `index_anchor_v1`)
and sends each patient a constant base window: birth (or the constant start) to last contact (or the constant end). The
request's `index` block names, for each side of the period placed by an event, the hidden step and its offset. Here,
for each patient and each hidden step, the step's candidates in the store (the events whose concept closure holds the
step's concept, none of a kind the request's `exclude_kinds` leaves out) give its possible range:
- `first`: [least start_lo, least start_hi]; `last`: [greatest start_lo, greatest start_hi] (D30's envelopes);
- an Nth occurrence: [Nth smallest start_lo, Nth smallest start_hi]; Nth from last: from the greatest. As D32's derived
  occurrence, it ranks coded records only: a letter mention is never its candidate, whatever `exclude_kinds` says.
In every world each event lies in its recorded range, and the engine's domains only narrow it, so the step's time in
any world lies in this range (an order-statistic bound; it contains D32's envelope). The window is then
- start: max(base start, lo + start offset), when the start is placed by an event;
- end: min(base end, hi + end offset), when the end is.
The decision log (D52, design §2) shows that the request then matches exactly what it matches over the base window: a
START of 0, or of a time no later than the period's earliest start (when that is after birth), gives the same clamp,
and an END of last contact, or of a time between the period's latest end and last contact, gives the same eligibility.
Only coverage (`window_overlap_source_concepts_v1`), D44's admission scope and pruning read the narrower window.

A patient gets no window (`excluded`) with a reason:
- INDEX_ANCHOR_NOT_RECORDED: a hidden step has no candidate (fewer than N, for an Nth occurrence);
- INDEX_NO_POSSIBLE_PERIOD: the narrowed start is after the narrowed end, which happens only when, in every world, the
  period ends before birth, ends after last contact, or ends before it starts.

D52b: a range depends only on the snapshot and its key (the hidden step's concept, its k, whether it counts from the end,
and the kinds its candidates leave out), so `ranges` computes it once per snapshot and key and reuses it (`_CACHE`, emptied
when another snapshot is asked); `cache=False` computes it afresh. The command line's `narrow` also narrows each case
without the cache and records whether the two are identical (`index_result.cache`), which D52b's census and reference rule.

Called by `store_trajectory.py` on every request with an `index` block (and never otherwise: `CALLS` counts), and by the
parity test and D52's tools through `narrow` on the command line, before `store_parity.py match`.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import math
import os
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

RULE = 'index_hull_v1'
MINUTE = 60_000_000
MENTION = 'mention'  # the converter's letter-mention kind (patterns/execution_store.py, MENTION_KIND)
CALLS = {'narrow': 0, 'range_queries': 0, 'range_hits': 0}  # D52 rule 6: `narrow` 0 for a request without an `index` block
# D52b: the ranges of the snapshot last asked, by range key, least recently used first (review, M1: bounded by a key count
# and by the patients' rows it holds, both configurable; a key whose ranges alone pass the row cap is not kept). At 300,000
# patients a key holds about 250,000 rows, so the default keeps four or five such keys (the page sends one per template).
CACHE_MAX_KEYS = int(os.environ.get('PTM_INDEX_CACHE_KEYS') or 64)
CACHE_MAX_ROWS = int(os.environ.get('PTM_INDEX_CACHE_ROWS') or 1_500_000)
_CACHE = {'snapshot': None, 'ranges': OrderedDict(), 'rows': 0, 'known': None}
MAX_K = 50  # the greatest N of an Nth hidden step the narrowing takes (review, M1)
MAX_CONCEPT_CHARS = 200


class IndexRefused(ValueError):
    """A malformed or unknown hidden step, refused by name before any SQL (D52b review, M1); `code` is stable."""

    def __init__(self, code, detail=''):
        super().__init__(f'{code}: {detail}' if detail else code)
        self.code = code
_DEFECT = None  # a planted defect (tools/d52_measure.py defects only): 'hull_from_hi', 'nth_off_by_one', 'nth_counts_mentions'

RANGE_SQL = """
    WITH f AS (SELECT DISTINCT e.patient_idx, e.event_id, e.start_lo, e.start_hi FROM events e
               JOIN event_concepts ec USING (patient_idx, event_id)
               JOIN concept_closure cc ON cc.concept = ec.concept
               WHERE cc.ancestor = ? {kinds})
    SELECT p.patient_id, count(*), {lo}, {hi}
    FROM f JOIN patients p USING (patient_idx) GROUP BY p.patient_id"""
# The k-th smallest (or greatest) start bound; for k = 1 an aggregate, the same value without sorting.
ORDER_STATISTIC = {(1, False): ('min(f.start_lo)', 'min(f.start_hi)'), (1, True): ('max(f.start_lo)', 'max(f.start_hi)')}


def hidden_steps(query, conditions, index):
    """{step id: (concept, k, from the end)} for the steps the index block names, each checked for its form (D52b review,
    M1): a step of the query with a concept string; occurrence `first` or `last`, or `any` with an occurrence condition
    {nth | nth_last: k}, k an integer from 2 to MAX_K. Anything else is refused by name, before any SQL."""
    if not (isinstance(query, dict) and isinstance(query.get('steps'), list) and isinstance(index, dict)):
        raise IndexRefused('INDEX_STEP_MALFORMED')
    steps = {s.get('id'): s for s in query['steps'] if isinstance(s, dict)}
    derived = (conditions or {}).get('steps', {}) if isinstance(conditions, (dict, type(None))) else None
    if not isinstance(derived, dict):
        raise IndexRefused('INDEX_STEP_MALFORMED')
    out = {}
    for side in ('start', 'end'):
        x = index.get(side)
        if not x:
            continue
        if not (isinstance(x, dict) and x.get('step') in steps):
            raise IndexRefused('INDEX_STEP_MALFORMED', side)
        sid = x['step']
        c, occ = steps[sid].get('concept'), steps[sid].get('occurrence', 'any')
        if not (isinstance(c, str) and 0 < len(c) <= MAX_CONCEPT_CHARS):
            raise IndexRefused('INDEX_STEP_MALFORMED', 'concept')
        if occ == 'first':
            out[sid] = (c, 1, False)
        elif occ == 'last':
            out[sid] = (c, 1, True)
        elif occ == 'any':
            raw = derived.get(sid)
            occurrence = raw.get('occurrence') if isinstance(raw, dict) else None
            if not (isinstance(occurrence, dict) and len(occurrence) == 1):
                raise IndexRefused('INDEX_OCCURRENCE_UNSUPPORTED', sid)
            (which, k), = occurrence.items()
            if which not in ('nth', 'nth_last') or type(k) is not int or not 2 <= k <= MAX_K:
                raise IndexRefused('INDEX_OCCURRENCE_UNSUPPORTED', sid)
            out[sid] = (c, k, which == 'nth_last')
        else:
            raise IndexRefused('INDEX_OCCURRENCE_UNSUPPORTED', sid)
    return out


def _known(store):
    """(concepts, kinds) the snapshot holds, read once per snapshot (kept beside the cache)."""
    if _CACHE['known'] is None or _CACHE['known'][0] != store.snapshot_id:
        concepts = {c for c, in store.query('SELECT DISTINCT concept FROM concept_closure')}
        kinds = {k for k, in store.query('SELECT DISTINCT kind FROM events')}
        _CACHE['known'] = (store.snapshot_id, concepts, kinds)
    return _CACHE['known'][1], _CACHE['known'][2]


def validate(store, query, conditions, index):
    """The hidden steps' forms (`hidden_steps`), and that each concept and every kind `exclude_kinds` names is one the
    snapshot holds; otherwise refused by name before any SQL of the narrowing (INDEX_CONCEPT_UNKNOWN,
    INDEX_EXCLUDE_KINDS_UNKNOWN). Only a validated key is ever computed or cached."""
    steps = hidden_steps(query, conditions, index)
    excluded = (conditions or {}).get('exclude_kinds', [])
    if not (isinstance(excluded, list) and all(isinstance(k, str) for k in excluded)):
        raise IndexRefused('INDEX_EXCLUDE_KINDS_UNKNOWN')
    concepts, kinds = _known(store)
    for sid, (c, _, _) in steps.items():
        if c not in concepts:
            raise IndexRefused('INDEX_CONCEPT_UNKNOWN', sid)
    if not set(excluded) <= kinds | {MENTION}:
        raise IndexRefused('INDEX_EXCLUDE_KINDS_UNKNOWN')
    return steps


def step_keys(query, conditions, index):
    """{step id: its range key}: the hidden step's concept, k, whether it counts from the end, and the kinds its
    candidates leave out. The key is all a range depends on, so ranges are kept per snapshot and key (D52b)."""
    excluded = list((conditions or {}).get('exclude_kinds', ()))
    occurrence = {s['id']: s.get('occurrence', 'any') for s in query['steps']}
    out = {}
    for sid, (concept, k, from_end) in hidden_steps(query, conditions, index).items():
        # D52 review, M1: a derived occurrence (an Nth, N > 1) ranks coded records only (D32), whatever `exclude_kinds`
        # says, so its candidates never include a letter mention. Derived means occurrence `any` with an occurrence
        # condition (D52b review, nit), which `hidden_steps` has checked.
        derived = occurrence[sid] == 'any'
        kinds = tuple(sorted(set(excluded) | ({MENTION} if derived and _DEFECT != 'nth_counts_mentions' else set())))
        if _DEFECT == 'nth_off_by_one' and k > 1:
            k -= 1
        out[sid] = (concept, int(k), bool(from_end), kinds)
    return out


def _range_query(store, key):
    """{patient id: (count, lo, hi)} for one range key, by SQL over the store's events."""
    concept, k, from_end, kinds = key
    kind_sql = (f"AND e.kind NOT IN ({', '.join('?' for _ in kinds)})") if kinds else ''
    order = 'DESC' if from_end else 'ASC'
    lo, hi = ORDER_STATISTIC.get((k, from_end), (f"list_sort(list(f.start_lo), '{order}')[{int(k)}]",
                                                  f"list_sort(list(f.start_hi), '{order}')[{int(k)}]"))
    CALLS['range_queries'] += 1
    return {pid: (n, lo, hi) for pid, n, lo, hi in store.query(RANGE_SQL.format(kinds=kind_sql, lo=lo, hi=hi), [concept, *kinds])}


def ranges(store, query, conditions, index, threads=None, cache=True):
    """{step id: {patient id: (count, lo, hi)}} on the store's clock; lo and hi None when there are fewer than k.

    The request is validated first (`validate`), so nothing malformed or unknown reaches SQL or the cache. D52b: the ranges
    of a key are computed once per snapshot and reused by every later request naming the same key (`_CACHE`, emptied
    when another snapshot is asked; least recently used keys evicted past CACHE_MAX_KEYS keys or CACHE_MAX_ROWS rows; a
    key whose ranges alone pass the row cap is computed but not kept). `cache=False` computes them afresh and neither
    reads nor fills the cache. A snapshot is immutable, so a cached range is the range. The ranges returned may be the
    cache's own dictionaries: callers read them and never change them. With `threads`, a computation runs on that many
    DuckDB threads (a request matched on that many workers; the store's own setting is restored after)."""
    validate(store, query, conditions, index)
    keys = step_keys(query, conditions, index)
    cached = _CACHE['ranges']
    if cache and _CACHE['snapshot'] != (store.snapshot_id, _DEFECT):
        _CACHE['snapshot'] = (store.snapshot_id, _DEFECT)
        cached.clear()
        _CACHE['rows'] = 0
    missing = sorted({key for key in keys.values() if not (cache and key in cached)})
    computed = {}
    if missing:
        before = None
        if threads:
            before = store.db.execute("SELECT current_setting('threads')").fetchone()[0]
            store.db.execute(f'SET threads = {int(threads)}')
        try:
            for key in missing:
                computed[key] = _range_query(store, key)
        finally:
            if before is not None:
                store.db.execute(f'SET threads = {int(before)}')
        if cache:
            for key, value in computed.items():
                if len(value) <= CACHE_MAX_ROWS:
                    cached[key] = value
                    _CACHE['rows'] += len(value)
            while cached and (len(cached) > CACHE_MAX_KEYS or _CACHE['rows'] > CACHE_MAX_ROWS):
                _, gone = cached.popitem(last=False)
                _CACHE['rows'] -= len(gone)
    out = {}
    for sid, key in keys.items():
        if key in computed:
            out[sid] = computed[key]
        else:
            CALLS['range_hits'] += 1
            cached.move_to_end(key)
            out[sid] = cached[key]
    return out


def clear_cache():
    """Empty the cache (a measurement of a first request; D52b's latency tool)."""
    _CACHE['snapshot'] = None
    _CACHE['ranges'].clear()
    _CACHE['rows'] = 0


def cache_bytes():
    """An estimate of the memory the cache holds, in bytes: its dictionaries, their patient ids, tuples and integers
    (D52b review, S3; reported, not ruled)."""
    total, seen = sys.getsizeof(_CACHE['ranges']), set()
    for key, value in _CACHE['ranges'].items():
        total += sys.getsizeof(value) + sys.getsizeof(key)
        for pid, row in value.items():
            if id(pid) not in seen:
                seen.add(id(pid))
                total += sys.getsizeof(pid)
            total += sys.getsizeof(row) + sum(sys.getsizeof(v) for v in row)
    return total


def narrow(store, query, conditions, index, windows, offset_unit=1, threads=None, cache=True):
    """(windows, reasons, found): each base window (a patient id's {start, end} on the store's clock, or None) narrowed;
    `reasons` names each patient the narrowing excluded; `found` is `ranges` (with `threads` and `cache`). Offsets in the
    index block are multiplied by `offset_unit` (the server passes minutes, so 60,000,000)."""
    CALLS['narrow'] += 1
    found = ranges(store, query, conditions, index, threads, cache)
    out, reasons = {}, {}
    for pid, w in windows.items():
        if w is None:
            out[pid] = None
            continue
        start, end = w['start'], w['end']
        missing = False
        for side in ('start', 'end'):
            x = index.get(side)
            if x is None:
                continue
            n, lo, hi = found[x['step']].get(pid, (0, None, None))
            if lo is None:
                missing = True
                break
            if side == 'start':
                start = max(start, (hi if _DEFECT == 'hull_from_hi' else lo) + x['offset'] * offset_unit)
            else:
                end = min(end, hi + x['offset'] * offset_unit)
        if missing:
            out[pid], reasons[pid] = None, 'INDEX_ANCHOR_NOT_RECORDED'
        elif start > end:
            out[pid], reasons[pid] = None, 'INDEX_NO_POSSIBLE_PERIOD'
        else:
            out[pid] = {'start': start, 'end': end}
    return out, reasons, found


def narrow_cases(snapshot, cases, cohort):
    """The cases (minutes from birth, as `store_parity.py match` reads them) with each `index` case's windows narrowed;
    each such case gains `index_result`: {windows, reasons, ranges} in minutes. A narrowed start is rounded down and an
    end up to whole minutes, which only widens the window within what the argument allows."""
    import store_parity
    from patterns import execution_store as es
    births = store_parity.birth_micros(cohort)
    out = []
    with es.open_store(snapshot) as store:
        for case in cases:
            index = case.get('index')
            if not index:
                out.append(case)
                continue
            micros = {pid: None if w is None else {'start': births[pid] + w['start'] * MINUTE, 'end': births[pid] + w['end'] * MINUTE}
                      for pid, w in case['windows'].items()}
            query = store_parity.to_micros(case['query'])
            hits = CALLS['range_hits']
            windows, reasons, found = narrow(store, query, case.get('conditions'), index, micros, MINUTE)
            hit = CALLS['range_hits'] > hits
            # D52b's ruled identity: the narrowing without the cache gives the same windows, reasons and ranges.
            fresh = narrow(store, query, case.get('conditions'), index, micros, MINUTE, cache=False)
            identical = fresh == (windows, reasons, found)
            minutes = {pid: None if w is None else {'start': math.floor((w['start'] - births[pid]) / MINUTE),
                                                    'end': math.ceil((w['end'] - births[pid]) / MINUTE)}
                       for pid, w in windows.items()}
            rng = {sid: {pid: [n, None if lo is None else (lo - births[pid]) / MINUTE, None if hi is None else (hi - births[pid]) / MINUTE]
                         for pid, (n, lo, hi) in per.items() if pid in births}
                   for sid, per in found.items()}
            out.append({**case, 'windows': minutes,
                        'index_result': {'rule': RULE, 'base_windows': case['windows'], 'windows': minutes, 'reasons': reasons,
                                         'ranges': rng, 'cache': {'hit': hit, 'identical_without_cache': identical}}})
    return out


def main(argv):
    if argv[:1] == ['narrow'] and len(argv) == 2:
        data = json.load(sys.stdin)
        cohort = json.loads(Path(data['cohort']).read_text())
        print(json.dumps({**data, 'cases': narrow_cases(Path(argv[1]), data['cases'], cohort)}))
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == '__main__':
    sys.path.insert(0, str(HERE))
    sys.exit(main(sys.argv[1:]))
