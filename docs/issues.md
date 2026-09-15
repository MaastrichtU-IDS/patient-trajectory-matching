# Outstanding issues

Known gaps and open questions. Every entry cites the specification section that identifies it — nothing here is invented. This page records what the project already says about its own limits, collected in one place.

Issues are grouped by the area they block. Tags mark the kind of work: **[gap]** specified but unimplemented · **[open]** an unresolved design question · **[risk]** something that could be misread or misused.

The [formal definition v2](temporal-kg/) carries its own decision register of eleven questions blocking operationalization. That register and this page are two views of the same open problems; [the mapping between them](#relationship-to-the-v2-decision-register) is at the end.

---

## Ontology and reasoning

### O1. No OWL reasoning **[gap]**
*Source: [2.4 §3](../addenda/specification-2.4.md), `structural-report.json`*

The adapter executes only named subclass closure, the `hasFeature` inverse and the PRO participation chain. Arbitrary OWL expressions, existential witness generation and complete consistency checking are out of scope. `full_owl_reasoning_tested: false`.

Disjointness is checked only across the named upper categories this profile needs, not for arbitrary class expressions.

### O2. Toy taxonomy is the only matcher reasoning input **[gap]**
*Source: [2.4 §4](../addenda/specification-2.4.md)*

Semantic entailment in the matcher runs against `ontology/toy-taxonomy.json`, not the RDF graph or a terminology service. Real SNOMED/RxNorm/LOINC mappings must be reviewed before the Rust reasoning stack is integrated. The seam is clean, but nothing occupies it.

### O3. SULO pin is unreviewed for production **[open]**
*Source: [2.4 §8](../addenda/specification-2.4.md), README*

SULO 0.2.14 is vendored and digest-checked. The addendum asks that the pinned version and actual terminology mappings be reviewed before integration. That review has not happened.

### O4. `hasValue` RDF-term counting is not OWL datatype equality **[open]**
*Source: [2.4 §1](../addenda/specification-2.4.md)*

`hasValue` is functional in SULO. The adapter additionally requires exactly one explicitly recorded literal per selected scalar datum, counted as RDF terms. This is a validation rule, not a complete implementation of OWL datatype equality. Two lexically distinct but value-equal literals are not reconciled.

### O5. SULO extension not published **[gap]**
*Source: SULO-001*

The proposed extension, examples and profile diagnostics have not been published externally.

---

## Clinical modeling

### C1. Quality is modeled at patient level **[risk]**
*Source: [2.4 §2](../addenda/specification-2.4.md)*

The measured quality is a feature of the person. A specimen-based laboratory adapter must explicitly model the specimen, the sampling process and the relevant roles, rather than assuming specimen and patient share a bearer. **Not implemented.** Reusing this pattern directly for real laboratory data would encode a modeling error.

### C2. Not a clinical phenotype **[risk]**
*Source: README, [2.4 §4](../addenda/specification-2.4.md)*

The creatinine branch is a constructed computational exemplar. It is not a complete AKI phenotype, and a match is not evidence of causation. All patient examples and the DrugA/DrugB alternatives are constructed.

### C3. No clinical validation **[gap]**
*Source: CLIN-001, CLIN-002, EVAL-001*

Independently authored clinical cases, candidate recall, clinical validity and source fidelity are all specified and unmeasured.

---

## Temporal semantics

### T1. The three profiles are not unified **[open]**
*Source: [2.4 §5](../addenda/specification-2.4.md), [exact-interval-profile.md](exact-interval-profile.md)*

Intervals are now implemented, but in a **separate** profile. The point-anchor oracle still uses point anchors and priced relaxation; `exact-interval-1.0` and `interval-cohort-1.0` use exact intervals with no cost model. A trajectory query cannot currently mix them, and the relaxation semantics of the v2.4 matcher have no counterpart in the interval matcher.

Whether these converge into one matcher, or stay deliberately separate with a documented bridge, is unresolved.

### T2. Allen catalogue partially implemented **[gap]**
*Source: [2.1 §6](../addenda/specification-2.1.md), [exact-interval-profile.md](exact-interval-profile.md)*

The exact-interval evaluator exposes four request operators — `before`, `meets`, directional `overlaps` and bounded `gap` — and internally reports the single basic Allen relation for comparable inputs, including containment, equality and inverses.

The full request interface specified in 2.1 §6 is not exposed. `during`, `starts`, `finishes` and the inverse relations cannot be requested directly.

### T2b. Bounded uncertainty unimplemented **[gap]**
*Source: [sulo-owl-time-review.md](sulo-owl-time-review.md) §11, [interval-cohort-matching.md](interval-cohort-matching.md)*

**This is the largest remaining temporal gap.** All three profiles handle exact recorded times only. Shared variables, joint feasibility and explicit certain/possible results are designed and unbuilt.

The consequence is visible in the cohort matcher: an unresolved binding means *missing comparability*, explicitly not a proven possible realization of an uncertain temporal system. The vocabulary for the latter does not exist yet.

**The semantics are now specified.** [Formal definition v2](temporal-kg/) §8 gives the separated baseline — `Supported(mu)` from source eligibility and OWL entailment, then `Certain(mu)` as `UNSAT(Gamma and not C[mu])` and `Possible(mu)` as `SAT(Gamma and C[mu])` — plus the fixed-witness policy `exists mu forall theta`, evaluated before projecting the patient identifier. §8.1 gives the worked reason for that policy: for two candidate times that can be `(12,36)` or `(36,12)`, a 24-hour query has a qualifying candidate in every assignment, yet neither fixed candidate is certain. What remains is the solver integration, not the definition. See v2 Q8.

### T3. No time normalizer **[gap]**
*Source: `v21-additions-report.json`*

16 declarative normalization cases exist as expectations. `normalizer_implemented: false`. These are a different set from the 16 matcher cases, which do execute.

### T4. Calendar, age and relative offsets unhandled **[open]**
*Source: [2.4 §5](../addenda/specification-2.4.md), [2.1 §6](../addenda/specification-2.1.md)*

Ages, calendar dates, relative offsets and duration units need typed information objects and separately declared adapters. A calendar age in years cannot be normalized by assuming a fixed number of seconds per year. A signed relative offset requires an identified anchor and frame, and should not be typed as a SULO Duration when negative. MIMIC-IV `anchor_age` top-codes ages above 89 as 91, which is a de-identification category rather than an age.

### T5. Bitemporal replay unimplemented **[gap]**
*Source: [2.3](../addenda/specification-2.3.md), TRP-001 to TRP-011*

Source-as-known versus retrospective reconstruction, availability cutoffs, snapshot selection and assertion revision are fully specified with 8 declarative case families. No replay engine exists.

### T6. Revision selection not implemented **[gap]**
*Source: [2.4 §2](../addenda/specification-2.4.md)*

The runner consumes one already-selected snapshot. A revision to a recorded observation requires the versioning policy in 2.3.

---

## Ingestion and sources

### S1. No clinical source adapter **[gap]**
*Source: [2.4 §3](../addenda/specification-2.4.md), ETL-001*

`build_graph` reads constructed synthetic JSON. It is not a MIMIC or FHIR importer. Reconciling every source row with typed transformation outcomes is specified and unbuilt.

### S2. Source completeness is an input, not a check **[risk]**
*Source: [2.4 §5](../addenda/specification-2.4.md)*

`source_search_complete` arrives from the fixture manifest. Source-completeness verification is not implemented. A caller can assert completeness that was never established — and the matcher's `UNRESOLVED` semantics depend on this flag being truthful.

### S3. Age derivation not implemented **[gap]**
*Source: [2.4 §5](../addenda/specification-2.4.md)*

Age arrives from the manifest. Deriving it from graph data is unimplemented.

### S4. Narrow ingestion profile **[gap]**
*Source: [2.4 §6](../addenda/specification-2.4.md)*

Not-given, planned, refused and prescription records are rejected. They need a dedicated information-record model. The rejection is deliberate — they must never become completed administrations — but the model that would represent them properly does not exist.

### S5. MIMIC-IV study not run **[gap]**
*Source: `v21-additions-report.json`*

`full_mimic_analyzed: false`. The plan exists; the study has not been performed.

---

## Services and interfaces

### A1. No service implements the API **[risk]**
*Source: `schemas/openapi.json`, `structural-report.json`*

14 REST paths are defined and structurally valid. `production_services_tested: false`. Nothing serves them.

### A2. OpenAPI is at 2.0 while the graph contract is at 2.4 **[open]**
*Source: [architecture.md §8](architecture.md#8-contract-surfaces-and-their-versions)*

The OpenAPI document describes the pre-PRO/SOLID event model and contains no role or bearer vocabulary. The projection bridges them, but a reader who opens `openapi.json` first will not find the current architecture there. Whether the API surface should expose the graph model directly is unresolved.

### A3. No UI **[gap]**
*Source: [2.2](../addenda/specification-2.2.md), 14 UI requirements*

Wireframes, tokens, storyboard and interaction contracts exist. No running interface, and no measured usability results.

### A4. Evidence panels unimplemented **[gap]**
*Source: [2.4 §7](../addenda/specification-2.4.md)*

The release provides the graph and projection evidence. The result inspector that would show "person as patient in measurement", the original value beside the normalized one, and the semantic relaxation explanation, does not exist.

### A5. Similarity and refinement unimplemented **[gap]**
*Source: REFINE-001 to REFINE-003, `v21-additions-report.json`*

`refinement_service_implemented: false`. Patient similarity, ANN, arbitrary selectors, `NOT_RECORDED`, optionality and deletion are all out of scope for the current matcher.

---

## Evaluation

### E1. No Graphiti measurements **[risk]**
*Source: [2.3](../addenda/specification-2.3.md), `evaluation/graphiti-comparison-2.3.json`*

The comparison is a planned protocol. No Graphiti results exist in this repository. The file should not be read as a benchmark.

### E2. Fixture reports are not evidence of readiness **[risk]**
*Source: [2.4 §8](../addenda/specification-2.4.md), `v24-pro-solid-report.json`*

`production_readiness_claim: false` is recorded in the report itself. Passing this suite establishes the bounded profile only.

---

## Repository hygiene

### R1. Report files mutate on every run **[open]**

`verification/v24-pro-solid-report.json` records the runner's Python version, so it changes whenever the interpreter patch version differs. CI reports this as a notice rather than a failure. The release manifest hashes inputs only, so this drift does not affect integrity checking — but it does mean a local run can leave the working tree dirty.

### R3. Interval profiles are not in the requirement register **[open]**

`requirements.csv` tracks 104 requirements and marks 10 executable, all PS-\* in the point-anchor profile. The `exact-interval-1.0` and `interval-cohort-1.0` profiles are executable and CI-verified but have no requirement IDs, no acceptance gate and no traceability row.

Until they do, [status.md](status.md) and the register disagree about what is implemented, and the register is the one that undercounts.

### R2. Legacy ontology drafts are loadable **[risk]**
*Source: [2.4 §8](../addenda/specification-2.4.md)*

`ontology/legacy-2.3/` is non-normative and must not be loaded with the current profile. Nothing mechanically prevents it.

---

## Relationship to the v2 decision register

[Formal definition v2](temporal-kg/) §13 lists eleven decisions that block operationalization, each with a proposed baseline and the evidence required to close it. They overlap substantially with the gaps above, from the other direction: the register asks *what must be decided*, this page records *what is not built*.

| v2 | Blocked capability | Related issues here |
|---|---|---|
| Q1 | Execution boundary — all metric queries | [O1](#o1-no-owl-reasoning-gap) — only a limited closure is executed; whether an external evaluator is permitted decides how far that can go |
| Q2 | Identity, ingestion and temporal joins | [O4](#o4-hasvalue-rdf-term-counting-is-not-owl-datatype-equality-open) — RDF-term counting is not datatype equality; canonicalization policy is undefined |
| Q3 | Clocks and numeric comparisons | [T4](#t4-calendar-age-and-relative-offsets-unhandled-open) — calendar, age and relative offsets; also the strict clock rule the interval matcher already enforces |
| Q4 | Process extents, ongoing or disconnected histories | [T1](#t1-the-three-profiles-are-not-unified-open) — point versus interval profiles; ongoing extents are unasserted in both |
| Q5 | State templates and time-varying domain queries | [C1](#c1-quality-is-modeled-at-patient-level-risk), [T5](#t5-bitemporal-replay-unimplemented-gap) — patient-level quality and the unbuilt replay layer |
| Q6 | Imports and biomedical mappings | [O2](#o2-toy-taxonomy-is-the-only-matcher-reasoning-input-gap), [O3](#o3-sulo-pin-is-unreviewed-for-production-open) — toy taxonomy, unreviewed pin |
| Q7 | Evidence selection and as-of answers | [T5](#t5-bitemporal-replay-unimplemented-gap), [T6](#t6-revision-selection-not-implemented-gap), [S2](#s2-source-completeness-is-an-input-not-a-check-risk) — replay, revision selection, unverified completeness |
| Q8 | Logic/solver interface and complete evaluation | [T2b](#t2b-bounded-uncertainty-unimplemented-gap) — the definition now exists; the solver integration does not |
| Q9 | Clinical query and application validity | [C2](#c2-not-a-clinical-phenotype-risk), [C3](#c3-no-clinical-validation-gap) — no phenotype validation, no clinical review |
| Q10 | Relaxation catalogue and robust ranking | [T1](#t1-the-three-profiles-are-not-unified-open) — the point-anchor cost model has no interval counterpart |
| Q11 | Scale and production acceptance | [A1](#a1-no-service-implements-the-api-risk), [E2](#e2-fixture-reports-are-not-evidence-of-readiness-risk) — no service, no benchmark |

Two observations from lining them up.

**Q8 has moved.** When this page was written, bounded uncertainty was an undefined gap. v2 §8 now specifies `Certain` and `Possible` precisely, with a solver contract. It is no longer a modeling question, only an implementation one — the only entry on this list where that is true.

**Nothing in the register corresponds to [R3](#repository-hygiene).** The requirement register's failure to track the interval profiles is a bookkeeping problem local to this repository, not a semantic decision. It is also the cheapest item on either list to close.


## Implementation sequence

The [documentation guide](README.md) gives the current path:

1. Reproduce the PRO/SOLID adapter and reference oracle
2. Run the exact-interval adapter and its conformance suite
3. Run the interval cohort matcher and its differential suite
4. **Add bounded uncertainty** — shared variables, joint feasibility, explicit certain/possible results, following [formal definition v2](temporal-kg/) §8
5. Extend and benchmark the indexes on representative clinical data, preserving differential checks against the reference implementation

The team-level assignments from [2.4 §8](../addenda/specification-2.4.md) still stand: ontology and domain mapping with review; source adapters with reconciliation; matcher integration; evidence-driven UI. With three people, combine matcher integration and UI.

Any AI-assisted coding must pass these contracts and retain the declared supported profile. No AI provider credentials are needed.
