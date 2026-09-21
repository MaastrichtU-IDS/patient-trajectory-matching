# Status

What executes, what is specified, and what has been verified. Status labels are defined in [architecture.md](architecture.md).

**Summary: <!-- test-counts:total -->870<!-- /test-counts:total --> contract checks and an integrated synthetic research journey. The current completion audit records 10 supported, 64 partial, 4 blocked and 26 specification-only requirements. Full-product completion remains false.**

The original `requirements.csv` remains byte-preserved as the historical authority. The [current completion register](completion.md) maps every original row to evidence and remaining clauses and separately tracks 16 additional profiles, including the interval work. The family table below reports the original CSV statuses; it is not the current implementation assessment.

Do not infer production readiness from passing fixture reports. The verification reports certify this pack's internal consistency only.

## Requirements by family

| Family | Total | Executable | Specified | Subject |
|---|---:|---:|---:|---|
| FR | 35 | 0 | 35 | Functional requirements |
| NFR | 16 | 0 | 16 | Non-functional requirements |
| UI | 14 | 0 | 14 | Patient workspace and interaction |
| TRP | 11 | 0 | 11 | Temporal replay |
| **PS** | **10** | **10** | **0** | **PRO/SOLID executable profile** |
| TIME | 4 | 0 | 4 | Time normalization |
| TKG | 3 | 0 | 3 | Temporal knowledge graph semantics |
| REFINE | 3 | 0 | 3 | Refinement sessions |
| CLIN | 2 | 0 | 2 | Clinical study |
| ETL | 2 | 0 | 2 | Source reconciliation |
| SULO | 1 | 0 | 1 | Ontology extension publication |
| MATCH | 1 | 0 | 1 | Semantic alternative and temporal cost |
| EVAL | 1 | 0 | 1 | Evaluation separation |
| UC | 1 | 0 | 1 | Use case |
| **Total** | **104** | **10** | **94** | |

Full detail, including spec sections, release targets and acceptance gates, is in [`requirements.csv`](../requirements.csv).

These are requirement-register counts, not a count of every implemented capability. The new bounded evidence selector covers parts of temporal replay; it does not close the complete TRP requirements or change their register status.

## The executable profile

All ten PS requirements are covered by gate **AC19-PRO-SOLID** and verified by the 42-test acceptance suite.

| ID | Requirement |
|---|---|
| PS-001 | Represent patient participation with a PRO role and bearer; preserve the role binding |
| PS-002 | Use `hasValue` only on typed information objects; no specialized instance properties |
| PS-003 | Bind the measurement result and measured subject within its process context |
| PS-004 | Require one recorded scalar value and one supported concentration unit |
| PS-005 | Convert mg/L to mg/dL without losing original values or decimal digits |
| PS-006 | Normalize explicitly zoned anchor datetimes; reject unresolved timezone or unsupported precision |
| PS-007 | Preserve record identity, source hashes, snapshot identity and graph projection evidence |
| PS-008 | Integrate with existing matcher DTOs; retain entailment versus relaxation distinction |
| PS-009 | Reject invalid role context and unsupported statuses before graph projection |
| PS-010 | Separate closed-world profile validation, limited derivation and query acceptance |

## Executable profiles

| Profile | Entry point | Decides |
|---|---|---|
| Point anchor (v2.4) | `patterns/pro_solid.py` | Three-slot exemplar matching with priced relaxation |
| `exact-interval-1.0` | `patterns/exact_intervals.py` | Pairwise temporal operators over recorded intervals |
| `interval-cohort-1.0` | `patterns/interval_cohort.py` | Conjunctive slot queries across patient episodes |
| `bounded-interval-1.0` | `patterns/bounded_cohort.py` | Joint feasibility and fixed-witness possible/certain interval queries |

## Verification results

The synthetic checks below are produced by commands in [validation.md](validation.md) and re-checked by CI on each push. Public-demo reconciliation is a separate local run; CI checks its committed aggregate provenance without downloading patient data.

<!-- test-counts:start -->

