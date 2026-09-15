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

**To understand the model:** 2.4 → 2.3 (temporal semantics) → 2.1 §6 (time detail)

**To understand the product:** 2.1 §2 (use cases) → 2.2 (workspace) → 2.3 (replay)

**To implement:** [architecture.md](architecture.md) → 2.4 → [components.md](components.md) → [validation.md](validation.md)

## Traceability

104 requirements in [`requirements.csv`](../requirements.csv), each with spec sections, release target, acceptance gates, status and interpretation. Gates run AC01 to AC18, plus AC19-PRO-SOLID for the executable profile. See [status.md](status.md).
