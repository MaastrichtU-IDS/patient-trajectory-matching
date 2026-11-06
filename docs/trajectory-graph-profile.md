# Trajectory-graph profile

**Profile:** `trajectory-graph-1.0` · **Code:** [`patterns/trajectory_graph.py`](../patterns/trajectory_graph.py), reference [`patterns/trajectory_graph_reference.py`](../patterns/trajectory_graph_reference.py) · **Tests:** `python -m patterns.test_trajectory_graph`

This profile decides whether a patient's events match a trajectory shaped as a sequence, a tree or a graph, when event times are known only within ranges. It is the engine counterpart of the [trajectory mockup](../demos/mockups/trajectory/), whose browser search it is meant to replace (see [engine design](engine-design.md) §3 and §7.5). Its semantics are the mockup's; its decision procedure avoids the mockup's exhaustive search wherever a polynomial method is sound.

## Input

The engine reads a projected execution form, not RDF. Concept matching is the caller's: each event lists every concept it counts as, ancestors included. The [SULO adapter](trajectory-graph-sulo-adapter.md) produces this form from bounded, patient-local and claim snapshots, and carries each bound step's evidence back.

The trajectory mockup is one caller. `demos/mockups/server.py` exposes `POST /api/trajectory-graph/match` with `{query, patients}`. The page resolves each step's candidates itself, including relaxed alternatives, emits them as events tagged with the step, and expresses each relaxation as `widen_concept` to the alternative's tag, `extend_max` or `extend_grace`. It sends times in minutes. On the synthetic cohort the engine and the page's own search agree for every patient (`demos/mockups/test_engine_parity.cjs`). The page offers relaxation sets the engine treats differently in two cases: a step's concept and count relaxations cannot combine (one relaxation per step), and sets of three or four relaxations within the budget are tried by the engine but not by the page's search. No template exercises either.

**Query**

| Field | Meaning |
|---|---|
| `steps` | `{id, concept, occurrence}`. An event can bind a step if it lists the concept. `occurrence` is `any` (default), `first` or `last`: only the earliest or latest matching event, ordered by start range, then end range, then identifier |
| `relations` | `after`: start(`to`) − start(`from`) ∈ [`min`, `max`]; either bound may be absent, and either side may be the anchor `START` or `END`. `during`: start(`to`) ≥ start(`from`) and start(`to`) − end(`from`) ≤ `grace`; `from` must be a step |
| `scope` | `any`, `from_start` (every step starts at or after `START`), `within` (between `START` and `END`) or `after` (at or after `END`) |
| `relaxations` | `widen_concept` (a step's concept replaced, e.g. by its parent), `extend_max` (an `after` relation's maximum raised) or `extend_grace` (a `during` grace raised), each with a non-negative `cost` |
| `budget` | The largest total relaxation cost tried |
| `search_budget` | Steps allowed to any one bounded search (default 40,000) |

**Patient:** `{patient_id, window: {start, end} | null, events}`. An event has `start: [earliest, latest]` and, unless it is a point, `end: [earliest, latest | null]`, where `null` means still ongoing. Times are integers on one declared grid, such as days or microseconds. Steps may share an event: distinctness across steps is an NP-hard feature (engine design §7.3) and is not offered.

## Semantics

Each bound step has a start variable in its event's start range and an end variable in its end range, with end ≥ start, and end = start for a point. A binding is **possible** when some choice of these times satisfies every relation and scope constraint jointly. It is **exact** when every choice does. Because the variables range independently over their boxes apart from end ≥ start, exactness is the conjunction of each constraint holding over the two events' ranges: min(*y*) − max(*x*) ≥ lo and max(*y*) − min(*x*) ≤ hi. The mockup's exact test is the same.

