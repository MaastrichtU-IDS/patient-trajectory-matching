# D27 (draft): chartevents by route (b), with a sampled graph check

*Status: draft for the engine-increment log. Code on `feature/chartevents-route-b`, not yet integrated into the store (it waits for store 3.0, D26). Integration hooks: [chartevents-integration-notes.md](chartevents-integration-notes.md).*

D16 decided that chartevents and later bulk sources take route (b). It also required five things: bulk rows from the importer's pure claim identity; a deterministic sample of stores per batch through the full graph route, with a row-equality differential; point measurements and a series table; per-patient sharding with a Merkle input digest; and a differential that builds engine inputs once per patient. This entry records how each is met, the alternatives, and what stage 1 does not do.

## What the data and the code showed first

All counts here are aggregates over the open MIMIC-IV demo 2.2 (D11, D17).

- **The measurement importer cannot read the demo.** `mimic_measurement_import.read_observations` stops at 100,000 rows. The demo's `chartevents` has 668,862 rows and expands to 64.7 MB, just under the importer's 64 MiB limit. So route (b) needs its own streaming reader. It produces the importer's manifest exactly (a test compares them), with no row limit.
- **Only 150,051 rows are admissible measurements.** These are rows the importer's own admission rule (`admit`) accepts: a numeric value equal to its text, an explicit unit, and `warning = 0`. The other rows break down as follows:
  - 518,809 unsupported rows. A row can have several of these reasons:
    - 411,388 have no numeric value (textual items);
    - 468,721 are textual or qualified;
    - 506,291 have no unit;
    - 14,592 are flagged with a warning;
    - 1,159 have an unknown warning.
  - 2 invalid rows.
  - No exact duplicates.

  Of the 257,474 rows with a `valuenum`, 107,423 are not admitted:
  - 57,903 scored texts such as "15 Alert";
  - 35,965 rows whose only defect is a missing unit, over 194 items;
  - about 13,500 flagged with a warning.

  Rows name 1,318 item classes in 29 dictionary groupings. 292 items have admitted rows, each with exactly one unit. The admitted rows make 4,753 claim stores of at most 32 claims (140 stays, 100 patients).
- **Route (a) for a point is the measurement graph route, not the SULO adapter.** Measurement claims (`patient-local-measurement-claim-store-1.0`) have one point variable. The bounded profile requires start + 1 ≤ end, so no bounded snapshot holds them, and `trajectory_graph_sulo.from_claims` / `ingest_rows` do not apply. The repository's graph route for measurements runs through four steps:
  - `claim_rdf.encode` with measurement fields;
  - the importer's isolation certificate (`claim_isolation.check(..., local=True, measurement=True)`);
  - an accept-all analysis policy;
  - `measurement_claims.select` from the graph. It decodes the graph, so a lossy round trip changes the store digest and the policy no longer matches.
- **Route (a) is cheaper than D16 estimated, but still not for full MIMIC.** Measured single-process on this machine: 56 demo stores (1,718 claims) took 61 s, about **1.1 s a full store, or 36 ms a claim**.
  - For the demo's 150,051 admitted rows, that is about 1.5 CPU-hours. D16's "37 CPU-hours" assumed every one of the 668,862 rows at 0.2 s.
  - Full MIMIC-IV has on the order of 10⁸ admissible chart rows. At this rate that is weeks of CPU, so route (b) stands.
  - For the demo alone, a **census** (every store through route (a)) would take about 7 minutes on 14 workers. This is offered as an option (open question 2).
- **Route (b) is cheap.** 64,550 rows of 10 patients took 2.3 s, including the second streaming pass over the whole file. That is about 36 µs a row, so about 25 s for the demo.

## Decisions

### 1. Bulk rows are a function of the `describe()` claim

Each admitted row becomes one claim by `mimic_measurement_import.describe`, exactly as the importer builds it:
- the importer's ledger, duplicate rule and origin rule (earliest admitted charttime, ties broken by record id);
- each stay's admitted rows cut, in record order, into stores of at most 32 (D15's rule).

The store row is computed from three things only: the claim, the patient clock (origin label) and the row's source provenance (record number, row and file digests, storetime). The steps are `claim_fields(claim, clock)` and then `store_row(fields, provenance)`.
- The point event's time is the origin's coordinate plus the claim variable's offset from it. This is the same arithmetic as the interval route's `origin_us + start_us`.
- The evidence carries the claim id and `cr.digest(claim)`, which is the digest the graph route's claim support carries.

Where the importer can import a file whole (no stay over 32 rows, at most 16 items), a test requires our ledger, origins, claim stores and claim digests to equal the importer's. Only the store's `snapshot_id` differs: it names our run context and the store part, as D15's converter already does.

