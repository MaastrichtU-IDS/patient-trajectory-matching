# Decision: reconciling the two relaxation cost models

**Status:** **Accepted 21 September 2026 — option D.** The project accepted unifying the reported contract while keeping both evaluators. Acceptance settles the choice; implementation is separate and staged, and no cost either model reports has changed yet. The options below are retained as the record of what was decided against.

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

**Accepted: D.** The two models are not competing implementations of one idea; they price different things. Exact recorded times admit continuous compositional pricing safely, and bounded uncertainty does not, because composition is where a world-dependent choice can re-enter. What is not defensible is that both surfaces say *budget* and mean different things without saying so. D fixes the defect that actually reaches a researcher and leaves the pricing where the evidence supports it.

B deserves a second look if the oracle's 16 committed cases are ever re-authored for another reason; converging then would be much cheaper than converging now.

## Declared meanings, as accepted

These are the unified statements option D calls for. They describe both models as they behave today; neither model changed to produce them.

**Which model priced a result.** `robust-temporal-relaxation-1.0` already identifies itself: every result carries `profile`, and its context carries `context_id`. The point-anchor oracle path does not stamp an identifier, so it is recognised by `schema_version: guided-cohort-result-1` and by the absence of a catalogue profile. Giving that path an explicit identifier is implementation work with a cost recorded below.

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

Stamping the oracle path was attempted and deliberately not completed. The field itself is one line in `demo/cohort.py`, but the change propagates:

1. `run_cohort()` output is embedded in `demo/Guided_Cohort_Demo.html`, so the standalone demo must be rebuilt.
2. `demo/build_guided.py` rewrites `demo/cohort-data.json` as a side effect, giving it a new generation commit.
3. Those bytes are pinned at `construction_origin.sha256` in `examples/patient-similarity/dataset.json`, which `patterns/patient_similarity.py` surfaces as `source_dataset_sha256` and `app/server.py` compares against the file it reads. The application refuses to start otherwise, with `Similarity and trajectory source fingerprints differ`.
4. `examples/patient-similarity/dataset.json` is itself pinned in `verification/research-prototype-journey.json`.

So a reporting field on that path requires regenerating a fixture and its evidence, in that order. The oracle and its exemplar pattern are separately pinned in the v2.4 release manifest and must not change at all.

This does not block the accepted decision. It re-scopes the work: the identifier is not the free addition it appeared to be, and should be planned with the fixture regeneration it entails, or deferred until that fixture is being regenerated for another reason.

## What adoption still requires

- The explicit identifier on the oracle path, with the regeneration chain above.
- Acceptance cases that put one clinical question through both surfaces and assert the reported cost, budget and tie-break are interpreted as documented. Nothing currently compares the two models against each other.
- A decision on whether a relaxed oracle result may be presented beside a certain or possible catalogue result in one view. The guided demo and `/journey` sit one click apart.

## Remaining decisions

Both models carry their own open questions, recorded where they are implemented and not reopened here: who approves relaxable predicates, widened limits, costs and catalogue versions ([robust-temporal-relaxation](../robust-temporal-relaxation.md), *authority*), and what domain meaning the costs should have, given that both are fixed policy penalties and neither is a learned preference or a clinical equivalence ([*utility*](../robust-temporal-relaxation.md)).

Nothing here is adopted. Both models remain as implemented, and every cost either currently reports is unchanged.
