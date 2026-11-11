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
| D26 | Execution store 3.0: typed event columns and an evidence table; end summaries for `during`; typed staging; D25 follow-ups, under rules fixed before measuring | Decided; end summaries kept (exact, 280.3 µs per candidate, small gain); typed staging kept (staging −89% at 1,000,000, peak +1.2%, build +18% mostly from the format); evidence table did not cheapen loading |
| D27 | Chartevents by route (b): store 3.1 with a series table; a census on the demo, a sample beyond it | Decided; demo census clean (4,753 of 4,753 claim stores through route (a), every chart row through the raw-row check, 0 differential mismatches); Merkle digest built, not wired |
| D28 | Value conditions in the match request (chartevents stage 2); the D26 and D27 follow-ups; an experiment on the store 3.0 build regression, under rules fixed before measuring | Decided; value conditions shipped (0 mismatches against an independent reference on 26,880 synthetic and 20,000 demo results; four planted defects caught); column-wise digest lines kept (build −12.0% at 1,000,000, identical content); a review's must-fix (conditions differing beyond 28 digits shared a tag) fixed before the final regeneration; evidence-in-events staging reverted (−1.8% at the pilot) |

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

**1. Typed record columns and an evidence table (store 3.0).**
- *Ingest contract: the document says it, the builder does not guess.* An ingest event may carry the D5 record fields as optional typed fields: `status`, `selection_state`, `claim_id`, `source_table` (non-empty strings), `claim_sha256`, `record_sha256`, `file_sha256` (sha256, lowercase hex) and `recorded_at`, `available_at` (times on the patient's clock, microseconds from the origin). A field present must hold a value of its kind (`INVALID_RECORD_FIELD`, `INVALID_TIME`, `TIME_OUT_OF_RANGE`); an unknown value is left out, never null, so each meaning has one form and no normalisation is needed. `evidence` stays required and free-form. The converters move the fields out of their evidence: the synthetic converter gives each event its record's `recorded_at`, `available_at` and `status` (a stop's evidence, nested in its exposure's, keeps its own fields as written); the MIMIC mapping gives the row's status and selection state, the claim's identifier and digest, `source_table`, the source row's and file's sha256 and, where MIMIC has one, the recorded time (`available_at` is left out: the release has none).
  - *Declined:* (a) the builder splitting `evidence` by key name. The store would give reserved meanings to keys of a free-form object, and the converters name them differently (the synthetic `status` and ISO `recorded_at`, MIMIC's `record_status` and `recorded_at_us`), so the store would have to know each converter's spelling and parse values it cannot validate as typed. (b) A required `record` object, or new required fields: every document without them would be refused.
- *Ingest identifier kept at `execution-store-ingest-2.0`*, by D25's reasoning: the fields are optional and additive, a document without them is read and digested exactly as before (the hand-built fixture keeps its input digest, `09c452db…`, recorded at fdd6e97), and code from before D26 refuses a document using them by name (`INVALID_EVENT`), never misreads it. The synthetic and MIMIC input digests change because their converters now produce different documents, not because of a label. *Declined:* `execution-store-ingest-3.0` for every document (every input digest would change for a label, D25's option (b)).
- *Store profile `execution-store-3.0`.* `events` loses `evidence` and gains the nine record columns after `proper` (VARCHAR, BIGINT for the two times, null where absent); a new table `event_evidence` (patient_idx, event_id, evidence as canonical sorted-key JSON) is keyed like `events`, partitioned by bucket and written `events`-style, one row per event; `concept_postings` gains `max_end_hi` (part 2). A reader of 2.x selecting `events.evidence` breaks, hence the major version. The canonical forms are unchanged in rule (sorted canonical rows per table and bucket, D20); `event_evidence`'s key is (patient_idx, event_id). The manifest schema requires `event_evidence` and the 3.0 profile.
- *Matching never reads evidence.* `_load_events`, comparability, exclusions and pruning select named columns of other tables. A test replaces `event_evidence` with a view that fails when read and finds every result of 30 queries byte for byte the same. `Store.evidence(patient_id)` returns a patient's evidence objects as the ingest gave them; tests compare them object for object and as canonical text on the fixture (nested lists, nulls, non-ASCII), the synthetic cohort and the MIMIC fixture.
- *Witness and detail output.* No output of the store shows evidence: `match` results carry bindings, witness times and statuses, never evidence, at every detail level, so they are byte-identical by construction, and the exactness evidence below confirms it. The MIMIC verifier's raw-time check now reads `recorded_at` and `record_sha256` from the record fields instead of the evidence.
- *Reference.* `execution_store_reference.py` builds the record columns, `event_evidence` and `max_end_hi` independently, and the store must equal it row for row and digest for digest on the synthetic cohort, the fixture (now with record fields set on one event), the MIMIC fixture and the non-ASCII case.
- *Digest records.* `patterns/fixtures/execution_store_content_digests_3_0.json` records the input digest and every table's digests of the 3.0 builder with ndjson staging (a81ef03) for the synthetic cohort, the tenfold cohort, the fixture and the MIMIC fixture; the tests rebuild each at several part sizes, and with Parquet staging, and require identity. Against the 2.0 record (fdd6e97), 3.0's tables read as 2.0's (`events` joined to its evidence, `patients` and `concept_postings` as their 2.0 columns) equal it table for table for the fixture, input digest included, and for the synthetic cohorts every table but `events` (whose evidence the converter changed); for the MIMIC fixture every table but `events`.
- *Arm A of the matcher comparison* (the matcher at 7dc3364) reads store 2.0, and no relabelled view of 3.0's files can serve it, since `evidence` moved out of `events`. It now reads its own 2.0 snapshot of each size, built by its own builder and converter from this tree's generator (`benchmark_matcher.py --build-arm-a`, then `--arm-a-snapshots`). The report checks, per size, that the two snapshots hold the same rows in every column arm A's matcher reads (`arm_a_snapshot.same_rows`: `patients`, `events` without evidence and the 2.1 and 3.0 columns, `event_concepts`, `concept_closure`, `concept_postings` without `max_end_hi`, `coverage`, `source_concepts`, `excluded_records`, digested by this tree's canonical form), then compares per-query result digests as before. A test builds arm A's snapshot at 0a18b9a and checks both; another shows the rows check fails, in `events` and the postings alone, when one event moves by a microsecond. Without its own snapshot, arm A is recorded `not_run` on a snapshot of another profile. The relabelled view (`readable_snapshot`, `snapshot_view`) and its guard are removed with the view: no view remains whose semantics the guard could keep.
  - *Declined:* a view converted from the 3.0 files (events joined to their evidence, rewritten as 2.0 Parquet). It would be this tree's code producing arm A's input, which is what arm A is meant to be independent of, and it still needs new files and digests; arm A's own builder is the stronger control and costs one extra build per size.

