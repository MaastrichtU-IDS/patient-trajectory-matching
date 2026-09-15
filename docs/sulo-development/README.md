# SULO development proposal from patient trajectory matching

**Status: proposed work programme, 15 September 2026.** This package records lessons and candidate requirements. The subsequent [claim-projection prototype](../claim-projection.md) supplies bounded implementation evidence for D2; the baseline findings and broader adoption gates below retain their original scope. It does not change SULO, the executable profiles, clinical policies, or the conformance status of the original 18 formal checks. Requirements below are proposed acceptance conditions, not an adopted SULO standard.

**Evidence baseline:** patient-trajectory-matching commit [`85943a9`](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/commit/85943a9d82e763354201798490d947146c1a43d5), with [pinned SULO 0.2.14](../../ontology/sulo-pin.json) and checked rustDL 0.4.28 support. Later releases must be evaluated separately.

## What the work establishes

Our working hypothesis is that a small upper vocabulary, reusable representation patterns, and explicit computational contracts can support precise, auditable temporal applications. The implementation supplies bounded feasibility evidence. It does not yet establish superiority, general clinical validity, or minimality in a formal sense.

| Finding | Evidence in this repository | Limit of the conclusion |
|---|---|---|
| Application classes and individuals can reuse SULO relations | [PRO/SOLID contract](../../addenda/specification-2.4.md), [class module](../../ontology/pro-solid-profile.ttl), [bounded module](../../ontology/bounded-interval-profile.ttl) | Few properties does not imply few triples, simple queries, or low modelling effort |
| Patient capacity can remain explicit through reasoning and matching | [Role-mediated semantic support](../semantic-support.md) | Synthetic role inference; no validated clinical terminology mapping |
| Joint temporal constraints and fixed named witnesses support auditable matching | [Bounded uncertainty](../bounded-temporal-uncertainty.md) | Integer microseconds, named finite candidates, admitted operators and clocks only |
| Different representations can preserve selected query answers | [Eight paired formal/SULO fixtures and 33 comparisons](../temporal-kg/sulo-interface.md) | Query-fragment preservation, not ontology equivalence or a general mapping theorem |
| A source-to-answer path can preserve exclusions and evidence | [Record-query pipeline](../mimic-record-query.md), [aggregate demo report](../../verification/mimic-record-query-demo-report.json) | 20,404 input rows reconciled; only three episodes required semantic/temporal evaluation; not a scalability benchmark |
| Records and occurrence need different interpretations | [Recorded-process convention](../mimic-record-query.md) | Application flags do not supply epistemic or contextual OWL semantics |

The baseline reports **357 checks: 334 suite tests, 16 oracle cases and seven properties**. These test several components and contracts; they are not 357 independent demonstrations of SULO's correctness. The standalone 18 OWL checks concern a separate formal ontology and retain their existing [mapping obligations](../temporal-kg/sulo-conformance.json).

## Three deliverables

| Deliverable | Concrete content | Completion gate |
|---|---|---|
| [D1. Candidate normative pattern catalogue](pattern-catalogue.md) | PRO participation, SOLID values, source identity and temporal descriptions; explicit graph and query contracts | Each adopted pattern has valid/invalid fixtures, supported entailments, validation responsibilities and a declared reasoning profile |
| [D2. Record-versus-occurrence pattern](record-and-occurrence.md) | Separate source content, source-derived assertions and accepted occurrence assertions; explicit promotion policy | A withdrawn or unaccepted claim cannot enter the accepted occurrence view through an unintended inference path |
| [D3. Temporal interoperability profile](temporal-interoperability.md) | Identity, coordinates, clocks, operators, uncertainty and evidence-preserving translation | Independent implementations agree on the declared query fragment, including failures and unknowns |

D1 can consolidate the existing patterns immediately. D2 needs a representation decision before extending clinical claims. D3 can document the existing interval fragment now, then add measurement timestamps as a separately versioned extension. The catalogue is a candidate upstream contribution; adoption in AIDAVA-DEV/sulo remains a separate release decision.

## Evaluation that could support or refute the hypothesis

Use the same source evidence, clinical definitions, query tasks, temporal engine and hardware across these conditions:

1. SULO with published patterns and validators.
2. An explicit domain schema with dedicated relations, using OWL-Time for temporal descriptions and equivalent validation.
3. A direct relational representation evaluated by independently specified SQL/reference queries.

Compare semantic inference only on tasks for which each condition has an explicit equivalent implementation. The SQL condition is a task-level reference, not evidence that SQL and OWL have identical semantics. Use an additional SULO-without-pattern-guidance condition to separate vocabulary effects from documentation/tooling effects.

| Question | Measurement and control |
|---|---|
| Are answers preserved? | Exact named bindings, certainty status, exclusions and evidence agree on frozen cases; report every discrepancy |
| Does minimal vocabulary reduce total effort? | Modelling and correction time, task accuracy, application class/property count, triple count and query complexity; give equal training/tool support |
| Are explanations usable? | Whether independent users correctly identify the patient role, source claim, temporal witness and limitation of an answer |
| Is execution efficient? | Ingestion throughput, peak memory, cold/warm latency and completion rate as events, candidates and rules grow; report limit failures as failures, not omitted runs |
| Does the approach transfer? | A second source encoding with the same competency questions, with explicit mapping losses and clinical review |

Freeze tasks, expected answers and evaluation criteria before comparing implementations. A human study needs its own recruitment and sample-size plan; no sample size is implied here. A result in which SULO uses fewer properties but increases error or overall effort would weaken the practical simplicity claim. Failure to preserve a required distinction without extensive application conventions would motivate revising the pattern or the core commitment.

## First application of the deliverables

Use the proposed **low recorded MAP before a norepinephrine segment, followed by blood-pressure observations** example. It tests measurement results, units, observation timestamps, interval anchors, source descriptions and later outcome aggregation in one workflow.

The first implementation should select baseline/treatment eligibility before computing outcomes, preserve absent follow-up as unknown, and describe changes as observed associations. It should use synthetic fixtures before any public-demo measurement run. The subsequent [source mixed-query pipeline](../source-mixed-query.md) implements the technical path with synthetic fixtures, and the [clinical source preflight](../clinical-source-preflight.md) now measures public-demo candidate coverage and capacity barriers. Clinical review and real-source mixed-query evaluation remain pending.

Suggested delivery sequence:

1. Review D1–D3 and settle the assertion-view decision in D2.
2. Publish the selected pattern fixtures and operation-specific conformance cases.
3. Implement measurement ingestion, value/unit filtering and point/interval comparisons in new profiles.
4. Complete the proposed clinical example with a reviewed source mapping and independent reference answers.
5. Run comparative evaluation; propose upstream SULO changes only where the evidence identifies a core issue.
