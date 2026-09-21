# Proposed decision: reconciling the two relaxation cost models

**Status:** Proposed, 21 September 2026. This records the design problem and options; it adopts nothing, changes no executable profile and changes no cost already reported by either model.

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
| Uncertainty | None: exact recorded point times | `exists option, exists binding, forall feasible timelines` |
| Accepts | Any binding within budget | Only `CERTAIN` bindings; `POSSIBLE` reported separately |
| Equal-cost tie | Lower cost, then fewer altered constraints, then binding order | **Original query first**, then option id, binding, episode |

## Where the models genuinely disagree

These are not presentation differences. Each changes which cohort a researcher gets.

**1. Composition.** The oracle reaches cost 1.5 by adding a reviewed concept alternative priced 1 to half of an available temporal extension priced 1. The catalogue cannot express that: a 1.5 result must be one authored option priced 1.5. So the oracle admits priced combinations nobody authored individually, and the catalogue admits only combinations somebody approved as a whole. The catalogue documents the consequence concretely: at budget `"1"` two options costing 0.5 each both fit individually and still return no robust match, because the engine does not combine them ([custom relaxation catalogue](../custom-relaxation-catalogue.md)). The oracle, given the same budget and two components priced 0.5, would accept.

**2. Continuity.** The oracle's temporal cost is `max(0, (gap_max - limit) / extra_seconds) * maximum_cost` — a one-day overrun costs half of a two-day overrun. Catalogue costs do not vary with how far a limit moved. Two overruns of different size are either the same option at the same price, or two separately authored options.

**3. What the count budget counts.** `max_relaxed_constraints` counts constraints whose component cost exceeded zero. The catalogue's changed-target budget counts targets a single option modifies. The same number therefore bounds different things.

**4. Certainty.** The catalogue accepts only bindings certain in every feasible timeline, because its inputs carry bounded uncertainty. The oracle has no possible/certain distinction to make: its times are exact, so a relaxed acceptance is unconditional. A reader comparing a "relaxed match" from each surface is not comparing like with like.

**5. Tie-breaking.** [#49](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/pull/49) made the catalogue prefer the unchanged original on an equal-cost tie, so a zero-cost alternative can never displace the original for no benefit. The oracle has no notion of an `original` option in its ordering; a zero total cost is `EXACT` and wins on cost alone, which reaches the same outcome by a different route and only because zero is the floor.

## Options

**A. Keep both, document the bridge.** Add a profile identifier to every relaxation result and state in both runbooks which model produced it. Cheapest, changes no behaviour, and leaves "budget 2" ambiguous across surfaces.

**B. Converge on the catalogue model.** Re-express the oracle's two ops as authored catalogue options. Gains one auditable vocabulary and one tie-break rule. Loses continuous temporal pricing: the 16 existing oracle cases include costs that are proportions of an extension, so either they are re-authored as discrete options or their expected costs change — and those cases are a committed contract with a passing fixture report.

**C. Converge on the oracle model.** Give the catalogue compositional, continuous costs. Gains expressive pricing. Loses the property [#46](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/pull/46) was built to guarantee: a single approved modification fixed before quantifying over worlds. Implicit combination would reintroduce exactly the world-dependent choice that contract forbids.

**D. Unify the contract, keep two evaluators.** Treat the split as legitimate — the query classes really do differ — and unify only what a reader sees: one declared meaning for each budget dimension, one tie-break rule, one `relaxation_profile` field on every result, and one reporting shape for cost provenance. Each evaluator keeps its own pricing because each is correct for its own inputs.

**Recommendation: D.** The two models are not competing implementations of one idea; they price different things. Exact recorded times admit continuous compositional pricing safely, and bounded uncertainty does not, because composition is where a world-dependent choice can re-enter. What is not defensible is that both surfaces say *budget* and mean different things without saying so. D fixes the defect that actually reaches a researcher and leaves the pricing where the evidence supports it.

B deserves a second look if the oracle's 16 committed cases are ever re-authored for another reason; converging then would be much cheaper than converging now.

## What adoption would require

- A named field on every relaxation result identifying which model priced it, present in exports and in replay.
- One declared definition per budget dimension, with the oracle's constraint count and the catalogue's target count either reconciled or explicitly renamed so they cannot be read as the same limit.
- One tie-break statement covering both, including whether the oracle should gain an explicit original-first rule rather than relying on zero being the cost floor.
- Acceptance cases that put the same clinical question through both surfaces and assert that the reported cost, budget and tie-break are interpreted as documented — currently nothing compares the two models against each other.
- A decision on whether a relaxed result from the oracle path may be presented beside a certain/possible result from the catalogue path in one view. The guided demo and `/journey` already sit one click apart.

## Remaining decisions

Both models carry their own open questions, recorded where they are implemented and not reopened here: who approves relaxable predicates, widened limits, costs and catalogue versions ([robust-temporal-relaxation](../robust-temporal-relaxation.md), *authority*), and what domain meaning the costs should have, given that both are fixed policy penalties and neither is a learned preference or a clinical equivalence ([*utility*](../robust-temporal-relaxation.md)).

Nothing here is adopted. Both models remain as implemented, and every cost either currently reports is unchanged.
