# Execution store, stages 1 and 2

**Profile:** `execution-store-3.0` (ingest `execution-store-ingest-2.0`) · **Code:** [`patterns/execution_store.py`](../patterns/execution_store.py), reference [`patterns/execution_store_reference.py`](../patterns/execution_store_reference.py), synthetic converter [`patterns/synthetic_ingest.py`](../patterns/synthetic_ingest.py) · **Manifest schema:** [`schemas/execution-store-manifest.schema.json`](../schemas/execution-store-manifest.schema.json) · **MIMIC converter:** [`patterns/mimic_store_ingest.py`](../patterns/mimic_store_ingest.py), demo verifier [`patterns/verify_mimic_store_ingest.py`](../patterns/verify_mimic_store_ingest.py) · **Tests:** `python -m unittest patterns.test_execution_store patterns.test_mimic_store_ingest` · **Lock:** [`patterns/requirements-store.lock.txt`](../patterns/requirements-store.lock.txt)

*Stage 1 is implemented on the synthetic paediatric cohort. Stage 2 loads the MIMIC-IV demo through the SULO adapter ([Stage 2](#stage-2-the-mimic-iv-demo-through-the-sulo-adapter)).* The store is the first piece of the server-side design in [engine design](engine-design.md) §4: patient events, concept postings, a terminology closure and coverage, written as immutable Parquet snapshots on embedded DuckDB. A candidate filter that stays sound under relaxation sits in front of the [trajectory-graph matcher](trajectory-graph-profile.md). The decisions are D4 to D7 and D10 in the engine-increment decision log, plus D14 and D15 for stage 2, and D19, D20 and D23 for scaling: 2.0 builds by streaming one patient at a time, derives one part of a bucket at a time and digests every table without holding it in Python, and builds 1,000,000 patients under a 4 GB limit ([Streaming build](#streaming-build), [Verification](#verification), [Scaling](#scaling)). 2.1 (D24) holds shared time variables and strict intervals: each event keeps the variables it names and whether it is `proper`, and the engine receives them. Since D25 `match` prunes candidates on the posting summaries before loading their events ([Time pruning](#time-pruning-d25-d26)), with no result changed, and the ingest normalises fields that only restate the engine's defaults. 3.0 (D26) keeps an event's record fields (status, selection state, recorded and available times, claim, source table, record and file digests) as typed columns and its free-form evidence in its own table, which matching never reads; the postings keep each concept's latest end, so that a `during` relation's grace prunes too; and the builder stages rows as typed Parquet files.

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
                           "start_var": "optional", "end_var": "optional", "proper": false,
                           "status": "optional", "recorded_at": 0, "claim_sha256": "optional, …",
                           "evidence": {"…": "free-form provenance"}}],
               "coverage": [{"source": "ehr", "lo": 0, "hi": 0, "basis": "record_span"}],
               "excluded": [{"source": "ehr", "concept": "…", "start": [lo, hi], "reason": "…"}]}]}
```

- **Times** are integer microseconds from the fixed origin 0001-01-01T00:00 on the patient-local calendar, as in `indexed_source_windows`. A time never depends on the request that produced it. Each patient has one clock, and coverage is on it.
- **Events.** `concepts` lists the event's direct concepts, never their ancestors. `end` is absent for a point event; `[lo, null]` means the event is still ongoing. Every event also names its `source`, which ties it to coverage.
- **Time variables and strict intervals (2.1, D24).** An event may name its start's and end's time variables (`start_var`, `end_var`, as the [engine profile](trajectory-graph-profile.md) names them; a point has no end variable) and be `proper`, ending strictly after it starts (a point cannot be). Events naming one variable share one time: one time recorded twice, or an event that starts when another ends. The fields are optional, so a document without them is read, and digested, exactly as before, and the ingest profile is unchanged. A patient whose shared variables no time can satisfy is refused with the engine's code (`EMPTY_SHARED_VARIABLE`), so is a proper event that cannot end after it starts (`EMPTY_PROPER_EVENT`, D25), and so is a patient whose linked variables exceed the engine's limit (`SHARED_VARIABLE_COMPONENT_LIMIT`), so that `match` never meets any of them.
- **Record fields (3.0, D26).** An event may carry the D5 record fields: `status`, `selection_state`, `claim_id` and `source_table` (non-empty strings), `claim_sha256`, `record_sha256` and `file_sha256` (sha256 in lowercase hex), and `recorded_at` and `available_at` (times on the patient's clock, as above). Each is optional and, when present, must hold a value of its kind (`INVALID_RECORD_FIELD`, `INVALID_TIME`, `TIME_OUT_OF_RANGE`); an unknown value is left out, never null. They become typed event columns. `evidence`, still required, holds the rest of the record's provenance, free-form. A document without record fields is read and digested as before, so the ingest identifier is unchanged, as in D25; code from before D26 refuses a document using them by name (`INVALID_EVENT`).
- **Normal form (D25).** A field that only restates the engine's default is dropped at ingest: `proper: false`, a `start_var` equal to `<event_id>.s` and an `end_var` equal to `<event_id>.f`. The engine decides an event with them and without them alike, so the two forms have one input digest, one set of rows and one snapshot. A name the engine would not have given the event itself stays, even when it is another event's default (`b.s` on event `a` shares `b`'s start) or the event's own default for its other end. Documents that use none of these fields, the synthetic cohort among them, and the MIMIC conversion, whose events name source variables and are all proper, are unchanged by it, and so are their input digests.
- **Bounds and shared variables only.** The store keeps each time as bounds, with the variables events share and each proper event's start before end. Other source constraints between events, which those cannot express, are not stored: a patient that has any must be declared `box_representable: false` and is refused (`INGEST_PATIENT_NOT_BOX_REPRESENTABLE`). Shared variables do not make a patient non-box. The field is optional and defaults to true.
- **Selection.** `provenance.selection`, when given, says how the source chose which records to include (mode, version, availability rule). It is checked for shape and copied into the manifest.
- **Labels.** `provenance.labels`, when given, is a flat map of strings and booleans saying what the snapshot's concepts and coverage are, such as `clinical_mapping_verified: false` and `concept_basis: source_item_class`. It is copied into the manifest as `labels`; anything else is refused with `INVALID_LABELS`.
- **Closure.** The closure is a map from each concept to all of its strict ancestors. It must be closed (every ancestor is itself a key) and transitive, and must not contain cycles. `closure.bases`, when given, names what kind of concept each one is, for every concept (for example `source_item_class`); otherwise each is `declared` (`INVALID_CONCEPT_BASES`).
- **Excluded records.** `excluded`, optional, lists records the source held that the ingest left out, such as rows an admission step did not keep: a concept from the closure, a start range and a reason. They never become events. They only stop `match` from reporting a `none` they could have changed (see [Comparability](#comparability)).

- **Patient identifiers** may not contain a tab, newline or carriage return, which delimit the input digest's lines (`INVALID_PATIENT_ID`).

`validate_ingest` refuses anything else with a named `IngestError` code. Examples are `DUPLICATE_EVENT_ID`, `DUPLICATE_PATIENT_ID`, `INVERTED_BOUNDS` (any lo > hi), `END_BEFORE_START`, `INVALID_EVENT_VARIABLE`, `INVALID_EVENT_PROPER`, `INVALID_RECORD_FIELD`, `EMPTY_SHARED_VARIABLE`, `EMPTY_PROPER_EVENT`, `UNKNOWN_CONCEPT` (an event concept that is not in the closure), `CLOSURE_UNKNOWN_ANCESTOR`, `CLOSURE_NOT_TRANSITIVE`, `CLOSURE_CYCLE`, `INVALID_TIME`, `TIME_OUT_OF_RANGE`, `DUPLICATE_COVERAGE`, `INVALID_SELECTION` and `INGEST_PATIENT_NOT_BOX_REPRESENTABLE`. The streaming builder applies the same checks one patient at a time and finds duplicate patient identifiers in DuckDB at `finish()`. Nothing is written when an ingest is refused.

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
| Record fields (D26) | `recorded_at` and `available_at` in microseconds on the same clock, and `status` where the record has one. The event's `evidence` holds the rest of the record's provenance; a stop's evidence, inside its exposure's, keeps its own fields as written |
| Event identifier | `source/record_id`, with `#m<i>` for the i-th mention in a letter. An earlier generator reused `C029-R0271` for two records; the generator now keeps record identifiers unique, and the source stays in the key as a safeguard. The store's duplicate check still refuses any repeat |
| Coverage | For each source, the patient's record span: earliest to latest `occurred` over every record that source holds, converted or not, with basis `record_span`. This coverage is inferred, and gaps inside the span are not represented |
| Ancestors | Transitive closure of each concept's `parents` and `classes`. The version is the dataset identifier plus a digest of the hierarchy |

**Selection mode.** Selection is retrospective and takes the latest version. `available_at` is kept as each event's typed `available_at` but is not applied. This matters: 5,612 of the 7,093 records become available after they occur, so a source-as-known-at-the-index selection would differ. The mode and that count are recorded in `provenance.selection` and in the manifest.

The skip counts are recorded in `provenance.skipped`, and the converter's own sha256 in `provenance.converter_sha256`. On the committed seed there are 48 patients and 8,365 events.

Record span was chosen over follow-up for coverage. It applies to every source in the same way, including the diary, registry and pharmacy, which follow-up says nothing about. It is also derived only from recorded data. The cost is that the one documented follow-up gap is not carved out of the hospital's span.

## Streaming build

```python
with StoreBuilder(root, {'profile': ..., 'dataset_id': ..., 'closure': ...}, threads=1, memory_limit='4GB') as builder:
    for patient in patients:          # any iterable; one patient in memory at a time
        builder.add(patient)
    snapshot_dir = builder.finish(provenance)
```

