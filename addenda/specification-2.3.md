# Version 2.3 temporal replay and Graphiti assessment

Status: specified, not implemented or benchmarked. The v2.0 executable schemas and oracle are unchanged.

A temporal knowledge graph is a knowledge graph equipped with an explicit temporal interpretation: the meaning of its assertions includes when they apply and how represented events are related in time. For this product, the graph is also versioned and source-grounded, so that interpretation can be evaluated against a specified product snapshot and source-information cutoff. It represents supported claims, unresolved information and conflicts; timestamps alone do not establish truth.

Each assertion has a declared temporal scope: timeless, a specified instant or interval, a constrained set of possible times, or unknown. Unknown scope is not timeless scope. Time values refer to named clocks with explicit origin, unit and precision; incompatible clocks are not silently compared. Temporal relations connect identified events or intervals under the semantics in section 6.

Occurrence time and assertion applicability are distinct. A statement that a specimen was measured on Monday describes a historical event; it does not become false on Tuesday. A claim that a patient occupied a bed during an interval applies to that interval. A correction changes the supported assertion about an event, rather than moving or deleting the event itself.

Operationally, a temporal view first selects the assertion history present in the chosen product snapshot, then applies the named source-availability and revision policy, and finally evaluates the requested temporal predicates. Applicable, inapplicable, unresolved and conflicting evidence remain distinguishable. A trajectory query evaluates relations across the selected history; it is not limited to a single instantaneous view. This interpretation, together with the graph structure and provenance, is the defining contract.

[[PAGEBREAK]]
### Observation identity and assertion revision

The platform MUST distinguish a new clinical event, an additional record of the same event, a correction to an assertion, a change of state and conflicting evidence. A newer ingestion timestamp alone cannot establish which operation occurred. Identity and revision decisions use source identifiers, specimen or administration context, explicit correction links and a versioned mapping policy. Unresolved conflicts retain their sources and follow section 5.

For a constructed example, specimen M1 is measured on Monday and reported as creatinine 1.0 mg/dL. A different specimen M2 is measured on Tuesday and reported as 1.5 mg/dL. On Wednesday, the laboratory corrects the result for M1 to 1.2 mg/dL. M1 and M2 remain distinct observations. The correction creates a successor assertion for M1, linked to its predecessor and source correction; M2 does not supersede M1. Neither result is modelled as a patient state lasting until the next measurement.

Assertion history MUST preserve both original and corrected values, their source availability and product publication metadata. An authoritative correction may supersede a prior assertion under the named source policy. A merely contradictory statement remains competing evidence. Ending a medication process, correcting its recorded end and withdrawing an erroneous administration record are different operations with different provenance.

Source recording time and source availability time are separately typed where both exist. An adapter may equate them only through a documented source rule. Missing availability or revision history remains unknown. The platform must not derive clinician knowledge from the date on which a retrospective dataset was imported.

Temporal replay uses an immutable published snapshot as its evidence archive. Source-as-known replay applies a declared availability cutoff and only correction or supersession information available by that cutoff. It may therefore select an earlier source assertion preserved in the archive, even when a later source correction is also present. Retrospective reconstruction may use later corrections, but MUST carry a different mode label. Both modes exclude archive entries unavailable to the selected product snapshot. Original source history, reconstructed history and product publication history cannot be silently interchanged.

[[PAGEBREAK]]
### Temporal replay and evidence availability

Temporal replay lets a researcher inspect how a patient history and its cohort change with clinical time, information availability and the platform snapshot. It extends the existing index control and revision history. The initial workspace keeps the normal clinical/index-time control visible; a labelled Replay control opens the additional settings. H0 specifies and checks the constructed cases. R1 implements replay only for datasets with an advertised history capability.

| Control | Meaning | Required feedback |
|---|---|---|
| Clinical or index time | Clinical window or per-patient index rule used by the query | History and follow-up boundaries; elapsed, calendar or shifted-clock meaning |
| Source availability cutoff | Latest information eligible under the source-as-known policy | Per-patient anchor and offset, source timestamp role, unknown coverage |
| Platform snapshot | Published archive and semantic versions available to the run | Snapshot identity and availability of historical assertion versions |
| Replay mode | Source-as-known or retrospective reconstruction using later corrections | Persistent mode label and explicit indication of later evidence use |

The availability cutoff is a named per-patient rule for cohort comparisons, such as information available by each patient's index. It is not one absolute date applied to unrelated shifted patient clocks. Numeric and keyboard controls accompany any slider. Users can inspect raw timestamps, source time roles and normalization evidence without changing query meaning. Changing a cutoff, mode or snapshot creates a semantic revision; panning or zooming the timeline does not.

The evidence drawer shows an observation's identity, original and successor assertions, correction source, clinical time, source recording and availability times where known, and product publication history. Superseded assertions stay inspectable. A missing timestamp is labelled unknown; a missing archive is labelled unavailable. The interface MUST NOT display a fully reconstructed source-as-known history when the source only supports a retrospective current-state export.

