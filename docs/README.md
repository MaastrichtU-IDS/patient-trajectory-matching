# Documentation guide

The repository contains an executable contract pack and specifications for a broader patient trajectory matching product. Read each document's status before treating a described capability as implemented.

| Document | Purpose | Status |
|---|---|---|
| [Repository README](../README.md) | Setup, scope, and runnable examples | Current entry point |
| [PRO/SOLID addendum v2.4](../addenda/specification-2.4.md) | Canonical graph, validation, projection, and exemplar matching | Current executable contract within its declared profile |
| [Exact-interval profile](exact-interval-profile.md) | Source/RDF adapter, explicit clocks, endpoint comparisons, and evidence | Executable `exact-interval-1.0`; separate from the point-anchor oracle |
| [Interval cohort matching](interval-cohort-matching.md) | Versioned slot queries, indexed joins, exhaustive reference, and evidence | Executable `interval-cohort-1.0`; exact recorded intervals |
| [SULO and OWL-Time review](sulo-owl-time-review.md) | Detailed comparison and recommendations for temporal representation and reasoning | Design guidance; bounded uncertainty and general interval matching remain future work |
| [Temporal precedence](decisions/temporal-precedence.md) | Strict precedence, direct succession, and temporal contact | Proposed decision; no SULO core change adopted |
| [Replay addendum v2.3](../addenda/specification-2.3.md) | Observation/correction semantics and Graphiti comparison | Specified |
| [Workspace addendum v2.2](../addenda/specification-2.2.md) | Patient workspace and interaction design | Specified |
| [Clinical workflow addendum v2.1](../addenda/specification-2.1.md) | Query by example, normalization cases, and MIMIC-IV study plan | Specified |

## Recommended implementation path

1. Reproduce the existing PRO/SOLID adapter and reference oracle using the repository README.
2. Run the exact-interval adapter and its conformance suite, which preserve explicit start/end descriptors, clock scope, PRO role witnesses, and SOLID values.
3. Run the interval cohort matcher and its differential suite: versioned required/distinct slots, exact conjunctive constraints, and patient-episode joins with retained evidence.
4. Add bounded uncertainty with shared variables, joint feasibility, and explicit certain/possible results.
5. Extend and benchmark the indexes on representative clinical data, preserving differential checks against the reference implementation.

The detailed acceptance gates are in section 15 of the review. The Rust/Python stack, including the planned horned-owl/py-horned-owl and rustDL integration, needs operation-specific capability checks; passing the current fixture suite does not establish full OWL or temporal reasoning support.

## Decision and release boundaries

The v2.4 application extension adds classes and individuals, with no new object or datatype properties. Proposed changes to SULO itself are a separate upstream release decision. The precedence document records that proposal without activating its names or axioms in the application profile.

The review also refers to the separately prepared product specification v2.3 and formal temporal knowledge graph definition. Those source documents are not included in this repository snapshot. The executable files and addenda here establish only their explicitly documented scope.
