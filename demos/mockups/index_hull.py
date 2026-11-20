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

Called by `store_trajectory.py` on every request with an `index` block (and never otherwise: `CALLS` counts), and by the
parity test and D52's tools through `narrow` on the command line, before `store_parity.py match`.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

RULE = 'index_hull_v1'
MINUTE = 60_000_000
MENTION = 'mention'  # the converter's letter-mention kind (patterns/execution_store.py, MENTION_KIND)
CALLS = {'narrow': 0}  # D52 rule 6: 0 for every request without an `index` block
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
    """{step id: (concept, k, from the end)} for the steps the index block names."""
    concept = {s['id']: (s['concept'], s.get('occurrence', 'any')) for s in query['steps']}
    derived = (conditions or {}).get('steps', {})
    out = {}
    for side in ('start', 'end'):
        x = index.get(side)
        if not x:
            continue
        sid = x['step']
        c, occ = concept[sid]
        if occ == 'first':
            out[sid] = (c, 1, False)
        elif occ == 'last':
            out[sid] = (c, 1, True)
        else:
            (which, k), = derived[sid]['occurrence'].items()
            out[sid] = (c, int(k), which == 'nth_last')
    return out


def ranges(store, query, conditions, index, threads=None):
    """{step id: {patient id: (count, lo, hi)}} on the store's clock; lo and hi None when there are fewer than k. With
    `threads`, the queries run on that many DuckDB threads (a request matched on that many workers; the store's own
    setting is restored after)."""
    if threads:
        before = store.db.execute("SELECT current_setting('threads')").fetchone()[0]
        store.db.execute(f'SET threads = {int(threads)}')
        try:
            return ranges(store, query, conditions, index)
        finally:
            store.db.execute(f'SET threads = {int(before)}')
    excluded = list((conditions or {}).get('exclude_kinds', ()))
    out = {}
    for sid, (concept, k, from_end) in hidden_steps(query, conditions, index).items():
        # D52 review, M1: a derived occurrence (an Nth, N > 1) ranks coded records only (D32), whatever `exclude_kinds`
        # says, so its candidates never include a letter mention.
        derived = sid in ((conditions or {}).get('steps') or {})
        kinds = sorted(set(excluded) | ({MENTION} if derived and _DEFECT != 'nth_counts_mentions' else set()))
        kind_sql = (f"AND e.kind NOT IN ({', '.join('?' for _ in kinds)})") if kinds else ''
        if _DEFECT == 'nth_off_by_one' and k > 1:
            k -= 1
        order = 'DESC' if from_end else 'ASC'
        lo, hi = ORDER_STATISTIC.get((k, from_end), (f"list_sort(list(f.start_lo), '{order}')[{int(k)}]",
                                                      f"list_sort(list(f.start_hi), '{order}')[{int(k)}]"))
        sql = RANGE_SQL.format(kinds=kind_sql, lo=lo, hi=hi)
        out[sid] = {pid: (n, lo, hi) for pid, n, lo, hi in store.query(sql, [concept] + kinds)}
    return out


def narrow(store, query, conditions, index, windows, offset_unit=1, threads=None):
    """(windows, reasons, found): each base window (a patient id's {start, end} on the store's clock, or None) narrowed;
    `reasons` names each patient the narrowing excluded; `found` is `ranges` (with `threads`). Offsets in the index block
    are multiplied by `offset_unit` (the server passes minutes, so 60,000,000)."""
    CALLS['narrow'] += 1
    found = ranges(store, query, conditions, index, threads)
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
            windows, reasons, found = narrow(store, query, case.get('conditions'), index, micros, MINUTE)
            minutes = {pid: None if w is None else {'start': math.floor((w['start'] - births[pid]) / MINUTE),
                                                    'end': math.ceil((w['end'] - births[pid]) / MINUTE)}
                       for pid, w in windows.items()}
            rng = {sid: {pid: [n, None if lo is None else (lo - births[pid]) / MINUTE, None if hi is None else (hi - births[pid]) / MINUTE]
                         for pid, (n, lo, hi) in per.items() if pid in births}
                   for sid, per in found.items()}
            out.append({**case, 'windows': minutes,
                        'index_result': {'rule': RULE, 'base_windows': case['windows'], 'windows': minutes, 'reasons': reasons,
                                         'ranges': rng}})
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
