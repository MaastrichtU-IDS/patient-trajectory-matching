# Execution store, stage 1

**Profile:** `execution-store-1.0` · **Code:** [`patterns/execution_store.py`](../patterns/execution_store.py), reference [`patterns/execution_store_reference.py`](../patterns/execution_store_reference.py), synthetic converter [`patterns/synthetic_ingest.py`](../patterns/synthetic_ingest.py) · **Manifest schema:** [`schemas/execution-store-manifest.schema.json`](../schemas/execution-store-manifest.schema.json) · **Tests:** `python -m unittest patterns.test_execution_store` · **Lock:** [`patterns/requirements-store.lock.txt`](../patterns/requirements-store.lock.txt)

*Implemented on the synthetic paediatric cohort only.* The store is the first piece of the server-side design in [engine design](engine-design.md) §4: patient events, concept postings, a terminology closure and coverage, written as immutable Parquet snapshots on embedded DuckDB. A candidate filter that stays sound under relaxation sits in front of the [trajectory-graph matcher](trajectory-graph-profile.md). MIMIC-IV ingest is stage 2. The decisions are D4 to D7 and D10 in the engine-increment decision log.

DuckDB is imported lazily inside `execution_store.py` and is pinned in its own lock file, so the rest of the engine never needs it.

## Ingest contract

A store is built from one ingest document, whatever its source:

```json
{"profile": "execution-store-ingest-1.0", "dataset_id": "…", "provenance": {"…": "free-form"},
 "closure": {"version": "…", "ancestors": {"dravet": ["dee", "epilepsy", "generalized_epilepsy"], "…": []}},
 "patients": [{"patient_id": "C001", "clock_id": "patient_C001",
               "events": [{"event_id": "ehr/C001-R0001", "kind": "condition", "source": "ehr",
                           "concepts": ["febrile_seizure"], "start": [lo, hi], "end": [lo, hi | null],
                           "evidence": {"…": "free-form provenance"}}],
               "coverage": [{"source": "ehr", "lo": 0, "hi": 0, "basis": "record_span"}]}]}
```

- **Times** are integer microseconds from the fixed origin 0001-01-01T00:00 on the patient-local calendar, as in `indexed_source_windows`. A time never depends on the request that produced it. Each patient has one clock, and coverage is on it.
- **Events.** `concepts` lists the event's direct concepts, never their ancestors. `end` is absent for a point event; `[lo, null]` means the event is still ongoing. Every event also names its `source`, which ties it to coverage.
- **Closure.** The closure is a map from each concept to all of its strict ancestors. It must be closed (every ancestor is itself a key) and transitive, and must not contain cycles.

`validate_ingest` refuses anything else with a named `IngestError` code. Examples are `DUPLICATE_EVENT_ID`, `DUPLICATE_PATIENT_ID`, `INVERTED_BOUNDS` (any lo > hi), `END_BEFORE_START`, `UNKNOWN_CONCEPT` (an event concept that is not in the closure), `CLOSURE_UNKNOWN_ANCESTOR`, `CLOSURE_NOT_TRANSITIVE`, `CLOSURE_CYCLE`, `INVALID_TIME`, `TIME_OUT_OF_RANGE` and `DUPLICATE_COVERAGE`. Nothing is written when an ingest is refused.

### Synthetic converter

`synthetic_ingest(cohort)` converts `demos/mockups/synthetic/generate.py` output. It uses the records, never the generator's `truth`, `quality` or `truth_event_id`.

| Cohort data | Ingest |
|---|---|
| Coded record | One event. Its concept is the cohort concept carrying that (system, code). The 32 deliberately local codes no concept carries are skipped and counted |
| Letter mention (clinic, referral, discharge) | One point event per mention asserted `present` of the `patient`, timed by the letter. Negated, hypothetical, possible and family mentions are skipped and counted |
| Medication order | An exposure starting at the order. A later `stopped` order for the same medication closes the latest open order before it (12 exposures); an order never closed is ongoing (84). One stop has no order to close and is skipped |
| Dispensing, measurement, diary summary, … | Point events |
| Uncoded diary entries and screening results | Skipped and counted |
| Times | The record's `occurred` earliest and latest, as written |
| Event identifier | `source/record_id`, with `#m<i>` for the i-th mention in a letter. Record identifiers alone collide once in the cohort (`C029-R0271`), which the store's duplicate check found |
| Coverage | For each source, the patient's record span: earliest to latest `occurred` over every record that source holds, converted or not, with basis `record_span`. This coverage is inferred, and gaps inside the span are not represented |
| Ancestors | Transitive closure of each concept's `parents` and `classes`. The version is the dataset identifier plus a digest of the hierarchy |

