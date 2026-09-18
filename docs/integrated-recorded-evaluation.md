# Integrated recorded workflow evaluation

`tools/evaluate_integrated_recorded.py` evaluates the actual
`RecordedJourneyWorkspace` on the pinned public MIMIC-IV demo 2.2 files. It
extends the pressure-service benchmark with typed pattern editing, explicit
weighted comparison, durable storage, restart recovery, export and exact replay.

Run from the repository root with the documented semantic dependencies installed:

```sh
python -m tools.evaluate_integrated_recorded \
  --mimic-dir /path/to/mimic-demo/icu \
  --output verification/integrated-recorded-evaluation.json
```

The default runs arterial, noninvasive and ART strata sequentially in isolated
processes. Add `--workers 3` to run the three isolated strata concurrently; their
reported timings then include competing workloads on the shared host. Use `--strata art` for the smallest initial rehearsal. Each stratum
reopens its admitted sources for exact replay, so expect substantial time beyond
the underlying service benchmark. Do not edit implementation files while it runs:
source and implementation fingerprints must remain stable. The output is written
only after every requested stratum completes every check. A failed run does not
produce a success report.

## What is checked

1. Execute the default query through the durable workspace, retaining every
   admitted anchor and the complete source roster.
2. Revise the baseline predicate to `<= 60`, narrow its window to 25 minutes and
   narrow follow-up to 60 minutes. Assert that the original evidence is unchanged
   and both queries retain the same admitted population.
3. Independently enumerate every anchor's point bindings using exact rational
   arithmetic; reconcile them with the engine result and its SQL agreement flag.
   Preserve missing follow-up rather than imputing an outcome.
4. Compare the first deterministically ordered reference with complete features,
   using latest value (weight 2, scale 10 mmHg), count (weight 1, scale 2) and
   recency (weight 1, scale 5 minutes). Recompute features from admitted raw points,
   all patient-best anchors, exact distances, contribution sums, complete ranking
   and unresolved coverage independently of the similarity implementation.
5. Export the full retained evidence and comparison. Close the workspace and
   reopen the same SQLite state. Require identical job, snapshot, comparison and
   export, then perform a fresh replay against the explicitly supplied demo path.

The aggregate report contains timings, sampled process-tree memory, evidence
sizes, counts, source/implementation hashes and check outcomes. It excludes raw
rows, patient identifiers, anchor tokens and local source paths. Temporary
full-evidence snapshots and the SQLite workspace remain local and are removed
when the evaluator finishes.

`python -m unittest tools.test_integrated_recorded_evaluation` checks the
independent oracles against the actual authored engine and deliberately corrupts
bindings, distances and unresolved coverage to ensure those checks fail.

## Executed result (18 September 2026)

The committed [aggregate report](../verification/integrated-recorded-evaluation.json)
passed for all three pressure strata, executed with three concurrent isolated
workers. Each stratum retained the same 100 patients, 140 stays and 944 treatment
anchors. The original and revised queries account for **5,664 independently
checked anchor results**, followed by exact fresh-source replay of each workflow.
All original-evidence, durable-restart and complete-export equality checks passed.

| Stratum | Cold query + save (s) | Pattern revision + save (s) | Weighted comparison (s) | Fresh replay (s) | Ranked / unresolved peers |
| --- | ---: | ---: | ---: | ---: | ---: |
| ART | 59.43 | 1.23 | 0.13 | 69.04 | 2 / 97 |
| Noninvasive | 432.45 | 4.25 | 0.46 | 481.90 | 22 / 77 |
| Arterial | 513.14 | 4.71 | 0.51 | 485.31 | 16 / 83 |

There were 99 eligible peers after excluding each selected reference patient.
Large unresolved counts are an explicit consequence of sparse eligible pre-index
pressure data; they are not silently removed or assigned zero-valued features.
The largest export was 7,050,193 bytes, below the 8 MiB limit. Maximum sampled
worker-process-tree RSS ranged from 643.9 to 892.9 MiB. Total sequence times were
132.12, 923.39 and 1,009.18 seconds respectively; they include independent checks,
restart readback and replay. These are descriptive shared-host measurements, not
production capacity estimates.

## Interpretation

This is technical workflow acceptance. It does not establish clinical mapping
correctness or similarity usefulness. Its three comparison features describe one
reviewed pressure stream; they do not evaluate the separately admitted clinical
variable feature-pack extension. The public demo is small, its pressure strata
overlap, and its patient counts must not be summed across strata. No full
credentialed MIMIC-IV cohort, clinician relevance judgments, patient-held-out
retrieval study or production throughput is claimed.

Timings are one sequence per stratum on a shared host. A cold run starts fresh
application state without clearing operating-system caches. Sampled RSS includes
the independent checks and replay; it is not an exact instantaneous peak.