*Alternatives:*
- **Rows straight from the CSV.** Cheaper still, but not tied to any claim, so the graph check would compare unrelated code paths.
- **Every row through the graph.** This is route (a), costed above.

### 2. The sample: three rules, order-free

Every claim store has a rank: sha256 of a salt, the dataset id and its key (patient, stay, part). A store goes through route (a) if any of these holds:

| Rule | Default | Why |
|---|---|---|
| `rate`: rank below `rate` · 2²⁵⁶ (a Bernoulli draw fixed by the hash) | 1% | Catches defects spread at random. If a fraction *p* of stores were defective independently of the hash, *n* sampled stores all miss with probability (1 − *p*)ⁿ: about 5% for *p* = 2% and *n* = 150, and 0.05% for *p* = 5% |
| `batch_minimum`: the lowest-ranked stores of each batch | 2 per batch | Every batch is backed by checked stores. **A batch is the execution-store bucket of the patient** (8 buckets), which is also a leaf group of the two-level Merkle tree (decision 4). So every bucket root covers rows that were checked against the graph |
| `stratum`: for each stratum, the lowest-ranked store holding it | on | Catches systematic defects, which are keyed to a value, not random. The strata are each (item, unit); each lexical shape of the value (sign, fraction, exponent, leading or trailing point); storetime present or missing; first or later part of a stay; and whether the store holds the patient's clock-origin row |

Each rule is a function of the whole set of stores. None depends on ingest order, shard count or part size, and each is computed streaming, in memory bounded by the number of strata and batches. The run is two-phase:
1. The bulk pass offers every store's key and strata, and records the digest of the rows it emitted.
2. The selected stores' patients are re-read. Their claim stores are rebuilt and their bulk rows recomputed, and these must have the recorded digest, so the rows checked are the rows ingested. Then route (a) runs on them, and the differential compares every field of every event, its evidence and its series row. It reports only the names of differing fields, never values.

*Measured on 10 demo patients* (a deterministic subset; an ad hoc run, not a committed report): 56 of 474 stores were sampled (53 by stratum, 12 batch minima, 3 by rate), covering 1,718 claims. 56 of 56 were equal, with no digest mismatch, and 56 isolation certificates verified. *Estimated for the whole demo:* strata grow to about 300 (292 item–unit pairs plus shapes), so about 130 to 180 stores, or 2.5 to 3.5 minutes single-process.

*What every row gets, and what only the sample gets.*
- **Every row** is admitted by the importer's `admit`, described by the importer's `describe()`, and validated by the store's ingest.
- **Only sampled stores** get the RDF round trip, the isolation certificate and the selection.
- So a defect confined to a few rows in unsampled stores, which no stratum isolates, would not be caught by the sample. It could only be caught by construction (shared code) or by a census.

Reports must say: *rows are tied to the graph by claim identity and checked on a sample, not each validated through the graph.* `summarise` writes that sentence into the check's output.

*Alternatives:*
- **A fixed count per batch**, which ignores strata.
- **A minimum of one store per patient.** For full MIMIC-IV that is about 50,000 stores, or about 15 CPU-hours. Declined as a default.
- **A per-shard minimum.** It ties the sample to the execution plan.
- **A census.** Affordable for the demo only.

### 3. Sharding

The file is read twice, streaming:
- **Pass one** checks the header and every record's shape, hashes the compressed and decompressed bytes (record identifiers contain the CSV digest, so they are needed before any claim) and collects the item ids. The closure must be known before `StoreBuilder` takes its first patient.
- **Pass two** hashes again, refuses a file that changed between the passes (`SOURCE_CHANGED_DURING_RUN:chartevents`), and spills each row to one of `shards` files by a hash of its patient.

Each shard is then grouped by patient. Python holds one shard, and every patient is one batch. The order of patients does not matter: the store's identity and the Merkle root are order-free, and a test shows identical patients, series and store summaries at 1, 3 and 7 shards. A file sorted any way gives the same result (the fixture interleaves patients).

### 4. A Merkle tree over patients for the input digest

The construction is RFC 6962 / RFC 9162, the Certificate Transparency tree (`patterns/merkle_patients.py`):
- **Leaves** are sha256(0x00 ‖ patient_id ‖ TAB ‖ patient digest), in code-point order of patient id.
- **Inner nodes** are sha256(0x01 ‖ left ‖ right), split at the largest power of two.
- **The empty tree** is sha256("").
- **Two-level form:** one tree per store bucket, then a tree over the 8 bucket roots, with empty buckets as the empty root.
- **The input digest** is sha256 of `merkle-patients-1 TAB form`, the caller's canonical header, the root and the patient count.