| Check | Result |
|---|---|
| Oracle cases | 16 passed |
| Oracle property checks | 7 passed |
| PRO/SOLID acceptance tests | 42 passed |
| Exact-interval conformance tests | 51 passed |
| Interval cohort tests | 18 passed |
| Extended interval query tests | 5 passed |
| Bounded uncertainty tests | 22 passed |
| Bounded RDF ingestion tests | 21 passed |
| Extended metric relaxation tests | 13 passed |
| Robust costed relaxation tests | 12 passed |
| Relaxation cost-model contract tests | 5 passed |
| Evidence selection tests | 29 passed |
| Temporal interface conformance tests | 22 passed |
| Checked Rust semantic support tests (optional dependency, separate CI job) | 22 passed |
| Joint temporal/semantic selection tests | 28 passed |
| Reproducible GEN-01/COH-01 use-case tests | 4 passed |
| MIMIC inputevents admission tests | 28 passed |
| Patient-local clock and RDF tests | 27 passed |
| End-to-end MIMIC record-query tests | 24 passed |
| Structured claim/projection tests | 45 passed |
| State validity and coverage tests | 22 passed |
| Patient-local claim projection tests | 32 passed |
| MIMIC pending claim import tests | 25 passed |
| Measurement claims and chartevents import tests | 35 passed |
| Mixed record query tests | 34 passed |
| Source mixed-query and SQL comparison tests | 28 passed |
| Clinical source preflight tests | 20 passed |
| Indexed source-window tests | 24 passed |
| Partitioned window execution tests | 27 passed |
| Unique-claim review tests | 28 passed |
| Source-fidelity audit and reviewed execution tests | 15 passed |
| Separate pressure-stratum provenance and comparison tests | 3 passed |
| Pressure-cohort overlap and provenance tests | 14 passed |
| Reviewed record mapping tests | 18 passed |
| Source record catalogue tests | 18 passed |
| Reviewed measurement selector tests | 18 passed |
| Measurement source catalogue and audit tests | 20 passed |
| Prepared measurement session tests | 20 passed |
| Pre-index similarity and immutable refinement tests | 28 passed |
| **Total** | **870 contract checks** |
| Release manifest digests | 9 verified |
| Point-anchor fixture outcome | `EXACT`, cost 0 |
| Graph reproducibility | Regenerated graph isomorphic to the committed copy, 131 triples |
| Interval comparisons | 9 evaluated, 5 satisfied / 4 not satisfied as specified |
| Cohort example | P1 `MATCH`, P2 `NO_RECORDED_MATCH`, P3 `INCOMPARABLE` |
| Bounded example | P1 `CERTAIN_MATCH`, P2 `POSSIBLE_MATCH`, P3 `NO_RECORDED_MATCH`, P4 `INCOMPARABLE` |
| Oracle on Python 3.10 / 3.11 / 3.13 | 16 cases passed on each |

The total is 847 suite tests plus 16 oracle cases and seven oracle properties; nested differential scenarios and manifest checks are not added again. Separately, the demo suite has 104 tests, the research HTTP suite has 15, and the completion checker has 13. Eight Node DOM-state suites cover the three existing demos and the research workspace. Browser visual review and target-cluster validation remain unperformed.

<!-- test-counts:end -->

**Properties checked by the oracle:** cost decomposition · zero-cost exact equivalence on supplied cases · subclass direction · budget monotonicity on supplied cases · not-given exclusion · incomplete-source propagation · unsupported-pattern rejection.

## Matcher behaviour verified

| Observed drug class | Relationship to DrugA | Result |
|---|---|---|
| DrugA | Same class | Exact, cost 0 |
| DrugAChild | Subclass | Exact, cost 0 |
| DrugB | Reviewed alternative, no subclass entailment | Relaxed, semantic cost 1 |
| DrugB, budget 0 | Excluded by policy | No accepted match |

## Status by component