| Status | Meaning |
|---|---|
| `exact` | Some binding is exact |
| `possible` | Some binding is possible and none is exact (a stopped exact search is noted) |
| `relaxed` | No unrelaxed binding is possible; the cheapest relaxation set that admits one, with its certainty |
| `unresolved` | The unrelaxed search stopped before finding or ruling out a binding; any relaxed match found is reported beside it, not as the status |
| `none` | Every search finished and nothing matched (a stopped relaxation search is noted) |
| `excluded` | The patient has no index window |

Relaxation sets are tried in order of total cost, then size, then identifier, with at most one relaxation per step or relation, up to `budget`. The first set that admits a binding is reported.

## Decision procedure

| Question | Trajectory shape | Method | Cost per patient |
|---|---|---|---|
| Exact | Forest (relations between steps form no cycle) | Arc consistency on the pairwise certainty relation over candidate events, then a backtrack-free assignment (Freuder 1982) | Polynomial, O(*e* · *n*³) with AC-3 |
| Exact | Graph with a cycle | Arc consistency, then backtracking over the remaining candidates | Bounded by `search_budget` |
| Possible | Forest, every candidate a point event | Arc consistency on pairwise plausibility, then dynamic programming over unions of time intervals: leaves restrict their parent's start times to those that can reach a child time, and a top-down pass takes the latest admissible time at each step | Polynomial |
| Possible | Cycles, or exposures (start and end chosen together) | Arc consistency, then backtracking that checks each partial binding's temporal network (`temporal_stn.py`) | Bounded by `search_budget` |

Arc consistency is always sound pruning: an event without support for some relation cannot be in any binding. On exact times it is often decisive even on cycles. For example, B and C each 1–2 days after A but exactly 10 days apart propagates around the cycle until every domain is empty.

Why the polynomial cases stop where they do:

- Exactness is a binary constraint over event choices, so the classic result for tree-structured constraint problems applies for every step kind.
- Possibility on point events can also be read as max-closed constraints over time variables (engine design §7.2). On a cyclic graph, though, propagation can shave a domain one grid point at a time, so its cost grows with the length of the time ranges, not with the number of events.
- An exposure's start and end are chosen together, which breaks max-closure (engine design §7.3).

Those cases are searched, under a budget, and an exhausted budget is reported as `unresolved`.

Each result names its `method` and whether it is `complete`. A possible result carries a witness start time for each step.

## Verification

`patterns/test_trajectory_graph.py` has 19 tests:

- **Semantics.** Exact and possible sequences, first occurrence, `during` with grace and an ongoing exposure, scope and anchors, exclusion, and cost-ordered relaxation.
- **Methods.** A six-step tree with 300 candidates per step (300⁶ bindings) decided by propagation with a search budget of 1. A cycle searched. An exhausted budget reported as unresolved, with the relaxed match beside it. A contradictory cycle refuted by arc consistency alone.
- **Validation.** Queries and patient records outside the profile are refused by name.
- **Example.** The committed example (`examples/trajectory-graph/`) gives one patient of each outcome.
- **Differential.** 1,500 seeded random trajectories: chains, trees and graphs with a cycle, with point events and with exposures. The engine and `trajectory_graph_reference.py` must agree on status, certainty, relaxation set and cost, and every binding the engine reports must have the claimed certainty under the reference's own test.

The reference enumerates every binding. It shares no decision code with the engine: candidate selection, the constraint semantics and its feasibility test (Bellman–Ford) are written again. Planting a defect in the engine's interval propagation, or using certainty where plausibility belongs, makes dozens of differential cases fail.

## Not in this profile

- An adapter from SULO claims or the execution store. The SULO adapter is a separate module: see [trajectory-graph SULO adapter](trajectory-graph-sulo-adapter.md).
- Event-count steps and measurement conditions; callers supply them as derived events.
- Distinct events across steps, disjunctive relations, nested "while taking" and absence (engine design §7.3).
- Clocks: every time is on one comparable grid.

```sh
python -m patterns.trajectory_graph examples/trajectory-graph/query.json examples/trajectory-graph/patients.json
python -m patterns.test_trajectory_graph
```
