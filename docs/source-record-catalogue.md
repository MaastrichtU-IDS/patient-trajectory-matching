# Source-verified record catalogues

Status: executable `source-record-catalogue-1.0`, with synthetic mapped-query verification and a public MIMIC-IV demo 2.2 catalogue report. Clinical mapping decisions and live pressure-service integration remain pending.

The reviewed mapping compiler checks supplied documents. This adapter additionally verifies their source classes against pinned inputevents, icustays and d_items CSV bytes. Before mapped execution, it reproduces every supplied interval claim and its recorded clock origin using the existing source importer.

## Operations

| Operation | Checks | Output |
|---|---|---|
| `build(folder, request)` | Three source hashes, admission, dictionary identity, input-table linkage, selected labels/classes and row accounting | Catalogue and aggregate source evidence |
| `audit_store(folder, request, catalogue, store)` | Fresh source read; exact catalogue; recorded-store profile/dataset; every supplied claim and clock origin | Store-bound source audit |
| `execute(...)` | Source audit followed by reviewed mapping execution with existing source policies | Source audit, mapping evidence and mixed-query result |

The [closed request schema](../schemas/source-record-catalogue.schema.json) requires a dataset label, release, 1–16 positive numeric item codes and three file SHA-256 hashes. Existing staging limits apply: 64 MiB compressed and expanded per file and 100,000 rows per table. This is a bounded loader.

Catalogue entries contain the exact dictionary label and the importer's `mimic-record-item/<itemid>` class. The dataset identifier uses the same canonical namespace as imported stores. Dictionary evidence preserves the dictionary row, record number and file/CSV/row hashes. Matching supplied hashes and a release label do not authenticate a publisher. No target terminology or acceptance decision is generated.

The store audit admits bounded subsets: every supplied claim must reproduce, but omitted records do not fail the audit. `complete_source_coverage_verified` therefore remains false. Unknown claims, altered timestamps, classes, source hashes or clock origins fail. The origin is the earliest admitted start for the patient before the item filter, as in the importer; clock policy and origin evidence are compared too.

Reconstruction shares the importer's admission and description implementation. It verifies correspondence to pinned transformations, not their independent clinical correctness. The synthetic verifier separately compares mapped bindings with the existing literal-source SQL oracle, which shares admission but independently evaluates temporal/scalar joins.

## Public-demo evidence

The [aggregate report](../verification/source-record-catalogue-demo-report.json) was generated from the pinned open-access demo:

| Source fact | Result |
|---|---:|
| Input rows accounted for | 20,404 |
| Selected item | 221906 — Norepinephrine |
| Selected staged segments | 944 |
| Selected unsupported rows | 3 |
| Dictionary rows | 4,014 |
| Source claims accepted by this operation | 0 |
| Clinical terminology targets accepted | 0 |

The extracted [catalogue](../data/terminology/mimic-demo-2.2-pending/catalogue.json) is the source document for the terminology review handoff. Its companion README now links the complete proposed norepinephrine pack and empty review journal. The report contains dictionary metadata and aggregate counts, with no patient/stay identifiers, clinical row contents or bindings. This run does not execute mapped queries on public-demo records or replace existing source-fidelity reviews.

```bash
python -m patterns.source_record_catalogue \
  --input-dir /path/to/mimic-iv-demo/icu \
  --request data/clinical-source-catalogue-request.json \
  --output /tmp/source-catalogue-report.json
```

Save the output's `catalogue` object as `catalogue.json` when assembling a mapping pack; `context.catalogue_sha256` binds it. The CLI atomically writes one report, rejects overwriting source/request inputs, and exits 2 on invalid input while preserving a previous report. Check exit status before consuming output.

## Synthetic verification

```bash
python -m patterns.verify_source_record_catalogue
python -m unittest patterns.test_source_record_catalogue
```

The [synthetic pack](../examples/source-record-catalogue/) supplies an authored mapping from code 1000 to an authored record-query family and a separately authored source review bound to that policy. Runtime never regenerates or rebinds review decisions. The [report](../verification/source-record-catalogue-synthetic-report.json) reproduces three matching synthetic patients across a five-stay source roster. Every mapped binding, including optional follow-up and numeric changes, agrees with the literal-source SQL control. Actual Rust support and PRO witnesses are required.

Eighteen tests cover file pins, dictionary identity, changed catalogues, claim/clock tampering, policy separation, pending mapping blockers, compression identity, CLI behavior and report provenance. CI executes synthetic queries and checks the public report's pins/artifact hashes without downloading patient records.

## Remaining work

Supply reviewed `terminology.json`, `mappings.json` and `review.json` alongside the catalogue under [the mapping contract](reviewed-record-mappings.md). Dictionary labels and units do not justify ingredient identity, dose equivalence, actual administration or course initiation. The three pressure items in the [worksheet](../data/clinical-terminology-review.json) remain separate and need a measurement-mapping extension.

The audit does not accept claims, audit measurement values, establish inter-store alignment, verify physical elapsed time or reconstruct historical availability. Existing source policies, measurement checks and alignment rules govern execution. The compiler's general `catalogue_source_fidelity_verified` flag stays false because it is source-agnostic; the separate source audit states precisely what this adapter checked.

Each audit rereads/stages the bounded snapshot. Live integration should reuse that work at the reviewed-session boundary, with strict invalidation and source-policy binding, then compare complete cohort/anchor SQL evidence. This increment adds no latency claim or live terminology selector.
