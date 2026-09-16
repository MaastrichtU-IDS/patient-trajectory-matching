# Reviewed measurement selectors

Status: executable `reviewed-measurement-mappings-1.0`, with authored synthetic mappings and actual Rust/literal/SQL verification. Clinical pressure mappings and live pressure-service integration remain pending.

A reviewed measurement class now selects source item codes before temporal execution. Each supported item runs as a separate baseline/follow-up stratum. This allows a terminology category to find eligible records while preserving source-item and literal-unit distinctions.

## Contract and semantics

Reuse the four [reviewed mapping documents](reviewed-record-mappings.md): catalogue, terminology snapshot, proposals and review journal. The catalogue's dataset must match the measurement store. Source classes use the application namespace `https://example.org/trajectory/measurement-record-item/` followed by the exact item code. Target classes remain distinct application record-query classes; terminology concept IRIs are metadata.

The [selector schema](../schemas/reviewed-measurement-selector.schema.json) adds:

| Field | Meaning |
|---|---|
| `mapping_context_id` | Exact compiled mapping/review/implementation context |
| `class_iri` | Target application record-query class |
| `source_item_ids` | Explicit bounded source-item scope |
| `unit_lexical` | Exact recorded unit spelling for both baseline and follow-up |

The supplied mixed query must name exactly that item set and unit on both sides. The adapter rejects a mismatch rather than silently changing the caller's scope. Review changes require a new selector context; source policies are passed unchanged. Measurement policies continue to select record descriptions under their existing empty semantic policy. They do not themselves accept terminology mappings. The outer result binds both policy inputs, mapping evidence, selector, query, stores, alignment and implementation hashes.

The compiled one-way rules are checked through Rust and the finite reference checker using **synthetic catalogue witnesses**: one witness has each declared source-item class. Target membership determines which item codes support the selector. These witnesses are not patient measurements or processes, and they are never added to the source claim graph. This is checked catalogue-rule selection followed by literal record selection; it does not claim OWL classification of a patient measurement graph or a full terminology hierarchy.

Each requested item has an outcome and mapping-decision evidence. A target not entailed by the admitted rule module excludes that item from this query; it does not establish clinical absence or an OWL negative assertion. Unsupported items remain visible in the selection plan.

## Separate strata and evidence

The adapter creates a literal mixed query per supported item, with the same item and unit on both sides. The original stores and all their selected temporal constraints remain intact. Numeric thresholds, temporal windows, source acceptance, explicit clock alignment and PRO treatment witnesses use the existing engine.

Baseline/follow-up pairs never cross item boundaries. A shared target concept is not a reason to pool observations or convert units. The top-level patient identifiers are explicitly the union of memberships of separate item queries; the individual queries, proofs, source records and context IDs remain available under `strata`.

Follow-up remains optional for eligibility. Possible and certain answers keep the existing fixed-binding temporal semantics. Missing alignment produces the existing incomparable bindings; it cannot become a possible match by terminology expansion.

| Outcome | Behaviour |
|---|---|
| `COMPLETED_REVIEWED_MEASUREMENT_QUERY` | Every supported stratum completed; combined memberships available |
| `BLOCKED_MAPPING_REVIEW` | At least one catalogue mapping pending or withdrawn; no backend query |
| `BLOCKED_MEASUREMENT_MAPPING_SEMANTICS` | Rust/reference gate unresolved; no literal-item fallback |
| `NO_SUPPORTED_MEASUREMENT_ITEMS` | Nothing in the requested scope supports the selector; no completed cohort answer or patient lists |
| `BLOCKED_INCOMPLETE_MEASUREMENT_STRATA` | At least one stratum failed; combined patient lists withheld, diagnostic stratum results retained |
| Invalid input / stale context | Profile error; no new valid result |

Consumers must check the outer status before using memberships. The CLI exits 0 only for completed execution. Valid blocked output replaces the previous file with an explicit blocked result; invalid input exits 2 and preserves an older output. Input paths cannot be overwritten.

## Run and verify

```bash
python -m patterns.reviewed_measurement_mappings --output /tmp/mapped-measurements.json
python -m patterns.verify_reviewed_measurement_mappings
python -m unittest patterns.test_reviewed_measurement_mappings
```

The CLI defaults to [the authored example pack](../examples/reviewed-measurement-mappings/). It accepts the four mapping files, `--selector`, and the seven existing mixed-query files: interval store/policy, treatment semantic policy, measurement store/policy, alignment and query. The Python `execute(...)` function accepts the same inputs with an optional per-backend timeout.

In the example, `pressure_a` and `pressure_b` map to an authored family; `pressure_c` maps elsewhere. P1 qualifies in item A and P2 in item B; P3's item is excluded by the selector. P1 also has an item B follow-up, which cannot join its item A baseline. P2 stays eligible without follow-up.

The [report](../verification/reviewed-measurement-mappings-report.json) reproduces every stratum against an unchanged literal-item execution and the exact SQL reference. The SQL check shares explicit source selection and consumes exact labels from the authored fixture; it does not independently verify CSV extraction. Catalogue support and treatment support use actual Rust. Eighteen tests cover review lifecycle, stale selectors, scope/unit mismatches, source policies, backend blocking, partial-stratum failure, uncertainty, alignment, CLI behaviour and reproducible evidence. The test suite also checks that clinical candidates remain unaccepted.

## Limits and next steps

This profile supports reviewed implications for whole source-item classes. A mapping that depends on a per-row method, device, route or other qualifier needs a separate qualified-mapping extension; do not encode a conditional claim as an unconditional item implication. Source-catalogue fidelity, clinical correctness and terminology publisher identity are not established by this adapter. The [clinical candidate dossier](clinical-terminology-candidates.md) remains a review input: no LOINC release is pinned for measurement execution and no pressure mapping is accepted.

The existing 16-item mapping limit bounds the number of strata. Each stratum retains the existing mixed-query variable/candidate limits and backend timeout. Queries rerun the current engine per stratum, so this increment makes no latency improvement claim. [Source audit for measurement catalogues](measurement-source-catalogue.md) now executes as a separate wrapper. [Prepared measurement sessions](prepared-measurement-session.md) now provide bounded reuse with source/implementation checks. Clinical acceptance and pressure-batch integration precede live UI integration. No SULO axioms, measurement-claim graph encoding, unit conversions or existing live query behaviour change.
