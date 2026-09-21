# D3. Temporal interoperability profile

**Status: candidate specification derived from executable interval profiles.** The current implementation is described in [bounded uncertainty](../bounded-temporal-uncertainty.md), [patient-local clocks](../patient-local-clocks.md) and [SULO interface conformance](../temporal-kg/sulo-interface.md). Proposed extensions below are not enabled by this document.

## Preserve meaning at the interface

A temporal exchange should identify the represented process or claim, its patient-role/bearer witness, temporal descriptors, coordinate variables and units, clock identity/scope, source constraints, interpretation policy, source evidence and supported query operations. These are distinct identities even when some coordinates coincide.

Pinned SULO treats TimeInstant and TimeInterval as information-bearing quantities. The standalone temporal core uses primitive temporal entities. OWL-Time supplies another vocabulary for instants, intervals, temporal positions and reference systems. A transformation must state what it preserves instead of asserting blanket class equivalence. The existing paired harness establishes only selected query-answer agreement. [OWL-Time vocabulary](https://www.w3.org/TR/2022/CRD-owl-time-20221115/)

## Candidate conformance requirements

| ID | Requirement | Existing support / remaining work |
|---|---|---|
| T01 | Declare time domain, resolution, bounds and arithmetic | Signed integer microseconds implemented; rational time is outside this executable fragment |
| T02 | Declare origin, clock identity, patient/global scope and offset/local interpretation | Implemented profiles; no automatic reconciliation of incompatible clocks |
| T03 | Preserve source labels and transformation evidence | Local RDF round trip implemented; numerical precision does not establish physical accuracy |
| T04 | Specify proper half-open intervals and operator boundaries | Implemented interval operators below; timestamped observations need a new extension |
| T05 | Preserve shared variables and every source difference constraint | Implemented; replacing joint constraints with independent endpoint ranges is not equivalent |
| T06 | Evaluate a fixed named binding against all feasible source timelines for certainty | Implemented bounded matcher; inconsistent source constraints are rejected before this quantification |
| T07 | Preserve semantic eligibility, role witnesses and evidence context | Checked restricted semantic support implemented; full import and identity reasoning remain open |
| T08 | Distinguish answer status from execution and source completeness | Implemented profile-specific statuses; a common exchange vocabulary still needs versioning |
| T09 | Make translation losses and unsupported inputs explicit | Existing closed readers reject unsupported graphs; general OWL-Time round trip is not implemented |

For proper intervals A = [sA, eA) and B = [sB, eB), the implemented operator meanings are:

| Operator | Condition |
|---|---|
| `before(A,B)` | eA < sB |
| `meets(A,B)` | eA = sB |
| `overlaps(A,B)` | sA < sB < eA < eB; directional Allen overlap |
| `gap(A,B,l,u)` | l ≤ sB − eA ≤ u, with 0 ≤ l ≤ u |

`overlaps` does not mean arbitrary nonempty intersection. A zero-gap match does not imply strict `before`. For uncertain bounds, possible means some feasible source timeline satisfies the whole query for that binding; certain means every feasible source timeline does. A patient-level certain answer requires one qualifying named binding that works across those timelines. It is stronger than permitting different witnesses in different timelines.

## Measurement timestamp extension

The proposed MAP example requires observation coordinates alongside infusion intervals. Do not encode a timestamp as an invented one-microsecond process duration or weaken the existing proper-interval rule. Specify a timestamped observation description and distinguish its charted coordinate from any unknown measurement-process duration.

A candidate query contract would use a baseline observation strictly before the selected segment start, at most 30 minutes earlier, and follow-up observations strictly after that start through 120 minutes inclusive. Thresholds and windows are proposed research parameters requiring review. Unit/method compatibility, baseline selection, duplicate readings, ties and missing follow-up need explicit rules. Cohort eligibility must be decided before outcome summaries.

No current interval API should accept this extension under its existing version. Add mixed point/interval fixtures and a direct timestamp reference evaluator, including exact boundaries, uncertain timestamps, concurrent observations and incomparable clocks.

## Precedence and direct succession

Retain the existing [proposed precedence decision](../decisions/temporal-precedence.md) as the single decision record. A direct subproperty below a transitive precedence property remains a candidate upstream change; pinned SULO has not adopted it.

Omitting transitivity on a direct property does not express non-transitivity or the absence of intermediates. Scoped adjacency depends on selected members, granularity, order and completeness. It can change when evidence or inferred eligibility changes. Keep adjacency as a scoped query operation until that context contract is settled; temporal contact is a different operator.

Any upstream transitivity change needs a review of downstream uses requiring simple properties. OWL 2 DL imposes global restrictions on transitive properties and their use in certain axioms. [OWL 2 structural restrictions](https://www.w3.org/TR/owl2-syntax/#Global_Restrictions_on_Axioms_in_OWL_2_DL)

For efficiency, index eligible processes and endpoints, restrict by patient/episode and semantic selector before temporal joining, and cache only under a complete evidence/ontology/clock/policy key. Avoid materializing every precedence pair by default. Sparse direct edges alone are not a complete evaluator for all explicit or timestamp-derived precedence facts. These are optimization proposals; benchmark them without weakening fixed-witness correctness or silently truncating inputs.

## Evidence required before adoption

Extend the paired representation suite with independent source graphs, declared mappings and expected answers. Compare named bindings, temporal status, retained evidence, invalid/inconsistent inputs and unsupported cases. Include shared boundaries with separate start/end descriptors, different clock origins, correlated uncertainty, endpoint equality and missing records.

For the broader OWL-Time mapping, publish the admitted term/axiom subset and loss report, then demonstrate round-trip preservation only for that subset. Do not relabel the existing standalone-core comparison as an OWL-Time conformance test. Keep the original 18 formal obligations separately traceable.
