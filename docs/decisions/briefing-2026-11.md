# Briefing for the project owner, November 2026

This is a reader's guide to [the engine increment decision log](engine-increment-log.md), decisions D1 to D25. It was written on 2026-11-09 and summarises; where it and the log differ, the log is right. Numbers come from the log, [the execution store page](../execution-store.md) and the committed reports in `verification/`. Each is labelled with its source. Part 7 describes work still in progress, and is the part to update.

## 1. Summary

From 2026-11-06 you delegated three things, and every decision inside them: package the engine, build an execution store, and build a SULO adapter for the trajectory-graph engine. All three exist and are merged (PRs #120 to #136).
- The engine builds as a wheel. Nothing is published.
- The adapter turns bounded, patient-local and claim sources into the engine's input and checks each answer against the source.
- The store is an embedded DuckDB database writing immutable Parquet snapshots. It builds and verifies at 1,000,000 synthetic patients under a 4 GB database limit.
- Matching at 300,000 patients meets the speed target on one machine, in Python, with 8 or 14 worker processes. No Rust was built.

All scale evidence is synthetic data on one machine. Nothing is deployed, and nothing clinical has been reviewed.

## 2. What changed, by area

**Packaging (D1 to D3, #120).** A `pyproject.toml` builds a wheel with hatchling, in place. One path locator sits behind the existing `ROOT`, so installed and checkout code read the same files. Reports that bound the changed file were regenerated, and a v26 release manifest was cut. The import name `patterns` is generic and may collide in a shared environment. Renaming was left for later.

**Execution store (D4 to D7, D19, D20, D23 to D25; #122, #127, #129, #134, #135, #136).**
- *Stage 1.* DuckDB writes Parquet snapshots. A snapshot's identity is a digest of its canonical rows, not of the Parquet bytes, which vary with the writer. A pure-Python reference builds the same tables and the tests compare them.
- *2.0 streaming (D20).* The scaling benchmark (D19) showed 1.0 could not build 100,000 patients (killed at 40.5 GiB). Version 2.0 adds patients one at a time and digests as a stream, so Python holds one patient. It also has two verification tiers: a file-checksum check on open (the default), and a full content check.
- *The 1M build (D23).* DuckDB ran out of memory in one large statement. The derive now works in parts of about 2,000,000 staged rows. The same input gives the same digests.
- *2.1 shared variables (D24).* A shared variable means two events have the same start or end time, which a plain box of bounds cannot express. The store now keeps them. This needed the engine change below.
- *Verification memory (D24).* Content checking sorted a whole bucket at once. It now sorts one range of patient indices at a time, so its memory no longer grows with the cohort.
- *Time pruning (D25).* Posting summaries record, per patient and concept, the earliest and latest start. A candidate whose summaries prove the engine would answer `none` is skipped before its events are loaded, and gets the same `none`. It was kept under a rule fixed before measuring.

**Matcher and engine (D13, D21, D22, D24; #123, #130, #131, #133, #135).**
- *D13.* The engine gave each step its own time variables, so one event could take two times and give a false "possible". Variables are now keyed by source variable.
- *D21 rule and outcome.* The rule was fixed before measuring: cheap wins first, then parallel Python, then Rust only if both missed the target, and only if Rust passed a 10x speed gate. Chunked loading, prepared queries and parallel workers did not meet the target at 30 queries x 3 repeats (14 workers: p95 15.3 s against 10 s). The next step was the engine kernel. It replaces a call per event pair with a closed-form test: an algebraic bound on one end of the other event's time range, exact on integer times. A frozen copy of the old engine agrees on 11,750 comparisons. The target was then met at 14 workers, so no Rust prototype was built.
- *Strict intervals (D22, D24).* The source requires start before end, strictly. The engine allowed equal. An optional per-event `proper` flag now makes it strict. The adapter marks interval events proper, so the engine's model equals the source's. The trajectory page sends none and is unchanged.

**SULO adapter (D8, D9, D22, D24; #121, #132, #135).** It reads `bounded_intervals.Snapshot`, and the graph route is canonical. Each patient's variables are tightened to bounds. A patient whose source constraints a box cannot represent is refused, not guessed at. Every binding is checked against the source network. Shared source variables were refused at first (D9), passed to the engine in D22, and decided exactly since D24. Non-box source networks are still refused. Accepting them needs the engine to take edges between events.

**MIMIC demo stages (D11, D14 to D18; #126).** The open MIMIC-IV demo (100 patients, 20,404 `inputevents` rows) is fetched locally, checksum-verified and never committed. Every admitted row goes through the canonical claim route, in stores of at most 32 claims. The store indexes source item classes, not clinical concepts. Each class takes its dictionary category as an ancestor, labelled `source_dictionary_grouping`. Admission keeps 10,980 rows. The dropped rows are kept as excluded records, so a step they might have changed is reported `not_comparable`, not `none`.

## 3. Key numbers

All values are synthetic data on one machine (M4 Pro, 64 GB), with a 4 GB DuckDB limit and one DuckDB thread. Sources: **S** = `verification/execution-store-scaling-report.json` (D25 run; the build rows are D24's, which the D25 run reproduced within about 1%), **M** = `verification/matcher-comparison-report.json`.

| Measure | 100,000 | 300,000 | 1,000,000 | Source |
|---|---|---|---|---|
| Build time, add to publish | 495 s | 1,482 s | 4,968 s (83 min) | S |
| Build peak memory | 5.14 GiB | 8.08 GiB | 8.14 GiB | S |
| Snapshot on disk | 1.34 GB | 3.86 GB | 3.51 GB (smaller than at 300,000 because of replication; linear growth would give about 13 GB) | S |
| Content verification: time, peak memory | 85 s, 2.40 GiB | 252 s, 2.42 GiB | 819 s, 2.33 GiB | S |
| Open with the file check, warm | 0.47 s | 1.30 s | 1.18 s | S |
| Single-process `match()`, p50 / max | 1.0 / 18.6 s | 2.5 / 59.9 s | 7.5 / 245 s (ten queries) | S |

Matching at 300,000 patients, full detail, 30 queries x 3 repeats. The target is p95 at most 10 s, maximum at most 30 s, and query process under 4 GB.

| Workers | p95 (range over repeats) | Max | Query process peak | Cost per candidate, end to end | Source |
|---|---|---|---|---|---|
| 8 | 8.41 s (8.41 to 8.45) | 10.25 s | 0.85 GiB | 40.4 µs | M, `confirmation` |
| 14 | 7.81 s (7.01 to 8.41) | 9.23 s | 0.83 GiB | 36.1 µs | M, `confirmation` |

The single-process matcher (arm B) costs 279.6 µs per candidate end to end at 300,000 in the ten-query main run, and 189.9 µs in the 30-query confirmation (M and log D25). Before D21 it was 760 µs in that run's own measurement (log D21). Every per-query result digest equals the old matcher's (log D25). The MIMIC demo differential, 500 queries and 50,000 results, has 0 mismatches (log D25).

Cautions on the matching rows:
- The 8-worker p95 was 10.56 s in the D24 run, which missed the target. It is 8.41 s in D25 with pruning. The two runs are on different days, and the log does not separate pruning from run-to-run spread.
- The 14-worker p95 moved from 7.9 s (D21) to 9.1 s (D23) on identical results, with no established cause.
- Pruning does not help every query. The slowest ten-query case (55 s single-process) has scope `any` and open relations, which prune nothing.

## 4. Decisions you may want to revisit

1. **DuckDB and Parquet, not Postgres, embeddings or Rust (D4, D7).** *Alternatives:* Postgres with pgvector, SQLite, a Rust engine first. DuckDB needs no server, handles the scale on one machine, and Parquet moves to other engines without a format migration. Embeddings and Postgres stay deferred. Postgres is still planned for jobs, results and audit.
2. **Stay in Python at the D21 outcome.** *Alternative:* build the Rust prototype. The rule was fixed before measuring, and it was met only after the kernel work and only narrowly at 8 workers. The margin at 14 workers is 16 to 21% on one machine (log D21). A larger cohort, a slower machine or a stricter target could reopen this.
3. **Open the store with a file check by default (D20).** *Alternative:* a full content check on every open. Content checking runs at about 440,000 rows a second, which is 819 s at 1,000,000 patients. It always runs at build time and in CI. Skipping it on open trades safety for speed.
4. **Every demo row through the canonical route, in stores of at most 32 claims (D15).** *Alternative:* a bulk route. Splitting changes nothing the store reads, and the demo took 62 s on 8 processes. It uses a caller-declared accept-all policy, which is an analysis choice, not clinical review.
5. **Conservative `not_comparable` for excluded records (D18).** *Alternative:* judge only by window overlap, which is unsound under scope `any`. On the demo's 50,000 differential results, 27,257 `none` become `not_comparable`. The log calls this honest about how little a continuous-segment subset can rule out, not a defect.
6. **Dictionary categories as ancestors of source item classes (D14).** *Alternatives:* no ancestors, or ancestors from a clinical terminology. The categories are the source's own grouping and need no reviewer. They are never called clinical concepts.
7. **Bulk sources use route (b) with a sampled graph check (D16).** *Alternative:* every row through the graph, about 37 CPU-hours for the demo's chartevents alone. Bulk rows would be tied to the graph by claim identity and checked on a sample. Reports must say so.
8. **Aggregate counts only for credentialed data (D17).** The open demo's reports commit digests of row-level data. For full MIMIC-IV they will not, unless the data owner confirms the agreement allows it.
9. **`proper` as a per-event flag (D24).** *Alternative:* a profile-level or query-level setting. Strictness describes a record, and a patient's network is solved once whatever the query. A profile option would have needed a new profile identifier for every caller.
10. **Keep the ingest profile identifier, add a normal form (D25).** *Alternatives:* a 2.1 ingest identifier, or refusing redundant forms. The MIMIC input digest then does not change for a label alone. `box_representable` is not normalised, so absent and `true` still give different digests.
11. **Merges wait for a settled, complete, green check run (D12).** This followed a merge of #116 with one check still pending. The cost is waiting. The alternative is merging on the first green poll.

## 5. Known limits and open items

- **Synthetic data, replicated.** Beyond 10,000 patients the cohort replicates a distinct pool of 10,000. Match outcomes are exact multiples, the snapshot size is flattered, and a million distinct patients was never built. The pruned share at scale is the 10,000-patient pool's.
- **One machine.** One M4 Pro, one DuckDB thread, one 4 GB limit. Latency at 100,000 and above rests on ten queries in the main runs, so p95 equals the maximum there.
- **Unexplained variance.** The 14-worker rise from D21 to D23 (p95 7.9 to 9.1 s) and the 8-worker rise from D23 to D24 (9.40 to 10.56 s), both on identical results, have no established cause. The 8-worker fall in D25 (to 8.41 s) came with pruning, which cut cost per candidate by 21%, but the log does not separate pruning from run-to-run spread.
- **Build peak memory.** About 8.1 GiB resident under a 4 GB limit. The factor of about two between limit and resident memory is DuckDB's or the allocator's, and is unresolved. Writing the Parquet files sorts each whole bucket and sets the peak. A ranged write does not give the same Parquet bytes, so it was not done. Staging and the build database need about 144 GB of disk at 1,000,000 patients.
- **Parallel matching at 1,000,000** was not measured. Single-process matching there holds 6.86 GiB and takes up to 245 s.
- **Deferred D25 follow-ups.** Record pruned candidates per query in the matcher benchmark, so the pruned share is bound evidence and not an ad hoc measurement. Add a sortedness assertion in `_prune`. Close the benchmark worker's store and pool if a query raises. Per-concept end summaries would let `during`'s grace prune, but need a format change.
- **Not built.** Chartevents (668,862 demo rows; route (b) of D16, point-measurement support, a series table, per-patient streaming in the MIMIC converter). Non-box source networks in the adapter. Bucket-level roots for incremental appends. Embeddings, Postgres and Rust.
- **Hosting.** Nothing is deployed or hosted. This waits on you.

## 6. What needs you

- **Hosting and deployment.** Where the store and matcher run, and who operates them.
- **Anything clinical.** The store indexes source item classes, with `clinical_mapping_verified: false`. Clinical concept mappings wait for a qualified reviewer (D-CLINICAL). The accept-all policy is not review.
- **Data use.** Whether the credentialed MIMIC-IV agreement allows committing row-level digests (D17, D-DATA).
- **Publishing.** The wheel is unpublished. Publishing, and renaming the `patterns` import before it, are yours to decide.

## 7. Since the briefing was written

- **D26, execution store 3.0 (merged after this briefing; see the log).**
  - *Format.* The D5 record fields are now typed columns, and the remaining free-form evidence moved to an `event_evidence` table that matching never reads. The expected speed gain did not happen: event loading is unchanged, the build is 17 to 20% slower, content verification is 24% slower at 1,000,000, and the 1,000,000 snapshot is 56% larger, mostly a replication effect of the synthetic record times. The format was kept for its typed, validated record fields, which a selection mode on availability times needs. A review recommended keeping it.
  - *End-time summaries.* These let `during`'s grace prune. They were kept under a rule fixed before measuring, but the gain is small: 36.22% of candidates pruned against 36.10%.
  - *Typed staging* (Parquet). Staged bytes at 1,000,000 fell from 120 GB to 13 GB, with peak memory up 1.2%.
  - *Unchanged.* Results are unchanged everywhere. The D21 target still holds: p95 8.53 s at 8 workers and 7.71 s at 14.
- **Chartevents (D16, route (b)), in progress.** The design and modules are built and reviewed on a branch. Integration into store 3.0 and the full demo run follow D26. The design draft is `docs/decisions/chartevents-route-b.md` on that branch.
