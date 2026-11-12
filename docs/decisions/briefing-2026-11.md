# Briefing for the project owner, November 2026

This is a reader's guide to [the engine increment decision log](engine-increment-log.md), decisions D1 to D25. It was written on 2026-11-09 and summarises; where it and the log differ, the log is right. Numbers come from the log, [the execution store page](../execution-store.md) and the committed reports in `verification/`. Each is labelled with its source. Part 7 describes work still in progress, and is the part to update.

## 1. Summary

From 2026-11-06 you delegated three things, and every decision inside them: package the engine, build an execution store, and build a SULO adapter for the trajectory-graph engine. All three exist and are merged (PRs #120 to #136).
- The engine builds as a wheel. Nothing is published.
- The adapter turns bounded, patient-local and claim sources into the engine's input and checks each answer against the source.
- The store is an embedded DuckDB database writing immutable Parquet snapshots. It builds and verifies at 1,000,000 synthetic patients under a 4 GB database limit.
- Matching at 300,000 patients meets the speed target on one machine, in Python, with 8 or 14 worker processes. No Rust was built. *Since D30 (section 7) the margin is narrow: p95 9.46 s at 14 workers, and 8 workers now miss it.*

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
- **Not built.** Value conditions on chart measurements (D27 stage 2; chartevents itself was built after this briefing, see section 7). Non-box source networks in the adapter. Bucket-level roots for incremental appends. Embeddings, Postgres and Rust.
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
- **D27, chartevents by route (b), and store 3.1.** Chartevents now joins the MIMIC demo store.
  - *Route (b).* Each admitted chart row becomes a point event computed from the measurement importer's own claim description, with no graph built per row. Its value and unit go into a new `series` table. That table is the store format change, so the profile is `execution-store-3.1`.
  - *Census.* Every chart row is checked against the raw file by independent code, and every one of the 4,753 claim stores went through the full measurement graph route. Their rows equal the bulk rows and the rows the built store holds. The graph route shares its row mapping with route (b), so on that mapping the raw-row check is the evidence.
  - *Differential.* The 500-query differential has no mismatch, and `exhaustive_match` agrees with the faster reference on the first 50 queries.
  - *What it costs.* Admission keeps only numeric rows with a unit and no warning: 150,051 of 668,862. The rest are excluded records, so most `none` results on chart items are `not_comparable` (D18).
  - *Not yet.* Value conditions (such as "a mean arterial pressure below 65") are stage 2. Textual and unitless rows, unit equivalence and reviewed mappings are stage 3.
  - *Merkle digest.* The Merkle input digest of D16 is built but deliberately left out of the store until incremental appends exist.
  - *Review fixes.* An independent review found that a patient the inputevents route refuses could come back through chartevents as a chart-only patient and read `none`; refusal is now whole-patient (the demo has no refusals, so no figure moved). The raw-row check now also recomputes chartevents' coverage, which decides `none` against `NO_SOURCE_COVERAGE`.
  - *Unchanged elsewhere.* The scaling report (to 1,000,000) and the matcher comparison were regenerated on store 3.1. Every result digest equals D26's, the 300 recorded match digests are unchanged, and build, verification and matching costs are within run-to-run spread (build +0.75% at 1,000,000). The D21 target still holds: p95 8.41 s at 8 workers and 7.05 s at 14.
- **D28, value conditions (chartevents stage 2), the D26 and D27 follow-ups, and the build regression.**
  - *Value conditions.* A match request can now ask, for example, for "a mean arterial pressure below 65 mmHg". Each condition becomes a per-query tag concept for the engine, which is unchanged.
    - Units are literal: there is no conversion until stage 3, and a unit nobody records makes a step not comparable.
    - A value too fine or too large for the stored integer is compared exactly on its stored decimal.
    - Excluded chart rows of the item count as possibly satisfying the condition (D18).
    - A value relaxation must widen the condition, so its events contain the stricter condition's.
  - *Evidence.* The store agrees with an independently written reference on every result: 26,880 synthetic results and 20,000 on the demo, with 0 mismatches. Four planted defects are caught: a threshold off by one, the unit ignored, and a too-fine value read as matching or as not matching. The demo has no too-fine values, so the last two are caught on the synthetic fixtures only.
  - *Review fix.* An independent review found one must-fix: two conditions differing only beyond the 28th digit could share one tag, giving a wrong answer. Tag names are now exact, and a property test over long values checks it. The reports were regenerated again after the fix.
  - *Build regression.* Profiling showed that D26's slower build was Python writing digest lines, not the evidence table itself. Writing them a column at a time, with identical bytes, makes the build at 1,000,000 12.0% faster (5,191 against 5,898 s) and content verification 38% faster. That passed a rule fixed before measuring. The store format itself is no cheaper: the digest code got faster for every table. The two levers D26 named saved 1.8% and about 0.2%: one was reverted, and the other was not built.
  - *Follow-ups.* The five D26 and D27 follow-ups are done.
  - *Unchanged.* Results are unchanged everywhere: the matcher arms agree, the 300 recorded match digests are unchanged, and every demo figure is unchanged. Every matcher arm's per-query result digests are now kept in the report. The D21 target still holds: p95 8.39 s at 8 workers and 7.26 s at 14.
    - The first D28 run gave 8.95 s at 14 workers, slower than its own 8 workers.
    - A scratch rerun then (7.11 s) was not committed and does not establish run-to-run spread.
    - Why the two committed runs differ is not established.
- **D29, the mockups on the execution store, stage 1 of 3.** The trajectory mockup still finds each step's events in the browser, on synthetic data. The plan moves it onto the store in three stages, each with its own trigger. Stage 1 (real data) puts the store beside the pages. Stage 2 puts it under the trajectory page by default, once the store can express what the page offers (or refuses it by name) and the parity test is clean. Stage 3 retires the browser path, and waits for the hosting decision.
  - *Stage 1, built.* A new mockup page, *Store query*, runs trajectory queries on the open MIMIC-IV demo in the store on the server. A query can carry value conditions and relaxations, for example "mean blood pressure below 65 mmHg, then norepinephrine within 6 hours". The store is built locally and never committed. The server sends back aggregate counts only, checked against closed schemas before anything leaves it. It serves a store only if it holds the open demo's own 100 patients, checked by a pinned digest of their ids, not by the snapshot's label: an independent review showed the label alone could be edited, or written by the build command for full MIMIC-IV. Counts alone are not disclosure control (no small-cell suppression; differencing can isolate one patient's value), so credentialed data would need real disclosure control, which is not built. Tests check both rules.
  - *Parity.* The synthetic cohort was also built into a store and compared with the trajectory page over ten trajectories both can express. 427 of 480 patient results agree. Each of the other 53 has a named cause: the store keeps every record and letter mention as its own event, where the page merges them and builds medication courses, and the store's comparability rule reads gaps in source coverage where the page does not. These are documented, not fixed.
  - *Next, stage 2.* The store cannot yet express Nth occurrences, "at least N times within", rises and falls, non-negative gene results, free-text options, merged events, inferred medication courses, times from a letter's wording or rule-placed index periods. That list is the input to stage 2, together with a decision on each difference and the converter itself emitting measurement values.
- **D30, a correctness increment: `first` and `last` under uncertain times, and letter mentions at or before their letter.** An independent review of the stage 2 design found that the engine's shipped `first` and `last` could give wrong `exact` and wrong `none` results, and that synthetic letter mentions were timed by their letter. The owner made D30 a correctness fix, and moved Nth and counts to D32 and rises and falls to D33 (first numbered D31 and D32; plan in `d32-d33-store-step-language-plan.md`).
  - *`last`* took the event with the largest *earliest* start. An event known only to lie somewhere in March to December lost to one fixed on 10 March, so "the last seizure before the end of March" was wrongly `exact`. It now takes the latest start.
  - *Uncertain targets.* When the records leave it uncertain which event is first or last, the step is decided on the range of times the true first (or last) can take, where that is exact. Otherwise the result is `unresolved`, with the binding found beside it. "Otherwise" means a relation reads the step's end, another step can bind its events, or the events share time variables. A new check enumerates every choice of times on small cases and finds no wrong claim; the engine before D30 made such claims.
  - *Letter mentions* now range from birth to the letter. Before, 2,443 of the 2,444 missed their true time.
  - *What moved.* The MIMIC demo is unchanged (all its times are exact). On synthetic data, 100 of the 300 recorded match digests moved, 57 of the 480 parity results, and about half of the matcher benchmark's queries. Each change is attributed to one of the three fixes by rerunning with them switched off. The store and the trajectory page now agree on 401 of 480 results.
  - *Review and fixes.* An independent review of the built increment found one must-fix: a step was treated as safe although another uncertain `first` or `last` step could take one of its events (two overlapping concept families). It is fixed in the engine and the reference, and the small-case check now uses overlapping families; it moved 2 of the 300 recorded digests. The review also corrected wording (the attribution localizes changes to stages; it does not prove them fixes), and made incomplete `none` results, where a relaxed search was left undecided, visible in the reports and on the store query page.
  - *Cost.* The first run missed the D21 latency target (p95 13.05 s). The review traced the time to a repeat search that rarely refutes; three changes that move no result (each checked byte for byte) brought it back: p95 9.46 s at 14 workers against 10 s, a 5% margin, maximum 10.2 s, memory 0.83 GiB. At 8 workers it is missed (11.70 s). The next lever, one search over an envelope with order facts, changes results and is in the D32/D33 plan.