The skip counts are recorded in the ingest's `provenance.skipped`. On the committed seed there are 48 patients and 8,406 events.

Record span was chosen over follow-up for coverage. It applies to every source in the same way, including the diary, registry and pharmacy, which follow-up says nothing about. It is also derived only from recorded data. The cost is that the one documented follow-up gap is not carved out of the hospital's span.

## Tables

`build(ingest, root, threads=1)` validates the ingest, stages it as newline-delimited JSON, derives the tables in DuckDB SQL and writes them to `root/<snapshot_id>/`:

| Table | Columns | File |
|---|---|---|
| `patients` | patient_idx (dense, by sorted patient_id), patient_id, bucket | `patients.parquet` |
| `events` | patient_idx, event_id, kind, source, clock_id, start_lo, start_hi, end_lo, end_hi, ongoing, evidence (canonical JSON) | `events/bucket=k/` |
| `event_concepts` | patient_idx, event_id, concept, basis (`direct`) | `event_concepts/bucket=k/` |
| `concept_closure` | concept, ancestor, basis (`self` or `declared`), version; reflexive | `concept_closure.parquet` |
| `concept_postings` | concept, patient_idx, n, min_start_lo, max_start_hi, over closure-expanded concepts | `concept_postings.parquet` |
| `coverage` | patient_idx, source, clock_id, lo, hi, basis | `coverage/bucket=k/` |

- **Points and exposures.** A point event has a null `end_lo` and `end_hi`. An ongoing exposure has `end_lo` set and a null `end_hi`.
- **Postings.** A posting row is one (concept, patient) pair. Its `n` counts distinct events carrying the concept directly or through an ancestor, so an event coded both `dee` and `dravet` counts once for `epilepsy`. It also keeps the earliest and latest start.
- **Buckets** are the first 32 bits of sha256(patient_id) modulo 8. A patient never moves bucket when other patients are added.
- **Partitions.** Patient-keyed tables are hive-partitioned by bucket, with the bucket also written in the file. Postings are keyed by concept and the closure by concept, so each is one file: splitting a concept's list by bucket would buy nothing at this scale.

`open_store(path)` validates the manifest and exposes each table as a DuckDB view over its files. `Store.verify()` recomputes every table's count and digest from the files.

## Snapshot identity

`manifest.json` is validated against the schema and records:
- the profile;
- the dataset;
- the input digest;
- the closure version;
- the time basis;
- the bucket count;
- the canonical form;
- the builder: the sha256 of `patterns/execution_store.py` and of the manifest schema, plus the DuckDB version;
- for each table, its columns, path, partitioning, row count and canonical-content digest.

**Canonical content.** Each row is written as a compact UTF-8 JSON array of its values in column order. The lines are sorted by code point, each is terminated by a newline, and the result is hashed with sha256. Parquet bytes are never hashed, because they vary with the writer, its version and its threads. The digests are computed from the files as written, read back through a fresh connection.

**Input digest.** This is the sha256 of the canonical ingest, which is sorted by patient, event, concept and coverage period. The same content in another order has the same digest.

**`snapshot_id`.** This is the sha256 of the canonical JSON of the profile, input digest, closure version, builder artifact digests and each table's row count and content digest. The DuckDB version is recorded but is not part of the identity. If a different writer produced different rows, the table digests and therefore the identifier would change. If it produced the same rows, the snapshot is the same.

**Immutability.** Snapshots are written to a temporary directory and renamed into place. Building again into a root that already holds the identifier leaves the existing directory untouched.

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

**Why survivors get the same result.** `match` loads each candidate's events that carry any query concept through the closure, listing only those concepts. The engine only tests whether a concept is in an event's list, and it orders candidates by time and identifier. So for every relaxation set each step sees the same candidate list, and the result is identical to running on the patient's full history.

