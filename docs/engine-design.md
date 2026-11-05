# Engine design: matching, results, scale, similarity and complexity

Status: engineering analysis, 5 November 2026. It records how the matching engine works today, how the deployed workspace actually produces its results, and a proposed design for trajectory matching and similarity over millions of patients, with the complexity of the queries involved. Statements about the current code are measured or cited; the scale design, cost estimates and recommendations are proposals for decision, not implemented capabilities. The project-level overview is the [design document](design.md).

## 1. The matching engine today

The engine is `patterns/`, with the dependency-free oracle `reference_oracle.py`. It is a pipeline with several matching profiles, each deciding a precisely bounded kind of question, not one general matcher.

```
records ─► build graph ─► validate ─► select evidence ─► candidates per step ─► search bindings ─► classify ─► result + evidence
           (PRO/SOLID)    (SHACL +    (availability,     (concept entailment    (exhaustive /      (exact, relaxed,
                           contracts)  revisions,          or derived classes)   indexed join /     possible, certain,
                                       completeness)                             temporal network)  unresolved, …)
```

1. **Build and validate.** Source rows become a PRO/SOLID RDF graph: participation through roles, values through typed information objects. SHACL shapes plus procedural checks run before projection. A broken input raises a `ContractError`; it is never reported as a non-match.
2. **Select evidence.** Source-as-known selection uses only what was available by a cutoff; retrospective selection may use later corrections, under its own label. Revision chains are explicit. Whether a search was complete is derived, not assumed: relaxing a query widens what must have been searched, so a search complete for the exact question can be incomplete for the relaxed one, and is then `UNRESOLVED`.
3. **Candidates per step.** A record qualifies if its concept entails the requested one, through a subclass taxonomy or through rustDL-derived class membership. Entailment costs nothing. Only performed events about the patient count.
4. **Search bindings.** One event per step, all temporal constraints satisfied, joined on the same person, never the same role individual.
5. **Classify** and attach evidence identifiers that lead back to graph resources and source records.

| Profile | Code | Mechanism |
|---|---|---|
| Point anchor | `reference_oracle.py` | Exhaustive search over a fixed three-step pattern (baseline lab, exposure, follow-up lab). A hard value rule is never relaxable; a reviewed concept alternative costs 1 and an extended exposure window costs up to 1. For uncertain exposure times it prices the worst feasible case, then takes the cheapest binding. Standard library only. |
| Exact interval | `exact_intervals.py` | Pairwise temporal operators over recorded intervals |
| Interval cohort | `interval_cohort.py` | Conjunctive slot queries across episodes; an indexed engine (per-clock start indexes, smallest-domain join order, incremental pruning) checked against an exhaustive reference (`interval_cohort_reference.py`) |
| Bounded interval | `bounded_cohort.py`, `temporal_stn.py` | Times known within bounds; see below |
| Robust relaxation | `robust_relaxation.py` | A finite catalogue of declared modifications with constant costs; a relaxed binding is accepted only if it holds in every feasible timeline |
| Mixed record query | `mixed_record_query.py` and the source pipeline | On recorded data: baseline and treatment eligibility, then follow-up inspection, with every anchor cross-checked against SQL |
| Similarity | `patient_similarity.py` | Query by example over pre-index features, with coverage beside the score |

**The bounded engine.** Every interval endpoint is an integer variable on a discrete grid. Recorded bounds and query constraints become difference edges (`a − b ≤ k`) of a simple temporal network: *before* is end(A) < start(B); *meets* is end(A) = start(B); *overlaps* is the three Allen inequalities; a bounded gap is min ≤ start(B) − end(A) ≤ max. Shortest paths in exact integers, with replayable path certificates, decide:

| Status | Condition | Evidence returned |
|---|---|---|
| Impossible | Source plus query has a negative cycle | The cycle |
| Possible | Source plus query is consistent | A witness timeline |
| Certain | Every query edge is entailed by the source alone, never by the source narrowed by the query | The entailing paths |
| Incomparable | The events are on different clocks | The comparable part's witness |

