# Engine increment: decision log

A short reader's guide to this log is in the [owner briefing, November 2026](briefing-2026-11.md).

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
| D20 | Execution store 2.0: streaming per-patient ingest, streamed content digests, two verification tiers | Decided |
| D21 | Matcher scaling: cheap wins, then parallel Python, then Rust only if needed | Decided; arms B and C built, then the engine kernel; target met at 30 queries × 3 repeats (14 workers: p95 7.9 s, max 11.8 s, 0.82 GiB); stay in Python, no Rust prototype |
| D22 | The adapter passes shared source variables to the engine; the store still refuses them | Decided; its follow-ups (strict intervals in the engine, the store) done in D24 |
| D23 | Per-bucket derive: the store builds at 1M patients | Decided; 1,000,000 patients build and verify under a 4 GB limit; verification memory follow-up done in D24 |
| D24 | Shared variables end to end: strict intervals in the engine; the store holds shared variables | Decided |
| D25 | D24 review follow-ups; time pruning on the posting summaries, under a rule fixed before measuring | Decided; pruning kept: exact, arm B −18.4% per candidate at 300,000, 8-worker p95 8.41 s; ranged Parquet write not done |
| D26 | Execution store 3.0: typed event columns and an evidence table; end summaries for `during`; typed staging; D25 follow-ups, under rules fixed before measuring | Rules fixed; in progress |

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
- **Shared source variables.** A variable referenced by more than one event is an equality no box can represent. Until the engine shares variables (D13), such patients are refused (`SHARED_SOURCE_VARIABLE`). *Superseded by D22:* `match_snapshot` now passes source variable ids to the engine and no longer refuses them; the box verdict in `ingest_rows` still marks them, and the execution store still refuses them.
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

The adapter keeps its refusal of shared source variables for now, and its source-network check permanently. Passing source variable ids through is a follow-up, done in D22.

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

## D20 Execution store 2.0: streaming per-patient ingest, streamed content digests, two verification tiers

D19 found two memory walls, both in the Python side of the storage design: `build()` held, copied and serialised the whole ingest and read every table back into Python, and `verify()` read every table back on every open. Profile `execution-store-2.0` (ingest `execution-store-ingest-2.0`) removes both. The canonical forms change, so every snapshot identifier changes.

**Decisions:**
- **Streaming builder.** `StoreBuilder(root, header, threads, memory_limit)` takes the header (profile, dataset, closure) up front, then `add(patient)` repeatedly, then `finish(provenance)`. `add()` validates one patient against the closure, canonicalises it, appends its rows to staging files and appends `(patient_id, sha256 of its canonical JSON)` to a staged patient list; Python holds one patient. Duplicate patient identifiers are found in DuckDB (`GROUP BY … HAVING count(*) > 1`) and refused by name; on any refusal nothing is written under the root. `build(ingest)` stays, as a wrapper over `ingest['patients']`. The synthetic converter gains `iter_patients`; the MIMIC converter still hands over a document.
- **Input digest.** The sha256 of the canonical JSON of {profile, dataset_id, closure, provenance}, a newline, and `<patient_id>\t<patient digest>\n` per patient in identifier order, sorted by DuckDB and streamed. Ingest order does not matter. This replaces the planned Merkle tree over shards (D19 step 1): it is a flat list of per-patient digests, which is what streaming needs; shard or bucket roots wait for incremental appends.
- **Content digests**, per table and per bucket (an unpartitioned table is one bucket). Each bucket is one `ORDER BY` query on a total key with every column as a tie-break, fetched about 10,000 rows at a time; each row is hashed as a compact JSON array and a newline; the table digest hashes `<bucket>\t<digest>\n` over the buckets. Columns are only BIGINT, INTEGER, BOOLEAN, VARCHAR and null (floats and decimals refused by the schema and by a check on the derived tables); evidence stays a sorted-key JSON string; string order is binary, and a connection with `default_collation` set is refused.
  - *Declined:* `string_agg` (it materialises the table); DuckDB's `hash()` (not stable across versions); XOR or sums of row hashes (weak against repeated or cancelling rows); lines built in SQL with `to_json` (DuckDB's text form, not the specification's).
- **Two verification tiers.** The manifest records each Parquet file's sha256, hashed as a stream: integrity, not identity. `open_store(verify='files')` checks those and is the default on open; `verify='content'` re-derives the streamed content digests; `verify=False` stays explicit. `finish()` always verifies the written files by content before publishing, against digests taken from the derived tables, and the tests and CI open with `content`. `match()` reports the level used (`'files'`, `'content'` or `false`) in its output and context (match profile `execution-store-match-2.0`).
- **Why `files` by default.** A content check streams every row at about 440,000 rows a second: 8.3 s at 10,000 patients, 83 s at 100,000 and 247 s at 300,000, so on the order of a quarter of an hour per open at 1,000,000 patients (365 million rows, extrapolated). The file check took 1.45 s at 300,000. Content still always runs at build time, where its cost is part of a build that takes far longer, and in CI.
- **DuckDB derive.** File-backed (`build.duckdb` in the build directory), with `memory_limit` (default 4 GB, recorded in the manifest outside the identity), `temp_directory` and `preserve_insertion_order=false`, so order comes only from `ORDER BY`. The tables are derived straight from the staging files, which are deleted once loaded.
- **The reference** computes the input digest from per-patient canonical JSON and the per-bucket content digests by the same keys and line encoding, independently of the store. The tests require equality table by table, bucket by bucket and for the input digest, on the synthetic cohort, a hand-built fixture and the MIMIC fixture.

**Not in this increment:** an evidence table and the D5 typed columns, and bucket-level Merkle roots for incremental appends. They are next, with matching (D19 step 3).

