# Status

What executes, what is specified, and what has been verified. Status labels are defined in [architecture.md](architecture.md).

**Summary: temporal profiles, evidence selection, mapping conformance and checked Rust semantic support, 402 passing checks, and 10 of 104 tracked requirements marked executable.**

The requirement register predates the interval work. `requirements.csv` still tracks 104 requirements with 10 marked executable, all of them PS-\* in the point-anchor profile. The `exact-interval-1.0`, `interval-cohort-1.0`, and `bounded-interval-1.0` profiles are executable and CI-verified but **are not yet represented in the register** — see [issues.md R3](issues.md#repository-hygiene).

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

| Check | Result |
|---|---|
| Oracle cases | 16 passed |
| Oracle property checks | 7 passed |
| PRO/SOLID acceptance tests | 42 passed |
| Exact-interval conformance tests | 51 passed |
| Interval cohort tests | 18 passed |
| Bounded uncertainty tests | 22 passed |
| Bounded RDF ingestion tests | 21 passed |
| Evidence selection tests | 29 passed |
| Temporal interface conformance tests | 22 passed |
| Checked Rust semantic support tests (optional dependency, separate CI job) | 22 passed |
| Joint temporal/semantic selection tests | 28 passed |
| MIMIC inputevents admission tests | 28 passed |
| Patient-local clock and RDF tests | 27 passed |
| End-to-end MIMIC record-query tests | 24 passed |
| Structured claim/projection tests | 45 passed |
| **Total** | **402 checks passing** |
| Release manifest digests | 9 verified |
| Point-anchor fixture outcome | `EXACT`, cost 0 |
| Graph reproducibility | Regenerated graph isomorphic to the committed copy, 131 triples |
| Interval comparisons | 9 evaluated, 5 satisfied / 4 not satisfied as specified |
| Cohort example | P1 `MATCH`, P2 `NO_RECORDED_MATCH`, P3 `INCOMPARABLE` |
| Bounded example | P1 `CERTAIN_MATCH`, P2 `POSSIBLE_MATCH`, P3 `NO_RECORDED_MATCH`, P4 `INCOMPARABLE` |
| Oracle on Python 3.10 / 3.11 / 3.13 | 16 cases passed on each |

The total is 379 suite tests plus 16 oracle cases and seven oracle properties; nested differential scenarios and the manifest checks are not added again.

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
| Structured claims and acceptance | Executable bounded prototype | [45 tests](claim-projection.md); an empty-Process model for the claim description closure, explicit projection and withdrawal |
| Rust semantic support | Executable restricted integration | [22 differential/gate tests](semantic-support.md); complete checked class support and consistency for the admitted module |
| Joint temporal/semantic selection | Executable restricted integration | [28 tests](joint-evidence-selection.md); shared support chains, cutoffs and scoped semantic facts |
| Full temporal replay | Partially implemented | The bounded selector does not implement all 8 declarative families, historical semantics, derived indices or a replay UI |
| Graphiti comparison | Specified | Protocol only, no measurements |
| UI | Specified | Wireframes and contracts, no running interface |
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
