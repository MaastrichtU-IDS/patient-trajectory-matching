# D27 (draft): chartevents by route (b), with a graph check and a raw-row check

*Status: superseded by D27 in [the engine-increment log](engine-increment-log.md), which records the integration into store 3.1 and the demo census, and, for stage 2 (value conditions), by D28. This draft is kept for its alternatives and reasoning; where they differ, the log wins. D28 departs from section 7's stage-2 sketch in two places: a condition is a unit with up to two bounds rather than one `op`, and its value relaxations are their own relaxation kind (`widen_value`) in the request.*

D16 decided that chartevents and later bulk sources take route (b). It required five things:
- bulk rows from the importer's pure claim identity;
- a deterministic sample of stores per batch through the full graph route, with a row-equality differential;
- point measurements and a series table;
- per-patient sharding, with a Merkle input digest;
- a differential that builds engine inputs once per patient.

This entry records how each is met, the alternatives, what was decided, and what stage 1 does not do.

## Decisions taken by the project lead (2026-11-09)

1. **Input digest: D20's flat digest stays the identity.** `merkle_patients.py` stays unwired: no root is recorded that nobody verifies, and no run writes one. It is wired in only when incremental appends exist. D16's Merkle requirement is deferred to then; the module and its tests are ready for it.
2. **Census on the demo: yes, in three parts.**
   - (i) The raw-row check over **every** chart row.
   - (ii) The round trip (validate, encode, decode, digest compare) on **every** claim store.
   - (iii) One full route-(a) census on every store, now that the isolation certificate is no longer computed twice.

   The sampler stays the design for full MIMIC-IV.
3. **Sample defaults for full MIMIC-IV** (1%, 2 per bucket, strata) are accepted for now, and revisited if full MIMIC-IV is ever run. A per-patient minimum stays declined.
4. **Unitless and textual rows are stage 3.** They stay excluded records until then.
5. **The series design is accepted**, with the fixes recorded under decision 6 below.
6. **Stage-2 value conditions** go in the store's match request, compiled into per-query tag concepts.
   - Tags are not part of the snapshot identity.
   - A value relaxation must widen to a tag whose events include the first tag's events.

## What the data and the code showed first

All counts here are aggregates over the open MIMIC-IV demo 2.2 (D11, D17).

- **The measurement importer cannot read the demo.** `mimic_measurement_import.read_observations` stops at 100,000 rows. The demo's `chartevents` has 668,862 rows (64.7 MB expanded, just under the importer's 64 MiB limit). Route (b) streams the file instead, and reproduces the importer's manifest exactly; a test compares the two.
- **150,051 rows are admissible measurements** under the importer's own rule (`admit`): a numeric value equal to its text, an explicit unit, and `warning = 0`.
  - 518,809 rows are unsupported. A row can have several reasons:

    | Reason | Rows |
    |---|---|
    | `MISSING_UNIT` | 506,291 |
    | `TEXTUAL_OR_QUALIFIED_VALUE` | 468,721 |
    | `MISSING_NUMERIC_VALUE` (all with a text value) | 411,388 |
    | `SOURCE_WARNING_FLAGGED` | 14,592 |
    | `SOURCE_WARNING_UNKNOWN` | 1,159 |
    | `MISSING_VALUE_TEXT` | 0 |

  - 2 rows are invalid (`VALUE_NUMERIC_DISAGREEMENT`). There are no exact duplicates.
  - Of the 257,474 rows that have a `valuenum`, 107,423 are not admitted:

    | Reason set | Rows |
    |---|---|
    | Missing unit, textual (scored text such as "15 Alert") | 57,903 |
    | Missing unit only (over 194 items) | 35,965 |
    | Warning flagged | 12,520 |
    | Missing unit, warning flagged | 806 |
    | Missing unit, warning flagged, textual | 227 |
    | Missing unit, value disagreement | 2 |

  - The rows name 1,318 item classes in 29 dictionary groupings. 292 items have admitted rows, each with exactly one unit.
  - The admitted rows make 4,753 claim stores of at most 32 claims, over 140 stays and 100 patients.
- **Route (a) for a point is the measurement graph route, not the SULO adapter.** A measurement claim (`patient-local-measurement-claim-store-1.0`) has one point variable. The bounded profile requires start + 1 ≤ end, so no bounded snapshot holds it, and `trajectory_graph_sulo.from_claims` and `ingest_rows` do not apply. The graph route for measurements is:
  - `claim_rdf.encode` with measurement fields;
  - an accept-all analysis policy;
  - `measurement_claims.select` from the graph, which decodes it and runs the importer's isolation certificate.