**Tests.** The differential test runs 300 seeded random queries of 2 to 4 steps (chains, trees, cycles, `during`, anchors, all scopes and occurrences, `widen_concept` and `extend_max`, budgets 0 to 1, search budgets 1 to 300) over all 48 patients with random windows.
- Each query is shaped around one anchor patient's events, so that matches occur.
- The store path must give every patient the same status as `trajectory_graph.match` run over every patient's converted, closure-expanded ingest, and the same full result for every patient it did not filter.
- No patient outside the candidate set may be exact, possible, relaxed or unresolved.

On the committed seed the 14,400 patient results are:

| Status | Results |
|---|---|
| `exact` | 531 |
| `possible` | 84 |
| `relaxed` | 117 |
| `unresolved` | 6 |
| `none` | 10,825 |
| `not_comparable` | 2,164 |
| `excluded` | 673 |

The filter keeps 4,203 of the 14,400 patient results for the matcher.

**Planted defects** show that the tests can fail. Dropping one closure row (`carbamazepine → sodium_channel_blocker`) makes `concept_closure` and `concept_postings` differ from the reference. A filter that ignores widenings drops relaxed patients, which the differential test reports.

## Comparability

When a patient's result is `none`, whether the engine returned it or the filter did, `match` checks every step against coverage. A step is covered when some source that records any of the step's concepts has a coverage period meeting the patient's window (lo ≤ window end and hi ≥ window start). The step's concepts include its widenings. A source records a concept if it holds any event carrying that concept, through the closure, anywhere in the snapshot.

If some step is not covered, the result is `{"status": "not_comparable", "reason": "NO_SOURCE_COVERAGE", "uncovered_steps": […]}`: these records could not have answered the question. An exact, possible, relaxed or unresolved result is never changed. A filtered `none` keeps `"reason": "FILTERED"`.

Limits in stage 1:
- **Inferred recorders.** Which sources record a concept is inferred from the snapshot, not declared by the source owner (D-HISTORY).
- **Overlap only.** Coverage that only partly meets the window still counts. The §4.3 cases where a source starts after the exposure, or where a relaxation needs a window wider than coverage (`unresolved`), are not yet distinguished.

`exhaustive_match` in the reference applies the same rule, written independently from the ingest.

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
- non-ASCII evidence;
- an empty partitioned table.

## Timings

These are on one laptop core, CPython 3.12 and DuckDB 2.0.0:

| Step | Time |
|---|---|
| Generating and converting the cohort | 0.3 s |
| Building the snapshot (48 patients, 8,406 events) | 0.3 s |
| Differential test (300 queries, store path and exhaustive path) | about 16 s |
| Whole suite (18 tests) | about 22 s |

## Stage 2

- **MIMIC-IV demo data.** Ingest goes through the SULO adapter's rows (D8, D9), from `inputevents`, `icustays` and `d_items`. Items are indexed under their source item classes, not clinical concepts, and the labels and reports say so (D6). Coverage comes from ICU stays.
- **Event columns.** The events table gains the D5 columns now carried inside `evidence`: status, selection state, recorded and available times, claim identifier and digest, source table, record and file digest.
- **Later.** `chartevents` (668,862 rows) comes in a later increment. Time pruning on posting summaries comes once differential tests cover it.

## Deliberately not done

- **D7.** There are no embeddings, Postgres or Rust engine. Information content per snapshot will come from posting counts, which are stored, but stage 1 computes none. An approximate-nearest-neighbour index is added only if recall at full scale needs it. A Rust engine would be a second implementation over the same Parquet, with this Python path as its reference.
- **Not in stage 1:**
  - bitmaps;
  - anchor tables;
  - series;
  - bitemporal selection;
  - partition-parallel workers;
  - streaming results.

```sh
python -m pip install -r patterns/requirements.lock.txt -r patterns/requirements-store.lock.txt
python demos/mockups/synthetic/generate.py --output /tmp/cohort.json
python -m patterns.execution_store synthetic /tmp/cohort.json /tmp/store   # prints the snapshot directory
python -m unittest -v patterns.test_execution_store
```
