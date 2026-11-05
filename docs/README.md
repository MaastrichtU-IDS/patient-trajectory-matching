# Documentation guide

The repository holds executable research profiles and the specifications of a broader patient trajectory matching product. Read each page's status line before treating a described capability as implemented. Current test counts and requirement coverage live in [status](status.md) and [completion](completion.md), which are checked; this index carries no counts.

## Start here

| Page | Purpose |
|---|---|
| [Design document](design.md) | The project as it stands — layers, semantics, data, integrity, deployment — and what remains |
| [Engine design](engine-design.md) | How results are generated today, matching at scale, similarity on coded terms, embeddings, query complexity |
| [Architecture](architecture.md) | Design, pipelines, constraint model, PRO/SOLID encoding, temporal semantics, matching costs |
| [Components](components.md) | What each part does, how to use it, what it depends on |
| [Status](status.md) | What executes, what is specified, and what has been verified |
| [Validation](validation.md) | Every executable component, how to run it, and what correct output looks like |
| [Specifications](specifications.md) | Every contract and design document, what each covers, and a reading order |
| [Completion](completion.md) | The 104 original requirements mapped to evidence and remaining clauses |
| [Issues](issues.md) | Known gaps and open questions, each tied to the section that identifies it |

## Core temporal profiles

The engine in `patterns/`, one profile per page.

| Page | Covers |
|---|---|
| [Exact occurrence intervals](exact-interval-profile.md) | `exact-interval-1.0`: pairwise temporal operators over recorded intervals |
| [Exact interval cohort matching](interval-cohort-matching.md) | `interval-cohort-1.0`: conjunctive slot queries across patient episodes |
| [Extended interval queries](extended-interval-query.md) | Operators beyond before, meets, overlaps and bounded gap |
| [Bounded temporal uncertainty](bounded-temporal-uncertainty.md) | `bounded-interval-1.0`: possible and certain matches over bounded times |
| [Bounded RDF ingestion](bounded-rdf-ingestion.md) | `bounded-rdf-1.0`: validated graph input for the bounded matcher |
| [Evidence selection](evidence-selection.md) | `evidence-selection-1.0`: which records a bounded query may use |
| [Joint temporal and semantic selection](joint-evidence-selection.md) | Temporal records and semantic assertions selected over the same revision chains |
| [Checked semantic support](semantic-support.md) | rustDL-derived class membership feeding the bounded matcher |
| [State validity and coverage](state-validity-coverage.md) | When repeated measurements do and do not establish a continuous state |
| [Patient-local clocks](patient-local-clocks.md) | Source, RDF and query profiles on each patient's own clock |
| [Robust temporal relaxation](robust-temporal-relaxation.md) | Relaxations that hold in every feasible timeline |
| [Explicit metric relaxation](extended-relaxation.md) | Priced metric changes for extended interval queries |
| [Custom relaxation catalogue](custom-relaxation-catalogue.md) | Explicitly permitted metric changes in the journey builder |

## Claims and the record model

| Page | Covers |
|---|---|
| [Structured claims and projection](claim-projection.md) | `claim-projection-1.0`: claims, acceptance policies and controlled assertion projection |
| [Patient-local claim projection](local-claim-projection.md) | The same projection on patient-local clocks |
| [Recorded measurement claims](measurement-claims.md) | A bounded `chartevents` importer and scalar/point claim store |
| [Mixed record queries](mixed-record-query.md) | Treatment intervals and measurement points in one trajectory |

## MIMIC and source pipeline

| Page | Covers |
|---|---|
| [MIMIC-IV inputevents staging](mimic-inputevents-staging.md) | Admission and reconciliation of recorded input components |
| [Staging to matcher handoff](mimic-matcher-handoff.md) | From staged records to matcher input |
| [MIMIC records as pending claims](mimic-claim-import.md) | Staged records imported as claims awaiting review |
| [End-to-end recorded queries](mimic-record-query.md) | Admission through RDF ingestion to checked query results |
| [Source mixed queries](source-mixed-query.md) | Source imports, explicit review and SQL-checked mixed queries |
| [Source record catalogues](source-record-catalogue.md) | Source-verified catalogues of what a source holds |
| [Clinical source preflight](clinical-source-preflight.md) | Candidate sources and coverage checks before querying |
| [Indexed source windows](indexed-source-windows.md) | Source selection and pending anchor batches |
| [Partitioned window execution](partitioned-window-query.md) | Exact-record windows executed by verified partition plans |
| [Review each claim once](unique-claim-review.md) | A review package of unique source claims |

## Terminology and mapping review

| Page | Covers |
|---|---|
| [Clinical terminology candidates](clinical-terminology-candidates.md) | Proposed mappings for domain review; none accepted |
| [Reviewed record mappings](reviewed-record-mappings.md) | Compiled record mappings with synthetic verification |
| [Reviewed measurement selectors](reviewed-measurement-mappings.md) | Measurement mappings verified through Rust, literal and SQL paths |
| [Measurement source catalogue](measurement-source-catalogue.md) | Catalogue and claim audit for measurement sources |
| [Clinical review workflow](clinical-review-workflow.md) | How a review package is recorded and validated |

## Pressure and measurement services

The reviewed blood-pressure query service in `services/`, presented by `demos/presentation/`.