- **Costs, measured ad hoc, single process, on this machine.**
  - **Route (a), first measurement.** 56 stratum-chosen stores of 10 patients (1,718 claims) took 61 s, about 1.1 s a store. That code computed the isolation certificate twice.
  - **Route (a), after removing the duplicate.** 38 stores of 3 patients (1,207 claims) took 27 s with the round trip included: about **0.71 s a store, or 22 ms a claim**.
  - **Round trip.** On all 172 stores of those 3 patients, 87 ms a store.
  - **Route (b).** About 50 µs a row (23,352 rows in 1.2 s, including a streaming pass over the whole file).
  - **Raw-row check.** It reads the whole file each time: 0.75 s.
  - **Route (a) and D16's estimate.** D16's 0.2 s a claim was the SULO interval route (per-store bounded-snapshot graphs), a different route. For the measurement graph route the demo's admitted rows would take about 56 CPU-minutes. Full MIMIC-IV has on the order of 10⁸ admissible chart rows, weeks of CPU, so route (b) stands there.

## Decisions

### 1. Bulk rows are a function of the `describe()` claim

Each admitted row becomes one claim by `mimic_measurement_import.describe`, exactly as the importer builds it. The importer's ledger, duplicate rule and origin rule (earliest admitted charttime, ties broken by record id) apply. Each stay's admitted rows are cut, in record order, into stores of at most 32 (D15).

The store row is computed only from the claim, the patient clock (its origin label) and the row's source provenance (record number, row and file digests, storetime), by `claim_fields` and then `store_row`. The point's time is the origin's coordinate plus the claim variable's offset from it. The evidence carries the claim id and `cr.digest(claim)`.

Where the importer can import a file whole, a test requires our ledger, origins, claim stores and claim digests to equal the importer's. Only the store's `snapshot_id` differs, because it names our run context and the store's part, as in D15.

*Alternatives:* rows straight from the CSV, which are tied to no claim; every row through the graph, which is route (a).

### 2. What the graph route checks, and what it cannot

The route-(a) row is built by `record_fields` from the selected record, then by the same `store_row` as route (b), with the same `value_columns`, `provenance_of`, `micros` and item namespace, from the same `describe()` claim. So the differential independently exercises only these:
- `claim_fields` against `record_fields`, that is, reading a claim against reading a selected record;
- the RDF encode and decode round trip;
- validation, isolation and selection accepting the store;
- the selection's own time offsets (`patient_local.coordinate`) against route (b)'s;
- the selection's decimal rendering against `value_columns`.

It does not test the shared mapping. A review planted defects in `micros`, in the micro-unit value, in `recorded_at_us` and in the item namespace, and a full census of the graph route still found 0 mismatches. A test now records that blindness.

### 3. The raw-row check, required over every row

`patterns/mimic_chartevents_raw_check.py` imports nothing from the converter, the importer, `patient_local` or `measurement_claims`. It reads the CSV itself and recomputes each row with its own code:
- the item class, from a literal namespace and the row's `itemid`;
- the time, by `datetime` arithmetic from 0001-01-01 on `charttime`;
- the recorded time, the same way from `storetime`;
- the value spelling and the unit;
- `value_micro`, by string parsing and integer arithmetic, with no `Decimal`;
- the record number inside the event id, and the event's kind, source and point form.

It accounts for every row exactly once: as an event (a first occurrence whose item is known), as an exact duplicate, as a row naming an unknown item, or as an excluded record. Excluded records are compared as a multiset of (patient, source, item class, start).

Which rows admission keeps is the importer's rule and is not recomputed. What the check verifies is that every row is accounted for once, and that every event and every excluded record carries the raw row's own content.

A test plants each of the four defects above, and the raw check catches each one on the expected field. On 3 demo patients it checked 23,352 rows (5,435 events, 17,917 excluded records) with no difference. Integration must refuse a conversion that fails it.

### 4. The sample and the census

**The sample (the design for full MIMIC-IV).** Every claim store has a rank: the sha256 of a salt, the dataset id and its key (patient, stay, part). The part is D15's cut into stores of 32, so changing that cut changes keys and the sample. A store goes through the round trip and route (a) if any of these holds:

| Rule | Default | Purpose |
|---|---|---|
| `rate`: rank below `rate` · 2²⁵⁶ | 1% | A Bernoulli draw fixed by the hash. If a fraction *p* of stores failed the graph route independently of the hash, *n* sampled stores all miss with probability (1 − *p*)ⁿ: about 5% for *p* = 2% and *n* = 150 |
| `batch_minimum`: the lowest ranks of each batch | 2 | Every batch, the execution-store bucket of the patient, has graph-checked stores |
| `stratum`: the lowest-ranked store holding each stratum | on | Steers the graph route to variety in claim fields and RDF round trips: each (item, unit); each lexical shape of the value (sign, fraction, exponent, leading or trailing point, trailing zeros); whether a value has no exact micro-unit integer; first or later part of a stay; and the store holding the clock-origin row. Strata do not test the shared row mapping, since the raw-row check does that. The earlier `storetime` stratum was dropped: storetime feeds only the shared mapping |

