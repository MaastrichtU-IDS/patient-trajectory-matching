# Three separately reviewed pressure strata

All three planned measurement-item strata now complete the exact-record query on the pinned, public MIMIC-IV demo 2.2 release. Each has its own explicit automated source-fidelity review and same-patient calendar declarations. Every anchor agrees with the independent unpartitioned SQL reference, and each run retains all 140 ICU stays. Clinical interpretation remains unverified.

## Common question, separate measurement items

Each request selects recorded norepinephrine segments (item 221906), a same-patient/stay baseline pressure below 65 mmHg strictly before segment start and at most 30 minutes earlier, and optional same-item/unit follow-up observations from segment start through 120 minutes afterward. All eligible baseline/segment pairs are retained; improvement and follow-up availability are not eligibility requirements. Follow-up can occur after the recorded segment ends.

Only the request identity and pressure item differ across the three requests. They share the original source files, treatment anchors, window parameters, unit, threshold and declared calendar basis. The comparison below concerns separate source-item coverage under one query. It does not establish that the items are clinically interchangeable or measure differences in device accuracy.

| Dictionary label | Request | Explicit review | Aggregate evidence |
|---|---|---|---|
| Arterial Blood Pressure mean | [arterial request](../examples/indexed-source-windows/demo-arterial-request.json) | [declaration](../data/arterial-source-fidelity-review.json) | [execution report](../verification/reviewed-arterial-demo-report.json) |
| Non Invasive Blood Pressure mean | [noninvasive request](../examples/indexed-source-windows/demo-noninvasive-request.json) | [declaration](../data/noninvasive-source-fidelity-review.json) | [execution report](../verification/reviewed-noninvasive-demo-report.json) |
| ART BP Mean | [art request](../examples/indexed-source-windows/demo-art-request.json) | [declaration](../data/art-source-fidelity-review.json) | [execution report](../verification/reviewed-art-demo-report.json) |

The [arterial demonstration](reviewed-arterial-demo.md) documents the unchanged audit, claim-review and execution mechanism. The two additional declarations adopt the same explicitly automated review scope, but bind different package/audit hashes and exact coverage counts. They attribute no review to a human clinician. All audited claims pass their source-fidelity checks before the declarations are applied. A source audit alone still creates no acceptance or calendar decisions.

## Observed results

| Result | Arterial | Non-invasive | Alternate arterial label |
|---|---:|---:|---:|
| Measurement item | 220052 | 220181 | 225312 |
| Unique source claims audited and accepted | 2022 | 1784 | 1042 |
| Unique selected measurement points | 1078 | 840 | 98 |
| Patient-calendar declarations | 17 | 25 | 4 |
| Completed batches | 964 | 962 | 944 |
| Anchors agreeing with unpartitioned SQL | 944 | 944 | 944 |
| ICU stays retained | 140 | 140 | 140 |
| Patients with an eligible pair | 13 | 19 | 2 |
| Stays with an eligible pair | 15 | 23 | 2 |
| Segments with an eligible pair | 66 | 131 | 3 |
| Eligible baseline/segment pairs | 86 | 151 | 3 |
| Follow-up bindings | 340 | 603 | 10 |
| Eligible pairs without selected follow-up | 1 | 10 | 0 |
| Positive / negative / zero deltas per binding | 297 / 25 / 18 | 474 / 107 / 22 | 8 / 2 / 0 |
| Stays with anchors but no eligible pair | 18 | 10 | 31 |
| Stays with no admitted treatment anchor | 107 | 107 | 107 |

All batches, anchors and stays complete without blocked outcomes. There are 2,832 anchor/stratum SQL comparisons across the three runs; these represent the same 944 treatment anchors evaluated under three separate item selections. Similarly, the 140 stays in each column are the same source population, not 420 distinct stays.

Patient counts are distinct within a stratum. They must not be summed to estimate a combined cohort: patients can occur in more than one stratum. The subsequent [overlap analysis](pressure-cohort-overlap.md) reproduces all three queries and establishes 23 distinct patients in their membership union, with separate stay and segment comparisons. Calendar declarations count patients with applicable non-empty measurement batches, not matched patients. Measurement counts describe the admitted records selected by the windows, not all observations of that item in the source release.

A binding identifies one baseline/segment/follow-up combination. Baselines, segments and follow-up measurements can be reused across different combinations. The deltas therefore describe dependent recorded observations; their signs are not patient response rates or treatment-effect estimates. An eligible pair without a selected follow-up remains eligible, and an empty window is not evidence that a measurement or clinical event never occurred.

## What this adds to the SULO work

The same admitted representation and matcher support three distinct source-item selections without adding ontology predicates or changing the temporal engine. This extends the evidence for the restricted source-to-claim-to-query path. It does not prove full mixed OWL reasoning or that a recorded claim is clinically true.

The result also demonstrates why measurement identity belongs in the query and its evidence: the same temporal and numeric conditions produce different source-record coverage when a different item is selected. Pooling the items would change the question and require a separately justified mapping. The source-fidelity declarations establish faithful record representation; clinical mapping remains a separate decision.

For efficient execution, the existing unique-claim review and pair-covering partitions remain sufficient for these bounded windows. Every merged answer is reconciled against the whole original window. These runs establish completeness within the selected records; they are not production-scale throughput measurements.

## Reproduce and validate

Use the pinned Python/Rust dependencies in [validation](validation.md) and supply the four original ICU files matching the [public-demo source pin](../data/clinical-source-demo-pin.json). The [open-access demo release](https://physionet.org/content/mimic-iv-demo/2.2/) and its [published checksums](https://physionet.org/files/mimic-iv-demo/2.2/SHA256SUMS.txt) identify the source. No credentialed full-MIMIC dataset is used for these results.

```sh
for stratum in arterial noninvasive art; do
  python -m patterns.reviewed_source_query \
    --input-dir /path/to/mimic-demo/icu \
    --request "examples/indexed-source-windows/demo-${stratum}-request.json" \
    --declaration "data/${stratum}-source-fidelity-review.json" \
    --output "verification/reviewed-source-query-run/${stratum}-report.json" || exit 2
done
python -m patterns.test_reviewed_pressure_strata
```

The runner reconstructs source selection, package and independent audit before checking the declaration. Changed relevant inputs or implementation invalidate the declaration. Only aggregates and hash-bound declarations are committed; source rows, individual claim histories, bindings and patient/stay identifiers remain local. Existing fail-closed behavior suppresses aggregate membership if execution is incomplete.

Three additional CI tests bind all reports to current implementation hashes, pinned full-file manifests, exact separate requests/declarations, completed accounting, comparable criteria and the candidate-plan results. Together with the existing 15 audit/execution tests, they guard the additional evidence without downloading source records in CI. They verify committed provenance and accounting; reproducing actual source execution requires the local command above.

## Remaining decision

The [clinical candidate plan](../data/clinical-candidate-plan.json) remains `PROPOSED_FOR_REVIEW`, with no clinical reviewer recorded. Review should now assess the concrete question and evidence: item meanings, segment-start interpretation, admission policies, baseline/follow-up windows and intended analysis unit. Recorded segment starts are not inferred course initiations; chart time is not historical availability. Clinical occurrence, physical elapsed time, cross-patient calendar comparisons, source-as-known replay and causal effects remain outside the result.