| Page | Covers |
|---|---|
| [Reviewed arterial-pressure demonstration](reviewed-arterial-demo.md) | A complete exact-record query on the pinned MIMIC-IV demo stratum |
| [Three reviewed pressure strata](reviewed-pressure-strata.md) | The same query on each separately reviewed measurement item |
| [Pressure cohort overlap](pressure-cohort-overlap.md) | How the three strata's cohorts overlap |
| [Prepared measurement sessions](prepared-measurement-session.md) | Source-audited sessions prepared once and queried many times |
| [Reusing batch preparation](prepared-pressure-queries.md) | Checked batch views kept across changed queries |
| [Reusing completed queries](pressure-query-cache.md) | A bounded result cache for repeated controls |
| [Live pressure inspector](live-pressure-inspector.md) | Background jobs and the interactive pressure page |
| [Live mapped pressure service](mapped-pressure-service.md) | The opt-in HTTP service over reviewed mappings |
| [Configured mapped pressure service](configured-pressure-service.md) | Startup configuration for supplied records and reviews |
| [Configured workload verification](configured-pressure-workload.md) | Sequential workloads comparing prepared, cached and fresh execution |
| [Workload memory measurement](pressure-workload-memory.md) | Peak memory across the service's process tree (Linux) |

## Research workspace

The product surface in `app/`.

| Page | Covers |
|---|---|
| [Research workspace](research-workspace.md) | The authored patient-to-cohort journey: rank, refine, compare, inspect, replay |
| [Patient similarity](patient-similarity.md) | Bounded similarity and immutable refinement |
| [Guided patient journey](guided-patient-journey.md) | The `/journey` route from reference patient to explained cohort |
| [Configurable trajectory builder](configurable-trajectory-builder.md) | Two- and three-event temporal questions in `/journey` |
| [Temporal workspace](temporal-workspace.md) | Temporal uncertainty in the workspace |
| [Interval query editor](interval-query-editor.md) | Editable Allen, gap, duration and overlap constraints |
| [Recorded evidence in the journey](guided-recorded-evidence.md) | Recorded treatment and measurement evidence connected to `/journey` |
| [Recorded query by example](recorded-query-by-example.md) | Ranking and replay from a recorded reference segment |
| [Durable recorded workspace](durable-recorded-workspace.md) | Keeping completed jobs and accepted intents across restarts |
| [Recorded workflow interface](recorded-workflow-ui.md) | The recorded section of `/journey`, verified end to end |

## Evaluation

| Page | Covers |
|---|---|
| [Recorded workflow evaluation](recorded-workflow-evaluation.md) | One recorded question evaluated, with its clinical-review limits |
| [Integrated recorded evaluation](integrated-recorded-evaluation.md) | The whole recorded workflow evaluated on the pinned demo |
| [Distinct-variable evaluation](clinical-feature-source-evaluation.md) | Supplemental clinical variables extracted and verified against source |
| [Retrieval evaluation](retrieval-evaluation.md) | Held-out retrieval mechanics and authored scaling |
| [Integrated workflow review](completion-review.md) | Technical review of the recorded-pattern, feature, durable-job and owner-access additions |

## Deployment and release

| Page | Covers |
|---|---|
| [Deployment](deployment.md) | The container, Compose and Helm for the authored research prototype |
| [Reproducible local release](research-release.md) | Single-owner private release: credentials, backups, restoration |
| [Recorded workflow delivery](recorded-workflow-delivery.md) | Delivery and acceptance of the recorded workflow, including the demo cohort |

## Specifications, decisions and ontology work

| Page | Covers |
|---|---|
| [Specification addenda 2.1–2.4](../addenda/) | The product specification text the requirements cite |
| [Design assets](specification/ui/) | Specified wireframes, tokens, storyboard and interaction contracts; no implementation |
| [Evaluation protocol](specification/evaluation/) | The planned Graphiti comparison; no measurements |
| [Decision: relaxation cost models](decisions/relaxation-cost-models.md) | Accepted: one reported contract, two evaluators |
| [Decision: source completeness](decisions/source-completeness.md) | Accepted: completeness derived from declared coverage |
| [Proposed decision: temporal precedence](decisions/temporal-precedence.md) | Precedence and direct succession, not adopted |
| [SULO temporal representation review](sulo-owl-time-review.md) | Developer review and recommendations |
| [SULO development proposal](sulo-development/README.md) | Pattern catalogue, records and occurrence, temporal interoperability |
| [Temporal knowledge graph definitions](temporal-kg/README.md) | Formal definitions, their validation, the revision response and the SULO interface |

## Demonstrations

| Folder | What it is |
|---|---|
| [`demos/presentation/`](../demos/presentation/README.md) | The guided cohort demo and the live pressure inspector, served on `:8765` |
| [`demos/mockups/`](../demos/mockups/README.md) | Exploratory interfaces for defining and matching trajectories, on a synthetic cohort |

## Decision and release boundaries

The v2.4 application extension adds classes and individuals, with no new object or datatype properties. Proposed changes to SULO itself are a separate upstream release decision. The precedence document records that proposal without activating its names or axioms in the application profile.

The formal temporal knowledge graph definitions are included under [temporal-kg/](temporal-kg/). The separately prepared product specification v2.3 remains outside this snapshot. The executable profiles establish only their documented subsets; in particular, the bounded discrete profile does not implement the full OWL/rational-time formal definition.
