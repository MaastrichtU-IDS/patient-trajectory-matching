# Source imports, explicit review, and SQL-checked mixed queries

`source-mixed-query-1.0` connects the [pending interval importer](mimic-claim-import.md) and [measurement importer](measurement-claims.md) to the [mixed interval/point query](mixed-record-query.md). Every requested ICU stay remains in the result, including empty and blocked stays. Every input row remains in its import ledger.

The executable scope is exact recorded source labels, literal item codes, and explicit record selection within an ICU stay. It supports a reproducible technical comparison; it does not establish a clinical phenotype, treatment occurrence, improvement, source availability history, or causality. No real patient records are included in this increment.

## Two-step use

Install the pinned semantic dependencies as described in [validation](validation.md#setup), then prepare claims without accepting any:

```sh
python -m patterns.source_mixed_query
```

The default output is `verification/source-mixed-query-run/result.json`. It contains both complete import results and `review_template`. The template has an empty decision list for every available store, empty clock-alignment bindings, and `null` for a policy or alignment that has no corresponding store. Preparation never accepts a record or declares calendar compatibility.

To execute the explicitly constructed synthetic review:

```sh
python -m patterns.source_mixed_query \
  --review examples/source-mixed-query/synthetic-review.json
python -m patterns.test_source_mixed_query
python -m patterns.verify_source_mixed_query
```

For other locally supplied sources, use `--input-dir`, `--request`, `--review`, and `--output`. Extract `review_template` into a separate JSON file, inspect the claims and source provenance, and provide explicit acceptance/rejection/withdrawal decisions and any justified patient-calendar alignment. An acceptance decision selects a **record description** for computation; it is not clinical approval. Nothing in the tool supplies those decisions for real data.

`run(folder, request, review=None)` is the Python entry point. Without a review it returns preparation. With a review it reimports source files and checks the complete preparation hash before evaluation. The internal `evaluate` handoff operates on an in-memory `prepare` result; it is not an ingestion interface for externally edited preparation JSON.

The request is limited to 256 KiB and a review file to 4 MiB. The CLI protects source/request/review paths from overwrite and atomically replaces output. Invalid inputs preserve an existing output; blocked execution writes diagnostics and exits 2. Preparation and completed comparisons exit 0. Per-backend timeout defaults to 20 seconds; the Python API accepts a positive timeout up to 60 seconds.

## Request and review contracts

The [request schema](../schemas/source-mixed-query.schema.json) wraps the existing interval-import request, measurement-import request, and mixed query. The importers must agree on the dataset label, requested ICU-stay roster, and exact shared `icustays` and `d_items` manifests. The measurement import must cover every baseline/follow-up item selector.

This source path restricts treatment selection to one imported literal item class, `https://example.org/trajectory/mimic-record-item/<itemid>`. Its semantic policy is the importer's source-item policy, with no additional classification rules. The mixed query itself still supports its broader restricted class-policy route; that capability is not claimed for this raw-row SQL reference. An item code is not silently renamed to norepinephrine, MAP, or another clinical concept.

The review must list every prepared `(patient_id, episode_id)` exactly once in the prepared order. It binds the preparation context, which includes both importer contexts, source manifests, query, and implementation hashes. Each decision additionally binds the whole store, claim contents and semantic policy. Each alignment binds both stores. Source, query or implementation changes require a fresh review context. These hashes detect stale inputs; they are not signatures or independent evidence of reviewer identity or clinical approval.

Alignment stays a caller declaration under the mixed profile's patient-clock contract. Equal clock IDs alone do not align tables. No arbitrary clock, origin, or empty store is invented when one side has no admitted selected records. In that case there is no possible selected baseline–treatment pair, and the episode is still retained.

## Independent relational comparison

The [SQLite reference](../patterns/source_mixed_reference.py) independently rereads the same hashed source CSV files after import. It uses raw row labels and values, an independent integer calendar calculation, SQL joins/windows, and exact Decimal scalar functions. It does not call the STN, graph projection, mixed query-edge compiler or clock normalizer.

The shared boundary is deliberate: source admission, duplicate handling and final hash-bound claim selection are inherited from the importers and policy validator. The comparison therefore checks the downstream projection/query against selected admitted raw rows; it is **not an independent validation of admission policy or source truth**. A changed source file between import and reference reading invalidates the run.

SQL selects every eligible named baseline–treatment pair and left-joins distinct follow-up measurements. This keeps an eligible pair when no follow-up exists. It compares complete binding identities and numeric changes, not just patient counts:

| Compared field | Meaning |
|---|---|
| Patient and ICU stay | Both sides must share the same scope |
| Treatment event ID and baseline ID | Exact named eligible pair |
| Follow-up ID, or null | Distinct named follow-up, or no selected follow-up in the window |
| Delta and unit, or null | Exact difference only for identical item and unit strings |
| Patient sets and follow-up status | Must agree with the binding-level results |

The source profile fixes labels exactly, so every comparable candidate must be `CERTAIN` or `IMPOSSIBLE`. A `POSSIBLE` or `INCOMPARABLE` candidate blocks this exact comparison. The mixed profile's bounded uncertainty remains covered by its separate finite-world oracle; SQL comparison does not extend that guarantee to uncertain source times.

## Completion and failure semantics

| Episode status | Meaning |
|---|---|
| `VERIFIED_RECORD_MATCH` | Exact selected binding(s), with SQL agreement |
| `VERIFIED_NO_SELECTED_RECORD_MATCH` | No eligible selected binding, with SQL agreement |
| `BLOCKED_IMPORT` | A source importer could not represent the complete selected stay |
| `BLOCKED_MIXED_QUERY` | Semantic, projection, temporal, or mixed resource gate failed |
| `BLOCKED_COMPARISON` | Exact comparison is unavailable, including unresolved alignment |
| `BLOCKED_REFERENCE_DISAGREEMENT` | Binding, value, or patient-set mismatch |

One blocked stay makes the overall status `BLOCKED_INCOMPLETE_SOURCE_QUERY`: authoritative patient lists and eligible stays are `null`. Completed episode diagnostics remain available. No partial list is promoted to a complete cohort. Inherited importer bounds (32 claims per store/stay, 256 stays, 100,000 source rows, 64 MiB per raw/expanded table) and mixed bounds (64 variables, 128 baseline tests, 256 conservative follow-up tests per execution) remain in force; no records are truncated to fit.

`COMPLETED_VERIFIED_SOURCE_QUERY` is complete only over explicitly selected admitted records in the requested source scope. Import ledgers still show duplicates, invalid/unsupported records, scope exclusions, and pending claims. Applying the untouched pending review yields zero selected claims and zero eligible pairs; it does not show that the patients lacked treatment or an abnormal measurement. `clinical_knowledge_status` remains `UNKNOWN` in every outcome.

## Reproduced synthetic example

The [CSV fixture](../examples/source-mixed-query/) contains five inputevent rows and nine measurement rows. Admission retains three interval and six measurement claims; exact duplicates and unsupported rows remain accounted for. The committed review explicitly selects those fabricated claims and declares their constructed shared patient calendars.

| Synthetic patient / stay | Baseline | Follow-up | Eligibility |
|---|---|---|---|
| 1 / 100 | 58 at 09:40 | 68 at 10:30; 72 at 11:30 | Eligible; differences +10, +14 |
| 1 / 101 | None selected | None selected | No selected record match; stay retained |
| 2 / 200 | 60 at 09:45 | None selected | Eligible; clinical response unknown |
| 3 / 300 | 62 at 09:50 | 55 at 10:30 | Eligible; difference −7 |
| 4 / 400 | None selected | None selected | No selected record match; stay retained |

All three interval records span 10:00–10:45. The illustrative baseline threshold is below 65 mmHg within 30 minutes strictly before the segment start; follow-up extends through 120 minutes from start. These are synthetic pressure records and literal input-item records. No validated MAP/norepinephrine mapping or public-demo mixed evaluation is claimed.

The [reproducible report](../verification/source-mixed-query-report.json) pins source fixtures, implementation, review and comparison results. Tests include deliberate projection corruption and reference disagreement, which must suppress the whole authoritative cohort. The next study gate is a reviewed source/item-selection and calendar-compatibility plan, followed by a bounded real-source run with aggregate-only published evidence and explicit coverage.
