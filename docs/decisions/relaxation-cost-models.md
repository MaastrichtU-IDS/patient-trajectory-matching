# Decision: reconciling the two relaxation cost models

**Status:** **Accepted 21 September 2026 — option D.** The project accepted unifying the reported contract while keeping both evaluators. The identifier the decision called for was implemented on 3 October 2026, and the last open adoption item was decided on 4 October 2026; both are recorded at the end. No cost either model reports has changed. The options below are retained as the record of what was decided against.

**Related:** [exemplar pattern](../../examples/exemplar.pattern.json) and [reference oracle](../../reference_oracle.py); [robust temporal relaxation](../robust-temporal-relaxation.md); [custom relaxation catalogue](../custom-relaxation-catalogue.md); [extended relaxation](../extended-relaxation.md).

## Problem

Two executable models price relaxation, and a researcher meets both without being told which one is answering.

The word **budget** appears in both. It does not mean the same thing. A budget of `"2"` admits a compositional sum of two priced constraint changes in one model, and admits any single catalogue option costing at most 2 in the other. Neither is wrong for its own query class; the collision is in the shared vocabulary and the shared result field names.

| | Point-anchor oracle | `robust-temporal-relaxation-1.0` |
|---|---|---|
| Evaluator | [`reference_oracle.py`](../../reference_oracle.py) | [`patterns/robust_relaxation.py`](../../patterns/robust_relaxation.py) |
| Reached from | `patterns/pro_solid.py`, `demo/cohort.py`, the five-stop guided demo | `app/journey.py`, `app/temporal.py`, `app/interval_editor.py`, `app/relaxation_catalogue.py` |
| Relaxation vocabulary | Two fixed ops: `concept_alternative`, `extend_max_gap` | A finite authored catalogue, up to 16 options |
| Cost shape | Semantic cost is a fixed policy value; temporal cost is **continuous**, proportional to the overrun | Every option carries one **fixed** exact decimal cost |
| Composition | Costs **sum across targets**: `total = sum(components.values())` | Options are **never implicitly combined**; no cost is summed across options |
| Budget dimensions | `max_total_cost` and `max_relaxed_constraints` (altered constraints) | cost budget and changed-**target** budget |
| Uncertainty | Worst case priced as a relaxation; measurement times must be exact | `exists option, exists binding, forall feasible timelines` |
| Accepts | Any binding within budget | Only `CERTAIN` bindings; `POSSIBLE` reported separately |
| Equal-cost tie | Lower cost, then fewer altered constraints, then binding order | **Original query first**, then option id, binding, episode |

## Where the models genuinely disagree

These are not presentation differences. Each changes which cohort a researcher gets.

**1. Composition.** The oracle reaches cost 1.5 by adding a reviewed concept alternative priced 1 to half of an available temporal extension priced 1. The catalogue cannot express that: a 1.5 result must be one authored option priced 1.5. So the oracle admits priced combinations nobody authored individually, and the catalogue admits only combinations somebody approved as a whole. The catalogue documents the consequence concretely: at budget `"1"` two options costing 0.5 each both fit individually and still return no robust match, because the engine does not combine them ([custom relaxation catalogue](../custom-relaxation-catalogue.md)). The oracle, given the same budget and two components priced 0.5, would accept.

**2. Continuity.** The oracle's temporal cost is `max(0, (gap_max - limit) / extra_seconds) * maximum_cost` — a one-day overrun costs half of a two-day overrun. Catalogue costs do not vary with how far a limit moved. Two overruns of different size are either the same option at the same price, or two separately authored options.

**3. What the count budget counts.** `max_relaxed_constraints` counts constraints whose component cost exceeded zero. The catalogue's changed-target budget counts targets a single option modifies. The same number therefore bounds different things.

**4. Certainty.** The catalogue accepts only bindings certain in every feasible timeline, because its inputs carry bounded uncertainty. The oracle takes bounded times too — `C12` records an exposure with a two-day span — but it never withholds a match because some feasible timeline would break it. It prices the worst case, admits the patient, and reports the uncertainty in `exact_status` rather than in `accepted_as`. The same recorded uncertainty therefore puts a patient in the cohort on one surface and out of it on the other, and a reader comparing a "relaxed match" from each is not comparing like with like.

An earlier draft of this note gave the reason as the oracle having exact times and so no distinction to make. That was wrong, and `C12` has contradicted it since before the note was written: the oracle's *measurement* times must be exact, its exposure times need not be, and what it lacks is not uncertainty but the certain-in-every-timeline filter.

