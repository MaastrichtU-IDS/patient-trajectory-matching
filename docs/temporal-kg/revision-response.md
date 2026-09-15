# Response to temporal knowledge graph formal definition v2

**Status:** Proposed project decisions for review, 15 September 2026. This response does not close Q1–Q11, change an executable profile, or adopt new SULO properties. Acceptance of a design choice and verification of its implementation are separate milestones.

**Reviewed report:** [Temporal Knowledge Graph: Revised formal definition, v2 working specification](Temporal_Knowledge_Graph_Formal_Definition_v2.pdf), especially §§8, 11 and 13. The [original definition](Temporal_Knowledge_Graph_Formal_Definition.pdf) remains available for comparison.

**Implementation baseline:** [repository commit 2b9e055](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/tree/2b9e055b14b668441ae83f23042c4d3607011fa7), including the merged bounded RDF ingestion profile. See the [existing gap register](../issues.md#relationship-to-the-v2-decision-register) for implementation traceability.

## Assessment and proposed commitments

The revision provides an explicit division between ontology entailment, temporal constraint evaluation, evidence selection, and query answering. Adopt that division as the proposed service architecture. Preserve source-backed named witnesses, joint temporal feasibility, and fixed-witness certainty before patient projection.

Three changes from the original definition require explicit acceptance:

1. Ordinary OWL interpretations of processes and temporal situations replace the original family of time-indexed interpretations. A domain axiom about a changing state needs a reviewed situation translation; importing the axiom alone does not supply that translation.
2. A possible match requires entailed ontology support and eligible evidence. It is then possible with respect to temporal uncertainty. A model that supplies a missing positive ontology fact is insufficient for this service.
3. Certain patient membership requires one fixed eligible binding that succeeds in every feasible timeline. It does not assert the broader result in which different bindings can succeed in different timelines.

Retain the current application constraints: PRO role/bearer paths establish patient participation; all instance literals use `sulo:hasValue` on typed information objects; application extensions add classes and individuals without adding object or datatype properties. A future SULO release change is a separate decision.

## Response to Q1–Q11

Each row is a recommendation. “Evidence to close” describes the acceptance obligation; it does not report a completed check.

| ID | Recommended decision | Current coverage | Evidence to close the relevant capability |
|---|---|---|---|
| Q1 — Execution boundary | Permit an external temporal evaluator alongside OWL support checks. Identify ontology conclusions, evaluator conclusions, and evidence-selection decisions separately. | The executable profiles already use external arithmetic/constraint evaluation, with limited ontology support. | An approved service contract and result examples identifying each conclusion's source; an explicit supported ontology fragment. |
| Q2 — Identity | Keep individual identity, source-record identity, and temporal coincidence distinct. Normalize approved identities before compiling temporal constraints, retaining aliases and provenance. | The bounded RDF reader checks explicit identifiers and references but rejects `owl:sameAs`; it does not implement general OWL identity. | Merge/conflict cases for patients, processes, roles and temporal objects; functional-property equality cases; deterministic rebuilding of affected constraints and bindings. |
| Q3 — Clocks and precision | Version numeric profiles. Retain discrete microseconds for the existing profile; add exact rational strict constraints only under a separately declared profile. Define clock conversion and source precision independently. | Finite integer-microsecond bounds and shared variables within one clock; different clocks remain incomparable. | Source-specific timezone, precision, rounding and availability rules; conversion fixtures; strict rational boundary tests for the rational profile; rejection of unsupported mappings. |
| Q4 — Process extents | Initially support point occurrences and bounded proper intervals through their declared profiles. Classify each source timestamp's meaning. Explicitly defer ongoing and disconnected extents. | Point-anchor and interval contracts exist separately. The bounded matcher requires proper intervals with finite endpoint bounds. | Type-specific source mappings; point/window/interval counterexamples; explicit exclusion or a separate contract for unknown ends and disconnected occurrences. |
| Q5 — Temporal situations | Introduce reviewed templates for individual state predicates as needed. Specify validity, participating entities or values, overlap conflicts, and observation-to-state assumptions. | No general temporal-situation evaluator. The point measurement example does not establish a general persistent-state model. | At least one end-to-end template with positive, conflicting, missing and boundary cases; a reviewed translation of each relevant domain axiom. |
| Q6 — Imports and mappings | Preserve the SULO class-only application policy. Specify a mapping from the report's abstract interface to supported SULO patterns. Pin and validate the selected import closure. | Pinned SULO and local modules; narrow named subclass paths. The report's standalone core is a different ontology artifact. | Available standalone artifacts, mapping rationale, full-closure profile/consistency checks, and query-answer comparisons for the declared mapping. |
| Q7 — Evidence selection | Implement a versioned selection operation producing immutable reasoning snapshots, with explicit correction, duplicate-support, cutoff and conflict policies. | One supplied snapshot can be ingested; selection across source revisions is unimplemented. | Executable selection fixtures for revisions, duplicate support, missing availability, conflicts and historical/retrospective mappings; reproducible snapshot identities. |
| Q8 — Logic/solver interface | Use the separated baseline first: finite supported named bindings plus conjunctive difference constraints. Specify the ontology consequences consumed by compilation. Defer temporal-to-ontology feedback and richer bridges. | Joint feasibility, discrete entailment, certificates and fixed-witness answers; no full OWL/rational-time interface. | Operation-specific reasoner capability evidence, an explicit compiler contract, supported-fragment soundness/completeness arguments and independent answer-preservation cases. |
| Q9 — Clinical query | Make the chosen landmarks and boundary operators explicit in every approved pattern. Preserve the report's example as strict completion-to-start separation of at most 48 hours. | Generic temporal operators exist; the administration/collection example is not a validated clinical phenotype. | Clinical approval and source-backed examples covering equality at completion, the 48-hour boundary, precision uncertainty and the meanings of administration/collection timestamps. |
| Q10 — Relaxation | Begin with a finite approved catalogue of constant-cost modifications and explicit protected conditions. Fix both binding and modification before varying uncertain times. Keep possible-only ranking separate. | The point-anchor oracle has a specific cost model; the interval matchers have no priced relaxation. | Approved substitutions, widenings, budgets and tie-breaking; robust feasibility checks and independent cost comparisons. A variable-cost extension additionally needs a complete optimizer for its declared fragment. |
| Q11 — Operational acceptance | Define data scale, supported query sizes, latency/resource budgets, completeness and failure behavior before selecting optimizations. | Synthetic differential/certificate tests; bounded queries support one to eight required distinct slots. No representative clinical benchmark or deployed service. | Representative source-backed datasets, reference answers, candidate-recall and end-to-end measurements, resource-limit tests and explicit incomplete-search reporting. |

## SULO representation and interface mapping

The report's `hasBeginning`, `hasEnd`, `exactExtent`, `validExtent` and situation-slot properties belong to its standalone core. They are not application vocabulary approved for this repository. Use the following correspondence to develop an extraction contract; it is not a set of `owl:equivalentProperty` or `owl:equivalentClass` axioms.

Prefixes below refer to existing modules: `sulo:` is `https://w3id.org/sulo/`, `bt:` is `https://example.org/trajectory/bounded/`, and `ex:` is `https://example.org/trajectory/toy/`.

| Report concept or operation | Existing SULO representation / proposed treatment | Mapping obligation |
|---|---|---|
| Patient participating in a process | Process → `sulo:hasParticipant` → `ex:PatientRole` → `sulo:isFeatureOf` → Person | Preserve both links and the role type in the binding. Generic participation of the bearer is insufficient. |
| Complete interval occurrence, `exactExtent` | The bounded profile selects Process → `sulo:atTime` → `bt:OccurrenceInterval` | The source/profile must establish that this is the complete occurrence extent. Do not give every use of the general `atTime` property this stronger meaning. |
| Beginning/end coordinates | Interval → `sulo:hasDirectPart` → `bt:StartDescriptor` / `bt:EndDescriptor` → `sulo:refersTo` → `bt:TemporalVariable` | Validate the unique selected descriptor/reference for each endpoint and retain its evidence. These paths extract solver variables; they do not establish the report's primitive TimePoint ontology. |
| Primitive point/interval distinction | Bounded intervals specialize `sulo:TimeInterval`; temporal variables are information objects | Review the ontological mapping of primitive temporal objects separately from descriptions and solver variables. No point/variable equivalence is asserted here. |
| Coordinate uncertainty | Variable → typed lower/upper bound descriptors → `sulo:hasValue`, with an explicit unit and clock binding | A bound constrains a coordinate. It is neither an observed exact value nor a period throughout which a state holds. |
| Difference constraint | Typed left/right operand bindings refer to variables; typed operator and upper-bound data specify the inequality | Validate the supported operator, unit, scope and references; compile once and retain a trace to the original graph. |
| `validExtent` and situation-specific slots | No approved executable counterpart yet | Define a class-only situation pattern and its query extraction under Q5/Q6. Do not substitute `atTime` or general participation without specifying validity and slot semantics. |
| Assertion support and revision | Source-record information and typed metadata are present; assertion selection is a separate pending operation | Specify accepted assertion bundles and their supporting sources under Q7. Source links alone do not activate, supersede or retract axioms. |

The [bounded RDF reader](../bounded-rdf-ingestion.md) imposes a closed graph contract. Its exactly-one-field checks, distinct descriptor rules and rejection of unknown triples are ingestion rules. They are not proofs of general OWL cardinality, individual inequality, or consistency. Distinct RDF names alone do not establish OWL inequality.

To accept a mapping, show how each admitted source graph produces eligible named bindings and constraints, which ontology assertions are entailed, and how temporal answers correspond to the report's supported fragment. Include counterexamples for missing named endpoints, ambiguous descriptions, inferred equality and incompatible clocks. The reported standalone-core checks cannot establish this mapping by themselves.

## Numeric and query semantics to preserve

### Time domain and uncertainty

The constraint `0 < x < 1 microsecond` has rational solutions and no integer-microsecond solution. The existing conversion of strict order to a one-microsecond separation is correct only for its declared grid. Do not claim equivalence to the report's rational-time semantics or silently round a rational constraint into that profile.

Storage precision does not determine measurement accuracy. A source mapping must say whether a minute-resolution timestamp is exact, truncated, rounded, or otherwise uncertain. Preserve its original value and the transformation rule.

Only admit clock conversions whose compiled constraints remain in the supported fragment. Known conversions of individual coordinates do not by themselves prove that every transformed cross-clock constraint is a difference constraint. Unknown mappings remain unsupported/incomparable. Preserve shared offsets and anchors as shared variables whenever supported; do not replace correlated uncertainty with independent endpoint ranges.

### Support, feasibility and certainty

For an eligible fixed named binding `mu`, first establish the supported ontology atoms. Require ontology consistency for the declared reasoning service and a nonempty feasible source set `Omega` before reporting normal answers. The current limited profiles do not implement the report's full OWL consistency gate.

- Possible: `SAT(Gamma AND C[mu])`, with all query conditions solved jointly.
- Certain: `UNSAT(Gamma AND NOT C[mu])`, tested against the original source constraints. For a conjunction, each negated conjunct can be tested separately.
- Certain patient: there exists one eligible supported binding that is certain, evaluated before patient projection.

The quantifier order is `exists mu, forall theta`, not `forall theta, exists mu`. The latter can establish patient membership with a different event combination in each timeline. It may be a useful future service but requires a distinct answer contract and explanation.

Define the scope of the selected source constraint system explicitly. Do not hide source inconsistency by dropping inconvenient assertions during candidate filtering. Decomposition is valid only when its independence assumptions are established. The current bounded profile checks its supplied source networks before matching and prohibits cross-patient, cross-episode and cross-clock source constraints.

Keep ontology inconsistency, temporal inconsistency, unsupported evidence/syntax, incomparable clocks, possible-only results, complete absence of a recorded match, and execution failure distinct. Search completeness describes the admitted evidence scope; it does not establish clinical absence or source completeness.

### Clinical boundaries and robust relaxation

The report's administration/collection example is `0 < collection_start - administration_end <= 48 hours`. On the current grid, express it using `before` together with an inclusive `gap` upper bound of `172800000000` microseconds. A zero-minimum `gap` condition alone also permits contact and is therefore insufficient to express the example. This translation is a computational example pending clinical approval.

For robust relaxation, choose a binding and one approved modification before evaluating uncertain timelines. Source constraints and protected conditions remain fixed. With a finite constant-cost catalogue, test robust feasibility for each modification and select the least-cost feasible choice within budget. Do not reinterpret a possible relaxed match as robust acceptance. The report's more general supremum cost model remains an extension requiring a supported optimization contract.

## Precedence and adjacency

Retain the [existing precedence proposal](../decisions/temporal-precedence.md) as a separate upstream decision. The report's simple asymmetric `pointBefore` and evaluator closure do not adopt a transitive SULO `precedes` property.

A direct subproperty of a transitive property remains a candidate structure if the direct property remains simple. OWL 2 DL's simple-property restrictions prevent declaring the transitive property itself asymmetric or irreflexive; the [W3C structural specification](https://www.w3.org/TR/owl2-syntax/#Global_Restrictions_on_Axioms_in_OWL_2_DL) defines these restrictions.

Adjacency additionally requires an explicit sequence scope, granularity and evidence snapshot. Keep it as a scoped query operation until those choices are settled. Temporal contact is the separate equality condition described by [OWL-Time `intervalMeets`](https://www.w3.org/TR/owl-time/#time:intervalMeets). No new precedence property or axiom is introduced by this response.

## Missing validation artifacts

[Issue #6](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/issues/6) requests the report's `temporal-core.ofn`, `example.ofn`, checker and outputs for all 18 reported checks. At the reviewed baseline, these artifacts are absent from the repository. The results remain attributed to the report and have not been independently reproduced here.

Preferred delivery locations are `docs/temporal-kg/ontology/` for the two OWL files and dependencies, and `docs/temporal-kg/validation/` for the checker, README and `results/` outputs. A ZIP attached to the issue is also sufficient for initial review. Do not close the request merely because this response has been added.

The Java checker documents a collaborator's validation run. Retain the Rust/Python implementation direction. Planned horned-owl/py-horned-owl and rustDL integration still requires evidence for each needed operation and ontology fragment; library selection is not an integration or conformance result.

## Proposed delivery order and review record

1. Review the recommendations above and obtain the standalone artifacts through issue #6. Record each decision independently, including any explicit deferral.
2. Specify and validate the SULO extraction mapping and admitted ontology fragment. Keep the current executable profiles versioned and available during that work.
3. Implement evidence selection into immutable snapshots, starting with correction, duplicate-support, cutoff and conflict fixtures. Integrate it with the bounded matcher through the declared snapshot contract.
4. Extend ontology support or rational-time handling only for an identified required capability, with the corresponding conformance cases. Do not describe the current integer profile as full v2 conformance.
5. Benchmark and optimize the admitted fragment. Cache stable snapshot/type information and source temporal closure; evaluate sparse or incremental propagation and candidate pruning against independent reference answers. Preserve shared constraints and whole-pattern feasibility. Marginal bounds may support conservative rejection but cannot replace joint feasibility checks.

For each Q1–Q11 decision, record the chosen option, scope/profile, reviewer and date, evidence links, remaining exclusions, and acceptance outcome. A row may be **proposed**, **accepted with verification pending**, **verified for a named profile**, or **explicitly deferred**. Do not mark a broader capability verified because one narrower profile passes its tests.
