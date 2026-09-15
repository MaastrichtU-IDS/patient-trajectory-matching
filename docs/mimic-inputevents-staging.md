# MIMIC-IV inputevents admission and reconciliation

**Implemented profile:** `mimic-inputevents-staging-1.0`, standard-library Python, 28 tests. This stages recorded input components from the MIMIC-IV demo 2.2 CSV schema. It does not emit a temporal graph or a cohort answer. It is the first bounded step toward the ICU interval-exposure extension described in [specification 2.1](../addenda/specification-2.1.md), separate from that specification's hospital point-event profile.

## Run the synthetic example

```sh
python -m patterns.mimic_inputevents
python -m patterns.test_mimic_inputevents
```

The six constructed rows in `examples/mimic-inputevents/` produce two staged segments, two unsupported rows, one invalid row, and one duplicate. The default output is `verification/mimic-inputevents-run/result.json`, containing `summary`, `reconciliation`, and `segments`. Exit 0 means staging completed; the summary always says `STAGED_NOT_MATCHER_READY`, `matcher_ready: false`, and `matching: null`.

For supplied files, pass their directory and an explicit dataset/shift namespace:

```sh
python -m patterns.mimic_inputevents \
  --input-dir data/local-mimic/icu \
  --dataset-id mimic-iv-demo-2.2 \
  --output verification/mimic-inputevents-run/result.json
```

The directory must contain exactly one `.csv` or `.csv.gz` for each of `inputevents`, `icustays`, and `d_items`. Required columns are pinned in `patterns/mimic_inputevents.py`; column order may vary, but missing, duplicate, or extra columns fail admission. Source spelling is retained. Each table is bounded to 100,000 records and 64 MiB both compressed and expanded. The loader and detailed result are in memory; it is not a full-release streaming loader. No network access or optional Rust dependency is needed.

A file, encoding, CSV structure, limit, or dimension-integrity error exits 2 and leaves an existing result untouched. Such a failure makes no complete-reconciliation claim. The successful bundle is replaced as one file. Consumers must check the exit status before reading an older result.

## Input checks and row outcomes

`icustays` supplies the subject/admission/stay association. Duplicate dimension keys, malformed dimension identifiers, and contradictory admission-to-patient assignments fail the whole run. `d_items` supplies item lookup and table membership. Stay dates, labels, dose units, other clinical fields, and administration history are not semantically validated by this profile.

Each parseable input record receives one outcome:

| Outcome | Rule |
|---|---|
| `STAGED_SEGMENT` | Joined identities agree; item belongs to inputevents; supported category/status; finite positive amount and rate with nonempty units; valid increasing recorded endpoints; duration is not exactly one minute |
| `UNSUPPORTED_ROW` | Structurally valid, but outside one or more of those admission policies |
| `INVALID_ROW` | Invalid identifier, missing or mismatched dimension reference, invalid required timestamp, nonpositive recorded interval, or malformed/nonfinite quantity |
| `DUPLICATE_ROW` | Every parsed source field equals an earlier row; retains a reference to that first record and creates no second segment |

Exact duplicates are classified first, including duplicates of rejected records. For other records, all encountered reasons are retained; invalid reasons take outcome precedence over unsupported reasons. Thus reason counts overlap and do not sum to the input row count. Completeness means every input CSV record received an outcome, not that every real clinical event or historical source revision is present.

The admitted categories are exactly `Continuous IV` and `Continuous Med`. End-status spellings are exactly `FinishedRunning`, `ChangeDose/Rate`, `Paused`, and `Stopped`, observed in demo 2.2. These are recorded segment end statuses, not instructions to create extra administration or stop processes. Unknown statuses, bolus, push, and non-IV categories remain unsupported. Future spellings require a profile revision.

A one-minute interval remains unsupported even in an otherwise admitted continuous category: this is a conservative ambiguity policy, not proof that every such row is a bolus. Shorter intervals are not automatically classified as boluses either. All staged endpoint precision remains unverified.

