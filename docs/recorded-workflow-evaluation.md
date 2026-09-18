# Recorded workflow evaluation and clinical review

The implemented evaluation checks one concrete recorded question: which ICU
patients have a recorded norepinephrine segment with a same-item pressure below
the chosen threshold shortly before its start, and which pressure observations
are recorded afterward? It keeps every eligible baseline/segment pair, including
pairs without follow-up. A segment start does not establish course initiation;
an observed pressure difference does not estimate a treatment effect.

## Reproduce the technical evaluation

Install the pinned dependencies in [validation](validation.md). Supply the four
original files of the public MIMIC-IV demo 2.2 release matching
[the source pin](../data/clinical-source-demo-pin.json): `inputevents.csv.gz`,
`chartevents.csv.gz`, `icustays.csv.gz`, and `d_items.csv.gz`.

```sh
python -m tools.evaluate_recorded_workflow \
  --mimic-dir /path/to/mimic-demo/icu \
  --output verification/recorded-workflow-evaluation.json
python -m unittest tools.test_recorded_evaluation
```

The evaluator runs each of the arterial, non-invasive and alternate arterial
strata in its own process, sequentially. It verifies the source pin and existing
source-fidelity declarations. Per stratum it executes:

1. A cold application query: threshold strictly below 65 mmHg, baseline within
   30 minutes strictly before segment start, optional follow-up from zero through
   120 minutes after start.
2. The same query with warm preparation but a cleared complete-result cache.
3. A changed query: threshold below 60, baseline within 25 minutes, follow-up
   through 60 minutes.
4. A complete-result cache hit for that changed query, with source/review checks.

Every admitted treatment anchor must agree with the existing independent SQLite
reference. A second, exhaustive Python enumeration reconstructs all expected
baseline/follow-up bindings from the admitted evidence, checks exact decimal
differences, missing follow-up, and aggregate reconciliation. It uses neither the
temporal engine nor its compiler nor the SQL reference. Warm and cached full
results must be identical to their corresponding fresh executions, apart from
elapsed time. Zero-match anchors and stays without anchors remain accounted for.

The report includes only counts, source and implementation hashes, fixed query
parameters, runtime and memory measurements. It does not contain source records,
patient/stay identifiers, individual values, or local source paths. Three strata
share a population; their patient counts must not be added.

## Observed public-demo results

The [fresh aggregate report](../verification/recorded-workflow-evaluation.json)
records 12 completed trials: four per stratum. Each trial retains all 140 ICU
stays and checks all 944 treatment anchors, giving 11,328 anchor/trial comparisons
with both SQL and independent Python enumeration. These are repeated checks of
the same source anchors, not 11,328 distinct treatment segments.

| Default query | Arterial | Non-invasive | Alternate arterial label |
|---|---:|---:|---:|
| Matched patients | 13 | 19 | 2 |
| Eligible baseline/segment pairs | 86 | 151 | 3 |
| Follow-up bindings | 340 | 603 | 10 |
| Eligible pairs without selected follow-up | 1 | 10 | 0 |
| Changed-query matched patients | 12 | 16 | 1 |
| Changed-query eligible pairs | 36 | 83 | 1 |

| Observed job time / whole-worker memory | Arterial | Non-invasive | Alternate arterial label |
|---|---:|---:|---:|
| Cold default job (s) | 571.177 | 393.731 | 57.014 |
| Warm default, fresh execution (s) | 5.292 | 3.126 | 0.754 |
| Warm changed query (s) | 7.744 | 3.147 | 1.004 |
| Changed-query complete-result cache hit (s) | 0.121 | 0.035 | 0.029 |
| Maximum sampled process-tree RSS (MiB) | 532.18 | 540.02 | 530.64 |

Full evidence equality holds for the repeated default query and changed-query
cache hit. Missing follow-up remains explicit in every run. The much larger
cold cost includes graph/reasoning preparation that warm queries reuse; these
single local measurements are not a performance service-level promise.

## Performance interpretation

The isolated worker's entire process tree is sampled through Linux `/proc` every
50 ms. The report records maximum observed aggregate RSS, sampling gaps, process
counts and limitations. This is not an exact memory peak or unique physical
memory: short-lived allocations may be missed and shared pages may be counted
more than once. Memory covers all four trials and their independent verification,
not a separate per-query memory estimate.

