# Specifications

The design specifications, what each covers, and how they relate. Status labels are defined in [architecture.md](architecture.md).

The pack is a companion to a product specification maintained separately. The addenda here extend it. **[Addendum 2.4](../addenda/specification-2.4.md) is the current modeling contract** — start there.

| Version | Document | Covers | Status |
|---|---|---|---|
| **2.4** | [specification-2.4.md](../addenda/specification-2.4.md) | PRO/SOLID encoding, executable slice | **Executable** |
| 2.3 | [specification-2.3.md](../addenda/specification-2.3.md) | Temporal replay, Graphiti assessment | Specified |
| 2.2 | [specification-2.2.md](../addenda/specification-2.2.md) | Patient workspace and interaction design | Specified |
| 2.1 | [specification-2.1.md](../addenda/specification-2.1.md) | Use cases, time semantics, MIMIC study plan | Specified |

Each addendum states its own implementation status in its opening lines, and each was explicit that prior executable contracts remained unchanged.

Two further executable contracts are specified as standalone documents rather than numbered addenda, and two design documents develop the next temporal steps:

| Document | Covers | Status |
|---|---|---|
| [exact-interval-profile.md](exact-interval-profile.md) | Interval adapter, clocks, endpoint comparisons, evidence | **Executable** `exact-interval-1.0` |
| [interval-cohort-matching.md](interval-cohort-matching.md) | Slot queries, indexed joins, differential reference | **Executable** `interval-cohort-1.0` |
| [sulo-owl-time-review.md](sulo-owl-time-review.md) | Temporal representation, uncertainty, reasoning, indexes | Design guidance |
| [decisions/temporal-precedence.md](decisions/temporal-precedence.md) | Strict precedence, direct succession, temporal contact | Proposed; not adopted |
| [temporal-kg/](temporal-kg/) | Formal definition of the temporal knowledge graph, v1 and v2 | Formal specification |

---

## 2.4 — Executable PRO and SOLID contracts

**15 September 2026 · Executable · Supersedes the initial ontology encoding examples in 2.3**

The current modeling contract. It leaves the temporal knowledge graph definition intact and specifies how its entities, roles and literal-bearing assertions are encoded in the first runnable profile.

| Section | Subject |
|---|---|
| Decision | PRO for participation, SOLID for literal-bearing information |
| §1 | Composition and meaning — the eight-row encoding table |
| §2 | Worked graph |
| §3 | Executable slice — the five pipeline stages |
| §4 | Exemplar query and semantic relaxation |
| §5 | Temporal and value normalization |
| §6 | Constraints and failure semantics — the three-level model |
| §7 | Product and UI integration |
| §8 | Acceptance and remaining implementation work |

**Key decisions**

- Participation goes through a role and bearer, never directly
- `sulo:hasValue` is the only literal-bearing predicate in instance data
- The extension declares no new object or datatype properties
- Ontology axioms, ingestion constraints and query constraints stay distinct
- A `ContractError` is a profile violation, never "patient does not match"

Implemented by `patterns/pro_solid.py`, `ontology/pro-solid-profile.ttl` and `ontology/pro-solid-shapes.ttl`. Verified by 42 acceptance tests under gate AC19-PRO-SOLID. Adds requirements PS-001 to PS-010.

The earlier proposal, assertion and shape drafts are archived in `ontology/legacy-2.3/` and are **non-normative**.

---

## Exact-interval profile 1.0

**Executable · separate from the point-anchor contract**

An interval adapter and pairwise temporal evaluator using the same pinned SULO core, introducing classes and individuals with no new object or datatype properties.

Processes carry an occurrence interval with distinct typed start and end descriptors and no scalar value on the interval itself. Clocks are explicit: a temporal edge requires the same clock resource and descriptor.

Four request operators: `before` (`eA < sB`), `meets` (`eA == sB`), directional `overlaps` (`sA < sB < eA < eB`) and `gap` with inclusive integer bounds. Overlapping intervals have a negative signed gap and do not satisfy a nonnegative-gap request. A computed gap is a signed difference, not a Duration assertion.

`SATISFIED` and `NOT_SATISFIED` describe one exact constraint on one recorded pair. Neither establishes cohort membership or clinical absence.

Verified by 51 conformance tests including an RDF round trip. Report: `verification/exact-interval-report.json`.

---

## Interval cohort matching 1.0

**Executable · builds on the exact-interval projection**

Conjunctive queries over required, distinct interval slots within patient episodes. Adds no RDF classes or properties; leaves the v2.4 API and oracle independent.

Two engines implement the same semantics — an indexed join and an exhaustive reference — and are checked differentially. Episode verdicts are `MATCH`, `INCOMPARABLE` or `NO_RECORDED_MATCH`; a matching episode can still carry visible unresolved bindings.

`search_complete: true` means every matching and unresolved binding was enumerated over the validated snapshot. It does not assert complete clinical records, absence in reality, or certain-answer semantics.