Replay coverage is COMPLETE, PARTIAL or UNAVAILABLE, with reasons and affected records or predicates. This field is independent of computation_status: a completed run can have partial historical evidence. Unknown availability follows an explicit policy and cannot silently pass a pre-index information filter. If it could change an answer, the affected membership remains unresolved. Replay evidence does not establish what a clinician actually read or knew.

[[PAGEBREAK]]
### Comparing replay results

Two completed runs may be compared by stable patient or episode identifiers. The comparison fixes the query, ontology bundle, population and index rules where possible, and changes one temporal control at a time. It reports added, removed, unchanged and unresolved membership, together with the supported difference in source evidence. If a corrected record changes a derived index, eligibility or baseline selector, that dependency is shown and recomputed. A fixed-index diagnostic comparison is a separate, explicitly labelled analysis.

Membership explanations distinguish a new clinical event, a late source record, a corrected assertion, a changed query and changed semantic or normalization versions. More than one change can apply to the same patient. The interface reports an undetermined attribution when it cannot isolate a cause; it must not infer a clinical causal explanation from a data difference. Reranked top-k changes are displayed separately from changes in cohort eligibility.

In the constructed M1/M2 example, a retrospective pattern asks for a recorded rise of at least 0.3 mg/dL within 48 hours. M1 = 1.0 and M2 = 1.5 support an exact match before the correction. After M1 is corrected to 1.2, the rise is still exactly 0.3, so membership is unchanged while the baseline evidence changes. A boundary variant with M2 = 1.4 changes from exact to excluded after correction. Replaying the earlier source-availability cutoff retains the earlier assertion in both examples when the archive supports it. These are illustrative query tests, not diagnostic definitions.

Every replay manifest includes the clinical/index rule, source-availability rule, replay mode, product snapshot, semantic and normalization versions, correction policy, coverage report, query hash and result identity. Caches and asynchronous responses bind to all these fields. A late response for an earlier replay cannot replace the active view. Unknown coverage and exploratory use of later corrections remain visible in frozen cohorts and exports. Existing access checks apply to historical versions as well as current evidence.

Acceptance requires users to explain why two measurements coexist, why a correction changes an earlier assertion, why a late result cannot enter a pre-index source-as-known view, and why a fully computed result can still have partial replay coverage. Add these tasks to the section 20 usability study and the temporal cases in section 24. The existing wireframes remain the base workspace design; the replay controls are specified here as an extension awaiting implementation.

[[PAGEBREAK]]
### Zep and Graphiti architecture assessment

Decision ADR23 keeps the clinical event model, deterministic structured ETL, ontology compilation and cohort matcher authoritative. Zep and its open-source Graphiti framework are relevant prior art and an optional comparison target for narrative context and candidate retrieval. Adoption requires task-specific evidence; neither component is a mandatory H0 dependency.

Zep's article emphasizes preserving temporal information and the origin of assertions. The associated paper separates source episodes, extracted entity facts and community summaries. An episode in that architecture is an ingestion unit, such as a message or document; it must not be mapped automatically to a hospital encounter or an episode of care. The source-to-assertion linkage is useful for our evidence design. [S23, S24]

Graphiti describes hybrid embedding, keyword and graph retrieval and custom entity and edge types defined through Pydantic models. Those capabilities can support structured contextual retrieval. A typed application schema does not establish SULO alignment, OWL entailment, clinical equivalence or the exact and relaxed query semantics required here. These obligations stay with the semantic bundle and matcher. [S25]

| Capability | Potential reuse | Required clinical contract |
|---|---|---|
| Source episodes and assertion links | Preserve evidence for retrieved context | Distinct event, encounter, source record and assertion identities |
| Temporal assertion lifecycle | Preserve change and supersession | Correction rules and historical replay without erasing repeated observations |
| Incremental graph updates | Refresh a contextual index | Immutable published snapshots and recomputation of affected derived results |
| Hybrid retrieval | Explore narratives or generate candidates | Candidate recall measured separately from complete cohort execution |
| Custom entity and edge types | Constrain application payloads | Pinned SULO and domain ontology semantics with inference evidence |

The paper describes semantic contradiction detection and invalidation of overlapping older facts, with preference for newer ingestion. Our adaptation MUST distinguish contradiction, clinical change and correction before any supersession. The M1/M2 example in section 4 is a mandatory counterexample to automatic replacement of a patient's earlier measurement. Timestamped assertions remain claims with provenance; their presence does not prove truth or guarantee contradiction-free answers. [S24]

[[PAGEBREAK]]
### Integration boundaries and evidence limits

The article's four conceptual timestamp labels are valid-from, valid-to, observed and recorded. The Graphiti edge implementation inspected for this revision exposes created_at, expired_at, valid_at, invalid_at and reference_time. Before any adapter is enabled, pin the relevant version and document the actual lifecycle and timestamp mapping. Field names alone do not demonstrate complete historical reconstruction or the source-availability semantics required in section 20. [S23, S26]

The reviewed material does not establish support for our full interval algebra, uncertain temporal values, calendar and age normalization, exhaustive trajectory binding or ontology-aware alignment costs. This is a boundary of the assessment, not a claim that every such extension is impossible. Graphiti's inspection here was limited to documentation and source; no runtime compatibility or performance result is claimed.

