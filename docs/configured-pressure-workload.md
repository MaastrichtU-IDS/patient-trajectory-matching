# Configured pressure workload verification

Status: executable sequential workload runner for the [configured mapped service](configured-pressure-service.md), with authored fixture evidence. It measures supplied workloads within the existing service limits; it does not establish clinical validity or representative clinical-data performance.

PR #42 made the source and review inputs configurable. This increment makes the next evaluation repeatable: the same bounded list of controls is executed through the HTTP service, compared with fresh mapped execution, and inspected through both evaluated and repeated job routes. The report records the work actually performed when caches hit, cannot retain a result, or evict prepared batches.

## Run

From the repository root, with the pinned Python/Rust dependencies installed:

```sh
python demo/benchmark_configured_pressure.py \
  --config examples/configured-pressure-service/config.json \
  --workload examples/configured-pressure-service/workload.json \
  --repetitions 3 \
  --output verification/configured-pressure-workload-run/local-report.json
```

The command starts and closes its own loopback HTTP server. It requires no separately running demo server. Use an existing source configuration and explicit reviews to evaluate another supplied dataset. It neither generates nor accepts source or terminology review decisions.

The [workload file](../examples/configured-pressure-service/workload.json) follows a closed [schema](../schemas/pressure-workload.schema.json). It contains 1–8 distinct control objects, each with `threshold`, `baseline_minutes` and `followup_minutes`; the command accepts 1–10 repetitions, defaulting to three. Workload files are limited to 64 KiB. Every query must fit the configured parent envelope and existing pressure-control rules before execution starts. Files, mappings, units, items and treatment classes cannot be selected through the workload.

## What each measurement means

The run begins with one default query that prepares the source and batches. It then evaluates each workload query in order, repeating that order the requested number of times.

| Measurement | Executed work | Verification |
|---|---|---|
| Initial service job | Default controls, source admission and batch preparation | Existing service completion and anchor SQL gates |
| Prepared trial | Complete-result cache explicitly cleared; prepared batch cache retained | Complete result equals fresh mapped execution; SQL reruns for every anchor |
| Identical repeat | Same controls submitted again | Complete result equals the prepared trial; actual cache mode recorded |
| Fresh mapped reference | Direct session execution with fresh mapped batch execution, following initial source-fidelity preparation | Every result field except elapsed duration equals the prepared result |
| HTTP inspections | Every anchor from both service jobs | Each response equals the fresh reference inspection and has SQL agreement |

The deliberate complete-result cache clearing makes repeated prepared-query measurements possible without confusing them with saved-result lookups. It does not clear prepared batches. Prepared cache evictions can therefore cause new source admission and compilation; those operations are counted. An identical repeat whose result could not fit in the complete-result cache is reported as a recomputation. Such trials are excluded from cached-latency summaries, which have zero samples and null timing values if no hit occurred.

Per-query summaries report sample count, minimum, median and maximum. They keep internal service job durations, observed HTTP durations, and direct fresh-reference durations separate. HTTP time includes submission, polling and result transfer; the polling interval is 10 ms. Inspection time is outside these query measurements. Source audits, mapping plans, network compilations and anchor SQL calls are instrumented during service jobs only. The instrumentation itself contributes overhead.

This is a fixed-order sequential experiment, not a randomized speedup study. The cold preparation is measured once. Fresh-reference time omits startup and initial source-fidelity preparation. Cache memory fields describe serialized payload at trial end; they are neither peak memory nor process RSS. The runner owns the module-global service during execution and must run in its own process, not concurrently with another in-process server or benchmark. Its 600-second job polling deadline is not a hard process timeout: cleanup waits for any active worker to finish.

## Provenance and failure behavior

The report binds the startup configuration context, configuration/workload byte hashes, query controls, repetition count, implementation artifacts, final session identity and four supplied source-file hashes. It contains aggregate cohort/coverage counts and cache/timing observations. It omits patient rows, patient/stay identifiers, source paths, source labels, reviewer text and individual inspection responses. Clinical mapping verification remains false; source scope remains supplied records without publisher authentication.

Configuration/review, workload, source and implementation checks run at the relevant execution boundaries and before a verified report is returned. Any incomplete job, source change, result difference or inspection difference fails the workload. No partial trial metrics are published as a successful workload.

Output is written atomically. Once inputs and output protection have been validated, an execution failure replaces a prior output with a `FAILED` report containing only context identity and a fixed failure stage/code; exit status is 1. This avoids leaving an old success at the requested report location after a failed execution. Invalid initial inputs or unsafe output paths are rejected before writing, with a failure summary on stdout. Source/mapping directories, configuration/request/review/workload files and implementation artifacts are protected from overwrite, including resolved symlink targets. Reports must use a `.json` filename.

## Authored evidence

The [committed report](../verification/configured-pressure-workload-report.json) runs two queries three times:

| Query | Threshold | Baseline / follow-up | Eligible patients |
|---|---:|---|---:|
| 0 | <65 mmHg | 15 / 60 minutes | 1 |
| 1 | <59 mmHg | 10 / 30 minutes | 0 |

All five stays and three anchors remain represented. Each of the six prepared trials reuses one measurement batch, with zero repeated source audits, mapping plans or compilations, and three anchor SQL checks. The six identical repeats use the complete-result cache. All result fields except duration agree with fresh execution; all 36 HTTP inspections agree with their fresh reference. The empty cohort for query 1 is a completed record-query result, not an incomplete search or proof of clinical absence.

The fixture is tiny and authored. Its timings establish a reproducible measurement method, not a workload-scale or clinical-data performance claim. Next evaluation work is to run explicitly reviewed representative datasets, vary event density and batch coverage, and measure peak memory and cold-preparation costs.

## Tests and CI

```sh
python -m unittest discover -s demo -p 'test_configured_pressure_workload.py'
```

Twelve tests cover workload/envelope bounds, actual HTTP/fresh-result/inspection equivalence, cache non-retention and eviction, changed inputs, deliberate result/evidence differences, pending reviews, safe atomic report publication, and committed provenance. CI runs a new one-repetition workload and uploads `verification/configured-pressure-workload-run/report.json` as `configured-pressure-workload-evidence`. Timing values are not CI acceptance thresholds. The demo suite totals 92 tests; the contract suite was 781 checks at this increment.