It ships with a streaming form (O(log n) hashes, for DuckDB's `ORDER BY` output) and inclusion proofs. The tests pass the Certificate Transparency reference vectors for 1 to 8 leaves and check the streaming, batch and naive roots on many sizes. They also check that adding a patient changes only its bucket's root and the top.

- **Why RFC 6962.** Domain-separated leaves and nodes, and no duplicated last node, so distinct leaf lists cannot share a root (unlike Bitcoin's tree). It is also a public specification that another implementation can be checked against.
- **Why two levels.** Buckets are already the store's partitions. An append rehashes one bucket and the top, and each bucket's root can be computed by whoever holds it. This is the "bucket-level roots" item the store's *Next* list deferred (D20).
- **Why the patient digest is the caller's.** The module never sees the store's canonical form, so the store and its reference each supply their own digests.

*Alternatives:*
- **Keep D20's flat sorted list.** Equally order-free, but it has no proofs and no incremental rehash.
- **A sparse Merkle tree keyed by hash(patient id).** It supports non-membership proofs, but costs 256 hashes a leaf.
- **Trees per execution shard.** They put the shard plan into the identity.

*Identity.* Switching the input digest changes every snapshot id. D26 changes canonical forms anyway, so the switch should land in the same integration, so that identities change once (open question 1). Under D17, a Merkle root is a digest of row-level data: it is committed for the open demo and not for credentialed MIMIC-IV.

### 5. Point measurements in the engine and the store

Nothing new is needed in the engine. Points are a native event form: there is no `end`, the end is the start, and a point cannot be `proper`. The point-forest method applies when every step is a point.

The store contract already accepts points (null `end_lo` and `end_hi`). A test builds today's store from the route-(b) patients and matches two queries, where the store agrees with `exhaustive_match` in status and, where it ran the engine, in full:
- a first lab followed by a rate within two hours;
- a grouping followed by an item that only excluded rows hold, which is `NO_SOURCE_RECORDS_CONCEPT`.

The other store rules carry over:
- **Time pruning (D25)** reads start summaries, which a point fills.
- **Coverage** is widened to a stay's own points, as for inputevents. There are 52 such points in the 10-patient subset.
- **Excluded records (D18)** carry item class and charttime.

The event kind `recorded_measurement` has no class in the bounded-interval ontology, so a measurement event carries only its item class, with no structural concept. Adding an ontology class touches report-bound ontology files, so it is left to stage 3.

### 6. The series table

There is one row per measurement event, beside `events`, partitioned by bucket:

| Column | Type | Meaning |
|---|---|---|
| `patient_idx` | BIGINT | as in `events` |
| `event_id` | VARCHAR | the point event |
| `concept` | VARCHAR | the item class (the event's direct concept) |
| `start_lo`, `start_hi` | BIGINT | the event's time, copied, so a series scan needs no join |
| `value_lexical` | VARCHAR | the exported spelling (`valuenum`), as the claim holds it |
| `value_decimal` | VARCHAR | exact decimal text, `format(Decimal, 'f')`, as `measurement_claims.select` renders it |
| `value_micro` | BIGINT, nullable | the value × 10⁶ when that is an exact integer within BIGINT; otherwise null |
| `unit` | VARCHAR | the literal unit, with no conversion |

- **Key:** `patient_idx, concept, start_lo, start_hi, event_id`. This is total, since event ids are unique within a patient, and it orders a (patient, item) series by time for content digests and scans.
- **Types:** only D20's canonical types. DOUBLE and DECIMAL are refused, because their text forms are not canonical and doubles round.
- **`value_micro`:** gives exact integer comparison in SQL. It was non-null for all 14,957 values in the 10-patient subset. A value it cannot hold (more than six decimals, or beyond BIGINT) keeps its exact decimal, and any comparison on it must fall back to exact decimal arithmetic.
- **Postings stay small.** They remain one row per (concept, patient), and values never enter them.

*Alternatives:*
- **Values in the event's evidence.** They are not queryable, and D26 moves evidence out of `events`.
- **A (mantissa, exponent) pair.** Exact for every value, but SQL comparison needs alignment. Kept as the fallback if `value_micro`'s gaps matter.
- **A 10⁻⁹ scale.** It narrows the range.
- **A series-only representation, with no events.** The engine binds events, so it would see no measurement.

### 7. Measurement conditions: values and units, staged

Full value-based conditions are too big for one increment alongside the ingest, so the work is staged.

**Stage 1 (this increment).**
- **Ingest:** chartevents rows become point events with source item classes and groupings, plus the series table, inside the snapshot identity and content-verified.
- **Queries** match by item class and grouping, with every existing relation, relaxation, coverage and exclusion rule.
- **Values** are stored, digested and readable through a store accessor (for display and audit), but `match` takes no value condition.
- **Proof:** the sampled graph check, the Merkle digest, and the prepared-reference differential.

**Stage 2: value conditions without changing the engine.**
- **The request.** The store's match request (not the engine query) gains `conditions: {step_id: {unit, op, value}}`. `op` is one of `lt le eq ge gt` and `value` is an exact decimal string: the predicate vocabulary of `mixed-record-query-1.0`, so the two profiles agree.
- **Compilation.** The store compiles each condition into a per-query tag concept:
  - the events of the step's concept closure whose series row has that unit and satisfies the op (exactly: on `value_micro`, or by Decimal where it is null) carry the tag;
  - the step's concept is replaced by the tag;
  - a value relaxation (a threshold moved, with a cost) becomes a `widen_concept` to a second tag. So the engine, its reference and the trajectory page are untouched.
- **Candidates and pruning:** SQL over `series`, with the qualifying rows' earliest and latest start.
- **Exclusions:** an excluded record of the item reaches the step whatever its value, since admission dropped it and its value is not trusted. This is conservative.
- **The differential** applies the same conditions in Python in `PreparedReference`.
- *Declined for stage 2:* native value predicates in the engine. The semantics would be cleaner, but it changes the engine profile, the reference, the page parity test and every prepared query.

**Stage 3.**
- **Reviewed unit equivalence and conversions** (a review question, D-CLINICAL).
- **A textual or categorical measurement claim profile.** It would turn most of the 411,388 textual rows from excluded records into events, and with them most of the `not_comparable` results D18 will produce.
- **Unitless numeric values** (35,965 rows) with an explicit `unit: none`. This needs a measurement-claim schema version.
- **Reviewed item-to-concept mappings** through `reviewed_measurement_mappings` (`measurement-record-item/` targets).
- **Series aggregates**, such as "two consecutive readings below".
- **A point route through the SULO adapter**, which needs a bounded point profile and ontology class.

### 8. What the trajectory-graph query language needs

Stage 1 needs nothing: the query is `trajectory-graph-1.0` unchanged. Stage 2, as proposed, needs nothing in the engine either: conditions live in the store request and compile to concepts. A native value predicate on steps would be a `trajectory-graph-1.1`, which is declined above.

### 9. Item ids to concepts

- **The item class is `mimic-record-item/<itemid>`**, the namespace inputevents uses. d_items item ids are unique across tables (`linksto` says which), so one closure serves both sources.
- **The d_items category is its one ancestor** (D14, `source_dictionary_grouping`). An item with an empty category has none.
- **The closure** holds the item classes chart rows name, admitted or excluded, intersected with the dictionary. An unknown item has no concept and is counted; the demo has none.
- **Labels:** `concept_basis: source_item_class` and `clinical_mapping_verified: false`. The concept basis of a measurement's item is `claim_item_field`, since the measurement claim carries the item as a field, not as a class fact.

### 10. A differential that builds engine inputs once per patient

`patterns/prepared_reference.py` builds the following once per ingest:
- each patient's closure-expanded engine events;
- a concept-to-events index;
- the shared-variable flag;
- a concept-to-excluded-records index;
- the recorders map.

Per query it sends the engine only events carrying a step concept. A patient with shared variables keeps every event, because an unbindable event can link two bindable ones (D24). A test shows a filtered patient going from `none` to a wrong `possible`, and that mutation fails the test. It then applies the same coverage rule and calls `admission_check` unchanged.

Its results equal `exhaustive_match` on seeded queries over the synthetic cohort, a shared-variable fuzz cohort and the chartevents fixture. On 10 demo patients it was 5 times faster: 22 ms against 110 ms a query, on an ad hoc run with 0 mismatches.

For chartevents there are no adapter rows for the bulk: that is what route (b) means. So the three-way differential becomes three comparisons:
- the store against the prepared reference, on every patient;
- route-(a) rows against bulk rows, on the sample;
- inputevents adapter rows, as before.

## Consequences and limits

- **Many chart results will be `not_comparable`.** Most rows of the 1,318 items are excluded records: 518,809 against 150,051 events. Under D18, most `none` results on chart items will be `not_comparable`. As in D18, that is honest about what a numeric subset can rule out, and stage 3's textual profile is the lever.
- **Stage 1 does not answer value questions.** "MAP below 65" is stage 2.
- **Rows outside the sample are not each graph-checked** (decision 2).
- **Measured on part of the demo only.** All measurements are single-process on one machine, over a 10-patient subset. The whole demo waits for D26, by agreement.
