# Reusing reviewed batch preparation across changed queries

The live pressure service now retains checked batch views between different thresholds and narrower time windows. Each changed query still evaluates its own measurement predicates, baseline/follow-up temporal constraints and complete SQL reference. An identical completed request can continue to use the separate [result cache](pressure-query-cache.md).

## What is reused

A batch's first request runs the original mixed executor, including record selection, claim isolation, graph projection and checked Rust semantic support. Only a complete result can supply reusable views. The preparation retains the selected measurement records, interval projection and treatment witnesses, explicit clock alignment and compiled source temporal networks. All selected bounds and source correlations remain in those networks.

The preparation key binds both complete stores, both acceptance policies, the semantic policy, explicit alignment, treatment query ID/class, backend timeout and implementation hashes. Changing any of these inputs requires fresh preparation. Baseline and follow-up selectors are deliberately outside that key: they are validated and evaluated anew. Thus reuse cannot turn a record's prior eligibility into eligibility under a different query.

Each changed query:

1. Applies its item/unit and exact scalar predicates to the selected measurements.
2. Constructs its baseline and follow-up constraints.
3. Classifies each named binding against the original feasible source timelines.
4. Calculates recorded differences and preserves optional follow-up.
5. Merges the complete partition results and checks every anchor against the independent unpartitioned SQL query.

Certainty keeps its original quantifier: one named binding must hold in every feasible selected-source timeline. Evaluation never treats a baseline-restricted timeline as the source for the joint follow-up proof. Patient/stay boundaries, explicit clock comparability and half-open interval behavior remain part of evaluation.

## Implementation and provenance

[`PreparedExecutor`](../patterns/prepared_mixed_query.py) is an additive executor. The original [`mixed_record_query.execute`](../patterns/mixed_record_query.py) remains the reference path. The prepared evaluator uses the same validation, decimal, query-edge and temporal-classification primitives. Its composition mirrors the reference orchestration; differential tests compare every returned field, including proof paths, selected graphs and context IDs. Changes to either evaluator require maintaining this equivalence coverage.

[`Session.execute`](../patterns/reviewed_pressure_session.py) now accepts an internal `batch_executor` callback. Its default is the original executor. The existing review checks, complete-anchor gate, merging, SQL reference and inspector apply to either path. This callable is a trusted Python integration point; the HTTP interface accepts only the existing closed query controls.

The semantic query/evidence context stays identical when both executions produce the same result. The job's separate `execution.batch_preparation.cache.implementation_context_id` binds the prepared implementation; verification reports record its artifact map. Reused treatment support is reported as reused preparation, while temporal eligibility and SQL reconciliation are fresh computations. No new SULO relation or broader OWL reasoning claim is introduced.

## Bounds and invalidation

The current session keeps at most 1,024 prepared batches and 128 MiB of serialized view payload, using least-recently-used eviction. Oversized views execute without retention. The byte limit excludes derived network objects and Python object overhead; it is not a heap-memory cap. Workloads exceeding the budget can require repeated preparation. The complete-result cache retains its separate three-entry/32 MiB limit.

Preparation is private to one service worker and stays in memory. Public result/inspection responses copy cached objects, including temporal path certificates. Selecting another stratum replaces preparation. A failed or blocked job clears reusable state; source/review changes refuse successful publication. Implementation hashes are checked before and after each batch, and the service retains its checks around the complete job. The pinned installed runtime must remain fixed until server restart; repository hashes do not fingerprint arbitrary installed-package files.

As with the previous cache, these checks operate at validation boundaries rather than providing a transactional filesystem snapshot. Prior completed jobs remain historical evidence while retained; changed inputs do not relabel them as current results.

## Run and inspect

The existing commands enable prepared reuse by default:

```sh
python demo/serve.py --pressure-synthetic
python demo/serve.py --mimic-dir /path/to/public-demo/icu
```

Run a query, then change the threshold or narrow a window. The page labels a fresh query over prepared records and shows the number of reused batches. The API reports fresh/reused batch counts, preparation and reevaluation durations, retained entry/payload counts, evictions and the prepared implementation context. A complete-result cache hit reports zero batch work.

The Python constructor `PressureService(..., prepared=False)` runs the original batch path. The earlier completed-result benchmark explicitly selects this mode so its fresh-query baseline remains meaningful.

## Local measurements

The authored changed query took 0.018 seconds through the prepared service and 1.168 seconds through the original session executor. On the public arterial sequence, all 557 non-empty batches fit in the cache (104,819,911 serialized bytes); the changed query took 2.313 seconds with zero fresh batch preparations. These figures include fresh temporal evaluation and per-anchor SQL reconciliation. The original executor took 336.433 seconds for the same changed query. All result fields except duration and all 944 anchor inspections match exactly. The changed controls select 12 patients, 14 stays, 27 segments, 36 eligible pairs and 106 follow-up bindings, with one eligible pair lacking follow-up; all 140 source stays remain represented.

The initial public query and preparation took 339.961 seconds. Preparation therefore remains a startup cost; this increment benefits subsequent exploration within that reviewed source session. These are local samples, without a controlled hardware or statistical performance study.

## Verification

Eleven new demo tests cover exact differential results, uncertain finite-world cases, scope and precision, all preparation-key inputs, invalidation, bounded eviction, response isolation, fresh SQL execution and corruption detection. They bring the demo suite to 51 Python tests, separate from the 687 contract checks. Existing mixed-query fixtures also check their results against an independent finite-world oracle within the differential test.

```sh
python demo/benchmark_prepared_pressure.py --output verification/prepared-pressure-synthetic-report.json
python demo/benchmark_prepared_pressure.py --mimic-dir /path/to/public-demo/icu --output verification/prepared-pressure-arterial-report.json --live-report verification/live-pressure-demo-report.json
python -m unittest discover -s demo -p 'test_*.py'
node demo/test_pressure_ui.cjs
```

The benchmark first executes the default query, then changes all three controls to threshold 60, baseline 25 minutes and follow-up 60 minutes. It compares that changed query with a full execution of the original graph/semantic/SQL path and compares every anchor inspection. Reports include only aggregate counts, timings and provenance hashes. The live and complete-result reports are regenerated because the session's implementation hash changed; the original source reviews and declarations remain the same.

The [synthetic report](../verification/prepared-pressure-synthetic-report.json) and [public arterial report](../verification/prepared-pressure-arterial-report.json) are local measurement sequences, without a statistical latency guarantee. CI checks artifact provenance and behavioral invariants without downloading patient records or enforcing timing thresholds. Clinical interpretation and visual browser/layout verification remain open.