Reported job time distinguishes source preparation, query execution, final
validation and cache handling; outer wall time includes polling. Each stratum has
one observed cold/warm/cache sequence, not a latency percentile. Cold means new
application state, not flushed operating-system caches. Other work may share the
host. This establishes measured behavior on the complete **public-demo** source
population; it does not establish production throughput or full-MIMIC scalability.
No scaled authored fixture or held-out clinical retrieval evaluation is claimed.
This workload exercises the literal recorded-pressure service. Browser rendering,
the configurable recorded-pattern compiler and multi-feature peer scoring are
outside these timing measurements and require their own acceptance checks.

## Clinical review worksheet — not yet approved

The [candidate plan](../data/clinical-candidate-plan.json) remains
`PROPOSED_FOR_REVIEW`. Automated source-fidelity declarations establish faithful
representation of selected records. They are not clinician approval. A qualified
clinical collaborator should record their name, role, date, source release and
versioned decision for each item below, including accepted changes or rejection.

| Decision | Concrete material to review | Required recorded response |
|---|---|---|
| Scientific question | Norepinephrine segment with preceding pressure and subsequent observations | Intended descriptive use; why this question matters; exclusions |
| Treatment anchor | Item 221906 and exact source segment starts | Whether segments may be analyzed as such; course/revision/duplicate rules |
| Measurement identity | Items 220052, 220181 and 225312 as separate strata | Accept/reject each item's role and unit admission; whether any pooling is justified |
| Eligibility | All admitted ICU stays, no implemented age restriction | Population exclusions, repeated-stay handling, missing-data policy |
| Baseline | All eligible measurements, strictly before anchor, 30-minute window, threshold 65 | Accept or revise threshold, window and analysis unit; identify confounding implications |
| Follow-up | Optional measurements through 120 minutes, possibly after segment end | Accept or revise timing; ensure absent measurement is not interpreted as no response |
| Time and availability | Patient-local calendar; chart time differs from availability | Which time claims are defensible and which require additional source history |
| Similarity | Explicit pre-index feature profile and its explanations | Clinically relevant features/scales/weights; minimum coverage; prohibited leakage |
| Validation target | Independently adjudicated eligibility and relevant-peer judgments | Reviewer rubric, disagreements, uncertainty labels and sign-off criteria |

Do not change the candidate plan to an approved state without the actual review.
Do not infer a clinical label from SQL agreement or from the graph's match status.

## Full-source and held-out evaluation protocol

1. Obtain an authorized, locally configured full-source release and record its
   manifests, dictionary version, review declarations and governance conditions.
   The public demo is not a substitute for those inputs. Re-run source admission
   and reconcile every retained, excluded and blocked anchor before interpretation.
2. Freeze a patient-disjoint development/evaluation split and a versioned clinical
   feature/profile definition before inspecting evaluation outcomes. No follow-up
   value or temporal membership label may enter pre-index ranking. Availability
   claims need availability timestamps, not chart time alone.
3. Have independent reviewers label a prespecified stratified sample of positive,
   negative, missing-follow-up and uncertain records, and assess relevant peers
   without seeing algorithm ranks. Record agreement and adjudication separately.
   Set sample size, review budget and acceptance thresholds before evaluation.
4. Compare temporal eligibility with an independently written source query and
   clinical adjudication. Report technical disagreement, clinical disagreement,
   incomplete coverage and unresolved cases separately. For peer relevance,
   compare against declared simple baselines using precision/recall at k and
   nDCG only after a relevance rubric and labels exist; report patient-level
   uncertainty intervals and strata rather than pooling dependent bindings.
5. Benchmark the frozen workload at explicit cohort sizes and feature counts,
   recording cold preparation, warm execution, complete-result reuse, concurrent
   jobs, queue delay, restart/replay behavior and process-tree memory. Report
   throughput and latency quantiles over repeated runs with controlled hardware
   and load. Authored scaling fixtures may stress mechanics but cannot establish
   clinical retrieval quality or full-source performance.

The present technical evaluation completes reproducible query correctness and
local public-demo measurements. Clinical usefulness, a validated phenotype,
held-out retrieval quality and representative full-source performance require
the external review, source access and labels described above.