- **`add(patient)`** validates one patient against the closure, puts it in canonical order (events by id, each event's concepts, coverage and excluded records sorted), stages its rows, and appends `(patient_id, sha256 of its canonical JSON)` to a staged patient list. The rows go to the staging files of the patient's current *part*: a run of whole patients of one bucket (the bucket is a function of the identifier alone), closed once it holds `part_rows` staged rows (default 2,000,000 events, event concepts, coverage periods and excluded records; D23). A part always ends between patients. Python holds one patient: over 2,016 streamed synthetic patients the tracemalloc peak inside `add()` is 0.66 MiB, against a largest patient of 0.25 MiB of JSON and a 170 MiB stream, and nothing accumulates between calls (a test asserts both), beyond the staging buffers below.
- **Typed staging (D26).** Each staged table (events, evidence, event concepts, coverage, excluded records, the patient list and the closure) is staged as typed Parquet files: its rows are buffered as typed columns, `stage_rows` (10,000) at a time per table and open part, and each batch is written by a small in-memory DuckDB connection (`COPY (SELECT unnest(?), …) TO … (FORMAT parquet)`). The buffers hold at most 33 MiB over the 2,016-patient stream (eight open parts). `staging='ndjson'` writes one JSON line per row instead, the earlier path. Both give the same tables and digests, for any `stage_rows` (the tests compare them, and the 3.0 digests recorded with ndjson staging at a81ef03 are reproduced with Parquet staging).
- **`finish(provenance)`** opens a file-backed DuckDB database in the build directory with `memory_limit`, a `temp_directory` for spilling and `preserve_insertion_order=false` (order comes only from `ORDER BY`). It loads the staged patients, refuses a duplicate patient identifier anywhere in the stream (`GROUP BY patient_id HAVING count(*) > 1`, `DUPLICATE_PATIENT_ID`), computes the input digest, derives the tables from the staging files one part at a time, bucket by bucket (below), digests every table bucket by bucket, writes the Parquet files and hashes each, then verifies the written files by content before renaming the snapshot into place.
- **Per-part derive (D23).** The patient index (dense, by sorted identifier, with each patient's bucket), `patients` and `concept_closure` are derived once. Then, for each bucket in turn and each of its parts, the part's events and event concepts are loaded into temporary tables, joined to the patient index; the part's postings and its distinct (source, concept) pairs are computed from those two tables; and the part's rows are appended to `events`, `event_concepts`, `coverage`, `excluded_records` and `concept_postings`. `source_concepts` is the distinct union of the parts' pairs. Each statement sees one part, so the derive's working set is set by `part_rows`, not by the cohort. Rows are appended bucket by bucket, so each bucket's rows sit together and `WHERE bucket = k` skips the others. Partitioned tables are written one bucket at a time, in the layout `PARTITION_BY (bucket)` gives (`bucket=k/data_0.parquet`).
- **Identity.** The per-part derive gives the same tables as one statement over all the staging, so the same content digests and input digest, for any part size: every row of the patient-keyed tables and of the postings depends on one patient's staged rows (plus the closure and the patient index), every patient is in exactly one part, and a distinct union of distinct sets is the distinct set. Parts never change the bucket a row is in either: the bucket comes from the patient index, not from the part. The bucket count stays 8. `PerBucketDeriveTests` require the digests recorded at main fdd6e97 (`patterns/fixtures/execution_store_content_digests.json`) and compare with main's single-statement derive, kept frozen in the test (D23).
- **Instrumentation.** `on_stage(name, info)` reports `derived:whole`, then `derived:bucket=0` to `derived:bucket=7` with the bucket's part count, each statement kind's seconds, the largest DuckDB-accounted memory and temporary storage after any statement (`duckdb_memory()`) and the process's peak resident memory, then `derived:source_concepts` and `derived`. The scaling benchmark adds resident memory after each.
- **Refusals.** Any refusal in `add()` or `finish()`, or leaving the `with` block unfinished, discards the build: staging, database and unpublished snapshot, and the root directory if the builder created it. A refused builder takes nothing more (`BUILDER_CLOSED`).
- **`build(ingest, root, threads=1, memory_limit='4GB')`** is a wrapper that streams `ingest['patients']` through the builder, so callers holding a whole document keep working. `synthetic_ingest.iter_patients(cohort, skipped)` streams the synthetic converter, with `synthetic_header` and `synthetic_provenance` around it; the MIMIC converter still hands over a document.
- **`memory_limit`** is DuckDB's (default `4GB`). It is recorded in the manifest as `builder.memory_limit`, outside the identity: 256 MB and 4 GB give the same snapshot.
- **`work_dir`**, optional, holds the staging files, the build database and DuckDB's spill files, for example on another disk (the Parquet staging is about 76 bytes per event, 13.1 GB for 1,000,000 synthetic patients, against 0.69 KB and 120 GB as ndjson before D26; the build database is 26.6 GB). The snapshot itself is always written under the root, so publishing it is one rename.
- **Leftovers.** Each build works in `.building-*` directories under the root (and `work_dir`), removed when the build ends, however it ends. A killed process cannot remove them; `sweep_stale_builds(directory, older_than_seconds=86400)` removes those older than the threshold and returns their paths. Choose a threshold longer than any build, since a live build that stalls past it would be swept too.

## Tables

`finish()` writes the tables to `root/<snapshot_id>/`:

| Table | Columns | File |
|---|---|---|
| `patients` | patient_idx (dense, by sorted patient_id), patient_id, bucket, shared_variables (two of its events name one time variable, default names included) | `patients.parquet` |
| `events` | patient_idx, event_id, kind, source, clock_id, start_lo, start_hi, end_lo, end_hi, ongoing, start_var, end_var (null where the ingest names none), proper, status, selection_state, recorded_at, available_at, claim_id, claim_sha256, source_table, record_sha256, file_sha256 (the record fields, null where the ingest gives none) | `events/bucket=k/` |
| `event_evidence` | patient_idx, event_id, evidence (canonical JSON); one row per event | `event_evidence/bucket=k/` |
| `event_concepts` | patient_idx, event_id, concept, basis (`direct`) | `event_concepts/bucket=k/` |
| `concept_closure` | concept, ancestor, basis (`self` or `declared`), version, concept_basis, ancestor_basis (from `closure.bases`); reflexive | `concept_closure.parquet` |
| `concept_postings` | concept, patient_idx, n, min_start_lo, max_start_hi, max_end_hi (null when an event of the concept is ongoing), over closure-expanded concepts | `concept_postings.parquet` |
| `coverage` | patient_idx, source, clock_id, lo, hi, basis | `coverage/bucket=k/` |
| `source_concepts` | source, concept, basis (`inferred_from_records`), over closure-expanded concepts | `source_concepts.parquet` |
| `excluded_records` | patient_idx, source, concept, start_lo, start_hi, reason, n (identical records counted once) | `excluded_records/bucket=k/` |

- **Points and exposures.** A point event has a null `end_lo` and `end_hi`. An ongoing exposure has `end_lo` set and a null `end_hi`.
- **Variables (2.1).** `start_var` and `end_var` are the names the ingest gave, null otherwise (the engine's default, `<event_id>.s` or `.f`, which is also what a name equal to that default becomes, D25); `proper` is false unless the ingest set it true. The staging leaves the three out of a line that has none, so they cost a synthetic build nothing.
- **Record fields and evidence (3.0, D26).** The record fields are typed columns of `events`, as the ingest gave them. The free-form evidence, which nothing in matching reads, is its own table, keyed like `events` and partitioned the same way, so `events` holds only what matching and audit filters read; `Store.evidence(patient_id)` returns a patient's evidence objects as the ingest gave them.
- **Postings.** A posting row is one (concept, patient) pair. Its `n` counts distinct events carrying the concept directly or through an ancestor, so an event coded both `dee` and `dravet` counts once for `epilepsy`. It also keeps the earliest and latest start and the latest end (D26): a point ends when it starts, and an exposure still ongoing has no latest end, so the summary is null.
- **Concept bases.** Every closure row carries the kind of its concept and of its ancestor, so a row read alone says whether it relates source item classes, dictionary groupings, ontology classes or declared concepts.
- **Source concepts.** One row for each (source, concept) pair where the source holds some event carrying the concept, directly or through an ancestor. The table is fixed at build time and is part of the snapshot's identity, so a comparability answer never depends on which patients a query happens to scan.
- **Buckets** are the first 32 bits of sha256(patient_id) modulo 8. A patient never moves bucket when other patients are added.
- **Partitions.** Patient-keyed tables are hive-partitioned by bucket, with the bucket also written in the file. Postings, the closure and the source concepts are keyed by concept, so each is one file: splitting a concept's list by bucket would buy nothing at this scale.

## Verification

`open_store(path, verify='files')` validates the manifest (its schema, each table's bucket digests against its table digest, and the identifier against its own digest), exposes each table as a DuckDB view over its files, and verifies at one of two levels:

| Level | Checks | Cost | Used |
|---|---|---|---|
| `'files'` (default on open) | The Parquet files are exactly those the manifest lists, each with its recorded sha256, hashed as a stream before DuckDB reads any of them (`SNAPSHOT_FILE_MISMATCH`) | Reading the bytes once; memory bounded | Every open unless told otherwise |
| `'content'` | Every table's per-bucket content digests, re-derived from the files and streamed (`SNAPSHOT_CONTENT_MISMATCH`, naming table and bucket) | Ordered queries per table and bucket, each over at most `DIGEST_SPAN` patient indices, fetched in batches; memory bounded independently of the cohort (D24) | Always by `finish()` before publishing, and when reusing an existing snapshot; in the tests and CI |
| `False` | Nothing beyond the manifest | | Explicitly |

File digests are integrity, not identity: Parquet bytes vary with the writer, its version and its threads, so they are recorded beside the content digests and are not part of the snapshot identifier. So the `files` tier catches corruption, partial copies and edited files, but not a coordinated edit of a Parquet file and its manifest `files` entry. Three checks cover that: content verification; `open_store(path, expected_snapshot_id=...)`, which refuses any other identifier (`SNAPSHOT_ID_NOT_EXPECTED`); and the directory name, which, when it is a 64-hex identifier, must be the manifest's (`SNAPSHOT_DIRECTORY_MISMATCH`). The match context also records `files_digest`, the sha256 of the manifest's `files` map, so a result names the exact files it read. A file rewritten with other valid content and a matching byte digest passes `'files'` and fails `'content'`, and the identifier its content would give (`Store.derived_snapshot_id()`) differs from the manifest's; rows reordered within a file still pass `'content'`. `match` reports the level used as `verified` (`'files'`, `'content'` or `false`), in its output and in its `context`.

**Verification memory (D24).** Content verification runs under a memory limit, never DuckDB's default of most of the machine: the one the store was opened with, or else the build's (`builder.memory_limit` in the manifest), set for the verification and reset after it. `finish()` and the scaling benchmark already passed the build's limit. The resident memory that still grew with the cohort under that limit (2.37 GiB at 100,000 patients, 7.85 GiB at 1,000,000 before D24) came from each bucket's single ordered query: the sort of a whole bucket of `events` (21.6 million rows at 1,000,000 patients), which DuckDB holds or spills under the limit while the allocator keeps about twice the limit resident. A partitioned table's key begins with `patient_idx`, so each bucket is now read as consecutive `patient_idx` ranges of `DIGEST_SPAN` (100,000) indices, the first open below and the last open above, then the rows whose index is null. Each ordered query sorts one range, about 12,500 patients of the bucket, and the ranges concatenated are the bucket in canonical order: the same digests for any span, and every row the files could hold is covered, whatever their content. A bucket whose largest index is below the span is one query, as before. The build's own content digests use the same function.

The default on open is `'files'` because a content check streams every row: 8.3 s at 10,000 patients, 82 s at 100,000, 247 s at 300,000 and 843 s (14 minutes) at 1,000,000, against 1.2 s for the file check at 1,000,000 (see [Scaling](#scaling)). Content always runs at build time and in CI.

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

**Canonical content** (`canonical_form`). Each table is digested bucket by bucket: buckets `0` to `7` for a partitioned table, empty ones included, and the one bucket `all` for the others. A bucket digest is the sha256 of its rows in key order, each row a compact UTF-8 JSON array of its values in column order followed by a newline. The table digest is the sha256 of `<bucket>\t<bucket digest>\n` over its buckets in order. DuckDB orders each bucket with `ORDER BY` the table key and then every column as a tie-break, nulls last; Python fetches 10,000 rows at a time (any batch size gives the same digest) and hashes them as they arrive, so no table is ever held in Python. A large partitioned bucket is ordered one `patient_idx` range at a time and the ranges are hashed in order, which is the same row sequence ([Verification memory](#verification)). The keys are total:

| Table | Key |
|---|---|
| `patients` | patient_idx |
| `events` | patient_idx, event_id |
| `event_evidence` | patient_idx, event_id |
| `event_concepts` | patient_idx, event_id, concept |
| `concept_closure` | concept, ancestor |
| `concept_postings` | concept, patient_idx |
| `coverage` | patient_idx, source, lo, hi, basis |
| `source_concepts` | source, concept |
| `excluded_records` | patient_idx, source, concept, start_lo, start_hi, reason |

- **Types.** Table columns may only be `BIGINT`, `INTEGER`, `BOOLEAN` or `VARCHAR` (and null). Floats and decimals, whose text forms are not canonical, are refused by the manifest schema and by a check on the derived tables (`TABLE_TYPES_NOT_ALLOWED`). Evidence stays a sorted-key canonical JSON string, in `event_evidence`.
- **Collation.** String order is binary, that is code point order, which is also UTF-8 byte order and Python's. The builder and the reader refuse a DuckDB connection whose `default_collation` is set (`DUCKDB_COLLATION_NOT_BINARY`).
- **Declined:** `string_agg` (it materialises the table), DuckDB's `hash()` (not stable across versions), XOR or sums of row hashes (weak against repeated or cancelling rows), and building the lines in SQL with `to_json` (its text form is DuckDB's, not the specification's).

The digests the manifest records come from the derived tables in the build database. The written files are then verified by content against them before publishing, so the Parquet round trip is checked too.

**Input digest** (`input_form`). The sha256 of: the canonical JSON of `{profile, dataset_id, closure, provenance}` (sorted keys, each ancestor list sorted), a newline, then `<patient_id>\t<patient digest>\n` for every patient in code-point order of `patient_id`, where a patient digest is the sha256 of the patient's canonical JSON, in the normal form above (D25). DuckDB sorts the staged patient list and Python streams it. The same content in any order, patients or events, has the same digest. It covers the provenance, including the selection mode.

**`snapshot_id`.** This is the sha256 of the canonical JSON of the profile, input digest, closure version, builder artifact digests and each table's row count and content digest. The DuckDB version is recorded but is not part of the identity. If a different writer produced different rows, the table digests and therefore the identifier would change. If it produced the same rows, the snapshot is the same.

**Immutability.** Snapshots are written to a temporary directory and renamed into place. Building again into a root that already holds the identifier verifies the existing snapshot by content. If its files still hold the identifier's content, the directory is left untouched and reused; if not, the build is refused.

## Candidate filter and soundness

`candidates(store, query)` takes a `trajectory-graph-1.0` query:
1. For each step, it takes the union of postings over the step's concept and every `widen_concept` concept for that step that appears in some relaxation set the engine would try. These sets come from `trajectory_graph.relaxation_sets`, so they respect the budget and the one-relaxation-per-step limit.
2. It intersects the steps' patient sets, smallest first.
3. It refuses an unknown query or relaxation concept (`UNKNOWN_QUERY_CONCEPT`) rather than matching it as nothing.

The filter is by concept only. Time is used after it, by `match` ([Time pruning](#time-pruning-d25-d26)).

**Why no matching patient is dropped.** Each status the engine can return requires every step to have at least one candidate event:
- **Exact, possible.** Every step has a candidate event under its own concept.
- **Relaxed.** Some tried relaxation set R admits a binding, so every step has an event listing concept_R(step). That is the step's own concept or the concept of R's single widening on that step.
- **Unresolved.** The unrelaxed search can only stop inside bounded backtracking. That runs only after every step's domain is non-empty under its own concept.

`occurrence: first` or `last` only narrows a non-empty list. An engine event lists a concept exactly when the concept, or one of its descendants, is among the event's direct concepts. That is exactly when the patient has a posting for that concept, because postings are built over the reflexive closure. So the patient lies in every step's union, and therefore in their intersection, whatever the order. `extend_max` and `extend_grace` change no concepts.

A patient outside the candidate set is therefore `none` under exhaustive matching. `excluded` is decided from the window before the filter is consulted.

**Why survivors get the same result.** `match` loads each candidate's events that carry any step's concept or widening through the closure, listing only those concepts, with their variables and `proper`. The engine only tests whether a concept is in an event's list, and it orders candidates by time and identifier. So for every relaxation set each step sees the same candidate list, including the same first and last event. An event that names no variable another names links only its own two variables, so leaving it out changes no other event's times. The result is identical to running on the patient's full history.
- **Patients whose events share variables** (`patients.shared_variables`) are different. An event no step can bind may still link two that can (an exposure whose start is one step's event and whose end is another's), so each such candidate's events are loaded whole, those carrying no step concept with an empty concept list (D24). The serial and parallel paths both do this; the tests show that a loader filtering such a patient by concept, as 2.0 did, turns a `none` into `possible`.

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

## Time pruning (D25, D26)

After the candidate filter, `match` drops each candidate that the posting summaries prove the engine would answer `none`, before loading its events. Loading a candidate's events had become the larger part of its cost (D21, D23), and a pruned candidate costs a few microseconds instead.

**The test.** For the unrelaxed query and each relaxation set the engine would try (`trajectory_graph.relaxation_sets`, the same sets the filter uses), a set is impossible for a patient when any of these holds:
- some step's concept under that set has no posting for the patient;
- a step's unary constraints (the scope, and `after` relations to `START` or `END`, with that set's `extend_max`) need a start the posting cannot reach: the latest start `max_start_hi` is below a lower bound, or the earliest start `min_start_lo` is above an upper bound, with the patient's window as the anchors;
- an `after` relation between two steps, or the start-to-start half of a `during` (the "from" step's start at or before the "to" step's), needs a gap the two postings cannot reach: `max_start_hi(to) − min_start_lo(from) < min`, or `min_start_lo(to) − max_start_hi(from) > max`;
- (D26) the grace half of a `during`, the "to" step's start at most `grace` after the "from" step's end, cannot be met: `min_start_lo(to) − max_end_hi(from) > grace`, with that set's `extend_grace`. A null `max_end_hi` (an ongoing exposure) is never tested.

A candidate is pruned when every set is impossible. Its result is the engine's own `none`, `{"patient_id": …, "status": "none", "complete": true}`, key for key, and everything after the engine (comparability, excluded records, counts, the listing at each detail level) treats it as the engine's result, because it is.

**Why the engine's answer is exactly that.** A posting row covers exactly the events the engine lists under the concept (an event carries a concept when one of its direct concepts has it in its closure), so every candidate event of the step, including the one `first` or `last` picks, has `start_lo ≥ min_start_lo` and `start_hi ≤ max_start_hi`. The engine decides each start over `Times.dom`, the tightest range the patient's own facts leave it, which is inside the recorded bounds: shared variables and `proper` only narrow it. Unary constraints are on starts, and `plausible`, which `certain` implies, passes an event only if its start range reaches each bound. Between two steps `plausible` needs the difference of the two times to reach the relation's interval within the patient's distances, which never exceed the difference of the bounds (zero for one variable). For the grace half of `during` that is end(from) − start(to) reaching −grace: the engine's latest end of a "from" event, its `Times.dom` upper end, is at most its recorded `end_hi` (its `start_hi` for a point, whose end is its start; unbounded for an ongoing exposure), so at most `max_end_hi`, and its "to" event's earliest start at least `min_start_lo`. So in an impossible set some step's candidate domain, or some arc, empties under both tests: exact and possible are not found, by arc consistency, complete, before any search. With every set impossible no search ran, so no search stopped, and the engine returns `none` with `complete: true` and no note.

**What it leaves alone.**
- *Step kinds.* The query language (`trajectory-graph-1.0`) has positive concept steps only, with `occurrence` any, first or last; there are no negated or absence steps. Absence enters only after the engine, through comparability and excluded records, which run on a pruned candidate's `none` exactly as on the engine's.
- *Ends (D26).* The grace half of `during` is the only constraint on an end in `trajectory-graph-1.0`, and `plausible` needs only its "from" event's latest end and its "to" event's earliest start, both summarised; the earliest end, which no constraint needs, is not stored. In general a test that would need a summary not stored is left out, which only weakens the test.
- *Ongoing events* have an open end, recorded as a null `max_end_hi`, which no test reads; their starts are bounded.
- *Detail levels.* `full` lists every patient and `decided` lists every candidate with a window; both list a pruned candidate with the engine's `none`. `counts`, `plan` and `candidates` are computed as before (pruning comes after the filter), so no field of any output changes.
- *Workers.* The parent prunes before cutting units, so workers receive only the candidates left; results merge by patient index as before.

**Exactness evidence** (`TimePruningTests`). Pruning on against off (`execution_store._PRUNE`), byte for byte (`json.dumps` without sorting keys): the 300 differential queries on the synthetic cohort (one in three also at `detail='decided'`, one in six at 2 workers with chunks of 3, both details); 150 queries on each of two shared-variable fuzz cohorts (the synthetic cohort with proper exposures and one to six shared variables added in about 60% of patients, after a D24 reviewer's script), with the same sampling, plus a 60-query exhaustive differential on each; and 201 queries on the fixture with the shared-variable patients. The 300 recorded match digests, the exhaustive differential, the worker tests and the MIMIC fixture's differential all run with pruning on. A further test computes the engine's result on each pruned candidate's loaded events and finds the same dict. On the synthetic cohort it prunes 1,579 of the 3,974 candidates with a window over the 300 queries, 2 of them by the end summaries alone (73 of the queries have a `during`), and on the fuzz cohorts 1,731 of 4,048, 903 of them patients that share variables and 10 by the end summaries alone. Two planted defects fail: pruning on the unrelaxed query alone, and a bound off by one, which a constructed case with a start exactly on the window's bound catches for each scope.

*End summaries (D26).* `compare` also checks that the start summaries alone prune a subset of what pruning with the end summaries prunes. A constructed case puts a diary entry 20 days after an exposure's latest end: with a grace of 20 days the engine says `possible` and nothing is pruned, with 19 it says `none` and the end summaries prune it (the start summaries alone cannot); an ongoing exposure is never pruned on its end. Three planted defects fail: the grace's bound off by one, an exposure's start read as its end, and an ongoing exposure's end read as its start (summaries ignoring the null). The D25 reviewer's fuzz, adapted to the end summaries (summaries computed again from the ingest, small time horizons so bounds coincide, `during` in 30% of relations with `extend_grace`), found no difference: 102,831 engine cases, 77,947 of them pruned and each the engine's own `none` (319 only by the end summaries), and 4,800 store queries at 1 and 2 workers, pruning on against off byte for byte. The three end defects and D25's three each produce wrong results there.

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
  - `files_digest`, the sha256 of the manifest's `files` map;
  - the labels.
- `context_id`: the sha256 of that context. It is the key under which a result can be cached or audited, as engine design §4.5 asks.

`match(store, query, windows, detail='full', chunk=2000)`:
- `detail='full'` (the default) lists every patient in `results`, in patient order, exactly as before the chunked matcher (D21).
- `detail='decided'` lists only the patients the engine ran on (every candidate that has a window) and those whose status comparability or exclusion changed from `none`, in patient order. It adds `detail: 'decided'` and `implicit: {FILTERED: n, NO_INDEX_WINDOW: m}`, the numbers of patients left out, by the result `full` would show for them (`none` with reason `FILTERED`; `excluded`). `counts` and every other field equal the `full` output's, and each listed result equals its `full` counterpart. Use it when the caller needs the decided patients and the counts, not 300,000 identical `FILTERED` dicts.
- `chunk` is the number of candidates loaded and matched at a time; it changes no result.

## Determinism

- Building with one thread and with four threads gives the same `snapshot_id` and table digests.
- Rebuilding from a shuffled ingest (patients and the events within them) gives the same identifier and leaves the existing directory untouched.
- Streaming the converter's iterator gives the same identifier as building its whole document.
- Fetch batch sizes of 1, 7 and 10,000 rows, and DuckDB memory limits of 256 MB and 4 GB, give the same identifier.
- Parquet and ndjson staging, and 1, 7 or 10,000 rows per Parquet staging file, give the same identifier (D26).
- Non-ASCII identifiers and concepts (precomposed and combining accents, an astral character, capitals), nulls in evidence and an ongoing end, built with four threads and batches of 3 rows, give the reference's input digest and every table digest.
- The patient index comes from sorted identifiers, and buckets come from a hash.

The reference rebuilds every table in pure Python from the ingest, with no code shared with the store, and computes the input digest and every table and bucket digest again from the specification, the record columns, `event_evidence` and the postings' latest end included (D26). It must equal the store row for row, table digest by table digest and bucket digest by bucket digest, and on the input digest, on the synthetic cohort, on a hand-built fixture and on the MIMIC fixture. The fixture has:
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

`tools/benchmark_execution_store.py` measures how the store and matching scale with cohort size. The report is `verification/execution-store-scaling-report.json`. It binds by digest the benchmark, `execution_store.py`, `execution_store_reference.py`, `synthetic_ingest.py`, `trajectory_graph.py`, `temporal_stn.py`, the manifest schema, `generate.py` and `test_execution_store.py` (the source of the query set). It is evidence for D19, D20 and D23 and for the deferred decision on a Rust engine (D7), from one machine and synthetic data. The 1.0 figures below come from the 1.0 report as regenerated with the fixed generator (#128), on the same machine and cohorts; the "before D23" figures come from the report as committed at main fdd6e97.

```sh
python tools/benchmark_execution_store.py --sizes 1000,10000,100000,300000,1000000 \
    --memory-limit 4GB --budget-seconds 14400 --keep --work-dir /tmp/store-1m-scaling
```

The report covers 1,000 to 1,000,000 patients, all built with the per-bucket derive (D23), the 2.1 schema and ranged content digests (D24) and the fixed generator, in one run of 9,212 s (2 h 34 min; `--work-dir /tmp/store-sv-scaling`). `--keep` kept the snapshots for the matcher comparison. "Before D24" figures are the report as committed at main 0a18b9a.

**Regenerated for D25** (`--work-dir /tmp/store-d25-scaling`, 9,047 s). The build path is unchanged except for the ingest's normal form, which leaves these cohorts as they are: every size's Parquet files have the same sha256 as the D24 run's, and so do the content digests and the input digests; only the snapshot identifiers changed, through the builder's digest. Build, verification and open figures reproduced the D24 run's within about 1% (build 1,493 s against 1,482 s at 300,000 and 4,958 against 4,968 s at 1,000,000; build peaks 8.08 and 8.14 GiB; content verification 251 and 818 s at 2.60 and 2.51 GiB), so the build rows below are left as the D24 run's. The query rows are the D25 run's: time pruning (D25) in `match()`, with the same status mix and candidate counts at every size and 5,000 results equal to exhaustive matching at 1,000.

**Regenerated for D26** (store 3.0 with typed staging; `--work-dir /tmp/store-d26-scaling`, 10,580 s). The cohorts are the same, but the snapshots are a different format: `events` holds the record fields instead of the evidence, `event_evidence` holds the evidence, the postings hold `max_end_hi`, and the staging is Parquet. Query results are unchanged: the status mix and every query's candidate count equal the D25 run's at every size, and 5,000 results equal exhaustive matching at 1,000. The build, verification and snapshot figures changed, as below; the larger table under Results is the D24 and D25 runs' (store 2.1, ndjson staging), kept for the history it records.

| D26 against D25 | 100,000 | 300,000 | 1,000,000 |
|---|---|---|---|
| Build, add to publish | 598 s (500 s) | 1,746 s (1,493 s) | 5,854 s (4,958 s), +18% |
| of which `add()` | 278 s (235 s) | 828 s (707 s) | 2,760 s (2,336 s) |
| of which the derive | 41 s (42 s) | 109 s (117 s) | 384 s (411 s) |
| Peak memory, build process | 4.60 GiB (5.13) | 8.01 GiB (8.08) | 8.24 GiB (8.14), +1.2% |
| Staged bytes (Parquet; ndjson before) | 1.31 GB (11.97 GB) | 3.93 GB (36.0 GB) | 13.09 GB (120.1 GB), −89% |
| Build database | 2.66 GB (2.38) | 7.96 GB (7.16) | 26.6 GB (24.0) |
| Snapshot on disk | 1.25 GB (1.34) | 3.58 GB (3.86) | 5.49 GB (3.51), +56% |
| Rows content verification reads | 53.8 M (36.5 M) | 161.5 M (109.5 M) | 538.2 M (365.1 M) |
| Content verification, separate process: time, peak | 102 s, 1.73 GiB (84 s, 2.40) | 305 s, 1.76 GiB (251 s, 2.43) | 1,013 s, 1.72 GiB (818 s, 2.34) |
| Open with the `files` check, warm | 0.44 s (0.46) | 1.20 s (1.30) | 1.85 s (1.18) |
| `match()` p50 / max, one process | 1.02 / 18.6 s (1.03 / 18.6) | 2.46 / 59.2 s (2.47 / 59.9) | 7.56 / 244 s (7.52 / 245) |
| Query process peak | 1.19 GiB (1.19) | 2.36 GiB (2.33) | 6.99 GiB (6.86) |

- **The build is 16 to 20% slower** (17 to 20% from 100,000 up), and that is mostly the format, not the staging. A same-tree pilot (ndjson against Parquet staging, build only, store 3.0) gave 567 against 583 s at 100,000 and 1,710 against 1,737 s at 300,000: Parquet staging costs 1.6 to 3% (its `add()` is 6 to 8% slower, converting each batch of Python lists to typed columns; its derive 10 to 16% faster, reading typed files instead of parsing JSON), while 3.0 with ndjson staging was already 13 to 15% slower than 2.1. The new `event_evidence` table adds a derive statement per part (155 s at 1,000,000), a table to digest, write and verify (as many rows as `events`), and the record fields make every event row wider.
- **Staging is 89% smaller** (13.1 against 120.1 GB at 1,000,000; about 76 bytes per event against 694), in 5,287 files at 100,000 and about 15,800 at 300,000.
- **Memory** is unchanged in shape: the build peak is set by writing and verifying the Parquet files, about 2.2 times the 4 GB limit (8.24 GiB at 1,000,000, against 8.14 before). Content verification in its own process fell to 1.7 GiB (from 2.3 to 2.4), each table's sort being narrower, but takes 21 to 24% longer, reading 47% more rows (the evidence table repeats each event's key) at 530,000 rows a second (440,000 before).
- **The snapshot** is 7% smaller at 100,000 and 300,000 and 56% larger at 1,000,000. The synthetic converter now stores each event's `recorded_at` and `available_at` as typed microseconds: 2.03 GB of the 4.11 GB `events` table at 1,000,000 (318 and 288 MB at 300,000), shifted with every copy so that they never repeat. Before, they were ISO strings inside the evidence, and replication (copies of a patient sort together) let Parquet's dictionary encoding take whole evidence strings; `events` without the record times is 2.08 GB, against 3.10 GB for 2.1's `events` with its evidence. `event_evidence` is 0.90 GB at 1,000,000 and 2.00 GB at 300,000. The 1,000,000 figure is thus an artefact of replication more than of the format; with distinct patients the snapshot would grow linearly from 300,000 (about 12 GB).
- **Opening** with the `files` check took 1.85 s warm at 1,000,000 (1.18 s before), reading 56% more bytes.

### Method

- **Machine.** Apple M4 Pro, 14 logical CPUs, 64 GB, macOS, CPython 3.12.15, DuckDB 2.0.0. DuckDB had one thread and a 4 GB memory limit for build and open (DuckDB reads `4GB` as 4 × 10⁹ bytes, 3.73 GiB). Matching is single-threaded Python.
- **Processes.** Each size runs in fresh subprocesses. One generates, converts and builds; another opens and queries; a third profiles both verification levels; and, up to 10,000 patients, a fourth runs cProfile on one `match()`. Peak memory is the maximum resident set size of each. The parent kills a size that passes the wall-time budget (4 h) or 40 GB resident, and records why.
- **Streaming.** The build process writes the distinct pool to an ndjson file one generator seed at a time. It then reads the file back a line at a time, replicating as it reads, into `StoreBuilder.add`, so no process holds the cohort. The builder's stage callback records time and resident memory after each stage of `finish()`, including each bucket of the derive, and resident memory is sampled every 1,000 patients while adding.
- **Cohorts.** The distinct pool is the generator with one seed per 48 patients (seed 20261103 + i), relabelled per cohort, up to 10,000 patients. Beyond that the pool is replicated: copy k of a patient takes the id `<id>~r<k>` and a whole-day shift that depends only on k. Replication adds no variety. Patients have a mean of 173 events (p95 384, max 519). Since the generator fix (#128) no seed fails; the benchmark still skips and records a failing seed as a safeguard.
- **Queries.** Seeded queries from `random_query`, anchored on the first cohort and identical for every size, with index windows from `random_windows` per pool patient, following the copy. Up to 10,000 patients the benchmark runs 50 queries with 3 repeats and takes the median per query. Above that it runs 10 queries once, because a wide `match()` takes minutes there, so the percentiles from 100,000 up rest on ten samples. At 1,000,000 the same ten queries ran in full (no reduced set was needed). `match()` is timed end to end, includes `candidates()` and runs single-process (`workers=1`).
- **Correctness.** At 1,000 patients, 5 queries were compared with exhaustive matching: 5,000 patient results, 0 mismatches. Every build verified its own files by content before publishing. The verify profile then re-derived every digest from the files at every size, 1,000,000 included, and agreed each time.

### Results

| | 1,000 | 10,000 | 100,000 | 300,000 | 1,000,000 |
|---|---|---|---|---|---|
| Events | 172,754 | 1,731,493 | 17,314,930 | 51,944,790 | 173,149,300 |
| Build (`StoreBuilder`, add to publish) | 5.8 s | 50.1 s | 495 s | 1,482 s | 4,968 s (83 min) |
| of which `add()` | 2.3 s | 23 s | 231 s | 694 s | 2,331 s |
| of which the derive | 0.9 s | 4.5 s | 42 s | 119 s | 414 s |
| Peak memory, build process | 0.45 GiB | 1.10 GiB | 5.14 GiB | 8.08 GiB | 8.14 GiB |
| Before D24: build / peak | 5.5 s / 0.42 GiB | 47.7 s / 0.98 GiB | 485 s / 5.11 GiB | 1,463 s / 8.09 GiB | 4,901 s / 8.12 GiB |
| Before D23: build / peak | 5.2 s / 0.66 GiB | 48.4 s / 2.50 GiB | 503 s / 8.48 GiB | 1,597 s / 8.78 GiB | refused in the derive (pre-fix generator run, 11.6 GiB) |
| 1.0: build / peak | 4.3 s / 1.2 GB | 38.8 s / 11.8 GB | killed at 40.5 GiB after 327 s | not attempted | not attempted |
| Derive parts | 8 | 8 | 24 | 56 | 176 |
| Staged ndjson (deleted once derived) | 0.12 GB | 1.19 GB | 12.0 GB | 36.0 GB | 120.1 GB |
| Build database | 0.04 GB | 0.27 GB | 2.38 GB | 7.15 GB | 24.0 GB |
| Snapshot on disk | 14.4 MB | 142.9 MB | 1.34 GB | 3.86 GB | 3.51 GB (see below) |
| Open with the `files` check, cold / warm | 0.16 / 0.03 s | 0.10 / 0.06 s | 0.81 / 0.47 s | 1.82 / 1.30 s | 1.73 / 1.18 s |
| 1.0: open with full content verification | 0.89 s | 8.1 s | | | |
| `content` verification of the files | 1.0 s | 8.6 s | 85 s | 252 s | 819 s |
| Peak memory, content verification (separate process; D24: one `patient_idx` range per sort) | 0.20 GiB | 0.59 GiB | 2.40 GiB | 2.42 GiB | 2.33 GiB |
| Before D24: content verification time / peak | 0.9 s / 0.19 GiB | 8.3 s / 0.47 GiB | 82 s / 2.37 GiB | 247 s / 5.53 GiB | 843 s / 7.85 GiB |
| 1.0: peak memory, verification | 0.38 GiB | 2.95 GiB | about 30 GiB (extrapolated) | | |
| Peak memory, query process (D25: time pruning, one process; D24 run: 0.60, 1.09, 1.17, 2.32, 6.99 GiB; before D24: 0.57, 0.99, 1.12, 2.30, 6.79 GiB; 2.0 as first measured: 0.59, 1.56, 6.84, 21.0 GiB to 300,000) | 0.57 GiB | 1.01 GiB | 1.19 GiB | 2.33 GiB | 6.86 GiB |
| `candidates()` p50 / max | 1.2 / 1.9 ms | 3.4 / 5.4 ms | 19 / 32 ms | 55 / 98 ms | 193 / 356 ms |
| `match()` p50 / max (D25: time pruning, one process; D24 run: 0.05 / 0.28, 0.47 / 3.28, 1.7 / 18.8, 4.6 / 59.9, 17.3 / 245 s; before D24: 0.05 / 0.27, 0.45 / 3.16, 1.6 / 18.9, 4.5 / 60.3, 16.6 / 246 s; chunked matcher before it: 0.08 / 0.92, 0.79 / 9.56, 1.9 / 23.7, 5.4 / 72.3 s; 2.0 as first measured: 0.10 / 1.00, 1.20 / 10.6, 3.5 / 29.6, 10.0 / 95.0 s) | 0.04 / 0.24 s | 0.35 / 2.86 s | 1.0 / 18.6 s | 2.5 / 59.9 s | 7.5 / 245 s |

- **1,000,000 patients build and verify** under the 4 GB limit: 83 minutes from the first `add()` to publication, peak 8.14 GiB, and a separate content verification of the files in 14 minutes that agreed with the manifest.
- **Content verification's memory no longer grows with the cohort (D24):** 2.40, 2.42 and 2.33 GiB at 100,000, 300,000 and 1,000,000 patients, against 2.37, 5.53 and 7.85 GiB before, at the same speed (433,000 to 445,000 rows a second). At 100,000 every bucket's largest index is below the span, so it is one query per bucket, as before. The verifying process had the 4 GB limit before too: the growth was the whole-bucket sort, not a missing limit.
- **The derive's memory does not grow with the cohort either (D23).** In the build, the content digests now stay near the derive's level (5.85 GiB at 1,000,000, against 8.08 before D24). The build peak is therefore set by writing the Parquet files, each bucket sorted for reads (`SORT`): 7.91 GiB at 1,000,000. The in-process content verification then holds that level (8.08 GiB at 300,000, 8.14 at 1,000,000), about 2.2 times the limit (3.73 GiB). At 100,000 the D23 build fell from 8.48 to 5.11 GiB.
- **2.1 costs the build almost nothing.** The staging is byte for byte the same size (the three event fields are left out of lines without them), and builds took 0.7 to 2% longer, within run-to-run spread. Matching on these cohorts is unchanged in result, and in cost per candidate in the matcher comparison (below). The single-query `match()` times here moved by +3.5 to +5% at p50, within this benchmark's run-to-run spread for single measurements.
- **The snapshot at 1,000,000 is smaller than at 300,000.** This is an artefact of replication: copies of a patient sort next to each other (`<id>`, `<id>~r1`, ...), so at 1,000,000 a bucket's events table holds runs of about 12 identical evidence strings and Parquet's dictionary encoding takes them, where at 300,000 the runs are about 4. Events are 3.10 GB of the 3.51 GB. Linear growth from 300,000 would give about 13 GB.
- **Time pruning (D25) cuts the median single-process `match()`** by 40% at 100,000 and about half at 300,000 and 1,000,000 (1.7 to 1.0 s, 4.6 to 2.5 s, 17.3 to 7.5 s) and leaves the maximum where it was, since the slowest query (seed 7, scope `any`, open relations) has nothing the summaries can prune. Query-process memory is unchanged (6.86 against 6.99 GiB at 1,000,000). A first version pruned before cutting chunks, so its chunks of survivors were sparse and one load of 200 survivors spread over the whole cohort held 1.5 GiB; it raised this peak to 7.99 and then 8.28 GiB in two discarded runs, and chunks are now cut before pruning.
- **Matching at 1,000,000** (single process, ten queries): `match()` p50 7.5 s and max 245 s, query process 6.86 GiB, which holds a result for every patient (D24: 17.3 s, 245 s, 6.99 GiB). The partition-parallel matcher (D21, arm C) was not run at this size; it is the lever.

### Where memory and time go in the build

Resident memory after each stage of `finish()`, in GiB, with the peak so far in brackets where it differs, and each stage's seconds:

| Stage | 100,000 | 300,000 | 1,000,000 |
|---|---|---|---|
| All patients added (Python holds one patient at a time) | 0.08 (0.21), 266 s | 0.08 (0.21), 802 s | 0.08 (0.21), 2,665 s |
| Staged patients loaded, duplicates checked, input digest | 0.16, 0.2 s | 0.21, 0.3 s | 0.34, 0.9 s |
| DuckDB derive, one part at a time | 4.00, 40 s | 5.19, 116 s | 5.49, 401 s |
| Content digests from the derived tables (D24: one `patient_idx` range per sort) | 4.42 (4.60), 84 s | 5.47 (5.66), 253 s | 5.64 (5.85), 844 s |
| Parquet written bucket by bucket, each file hashed | 4.81 (4.94), 15 s | 7.80 (7.85), 51 s | 7.90 (7.91), 166 s |
| Content verification of the written files | 4.81 (5.14), 85 s | 7.77 (8.08), 249 s | 7.94 (8.14), 817 s |

The derive rows above are from this run: 4.13 GiB, 42 s at 100,000; 5.18 GiB, 119 s at 300,000; 5.52 GiB, 414 s at 1,000,000. Before D24 the content digests took the process to 7.74 (7.93) GiB at 300,000 and 7.87 (8.08) at 1,000,000. Before D23 the derive took the process to 8.46 GiB at 100,000 (43 s) and to 8.17 GiB, peak 8.59, at 300,000 (189 s); the content digests, Parquet and verification then ran at that level.

- **The derive is bounded by the part, not the cohort.** A part holds about 2,000,000 staged rows, a million events. At 1,000,000 patients each of the 176 parts runs ten statements; DuckDB's accounted memory after any statement was at most 3.71 GB, it spilled nothing (`temporary_storage_bytes` 0 throughout), and each bucket took 49 to 51 s. Resident memory rose while DuckDB's buffer pool filled with table pages and then held: after each bucket at 1,000,000, 4.80, 5.24, 5.32, 5.35, 5.40, 5.42, 5.44 and 5.49 GiB; at 300,000 it levelled at 5.04 to 5.19 GiB from the fourth bucket on. Main's derive at 300,000 used up to 13.2 GB of temporary storage in its events load and 5.6 GB in its postings statement.
- **The derive is faster too**: 116 s against 189 s at 300,000, because nothing spills. Loading the events is 70% of it (279 of 401 s at 1,000,000), then event concepts (59 s), postings (34 s) and source concepts (17 s).
- **The peak is now in writing the Parquet files.** Before D24 each bucket's events were sorted whole by the canonical key (21.6 million rows per bucket at 1,000,000) for the content digests, which took the process to 7.92 GiB at 300,000 and 8.05 GiB at 1,000,000. The digests now sort one `patient_idx` range at a time and stay near 5.6 GiB. Writing each bucket sorted for reads (`COPY ... ORDER BY`) still sorts a whole bucket, and takes the process to 7.9 GiB; ranging that sort too is possible, since files may hold several row groups, but it is not done.
- **Resident memory against `memory_limit`.** DuckDB keeps to its own accounting (at most 3.71 GB against the 4 GB limit). The process holds about 1.5 times the limit through the derive and about 2.2 times at the peak, at 300,000 and 1,000,000 alike; under a 1.2 GB limit at 300,000 (a pilot run of this code) the build peaked at 2.84 GiB, 2.5 times, and its derive at 2.45 GiB with DuckDB at 0.82 GiB. The limit is a lever on resident memory, and now also a cap independent of the cohort, but the factor between them (allocation outside the buffer manager, or freed memory the macOS allocator keeps) is not resolved.
- **Time is linear**: 4.85 to 4.90 s per 1,000 patients at 100,000, 300,000 and 1,000,000. `add()` is 47% of it (validation, canonical form, staging and the patient digest, all in Python); the content digests 17%, content verification 17%, the derive 8% and writing 3%.
- **Disk.** Staging was about 0.69 KB per event as ndjson (120 GB at 1,000,000), deleted once derived; the build database is 24 GB at 1,000,000. Both live in `work_dir`. Since D26 the staging is Parquet, about 76 bytes per event (13.1 GB), and the build database 26.6 GB.
- **Content verification runs at about 424,000 to 445,000 rows a second** from 10,000 patients up; at 1,000,000 its 365 million rows took 819 s. Its process peaked at 2.33 GiB (7.85 GiB before D24). Time, not memory, is why the default check on open is `files`, which takes 1.2 s at 1,000,000.

### 1,000,000 patients: before and after D23

*Before (from a run with the pre-fix generator, not in the report):* the build streamed all 1,000,000 patients in 45 minutes (173 million events, 120 GB of staging), with Python at 0.18 GiB. Then DuckDB refused inside the derive: `Out of Memory Error: failed to pin block of size 256.0 KiB (3.7 GiB/3.7 GiB used)`. The process peak was 11.6 GiB. Nothing was written under the store root.

*Diagnosis (D23).* Main's derive, run statement by statement on the staged 300,000-patient cohort, fails in the postings statement under a 1.2 GB limit (4 GB × 0.3, the same limit per patient as 4 GB at 1,000,000), with the same error. Its two halves run as separate statements succeed. The statement plans four hash aggregates and a hash join, all sized by the events (pre-aggregated events by patient and event, 52 million groups at 300,000; the closure-expanded `DISTINCT`, 83 million rows), live at once. It needs about 0.6 to 0.67 GB per 100,000 patients, so about 6 to 6.7 GB at 1,000,000; 4 GB held to about 600,000 to 650,000 patients. D23 has the figures.

*After:* the cohort of the regenerated report (fixed generator, 173,149,300 events) builds in 83 minutes with a peak of 8.14 GiB, verifies, opens in 1.2 s and answers the ten queries.

### What grows how

- **Build time and rows are linear**: about 3.3 times for 3.3 times the patients. The snapshot is linear up to 300,000; beyond, replication makes it compress better (above).
- **Build memory is DuckDB's buffer pool plus what DuckDB does not return, and is now flat**: 0.45, 1.10, 5.14, 8.08 and 8.14 GiB from 1,000 to 1,000,000. Content verification in its own process is flat at about 2.4 GiB from 100,000 up (D24). It grows until the buffer pool fills, about 300,000 patients here, and then stays at about 2.2 times the limit. Before D23 it was 8.5 to 8.8 GiB already at 100,000 to 300,000 and failed at 1,000,000.
- **Opening is fast at every size.** The `files` check reads the Parquet bytes once: 1.2 s warm at 1,000,000, against 8.1 s for the 1.0 content check at 10,000.
- **Matching is the wall.** (As first measured, before D21: the query process peaked at 21.0 GiB at 300,000 and a wide `match()` took 95 s; the single-process matcher now holds 2.3 GiB and takes 60 s there, and 7.0 GiB and 245 s at 1,000,000.) Single-process `match()` still builds a result for every patient and loads every candidate's events into Python; partition-parallel matching (D21) is the lever above 300,000.

### Matching cost (D21)

`tools/benchmark_matcher.py` and `verification/matcher-comparison-report.json` compare three arms on the same snapshots and the same seeded queries: the matcher before D21 (arm A), the single-process matcher of this tree (arm B), and partition-parallel matching (arm C). Up to 10,000 patients the benchmark runs 50 queries with 3 repeats; above that, 10 queries once. Each arm, size and worker count runs in its own process. The report binds the benchmark, `execution_store.py`, `trajectory_graph.py`, `temporal_stn.py`, the scaling benchmark and the byte-identity fixture. Arm B now has two changes behind it, the cheap wins and the engine kernel, described below.

**What changed first (cheap wins).** Results are byte-identical to arm A's. The report compares a digest of every patient's result, query by query (50 of 50 at 1,000 and 10,000 patients, 10 of 10 at 100,000 and 300,000), and the test suite compares 300 seeded queries against a committed fixture made from the old matcher.
- Candidates are loaded and matched 2,000 at a time (`chunk`): the chunk goes into a temporary table, the events are joined to it (and to the index range it spans) instead of an `IN` list of up to 106,000 parameters, and only each compact result is kept.
- `trajectory_graph.PreparedQuery` validates the query once and caches the applied concepts, compiled constraints, constraint split, step graph and relaxation sets per query and per relaxation set; the store calls `match_prepared(pq, patient, trusted=True)`, which skips the per-event checks the store made at ingest (`Times` still runs).
- Windows are checked as plain integers with the same refusal codes, no result dict is built for filtered patients until the output asks for it, and `detail='decided'` lists only decided patients.
- Comparability runs in SQL: the windows go into a temporary table and the coverage-overlap test is a join. The excluded-record read is streamed.

**The engine kernel (D21's next step).** The profile put the pairwise arc-consistency kernel (`certain`, `plausible` and `gap`) at about 60% of a wide match: one Python call per constraint, per pair of candidate events, each building a binding dict and calling `Times.gap` twice. Every result is unchanged. The change, all in `trajectory_graph.py`:
- **Closed-form pair tests.** When two events' variables are not linked by the patient's own facts, `Times.gap(x, y)` is just `dom[y].hi - dom[x].lo`. So each constraint between steps a and b becomes a bound on one end of b's event's range, set by a's event: `certain` gives a lower bound on a lower end and an upper bound on an upper end, and `plausible` the reverse. Times and bounds are integers, and only upper ends and bounds can be infinite, so moving a term across a comparison is exact. The derivation is in the comment above `_arc_spec`. Each query plan compiles these bounds once per step pair, direction and test (`_Arc`); a revision then scans plain tuples.
- **Linked pairs keep the original test.** A pair whose variables may be linked is decided by `certain` or `plausible` as before: one event bound to both steps, or two events with variables in one component of the patient's network. An event's record carries its components only when another event shares one, so this check costs nothing for unshared events.
- **Unary tests** (a step against `START` or `END`) use the same closed form with the anchor's value; an anchor is never linked.
- **Order and choices are unchanged.** AC-3 keeps its queue order, a revision filters a domain without reordering it, and the tree assignment takes the same first supporting event. Candidate order is the same, (s, f, id) with stable sorts, from one sort per patient (`Times.candidate_records`) instead of one per step and relaxation set.
- **Per-patient set-up.** Building `Event`s skips the frozen dataclass's per-field `object.__setattr__`; the result is the same class with the same attribute dictionary, so equality, hashing and repr are unchanged. `Times` skips union-find and the component limit when no variable is named by two events, since every component is then one event's. Both changes give the same `dom`, `component` and `distance`.

*Evidence that nothing changed.*
- `KernelIdentityTests` runs the engine at main 12b3440 against the current one. The old engine is kept byte for byte as `patterns/fixtures/trajectory_graph_before_kernel.py`, pinned by digest and not packaged. A frozen copy, rather than recorded outputs, lets the test compare any number of new cases and compare `decide`'s intermediate results (method, search steps, binding, witness) as well as `match`.
- Each comparison checks `match`, `match_prepared` with `trusted`, and `decide` for every relaxation set. There are 11,750 of them:
  - the differential's 1,500 cases, each with moved, reversed and windowless relatives (6,000);
  - 2,400 new cases of six shapes, each also widened with more events, negative times, search budgets down to 1, anchors on `END` and `after` scope (4,800);
  - 400 cases with more shared variables, plain and widened (800);
  - 150 widened cases with the closed form switched off.
- Mutating the kernel (an off-by-one bound, linked pairs decided by the closed form, a changed candidate order, shared events without keys, a wrong range end) fails these tests.
- The existing differential, the store's digest fixture and the benchmark's per-query digests all still match: arm B equals arm A at every size, and arm C equals B at every worker count.

*Kernel speed-up.* Engine-only, over the 142,657 candidate patients of the 50 queries at 10,000 patients (inputs captured from the store, the same calls replayed):

| | Main (12b3440) | This change |
|---|---|---|
| Engine time | 42.5 s (298 µs per candidate) | 13.1 s (92 µs), 3.2× faster |
| Kernel under cProfile (`_arc_consistency` and unary filtering) | 55.5 s, 55% of the engine | 6.5 s, 29%, 8.5× faster |
| Calls to `Times.gap` | 72.5 million | 96,000 |
| Calls to `certain` and `plausible` | 41.4 million | 41,000 |

The digest of every result is equal. What remains in the engine is mostly per-event set-up: building `Event`s and `Times`, about half of it.

**Arms A, B and C.** Each cell is the wall time of `match()` over the queries, detail `full`. Per-candidate cost is total seconds over total candidates. Peaks are the query process's own resident set and, for C, the largest worker's; the two overlap in shared pages, so they are not summed. All rows come from one run. C with 1 worker is the unchanged single-process path, a control. The "before the kernel" column is the previous report: same machine, snapshots built from the same cohorts, candidate counts equal query by query. The 30-query "before" rows come from an uncommitted same-day run of main 12b3440, so they are not bound evidence.

| Patients | Arm | p50 / p95 / max (s) | µs per candidate, end to end (engine) | before the kernel: p50 / p95 / max (s), µs | Query-process peak | Largest worker peak |
|---|---|---|---|---|---|---|
| 1,000 | A | 0.10 / 0.44 / 0.99 | 579 (378) | | 0.29 GiB | |
| | B | 0.04 / 0.21 / 0.26 | 248 (95) | 0.07 / 0.34 / 0.86, 430 | 0.27 GiB | |
| | C, 1 worker (control) | 0.04 / 0.20 / 0.26 | 247 | | 0.27 GiB | |
| | C, 14 workers | 0.05 / 0.21 / 0.26 | 251 | 0.08 / 0.34 / 0.86, 438 | 0.14 GiB | 0.21 GiB |
| 10,000 | A | 0.99 / 4.50 / 9.82 | 585 (371) | | 1.46 GiB | |
| | B | 0.39 / 2.22 / 2.89 | 257 (91) | 0.72 / 3.84 / 9.11, 451 | 0.88 GiB | |
| | C, 1 worker (control) | 0.40 / 2.24 / 2.90 | 258 | | 0.87 GiB | |
| | C, 2 workers | 0.39 / 1.27 / 1.82 | 172 | 0.66 / 2.28 / 5.79, 301 | 0.28 GiB | 0.69 GiB |
| | C, 4 workers | 0.35 / 1.20 / 1.26 | 147 | 0.49 / 2.21 / 3.09, 250 | 0.28 GiB | 0.64 GiB |
| | C, 8 workers | 0.29 / 1.21 / 1.40 | 143 | 0.42 / 2.24 / 3.07, 241 | 0.28 GiB | 0.63 GiB |
| | C, 14 workers | 0.29 / 1.26 / 1.34 | 145 | 0.43 / 2.29 / 3.10, 244 | 0.28 GiB | 0.67 GiB |
| 100,000 | A | 3.40 / 28.94 / 28.94 | 741 (391) | | 7.65 GiB | |
| | B | 1.50 / 18.04 / 18.04 | 344 (106) | 1.83 / 23.65 / 23.65, 539 | 0.74 GiB | |
| | C, 1 worker (control) | 1.50 / 18.04 / 18.04 | 343 | | 0.76 GiB | |
| | C, 2 workers | 0.95 / 9.22 / 9.22 | 187 | 1.11 / 12.01 / 12.01, 288 | 0.31 GiB | 0.61 GiB |
| | C, 4 workers | 0.69 / 5.48 / 5.48 | 121 | 0.78 / 6.72 / 6.72, 175 | 0.31 GiB | 0.59 GiB |
| | C, 8 workers | 0.68 / 3.44 / 3.44 | 92 | 0.78 / 4.39 / 4.39, 129 | 0.31 GiB | 0.56 GiB |
| | C, 14 workers | 0.68 / 2.97 / 2.97 | 83 | 0.77 / 3.67 / 3.67, 114 | 0.31 GiB | 0.55 GiB |
| 300,000 | A | 10.28 / 94.51 / 94.51 | 764 (389) | | 19.97 GiB | |
| | B | 4.08 / 56.15 / 56.15 | 346 (106) | 5.01 / 71.76 / 71.76, 538 | 0.93 GiB | |
| | C, 1 worker (control) | 4.06 / 55.97 / 55.97 | 345 | | 0.93 GiB | |
| | C, 2 workers | 2.50 / 26.87 / 26.87 | 180 | 3.00 / 35.30 / 35.30, 278 | 0.59 GiB | 0.62 GiB |
| | C, 4 workers | 1.75 / 14.86 / 14.86 | 111 | 1.98 / 18.59 / 18.59, 160 | 0.60 GiB | 0.61 GiB |
| | C, 8 workers | 1.41 / 9.02 / 9.02 | 77 | 1.55 / 12.01 / 12.01, 109 | 0.64 GiB | 0.55 GiB |
| | C, 14 workers | 1.30 / 7.25 / 7.25 | 66 | 1.42 / 9.70 / 9.70, 91 | 0.64 GiB | 0.59 GiB |

At 1,000 patients, C at 2, 4 and 8 workers equals C at 14 to within 0.01 s and 3 µs, so those rows are left out; the report has every row. At 100,000 and 300,000 the p95 is the maximum, since there are ten queries. `detail='decided'` costs the same as `full` to within noise (arm B_decided in the report); it shrinks the output, not the time.

- **Memory** is the clear win of the cheap wins: 20.0 GiB to 0.93 GiB at 300,000, flat in the number of patients. With workers the query process is smaller still (0.6 GiB at 300,000), since the candidates' events never reach it; each worker stays near 0.6 GiB. The kernel changed no memory figure.
- **Time (B).** Per candidate at 300,000, arm A costs 764 µs, the cheap wins 538 µs and the kernel 346 µs (engine 389, 296 and 106 µs). Event loading in the store, 208 µs, is now the larger part. Per-candidate cost no longer grows with the cohort beyond 100,000.
- **Parallel (C)** at 14 workers speeds the widest of the first ten queries up 7.7 times over B (56.2 s to 7.25 s at 300,000); the kernel cut the same query at 14 workers from 9.70 s to 7.25 s. Scaling is close to linear to 4 workers, and 14 workers (the 4 efficiency cores included) gain a little over 8. What does not parallelise is the query process's own work: candidates(), the windows dictionary, comparability, exclusions, merging and the context digests. Small queries do not gain: a query with fewer than 2,000 candidates is one unit (1,000 patients: no change at any count).
- **Start-up** of the pool (spawn, import, open the snapshot in each worker) is 0.20 s at 2 workers and 0.25 s at 14, paid once per pool (cold figure, page cache warm); the timings above are of a warm pool.
- **Byte identity.** Every arm C result equals arm B's, and every arm B result equals arm A's: the per-query digest of every patient's result matches for 50 of 50 queries at 1,000 and 10,000 patients and 10 of 10 at 100,000 and 300,000, at each of 1, 2, 4, 8 and 14 workers. The tests compare workers 1, 2 and 4 byte for byte over 25 seeded queries (with relaxations, exclusions and not-comparable results), against the committed digest fixture of the matcher before D21, and run a MIMIC stage-2 query with 2 workers.

**How it works (`match(..., workers=N)`).**
- **Units.** The query process runs `candidates()` and checks the windows. The engine's work, loading each unit's events and matching its patients, is cut into units of `chunk` (2,000) candidate patients.
- **Pool.** Units go to a persistent pool of `spawn`ed worker processes (`get_pool`, `MatchPool`, `close_pools()`), one pool per snapshot path and worker count.
- **Workers.** Each worker opens the snapshot once with `verify=False`, checks that it is the same snapshot id and file list the parent opened, and keeps its own connection; no store or connection is shared. A unit carries its patients' indices, ids and windows and the query, and returns each patient's result.
- **Merge.** The parent merges results by patient index, then runs comparability and exclusions as before. The result's `verified` is the level the parent opened at.
- **Memory.** Each worker's DuckDB memory limit is the store's (the default 4 GB if none) divided by the worker count, at least 256 MB, and it removes its spill directory when the pool closes. The limit is a property of the pool (`MatchPool.memory_limit`), not of the result, whose bytes do not depend on the worker count.
- **Lifetime.** Pools outlive the `Store` they were made from and are shared per (snapshot path, worker count), so a caller that is done should call `close_pools()` (it also runs at interpreter exit).
- **Failure.** A worker that raises or dies fails the whole call with `MatchWorkerError` (code `MATCH_WORKER_FAILED`), and the pool is discarded if a process died; no partial result is returned.
- **What stays in the parent.** Comparability and exclusions were under 2% of the time in the profile at 10,000 patients (above); their share at 300,000 was not profiled separately.

**Confirmation at 300,000 patients (30 queries, 3 repeats).** Seeds 0 to 29 (the first ten are the queries above), three repeats each at 8 and 14 workers, 90 measurements per arm; arm B runs the 30 queries once. The same runs were made with the code before the kernel (main 12b3440's benchmark and matcher) on the same snapshots, the same day, so the two halves of the table compare directly. That run's report was a scratch copy and is not committed; the committed report holds this change's confirmation. Every one of the 30 results is byte-identical to B's at both worker counts, in both halves. The table pools every (query, repeat) measurement:

| Arm | Code | p50 | p95 (range over the 3 repeats) | max (range) | µs per candidate | Query-process peak |
|---|---|---|---|---|---|---|
| B (1 repeat) | before the kernel | 22.7 s | 116.8 s | 139.7 s | 418 (engine 242) | 1.49 GiB |
| B (1 repeat) | this change | 11.9 s | 56.5 s | 81.8 s | 258 (engine 82) | 1.49 GiB |
| C, 8 workers | before the kernel | 4.6 s | 19.6 s (19.6 to 19.8) | 21.8 s (21.4 to 21.8) | 75 | 0.80 GiB |
| C, 8 workers | this change | 3.1 s | 9.3 s (9.0 to 9.4) | 12.5 s (11.9 to 12.5) | 49 | 0.82 GiB |
| C, 14 workers | before the kernel | 3.8 s | 17.1 s (15.7 to 17.4) | 19.8 s (17.7 to 19.8) | 66 | 0.82 GiB |
| C, 14 workers | this change | 2.5 s | 7.9 s (7.4 to 8.4) | 11.8 s (10.0 to 11.8) | 42 | 0.82 GiB |

The largest worker's peak was 1.06 GiB at 8 workers and 0.96 GiB at 14. Per-query medians at 14 workers: the slowest query, seed 19 (195,270 candidates), takes 10.3 s, against 19.4 s before the kernel. Queries whose time was mostly the engine gained most: seed 23 fell from 17.1 s to 4.6 s at 14 workers, and from 116.8 s to 32.2 s in arm B. Queries whose patients have many events are bounded by event loading in the store and gained least: seed 7 fell from 69.5 s to 56.5 s in arm B, and from 9.9 s to 7.6 s at 14 workers. The previous report's confirmation, on an earlier build of the same snapshots, gave p95 15.3 s (15.1 to 18.7 over its repeats) and max 20.7 s at 14 workers. Its range overlaps the "before" row here.

**Target (D21)** is a 300,000-patient full-detail `match()` with p95 at most 10 s, maximum at most 30 s and query-process memory under 4 GB. Arm B alone meets the memory target and misses latency (p95 = max = 56.2 s over ten queries; p95 56.5 s and max 81.8 s over thirty). B+C at 14 workers **meets the target at 30 queries × 3 repeats: p95 7.9 s (7.4 to 8.4 over the repeats), max 11.8 s, query process 0.82 GiB**. At 8 workers it is met narrowly: p95 9.3 s (9.0 to 9.4), max 12.5 s. Before the kernel the same runs gave p95 17.1 s at 14 workers and 19.6 s at 8.

**Decision.** Target met; stay in Python. By D21's rule the Rust prototype (arms D and D′) is not built, and no toolchain is installed. The recommended worker count on this machine stays 14; the library default stays `workers=1`. The p95 margin at 14 workers is 21% pooled (7.9 s against 10 s) and 16% in the slowest repeat (8.4 s). The levers left if more is needed are event loading in the store (now the larger part of a candidate's cost) and time pruning of step domains on posting summaries.

**Regenerated for D23.** The report was regenerated on the snapshots of the D23 scaling run (same cohorts and contents, new identifiers, since `execution_store.py` changed). Every per-query result digest equals the previous report's, for every arm and size and in the confirmation, and arm C equals B and B equals A as before. The tables above are from the previous run. In this one the confirmation at 14 workers gave p95 9.1 s (8.6 to 9.4 over the repeats), max 12.9 s and a 0.79 GiB query process; at 8 workers p95 9.4 s (9.3 to 9.7) and max 13.4 s. The target is still met, but the p95 margin at 14 workers is 9% here against 21% before. Single-process event loading and engine cost per candidate are unchanged at 300,000 (arm B: loading 160.34 → 160.26 µs, engine 82.48 → 82.22 µs), and so is C at 8 workers (48.95 → 49.55 µs), so the store change did not slow matching. Only C at 14 workers moved, 41.59 → 47.32 µs per candidate (p95 7.9 → 9.1 s), by 14% between two runs on identical results; the cause is not established (likely machine state at full core count), and two runs cannot show it is within normal variation. The margin in this run is 9%.

**Regenerated for D25 (time pruning).** The report was regenerated on the snapshots of the D25 scaling run (the same Parquet files as D24's, new identifiers), with a new arm `B_unpruned`: this tree with pruning turned off, a same-run baseline. Every per-query digest of arm B equals arm A's (the matcher before D21, through the 2.0 view, which reports 0 patients sharing variables and 0 events proper or naming a variable), arm `B_unpruned`'s and every C arm's, at every size (50, 50, 10, 10) and in the confirmation (30 of 30 at each worker count, and B against `B_unpruned`); candidate counts equal the D24 report's query by query.

| 300,000 patients | D24 report | D25: `B_unpruned` (same run) | D25: B, pruning |
|---|---|---|---|
| B, ten queries: µs per candidate end to end (engine, loading) | 342.7 (106.9, 204.0) | 343.9 (108.3, 203.7) | 279.6 (83.9, 162.5), −18.4% |
| B, ten queries: p50 / p95 = max | 4.14 / 54.9 s | 4.11 / 55.1 s | 2.34 / 55.3 s |
| B, thirty queries (confirmation): µs per candidate; p50 / p95 / max | 258.4; 12.1 / 55.9 / 82.0 s | 257.8; 11.9 / 55.1 / 82.3 s | 189.9; 9.3 / 50.0 / 56.3 s |
| C, 8 workers, confirmation: p95 (range over repeats) / max | 10.56 s (9.94 to 11.65) / 15.1 s | | 8.41 s (8.41 to 8.45) / 10.3 s |
| C, 14 workers, confirmation: p95 (range) / max | 9.35 s (9.06 to 9.89) / 13.4 s | | 7.81 s (7.01 to 8.41) / 9.2 s |
| Query process (confirmation, B / C at 14) | 1.64 / 0.79 GiB | 1.64 GiB | 1.15 / 0.83 GiB |

At 300,000 the summaries prove `none` for 36% of the candidates with a window over the thirty queries (912,210 of 2,526,690), from none (scope `any` with open relations: seeds 2, 7, 12, 24) to 99.6% (seed 0), at 1.2 to 3.7 µs each in the query process. These pruned counts and costs are from ad hoc instrumented runs on the same snapshots, not in the matcher report, which records candidates per query but not pruned counts. Per candidate, arm B costs 18% less over ten queries and 26% less over thirty; engine and loading time both fall, since pruned candidates take neither. The maximum of the ten-query run does not move (seed 7 prunes nothing), and the confirmation's maximum falls from 82 to 56 s because seed 19, the slowest of the thirty, loses 60% of its candidates. In the confirmation every C arm's p95 and maximum fall too; **the D21 target is met again at 8 workers** (p95 8.41 s, every repeat under 8.5 s; max 10.3 s) and with a wider margin at 14 (p95 7.81 s, max 9.2 s). C at 1 to 14 workers, ten queries at 300,000: 280.1, 152.4, 95.8, 70.4 and 61.5 µs per candidate against 342.0, 181.7, 111.1, 79.3 and 68.3 in D24.

**Regenerated for D26 (store 3.0).** The report was regenerated on the snapshots of the D26 scaling run (store 3.0, Parquet staging; same cohorts). Arm A, the matcher at 7dc3364, reads store 2.0, and 3.0 moved `evidence` out of `events`, so no relabelled view of the same files can serve it: arm A now reads its own 2.0 snapshot of each size, built by its own builder and converter from the same generator output (`benchmark_matcher.py --build-arm-a`, `--arm-a-snapshots`; `readable_snapshot` and its guard are gone with the view). The report checks that both snapshots hold the same rows in every column arm A's matcher reads, table by table (`arm_a_snapshot.same_rows`): equal at every size (1, 12, 116 and 344 s to check). Every per-query digest of arm B equals arm A's, `B_unpruned`'s and every C arm's at every size (50, 50, 10, 10), and candidate counts and listed results equal the D25 report's query by query. The report now also records, per query, the candidates time pruning proved `none`, the time it took, and how many the start summaries alone prove (`pruning` in each arm entry).

| 300,000 patients, ten queries | D25 report | D26 |
|---|---|---|
| B: µs per candidate end to end (engine, loading) | 279.6 (83.9, 162.5) | 280.3 (83.5, 163.6) |
| `B_unpruned` | 343.9 (108.3, 203.7) | 345.5 (108.5, 205.0) |
| A, on its own 2.0 snapshot | 762.2 (388.7, 239.6) | 756.4 (385.6, 238.1) |
| C at 8 and 14 workers | 70.4, 61.5 | 70.7, 61.4 |
| B: pruned, of candidates with a window; by the start summaries alone; µs per candidate pruned | (not recorded) | 138,690 of 324,120 (42.8%); 138,360; 2.76 |

Taking `evidence` out of `events` did not make event loading cheaper: B's loading cost is 163.6 against 162.5 µs per candidate at 300,000 (+0.7%, within run-to-run spread), since the loader never selected that column and Parquet reads only the columns asked for. Over the ten queries the end summaries prune 330 more candidates (one query); per candidate, B costs 0.25% more than in D25.

*Confirmation (30 queries, 3 repeats, D26).* All 30 results byte-identical between B, `B_unpruned` and every C arm. Arm B 188.3 µs per candidate (D25: 189.9), p50 / p95 / max 9.3 / 49.9 / 55.8 s. C at 8 workers: p95 8.53 s (8.50 to 8.58 over the repeats), max 10.4 s (D25: 8.41 s, 10.3 s); at 14 workers: p95 7.71 s (7.09 to 7.94), max 9.0 s, query process 0.82 GiB (D25: 7.81 s, 9.2 s). The D21 target is met at both counts. Over the thirty queries pruning proves `none` for 915,060 of 2,526,690 candidates with a window (36.22%), 912,210 of them by the start summaries alone, the figure D25 measured ad hoc: the end summaries add 2,850.

**Regenerated for D24.** The report was regenerated on the snapshots of the D24 scaling run (execution store 2.1, same cohorts, new identifiers). Arm A's code reads store 2.0; it opens the 2.1 snapshots as the same Parquet files under a manifest relabelled 2.0 (`snapshot_view` in its arm entries, `readable_snapshot` in the benchmark; 2.1 only added columns), and its results equal arm B's byte for byte for every query and size. C equals B at every worker count and in the confirmation. Candidate counts and listed results equal the previous report's query by query. Arm B's cost per candidate at 300,000 is unchanged (342.7 against 344.5 µs; loading 204.0 against 206.7 µs). The confirmation at 14 workers gave p95 9.35 s (9.06 to 9.89 over the repeats), max 13.4 s and a 0.79 GiB query process, so the target is still met. At 8 workers p95 was 10.56 s (one repeat at 11.6 s) against 9.40 s before, on identical results and with 3.6% more cost per candidate, so **at 8 workers the target is no longer met**; the cause is not established. The recommended count stays 14.

### Limits of this evidence

- One machine, one DuckDB thread and one memory limit (4 GB). Thread counts and other limits were not swept at scale.
- Synthetic data. Beyond 10,000 patients the cohort is replicated, which multiplies candidates exactly.
- Ten queries at 100,000, 300,000 and 1,000,000 in the main runs, so their latency percentiles are coarse; the D21 target is judged on the 30-query, 3-repeat confirmation at 300,000.
- The diagnosis of the 1,000,000-patient failure (D23) comes from separate diagnostic runs of main's derive on a staged 300,000-patient cohort, under 1.2 GB as a stand-in for 1,000,000 under 4 GB, and from threshold runs on the first 100,000 to 300,000 of its patients; the earlier resident-memory diagnosis came from runs at 30,000 and 100,000 with the pre-fix generator. None of them is in the report.
- Replication flatters the snapshot size at 1,000,000 (above) and makes postings, candidates and match outcomes exact multiples; a cohort of a million distinct patients was not built.
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

| Adapter row (`trajectory-graph-sulo-ingest-1.1`) | Store ingest |
|---|---|
| `patient_id`, `clock_id`, `clock_origin` | `patient_id`, `clock_id`. One clock per patient; a patient whose rows name two clocks would be refused (`CLOCKS_NOT_REBASABLE`) |
| `start_us`, `end_us` (tightened, on the event's own clock) | `start`, `end` as `origin_us + start_us`, where `origin_us` is the clock origin's patient-local coordinate from 0001-01-01T00:00. Every MIMIC event is a closed interval |
| `start_var`, `end_var`, `proper` | `start_var`, `end_var`, `proper` (true for every row): the event's source variables, so events that share one, in one claim store or across a patient's stores, share it in the store too (D24) |
| `box_representable`, `non_box_constraints` | A patient the adapter marks not box-representable (a non-box source constraint) is left out and counted under `INGEST_PATIENT_NOT_BOX_REPRESENTABLE`, by the adapter's reason (`SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE`). `shared_variables` no longer refuses a patient (before D24 it did, as `SHARED_SOURCE_VARIABLE`) |
| `kind_class` and the concepts with a `semantic_fact` basis | `concepts`: the event kind's class and the source item class |
| The other concepts (`event_kind_closure`) | The closure: the kind class's ontology ancestors. The mapping checks that each event's closure reaches exactly the adapter's concepts, plus the item's grouping |
| (no adapter field) | Each item class's `d_items` category as its one ancestor (D14). `closure.bases` gives every concept's kind: `source_item_class`, `source_dictionary_grouping` or `ontology_closure` |
| `record_status`, `selection_state`, the claim support | The record fields (D26): `status`, `selection_state` (`accepted`), `claim_id` and `claim_sha256` from the claim support, whose digest must equal `describe()`'s |
| The importer's claim provenance | The record fields `recorded_at` (from `storetime`, when the row has one), `record_sha256` and `file_sha256` (the source row's and file's digests) and `source_table` (`inputevents`); no `available_at`, since the release carries no availability times |
| Evidence, claim supports, declared bounds, variables, concept bases | `evidence`, free-form, with `history_mode: retrospective`; since D26 it no longer repeats the record fields |
| `event_kind` | `kind` (`recorded_input_segment`). `source` is `inputevents:continuous_segments`, the admitted subset |
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
- **Refusals.** MIMIC claims carry no constraints between events and no shared variables, so the demo has no refusals. The fixture tests show refusal on a store with an added source constraint a box cannot hold and on a source row repeated across stores. A variable shared across two of a patient's stores is kept (D24): the store shares it, and `after` from 0 to 0 between the two events, given one uncertain start, is exact through the store as through the engine on the adapter's rows (possible without the sharing).

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
| Table rows: `event_concepts`, `event_evidence`, `concept_postings`, `concept_closure`, `source_concepts` | 21,960, 10,980, 1,412, 330, 84 |

The snapshot identifier is `54dbd42faf5c53759eee8d3fa6045ac540839452f1eb450b04b55c1dd188bf4b` (profile `execution-store-3.0`). It changes whenever the converter or the store builder changes, or the rows they produce do. Regenerated for D24, every figure of the report is unchanged; only the identifiers changed (the snapshot id, the input digest, the adapter's ingest profile and the bound artifacts' digests), since the events now carry their variables and `proper`. Regenerated for D25 (time pruning, ingest normalisation, `EMPTY_PROPER_EVENT`), every figure is unchanged again, the input digest included (`8e24100f…`: the demo's events name source variables and are all proper, so normalisation leaves them as they are); only the snapshot identifier and the bound artifacts' digests changed. The 500-query differential ran with time pruning on, with no mismatch. Regenerated for D26 (store 3.0), every figure is unchanged again; the snapshot gains the `event_evidence` table (10,980 rows, one per event), and the input digest changed (`ec0612b1…`), since each event now carries its record fields beside an evidence object that no longer repeats them. The differential ran with the end summaries on, with no mismatch.

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
| 424 stores through isolation, acceptance, the graph route and `ingest_rows`, 8 worker processes | 62 s (about 480 CPU-seconds) |
| Mapping, excluded records and coverage | 0.3 s |
| Building the snapshot, content check included | 2.1 s |
| Opening it with the file check | 0.02 s |
| Differential (500 queries, three paths) | 155 s (D26 run; 154 s in the D25 run, 162 s in the D24 run) |

### Chartevents path

This is not built. `chartevents` has 668,862 demo rows. At about 0.2 CPU-seconds per claim, route (a) would take about 37 CPU-hours for the demo alone, and more for full MIMIC-IV. The planned path:
- **Route (b).** Bulk rows come from pure `describe()` identity, the measurement importer's claim description, without the graph route. For each batch, a deterministic sample of stores goes through the full graph route, and a differential requires its store rows to equal the bulk rows exactly. The report must then say that bulk rows are tied to the graph by claim identity and checked on a sample, not each validated through the graph.
- **Measurements are points.** A chart value is a point event with a value and unit, not an interval. The values go into a series table keyed by patient, item and time, beside the events table, so that the matcher's concept postings stay small.
- **Per-patient streaming.** Done for the store in 2.0 (D20): the builder takes one patient at a time and the input digest is over per-patient digests. The MIMIC converter still builds a whole document; it should yield patients to `StoreBuilder` instead.
- **The differential at scale.** The exhaustive and adapter-row engine inputs are built once per patient and reused across queries, not rebuilt for every query.

### Next

- **Store 2.x (after D20).** In order:
  - an evidence table and the D5 typed event columns: done in D26. The expected gain did not happen: event loading is unchanged (the loader never selected `evidence`, and Parquet reads only selected columns), and content verification is 24% slower at 1,000,000 because the evidence table adds rows. What 3.0 gains is typed, validated record fields for selection modes and status filtering;
  - bucket-level roots so that an append rebuilds and rehashes only the buckets it touches (the per-bucket derive of D23, which lets 1,000,000 patients build, is done: [Scaling](#1000000-patients-before-and-after-d23));
  - matching, the next wall per D19 step 3.
- **`chartevents`**, by the path above.
- **Time pruning** on the posting summaries: done in D25, with a differential of pruning on against off, and on end summaries for a `during` relation's grace in D26 ([Time pruning](#time-pruning-d25-d26)).
- **Per-bucket joins.** The candidate patient and event lookups are `IN` lists over the candidate set today. At full scale they become joins against a candidate table, one bucket partition at a time.
- **Event columns and an evidence table:** done in D26 (store 3.0, [Tables](#tables)).
- **A tighter exclusion test.** The exclusion rule reads only the query's scope. Reading anchor relations too would turn fewer `none` results incomparable, but only once differential tests cover it.

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