Verified by 18 contract and differential tests.

---

## SULO and OWL-Time review

**Design guidance · 17 sections**

The most detailed temporal analysis in the repository. Covers what OWL-Time contributes, what temporal individuals denote, a class-only temporal profile, scalar values and frames, keeping occurrence/validity/evidence history distinct, core changes worth considering, how to allocate reasoning responsibilities, an efficient temporal execution kernel, uncertainty and certain answers, compiling representation into indexes, and an OWL-Time bridge that does not change canonical SULO.

§11 (uncertainty, certain answers and relaxation) and §15 (recommended implementation sequence) are the sections that define the next build step. §16 states the verification boundary.

Bounded uncertainty and general interval matching remain future work.

---

## Temporal precedence decision

**Proposed · not adopted**

Distinguishes strict whole-interval precedence, direct succession within a sequence, and zero-gap temporal contact — and the scope needed to interpret adjacency under incomplete knowledge.

`precedes`, `directlyPrecedes` and `immediatelyPrecedes` are **not** materialized by the interval evaluator, and are not additions to the pinned ontology or the current application vocabulary. Adoption is a separate upstream SULO release decision.


---

## Temporal knowledge graph: formal definition

**Formal specification · two PDFs under [`docs/temporal-kg/`](temporal-kg/)**

The mathematical definition underlying everything else in this repository. Where the addenda specify contracts and the review gives guidance, these define the semantics.

### v1 — `Temporal_Knowledge_Graph_Formal_Definition.pdf` (4 pages)

Defines a temporal knowledge graph as a tuple

```
K = (Sigma, O, A, T, Gamma)
```

— signature, ontology axioms, assertions, temporal structure and explicit temporal constraints.

Time positions are rational coordinates on a declared reference axis; proper intervals are half-open `[s,e)`. **A comparison between values from different clocks requires an explicit mapping into a common reference frame; an unknown mapping does not license a temporal comparison.** Uncertain positions are variables constrained by `Gamma`, never arbitrary assigned timestamps — which is the formal statement of the rule the interval cohort matcher enforces as `INCOMPARABLE`.

A temporally qualified assertion `R(a,b)@[s,e)` holds throughout the stated interval and asserts **nothing** outside it. Process occurrence is separate: `Occurs(p,I)` is a claim about the process's temporal extent, and does not expire when `I` ends.

Trajectory patterns split into `Psi_ontology` and `Psi_time`. A binding is a **certain match** when `K |= Psi[mu]`, and a **possible match** when at least one model satisfies it; a possible match that is not certain remains unresolved.

### v2 — `Temporal_Knowledge_Graph_Formal_Definition_v2.pdf` (9 pages, 15 September 2026)

A working specification that revises v1 substantially:

- **Representation fixed to OWL 2 DL.** The family of interpretations `I_t` is replaced by an OWL-native representation; a single interpretation describes an entire history.
- **Primitive points and intervals** are disjoint classes, with functional and disjoint `hasBeginning` / `hasEnd`.
- **Temporal situations** represent time-dependent relations as typed individuals with a validity extent, replacing timeless shortcuts.
- **Evidence, assertion identity and revision** get explicit structure, with a versioned manifest and an immutable selected snapshot.
- **Finite query language** with a separated baseline: `Supported`, then `Certain` and `Possible` over the constraint system, evaluated by a solver contract.
- **Controlled relaxation** with a declared cost function and worst-case scoring over feasible assignments.

§11 reports **18 ROBOT v1.9.10 / OWLAPI profile checks passing** with HermiT for the standalone core and example, covering satisfiability, PRO inference, disjointness, endpoint equality consequences and the external endpoint-order condition. §12 positions the result on a six-axis classification.

