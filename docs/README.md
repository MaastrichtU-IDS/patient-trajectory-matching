# Documentation guide

The repository contains executable contract packs and specifications for a broader patient trajectory matching product. Read each document's status before treating a described capability as implemented.

## Project documentation

Start here for the overall design and current state.

| Page | Purpose |
|---|---|
| [Architecture](architecture.md) | Design, pipelines, constraint model, PRO/SOLID encoding, temporal semantics |
| [Components](components.md) | What each part does, how to use it, what it depends on |
| [Specifications](specifications.md) | Every contract and design document, what each covers, reading order |
| [Status](status.md) | Executable versus specified, by family and component |
| [Outstanding issues](issues.md) | Known gaps and open questions, each citing its source |
| [Running and validating](validation.md) | Every command, expected output, troubleshooting |

## Contracts and design documents

| Document | Purpose | Status |
|---|---|---|
| [Repository README](../README.md) | Setup, scope, and runnable examples | Current entry point |
| [PRO/SOLID addendum v2.4](../addenda/specification-2.4.md) | Canonical graph, validation, projection, and exemplar matching | Current executable contract within its declared profile |
| [Exact-interval profile](exact-interval-profile.md) | Source/RDF adapter, explicit clocks, endpoint comparisons, and evidence | Executable `exact-interval-1.0`; separate from the point-anchor oracle |
| [Interval cohort matching](interval-cohort-matching.md) | Versioned slot queries, indexed joins, exhaustive reference, and evidence | Executable `interval-cohort-1.0`; exact recorded intervals |
| [Bounded temporal uncertainty](bounded-temporal-uncertainty.md) | Shared-variable constraints, possible/certain bindings, and certificates | Executable `bounded-interval-1.0`; discrete microsecond source profile |
| [Bounded RDF ingestion](bounded-rdf-ingestion.md) | Closed RDF validation, explicit identities, and source evidence | Executable `bounded-rdf-1.0` input route to the bounded matcher |
| [Evidence selection](evidence-selection.md) | Explicit support revisions, cutoff selection and bounded matcher integration | Executable bounded subset; broader replay remains open |
| [Temporal KG formal definition](temporal-kg/) | Formal semantics of the graph, matching and entailment; v2 fixes representation to OWL 2 DL and adds a register of eleven open decisions | Formal specification; full OWL/rational-time query engine remains unimplemented |
| [End-to-end recorded-source query](mimic-record-query.md) | Source admission through checked semantic/temporal matching with full accounting | Executable; 24 tests and a pinned public-demo reproduction |
| [Patient-local clocks](patient-local-clocks.md) | Local calendar normalization, RDF evidence and fixed-witness queries | Executable; 27 tests, clinical source mapping and replay remain open |
| [MIMIC inputevents staging](mimic-inputevents-staging.md) | CSV admission, source reconciliation and demo verification | Executable; 28 tests, no clinical graph or replay export |
| [Joint evidence selection](joint-evidence-selection.md) | Common availability/revision selection for temporal and semantic facts | Executable; 28 tests, historical ontology replay remains open |
| [Checked Rust semantic support](semantic-support.md) | Restricted class entailment and consistency gate before bounded matching | Executable; 22 Rust/reference tests, full SULO/import reasoning remains open |
| [SULO temporal interface](temporal-kg/sulo-interface.md) | Executable mapping contract, paired fixtures and conformance register | Verified bounded query fragment; full OWL mapping remains open |
| [Response to formal definition v2](temporal-kg/revision-response.md) | Recommendations for Q1–Q11, SULO interface mapping, conformance evidence and delivery order | Proposed decisions for review; no profile or ontology change |
| [SULO and OWL-Time review](sulo-owl-time-review.md) | Detailed comparison and recommendations for temporal representation and reasoning | Design guidance; implemented subsets are specified in the profiles above |
| [Temporal precedence](decisions/temporal-precedence.md) | Strict precedence, direct succession, and temporal contact | Proposed decision; no SULO core change adopted |
| [Replay addendum v2.3](../addenda/specification-2.3.md) | Observation/correction semantics and Graphiti comparison | Specified |
| [Workspace addendum v2.2](../addenda/specification-2.2.md) | Patient workspace and interaction design | Specified |
| [Clinical workflow addendum v2.1](../addenda/specification-2.1.md) | Query by example, normalization cases, and MIMIC-IV study plan | Specified |

## Recommended implementation path

1. Reproduce the existing PRO/SOLID adapter and reference oracle using the repository README.
2. Run the exact-interval adapter and its conformance suite, which preserve explicit start/end descriptors, clock scope, PRO role witnesses, and SOLID values.
3. Run the interval cohort matcher and its differential suite: versioned required/distinct slots, exact conjunctive constraints, and patient-episode joins with retained evidence.
4. Run the bounded uncertainty and RDF ingestion profiles with their finite-world, certificate, and graph-validation checks.
5. Review the [v2 response](temporal-kg/revision-response.md), the supplied [standalone validation evidence](temporal-kg/validation/README.md), and the executable [SULO interface conformance](temporal-kg/sulo-interface.md). Run the [evidence-selection profile](evidence-selection.md); source-specific mappings, history coverage and full OWL support remain open.
6. Run [MIMIC inputevents staging](mimic-inputevents-staging.md) and review its reconciliation and blocked handoff. The [recorded-source query pipeline](mimic-record-query.md) now completes the retrospective record-evidence path, including local-clock Rust support and public-demo verification. Clinically interpreted occurrence and source-as-known replay require additional evidence and reviewed policies.
7. Extend and benchmark the admitted fragment on representative clinical data, preserving differential checks against the reference implementation.

The detailed acceptance gates are in section 15 of the review. The Rust/Python stack now includes [checked rustDL integration](semantic-support.md) with its horned-owl parser for a restricted generated module. Broader integration, including py-horned-owl, still needs operation-specific capability checks. Passing the current fixture suite does not establish full OWL or temporal reasoning support.

## Decision and release boundaries

The v2.4 application extension adds classes and individuals, with no new object or datatype properties. Proposed changes to SULO itself are a separate upstream release decision. The precedence document records that proposal without activating its names or axioms in the application profile.

The formal temporal knowledge graph definitions are now included under [temporal-kg/](temporal-kg/). The separately prepared product specification v2.3 remains outside this snapshot. The executable profiles establish only their documented subsets; in particular, the bounded discrete profile does not implement the full OWL/rational-time formal definition.
