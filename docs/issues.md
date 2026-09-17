# Outstanding issues

Known gaps and open questions. Every entry cites the specification section that identifies it — nothing here is invented. This page records what the project already says about its own limits, collected in one place.

Issues are grouped by the area they block. Tags mark the kind of work: **[gap]** specified but unimplemented · **[open]** an unresolved design question · **[risk]** something that could be misread or misused.

The [formal definition v2](temporal-kg/) carries its own decision register of eleven questions blocking operationalization. That register and this page are two views of the same open problems; [the mapping between them](#relationship-to-the-v2-decision-register) is at the end.

---

## Ontology and reasoning

### O1. Full OWL reasoning remains open **[gap]**
*Source: [2.4 §3](../addenda/specification-2.4.md), `structural-report.json`*

The point-anchor adapter executes only named subclass closure, the `hasFeature` inverse and the PRO participation chain. Arbitrary OWL expressions, existential witness generation and complete consistency checking are out of scope. `full_owl_reasoning_tested: false`.

The optional [Rust semantic-support profile](semantic-support.md) now checks named class entailment and consistency for a restricted, explicitly generated Horn module, with an independent finite evaluator on every run. Full SULO/import-closure reasoning and identity normalization remain unimplemented. The original point-anchor disjointness checks still cover only its named upper categories.

The separate [claim-description model checker](claim-projection.md) now verifies one constructive model of the complete pinned SULO closure plus its closed information-object encoding. This establishes claim isolation for that encoding; it does not establish full-SULO consistency of the accepted clinical assertion view.

### O2. Broader terminology integration remains open **[gap]**
*Source: [2.4 §4](../addenda/specification-2.4.md)*

Semantic entailment in the point-anchor matcher runs against `ontology/toy-taxonomy.json`, not the RDF graph or a terminology service. The bounded semantic extension now runs Rust over an explicit, constructed class/rule module. Real SNOMED/RxNorm/LOINC mappings and broader OWL fragments still need review and operation-specific conformance evidence. The [reviewed record-mapping compiler](reviewed-record-mappings.md) now supplies explicit version/hash binding, accept/withdraw lifecycle and synthetic Rust-backed query evidence. The [clinical worksheet](../data/clinical-terminology-review.json) identifies four pending source items; no real terminology mapping is accepted. [Pinned source-catalogue and claim reproduction](source-record-catalogue.md) now executes, with a public-demo source catalogue and synthetic mapped-query SQL evidence. [Concrete clinical targets](clinical-terminology-candidates.md) are now proposed with NLM/LOINC evidence and a complete pending norepinephrine pack. [Measurement item-class selectors](reviewed-measurement-mappings.md) now execute synthetically with separate strata and Rust/literal/SQL evidence. [Measurement source auditing](measurement-source-catalogue.md) now verifies pinned dictionary, scalar-claim and clock correspondence with synthetic raw-CSV/Rust/SQL evidence. [Prepared measurement sessions](prepared-measurement-session.md) now reuse checked source/semantic views and temporal networks with digest invalidation and synthetic equivalence evidence. [The authored live pressure route](mapped-pressure-service.md) now integrates prepared mapped batches and invalidates sessions on review changes. [Startup configuration for supplied sources and reviews](configured-pressure-service.md) now executes with request-derived UI settings and restart-bound review inputs. Domain review, row-qualified mappings and representative workload evaluation remain next steps.

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

### T1. The four profiles are not unified **[open]**
*Source: [2.4 §5](../addenda/specification-2.4.md), [exact-interval-profile.md](exact-interval-profile.md)*

Intervals are now implemented, but in a **separate** profile. The point-anchor oracle still uses point anchors and priced relaxation; `exact-interval-1.0` and `interval-cohort-1.0` use exact intervals with no cost model. A trajectory query cannot currently mix them, and the relaxation semantics of the v2.4 matcher have no counterpart in the interval matcher.

The discrete bounded-interval profile now adds shared-variable uncertainty, also as a separate contract with no priced relaxation.

Whether these converge into one matcher, or stay deliberately separate with a documented bridge, is unresolved.

### T2. Allen catalogue partially implemented **[gap]**
*Source: [2.1 §6](../addenda/specification-2.1.md), [exact-interval-profile.md](exact-interval-profile.md)*

The exact-interval evaluator exposes four request operators — `before`, `meets`, directional `overlaps` and bounded `gap` — and internally reports the single basic Allen relation for comparable inputs, including containment, equality and inverses.

The full request interface specified in 2.1 §6 is not exposed. `during`, `starts`, `finishes` and the inverse relations cannot be requested directly.

### T2b. Broader uncertainty and RDF ingestion **[partial]**
*Source: [bounded-temporal-uncertainty.md](bounded-temporal-uncertainty.md), [sulo-owl-time-review.md](sulo-owl-time-review.md) §11*

The discrete bounded profile now implements shared variables, joint feasibility, fixed-witness certain/possible answers, and proof certificates. It accepts validated synthetic JSON and, through [bounded-rdf-1.0](bounded-rdf-ingestion.md), supplied PRO/SOLID graphs under a closed contract. Broader RDF mappings, dense-time semantics, general disjunction, clock reconciliation, and optimized uncertain candidate search remain gaps.

INCOMPARABLE still means missing clock comparability; it is never silently upgraded to a possible temporal realization.

**The semantics are now specified.** [Formal definition v2](temporal-kg/) §8 gives the separated baseline — `Supported(mu)` from source eligibility and OWL entailment, then `Certain(mu)` as `UNSAT(Gamma and not C[mu])` and `Possible(mu)` as `SAT(Gamma and C[mu])` — plus the fixed-witness policy `exists mu forall theta`, evaluated before projecting the patient identifier. §8.1 gives the worked reason for that policy: for two candidate times that can be `(12,36)` or `(36,12)`, a 24-hour query has a qualifying candidate in every assignment, yet neither fixed candidate is certain. The bounded profile now implements the discrete temporal checks. Full OWL support, rational strict inequalities, broader RDF mappings, and the bridge/interface requirements remain open under v2 Q8.

### T3. No time normalizer **[gap]**
*Source: `v21-additions-report.json`*

16 declarative normalization cases exist as expectations. `normalizer_implemented: false`. These are a different set from the 16 matcher cases, which do execute.

### T4. Calendar and age normalization incomplete **[open]**
*Source: [2.4 §5](../addenda/specification-2.4.md), [2.1 §6](../addenda/specification-2.1.md)*

The bounded profile implements signed microsecond offsets between shared variables under one clock. Raw ages, calendar dates, and other relative-time units still need separately declared adapters. A calendar age in years cannot be normalized by assuming a fixed number of seconds per year. A signed relative offset requires an identified anchor and frame, and should not be typed as a SULO Duration when negative. MIMIC-IV `anchor_age` top-codes ages above 89 as 91, which is a de-identification category rather than an age.

### T5. Full bitemporal replay remains incomplete **[partial]**
*Source: [2.3](../addenda/specification-2.3.md), TRP-001 to TRP-011*

The [bounded evidence-selection profile](evidence-selection.md) executes source-as-known and retrospective selection over explicit source-support chains, with per-patient absolute cutoffs and conservative blockers. The [joint extension](joint-evidence-selection.md) now applies this same selection to normalized temporal and semantic fact bundles before checked Rust matching. The 8 broader declarative replay families remain only partially covered: no historical ontology/mapping replay, derived indices, clinical history validation or replay UI is implemented.

### T6. General revision selection remains incomplete **[partial]**
*Source: [2.4 §2](../addenda/specification-2.4.md)*

The original runner consumes one already-selected snapshot. The new [bounded selection layer](evidence-selection.md) prepares such snapshots from normalized assertion bundles and explicit source-support revisions. Joint temporal/semantic bundles now support atomic corrections, but general observation/source mappings and the complete versioning policy in 2.3 remain open.

---

## Ingestion and sources

### S1. Clinical source-to-matcher handoff incomplete **[gap]**
*Source: [2.4 §3](../addenda/specification-2.4.md), ETL-001*

`build_graph` still reads constructed synthetic JSON. The separate [MIMIC inputevents staging adapter](mimic-inputevents-staging.md) now reconciles all rows in the pinned demo 2.2 inputevents extract, retaining unsupported and invalid outcomes. It emits no clinical graph or cohort answer. The [patient-local clock bridge](patient-local-clocks.md) converts explicit bounds; the [recorded-source pipeline](mimic-record-query.md) now connects MIMIC staging through RDF and checked Rust/temporal queries using recorded-label semantics. Choosing justified occurrence bounds and reviewed clinical mappings for MIMIC, then integrating source availability and revision-history coverage, remain open; the staging handoff is still blocked. Other source tables and FHIR ingestion remain unimplemented.

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

### A3. Production workspace remains unimplemented **[gap]**
*Source: [2.2](../addenda/specification-2.2.md), 14 UI requirements*

Wireframes, tokens, storyboard and interaction contracts exist. Local guided cohort selection and the [reviewed pressure inspector](live-pressure-inspector.md) now execute. The broader production workspace and measured usability remain open.

### A4. General evidence workspace remains incomplete **[partial]**
*Source: [2.4 §7](../addenda/specification-2.4.md)*

The guided demo exposes constructed PRO/SOLID evidence and semantic/time costs; the [pressure inspector](live-pressure-inspector.md) exposes actual recorded-source claims, acceptance decisions and treatment role witnesses. The general product evidence workspace remains unimplemented.

### A5. Broader similarity and refinement remain incomplete **[partial]**
*Source: REFINE-001 to REFINE-003, `v21-additions-report.json`*

The [bounded patient similarity engine](patient-similarity.md) and [research workspace](research-workspace.md) now implement pre-index ranking, missingness coverage, hard filters, immutable revisions, two refinements, source inspection and replay on authored patients. Historical reports describing refinement as unimplemented predate this increment. ANN, general selectors, arbitrary event promotion, optionality/deletion and the complete refinement specification remain open; see the row-specific [completion assessment](completion.md).

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

### R3. Current traceability supplements the historical register **[addressed]**

The [completion register](completion.md) now retains the exact 104 original requirements and records evidence, acceptance commands, remaining clauses and external dependencies. Sixteen additional profiles have separate traceability entries. Its checker verifies coverage and evidence digests; it does not execute the referenced acceptance commands or claim full-product completion.

### R2. Legacy ontology drafts are loadable **[risk]**
*Source: [2.4 §8](../addenda/specification-2.4.md)*

`ontology/legacy-2.3/` is non-normative and must not be loaded with the current profile. Nothing mechanically prevents it.

---

## Relationship to the v2 decision register

[Formal definition v2](temporal-kg/) §13 lists eleven decisions that block operationalization, each with a proposed baseline and the evidence required to close it. They overlap substantially with the gaps above, from the other direction: the register asks *what must be decided*, this page records *what is not built*.

The [response to the revision](temporal-kg/revision-response.md) proposes choices and acceptance evidence for every question, including a SULO interface mapping. The [SULO interface conformance register](temporal-kg/sulo-interface.md) now verifies a bounded common query fragment and classifies every original OWL check. General ontology support and identity normalization remain open. [Issue #6](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/issues/6) is closed: the [standalone artifacts and reproduced results](temporal-kg/validation/README.md) are available.

| v2 | Blocked capability | Related issues here |
|---|---|---|
| Q1 | Execution boundary — all metric queries | [O1](#o1-full-owl-reasoning-remains-open-gap) — only a limited closure is executed; whether an external evaluator is permitted decides how far that can go |
| Q2 | Identity, ingestion and temporal joins | [O4](#o4-hasvalue-rdf-term-counting-is-not-owl-datatype-equality-open) — RDF-term counting is not datatype equality; canonicalization policy is undefined |
| Q3 | Clocks and numeric comparisons | [T4](#t4-calendar-and-age-normalization-incomplete-open) — calendar, age and relative offsets; also the strict clock rule the interval matcher already enforces |
| Q4 | Process extents, ongoing or disconnected histories | [T1](#t1-the-four-profiles-are-not-unified-open) — point versus interval profiles; ongoing extents are unasserted in both |
| Q5 | State templates and time-varying domain queries | [C1](#c1-quality-is-modeled-at-patient-level-risk), [T5](#t5-full-bitemporal-replay-remains-incomplete-partial) — patient-level quality and broader replay gaps |
| Q6 | Imports and biomedical mappings | [O2](#o2-broader-terminology-integration-remains-open-gap), [O3](#o3-sulo-pin-is-unreviewed-for-production-open) — toy taxonomy, unreviewed pin |
| Q7 | Evidence selection and as-of answers | [T5](#t5-full-bitemporal-replay-remains-incomplete-partial), [T6](#t6-general-revision-selection-remains-incomplete-partial), [S2](#s2-source-completeness-is-an-input-not-a-check-risk) — bounded selection implemented; broader replay and verified completeness remain open |
| Q8 | Logic/solver interface and complete evaluation | [T2b](#t2b-broader-uncertainty-and-rdf-ingestion-partial) — a discrete temporal solver is implemented; the full OWL/rational-time interface remains open |
| Q9 | Clinical query and application validity | [C2](#c2-not-a-clinical-phenotype-risk), [C3](#c3-no-clinical-validation-gap) — no phenotype validation, no clinical review |
| Q10 | Relaxation catalogue and robust ranking | [T1](#t1-the-four-profiles-are-not-unified-open) — the point-anchor cost model has no interval counterpart |
| Q11 | Scale and production acceptance | [A1](#a1-no-service-implements-the-api-risk), [E2](#e2-fixture-reports-are-not-evidence-of-readiness-risk) — no service, no benchmark |

Two observations from lining them up.

**Q8 has progressed.** v2 §8 specifies `Certain` and `Possible` with a fixed-witness solver contract. The bounded profile implements a discrete specialization and verifies it against finite worlds. The full interface still requires the reasoner/import choices, exact rational semantics, and answer-preservation obligations in Q8; these fixture checks do not close that decision.

**Nothing in the register corresponds to [R3](#repository-hygiene).** The requirement register's failure to track the interval profiles is a bookkeeping problem local to this repository, not a semantic decision. It is also the cheapest item on either list to close.


## Implementation sequence

The [documentation guide](README.md) gives the current path:

1. Reproduce the PRO/SOLID adapter and reference oracle
2. Run the exact-interval adapter and its conformance suite
3. Run the interval cohort matcher and its differential suite
4. Run the bounded uncertainty profile and its finite-world/certificate checks, following the fixed-witness criterion in [formal definition v2](temporal-kg/) §8; run the RDF ingestion suite
5. Review the [v2 response and SULO mapping](temporal-kg/revision-response.md), inspect the supplied [validation evidence](temporal-kg/validation/README.md), run the [SULO interface conformance](temporal-kg/sulo-interface.md) and [bounded selection profile](evidence-selection.md); validate source-specific mappings and history coverage before extending replay
6. Extend and benchmark the admitted fragment on representative clinical data, preserving differential checks against the reference implementation

The team-level assignments from [2.4 §8](../addenda/specification-2.4.md) still stand: ontology and domain mapping with review; source adapters with reconciliation; matcher integration; evidence-driven UI. With three people, combine matcher integration and UI.

Any AI-assisted coding must pass these contracts and retain the declared supported profile. No AI provider credentials are needed.


### Repeated-query performance follow-up

The [complete-result cache](pressure-query-cache.md) now removes repeat execution for identical reviewed pressure controls, with bounded storage, strict invalidation and aggregate measurement reports. The prepared-query increment below now separates immutable, context-bound preparation from query-dependent filtering and temporal classification, and compares results and proofs against the original graph/SQL path. It must preserve original claim acceptance and artifact/backend identity; no broader terminology mapping or new SULO entailment follows from caching.


The first [prepared-query increment](prepared-pressure-queries.md) now executes: it retains checked batch views and source temporal networks across changed controls, with exact differential comparisons and fresh SQL reconciliation. Remaining performance work includes cold preparation, repeated work across pair-covering batches, measured memory use and workloads beyond the current cache budget.


The [configured workload runner](configured-pressure-workload.md) now provides repeated service/HTTP/fresh-reference observations and actual cache admission/reuse counts. Authored evidence covers six verified trials and 36 inspections. Representative clinical workloads, cold-preparation optimization and measured peak memory remain open.