| Component | Status | Evidence |
|---|---|---|
| Reference oracle | Executable | 16 cases, 7 properties |
| PRO/SOLID adapter | Executable | 42 tests, bounded profile |
| Exact-interval adapter | Executable | 51 tests, RDF round trip |
| Interval cohort matcher | Executable | 18 tests, differential against reference engine |
| Ontology profile and shapes | Executable | Loaded and enforced in CI |
| SULO pin | Executable | Digest verified at load |
| JSON Schema and OpenAPI | Structurally validated | `structural-report.json`; no service implements them |
| Interval cohort schema | Executable | Enforced by `validate_query` on every run |
| Time normalizer | Specified | 16 expectations, `normalizer_implemented: false` |
| Bounded temporal uncertainty | Executable | 22 tests; finite-world and certificate checks in the discrete profile |
| Bounded RDF adapter | Executable | 21 tests; graph validation, source equivalence, and preserved RDF evidence |
| Temporal precedence vocabulary | Proposed | `decisions/temporal-precedence.md`; not adopted into SULO |
| Refinement service | Specified | `refinement_service_implemented: false` |
| Evidence selection | Executable bounded subset | [29 tests](evidence-selection.md); explicit source chains and availability cutoffs |
| SULO temporal interface | Executable restricted conformance harness | [8 paired fixtures, 33 query comparisons and 22 tests](temporal-kg/sulo-interface.md); full OWL mapping remains open |
| Patient-local claim projection | Executable bounded extension | [32 tests](local-claim-projection.md); separate local and recorded profiles, preserved labels and clock isolation |
| Structured claims and acceptance | Executable bounded prototype | [45 tests](claim-projection.md); an empty-Process model for the claim description closure, explicit projection and withdrawal |
| Rust semantic support | Executable restricted integration | [22 differential/gate tests](semantic-support.md); complete checked class support and consistency for the admitted module |
| Joint temporal/semantic selection | Executable restricted integration | [28 tests](joint-evidence-selection.md); shared support chains, cutoffs and scoped semantic facts |
| Full temporal replay | Partially implemented | The bounded selector does not implement all 8 declarative families, historical semantics, derived indices or a replay UI |
| Graphiti comparison | Specified | Protocol only, no measurements |
| UI | Local demonstrations executable | Guided synthetic cohort selection and [reviewed pressure inspection](live-pressure-inspector.md); production workspace and measured usability remain open |
| Pressure-cohort overlap | Executable aggregate comparison | [14 tests](pressure-cohort-overlap.md); three source queries reproduced; 23 patients, 29 stays and 196 segments in the union of separate memberships |
| Three reviewed pressure strata | Executable technical demonstration | [Separate execution reports](reviewed-pressure-strata.md); all 2,832 anchor/stratum SQL comparisons agree; 140 stays retained per stratum; clinical interpretation unverified |
| Reviewed arterial source query | Executable technical demonstration | [15 tests](reviewed-arterial-demo.md); 2,022 source claims audited, explicit automated review, complete arterial execution; clinical interpretation unverified |
| Unique-claim review | Executable explicit review integration | [28 tests](unique-claim-review.md); unique source evidence, lifecycle propagation and complete pending demo compilation; subsequent reviews of all three strata available |
| Partitioned window execution | Executable exact-record integration | [27 tests](partitioned-window-query.md); all 2,832 demo anchor plans covered, synthetic Rust/SQL execution verified; subsequent executions of all three strata available |
| Indexed source windows | Executable exact-record selector | [24 tests](indexed-source-windows.md); 2,832 indexed/direct window comparisons agree; original blocked windows retained; subsequent partitioned reviewed execution available |
| Clinical source preflight | Executable aggregate scan | [20 tests](clinical-source-preflight.md); 668,862 demo chart rows scanned; candidate mapping proposed; subsequent separate reviewed executions available |
| Source mixed-query pipeline | Executable exact-source integration | [28 tests](source-mixed-query.md); explicit review, full source/stay accounting, independent SQL agreement; synthetic only |
| MIMIC recorded-source query | Executable | CSV-to-RDF-to-Rust-to-temporal pipeline; public demo reproduced; clinical truth unverified |
| Patient-local clocks | Executable | 27 tests; explicit local bounds, isolated clocks, RDF and fixed-witness queries |
| MIMIC inputevents staging | Executable | 28 tests; 20,404 public-demo rows reconciled, clinical/matcher handoff blocked |
| MIMIC-IV study | Specified | Plan only, `full_mimic_analyzed: false` |

## Verification scope

Earlier version reports are explicit about their own limits:

| Report | Declared status |
|---|---|
| `v21-additions-report.json` | `structural_checks_only` |
| `v22-additions-report.json` | `structurally_verified_only` |
| `v23-additions-report.json` | `structurally_verified_only` |
| `v24-pro-solid-report.json` | `passed: true`, `production_readiness_claim: false` |
| `exact-interval-report.json` | Conformance and RDF round trip for `exact-interval-1.0` |
| `structural-report.json` | `full_owl_reasoning_tested: false`, `production_services_tested: false` |

## What passing does not establish

The original exclusions in [addendum 2.4 §8](../addenda/specification-2.4.md) establish a limited verification scope. After adding the discrete bounded profile, the following remain outside the demonstrated capabilities:

- full SULO or temporal reasoning
- specimen mapping
- raw clinical ETL
- bitemporal reconstruction
- generalized matching
- complete OWL consistency checking
- temporal certainty beyond the bounded discrete profile, including dense-time and general OWL certain answers

See [issues.md](issues.md) for the gaps behind these.


## Repeated reviewed pressure queries

The local pressure service now has a [bounded completed-result cache](pressure-query-cache.md). Identical controls can reuse the full cohort and evidence after source, review and implementation rechecks. Changed controls now use the prepared-query path described below, with fresh temporal evaluation and the independent SQL oracle. Eleven cache tests bring the demo suite to 40 Python tests, alongside the unchanged 687 contract checks. Reproducible synthetic/public-demo measurements compare every result and anchor inspection; these are local samples, not a production latency claim. Reuse of graph projections and semantic support across different queries is implemented in the next increment below.


## Prepared pressure-query execution

[Checked batch preparation](prepared-pressure-queries.md) is now reusable across changed pressure controls. Temporal classification, numeric filtering and every anchor SQL comparison remain fresh. Eleven additional demo tests bring that suite to 51; the 687 contract checks remain separate. Full-result and inspection equality are checked against the original executor, including uncertain-time fixtures and public arterial data. The previous complete-result cache remains the fastest path for identical requests.


