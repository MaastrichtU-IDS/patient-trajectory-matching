# Clinical terminology candidates

Status: proposals for domain review, researched on 2026-09-16. No clinical mapping is accepted, no source-acceptance policy is changed, and no clinical mapped query has executed.

**Recommendation:** start with an ingredient-level record selector for norepinephrine, preserve the three pressure source strata, and add measurement-method specificity only when the source evidence supports it. The table gives concrete targets; the justification for mapping each source item remains an application-level proposal.

## Proposed targets

| MIMIC demo 2.2 source item | Proposed target | Basis and unresolved condition |
|---|---|---|
| 221906 — Norepinephrine | RxNorm **7512**, ingredient | The dictionary names the drug; the NLM lookup returns the same name with term type `IN`. Review whether the admitted records support this ingredient-level content category. Do not identify the recorded process with the substance. |
| 220052 — Arterial Blood Pressure mean | LOINC **8478-0**, Mean blood pressure | Its method is unspecified. Retain item identity. Consider **76214-6** only after confirming invasive measurement for this item. |
| 225312 — ART BP Mean | LOINC **8478-0** | Review this item independently of 220052; the similar label does not establish duplicate records or interchangeable measurements. The same conditional invasive alternative applies. |
| 220181 — Non Invasive Blood Pressure mean | LOINC **76536-2**, Mean blood pressure by Noninvasive | The method matches the dictionary label at this level. Confirm the source meaning and unit policy; do not add continuous monitoring, cuff type or an algorithm. |

The source labels, table linkage and units were re-read from the pinned dictionary. The four rows and hashes are in [dictionary evidence](../data/terminology/evidence/clinical-dictionary-items.json). These metadata do not independently verify clinical measurement methods. The official target records are [RxNav 7512](https://mor.nlm.nih.gov/RxNav/search?searchBy=NameOrCode&searchTerm=7512), [LOINC 8478-0](https://loinc.org/8478-0), [76536-2](https://loinc.org/76536-2) and [76214-6](https://loinc.org/76214-6).

## Norepinephrine: a record category, not a product or process identity

The [captured NLM API responses](../data/terminology/evidence/rxnorm-7512-2026-09-16.json) contain an exact-name lookup, concept properties and the service version observed during this research. They identify 7512, `IN`, and unsuppressed status. The service reported data version `08-Sep-2026` and API version `3.1.355`. These are parsed responses with canonical JSON hashes, not a downloaded full release or an atomic multi-request snapshot. NLM documents the [properties endpoint](https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getRxConceptProperties.html) and [version endpoint](https://lhncbc.nlm.nih.gov/RxNav/APIs/api-RxNorm.getRxNormVersion.html).

The source dictionary's `Solution` type and `mg` unit do not identify a salt, concentration, brand, administered dose or precise clinical product. RxNorm distinguishes drug products by ingredient, strength and dose form; the proposed `IN` target deliberately stops at ingredient-level recorded content. [RxNorm technical documentation](https://www.nlm.nih.gov/research/umls/rxnorm/docs/techdoc.html).

The proposed implication is:

`mimic-record-item/221906 ⊑ record-query/rxnorm/7512`

The right-hand class is an application record selector associated with the RxNorm identifier. Its intended meaning is “a recorded input segment whose accepted recorded content is categorized as norepinephrine.” The external concept IRI remains mapping metadata. There is no `equivalentClass` or `sameAs` assertion, no drug-as-process type assertion, and no inference of actual administration, route, dose equivalence or treatment-course initiation. Existing PRO role/bearer witnesses and SOLID value representation remain unchanged.

The [pending pack](../data/terminology/mimic-demo-2.2-pending/) now contains all four documents: the source catalogue, terminology subset, one mapping proposal and an empty review journal. Its version field records the observed RxNorm data version. Before acceptance, retain or revalidate the exact terminology evidence and update the bound documents if anything changes. A review decision must concern the supplied proposal and evidence; a matching drug name is not itself an acceptance decision.

## Pressure: preserve method and source distinctions

LOINC [8478-0](https://loinc.org/8478-0) identifies a quantitative, point-in-time pressure observation in the arterial system without specifying a method. This supports proposing a common record-query category while retaining each source item. It supplies no OWL subclass hierarchy between LOINC concepts and no permission to pool measurements.

The conditional alternative [76214-6](https://loinc.org/76214-6), Invasive Mean blood pressure, adds an invasive method. Obtain source documentation or device/workflow evidence before choosing it for either arterial-labelled item. Do not infer that method solely from the word “arterial.”

For the noninvasive item, [76536-2](https://loinc.org/76536-2) avoids the extra commitment of [75996-9](https://loinc.org/75996-9), Mean blood pressure by Continuous non-invasive monitoring. The latter specifies CNAP and should not be selected from a generic noninvasive label. The 76536-2 page lists `cm[H2O]` under example units; the source dictionary uses literal `mmHg`. Record this discrepancy for unit-policy review, not as an instruction to convert values. The current engine continues to compare the same item and literal unit only.

The [measurement candidate document](../data/clinical-measurement-mapping-candidates.json) keeps decisions and target release versions empty. The term pages' “Last Updated” values are changes to individual terms, not evidence of a pinned current LOINC release. Acquire and identify the intended release before producing executable terminology snapshots. No measurement mapping pack or ontology import is generated here.

## Concrete review decisions

| Decision | Requested evidence or choice |
|---|---|
| Norepinephrine content | Accept, revise or reject the ingredient-level record meaning for item 221906; identify the reviewer and rationale. |
| Two arterial-labelled items | Use the method-unspecified proposal, or supply evidence supporting invasive coding for each item separately. |
| Noninvasive item | Confirm noninvasive mean pressure; retain unspecified device/algorithm and resolve the unit-policy question. |
| Release and query scope | Pin terminology evidence, retain item-specific strata, and decide whether any future cross-item query is scientifically justified. |

Record the norepinephrine decision in `review.json` using the existing mapping-row hash. Keep source-claim acceptance separate and bind it explicitly to any subsequently compiled policy. Pressure decisions first inform the measurement-mapping design; they cannot be executed by the current interval-only compiler adapter.

After review, the next implementation steps are qualified measurement selectors, context-bound reuse of source audits in prepared sessions, and complete Rust/temporal/SQL comparison before exposing terminology choices in the UI. The existing clinical worksheet remains pending and unmodified.

## Terminology attribution

LOINC content is attributed to Regenstrief Institute, Inc. and the LOINC Committee; see the [LOINC notice](../data/terminology/LOINC_short_license.txt) and [license](https://loinc.org/license). The material here is a small referenced candidate set, not a redistributed LOINC release. NLM describes access terms for its [RxNorm API](https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html).