**5. Tie-breaking.** [#49](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/pull/49) made the catalogue prefer the unchanged original on an equal-cost tie, so a zero-cost alternative can never displace the original for no benefit. The oracle has no notion of an `original` option in its ordering; a zero total cost is `EXACT` and wins on cost alone, which reaches the same outcome by a different route and only because zero is the floor.

## Options

**A. Keep both, document the bridge.** Add a profile identifier to every relaxation result and state in both runbooks which model produced it. Cheapest, changes no behaviour, and leaves "budget 2" ambiguous across surfaces.

**B. Converge on the catalogue model.** Re-express the oracle's two ops as authored catalogue options. Gains one auditable vocabulary and one tie-break rule. Loses continuous temporal pricing: the 16 existing oracle cases include costs that are proportions of an extension, so either they are re-authored as discrete options or their expected costs change — and those cases are a committed contract with a passing fixture report.

**C. Converge on the oracle model.** Give the catalogue compositional, continuous costs. Gains expressive pricing. Loses the property [#46](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/pull/46) was built to guarantee: a single approved modification fixed before quantifying over worlds. Implicit combination would reintroduce exactly the world-dependent choice that contract forbids.

**D. Unify the contract, keep two evaluators.** Treat the split as legitimate — the query classes really do differ — and unify only what a reader sees: one declared meaning for each budget dimension, one tie-break rule, one `relaxation_profile` field on every result, and one reporting shape for cost provenance. Each evaluator keeps its own pricing because each is correct for its own inputs.

**Accepted: D.** The two models are not competing implementations of one idea; they price different things. Pricing a worst case admits continuous compositional pricing safely — the oracle reads a bounded exposure time off its upper bound and never chooses a world — while accepting only what holds in every world does not, because composition is where a world-dependent choice can re-enter. What is not defensible is that both surfaces say *budget* and mean different things without saying so. D fixes the defect that actually reaches a researcher and leaves the pricing where the evidence supports it.

B deserves a second look if the oracle's 16 committed cases are ever re-authored for another reason; converging then would be much cheaper than converging now.

## Declared meanings, as accepted

These are the unified statements option D calls for. They describe both models as they behave today; neither model changed to produce them.

**Which model priced a result.** `robust-temporal-relaxation-1.0` identifies itself: every result carries `profile`, and its context carries `context_id`. The point-anchor oracle path now stamps `relaxation_profile: point-anchor-relaxation-1.0` on every result from `run_cohort()`, so neither surface has to be recognised by inference from `schema_version` or from the absence of a catalogue profile. The field reports which model priced the result; the implementation that produced it remains identified by `oracle_sha256`.

**The budget keys are already distinct, and are not interchangeable.** No rename is needed; the earlier draft of this note was wrong to suggest one.

| Key | Model | Bounds |
|---|---|---|
| `max_total_cost` | oracle | the **sum** of component costs across relaxed targets |
| `max_relaxed_constraints` | oracle | how many constraints have a component cost above zero |
| `max_cost` | catalogue | the cost of the **single** option applied; costs are never summed across options |
| `max_changed_targets` | catalogue | how many targets that one option modifies |

A limit of 2 therefore admits a compositional sum in the oracle and one option priced at most 2 in the catalogue. The numbers are not comparable and must not be presented as one control.

**Tie-breaking is equivalent in outcome, by different routes.** The catalogue prefers the unchanged original on an equal-cost tie, then option identifier, canonical binding and episode. The oracle orders by total cost, then by the number of altered constraints, then by binding. It has no explicit original-first rule and does not need one: an unrelaxed binding costs zero, zero is the floor for both its component costs, and a zero total is reported as `EXACT`. That reasoning holds only while every authored relaxation cost is nonnegative, which both models require today.

## Implementation cost of the identifier

Stamping the oracle path is done. The field itself is one line in `demo/cohort.py`, but the change propagates, and the order below is the order it must be carried out in:

1. `run_cohort()` output is embedded in `demo/Guided_Cohort_Demo.html`, so the standalone demo must be rebuilt.
2. `demo/build_guided.py` rewrites `demo/cohort-data.json` as a side effect, giving it a new generation commit.
3. Those bytes are pinned at `construction_origin.sha256` in `examples/patient-similarity/dataset.json`, which `patterns/patient_similarity.py` surfaces as `source_dataset_sha256` and `app/server.py` compares against the file it reads. The application refuses to start otherwise, with `Similarity and trajectory source fingerprints differ`.
4. `examples/patient-similarity/dataset.json` is itself pinned in `verification/research-prototype-journey.json`.

So a reporting field on that path requires regenerating a fixture and its evidence, in that order. Regenerating the evidence before the fixture captures a digest that is about to change, and the failure surfaces two steps away as a refusal to start rather than as a stale pin.

The oracle and its exemplar pattern are separately pinned in the release manifest (v2.5, carried over unchanged from v2.4) and did not change: the identifier is stamped by `demo/cohort.py`, which calls the oracle, not by the oracle itself. That is what kept this a fixture regeneration rather than a release change.

## What adoption still requires

- ~~Acceptance cases that put one clinical question through both surfaces~~ — done on 3 October 2026 in `patterns/test_relaxation_contract.py`, class `OneClinicalQuestionThroughBothSurfaces`. One timeline specification generates both fixtures, and a test reads the gap back out of each to establish that the two surfaces were asked the same thing before any divergence is attributed to the models. What the cases pin is below.
- ~~A decision on whether a relaxed oracle result may be presented beside a certain or possible catalogue result in one view~~ — decided on 4 October 2026: no. What that required of the product is below.

### What the acceptance cases pin

The shared question is the temporal core both surfaces express: *was there an administration followed, within seven days, by a creatinine measurement?* The oracle's value delta and baseline selection are held fixed rather than compared, because the bounded profile expresses neither.

| Timeline | Stated budget | Oracle | Catalogue |
|---|---|---|---|
| Exposure 7 days before | 0.5 | `EXACT`, cost 0 | `original`, cost 0 |
| 8 days | 0.5 | `RELAXED`, cost 0.5 | admitted, cost 0.5 |
| 9 days | 0.5 | **`NONE`** | **admitted, cost 0.5** |
| Exposure recorded in a 6.5–7.5 day span | 0.5 | **`RELAXED`, cost 0.25** | **possible, not robust** |

The two disagreements are the point. At nine days both surfaces are given the number 0.5 and return different cohorts, because the oracle prices the overrun and the catalogue prices the authored option. Under recorded uncertainty the oracle prices its worst case and admits; the catalogue quantifies over timelines and refuses. Neither answer is wrong for its own model, which is why the `relaxation_profile` field has to be read before the number is.

Two further differences surfaced while writing the cases, both now pinned:

- **Composition is a difference in vocabulary before it is a difference in arithmetic.** A catalogue option relaxes a metric constraint, so the concept half of the oracle's summed total on `C05` has no target it could be authored against: the policy is rejected with `INVALID_RELAXABLE_TARGET` before any budget is considered.
- **The two surfaces do not share a representation of an instant.** The oracle's events are points; the bounded profile requires `start + 1 <= end` and rejects a zero-length event as an inconsistent source. The acceptance builder gives each instant a one-minute extent, placed so that it cannot move the gap under test. That is a translation step, and a comparison that forgot it would be comparing a timeline neither surface was given.

### Whether the two may share a view — decided, no

A relaxed oracle result may **not** be presented beside a certain or possible catalogue result as a comparable finding. The acceptance cases above are the reason, and they are stronger than a stylistic preference: given the same stated number, nine days returns `NONE` from one surface and an admitted cost-0.5 match from the other, and under recorded uncertainty one admits on its worst case where the other refuses for want of robustness. Two results placed side by side read as two values of one control. Here they are two answers to two different questions, and nothing on the screen said so.

That decision only matters if something enforces it, and the `relaxation_profile` field was not enough on its own: it was in both payloads from 3 October and reached neither screen. Three things now carry it.

- **Each result names the model that priced it**, on screen, beside the result, on all four surfaces that show a budget: `/`, `/journey`, `/temporal` and `/temporal/editor`. The workspace reads `relaxation_profile` off the oracle result; the three catalogue surfaces read `profile` off the executed policy, which `/temporal` nests under `inputs` and the other two carry at `policy`. All read the identifier out of the payload rather than printing a constant, because a label kept by hand drifts from the model that actually answered — which is the failure the field exists to prevent. Two disagreeing identifiers refuse rather than pick one.

  Only the executed *policy* is read. `profile` is not one field in this repository: `result.profile` on the interval editor is the query language, `extended-interval-query-1.0`, not a cost model. A first implementation read it, and the line refused on a result that was perfectly well identified.
- **Each surface states what its own number bounds**, beside its own control, and that it is not the other surface's number. The two statements have to disagree about summing, because that is the difference.
- **No response carries both identifiers.** `app/test_server.py` serializes a response from each of the four surfaces and asserts that each names its own model and mentions neither the other's anywhere in its payload. That makes the separation a property of the data rather than of whichever page happens to render it, so a future combined view would fail the test rather than quietly succeed.

What is *not* claimed: the two surfaces remain one click apart in the research navigation, and nothing prevents a reader from opening both. The decision is that neither surface may present the other's result as comparable, and that a reader who navigates between them is told, on each, which model answered and what its number means.

## Remaining decisions

Both models carry their own open questions, recorded where they are implemented and not reopened here: who approves relaxable predicates, widened limits, costs and catalogue versions ([robust-temporal-relaxation](../robust-temporal-relaxation.md), *authority*), and what domain meaning the costs should have, given that both are fixed policy penalties and neither is a learned preference or a clinical equivalence ([*utility*](../robust-temporal-relaxation.md)).

Both models remain as implemented, and every cost either currently reports is unchanged. What was adopted is the reported contract: each result names the model that priced it, and the budget keys stay distinct because the limits they bound are not comparable.