**2. End summaries and pruning for `during`'s grace.**
- *What.* `concept_postings.max_end_hi` is, per concept and patient, the latest recorded end over the events carrying the concept: an exposure's `end_hi`, a point's `start_hi` (a point ends when it starts), and null when any of them is an ongoing exposure (no bound). `_prune_plan` adds, per relaxation set, the tests a constraint on an end allows (`gaps`), with that set's `extend_grace`; `_impossible` applies them after the step and start-pair tests, never on a null.
- *Why only the latest end.* In `trajectory-graph-1.0` the only constraint on an end is `during`'s grace: start(to) − end(from) ≤ grace, from −∞. `plausible` passes a pair only if lo(start(to)) − hi(end(from)) ≤ grace (the other half has lo = −∞), which needs the "from" event's latest end and the "to" event's earliest start, both summarised. The earliest end appears in no test the language can produce, so `min_end_lo` is not stored; the plan leaves out any test needing a summary that is not stored (a general constraint on ends would only be weakened, never wrong).
- *Why it is exact, as D25's argument.* The engine decides each end over `Times.dom` of its variable, which lies inside the recorded end range (inside the start range for a point; unbounded above for an ongoing exposure), and shared variables and `proper` only narrow it; a linked pair's distance is at most the bounds' difference. So `max_end_hi` bounds every candidate's end from above, and when lo(start(to)) − max_end_hi(from) > grace every pair of the arc fails `plausible`, so the arc empties under both tests, by arc consistency, complete, before any search; the D25 reasoning for sets and statuses carries over unchanged.
- *Tests.* Pruning on against off byte for byte on the 300 synthetic queries, two shared-variable fuzz cohorts (150 queries each, plus an exhaustive differential) and the fixture with shared-variable patients, at both detail levels and at 2 workers for a sample, as in D25; on every query the start summaries alone must prune a subset of what the end summaries add to. A constructed case puts a diary entry 20 days after an exposure's latest end: with a grace of 20 days the engine says `possible` and nothing is pruned, with 19 it says `none` and the end summaries prune it where the start summaries cannot; an ongoing exposure is never pruned on its end. Three planted defects fail: the grace's bound off by one, an exposure's start read as its end, and summaries that read an ongoing exposure as ending when it starts.
- *The D25 reviewer's fuzz, adapted* (`/tmp/rv25/` copied to `/tmp/d26/rv26/`; summaries recomputed from the ingest, including the latest end; time horizons of 40 so bounds coincide; `during` in about 30% of relations with `extend_grace`): 102,831 engine cases in two seeds, 77,947 of them pruned, each pruned case exactly the engine's own `none` (319 pruned only thanks to the end summaries); 4,800 store queries at 1 and 2 workers, pruning on against off byte for byte, with every pruned candidate checked against the engine directly (32,937; that direct check passed events without concept-closure expansion, which makes the engine more likely to say `none`, so it is weaker than it looks, and the on/off byte comparison is the real evidence). Each of the six planted defects (D25's three and the three on ends) produces wrong results there (18 to 427 per 25,670 cases). A first run of the store fuzz reported two "mismatches" whose results were identical: it ran while `execution_store.py` was being edited, and the match context's artifact digest, read from disk at each call, differed between the pruning-on and pruning-off calls; rerun on the committed code it found none.

**3. Typed staging.**
- *What.* `add()` buffers each staged table's rows (events, evidence, event concepts, coverage, excluded records per open part; the patient list and the closure for the build) as typed Python columns, 10,000 rows at a time (`stage_rows`), and writes each batch as one Parquet file through a small in-memory DuckDB connection: `COPY (SELECT unnest(CAST(? AS T[])) AS c, …) TO 'file' (FORMAT parquet)`. The derive reads a part's files with `read_parquet([...])` where it read `read_json`, columns and types as before; a staged table with no rows is an empty typed relation. `staging='ndjson'` keeps the earlier path, for the pilot and the tests.
- *Why Parquet through DuckDB.* (a) pyarrow is not installed and would be a new dependency of the store lock; DuckDB, already pinned, converts Python lists to typed columns and writes Parquet itself. (b) DuckDB-native staging tables in the build database: in a 200,000-row micro-benchmark (synthetic event rows) they took 0.58 s and 14.7 MB against 0.47 s and 7.9 MB for Parquet files (ndjson: 0.48 s to write, 0.20 s to load, 108.6 MB; Parquet loaded in 0.15 s), and they would hold the build database open through `add()` and grow a file DuckDB does not shrink. (c) There is no appender in DuckDB's Python API, and `executemany` is row by row. Parquet files also keep the staging's shape: per part, deleted once derived, in `work_dir`.
- *Identity.* Staging alone changes no table: the 3.0 digests recorded with ndjson staging are reproduced with Parquet staging for the synthetic cohort, the tenfold cohort, the fixture and the MIMIC fixture, at several part sizes; ndjson and Parquet staging, and 1, 7 and 10,000 rows per file, give one snapshot identifier on the fixture with shared variables, record fields and excluded records; main's single-statement derive over the gathered staging gives the same digests from either staging.
- *Memory in `add()`.* The buffers held at most 33 MiB (tracemalloc) over 2,016 streamed synthetic patients, against 0.5 MiB with ndjson; the bound is `stage_rows` times the staged tables of the open parts (at most eight), not the cohort.

**4. The D25 review's follow-ups.**
- *Pruned candidates are bound evidence.* The matcher benchmark wraps `_prune` in each worker: per query it records the candidates with a window, how many pruning proved `none`, the seconds spent pruning and, from the first repeat and outside the timing, how many the start summaries alone prove (the same call with `_PRUNE_ENDS = False`). Each arm entry has a `pruning` block with the totals and shares; `B_unpruned` has none, and C arms count in the parent, where pruning runs. A test checks the totals against the per-query rows and that the start-only count is a subset.
- *The sorted precondition is enforced*: `_prune` refuses candidates that are not strictly increasing (`PRUNE_CANDIDATES_NOT_SORTED`), with a test.
- *The benchmark worker closes its store and pool* in a `finally`, however the queries end. The scratch directory D25 cleaned up belonged to the relabelled view, which is gone.

**Pilots** (ad hoc runs on this tree, not in any committed report; the verdicts are taken on the regenerated reports).
- *Typed staging*, build only, same tree (store 3.0), one at a time:

  | | 100,000, ndjson | 100,000, Parquet | 300,000, ndjson | 300,000, Parquet |
  |---|---|---|---|---|
  | Build, add to publish | 567 s | 583 s (+2.8%) | 1,710 s | 1,737 s (+1.6%) |
  | of which `add()`; the derive | 259 s; 45 s | 278 s; 39 s | 780 s; 129 s | 826 s; 108 s |
  | Staged bytes | 12.85 GB | 1.31 GB (−89.8%) | 38.69 GB | 3.93 GB (−89.8%) |
  | Build peak | 4.31 GiB | 4.61 GiB (+7.0%) | 7.99 GiB | 8.01 GiB (+0.3%) |

  Staged bytes fell by 90% at both sizes; build time rose by 2 to 3%; the peak rose by 7% at 100,000, where the buffer pool is not yet full, and by 0.3% at 300,000, where it is (the peak is set by writing and verifying the Parquet files, after staging is gone; the larger build database, 2.66 against 2.41 GB, is the likeliest cause at 100,000, not established). On the rule's terms (bytes or time at least 20% better, peak at most 5% worse, at 1,000,000) the pilot pointed to keeping it, so the regeneration used Parquet staging. The same pilot measured the format: 3.0 with ndjson staging built 13 to 15% slower than the D25 report's 2.1 builds (567 against 500 s, 1,710 against 1,493 s), with a lower or equal peak (4.31 against 5.13 GiB, 7.99 against 8.08) and a 7% smaller snapshot.
- *End summaries*, arm B, ten queries at 300,000 (the Parquet pilot snapshot): 275.3 µs per candidate end to end with end summaries, 276.6 µs with the start summaries alone (`--no-prune-ends`), both under 288.0 µs, with equal digests; the end summaries pruned 330 more of 324,120 candidates with a window (one query, seed 8). Over the thirty confirmation queries at 10,000 patients (the pool the 300,000 cohort replicates), 36.22% pruned against 36.10% by start summaries alone (95 more of 84,223, in seeds 8 and 20). The pilot met all three criteria, the third narrowly, so the summaries stayed in for the regeneration.

**Measurement and verdicts.** The reports were regenerated one at a time, after the pilots: the MIMIC demo (`--workers 8`), the scaling run to 1,000,000 with `--keep` (Parquet staging, 10,580 s), arm A's own 2.0 snapshots of 1,000 to 300,000 (`--build-arm-a`), then the matcher comparison with the committed settings, plus `--confirm --large-queries 30 --large-repeats 3` at 300,000.

| Rule | Measured | Required | Verdict |
|---|---|---|---|
| End summaries: exact | Every per-query digest of B equals A's (own 2.0 snapshot), `B_unpruned`'s and every C arm's, at every size and in the confirmation; 300 recorded digests; differentials; adapted fuzz; six planted defects fail | exact | met |
| End summaries: arm B at 300,000, ten queries, µs per candidate end to end | 280.3 | at most 288.0 (279.6 + 3%) | met (+0.25% against D25) |
| End summaries: pruned share over the 30 confirmation queries | 36.22% (915,060 of 2,526,690) against 36.10% (912,210) by the start summaries alone: +2,850 candidates | higher than start summaries alone | met |
| Typed staging at 1,000,000: staged bytes | 13.09 GB against 120.1 GB, −89.1% | at least 20% less (or build time) | met |
| Typed staging at 1,000,000: build time | 5,854 s against 4,958 s, +18% (mostly the format; staging alone +1.6 to 2.8% in the pilot) | (either criterion) | not met, not needed |
| Typed staging at 1,000,000: build peak | 8.24 GiB against 8.135 GiB, +1.2% | at most +5% (8.54 GiB) | met |

- **Verdict: keep the end summaries**, all three criteria met. The gain is small: the end summaries add 2,850 pruned candidates of 2,526,690 over the thirty queries (0.11 points), in the few queries whose `during` grace a posting can refute, at no measurable cost (+0.25% per candidate over ten queries, within run-to-run spread); the column costs little (concept_postings 164.5 MB at 1,000,000 patients, all its columns included).
- **Verdict: keep typed staging.** Staging is 89% smaller at every size (13.1 against 120.1 GB at 1,000,000) and the peak moved by 1.2%. Build time did not improve: Parquet staging's `add()` is slower than writing JSON lines (converting each batch of Python lists to typed columns), and its faster derive does not make up for it.
- **Typed columns and the evidence table (design change), against D25.**
  - *Event loading*: unchanged, not cheaper. Arm B's loading cost at 300,000 is 163.6 against 162.5 µs per candidate (+0.7%); the loader never selected `evidence`, and Parquet reads only the columns selected, so moving it saved no reads.
  - *Digest time*: worse. Content verification in its own process takes 1,013 against 818 s at 1,000,000 (+24%; +21% at 100,000 and 300,000), reading 47% more rows (538 against 365 million: the evidence table repeats each event's key), at 530,000 rows a second against 446,000; its peak fell to 1.72 GiB from 2.34.
  - *Snapshot size*: 7% smaller at 100,000 and 300,000 (1.25 and 3.58 GB against 1.34 and 3.86), 56% larger at 1,000,000 (5.49 against 3.51 GB). The synthetic converter now stores `recorded_at` and `available_at` as typed microseconds, 2.03 of the 4.11 GB of `events` at 1,000,000, shifted with every replicated copy so they never repeat, where before they were ISO text inside evidence strings that replication made compress well (`events` without them is 2.08 GB, against 3.10 GB for 2.1's `events` with evidence). At 1,000,000 that is an artefact of replication more than of the format; distinct patients would grow linearly from 300,000.
  - *Build time*: worse, 17 to 20% from 100,000 up (5,854 against 4,958 s at 1,000,000), of which the pilot attributes 13 to 15 points to the format (a table more to derive, digest, write and verify; wider event rows) and 1.6 to 2.8 to the staging.
  - *Open*: the `files` check reads 56% more bytes at 1,000,000 (1.85 against 1.18 s warm).
  - *Matching*: unchanged in result and cost (`match()` p50 and maximum, query-process peaks and status mixes within 2% of D25 at every size).

**Regenerated reports.**
- `verification/execution-store-mimic-demo-report.json`: every figure unchanged; the snapshot gains `event_evidence` (10,980 rows); the input digest changed (`ec0612b1…`, since each event carries its record fields and an evidence object without them), and so did the snapshot identifier and the bound artifacts' digests. Its 500-query differential ran with the end summaries on, with no mismatch.
- `verification/execution-store-scaling-report.json`: as in the table above and docs/execution-store.md ("Regenerated for D26"); every size's status mix and candidate counts equal D25's, and 5,000 results equal exhaustive matching at 1,000.
- `verification/matcher-comparison-report.json`: arm A on its own 2.0 snapshots (`same_rows` equal at every size), per-query pruning counts, and the confirmation: all 30 results byte-identical between B, `B_unpruned` and every C arm (yes); arm B 188.3 µs per candidate (D25: 189.9), p50 / p95 / max 9.3 / 49.9 / 55.8 s; C at 8 workers p95 8.53 s (8.50 to 8.58 over the repeats), max 10.4 s (D25: 8.41 s, 10.3 s); at 14 workers p95 7.71 s (7.09 to 7.94), max 9.0 s (D25: 7.81 s, 9.2 s); query process 0.82 GiB at 14. The D21 target is met at 14 workers and met at 8.

*Limitations:* one machine; synthetic data replicated beyond 10,000 patients, which flatters the 1,000,000-patient snapshot before D26 and penalises it after, and makes pruned shares at scale the pool's; the end summaries' gain is small on this query mix, which draws `during` in about one relation in ten and whose patients' exposures are short; ten queries in the main runs at 100,000 and above; the typed-staging pilot is build-only and one run per cell; the peak difference at 100,000 in the pilot (+7%) was not explained.
- *Corroboration.* The start summaries alone prune 912,210 of 2,526,690 candidates over the thirty queries in this run, the figure D25 measured ad hoc on the 2.1 snapshots: the count is now bound evidence and agrees.
- *Clock origins (for the chartevents branch).* D26 adds no clock column: store times stay absolute microseconds from the fixed origin; `events.clock_id` is the patient's clock as before (D5), and no table holds a clock origin or assumes one origin per clock id. The MIMIC mapping keeps `clock_origin` and `clock_origin_us` per event, inside that event's free-form evidence (now `event_evidence`), as provenance; they are not record fields and nothing reads them.

*Follow-ups* (not done here, because each touches a file a report binds):
- `read_digests` in `tools/benchmark_matcher.py` creates a DuckDB spill directory per call (`matcher-digest-*` under the temporary directory) and does not remove it; harmless (they stay empty) but untidy. **Done in D28.**
- The build is 17 to 20% slower in store 3.0, mostly from the evidence table (its derive statement, digest, write and verification). Deriving `event_evidence` from the events part instead of its own staged table, or skipping its in-process verification, are the obvious levers; neither is measured. **Measured in D28:** the evidence-in-events staging saved 1.8% (reverted) and a joint scan about 0.2% a pass (not built); the time was Python writing canonical lines, and writing them a column at a time cut the build at 1,000,000 by 12.0% (kept; the format's own cost is unchanged).
- *From review:* the rows check covers the columns arm A reads, not B's 2.1 additions (`start_var`, `end_var`, `proper`, `shared_variables`). Those are null or false on every synthetic cohort, and the removed view guard used to assert it; record their counts in `arm_a_snapshot`. **Done in D28** (`arm_a_snapshot.columns_2_1`: all 0 at every size).
- *From review:* `_ParquetStage` bounds its Python buffer in rows (stage rows × five tables × up to eight open parts), not bytes. With evidence of several KB per event (MIMIC, chartevents) that buffer sits outside DuckDB's memory limit; cap the evidence stage by bytes or document the sizing.
- *From review:* an independent fuzz (200,000 engine cases, 92,121 pruned, 1,020 only by end summaries; 1,890 store matches in three pruning configurations, both stagings, several part sizes) found no difference, and a planted grace off-by-one was caught.
- The record times of the synthetic converter cost 2 GB of snapshot at 1,000,000 patients; a delta encoding against `start_lo` would recover most of it, at the price of a less direct column.

## D27 Chartevents by route (b): store 3.1 with a series table; a census on the demo, a sample beyond it

D16 decided that chartevents and later bulk sources take route (b). It required five things:
- bulk rows from the importer's pure claim identity;
- a deterministic sample of stores per batch through the full graph route, with a row-equality differential;
- point measurements in the store mapping, and a series table for measurement conditions;
- per-patient sharding, with a Merkle input digest;
- a differential that builds engine inputs once per patient.

The design was drafted on a branch while D26 ran (`docs/decisions/chartevents-route-b.md`, integration notes beside it). An independent review then found three must-fixes, listed under *Review fixes* below. The project lead decided its open questions, and the work was integrated into store 3.0 after D26 merged.

**Decisions taken by the project lead (2026-11-09).**
1. **Input digest.** D20's flat input digest stays the identity. `patterns/merkle_patients.py` (an RFC 6962 tree over patients, two-level by bucket, with inclusion proofs, checked against the Certificate Transparency vectors) stays unwired. It is wired in only when incremental appends exist, so no root is recorded that nobody verifies, and no run writes one. D16's Merkle requirement is deferred to then.
2. **Census on the demo.** Every chart row goes through the raw-row check; every claim store through the round trip (validate, encode, decode, digest); and every claim store once through route (a). The sampler stays the design for full MIMIC-IV.
3. **Sample defaults** for full MIMIC-IV (1%, 2 stores per bucket, strata) are accepted for now and revisited if full MIMIC-IV is run. A per-patient minimum stays declined.
4. **Unitless and textual chart rows are stage 3.** Until then they stay excluded records.
5. **The series design is accepted**, with the review's fixes.
6. **Stage-2 value conditions** go in the store's match request, compiled into per-query tag concepts. Tags stay out of the snapshot identity, and a value relaxation must widen to a tag that contains the first tag's events.

**What the data showed.** Aggregates over the open demo (D11, D17).
- **The measurement importer's reader stops at 100,000 rows.** The demo has 668,862 rows (64.7 MB expanded), so route (b) streams the file in two passes and reproduces the importer's manifest exactly.
- **Admission keeps 150,051 rows** under the importer's rule (`admit`): a numeric value equal to its text, an explicit unit, and `warning = 0`.
  - 518,809 rows are unsupported, by reasons that overlap: missing unit 506,291; textual or qualified 468,721; no numeric value 411,388; warning flagged 14,592; warning unknown 1,159.
  - 2 rows are invalid, and there are no duplicates.
  - 292 items have admitted rows, each with one unit.
  - The admitted rows make 4,753 claim stores of at most 32.
- **Route (a) for a point is the measurement graph route, not the SULO adapter.** The bounded profile requires start + 1 ≤ end, so no bounded snapshot holds a point. The route is:
  - `claim_rdf.encode` with measurement fields;
  - an accept-all analysis policy;
  - `measurement_claims.select` from the graph, which decodes it and runs the importer's isolation certificate.

**Design.**
- **Bulk rows from claim identity.** For each admitted row, `mimic_measurement_import.describe` builds the claim exactly as the importer would: the importer's ledger, duplicate and origin rules, and D15's cut into stores of 32. The store row is computed from that claim, the patient clock and the row's provenance alone (`claim_fields`, then `store_row`):
  - a point event whose time is the origin's coordinate plus the claim variable's offset;
  - D26's typed record fields: status, selection state, claim id and digest, source table, the row's and file's sha256, and the recorded time where there is a storetime;
  - free-form evidence;
  - one series row.

  Where the importer can import a file whole, a test shows our ledger, origins, claim stores and claim digests equal the importer's.
- **What the graph route checks, and what it does not.** The route-(a) row passes through the same `store_row` as the bulk row, from the same claim, so the differential exercises independently only:
  - `claim_fields` against `record_fields`;
  - the RDF round trip;
  - acceptance by validation, isolation and selection;
  - the selection's own time offsets and decimal rendering.

  The review planted defects in `micros`, the micro-unit value, the recorded time and the item namespace, and a full census found none of them. A test records that blindness.
- **The raw-row check** (`patterns/mimic_chartevents_raw_check.py`) is the independent check of the shared mapping, and it is required over every row. It imports nothing from the converter, the importer, `patient_local` or `measurement_claims`. From each raw CSV row it recomputes:
  - the item class;
  - the time and recorded time, by `datetime` arithmetic;
  - the spelling and unit;
  - the micro-unit value, by string parsing and integer arithmetic;
  - the row's sha256;
  - the record number in the event id;
  - the kind, the source, and the point form.

  It accounts for every row once: as an event, a duplicate, a row with an unknown item, or an excluded record (compared as a multiset). Planted defects in each of the four places are caught. It also recomputes chartevents' coverage from the raw rows and `icustays` (one period per stay holding any chart row of the patient, widened to the stay's own events) and compares it with the ingest's and the built store's coverage rows. Coverage decides `none` against `NO_SOURCE_COVERAGE`; three planted coverage defects (no widening, the last period dropped, the merge dropping chart coverage beside inputevents) passed every other check and are now caught. It does not check an excluded record's `reason`, which is the importer's admission rule.
- **The sample** (full MIMIC-IV) and **the census** (the demo). Every claim store has a rank, sha256(salt, dataset, patient, stay, part). A store is sampled if any of three rules picks it:
  - its rank is below 1% · 2²⁵⁶;
  - it is among the 2 lowest-ranked stores of its execution-store bucket;
  - it is the lowest-ranked store holding a stratum. The strata are item and unit, the value's lexical shape (trailing zeros included), a null micro-unit value, a stay's first or later part, and the clock-origin row.

  Each rule is free of ingest order, shard count and part size. On the demo, every store is selected instead.
- **Point measurements in the store.** The engine and the store already take points. Chartevents covers a stay only where it holds a chart row; this is stricter than D15's rule for inputevents, so its bases have their own names (`inferred:icustay_with_chart_rows`, `…+records`, label `chartevents_coverage_basis`). The event kind `recorded_measurement` has no ontology class, so a chart event carries its item class only, with the d_items category as its one ancestor (D14).
- **Store 3.1: the `series` table.**
  - *Columns.* patient_idx, event_id, item_class, start_lo, start_hi (copied from the event), value_lexical, value_decimal (`format(Decimal, 'f')`, exact and scale-preserving, not a value key), value_micro (the value × 10⁶ by integer arithmetic on the decimal's digits and exponent; null when inexact or outside BIGINT, never a refusal), unit (literal).
  - *Key and layout.* Keyed (patient_idx, item_class, start_lo, start_hi, event_id) and partitioned by bucket.
  - *Validation.* The ingest validates each row by name (`SERIES_UNKNOWN_EVENT`, `SERIES_EVENT_NOT_POINT`, `DUPLICATE_SERIES_EVENT`, `SERIES_ITEM_CLASS_MISMATCH`, `INVALID_SERIES_VALUE`, `INVALID_SERIES_UNIT`, `INVALID_SERIES`), using the rules in `patterns/series_values.py`, which the converter shares. A test keeps `decimal_of` equal to `measurement_claims.decimal_value`.
  - *Derive check.* The derive refuses a row not of a point event of its part at that time with that direct concept (`SERIES_ROWS_NOT_OF_THEIR_EVENTS`). That guards the event link only: a corrupted `value_micro` or `unit` between ingest and table is caught by the store-against-reference equality (tests) and, on the demo, by the graph rows against the built store, not by this check (its code comment says more than this; see the follow-ups).
  - *Reading.* Matching does not read the table; `Store.series` returns rows for display.
  - *Reference.* The reference builds the table independently.
- **Why 3.1.** The series table is a new table in the snapshot, so every snapshot identifier changes, and a 3.0 reader would miss a table it does not know. It changes no existing column, though: a document without series gives the same rows in every other table, and a 3.0 reader of those tables is not misled. So this is a minor version. The ingest identifier stays `execution-store-ingest-2.0` by D25's reasoning (an optional, additive field; code from before 3.1 refuses it by name, `INVALID_PATIENT`). The manifest schema requires `series` and the 3.1 profile.
- **The evidence stage is also cut by characters.** A D26 review follow-up: `_ParquetStage` bounded its buffer in rows only, and evidence is several KB per MIMIC event and held in Python, outside DuckDB's limit. The evidence stage now also flushes once its buffered evidence text reaches 32 Mi characters (`stage_evidence_chars`), per open part; with up to eight parts open (one per bucket) the bound is about 8 × 32 Mi characters. Like `stage_rows`, this cuts files, never tables, and a test builds one snapshot with a cap of one character.
- **The MIMIC merge.** `mimic_store_ingest.convert(..., chartevents=True)` joins each patient's two sources: events (ids disjoint by prefix), coverage and excluded records per source, and series.
  - *Refusal is whole-patient.* A patient inputevents refuses (`INGEST_PATIENT_NOT_BOX_REPRESENTABLE`, `CLOCKS_NOT_REBASABLE`) is left out of the chartevents merge too, and counted (`patients_refused_by_inputevents`, `rows_of_refused_patients`). A review found the first integration re-admitting such a patient as chart-only, which made a grouping both sources share ('Medications', 'Nutrition - Enteral' in the real dictionary) read `none` for a patient that was refused. A test reproduces the review's case. The demo has no refusals, so none of its figures moved.
  - *Clocks.* Store times are coordinates on the patient's naive local calendar from 0001-01-01, not absolute instants. Both importers convert their labels with the same `patient_local.coordinate`, each from the origin it chose, and MIMIC applies one date shift per patient across all its tables, so the two sources' coordinates of one patient lie on one calendar. That is why both can name the clock `patient_<id>` and need no rebasing; each event keeps its importer's origin as provenance in `event_evidence` (D26 has no clock-level origin column). `CLOCKS_NOT_REBASABLE` is a different case: inside the inputevents route, two adapter rows of one patient naming different clock ids or origins on a policy the converter cannot rebase, which the converter cannot place on one calendar by construction, so it still refuses them.
  - *Labels.* `source` becomes `sources`, chartevents' own labels take a `chartevents_` prefix, and the labels both sources set must agree (not the coverage bases, which differ on purpose).
  - *Streaming.* `stream_build` feeds `StoreBuilder` one merged patient at a time, with the union closure known in advance. A test shows it gives the snapshot of the document route.
- **The differential.** It builds engine inputs once per patient (`patterns/prepared_reference.py`). The reference keeps every event of a patient with shared variables (D24), and a test shows filtering such a patient would turn `none` into a wrong `possible`. Filtering by step concept is the same argument the store's loader makes, so `exhaustive_match` (no filter) and an unfiltered route-(a) engine path run on the first 50 of the 500 queries, where the prepared reference must agree with `exhaustive_match` result for result. The coverage rule is one function in the reference. The third path holds every chart event as route (a) gave it, since the census covers every store.
- **Graph rows against the built store.** For every graph-checked store, the route-(a) rows in the store's form are compared with the rows read back from the built snapshot for those event ids: typed columns, evidence, direct concepts and series. Without this, "re-derivable" (the converter re-derives the rows it recorded) would be the only claim.

**Review fixes** (the three must-fixes).
1. `value_micro` came from `Decimal.scaleb(6)` under a 28-digit context, while spellings may have 64 characters, so '1000000000000.0000000000000000001' gave exactly 10¹⁸, labelled exact. It is now integer arithmetic on `as_tuple()`. The tests cover both BIGINT edges, ±308 exponents, '-0' and those inputs, and the store refuses a `value_micro` that differs.
2. The graph check shares its row mapping with route (b). Its documents now say exactly what it exercises, and the independent raw-row check over every row is required.
3. "The rows checked are the rows ingested" was unsupported. The converter's digest shows only that rows are re-derivable; the built-store comparison above shows they are the rows the snapshot holds.

**The demo** (`verification/execution-store-mimic-demo-report.json`, regenerated with `--workers 8 --census-workers 14`). All figures are aggregates.

| Chartevents | Count |
|---|---|
| Source rows (pinned file), admitted as events | 668,862, 150,051 |
| Unsupported, invalid, duplicate rows | 518,809, 2, 0 |
| Excluded records: 518,809 unsupported plus 2 invalid rows; table rows after identical records merge, inputevents' 9,398 included | 518,811; 448,419 |
| Claim stores (at most 32 claims) | 4,753 |
| Item classes (rows name), groupings | 1,318, 29 |
| Coverage periods (widened by own records), chart events outside their stay | 140 (134), 1,200 |
| Series rows (micro-unit value null) | 150,051 (0) |

| Check | Result |
|---|---|
| Raw-row check, every row | 668,862 rows: 150,051 events and 518,811 excluded records, each the raw row's own content; no field mismatch, no excluded-record difference; chartevents coverage recomputed from the raw rows and icustays: 140 periods, equal to the ingest's and the built store's |
| Round trip, every store | 4,753 of 4,753 |
| Route (a), every store | 4,753 stores, 150,051 claims; every row equal field for field; isolation verified on all 4,753 |
| Graph rows against the built store | 4,753 stores, 150,051 events, 0 mismatched |
| Differential, 500 queries × 100 patients | 50,000 results: store against reference 0 mismatches, against the route-(a) engine rows 0, dropped matches 0; 4,365 bindings compared |
| Exhaustive slice, first 50 queries | 5,000 results; `prepared_reference` equals `exhaustive_match` on all of them |

- **Store tables.** events 161,031 (10,980 inputevents, 150,051 chartevents); event_concepts 172,011; concept_postings 8,891; concept_closure 2,995; source_concepts 393; coverage 280; excluded_records 448,419; series 150,051.
- **Identity.** The snapshot identifier is `0eb0323b…` (profile `execution-store-3.1`), and the input digest is `7a0dd1fd…`.
- **Inputevents is unchanged.** Its counts, refusals, admission summary and raw-time check equal D26's report figure for figure.
- **Why the differential's mix is not comparable with D26's.** The query generator now draws from the item classes of both sources, and coverage includes chartevents. The status mix is: exact 3,287; relaxed 44; unresolved 1,038 (1,034 of them for `ADMISSION_EXCLUDED_RECORDS`); none 9,891; not_comparable 33,233 (26,081 `ADMISSION_EXCLUDED_RECORDS`, 7,152 `NO_SOURCE_COVERAGE`); excluded 2,507.
- **Exclusions dominate, as D18 predicts.** Excluded records of the two sources together turned 25,211 `none` results not comparable, displaced 870 first or last targets and made 1,034 relaxed results unresolved; the report does not attribute them per source (D26 had 27,257 `none` results not comparable from inputevents' alone, on another query mix).
- **Timings** (one run, M4 Pro):
  - route (b) 20.2 s;
  - census 522 s on 14 workers (the round trip and route (a) on every store; the tail is the largest patient's stores, run in one worker);
  - store build 17.8 s; graph rows against the store 8.9 s; raw-row check, coverage included, 3.0 s;
  - differential 532 s (D26: 155 s, without chart events);
  - whole run 1,166 s; 6,313 CPU-seconds of the verifier and its workers and a 2.2 GB main-process peak (the report's `resources`).

**Regenerated reports, and exactness.** The reports were regenerated one at a time:
1. the MIMIC demo (above);
2. the scaling run to 1,000,000 with `--keep` (`--work-dir /tmp/store-d27-scaling`, 10,627 s);
3. arm A's own 2.0 snapshots of 1,000 to 300,000 (`--build-arm-a`);
4. the matcher comparison with the committed settings, plus `--confirm --large-queries 30 --large-repeats 3` at 300,000.

| Evidence | Result |
|---|---|
| Synthetic cohorts (no series) | 3.1 builds the same tables as 3.0, with `series` empty. The 3.0 digest records (input digest and every table, synthetic, tenfold and fixture, several part sizes) are reproduced; the MIMIC fixture's record is reproduced when it is converted with the converter digest it was made with. |
| Scaling report | Every size's table row counts, every query's candidate count, the status mix and the content rows verified equal the D26 run's; 5,000 results equal exhaustive matching at 1,000. |
| Matcher comparison | Every per-query digest of every arm at every size, and of every arm in the confirmation, equals the D26 report's. B equals A (on A's own 2.0 snapshots, `same_rows` true at every size), `B_unpruned` and every C arm (50, 50, 10, 10; 30 of 30 in the confirmation). |
| Recorded match digests | The 300 recorded match digests are unchanged. |

| Cost against D26 | D26 | D27 |
|---|---|---|
| Build at 1,000,000 | 5,854 s | 5,898 s (+0.75%) |
| Build peak at 1,000,000 | 8.24 GiB | 8.27 GiB |
| Content verification at 1,000,000 | 1,013 s, 1.72 GiB | 1,013 s, 1.72 GiB |
| Snapshot bytes | | equal to within 0.03% (the empty `series` files) |
| Arm B at 300,000, µs per candidate end to end | 280.3 | 279.1 |
| Arm B in the confirmation, µs per candidate | 188.3 | 188.4 |
| C, 8 workers, confirmation: p95 (range over repeats); max | 8.53 s; 10.4 s | 8.41 s (8.33 to 8.59); 10.3 s |
| C, 14 workers, confirmation: p95 (range over repeats); max | 7.71 s; 9.0 s | 7.05 s (7.01 to 7.90); 8.1 s |
| Query process, C at 14 workers | 0.82 GiB | 0.86 GiB |

- **Store 3.1 costs the synthetic workloads nothing measurable.** The differences above are within run-to-run spread.
- **The D21 target holds at 8 and 14 workers.** The 14-worker p95 (7.05 s pooled; 7.01, 7.05 and 7.90 s by repeat) lies inside D26's own spread over its repeats (7.09, 7.54 and 7.94 s): a fall between two runs on identical results, not a measured gain.

*Limitations:*
- Stage 1 answers no value question.
- Beyond the demo, the graph route is a sample.
- The graph route shares its mapping with route (b), so its agreement says nothing about that mapping, which the raw-row check verifies against the raw rows.
- Admission's rule, not the raw check, decides which rows are events.
- Excluded chart rows outnumber chart events (518,811 to 150,051), so by D18 many `none` results become `not_comparable`; the share on chart items alone was not measured.
- One machine.

*Follow-ups:*
- **Stage 2:** value conditions in the match request. **Done in D28.**
- **Stage 3:**
  - textual and unitless measurement claims;
  - reviewed unit equivalence and item mappings;
  - series aggregates;
  - a SULO point route.
- **The Merkle digest**, when appends exist.
- **In `execution_store.py`** (left for the next store change, since editing it forces the six-hour scaling and matcher regeneration): `validate_series` raises a bare `TypeError` on an unhashable `event_id` instead of a named refusal; `series: []` and an absent `series` give different input digests (normalise it, as D25 normalised defaults); the derive check's comment ("this guards the copy") should say it guards the event link only. **All three done in D28.**
- **`measurement_claims.select` re-reads its artifact files on every call.** That is a few milliseconds per store, left alone.

## D28 Value conditions in the match request; the D26 and D27 follow-ups; the store 3.0 build regression

Three parts, bundled because each edits `patterns/execution_store.py`, which forces the six-hour regeneration of the scaling and matcher reports once:
1. chartevents stage 2: value conditions in the store's match request, compiled into per-query tag concepts, with no engine change (decided in D27, decision 6);
2. the follow-ups listed at the end of D26 and D27;
3. an experiment on the build time store 3.0 lost to the `event_evidence` table (D26: 17 to 20% slower).

**Pre-committed rules** (written and committed before any measurement of this increment, as D25's and D26's were).
- *Exactness for every request without value conditions.* No result may change at any `detail` level. Every per-query result digest in the regenerated matcher comparison must equal the D27 report's, query by query, for every arm and size and in the confirmation (B equal to A on A's own snapshots, to `B_unpruned` and to every C arm); the 300 recorded match digests (`patterns/fixtures/execution_store_match_digests.json`) must be unchanged; the scaling report's table row counts, candidate counts and status mixes must equal D27's; and every figure of the MIMIC demo report other than identifiers, digests, timings and the new value-query differential must equal D27's. A request without `conditions` must produce the same output, key for key, as before this change, apart from the context's artifact digests.
- *Value conditions ship only if all of these hold.*
  1. The store's results equal an independently written reference's, result for result (`json.dumps` without sorting keys), with 0 mismatches: on seeded random value queries over synthetic fixtures with series (values on and next to every threshold, several units per item, micro-unit values that are null, excluded records, shared variables), at 1 and 2 workers and both detail levels; and on the MIMIC demo, as an extension of its differential with value queries.
  2. Each planted defect makes that differential fail: a threshold off by one micro-unit, the unit ignored, and a null `value_micro` read as matching (and, for symmetry, as not matching).
  3. Every value relaxation the differential draws is checked to widen to a tag whose events contain the stricter tag's events.
  4. Time pruning stays exact for tagged steps: pruning on against off byte for byte on the value differential.

  A failure is fixed, never tuned away; the fixtures are not narrowed to pass.
- *The build-regression experiment (part 3).* A lever is kept only if
  1. the snapshot identity and every content digest are byte-identical with and without it, in the same tree (the lever is a builder option during the experiment, so the builder artifacts are equal); and
  2. the build at 1,000,000 patients in the regenerated scaling report (`build_seconds`) is at least 8% faster than D27's 5,898 s, that is at most 5,426 s, with the build process's peak resident memory not more than 5% above D27's 8.27 GiB (`build_phase.peak_rss_bytes` 8,880,553,984 bytes, so at most 9,324,581,683 bytes).

  Otherwise the lever is reverted and the numbers recorded here. Pilot first, at 100,000 and 300,000 patients, with and without the lever in the same tree, one build at a time. The pilot decides whether the 1,000,000-patient measurement is attempted: if the lever saves less than 8% of the build at 300,000, or raises its peak by more than 5%, it is reverted before the regeneration and the pilot's numbers are the recorded result, since the 1,000,000 build is dominated by the same stages.

**1. Value conditions (chartevents stage 2).** D27 decided where they live: in the store's match request, compiled into per-query tag concepts, with no engine change; tags outside the snapshot identity; a value relaxation must widen to a tag whose events contain the first tag's. The design, decision by decision (the store doc's [Value conditions](../execution-store.md#value-conditions-d28) has the full contract):
- *Request syntax.* `match(..., conditions={'profile': 'execution-store-conditions-1.0', 'steps': {step: condition}, 'relaxations': [widen_value ...]})`. A condition is a literal unit and either `eq` or at most one lower bound (`gt`, `ge`) and one upper bound (`lt`, `le`); values are exact decimal strings under the measurement decimal rule. Refusals are named (`INVALID_CONDITIONS`, `UNKNOWN_CONDITION_STEP`, `INVALID_CONDITION`, `INVALID_CONDITION_VALUE`, `EMPTY_CONDITION`, `VALUE_RELAXATION_MUST_WIDEN`, `VALUE_RELAXATION_STEP_NOT_CONDITIONED`, `INVALID_VALUE_RELAXATION`, `DUPLICATE_RELAXATION`, `VALUE_TAG_NAME_TAKEN`).
  - *Declined:* the draft's single `{unit, op, value}` (ranges, such as a normal interval, are common, and a step holds one tag); floats (not exact); value predicates in the engine (`trajectory-graph-1.1`, declined in D27).
- *What a tag holds.* An event carries the tag of (concept C, condition K) when it has a series row whose item class C covers (reflexively, through the closure), whose unit equals K's letter for letter, and whose value satisfies K exactly. A series row's item class is a direct concept of its event, so a tag's events are a subset of C's.
- *Units: literal.* A row in another unit does not satisfy a condition (stage 2 has no conversion). A tag's recorders, for the coverage rule, are the sources holding a series row of C in K's unit anywhere in the snapshot; a unit no source records for C makes the step not comparable (`NO_SOURCE_RECORDS_CONCEPT`), not `none`.
  - *Declined:* reading a row in another unit as unknown, D18-style. Without unit knowledge (stage 3) the store cannot tell `beats/min` from `mmHg`, so every other unit would have to count as possibly satisfying: every condition on a grouping that spans units would be not comparable, and on the demo, where each admitted item has one unit, item-level conditions would gain nothing. The literal reading is exact for what it says, and stage 3's reviewed unit equivalence is where commensurable units belong.
- *Groupings.* A condition on a grouping reads every item class under it, in K's unit; an event of the grouping without a series row (an inputevents exposure under 'Medications') never carries the tag. *Declined:* refusing groupings (it would refuse a well-defined question), and requiring one unit per grouping (the unit is already the condition's).
- *Null `value_micro`: compared exactly, neither refused nor unknown.* The stored `value_decimal` is exact (`format(Decimal, 'f')`, validated at ingest), so a row whose micro-unit integer is null (finer than 10⁻⁶ or beyond BIGINT) is compared by Decimal arithmetic on it, which does not round. Rows with an exact `value_micro` are compared in SQL against integer bounds derived from K by exact rational arithmetic (⌈t·10⁶⌉ for `ge`, ⌊t·10⁶⌋ + 1 for `gt`, ⌊t·10⁶⌋ for `le`, ⌈t·10⁶⌉ − 1 for `lt`), so a threshold finer than a micro-unit is exact too.
  - *Declined:* refusing such rows (they hold a well-defined value; refusing the patient or the query would discard answerable questions), and treating them as unknown with D18's rule (sound, but it would make a `none` not comparable for a value the store holds exactly).
- *Events without a value* do not carry a tag. The store does not know which events should have one; a source that drops a measurement's value must record the row as an excluded record, which the MIMIC route does (every admitted chart row has a series row: 150,051 of 150,051).
- *`first` and `last`* choose among the tag's events: the first value satisfying the condition, which is the engine's own semantics for the compiled concept, and the natural reading of "the first MAP below 65".
- *Compilation.* Each conditioned step's concept becomes its tag; a `widen_concept` on it becomes a `widen_concept` to the tag of the wider concept under the same condition; a `widen_value` becomes a `widen_concept` to the tag of the step's concept under the wider condition, appended after the query's relaxations with its own identifier and cost. On one step the two are alternatives, never combined (one relaxation per step, as the engine does). The tag's name is `value-condition:` and a digest of the concept and the condition's normal form.
- *Relaxation containment.* `widen_value` is refused unless the wider condition is in the same unit and admits every value the stricter one admits, checked exactly on the bounds (`lt 65` to `le 65` widens; `le 64.9999999` does not). With one concept, the wider tag's events then contain the stricter tag's.
- *Candidates and pruning, exactly.* Tags have no postings. A tag's candidates are the patients holding an event that carries it, read from `series` (`_tag_patients`); its posting summaries are computed from the series rows carrying it, per pruning slice (`_tag_summaries`): earliest start, latest start, and latest end equal to the latest start (series rows are of points only). Both cover exactly the events the engine lists under the tag, so D25's argument for the candidate filter and D25 and D26's for pruning hold unchanged.
  - *Declined:* tag postings materialised per query in a temporary table (workers have their own connections and could not read it), and base-concept postings as a superset (sound, but it would prune nothing on the condition and load events that cannot bind).
- *Excluded records (D18) and coverage.* Excluded records carry an item class and no value or unit, so a value step reaches them through the concepts its tags stand for, whatever its condition: an excluded row of the item might have satisfied the condition, and D18's rule already treats a record that might bind as if it could. So `none` becomes not comparable, `relaxed` unresolved, `possible` incomplete, a `first` or `last` target that an excluded record could displace not comparable, and an all-`any` match stands. This is sound for conditions for the reason it is sound for concepts: the rule assumes the worst of every reached record. Coverage reads the tag's recorders, never more than its concept's, so it fires at least as often. The structural-concept refusal and the concept checks read the tags' concepts.
- *Output and identity.* A request without `conditions` gives the same output, key for key. With them, the output adds `value_conditions` (the rule `series_unit_exact_value_v1`, each tag's concept and condition), and the context adds `conditions_digest` and `value_rule`. Tags live in no table; the store profile stays `execution-store-3.1`, and the match profile `execution-store-match-2.0` (an additive field, read only by callers who sent conditions).
- *Engine and profile.* Unchanged; `trajectory-graph-profile.md` says that the store supplies value conditions as derived concepts.

**2. The D26 and D27 follow-ups.**
- *`read_digests`* (`tools/benchmark_matcher.py`) removes its DuckDB spill directory however it ends; a test checks that no directory is left after a call, a `columns_2_1` call and a failing call.
- *Arm A's rows check* now records, per size, the rows of B's snapshot in which each 2.1 column is set (`arm_a_snapshot.columns_2_1`: `start_var` and `end_var` not null, `proper` and `shared_variables` true), counted in its own Parquet files, and `all_zero`. Arm A neither reads nor writes these columns, so its results are comparable with B's only where they are all unset; a test shows 0 on a synthetic cohort and the counts on one with a proper exposure sharing a start.
- *`validate_series`* refuses an event id that is not a non-empty string (`INVALID_SERIES`) before hashing or looking it up, where an unhashable one raised a bare `TypeError`.
- *`series: []`* is normalised to an absent `series` at ingest, in the store (`canonical_patient`) and, independently, in the reference (`patient_digest`), so the two have one input digest and one snapshot (a test). The converters never write an empty series, so no recorded input digest moves: the synthetic, tenfold and fixture records and the MIMIC fixture's are reproduced, and the regenerated demo report's input digest is D27's (`7a0dd1fd…`).
- *The derive check's comment* now says it guards the event link only (event, time and item class), and that a value or unit changed between ingest and table is caught by the reference equality and the demo's graph rows against the store.

**3. The store 3.0 build regression (experiment).** D26 named two levers: derive `event_evidence` from the events staging, and verify it in the same pass. A profile came first. At 30,000 patients, digesting `events` took 11.4 s, of which only 3.8 s was DuckDB's ordered scan and fetch; the rest was Python writing each row's canonical line (`json.dumps` per row). For `event_evidence` the split was 2.2 s of 10.6 s, and for `event_concepts` 1.8 s of 7.0 s. The build digests every table twice: once in the build database, and once from the written files to verify them. So the evidence table's cost is mostly Python formatting, which neither named lever touches. Three levers were tried, each a switch in the same tree, so the builder artifacts and therefore the identity could be compared.
- **L1, evidence in the events staging** (`evidence_stage='events'`, named by D26). Each event's evidence is staged in its events row, and `event_evidence` is derived from the part's events. This saves a staged table, its join to the patient index and a dictionary per event in `add()`.
- **L2, events and evidence verified in one pass** (named by D26). This was measured without being built. On the 300,000-patient pilot snapshot, the DuckDB side of one ordered scan of both tables (joined on their key) took 57.8 s, against 61.2 s for the two separate scans. That saves about 3.4 s per digest pass, about 0.2% of the build per pass. Building it would also need a guard: the two keys must be shown to be a bijection before a joint scan can stand for two digests. It was not built.
- **L3, canonical lines a column at a time** (`rows_text`, found by the profile). `content_digests` writes a batch's lines column by column. Strings go through `json.dumps`'s own escaper (`json.encoder.encode_basestring`), integers through `int.__repr__`, and booleans and nulls as `true`, `false` and `null`, so the text is `json.dumps(ensure_ascii=False)`'s byte for byte. A batch holding any other type falls back to the row-by-row form, which stays the definition. A test compares the two on 300 random batches of every allowed type, including non-ASCII, astral, control characters, quotes, backslashes and integers beyond BIGINT. Content digests, input digests and identifiers cannot move, and the identity tests reproduce every recorded digest.

*Pilots* (build only, same tree, one build at a time, the scaling benchmark's cohort; ad hoc runs, not in a committed report). Every build of a size gave one snapshot identifier (100,000: `f8f1b3bf…`; 300,000: `a0e04619…`).

| | 100,000: build | 300,000: build | 300,000: digests in the build, verification | 300,000: peak |
|---|---|---|---|---|
| Neither | 584.0 s | 1,767.9 s | 311.4 s, 309.5 s | 7.99 GiB |
| L3 (column-wise lines) | 526.0 s (−9.9%) | 1,543.2 s (**−12.7%**) | 198.5 s, 196.3 s | 8.01 GiB (+0.2%) |
| L1 (evidence in events staging) | 576.9 s (−1.2%) | 1,736.4 s (−1.8%) | 309.9 s, 306.4 s | 8.00 GiB |
| L1 and L3 | 504.6 s (−13.6%) | | | |

The 100,000 L3 build ran while the value-condition tests ran on other cores; its `add()` was 16 s slower than its neighbours'.
- **L1: reverted before the regeneration**, by the pre-committed pilot gate: it saves 1.8% at 300,000, against 8%. Its gain is in `add()` and in the evidence derive (45.3 to 40.3 s at 300,000), partly offset by a wider events derive (31.7 to 35.7 s).
- **L2: not built.** The measurement puts it far below the gate.
- **L3: passes the pilot gate** (−12.7% at 300,000, peak +0.2%), so the regeneration used it, and the verdict is taken at 1,000,000 below.

**Review fixes** (an independent review of the first D28 run; the reports were then regenerated again, and the figures below are from that second run).
- **Must-fix: distinct conditions could share one tag.** A tag's name came from the condition's normal form, written by `Decimal.normalize`, which rounds to the context's 28 digits, while a condition's value may have 64 characters.
  - So `ge 1.00000000000000000000000000001` and its widening `ge 1` shared one tag; the last one written won.
  - On one point valued 1, the store answered `exact`, with no relaxation. The reference answered correctly: `relaxed` by `v`, cost 1. Two steps on one concept, with conditions differing only beyond the 28th digit, collided the same way.
  - `value_conditions` also showed rounded conditions; the reviewer's round-trip fuzz found 6,434 of about 40,000.
  - The normal form is now exact: trailing zeros are stripped from the digits, never rounded.
  - Tests: the reviewer's case; two steps on one concept; and a round-trip property over 4,000 random conditions with values of up to 64 characters. The property checks that the form's values are the condition's, Decimal for Decimal, that equal conditions share a tag, and that unequal ones never do. All three fail with the old normaliser.
  - Why the differentials missed it: their thresholds come from value pools with at most 22 significant digits, so no two differed beyond the 28th digit. The fixtures were not widened to hide this; the property test is the evidence for long values.
- *A chartevents run with `--value-seeds 0` lost D27's `route.source`*: it had moved under the value-differential branch. It is restored, and the regenerated demo report has it.
- *Per-query result digests are now in the matcher report*, for every arm, size and the confirmation; `B_decided` has none, since its listing differs by design. Before, the benchmark computed them but did not write them, so "equal to D27's" could only be inferred. See *Exactness* below.
- *Other review points:*
  - A `widen_value` with a non-string step is refused by name (`INVALID_VALUE_RELAXATION`), not by a `TypeError`.
  - The mockups pick the 14-worker arm by its count, not its position.
  - The roadmap says "identical content digests", not "identical snapshots", since snapshot identifiers change with the builder.

**Measurement and verdicts.** Each regeneration ran one job at a time, after the pilots, twice: the first run, then the run after the review fixes. Each run consisted of:
1. the MIMIC demo (`--workers 8 --census-workers 14`; 1,364 s both times);
2. the scaling run to 1,000,000 with `--keep` (9,103 s, then 9,076 s);
3. arm A's own 2.0 snapshots of 1,000 to 300,000 (`--build-arm-a`), with D27's snapshot identifiers both times;
4. the matcher comparison with the committed settings, plus `--confirm --large-queries 30 --large-repeats 3` at 300,000.

The committed reports are the second run's.

| Rule | Measured | Required | Verdict |
|---|---|---|---|
| Value conditions: store against reference, synthetic | 0 mismatches. 560 requests (26,880 results) on three cohorts, against the prepared reference on every query and `exhaustive_match` on every fourth; also 0 at 2 workers, at `decided` and with pruning off. Long values: the round-trip property above | 0 | met (after the review's fix) |
| Value conditions: store against reference, MIMIC demo | 0 mismatches. 200 requests (20,000 results; 22,000 comparisons with the exhaustive slice), in both runs | 0 | met |
| Value conditions: planted defects | 160 synthetic queries: threshold off by one 20 wrong results, unit ignored 1,409, null `value_micro` as matching 199, as not matching 95. 40 demo queries: off by one 130, unit ignored 174, null defects 0 (the demo holds no null `value_micro`) | each fails the differential | met (on the synthetic fixtures for all four) |
| Value conditions: relaxation containment | 349 synthetic and 176 demo value relaxations, each widening to a tag that contains the stricter tag's events | all | met |
| Value conditions: pruning exact | Pruning on against off byte for byte on 400 synthetic requests, with candidates pruned | byte-identical | met |
| Exactness without conditions: matcher | See *Exactness* below | equal to D27's | met as far as the evidence reaches (below) |
| Exactness without conditions: other | The 300 recorded match digests are unchanged. The scaling report's table row counts, candidate counts and status mixes equal D27's at every size. Every demo figure but the profile label and the snapshot identifier equals D27's, the input digest included | unchanged | met |
| Build lever L3: identity | Same snapshot identifier with and without it in the pilots at 100,000 and 300,000; every recorded content digest reproduced by the tests | byte-identical | met |
| Build lever L3: build at 1,000,000 | 5,191 s against 5,898 s, −12.0% (first run: 5,215 s, −11.6%) | at most 5,426 s (−8%) | met |
| Build lever L3: peak at 1,000,000 | 8,846,835,712 bytes (8.24 GiB) against 8,880,553,984 (8.27 GiB), −0.4% (first run: 8,843,771,904) | at most 9,324,581,683 (+5%) | met |

*Exactness for requests without conditions, as the evidence allows.*
- *Within the run.* Every arm's per-query result digests are equal: A, B, `B_unpruned` and C at 1, 2, 4, 8 and 14 workers, at every size (50, 50, 10, 10), and B, `B_unpruned` and every C arm in the confirmation (30 of 30).
- *Against D27, per size.* Arm A's snapshots have D27's snapshot identifiers and arm A's code is unchanged, so arm A's results are D27's arm A results. D27 recorded B equal to A on them. So B's results equal D27's B, query by query, at every size.
- *Against D27, in the confirmation.* The confirmation's B digests for seeds 0 to 9 equal the main run's digests at 300,000 (10 of 10), which tie them to arm A. For seeds 10 to 29 the evidence is that candidate counts and listed results equal D27's, query by query, and that the result counts are equal.
- *Against D28's first run.* That report kept no digests, so the comparison is by candidate counts and listed results (equal), and arm A ties both runs to the same results. From this report on, runs can be compared digest for digest.

- **Verdict: value conditions ship.** Every criterion is met once the review's must-fix is fixed. The first run's code had the tag-collision defect, which no differential draw reached; its reports were superseded.
- **Verdict: keep L3** (column-wise canonical lines). The build at 1,000,000 is 12.0% faster with the same content. Content verification in its own process is 38% faster (625.8 against 1,013.1 s at 1,000,000), since it is the same function.
  - The format's own cost has not shrunk: the digest code got faster, for every table.
  - The 2.1 builds D26 compared against had the old digest code, so "3.0 is now +4.7% against 2.1" (5,191 against 4,958 s) compares unlike builds. The like-for-like statement is D26's: 3.0's format costs 13 to 15% at the same digest code, and it still does.
- **L1 reverted and L2 not built**, as above.

*Cost against D27, matching* (`verification/matcher-comparison-report.json`, second run):

| | D27 | D28 |
|---|---|---|
| Arm B at 300,000, µs per candidate end to end (loading, engine) | 279.1 | 278.6 (162.8, 82.6) |
| Arm B in the confirmation, µs per candidate | 188.4 | 189.2 |
| C, 8 workers, confirmation: p95 (range over repeats); max | 8.41 s (8.33 to 8.59); 10.3 s | 8.39 s (8.35 to 8.72); 10.3 s |
| C, 14 workers, confirmation: p95 (range over repeats); max; µs per candidate | 7.05 s (7.01 to 7.90); 8.1 s; 34.8 | 7.26 s (7.22 to 7.32); 8.8 s; 35.2 |
| Query process, C at 14 workers | 0.86 GiB | 0.86 GiB |
| Pruned share over the 30 confirmation queries | 36.22% | 36.22% |

- **The first D28 run's 14-worker confirmation was slower.** It had p95 8.95 s (8.06 to 9.26), max 11.1 s and 40.1 µs per candidate, and so was slower than 8 workers in the same run (8.51 s). A rerun of that arm straight afterwards gave 7.11 s, but it went into a scratch copy, was not committed, and does not by itself establish run-to-run spread.
  - The second run, on code that differs in that path only by the review fixes (none of which touches matching without conditions), gives 7.26 s, with 14 workers again faster than 8.
  - What is measured: two committed runs of one tree's matching path gave 8.95 and 7.26 s. The cause of the difference is not established.
- **Matching is otherwise unchanged in cost.** Arm B is within 1% at every size, C at 8 workers within 1%, and the pruned share is the same.

*Limitations:*
- One machine.
- The synthetic data are replicated beyond 10,000 patients.
- The value differentials are seeded, not exhaustive, and their thresholds have at most 22 significant digits. Longer values are covered by the round-trip property test and the review's cases, not by the differential.
- On the demo, every item has one unit and no value has a null `value_micro`. So the unit rule is exercised there only through conditions in other units (36 conditions), and the null rule not at all; the synthetic fixtures carry both.
- The value-query status mix on the demo is dominated by excluded chart rows (15,364 of 20,000 results not comparable), as D18 predicts for a source that admits 22% of its rows.
- Unit conversion, textual values and a D18-style reading of other units are stage 3.
- The L3 pilot at 100,000 shared the machine with a test run.

*Follow-ups:*
- Stage 3 as listed in D27: textual and unitless measurement claims, reviewed unit equivalence and item mappings, series aggregates, a SULO point route. Reviewed unit equivalence would let a condition name a set of commensurable units; until then a condition reads one literal unit.
- A tag's candidates and recorders are computed with a whole-snapshot scan of `series` per tag and request. That is cheap on the demo (the value differential's 200 queries took 196 s, store and reference together) and unmeasured at full MIMIC-IV scale. Per-(concept, unit) value summaries or postings would be the store-format lever if it matters.
- `excluded: []` and an absent `excluded` still give different input digests, as `series: []` did before D28 (review nit; out of scope here).
- Ranging the Parquet-write sort (the build peak), still open since D24.
