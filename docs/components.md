# Components

Each component's purpose, interface, dependencies and scope limits. Status labels are defined in [architecture.md](architecture.md).

| Component | Path | Status |
|---|---|---|
| [Reference oracle](#reference-oracle) | `reference_oracle.py` | Executable |
| [PRO/SOLID adapter](#prosolid-adapter) | `patterns/pro_solid.py` | Executable |
| [Acceptance suite](#acceptance-suite) | `patterns/test_pro_solid.py` | Executable |
| [Exact-interval adapter](#exact-interval-adapter) | `patterns/exact_intervals.py` | Executable |
| [Interval cohort matcher](#interval-cohort-matcher) | `patterns/interval_cohort.py` | Executable |
| [Bounded uncertainty matcher](#bounded-uncertainty-matcher) | `patterns/bounded_cohort.py` | Executable |
| [Bounded RDF adapter](#bounded-rdf-adapter) | `patterns/bounded_rdf.py` | Executable |
| [Mixed record queries](mixed-record-query.md) | `patterns/mixed_record_query.py` | Explicitly aligned interval/point record queries with fixed-witness certainty |
| [Measurement point claims](measurement-claims.md) | `patterns/measurement_claims.py`, `patterns/mimic_measurement_import.py` | Recorded scalar/point descriptions and explicit record selection |
| [MIMIC pending claim import](mimic-claim-import.md) | `patterns/mimic_claim_import.py` | Source-to-description provenance and complete import accounting |
| [Patient-local claim projection](local-claim-projection.md) | `patterns/local_claim_projection.py` | Executable local-clock claim selection and reasoning |
| [Structured claims and controlled projection](claim-projection.md) | `patterns/claim_projection.py` | Executable claim-only RDF and explicit analysis-view selection |
| [MIMIC recorded-source query](mimic-record-query.md) | `patterns/mimic_record_query.py` | Executable retrospective record-evidence pipeline |
| [Patient-local clocks](patient-local-clocks.md) | `patterns/patient_local.py` | Executable local bounds, RDF ingestion and queries |
| [MIMIC inputevents staging](mimic-inputevents-staging.md) | `patterns/mimic_inputevents.py` | Executable staging; no matcher export |
| [Ontology profile](#ontology-profile) | `ontology/` | Executable |
| [Contract schemas](#contract-schemas) | `schemas/` | Structurally validated |
| [Fixtures](#fixtures) | `examples/` | Executable inputs and specified cases |
| [Verification reports](#verification-reports) | `verification/` | Generated |
| [UI assets](#ui-assets) | `ui/` | Specified |
| [Evaluation plan](#evaluation-plan) | `evaluation/` | Specified |
| [Dataset plans](#dataset-plans) | `data/` | Specified |

---

## Reference oracle

**Path:** `reference_oracle.py` · **Status:** Executable · **Dependencies:** none (standard library only)

Exhaustive three-slot exemplar matcher. Given a case, a pattern AST and a taxonomy, it returns an acceptance decision with a decomposed cost.

```python
from reference_oracle import evaluate
result = evaluate(case, pattern, taxonomy)
```

**Inputs**

| Argument | Shape |
|---|---|
| `case` | `patient_id`, `episode_id`, `age`, `source_search_complete`, `events[]`, optional `budget_override` |
| `pattern` | Exemplar AST: `scope`, `events`, `constraints`, `relaxations`, `budget` |
| `taxonomy` | JSON subclass hierarchy; the oracle's only reasoning input |

**Output:** `exact_status`, `accepted_as`, `total_cost`, `relaxed_constraint_ids`, `baseline_event_id`, `binding`, `reason_codes`, `components`, `clinical_knowledge_status`.

**Scope limits.** Three required, distinct slots (baseline, exposure, follow-up). Exact baseline and follow-up point times; bounded uncertain exposure point times. It rejects unsupported patterns rather than silently approximating them. It is not a general matcher and not an OWL reasoner.

**Why it has no dependencies.** It is the stable core of the system, imported by the adapter and never importing it. This keeps matcher semantics independent of the RDF stack and lets it run on Python 3.10 through 3.13.

---

## PRO/SOLID adapter

**Path:** `patterns/pro_solid.py` · **Status:** Executable · **Dependencies:** `rdflib`, `pyshacl`, `reference_oracle`

Five-stage pipeline from synthetic source rows to a match result. See [architecture.md §2](architecture.md#2-pipeline).

```sh
python -m patterns.pro_solid [--source ROWS] [--manifest M] [--graph TTL] [--output DIR]
```

| Flag | Default | Meaning |
|---|---|---|
| `--source` | `examples/pro-solid/source-rows.json` | Synthetic rows to build from |
| `--manifest` | `examples/pro-solid/manifest.json` | Query scope, age, snapshot, clock origin |
| `--graph` | — | Validate and project existing Turtle instead of building from rows |
| `--output` | `verification/pro-solid-run` | Where the four outputs are written |

**Outputs:** `graph.ttl`, `matcher-case.json`, `evidence.json`, `match.json`.

**Public functions**

| Function | Purpose |
|---|---|
| `build_graph(rows)` | Source rows to instance RDF |
| `materialize(source)` | Limited closure into a separate graph |
| `validate_graph(source)` | SHACL plus procedural checks; raises `ContractError` |
| `project(source, manifest)` | Returns `(case, evidence)` |

**Error model.** All profile failures raise `ContractError` with a named code such as `SOLID_LITERAL_PROPERTY`, `ROLE_CARDINALITY:PatientRole`, `UNSUPPORTED_OR_UNZONED_TIME` or `MEASUREMENT_SUBJECT_MISMATCH`. A `ContractError` is a profile violation, never a statement that the patient does not match.

**Supported profile**

- Event kinds: `measurement`, `administration`
- Status: `performed` only
- Measurement codes: `ex:Creatinine`
- Drug codes: `ex:DrugA`, `ex:DrugAChild`, `ex:DrugB`
- Units: `mg/dL`, `mg/L` (converted by an exact factor of 0.1)
- Times: complete datetime, explicit known offset, at most six fractional digits

**Scope limits.** A concrete source-adapter example, not a raw MIMIC or FHIR importer. Age derivation from graph data and source-completeness verification are not implemented; both arrive as manifest inputs.

**Runtime note.** The module sets `rdflib.NORMALIZE_LITERALS = False` at import to retain source literal spelling, and restores the flag after pySHACL mutates it. In a server, isolate this loader or configure the equivalent at startup.

---

## Acceptance suite

**Path:** `patterns/test_pro_solid.py` · **Status:** Executable · **42 tests**

```sh
python -m patterns.test_pro_solid   # writes verification/v24-pro-solid-report.json, nonzero exit on failure
```

Coverage groups:

| Group | Examples |
|---|---|
| Projection and schema | `test_exact_normalized_trajectory_and_existing_event_schema` |
| PRO derivation | `test_pro_chain_is_derived_without_losing_role_binding`, `test_generic_participation_is_insufficient` |
| Role integrity | `test_care_provider_does_not_become_patient`, `test_role_reuse_across_processes`, `test_wrong_role_type` |
| SOLID discipline | `test_literal_directly_on_person_rejected`, `test_specialized_literal_property_rejected` |
| Values and units | `test_missing_value_rejected`, `test_multiple_units_rejected`, `test_unit_conversion_preserves_long_decimal` |
| Time | `test_unzoned_datetime_not_silently_utc`, `test_unknown_timezone_marker_rejected`, `test_timezone_equivalence` |
| Identity | `test_identical_observation_values_remain_distinct_records`, `test_duplicate_record_ids_rejected` |
| Matching | `test_subclass_entailment_is_exact`, `test_reviewed_semantic_relaxation_has_cost`, `test_semantic_relaxation_can_be_disabled` |
| Evidence | `test_source_row_hash_and_snapshot_retained`, `test_changed_value_changes_evidence_identifier` |

The 16 oracle cases and 7 property checks run separately via `reference_oracle.py`.

---

## Exact-interval adapter

**Path:** `patterns/exact_intervals.py` · **Status:** Executable · **Profile:** `exact-interval-1.0` · **Dependencies:** `rdflib`, `pyshacl`

Source rows to interval RDF to pairwise temporal evaluation. A separate profile alongside the v2.4 point-anchor contract, sharing the pinned SULO core and adding no new object or datatype properties.

```sh
python -m patterns.exact_intervals [--graph examples/exact-interval/graph.ttl]
python -m patterns.test_exact_intervals    # 51 tests
```

**Outputs** to `verification/exact-interval-run/`:

| File | Contents |
|---|---|
| `graph.ttl` | Canonical named PRO/SOLID instances, literal spelling preserved |
| `intervals.json` | Normalized exact intervals and evidence identifiers |
| `evidence.json` | Source, role, endpoint, duration, clock, snapshot, implementation bindings |
| `comparisons.json` | Requested comparisons, statuses, arithmetic rules, evidence references |

**Representation.** A process has exactly one `atTime` occurrence interval, typed `ei:ExactOccurrenceInterval`, with distinct typed start and end parts and **no scalar `hasValue` on the interval itself**. Boundaries are `ei:ExactStartTime` / `ei:ExactEndTime`, each with one `xsd:dateTimeStamp` and a direct Second unit. An optional `ei:ElapsedDuration` carries one decimal value and one supported unit. A `ei:ClockBinding` refers to an `ei:TemporalReferenceSystem`.

**Operators.** `before`, `meets`, `overlaps` (directional) and `gap` with inclusive integer bounds. See [architecture.md §5](architecture.md#5-temporal-architecture) for exact definitions.

**Error model.** Invalid profile input exits with **code 2** and an `INVALID_INPUT` diagnostic. An unsuccessful invocation does not replace existing output files, so consumers must check the exit code before reading output.

**Scope limits.** Constructed data demonstrating process timing and patient participation. Not a clinical infusion, medication, specimen, MIMIC or FHIR mapping. Does not change the point-anchor oracle and does not provide generalized cohort interval matching.

---

## Interval cohort matcher

**Path:** `patterns/interval_cohort.py` · **Status:** Executable · **Profile:** `interval-cohort-1.0` · **Dependencies:** exact-interval projection

Conjunctive slot queries over recorded intervals, across patient episodes in one graph and snapshot. Adds no RDF classes or properties and leaves the v2.4 API and oracle independent.

```sh
python -m patterns.interval_cohort
python -m patterns.interval_cohort --engine reference --output verification/interval-cohort-run/reference.json
python -m patterns.test_interval_cohort    # 18 tests
```

Also accepts `--source`, `--graph`, `--manifest`, `--query` and `--output`.

**Two engines, one semantics**

| Engine | File | Role |
|---|---|---|
| Indexed | `interval_cohort.py` | Production path, with index pruning |
| Reference | `interval_cohort_reference.py` | Exhaustive, independently written |

The suite checks them **differentially**. The two must agree on the semantic result while reporting different execution counters. This is the repository's strongest correctness property: the optimization is held to an independent specification of the same answer, rather than to its own output.

**Query contract.** [`schemas/interval-cohort.schema.json`](../schemas/interval-cohort.schema.json) plus semantic checks in `validate_query`. Versioned required and distinct slots, exact conjunctive constraints, patient-episode joins.

**Outcomes**

| Verdict | Meaning |
|---|---|
| `MATCH` | Some binding satisfies every constraint |
| `INCOMPARABLE` | No match, but a binding has no false constraint and an incomparable edge |
| `NO_RECORDED_MATCH` | Otherwise |

A `MATCH` episode can still carry unresolved bindings, which stay visible in the result.

**Worked example.** Three constructed patients: P1 matches (bindings A→D and C→D), P2 has no recorded match (its infusion follows its collection), P3 is incomparable (its candidate events use different clock resources).

**Scope limits.** Exact recorded intervals only. A temporal edge requires the same clock resource and descriptor; equal coordinate values do not establish a mapping. No comparison crosses episodes. Patients with no projected events are outside the result scope — there is no external cohort roster. Profile or schema failure exits 2 and does not clear an older output file. The separate bounded profile below adds uncertainty. Clinical source mapping and patient-to-patient similarity remain future work.

---

## Bounded uncertainty matcher

**Path:** `patterns/bounded_cohort.py` · **Status:** Executable · **Source profile:** `bounded-interval-1.0` · **Query profile:** `bounded-interval-query-1.0`

```sh
python -m patterns.bounded_cohort
python -m patterns.test_bounded_intervals  # 22 tests
```

`bounded_intervals.py` validates the synthetic source schema, emits PRO/SOLID RDF, and compiles bounded variables and source difference constraints. `temporal_stn.py` computes exact-integer closure and proof paths. The matcher enumerates distinct named bindings within patient episodes, checks whole-pattern possibility, and tests fixed-witness certainty against the original feasible source set. `bounded_reference.py` supplies an independent finite-world oracle for small test cases.

**Outputs:** `graph.ttl` and `result.json` in `verification/bounded-interval-run/`. Results retain source, role, variable, and constraint evidence; feasible timelines, counterexamples, entailment paths, and negative cycles explain decisions. Source inconsistency exits 2 with `INCONSISTENT_SOURCE`; other input violations produce `INVALID_INPUT`.

**Scope:** bounded integer microseconds and conjunctive difference constraints; shared anchors and the four existing temporal operators. The original input route accepts JSON; the bounded RDF adapter below accepts supplied graphs under a closed contract. Dense time, general OWL reasoning, and optimized uncertainty search are pending. See the [full contract](bounded-temporal-uncertainty.md).

---

## Bounded RDF adapter

**Path:** `patterns/bounded_rdf.py` · **Status:** Executable · **Input profile:** `bounded-rdf-1.0`

```sh
python -m patterns.bounded_rdf
python -m patterns.bounded_rdf --graph verification/bounded-rdf-run/graph.ttl
python -m patterns.test_bounded_rdf  # 21 tests
```

Reads arbitrary named instance IRIs with explicit identifiers, validates PRO roles, bounds, units, clock scope, provenance, and complete triple coverage, then uses the shared bounded temporal compiler. Outputs the supplied graph and match results with original resources, literal spellings, and source-hash declarations. The synthetic JSON export and RDF reload routes produce identical complete results.

**Scope:** the supported class vocabulary and closed structure in the [RDF contract](bounded-rdf-ingestion.md). No blank nodes, expanded closures, arbitrary ontology axioms, or general OWL identity reasoning. Hash declarations are retained without claiming verification against unavailable source rows. Invalid inputs exit 2; inconsistent networks retain RDF-backed negative-cycle evidence.

---

## Ontology profile

**Path:** `ontology/` · **Status:** Executable

| File | Contents |
|---|---|
| `vendor/sulo-0.2.14.ttl` | SULO upper ontology, vendored for offline use |
| `sulo-pin.json` | Version, SHA-256, source URL, declared reasoning profile |
| `pro-solid-profile.ttl` | 25 application classes plus disjointness axioms |
| `pro-solid-shapes.ttl` | 10 SHACL node shapes |
| `exact-interval-profile.ttl` | `ei:` interval, boundary, clock and duration classes |
| `exact-interval-shapes.ttl` | SHACL shapes for the interval profile |
| `bounded-interval-profile.ttl` | Shared variables, bounds, and constraint binding classes; no new properties |
| `bounded-rdf-profile.ttl` | Profile, variable, and constraint identifier classes for external RDF ingestion |
| `toy-taxonomy.json` | The oracle's subclass hierarchy |
| `toy.ofn` | OWL functional-syntax rendering of the toy hierarchy |
| `legacy-2.3/` | **Non-normative** archived drafts |

The `ex:` prefix denotes `https://example.org/trajectory/toy/`. Instance data uses `https://example.org/trajectory/data/`.

SULO is checked against its digest at load; a mismatch raises `SULO_PIN_MISMATCH`. Declared reasoning profile: named subclass closure, `hasFeature` inverse, PRO participation chain. No full OWL consistency or completeness claim.

The archived drafts in `legacy-2.3/` must not be loaded with the current profile.

---

## Contract schemas

**Path:** `schemas/` · **Status:** Structurally validated; no service implements them

| File | Contents |
|---|---|
| `contracts.schema.json` | JSON Schema draft 2020-12, 22 definitions |
| `openapi.json` | OpenAPI 3.1, 14 paths, version 2.0.0 |
| `interval-cohort.schema.json` | Interval cohort query contract — **executable and enforced** |
| `bounded-interval.schema.json` | Bounded source and query contracts — **executable and enforced** |

Paths cover capabilities, dataset snapshots, semantic bundles, ingestion jobs, pattern validation and storage, cohort match jobs, job lifecycle, results, evidence and exports.

**Important.** These describe the pre-PRO/SOLID event model and contain no role or bearer vocabulary. The projection stage bridges the 2.4 graph to these 2.0 DTOs. See [architecture.md §8](architecture.md#8-contract-surfaces-and-their-versions). Cross-field invariants still require semantic validation; the schema alone does not enforce them.

---

## Fixtures

**Path:** `examples/` · **Status:** mixed

| File | Status | Contents |
|---|---|---|
| `exemplar.pattern.json` | Executable | Canonical exemplar AST; all constraints hard unless explicitly relaxed |
| `cases.json` | Executable | 16 cases with expected acceptance, costs and baseline choices |
| `pro-solid/source-rows.json` | Executable | Synthetic rows for the adapter |
| `pro-solid/manifest.json` | Executable | Scope, age, snapshot, completeness, clock origin |
| `pro-solid/graph.ttl` | Executable | Committed copy of the generated graph |
| `pro-solid/measurement-bindings.rq` | Executable | SPARQL projection retaining patient and result role bindings |
| `exact-interval/` | Executable | Source rows, manifest, comparison requests, committed graph |
| `interval-cohort/` | Executable | Source rows, manifest and query for the three-patient example |
| `bounded-interval/` | Executable | Correlated variable source and four-patient query example |
| `cohort-request.json`, `result-page-C05.json`, `evidence-C05.json`, `manifest-C05.json` | Structural | Worked request, result, evidence and manifest examples |
| `qbe-profile-2.1.json`, `refinement-session-2.1.json` | Specified | Query-by-example profile and refinement session |
| `time-normalization-cases-2.1.json` | Specified | 16 normalization expectations, no normalizer implemented |
| `temporal-replay-cases-2.3.json` | Specified | 8 declarative case families |

The 16 matcher cases and the 16 normalization expectations are **different sets**. Only the matcher cases execute.

---

## Verification reports

**Path:** `verification/` · **Status:** Generated

| File | Produced by |
|---|---|
| `reference-report.json` | `reference_oracle.py` |
| `v24-pro-solid-report.json` | `patterns/test_pro_solid.py` |
| `exact-interval-report.json` | `patterns/test_exact_intervals.py` |
| `pro-solid-run/` | `patterns/pro_solid.py` |
| `structural-report.json` | Authoring-time structural validation |
| `v21-` / `v22-` / `v23-additions-report.json` | Per-version structural checks |
| `v24-release-manifest.json` | 9 SHA-256 digests over adapter inputs |

Reports certify this pack's internal consistency only. `v24-pro-solid-report.json` records `production_readiness_claim: false` and the runner's Python version, so it changes whenever the interpreter patch version changes.

The release manifest hashes **inputs**, not reports. CI verifies all nine.

---

## UI assets

**Path:** `ui/` · **Status:** Specified. No running interface exists.

| File | Contents |
|---|---|
| `wireframe-overview.png`, `wireframe-refinement.png` | Annotated wireframes |
| `design-tokens-2.2.json` | Design tokens |
| `storyboard-2.2.json` | Eight-state storyboard |
| `interaction-contracts-2.2.json` | Interaction contracts |
| `temporal-replay-2.3.json` | Replay controls and identity |

Specified in [addendum 2.2](../addenda/specification-2.2.md) and [2.3](../addenda/specification-2.3.md). 14 UI requirements, gate AC16.

---

## Evaluation plan

**Path:** `evaluation/graphiti-comparison-2.3.json` · **Status:** Specified. No results measured.

A planned six-hour synthetic comparison protocol against Zep/Graphiti. This is an experimental design, not a benchmark result. No Graphiti numbers exist in this repository.

---

## Dataset plans

**Path:** `data/` · **Status:** Specified, except demo inventory and inputevents staging evidence

| File | Contents |
|---|---|
| `source-plan.json` | Dataset roles |
| `mimic-demo-inspection.json` | Inspected demo inventory with hashes of decompressed CSVs |
| `mimic-inputevents-demo-pin.json` | Published compressed hashes and observed row counts for staging reproduction |
| `full-mimic-study-plan-2.1.json` | Full MIMIC-IV study plan |

No MIMIC patient rows are redistributed here. MIMIC-IV carries its own access requirements. The full study has not been run.

## Source mixed-query pipeline

[`patterns/source_mixed_query.py`](../patterns/source_mixed_query.py) connects both pending-claim importers to mixed record matching. It prepares an undecided review template, validates explicit hash-bound selection/alignment, retains every requested stay, and gates complete cohort output on independent raw-row SQLite comparison. [`source_mixed_reference.py`](../patterns/source_mixed_reference.py) shares admission and selection but implements its own temporal/scalar joins. See [the contract and two-step CLI](source-mixed-query.md).

## Clinical source preflight

[`patterns/clinical_source_preflight.py`](../patterns/clinical_source_preflight.py) streams candidate measurement coverage, reuses the current admission policies, retains complete aggregate row accounting, and screens source-file and per-stay count limits. It produces no claims, acceptance decisions, clock alignment or cohort answer. The [pinned public-demo report and proposed review plan](clinical-source-preflight.md) establish the need for indexed window selection before a real-source mixed query.

## Indexed source windows

[`patterns/indexed_source_windows.py`](../patterns/indexed_source_windows.py) streams original source rows into a patient/stay/item/time index, checks every anchor window against a direct datetime reference, retains blocked/empty anchors, and exports an individually requested count-bounded anchor as pending claims. It preserves original hashes and row numbers. [The contract and aggregate demo report](indexed-source-windows.md) distinguish window completeness, count bounds, batch validation and explicit acceptance.

## Partitioned window execution

[`patterns/partitioned_window_query.py`](../patterns/partitioned_window_query.py) covers each complete exact-record window with batches of at most 16 points, checks record and pair coverage, validates consistent explicit review, runs the existing mixed matcher, and reconciles merged bindings with an unpartitioned raw-row SQL reference. It retains every batch, anchor and stay and withholds complete cohort membership on any blocked anchor. [The contract](partitioned-window-query.md) limits this decomposition to independent exact source records and reports public-demo partition plans separately from synthetic execution.

## Unique source-claim review

[`patterns/unique_claim_review.py`](../patterns/unique_claim_review.py) builds a package of unique claims with source evidence and affected batches/anchors, compiles explicit histories and patient calendar decisions into the existing partition policies, and validates the complete expansion before execution. It records reviewer attribution without authenticating identity and requires all claim/calendar reviews to be resolved for its execution command. [The contract](unique-claim-review.md) includes a runnable synthetic review and aggregate validation of all three pending demo packages.

## Reviewed source-fidelity execution

[`patterns/source_fidelity_audit.py`](../patterns/source_fidelity_audit.py) rereads original CSV files and independently checks claim content, source evidence, identity and clock-origin witnesses. It makes no decisions. [`patterns/reviewed_source_query.py`](../patterns/reviewed_source_query.py) requires a separate declaration bound to the exact package and audit, materializes explicit unique-claim decisions and patient-calendar declarations, then invokes the existing partition executor. [The arterial demonstration](reviewed-arterial-demo.md) publishes aggregate execution and reconciliation evidence, with automated record fidelity distinguished from clinical review.

The [three-stratum comparison](reviewed-pressure-strata.md) now adds separately declared non-invasive and alternate arterial-label executions, complete SQL reconciliation and aggregate coverage counts. Three further tests verify their provenance, common query criteria and candidate-plan accounting.

[`patterns/verify_pressure_overlap.py`](../patterns/verify_pressure_overlap.py) freshly reproduces all three reviewed pressure queries, validates common source/query/population scope and compares membership at patient, stay and treatment-segment levels. The [overlap report](pressure-cohort-overlap.md) contains only aggregate counts and provenance hashes, with incomplete inputs preventing publication.

The [prepared pressure executor](prepared-pressure-queries.md) retains checked batch views across changed controls, reevaluates temporal eligibility and preserves the complete per-anchor SQL gate.

The [completed pressure-query cache](pressure-query-cache.md) reuses exact completed results after source, review and implementation checks; new controls use prepared views with fresh temporal evaluation and the SQL oracle.

The [live pressure inspector](live-pressure-inspector.md) adds immutable reviewed-window sessions, bounded query controls and complete SQL-reconciled execution through local asynchronous HTTP jobs. The UI exposes actual source decisions and treatment PRO witnesses; it retains the previous result on failure and admits no wider source window without another review.


## Reviewed record-mapping compiler

[`patterns/reviewed_record_mappings.py`](../patterns/reviewed_record_mappings.py) validates four closed input documents and compiles only explicitly accepted source-record subclass implications. It binds review and implementation evidence, blocks pending/withdrawn proposals, and passes a caller-supplied, exactly bound source policy through the existing mixed matcher. [The runbook](reviewed-record-mappings.md) includes a reproducible synthetic example and the pending clinical handoff.


## Source-verified mapping catalogue

[`patterns/source_record_catalogue.py`](../patterns/source_record_catalogue.py) binds a mapping source catalogue to pinned CSV/dictionary bytes and reproduces supplied interval claims and clock origins before reviewed mapping execution. It retains separate source and mapping policies. [The runbook](source-record-catalogue.md) documents bounded subset semantics, public-demo catalogue evidence and synthetic Rust/SQL verification.


## Reviewed measurement selectors

[`patterns/reviewed_measurement_mappings.py`](../patterns/reviewed_measurement_mappings.py) checks reviewed catalogue implications with Rust/reference witnesses, then runs each supported source item as a separate mixed-query stratum with identical baseline/follow-up unit spelling. It preserves source policies and suppresses combined membership on incomplete execution. [The profile](reviewed-measurement-mappings.md) explains the distinction between catalogue-rule reasoning and patient measurement graphs.

### Measurement source catalogue and claim audit

[`patterns/measurement_source_catalogue.py`](../patterns/measurement_source_catalogue.py) reproduces dictionary labels, supplied scalar claims and patient clock origins from three pinned CSV files before calling the reviewed measurement selector. The outer context binds source-audit and query evidence without changing acceptance policies. [The runbook](measurement-source-catalogue.md) defines subset fidelity, exact lexical preservation and the limits of synthetic CSV/SQL checks.

### Prepared measurement sessions

[`patterns/prepared_measurement_session.py`](../patterns/prepared_measurement_session.py) admits a successful source-audited measurement query and retains private record views, a checked selector plan and a shared temporal network. Changed numeric/temporal controls use the existing prepared evaluator. Before/after digest checks invalidate changed sources or implementations; review changes require a new session. [The runbook](prepared-measurement-session.md) documents snapshot lifetime, budgets and batch-integration limits.

### Mapped pressure service

[`patterns/mapped_pressure_session.py`](../patterns/mapped_pressure_session.py) binds a reviewed single-item selector to the original pressure batch envelope and delegates batch work to source-audited prepared measurement sessions. [`demo/mapped_pressure.py`](../demo/mapped_pressure.py) adds mapping-file lifecycle checks to the existing job/result-cache interface; [`demo/serve_mapped_pressure.py`](../demo/serve_mapped_pressure.py) exposes the authored example through the existing localhost routes. [The profile](mapped-pressure-service.md) records HTTP equivalence and explicit public-data limitations.


### Startup pressure configuration

[`patterns/pressure_service_config.py`](../patterns/pressure_service_config.py) validates and freezes the source paths, bounded pressure request and review inputs. The mapped launcher adds the configuration context to session provenance and uses [`demo/configured_pressure.js`](../demo/configured_pressure.js) for request-derived page labels/defaults/limits. [The runbook](configured-pressure-service.md) specifies startup and restart semantics.


### Configured workload runner

[`demo/benchmark_configured_pressure.py`](../demo/benchmark_configured_pressure.py) executes bounded query matrices through the configured HTTP service, compares every complete result and anchor inspection with fresh mapped execution, and writes aggregate timing, operation-count and cache evidence. [The method](configured-pressure-workload.md) distinguishes prepared evaluation, retained-result reuse and recomputation.
