# Engine increment: decision log

From 2026-11-06 the project lead delegated three workstreams and every decision in them:

1. package the engine in place;
2. build an execution store (a columnar store, Postgres with embeddings, and/or a Rust engine were allowed);
3. build a SULO adapter for the trajectory-graph engine.

This log records each decision, the alternatives and the reasoning, in the order they were taken. An independent design review by a separate model ("the advisor") preceded the decisions, and its points are credited where adopted or declined. Each entry names the pull request that implements it.

| # | Decision | Status |
|---|---|---|
| D1 | Package with hatchling, in place | Decided |
| D2 | One locator, behind the existing `ROOT` | Decided |
| D3 | Regenerate what the locator change invalidates; cut a v26 manifest | Decided |
| D4 | Execution store on embedded DuckDB with immutable Parquet snapshots | Decided |
| D5 | Store schema, time origin and snapshot identity | Decided |
| D6 | First store increment indexes source item classes, not clinical concepts | Decided |
| D7 | No embeddings, Postgres or Rust in the first store increment | Decided |
| D8 | The adapter reads `bounded_intervals.Snapshot`; the graph route is canonical | Decided |
| D9 | Adapter time, clocks and refusals | Decided |
| D10 | Sequencing, ownership and merge order | Decided |
| D11 | MIMIC-IV demo files are fetched locally and verified, never committed | Decided |
| D12 | Merges wait for a settled, complete, green check run | Decided |
| D13 | One event has one time: engine variables are keyed by source variable | Decided |
| D14 | Demo item classes take their dictionary category as a labelled ancestor | Decided |
| D15 | Store stage 2 sends every demo row through the canonical route, 32 claims per store | Decided |
| D16 | Chartevents and later bulk sources take route (b), with a sampled graph check | Decided, not yet built |
| D17 | Committed reports on credentialed data carry aggregate counts only | Decided |
| D18 | A step that admission may have hidden is not comparable, judged by the query's scope | Decided |
| D19 | Scaling: fix the Python-side storage memory first; defer the Rust matcher until it is measured | Decided |

## D1 Package with hatchling, in place

A `pyproject.toml` builds a wheel containing `patterns/` (without `test_*`, `verify_*` and caches), `reference_oracle.py` as a top-level module, the lock files, and the contracts the engine reads at run time (`schemas/*.json`, `ontology/*.ttl`, `ontology/vendor/`, `ontology/sulo-pin.json`, `ontology/toy-taxonomy.json`) as package data under `patterns/_contracts/`. Optional dependencies are extras: `semantic` (rustdl) and `store` (DuckDB). Nothing is published, and there are no console scripts. The import name `patterns` is generic and could collide in a shared environment, so renaming waits for stage 3 (design §10.4).

*Alternatives:* setuptools, which offers nothing extra here; moving the code under `src/`, which would break every report binding by path; publishing, which is premature.

## D2 One locator, behind the existing `ROOT`

Nearly every engine module builds paths from `pro_solid.ROOT`, re-exported as `exact_intervals.ROOT`. A new `patterns/_resources.py` supplies it:
- **In a checkout** it is the same `Path` as today.
- **Installed**, it is a small path-like object. It serves `patterns/…` from the package, `schemas/…` and `ontology/…` from `_contracts`, and raises `REPOSITORY_RESOURCE_UNAVAILABLE:<path>` for anything else. Examples, data pins and reports are repository resources, so nothing is ever written into site-packages.

The only existing line changed is the `ROOT` definition in `pro_solid.py`.

*Alternative declined:* design §10.4 proposed replacing the direct reads (`ROOT / 'schemas/…'`) module by module. That is more than 80 reads in about 60 modules, and would invalidate about 30 reports, three of them reproducible only on Linux. The advisor argued against it, and I agree: one definition, one blast radius.

## D3 Regenerate what the locator change invalidates; cut a v26 manifest

Changing `pro_solid.py` invalidates the reports that bind it. They are regenerated in the same pull request:
- the completion register;
- the MIMIC record-query demo report, from the verified demo files (D11);
- the research-prototype journey and its run copy;
- the test-count report.

