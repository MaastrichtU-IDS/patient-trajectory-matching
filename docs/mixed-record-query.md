# Mixed recorded treatment and measurement queries

`mixed-record-trajectory-1.0` combines explicitly selected treatment **interval records** and measurement **point records**. It first identifies baseline–treatment eligibility, then inspects follow-up measurements. Follow-up presence, direction of change and improvement are never conditions for baseline eligibility.

This is a bounded retrospective record query. Treatment class support passes through the existing Rust/finite-model gate. Measurement item/unit predicates and mixed temporal geometry are explicit application operations; the result does not claim a full mixed OWL entailment procedure or clinical validation.

## Run the synthetic example

```sh
python -m patterns.mixed_record_query
python -m patterns.test_mixed_record_query
python -m patterns.verify_mixed_record_query
```

The default [fixtures](../examples/mixed-record-query/) use explicit synthetic acceptance policies, a constructed treatment-item classification rule and a declared shared patient calendar. The query asks for a pressure reading below 65 mmHg, strictly before the recorded treatment start and at most 30 calendar minutes earlier. It then inspects matching point records from treatment start through 120 calendar minutes later, inclusive. This threshold is an illustrative query parameter, not a validated clinical protocol.

All three treatment records span 10:00–10:45 on their respective synthetic patient calendars:

| Patient | Eligible baseline | Follow-up records | Result |
|---|---|---|---|
| P1 | 09:40, 58 mmHg | 10:30, 68; 11:30, 72 | Eligible; recorded differences +10 and +14 mmHg |
| P2 | 09:45, 60 mmHg | None selected | Eligible; no selected follow-up in the window; clinical response unknown |
| P3 | 09:50, 62 mmHg | 10:30, 55 | Eligible; recorded difference −7 mmHg |

P3's later low reading is also examined as a potential baseline and is rejected by the temporal condition. No “first”, “latest”, “best”, or outcome-dependent baseline is silently selected. The profile enumerates all eligible named baseline–treatment pairs. A treatment record's segment start is not necessarily the start of the patient's treatment course.

The [report](../verification/mixed-record-query-report.json) is synthetic only. No public-demo mixed clinical query, clinical phenotype or causal effect has been evaluated.

## Inputs and selection boundary

The CLI accepts seven JSON files, each at most 256 KiB:

| Input | Contract |
|---|---|
| `--interval-store` | `patient-local-record-claim-store-1.0` |
| `--interval-policy` | Explicit hash-bound claim acceptance decisions |
| `--semantic-policy` | Existing restricted class/rule policy for treatment selection |
| `--measurement-store` | `patient-local-measurement-claim-store-1.0` |
| `--measurement-policy` | Measurement acceptance decisions bound to its fixed empty semantic policy |
| `--alignment` | Explicit store-bound patient-clock compatibility declarations |
| `--query` | [Mixed query schema](../schemas/mixed-record-query.schema.json) |

`--output` defaults to `verification/mixed-record-query-run/result.json`. The Python entry point is `patterns.mixed_record_query.execute`, with those seven decoded inputs in that order and an optional backend timeout. Input files are protected from overwrite and output replacement is atomic. Invalid input preserves an earlier output. A blocked execution writes its diagnostic result and returns exit code 2; a completed record query returns 0, including when no eligible selected binding exists.

The query uses [local claim projection](local-claim-projection.md) for treatment-class support and [measurement selection](measurement-claims.md) for scalar/point records. Neither importer’s empty acceptance policy becomes an acceptance decision automatically. Pending or withdrawn measurements cannot supply baseline or follow-up evidence. Conflicting selected records, missing time dependencies, inconsistent selected intervals and unresolved treatment semantics block the authoritative answer. They are not negative cohort results.

The treatment side retains its existing accepted interval graph and PRO patient-role/bearer witnesses. Measurement records remain selected descriptions; no measurement occurrence graph is generated. The new mixed operation relates the selected record contents. It does not union both description graphs into OWL and claim that the temporal result follows from that union.

## Clock alignment and normalization

Matching clock IDs do not establish compatibility across stores. `patient-local-clock-alignment-1.0` binds both complete store hashes and declares specific `(patient_id, interval_clock_id, measurement_clock_id)` pairs, each with the basis `same_dataset_patient_calendar` and an explicit reason. Both stores must declare the same dataset namespace, and each referenced clock must use the existing patient-local policy and belong to the declared patient. Stale hashes, duplicate bindings, unknown clocks, cross-dataset inputs and cross-patient declarations fail validation.

An absent compatibility declaration produces `INCOMPARABLE` for the affected binding. It does not become “before”, “after” or “no matching record”, even when clock identifiers happen to be identical. Alignment is a caller declaration, not independent proof that the source systems used the same calendar or date shift.

All retained local labels are converted using exact integer arithmetic to a shared computational reference, `0001-01-01T00:00:00`. This is an internal coordinate reference, not a clinical date or timezone assertion. Raw labels, original clock origins, source contexts and the alignment declaration remain in the evidence. Independently rebasing either store's origin leaves the mixed temporal network and answers unchanged. Shifting the shared synthetic calendar preserves the answers.

