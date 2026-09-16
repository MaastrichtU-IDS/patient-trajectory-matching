# Prepared source-audited measurement sessions

Status: executable `prepared-measurement-session-1.0`, with synthetic query equivalence and operation-count evidence. Integration with live pressure batches and clinical mapping acceptance remain pending.

Repeated measurement-class queries can now reuse a successful source audit, checked catalogue selection, accepted record views and compiled temporal network. The session evaluates changed numeric thresholds and temporal windows against the same supplied stores. It preserves the existing source-query result, including context identifiers, separate item/unit strata, possible/certain memberships and optional follow-up.

## Admission and reuse

[`Session`](../patterns/prepared_measurement_session.py) accepts the same folder and inputs as [`measurement_source_catalogue.execute`](measurement-source-catalogue.md), plus an optional serialized preparation budget. Construction runs that complete source-audited reference execution. A pending/withdrawn mapping, unresolved reasoning result, invalid source claim or incomplete query cannot create a reusable session. There is no API for inserting caller-constructed audit certificates or prepared views.

A successful constructor checks that all item strata have identical underlying record views, retains one private copy of those views, and compiles a shared source temporal network. Per-item selectors are reapplied for each query; baseline/follow-up pairs still use the same item and literal unit. Query evaluation uses the existing prepared mixed-query arithmetic and fixed-binding certainty semantics.

| Input or work | Session behavior |
|---|---|
| Measurement source CSVs | Byte digests checked before/after construction and every query |
| Measurement source audit | Successful result retained from construction |
| Mapping catalogue, terminology, proposals and review | Private constructor snapshot; checked selector plan reused |
| Stores, acceptance policies and clock alignment | Fixed constructor snapshot; checked views reused |
| Treatment class and query ID | Fixed; changing either requires another session |
| Measurement item set and literal unit | Fixed on both sides; item-list order may change |
| Numeric comparison and temporal controls | Revalidated and evaluated for every query |
| Compiled source network | Reused across strata and queries |
| Implementation artifacts | Digests checked before/after construction and every query |

Source checks cover the measurement audit's three tables: `chartevents`, `icustays` and `d_items`. The supplied interval store is immutable session input; this profile does not independently verify interval-source CSVs. Raw source files are still read for SHA-256 checks on every query, but CSV parsing, source auditing, catalogue reasoning, treatment classification and network compilation are not repeated. Initial preparation still runs the original query once per supported item.

## Snapshot lifetime and failure behavior

The constructor copies its input documents; changing a caller's dictionary or review object cannot alter the active snapshot. Applying a new source policy, mapping review, selector, store, alignment or backend timeout requires a new session. This API does **not** watch external review files or automatically apply later withdrawals. A service adopting it must close or replace sessions when it adopts a new review context.

A detected source or implementation change before or during evaluation invalidates the session and raises an error without returning a successful result. Restoring the old file later does not revive it. An incomplete stratum withholds combined memberships, returns the existing explicit blocked result and closes the session. Invalid query syntax or changes outside the fixed selector/treatment scope are rejected without changing the valid snapshot. `close()` explicitly discards prepared state.

The class is intended for one worker. Returned results and `snapshot()` metadata are defensive copies. The default budget is 16 MiB of serialized retained payload, configurable up to 64 MiB; it is not a bound on Python resident memory, temporary construction allocations or compiled-network storage. Existing per-file, per-store, temporal variable and query candidate limits also apply. Oversized preparations fail explicitly instead of silently switching execution modes.

## API example

```python
from patterns.prepared_measurement_session import Session
from patterns.verify_prepared_measurement_session import EXAMPLE, load_example, arguments, queries

# Authored synthetic source files and explicit synthetic reviews.
values, prepared = load_example()
args = arguments(values, prepared, 0)
session = Session(EXAMPLE, *args)
try:
    for query in queries(args[-1]):
        response = session.execute(query)
        if response['status'] == 'COMPLETED_REVIEWED_MEASUREMENT_QUERY':
            print(response['result']['query_result']['certain_patient_ids'])
finally:
    session.close()
```

`response['result']` has exactly the original source-audited execution result. The outer response adds a prepared-session context linking it to the admitted snapshot. `snapshot()` exposes the session context, evaluation count and invalidation state without exposing cached objects.

The controls operate over all records in the supplied bounded stores. This profile does not establish complete source coverage or the wider retrieval envelope of an indexed pressure batch. Live-service integration must preserve that batch's reviewed envelope rather than treating this API as permission to retrieve additional records.

## Verification and remaining work

```bash
python -m patterns.verify_prepared_measurement_session
python -m unittest patterns.test_prepared_measurement_session
```

The [authored fixture](../examples/prepared-measurement-session/) has two source items mapped to one synthetic category. It includes a follow-up on a different item from its baseline and a patient without follow-up. Four query variants across three stays exercise threshold changes, shorter baseline windows and different follow-up controls.

The [committed report](../verification/prepared-measurement-session-report.json) records 12 prepared queries whose complete source-query results equal fresh executions. Each stratum also agrees with a SQL control using raw CSV values; admission and source-policy selection are shared. Actual call instrumentation confirms that prepared queries repeat none of the source-audit, mapping-plan, fresh mixed-query or network-compilation operations. This demonstrates eliminated work, not a latency or full-data scalability claim.

Twenty tests additionally cover private input/results, source and implementation changes before/during work, pending/withdrawn review, backend failure, preparation budgets, fixed query scope, missing alignment, empty acceptance, uncertain treatment timing, partial-stratum failure and closure. Clinical mappings remain pending. Next steps are pressure-batch integration with review-context replacement, followed by measured end-to-end latency on representative workloads.