**Section 13 is a decision register of eleven open questions (Q1–Q11)**, each naming the capability it blocks, a proposed baseline, and the evidence required to close it. See [issues.md](issues.md#relationship-to-the-v2-decision-register) for how these map onto the gaps catalogued there.

The document is explicit that the complete temporal query engine remains to be implemented, and that the application fragment has no end-to-end implementation in the deliverable.


---

## 2.3 — Temporal replay and Graphiti assessment

**Specified, not implemented or benchmarked**

Defines what "temporal knowledge graph" means for this product: a knowledge graph with an explicit temporal interpretation, versioned and source-grounded, representing supported claims, unresolved information and conflicts. Timestamps alone do not establish truth.

**Core distinctions**

- Each assertion has a declared temporal scope: timeless, an instant, an interval, a constrained set of possible times, or unknown. **Unknown scope is not timeless scope.**
- Occurrence time and assertion applicability are distinct. A correction changes the supported assertion about an event; it does not move or delete the event.
- A new clinical event, an additional record of the same event, a correction, a state change and conflicting evidence are five different things. A newer ingestion timestamp alone cannot establish which occurred.

**Replay modes**

| Mode | Uses |
|---|---|
| Source-as-known | Only correction and supersession information available by a declared cutoff |
| Retrospective reconstruction | Later corrections, under a mandatory distinct mode label |

The two cannot be silently interchanged. Replay coverage is `COMPLETE`, `PARTIAL` or `UNAVAILABLE`, independent of `computation_status`. Replay evidence does not establish what a clinician actually read or knew.

Contributes 11 TRP requirements and gates AC17/AC18. Assets: `ui/temporal-replay-2.3.json`, `examples/temporal-replay-cases-2.3.json` (8 case families), `evaluation/graphiti-comparison-2.3.json`.

**No Graphiti measurements exist in this repository.** The comparison is a protocol.

---

## 2.2 — Patient-centred workspace and interaction design

**Specified, not implemented**

Section 20 of the product specification. The proposed distinguishing feature is a continuous connection between the events selected, the criteria being edited, the patients entering or leaving the result, and the evidence explaining each decision. A design succeeds when users can predict and explain those changes.

The addendum is explicit that visual novelty and clinical usefulness remain hypotheses to test.

**Progressive disclosure**

| Stage | Interaction | Exposed next |
|---|---|---|
| Start | Select a patient and index rule | Feature contributions, coverage, ranked candidates |
| Focus | Mark a feature Must match / Prefer similar / Ignore | Eligibility versus ranking changes |
| Define | Promote events into trajectory slots | Order, gaps, values, context, observation scope |
| Relax | Enable reviewed alternatives or bounded extensions | Exact cohort, additions, per-constraint costs |

Covers asynchronous states, near-match explanations, revision history, accessible layouts and a formative usability study. Contributes 14 UI requirements and gate AC16. Assets in `ui/`: two annotated wireframes, design tokens, an eight-state storyboard, interaction contracts.

No running interface and no measured usability results exist.

---

## 2.1 — Use cases, time semantics and the MIMIC study

**Specified, not implementation-tested**

Leads with clinical examples and an iterative patient-to-cohort workflow.

### §2 Driving use cases

| Use case | Input | Success |
|---|---|---|
| UC01 People like this patient | Patient, index rule, history window, similarity profile | Ranked histories with component explanations |
| UC02 Patients following this trajectory | Similarities promoted into hard criteria and relaxations | Reproducible cohort with membership changes explained at every revision |
| UC03 Comparable evidence across sources | Typed mappings, clocks, units, provenance | Declared source meaning preserved; unknowns stay visible |

The driving clinical scenario is kidney function during antibiotic treatment. It is framed as an **exposure-associated trajectory, without an automatic claim that the medication caused injury.**

### §6 Time and constraint semantics

The fullest temporal specification in the pack, and the source for most of [issues.md §Temporal](issues.md#temporal-semantics):

- Explicit clock, origin, unit and precision; integer microseconds as the execution unit
- Points have one time variable with bounds; intervals add `start < end`
- Seven Allen relations defined by endpoint comparison, six more as inverses
- A day means 24 elapsed hours, not a calendar date transition
- Definite acceptance requires hard constraints to hold for **every jointly feasible** assignment; atomwise possibility under incompatible assignments is insufficient
- Seven source time forms, each with required interpretation and canonical treatment
- Age is an observation at a reference time; MIMIC's top-coded 91 is a de-identification category, not an age

OWL-Time is an interoperability reference for vocabulary, not the execution engine.

### §12 MIMIC-IV source and clinical study contracts

Pins the demo 2.2 schema, using `hosp.patients`, `admissions`, `labevents`, `d_labitems`, `emar` and `emar_detail`. Diagnosis and prescription tables are contextual extensions; ICU `inputevents` is an R1 extension for interval exposure with a separate reconciliation policy.

Assets: `examples/qbe-profile-2.1.json`, `examples/refinement-session-2.1.json`, `examples/time-normalization-cases-2.1.json` (16 expectations), `data/full-mimic-study-plan-2.1.json`.

The 16 normalization expectations are a **different set** from the 16 executable matcher cases.

---

## Reading order

**To understand the model:** [formal definition v2](temporal-kg/) → 2.4 → 2.3 (temporal semantics) → 2.1 §6 (time detail)

**To understand the temporal work:** [exact-interval-profile.md](exact-interval-profile.md) → [interval-cohort-matching.md](interval-cohort-matching.md) → [sulo-owl-time-review.md](sulo-owl-time-review.md)

**To understand the product:** 2.1 §2 (use cases) → 2.2 (workspace) → 2.3 (replay)

**To implement:** [architecture.md](architecture.md) → the contract for your profile → [components.md](components.md) → [validation.md](validation.md)

## Traceability

104 requirements in [`requirements.csv`](../requirements.csv), each with spec sections, release target, acceptance gates, status and interpretation. Gates run AC01 to AC18, plus AC19-PRO-SOLID for the point-anchor profile. See [status.md](status.md).

The register does not yet cover the interval profiles — see [issues.md R3](issues.md#repository-hygiene).
