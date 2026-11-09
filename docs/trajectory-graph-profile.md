# Trajectory-graph profile

**Profile:** `trajectory-graph-1.0` · **Code:** [`patterns/trajectory_graph.py`](../patterns/trajectory_graph.py), reference [`patterns/trajectory_graph_reference.py`](../patterns/trajectory_graph_reference.py) · **Tests:** `python -m patterns.test_trajectory_graph`

This profile decides whether a patient's events match a trajectory shaped as a sequence, a tree or a graph, when event times are known only within ranges. It is the engine counterpart of the [trajectory mockup](../demos/mockups/trajectory/), whose browser search it is meant to replace (see [engine design](engine-design.md) §3 and §7.5). Its semantics are the mockup's; its decision procedure avoids the mockup's exhaustive search wherever a polynomial method is sound.

## Input

The engine reads a projected execution form, not RDF. Concept matching is the caller's: each event lists every concept it counts as, ancestors included. The [SULO adapter](trajectory-graph-sulo-adapter.md) produces this form from bounded, patient-local and claim snapshots, and carries each bound step's evidence back.

The trajectory mockup is one caller. `demos/mockups/server.py` exposes `POST /api/trajectory-graph/match` with `{query, patients}`. The page resolves each step's candidates itself, including relaxed alternatives, emits them as events tagged with the step, and expresses each relaxation as `widen_concept` to the alternative's tag, `extend_max` or `extend_grace`. It sends times in minutes. A candidate emitted for several steps is one recorded event, so every copy names the same `start_var` and `end_var`, keyed by the underlying item (or measurement record) and whether it is a point or a span. The page's own network intersects the ranges of an event seen by several steps, as the engine does. On the synthetic cohort the engine and the page's own search agree for every patient (`demos/mockups/test_engine_parity.cjs`). The page offers relaxation sets the engine treats differently in two cases: a step's concept and count relaxations cannot combine (one relaxation per step), and sets of three or four relaxations within the budget are tried by the engine but not by the page's search. No template exercises either.

**Query**

| Field | Meaning |
|---|---|
| `steps` | `{id, concept, occurrence}`. An event can bind a step if it lists the concept. `occurrence` is `any` (default), `first` or `last`: only the earliest or latest matching event, ordered by start range, then end range, then identifier |
| `relations` | `after`: start(`to`) − start(`from`) ∈ [`min`, `max`]; either bound may be absent, and either side may be the anchor `START` or `END`. `during`: start(`to`) ≥ start(`from`) and start(`to`) − end(`from`) ≤ `grace`; `from` must be a step |
| `scope` | `any`, `from_start` (every step starts at or after `START`), `within` (between `START` and `END`) or `after` (at or after `END`) |
| `relaxations` | `widen_concept` (a step's concept replaced, e.g. by its parent), `extend_max` (an `after` relation's maximum raised) or `extend_grace` (a `during` grace raised), each with a non-negative `cost` |
| `budget` | The largest total relaxation cost tried |
| `search_budget` | Steps allowed to any one bounded search (default 40,000) |

**Patient:** `{patient_id, window: {start, end} | null, events}`. Times are integers on one declared grid, such as days or microseconds.

| Event field | Meaning |
|---|---|
| `id`, `concepts` | Identifier, unique in the patient, and every concept the event counts as |
| `start` | `[earliest, latest]` |
| `end` | `[earliest, latest \| null]`, where `null` means still ongoing; absent for a point |
| `start_var` | Optional name of the start's time variable; default `<id>.s` |
| `end_var` | Optional name of the end's time variable; default `<id>.f`. A point has none: its end is its start |
| `proper` | Optional, default `false`. `true` makes the event end strictly after it starts, at least one grid step later. A point cannot be proper |

Events that name the same variable share one time. That covers one time recorded by several sources, or a dose that starts when an exposure ends. A shared variable lies in every range that names it, and every event ends at or after it starts. A record whose shared variables no time can satisfy is refused with `EMPTY_SHARED_VARIABLE`, whether the ranges naming one variable do not meet or the events' order rules out every time. A variable name that is not a non-empty string, or an `end_var` on a point, is refused with `INVALID_EVENT_VARIABLE`. Default names are names like any other: an event whose `start_var` is `m.f` starts when event `m` ends.