The v25 release manifest describes a past release, so it is not rewritten. A v26 manifest is cut, and v25 joins the historical exemptions in `tools/check_report_provenance.py` and `.github/scripts/check_manifest.py`, as v24 already is.

**Amended after review (#120).**
- **v26 scope.** The v26 manifest binds the same nine files as v25, with current digests, not the packaging files. Binding packaging files would break every later branch's manifest check.
- **A digest the provenance gate can't see.** The guided cohort records the `pro_solid.py` digest in `projection_sha256`, which a demo test checks but the gate does not. So the guided cohort and the patient-similarity fixture that pins it were rebuilt too.
- **The installed-package check proves equality:**
  - it derives the expected module list from the checkout, so new modules don't break it;
  - it requires the two probes to import from site-packages and from the checkout respectively;
  - it compares every wheel file byte for byte with its source.
- **Checkout detection** requires named contract files.
- **A dedicated error.** Refusals raise `RepositoryResourceUnavailable`, a `LookupError`, so command-line handlers that catch `ValueError` do not relabel them as bad input.
- **`temporal_interface`** now imports when installed.
- **Python version.** Python 3.12 is required, since that is what CI tests.
- **The `store` extra** is left to the store pull request.

## D4 Execution store on embedded DuckDB with immutable Parquet snapshots

The store is DuckDB, embedded and pinned in its own lock behind the `store` extra and imported lazily, writing immutable Parquet snapshots partitioned by patient bucket.
- There is no service to run, so CI needs only pip.
- It is columnar and handles full MIMIC-IV (about 330 million chart rows) on one machine.
- Parquet moves to BigQuery, ClickHouse or a Rust/DataFusion worker without a format migration.

The repository already pairs an indexed store with an independent reference (`indexed_source_windows` over SQLite), and the store follows that pattern: a pure-Python reference builds the same tables, and tests compare them row for row.

*Alternatives:*
- SQLite: standard library, but it would need replacing before full MIMIC.
- Postgres with pgvector: a server with no benefit in this increment, and design §6 defers embeddings.
- A Rust engine first: the matcher's semantics are still moving, and a port needs its own differential harness.

## D5 Store schema, time origin and snapshot identity

**Tables:**
- `patients`: dense index, identifier, bucket.
- `events`: patient, event, kind, clock, start and end bounds, ongoing, status, selection state, recorded and available times, claim identifier and digest, source table, record and file digest.
- `event_concepts`, with a basis for each concept.
- `concept_closure`, with basis and version.
- `concept_postings`: concept, patient, count, earliest and latest start.
- `coverage`: patient, source, clock, bounds, basis.

**Time origin.** Times are integer microseconds on the patient-local calendar from the fixed origin 0001-01-01, as `indexed_source_windows` uses, so a time never depends on the request that produced it.

**Snapshot identity.** The snapshot identifier is the digest of the canonical inputs plus each table's row count and canonical-content digest. It is computed over sorted canonical rows, never Parquet bytes, which vary with the writer.

## D6 First store increment indexes source item classes, not clinical concepts

The MIMIC-IV demo's claims are imported pending, and its items map only to their source item code classes; clinical mappings wait on a qualified reviewer (D-CLINICAL). So the first store indexes recorded source records under source item classes, and says so in its labels and reports. The concept hierarchy, information content and relaxation logic are tested on the synthetic paediatric cohort, which has a real hierarchy.

**Data.** `inputevents`, `icustays` and `d_items` (100 patients, 140 stays, 20,404 rows). `chartevents` (668,862 rows) waits for a later increment.

**Candidate filter.** For each step, the union of postings for its concept and for every concept widening within the relaxation budget, intersected across steps with the rarest first. This is sound under relaxation. Time-based pruning on the posting summaries waits until differential tests cover it.

**Comparability.** A patient whose window a source does not cover is reported as not comparable, never as no match.

**Amended after review.**
- **Ingest selection.** The converter ingests performances only. Orders and superseded records are dropped, and the selection mode (retrospective, latest version, availability not applied) is recorded in the ingest provenance and the manifest.
- **Verification on open.** Opening a store verifies every table against the snapshot identity by default, and so does a rebuild into an existing snapshot. Skipping verification is explicit, and recorded in results as `verified: false`.
- **Recording sources.** Which sources record a concept is a `source_concepts` table built into the snapshot, so it never depends on who else is in the cohort. A step whose concept no source records is reported as not comparable (`NO_SOURCE_RECORDS_CONCEPT`).
- **Match results** carry the coverage rule (`window_overlap_source_concepts_v1`) and a context id covering the snapshot, query, windows and code.
- **Box representability.** Patients the SULO adapter marks not box-representable are refused at ingest. The store holds boxes only.

## D7 No embeddings, Postgres or Rust in the first store increment

Information content per snapshot comes from posting counts, the baseline of design §5 and §6. An approximate-nearest-neighbour index is added only if recall at full scale needs it. Postgres is kept for jobs, results and audit (FR-060, FR-061, FR-081). A Rust engine would come later, as a second implementation over the same Parquet files, with the Python path as its reference.

## D8 The adapter reads `bounded_intervals.Snapshot`; the graph route is canonical

Bounded JSON, bounded RDF, patient-local graphs and claim selections all reach `bounded_intervals.Snapshot`, so `patterns/trajectory_graph_sulo.py` reads that, and the graph route is the canonical one ("the graph wins"). The point-anchor oracle is a fixed three-step pattern, and exact-interval graphs are the bounded case with equal bounds, so neither needs its own route.

Engine event identifiers are snapshot event identifiers, and each bound step's evidence (evidence identifier, claim, role, person, source record and hash, context) is carried into the result. Concepts are IRIs. Ancestors come from the loaded ontology, computed once per run, and each event–concept pair records its basis. An unknown query or relaxation concept is refused rather than matched as nothing. Modules bound by Linux-only reports (`claim_projection`, `temporal_stn`, `mimic_*`) are not edited.

## D9 Adapter time, clocks and refusals

Each patient's variables are tightened to their minimal bounds from the snapshot's network.
- **Constraints a box can't represent.** If a patient's source constraints go beyond bounds and proper intervals, the adapter refuses (`SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE`). Exactness over the box would be sound, but possibility would not.
- **Witness checks.** Every possible witness is checked against the source network plus the query's edges.
- **Clocks.** There is one clock per patient. Clocks with offset-datetime origins are rebased by their exact integer difference; otherwise the events are left out and the patient is reported incomparable.
- **Units.** Declared units convert to microseconds exactly, and calendar months are refused (T3, T4).
- **Window.** The index window is explicit in the request.

**Amended after review.**
- **Shared source variables.** A variable referenced by more than one event is an equality no box can represent. Until the engine shares variables (D13), such patients are refused (`SHARED_SOURCE_VARIABLE`).
- **Window clocks.** A window must name its clock whenever the patients it covers use more than one clock (`SHARED_WINDOW_CLOCK_REQUIRED`, `WINDOW_CLOCK_REQUIRED`). Otherwise the same window would mean different absolute periods.
- **The store's input is `ingest_rows(view)`.** It is query-independent: each event's own clock, declared and tightened bounds, variable ids, a per-patient `box_representable` verdict, and asserted concepts with bases. Entailment is marked `not_materialized`. `match_snapshot` no longer emits rows a store could mistake for snapshot facts.
- **Bounded network check.** The source-network check is restricted to the bound events; the whole-network replay stays as its test reference.
- **Context.** The route's modules and the semantic run's digests are in the result context.
- **Disjointness.** A vocabulary that declares disjointness requires a semantic run.
- **Points.** Without `during`, events go to the engine as points, so trees of steps use the polynomial method.
- **Accepted departures:**
  - a possible binding refuted by the source network becomes unresolved, as a safety net;
  - example parity compares decisions, not witnesses;
  - `ENTAILMENT_REQUIRED` and `PATIENT_NOT_IN_SNAPSHOT` are added.

## D10 Sequencing, ownership and merge order

- Packaging and the adapter run in parallel worktrees; the adapter only adds files.
- The store starts on the synthetic fixture, and MIMIC ingest waits for the adapter's row.
- Each workstream owns its own workflow file.
- Shared generated reports (completion register, test counts) are regenerated after each rebase.
- Packaging merges first, because it cuts the v26 manifest.
- Straightforward implementation goes to a smaller model, and design-heavy work to a larger one. A separate reviewer reads each non-trivial pull request before it merges.

## D11 MIMIC-IV demo files are fetched locally and verified, never committed

The open-access MIMIC-IV demo 2.2 files are downloaded from PhysioNet into the git-ignored `data/local-mimic/`, and each is verified against the published `SHA256SUMS.txt`: inputevents, icustays, d_items, chartevents, patients, admissions. Only aggregate reports are committed, as before. CI does not download patient data.

## D12 Merges wait for a settled, complete, green check run

On 2026-11-06 a watcher merged #116 while one check was still pending, because GitHub had not yet registered it after a push. The check later passed. Since then a pull request merges only when at least the expected number of checks are registered, none are pending, every one passed, and that holds on two polls a minute apart.

## D13 One event has one time: engine variables are keyed by source variable

The SULO adapter found that the trajectory-graph engine gave each step its own time variables. So one event bound to two steps could take two different times, and the engine could report a false "possible". Its exhaustive reference and the trajectory page shared the same modelling, so their comparisons never caught it. The adapter's source-network check kept wrong answers out of its own results.

**Fix.** Engine events carry optional `start_var` and `end_var` (defaulting to the event's own), and events naming the same variable share it.
- **One network per patient.** Each patient's record is one simple temporal network over its variables: every range that names a variable, and every event's end at or after its start. Tightened ranges and distances within linked groups are computed once.
- **Certainty** uses those distances, so it remains a binary relation over event choices, and arc consistency plus forest assignment stays complete.
- **Possibility.** The joint check is the patient network restricted to the binding's variables, which is an exact projection.
- **Point-forest method.** It verifies any binding whose steps are linked, and falls back to bounded search when the check fails.
- **The reference** was rewritten independently, and **the trajectory page** keys its variables by event identity.

**Consequence: exactness changed.** An exposure's own end at or after its start, and the zero difference between two terms on one variable, now count toward certainty. On the page, one heavy trajectory moves four patients from possible to relaxed exact, because they were possible only through one event taking two times.

**Found along the way, and fixed:**
- Two contradictory relations on one pair of steps crashed the point-forest method, a bug already on `main`. It now returns no match.
- Linked groups beyond a combined size are refused (`SHARED_VARIABLE_COMPONENT_LIMIT`), so a request cannot occupy the server.

The adapter keeps its refusal of shared source variables for now, and its source-network check permanently. Passing source variable ids through is a follow-up.

## D14 Demo item classes take their dictionary category as a labelled ancestor

Under D6 the MIMIC-IV demo's concepts are source item classes, `mimic-record-item/<itemid>`. In stage 2 of the store each item class also has one ancestor: its `d_items` category (for example `Fluids/Intake` or `Medications`), under its own namespace `mimic-record-item-category/`, percent-encoded. The basis is `source_dictionary_grouping`.
- **Why.** A flat set of 76 item classes would leave the closure, the postings union over ancestors and `widen_concept` relaxation untested on real data. The category is the source dictionary's own grouping of its items. It asserts nothing clinical, so it does not need a reviewer (D-CLINICAL).
- **Labelling.** These are never called clinical concepts. The store ingest's `provenance.labels`, which the manifest now copies as `labels`, say `clinical_mapping_verified: false`, `concept_basis: source_item_class` and `ancestor_basis: source_dictionary_grouping`, and the demo report repeats them. The closure version digests every ancestor and its basis.
- **No invention.** An item with an empty category gets no ancestor.

*Alternatives:* no ancestors, which tests nothing hierarchical on the demo; ancestors from a clinical terminology, which needs reviewed mappings that do not exist.

## D15 Store stage 2 sends every demo row through the canonical route, 32 claims per store

The canonical path is MIMIC rows, then pending claims (`mimic_claim_import.describe`), then a claim selection under a caller-declared accept-all analysis policy, then the snapshot by the patient-local graph route, then `ingest_rows`, then the store. The route has resource limits:
- a patient-local record claim store holds at most 32 claims (schema `maxItems`, and the importer's `MAX_CLAIMS` per ICU stay);
- at most 512 bundle rows and 256 entries in any list per store, a 256 KiB document, and a claim graph of at most 32,768 triples and 8,192 nodes;
- a semantic policy declares at most 32 classes;
- an import request names at most 16 items and 256 stays.

The demo has 76 admitted item classes, and 60 of its 136 stays with admitted rows exceed 32 claims; they hold 10,143 of the 10,980 admitted rows. So `mimic_claim_import.run` alone would block most of the demo.

**Decision: option (a), every row through the canonical route.**
- Each stay's claims are split, in source-record order, into stores of at most 32.
- Each store is built exactly as the importer builds one: the same `describe()` claims, one patient clock whose origin is the patient's earliest admitted start, and the importer's isolation certificate.
- Each store declares only its own items in its semantic policy.
- Each store is accepted, selected by `trajectory_graph_sulo.from_claims` and projected by `ingest_rows`.

Splitting changes nothing the store reads. The claims carry no constraints between them, and every event has its own variables, so each event's box is the same whichever store holds it. A variable named by two rows across a patient's stores would still be refused as shared. On the fixture, the stores for a stay under the limit equal the importer's own, claims, clock and claim provenance alike. Route identity (snapshot context and evidence identifiers) is per store, and deterministic.

The demo's 424 stores take 62 seconds on 8 worker processes (about 480 CPU-seconds), so option (b), which would build bulk rows from `describe()` and check a sample through the graph, was not needed.

*Consequences:* the importer's per-stay limit becomes a per-store limit, and it is the store, not the adapter, that joins a patient's stores. That join is what the store is for.

**Amended after review.**
- **Row identity across stores.** After joining a patient's stores, the converter requires every record id, source key and event id to be unique for that patient (`DUPLICATE_SOURCE_ROW_ACROSS_STORES`). The demo's rows are one release with no revisions, so splitting in record order cannot separate two versions of one record. A future source with revisions or repeated row ids must split by source-record chain, so that a record and its revisions share a store, and must rerun the claim-conflict check per patient after joining.
- **The admitted subset.** Admission keeps 10,980 of 20,404 rows: continuous IV and medication segments with four statuses. So the store names its source `inputevents:continuous_segments` and lists the admitted statuses and categories in its labels. Every row admission leaves out is kept as an excluded record (item class and start) in a new snapshot table, `excluded_records`. A `none` that such a record could have changed becomes `not_comparable` with `ADMISSION_EXCLUDED_RECORDS`, following engine design §4.3. Item classes seen only in excluded rows join the closure (83 in the demo), so a query naming one is answered, never refused as unknown.
- **Coverage beyond the stay.** 53 admitted records start before their stay's intime or end after its outtime, by up to 503 minutes (median 4). Each stay's period is widened to hold its own admitted records, with basis `inferred:icustay+records`; this applies to 16 of the 140 periods.

## D16 Chartevents and later bulk sources take route (b), with a sampled graph check

Route (a) costs about 0.2 CPU-seconds per claim, mostly per-store graph building and isolation certificates. For the demo's 668,862 chartevents rows that is about 37 CPU-hours; for full MIMIC-IV it would take years. So bulk sources take route (b):
- **Bulk rows** are built directly with the importer's pure claim identity (`describe()` or its measurement equivalent).
- **A deterministic sample of stores** per batch goes through the full graph route, with a row-equality differential on that sample. Reports must say rows are tied to the graph by claim identity and checked on a sample, not each validated through the graph.

Chartevents also needs point-measurement support in the store mapping and the series table for measurement conditions. Its ingest must be sharded per patient, with the input digest computed as a Merkle tree over patients. The differential must build engine inputs once per patient. None of this is built yet.

## D17 Committed reports on credentialed data carry aggregate counts only

The open MIMIC-IV demo's reports commit `input_digest` and `snapshot_id`, which are digests of row-level data. That is acceptable for open-access data. For credentialed full MIMIC-IV, committed reports carry aggregate counts only. Digests of row-level data stay out of the repository unless the data owner confirms the data use agreement allows them (D-DATA).

## D18 A step that admission may have hidden is not comparable, judged by the query's scope

Admission keeps only continuous IV segments: 9,424 of the demo's 20,404 inputevents rows are dropped, mostly drug pushes and boluses. A patient whose source recorded an item only in dropped rows would otherwise get `none`, as if the source had been searched.
- **What the store keeps.** It names its source `inputevents:continuous_segments`, labels the admitted statuses and categories, and keeps an `excluded_records` table inside the snapshot identity.
- **Reach.** A step reaches an excluded record when the record's concept is one of the step's concepts or widenings, or a descendant of one, and the query's scope admits its start: anywhere for `any`, inside the window for `within`, and so on (`excluded_records_scope_v1`).
- **The rule.** Excluded records can falsify more than a `none`, so the rule applies to every engine result, in this order:
  - A `first` or `last` step whose bound event an excluded record of its concepts could displace (`start_lo` at or before its `start_hi` for first, `start_hi` at or after its `start_lo` for last) makes the result `not_comparable`, whatever its status. This test takes every such record, admitted by the scope or not, because the engine chooses the first or last event among all events carrying the concept and only then applies the scope. A record outside the window can therefore still become the target and move the answer out of the window. The binding found may not be a binding of the query against the whole source, so it is not offered as a match beside an `unresolved` status. Bound events are looked up per patient, since event ids are unique only within a patient.
  - `none` becomes `not_comparable`.
  - `relaxed` becomes `unresolved`, with the relaxed match beside it. `relaxed` asserts that no unrelaxed binding exists, and an excluded record can supply one; the engine already reports an undecided unrelaxed search this way.
  - `possible` stays possible but becomes incomplete, with note `EXCLUDED_RECORDS_MAY_MAKE_EXACT`: it asserts that no binding is exact.
  - `exact` and `possible` bindings whose steps all have occurrence `any` stand, because adding records only adds bindings.
- **Structural concepts.** Excluded records carry an item class and its grouping, never the event kind's ontology classes. On a snapshot with excluded records, a query naming a structural class is refused (`STRUCTURAL_CONCEPT_EXCLUSION_UNKNOWN`) rather than answered blind.

*Alternative declined:* "overlapping the window" for every scope. It would turn fewer results not comparable, but under scope `any` the engine can bind events outside the window, so it would be unsound.

*Consequence:* on the demo's 50,000 differential results, 27,257 `none` results become `not_comparable`, 560 `first` or `last` matches (356 exact, 204 relaxed) become `not_comparable`, and 592 relaxed results become `unresolved` with their match beside them. No `possible` result arises, since every MIMIC time is exact. The store still agrees with the reference and with the admitted-only engine run under these rules, with no mismatch. That is honest about how little a continuous-segment subset can rule out. It is not a defect to tune away.

## D19 Scaling: fix the Python-side storage memory first; defer the Rust matcher until it is measured

The scaling benchmark (`tools/benchmark_execution_store.py`, `verification/execution-store-scaling-report.json`) grew the synthetic cohort on one machine (M4 Pro, 64 GB). It completed 1,000 and 10,000 patients. At 100,000 the store build exceeded 40 GB.

**Where memory goes, measured stage by stage:**
- `build()` roughly triples the in-memory document: a canonical copy, one JSON string for the input digest, DuckDB's derive alongside the Python document, then the read-back content digests.
- `verify()` on open reads every table back into Python: 2.9 GB at 10,000 patients, extrapolated to about 29 GB at 100,000.

So the first two limits are Python-side storage memory, not query time or Parquet size. At 10,000 patients the snapshot is 143 MB and the candidate filter takes milliseconds.

**Decision, in order:**
1. **Per-patient shard ingest.** The input digest becomes a Merkle tree over patient shards.
2. **Streamed digests.** The canonical digest is streamed, and content digests are computed in DuckDB or streamed, never by holding a table in Python. This changes the canonical form and every snapshot id, and the reference must follow.
3. **Then matching.** A wide match is about 81% engine and 16% event loading (one profile, so indicative); median 1.2 s and maximum 10.5 s at 10,000 patients. Partition-parallel Python and a Rust matcher are to be compared by measurement before choosing.

*Why not Rust now:* storage is not the bottleneck. The matcher question needs a measured comparison the benchmark did not make, and everything past 10,000 patients is an extrapolation from two points.

*Limitations:* one machine; synthetic data; the 100,000 cohort replicates a distinct pool of 10,000; and generator seeds that failed were excluded (a generator defect, since fixed).

