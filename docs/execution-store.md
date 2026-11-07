# Execution store, stages 1 and 2

**Profile:** `execution-store-1.0` · **Code:** [`patterns/execution_store.py`](../patterns/execution_store.py), reference [`patterns/execution_store_reference.py`](../patterns/execution_store_reference.py), synthetic converter [`patterns/synthetic_ingest.py`](../patterns/synthetic_ingest.py) · **Manifest schema:** [`schemas/execution-store-manifest.schema.json`](../schemas/execution-store-manifest.schema.json) · **MIMIC converter:** [`patterns/mimic_store_ingest.py`](../patterns/mimic_store_ingest.py), demo verifier [`patterns/verify_mimic_store_ingest.py`](../patterns/verify_mimic_store_ingest.py) · **Tests:** `python -m unittest patterns.test_execution_store patterns.test_mimic_store_ingest` · **Lock:** [`patterns/requirements-store.lock.txt`](../patterns/requirements-store.lock.txt)

*Stage 1 is implemented on the synthetic paediatric cohort. Stage 2 loads the MIMIC-IV demo through the SULO adapter ([Stage 2](#stage-2-the-mimic-iv-demo-through-the-sulo-adapter)).* The store is the first piece of the server-side design in [engine design](engine-design.md) §4: patient events, concept postings, a terminology closure and coverage, written as immutable Parquet snapshots on embedded DuckDB. A candidate filter that stays sound under relaxation sits in front of the [trajectory-graph matcher](trajectory-graph-profile.md). The decisions are D4 to D7 and D10 in the engine-increment decision log, plus D14 and D15 for stage 2.

DuckDB is imported lazily inside `execution_store.py` and is pinned in its own lock file, so the rest of the engine never needs it.

## Ingest contract

A store is built from one ingest document, whatever its source:

```json
{"profile": "execution-store-ingest-1.0", "dataset_id": "…",
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

`validate_ingest` refuses anything else with a named `IngestError` code. Examples are `DUPLICATE_EVENT_ID`, `DUPLICATE_PATIENT_ID`, `INVERTED_BOUNDS` (any lo > hi), `END_BEFORE_START`, `UNKNOWN_CONCEPT` (an event concept that is not in the closure), `CLOSURE_UNKNOWN_ANCESTOR`, `CLOSURE_NOT_TRANSITIVE`, `CLOSURE_CYCLE`, `INVALID_TIME`, `TIME_OUT_OF_RANGE`, `DUPLICATE_COVERAGE`, `INVALID_SELECTION` and `INGEST_PATIENT_NOT_BOX_REPRESENTABLE`. Nothing is written when an ingest is refused.

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

## Tables

`build(ingest, root, threads=1)` validates the ingest, stages it as newline-delimited JSON, derives the tables in DuckDB SQL and writes them to `root/<snapshot_id>/`:

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

**Verification on open.** `open_store(path)` validates the manifest, exposes each table as a DuckDB view over its files, and verifies by default. Verifying means recomputing every table's count and content digest from the files, which must equal the manifest's, whose identifier must equal its own digest. A tampered, missing or unreadable file is refused (`SNAPSHOT_CONTENT_MISMATCH`, `SNAPSHOT_UNREADABLE`). `open_store(path, verify=False)` skips the check explicitly, and `match` then reports `verified: false`.

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
- the builder: the sha256 of `patterns/execution_store.py` and of the manifest schema, plus the DuckDB version;
- for each table, its columns, path, partitioning, row count and canonical-content digest.

**Canonical content.** Each row is written as a compact UTF-8 JSON array of its values in column order. The lines are sorted by code point, each is terminated by a newline, and the result is hashed with sha256. Parquet bytes are never hashed, because they vary with the writer, its version and its threads. The digests are computed from the files as written, read back through a fresh connection.

**Input digest.** This is the sha256 of the canonical ingest, which is sorted by patient, event, concept and coverage period. The same content in another order has the same digest. It covers the provenance, including the selection mode.

**`snapshot_id`.** This is the sha256 of the canonical JSON of the profile, input digest, closure version, builder artifact digests and each table's row count and content digest. The DuckDB version is recorded but is not part of the identity. If a different writer produced different rows, the table digests and therefore the identifier would change. If it produced the same rows, the snapshot is the same.

**Immutability.** Snapshots are written to a temporary directory and renamed into place. Building again into a root that already holds the identifier verifies the existing snapshot's files. If they still hash to the identifier, the directory is left untouched and reused; if not, the build is refused.

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

The scope is the only constraint every step carries, so reaching over-approximates: it may flag a result the excluded records could not in fact have changed, but never the reverse. If no step reaches an excluded record, the result stands. Otherwise, in this order:

| Case | Result | Why |
|---|---|---|
| A `first` or `last` step reaches an excluded record that could displace its bound event: `start_lo` at or before the bound event's `start_hi` for `first`, `start_hi` at or after its `start_lo` for `last` (on any status with a binding, or the binding of an `unresolved` result's relaxed match) | `not_comparable` | The step's target may be the excluded record, so the binding found may not be a binding of the query at all. Carrying it beside an `unresolved` status would present as a match something that may not be one |
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
- `verified`: whether the store's files were verified on open.
- `coverage_rule`: `window_overlap_source_concepts_v1`, and `exclusion_rule`: `excluded_records_scope_v1`.
- `labels`: the manifest's labels, so a result read alone says what its concepts are.
- `context`, with:
  - the `snapshot_id`;
  - the sha256 of the canonical query and of the windows;
  - the sha256 of the engine artifacts `patterns/execution_store.py`, `patterns/trajectory_graph.py` and `patterns/temporal_stn.py`;
  - the coverage and exclusion rules;
  - the verified flag;
  - the labels.
- `context_id`: the sha256 of that context. It is the key under which a result can be cached or audited, as engine design §4.5 asks.

## Determinism

- Building with one thread and with four threads gives the same `snapshot_id` and table digests.
- Rebuilding from a shuffled ingest gives the same identifier and leaves the existing directory untouched.
- The patient index comes from sorted identifiers, and buckets come from a hash.

The reference rebuilds every table in pure Python from the ingest, with no code shared with the store. It must equal the store row for row on the synthetic cohort and on a hand-built fixture. The fixture has:
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
| Building the snapshot (48 patients, 8,365 events) | 0.3 s |
| Differential test (300 queries, store path and exhaustive path) | about 15 s |
| Whole suite (21 tests) | about 25 s |

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

The snapshot identifier is `4169368ee7ba9f9adc805f14cd55953960a40628372ba4d828d8a74797d5c46c`. It changes whenever the converter or the store builder changes, or the rows they produce do.

**Raw times.** Every one of the 10,980 events has its start, end and recorded time checked against its raw source row, to the microsecond, with no mismatch.

**Differential.** The verifier runs 500 seeded queries of 2 to 4 steps over source item classes and their groupings. Each query is shaped around one anchor patient's events, with gaps in minutes, first and last occurrences, widenings, `extend_max`, cycles and `during`. Every query runs over all 100 patients three ways:
- the store path;
- `exhaustive_match` over the whole ingest;
- the engine over each patient's adapter rows directly, without the store mapping or either comparability rule.

Of the 50,000 patient results, none mismatch on either comparison, and the candidate filter drops no match. Against the adapter rows, decisions are compared, not only statuses: binding, relaxations, cost and certainty, for the 1,909 results whose binding survives. Where the store's rules changed a result, it must follow from the admitted-only engine result as the table above says.

| Status | Results |
|---|---|
| `exact` | 1,291 |
| `relaxed` | 24 |
| `unresolved` (594 of them `ADMISSION_EXCLUDED_RECORDS`) | 596 |
| `none` | 10,262 |
| `not_comparable`, `ADMISSION_EXCLUDED_RECORDS` | 27,810 |
| `not_comparable`, `NO_SOURCE_COVERAGE` | 7,508 |
| `excluded` | 2,509 |

The excluded-records cases, out of the admitted-only engine's 1,644 exact, 818 relaxed and 45,027 `none` results:

| Case | Results |
|---|---|
| `none` to `not_comparable` | 27,257 |
| `first` or `last` target displaced, to `not_comparable` (353 exact, 200 relaxed) | 553 |
| `relaxed` to `unresolved` with the match beside it | 594 |
| `possible` made incomplete | 0, since every recorded MIMIC time is exact |

The 24 relaxed results that stand use 26 concept widenings; the 594 relaxed matches carried beside `unresolved` use 584 widenings and 31 extended maxima. The filter keeps 16,210 of the 50,000 patient results for the matcher.

Most `none` results become `not_comparable`, and most relaxed ones `unresolved`. That is what the subset costs: 99 of the 100 patients have dropped rows, mostly of common items, and under scope `any` a dropped record at any time could have bound a step.

**Timings**, on one laptop with CPython 3.12 and DuckDB 2.0.0:

| Step | Time |
|---|---|
| Admission and claim stores | 0.7 s |
| 424 stores through isolation, acceptance, the graph route and `ingest_rows`, 8 worker processes | 65 s (about 480 CPU-seconds) |
| Mapping, excluded records and coverage | 0.3 s |
| Building the snapshot | 1.0 s |
| Opening and verifying it | 0.2 s |
| Differential (500 queries, three paths) | 231 s |

### Chartevents path

This is not built. `chartevents` has 668,862 demo rows. At about 0.2 CPU-seconds per claim, route (a) would take about 37 CPU-hours for the demo alone, and more for full MIMIC-IV. The planned path:
- **Route (b).** Bulk rows come from pure `describe()` identity, the measurement importer's claim description, without the graph route. For each batch, a deterministic sample of stores goes through the full graph route, and a differential requires its store rows to equal the bulk rows exactly. The report must then say that bulk rows are tied to the graph by claim identity and checked on a sample, not each validated through the graph.
- **Measurements are points.** A chart value is a point event with a value and unit, not an interval. The values go into a series table keyed by patient, item and time, beside the events table, so that the matcher's concept postings stay small.
- **Per-patient sharding.** Ingest is staged one patient shard at a time. The input digest becomes a Merkle digest over per-patient digests, so a shard can be rebuilt and checked without rehashing the whole ingest.
- **The differential at scale.** The exhaustive and adapter-row engine inputs are built once per patient and reused across queries, not rebuilt for every query.

### Next

- **`chartevents`**, by the path above.
- **Time pruning** on the posting summaries waits until differential tests cover it.
- **Per-bucket joins.** The candidate patient and event lookups are `IN` lists over the candidate set today. At full scale they become joins against a candidate table, one bucket partition at a time.
- **Event columns.** The D5 columns are still inside `evidence`: status, selection state, recorded and available times, claim identifier and digest, source table, record and file digest.
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