**Proper events (D24).** A `proper` event ends at least one grid step after it starts: its order edge is start − end ≤ −1 instead of ≤ 0. Sources such as the bounded interval profile require it (start + 1 ≤ end, in microseconds). Without sharing it only narrows the event's own ranges: its start's latest value and its end's earliest move by at most one step. With sharing it reaches other events: if Y starts when a proper X ends, Y starts strictly after X starts. So `after` from 0 to 0 between them is impossible, and `after` at least 1 holds in every timeline. A proper event naming one variable as its start and end is always refused with `EMPTY_PROPER_EVENT`, whether or not other events name that variable. A proper event whose ranges leave no time for its end to follow its start is refused with `EMPTY_PROPER_EVENT` when no other event names its variables (D25; `EMPTY_SHARED_VARIABLE` before), and with `EMPTY_SHARED_VARIABLE` when the conflict's component contains a variable two events name, even if the event's own ranges alone would already be empty. A `proper` that is not a boolean, or any `proper` on a point, is refused with `INVALID_EVENT_PROPER`. The option is per event rather than per query or profile, for three reasons:
- it describes the record, not the question, and one patient may hold records from sources with different conventions;
- a patient's network is solved once per patient, whatever the query;
- the execution store keeps it as a column.

Without `proper` the engine decides exactly as before, byte for byte.

The patient's network is solved once per patient, in time cubic in each group of linked variables. A lone exposure links only its own two variables and costs nothing. Groups that sharing builds may together cost no more than one group of 300 variables, which solves in under a second. A larger record is refused with `SHARED_VARIABLE_COMPONENT_LIMIT` before any group is solved.

Steps may share an event, and then share its times: distinctness across steps is an NP-hard feature (engine design §7.3) and is not offered.

## Semantics

Times belong to variables, not to steps. A step's start and end are the variables its event names, so one event bound to two steps has one time, and so do two events that name one variable. A patient's own record is a simple temporal network over its variables: each lies in every range that names it, and each event ends at or after it starts (a proper event at least one grid step after). A binding is **possible** when some choice of the patient's times satisfies that network and every relation and scope constraint jointly. It is **exact** when every choice the network allows satisfies every constraint.

Every choice satisfies all the constraints exactly when, for each constraint, every choice satisfies it. So exactness is the conjunction of one test per constraint: over the patient's network, the largest value of *y* − *x* is at most hi and the smallest at least lo. Those extremes are shortest-path distances in the patient's network, which depend only on the two variables, never on the binding. Two terms on one variable differ by exactly 0, so a constraint between them is certain exactly when lo ≤ 0 ≤ hi. A variable that no event links to another ranges over its own range, and the test reduces to min(*y*) − max(*x*) ≥ lo and max(*y*) − min(*x*) ≤ hi. The mockup's exact test is the same, over the events it binds. A proper event's −1 edge is part of the network: its own ranges are solved with it, and the distances within a linked group include it. Between two variables that nothing links, the joint range is still the product of their ranges, so the pairwise kernel's closed form over the tightened ranges stays exact.

**This changes exactness.** Before shared times, each step had its own copy of its event's variables, and certainty was tested over the two ranges alone. Two facts now count toward certainty that did not before:
- an exposure's own end ≥ start;
- the zero difference between two terms on one variable.

So some bindings that were possible are now exact, and some that were possible are now impossible, because one event cannot take two times. On the trajectory page, the heavy trajectory "two seizures then a febrile seizure" moves 4 of the 48 synthetic patients from possible to relaxed/exact. They had been possible only because one seizure took two times. No template's results change.

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
| Possible | Forest, every candidate a point event | Arc consistency on pairwise plausibility, then dynamic programming over unions of time intervals: leaves restrict their parent's start times to those that can reach a child time, and a top-down pass takes the latest admissible time at each step. If the binding found puts two steps on linked variables, it is checked against the joint network, and searched for as below if the check fails | Polynomial, unless the check fails |
| Possible | Cycles, or exposures (start and end chosen together) | Arc consistency, then backtracking that checks each partial binding's temporal network (`temporal_stn.py`) | Bounded by `search_budget` |

Arc consistency is always sound pruning: an event without support for some relation cannot be in any binding. That holds with shared variables, because both pairwise tests read the patient's network. A possible binding's times satisfy the network and each constraint, so every constraint is plausible for its pair of events. Exactness is a conjunction of tests that each depend on two variables. On exact times it is often decisive even on cycles. For example, B and C each 1–2 days after A but exactly 10 days apart propagates around the cycle until every domain is empty.

The interval programme chooses each step's time independently. Steps whose events share a variable, or are linked through an exposure's start and end, are coupled in fact. The programme solves a relaxation of the problem: every true solution is one of its solutions. So its "no binding" is a sound refutation. A binding it finds stands as found only when no two steps' variables are linked. Otherwise the binding is checked against the joint temporal network and, if that fails, the question goes to the bounded search, which is exhaustive. Each answer is either refuted by a relaxation, verified by the exact network test, or searched, so the method is sound without having to reason about couplings inside the programme. The method reports `point_forest_intervals_checked` when the check confirms the binding, and `bounded_backtracking` when it falls back.