The rules are free of ingest order, shard count and the store builder's part size (D23's `part_rows`). Memory holds one entry per stratum, `per_batch` entries per bucket, and the rate-drawn keys, which grow with rate × stores.

*Measured* (ad hoc, 10 demo patients): 56 of 474 stores sampled, 53 of them by stratum, with 0 mismatches. *Estimated for the whole demo:* 130 to 180 stores.

**The census (the design for the demo, decision 2).** It runs the raw-row check over every row, the round trip on every store (about 4,753 × 87 ms ≈ 7 minutes), and route (a) on every store (about 4,753 × 0.71 s ≈ 56 CPU-minutes, roughly 4 to 5 minutes on 14 workers when integration runs it). The command is `python -m patterns.mimic_chartevents_check --census`, with `--no-graph` for parts (i) and (ii) alone.

**Re-derivable, not yet "ingested".** The check recomputes each selected store's bulk rows and requires the digest the converter recorded. That shows the rows are re-derivable. The digest is taken before any merge or store mapping, so it does not show these are the rows the store holds. Integration must compare graph rows against the built store's rows for those event ids (integration notes, §3.6).

Reports say: *rows are tied to the graph by claim identity; graph-route rows equal bulk rows on the stores checked (every store on the demo, a sample beyond it); the shared row mapping is verified independently against every raw row.*

*Alternatives:*
- **A fixed count per batch**, which ignores strata.
- **A minimum of one store per patient.** For full MIMIC-IV that is about 50,000 stores.
- **A per-shard minimum**, which ties the sample to the execution plan.

### 5. Sharding

**Two streaming passes.**
- Pass one checks the header and record shape, hashes the compressed and decompressed bytes (record identifiers contain the CSV digest) and collects the item ids, since the closure precedes the first `StoreBuilder.add`.
- Pass two hashes again, refuses a changed file (`SOURCE_CHANGED_DURING_RUN:chartevents`), and spills each row to its patient's hash shard.

**Bounded open files.** At most 64 shard files are open at once (least recently used closed first, reopened to append), well under macOS's default of 256 descriptors.

**Shard sizing.** A shard is grouped in memory, and Python holds roughly 5 to 10 times its bytes. So the shard count should be about the expanded CSV size divided by the memory a shard may use. One shard suffices for the demo; full MIMIC-IV (about 30 GB expanded) wants several hundred.

**What order affects.** Shard count and the order patients come out in change nothing; a test compares 1, 3 and 7 shards, and a bounded-handle spill. Rows need not be grouped by patient. The file's row order does matter: record numbers name claims, events and stores, so a reordered file is a different input.

### 6. Point measurements and the series table

**Points need nothing new.** The engine takes points natively. The store holds them with a null end. A test builds today's store from route-(b) patients, matches points, and agrees with `exhaustive_match`. Coverage is widened to a stay's own points.

**Coverage.** Chartevents covers only stays in which it holds at least one row, admitted or not. A stay without a single chart row is not covered, so a query there is `not_comparable` (`NO_SOURCE_COVERAGE`), not `none`. This is stricter than D15's inputevents rule, which covers stays without *admitted* rows.

**Event kind.** `recorded_measurement` has no ontology class, so a measurement event carries its item class only.

**The series table.** One row per measurement event, partitioned by bucket:

| Column | Type | Meaning |
|---|---|---|
| `patient_idx` | BIGINT | as in `events` |
| `event_id` | VARCHAR | the point event |
| `item_class` | VARCHAR | the source item class (`mimic-record-item/<itemid>`), the event's direct concept |
| `start_lo`, `start_hi` | BIGINT | the event's time, copied so that a series scan needs no join |
| `value_lexical` | VARCHAR | the exported spelling (`valuenum`), as the claim holds it |
| `value_decimal` | VARCHAR | `format(Decimal, 'f')`, as `measurement_claims.select` renders it |
| `value_micro` | BIGINT, nullable | the value × 10⁶ if that is an exact integer within BIGINT, else null |
| `unit` | VARCHAR | the literal unit, with no conversion |

**Key.** `patient_idx, item_class, start_lo, start_hi, event_id`. It is total, and it orders a (patient, item) series by time. Only D20's canonical types are used.