An optional adapter receives source-derived records or reviewed narrative assertions with stable provenance references. It returns contextual assertions or candidate identifiers with component and snapshot versions, scores and source links. Untraceable output is ineligible as match evidence. The complete matcher verifies candidates under the canonical query and reports any retrieval limitation. An ANN or other incomplete candidate route cannot be labelled complete exact cohort execution.

LLM extraction MAY propose assertions from narratives in a permitted environment. Proposals preserve source spans, model and prompt versions, context, polarity, time interpretation and review status. They cannot replace deterministic mappings for structured MIMIC or Synthea records, silently resolve conflicting clinical facts or activate a semantic substitution policy. Hosted and local model availability, deployment costs and the no-JVM implementation constraint are assessed for the selected configuration. Development subscriptions do not imply API credits or authorize patient-data egress.

The published experiments concern conversational memory on DMR and LongMemEval. They do not measure clinical trajectory membership, interval constraint correctness or source-grounded alignment quality. Their results motivate a comparison, not a clinical performance claim. General temporal graphs, provenance and retrieval are established architectural territory. The project's differentiation hypothesis is their combination with formal clinical time and ontology semantics, progressive patient-to-cohort refinement, explicit relaxation costs and reproducible clinical evaluation. This hypothesis requires comparison with relevant prior work. [S24]

[[PAGEBREAK]]
### Bounded Graphiti comparison protocol

Assign one engineer up to six focused hours after the H0 integration path works. Use constructed records only. Pin the Graphiti commit, backend, models, prompts and extraction settings; record resource use and any unsupported operation. If setup exhausts the time budget, publish the capability findings and defer execution. This comparison is optional adoption evidence and does not block the core hackathon delivery.

Evaluate two configurations where supported: direct insertion of structured assertions, which isolates storage and retrieval behaviour, and extraction from equivalent synthetic narratives, which also measures interpretation errors. Document any model processing that remains in the direct route. Run each stochastic ingestion three times, retain outputs and report variation; repeatability is measured rather than assumed from a decoding setting. Keep a small independent enumeration of the intended event and assertion semantics as the reference.

| Case family | Constructed change | Required reference behaviour |
|---|---|---|
| T01 Repeated observations | M1 = 1.0 on Monday; M2 = 1.5 on Tuesday | Preserve both observations and their source links |
| T02 Source correction | Wednesday corrects M1 to 1.2 | Preserve both assertion versions; current evidence changes without deleting M2 |
| T03 Late availability | A pre-index specimen result becomes available after index | Exclude it from source-as-known pre-index features |
| T04 Competing claims | Two sources disagree without a correction relationship | Retain conflict; newer ingestion alone is insufficient authority |
| T05 Semantic alternatives | DrugAChild, permitted DrugB and unapproved DrugC | Entailment costs zero; only the named alternative is relaxed |
| T06 Time meaning | 48 hours, two calendar days and an unknown timezone | Apply the declared clock and unit semantics; preserve unresolved cases |
| T07 Missing history | Missing baseline, availability metadata or assertion archive | Keep record absence, unresolved evidence and replay coverage distinct |
| T08 Candidate scope | Relevant patterns mixed with unrelated events and distractors | Measure missed candidates against exhaustive reference membership |

For the bounded cohort tests, report candidate recall as the number of reference-eligible patients present in the candidate set divided by the total number of reference-eligible patients. Report shortlist size and budget, with zero-denominator cases marked not applicable. This differs from the top-k neighbour metric defined earlier in this section. Report final cohort agreement and alignment correctness after canonical verification separately; a high average recall cannot certify exhaustive retrieval.

Other outputs are observation and assertion preservation, correct supersession, evidence traceability, source-as-known replay agreement, extraction error categories, ingestion cost and end-to-end latency with hardware and cache state. Unsupported replay is reported as unsupported, not converted into an accuracy score. Keep clinical reviewers' interpretation of context separate from mechanically checkable fixture outcomes.

The adoption report records which component, if any, helps and at what cost. Optional use requires preservation of mandatory identity and provenance cases, explicit handling of unsupported semantics, and measured usefulness against the existing route. No adoption decision is based solely on conversational QA scores. Wider MIMIC testing follows only in the authorized environment after a concrete benefit is demonstrated; the comparison does not expand current data-access permissions.

[S23] Zep. Temporal Knowledge Graph for AI Agents. Article inspected 14 September 2026. https://www.getzep.com/ai-agents/temporal-knowledge-graph/

[S24] Rasmussen P, Paliychuk P, Beauvais T, Ryan J and Chalef D. Zep A Temporal Knowledge Graph Architecture for Agent Memory. arXiv 2501.13956v1, 2025. https://arxiv.org/html/2501.13956v1

[S25] Zep and contributors. Graphiti repository and architecture documentation. Moving source inspected 14 September 2026; pin a commit for comparison. https://github.com/getzep/graphiti

[S26] Zep and contributors. Graphiti entity edge implementation. Moving source inspected 14 September 2026; field mapping requires a pinned version. https://github.com/getzep/graphiti/blob/main/graphiti_core/edges.py