Only same-patient, same-episode bindings are considered. Identically named variables in the two stores receive separate internal namespaces. Source interval constraints retain their full correlations, and every selected variable bound is retained. Intervals receive the existing strict proper-duration condition; measurement points receive no invented duration edge.

## Temporal and scalar semantics

Let `s` and `e` be a treatment interval's endpoints, `b` a baseline point, and `f` a follow-up point. The interval is half-open, `[s,e)`, with `s < e` on the discrete microsecond grid.

| Query operation | Exact condition |
|---|---|
| Baseline window | `min_before_start_us <= s − b <= max_before_start_us` |
| Follow-up from start | `min_after_anchor_us <= f − s <= max_after_anchor_us` |
| Follow-up from end | `min_after_anchor_us <= f − e <= max_after_anchor_us` |
| Optional `within_interval: true` | Additionally require `s <= f < e` |

Window bounds are inclusive nonnegative integers. A baseline minimum of one microsecond expresses strict precedence on this grid; a minimum of zero permits a simultaneous label. Follow-up anchoring on the end with a nonnegative gap and `within_interval: true` has no solution, as the explicit conditions require both `f >= e` and `f < e`.

Baseline selection also requires a literal item-code match, an exact unit-string match, and one scalar operator: `lt`, `le`, `eq`, `ge` or `gt`. Comparisons use the existing bounded exact-decimal parser. Follow-up selection has item/unit filters and a time window, **without a value/improvement filter**. The baseline record cannot be reused as its own follow-up.

A numeric change is reported only when the baseline and follow-up have the same item ID and literal unit. Otherwise it is `INCOMPARABLE_ITEM_OR_UNIT`. No unit conversion, synonym equivalence or clinical concept equivalence is inferred. Arithmetic uses sufficient precision for the bounded decimal inputs and retains original spellings. A numeric increase or decrease is an observed record difference, not a classification of clinical improvement.

## Certainty, follow-up and completeness

The source network consists of all selected bounds, interval-duration conditions and interval constraints. Each named baseline–treatment pair is classified against that complete source network:

- `CERTAIN`: the same named pair satisfies the query in every feasible selected-source timeline.
- `POSSIBLE`: the pair satisfies it in some feasible timelines, but not all.
- `IMPOSSIBLE`: no feasible source timeline satisfies the pair's conditions.
- `INCOMPARABLE`: required clock compatibility is undeclared, without a comparable contradiction already proving impossibility.

A patient is certainly eligible only if at least one **fixed named** pair is certain. “Every timeline has some baseline” does not establish certainty if that baseline must change between timelines.

For eligible pairs, each named follow-up is evaluated as the **joint baseline–treatment–follow-up binding against the original source network**. The engine does not narrow the source to worlds satisfying a possible baseline and then incorrectly call the follow-up certain. Evidence includes source edges, constraint paths, possible witnesses, counterexamples and negative-cycle diagnostics from the existing STN classifier. The tests compare uncertain cases to an independent enumeration using raw dates and direct arithmetic.

`certain_patient_ids` and `possible_patient_ids` describe **baseline–treatment eligibility**, not a requirement to show a response. The possible list includes certain patients. Follow-up is reported separately for every eligible pair:

| Follow-up status | Meaning |
|---|---|
| `RECORDED_FOLLOWUP` | At least one joint named binding is certain |
| `POSSIBLE_RECORDED_FOLLOWUP` | Some joint binding is possible, none is certain |
| `UNKNOWN_CLOCK_ALIGNMENT` | Remaining candidate follow-up has undeclared compatibility |
| `NO_SELECTED_FOLLOWUP_IN_WINDOW` | No distinct selected follow-up satisfies the window |

A row with an incomparable baseline reports `UNKNOWN_BASELINE_ALIGNMENT`. Rows with impossible baseline eligibility do not inspect follow-up. `clinical_response_status` stays `UNKNOWN`; the numeric record evidence is exposed separately.

Completion is explicitly over **selected records represented in the supplied stores**. It does not establish full source coverage, all ICU-stay roster coverage, recording-time replay, clinical absence or source revision-history completeness. Empty accepted views produce no eligible selected binding; this is not a claim of patient health or absence of treatment. The original import ledgers remain necessary for interpreting exclusions and source coverage.

## Bounds and verification

The existing claim and Rust bounds remain in force. The mixed step additionally permits at most 64 selected temporal variables per episode, 128 baseline candidate tests and 256 conservative follow-up candidate tests. These limits are checked before temporal enumeration; they may block a query even if many candidates would later prove impossible. A block returns no authoritative patient lists or partial binding list. No truncation or silent sampling occurs.

There are 34 new tests, covering real Rust treatment support, explicit alignment and rebasing, independent uncertain-world enumeration, endpoint inclusivity and half-open membership, exact scalar arithmetic, retained correlations, fixed witnesses, patient/episode isolation, pending and withdrawn support, missing follow-up, failures, limits and CLI behavior. The whole suite totals **528 checks**: 505 suite tests, 16 oracle cases and seven properties.

The next extension is a source-reviewed end-to-end study using the two importers: explicit item/clinical mappings, demonstrable clock compatibility and coverage, and reproducible comparison with a relational reference. A larger or production temporal query engine, measurement semantic inference, qualified numeric bounds, clinical validation and causal analysis remain outside this profile.