**Fix: `value_micro` is integer arithmetic.** The first version used `Decimal.scaleb(6)` under the default 28-digit context, while `decimal_value` admits 64 characters. So '1000000000000.0000000000000000001' became exactly 10¹⁸, and '9000000000000.00000000000000000049' became 9 × 10¹⁸, both labelled exact. `micro_value` now works on `as_tuple()`:
- when *e* + 6 ≥ 0, the value is *n* · 10^(*e*+6);
- otherwise it is exact only if 10^−(*e*+6) divides *n*, and null if not;
- outside BIGINT it is **null, not a refusal**: the exact decimal text is still stored, and only comparisons on the integer are lost.

The raw-row check recomputes it independently, and the tests cover both overflow edges, ±2⁶³, exponents of ±308, '-0', and the two inputs above. The integration validator must use `micro_value`.

**`value_decimal` is exact and scale-preserving, but not a value key.** '1.5' and '1.50' differ, and '-0' stays as written. Equality, grouping and comparison use `value_micro`. Where it is null (finer than 10⁻⁶, or beyond BIGINT), they use exact decimal arithmetic. Normalising `value_decimal` was declined, because it would stop matching the selection's rendering, which the graph check compares.

*Alternatives:* values in evidence, which are not queryable; a (mantissa, exponent) pair, kept as the fallback; a 10⁻⁹ scale; series without events, which the engine could not bind.

### 7. Measurement conditions, staged

**Stage 1 (this increment).**
- Point events with item classes and groupings.
- The series table inside the snapshot identity.
- Queries by item class and grouping, with every existing rule.
- Values stored, digested and readable through a store accessor, but no value condition in `match`.
- The census and raw-row check on the demo, and the sampled design for beyond it.

**Stage 2 (decision 6).** The store's match request gains `conditions: {step_id: {unit, op, value}}`, with `op` one of `lt le eq ge gt` and an exact decimal value. This is `mixed-record-query-1.0`'s vocabulary.
- **Compilation.** Each condition becomes a per-query tag concept carried by the events of the step's concept closure whose series row has that unit and satisfies the op, compared exactly on `value_micro` or by Decimal where it is null. The step's concept becomes the tag.
- **Identity.** Tags are not part of the snapshot identity.
- **Relaxation.** A value relaxation is a `widen_concept` to a second tag whose events include the first tag's events: a threshold widened, never moved. So relaxation stays monotone, and the engine is untouched.
- **Candidates and pruning** use SQL over `series`.
- **Excluded records** of the item reach the step whatever their value.
- **The reference** applies the same conditions in Python.

**Stage 3.**
- Reviewed unit equivalence.
- Textual and categorical measurement claims (411,388 rows).
- Unitless numeric values (35,965 rows), which need a measurement-claim schema version.
- Reviewed item mappings.
- Series aggregates.
- A SULO point route.

### 8. The trajectory-graph query language

Stage 1 needs nothing. Stage 2, as decided, needs nothing in the engine either. Native value predicates would make a `trajectory-graph-1.1`; that is declined.

### 9. Item ids to concepts

- **The item class** is `mimic-record-item/<itemid>`, the same namespace as inputevents; d_items ids are unique across tables.
- **Ancestor.** The item's d_items category is its one ancestor (D14). An empty category gives none.
- **The closure** holds the item classes chart rows name, intersected with the dictionary. An unknown item has no concept and is counted.
- **Labels and basis.** These are never clinical concepts. The concept basis of a measurement's item is `claim_item_field`.

### 10. A differential with engine inputs built once per patient

`patterns/prepared_reference.py` builds the following once:
- each patient's closure-expanded events;
- a concept-to-events index;
- whether the patient shares variables;
- an excluded-records index;
- the recorders.

Per query it sends the engine only events carrying a step concept. A patient with shared variables keeps every event (D24), and a test shows that filtering such a patient turns `none` into a wrong `possible`. Its results equal `exhaustive_match` on seeded queries. On 10 demo patients it was 5 times faster (ad hoc).

It makes the same event-filter argument as the store's candidate loading, so it cannot test that argument on its own. The demo verifier therefore keeps `exhaustive_match` on a deterministic slice of its queries. The coverage rule is factored into one function, to become the reference's only copy at integration.

## Consequences and limits

- **Most chart results will be `not_comparable`.** Excluded records outnumber events by 518,809 to 150,051, so under D18 most `none` results on chart items become `not_comparable`.
- **Stage 1 answers no value question.**
- **Beyond the demo, the graph route is a sample.**
- **Measurements are ad hoc.** All are single-process, on one machine, on subsets of 3 to 10 patients. The full demo waits for D26, by agreement.
