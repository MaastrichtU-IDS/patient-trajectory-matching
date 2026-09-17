# Proposed decision: temporal precedence and direct succession

**Status:** Proposed, 15 September 2026. This records the design discussion; it does not adopt a SULO release change or extend the executable v2.4 profile.

**Related:** [SULO and OWL-Time review](../sulo-owl-time-review.md), section 8; [PRO/SOLID contract](../../addenda/specification-2.4.md), sections 5–6.

## Problem

SULO 0.2.14 provides `precedes` and its inverse `isPrecededBy` between processes, but does not declare them transitive. A direct/transitive property pair could represent sparse process sequences while supporting broader order inference. SULO already uses this structure for `hasDirectPart` and transitive `hasPart`. [Pinned SULO source](https://github.com/AIDAVA-DEV/sulo/blob/1a4abc1699471187e94fbc59591101b2b635d6ea/sulo.ttl)

The suggested name `immediatelyPrecedes` is ambiguous: it could mean adjacency in a sequence or coincidence of interval boundaries. Those meanings require different temporal conditions.

## Recommended semantics

Retain strict whole-interval precedence in the proposed interval profile. For proper occurrence intervals A and B with compatible clocks:

$$
\operatorname{precedes}(A,B) \iff e_A < s_B.
$$

This is a proposed interpretation for a future core release and profile, not an arithmetic equivalence currently axiomatized in SULO. Point-anchor ordering in v2.4 does not establish ordering of the complete process intervals.

| Concept | Intended condition | Consequence |
|---|---|---|
| Strict precedence | A ends before B starts | Transitive; a positive gap exists |
| Direct succession | A strictly precedes B, with no relevant sequence member strictly between them | A specialization of strict precedence; depends on sequence scope |
| Temporal contact | A ends exactly when B starts | Allen `meets`; incompatible with strict precedence for that pair |
| Start order | A starts before B starts | Processes may overlap; does not establish strict whole-interval precedence |

OWL-Time distinguishes `intervalBefore` from `intervalMeets` in the same endpoint terms. Neither the choice of half-open interval notation nor the absence of recorded intervening events makes equality satisfy strict inequality. [OWL-Time interval relations](https://www.w3.org/TR/owl-time/#time:intervalMeets)

The preferred proposed name for direct succession is **`directlyPrecedes`**, matching SULO's `hasDirectPart` naming. `immediatelyPrecedes` remains an alternative name if its definition explicitly permits temporal gaps. Do not introduce both names as separate canonical properties for the same concept.

If the intended meaning is zero-gap contact, retain it as a separate temporal operator or reconsider the superproperty definition explicitly. Broadening precedence to `e_A <= s_B` for proper intervals would admit contact, but changes the proposed strict meaning and needs migration and boundary tests. Zero-duration point cases require their own semantics.

## Candidate OWL structure

The following is illustrative future SULO vocabulary. It is not an importable application extension and must not be added to the v2.4 instance graph or vendored ontology as part of adopting this document.

```turtle
@prefix sulo: <https://w3id.org/sulo/> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

sulo:precedes a owl:ObjectProperty, owl:TransitiveProperty .

sulo:directlyPrecedes a owl:ObjectProperty ;
    rdfs:subPropertyOf sulo:precedes ;
    owl:inverseOf sulo:isDirectlyPrecededBy .

sulo:isDirectlyPrecededBy a owl:ObjectProperty ;
    rdfs:subPropertyOf sulo:isPrecededBy .
```

The existing inverse axiom between `precedes` and `isPrecededBy` is assumed. Their Process domain and range carry through the proposed subproperty hierarchy.

Given `A directlyPrecedes B` and `B directlyPrecedes C`, these axioms entail `A precedes C`. They do not entail `A directlyPrecedes C`.

Omitting a transitivity declaration does not forbid that last direct edge. OWL has no `NonTransitiveProperty` declaration, and the candidate axioms do not express the absence of an intermediate process. Adjacency validation remains a separate obligation.

A subproperty can remain simple even when its superproperty is transitive, provided it has no transitive or chain-defined subproperties. Optional asymmetry or cardinality restrictions can therefore be considered on the direct property. Do not make it globally functional: branches may have multiple successors. The transitive superproperty cannot itself be declared asymmetric or irreflexive in OWL 2 DL. Check downstream uses that require simple properties before releasing the transitivity change. [OWL 2 property hierarchy and restrictions](https://www.w3.org/TR/owl2-syntax/#Global_Restrictions_on_Axioms_in_OWL_2_DL)

## Sequence scope and incomplete knowledge

For a specified sequence scope S, the intended cover relation is:

$$
\operatorname{direct}_S(A,B) \iff A,B\in S\ \land\ \operatorname{precedes}(A,B)
\ \land\ \neg\exists C\in S:
\operatorname{precedes}(A,C)\land\operatorname{precedes}(C,B).
$$

This is a definition of intended scoped adjacency, not an OWL axiom supplied by the candidate structure. A scope must specify eligible members, process granularity, ordering interpretation, and the source/semantic snapshot used to establish membership and order. A scope with concurrent processes may form a partial order instead of a linear sequence.

For example, two administrations may be consecutive administrations while a measurement occurs between them. Including that measurement in the selected sequence changes adjacency. Incomplete source data or newly inferred eligible process types can also change a computed adjacency view.

The unqualified binary property does not encode S. Until the context representation is settled, evaluate adjacency as a scoped query operation or retain it in an explicitly interpreted assertion view. Do not flatten conflicting scope-dependent direct edges into one canonical timeless graph and then claim a global adjacency interpretation. A named graph alone does not define the required OWL context semantics.

An authoritative sequence assertion can provide direct succession as source knowledge. In contrast, computing that assertion from missing graph edges requires an explicit completeness policy. A query such as `FILTER NOT EXISTS` establishes absence in its evaluated view; it does not prove absence in every OWL model.

## Efficient execution

Store direct edges where succession is supported, and compute transitive consequences on demand or in a bounded cache. A linear sequence of n processes uses n−1 direct edges, while its complete precedence closure has n(n−1)/2 ordered pairs. General partial orders need not have a linear-size set of cover edges.

Retain normalized endpoints for timestamp-derived order. Explicit precedence facts can also exist independently of known direct chains. The subproperty axioms do not state that every precedence pair must be reachable through recorded direct edges, so direct-edge traversal alone is not a complete precedence evaluator in general.

Cache keys must include sequence scope, granularity, graph revision, semantic bundle, clock interpretation, and ordering policy. If new evidence inserts an intermediate process, invalidate the affected adjacency view while preserving ordinary precedence consequences that still hold. Shared patient identity continues to use the PRO role/bearer binding.

## Acceptance scenarios for adoption

These are proposed tests, not results from the current executable suite.

| Scenario | Required outcome |
|---|---|
| A directly precedes B; B directly precedes C | Infer A precedes C; do not infer the direct shortcut from these axioms |
| A ends at 10; B starts at 12; no member lies between in the declared complete scope | Strict precedence; direct succession may hold despite the gap |
| A ends at 10; B starts at 10 | Temporal contact; no strict-precedence edge for that pair |
| A starts before B but ends after B starts | Start order does not establish whole-interval precedence |
| A measurement is inserted between two administrations | Treatment-only adjacency can remain; a sequence including measurements must be reevaluated |
| The selected record scope is incomplete | Absence of an intermediate record alone cannot establish adjacency certainty |
| A direct-edge cycle is supplied | Report an invalid strict-order input through validation; transitivity alone does not reject it |
| Endpoint clocks cannot be reconciled | Do not derive timestamp order or contact |

Illustrative sketches for each row above, including sample process intervals and the unresolved questions specific to that row, are in [`examples/temporal-precedence-proposed/`](../../examples/temporal-precedence-proposed/README.md). They are not executable and not a preview of adopted behavior.

## Remaining decisions

Before implementation, resolve the canonical name, sequence/context representation, and whether the intended use is strict succession or zero-gap contact. Then assess downstream OWL compatibility, add conformance cases, and version the SULO dependency explicitly. The current class-only application contract remains usable while these upstream questions are resolved.
