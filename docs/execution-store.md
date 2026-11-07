# Execution store, stages 1 and 2

**Profile:** `execution-store-2.0` (ingest `execution-store-ingest-2.0`) · **Code:** [`patterns/execution_store.py`](../patterns/execution_store.py), reference [`patterns/execution_store_reference.py`](../patterns/execution_store_reference.py), synthetic converter [`patterns/synthetic_ingest.py`](../patterns/synthetic_ingest.py) · **Manifest schema:** [`schemas/execution-store-manifest.schema.json`](../schemas/execution-store-manifest.schema.json) · **MIMIC converter:** [`patterns/mimic_store_ingest.py`](../patterns/mimic_store_ingest.py), demo verifier [`patterns/verify_mimic_store_ingest.py`](../patterns/verify_mimic_store_ingest.py) · **Tests:** `python -m unittest patterns.test_execution_store patterns.test_mimic_store_ingest` · **Lock:** [`patterns/requirements-store.lock.txt`](../patterns/requirements-store.lock.txt)

*Stage 1 is implemented on the synthetic paediatric cohort. Stage 2 loads the MIMIC-IV demo through the SULO adapter ([Stage 2](#stage-2-the-mimic-iv-demo-through-the-sulo-adapter)).* The store is the first piece of the server-side design in [engine design](engine-design.md) §4: patient events, concept postings, a terminology closure and coverage, written as immutable Parquet snapshots on embedded DuckDB. A candidate filter that stays sound under relaxation sits in front of the [trajectory-graph matcher](trajectory-graph-profile.md). The decisions are D4 to D7 and D10 in the engine-increment decision log, plus D14 and D15 for stage 2, and D19 and D20 for scaling: 2.0 builds by streaming one patient at a time and digests every table without holding it in Python ([Streaming build](#streaming-build), [Verification](#verification)).

DuckDB is imported lazily inside `execution_store.py` and is pinned in its own lock file, so the rest of the engine never needs it.

## Ingest contract

A store is built from one ingest document, whatever its source:

```json
{"profile": "execution-store-ingest-2.0", "dataset_id": "…",
 "provenance": {"selection": {"mode": "retrospective", "version": "latest", "available_at": "not_applied"}, "…": "free-form"},
 "closure": {"version": "…", "ancestors": {"dravet": ["dee", "epilepsy", "generalized_epilepsy"], "…": []},
             "bases": {"dravet": "…", "…": "…"}},
 "patients": [{"patient_id": "C001", "clock_id": "patient_C001", "box_representable": true,
               "events": [{"event_id": "ehr/C001-R0001", "kind": "condition", "source": "ehr",
                           "concepts": ["febrile_seizure"], "start": [lo, hi], "end": [lo, hi | null],
                           "evidence": {"…": "free-form provenance"}}],
               "coverage": [{"source": "ehr", "lo": 0, "hi": 0, "basis": "record_span"}],
               "excluded": [{"source": "ehr", "concept": "…", "start": [lo, hi], "reason": "…"}]}]}
```

- **Times** are integer microseconds from the fixed origin 0001-01-01T00:00 on the patient-local calendar, as in `indexed_source_windows`. A time never depends on the request that produced it. Each patient has one clock, and coverage is on it.
- **Events.** `concepts` lists the event's direct concepts, never their ancestors. `end` is absent for a point event; `[lo, null]` means the event is still ongoing. Every event also names its `source`, which ties it to coverage.
- **Boxes only.** The store keeps each time as independent bounds, a box. A patient whose source constraints go beyond a box, or whose events share time variables, must be declared `box_representable: false` and is refused. The field is optional and defaults to true.
- **Selection.** `provenance.selection`, when given, says how the source chose which records to include (mode, version, availability rule). It is checked for shape and copied into the manifest.
- **Labels.** `provenance.labels`, when given, is a flat map of strings and booleans saying what the snapshot's concepts and coverage are, such as `clinical_mapping_verified: false` and `concept_basis: source_item_class`. It is copied into the manifest as `labels`; anything else is refused with `INVALID_LABELS`.
- **Closure.** The closure is a map from each concept to all of its strict ancestors. It must be closed (every ancestor is itself a key) and transitive, and must not contain cycles. `closure.bases`, when given, names what kind of concept each one is, for every concept (for example `source_item_class`); otherwise each is `declared` (`INVALID_CONCEPT_BASES`).
- **Excluded records.** `excluded`, optional, lists records the source held that the ingest left out, such as rows an admission step did not keep: a concept from the closure, a start range and a reason. They never become events. They only stop `match` from reporting a `none` they could have changed (see [Comparability](#comparability)).

- **Patient identifiers** may not contain a tab, newline or carriage return, which delimit the input digest's lines (`INVALID_PATIENT_ID`).

`validate_ingest` refuses anything else with a named `IngestError` code. Examples are `DUPLICATE_EVENT_ID`, `DUPLICATE_PATIENT_ID`, `INVERTED_BOUNDS` (any lo > hi), `END_BEFORE_START`, `UNKNOWN_CONCEPT` (an event concept that is not in the closure), `CLOSURE_UNKNOWN_ANCESTOR`, `CLOSURE_NOT_TRANSITIVE`, `CLOSURE_CYCLE`, `INVALID_TIME`, `TIME_OUT_OF_RANGE`, `DUPLICATE_COVERAGE`, `INVALID_SELECTION` and `INGEST_PATIENT_NOT_BOX_REPRESENTABLE`. The streaming builder applies the same checks one patient at a time and finds duplicate patient identifiers in DuckDB at `finish()`. Nothing is written when an ingest is refused.

### Synthetic converter

`synthetic_ingest(cohort)` converts `demos/mockups/synthetic/generate.py` output. It uses the records, never the generator's `truth`, `quality` or `truth_event_id`.

| Cohort data | Ingest |
|---|---|
| Coded record | One event. Its concept is the cohort concept carrying that (system, code). The 32 deliberately local codes no concept carries are skipped and counted |
| Record whose status does not assert performance | Skipped and counted. These are the 39 `test_order` records and one procedure with status `ordered`: an ordered EEG must not match a step asking for an EEG |
| Superseded record | Dropped: the one superseded genetic result (`C001-R0077`) gives way to its amendment (`C001-R0078`) |
| Letter mention (clinic, referral, discharge) | One point event per mention asserted `present` of the `patient`, timed by the letter. Negated, hypothetical, possible and family mentions are skipped and counted |
| Medication order | An exposure starting at the order. A later `stopped` order for the same medication closes the latest open order before it (12 exposures); an order never closed is ongoing (84). One stop has no order to close and is skipped |
| Dispensing, measurement, diary summary, … | Point events |
| Uncoded diary entries and screening results | Skipped and counted |
| Times | The record's `occurred` earliest and latest, as written |
| Event identifier | `source/record_id`, with `#m<i>` for the i-th mention in a letter. An earlier generator reused `C029-R0271` for two records; the generator now keeps record identifiers unique, and the source stays in the key as a safeguard. The store's duplicate check still refuses any repeat |
| Coverage | For each source, the patient's record span: earliest to latest `occurred` over every record that source holds, converted or not, with basis `record_span`. This coverage is inferred, and gaps inside the span are not represented |
| Ancestors | Transitive closure of each concept's `parents` and `classes`. The version is the dataset identifier plus a digest of the hierarchy |

**Selection mode.** Selection is retrospective and takes the latest version. `available_at` is kept in each event's evidence but is not applied. This matters: 5,612 of the 7,093 records become available after they occur, so a source-as-known-at-the-index selection would differ. The mode and that count are recorded in `provenance.selection` and in the manifest.

The skip counts are recorded in `provenance.skipped`, and the converter's own sha256 in `provenance.converter_sha256`. On the committed seed there are 48 patients and 8,365 events.

Record span was chosen over follow-up for coverage. It applies to every source in the same way, including the diary, registry and pharmacy, which follow-up says nothing about. It is also derived only from recorded data. The cost is that the one documented follow-up gap is not carved out of the hospital's span.

## Streaming build

```python
with StoreBuilder(root, {'profile': ..., 'dataset_id': ..., 'closure': ...}, threads=1, memory_limit='4GB') as builder:
    for patient in patients:          # any iterable; one patient in memory at a time
        builder.add(patient)
    snapshot_dir = builder.finish(provenance)
```

- **`add(patient)`** validates one patient against the closure, puts it in canonical order (events by id, each event's concepts, coverage and excluded records sorted), appends its rows to newline-delimited JSON staging files, and appends `(patient_id, sha256 of its canonical JSON)` to a staged patient list. Python holds one patient: over 2,016 streamed synthetic patients the tracemalloc peak inside `add()` is 0.66 MiB, against a largest patient of 0.25 MiB of JSON and a 170 MiB stream, and nothing accumulates between calls (a test asserts both).
- **`finish(provenance)`** opens a file-backed DuckDB database in the build directory with `memory_limit`, a `temp_directory` for spilling and `preserve_insertion_order=false` (order comes only from `ORDER BY`). It loads the staged patients, refuses a duplicate patient identifier anywhere in the stream (`GROUP BY patient_id HAVING count(*) > 1`, `DUPLICATE_PATIENT_ID`), computes the input digest, derives the tables straight from the staging files, digests every table bucket by bucket, writes the Parquet files and hashes each, then verifies the written files by content before renaming the snapshot into place.
- **Refusals.** Any refusal in `add()` or `finish()`, or leaving the `with` block unfinished, discards the build: staging, database and unpublished snapshot, and the root directory if the builder created it. A refused builder takes nothing more (`BUILDER_CLOSED`).
- **`build(ingest, root, threads=1, memory_limit='4GB')`** is a wrapper that streams `ingest['patients']` through the builder, so callers holding a whole document keep working. `synthetic_ingest.iter_patients(cohort, skipped)` streams the synthetic converter, with `synthetic_header` and `synthetic_provenance` around it; the MIMIC converter still hands over a document.
- **`memory_limit`** is DuckDB's (default `4GB`). It is recorded in the manifest as `builder.memory_limit`, outside the identity: 256 MB and 4 GB give the same snapshot.

## Tables

`finish()` writes the tables to `root/<snapshot_id>/`:

| Table | Columns | File |
|---|---|---|
| `patients` | patient_idx (dense, by sorted patient_id), patient_id, bucket | `patients.parquet` |
| `events` | patient_idx, event_id, kind, source, clock_id, start_lo, start_hi, end_lo, end_hi, ongoing, evidence (canonical JSON) | `events/bucket=k/` |
| `event_concepts` | patient_idx, event_id, concept, basis (`direct`) | `event_concepts/bucket=k/` |
| `concept_closure` | concept, ancestor, basis (`self` or `declared`), version, concept_basis, ancestor_basis (from `closure.bases`); reflexive | `concept_closure.parquet` |
| `concept_postings` | concept, patient_idx, n, min_start_lo, max_start_hi, over closure-expanded concepts | `concept_postings.parquet` |
| `coverage` | patient_idx, source, clock_id, lo, hi, basis | `coverage/bucket=k/` |
| `source_concepts` | source, concept, basis (`inferred_from_records`), over closure-expanded concepts | `source_concepts.parquet` |
| `excluded_records` | patient_idx, source, concept, start_lo, start_hi, reason, n (identical records counted once) | `excluded_records/bucket=k/` |

- **Points and exposures.** A point event has a null `end_lo` and `end_hi`. An ongoing exposure has `end_lo` set and a null `end_hi`.
- **Postings.** A posting row is one (concept, patient) pair. Its `n` counts distinct events carrying the concept directly or through an ancestor, so an event coded both `dee` and `dravet` counts once for `epilepsy`. It also keeps the earliest and latest start.
- **Concept bases.** Every closure row carries the kind of its concept and of its ancestor, so a row read alone says whether it relates source item classes, dictionary groupings, ontology classes or declared concepts.
- **Source concepts.** One row for each (source, concept) pair where the source holds some event carrying the concept, directly or through an ancestor. The table is fixed at build time and is part of the snapshot's identity, so a comparability answer never depends on which patients a query happens to scan.
- **Buckets** are the first 32 bits of sha256(patient_id) modulo 8. A patient never moves bucket when other patients are added.
- **Partitions.** Patient-keyed tables are hive-partitioned by bucket, with the bucket also written in the file. Postings, the closure and the source concepts are keyed by concept, so each is one file: splitting a concept's list by bucket would buy nothing at this scale.

## Verification

`open_store(path, verify='files')` validates the manifest (its schema, each table's bucket digests against its table digest, and the identifier against its own digest), exposes each table as a DuckDB view over its files, and verifies at one of two levels:

| Level | Checks | Cost | Used |
|---|---|---|---|
| `'files'` (default on open) | The Parquet files are exactly those the manifest lists, each with its recorded sha256, hashed as a stream before DuckDB reads any of them (`SNAPSHOT_FILE_MISMATCH`) | Reading the bytes once; memory bounded | Every open unless told otherwise |
| `'content'` | Every table's per-bucket content digests, re-derived from the files and streamed (`SNAPSHOT_CONTENT_MISMATCH`, naming table and bucket) | One ordered query per table and bucket, fetched in batches; memory bounded | Always by `finish()` before publishing, and when reusing an existing snapshot; in the tests and CI |
| `False` | Nothing beyond the manifest | | Explicitly |

File digests are integrity, not identity: Parquet bytes vary with the writer, its version and its threads, so they are recorded beside the content digests and are not part of the snapshot identifier. A file rewritten with other valid content and a matching byte digest passes `'files'` and fails `'content'`, and the identifier its content would give (`Store.derived_snapshot_id()`) differs from the manifest's; rows reordered within a file still pass `'content'`. `match` reports the level used as `verified` (`'files'`, `'content'` or `false`), in its output and in its `context`.

The default on open is `'files'` because a content check streams every row: 8.5 s at 10,000 patients, 83 s at 100,000 and 249 s at 300,000, so about a quarter of an hour per open at 1,000,000 (extrapolated), against 1.45 s for the file check at 300,000 (see [Scaling](#scaling)). Content always runs at build time and in CI.

## Snapshot identity

`manifest.json` is validated against the schema and records:
- the profile;
- the dataset;
- the input digest;
- the closure version;
- the time basis;
- the bucket count;
- the canonical form;
- the selection mode;
- the labels, if the ingest gave any;
- the builder: the sha256 of `patterns/execution_store.py` and of the manifest schema, the DuckDB version and the memory limit;
- for each table, its columns, path, partitioning, row count, content digest and per-bucket row counts and digests;
- `files`: the sha256 of every Parquet file, by relative path.

**Canonical content** (`canonical_form`). Each table is digested bucket by bucket: buckets `0` to `7` for a partitioned table, empty ones included, and the one bucket `all` for the others. A bucket digest is the sha256 of its rows in key order, each row a compact UTF-8 JSON array of its values in column order followed by a newline. The table digest is the sha256 of `<bucket>\t<bucket digest>\n` over its buckets in order. DuckDB orders each bucket with `ORDER BY` the table key and then every column as a tie-break, nulls last; Python fetches 10,000 rows at a time (any batch size gives the same digest) and hashes them as they arrive, so no table is ever held in Python. The keys are total:

| Table | Key |
|---|---|
| `patients` | patient_idx |
| `events` | patient_idx, event_id |
| `event_concepts` | patient_idx, event_id, concept |
| `concept_closure` | concept, ancestor |
| `concept_postings` | concept, patient_idx |
| `coverage` | patient_idx, source, lo, hi, basis |
| `source_concepts` | source, concept |
| `excluded_records` | patient_idx, source, concept, start_lo, start_hi, reason |

- **Types.** Table columns may only be `BIGINT`, `INTEGER`, `BOOLEAN` or `VARCHAR` (and null). Floats and decimals, whose text forms are not canonical, are refused by the manifest schema and by a check on the derived tables (`TABLE_TYPES_NOT_ALLOWED`). Evidence stays a sorted-key canonical JSON string.
- **Collation.** String order is binary, that is code point order, which is also UTF-8 byte order and Python's. The builder and the reader refuse a DuckDB connection whose `default_collation` is set (`DUCKDB_COLLATION_NOT_BINARY`).
- **Declined:** `string_agg` (it materialises the table), DuckDB's `hash()` (not stable across versions), XOR or sums of row hashes (weak against repeated or cancelling rows), and building the lines in SQL with `to_json` (its text form is DuckDB's, not the specification's).

The digests the manifest records come from the derived tables in the build database. The written files are then verified by content against them before publishing, so the Parquet round trip is checked too.

**Input digest** (`input_form`). The sha256 of: the canonical JSON of `{profile, dataset_id, closure, provenance}` (sorted keys, each ancestor list sorted), a newline, then `<patient_id>\t<patient digest>\n` for every patient in code-point order of `patient_id`, where a patient digest is the sha256 of the patient's canonical JSON. DuckDB sorts the staged patient list and Python streams it. The same content in any order, patients or events, has the same digest. It covers the provenance, including the selection mode.

**`snapshot_id`.** This is the sha256 of the canonical JSON of the profile, input digest, closure version, builder artifact digests and each table's row count and content digest. The DuckDB version is recorded but is not part of the identity. If a different writer produced different rows, the table digests and therefore the identifier would change. If it produced the same rows, the snapshot is the same.

**Immutability.** Snapshots are written to a temporary directory and renamed into place. Building again into a root that already holds the identifier verifies the existing snapshot by content. If its files still hold the identifier's content, the directory is left untouched and reused; if not, the build is refused.

## Candidate filter and soundness

`candidates(store, query)` takes a `trajectory-graph-1.0` query:
1. For each step, it takes the union of postings over the step's concept and every `widen_concept` concept for that step that appears in some relaxation set the engine would try. These sets come from `trajectory_graph.relaxation_sets`, so they respect the budget and the one-relaxation-per-step limit.
2. It intersects the steps' patient sets, smallest first.
3. It refuses an unknown query or relaxation concept (`UNKNOWN_QUERY_CONCEPT`) rather than matching it as nothing.

There is no time pruning in stage 1. The posting summaries are stored for it.

**Why no matching patient is dropped.** Each status the engine can return requires every step to have at least one candidate event:
- **Exact, possible.** Every step has a candidate event under its own concept.
- **Relaxed.** Some tried relaxation set R admits a binding, so every step has an event listing concept_R(step). That is the step's own concept or the concept of R's single widening on that step.
- **Unresolved.** The unrelaxed search can only stop inside bounded backtracking. That runs only after every step's domain is non-empty under its own concept.

`occurrence: first` or `last` only narrows a non-empty list. An engine event lists a concept exactly when the concept, or one of its descendants, is among the event's direct concepts. That is exactly when the patient has a posting for that concept, because postings are built over the reflexive closure. So the patient lies in every step's union, and therefore in their intersection, whatever the order. `extend_max` and `extend_grace` change no concepts.

A patient outside the candidate set is therefore `none` under exhaustive matching. `excluded` is decided from the window before the filter is consulted.

**Why survivors get the same result.** `match` loads each candidate's events that carry any step's concept or widening through the closure, listing only those concepts. The engine only tests whether a concept is in an event's list, and it orders candidates by time and identifier. So for every relaxation set each step sees the same candidate list, including the same first and last event, and the result is identical to running on the patient's full history.

**Tests.** The differential test runs 300 seeded random queries of 2 to 4 steps (chains, trees, cycles, `during`, anchors, all scopes and occurrences, `widen_concept` and `extend_max`, budgets 0 to 1, search budgets 1 to 300) over all 48 patients with random windows.
- Each query is shaped around one anchor patient's events, so that matches occur.
- Results are paired by patient identifier, which is asserted.
- The store path must give every patient the same status as `trajectory_graph.match` run over every patient's converted, closure-expanded ingest, and the same full result for every patient it did not filter.
- No patient outside the candidate set may be exact, possible, relaxed or unresolved.

On the committed seed the 14,400 patient results are:

| Status | Results |
|---|---|
| `exact` | 511 |
| `possible` | 78 |
| `relaxed` | 139 |
| `unresolved` | 8 |
| `none` | 10,884 |
| `not_comparable` | 2,107 |
| `excluded` | 673 |

The filter keeps 4,173 of the 14,400 patient results for the matcher. Two results moved from possible to exact when the engine began sharing an event's time variables between steps (decision D13).

**Planted defects** show that the tests can fail. Each is monkeypatched in:
- **Dropped closure row.** Removing `carbamazepine → sodium_channel_blocker` makes `concept_closure` and `concept_postings` differ from the reference.
- **Filter ignores widenings.** The filter drops relaxed patients, which the differential test reports.
- **Loader loads only each step's own concept.** Survivors lose the events their widenings would bind. The differential reports wrong results for patients the filter kept.
- **Loader loses each candidate's earliest event.** This changes which event is first, and the differential reports it the same way.

## Comparability

When a patient's result is `none`, whether the engine returned it or the filter did, `match` checks every step against coverage under rule `window_overlap_source_concepts_v1`. A step is covered when some source that records any of the step's concepts has a coverage period meeting the patient's window (lo ≤ window end and hi ≥ window start).
- **Step concepts.** They include the step's widenings.
- **Recording sources.** They come from `source_concepts`.

If some step is not covered, the result is `{"status": "not_comparable", "reason": "NO_SOURCE_COVERAGE", "uncovered_steps": […]}`: these records could not have answered the question. If some step's concepts are recorded by no source at all, the reason is `NO_SOURCE_RECORDS_CONCEPT` instead, with `unrecorded_steps` listing those steps.

**Excluded records** (rule `excluded_records_scope_v1`). Records the ingest left out can change any decision the engine made over the records it kept, so every engine result, and every `none` that survives the coverage check, is checked against `excluded_records`. A step *reaches* an excluded record when the record's concept is one of the step's concepts or widenings, or a descendant of one, and the query's scope admits its start:
- `within`: the start range meets the window;
- `from_start`: it can be at or after the window's start;
- `after`: it can be at or after the window's end;
- `any`: always, since such a step may bind an event anywhere.

The scope is the only constraint every step carries, so reaching over-approximates: it may flag a result the excluded records could not in fact have changed, but never the reverse. The displacement test for `first` and `last` steps is the exception: it uses every excluded record of the step's concepts, without the scope filter. Otherwise, if no step reaches an excluded record, the result stands. The cases apply in this order:

| Case | Result | Why |
|---|---|---|
| A `first` or `last` step has an excluded record of its concepts, admitted by the scope or not, that could displace its bound event: `start_lo` at or before the bound event's `start_hi` for `first`, `start_hi` at or after its `start_lo` for `last` (on any status with a binding, or the binding of an `unresolved` result's relaxed match) | `not_comparable` | The step's target may be the excluded record, so the binding found may not be a binding of the query at all. The engine chooses the first or last event among all events carrying the concept and only then applies the scope, so a record outside the scope can still displace the target and move the answer out of the window. Carrying the binding beside an `unresolved` status would present as a match something that may not be one |
| `none` | `not_comparable` | The excluded records may hold a binding. A search that could not see every record the source held is not a non-match (engine design §4.3) |
| `relaxed` | `unresolved`, with the relaxed match beside it as `relaxed_match` and `complete: false` | `relaxed` asserts that no unrelaxed binding exists, and an excluded record may supply one. This is the engine's own convention when the unrelaxed search is undecided |
| `possible` | `possible`, with `complete: false` and note `EXCLUDED_RECORDS_MAY_MAKE_EXACT` | `possible` asserts that no binding is exact, and an excluded record may make one exact |

Each changed result has reason `ADMISSION_EXCLUDED_RECORDS` (as a note, for `possible`) and lists `excluded_steps`. An `exact` or `possible` binding whose steps all have occurrence `any` stands, since adding records only adds bindings. An `unresolved` result without a binding is already undecided and is left alone. Coverage reasons take precedence.

**Structural concepts.** An excluded record carries a source concept and that concept's closure, never the structural classes its event would have had (labels `structural_concept_basis`, such as an event kind's ontology classes). On a snapshot holding excluded records, a query or widening naming a structural concept is therefore refused (`STRUCTURAL_CONCEPT_EXCLUSION_UNKNOWN`), not answered as if those records were not there.

Coverage never changes an exact, possible, relaxed or unresolved result. A filtered `none` keeps `"reason": "FILTERED"`.

Limits in stage 1:
- **Inferred recorders.** Which sources record a concept is inferred from the snapshot's records, not declared by the source owner (D-HISTORY).
- **Overlap only.** Coverage that only partly meets the window still counts. The §4.3 cases where a source starts after the exposure, or where a relaxation needs a window wider than coverage (`unresolved`), are not yet distinguished.

`exhaustive_match` in the reference applies both rules, written independently from the ingest.

## Match output

`match(store, query, windows)` returns, beside the per-patient results, the counts and the filter plan:
- `verified`: the verification level the store was opened with: `'files'`, `'content'` or `false`.
- `coverage_rule`: `window_overlap_source_concepts_v1`, and `exclusion_rule`: `excluded_records_scope_v1`.
- `labels`: the manifest's labels, so a result read alone says what its concepts are.
- `context`, with:
  - the `snapshot_id`;
  - the sha256 of the canonical query and of the windows;
  - the sha256 of the engine artifacts `patterns/execution_store.py`, `patterns/trajectory_graph.py` and `patterns/temporal_stn.py`;
  - the coverage and exclusion rules;
  - the verification level;
  - the labels.
- `context_id`: the sha256 of that context. It is the key under which a result can be cached or audited, as engine design §4.5 asks.

## Determinism

- Building with one thread and with four threads gives the same `snapshot_id` and table digests.
- Rebuilding from a shuffled ingest (patients and the events within them) gives the same identifier and leaves the existing directory untouched.
- Streaming the converter's iterator gives the same identifier as building its whole document.
- Fetch batch sizes of 1, 7 and 10,000 rows, and DuckDB memory limits of 256 MB and 4 GB, give the same identifier.
- The patient index comes from sorted identifiers, and buckets come from a hash.

The reference rebuilds every table in pure Python from the ingest, with no code shared with the store, and computes the input digest and every table and bucket digest again from the specification. It must equal the store row for row, table digest by table digest and bucket digest by bucket digest, and on the input digest, on the synthetic cohort, on a hand-built fixture and on the MIMIC fixture. The fixture has:
- a diamond in the closure;
- an event with two direct concepts;
- a closed and an ongoing exposure;
- a patient with no events;
- partial and missing coverage;
- a concept no source records;
- non-ASCII evidence;
- an empty partitioned table.

## Timings

These are on one laptop core, CPython 3.12 and DuckDB 2.0.0:

| Step | Time |
|---|---|
| Generating and converting the cohort | 0.3 s |
| Building the snapshot (48 patients, 8,365 events), content check included | 0.4 s |
| Differential test (300 queries, store path and exhaustive path) | about 19 s |
| Whole suite (35 tests) | about 85 s |

## Scaling

`tools/benchmark_execution_store.py` measures how the store and matching scale with cohort size. The report is `verification/execution-store-scaling-report.json`. It binds by digest the benchmark, `execution_store.py`, `execution_store_reference.py`, `synthetic_ingest.py`, `trajectory_graph.py`, `temporal_stn.py`, the manifest schema, `generate.py` and `test_execution_store.py` (the source of the query set). It is evidence for D19 and D20 and for the deferred decision on a Rust engine (D7), from one machine and synthetic data. The 1.0 figures below come from the previous report (D19), on the same machine and cohorts.

```sh
python tools/benchmark_execution_store.py --sizes 1000,10000,100000,300000,1000000 \
    --memory-limit 4GB --budget-seconds 14400 --work-dir /tmp/execution-store-scaling
```

### Method

- **Machine.** Apple M4 Pro, 14 logical CPUs, 64 GB, macOS, CPython 3.12.15, DuckDB 2.0.0. DuckDB had one thread and a 4 GB memory limit for build and open. Matching is single-threaded Python.
- **Processes.** Each size runs in fresh subprocesses. One generates, converts and builds; another opens and queries; a third profiles both verification levels; and, up to 10,000 patients, a fourth runs cProfile on one `match()`. Peak memory is the maximum resident set size of each. The parent kills a size that passes the wall-time budget (4 h) or 40 GB resident, and records why.
- **Streaming.** The build process writes the distinct pool to an ndjson file one generator seed at a time. It then reads the file back a line at a time, replicating as it reads, into `StoreBuilder.add`, so no process holds the cohort. The builder's stage callback records time and resident memory after each stage of `finish()`, and resident memory is sampled every 1,000 patients while adding.
- **Cohorts.** The distinct pool is the generator with one seed per 48 patients (seed 20261103 + i), relabelled per cohort, up to 10,000 patients. Beyond that the pool is replicated: copy k of a patient takes the id `<id>~r<k>` and a whole-day shift that depends only on k. Replication adds no variety. Patients have a mean of 173 events (p95 384, max 519). Generator seeds that cannot be built, or whose output the store refuses, are skipped and recorded (a generator defect, D19).
- **Queries.** Seeded queries from `random_query`, anchored on the first cohort and identical for every size, with index windows from `random_windows` per pool patient, following the copy. Up to 10,000 patients the benchmark runs 50 queries with 3 repeats and takes the median per query. Above that it runs 10 queries once, because a wide `match()` takes minutes there, so the percentiles at 100,000 and 300,000 rest on ten samples. `match()` is timed end to end and includes `candidates()`.
- **Correctness.** At 1,000 patients, 5 queries were compared with exhaustive matching: 5,000 patient results, 0 mismatches. Every build verified its own files by content before publishing. The verify profile then re-derived every digest from the files at every size, and agreed each time.

### Results

| | 1,000 | 10,000 | 100,000 | 300,000 | 1,000,000 |
|---|---|---|---|---|---|
| Events | 172,296 | 1,730,355 | 17,303,550 | 51,910,650 | 173,035,500 staged, not built |
| Build (`StoreBuilder`, add to publish) | 5.5 s | 51 s | 518 s | 1,588 s | failed after 2,693 s of adding (below) |
| of which `add()` | 2.4 s | 23 s | 234 s | 679 s | 2,294 s |
| Peak memory, build process | 0.64 GiB | 3.37 GiB | 7.00 GiB | 9.00 GiB | 11.6 GiB when DuckDB refused |
| 1.0: build / peak | 4.1 s / 1.2 GB | 38.2 s / 11.2 GB | killed at 40.4 GiB after 316 s | not attempted | not attempted |
| Snapshot on disk | 14.3 MB | 142.7 MB | 1.34 GB | 3.85 GB | |
| Staged ndjson (deleted once loaded) | 0.12 GB | 1.19 GB | 12.0 GB | 36.0 GB | 120 GB |
| Open with the `files` check, cold / warm | 0.13 / 0.02 s | 0.26 / 0.06 s | 0.66 / 0.46 s | 1.67 / 1.30 s | |
| 1.0: open with full content verification | 0.83 s | 8.1 s | | | |
| `content` verification of the files | 1.0 s | 8.5 s | 83 s | 249 s | about 14 min (extrapolated) |
| Peak memory, content verification | 0.18 GiB | 0.48 GiB | 2.42 GiB | 7.77 GiB | |
| 1.0: peak memory, verification | 0.38 GiB | 2.93 GiB | about 29 GiB (extrapolated) | | |
| Peak memory, query process | 0.56 GiB | 1.57 GiB | 7.43 GiB | 21.3 GiB | |
| `candidates()` p50 / max | 1.7 / 2.8 ms | 3.9 / 6.1 ms | 20 / 36 ms | 56 / 99 ms | |
| `match()` p50 / max | 0.11 / 1.03 s | 1.25 / 10.7 s | 3.5 / 29.6 s | 10.1 / 94.3 s | |

The whole run took 6,929 s (1 h 55 min), including the 1,000,000 attempt.

### Where memory and time go in the build

Resident memory after each stage of `finish()` at 100,000 and 300,000 patients, in GiB, with the peak so far in brackets where it differs, and each stage's seconds:

| Stage | 100,000 | 300,000 |
|---|---|---|
| All patients added (Python holds one patient at a time) | 0.08 (0.19), 274 s | 0.08 (0.18), 795 s |
| Staged patients loaded, duplicates checked, input digest | 0.16, 0.1 s | 0.21, 0.3 s |
| DuckDB derive | 6.71 (7.00), 44 s | 8.17 (8.94), 180 s |
| Content digests from the derived tables | 5.65, 91 s | 8.63, 268 s |
| Parquet written and each file hashed | 6.52, 25 s | 8.79, 102 s |
| Content verification of the written files | 6.32, 83 s | 8.66 (9.00), 243 s |

- **Python no longer holds the data.** Resident memory stays at 0.08 to 0.19 GiB while patients are added, at every size, because `add()` holds one patient (the tracemalloc test bounds it at 0.66 MiB per call). In 1.0 the same stage held the whole document: 3.4 GiB at 10,000 and 10.5 GiB at 100,000, before `build()` had started.
- **What remains is DuckDB's, and it exceeds `memory_limit`.** By DuckDB's own accounting (`duckdb_memory()`), its buffer manager stayed within the limit: a separate diagnostic at 100,000 saw at most 1.1 GiB accounted after each derive statement. But the process grew to 6.7 GiB during the derive at 100,000 and to 8.9 GiB at 300,000, about twice the 4 GB limit. Memory did not fall when the connection closed: the diagnostic still held 5.2 GiB after `close()`.
- **Most of it comes from loading the events.** Materialising the events table from the staging file accounts for most of the growth. Scanning the same file without materialising it stays at 0.14 GiB.
- **The limit is a lever, not a cap.** Resident memory does follow the limit: a 30,000-patient events load reached 1.29 GiB under 4 GB and 0.45 GiB under 256 MB. The rest is either outside DuckDB's buffer manager or freed memory the macOS allocator keeps. This is not resolved.
- **Content verification** shows the same pattern: 7.8 GiB at 300,000 under the same 4 GB limit. All of it is DuckDB's; Python holds one 10,000-row batch.
- **Time is linear**, at about 5.2 to 5.3 s per 1,000 patients at 100,000 and 300,000. `add()` is 43 to 45% of it: validation, canonical form, staging and the patient digest, all in Python. The two content passes are 32 to 34%, and the derive 8 to 11%.
- **Smaller builds are slower than in 1.0** (51 s against 38 s at 10,000), at the sizes where 1.0 fitted in memory. 2.0 validates and stages each patient in Python and digests twice, first from the derived tables and then from the files.
- **Content verification runs at about 439,000 rows a second** at every size, and the `events` table, with its evidence JSON, is two thirds of the time. The 365 million rows of 1,000,000 patients would take about 14 minutes (extrapolated). That is why the default check on open is `files`, which takes 1.45 s at 300,000 (2.7 GB/s).

### 1,000,000 patients: where it broke

The build streamed all 1,000,000 patients in 45 minutes (173 million events, 120 GB of staging), with Python at 0.18 GiB. It loaded the staged patients and computed the input digest. Then DuckDB refused inside the derive: `Out of Memory Error: failed to pin block of size 256.0 KiB (3.7 GiB/3.7 GiB used)`.

Some operator in the derive could not spill within the 4 GB limit; the report does not say which. The likeliest is the postings step, the derive's largest intermediate: a `DISTINCT` over every event's closure-expanded concepts, joined back to the events. As designed, the refusal left nothing under the store root. The process peak was 11.6 GiB.

This is the next storage wall, and it is DuckDB's, not Python's. The remedies, cheapest first:
- **A higher memory limit for the derive only.** It works on this machine, but it moves the wall rather than removing it.
- **Typed staging.** Staging as Parquet or typed CSV instead of ndjson would shrink the 120 GB of staging and the cost of materialising the events.
- **Derive one bucket at a time.** Every patient-keyed table, the postings and the source concepts can be derived per bucket, so each statement sees an eighth of the events. This also fits bucket-level digests and incremental appends.

### What grows how

- **Build time, snapshot size and rows are linear**: 9 to 10 times for 10 times the patients, and 3 times for 3 times. At this rate 1,000,000 patients would take roughly 90 minutes to build once the derive fits.
- **Build memory is DuckDB's buffer pool plus what DuckDB does not return.** It grows slowly with size (3.4, 7.0 and 9.0 GiB at 10,000, 100,000 and 300,000), not linearly with events as in 1.0 (about 6 KB per event).
- **Opening is fast at every size.** The `files` check reads the Parquet bytes once: 0.46 s warm at 100,000 and 1.3 s at 300,000, against 8.1 s for the 1.0 content check at 10,000.
- **Matching is now the wall.** The query process peaked at 21.3 GiB at 300,000, and a wide `match()` took 94 s. `match()` builds a Python result for every patient and loads every candidate's events into Python. This is D19's step 3: remove the per-patient floor, move the comparability check into SQL, join candidates per bucket, and then choose between partition-parallel Python and a Rust matcher by measurement.

### Limits of this evidence

- One machine, one DuckDB thread and one memory limit (4 GB). Thread counts and other limits were not swept at scale.
- Synthetic data. Beyond 10,000 patients the cohort is replicated, which multiplies candidates exactly.
- Ten queries at 100,000 and 300,000, so their latency percentiles are coarse.
- The resident-memory diagnosis (DuckDB's accounting against the process) comes from separate diagnostic runs at 30,000 and 100,000 patients, not from the benchmark itself. They ran beside the 1,000,000 build, at low priority.
- Resident memory is measured after each stage and as the process peak, so transient peaks inside a stage show only in the peak so far.

## Stage 2: the MIMIC-IV demo through the SULO adapter

`python -m patterns.mimic_store_ingest --input-dir DIR --dataset-id mimic-iv-demo-2.2 --workers 8` builds a snapshot from local `inputevents`, `icustays` and `d_items` files into the git-ignored `verification/execution-store-mimic-run/`. With no arguments it builds the synthetic fixture, [`examples/mimic-store-ingest/`](../examples/mimic-store-ingest/). `chartevents` is not read.

### Route

Every admitted row takes the canonical route (D8, D15):
1. `mimic_inputevents.stage` admits the rows.
2. Each admitted row becomes one pending claim, `mimic_claim_import.describe`, in a patient-local record claim store built as the importer builds one: one clock per patient, whose origin is the patient's earliest admitted start, and the importer's isolation certificate.
3. A caller-declared accept-all analysis policy (`caller_declared_accept_all`) accepts every claim. It is an analysis choice, not clinical review, and the labels say so.
4. `trajectory_graph_sulo.from_claims` selects the accepted claims through the patient-local graph route, and `ingest_rows` projects the snapshot.
5. `mimic_store_ingest.store_patients` maps the rows to this ingest contract, adds the excluded records and coverage, and `build` writes the snapshot.

A record claim store holds at most 32 claims, and a semantic policy declares at most 32 classes. So each ICU stay's claims are split, in source-record order, into stores of at most 32, and each store declares only its own items. D15 lists the other limits, and why the split changes nothing the store reads. On the fixture, a stay under the limit gives exactly the importer's store. After joining, every record id, source key and event id must be unique for its patient (`DUPLICATE_SOURCE_ROW_ACROSS_STORES`).

### Mapping the adapter's rows

| Adapter row (`trajectory-graph-sulo-ingest-1.0`) | Store ingest |
|---|---|
| `patient_id`, `clock_id`, `clock_origin` | `patient_id`, `clock_id`. One clock per patient; a patient whose rows name two clocks would be refused (`CLOCKS_NOT_REBASABLE`) |
| `start_us`, `end_us` (tightened, on the event's own clock) | `start`, `end` as `origin_us + start_us`, where `origin_us` is the clock origin's patient-local coordinate from 0001-01-01T00:00. Every MIMIC event is a closed interval |
| `box_representable`, `non_box_constraints`, `shared_variables` | A patient with any of them is left out and counted under `INGEST_PATIENT_NOT_BOX_REPRESENTABLE`, by the adapter's reason (`SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE` or `SHARED_SOURCE_VARIABLE`). A variable named by rows in two of a patient's stores counts as shared |
| `kind_class` and the concepts with a `semantic_fact` basis | `concepts`: the event kind's class and the source item class |
| The other concepts (`event_kind_closure`) | The closure: the kind class's ontology ancestors. The mapping checks that each event's closure reaches exactly the adapter's concepts, plus the item's grouping |
| (no adapter field) | Each item class's `d_items` category as its one ancestor (D14). `closure.bases` gives every concept's kind: `source_item_class`, `source_dictionary_grouping` or `ontology_closure` |
| Evidence, claim supports, declared bounds, variables, concept bases | `evidence`, with the claim id and digest from the claim support, which must equal `describe()`'s digest |
| The importer's claim provenance | `evidence.recorded_at_us` from `storetime`; `available_at_us: null` and `history_mode: retrospective`, since the release carries no availability times |
| `event_kind` | `kind` (`recorded_input_segment`). `source` is `inputevents:continuous_segments`, the admitted subset; `evidence.source_table` is `inputevents` |
| (rows admission left out) | `excluded`: each row's item class and start, with its admission reasons |
| ICU stays | `coverage`, one period per stay; see below. Every `icustays` subject is a patient, with or without events |

### Labels (D6, D14)

The concepts are source item classes, `mimic-record-item/<itemid>`, never clinical concepts. Their ancestors are `d_items` categories, `mimic-record-item-category/<category>`, labelled `source_dictionary_grouping`; an item with no category gets none. The event kind's class and its ontology ancestors are structural.

Every `concept_closure` row carries the kind of its concept and its ancestor, and every `match` result carries the manifest's labels, so neither read alone looks like clinical concepts.

The manifest's `labels` say:
- `clinical_mapping_verified: false`;
- `concept_basis: source_item_class` and `ancestor_basis: source_dictionary_grouping`;
- `structural_concept_basis: ontology_closure`, the kind of the event kind's class and its ancestors;
- `source: inputevents:continuous_segments`, from `source_table: inputevents` under admission profile `mimic-inputevents-staging-1.0`;
- the admitted statuses (`ChangeDose/Rate|FinishedRunning|Paused|Stopped`) and order categories (`Continuous IV|Continuous Med`), and what admission leaves out;
- `coverage_basis: inferred:icustay`, and `extended_coverage_basis: inferred:icustay+records`;
- `history_mode: retrospective`, the acceptance, and where the excluded records are.

The dataset identifier is suffixed `:source_item_class`. The selection is retrospective, as released, with availability not applied.

### The admitted subset and excluded records

Admission keeps continuous IV and medication segments with four statuses. In the demo it drops 9,424 of 20,404 rows, mostly drug pushes and boluses. If the store held only the admitted rows, a query on an item that a patient's source recorded only in dropped rows would come back `none`, and that `none` would be false against the source. The demo has 257 such (patient, item) pairs, over items the store's events do carry, and 1,254 over all items.

So every dropped row is kept in `excluded_records`, as its item class, its start and its admission reasons:
- An exact duplicate of another row adds nothing.
- A row with an unreadable start covers all time.
- A row whose item the dictionary does not hold has no concept, and is only counted.
- Item classes seen only in dropped rows join the closure, so a query naming one is answered rather than refused. No source records them, so every patient's `none` on them is `not_comparable` (`NO_SOURCE_RECORDS_CONCEPT`).

`match` then qualifies every result an excluded record could have changed, with `ADMISSION_EXCLUDED_RECORDS` (rule `excluded_records_scope_v1`, under [Comparability](#comparability)). A `none` or a displaced `first` or `last` target becomes `not_comparable`; a `relaxed` becomes `unresolved` with its match beside it; a `possible` becomes incomplete. Structural classes, which excluded records cannot carry, are refused as query concepts.

### Coverage, patients without admitted rows, and refusals

- **Coverage** is one period per ICU stay, intime to outtime, basis `inferred:icustay`. Where a stay's own admitted records start before intime or end after outtime, the period is widened to hold them, with basis `inferred:icustay+records`. The demo has 53 such records, by up to 503 minutes (median 4), and they widen 16 of the 140 periods. The alternative, reporting them as incomparable, would call a record the source evidently made a gap in the source.
- **Patients and stays without admitted rows.** The 2 patients and 4 stays with no admitted rows are kept, with their coverage, like every other: the admitted subset covered those stays and recorded nothing in them. What the source did record there is in `excluded_records`, so a query their dropped rows could answer is `not_comparable`, not `none`. One of the two patients has dropped rows; the other has no inputevents rows at all, and stays `none`.
- **Refusals.** MIMIC claims carry no constraints between events and no shared variables, so the demo has no refusals. The fixture tests show refusal on a store with an added source constraint a box cannot hold, on a variable shared across stores, and on a source row repeated across stores.

### The demo

[`verification/execution-store-mimic-demo-report.json`](../verification/execution-store-mimic-demo-report.json) holds aggregates only. It is regenerated from the verified demo files (D11) by `python -m patterns.verify_mimic_store_ingest --input-dir data/local-mimic/icu --workers 8`, which also checks the pinned file digests. The report binds its artifacts by digest for the provenance gate.

| | Count |
|---|---|
| Source rows, admitted | 20,404, 10,980 |
| Claims, claim stores | 10,980, 424 |
| Patients (with events) | 100 (98) |
| ICU stays (with claims), coverage periods (widened by records) | 140 (136), 140 (16) |
| Events | 10,980 |
| Excluded records (table rows, identical ones counted once), patients holding some | 9,424 (9,398), 99 |
| Item classes (only in excluded records), groupings | 159 (83), 6 |
| Refused patients | 0 |
| Table rows: `event_concepts`, `concept_postings`, `concept_closure`, `source_concepts` | 21,960, 1,412, 330, 84 |

The snapshot identifier is `f8dbd84b86e4c0571903ca83b0ec927a6ab693291e5ba174978211897b8e65d1` (profile `execution-store-2.0`). It changes whenever the converter or the store builder changes, or the rows they produce do.

**Raw times.** Every one of the 10,980 events has its start, end and recorded time checked against its raw source row, to the microsecond, with no mismatch.

**Differential.** The verifier runs 500 seeded queries of 2 to 4 steps over source item classes and their groupings. Each query is shaped around one anchor patient's events, with gaps in minutes, first and last occurrences, widenings, `extend_max`, cycles and `during`. Every query runs over all 100 patients three ways:
- the store path;
- `exhaustive_match` over the whole ingest;
- the engine over each patient's adapter rows directly, without the store mapping or either comparability rule.

Of the 50,000 patient results, none mismatch on either comparison, and the candidate filter drops no match. Against the adapter rows, decisions are compared, not only statuses: binding, relaxations, cost and certainty, for the 1,902 results whose binding survives. Where the store's rules changed a result, it must follow from the admitted-only engine result as the table above says.

| Status | Results |
|---|---|
| `exact` | 1,288 |
| `relaxed` | 22 |
| `unresolved` (592 of them `ADMISSION_EXCLUDED_RECORDS`) | 594 |
| `none` | 10,262 |
| `not_comparable`, `ADMISSION_EXCLUDED_RECORDS` | 27,817 |
| `not_comparable`, `NO_SOURCE_COVERAGE` | 7,508 |
| `excluded` | 2,509 |

The excluded-records cases, out of the admitted-only engine's 1,644 exact, 818 relaxed and 45,027 `none` results:

| Case | Results |
|---|---|
| `none` to `not_comparable` | 27,257 |
| `first` or `last` target displaced, to `not_comparable` (356 exact, 204 relaxed) | 560 |
| `relaxed` to `unresolved` with the match beside it | 592 |
| `possible` made incomplete | 0, since every recorded MIMIC time is exact |

The 22 relaxed results that stand use 24 concept widenings; the 592 relaxed matches carried beside `unresolved` use 582 widenings and 31 extended maxima. The filter keeps 16,210 of the 50,000 patient results for the matcher.

Most `none` results become `not_comparable`, and most relaxed ones `unresolved`. That is what the subset costs: 99 of the 100 patients have dropped rows, mostly of common items, and under scope `any` a dropped record at any time could have bound a step.

**Timings**, on one laptop with CPython 3.12 and DuckDB 2.0.0:

| Step | Time |
|---|---|
| Admission and claim stores | 0.7 s |
| 424 stores through isolation, acceptance, the graph route and `ingest_rows`, 8 worker processes | 63 s (about 480 CPU-seconds) |
| Mapping, excluded records and coverage | 0.3 s |
| Building the snapshot, content check included | 1.8 s |
| Opening it with the file check | 0.02 s |
| Differential (500 queries, three paths) | 224 s |

### Chartevents path

This is not built. `chartevents` has 668,862 demo rows. At about 0.2 CPU-seconds per claim, route (a) would take about 37 CPU-hours for the demo alone, and more for full MIMIC-IV. The planned path:
- **Route (b).** Bulk rows come from pure `describe()` identity, the measurement importer's claim description, without the graph route. For each batch, a deterministic sample of stores goes through the full graph route, and a differential requires its store rows to equal the bulk rows exactly. The report must then say that bulk rows are tied to the graph by claim identity and checked on a sample, not each validated through the graph.
- **Measurements are points.** A chart value is a point event with a value and unit, not an interval. The values go into a series table keyed by patient, item and time, beside the events table, so that the matcher's concept postings stay small.
- **Per-patient streaming.** Done for the store in 2.0 (D20): the builder takes one patient at a time and the input digest is over per-patient digests. The MIMIC converter still builds a whole document; it should yield patients to `StoreBuilder` instead.
- **The differential at scale.** The exhaustive and adapter-row engine inputs are built once per patient and reused across queries, not rebuilt for every query.

### Next

- **Store 2.x (after D20).** In order:
  - an evidence table and the D5 typed event columns, so evidence leaves the events table that dominates digests and loads;
  - a bucket-at-a-time derive, which the 1,000,000-patient build needs ([Scaling](#1000000-patients-where-it-broke)), and bucket-level roots so that an append rebuilds and rehashes only the buckets it touches;
  - matching, the next wall per D19 step 3.
- **`chartevents`**, by the path above.
- **Time pruning** on the posting summaries waits until differential tests cover it.
- **Per-bucket joins.** The candidate patient and event lookups are `IN` lists over the candidate set today. At full scale they become joins against a candidate table, one bucket partition at a time.
- **Event columns** (with an evidence table, next after D20). The D5 columns are still inside `evidence`: status, selection state, recorded and available times, claim identifier and digest, source table, record and file digest.
- **A tighter exclusion test.** The exclusion rule reads only the query's scope. Reading anchor relations too would turn fewer `none` results incomparable, but only once differential tests cover it.
- **Shared variables.** Once the adapter passes source variable ids through to the engine (D13), shared variables need not be refused. The store would then need variable columns.

## Deliberately not done

- **D7.** There are no embeddings, Postgres or Rust engine. Information content per snapshot will come from posting counts, which are stored, but stage 1 computes none. An approximate-nearest-neighbour index is added only if recall at full scale needs it. A Rust engine would be a second implementation over the same Parquet, with this Python path as its reference.
- **Not in stage 1:**
  - bitmaps;
  - anchor tables;
  - series;
  - bitemporal selection (`available_at` is recorded, not applied);
  - partition-parallel workers;
  - streaming results.

```sh
python -m pip install -r patterns/requirements.lock.txt -r patterns/requirements-store.lock.txt
python demos/mockups/synthetic/generate.py --output /tmp/cohort.json
python -m patterns.execution_store synthetic /tmp/cohort.json /tmp/store   # prints the snapshot directory
python -m unittest -v patterns.test_execution_store
```
