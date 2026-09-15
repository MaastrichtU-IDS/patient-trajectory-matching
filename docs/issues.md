# Outstanding issues

Known gaps and open questions. Every entry cites the specification section that identifies it — nothing here is invented. This page records what the project already says about its own limits, collected in one place.

Issues are grouped by the area they block. Tags mark the kind of work: **[gap]** specified but unimplemented · **[open]** an unresolved design question · **[risk]** something that could be misread or misused.

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

### T1. Point anchors only **[gap]**
*Source: [2.4 §5](../addenda/specification-2.4.md)*

The matcher uses point anchors. Proper process intervals, uncertain endpoints, event time versus assertion validity, and snapshot/cutoff selection are specified in [2.1 §6](../addenda/specification-2.1.md) but unimplemented. An occurrence anchor does not establish that a clinical process had zero duration.

### T2. Allen interval catalogue unimplemented **[gap]**
*Source: [2.1 §6](../addenda/specification-2.1.md)*

Seven relations are defined with endpoint semantics; six more are their inverses. Only `before` and endpoint gaps are in H0 scope. The complete catalogue belongs to R1.

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

### R2. Legacy ontology drafts are loadable **[risk]**
*Source: [2.4 §8](../addenda/specification-2.4.md)*

`ontology/legacy-2.3/` is non-normative and must not be loaded with the current profile. Nothing mechanically prevents it.

---

## Implementation sequence

From [2.4 §8](../addenda/specification-2.4.md), for a 3–5 person team:

1. Ontology and domain mapping, with review
2. Source adapters with reconciliation
3. Matcher integration
4. Evidence-driven UI

With three people, combine matcher integration and UI. Any AI-assisted coding must pass these contracts and retain the declared supported profile. No AI provider credentials are needed for this release.
