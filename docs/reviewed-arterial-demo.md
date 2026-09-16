# Reviewed arterial-pressure source demonstration

Status: a complete exact-record query on the pinned MIMIC-IV demo 2.2 arterial-pressure stratum, with aggregate evidence in the [execution report](../verification/reviewed-arterial-demo-report.json). Acceptance is an explicitly declared, automated source-fidelity review. No human clinical approval, validated phenotype or treatment-effect estimate is claimed.

## Question and declared interpretation

The [existing arterial request](../examples/indexed-source-windows/demo-arterial-request.json) asks for recorded norepinephrine input segments with an arterial mean-pressure value below 65 mmHg strictly before segment start and at most 30 minutes earlier. It retains every eligible baseline/segment pair and all same-item, same-unit observations from segment start through 120 minutes afterward. Follow-up is optional and an improvement is not required for eligibility.

These are illustrative parameters for a technical source query. Item 221906 denotes the selected recorded input component and item 220052 denotes the separate arterial mean-pressure stratum. Items 220181 and 225312 are not pooled into this run. The [clinical candidate plan](../data/clinical-candidate-plan.json) remains proposed for clinical review.

The [inputevents documentation](https://mimic.mit.edu/docs/iv/modules/icu/inputevents.html) explains that rate changes can create new rows within a continuing administration. Accordingly, each anchor is a recorded segment start, not an inferred treatment-course initiation. The [chartevents documentation](https://mimic.mit.edu/docs/iv/modules/icu/chartevents.html) distinguishes observation chart time from the time a value was entered or validated. This run uses charted observation time and does not infer historical availability.

The [demo release description](https://physionet.org/content/mimic-iv-demo/2.2/) documents consistent date shifting, and the [parent MIMIC-IV 2.2 description](https://physionet.org/content/mimiciv/2.2/) specifies one shift per patient. The declared calendar interpretation is therefore a common recorded calendar within a patient in this same release. Original clock-origin witnesses are checked separately. Cross-patient calendar comparison and physical elapsed-time verification remain outside the claim.

## Source-fidelity audit and explicit acceptance

[`source_fidelity_audit.py`](../patterns/source_fidelity_audit.py) rereads and hashes all four original CSV/gzip files using a separate bounded parser. It retains requested original record positions and checks each claim against a direct reference mapping from those CSV fields. It does not call the importers' claim-description functions.

All 2,022 unique claims pass: 944 recorded input segments and 1,078 measurement points. The audit checks exact claim content and identity/hash, original row/file evidence, item-table and patient/stay identity, and an original source witness for the clock origin. It also checks displayed source fields, proper input intervals, and unflagged numeric measurement literals. Extra claim facts, changed timestamps, altered scalar values and changed source files fail the relevant gate.

The audit creates no acceptance or alignment decisions. The separate [review declaration](../data/arterial-source-fidelity-review.json) explicitly selects **all and only the fidelity-verified claims in this exact package and audit**, and declares the limited same-patient calendar interpretation. Both context hashes and expected claim/calendar counts are fixed. Any changed source, request, package, audit or relevant implementation requires a new declaration.

The reviewer attribution identifies the Codex assistant and the automated source-fidelity basis. It does not attribute review to Michel Dumontier or to a clinician. This is rule-based record acceptance following the user's instruction to continue the technical demonstration; it is not manual adjudication of 2,022 clinical observations. Acceptance verifies faithful representation of the recorded source, not whether the underlying clinical event occurred as recorded.

[`reviewed_source_query.py`](../patterns/reviewed_source_query.py) materializes that bounded declaration as individual hash-bound decisions in the existing unique-claim review format. It propagates those decisions and 17 patient-calendar declarations to every relevant batch, runs the existing review validator, then invokes the unchanged partition executor.

## Results and complete accounting

| Result | Count |
|---|---:|
| Original inputevent rows scanned | 20,404 |
| Original chart rows scanned | 668,862 |
| Unique claims audited and accepted for record fidelity | 2,022 |
| Completed batches | 964 |
| Anchors agreeing with unpartitioned SQL | 944 |
| Retained ICU stays | 140 |
| Patients with an eligible baseline/segment pair | 13 |
| Stays with an eligible pair | 15 |
| Segments with an eligible pair | 66 |
| Eligible baseline/segment pairs | 86 |
| Follow-up bindings | 340 |
| Eligible pairs without selected follow-up | 1 |

There are no blocked batches, anchors or stays. Of the 140 stays, 107 have no admitted treatment anchor and 18 have anchors but no eligible selected-record match. The 340 follow-up bindings contain 297 positive, 25 negative and 18 zero deltas.

Every completed anchor is compared with an unpartitioned query over its original selected source rows using the independent SQLite reference. Oversized windows retain pair-covering partitions; all original anchors and stays remain accounted for. Empty measurement windows and stays with no admitted anchor remain explicit.

Patient membership follows baseline/segment eligibility. Missing follow-up does not remove a patient. Different baselines, repeated segments and repeated measurements can produce multiple bindings for one patient; these counts are dependent record-query observations. Delta signs describe matched same-item/unit record pairs and cannot be interpreted as treatment success rates or causal responses.

## Reproduce the run

Install the existing pinned Python/Rust dependencies as described in [validation](validation.md). Supply the four original files matching the [source pin](../data/clinical-source-demo-pin.json), then run:

```sh
python -m patterns.reviewed_source_query \
  --input-dir /path/to/mimic-demo/icu \
  --request examples/indexed-source-windows/demo-arterial-request.json \
  --declaration data/arterial-source-fidelity-review.json \
  --output verification/reviewed-source-query-run/arterial-report.json
```

The declaration is mandatory; no acceptance rule or calendar alignment is supplied by default. This command reconstructs the package and audit, checks the declaration, creates explicit per-claim decisions, validates all batches and runs the query. Its output is aggregate evidence only. A failed batch or anchor leaves `metrics` null instead of presenting an incomplete cohort count.

Detailed source evidence, review decisions and execution records from the development run remain local. The repository contains no redistributed patient rows or patient identifiers. The report records source manifests, package/audit/review/execution hashes and implementation hashes for audit and reproduction.

The independent reader limits original bytes to 128 MiB and expanded CSV bytes to 512 MiB; existing source and matcher limits also apply. The execution wrapper protects source, request and declaration files from overwrite and writes the aggregate output atomically. A completed query exits 0; invalid input or incomplete execution exits 2. Execution remains sequential and retains detailed results in memory; this is not a production-scale throughput benchmark.

## Validation and remaining work

Fifteen tests check the independent audit, source/claim tampering, full-file and gzip identity, declaration/hash/coverage gates, actual synthetic Rust/SQL execution, aggregate failure behavior, CLI protection and committed demo provenance. CI checks synthetic execution and the report's hashes without downloading real source files.

This completes the first reviewed mixed interval/measurement query on a real-source demo stratum. Clinical mapping and the scientific question still require review. The non-invasive and alternate arterial strata remain unexecuted; they should be evaluated separately under their own explicit declarations before any sensitivity comparison. The full MIMIC study, historical availability, clinical validation, causal analysis and full mixed OWL reasoning remain outside this result.