The source documentation explains that components can share an order, changes can produce new segments, and boluses can carry an artificial one-minute interval. This adapter keeps component rows and rate segments separate; neither `orderid` nor `linkorderid` is a unique event identifier. It does not merge orders, link EMAR records, or infer drug classes from labels. [Official inputevents documentation](https://mimic.mit.edu/docs/iv/modules/icu/inputevents.html).

## Provenance and time

Record IDs bind the explicit dataset namespace, exact CSV digest, and one-based data-record number. A quoted multiline CSV record is one record, not several source lines. Any CSV change changes that file's record IDs; this is snapshot identity, not a revision-matching algorithm. Exact source-file and decompressed-CSV SHA-256 digests are both retained; recompressing unchanged CSV preserves record IDs but changes file provenance and context.

Every reconciled row retains all raw column values and a canonical row digest: UTF-8 JSON, sorted keys, compact separators, unescaped Unicode. Staged segments additionally retain item and stay row locators/digests, item dictionary values, raw amount/rate units, order references, and component descriptions. Source files remain necessary to inspect the original dimension rows. The run context binds all three file descriptors and the adapter implementation digest. Ordinary staging validates the declared schema and computes hashes; only the separate demo verifier below checks release authenticity against published hashes.

Timestamp envelopes preserve raw values, calendar, lexical resolution, field role, and unresolved normalization. The admitted spelling is `YYYY-MM-DD HH:MM:SS`, a valid naive Gregorian datetime. Missing start or end is invalid; missing storetime is retained as missing. Supplied malformed storetime is invalid. No endpoint is clipped to ICU admission/discharge or inferred from an order neighbour. One-second lexical resolution does not establish one-second clinical precision.

The clock descriptor binds dataset and patient, is shared across that patient's stays, and has no offset or numeric origin. Identical-looking dates in different patients do not identify a common timeline. No UTC suffix, guessed timezone, historical DST rule, or artificial epoch is applied. Storetime is retained as a recording/validation value even when earlier than occurrence; it is never promoted to verified availability. This follows the separation between occurrence, recording, and shifted patient dates described in [MIMIC core concepts](https://mimic.mit.edu/docs/iv/about/concepts.html).

## Verified public-demo reconciliation

The [public MIMIC-IV demo 2.2](https://physionet.org/content/mimic-iv-demo/2.2/) was run locally. All three original compressed file hashes match the published `SHA256SUMS.txt`. The repository includes only a [hash/row-count pin](../data/mimic-inputevents-demo-pin.json), an [aggregate result](../verification/mimic-demo-inputevents-report.json), and synthetic fixtures. No patient rows or detailed demo results are redistributed.

| Quantity | Count |
|---|---:|
| Input records | 20,404 |
| ICU stay lookup records | 140 |
| Item dictionary records | 4,014 |
| Staged component segments | 10,980 |
| Unsupported input records | 9,424 |
| Invalid input records under these checks | 0 |
| Exact duplicate input records | 0 |

The counts describe this admission policy, not a medication cohort or clinically validated event set. Full MIMIC has not been analyzed.

To reproduce, obtain the three original `icu/*.csv.gz` files from the public demo's Files section, retaining its separate source license, and place them in an ignored local directory:

```sh
python -m patterns.verify_mimic_demo --input-dir data/local-mimic/icu
```

This verifies the pinned published compressed hashes and row counts, then writes only the aggregate report. It does not download files. Detailed staging output and local source data are ignored by git. CI runs synthetic cases and checks the committed report against the source pin and current implementation digest; it does not download or rerun patient data. A changed adapter requires regenerating and reviewing the demo report.

## Next handoff

All five blockers are explicit in every successful result:

| Blocker | Required follow-up |
|---|---|
| `PATIENT_LOCAL_CLOCK_BRIDGE_REQUIRED` | Add a versioned local-coordinate profile, explicit origin/anchor policy, and RDF round trip; preserve patient isolation and joint time constraints |
| `CLINICAL_MAPPING_REVIEW_REQUIRED` | Review item/category/component/status mappings and units before asserting a SULO process class or performed administration |
| `OCCURRENCE_PRECISION_POLICY_REQUIRED` | Establish justified endpoint bounds, including bolus, rounding and documentation uncertainty; do not turn lexical timestamps into exact clinical events |
| `SOURCE_AVAILABILITY_UNVERIFIED` | Supply trustworthy availability semantics or define a separately named retrospective-only contract |
| `SOURCE_REVISION_HISTORY_UNAVAILABLE` | Establish revision/withdrawal coverage before enabling source-as-known replay |

The next bounded implementation should be the patient-local coordinate bridge with synthetic differential and RDF checks. It must not merely append `Z` to satisfy the current offset-datetime profile. A later retrospective demonstration may be possible without reconstructing source-as-known history, but it needs its own honest contract. These staging rows cannot currently enter [joint evidence selection](joint-evidence-selection.md), whose clock and availability fields require explicit offset datetimes. No SULO properties, reasoning rules, matcher semantics, or original 18 formal checks change here.