Each fast engine has an independent reference implementation compared differentially (interval cohort, bounded, semantic, source mixed query), 938 contract checks cover the profiles, and every committed report is bound by digest to the files that produced it.

Limits: the temporal profiles answer different query formats ([issue T1](issues.md#t1-the-four-profiles-are-not-unified-open)); the oracle's pattern is fixed; the interval and bounded profiles cover four operators, not the full Allen catalogue; there is no absence ("no X between A and B") and no disjunction between steps; bounded times are discrete; it is not an OWL reasoner and makes no clinical or causal claim.

## 2. How the deployed workspace produces its results

The workspace (`app/`) was run in-process as deployed (`make_server` with default arguments), and the functions that build or validate graphs and produce results were wrapped and counted while each page's API was driven:

| Request | Executed |
|---|---|
| Startup | No graph built or validated |
| `POST /api/initial`, `/api/refine` (similarity, refinement) | Nothing above: ranking over a JSON fixture |
| `POST /api/trajectory` (exact versus relaxed cohort) | 18 oracle evaluations; no graph |
| `GET /api/evidence` | No graph |
| `POST /api/journey/run` | One bounded PRO/SOLID graph built |
| `POST /api/temporal/run` | One bounded graph built; two temporal-network cohort executions |
| Any request | **No SHACL validation** |

How each result is generated:

1. **The authored cohort was built as SULO graphs, offline.** `demos/presentation/build_guided.py` calls `services.cohort.build_dataset`, which runs `pro_solid.build_graph()` and `project()` (SHACL plus procedural checks) for each of the eleven patients. The committed `examples/guided-cohort/cohort-data.json` holds the projected events, evidence bindings and source rows, pinned by digest. The server never rebuilds or revalidates those graphs.
2. **Similarity and refinement** rank over `examples/patient-similarity/dataset.json`, derived from the same projections with an authored availability overlay.
3. **The exact-versus-relaxed comparison** runs `reference_oracle.evaluate` on each candidate's projected events. Concept matching uses the JSON toy taxonomy (`ontology/toy-taxonomy.json`), not OWL reasoning.
4. **The journey and temporal pages** build a bounded-interval PRO/SOLID graph with `rdflib` on every run (`robust_relaxation.execute` → `bounded_intervals.prepare` → `build_graph`). Matching runs on temporal networks compiled from the validated source JSON; the profile states that "source JSON is authoritative … RDF is a lossless evidence projection". The graph's digest enters the result's context and evidence identifiers point into it.
5. **The recorded-evidence section** runs claims, closed claim RDF trees (`claim_rdf`), review policies and Rust, literal and SQL cross-checks; the deployed default is the synthetic example.

What is verified as SULO-conformant, and where:

| Verified | Where |
|---|---|
| Nine ontology profile files and eleven instance graphs (the PRO/SOLID example, the presentation evidence graph, the exact-interval example, eight temporal-interface pairs) merged with SULO 0.2.14: OWL 2 DL profile and HermiT consistency, with 73 negative controls | CI, `.github/scripts/check_owl_consistency.py` |
| SULO itself, by sha256 | `ontology/sulo-pin.json`, at load time |
| Point-anchor graphs: SHACL plus procedural checks | When built: offline and in tests |
| Bounded-interval graphs: claim, role and bearer structure, no stray `hasValue`, Turtle round trip | Unit tests only |

Not verified: the bounded-interval graphs the journey and temporal pages build live are not SHACL-validated and are not in the CI OWL check; no step reasons over instance graphs during matching; full OWL reasoning (O1), production review of the SULO pin (O3) and `hasValue` datatype equality (O4) are open. "The graph wins" holds as the design rule — graphs are the authority the projections were built from — but no live result is computed from a graph validated during that request.

Proposed tightening, cheapest first: add the bounded example graph to the CI OWL check and SHACL-validate bounded graphs in their tests; check at startup that served projections equal a fresh re-projection of the committed graphs; derive the bounded profile's matching inputs from the validated graph instead of the source JSON (a profile change needing a decision record).

## 3. The trajectory mockup

`demos/mockups/trajectory/` calls no API. Recorded in headless Chromium while loading and exercising templates, search, filters and the detail drawer, the page makes five requests on load — the page, `shared/style.css`, `shared/core.js`, `trajectory/app.js`, and the dataset `data/synthetic-paediatric-rare.json` — and none during interaction. Everything is computed in the browser:

| Part | Code |
|---|---|
| Data loading, terminology index | `shared/core.js`: `loadData`, `searchIndex` |
| Records grouped into events (by the generator's ground truth) | `shared/core.js`: `evidenceFor`, `makeItem` |
| Index periods | `shared/core.js`: `windowFor`, `eventDay` |
| Measurement conditions | `shared/core.js`: `measureEvents` |
| Shape detection | `trajectory/app.js`: `analyse` |
| Candidates, temporal network, possible versus exact | `trajectory/app.js`: `candidates`, `compile`, `consistent`, `search` |
| Relaxation, not comparable | `trajectory/app.js`: `relaxOptions`, `matchPatient` |

None of the engine, workspace, oracle or SULO graphs is involved; the browser matcher re-implements the idea in `temporal_stn.py` and is covered by no engine test or provenance binding. Connecting it means a trajectory-graph profile in `patterns/` (§7.5), an endpoint in `app/` accepting the page's definition as JSON, and the page posting to it.

## 4. Server-side design for millions of patients

*Proposed.* At five million patients with about two thousand recorded events each, there are roughly ten billion events and tens of billions of triples. No triple store or OWL reasoner answers ad-hoc temporal queries over that interactively. The design keeps the project's split between the semantic record and an execution representation.

### 4.1 Architecture

```
 sources ─► ingest & validate ─► claim/graph store (authority, versioned)
 (EHR, OMOP,   (SHACL + profile        │
  registries)   checks per batch)      ▼ derive per snapshot
                              ┌───────────────────────────────────────────┐
                              │ execution store, partitioned by patient   │
                              │  events · series · coverage · postings ·  │
                              │  index anchors · terminology closure      │
                              └───────────────────────────────────────────┘
                                               ▲
 UI ─► API (stateless) ─► compiler ─► job queue ─► workers (one patient partition each)
             ▲                                        │ 1. candidate filter (indexes)
             └──── progress, counts, pages ◄──────────┤ 2. exact check per patient
                                                      │ 3. relax and classify
                   result store (per job, per patient)◄┘
```

Graphs are validated once, at ingestion and per batch, not per query. Every execution row carries an evidence identifier back into the graph, as `project()` does today.

### 4.2 Execution store

Partitioned by patient so that one patient's history sits on one worker; sorted by patient, concept and time. A columnar warehouse (ClickHouse, BigQuery, Parquet with DuckDB) or a purpose-built Rust service.

| Table | Content |
|---|---|
| `events` | Patient, event, concept, kind, start and end bounds, clock, status, `recorded_at`, `available_at`, version, supersedes, source references, quality flags |
| `series` | Measurements with value, unit, originals and time range |
| `coverage` | For each patient and source, the periods the records actually cover (diary months, follow-up, gaps): what separates "no match" from "not comparable" |
| `concept_postings` | For each concept, a compressed bitmap of patients and per-patient summaries (first, last, count) |
| `anchors` | Precomputed first, Nth and last times for common index events |
| Terminology | Concept closure (ancestors and descendants) and versioned code-to-concept mappings; raw codes retained |

Codes are mapped to concepts at ingestion, with the mapping version recorded; a query concept expands through the closure table, so search never re-reads source codes.

### 4.3 Postings and coverage

Two of these tables carry most of the design. **Postings** say where something was recorded, so the search knows which patients to look at. **Coverage** says where something *could have been* recorded, so that the absence of a record can be read correctly.

**Postings: who has what.** A posting list is an inverted index, like a search engine's index from a word to the documents containing it: for each concept, the patients with at least one admitted assertion of it (present, about the patient, performed, and available under the selection mode in use).

```
febrile seizure        → C001 C004 C015 C017 C021 C023 …
sodium-channel blocker → C001 C004 C015 C021 C023 C036 C044 …
Dravet syndrome        → C001 C004 C015 C017 C020 …
```

- **Bitmaps.** Patients are numbered 0 to N−1 and each concept stores one bit per patient, compressed (Roaring bitmaps, for example): about 125 KB per concept for a million patients before compression, far less after. Intersecting two lists takes milliseconds.
- **Ancestors.** Lists are kept for ancestor concepts too, or ancestors are unioned at query time, so a query for "seizure", or a relaxation from "febrile seizure" to "seizure", uses the broader list.
- **Per-patient summaries** beside each bit — first time, last time, count — allow coarse time pruning without reading events: a patient whose last blocker start precedes their first seizure cannot satisfy "blocker after seizure". Pruning uses the widest recorded ranges, so possible matches survive.

Postings drive **candidate filtering**: intersect the lists for the steps any match must contain, rarest first, and only the survivors get the per-patient check. A posting gives a necessary condition, never a sufficient one; appearing in every list means a patient might match, not that they do.

**Coverage: where absence means something.** Coverage records, for each patient and each source or kind of observation, the periods in which the source would have recorded something had it happened:

| Patient | Source | Covered periods |
|---|---|---|
| C015 | Parent seizure diary | 11 months to 7 years |
| C015 | Hospital follow-up | 6 months to last contact |
| C004 | Parent seizure diary | none |
| C047 | Hospital follow-up | birth to 3 years 10 months; a gap; 5 years 1 month to last contact |

A missing record is not evidence that nothing happened: no seizures recorded in a month may mean none occurred or that nobody was recording. Coverage separates the two:

| Situation | Without coverage | With coverage |
|---|---|---|
| The diary covers the months after the blocker and shows no rise | No match | **No recorded match**: searched where it would have shown, found nothing |
| No diary at all (C004), other steps match | No match — wrong | **Not comparable**: these records cannot answer the question |
| The diary starts after the blocker started (C015 in the mockup run) | No match, or possible | **Not comparable**: no baseline before the exposure |
| The relaxed question needs a wider window than the source covers | Silently incomplete | **Unresolved**: the search was not complete for this question |

The repository already applies this at small scale. Each oracle case carries a `source_search` declaration — basis `coverage`, the clock, the searched period and the concepts — and a question that needs a window or concept outside it, including the wider window a relaxation needs, is `UNRESOLVED` with the uncovered demands listed. The [source completeness decision](decisions/source-completeness.md) makes completeness derived from declared coverage, never assumed. The mockups report "not comparable" when the diary does not cover the months around an exposure.

**Together.** Postings narrow millions of patients to the few who might match. Coverage is consulted for every candidate, and for patients filtered out only because a required source is missing, so each patient receives the right kind of "no" — no match, not comparable or unresolved. The per-patient check then decides exact, possible or relaxed for the rest.

**Where coverage comes from** is the hard part. Some can be inferred: an ICU stay bounds charted vitals; a diary's uploaded months; first visit to last contact. Some must be declared by the source owner — which concepts a source records and whether completely — which is external dependency D-HISTORY. Results should label inferred coverage as inferred.

### 4.4 A query's life

Example: *first febrile seizure → any sodium-channel blocker within 12 months → seizures double within 60 days*, from the first seizure onward.

1. **Compile.** Validate the definition and canonicalize it with a stable hash. Derive a necessary condition from the most relaxed version the budget allows, so that filtering can never drop a relaxed match.
2. **Candidate filter.** Intersect concept postings (§4.3) (febrile seizure or, relaxed, any seizure; sodium-channel blocker; diary coverage), rarest first. Prune coarsely on per-patient summaries using the widest recorded ranges, so that possible matches survive. Patients who fail only on missing coverage (§4.3) are counted as not comparable, not discarded.
3. **Exact check per candidate,** in parallel by partition: load only the candidate's events for the query's concepts and run the trajectory-graph matcher (§7.5). Measurement conditions and event counts are computed here over that patient's series.
4. **Relax only where needed:** patients failing exactly but passing the relaxed filter are re-checked with declared relaxations, cheapest first, within the budget.
5. **Classify and aggregate:** exact, possible, relaxed with cost, no match, not comparable, unresolved, or no index period, with bindings and per-constraint verdicts. Results stream as partitions finish — counts by status, exclusions by reason, then pages — and full evidence is loaded only when one patient is opened.

*Planning estimate, not a measurement:* 100,000 candidates at about 2 ms each is about 200 CPU-seconds, a few seconds on 64 cores and less in Rust. Counts can be interactive; full results are an asynchronous job with progress and cancellation.

### 4.5 Correctness, time and reproducibility

- **Sound filtering:** differential tests on sampled partitions compare the indexed pipeline with an exhaustive reference over every patient, as the interval cohort profile already does.
- **Bitemporal records:** versions keep occurrence time and recording or availability time; source-as-known at the index filters on `available_at` and takes the version valid at that cutoff; retrospective selection takes the latest correction under its label. These are the evidence-selection modes applied to tables.
- **Reproducible results:** each result records the snapshot, engine code digests, query hash and mapping versions, which also makes result caching safe.

### 4.6 Operations and governance

A stateless API, a job queue and workers that scale to zero (FR-081, FR-082); results cached by query hash, snapshot and engine version; new records append to a partition and recompute only the affected patients' postings, anchors and cached results (FR-042); purpose-bound access, minimum cell sizes on counts, field-level restrictions and audit of every query and export (FR-060, FR-061) before any non-synthetic use.

### 4.7 What exists and what is missing

| Piece | Today |
|---|---|
| Graph to projection with evidence identifiers | Implemented (`pro_solid`, claims) |
| Temporal network with exact possible and certain | Implemented (`temporal_stn`, bounded cohort) |
| Indexed engine checked against an exhaustive reference | Implemented (interval cohort) |
| Source windows, partition plans, prepared sessions, result cache | Implemented at single-machine scale on the MIMIC-IV demo |
| Availability modes and revision chains | Implemented on small bundles (evidence selection) |
| Trajectory-graph matcher for sequences, trees and graphs | Browser mockup only |
| Partitioned event store, concept postings, coverage tables | Not implemented |
| Compiler with relaxation-sound filters | Not implemented |
| Distributed workers, job queue, streaming results | Not implemented |
| Similarity retrieval at scale, governance, audit | Specified only |

## 5. Similarity on coded terms

### 5.1 Today

The workspace scores clinical concepts as 1 − Jaccard similarity after ancestor expansion in a pinned toy taxonomy (`patterns/patient_similarity.py`); the mockups do the same. Two rules are worth keeping: a partially known concept set is treated as missing, and an empty set counts only with an explicit completeness declaration ([patient similarity](patient-similarity.md)).

Plain ancestor-Jaccard counts every shared ancestor equally: sharing "Seizure" counts as much as sharing "Dravet syndrome", and deep or crowded branches inflate scores.

### 5.2 Proposed method

1. **Normalize codes to concepts.** Map each coding once, at ingestion, to one concept per axis — HPO for phenotypes, MONDO or ORPHA for diseases, ATC or RxNorm ingredients and classes for medicines, LOINC with value for laboratory results — recording the mapping version. Broad codes stay broad: ICD-10 G40.4 maps to "generalised epilepsy syndrome", not to Dravet syndrome, and receives partial credit.
2. **Filter profiles as matching does.** Present assertions about the patient only; hedged ones only if the free-text setting allows; negated and family-history mentions never count as presence; only what was available by the index. An axis without declared complete coverage is unobserved and appears as coverage, never as a zero match.
3. **Weight by information content.** IC(c) = −log p(c), where p(c) is the fraction of patients annotated with c or a descendant.

| Measure | Definition | Role |
|---|---|---|
| simGIC | Σ IC over shared ancestors ÷ Σ IC over all ancestors | Drop-in upgrade of ancestor-Jaccard: same sets, weighted |
| Resnik | IC of the most informative common ancestor of two terms | How specific is what two terms share |
| Best-match average, reference to candidate | Mean over the reference's terms of the best Resnik match among the candidate's terms | "How much of this patient's picture does the candidate explain": asymmetric, tolerant of candidates with more recorded |

4. **Choose the IC population deliberately.** IC depends on the population it is computed from. Compute it from the searched database (postings counts propagated up the ontology), smooth very rare terms, and record the population and snapshot with each result.
5. **Combine axes with declared weights** (findings, diagnoses, genetics, medicines, onset age), preserving Must match, Prefer similar, Ignore and Hide per axis, and explain every score term by term: "febrile seizure ↔ febrile seizure (IC 0.69)", "myoclonic seizure ↔ seizure, shared ancestor *seizure* (IC 0.13)". This is the mockups' pairwise view with weights.

### 5.3 Measured comparison

On the synthetic paediatric cohort (48 patients), with IC computed from that cohort and each patient's recorded coded findings and diagnoses, reference patient C001 (ataxia, Dravet syndrome, febrile seizure, generalised epilepsy syndrome, myoclonic seizure, seizure, status epilepticus):

| Concept | IC in this cohort |
|---|---:|
| Seizure | 0.13 |
| Epilepsy | 0.61 |
| Febrile seizure | 0.69 |
| Generalised epilepsy syndrome | 1.47 |
| Dravet syndrome | 1.47 |
| Status epilepticus | 1.79 |

| Measure | Top three (score) | Dravet in top eight |
|---|---|---:|
| Ancestor Jaccard (today) | C027 0.90, C047 0.90, C036 0.80 | 8 |
| simGIC | C047 0.92, C027 0.78, C036 0.73 | 8 |
| Best-match Resnik, reference to candidate | C027 1.39, C047 1.39, C036 1.07 | 8 |

All three rank the same patients first because this cohort is enriched for Dravet syndrome and cleanly separated. The difference is in what drives the scores: under IC weighting C027 drops from 0.90 to 0.78, because much of what it shares with C001 is generic. Dravet syndrome's IC of 1.47 reflects a quarter of this cohort having it; in a general population of a million patients it would be about 10.

### 5.4 At scale

Retrieve candidates sparsely (an inverted index on informative concepts and their ancestors, skipping near-root terms) or densely (IC-weighted ancestor vectors in an approximate-nearest-neighbour index, filtered by Must-match predicates), then re-rank exactly with simGIC or best-match Resnik and report recall against exhaustive search (FR-043). Validate by retrieval with a hidden diagnosis on the synthetic cohort's truth labels and then on MIMIC-IV, using `tools/evaluate_retrieval.py`.

## 6. Embeddings: OWL2Vec* or a relational GNN

Learned embeddings should retrieve candidates only; the reported score stays explainable (§5).

| | OWL2Vec* | Relational GNN (R-GCN and similar) |
|---|---|---|
| Embeds | Ontology concepts, from walks over the ontology plus labels and synonyms | Nodes of an instance graph: patients, events, concepts |
| Trained on | The ontology: public, no patient data | Patient records and an objective (link prediction or outcomes) |
| Rare diseases | Works for concepts with few or no patients | Weak where few patients per disease |
| New patients | Nothing to retrain | Message passing re-run, or an inductive variant |
| Time and availability | Patient vectors built after filtering to admissible pre-index assertions | Time must be encoded, or post-index facts leak in |
| Scale | Ontology size (HPO ≈ 18,000 classes, SNOMED CT ≈ 350,000): one machine | Millions of patients and billions of edges: sampling, GPUs |
| Privacy | Concept vectors hold no patient data | Patient embeddings are derived patient data, exposed to membership inference |

Both fare poorly on the raw SULO encoding: a clinical fact is reified as process, role, bearer and information object, so "patient has febrile seizure" sits four or five hops deep and walks or message passing mostly learn the encoding pattern. Embed the concept ontologies and the flattened per-patient profile, not the instance graph.

**Recommendation.**

1. Baseline without learning: sparse IC-weighted ancestor vectors, approximate nearest neighbours, exact re-ranking, recall against exhaustive search. It may suffice, and it is explainable and deterministic.
2. Add OWL2Vec* only if the baseline misses relatedness the hierarchy does not capture: concept embeddings on the reference ontologies, patient vector as the IC-weighted mean of admitted concepts, retrieval only, with ontology versions, seeds and model digest pinned and recorded, evaluated against the baseline.
3. Treat an R-GNN as a later research study on credentialed data — for suggesting likely missing or miscoded terms, or prognostic similarity from outcomes — under the study design and outcome separation the specification requires (D-STUDY, CLIN-002) and a privacy review.

Hyperbolic or box embeddings represent hierarchies more faithfully than word2vec-style vectors and are worth trying only if evaluation shows OWL2Vec* blurring the hierarchy.

## 7. Complexity of trajectory queries

Per patient: *k* steps, *m* constraints, *n* candidate events per step, *V* = 2*k* + 2 network variables, *r* relaxation options; *P* patients searched.

### 7.1 As prototyped

The mockup binds steps in topological order and, after each choice, rebuilds the network and checks consistency with shortest paths (O(*V*³)). Worst case per patient O(*n*ᵏ · *V*³); relaxation multiplies by the number of relaxation sets tried (pairs, O(*r*²)); in total O(*P* · *r*² · *n*ᵏ · *k*³). The engine's bounded cohort profile likewise takes the product of candidate sets. A patient with 40 seizures and a four-step query is 40⁴ ≈ 2.6 million checks; the mockup caps the search at 40,000.

**Defect:** when the cap is reached the mockup reports *no recorded match* with a "possibly incomplete" note. An exhausted search is unresolved, not a non-match; any engine version must report `UNRESOLVED`.

### 7.2 Inherent complexity

For *possible* matches the core query is a constraint problem in which each step's time lies in one of its candidate events' ranges (a finite set of grid points) and each constraint has the form lo ≤ *t*ⱼ − *t*ᵢ ≤ hi.

Such constraints are **max-closed**: if two assignments satisfy one, so does their component-wise maximum. For the upper bound, with *y*₁ − *x*₁ ≤ *b* and max *y* = *y*₁, we have *y*₁ − max *x* ≤ *y*₁ − *x*₁ ≤ *b*; the lower bound is symmetric. Unary domain restrictions are trivially max-closed. A constraint problem made only of max-closed constraints is decided by arc consistency, and if no domain empties, every variable's maximum remaining value is a solution (Jeavons and Cooper, 1995).

| Query features | Per-patient complexity |
|---|---|
| Sequence, tree or general graph; "after" and gap constraints; uncertain event times; index-period scope | Polynomial: arc consistency on sorted domains, O(*m* · *n*²) naively, near O(*m* · *n* log *n*) with two-pointer revisions |
| Event-count steps, measurement conditions | Derived events first, O(*n* log *n*) per series; then as above |
| *Exact* (holds for every recorded time) | A constraint between event choices — min(*Y*) − max(*X*) ≥ lo and max(*Y*) − min(*X*) ≤ hi — polynomial in the same way when events' ranges are not nested; trees are polynomial regardless, by dynamic programming |

### 7.3 Features that make it NP-hard

| Feature | Why |
|---|---|
| Distinct events across steps with overlapping candidates | An all-different constraint; the interval cohort profile's distinct slots are in this class |
| Disjunctive constraints between steps ("B before or after A") | The general temporal constraint satisfaction problem, NP-complete (Dechter, Meiri and Pearl, 1991). Alternatives within one step only enlarge its candidate set and stay polynomial |
| "While taking" with nested medicine episodes | An episode's start and end are chosen together, and nesting breaks max-closure |
| Absence over the binding search | "No X between A and B" quantified over bindings |
| Minimum-cost relaxation with many options | An optimization over subsets, 2ʳ in general; with today's budget (total ≤ 1, each option ≥ 0.25) at most four combine, O(*r*⁴) |

Of these, only nested medicine episodes can arise in the mockup today.

### 7.4 Across the database

The candidate filter costs roughly the size of the posting lists intersected, sub-linear in *P* for selective concepts. Per-patient checks are independent and parallel: O(*P*_candidates · *m* · *n*²) for the polynomial class. Not-comparable and unresolved patients are counted, not dropped.

### 7.5 A trajectory-graph profile for the engine

1. Decide *possible* by arc consistency over sorted candidate domains; take the maxima as the binding. Polynomial for sequences, trees and graphs, justified by max-closure.
2. Decide *exact* the same way with the range relations; certify a chosen binding, and produce the failing counterexample, with the shortest-path machinery of `temporal_stn.py`.
3. Use bounded backtracking only for the hard features (distinctness, disjunction, nested "while taking", absence), with an explicit step budget, reporting `UNRESOLVED` when it is exhausted.
4. Make relaxation a cost-ordered search over the bounded combinations, stopping at the first success.
5. Check the decision procedure against an exhaustive reference implementation, as the interval cohort profile does.

## 8. Decisions and next steps

| Decision | Options |
|---|---|
| Storage for the execution store | Managed columnar warehouse (fastest to stand up) or a purpose-built Rust engine (fastest queries, more work) |
| Source model | OMOP CDM lifted into SULO claims, or each source directly |
| First scale target | Full MIMIC-IV (about 300,000 patients, credentialed) before millions |
| Live graph validation | Which of the three tightenings in §2 to adopt |
| Similarity | simGIC as default and best-match Resnik as the reference-to-candidate option; IC population per snapshot |
| Embeddings | Baseline first; OWL2Vec* only if the baseline's recall falls short |

Proposed first increment, within the repository's discipline and on one machine: the trajectory-graph profile with its reference implementation (§7.5); an execution store built from the MIMIC-IV demo with concept postings and coverage tables; a relaxation-sound compiler tested against the exhaustive reference; IC-weighted similarity with a recall benchmark; and the mockup's exhausted-search status corrected to unresolved.

## References

- P. Jeavons and M. Cooper. Tractable constraints on ordered domains. *Artificial Intelligence* 79(2), 1995.
- R. Dechter, I. Meiri and J. Pearl. Temporal constraint networks. *Artificial Intelligence* 49, 1991.
- P. Resnik. Using information content to evaluate semantic similarity in a taxonomy. *IJCAI*, 1995.
- C. Pesquita et al. Semantic similarity in biomedical ontologies. *PLoS Computational Biology* 5(7), 2009 (simGIC and best-match average).
- J. Chen et al. OWL2Vec*: embedding of OWL ontologies. *Machine Learning* 110, 2021.
- M. Schlichtkrull et al. Modeling relational data with graph convolutional networks. *ESWC*, 2018.