Why the polynomial cases stop where they do:

- Exactness is a binary constraint over event choices, so the classic result for tree-structured constraint problems applies for every step kind.
- Possibility on point events can also be read as max-closed constraints over time variables (engine design §7.2). On a cyclic graph, though, propagation can shave a domain one grid point at a time, so its cost grows with the length of the time ranges, not with the number of events.
- An exposure's start and end are chosen together, which breaks max-closure (engine design §7.3).

Those cases are searched, under a budget, and an exhausted budget is reported as `unresolved`.

Each result names its `method` and whether it is `complete`. A possible result carries a witness start time for each step.

## Verification

`patterns/test_trajectory_graph.py` has 41 tests:

- **Semantics.** Exact and possible sequences, first occurrence, `during` with grace and an ongoing exposure, scope and anchors, exclusion, and cost-ordered relaxation.
- **Methods.** A six-step tree with 300 candidates per step (300⁶ bindings) decided by propagation with a search budget of 1. A cycle searched. An exhausted budget reported as unresolved, with the relaxed match beside it. A contradictory cycle refuted by arc consistency alone. Two relations on one pair that leave it no gap (C during the point A, and at least 3 days after it) give no match; the interval programme used to crash on them.
- **Shared times.** One uncertain event bound to two steps cannot take two times: the engine before this rule called "B at least 5 days after A" possible on a single event, and the answer is none. `after` from 0 to 0, or a step during its own exposure, is exact on one event. Events naming one `start_var` share its time and the intersection of its ranges. An exposure orders the variables it names even when it is not bound. A forest binding that gives one event two times is caught by the joint check, and search finds the binding that works.
- **Proper events (D24).** If Y starts when a proper X ends, `after` from 0 to 0 is none and `after` at least 1 is exact; without `proper` both are possible. A lone proper exposure narrows only its own ranges and links no other variable. `proper` on a point, a non-boolean `proper`, and a proper event that cannot end after it starts are refused by name, on the trusted path too. `proper: false` decides byte for byte as an absent `proper`, on 300 differential cases.
- **Validation.** Queries and patient records outside the profile are refused by name, including malformed time variables, shared variables no time satisfies, and linked variables beyond the limit.
- **Example.** The committed example (`examples/trajectory-graph/`) gives one patient of each outcome.
- **Differential.** 1,500 seeded random trajectories: chains, trees and graphs with a cycle, with point events and with exposures. Steps often share a concept, so one event is often bound to several steps. In about a third of the cases some events name another's start or end variable, sometimes with a different range. In a quarter of the cases a second relation is placed on a pair that is already related, sometimes contradicting the first. The engine and `trajectory_graph_reference.py` must agree on status, certainty, relaxation set and cost, and every binding the engine reports must have the claimed certainty under the reference's own test. A further 900 cases (chains, trees and graphs) use proper exposures that could otherwise end as they start (739 of the cases have one). In 682 an event starts when an exposure ends, often between events two related steps can bind, and `after` bounds sit near zero and `during` graces at zero.

The reference enumerates every binding. It shares no decision code with the engine: candidate selection, the constraint semantics and the time model are written again. Its tests run Bellman–Ford over the whole patient's variables. A binding is possible if the patient's facts and the constraints are jointly satisfiable. It is exact if, for each constraint, the facts with that constraint's negation on the integer grid are unsatisfiable. Planting a defect in the engine's interval propagation, or using certainty where plausibility belongs, makes dozens of differential cases fail. So does giving each step its own copy of its event's variables, the modelling before shared times: 140 of the 1,500 cases fail. On the 900 proper cases, dropping `proper` changes the reference's answer in 16, and an engine that ignores it (a 0 edge where −1 belongs) disagrees with the reference in 15. With the pairwise kernel's closed form refused everywhere, every proper case decides byte for byte as with it. The reference adds `proper` independently: its facts give a proper event an edge of weight −1 from end to start, where other events have 0.

## Not in this profile

- An adapter from SULO claims or the execution store. The SULO adapter is a separate module: see [trajectory-graph SULO adapter](trajectory-graph-sulo-adapter.md).
- Event-count steps and measurement conditions; callers supply them as derived events.
- Distinct events across steps, disjunctive relations, nested "while taking" and absence (engine design §7.3).
- Clocks: every time is on one comparable grid.

```sh
python -m patterns.trajectory_graph examples/trajectory-graph/query.json examples/trajectory-graph/patients.json
python -m patterns.test_trajectory_graph
```
