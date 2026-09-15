# Clinical source candidates and coverage preflight

The [source mixed-query pipeline](source-mixed-query.md) is executable on synthetic fixtures. This increment checks the proposed clinical source items against the pinned MIMIC-IV demo 2.2 and measures coverage and capacity before any mixed-query evaluation. It creates no claims, accepts no records, declares no clock alignment, and reports no clinical cohort.

The [candidate plan](../data/clinical-candidate-plan.json) is **proposed for review**, with `clinical_review: null`. It is a separate technical pilot, not a replacement for the existing [D01 antibiotic/renal study](../data/full-mimic-study-plan-2.1.json).

## Candidate item mapping

The following identities come from the checksum-pinned demo `d_items` table. Dictionary identity supports a literal source-item selector; it does not establish clinical equivalence across labels or methods.

| Item | Dictionary label | Table | Proposed use |
|---|---|---|---|
| 221906 | Norepinephrine | inputevents | Recorded treatment-segment anchor |
| 220052 | Arterial Blood Pressure mean | chartevents | Primary measurement stratum, pending review |
| 225312 | ART BP Mean | chartevents | Separate alternate arterial-label stratum, pending review |
| 220181 | Non Invasive Blood Pressure mean | chartevents | Separate non-invasive sensitivity stratum, pending review |

All three measurement dictionary entries specify `mmHg`. The treatment dictionary unit is `mg`; it must not be confused with an infusion rate or used to infer dose equivalence. Keep measurement items separate: no OWL equivalence, automatic pooling, unit conversion, or cross-item numeric change is introduced.

The proposed illustrative query remains a recorded value below 65 mmHg, strictly before and within 30 calendar minutes of a norepinephrine segment start, followed by inspection of records through 120 calendar minutes from that start. Follow-up presence or direction of change never determines eligibility. Every eligible named baseline–segment pair is retained; there is no silent “best baseline” choice. A segment start need not be treatment-course initiation. These parameters require review before use as a scientific phenotype.