*Consequence:* (`verification/execution-store-scaling-report.json`, regenerated after the generator fix (#128) for 1,000 to 300,000 patients; 4 GB limit, one DuckDB thread.)
- Python stays at 0.08 to 0.21 GiB while patients are added, at every size.
- The 100,000-patient cohort that 1.0 could not build (killed at 40.5 GiB) builds in 504 s with a 7.86 GiB peak. That is under the target of about 8 GB, but narrowly.
- 300,000 builds in 1,560 s with an 8.85 GiB peak.
- Opening with the file check takes 0.47 s at 100,000, where 1.0's content check on every open would have needed about 30 GiB.
- Builds that fitted in 1.0 are slower (49 s against 39 s at 10,000), because 2.0 validates and stages each patient in Python and digests twice.
- The query process reached 21.0 GiB at 300,000 and a wide match took 95 s, so matching is now the wall for interactive use, as D19 expected.

*1,000,000 patients (from a run with the pre-fix generator, not in the regenerated report):* the patients streamed in 45 minutes (173 million events, 120 GB of staging), and then DuckDB refused inside the derive (`Out of Memory Error: failed to pin block`, 3.7 of 3.7 GiB). The operator was not isolated; the postings `DISTINCT` is the prime suspect. Nothing was written under the store root. This size was not rerun after the generator fix, and the result is kept out of the report JSON, which binds only the regenerated run.

*Limitations:* DuckDB keeps within its limit by its own accounting, but the process does not. Resident memory reached about twice the 4 GB limit during the derive (8.9 GiB at 300,000) and did not fall when the connection closed. It follows the limit (0.45 against 1.29 GiB for one load at 256 MB and 4 GB), so the limit is a lever and not a cap. Whether the rest is allocation outside the buffer manager or freed memory the macOS allocator keeps is not resolved. The 1,000,000-patient derive needs a bucket-at-a-time derive, typed staging or a larger limit; none is built here. The usual limits also apply: one machine, synthetic data replicated beyond 10,000 patients, and ten queries at the largest sizes.

## D21 Matcher scaling: cheap wins, then parallel Python, then Rust only if needed

D19 step 3 and D20's measurement left matching as the wall: at 300,000 patients a wide `match()` took 95 s and the query process reached 21 GiB. The profile showed waste (a 106,000-parameter `IN` list, every candidate's events held at once, a result dict and validation per patient) and, beneath it, an engine whose pairwise kernel is the real cost. The order is therefore: remove the waste, measure; parallelise Python, measure; build Rust only if both together miss the target.

**Protocol.** One report (`verification/matcher-comparison-report.json`, `tools/benchmark_matcher.py`), the same seeded queries and snapshots (store 2.0 at 1,000, 10,000, 100,000 and 300,000 patients), each arm and size in a fresh process; wall time p50, p95 and maximum, per-candidate cost (engine-only and end to end), query-process peak RSS, and a per-query digest of every result so that arms are compared for byte identity. Arms:
- **A** the matcher before D21 (git ref `7dc3364`).
- **B** this change: chunked load and match, prepared queries, trusted events, integer window checks, SQL comparability.
- **C** parallel Python over chunks at 1, 2, 4, 8 and 14 workers.
- **D** Rust, single-threaded and batched, including the FFI.
- **D'** Rust, parallel.

**Target**, at 300,000 patients on this machine (M4 Pro, 64 GB): full-detail `match()` p95 at most 10 s, maximum at most 30 s, query-process memory under 4 GB.

**Rule.**
- Stay in Python if B+C meets the target.
- Otherwise build Rust only if D is at least 10 times faster per candidate than B (single-threaded, including FFI), D' meets the target and it has 0 mismatches against the Python matcher.
- Between 3 and 10 times, prefer algorithmic work (time pruning of step domains, a candidate-count fast path) over a second language.
- Below 3 times, drop Rust.
- The Rust prototype is built only if B+C misses the target, so no toolchain is installed unless the measurements justify one.

**This PR: arm B.** Results are byte-identical to A (digests equal for 50 of 50 queries at 1,000 and 10,000, and 10 of 10 at 100,000 and 300,000; 300 seeded queries against a committed fixture in the tests). At 300,000 patients, detail `full`:

| | p50 | p95 = max | µs per candidate, end to end (engine) | query-process peak |
|---|---|---|---|---|
| A | 10.2 s | 93.7 s | 760 (388) | 19.8 GiB |
| B | 4.92 s | 71.1 s | 532 (296) | 0.92 GiB |

The memory target is met (0.92 GiB, flat in cohort size); the latency target is not (p95 and maximum 71 s). Per candidate B is 1.4 times faster than A end to end, so a Rust matcher would have to be 10 times faster than 532 µs, about 53 µs, to pass the rule's first gate. cProfile puts the engine's pairwise arc-consistency kernel at about 60% of a wide query at 100,000 patients and event loading at 11 to 23%, so the next steps, C and then algorithmic pruning, aim at that. Arm C follows.

**This PR: arm C, and the decision.** `match(..., workers=N)` runs the engine's units (2,000 candidates each) in a persistent pool of spawned processes, each with its own connection to the snapshot, and merges by patient index; results are byte-identical to arm B for every query at every size and worker count (50 of 50, 50 of 50, 10 of 10, 10 of 10 at 1, 2, 4, 8 and 14 workers). At 300,000 patients, detail `full`, warm pool:

| Workers | p50 | p95 = max | µs per candidate | query-process peak | largest worker peak |
|---|---|---|---|---|---|
| 1 | 4.98 s | 71.35 s | 536 | 0.92 GiB |  |
| 2 | 3.00 s | 35.30 s | 278 | 0.58 GiB | 0.60 GiB |
| 4 | 1.98 s | 18.59 s | 160 | 0.58 GiB | 0.58 GiB |
| 8 | 1.55 s | 12.01 s | 109 | 0.61 GiB | 0.55 GiB |
| 14 | 1.42 s | 9.70 s | 91 | 0.59 GiB | 0.60 GiB |

(ten queries, one repeat each)

**Target.** B+C at 14 workers: met narrowly in one early run, not met at 30 queries × 3 repeats: p95 15.3 s, max 20.7 s. The ten-query run (p95 = max = 9.70 s against 10 s, a 3% margin on one measurement of ten queries) did not hold up when repeated over thirty queries three times each (seeds 0 to 29, 90 measurements per arm, byte-identical to arm B for all 30):

| Arm | p50 | p95 (range over the 3 repeats) | max (range) | µs per candidate | Query-process peak |
|---|---|---|---|---|---|
| B (1 repeat) | 22.6 s | 117.0 s (117.0 to 117.0) | 140.6 s (140.6 to 140.6) | 417 | 1.48 GiB |
| C, 8 workers | 4.6 s | 19.6 s (19.5 to 19.7) | 21.6 s (21.5 to 21.6) | 75 | 0.78 GiB |
| C, 14 workers | 3.7 s | 15.3 s (15.1 to 18.7) | 20.7 s (17.5 to 20.7) | 62 | 0.78 GiB |

The maximum (20.7 s against 30 s) and memory (0.78 GiB against 4 GB) are met; the p95 is missed by a factor of about 1.5 at 14 workers, and at 8 workers (p95 19.6 s) by more. The thirty queries include wider ones than the first ten (seed 19: 195,270 candidates, 140.6 s in arm B), so their absolute figures are not comparable with the table above.

**Decision: the target is not met, so D21's rule stands, and the Python matcher is improved before the Rust prototype is considered.** Parallel Python cut the per-candidate cost about 6 times (536 to 91 µs over the first ten queries; 417 to 62 µs over thirty). An earlier draft of this entry kept Python anyway, on the grounds that Rust would probably miss its separate 10-times gate. That reasoning was declined: the rule was fixed before measuring so that a miss could not be argued away afterwards, and "probably" is not a measurement. The rule does not forbid improving arm B before the next measurement, and work on the shared algorithm benefits any later Rust port equally. So the order is:
1. **Algorithmic work on the engine, arm B.** About 60% of a wide match is the pairwise arc-consistency kernel (`certain`, `plausible`, `gap`), with time pruning on the posting summaries as the other lever. Results must stay byte-identical, or be differentially tested where they cannot.
2. **Re-measure B and C at 30 queries × 3 repeats at 300,000 patients.** If the target is met, stay in Python.
3. **If it is still missed, build the Rust prototype (arms D and D′) and apply the 10-times gate**, installing a toolchain then and not before.

**This PR: step 1, the engine kernel, and step 2, the re-measurement.** The kernel in `trajectory_graph.py` now decides each pair of candidate events by a closed form of the same test. When two events' variables are not linked, `Times.gap` reduces to range ends, so each constraint becomes a bound on one end of the other event's range. That is exact on integer times, and the derivation is in the code. Pairs that may be linked (one event bound to both steps, or events sharing a component) keep `certain` and `plausible` as they were. The plan compiles the bounds once per step pair, direction and test. Unary tests use the same closed form, candidate order comes from one sort per patient, and `Event` and `Times` are built without per-call overhead. AC-3's queue, domain order and the tree assignment's first-supporting-event choice are unchanged. Evidence that every result is unchanged:
- **Engine tests.** The existing tests and differential pass.
- **Frozen copy.** A new test runs the old engine, kept byte for byte as a test fixture, against the new one: 11,750 comparisons of `match` and `decide`, including shared variables, twin relations, wide domains and small search budgets. Seeded mutations of the kernel fail it.
- **Store.** The store's digest fixture still matches.
- **Benchmark.** The benchmark's digests are equal for every query, arm and size (B against A; C against B at every worker count).

Engine-only, over 142,657 captured candidates at 10,000 patients, the engine is 3.2 times faster (298 to 92 µs per candidate), and the kernel itself 8.5 times faster under cProfile (`Times.gap` calls: 72.5 million to 96,000).

Re-measured at 300,000 patients, 30 queries × 3 repeats, on the same snapshots and the same day as the code before the kernel (main 12b3440; that run's report is a scratch copy, not committed, so the "before the kernel" rows are not bound evidence); all 30 results are byte-identical between arms in both:

| Arm | Code | p50 | p95 (range over the 3 repeats) | max (range) | µs per candidate | Query-process peak |
|---|---|---|---|---|---|---|
| B (1 repeat) | before the kernel | 22.7 s | 116.8 s | 139.7 s | 418 (engine 242) | 1.49 GiB |
| B (1 repeat) | with the kernel | 11.9 s | 56.5 s | 81.8 s | 258 (engine 82) | 1.49 GiB |
| C, 8 workers | before the kernel | 4.6 s | 19.6 s (19.6 to 19.8) | 21.8 s (21.4 to 21.8) | 75 | 0.80 GiB |
| C, 8 workers | with the kernel | 3.1 s | 9.3 s (9.0 to 9.4) | 12.5 s (11.9 to 12.5) | 49 | 0.82 GiB |
| C, 14 workers | before the kernel | 3.8 s | 17.1 s (15.7 to 17.4) | 19.8 s (17.7 to 19.8) | 66 | 0.82 GiB |
| C, 14 workers | with the kernel | 2.5 s | 7.9 s (7.4 to 8.4) | 11.8 s (10.0 to 11.8) | 42 | 0.82 GiB |

Over the first ten queries (one repeat), arm B at 300,000 went from 538 to 346 µs per candidate and from 71.8 to 56.2 s at its maximum, and C at 14 workers from 9.70 to 7.25 s.

**Outcome: target met; stay in Python.** B+C at 14 workers meets every part of the target at 30 queries × 3 repeats: p95 7.9 s against 10 s (7.4 to 8.4 over the repeats), maximum 11.8 s against 30 s, query-process memory 0.82 GiB against 4 GB. At 8 workers it is met narrowly (p95 9.3 s, 9.0 to 9.4). By the rule, the Rust prototype is not built, arms D and D′ are not run, and no toolchain is installed. The p95 margin is 21% pooled and 16% in the slowest repeat (8.4 s). The levers left, if a larger cohort or a slower machine needs them, are event loading in the store (now 160 of 258 µs per candidate in arm B, the larger part) and time pruning of step domains on posting summaries.

Arms D and D' are not run. The recommended worker count on this machine is 14 (10 performance and 4 efficiency cores; the library default stays `workers=1`, so a caller opts in); a query of fewer than 2,000 candidates is one unit and gains nothing. Pool start-up is 0.2 to 0.25 s, once per pool; workers share the store's memory limit, divided by their count (at least 256 MB each). Comparability and exclusions still run in the query process (under 2% of the time in the 10,000-patient profile; not profiled separately at 300,000).

*Limitations:* one machine; synthetic data replicated beyond 10,000 patients; ten queries at 100,000 and above in the main runs, so p95 equals the maximum there (the target is judged on the 30-query, 3-repeat confirmation); arm A is the old code imported from a git archive over the same snapshot files; peak memory includes the caller's windows dictionary and the full result; the margin on the p95 is 16 to 21% on one machine.

## D22 The adapter passes shared source variables to the engine; the store still refuses them

D9 refused a patient whose events share a source variable (`SHARED_SOURCE_VARIABLE`), because the engine then gave each event its own times and would have understated exactness. D13 gave engine events optional `start_var` and `end_var`, and events naming one variable now share it. So the stopgap is lifted in `match_snapshot`.

**Design.**
- **Names.** Every engine event names its source variables, interval events both and point events their start. The engine names are the source variable ids themselves: unique in a snapshot, stable across routes, and on one clock each, since the bounded profile puts an event's two variables on one clock. Every event naming a variable is sent that variable's one tightened range, rebased by its clock's offset.
- **The other D9 rules stay.** A source constraint the tightened box does not imply still refuses the patient (`SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE`), checked before shared variables; so do the clock, unit and window-clock rules. Shared variables are equalities, which the engine models exactly; other source edges between events are not, and stay refused.
- **The source-network check** of every binding stays. With no lost source constraint the patient's region is exactly the tightened bounds, the proper-interval edges and the shared variables. The bounded check now replays the bound events plus every event linked to them through shared variables, naming variables by source id; events linked to none are independent of the binding. The tests compare it with the whole-network replay on shared-variable patients.
- **Points.** A shared start stays one engine variable when events go as points, and with each end belonging to its own event, every start in its tightened range still has a feasible end; the projection onto starts stays exact. A shared variable that is some event's end orders other events' starts through that event, and points would drop it, so such a patient keeps its intervals. The tests show the decisions equal the interval path on shared starts, and that a patient whose Y starts when X ends is `exact` through intervals where points would have said `possible`.
- **Engine refusals** of a patient's shared variables (`SHARED_VARIABLE_COMPONENT_LIMIT`, `EMPTY_SHARED_VARIABLE`) become that patient's `refused` result with the engine's code; other patients are matched. A validated snapshot cannot produce `EMPTY_SHARED_VARIABLE`, since an inconsistent source is refused when prepared, so the test injects the conflict into the engine's input.
- **Strict intervals (added after review).** The engine lets an event end as it starts; the source needs start + 1 ≤ end. Without shared variables the tightened box absorbs that, but once a shared variable links one event's end to another's start, the engine's region strictly contains the source's, and no tightening can close the gap: the missing fact is the joint relation through the variable. The review's repro, Y starting when X ends with `after` at least 1 µs, came back `possible` and complete although the source entails it. So:
  - a `possible` binding (or a relaxed result's or relaxed match's `possible` certainty) that the source entails is reported `exact` (`EXACT_BY_SOURCE_ENTAILMENT`). An entailed binding is exact over the source, so this is sound on any patient;
  - on a patient with a shared variable in an end role, a `possible` binding the source does not entail is marked `complete: false` (`EXACTNESS_NOT_DECIDED_STRICT_INTERVALS`), since another binding might be exact over the source but not over the engine's model.

**Before and after.** The reviewer's probe, X and Y sharing a start with `after` from 0 to 0, was `refused` (`SHARED_SOURCE_VARIABLE`); it is now `exact`, decided by the engine and entailed by the source network. The same patient with `after` at least 1 is `none`.

**The store still refuses them.** `ingest_rows` and the store contract are unchanged: the box verdict still counts a shared variable against `box_representable`, and the execution store refuses such patients at ingest (`INGEST_PATIENT_NOT_BOX_REPRESENTABLE`). Storing the variable ids and passing them to the engine from the store is a follow-up. The MIMIC demo has no shared variables, so its store report's figures are unchanged.

*What remains:*
- non-box source networks, which need the engine to take source edges between events;
- the engine's end at or after start is weaker than the source's start before end. With shared variables a possible binding can need an empty event (Y starts when X ends, starts equal); the source-network check refutes it, and the adapter reports `unresolved` rather than searching on to `none`. A possible binding the source does not entail is left incomplete. **Follow-up in the engine:** an optional per-event `proper: true`, or a profile option, making the start-to-end order edge in `Times` −1 instead of 0, with the reference and the trajectory page updated. The engine's model would then equal the source's, removing both this `unresolved` case and the incomplete `possible`. **Done in D24:** a per-event `proper`; the `unresolved` case is now `none` and the incomplete `possible` complete, both decided by the engine (the trajectory page sends no proper events and is unchanged);
- store support for shared variables. **Done in D24** (execution store 2.1).

## D23 Per-bucket derive: the store builds at 1M patients

D20 left one wall: at 1,000,000 patients the build streamed every patient and then DuckDB refused inside the derive (`Out of Memory Error: failed to pin block of size 256.0 KiB (3.7 GiB/3.7 GiB used)`) under the 4 GB limit. The operator was not isolated.

**Diagnosis.** Main's derive was run statement by statement on a staged 300,000-patient cohort (the benchmark's, with the fixed generator), with each statement's time, the largest DuckDB-accounted memory (`duckdb_memory()`, sampled every 0.25 s) and the process's resident memory.
- *Extrapolation.* Every statement's input grows with the events, and the events with the patients (the cohort beyond 10,000 is replicated). So the memory a statement needs at 1,000,000 patients is about 3.3 times its need at 300,000, and 300,000 patients under a 1.2 GB limit (4 GB × 0.3) stand in for 1,000,000 under 4 GB.
- *At 1.2 GB the postings statement fails*, after 23 s, with the same error (`Out of Memory Error: could not allocate block of size 256.0 KiB (1.1 GiB/1.1 GiB used)`). Every statement before it succeeds, spilling as needed: the events load spills 14.9 GB and takes 171 s.
- *Its parts alone succeed.* With the `DISTINCT` materialised first and the join and `GROUP BY` run as a second statement, both succeed at 1.2 GB (25 s and 47 s). So it is not one operator that cannot spill but the plan: DuckDB pre-aggregates the events by (patient, event) (52 million groups at 300,000), aggregates the closure-expanded `DISTINCT` (83 million rows) and again by (patient, event, concept), hash-joins the two aggregates and groups the result. Four hash aggregates and a hash join, each sized by the events, are live at once, and their minimum reservations together exceed the limit.
- *Threshold.* Run alone over the first N patients of the same staged cohort, the statement succeeded at 1.6 GB and failed at 1.2 GB for 200,000 patients, and succeeded at 2.0 GB and failed at 1.6 GB for 300,000; at 100,000 it succeeded at 1.2 GB, 0.6 GB and 0.4 GB but failed once at 0.9 GB, so near the edge the outcome is not deterministic. The need is about 0.6 to 0.67 GB per 100,000 patients, about 6 to 6.7 GB at 1,000,000: above 4 GB, which holds to about 600,000 to 650,000 patients.
- *Resident memory.* Under 4 GB the events load alone took the process to 5.5 GiB and the derive to 8.5 GiB at 300,000, with DuckDB at its limit; under 1.2 GB, to 2.4 and 3.0 GiB. The process holds about twice the limit, and materialising whole tables from staging is where it grows.

**Decision.** Derive one part at a time.
- **Parts.** `add()` writes each patient's rows to the staging files of the current part of the patient's bucket (the bucket is a function of the identifier alone, `bucket_of`). A part is closed once it holds `part_rows` staged rows (default 2,000,000: events, event concepts, coverage periods and excluded records), always between patients. The patient list and the closure stay one file each.
- **Derive.** The patient index, `patients` and `concept_closure` are derived once. Then, bucket by bucket and part by part, the part's events and event concepts are loaded into temporary tables joined to the patient index, the part's postings and distinct (source, concept) pairs are computed from them, and its rows are appended to the five patient-keyed tables. `source_concepts` is the distinct union of the parts' pairs. Each statement sees one part, about a million events, whatever the cohort.
- **Write.** Partitioned tables are written one bucket at a time in the layout `PARTITION_BY (bucket)` gave (`bucket=k/data_0.parquet`), so a sort holds one bucket rather than the table.
- **Bucket count.** It stays 8. Parts, not buckets, bound the derive, so no larger, recorded bucket count is needed, and no identity changes.
- **Instrumentation.** `on_stage` now reports each bucket of the derive (`derived:bucket=k`): parts, each statement kind's seconds, the largest DuckDB-accounted memory and temporary storage after any statement, and the process's peak; the scaling benchmark adds resident memory after each and records the build's peak over `memory_limit`.
- *Declined for now:* typed staging (Parquet or DuckDB-native batches instead of ndjson). With parts the JSON load is bounded, and it would need Arrow or a DuckDB connection inside `add()`. It would shrink the staging (0.69 KB per event) and the load time, and stays the next lever for build time and disk. **Taken up in D26**, under a rule fixed before measuring.

**Identity.** Same input, same tables, same digests. Every row of `events`, `event_concepts`, `coverage`, `excluded_records` and `concept_postings` depends on one patient's staged rows, the closure and the patient index, and every patient is in exactly one part, so the union of the parts' rows is the table one statement over all the staging gives. Postings are keyed (concept, patient), so per-part postings concatenated are the whole table's rows. A distinct union of distinct sets is the distinct set, which gives `source_concepts`. The bucket of a row comes from the patient index, never from the part, and the patient index and input digest are computed as before over the whole patient list. The canonical forms are unchanged, so the content digests and the input digest are those of main; only `snapshot_id` changes, through the builder artifact digest of `execution_store.py`.
- *Tests.* `patterns/fixtures/execution_store_content_digests.json` records the input digest and every table's per-bucket digests built at main fdd6e97 for the committed synthetic cohort, that cohort ten times over, the hand-built fixture and the MIMIC-shaped fixture. The tests rebuild each and require identical digests, at the default part size and with one patient per part (and several parts per bucket for the tenfold cohort). Main's single-statement derive is also kept frozen in the test and run over the same staging gathered into whole files; its digests equal the per-part derive's at three part sizes, on the synthetic cohort and on the fixture with excluded records and non-ASCII identifiers. A third test checks that parts hold whole patients of one bucket, that no patient is in two parts, and that `bucket_of` agrees with the SQL and the reference.
- *At scale.* The pure-Python reference and the content verification of every build still apply: the regenerated report's builds verified their files by content at every size, 1,000,000 included.

*Consequence* (`verification/execution-store-scaling-report.json`, regenerated for 1,000 to 1,000,000 patients in one run; 4 GB limit, one DuckDB thread; "before" is the report committed at main fdd6e97):

| | 100,000 | 300,000 | 1,000,000 |
|---|---|---|---|
| Build, add to publish | 485 s (before 503 s) | 1,463 s (before 1,597 s) | 4,901 s (82 min) |
| Derive | 40 s (before 43 s) | 116 s (before 189 s) | 401 s; 176 parts, nothing spilled |
| Resident after the derive | 4.00 GiB (before 8.46) | 5.19 GiB (before 8.17, peak 8.59) | 5.49 GiB |
| Peak, build process | 5.11 GiB (before 8.48) | 8.09 GiB (before 8.78) | 8.12 GiB; refused before |
| Staged ndjson, build database | 12.0 GB, 2.4 GB | 36.0 GB, 7.2 GB | 120.1 GB, 24.0 GB |
| Content verification, separate process | 82 s, 2.37 GiB | 247 s, 5.53 GiB | 843 s, 7.85 GiB; agrees |
| Open with the `files` check, warm | 0.47 s | 1.30 s | 1.19 s |

- **1,000,000 patients build and verify.** The build published a snapshot whose files the build itself and then a separate process verified by content; the ten benchmark queries ran in full (`match()` p50 16.6 s, max 246 s, one process, 6.79 GiB).
- **The derive's memory no longer grows with the cohort.** The build peak is now set by the per-bucket sorts for content digests, which DuckDB spills; it is 8.09 GiB at 300,000 and 8.12 GiB at 1,000,000 here. Content verification's resident memory still grows, from 2.37 GiB at 100,000 to 7.85 GiB at 1,000,000, probably because the verifying store is opened without the build's `memory_limit` (a follow-up). **Done in D24**, where the cause proved different: the verifying stores already had the limit, and the growth came from sorting a whole bucket per ordered query; content digests now sort one `patient_idx` range at a time. DuckDB's accounted memory stays within the limit (at most 3.71 GB), and the process holds about 1.5 times the limit through the derive and 2.2 times at its peak, now set by sorting each bucket's events for the content digests (7.92 and 8.05 GiB), which DuckDB does out of core. A pilot of this code at 300,000 under 1.2 GB, the stand-in that main failed, built with a 2.84 GiB peak.
- **Time is linear** at 4.85 to 4.90 s per 1,000 patients; `add()` in Python is 47% of it.
- **Matching is unchanged.** The matcher comparison, regenerated on the new snapshots, gives the same per-query result digest as before for every arm and size and in the 300,000-patient confirmation. That confirmation still meets the D21 target at 14 workers (p95 9.1 s, 8.6 to 9.4 over the repeats; max 12.9 s; 0.79 GiB), with a narrower margin than the 7.9 s of the run before. Single-process event loading and engine cost per candidate are unchanged at 300,000 (arm B: loading 160.34 → 160.26 µs, engine 82.48 → 82.22 µs), and so is C at 8 workers (48.95 → 49.55 µs), so the store change did not slow matching. Only C at 14 workers moved, 41.59 → 47.32 µs per candidate (p95 7.9 → 9.1 s), by 14% between two runs on identical results; the cause is not established (likely machine state at full core count), and two runs cannot show it is within normal variation. The margin in this run is 9%.

*Limitations:* the factor of about 2 between resident memory and the limit is still DuckDB's and the allocator's, not resolved, so on this machine a 4 GB limit means about 8 GiB resident. The patient list, the input digest's sort and the patient index are whole-cohort structures, about 100 bytes a patient, which DuckDB can spill. Staging and the build database need about 0.83 KB of disk per event at the end of the derive (144 GB at 1,000,000). Replication beyond 10,000 patients flatters the snapshot size at 1,000,000 (3.51 GB, less than at 300,000, because copies sort together and compress) and makes query results exact multiples. Single-process matching holds 6.8 GiB and takes up to 246 s at 1,000,000; partition-parallel matching (D21) was not measured at that size.

## D24 Shared variables end to end: strict intervals in the engine; the store holds shared variables

D22 passed shared source variables to the engine and left three follow-ups. First, the engine's events ended at or after they started, while the bounded profile's end strictly after. Second, the store still refused patients with shared variables. Third, content verification's memory grew with the cohort (D23). This change closes all three in one pull request. The first two depend on each other, because the store can hold shared variables only once the engine's model equals the source's; the third is independent and was bundled because it forced the same report regeneration: the store can hold shared variables only once the engine's model equals the source's.

**1. Engine: `proper` events.** An engine event may say `proper: true`. Its order edge in `Times` is then start − end ≤ −1 instead of ≤ 0, so it ends at least one grid step after it starts. A point cannot be proper (`INVALID_EVENT_PROPER`). A proper event naming one variable as both its start and its end, or one whose ranges leave no room for the order, is refused with `EMPTY_SHARED_VARIABLE`.
- *Per event, not per query or profile.* Strictness describes a record, not a question, and one patient may hold records from sources with different conventions. A patient's network is solved once, whatever the query. The store keeps it as a column. A profile-level option would have needed a new profile identifier for every caller; a query-level one would have made the patient's network depend on the query.
- *Exactness of the kernel.* An unlinked proper event's −1 edge links only its own two variables. It tightens that event's ranges (`dom`, solved with the edge), which the closed form reads, and never the joint range of two unlinked variables, which stays the product of their ranges. Linked pairs take the original test over distances that include the −1 edges.
- *Default unchanged.* Without `proper`, `Times` takes the same path: `strict` is empty and every edge is 0. `KernelIdentityTests` against the frozen pre-kernel engine (every result field, key order and `decide`'s intermediate decisions on 2,400 seeded cases and their wider relatives), the 1,500-case differential, and `proper: false` against an absent `proper` on 300 cases all pass. So do the store's 300 recorded match digests and the matcher comparison's arm A against arm B (below).
- *Reference.* `trajectory_graph_reference.py` gives a proper event a −1 edge from end to start in its Bellman–Ford facts, written independently. 900 new differential cases (proper exposures that could otherwise end as they start, events starting when an exposure ends, bounds near zero) agree with it. Dropping `proper` changes the reference's answer in 16 of them, an engine ignoring it disagrees in 15, and refusing the closed form everywhere changes nothing.
- *The trajectory page* sends no proper events: its events come from the synthetic cohort's records and points, and it never builds an interval that must be strict. So `app.js` is unchanged, and so is its Node parity test, since the engine's path without `proper` is unchanged.

**2. Adapter: interval events are proper.** `match_snapshot` sends every interval event with `proper: true`, since the bounded profile requires start + 1 ≤ end. With no lost source constraint the engine's network is then the source region exactly: the tightened box, the proper edges and one variable per shared source variable.
- `EXACTNESS_NOT_DECIDED_STRICT_INTERVALS` and its `strict_gap` path are removed.
- The source-network check, its downgrades and `EXACT_BY_SOURCE_ENTAILMENT` stay as safety nets. A test runs the D22 probes and chains through six relations, two units and both projections, and finds no adapter code and every result decided by the engine and complete. Against an engine stripped of `proper`, the old paths still fire.
- `ingest_rows` (now `trajectory-graph-sulo-ingest-1.1`) carries `proper: true`, and a shared variable no longer counts against `box_representable`; only a non-box source constraint does.

*Before and after* (strict-interval cases; the D22 probes are kept):

| Case | Before (D22) | After |
|---|---|---|
| Y starts when X ends, `after` 0 to 0 | `unresolved`, `POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK` (engine: possible) | `none`, decided by the engine |
| Y starts when X ends, `after` at least 1 µs | `exact`, `EXACT_BY_SOURCE_ENTAILMENT` (engine: possible) | `exact`, decided by the engine |
| The same as a relaxed result's certainty | relaxed, certainty lifted to `exact` by the adapter | relaxed, `exact` from the engine |
| Y starts when X ends, `after` 1 µs to 5 days | `possible`, `complete: false`, `EXACTNESS_NOT_DECIDED_STRICT_INTERVALS` | `possible`, complete, decided by the engine |
| X and Y share a start, `after` 0 to 0 (the reviewer's probe) | `exact` (engine) | `exact` (engine), unchanged |
| The same with `after` at least 1 day | `none` (engine) | `none` (engine), unchanged |

**3. Store 2.1: shared variables.** The ingest accepts `start_var`, `end_var` and `proper` on an event. They are optional, so the ingest profile and every existing input digest are unchanged. The store refuses at ingest what the engine would refuse when matching (`EMPTY_SHARED_VARIABLE`, `SHARED_VARIABLE_COMPONENT_LIMIT`), so a trusted `match` never meets it. `INGEST_PATIENT_NOT_BOX_REPRESENTABLE` stays for non-box source constraints.
- *Schema.* `events` gains `start_var`, `end_var` (null where the ingest names none, which means the engine's default) and `proper`, between `ongoing` and `evidence`. `patients` gains `shared_variables`: whether two of the patient's events name one variable, default names included, computed at ingest.
- *Staging.* A staged line leaves out the three event fields when they are absent, so a synthetic build stages exactly the bytes it did.
- *Matching.* `match` and the parallel workers pass each event's variables and `proper` to the engine. A candidate whose events share variables is loaded whole, with events carrying no step concept given an empty concept list. An event no step can bind may link two that can, so filtering such a patient by concept, as 2.0 would, can turn a `none` into `possible`; a test shows exactly that. Other patients load as before: an event that shares no variable links only its own two.
- *Identity.* The new columns are canonical content, so `patients` and `events` digests, and every snapshot identifier, change; the profile is `execution-store-2.1`. The pure-Python reference builds the columns independently, and the store-against-reference equality and the determinism tests hold. On the cohorts D23 recorded (synthetic, synthetic ×10, the fixture, at several part sizes), the input digest and every other table's digests equal the recorded ones. `patients` and `events`, read as their 2.0 columns, give the recorded digests too, and the new columns hold nothing there. On the MIMIC fixture the events' evidence names the adapter's 1.1 profile, so only the other tables are compared.
- *MIMIC stage 2.* The mapping passes each row's variables and `proper` through. A variable shared across a patient's claim stores is no longer refused: the store shares it, and the fixture test shows the store matching such a patient exactly as the engine does on the adapter's rows (`after` 0 to 0 exact through a shared uncertain start, possible without it). The demo has no shared variables, and every figure in its regenerated report is unchanged; only identity (snapshot id, input digest, adapter profile, artifact digests) and timings changed (the adapter took 62.2 s against 84.7 s, the whole run 227.0 s against 244.7 s, on the same machine in a different state).
- *Equality with the adapter.* A store test sends bounded sources with shared variables (the D22 probes and a three-event chain) through `ingest_rows` into the store. Over six relations, each patient's store result equals the adapter's own `match_snapshot`: status, binding, certainty, method, completeness and witness, on the store's time line.

**4. Verification memory.** `finish()` and the scaling benchmark already opened the verifying store with the build's `memory_limit`, so D23's guess at the cause was wrong. Under that limit, each bucket's single ordered query sorted a whole bucket (21.6 million `events` rows at 1,000,000 patients), and the process held about twice the limit. Two changes:
- `open_store(verify='content')` without a limit now verifies under the manifest's `builder.memory_limit`, then resets to DuckDB's default.
- `content_digests` orders a partitioned table's bucket one `patient_idx` range of `DIGEST_SPAN` (100,000) indices at a time, then the null indices. The key starts with `patient_idx`, so the concatenation is the bucket in canonical order: the same digests for any span, covering every row whatever the files hold. A test checks this with rows at index −5, 10⁶ and null. Each sort holds one range, about 12,500 patients of the bucket. The build's own digests use the same function.

*Consequence for verification memory* (`verification/execution-store-scaling-report.json`, regenerated for 1,000 to 1,000,000 patients in one run of 9,212 s; 4 GB limit, one DuckDB thread; "before" is the report at main 0a18b9a):

| | 100,000 | 300,000 | 1,000,000 |
|---|---|---|---|
| Content verification, separate process: peak | 2.40 GiB (before 2.37) | 2.42 GiB (before 5.53) | 2.33 GiB (before 7.85) |
| Content verification: time | 85 s (before 82) | 252 s (before 247) | 819 s (before 843) |
| Build: resident after the content digests (peak) | 4.42 (4.60) GiB (before 4.39, 4.57) | 5.47 (5.66) GiB (before 7.74, 7.93) | 5.64 (5.85) GiB (before 7.87, 8.08) |
| Build: peak, whole process | 5.14 GiB (before 5.11) | 8.08 GiB (before 8.09) | 8.14 GiB (before 8.12) |
| Build time | 495 s (before 485) | 1,482 s (before 1,463) | 4,968 s (before 4,901) |

- Verification memory no longer grows with the cohort. At 100,000 every bucket's largest index is below the span, so each bucket is one query, as before.
- The build peak did not fall. Writing the Parquet files sorts each whole bucket for reads, which now sets the peak (7.91 GiB at 1,000,000), and the in-process verification holds that level. Ranging that sort too is possible but not done.
- The staging is the same size to the byte (120.1 GB at 1,000,000), and every build verified its files by content.

*Matching is unchanged* (`verification/matcher-comparison-report.json`, regenerated on this run's snapshots with the committed settings, plus `--confirm --large-queries 30 --large-repeats 3` at 300,000).
- *Results.* Arm A, the matcher at 7dc3364, reads store 2.0. It opened the 2.1 snapshots as the same Parquet files under a manifest relabelled 2.0 (`snapshot_view` in its entries; 2.1 only added columns, which it never selects). Its results equal arm B's byte for byte for every query and size (50, 50, 10 and 10). C equals B at every worker count, and in the confirmation (30 of 30 at each count). Candidate counts and listed results equal the previous report's, query by query, for every arm and size.
- *Cost.* Arm B's cost per candidate at 300,000 is unchanged: 342.7 against 344.5 µs end to end, loading 204.0 against 206.7 µs, engine 106.9 against 105.4 µs; in the confirmation 258.4 against 257.6 µs. In the single-process arm the extra columns and the `shared_variables` lookup cost nothing measurable. For the parallel arm it is not shown: C at 8 workers costs 3.6% more per candidate than in D23, a difference not separated from run-to-run spread.
- *Target.* The confirmation still meets the D21 target at 14 workers: p95 9.35 s (9.06 to 9.89 over the repeats), max 13.4 s, query process 0.79 GiB, against 9.13 s, 12.9 s and 0.79 GiB in the D23 run. **At 8 workers the target is no longer met:** p95 rose from 9.40 to 10.56 s, one repeat at 11.6 s, against the 10 s target (`confirmation.target_check.C_w8.p95_met_all_runs` is now false; it was true in D21 and D23), with the same results and 3.6% more cost per candidate (51.4 against 49.6 µs). The decision rests on 14 workers, the recommended count, where the target holds; 8 workers was always the narrow case. As in D23, that spread is between two runs, and its cause is not established.
- *Follow-ups from review* (not done here, because each touches a file the matcher report binds, or a format, and is not a correctness defect today):
  - `readable_snapshot` in `tools/benchmark_matcher.py` relabels any 2.1 snapshot as 2.0. It is equivalent only when no patient shares variables and no event is proper, which holds for the synthetic cohorts benchmarked; it should refuse otherwise. Its temporary directory is also left behind if the worker raises. **Done in D25.**
  - A proper event that cannot end after it starts is refused as `EMPTY_SHARED_VARIABLE` even when nothing is shared; a neutral code would read better. **Done in D25** (`EMPTY_PROPER_EVENT`).
  - The ingest profile stays `execution-store-ingest-2.0` though it accepts three new optional fields, and `proper: false` or an explicit default variable name gives a different input digest from leaving them out. Normalising at ingest, or a 2.1 ingest profile, would make the identifier name one format. **Done in D25** (normalised at ingest; the identifier is kept).
  - Writing the Parquet files still sorts each whole bucket, which sets the build peak. **Not done in D25**: a ranged write does not give the same Parquet bytes.
- *Before and after for strict intervals and the store test* are above. The trajectory page and its Node parity test are unchanged: the page sends no `proper`, and the engine's path without it is the same code.

## D25 D24 review follow-ups; time pruning on the posting summaries

**Pre-committed rule for time pruning** (written and committed before any pruning measurement). After the candidate filter, a candidate that the per-patient posting summaries prove cannot match is pruned before its events are loaded.
- *Exactness* is required: no result may change at any `detail` level, the 300 recorded match digests (`patterns/fixtures/execution_store_match_digests.json`) must be unchanged, and a differential test (pruning on against off) must be byte-identical on the synthetic cohorts, the shared-variable fuzz cohorts and the existing differential suites, at 1 and 2 workers.
- *Keep pruning* only if it is exact and either
  1. arm B's end-to-end cost per candidate at 300,000 patients, in the regenerated matcher comparison's main run (ten queries, `per_candidate_microseconds.end_to_end`), falls by at least 15% against the D24 report (342.7 µs, so to at most 291.3 µs), or
  2. the 8-worker confirmation p95 (30 queries × 3 repeats, all runs pooled, `confirmation.target_check.C_w8.p95_seconds_all_runs`) is under 10 s.
- *Otherwise* the negative result is recorded here with its numbers, and the pruning code is removed from the pull request; the D24 follow-ups stay.
- *Pilot first:* arm B alone on the D24 300,000-patient snapshot, a few queries, to decide before a full regeneration. The pilot informs whether to regenerate; the verdict is taken on the regenerated report as stated above.

**1. The D24 review's follow-ups.**
- *`readable_snapshot`* (`tools/benchmark_matcher.py`) now refuses (`SnapshotViewError`) to relabel a 2.1 snapshot as 2.0 when any patient has `shared_variables` or any event is `proper` or names a `start_var` or `end_var`, counted in the snapshot's own Parquet files; it records both counts in `snapshot_view`, and it refuses any pair of profiles other than 2.1 read by 2.0 code. Its docstring states the equivalence only under that condition, and says that the copied manifest's content digests are 2.1's, not what 2.0 code would compute for the same rows, so the view is opened with `verify='files'` only. The scratch directory is now a context manager's, removed however the worker ends; a test shows the refusal for proper, named and shared snapshots, and the cleanup after a refusal and after a failure inside the block. Arm A's view of the synthetic snapshots still works: they hold none of these (both counts 0 in every arm A entry of the regenerated report).
- *`EMPTY_PROPER_EVENT`.* A proper event that names one variable for both ends is always refused with `EMPTY_PROPER_EVENT`, even when another event names that variable too. A proper event whose ranges leave no room for its end to follow its start (both ends at one time, say) is refused with `EMPTY_PROPER_EVENT` when no other event names its variables. `EMPTY_SHARED_VARIABLE` stays for conflicts in a component containing a variable two events name, a proper event's included, even when the event's own ranges would already be empty; the classification is by component, so it is coarse there. In `Times` the code follows the component: a component none of whose variables two events name is one event's own pair (only an event's order joins variables), so its emptiness is that event's. The store's ingest passes the engine's code through, the adapter maps it to a per-patient refusal like the other two (a validated snapshot cannot produce it; the test injects it), and the profile and adapter pages say so. The reference engine names no refusal codes and needed no change.
- *Ingest normalisation.* The store drops, at ingest, `proper: false`, a `start_var` equal to `<event_id>.s` and an `end_var` equal to `<event_id>.f` (`normal_event`, applied in the canonical patient that is both digested and staged), and the reference does the same independently (`normalised`). A document spelling every default out now has the same input digest, rows and snapshot identifier as one leaving them out (a test builds both). Before, `proper: false` already gave the same rows (the staged row leaves a false `proper` out) but a different input digest, and an explicit default name gave both a different digest and a non-null `start_var`. A name that is not the event's own default for that end stays (`b.s` on event `a` shares `b`'s start).
  - *Identifier: kept at `execution-store-ingest-2.0`.* With the normal form, the identifier names one format: every document the store accepts under it is read, digested and stored by what it means to the engine, and the fields are additive and optional, so a document without them is the 2.0 document it always was. Code from before D24 refuses a document using them by name (`INVALID_EVENT`), not by misreading it. The synthetic cohort uses none of them and the MIMIC conversion names only source variables, all proper, so their input digests are unchanged (the recorded-digest tests for the synthetic cohort, the tenfold cohort and the fixture pass; a test checks the MIMIC fixture's events are left as they are; the regenerated MIMIC demo report has the same input digest, `8e24100f…`).
  - *Declined:* (a) `execution-store-ingest-2.1` for documents using the fields, with 2.0 still accepted for the rest. It would mark the fields' arrival in the identifier, but the MIMIC input digest would change for a label alone, and a 2.1 document whose fields all normalise away would digest differently from the same 2.0 document, which is what normalisation is meant to stop. (b) 2.1 for every document: every input digest would change, the synthetic ones included, for no change in meaning. (c) Refusing the redundant forms instead of normalising them: it would break callers that spell defaults out (the D24 fixture's S4 does), for no gain.
  - *Not normalised:* `box_representable: true` against an absent field still gives different digests. The MIMIC conversion writes it explicitly, so normalising it would change the MIMIC input digest; it is outside the three fields the review named, and is left as it was.
- *Ranged Parquet write: not done.* Writing each bucket by `patient_idx` range into a pre-sorted table and copying that to the file, the natural analogue of D24's ranged digests, does not give the same Parquet bytes as the single `COPY ... ORDER BY`: on bucket 0 of the 10,000-patient snapshot's `events` the two files differ (sha256 `856461dc…` against `e91c9630…`; neither equals the snapshot's file either when rewritten from a reloaded table, so DuckDB's writer output depends on how its input arrives). Byte-identical files would need control over DuckDB's chunking that its `COPY` does not offer, and several files per bucket would change the snapshot's file layout. Content digests, and so identity, would not change, but the files would, which the task ruled out; and the memory gain was not measured. The build peak stays 8.1 GiB at 1,000,000 patients.

**2. Time pruning on the posting summaries.**
- *What.* After the candidate filter, `match` drops each candidate that the postings' start summaries (`min_start_lo`, `max_start_hi`, already stored per concept and patient) prove the engine would answer `none`, and gives it the engine's own `none` (`{"patient_id", "status": "none", "complete": true}`) without loading its events. For the unrelaxed query and every relaxation set the engine would try, a set is impossible when some step's concept under it has no posting for the patient, when a step's unary constraints (scope, and `after` to `START` or `END`, with that set's `extend_max`) need a start the posting cannot reach, or when an `after` between steps, or the start-to-start half of a `during`, needs a gap the two postings cannot reach. A candidate is pruned when every set is impossible. The parent prunes before cutting units, so workers receive only what is left.
- *Why it is exact.* Each posting covers exactly the events the engine lists under the concept, so every candidate event of a step, including the one `first` or `last` picks, starts within the posting's earliest and latest start; the engine decides each start over `Times.dom`, which is inside the recorded bounds (shared variables and `proper` only narrow it). `plausible`, which `certain` implies, passes an event or a pair only if those bounds can reach each constraint. So in an impossible set some domain or arc empties under both tests, by arc consistency, before any search, with `complete: true`; with every set impossible, no search ran, and the engine's answer is exactly that `none`. The query language has no negated or absence steps; absence enters only after the engine, through coverage and excluded records, which treat a pruned candidate's `none` as the engine's because it is. `during`'s grace bounds an end, which postings do not hold, so it is not used, and leaving a constraint out only weakens the test. Both detail levels list a pruned candidate with that `none`; `plan`, `candidates` and `counts` are computed as before.
- *No format change.* The stored summaries were enough, so the store profile stays 2.1 and the snapshot files are unchanged (the regenerated snapshots' Parquet files have the sha256 of the D24 run's, size by size). Per-concept end summaries would let `during`'s grace prune too; that is a format change, not made.
- *Alternatives considered.* Pruning inside the candidate filter (SQL over the postings) would have changed `candidates` and the `plan` counts, which the outputs and the recorded digests carry, and would have reported pruned candidates as `FILTERED`, not as the engine's `none`; pruning after the filter keeps every byte. A vectorised SQL version of the test was not built: the Python test costs 1.2 to 3.7 µs per candidate, and window and bound arithmetic on arbitrary integers would need care in SQL that Python's integers give for free. New summary columns (latest start per concept, end summaries) were not needed to pass the rule.

**3. Measurement and verdict.** The pilot (arm B alone, ten queries on the D24 300,000-patient snapshot; an ad hoc run, not in any committed report) gave 268.6 µs per candidate with pruning against 334.8 µs without on the same day (−19.8%; −21.6% against the D24 report's 342.7 µs), with the ten digests equal. So the reports were regenerated in full: the MIMIC demo, the scaling run to 1,000,000 with `--keep`, then the matcher comparison with the committed settings plus `--confirm --large-queries 30 --large-repeats 3`, on those snapshots, one at a time.

| 300,000 patients | D24 report | D25, `B_unpruned` (same run, pruning off) | D25, pruning |
|---|---|---|---|
| Arm B, ten queries: µs per candidate end to end (engine, loading) | 342.7 (106.9, 204.0) | 343.9 (108.3, 203.7) | **279.6** (83.9, 162.5) |
| Arm B, ten queries: p50 / p95 = max | 4.14 / 54.9 s | 4.11 / 55.1 s | 2.34 / 55.3 s |
| Arm B, confirmation (30 queries): µs per candidate; p50 / p95 / max | 258.4; 12.1 / 55.9 / 82.0 s | 257.8; 11.9 / 55.1 / 82.3 s | 189.9; 9.3 / 50.0 / 56.3 s |
| C, 8 workers, confirmation: p95 (range over repeats); max | 10.56 s (9.94 to 11.65); 15.1 s | | **8.41 s** (8.41 to 8.45); 10.3 s |
| C, 14 workers, confirmation: p95 (range); max | 9.35 s (9.06 to 9.89); 13.4 s | | 7.81 s (7.01 to 8.41); 9.2 s |
| Query process: B in the confirmation; C at 14 | 1.64; 0.79 GiB | 1.64 GiB | 1.15; 0.83 GiB |

- *Rule, criterion 1:* arm B's end-to-end cost per candidate at 300,000 in the main run fell from 342.7 to 279.6 µs, −18.4%, against the 15% required (at most 291.3 µs): **met**. Against the same-run baseline it is −18.7%.
- *Rule, criterion 2:* the 8-worker confirmation p95 is 8.41 s, under 10 s, in every repeat: **met**.
- *Exactness:* every per-query digest of arm B equals arm A's, `B_unpruned`'s and every C arm's at 1,000, 10,000, 100,000 and 300,000 (50, 50, 10, 10) and in the confirmation (30 of 30); the recorded 300 match digests, the differentials and the MIMIC demo's 500-query, 50,000-result differential (0 mismatches) pass with pruning on; the tests in part 2 compare on and off byte for byte.
- **Verdict: keep time pruning.** Both criteria are met, either alone would have sufficed, and the result is exact.
- *Where it helps and where it does not.* Over the thirty confirmation queries the summaries prove `none` for 36% of the candidates with a window (912,210 of 2,526,690), at 1.2 to 3.7 µs each. These pruned counts and per-candidate costs come from ad hoc instrumented runs on the same snapshots; the matcher report records candidates per query but not how many were pruned. Queries with a scope and windows gain most (seed 0: 99.6% pruned; seed 19, the slowest of the thirty: 60%, which takes the confirmation's maximum from 82 to 56 s in arm B). Queries with scope `any` and open relations prune nothing (seeds 2, 7, 12, 24), so the ten-query maximum (seed 7, 55 s in arm B) does not move. The 8-worker recovery rests on the same thirty queries as D24's miss; how much of the 2.15 s change in p95 is pruning and how much run-to-run spread is not separated, but its cost per candidate fell 21% (51.4 to 40.4 µs) and every repeat moved.
- *A memory regression found and fixed before the verdict.* The first version pruned the live candidates and then cut the survivors into chunks. The loader limits its scan to a chunk's index range, and chunks of survivors were sparse: at 1,000,000 patients one load of 200 survivors spanning the whole cohort held 1.5 GiB, and the scaling run's single-process query peak rose from 6.99 to 7.99 GiB. Reading the summaries in slices (a first guess at the cause) did not help (8.28 GiB in a second run). Chunks are now cut from the candidates before pruning, and pruned patients dropped from them, so no chunk spans more indices than without pruning: ten queries at 1,000,000 in one process peak at 1.75 GiB with pruning and 1.73 GiB without, and the regenerated scaling run's query peak is 6.86 GiB. A test checks that every loaded chunk lies inside a chunk cut before pruning. The two discarded scaling runs are not in any report.

**Regenerated reports.**
- `verification/execution-store-mimic-demo-report.json`: every figure unchanged, the input digest included (`8e24100f…`); only the snapshot identifier and the bound artifacts' digests changed. Its differential ran with pruning on.
- `verification/execution-store-scaling-report.json` (9,047 s): the Parquet files of every size, 1,000,000 included, have the sha256 of the D24 run's, and so do the content and input digests. Build, verification and open figures are within about 1% of D24's (build peak 8.08 and 8.14 GiB at 300,000 and 1,000,000). Single-process `match()` p50 halves from 100,000 up (17.3 to 7.5 s at 1,000,000), the maximum stays (245 s), status mixes and candidate counts are unchanged.
- `verification/matcher-comparison-report.json`: as above, with the new arm `B_unpruned` in the main run and the confirmation.

*Limitations:* one machine; synthetic data replicated beyond 10,000 patients, so the pruned share at scale is the 10,000-patient pool's; the gain depends on the query mix (scopes and anchored windows prune, scope `any` with open relations does not); ten queries in the main runs at 100,000 and above; the 8-worker p95 comparison is between two runs on different days; `during`'s grace and `extend_grace` prune nothing without end summaries.

*Follow-ups from review* (not done here, because each touches a file a report binds and none is a defect today):
- Record pruned candidates per query in `tools/benchmark_matcher.py`, so the pruned share and its cost are bound evidence rather than ad hoc measurements. **Done in D26.**
- `_prune` relies on its candidates being sorted (its `BETWEEN` bound); `_candidates` sorts them, but a cheap assertion would protect other callers. **Done in D26** (`PRUNE_CANDIDATES_NOT_SORTED`).
- The worker in `tools/benchmark_matcher.py` does not close its store and pool if a query raises (the scratch directory is still removed). **Done in D26** (try/finally; the scratch view is gone).
- Ranging the Parquet-write sort (the build peak) needs control over DuckDB's writer that `COPY` does not give. Not done in D26.

## D26 Execution store 3.0: typed event columns and an evidence table; end summaries; typed staging

One store-format increment with four parts, bundled so that the heavy reports are regenerated once:
1. the D5 event fields (status, selection state, recorded and available times, claim identifier and digest, source table, record and file digest) leave `events.evidence` for typed columns, and the remaining free-form evidence moves to its own patient-partitioned table, which matching does not read;
2. per-concept end summaries in `concept_postings`, so that a `during` relation's grace can prune;
3. typed staging in place of ndjson;
4. the D25 review's follow-ups in `tools/benchmark_matcher.py` and `_prune`.

**Pre-committed rules** (written and committed before any measurement of this increment, as D25's were).
- *Exactness, for every part.* No matcher result may change at any `detail` level: every per-query result digest in the regenerated matcher comparison must equal arm A's and arm B's counterparts, the 300 recorded match digests (`patterns/fixtures/execution_store_match_digests.json`) must be unchanged, and every figure of the MIMIC demo report other than identifiers, digests and timings must be unchanged, unless a recorded result is shown to have been wrong.
- *End-summary pruning.* Keep it only if all three hold:
  1. it is exact: pruning on against off byte-identical (`json.dumps` without sorting keys) on the synthetic cohort, the shared-variable fuzz cohorts and the differential suites, at 1 and 2 workers; the D25 reviewer's fuzz (`/tmp/rv25/`), adapted to end summaries, finds no difference; and planted defects in the end test fail;
  2. arm B's end-to-end cost per candidate at 300,000 patients, in the regenerated matcher comparison's main run (ten queries, `per_candidate_microseconds.end_to_end`), is at most 3% above D25's 279.6 µs, that is at most 288.0 µs;
  3. the share of candidates with a window that pruning proves `none`, over the 30 confirmation queries, is higher with end summaries than with start summaries alone, both counted in the same run.

  Otherwise the summaries and the code that reads them are removed, and the numbers are recorded here. Pilot first: arm B on a few queries at 300,000 patients, end summaries on and off, before the full regeneration.
- *Typed staging.* Keep it only if, at 1,000,000 patients in the regenerated scaling report, the build time (`build_seconds`, add to publish) or the staged bytes are at least 20% below the D25 report's (4,958 s; 120.1 GB), and the build process's peak resident memory is at most 5% above the D25 report's (8.135 GiB, so at most 8.54 GiB). Otherwise revert to ndjson staging and record the numbers. Pilot first at 100,000 to 300,000 patients, typed against ndjson staging in the same tree. The 1,000,000-patient comparison is against a report of the 2.1 format, so it includes the format change of part 1; the pilot's same-tree comparison separates staging from format, and is reported beside it.
- *Typed columns and the evidence table* are a design change, not an experiment, and stay whatever the measurements show. Their effect on event loading (arm B's loading cost per candidate), digest time (content verification), snapshot size and build time is reported against D25, and a regression is stated as one.
