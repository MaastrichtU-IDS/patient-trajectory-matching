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
| D20 | Execution store 2.0: streaming per-patient ingest, streamed content digests, two verification tiers | Decided |
| D21 | Matcher scaling: cheap wins, then parallel Python, then Rust only if needed | Decided; arms B and C built, then the engine kernel; target met at 30 queries × 3 repeats (14 workers: p95 7.9 s, max 11.8 s, 0.82 GiB); stay in Python, no Rust prototype |
| D22 | The adapter passes shared source variables to the engine; the store still refuses them | Decided |

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
- the engine's end at or after start is weaker than the source's start before end. With shared variables a possible binding can need an empty event (Y starts when X ends, starts equal); the source-network check refutes it, and the adapter reports `unresolved` rather than searching on to `none`. A possible binding the source does not entail is left incomplete. **Follow-up in the engine:** an optional per-event `proper: true`, or a profile option, making the start-to-end order edge in `Times` −1 instead of 0, with the reference and the trajectory page updated. The engine's model would then equal the source's, removing both this `unresolved` case and the incomplete `possible`;
- store support for shared variables.