## Reviewed terminology mapping infrastructure

The [mapping compiler and mixed-query adapter](reviewed-record-mappings.md) add 18 tests, bringing the contract total to 705 checks (682 suite tests plus 23 oracle checks). Explicitly accepted synthetic mappings execute with checked Rust semantics and preserved PRO witnesses. The [clinical worksheet](../data/clinical-terminology-review.json) has no accepted targets. Source-catalogue validation, domain review and pressure-service integration remain open; the 51-test demo suite is unchanged.


## Source-verified mapping catalogues

The [source catalogue adapter](source-record-catalogue.md) adds 18 tests: 723 contract checks total (700 suite tests and 23 oracle checks). Public demo item 221906 has a pinned source catalogue with 944 staged segments and three unsupported rows; no terminology target is accepted. The synthetic source-to-mapping query matches the literal-source SQL control and preserves PRO witnesses. Clinical target review, measurement mapping and integration into prepared pressure sessions remain open.


## Clinical terminology proposals

[The candidate dossier](clinical-terminology-candidates.md) supplies concrete RxNorm/LOINC targets and source evidence. The norepinephrine pack contains all four compiler inputs but returns `BLOCKED_MAPPING_REVIEW`. Pressure proposals remain non-executable, with source strata preserved and invasive/CNAP method commitments explicitly conditional. No runtime code, clinical acceptance or check counts change.


## Reviewed measurement selectors

[Measurement item-class selection](reviewed-measurement-mappings.md) now executes through checked catalogue rules and separate literal-item/unit mixed queries. Eighteen new tests bring the contract total to 741 checks (718 suite tests and 23 oracle checks); the demo suite remains 51. Synthetic Rust/literal/SQL verification preserves PRO, optional follow-up and temporal certainty. Clinical pressure mappings, row-qualified mappings and prepared-session integration remain pending.

## Measurement source correspondence

[Measurement source audits](measurement-source-catalogue.md) now reproduce catalogue entries, every supplied measurement claim and pre-filter patient clock origins from pinned CSV bytes before reviewed selector execution. Twenty new tests bring the total to 761 checks (738 suite tests and 23 oracle checks). The synthetic report compares six claims directly with CSV fields and reproduces three patient memberships against literal queries and raw-CSV SQL. Subset fidelity does not imply source coverage or clinical acceptance. Prepared-session reuse and clinical review remain pending.

## Prepared measurement query reuse

[Prepared measurement sessions](prepared-measurement-session.md) reuse a successful source audit, checked mapping plan, selected record views and compiled temporal network while checking source and implementation digests before/after every query. Twenty new tests bring the total to 781 checks (758 suite tests and 23 oracle checks). Twelve synthetic query variants reproduce fresh results and raw-CSV SQL with zero repeated preparation operations. Applying later review decisions requires session replacement; live pressure-batch integration and clinical acceptance remain pending.

## Live mapped pressure integration

[The opt-in mapped pressure service](mapped-pressure-service.md) now connects reviewed measurement selection and prepared sessions to the existing local pressure jobs and inspector. Mapping files are rechecked on every job, including complete-result hits; changes invalidate preparation and result caches. Sixteen new integration tests bring the demo suite to 67, alongside 781 contract checks. The synthetic HTTP benchmark preserves every result field except duration and all three anchor inspections against fresh mapped execution, with no repeated audit, mapping-plan or network compilation and three fresh SQL checks. [Configured mapped operation](configured-pressure-service.md) now accepts supplied sources, requests and explicit reviews, adding 13 integration tests (80 demo tests total) and a configured UI check. Public clinical mappings remain pending.


[Configured workload verification](configured-pressure-workload.md) now executes supplied query matrices with separate service/HTTP/fresh-reference timings and actual cache behavior. The committed authored sequence verifies six trials and 36 HTTP inspections; 12 new tests bring the demo suite to 92. Representative clinical workload and peak-memory evaluation remain open.


### Integrated synthetic research journey

[The research workspace](research-workspace.md) now connects pre-index query-by-example, two immutable refinements, exact/relaxed comparison over the entire eligible pool, source inspection and verified export replay. The committed aggregate journey binds all five steps to implementation and source hashes. [Similarity](patient-similarity.md) adds 28 tests; HTTP integration and completion traceability add 13 tests each. The original source is supplemented with explicitly authored availability evidence; this is not historical clinical validation.

[The memory profiler](pressure-workload-memory.md) adds 12 demo tests and measures the configured authored workload's sampled process-tree RSS. Its committed six-trial run observed 77.55 MiB; sequential RSS sampling is not an exact peak and does not establish representative clinical capacity. [The completion assessment](completion.md) records the full product's remaining clauses and external dependencies.