The [inputevents documentation](https://mimic.mit.edu/docs/iv/modules/icu/inputevents.html) describes source segments and status fields; their clinical interpretation must remain distinct from admission by the current technical profile. The [chartevents documentation](https://mimic.mit.edu/docs/iv/modules/icu/chartevents.html) identifies chart time as an observation-time proxy and store time as entry/validation time. Neither establishes historical availability. Numeric text must agree with `valuenum`; the existing importer still requires an explicit zero warning and an admissible literal unit.

The [demo release documentation](https://physionet.org/content/mimic-iv-demo/2.2/) describes consistent date shifting. This supports investigating a same-release, same-patient calendar alignment. The preflight does not create that declaration, assume UTC, or verify physical elapsed time. Cross-patient dates remain unsuitable for temporal joins.

## What was actually scanned

The [published checksums](https://physionet.org/files/mimic-iv-demo/2.2/SHA256SUMS.txt) were checked for all four original compressed files. The [source pin](../data/clinical-source-demo-pin.json) records their hashes; the [aggregate report](../verification/clinical-source-preflight-demo-report.json) additionally binds expanded CSV hashes, implementation and request.

The scan covers all **20,404 inputevents rows and 668,862 chartevents rows**, plus the 100-patient, 140-stay roster. The coverage request inventories all four candidate codes; it does not pool them into a clinical variable or select patients based on outcomes.

| Source item | Candidate rows | Admitted records | Admitted stays | Maximum records in one stay |
|---|---:|---:|---:|---:|
| Norepinephrine 221906 | 947 | 944 | 33 | 174 |
| Arterial mean 220052 | 5,560 | 5,543 | 65 | 447 |
| Non-invasive mean 220181 | 8,342 | 8,318 | 138 | 346 |
| ART mean 225312 | 488 | 488 | 10 | 125 |

Three treatment rows fail the existing one-minute-segment ambiguity rule. Seventeen arterial and 24 non-invasive readings are unsupported because their warning flag is set. All candidate measurement rows have the literal unit `mmHg`. These are technical admission counts, not independently adjudicated clinical facts. The report retains each item separately and reconciles all other source rows as outside the requested item scope. It does not validate unrelated chartevents concepts.

There are 33 stays with both admitted treatment and measurement records, 139 with at least one admitted candidate measurement, and one with neither admitted record type. Stay overlap does not establish temporal baseline eligibility or follow-up availability.

## Capacity findings and next implementation

The unchanged measurement importer admits at most 100,000 rows per supplied table, before filtering. The original demo `chartevents` file therefore cannot be passed directly to it. Its byte limits are satisfied; the row count is the immediate file-level blocker.

With all inventoried candidate records retained per stay, 99 stays exceed the 32-measurement-claim limit; nine exceed 32 interval claims and eleven exceed the downstream 30-event limit. All 33 two-sided stays exceed the conservative baseline/follow-up candidate-count bounds. Counts across the three measurement items describe inventory pressure, not a recommendation to combine those strata.

The count screen uses `i*m` potential baseline tests and `i*m*(m-1)` potential follow-up tests, for `i` admitted intervals and `m` admitted measurements. It deliberately ignores scalar, item-stratum and time-window reductions. Exceeding this upper bound does not prove that an optimized exact query is impossible; staying within it does not prove schema/semantic validity, clock comparability, complete source coverage, or query readiness.

The next implementation should provide an indexed, provenance-preserving source selection layer:

1. Stream and index the complete candidate source records, retaining original file hashes, row numbers, item identities and admission outcomes.
2. For each declared treatment-segment anchor and separately chosen measurement stratum, retrieve every admitted point in the union of the baseline and follow-up windows. Use exact source-label predicates whose equivalence to the query windows is tested independently.
3. Preserve the full requested-stay and candidate-anchor roster. Record every exclusion, empty window and blocked anchor; never discard an anchor because follow-up is absent or lower.
4. Bind bounded claim batches and explicit review to the original source records and selection request. A rewritten CSV with fresh row numbers alone is insufficient provenance.
5. If a complete anchor batch still exceeds a limit, report it as blocked. Do not truncate to the first 32 records or silently select a convenient patient subset. Suppress authoritative complete-cohort output when any required anchor is unresolved.
6. Compare complete binding identities with an independent relational calculation before publishing aggregate technical results.

This step addresses the observed source size and candidate growth. Raising existing limits alone would leave the combinatorial workload and source-accounting obligations unresolved. No exact query result, runtime speedup, clinical outcome, or causal effect is claimed by the current preflight.

## Reproduce and inspect

Install the dependencies in [validation](validation.md#setup). The synthetic scan uses the existing fabricated source fixture:

```sh
python -m patterns.clinical_source_preflight
python -m patterns.test_clinical_source_preflight
```

For the original, locally supplied demo files:

```sh
python -m patterns.clinical_source_preflight \
  --input-dir /path/to/original/demo/icu \
  --request examples/clinical-source-preflight/demo-request.json
python -m patterns.verify_clinical_source_preflight \
  --input-dir /path/to/original/demo/icu
```

The main CLI writes an aggregate result to `verification/clinical-source-preflight-run/result.json` by default; the verifier reproduces the committed aggregate report after checking the pin. No patient-level source files or identifiers are published. CI runs synthetic tests and verifies report hashes/arithmetic; it does not download patient data.

The streaming scan has explicit limits: 128 MiB compressed/raw chartevents, 512 MiB expanded, 1 MiB per physical line, two million CSV records, 100,000 requested-item rows, and 256 stays. Inputevents/dimension admission retains its existing bounds. CSV multiline records and gzip CRC/EOF are checked; a hash recheck detects source changes during the chart scan. A limit, malformed/truncated file, or source change invalidates the scan and preserves prior CLI output. It never labels a truncated prefix complete.

## Decisions for clinical review

Review the item identities and proposed separate strata, treatment-segment anchor meaning, numeric/warning admission rules, illustrative windows and threshold, same-patient calendar evidence, and source completeness. Review must also define the scientific question and appropriate handling of concurrent treatments, repeats and overlapping segments. The current prototype has no age-based eligibility or clinically adjudicated treatment-course reconstruction.

These decisions can be made against the concrete [candidate plan](../data/clinical-candidate-plan.json) and measured [coverage report](../verification/clinical-source-preflight-demo-report.json). Technical source selection can then be implemented and tested without representing unreviewed clinical judgments as approved mappings.
