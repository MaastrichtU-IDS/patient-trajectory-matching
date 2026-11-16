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
| D21 | Matcher scaling: cheap wins, then parallel Python, then Rust only if needed | Decided; arms B and C built, then the engine kernel; target met at 30 queries × 3 repeats (14 workers: p95 7.9 s, max 11.8 s, 0.82 GiB); stay in Python, no Rust prototype. D30: missed in its first run (13.05 s), met again after its review's levers (9.46 s at 14 workers), see D30. D31: judged by the paired protocol since its second attempt; met at 14 workers (8.49 s), missed at 8 (10.17 s) |
| D22 | The adapter passes shared source variables to the engine; the store still refuses them | Decided; its follow-ups (strict intervals in the engine, the store) done in D24 |
| D23 | Per-bucket derive: the store builds at 1M patients | Decided; 1,000,000 patients build and verify under a 4 GB limit; verification memory follow-up done in D24 |
| D24 | Shared variables end to end: strict intervals in the engine; the store holds shared variables | Decided |
| D25 | D24 review follow-ups; time pruning on the posting summaries, under a rule fixed before measuring | Decided; pruning kept: exact, arm B −18.4% per candidate at 300,000, 8-worker p95 8.41 s; ranged Parquet write not done |
| D26 | Execution store 3.0: typed event columns and an evidence table; end summaries for `during`; typed staging; D25 follow-ups, under rules fixed before measuring | Decided; end summaries kept (exact, 280.3 µs per candidate, small gain); typed staging kept (staging −89% at 1,000,000, peak +1.2%, build +18% mostly from the format); evidence table did not cheapen loading |
| D27 | Chartevents by route (b): store 3.1 with a series table; a census on the demo, a sample beyond it | Decided; demo census clean (4,753 of 4,753 claim stores through route (a), every chart row through the raw-row check, 0 differential mismatches); Merkle digest built, not wired |
| D28 | Value conditions in the match request (chartevents stage 2); the D26 and D27 follow-ups; an experiment on the store 3.0 build regression, under rules fixed before measuring | Decided; value conditions shipped (0 mismatches against an independent reference on 26,880 synthetic and 20,000 demo results; four planted defects caught); column-wise digest lines kept (build −12.0% at 1,000,000, identical content); a review's must-fix (conditions differing beyond 28 digits shared a tag) fixed before the final regeneration; evidence-in-events staging reverted (−1.8% at the pilot) |
| D29 | The mockups move onto the execution store in three stages; stage 1 puts the store beside the pages, with aggregate counts only and only the open MIMIC-IV demo, checked by content | Decided; stage 1 built (a store query page on the demo, served only if the store holds the demo's own patients; the synthetic cohort in a store agrees with the trajectory page on 427 of 480 results, each of the other 53 by a named cause); stage 2 inventory listed |
| D30 | A correctness increment: `last` takes the latest start; a `first` or `last` target the patient's facts leave uncertain is decided on its envelope where that is exact, and is otherwise unresolved; synthetic letter mentions range from birth to the letter | Decided; built, reviewed, fixed and regenerated. MIMIC unchanged; every moved synthetic result localized to a stage (102 of the 300 recorded digests, 57 parity results, every arm A difference); the worlds check (small grids, overlapping concept families) finds no violation, and finds them in the engine before D30; a review's must-fix (coupling through another uncertain step) fixed; three output-identical cost levers kept; the D21 target met again at 14 workers, narrowly (p95 9.46 s), missed at 8 |
| D31 | Latency: one envelope search for coupled `first` and `last` steps (lever 1 of the D32/D33 plan), under rules fixed before measuring | Built, sound on every check (0 violations in 44,500 small cases; reference agrees), then reverted under its rule: the regenerated 14-worker confirmation p95 was 9.41 s against a bar of 8.5 s (same-day calibration 8.2 s; 8 workers 9.90 s); 4 `not_comparable` results at 10,000 also moved; D30 stands. Next: fix the measurement protocol, then retry lever 1 or lever 5. *Second attempt:* a paired, interleaved confirmation protocol (owner-approved); the four results traced to the coverage rule, not D18; a D18 crash in main found and fixed; the order facts in the exact phase. Lever 1 kept: paired p95 ratio at 14 workers 0.80 to 0.88 in every pair against main (bar 0.90), replicated at 0.82 to 0.86; this tree's p95 8.49 s (replication 8.77 s), max 11.6 s, 0.76 GiB; 8 workers 10.17 s (stretch missed); every moved result from undecided by the engine's own decision; MIMIC unchanged |
| D32 | Nth and Nth-from-last occurrence, and "at least N times within a span or the index period", in the store's match request, over coded records; the converter's optional `series`; the translator and parity extension, under rules fixed before measuring | Decided; shipped. Sound on every check: 0 violations in 120,000 small cases against every choice of times, the reference agrees, 14 planted defects caught, 0 mismatches on 53,280 synthetic and 15,600 demo results. An early review's must-fix (derived tag names could collide) was fixed and everything regenerated. No change without the new fields: 300 digests, every matcher digest, scaling and MIMIC as D31. No latency regression: paired 14-worker p95 ratios 1.001 to 1.010 against main (bar 1.05), p95 8.10 s; replication 0.93 to 1.05. Parity 555 of 672 over fourteen trajectories, every difference named |
| D33 | Rises and falls (relative conditions) in the store's match request: a baseline of up to k previous readings within W, period readings timed by their period's start, the two-pass rule; and a build fingerprint so that engine-only changes can reuse the scaling report's builds, under rules fixed before measuring | Built and sound (60,000 small cases and 500 snapshot requests, 0 violations and 0 mismatches; seven planted defects caught), **not shipped**: the usefulness bar (at most 30% unresolved in each rise template) was missed on the graph template, 3 of its 4 candidates (one patient moves the share by 25 points), by D30's order rule on its `first` blocker; rises stay refused by name. The build fingerprint moved to its own increment after a review's must-fix; this branch changes documentation only |
| D34 | A runtime record of what the scaling report's builds load and read, by whole-file digests, with the engine split so that a build loads no matching code, so that a change to matching alone can reuse the report's builds (the static fingerprint of D33's part 2 superseded after two reviews), under rules fixed before measuring | Decided; shipped after three reviews. Every rule met (one stated exception: D32's paired verdict frozen). Results byte-identical: 300 digests, 1,170 matcher digests, scaling and MIMIC as D32. Reuse shown in tests, not yet at scale |
| D35 | An exact target split for the relaxation sets D30 and D31 leave undecided (`first` and `last` steps whose target is uncertain, typically with their end read), under rules fixed before measuring, after a disclosed calibration | Decided; shipped. Every rule met: 0 violations in 60,000 small cases, equal to the truth wherever the split completed, the reference agrees, four planted defects caught; only undecided results moved; MIMIC unchanged. The parity trajectory "a seizure while taking the first blocker" 0 of 10 unresolved (was 9; all `possible`); the 12 order-uncertain results at 10,000 all decided. Paired 14-worker p95 ratios 0.90 to 0.97 against main (bar 1.05), p95 8.97 s. First reuse of the scaling builds at 1,000,000: 1,740 s against 9,197 s. The evidence for usefulness is about 22 patients over two queries |
| D36 | Rises and falls retried: D33's relative conditions restored onto D35, in a module no build loads (so the scaling builds can be reused), with a usefulness test on 30 distinct synthetic cohorts and a MIMIC demo differential, under rules fixed before measuring | Decided; shipped. Every rule met: 0 violations in 60,000 small cases, 0 reference mismatches (500 snapshot requests, 6,800 MIMIC demo results), seven planted defects caught; nothing changed without the new fields (scaling reused at every size); usefulness 0 of 189, 0 of 189 and 0 of 142 `unresolved` over 30 redrawn cohorts (with D35's split off, the graph template 67 of 142); 14-worker p95 ratios at most 1.010, p95 8.64 s. The translator writes rises by default; the page is not yet served from the store |
| D37 | D29 stage 2: the trajectory page matches in the execution store by default, through the page's own translator; a decision on each parity cause; refusals shown as a labelled in-browser fallback, under rules fixed before measuring | Decided; shipped. Every rule met: every page template and 561 swept page definitions answered by the store (286) or refused by a named code (translator 255, store 20); parity 643 of 864 over eighteen trajectories, every difference from the closed declared list; the endpoint equals the store's own answers; store, engine and converter unchanged (one report rebound, figures identical). Two of five templates answered at the page's default setting; stage 3 waits on hosting |
| D38 | Gene steps in the store: the first report that is not negative, timed by its recording, across reclassification, from the classification the synthetic source now keeps in each genetic result's evidence (a converter wrapper in a module no build loads), with one entry point routing requests by profile, under rules fixed before measuring | Decided; shipped. Every rule met after a review's must-fix (letters counted as recording a gene step) was fixed: 0 violations in 60,000 small cases (undetermined classifications read every way), 0 reference mismatches there and on 400 snapshot requests, six planted defects caught, pruning on and off identical; nothing changed without gene steps (594 requests against main's builds, scaling builds reusable); Tree and Graph with their gene step 0 of 7 child types unresolved (0 of 122 candidates each) over seeds 1001 to 1030; parity 687 of 960, every difference declared, none from a gene step; 14-worker paired p95 ratios 0.83 to 1.02, p95 9.38 s. The translator writes gene steps by default; under coded only they are refused until a joint profile with D39 |
| D39 | A store-side "coded only": the match request may leave letter mentions out of every step (`exclude_kinds`, profile `execution-store-conditions-1.3`), in a module no build loads, so that the trajectory page's "coded only" and its Event frequency template can run on the store, under rules fixed before measuring | Rules committed before any measurement; in progress |
| D40 | Fewer `unresolved` results from exhausted search budgets: a stopped possible search settled by propagation (pair consistency, a bounds network, one checked descent), under rules fixed before measuring, after a disclosed calibration | Built, sound (0 violations in 80,000 small cases; 363 of 363 and 38 of 38 `SEARCH_BUDGET_EXHAUSTED` decided), **not shipped**: the MIMIC rule was missed as written; reverted. The finding: every large-store `unresolved` figure quoted since D35 comes from budget-1 queries the benchmark's generator draws, all decidable with a budget of 1,000,000 |

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

## D29 The mockups move onto the execution store in three stages; stage 1 puts the store beside the pages

The trajectory mockup resolves each step's candidate events in the browser, from the synthetic cohort, and the trajectory-graph engine decides timing and relaxations (`demos/mockups/server.py`). The store selects candidates itself, and its step language is smaller: occurrence any, first or last, and D28's absolute value conditions with a literal unit. The page also takes shortcuts the store does not take. It groups records into events by the generator's ground truth, times letter mentions from their wording, and builds medication courses from dispensings. So the store cannot simply replace the page's candidate resolution.

**Three stages, each with its trigger, in this order:**
1. *The store beside the pages (built here).* Trigger: real data. Real data cannot be shipped to a browser as patient rows (D17), so the first page that needs it queries the store on the server and returns aggregates. The trajectory page is unchanged.
2. *The store under the trajectory page, by default (next).* Trigger: the store can express everything the page offers, or refuses it by name, and the parity test is clean. Clean means every difference is either removed or accepted as a declared difference. The work:
   - Nth occurrence and "at least N times within" in the store: small and well defined.
   - Relative conditions (rises and falls against earlier values): a design decision first (what the baseline is, how uncertain times and excluded readings affect it), then a differential test.
   - A decision on each record-level difference the parity test found: coverage, letter mentions, record reconciliation and medication courses. Each either moves into the converters or is declared.
   - The clean parity must be measured with the synthetic converter itself emitting `series`. Today the parity helper adds them (below), so the value-condition comparison rests on the helper, not on the converter.

   The in-browser path stays as a labelled fallback for static hosting and offline demos, as the in-browser search already does when no engine is reachable.
3. *The browser path retired, or kept only as a demo.* Trigger: the hosting decision. Once a server with the store is deployed, the page talks to it, and the research workspace reaches the store through a job API (counts interactively, full results as an asynchronous job). If hosting stays static, the in-browser path remains the demo path.

Why this order: real data forces the switch first; a browser also stops scaling beyond thousands of patients; the page's richer step language is a feature to port, not a reason to stay in the browser.

*Alternative declined:* putting the store under the trajectory page at once. The store cannot express half the page's templates (rises, counts), and the two disagree on 53 of 480 results. Most of those come from the page's shortcuts; the 19 coverage differences come from the store's comparability rule, which the page does not apply. Either way, the page would have silently changed meaning.

**Stage 1, as built** (`demos/mockups/store_query.py`, `store-query/`, `store_parity.py`, the tests; the README has the details).
- *A store query page on the open MIMIC-IV demo.*
  - The store is built locally from `data/local-mimic/` by `patterns.mimic_store_ingest --chartevents` and is never committed. `server.py` takes it from `--mimic-store` or `PTM_MIMIC_STORE`.
  - Steps are item classes or dictionary categories, labelled from the demo's `d_items`, and the page says they are not clinical concepts.
  - Value conditions take a unit from those recorded for the item, and thresholds are strings throughout.
  - Relaxations are `widen_value`, the item's category, `extend_max` and `extend_grace`.
  - The index period is placed on the server from the store's coverage periods (first ICU stay, its first 24 hours, or all stays). The page explains the rule; the README notes that it is simpler than the demo differential's seeded windows.
  - Store and engine are unchanged.
- *Data rules for the endpoint, enforced on the server.*
  - **Aggregate counts only** (D17 and the demo convention). The match answer is built from counts and checked against a closed schema before it is sent. It allows known keys, integer counts from 0 to the store's patient count, and strings only from fixed vocabularies; the reason, note and certainty keys are also compared with the patient identifiers. Nothing the page sent is echoed. The status and catalogue answers have closed schemas too. A failing answer becomes `500 AGGREGATE_RULE_VIOLATION`. A malformed request gets a named 400, and an internal failure `500 INTERNAL_ERROR`, never a traceback.
  - **Only the open demo, by content.** The manifest's dataset id is checked first, as a cheap filter, but it is a label outside the snapshot identity: an independent review relabelled a synthetic snapshot and had it served, and the page's own build command, pointed at full MIMIC-IV, would have produced a snapshot labelled as the demo. So before anything is served, the store must hold exactly 100 patients whose sorted identifiers hash to a pinned sha256 of the open demo's subject ids (`DEMO_SUBJECTS_SHA256`, computed from the pinned `icustays.csv.gz`; the demo is open data, and its reports already commit digests, D17). Nothing is served before that check passes, and a store opened by a failed check is closed.
  - **Counts-only is not disclosure control.** There is no small-cell suppression, and differencing works: the review isolated one patient's maximum heart rate to 0.01 bpm by halving a threshold over 17 queries. That is acceptable only because the data are the open demo. A credentialed deployment would need real disclosure control (suppression, noise or query auditing), which is not built; the content check is what keeps credentialed data out.
  - The tests use stores built from the synthetic MIMIC fixture under the demo's id and under a credentialed id, a relabelled snapshot, and 100 patients that are not the demo's; all are refused under the real pin. A real demo store, when built locally, passes. A leaky answer is planted, and the check refuses it.
  - The pruned count comes from calling the store's own `_prune` on the inputs `match` used. It is read-only, but it is a private function, so a change to it can break this page.
- *The synthetic cohort in a store, with a parity test* (`test_store_parity.cjs`, in CI).
  - *Approach.* Node runs the page's own scripts, as `test_engine_parity.cjs` does, rather than a Python port that could drift from them. Locally a portable Node, checksum-verified, ran it from a scratch directory.
  - *Series.* The synthetic converter stores no series, so the parity helper adds each measured record's value and unit as a `series` row. The converter is unchanged, so no recorded digest moves.
  - *Results.* Over ten trajectories expressible in both, 427 of 480 results agree. A replay of the engine on the candidates the explanation reads gives the store's result for every patient. So the 53 differences come from candidate selection or the comparability rule, never from the query's translation or the windows. Each has a named cause, pinned per patient: coverage (19), letter mentions (27), record reconciliation (25) and medication records (11). Some patients have several.
  - *Not fixed.* None is a defect of either side; all are documented. Coverage is the store's comparability rule; the other three are the page's shortcuts against the store's literal records.
  - *Planted defects.* A one-day window shift fails the test as unexplained differences. Reading "at least" as "above" changes 10 results on a trajectory whose threshold equals recorded monthly counts, so the pinned table catches it independently of any one patient.

**The stage 2 inventory** (page features with no store form):
1. Nth and Nth-from-last occurrence, for steps and the index period's event.
2. "At least N times within a span" or "in the index period", and its one-fewer relaxation.
3. Rises and falls against earlier values, and the smaller-factor relaxation.
4. The page's "measurements do not cover the time around the earlier step".
5. Gene steps as the first report that is not negative, timed by its recording, with reclassification.
6. Free text as a query option (coded only, stated, or hedged).
7. Reconciled events: one happening reported by several sources counts once.
8. Medication courses inferred from dispensings and supply, and an ongoing course ending at last contact.
9. Times from a letter's wording.
10. Index periods placed per patient by rule.
11. Values without a unit, or in another unit (D28 stage 3).

Neither side expresses absence or alternatives.

*Follow-ups:* stage 2 as above; converter options for items 6 to 9, if the page's semantics are to be kept; a public count of pruned candidates in `match`'s output, so the page need not call `_prune`.

## D30 A correctness increment: `first` and `last` under uncertain times; letter mentions at or before their letter

An independent review of the D30 draft (the store's step language, now the D32/D33 plan in [d32-d33-store-step-language-plan.md](d32-d33-store-step-language-plan.md)) found that the engine's shipped `first` and `last` give wrong `exact` and wrong `none` results, and that the synthetic converter breaks the engine's core assumption that a true time lies inside its recorded range. The owner decided to make D30 a correctness increment, implemented now, and to move Nth and counts to D31 and relative conditions to D32 (renumbered D32 and D33 when the latency work took D31). The reviewer's scripts were kept in a scratch directory (`/tmp/rv30/`); their cases are tests here.

**The defects, with the review's counterexamples** (M may fall anywhere from day 60 to day 365, L is day 69, the window is [0, 90]):
1. *`last` took the largest earliest start.* `candidates` sorted by (start range, end range, id) and took the last, which is the event with the largest *earliest* start. So "the last K, then the window's end at least 0 later" was `exact` on L, which is false if M fell in November, and "the last K at least 1 after the window's end" was `none`, which is possible.
2. *A relation reading the end of a `first` or `last` step whose target is uncertain.* A is an exposure starting between day 60 and 365 and ending at 800; B runs from 64 to 70; X is at 400. "X during the first E" was `exact` on A, but if A starts after day 64 the first exposure is B, which ended at 70. With B ending at 900 and X at 850, the engine said `none`, which is possible.
3. *An existing over-claim, and its mirror.* "The first K after the window's end" was `possible` (M at 365), though the first K is on or before day 69 in every choice of times. And "the first K before the window's end" was `possible`, though it holds in every choice: `possible` asserts that no binding is exact, which was false.
4. *Found while fixing 1 to 3: a `first` or `last` step whose events another step can bind.* With `last` corrected, M is both the first K (least earliest start) and the last (greatest latest start). "The first K, then the last K at least 10 later" then binds M to both steps, a difference of exactly 0, and says `none`; if M fell on day 200, the first is L and the last M, 131 days apart. (The old engine also said `none` here, for another reason: its last was L.) The same holds for an `any` step whose concept the first step's events carry, and a difference of 0 on one event can equally make a binding look `exact`.
5. *Synthetic letter mentions were timed by their letter.* 2,443 of the 2,444 mentions asserted present of the patient have their true time outside that range (median 790 days before the letter, at most 3,559): a letter says what happened before it, not when. That is 2,444 of the synthetic store's 8,365 events.

**Decision 1: `last` is the greatest by (latest start, earliest start, end range, id).**
- *Why it is sound for a time constraint:* if every true start lies in its range, the true last start is at most the greatest latest start, which the chosen event attains, and at least the greatest earliest start, which is at least the chosen event's own earliest. So the true last start lies in the chosen event's range, as the true first start lies in the range of the event `first` picks. Over the patient's tightened ranges (`Times.dom`) the order can differ from the recorded one (a proper exposure's start is bounded by its end); the rule below therefore never relies on the order alone.
- *On exact times the two orders are the same,* ties included (a test on 500 seeded cases of exact points and exposures picks the same event as before), so the MIMIC demo, whose times are all exact, cannot move.
- *Documented correction, not a profile version.* The profile's identifier stays `trajectory-graph-1.0`. The old order contradicted the profile's own definition of `exact` and `possible` (over every choice of times); a new identifier would present the defect as an alternative semantics, and every caller (the store, the page, the SULO adapter, the examples) would have to change its requests for a fix. The profile document records the correction, and a match's context names the engine's file digest, so a result cached under the old engine is distinguishable.
- *Declined:* sorting `last` by the tightened ranges. It changes nothing the rule below does not already make sound, and differs from the profile's stated order.

**Decision 2: the order rule.** A `first` or `last` step's target is *certain* when every other event of its concept starts strictly after it (before it, for `last`) in every choice of times, or certainly at the same time (the order then breaks the tie). A certain step binds its event, as before. An uncertain step is *safe* when its concept's events name no shared variable, no relation reads its end (a `during` from it) unless every such event is a point, and no other step can bind one of those events. Then:
- *A safe step is decided on its envelope.* The true first start ranges exactly over [least earliest start, least latest start] of the concept's events (for `last`, [greatest earliest, greatest latest]): every true first start is in it, and with independent events every time in it is the first start in some choice of times (order statistics move by at most one grid step when one time does). So the step becomes a point of its own ranging over the envelope, and `exact`, `possible` and `none` are all sound. This corrects defect 3 both ways: "first K after the window's end" is `none`, "first K before it" `exact`.
- *An unsafe step leaves the set undecided* unless a sound refutation exists. A binding found on the picked event proves nothing either way; the result is `unresolved` with reason `OCCURRENCE_ORDER_UNCERTAIN`, `uncertain_steps`, and the binding beside it as `unverified_match` (its certainty, binding and method). If no binding is found, the search is repeated with every event that can be first (last) in some choice of times as the step's candidates, a superset of the true targets: a `none` there is sound and stands, anything else leaves the set undecided.
- *Relaxation sets.* An undecided unrelaxed query is handled as a stopped search is: the relaxation sets are tried, and a relaxed match found is reported beside the unresolved status. An undecided relaxation set is passed over like a stopped one, and the result says so (`complete: false`, note `CHEAPER_RELAXATION_ORDER_UNCERTAIN` or `RELAXATION_ORDER_UNCERTAIN`).
- *Every other place an end is read.* In `trajectory-graph-1.0` only the grace half of `during` reads a step's end; scope and `after` read starts. A point's end is its start, so a concept of points only is safe under `during` too.
- *Linked variables.* The argument that a time constraint on the picked event holds at the true target's time needs the variables to be unlinked: with a shared variable, the picked event's range is tied to another event's, and the true target's time need not satisfy that tie. So any shared variable among the concept's events makes the step unsafe.
- *Pruning and the candidate filter stay exact.* A pruned patient's every set is impossible over all of a concept's events, so both searches of an unsafe step find nothing and the engine answers the same `none`. An undecided unrelaxed query needs every step to have a candidate under its own concept, which the filter keeps.
- *The excluded-records rule (D18)* reads the binding of an `unverified_match` for its displacement test, as it reads a relaxed match's.
- *Alternatives declined:* `unresolved` for every uncertain step (sound, but on the synthetic cohort most `first` results would be undecided, including those the envelope decides exactly); a corrected `possible` by a feasibility check with a bound on the picked event (cheaper, but it still asserts "no binding is exact" falsely, as in defect 3's mirror); enumerating which event is first in each choice of times (exact, but exponential, and a disjunctive network beyond the engine's simple temporal one).

**Decision 3: letter mentions range from the earliest the record allows to the letter.** A mention's start is [birth, or the start of an earlier record (a prenatal scan) if that is earlier, the letter's latest time]. Verified on every mention against the generator's truth, which the converter never reads and a test does: 2,444 of 2,444 inside, against 1 before. The median range is about four years, so most `first` steps on a mentioned concept become uncertain, and the store answers what the records support. `synthetic_provenance` records the timing (`mention_time`); `mention_time='letter'` keeps the old timing, only so that the digests recorded before D30 can be reproduced by the tests and the attribution below.
- *The trajectory page does not share the letter timing:* it merges a mention into the happening it describes (2,392 of the 2,444, a ground-truth shortcut D29 declared) or times the rest from their wording, and all 52 of those hold their true time. It stays as it is.
- *Declined:* timing by wording in the converter (heuristic, D32 plan); an unbounded lower end (no sound bound is lost by birth on this cohort, and an unbounded range would leave every `first` on a mentioned concept undecided, even after a coded record).

**Decision 4: the trajectory page.** The page picks `last` among its candidates by (latest, earliest) as the engine now does. The page's own search does not test whether a `first` or `last` target is certain; it binds the reconciled event its most precise record dates. That is a declared difference in the store parity test (`order-uncertain`), not ported, since the page resolves candidates itself and sends the engine single candidates.

**Pre-committed rules** (written and committed before any measurement of this increment):
- *MIMIC unchanged.* Every figure of the regenerated MIMIC demo report other than identifiers, digests, artifact bindings and timings equals D28's, the differentials, the census and the value differential included.
- *Synthetic changes explained.* Every synthetic result that changes is explained by the three fixes. (The review of D30 corrected this wording: the mechanism *localizes* each change to the stage of one of the three changes; it does not by itself show that the change is a fix, and the stages are cumulative. The rule is kept as written, and the verdicts below say "localized".) Mechanism: with the snapshot built under `mention_time='letter'` and the engine's two switches set as before D30 (`_ORDER_RULE` off, `_LAST_KEY` the old order), this tree reproduces the earlier result digest; each change is then attributed to the first stage at which it appears: the `last` order, then the order rule, then the mention timing.
- *The 300 recorded match digests* are re-recorded, each changed seed with its stage, by that mechanism, in a committed fixture the tests check (before digests reproduced, stages recomputed, after digests equal to the store's).
- *Arm A* runs old code at 7dc3364 on its own snapshots, with the old `last` and the letter timing. Every query whose digest differs between B and A is explained by the same mechanism, at every size and in the confirmation: an attribution run reproduces arm A's digest query by query with this tree's code on a letter-timed snapshot built by this tree with the switches off, and attributes each change. Arm A's `same_rows` check will report the mention starts as different; that is recorded, not hidden.
- *Scaling report.* Table row counts and candidate counts equal D28's at every size (the fixes change times and decisions, not rows or concepts); content digests of `events` and `concept_postings` change, and the status mixes and pruned counts change; both are recorded per size.
- *Exactness elsewhere.* Every engine test passes, with the frozen-engine comparison restricted to the cases D30 cannot touch, and those cases remain the majority in every comparison. The engine and its reference agree on every differential case; engine, reference and the old engine are checked against the true semantics by enumerating every choice of times (`trajectory_graph_worlds.py`): 0 violations for the engine and the reference, and violations for the frozen pre-D30 engine; each part of the rule, undone, produces violations.
- *Parity.* Both parity tests pass, every change in their pinned tables explained.
- A failure is fixed, never tuned away.

**Measurement and verdicts, first run** (before the review of D30 below; superseded by the regeneration after it, at the end of this entry, which is what the committed reports hold). One regeneration, one job at a time, after the rules were committed (7d70184):
1. the MIMIC demo (`--workers 8 --census-workers 14`; 1,439 s);
2. the scaling run to 1,000,000 with `--keep` (`--work-dir /tmp/store-d30/scaling`, 9,291 s);
3. arm A's own 2.0 snapshots of 1,000 to 300,000 (`--build-arm-a`);
4. the matcher comparison with the committed settings, plus `--confirm --large-queries 30 --large-repeats 3` at 300,000;
5. letter-timed snapshots of 1,000 to 300,000 built by this tree, and the attribution (`tools/d30_attribution.py`, `verification/d30-attribution-report.json`). The first attribution run drew every stage's queries with this tree's converter and so did not reproduce arm A on 8 queries at 1,000 (below); it was corrected and run again, and the committed report is the second run's.

| Rule | Measured | Verdict |
|---|---|---|
| MIMIC unchanged | Every figure of the demo report other than identifiers, digests, artifact bindings and timings equals D28's, the input digest included (`7a0dd1fd…`); the differential and the value differential are equal as JSON. Snapshot `c4f41d29…` (D28: `9730e87b…`; the builder's digest changed) | met |
| The 300 recorded match digests | 100 moved. For all 300, this tree on the letter-timed store, with the switches off and the case drawn from that store, gives the digest recorded before D30. Localized to the stages at which each moved (cumulative, so the order rule acting on the wider mentions counts at the mention-timing stage): the mention-timing stage only 88; the order-rule and mention-timing stages 8; the last order alone 1; the last order and the mention timing 2; all three 1. `patterns/fixtures/execution_store_match_digests_d30.json` holds each seed's digests and stages, and `D30ExplanationTests` recomputes them | met |
| The store parity test | 57 of the store's 480 results moved: 53 by the mention timing, 4 by the order rule, none by the `last` order, each attributed by running the store as before D30 and then with each fix in turn. 401 of 480 agree with the page (427 before); the 79 differences are pinned, each by a named cause, `order-uncertain` (9) new. The engine parity test is unchanged (336 of 336) | met |
| Arm A against arm B | B differs from A on 24 of 50 queries at 1,000, 30 of 50 at 10,000, 5 of 10 at 100,000 and 5 of 10 at 300,000. The attribution reproduces arm A's digest on every query at every size (50, 50, 10, 10), with this tree on the letter-timed snapshots, the switches off and the queries drawn as arm A's worker draws them, and arm B's on every query; the queries that moved are exactly B's mismatches. By stages (mention timing alone; order rule and mention timing; last order and mention timing; all three): 1,000: 17, 5, 1, 1; 10,000: 20, 6, 0, 4; 100,000 and 300,000: 3, 2, 0, 0. In the confirmation (30 queries, against the D28 report's arm B, since arm A is not run there) 19 moved: 12, 5, 0, 2. Arm A's `same_rows` is false only in `events` and `concept_postings`, the mention starts | met |
| Arms B, `B_unpruned` and C | Byte-identical to each other at every size and in the confirmation (50, 50, 10, 10; 30 of 30) | met |
| Scaling report | Table row counts and every query's candidate count equal D28's at every size. Status mixes changed (below) | met |
| Engine and reference | The differential (2,400 cases) agrees, 68 of them unresolved by the order rule. The worlds check finds no violation in the engine or the reference on 3,000 small cases, and finds violations in the frozen engine from before D30 and with each part of the rule undone. The frozen-engine kernel comparison holds on every case D30 cannot touch, the majority in each test | met |

*Why arm A's queries differ from B's.* Each arm's worker draws the seeded queries with its own tree's converter and query generator, which shape a query around one patient's event times. Before D30 the two converters agreed, so the arms saw the same queries; since D30 a query anchored on a letter mention differs between them. That difference is part of the mention timing, and the attribution draws S0 to S2 as arm A does.

*Status mixes in the scaling report.* The data are synthetic, replicated beyond 10,000 patients. The queries themselves changed with the mention timing, as above, so these are not like for like:

| Size | Exact | Possible | Relaxed | Unresolved | None | Not comparable |
|---|---|---|---|---|---|---|
| 1,000 | 1,301 → 761 | 611 → 969 | 836 → 560 | 0 → 398 | 38,510 → 38,559 | 6,103 → 6,114 |
| 10,000 | 12,668 → 7,334 | 6,159 → 9,694 | 8,468 → 5,765 | 1 → 4,222 | 385,723 → 385,804 | 61,431 → 61,631 |
| 1,000,000 | 4,600 → 3,900 | 68,700 → 147,100 | 342,300 → 3,400 | 0 → 0 | 7,756,900 → 7,996,100 | 1,323,400 → 1,345,400 |

**The D21 target is now missed.** No performance rule was set for D30, which is a correctness increment; the cost is recorded here.

| | D28 | D30 |
|---|---|---|
| C, 14 workers, confirmation: p95 (by repeat); max | 7.26 s (7.26, 7.22, 7.32); 8.8 s | **13.05 s** (13.05, 13.19, 13.04); 16.1 s |
| C, 8 workers, confirmation: p95; max | 8.39 s; 10.3 s | 12.88 s; 16.0 s |
| Arm B, confirmation, µs per candidate end to end (engine) | 189.2 (57.7) | 244.1 (107.9) |
| Arm B at 300,000, main run, µs per candidate (engine) | 278.6 (82.6) | 380.8 (186.8) |
| Query process, C at 14 workers | 0.86 GiB | 0.82 GiB |
| Pruned share, confirmation | 36.22% | 34.66% (none by the end summaries alone; 2,850 candidates before) |

- *What is still met:* the maximum (16.1 s against 30 s) and memory (0.82 GiB against 4 GB). The p95 is missed by about 30%.
- *Where the cost comes from* (µs per candidate end to end, engine in brackets). At 300,000 patients over the ten main queries, in one process:

  | | Order rule off | Order rule on |
  |---|---|---|
  | Letter-timed store | 338 (118) | 352 (117) |
  | This run's store | 371 (118) | 464 (198) |

  At 10,000 the same four figures are 211, 217, 218 and 243 µs. So the wider mentions alone cost about 10%, and the order rule on wide mentions most of the rest: nearly every `first` or `last` step on a mentioned concept is now uncertain, so it is decided on an envelope or searched twice. This was an ad hoc run beside the attribution, so the absolute figures are inflated.
- *Next step, by D21's rule:* algorithmic work on the Python engine, not Rust. The levers are:
  - the second search of unsafe steps, bounded to the possible targets the posting summaries leave;
  - sharing the order analysis across relaxation sets;
  - a "coded records only" option, which would leave letter mentions out of `first` and `last` (D32 plan).

*Limitations:*
- One machine.
- Synthetic data replicated beyond 10,000 patients.
- The worlds check covers small grids only (two to four events); larger cases rest on the reference and the proofs.
- The attribution's stages are cumulative, so a change that first appears at the order rule might also have come from the mention timing alone.
- The cost split is one ad hoc run.

*Follow-ups:* the D21 target (above, taken up in D31); D32 and D33 as planned (first numbered D31 and D32); `trajectory_graph_worlds.py` extended to Nth and counts (D32 plan, change 3).

**Review of D30, and what it changed** (an independent review of the built increment; its scripts were kept in a scratch directory, `/tmp/rv30b/`, and its cases are tests here). The figures above are from the first D30 run; the regeneration after these fixes replaces them, and the verdicts below are taken on it.
- **Must-fix M1: "safe" missed coupling through another uncertain `first` or `last` step.** `_coupled` asked only whether another ordered step's *picked* event carried the step's concept. When that step's target is itself uncertain, its true target can be any of its possible targets. Counterexample (window [0, 10]; points A {K}, M {J, K} and Z {J}, each in [0, 3]; f the first K, l the last J): "l, then f at least 1 later" is impossible, and "f, then l at least 0 later" holds in every choice of times, yet the engine and the reference both said `possible`. The reference repeated the check, so the differential could not see it, and the worlds check's generator gave every event one concept, so it never met two overlapping families. *Fix:* another ordered step couples when its picked event (if certain) or any of its possible targets (if not) carries the concept, in the engine and, written again, in the reference. The small-case generator now draws events of concept x, y or both, and the review's case is a test. The review's targeted fuzz found 7 violations in 20,000 cases before the fix and none after.
- **Should-fix S1: the attribution localizes, it does not prove.** It names the stage at which a result moved; it does not show that the change is a fix. The stages are cumulative, so the order rule acting on the wide mention ranges is counted under "mention timing" (at 10,000, seed 21's twelve order-rule `unresolved` results all come from the rule on wide mentions). The wording is now "localized to the stages", and the tool and the fixture also report a second ordering (mention timing before the order rule), so the rule's effect on wide mentions is separated.
- **Should-fix S2: an undecided relaxation set turns `relaxed` into `none` without saying so in the counts.** The result is `none`, `complete: false`, note `RELAXATION_ORDER_UNCERTAIN`, and the store counts it as `none`. The convention stays (a relaxed match that rests on an undecided set is not reported), but the scaling and matcher reports and the store query page now count such incomplete `none` results separately, by note. At 1,000,000 most of the drop in `relaxed` (342,300 to 3,400) is this, not the changed queries. Some old relaxed matches were in fact false (seed 7's, the review found).
- **Should-fix S3: the profile changed its definition.** The profile defined `first` and `last` syntactically, by an order on recorded ranges, so the old `last` conformed to the written profile; what changed is the definition, made to agree with the profile's own semantics of `exact` and `possible`. The identifier stays `trajectory-graph-1.0`. A match through the store records the engine's digest in its context; bare `trajectory_graph.match` and the mockups' `/api/trajectory-graph/match` do not, which is a limitation (adding a field to every output would move every result digest). The page's "Nth from last" ordering changed with `last`.
- **S4:** statements that the worlds check finds no wrong claim are scoped to what it covers (below).
- **Latency.** The review profiled the p95 queries: most of the added time is the second search for unsafe steps, mostly arc consistency (`_Arc.revise`, O(n·m) per arc), and that search almost never refutes. Three levers that cannot change any output are built: a sorted sweep for `revise`'s common case (O((n + m) log m)), handling the case where one event is in both domains exactly as before; skipping the exact phase in the second search, whose only question is whether any binding exists; and a direct `certain` test for a family with no shared variables. A fourth lever, one search over an envelope with order facts, would change results and needs a proof; it and pruning on order (a store format change) are in the D32/D33 plan.

**Pre-committed rules for the review fixes** (committed before any measurement of them):
- *Levers.* Each lever is a switch (`trajectory_graph._LEVERS`) and is kept only if, with it on and off, every output is byte-identical: the engine's differential and every engine test, the worlds fuzz, the 300 recorded match digests, the store differentials and fixtures, and the matcher comparison's per-query digests (arms B, `B_unpruned` and C are compared with the switch on; a same-day in-process measurement at 10,000 to 300,000 on the kept D30 snapshots compares on against off). A lever that moves any output is dropped.
- *M1.* Every result M1 moves is a case where another ordered step's possible targets carry the concept; the 300 recorded digests are re-recorded with their stages recomputed, the before digest still reproduced.
- *Everything else as in the rules above:* MIMIC unchanged; every synthetic change localized to a stage; both parity tests pass with every change explained. The D21 target is reported as measured, met or not.

**Measurement and verdicts after the review** (the committed reports). One regeneration, one job at a time, after the review's rules were committed (f2111a1):
1. the MIMIC demo (1,314 s);
2. the scaling run to 1,000,000 with `--keep` (9,216 s);
3. the matcher comparison with the committed settings, arm A on its own 2.0 snapshots (reused from the first run: arm A's code is unchanged, and so are their identifiers), plus `--confirm --large-queries 30 --large-repeats 3` at 300,000;
4. the attribution, on the letter-timed snapshots of the first run (this tree's builder is unchanged, so they are the same snapshots).

Before it, the levers were measured on the kept D30 snapshots, in one process, on against off interleaved, on the same day (single process, µs per candidate end to end, engine in brackets):

| Size (queries) | Levers off | Levers on | Every digest | Slowest query, off → on |
|---|---|---|---|---|
| 10,000 (50) | 227 (106) | 223 (100) | identical | 2.78 → 2.23 s |
| 100,000 (10) | 400 (189) | 358 (139) | identical | 29.3 → 24.1 s |
| 300,000 (10) | 441 (194) | 411 (145) | identical | 97.0 → 82.9 s |

| Rule | Measured | Verdict |
|---|---|---|
| Levers change no output | Byte-identical on and off: the engine's differential, the kernel comparison's wide cases, 3,000 worlds cases and the review cases (a test); the 300 recorded digests (a test); the same-day measurement above at three sizes; and at scale, every per-query digest of arms B, `B_unpruned` and C in this run (levers on) equals the first D30 run's (levers off), at every size and in the confirmation | met: all three kept |
| M1 | 2 of the 300 recorded digests moved (seeds 68 and 248), each by one patient going from `none` to `unresolved`, and each reproduced exactly by the first run's coupling test (picked events only). No benchmark query and no parity result moved. The re-recorded fixture's before digests are still reproduced; the store differential's `unresolved` went from 158 to 160 | met |
| MIMIC unchanged | Every figure other than identifiers, digests, bindings and timings equals D28's | met |
| Synthetic changes localized | 102 of the 300 recorded digests moved since before D30, each localized to stages and recomputed by a test, in both orderings (order rule before mention timing: mention-timing stage only 90, order rule and mention timing 8, last order 1, last order and mention timing 2, all three 1; mention timing before the order rule: mention timing only 55, mention timing then the order rule 38, the order rule only 7, last order 1, last order and mention timing 2, all three 1). The parity test: 57 store results moved (53 at the mention-timing stage, 4 at the order rule; with the timing first, 45, 2 and 12 at both), unchanged by the review fixes. Arm A: every query reproduced at every size and in the confirmation, as in the first run; with the timing first, 1,000: mention timing only 10, mention timing then the order rule 11, the order rule only 2, last order and timing 1, all three 1; 10,000: 13, 12, 2, 0, 4; 100,000 and 300,000: 2, 4, 0, 0, 0; confirmation: 9, 7, 2, 0, 2 | met |
| Scaling | Row and candidate counts equal D28's at every size; the status mixes equal the first D30 run's. Incomplete `none` results, now counted apart: at 1,000,000, 310,600 with note `RELAXATION_ORDER_UNCERTAIN` (and 100 with `RELAXATION_SEARCH_STOPPED` at 10,000). They are most of the drop in `relaxed` (342,300 to 3,400): each was a `relaxed` result before D30, now a `none` whose relaxed search was left undecided by the order rule, and the review found at least some of the old relaxed matches false (seed 7's) | met |
| Parity | Both tests pass, unchanged by the review fixes (336 of 336; 401 of 480) | met |

**The D21 target, regenerated.** At 14 workers it is met again, narrowly; at 8 workers it is not.

| Confirmation, 30 queries × 3 | D28 | D30 first run | D30 after the review |
|---|---|---|---|
| C, 14 workers: p95 (by repeat); max | 7.26 s (7.22–7.32); 8.8 s | 13.05 s; 16.1 s | **9.46 s** (9.44, 9.46, 9.49); 10.2 s |
| C, 8 workers: p95; max | 8.39 s; 10.3 s | 12.88 s; 16.0 s | 11.70 s; 13.1 s |
| Arm B, µs per candidate end to end (engine) | 189.2 (57.7) | 244.1 (107.9) | 231.7 (94.9) |
| Query process, C at 14 workers | 0.86 GiB | 0.82 GiB | 0.83 GiB |

At 14 workers p95 9.46 s against 10 s (a 5% margin, every repeat under 10 s), maximum 10.2 s against 30 s and memory 0.83 GiB against 4 GB: by D21's rule Python stays and no Rust is built. The margin is thin and rests on one machine; the D28 margin was 27%. At 8 workers the target is missed (11.70 s), where D28 met it. The main run at 300,000 (ten queries) gives 9.88 s at 14 workers. The next lever, if a larger cohort or slower machine needs one, is lever 1 of the D32/D33 plan (one envelope search with order facts), then lever 5 (pruning on order).

*Further notes from the review:* `RECORDED_3_0`'s synthetic input digests now rest on the re-recorded values (the converter's own digest is part of the input digest), its table digests on the recording at a81ef03. `_ORDER_RULE`, `_LAST_KEY` and `_LEVERS` are module globals for tests and attribution in one process; workers never see them changed.

## D31 Latency: one envelope search for coupled `first` and `last` steps (lever 1), under rules fixed before measuring

After D30's review the 300,000-patient confirmation (30 queries × 3 repeats) gives a p95 of 9.46 s at 14 workers, a 5% margin on the D21 target, and 11.70 s at 8 workers, which misses (D28: 7.26 and 8.39 s). The D30 review put about 90% of the order rule's time on the p95 queries (seeds 7 and 26) in the second search of unsafe steps, which almost never refutes. This increment replaces D30's two searches with one envelope search where an argument covers the step (lever 1 of the plan for D32 and D33, [d32-d33-store-step-language-plan.md](d32-d33-store-step-language-plan.md)). Nth and counts move from D31 to D32 and relative conditions from D32 to D33, since this increment takes D31.

*Outcome (details at the end of this entry):* lever 1 was built and passed every soundness check, but the regenerated 14-worker confirmation gave a p95 of 9.41 s against the pre-committed bar of 8.5 s, so it was reverted and D30's engine and reports stood. A second attempt under a paired protocol ("D31, second attempt", at the end of this entry) kept it. The design, the argument and the rules below are kept as written; the profile describes the engine as shipped, so it does not carry the argument.

**Where the time goes** (design input, measured on D30's code before these rules, at 10,000 patients over the first 30 queries; counts only). Every unsafe step in the synthetic queries is unsafe because another step can bind one of its possible targets (*coupled*); no family has a shared variable (the synthetic store has none); in a few the step's end is read as well (a `during` from an exposure). Seed 26 is "the first clobazam, then an antiseizure drug": every clobazam event is an antiseizure event, so the `any` step can bind each of the first step's 24 possible targets (on average). D30's picked search finds nothing for 2,918 of 2,936 such patients, its second search then finds a binding, and the result is unresolved after two searches; the second search takes 0.44 of the query's 0.52 engine seconds in the order rule.

**Decision: lever 1.** An unsafe uncertain step is *covered* when its concept's events name no variable another event names and no relation reads its end unless every such event is a point (`_covered`). When every unsafe step of a set is covered, the set is decided by one search in two phases (`_envelope_search`); otherwise D30's path stands for the whole set. Safe and certain steps are decided as in D30.
- *Exact phase.* Each covered unsafe step is a point of its own over its envelope, linked to nothing, as a safe step is. An exact binding is reported `exact`. Its absence proves nothing, since the point has more choices than the true target.
- *Possible phase.* Each covered unsafe step's candidates are its *split* (`_Occurrence.split`), and the phase adds the *order facts* (`_order_facts`). With no binding (a complete search), the set is `none`. With a binding, the set is undecided ('possible'), exactly as D30 leaves it after a binding: `unresolved` with the binding beside it, or a relaxation set passed over. A stopped search is a stopped search, as before.
- *The split,* for a `first` step (mirrored for `last`). Order the possible targets (D30's pairwise test) by earliest start. A target is *blocked* when another step's candidates in this phase include it: an `any` step whose concept it carries, a certain ordered step whose event it is, or an unsafe ordered step among whose possible targets it is. Let m count the steps that block some target, and λ be the least earliest start at which the targets starting no later include an unblocked one, or more than m blocked ones. The candidates are a point over [λ, least latest start], then the blocked targets whose earliest start is below λ, as themselves. With no λ, the blocked targets alone.
- *The order facts,* each between two steps' starts:
  - (F1) for a covered unsafe step s on concept K and any other step t: s starts at or before t (at or after, for `last`) when t is bound to one of the patient's own events carrying K;
  - (F2) for two ordered steps s and u: s starts at or before u when s is `first`, u is `last` and some event carries both concepts, or both are `first` and every event of u's concept carries s's; s starts at or after u when both are `last` and every event of u's concept carries s's.
- *Not used in the exact phase.* The facts would make more bindings exact. But certainty is decided pair by pair, and through a third variable (a `first` and a `last` step both at or before or after an event a third step binds) a fact can bear on a pair it does not name, so the pairwise test would no longer decide the model exactly. They are left to a later increment.

**Why exact and none are sound** (by over-approximation; the same argument is in the profile). Write F for the step's family (the patient's events carrying its concept), P for its possible targets, [a, b] for its envelope over the patient's ranges (`Times.dom`), and T(w) for the true target in a choice of times w.
1. *What every choice of times gives the target.* T(w) is in P, since in w it starts at or before every other event of F, so it passes the pairwise test. Its start lies in [a, b]: it is at least its own earliest start, so at least a, and at most every other event's start, so at most b. It starts at or before every event of F. These hold over `Times.dom`, the ranges every choice of times satisfies, which already carry shared variables and `proper`.
2. *Exact phase.* Map w to the model by giving each covered unsafe step's point the start of T(w), and every other variable its value in w. The point's value lies in its range, by 1. Every constraint reads the same value in both:
   - a constraint on the step's start reads the target's start;
   - one on its end only where every event of F is a point, whose end is its start;
   - one on another step reads that step's event, unchanged, even when that event is T(w) itself (the map keeps both values equal).

   Scope, `after`, `during` and the anchors are all such constraints. So a binding of the `any` steps that holds in every choice of the model holds in every true choice: `exact` is sound.
3. *Possible phase.* Take a true binding that holds in w. Map it to the model: if T(w)'s earliest start is at least λ, the step takes the point, whose value is T(w)'s start (in [λ, b], by 1); otherwise T(w) is blocked with earliest start below λ (by λ's choice every unblocked target starts no earlier than λ), so it is a candidate itself. The facts hold in w:
   - F1, by 1;
   - F2, since the first of s's family is at or before a common event, which is at or before the last of u's, or since u's target is in s's family.

   Every stand-in (the event, the envelope point, a possible target as itself) maps to the target's start, so the facts hold whatever stands for each step. So the model has a binding wherever the truth has one, and a model with none proves `none`. The facts only remove choices: leaving them out still over-approximates (they are never needed for soundness, and decide more).
4. *Why no decided result is lost* (the possible phase refutes whatever D30's second search refutes). Take a binding of the model with the step on its point at time v. Some target starts no later than λ, is unblocked or one of more than m blocked ones, so at least one is bound by no other step; its latest start is at least b ≥ v (every event of F has latest start at least b), so it can take time v. Its variable is its own (a covered step's family names no shared variable), its end is not read, and its own end is chosen within its range. So the same binding with the step on that event holds in D30's second search, which therefore finds a binding too. With several covered steps the events are picked one at a time, each among targets not yet taken; m counts every step that can take one. A blocked target as itself is a candidate of D30's second search too. D30 says `none` only when its second search, over every possible target, finds nothing, so the envelope search says `none` there as well (if its own search completes).
5. *Relaxations and costs.* Each relaxation set is decided on its own concepts and bounds, so the argument holds set by set. The order of sets by cost and D30's conventions are unchanged: an undecided set is passed over, and the result says so (`complete: false` and its note).
6. *Search budgets and `complete`.* Each phase has the query's search budget, as each of D30's searches had. A stopped possible phase concludes nothing: the set is reported as a stopped search (`SEARCH_BUDGET_EXHAUSTED` unrelaxed, a stopped set otherwise). `exact` and `none` carry `complete: true`, as D30's decided results do. The envelope search can stop where D30's did not, or the reverse; the result is undecided either way.
7. *Shared variables.* A covered step's family has none, but other events may: the map keeps every other variable, so their links stand. In the possible phase the point is linked to nothing, and the facts link starts only by order edges, which the joint test (a temporal network over the binding) includes. A family with a shared variable keeps D30's path: soundness (1 to 3) would hold, but 4 needs each target's variable to be its own.
8. *`proper`.* It narrows `Times.dom`, from which the envelope, λ and the possible targets are computed. In the map the point takes the target's start, which in w already satisfies the target's own order with its end; the point has no end of its own.
9. *`during` and ends.* A relation from the step reads its end, and the point has none. A covered step's end is read only where every event of F is a point; an exposure family under a `during` from the step keeps D30's path.
10. *Time pruning.* Unchanged and still exact. A pruned patient's sets are impossible over every event of the concept. The envelope point lies within the posting's [min_start_lo, max_start_hi], its end (its start) within max_end_hi, and the blocked targets are the concept's own events. So both phases empty by arc consistency, and the engine gives the same `none` with `complete: true`.
11. *Excluded records (D18).* The binding of an `exact` result names the picked event for the envelope point, as a safe step's does. The displacement test, which compares an excluded record with the picked event's recorded range, is at least as wide as one against the envelope.

**The reference.** `trajectory_graph_reference.py` writes the rule again from the profile:
- coverage, the blocked targets, m and λ by its own satisfiability tests;
- the points as new variables;
- every binding enumerated over the same candidate sets, each decided by Bellman–Ford over the patient's facts, the points, and the order facts that apply to that binding.

It shares no decision code with the engine.

**Pre-committed rules** (written and committed before any measurement of lever 1):
- *Soundness.*
  - The independent worlds checker (`trajectory_graph_worlds.py`) finds zero violations: on at least 20,000 ad hoc cases from the multi-concept generator (`tiny_case`: events of concept x, y or both), on at least 20,000 from a generator of two or three ordered steps over overlapping families (after the D30 review's `fuzz3.py`), and on the test suite's own volume (3,000 small cases and every review case).
  - Every D30 review counterexample is sound.
  - Planted defects:
    - order facts dropped. This cannot make a result unsound (point 3), so the worlds checker cannot catch it; it must be caught by the differential against the reference, and by named cases whose decision needs the facts (the D30 review's M1 cases);
    - a fact reversed, a conditional fact applied to every event, the split's point started above λ, and an exact claim made on the split: each must be caught by the worlds checker.
  - The engine and the reference agree on every differential case.
- *Result movement,* against D30: this tree with `_ENVELOPE` off, which must reproduce D30's recorded digests.
  - Every changed result goes from undecided to decided. Undecided means `unresolved`, or `complete: false` with an order note (`RELAXATION_ORDER_UNCERTAIN`, `CHEAPER_RELAXATION_ORDER_UNCERTAIN`); decided means `exact`, `possible`, `relaxed` or `none` with `complete: true`.
  - On small cases every changed result is confirmed by the worlds checker.
  - No decided result changes.
  - A change inside an undecided result is not a decision. Such changes are counted and listed apart and do not fail this rule: the match reported beside `unresolved` (certainty, binding, method), the relaxed match beside it, the reason, an incomplete result's note, or an `unresolved` result that becomes `none` with `complete: false`.
  - MIMIC results unchanged. Its times are exact, so no target is uncertain.
  - The 300 recorded match digests are re-recorded with each seed's movement in a committed fixture (D30 digest reproduced with the switch off, new digest, each changed result's transition), which a test recomputes.
  - At every size and in the confirmation, an attribution reproduces each moved query's D30 digest with the switch off and its new digest with it on, and counts each moved result's transition.
- *Keep lever 1 only if* the 14-worker confirmation p95 (all runs pooled) is at most 8.5 s, at least 10% under D30's 9.46 s (a margin of about 15% on the target), with the maximum at most 30 s and query-process memory under 4 GB. The 8-worker figures are reported too; getting back under 10 s there is the stretch goal. Otherwise lever 1 is reverted, its numbers recorded here, and the next step stated.
- *Calibration first.* Before the regeneration, D30's code (b46d9bb, from a git archive) and this tree are measured on the same day, on the same snapshots, in process (arm B), at 10,000, 100,000 and 300,000 patients, interleaved and repeated, to put a figure on run-to-run drift (the D30 review saw about 5 to 10%).
- *Lever 5* (pruning on order; store format 3.2) is built only if lever 1 is not enough, under its own rule committed before it is measured; otherwise it stays the next lever.
- A failure is fixed, never tuned away.

**Built and tested** (e14617a; the implementation stays in this branch's history for a later attempt). The engine's `_envelope_search` and its switch `_ENVELOPE`, the reference written again, 4 new engine tests and a store test, the 300 recorded digests re-recorded with each result's transition, and the attribution tool extended by a D31 stage. Results on small cases:
- *Soundness.* The worlds checker found no violation: 3,000 multi-concept cases, 1,500 cases of two or three mostly ordered steps and the review cases in the test suite; and ad hoc, 20,000 of each generator. Every D30 review case is sound. The M1 case "the last J strictly before the first K" is now `none`, which is true. The other M1 case stays `unresolved`, since it needs a fact in the exact phase.
- *Planted defects.* All four soundness defects are caught by the worlds check: a fact reversed, a conditional fact applied to every event, the split's point without its blocked targets, and exact claimed on the split. Dropping the order facts is not caught by the worlds check, as expected (point 3); it is caught by the differential against the reference and by the M1 case.
- *The reference* agreed on every case: the 4,500 of the test and 40,000 ad hoc, statuses, what is reported beside them, and every reported binding.
- *Movement on small cases.* Against D30 (the switch off), every changed result started undecided:
  - 1,038 of the 20,000 multi-concept cases moved to a decision and 800 stayed undecided;
  - 2,837 of the 20,000 ordered cases moved to a decision and 2,151 stayed undecided;
  - no decided result changed.

  The changes that stay undecided are mostly `unresolved` results whose beside-match changed: D30 found `exact` on the picked event, while the envelope search finds only a possible binding.
- *Other suites.* All engine, store and SULO suites pass. Both parity tests are unchanged (336 of 336; 401 of 480): no parity result moved.

**Measurement** (after the rules were committed, 0b4189c; one heavy job at a time).

*Calibration, in process* (arm B, one worker, fresh process per run, interleaved D30, D31, D30, D31, on the same snapshots, which are D30's: the same identifiers at every size):

| Size (queries) | D30: µs per candidate end to end (engine), two runs | D31 | Slowest query, D30 → D31 |
|---|---|---|---|
| 10,000 (50) | 221.3 (99.3), 221.9 (99.4) | 219.0 (96.5), 219.0 (96.7) | 2.20 → 2.18 s |
| 100,000 (10) | 327.9 (135.1), 328.9 (135.6) | 326.5 (132.8), 325.9 (131.8) | 22.4 → 22.2 s |
| 300,000 (10) | 328.2 (134.7), 328.4 (135.0) | 325.9 (132.3), 326.5 (132.8) | 69.0 → 68.1 s |

- *Drift.* Run-to-run drift in process is under 0.5%, and each code's repeats give identical digests.
- *What moved.* D31's digests differ from D30's on 7 of 50 queries at 10,000 and 1 of 10 at 100,000 and 300,000.
- *Why the gain is small here.* The first ten queries include seed 7 but not seed 26. Lever 1 hardly moves seed 7, whose order rule acts in a relaxation set (engine 0.84 s with the rule off, 1.15 s in D30, 1.11 s in D31 at 10,000). It brings seed 26 close to the rule's absence (0.64 off, 1.15 in D30, 0.75 in D31).

*Calibration, arm C* (not in the rules; added to read drift at the target's own measure). The confirmation queries (30 × 3) at 300,000 were run in each order, each run a fresh process:

| Order | 14 workers: p95 (max) | 8 workers: p95 (max) |
|---|---|---|
| D30 then D31 | 9.39 (10.15) → 8.21 (10.26) s | 11.85 (13.12) → 11.32 (14.85) s |
| D31 then D30 | 8.20 (10.35) → 9.46 (10.24) s | 10.03 (12.95) → 13.75 (16.14) s |

- *At 14 workers* the two orders agree: lever 1 gives 8.2 s against 9.4 s, −12.5%.
- *At 8 workers* the second run of a pair is 10 to 16% slower whichever code it runs, so drift there is as large as the effect.

*The regeneration:*
1. the MIMIC demo (1,401 s);
2. the scaling run to 1,000,000 with `--keep` (9,331 s);
3. arm A's own 2.0 snapshots;
4. the matcher comparison with the committed settings, then `--confirm --large-queries 30 --large-repeats 3`.

The attribution (letter-timed snapshots and the extended tool) was stopped once the verdict below was taken. Its 1,000 snapshot had been built.

| Rule | Measured | Verdict |
|---|---|---|
| Soundness | 0 violations (small cases above, 44,500 in all, with the review cases); four soundness defects caught; facts dropped caught by the reference and M1 | met |
| Movement, small cases | Every change from undecided, none of a decided result; each moved result worlds-checked | met |
| Movement, 300 recorded digests | 23 seeds moved, 147 results: 132 to a decision (62 `unresolved` → `none`, 44 incomplete `none` → `none`, 24 `unresolved` → `exact`, 2 `unresolved` → `not_comparable`), 15 within undecided. With the switch off every D30 digest is reproduced | met |
| Movement at scale | Partly established: the attribution was stopped. Ad hoc on the 10,000 snapshot (50 queries), 7 queries moved; 6,664 results to a decision, 617 within undecided, and **4 `not_comparable` → `relaxed`** (2 complete, 2 with `CHEAPER_RELAXATION_ORDER_UNCERTAIN`). By the rule's labels these are not changes from undecided, though `not_comparable` is D18's verdict read off the result. The likely mechanism, not established: D30's `not_comparable` came from the displacement test reading the binding beside an undecided result, and lever 1 decides the unrelaxed query instead. At 1,000 all 711 changes are from undecided. In the confirmation, the digests of seeds 7, 26 and 29 moved | not met as written (4 results at 10,000; other sizes not attributed) |
| MIMIC unchanged | 0 differences from D30's report other than identifiers, digests, bindings and timings | met |
| Scaling | Snapshot identifiers, table rows and candidate counts equal D30's at every size. At 1,000 `unresolved` fell from 398 to 38, at 10,000 from 4,222 to 375. At 100,000 and above the 10 queries' statuses are unchanged, but the incomplete `none` results fell from 31,060, 93,180 and 310,600 (all `RELAXATION_ORDER_UNCERTAIN`) to 6,060, 18,180 and 60,600 (all `RELAXATION_SEARCH_STOPPED`) | met |
| **Keep lever 1 only if the 14-worker confirmation p95 ≤ 8.5 s** | **9.41 s** (by repeat 9.99, 9.14, 9.03); maximum 11.85 s; 0.82 GiB | **missed: lever 1 reverted** |
| 8 workers (stretch: under 10 s) | 9.90 s (9.75, 10.11, 9.90); maximum 13.13 s | reported |

**Why the confirmation missed, as far as it is known.** Lever 1 did what it was built for: in the confirmation's arm B, seed 26's engine time fell from 33.6 to 21.6 s. At 8 workers, seed 26 fell from 11.70 to 9.90 s and every query lever 1 does not touch took what it took in D30 (seed 15: 9.75 against 9.87 s, seed 25: 8.46 against 8.45 s). At 14 workers, those untouched queries ran 10 to 16% slower than in D30's confirmation (seed 15: 7.94 → 9.14 s, 19: 6.00 → 6.97, 25: 6.92 → 7.74, 11: 7.57 → 8.69), and seed 26 came in at 9.12 s. The 14-worker arm runs last, after about 90 minutes of continuous load. The same-day calibration measured 8.2 s for this code at 14 workers, in either order. So the miss is probably drift in the 14-worker arm, but that is not established, and the rule was fixed before measuring so that a miss is not argued away: the regenerated confirmation is the verdict.

**Decision: lever 1 is reverted.** The engine, the reference, the tests, the fixtures, the attribution tool and the three reports are D30's again, byte for byte (b46d9bb), so D30's committed reports, the 300 recorded digests and every other figure stand as they were. The decision log keeps the design, the argument, the rules and these numbers; the implementation is in e14617a.

**What is next.**
1. *The measurement first.* The 14-worker confirmation is the verdict, and in this run the queries lever 1 does not touch varied by 10 to 16% between two regenerations of it. A lever worth 10 to 12% cannot be judged against that. The protocol needs a fix decided before the next measurement, for example the D30 and new code interleaved in one confirmation, or the 14-worker arm run first and cool. That changes a committed protocol, so it is the owner's call.
2. *Lever 1 again, with two corrections from this attempt:*
   - the movement rule should classify a result by the engine's decision before D18's rule (or the displacement test should not read a beside-match), which settles the four `not_comparable` results;
   - the order facts could join the exact phase once certainty is decided with them jointly.
3. *Lever 5* (pruning on order, store format 3.2) is not built. Under D31's rule it was to follow only if lever 1 was not enough, and judging it needs the same measurement fixed first. It also helps only where pruning can act, which on seed 26 is 12,960 of 106,230 candidates.
4. *The D21 target* stands as D30 left it: met at 14 workers with a 5% margin (9.46 s), missed at 8 (11.70 s).

*Limitations:*
- One machine.
- Synthetic data, replicated beyond 10,000 patients.
- The worlds check covers small grids only.
- The movement at scale was attributed only at 1,000 and 10,000, ad hoc.
- The cause of the 14-worker slowdown is not established.

### D31, second attempt

The owner asked for a second attempt at lever 1 with the two corrections named above, under a measurement protocol changed first. This is recorded here, inside D31; the plan's D32 and D33 keep their numbers.

**Part A: a paired, interleaved confirmation (approved by the owner).** Run-to-run drift on this machine was 10 to 16% between two regenerations of the confirmation, as large as the effects the rules judge. From this attempt on, every "keep only if faster" rule is judged by `tools/benchmark_matcher.py --paired-confirm` (protocol `paired-interleaved-1.0`, described in [execution-store.md](../execution-store.md), "Matching cost"):
- *Two codes, same snapshot.* A reference code, a git ref extracted into a scratch directory as arm A's is, and the candidate (this tree), on the same snapshot and the same 30 seeded queries, each run a fresh process with a warm pool, as arm C.
- *Interleaved.* For each repeat and worker count the two run back to back; the reference runs first in even repeats and second in odd ones (ABBA), so slow drift falls on both alike. Four repeats, so that each order occurs twice; worker counts 14, then 8, within each repeat.
- *Recorded.* Per-query paired times; each run's p95, maximum and total; per pair the ratios (candidate over reference) of p95 and of total time, also split by which code ran first; the candidate's absolute D21 check over its own runs in the session (p95 pooled and by repeat, maximum, query-process memory); the two codes' per-query digests compared, each code's digests checked stable across its runs, and every digest listed where they differ.
- *What it can show:* a difference between two codes measured minutes apart on the same machine state, with drift that is slow against a pair cancelled to first order, and whether the difference holds in every pair and in both orders.
- *What it cannot show:* noise inside one run (one slow query in one run moves that pair's p95); drift faster than a pair; an effect that depends on what ran just before in a way the two orders do not balance; anything about another machine or day. The p95 of 30 queries is the second-slowest query, so a ratio of p95s may compare two different queries. The absolute target is still judged on one session.

**Part B1: the four `not_comparable` results at 10,000, and D18.** The first attempt guessed that D18's displacement test read the match beside an undecided result. Reproduced on the 10,000 snapshot (lever 1 reapplied from e14617a, the engine's own results captured before the store's rules), that guess is wrong:
- *The synthetic store has no excluded records* (0 rows at every size), so D18's rule never acts on synthetic results.
- *The four come from the coverage rule.* All four are seed 34 ("the first seizure frequency, the last sodium-channel blocker, any seizure frequency", relaxations: widen the second step, extend R3). D30's engine said `none` with `complete: false`, note `RELAXATION_ORDER_UNCERTAIN`: the relaxation sets were left undecided. The coverage rule turns every engine `none` into `not_comparable` when a step has no recording source covering the window, complete or not, and here it gave `NO_SOURCE_COVERAGE`. Lever 1 decides the sets: the engine says `relaxed` (exact, by `wS1` and `xR3`), complete for two patients and with `CHEAPER_RELAXATION_ORDER_UNCERTAIN` for two. The coverage rule reads only `none`, so the result stays `relaxed`.
- *By the engine's own decision* these are changes from undecided (an incomplete `none`) to a decision (two complete `relaxed`) or to a still incomplete result (two). That is what the first attempt's label, read off the store's result, could not see.
- *The coverage rule and `relaxed`.* A `relaxed` result also asserts that no unrelaxed binding exists, and here no source covered the window for any step. The documented rule leaves this alone ("Coverage never changes an exact, possible, relaxed or unresolved result"; "where a relaxation needs a window wider than coverage (`unresolved`), not yet distinguished", [execution-store.md](../execution-store.md), "Comparability"). This is a stage-1 limit as documented, not a defect of D30 or D31; it is listed as a follow-up for the owner, not changed here.
- *A bug in main, found while testing the guess.* The excluded-records rule does read the match beside an order-uncertain `unresolved` (`unverified_match`), as D30 decided and as the store's reference does. But `match` fetched bound events' starts only for a result's own binding or its relaxed match, so on such a result with an excluded record of a `first` or `last` step's concept the store raised `KeyError` instead of answering. Concrete case (a test now): the first X is uncertain (x1 may start from minute 0 to 50, x2 from 10 to 20), another step binds any X, and an excluded X lies before both; the reference says `not_comparable`, main's store raises. No committed report reaches it: the synthetic stores have uncertain times and no excluded records, the MIMIC demo has excluded records and exact times. *Fix:* the starts are fetched for that binding too (`execution_store.match`). The rule itself is sound: reading the beside match turns one result that claims nothing (`unresolved`) into another (`not_comparable`); no claim the worlds check reads is made either way.

**Part B2: the order facts in the exact phase too.** The first attempt left them out of the exact phase because certainty was decided pair by pair, and through a third variable a fact can bound a pair it does not name. They now join it, with certainty decided jointly:
- *Where.* After the possible phase finds a binding (or stops) and the exact phase without facts finds none, the exact phase runs again with the facts that can apply to its candidates (`_exact_with_facts`): F2, and F1 where the other step is an `any` step or a certain `first` or `last` step (an unsafe or safe step is on a point there, an event of no one, so F1 on it never applies). A binding found is `exact`; otherwise the set stays undecided as before. A switch (`_EXACT_FACTS`) turns this off, for the tests and the attribution.
- *Joint certainty* (`_certain_jointly`). For a full binding, the network over its variables (their ranges and the distances between linked ones, which carry everything the patient's minimal network says of them), with each point's range and the facts the binding meets as edges. Its shortest paths are the exact bounds of every difference over the choices of times the model allows; a constraint is certain when both bounds lie within it.
- *Search.* Domains are pruned by the pairwise plausibility tests with the facts (`_fact_arcs`), and each prefix of a binding must be jointly feasible with the constraints and its facts; full bindings are tested for joint certainty. The search has the query's budget; a stopped search concludes nothing.
- *The reference* (`trajectory_graph_reference.py`, its own code) adds the facts a binding meets to the patient's facts and decides each constraint of each exact-phase binding by Bellman–Ford over all of them, which is joint by construction (`EXACT_FACTS`).

*Why it is sound* (extends point 2 of the argument above). Map a true choice of times w to the model as in point 2: each point (of a covered unsafe step, or of a safe step) takes the start of its true target, every other variable its value in w.
1. The facts the binding meets hold there. F1 applies only where the other step is bound to one of the patient's own events carrying K (an `any` event, or a certain step's event), and the step's true target starts at or before every event of its family (point 1). F2 holds as in point 3: it relates the true targets' starts, and each point takes its true target's start.
2. So every true choice maps into the model's choices restricted by those facts. A binding whose every constraint holds over all the restricted choices holds in every true choice, with the same values: `exact` is sound.
3. The restricted choices are never empty (the true ones map into them), so a certain binding holds in some restricted choice. It therefore passes the pairwise plausibility tests with the facts, and every prefix is jointly feasible: the pruning keeps it.
4. The joint network decides certainty over the restricted choices exactly. The facts link only the binding's step starts, so any path through the rest of the patient between two of the binding's variables is bounded by their distance in the patient's minimal network, which the network carries.
5. Nothing decided is lost. The phase runs only where the set was undecided, after the factless exact phase (any binding certain without the facts is certain with them), and it can only add `exact`.

*Development check before these rules* (disclosed, not a rule measurement): at 10,000 patients over 50 queries, in process, the exact phase with facts ran 2,520 times, found no exact binding, stopped on the search budget 1,712 times, and cost 0.6 s of 32.9 s.

**Pre-committed rules for the second attempt** (written and committed before any measurement of it):
- *Soundness*, as the first attempt:
  - the worlds checker finds no violation on at least 20,000 ad hoc cases from each generator (`tiny_case`, multi-concept; `ordered_case`, two or three mostly ordered steps over overlapping families), new seeds, and on the suite's own cases (3,000 and 1,500, and every review case);
  - the reference agrees on every one of those cases (status, what is reported beside it, and every reported binding by `binding_reaches`);
  - the planted defects are caught: the first attempt's five (facts dropped, by the reference and the M1 case; a fact reversed, a conditional fact applied to every event, the split without its blocked targets, exact claimed on the split, by the worlds check), and two in the exact phase's facts, each caught by the worlds check: an exact-phase fact reversed, and an exact-phase conditional fact applied to every event. Dropping the exact-phase facts cannot be unsound (it is the first attempt); the reference and the second M1 case catch it.
- *Movement*, against D30 (this tree with `_ENVELOPE` off, which must reproduce D30's digests), classified by the engine's own result (`trajectory_graph.match_prepared`, before the store's coverage and excluded-records rules):
  - undecided means `unresolved`, or any result with `complete: false`; decided means `exact`, `possible`, `relaxed` or `none` with `complete: true`. (The owner's wording names `unresolved` and an incomplete `none`; an incomplete `relaxed` or `possible` is undecided in the same way, a cheaper set or the exact search left open, so it is counted as undecided too.)
  - Every changed engine result starts undecided. It ends decided, or still undecided (counted and listed apart, as in the first attempt, not a failure).
  - No decided engine result changes. The only allowed exceptions are proven fixes of a D18 bug found in part B1; the bug found raised an error and moved no recorded result, so none is expected, and any would be listed.
  - On small cases every changed result is confirmed by the worlds check.
  - MIMIC results unchanged.
  - The 300 recorded digests are re-recorded with each seed's movement in a committed fixture: D30's digest reproduced with the switch off, the first attempt's (exact-phase facts off), the new digest, and each changed result's transition by the engine's decision and by the store's status; a test recomputes it.
  - At every size and in the confirmation the attribution localizes every moved result and classifies it by the engine's decision.
- *Latency, under the paired protocol, against main's code (17728fc), at 300,000 patients, 30 queries, 4 repeats, 14 and 8 workers.* The verdict is the paired confirmation run before the regeneration, on the 300,000 snapshot this tree builds (D30's snapshot identifier):
  - **keep lever 1 only if** at 14 workers the paired p95 ratio is at most 0.90 in every one of the four pairs, and this tree's own p95 over its 14-worker runs is at most 10 s, with the maximum at most 30 s and query-process memory under 4 GB;
  - the 8-worker ratios and absolute figures are reported; under 10 s there is the stretch goal;
  - the regeneration repeats the paired confirmation into the committed report (against 17728fc; the candidate is this tree if lever 1 is kept, or the lever 1 commit as a git ref if it is reverted). If lever 1 was kept and this replication misses the bar, lever 1 is reverted after all;
  - otherwise lever 1 is reverted again and the numbers recorded.
- *Whatever the verdict,* the D18 fix and the paired protocol stay. Both change files the committed reports bind (`execution_store.py`, `test_execution_store.py`, `benchmark_matcher.py`), so the MIMIC demo report, the scaling report, the matcher report and the attribution are regenerated in either case, one heavy job at a time.
- A failure is fixed, never tuned away.

**Built and tested** (rules fe43d0b, implementation 036fff2). Lever 1 is e14617a's, reapplied; the exact phase with facts, its switch and the reference's are new; the D18 fix and its test; the movement fixture and the attribution classify by the engine's own result; the paired mode and its tests (on hand-made runs, and on a 96-patient store with the committed tree as reference). The paired mode was also tried at 1,000 patients (6 queries, 2 repeats, 1 and 2 workers) and at 10,000 (12 queries, 2 repeats, 1 and 4 workers): order alternated as specified, and at 10,000 the one moved query (seed 7) was listed with both codes' digests.

**Measurement** (after the rules; one heavy job at a time). The new snapshots have new identifiers, since the builder's digest (`execution_store.py`) changed with the D18 fix; their tables' rows and every query's candidates equal D30's at every size. The 300,000 snapshot of the verdict (built by this tree alone, `115e4243…`) is the one the scaling run then built again, with the same identifier.

| Rule | Measured | Verdict |
|---|---|---|
| Soundness, worlds check | 0 violations: 20,000 `tiny_case` and 20,000 `ordered_case` cases ad hoc (seeds `d31b-tiny`, `d31b-ordered`), and the suite's 3,000, 1,500 and review cases | met |
| Soundness, reference | Agrees on all 40,000 ad hoc cases and the suite's (status, beside it, every reported binding) | met |
| Planted defects | The first attempt's five caught as before; an exact-phase fact reversed and an exact-phase conditional fact applied to every event both caught by the worlds check (125 and 4 unsound results over the suite's cases); the exact-phase facts dropped caught by the reference (24 disagreements) and by the second M1 case (back to `unresolved`) | met |
| Movement, small cases | Against D30: 1,148 and 3,051 results to a decision, 737 and 1,960 within undecided (multi-concept, ordered); against the first attempt: 69 and 171, all to a decision (mostly `unresolved` → `exact`); no decided result changed; every new result passes the worlds check | met |
| Movement, 300 recorded digests | 23 seeds moved (all by the first attempt's part; the exact-phase facts move none); 149 engine results, every one from undecided: 134 to a decision (64 `unresolved` → `none`, 46 incomplete `none` → `none`, 24 `unresolved` → `exact`), 15 within undecided. By the store's status two of these are `not_comparable` → `not_comparable` and two `unresolved` → `not_comparable` (the coverage rule on an engine `none`). With the switch off every D30 digest is reproduced | met |
| Movement at scale (attribution) | Every arm A and D30 digest reproduced at every size and in the confirmation. Engine results moved by D31, all from undecided: 1,000: 658 to a decision, 70 within; 10,000: 6,893 and 676; 100,000: 27,270 and 6,630; 300,000: 81,810 and 19,890; confirmation: 178,140 and 19,890. None is a decided result. The four results of part B1 are among the 10,000 figures (incomplete `none` → `relaxed`, two complete) | met |
| MIMIC unchanged | 0 differences from D30's report beyond identifiers, digests, bindings and timings (snapshot `3fe29499…`) | met |
| Scaling (not a rule; a test's bound) | Rows and candidate counts equal D30's at every size; status mixes as the first attempt's. The first scaling run's 1,000,000 build peak, **8.61 GiB** at the Parquet write, broke the D23 test bound as it then stood (under 1.05 × the 300,000 peak: 1.05 × 8.02 = **8.42 GiB**) with the builder unchanged since D30 (8.23 GiB); the scaling run was repeated once, unchanged, and gave 8.28 GiB with the same snapshots and status mixes, which is the committed report. Both runs are recorded; the spread of that peak (at least 4%) is close to the bound's margin. Owner decision: the bound is widened to 1.10× (below) | recorded; bound widened by the owner |
| **Latency, paired verdict (P1)**, 14 workers: p95 ratio ≤ 0.90 in every pair; own p95 ≤ 10 s; max ≤ 30 s; memory < 4 GB | `verification/d31-paired-verdict.json`. Ratios 0.875, 0.861, 0.804, 0.844 (reference first, candidate first, reference first, candidate first); pooled over the four pairs 0.899. This tree: p95 8.49 s pooled (8.10, 8.14, 8.80, 8.21 by repeat), max 11.64 s, 0.76 GiB; main's code in the same session: 9.45 s pooled | **met: lever 1 kept** |
| Paired replication in the regeneration (P2) | `paired_confirmation` of the matcher report. 14 workers: ratios 0.831, 0.843, 0.816, 0.855, pooled 0.886; this tree p95 8.77 s pooled (9.20, 8.35, 8.61, 8.46), max 12.02 s, 0.73 GiB; main's code 9.90 s | met |
| 8 workers (stretch: under 10 s) | P1 (`verification/d31-paired-verdict.json`): ratios 0.851, 0.795, 0.830, 0.833; this tree p95 10.17 s pooled (9.81 to 11.58), max 13.38 s. P2: ratios 0.826, 0.825, 0.833, 0.924; p95 11.56 s pooled (10.05 to 12.57), max 16.62 s | reported; stretch missed in both |

*What the gain is, and is not.* The p95 ratio comes from one query. Both the per-pair ratios and the pooled ones (0.899 in P1, 0.886 in P2, every run's 30 queries of a code pooled) rest on the p95, which over 30 queries is the second-slowest query; the pooled ratio is close to the bar although every pair is under it. Total time per run barely moves (ratios 0.97 to 1.02 at 14 workers in P1, 0.97 to 0.99 in P2). Over the four pairs, seed 26 ("the first clobazam, then an antiseizure drug") takes a median 9.59 → 8.17 s at 14 workers in P1 (10.22 → 8.53 s in P2), and at 30 queries the p95 is the second-slowest query, which for main's code is seed 26. Seed 7, the slowest query and the maximum, is unchanged (10.34 → 10.38 s). Seed 29 is slower (6.50 → 7.00 s in P1, 6.88 → 7.36 s in P2). The development check at 10,000 points at the exact phase with facts: there, with it off, seed 29 took 1.21 s against 1.44 s on, and the phase decided nothing on the synthetic cohort. So the second correction costs a little here and gains nothing measurable at scale; it is kept because it is sound and decides cases the small-case checks show (240 of 40,000), and its cost is inside the verdict.

*The old confirmation, regenerated* (`--confirm`, 30 queries, 3 repeats, arms run one after another, kept in the report): at 14 workers p95 9.89 s (9.34, 9.50, 10.17), max 13.25 s; at 8 workers 9.87 s (9.87, 9.79, 9.99), max 13.20 s; arm B 229.7 µs per candidate end to end (engine 93.7; D30 231.7 and 94.9); query process 0.82 GiB. The 14-worker arm runs last, about 90 minutes into the job, and is again slower than the same code in the paired session (8.49 and 8.77 s). Under the new protocol the D21 target is judged on the paired runs; on those it is met at 14 workers with a 12 to 15% margin, and missed at 8.

*Byte identity.* In both paired runs the two codes agree on 27 of 30 queries; seeds 7, 26 and 29 differ, as the attribution localizes, and each code's digests are stable across its runs. Arms B, `B_unpruned` and every C count agree at every size and in the confirmation.

**Decision: lever 1 is kept, with the order facts in the exact phase.** The engine, the reference, the fixtures, the attribution tool and the reports are this branch's. The D21 target: met at 14 workers in the paired session (p95 8.49 s, replicated at 8.77 s), not met at 8 workers (10.17 and 11.56 s).

*The scaling regeneration's first run* (aggregates; its report was replaced by the second run's, kept in scratch, not committed). 9,203 s in all. By size (build seconds, build peak, single-process `match()` p50): 1,000: 6 s, 0.36 GiB, 0.05 s; 10,000: 51 s, 0.85 GiB, 0.41 s; 100,000: 514 s, 4.61 GiB, 1.23 s; 300,000: 1,534 s, 8.02 GiB, 3.32 s; 1,000,000: 5,187 s, **8.61 GiB**, 11.76 s. Snapshot identifiers, rows, candidates and status mixes equal the committed second run's (9,131 s; 1,000,000: 5,123 s, 8.28 GiB, 11.76 s). **Owner decision: the D23 bound is widened to 1.10×.** The 1,000,000-patient build peak must be at most 1.10 × the 300,000 one (`tools/test_benchmark_execution_store.py`). The peak varies by at least 4% between identical runs (8.61 and 8.28 GiB at 1,000,000 with the builder unchanged, against 8.02 GiB at 300,000: 1.07× and 1.03×), so 1.05× failed on noise; the gate exists to catch memory growing with patient count, which 10% still catches (1.10 × 8.02 = 8.82 GiB). Both runs pass the widened bound. The committed report is the second run's; no report records or binds the bound, so nothing was regenerated for it.

*Follow-ups:*
- The coverage rule and `relaxed` (part B1): a `relaxed` result whose unrelaxed `none` rests on steps no source covered is reported as `relaxed`. Documented as a stage-1 limit; whether coverage should turn it `unresolved`, as the excluded-records rule does, is the owner's call.
- The exact phase with facts costs seed 29 about 7 to 11% at scale and decided nothing there; at 10,000 it ran out of its search budget in 1,712 of its 2,520 runs. A smaller budget for it (or skipping it when no fact could make a constraint certain) is a cheap lever, under its own rule. The same budget question applies to the possible phase: at 300,000, seed 7 still leaves 19,890 results `none` with `RELAXATION_SEARCH_STOPPED`.
- `decide`'s `possible=None` parameter (from the first attempt) is no longer used by any caller; remove it in the next engine change.
- 8 workers: lever 5 (pruning on order) remains the next candidate, judged by the paired protocol.

*Limitations:* one machine; synthetic data replicated beyond 10,000 patients; the worlds check covers small grids only; the paired protocol cancels slow drift but not noise inside a run, and two pairs of each order are few; the p95 of 30 queries rests on the second-slowest query.

## D32 Nth occurrence and counts in the store's match request, over coded records; the converter's `series`; the translator

The plan, reviewed twice, is the D32 part of [d32-d33-store-step-language-plan.md](d32-d33-store-step-language-plan.md) (sections 1, 2, 5 and 6, and the shared mechanism). The owner fixed its scope as follows. Rises and falls (D33) are out of scope.
1. **Nth and Nth-from-last occurrence.** The step's time is the time of the Nth occurrence: a derived point whose start range runs from the Nth-smallest lower bound to the Nth-smallest upper bound of `Times.dom`, the patient's tightened ranges (mirrored for Nth from last).
   - `exact` and `none` are sound.
   - A coupled step is `unresolved` (`OCCURRENCE_TIME_COUPLED`), never `possible`. A step is coupled when another step can bind one of its events, or when its events share variables.
   - `during` from an Nth step is refused (`NTH_STEP_HAS_NO_END`).
   - D18's displacement condition applies as the plan states, and reach for `none` ignores the scope.
   - Nth with `first` or `last` on one step is refused (`OCCURRENCE_CONFLICT`).
2. **"At least N times within a span" and "within the index period",** with `widen_count`, the page's one-fewer relaxation.
   - The step binds the record at which the count is reached. Its strength, certain or possible, is decided exactly from the patient's ranges.
   - A period count is certain only when the record's whole range lies inside the window.
   - The two-pass rule applies with the review's fixes:
     - pass 2 runs on any tag that some relaxation set uses;
     - `first`, `last` and Nth are refused on a step that carries a count (`OCCURRENCE_ON_DERIVED_STEP`);
     - `complete` is false if either pass was incomplete;
     - the worlds checker checks every status except `unresolved`.
   - The candidate filter uses the smallest N′ over the step and its `widen_count` relaxations.
3. **Coded records only.** Counts and Nth run over events whose kind is not `mention`. Stated or hedged free text asked of these features is refused by name in the translator. Results are labelled as counts of recorded events, never of real episodes.
4. **The converter's `series`:** an option of `synthetic_ingest`, off by default. When it is on, unitless measurements become excluded records (`MEASUREMENT_WITHOUT_UNIT`). A snapshot without series refuses value conditions by name (`NO_SERIES_IN_SNAPSHOT`).
5. **The translator and the parity extension** for the trajectory page's Nth and count templates. Parity is measured with the converter emitting `series`. Every difference is named, and every unbuilt feature is refused by code.
6. **The unresolved share** is reported per feature and per template. The owner's scope asks for it to be reported, so no ship bar is set on it (the plan had proposed one).
7. **The page's literal mode** is optional and is built only if cheap.

**Pre-committed rules** (written and committed before any measurement of this increment):
- **Soundness, for each new feature (Nth, span counts, period counts).** Each feature ships only if all of the following hold.
  1. *The worlds checker finds 0 violations.* The checker is `trajectory_graph_worlds.py`, extended to Nth and counts: it reads the store's derived steps as their meaning (the Nth occurrence's time in each world, and the count reached at the bound record in each world), never as their compiled events. It runs on at least 20,000 small-grid cases per feature, from multi-concept generators (events of concept x, y or both) and from coupled generators (several ordered or derived steps over overlapping families, steps binding the counted or ordered events, and shared variables). It checks every status except `unresolved`: `exact`, `possible`, `relaxed`, `none`, and the matches reported beside `unresolved`.
  2. *0 mismatches against an independent reference* (`json.dumps` without sorting keys). The reference is written again in `execution_store_reference.py`, sharing no derivation code with the store. It is compared on seeded synthetic requests, at least 400 per feature, at 1 and 2 workers, at both detail levels and with pruning on and off, and on the MIMIC demo differential extended with the features.
  3. *Every planted defect is caught* by the worlds checker or the differential:
     - Nth: N off by one; the record ranked N (rule R); Nth from last counted from the front; the range read from recorded bounds instead of `Times.dom`; coupling ignored; D18 reach scoped for `none`;
     - counts: the span's boundary off by one; the record counted as its own witness; forced and allowed swapped; the period's containment test read as overlap; τ cut to the window for the certain test; the candidate filter on N instead of the smallest N′;
     - the two-pass rule: pass 2 triggered only by the unrelaxed query's tags; an occurrence allowed on a derived step.
  4. *Relaxations keep every stricter match:* each drawn `widen_count` and widened derived tag holds every event of the stricter tag, at both strengths.
  5. *Pruning on and off are byte-identical* on each feature's differential.
- **No change without the new fields.** Every request that uses neither Nth nor counts gives byte-identical results to main (7f5930e):
  - the 300 recorded match digests;
  - every per-query matcher digest at every size and in the confirmation (B equal to A on A's own snapshots, to `B_unpruned` and to every C arm, and equal to the D31 report's digests query by query);
  - the scaling report's row counts, candidate counts and status mixes;
  - every MIMIC demo figure other than identifiers, digests, bindings, timings and the new differential.

  A D28 request (`execution-store-conditions-1.0`) is answered key for key as before.
- **No latency regression.** The test is the paired protocol (`tools/benchmark_matcher.py --paired-confirm`, `paired-interleaved-1.0`), against main's code (7f5930e), at 300,000 patients, 30 queries, 4 repeats, at 14 and 8 workers. It passes only if:
  - the 14-worker paired p95 ratio (this tree over main) is at most 1.05 in every one of the four pairs;
  - this tree's own p95 over its 14-worker runs is at most 10 s;
  - the maximum is at most 30 s and query-process memory is under 4 GB.

  The verdict run comes before the regeneration, and the regeneration repeats it into the matcher report. If it misses, the cause is found and fixed in the unchanged path; nothing is tuned away. The 8-worker figures are reported.
- **Parity, measured with the converter emitting `series`:**
  - no unexplained difference;
  - every cause comes from a closed list: coverage, letter mentions, record reconciliation, medication records, measurement records (unitless values among them), `order-uncertain`, and two new causes. `occurrence-envelope` covers the page binding the record ranked N where the store binds the envelope. `count-timing` covers the page's consecutive-run rule against the store's exact strengths and the two-pass rule;
  - the replay holds for every trajectory, derived steps included;
  - every unbuilt feature is refused by its code;
  - the tables are pinned per trajectory and patient.
- A failure is fixed, never tuned away. A feature that cannot meet its soundness rules ships as a named refusal. If the scope proves too large, the increment stops after a coherent part that meets every rule, and says so.

*Two reports frozen as historical (owner decision).* `verification/d30-attribution-report.json` (D30's attribution of moved results to stages) and `verification/d31-paired-verdict.json` (D31's paired latency verdict) bind `patterns/execution_store.py`, which D32 changes. Both are records of earlier decisions on the tree they were produced on (7f5930e). Regenerating them on D32 code would change what they record, not refresh them. So they are listed in `HISTORICAL` in `tools/check_report_provenance.py`, beside the superseded release manifests. The D31 verdict is superseded for this tree by `verification/d32-paired-verdict.json`; the attribution is a closed record that nothing supersedes. The provenance gate stays strict for every other report.

**Built** (the store doc's [Derived steps](../execution-store.md#derived-steps-nth-occurrence-and-counts-d32) has the contract).
- *The request* (`execution-store-conditions-1.1`, `Request`).
  - Per step, beside or without a value condition, an Nth or Nth-from-last occurrence, or a count within a span or the index period.
  - The relaxation `widen_count`, optionally naming a wider concept (the page's "parent concept and one fewer" as one relaxation).
  - Each is compiled into a derived tag, `derived-step:<step>/<own or relaxation id>`. A reader can reproduce the name from the request alone, and the reference does.
  - Refusals by name: `OCCURRENCE_CONFLICT`, `OCCURRENCE_ON_DERIVED_STEP`, `NTH_STEP_HAS_NO_END`, `INVALID_OCCURRENCE`, `INVALID_COUNT`, `INVALID_COUNT_RELAXATION`, `COUNT_RELAXATION_MUST_NARROW`, `COUNT_RELAXATION_STEP_NOT_COUNTED`, `DERIVED_TAG_NAME_TAKEN`, `DERIVED_EVENT_ID_TAKEN`, and `NO_SERIES_IN_SNAPSHOT` (a 1.1 value condition on a snapshot built without series; a 1.0 request keeps its D28 answer, key for key).
- *Derivation per patient* (`derive_patient`), after a chunk's events are loaded. Mentions are told apart by `events.kind`. The engine is unchanged.
  - An Nth step's tag is carried by one point with its own variable over the order-statistic envelope of the coded events' `Times.dom` starts.
  - A count step's tag is carried by the records at which the count is reached. Each is certain or possible, by the exact sweep (`count_sweep_v1`) or, where the counted events share variables, by the sound pairwise test (`count_pairwise_linked_v1`).
- *Matching* (`match_derived`).
  - The engine runs over pass 1, then pass 2 when the two-pass rule calls for it, and the results combine by the rule's table.
  - Where an Nth step is coupled in some set, the set loop is the engine's own `match_prepared` with one change (`_coupled_match`): a coupled set whose result would be `possible` is undecided (`OCCURRENCE_TIME_COUPLED`).
  - Where a tried count tag was decided by the pairwise test, a claim that no binding is exact is not reported.
- *Store integration.*
  - Candidates: at least N events of the tag's concept, through postings or series, the union over the step's tags, so the smallest N′.
  - Pruning on the concept's summaries.
  - Coverage by what a tag counts.
  - D18: Nth steps displaced like `first` or `last` on their envelope; Nth and span-count steps reach every excluded record; period counts reach records overlapping the window.
  - Workers, `detail='decided'` and the output (`derived_steps`, the context's `derived_rules`).
- *A soundness argument, beyond the plan.* The plan had a coupled Nth step searched again over its possible targets, keeping only a `none` from that search. Once the point is a variable of its own over the envelope, every true choice of times maps into the engine's model with the point at the true Nth time. This is D31's argument for its exact phase, and it needs no independence. So `exact` and `none` from the model are already sound, and only `possible` can be wrong. A coupled set's `possible` is therefore undecided, and no second search is run. The worlds checker confirms this on the coupled generator.

**Measurement and verdicts** (after the rules were committed at 1317259; one heavy job at a time).

*Soundness (small cases, ad hoc, seeds apart from the suite's).* Generated by `patterns/derived_cases.py` and checked by `trajectory_graph_worlds.check_derived` against every choice of the patient's times:
- Generators: 20,000 cases per feature (Nth, span count, period count) from each of two generators. *Multi* has events of concept x, y or both, exposures, proper events and mentions. *Coupled* adds steps that bind the counted or ranked events, second derived steps on overlapping families, and shared variables in a third of the cases.
- In all, 120,000 cases: **0 violations**, and **0 disagreements with the reference** (status, everything beside it, every binding).
- The suite repeats this at 1,000 cases per feature and generator.
- Statuses, multi / coupled:

  | Feature | exact | possible | relaxed | none | unresolved |
  |---|---|---|---|---|---|
  | Nth | 3,465 / 1,284 | 670 / 23 | 171 / 129 | 13,327 / 14,794 | 2,367 / 3,770 |
  | Span count | 3,369 / 1,948 | 1,102 / 717 | 978 / 660 | 11,756 / 13,340 | 2,795 / 3,335 |
  | Period count | 2,763 / 1,612 | 753 / 528 | 707 / 466 | 12,525 / 14,146 | 3,252 / 3,248 |

- *Envelopes and strengths against enumeration* (suite, 1,800 cases):
  - every envelope holds every Nth time, and equals the set of Nth times wherever the ranked events share no variable;
  - every count strength equals enumeration there;
  - with shared variables, certain is a subset of the enumerated certain, and every possibly reached record is tagged.

*Planted defects* (each a switch, `_D32_DEFECT`): unsound results among the small cases (multi and coupled together), or results changed on the snapshot fixture.

| Defect | Caught by | Count |
|---|---|---|
| Nth: N off by one | worlds | 1,079 of 6,000 cases |
| Nth: the record ranked N (rule R) | worlds; the plan's §1.2 case | 51 of 6,000 |
| Nth from last counted from the front | worlds | 368 of 6,000 |
| Nth range from recorded bounds, not `Times.dom` | worlds | 13 of 6,000 |
| Coupling ignored (the review's "2nd x at least 1 before the last x" case) | worlds | 274 of 6,000 |
| D18 reach scoped for an Nth `none` | differential | 169 mismatches in 120 requests |
| Span boundary off by one | worlds | 133 of 6,000 |
| The record counted as its own witness | worlds | 742 (span), 627 (period) of 6,000 |
| Forced and allowed swapped | worlds | 365 of 6,000 |
| Period containment read as overlap | worlds | 166 of 6,000 |
| τ cut to the window for the certain test | worlds | 166 of 6,000 (the same mistake as the line above, by another path) |
| Candidate filter on N, not the smallest N′ | differential | 19 results changed in 32 requests with a `widen_count` |
| Pass 2 triggered only by the unrelaxed query's tags | worlds | 56 (span), 150 (period) of 10,000 |
| An occurrence allowed on a count step | worlds | 60 of 4,000 |

*Store against the reference (snapshots).*
- The suite: 180 requests per feature on four cohorts. These are three fixtures made for it (wide and exact ranges, duplicates, mentions, exposures, shared variables, excluded records, partial coverage, values) and the synthetic cohort.
- Ad hoc: 420 requests per feature, 1,260 in all and 53,280 patient results, each compared at 1 worker, at 2 workers (chunk 9), at `detail='decided'` and with pruning off. **0 mismatches against the reference; 0 differences between the four runs.**
- *One defect found, in the reference.* The ad hoc run first found one mismatch, in a period count. The reference tried τ only at the record's own range ends, which both lay outside the window, and so missed that the count (N = 1) was possible inside it. The store was right. The reference was fixed (1e8ec60), a test pins the case, and the runs above are after the fix.
- *Relaxation containment:* every `widen_count` drawn holds the stricter tag's events at both strengths (fixture and cohort, over 100 checks in the suite; the demo below).
- *Pruning acts on derived steps* (the suite checks that some candidates are pruned) and is exact (above).

*Coded records against records against happenings* ("at least 3 seizures within 183 days", committed seed).
- The store's sweep: 15 patients over coded records only, 13 of them certainly; 39 over every record, 15 certainly.
- The plan reported 36 certainly over every record, from before D30 widened letter mentions to range from birth. The review recomputed 35 under that timing.
- Over true happenings: 11 (the plan's analysis, from the generator's truth, which no store code reads).

*Latency: the paired verdict* (`verification/d32-paired-verdict.json`; this tree against main, 7f5930e, on the 300,000 snapshot this tree builds, `64244627…`; 30 queries, 4 repeats, ABBA). The confirmation's queries carry no Nth occurrence or count, so this measures the path every earlier request takes.

| | Ratios of p95, by pair (main first, tree first, main first, tree first) | Pooled | This tree: p95 pooled (by repeat); max; memory |
|---|---|---|---|
| 14 workers | 1.010, 1.001, 1.001, 1.001 | 1.001 | **8.10 s** (8.08, 8.12, 8.10, 8.13); 10.24 s; 0.80 GB |
| 8 workers | 1.022, 0.993, 1.007, 1.038 | 1.001 | 9.86 s (9.86, 9.77, 9.84, 10.47); 13.06 s |

- *The rule is met.* Every 14-worker pair is at most 1.05 (largest 1.010), this tree's p95 is 8.10 s against 10 s, the maximum 10.24 s against 30 s, and memory 0.80 GB against 4 GB.
- *Results are identical.* The two codes give byte-identical results on all 30 queries, and each code's digests are stable across their runs.
- *8 workers.* This session's p95 is 9.86 s for this tree and about the same for main's code. D31's sessions gave 10.17 and 11.56 s for main's code there, so the 8-worker figure varies across sessions by more than this comparison resolves.
- *Before the review* (pre-fix code), the first verdict gave 14-worker ratios 1.015, 0.926, 0.999 and 0.997, with this tree's p95 8.12 s. That run was superseded, since the fix changed `execution_store.py`.

*Parity* (`demos/mockups/test_store_parity.cjs`; the converter emitting series; translator `trajectory/store_request.js`):
- *Agreement.* 555 of 672 patient results agree over fourteen trajectories. The ten of D29 give 397 of 480: four weight results moved, all from the converter's unitless records now being excluded records. The four new trajectories, with Nth occurrences and counts, give 158 of 192.
- *Causes.* Every one of the 117 differences has a cause from the closed list: count timing 20, coverage 19, letter mentions 83, measurement records 4, medication records 66, occurrence envelope 14, order uncertain 9, record reconciliation 69.
- *Replay.* It holds for every patient, derived steps included. It runs the store's derivation on the listed candidates, and the store's excluded-records rule is allowed for.
- *Refusals.* Each is asserted on the page's own templates where they apply: `RELATIVE_CONDITION_UNSUPPORTED` (the sequence and graph templates), `GENE_CLASSIFICATION_UNSUPPORTED` (tree), `FREE_TEXT_COUNT_UNSUPPORTED` (event frequency over stated text, and an Nth over it), `FREE_TEXT_HEDGED_UNSUPPORTED` and `NTH_STEP_HAS_NO_END`.
- *Rule: met.*

*The unresolved share* (reported, as the owner's scope asks; no bar was set):

| Where | Nth | Span count | Period count |
|---|---|---|---|
| Small cases, multi / coupled (of 20,000 each) | 11.8% / 18.9% (mostly `OCCURRENCE_TIME_COUPLED`) | 14.0% / 16.7% (mostly `STEP_CONDITION_UNCERTAIN`) | 16.3% / 16.2% (mostly `STEP_CONDITION_UNCERTAIN`) |
| Synthetic snapshot differential, ad hoc (results with a window) | 684 of 16,888, 4.1% | 1,576 of 16,872, 9.3% | 1,099 of 16,843, 6.5% |
| MIMIC demo (results with a window; 50 queries each) | 8 of 4,746, 0.2% (all `SEARCH_BUDGET_EXHAUSTED`) | 60 of 4,744, 1.3% (all `ADMISSION_EXCLUDED_RECORDS`) | 180 of 4,733, 3.8% (all `ADMISSION_EXCLUDED_RECORDS`) |

Per parity template (results with a window): event frequency 1 of 38; the second seizure 0 of 38; the second-to-last seizure 0 of 38; at least 2 febrile seizures in the index period 8 of 38. Among the ten D29 trajectories, 9 results are unresolved for "a seizure while taking the first blocker" (D30, as before) and 1 for the weight trajectory (an excluded unitless record, D32); the rest have none.

**An early independent review, and the restart.** A review of D32 during the first regeneration found one must-fix. The regeneration was stopped (it had finished the verdict, the MIMIC demo, the scaling run, arm A and the matcher's main run) and restarted from the start on the fixed code. The figures in this entry are the second run's.
- **Must-fix: derived tag names could collide**, giving a wrong `exact`.
  - A tag was named `derived-step:<step>/<variant>`, and a later name silently replaced an earlier one. So a relaxation with id `own` replaced the step's own tag, and ids holding `/` collided across steps (step `A` with relaxation `r/own`, step `A/r` with its own tag), even with the relaxation over budget.
  - The review's three counterexamples (a count with a `widen_count` named `own`, an Nth with a `widen_concept` named `own`, and the cross-step case at budget 0) each answered `exact` where the truth is impossible.
  - The reference named tags the same way, so the differential could not see it.
  - *Fix:* a name already taken is refused (`DERIVED_TAG_NAME_COLLISION`), in the store and in the reference. Own tags are named first, so the one check covers all three cases. The three cases are tests.
  - The translator's ids never collide, so no parity figure moved; the parity table is unchanged.
- *Should-fix, fixed.*
  - The translator sent profile 1.0 for a page query with only value conditions, so `NO_SERIES_IN_SNAPSHOT` did not apply to it. It now always sends 1.1, and `candidates()` refuses as `match()` does.
  - Where pass 2 was unresolved for a reason of its own (an Nth step's coupling, an uncertain first or last target, a stopped search), the result said `STEP_CONDITION_UNCERTAIN`. It now keeps pass 2's reason.
- *Should-fix, documented and not changed.*
  - D18 leaves a `possible` match beside `unresolved` untouched (the review's fuzz: 11 in 30,000); this is D18's convention, now a follow-up.
  - The 39/15/11 figures now state each one's strength and source.
  - The README's per-cause counts are labelled.
- *Nits:* the MIMIC not-comparable share is of all results with a window; the worlds check's reach (no value conditions, D18, coverage or pruning) and the count sweep's quadratic case are stated under the limitations.
- *After the fixes:*
  - the review's own fuzz, its generator and its brute-force oracle written apart from `trajectory_graph_worlds`, gives 0 violations and 0 reference mismatches on 5,000 cases;
  - the 120,000 small cases, the 1,260-request snapshot differential, the planted defects and every suite pass again, with the same figures.

*No change without the new fields.*

| Check | Measured | Verdict |
|---|---|---|
| The 300 recorded match digests | Reproduced, unchanged (`patterns.test_execution_store`, D30's and D31's movement fixtures included) | met |
| MIMIC demo | Every figure other than identifiers, digests, bindings, timings and the new `derived_differential` equals D31's, the input digest included (snapshot `3fe29499…` → `a462557f…`; the builder's digest changed). The run took 1,689 s | met |
| Scaling report | Every table's row count, every query's candidate count, the status mixes and the incomplete `none` counts equal D31's at every size, 1,000 to 1,000,000. Builds, peaks and match times are within run-to-run spread (1,000,000: build 5,138 s, peak 8.24 GiB, `match()` p50 11.66 s; D31: 5,123 s, 8.28 GiB, 11.76 s) | met |
| Matcher per-query digests | Every arm's per-query result digests equal the D31 report's at every size (1,000 to 300,000; 50, 50, 10 and 10 queries): A on its own 2.0 snapshots (D31's identifiers), B, `B_unpruned`, `B_decided` and C at 1, 2, 4, 8 and 14 workers. B differs from A as it has since D30 (mention timing; `same_rows` false only in `events` and `concept_postings`). In the confirmation (30 queries, 300,000), B, `B_unpruned` and every C arm equal D31's digests query by query and each other (30 of 30) | met |
| A D28 request (1.0) | Answered key for key as before (a test compares a 1.0 and an equal 1.1 value request; the value differential is unchanged) | met |

*The regeneration* (one job at a time):
1. the paired verdict above (2,283 s, after building its 300,000 snapshot in 1,902 s);
2. the MIMIC demo (1,689 s);
3. the scaling run to 1,000,000 with `--keep` (9,138 s);
4. arm A's own 2.0 snapshots per D26 (2,284 s, in the first run; arm A's code is unchanged and its snapshots have D31's identifiers, so the second run reused them, as D30's did);
5. the matcher comparison with the committed settings (2,730 s), then `--confirm --large-queries 30 --large-repeats 3` (5,662 s), then the paired replication against main (2,353 s).

*The paired replication* (`paired_confirmation` of the matcher report, on the scaling run's 300,000 snapshot, `64244627…`, which is the verdict's).
- *14 workers:* p95 ratios 0.943, 1.009, 0.934 and 1.047, each at most 1.05; this tree's p95 is 8.51 s pooled (8.60, 8.38, 8.60, 8.48), with maximum 11.40 s and memory 0.80 GB.
- *8 workers:* ratios 1.006, 0.935, 0.989 and 1.008; this tree's p95 is 10.05 s.
- *Results:* byte-identical between the codes on all 30 queries.
- *The old sequential confirmation*, kept for comparison: at 14 workers p95 8.16 s (8.11, 8.16, 9.28), maximum 10.31 s; at 8 workers 10.01 s; arm B 229.2 µs per candidate (D31: 229.7).

**Decision: D32 ships.** Every pre-committed rule is met: soundness, no change without the new fields, no latency regression, and parity. Requests without the new fields take the code path they took before. The engine and its profile are unchanged (`trajectory-graph-1.0`). The store profile stays `execution-store-3.1`, and the match profile `execution-store-match-2.0`.

*Where D32 departs from the plan, and why:*
- *Coupled Nth steps.* No second search over the possible Nth targets is run. The envelope point already makes `exact` and `none` sound (argument above). So a coupled set's `possible` is undecided, and nothing is searched again.
- *The translator.* It is JavaScript beside the page (`trajectory/store_request.js`), loaded by the parity test, not the server-side `store_trajectory.py` the plan names. Serving the page from the store by default is stage 2's switch, and needs a server-side port.
- *Literal mode was not built.* It was optional, and not cheap enough within this increment.
- *The store query page gains no Nth or count.* Its form is unchanged; the brief made this conditional.
- *A combined relaxation.* The page's "parent concept and one fewer" pair is one `widen_count` naming the parent, identified as `node:A+count:A`. The engine allows one relaxation per step, so at equal cost it ranks before two-element sets, which the page does not. No parity difference arose from it on the committed seed.

*Limitations.*
- The worlds check covers small grids only. Larger cases rest on the reference and the arguments.
- The worlds check reads derived steps over plain concepts only. It does not cover value conditions or `widen_value` with derived steps, nor D18, coverage or pruning. Those rest on the differentials against the reference (fixtures with values, excluded records and partial coverage), on pruning on against off, and on the arguments.
- D18 leaves a relaxed match beside `unresolved` as it is (its convention since D18). After a count's two passes, a `possible` match there can be wrong when an excluded record could be an extra witness: the review's fuzz found 11 in 30,000.
- `_count_strengths` is quadratic in the counted events whose ranges are wide (each record's range is scanned for change points). That is cheap on these cohorts and on MIMIC, whose times are exact. A faster sweep is a follow-up if wide families grow.
- The pairwise count test (shared variables) is sound but not exact. Where it applies, a claim that no binding is exact is not reported.
- On MIMIC every time is exact, so counts are never only possible there, and envelopes are points.
- The parity's Nth and count trajectories run the page with coded records only, while the store's other steps still read letter mentions. That adds letter-mention differences there.
- Counts are of recorded events. For one question: possibly 39 patients over every record (certainly 15), possibly 15 over coded records (certainly 13), and 11 over true happenings (generator truth).
- One machine, one session per latency verdict.

*Follow-ups.*
- D33 (rises and falls), under its own rule.
- D18 and a `possible` match beside `unresolved` after a count: whether excluded records should remove it (a change to D18's convention).
- A faster count sweep for families of many wide ranges.
- A server-side translator for the page on the store by default.
- "Coded only" for every step (the plan's `exclude_kinds`), which would remove the letter-mention differences in the Nth and count trajectories.
- Exact `possible` for counts without `unresolved`, by splitting a record's range where the count is possible (the plan's §2.3), if the `STEP_CONDITION_UNCERTAIN` share matters.
- Literal mode on the page.

## D33 Rises and falls in the store's match request (built, not shipped); the build fingerprint moved to its own increment

The plan is section 3 of [d32-d33-store-step-language-plan.md](d32-d33-store-step-language-plan.md) with its review amendments, and the owner's decisions of 2026-11-14 below. The trajectory page's three headline templates (Sequence, Tree, Graph) ask for a rise in the monthly seizure count; the store refused them (`RELATIVE_CONDITION_UNSUPPORTED`). This increment builds them under a ship-only-if rule, and adds a mechanism to the scaling report's provenance (part 2).

**Owner decisions (2026-11-14).**
1. *Timing of monthly summaries.* A monthly summary reading (the diary's "seizures this month") is a whole-month period, ordered by its start. Each reading covers its month; "earlier readings" are those whose period starts earlier; "within W" is measured between period starts. This is a declared rule, and it assumes what it says: a monthly count describes its month, not one moment in it.
2. *Baseline.* The mean of up to k previous readings of the same item class and the same unit, strictly earlier, within W. Exact arithmetic, with an explicit positive floor (the page's "1"). "Rises to at least ×f" means value ≥ f × max(floor, baseline); "falls to at most ÷f" means value ≤ max(floor, baseline) / f. A reading in another unit among the previous k leaves the baseline undetermined. The smaller-factor relaxation must contain the stricter condition.
3. *Certainty* (from the review of the plan). A tag is certain when every configuration satisfies the condition, not only when the configuration is determined. An undetermined baseline (uncertain order or membership, an excluded reading that could be in the baseline, a reading in another unit) counts as possible, through D32's two-pass rule; an excluded reading that could be the rising reading itself is D18's ordinary reach. `first`, `last` and Nth on a step carrying a relative condition are refused, as D32 refuses them on counts.

**Design** (the store doc's *Relative conditions* section will hold the contract).
- *Request: profile `execution-store-conditions-1.2`.* Per step, `relative: {kind: rise | fall, factor, unit, floor, baseline: {previous: k, within: W}}`: `factor` a decimal string greater than 1, `floor` a decimal string greater than 0, `unit` a literal unit, k an integer from 1 to 3, W a positive number of microseconds. The relaxation `widen_factor: {id, kind, step, cost, factor}` names a smaller factor, still greater than 1. A 1.0 or 1.1 request is answered as before, key for key; 1.1 knows no `relative`. Refusals by name: `INVALID_RELATIVE`, `INVALID_FACTOR`, `RELATIVE_WITH_ABSOLUTE` (with a value condition on the step), `OCCURRENCE_ON_DERIVED_STEP` (with `first`, `last`, an Nth or a count), `INVALID_FACTOR_RELAXATION`, `FACTOR_RELAXATION_STEP_NOT_RELATIVE`, `FACTOR_RELAXATION_MUST_WIDEN`, and D32's `NO_SERIES_IN_SNAPSHOT`.
  - *k.* The owner's "up to k = 3" is read as a parameter of at most 3 (the bound that keeps the enumeration of configurations small), not as a fixed 3. The translator sends the page's own k = 2 and W = 75 days. For monthly readings with W = 75 days the two coincide: three month starts back are at least 89 days earlier, so at most two previous readings ever lie within W.
- *What the tag means.* An event e carries the relative tag of (concept K, condition) when it has a series row whose item class K covers, in the condition's unit (the reading c), and, in a choice of the patient's times, the baseline of c is determined and satisfies the comparison. Its strength is *certain* when that holds in every choice, and *possible* when in some choice it holds or the baseline is undetermined.
- *Readings and their times* (`relative_previous_k_v1`). The readings of an item class I are its series rows (any unit) and its excluded records (no value). A reading's time is:
  - a *period reading* (below): its recorded start's lower bound, exactly: the period's start;
  - a *point reading*: its start, over the patient's tightened range (`Times.dom`), uncertain where that range is;
  - an excluded record: its recorded start range (its kind is not recorded, so it is not known to be a period).
  On MIMIC every chart time is exact, so point and period timing coincide there.
- *Which readings are period summaries: declared by the source, in the snapshot.* A snapshot label `period_reading_kinds` lists the event kinds whose series rows summarise their whole recorded start range. The synthetic converter sets it to `diary_summary` when it emits series (D32's option); the store reads `events.kind`. A snapshot without the label has no period readings.
  - *Why there.* The source adapter is what knows that a diary record counts a month; a label is part of the snapshot's identity, so the reading rule is bound to the data it describes.
  - *Declined:* inferring it from the unit (`/month` names a rate, not the period it was counted over); an explicit request field (it would put a fact about the records with each caller, who could misstate it); a series column (a store format change, 3.2, for one flag); the record's precision (it lives in free-form evidence, which matching never reads).
- *Baseline.* In a choice of times with c at τ, the previous readings are those of c's item class (other than c) whose time t satisfies τ − W ≤ t ≤ τ − 1 (strictly earlier, within W inclusive); the baseline is the k most recent of them, by time, at equal times excluded records first and then event ids in ascending order. No previous reading: no baseline, the condition is false. Any excluded record or reading in another unit among them: undetermined. Otherwise the mean of their values, compared exactly (`Fraction` over `value_decimal`).
  - *Why excluded records first at a tie:* an excluded record in the baseline makes it undetermined, so putting it first gives the same certain and possible as either order.
- *Exact computation.* With c at τ, each other reading is forced into the window, forced out, or optional, and the configurations (the possible baselines) are enumerated exactly: a set S of size below k is realizable exactly when it is the forced readings plus some optional ones; a set of size k is realizable exactly when, with its members at their latest time in the window, every non-member can lie outside the window or rank below the lowest member at its earliest time in the window. τ is tried at every point where any of these tests can change (the readings' bounds offset by 0 to 3 and W − 1 to W + 3). Certain when every configuration at every τ is true; possible when one is true or undetermined.
  - *Linked readings.* Where two point readings of the item class have start variables in one component of the patient's network, their times are correlated; they are then treated as independent over their tightened ranges, which is sound (certain under, possible over) but not exact, and the tag is marked inexact, so that D32's rule withholds every claim that no binding is exact.
  - *Budget.* More than 6 readings of uncertain time that could precede c within W (c included): c is possible and not certain, inexact. Neither cohort reaches it.
- *Matching.* The two-pass rule of D32, unchanged: pass 1 over certain tags, pass 2 over certain and possible ones when a tried tag has a possible-only event; a relative step is listed in `uncertain_steps` with reason `STEP_CONDITION_UNCERTAIN`.
- *Relaxations.* `widen_factor` is compiled to a `widen_concept` to the relative tag with the smaller factor and every other parameter kept. With a positive floor, max(floor, baseline) > 0, so f′ < f gives f′·M < f·M and M/f < M/f′: every configuration that satisfies the stricter condition satisfies the wider, so the wider tag holds every event of the stricter at both strengths. A `widen_concept` of a relative step gives the relative tag of the wider concept, each reading still compared with its own item class.
- *Candidates, pruning, coverage, D18.* A relative step's events are among those with a series row of K in the condition's unit, which D28 already tags exactly: the step loads, filters candidates (at least one such row), prunes (its summaries) and judges coverage (the sources holding such a row) by that unit tag. D18's reach for the step is its scope (the bound reading meets the scope; an excluded record the scope admits might have been the rise); excluded records that might be in a baseline are read inside the tag.
- *Output.* `derived_steps` lists the relative tags and adds `relative_rules`; the context adds their digest. The unit tag appears under `value_conditions` (its condition is the unit alone). A request without `relative` gives the output it gave before.

**Part 2: a build fingerprint for the scaling report (owner's regeneration policy, 2026-11-13).** Every edit to `patterns/execution_store.py` makes the scaling report stale, though most edits touch matching only, so each forces the 1,000,000-patient build (about 1.5 hours of a 2.5-hour run). The scaling report will bind its build phase by a *fingerprint*: a digest of the source of every top-level definition the build reaches, found by following names from the build's entry points through the module's own definitions, with every other repository module those definitions import bound by its full digest. The report's query phase keeps the full digests. `tools/check_report_provenance.py` recomputes each fingerprint, so the gate stays strict for both parts. A later run whose build fingerprint, build files, DuckDB version and kept snapshots match may reuse the earlier builds and run only the query phase, recording where each build came from. This run builds afresh (the earlier report has no fingerprint).

**Pre-committed rules** (written and committed before any measurement of this increment).
- **Soundness.** Relative conditions ship only if all of these hold:
  1. *The worlds checker finds 0 violations.* `trajectory_graph_worlds.py`, extended to relative conditions, reads a relative step as its meaning (in each world, the readings' times, the baseline by its definition, an excluded record's value and a unit mismatch as unknown, which may be either), never as its compiled tags. At least 20,000 small-grid cases from each of three generators: multi-concept (readings of concept x, y or both, other steps on them), coupled (other steps binding the readings, second derived steps, shared variables among readings), and period readings (period readings among point readings, excluded readings, other units). Every status but `unresolved` is checked, and the match beside `unresolved`.
  2. *0 mismatches against an independent reference* (`json.dumps` without sorting keys), written again in `execution_store_reference.py` (configurations by enumerating each reading's candidate times, not by the store's test of realizability): on every small case above; on at least 400 seeded relative requests over snapshots (fixtures made for it and the synthetic cohort built with series), at 1 and 2 workers, both detail levels, pruning on and off; and on the MIMIC demo differential extended with relative requests.
  3. *Planted defects caught,* each by the worlds checker or the differential: the baseline window off by one; k miscounted (k + 1 readings); the floor ignored; unit mixing allowed (a reading in another unit averaged in); an excluded reading ignored; certainty claimed only on a determined configuration (the plan's rule, which the review rejected); and a relaxation that does not contain the stricter condition (the narrowing check skipped).
  4. *Relaxations keep every stricter match:* every drawn `widen_factor` holds every event of the stricter tag, at both strengths.
  5. *Pruning on and off byte-identical* on the relative differential.
- **No change without the new fields.** Every request without a relative condition gives byte-identical results to main (2c4fafd): the 300 recorded match digests; every per-query matcher digest at every size, for every arm and in the confirmation, equal to the D32 report's; the scaling report's row counts, candidate counts and status mixes equal to D32's; every MIMIC demo figure other than identifiers, digests, bindings, timings and the new relative differential equal to D32's. A 1.0 or 1.1 request is answered key for key as before.
- **Usefulness bar** (ship only if met; otherwise relative conditions ship as the named refusal `RELATIVE_CONDITION_UNSUPPORTED` and these numbers are recorded). On the synthetic cohort built by the converter emitting series, for the trajectory page's three rise templates (Sequence, Tree, Graph) translated to the store with the page's own window, scope and settings as the parity test uses them, the `unresolved` share is at most 30% of the patients with a candidate (store candidates with an index window), *in each template*. Tree and Graph carry a gene step, which the store refuses by name (`GENE_CLASSIFICATION_UNSUPPORTED`, not built); they are therefore measured without that step and its relations, labelled so, and their refusal as written is asserted too. The share is reported per template and per reason, with the store-against-page parity for those templates, every difference named by a cause.
- **Latency.** Under the paired protocol (`tools/benchmark_matcher.py --paired-confirm`, `paired-interleaved-1.0`) against main (2c4fafd), at 300,000 patients, 30 queries, 4 repeats, 14 and 8 workers, in the regeneration's matcher report: the 14-worker paired p95 ratio (this tree over main) is at most 1.05 in every pair, this tree's own 14-worker p95 at most 10 s, the maximum at most 30 s and query-process memory under 4 GB. The 8-worker figures are reported. A miss is traced and fixed in the unchanged path, never tuned away.
- **The fingerprint (part 2).** It must change when any definition the build reaches changes and must not change for a definition only matching reaches (tests on both); the provenance gate must fail a scaling report whose fingerprint no longer matches; a reuse must be refused when the fingerprint, a build file, the DuckDB version or a kept snapshot's identity differs. It changes no figure: the regenerated scaling report's rows, candidates and status mixes are judged as above.
- A failure is fixed, never tuned away.

*Part 2 moved out (after the rules; the rules above are kept as committed).* The build fingerprint was built in this branch, and an independent review then found a must-fix in it: edits that change what a build does without changing the fingerprint (top-level statements other than definitions, the benchmark's worker dispatch and `run_phase` outside the roots, a module-level `globals()` write, an import swapped in a module the build reaches), and a gate test that counted bindings without the fingerprint entries. Since rises did not ship, the mechanism was the only reason this branch changed code or needed a regeneration. It was therefore removed from this branch (the revert of cb9968f) and moves to its own increment, with the review's fixes, from main; the regeneration this branch had started was stopped after nine minutes, before any report was written. This branch changes no code, test or report: everything but the documentation is byte-identical to main.

**Built** (bccee57; the implementation stays in the branch's history). The store's request (`Request`, `_relative_spec`, profile 1.2, `widen_factor`), the readings loader (`_readings`: series rows of the covered item classes in any unit, their events' kinds and starts, the item classes' excluded records, the snapshot's period label), the strengths (`_patient_readings`, `_relative_strengths`), the two-pass rule extended to relative steps, D18 reach by scope; the converter's label (`labels.period_reading_kinds = 'diary_summary'` with `series` on); the reference written again (`patient_readings`, `_relative_tags`: every choice of each reading's candidate times, a product, not the store's test of realizability); the worlds checker (`outcome`, `relative_strengths`, an excluded record a variable of its own in every world, an unknown outcome read as either); three small-case generators (`derived_cases.relative_case`); `patterns/test_relative_conditions.py`; the translator (rises translated with the page's own k = 2, W = 75 days and floors) and the parity test extended with the three rise templates.

**Measurement** (after the rules, ce358d6; on bccee57).

| Rule | Measured | Verdict |
|---|---|---|
| Soundness 1: worlds checker, at least 20,000 per generator | 60,000 cases (20,000 per generator, seeds from 1,000,000): **0 violations**. Statuses (exact, possible, relaxed, none, unresolved): multi 732, 136, 86, 16,563, 2,483; coupled 304, 101, 52, 17,314, 2,229; period 641, 362, 100, 16,186, 2,711. The suite repeats this at 1,000 per generator | met |
| Soundness 2: reference | **0 mismatches** on the 60,000 small cases (status, everything beside it, every binding). Snapshots, ad hoc: 500 requests (fixture 130, fixture with shared variables 120, a fixture without the period label 100, the synthetic cohort built with series 150), 21,200 patient results, 26,544 comparisons (126 requests also against `exhaustive_match`): **0 mismatches**, and 0 differences at 2 workers (chunk 9), at `decided` and with pruning off. The suite runs 180 requests. *Not run:* the MIMIC demo differential with relative requests, which would have come with the demo report's regeneration; the bar below decided first | met for what was run; the MIMIC part not run |
| Soundness 3: planted defects | Worlds-check violations over 12,000 cases (4,000 per generator: multi, coupled, period): window off by one 167 (72, 35, 60); k + 1 readings 26 (10, 4, 12); floor ignored 61 (26, 12, 23); unit mixing 303 (77, 97, 129); excluded reading ignored 279 (77, 44, 158); certainty only on a determined configuration 1 result (coupled), and 26 tag strengths wrong against enumeration in 900 cases (0 without the defect); a relaxation that does not contain the stricter condition (the narrowing check skipped, a larger factor drawn) 185 containment failures in 1,346 checks | each caught |
| Soundness 4: containment | 439 factor relaxations on small cases and 90 on snapshots, every one holding the stricter tag's events at both strengths | met |
| Soundness 5: pruning | On and off byte-identical on the 500 snapshot requests; pruning acts on relative steps (a suite test) | met |
| **Usefulness bar**: at most 30% `unresolved` among patients with a candidate, in each template | Synthetic cohort, converter emitting series, the page's own window (from the second seizure, 24 months), scope (`from_start`) and free-text setting (stated). **Sequence: 0 of 6. Tree without its gene step: 0 of 6. Graph without its gene step: 3 of 4 (75%)**, all `OCCURRENCE_ORDER_UNCERTAIN`. Pooled over the three, 3 of 16 (18.75%). Tree and Graph as written are refused by their gene step (`GENE_CLASSIFICATION_UNSUPPORTED`, asserted). *The verdict rests on 4 graph candidates: one patient moves that share by 25 points* (2 of 4 would also miss; 1 of 4 would pass) | **missed** (graph template) |
| Parity of the three templates | 49 of 117 patient results agree; every one of the 68 differences is named: coverage 59 (the diary covers no part of the window, so the store says not comparable where the page says none), order uncertain 3 (graph), and letter mentions, medication records and record reconciliation together 6. **No difference comes from the rise:** none is named `relative-baseline`; wherever results differ, the readings the store tags as rises are the page's. All seventeen trajectories: 631 of 816 agree, every difference named, the replay holding for every patient | met |
| Latency; no change without the new fields | Not measured: relative conditions did not ship, so the shipped matching code is main's (below) | — |

*Why the graph template misses.* Its blocker step is `first`, and the rise follows it by `during`, which reads the blocker's end. D30's order rule leaves a `first` step undecided when its target is uncertain and a relation reads its end; on 3 of the 4 patients with a candidate the records leave the first blocker uncertain. The rise's tags were decided (with period starts every diary reading's baseline is determined, so every tag is certain or absent and no second pass runs); the uncertainty is D30's order rule on the `first` blocker with `during`. The same three patients are unresolved in the parity test's earlier trajectory "a seizure while taking the first blocker" (9 of its 10 candidates).

*The k question.* The owner's "up to k = 3" was read as a parameter of at most 3, and the translator sent the page's k = 2. For the diary's monthly counts with W = 75 days the two give the same baseline (three month starts back are at least 89 days earlier), so the bar's numbers would be the same with k = 3; this was argued, not measured.

**Decision: rises and falls are not shipped; the translator keeps refusing them by name** (`RELATIVE_CONDITION_UNSUPPORTED`). The rule committed before measuring is the verdict: it asks for each template, and the graph template misses. The store, the reference, the converter, the tests and the translator are main's again (415c7f3 reverts bccee57), so no report that binds them moves. *For the owner:* the bar as the owner wrote it ("the unresolved share is at most 30% of patients with a candidate; report the share per template") does not say "in each template"; I added that when writing the rule. Read as one share over the three templates it is met (3 of 16). The rule as committed stands here; if the owner rules that the share was meant over the three together, bccee57 can be restored, with the regeneration it needs (the MIMIC demo with its relative differential, the scaling report, the matcher with its confirmation and the paired latency rule).

**Owner decision (2026-11-14): the per-template reading stands, as committed.** Rises and falls stay refused. The next step is to look at D30's order rule on a `first` step whose end a `during` reads (the cause of the graph template's three unresolved results), and to retry rises and falls later with a usefulness test on more patients than the four graph candidates this verdict rests on.

**Part 2: moved to its own increment** (above). What was built here and reviewed: `patterns/build_fingerprint.py` (a fingerprint over the definitions the scaling benchmark's `build_worker` reaches, followed across this repository's modules to a fixed point), the scaling report's `build_provenance` and `--reuse-builds` with named refusals, and the gate recomputing each fingerprint; it is commit cb9968f, reverted here. Beyond the must-fix, the review asked for: the environment (or the first run's environment, date and report) carried through chained reuses; reused build figures flagged on the results page; the gate recomputing the whole fingerprint map from its entry points; the data files pinned explicitly; the departure from these rules recorded (the rules say the build's modules are bound "by full digest", the build closes over them instead); `budget_seconds_per_size`, `rss_limit_bytes` and the report profile compared on reuse; unknown keys in a fingerprint map refused rather than skipped; `importlib.import_module`, `__dict__` and `attrgetter` refused as dynamic references; and the re-export limit stated. The next increment takes these with the must-fix, from main.

## D34 Reusing the scaling report's builds when only matching changed: a build fingerprint, redesigned as a runtime record

D33 built this as its part 2, under D33's rules (ce358d6). An independent review found a must-fix in it, and since D33's rises did not ship, the mechanism was D33's only code change; it was removed from D33 (decision log, D33) and is rebuilt here, from main after D33, with every fix the review listed.

*Why a new entry, not D33's part 2.* D33 is closed and merged as documentation only. Its rules for this mechanism said that the modules the build reaches are bound "by full digest"; the design below binds them by a closure over their definitions instead, which is the departure the review asked to record. A new increment has its own rules, committed before its measurement, and its own regeneration; folding it into D33 would mean amending rules after D33's measurement.

**Design** (as cb9968f, with the review's fixes; the store doc's *Build provenance and reuse* will hold the contract).
- *What it binds.* The scaling report binds its build phase by *fingerprint* and its query phase by whole-file digests (`artifacts`, unchanged). A fingerprint is computed per module, from roots: the build process's entry point and the parent's code that measures and records a build. They are followed within and across this repository's modules to a fixed point (`patterns/build_fingerprint.py`, rule `build_closure_v1`).
- *Closure, not full digest, and its limits.* Within a module, a fingerprint covers:
  - every top-level definition (function, class, or assignment to a plain name) that a reached definition names, transitively;
  - *every top-level statement that is not such a definition*, whether reached or not: imports, expression statements, conditional or looping blocks, and assignments to anything but a plain name, with the module docstring left out (review must-fix 1: `if True: BUCKETS = 9`, an import swapped for another);
  - across modules: a module named with an attribute (`es.StoreBuilder`) adds that name to the other module's roots; a module named as a value (passed, or `module.__dict__`) adds every definition of it;
  - the cost of covering every top-level statement: an edit to any of them, even one only matching reads, changes the fingerprint (conservative).

  Refused, since a name reached through a string would not be followed (`DYNAMIC_REFERENCE`):
  - anywhere in reached code or at top level: `getattr`, `setattr`, `delattr`, `globals`, `vars`, `eval`, `exec`, `__import__`, `importlib.import_module` and `sys.modules`;
  - `__dict__` and `operator.attrgetter`, wherever they could reach a module: `__dict__` on a module alias or on `globals()`, or `attrgetter` applied to a module.

  The engine applies `attrgetter` and `__dict__` to its own event objects (`Times`, `events_of`). Those reach no module name, and refusing them would refuse the build itself, so on objects they are allowed.

  *Re-exports* (a name one module imports from another and a third reads from the first) are followed only as far as the importing module's top-level import statement, which is always covered; a re-exported definition is fingerprinted in the module that defines it only if a reached definition there names it. The build's modules have none (a test lists the imports followed).
- *Roots* (must-fix 1): the build process runs `tools/scaling_build_worker.py` (new; its `main` and every top-level statement are roots), not the benchmark's own `main`, so no edit in the benchmark's query or dispatch code can reach a build. In the benchmark, `build_worker`, `build_size` (the part of a size's run that records its build figures), `run_phase`, `current_rss` and `limit_bytes` are roots.
- *Data files, pinned* (should-fix). `BUILD_FILES` lists the data files the build reads (the manifest schema, the synthetic generator, and what the resource locator names), bound by whole digest. The fingerprint's own discovery of path constants must find no file outside that list, or the report refuses to be written (`UNPINNED_BUILD_FILE`).
- *The gate* (should-fix). `tools/check_report_provenance.py` recognizes a build provenance object by shape. It refuses unknown keys or an unknown rule, recomputes the whole map from its recorded entry points (`fingerprints(entry_points)`: the same modules, roots and digests), checks the pinned files' digests, and fails on any difference. A fingerprint map found anywhere but inside such an object fails.
- *Reuse and its origin* (should-fix). `--reuse-builds REPORT` reuses an earlier run's kept snapshots only if all of these hold, and refuses by name otherwise:
  - that run's fingerprints, pinned files and DuckDB version equal this tree's;
  - its build parameters equal this run's: threads, memory limit, staging, cohort sizes, `budget_seconds_per_size`, `rss_limit_bytes`, the report profile and the generator seed;
  - its environment equals this machine's (platform, CPU, memory, Python, DuckDB): the reused build figures were measured there;
  - each kept snapshot opens with its identifier and file digests verified.

  A reused size says so (`build_reused`). The report carries `build_origin`: the report that first built the snapshots (by digest), its generation time and its environment. Origin passes unchanged through chained reuses. The results page marks reused build figures and names their origin.

**Pre-committed rules** (written and committed before any measurement of this increment).
1. *Every bypass is caught.* Each edit the review found changes the fingerprint or is refused, a test each:
   - `if True: BUCKETS = 9` after `BUCKETS = 8`;
   - a top-level `globals()['BUCKETS'] = 9`;
   - `es.BUCKETS = 9`, and forcing ndjson staging, in the build process's entry point;
   - `from _pydecimal import ...` in `series_values.py`;
   - a change to how `run_phase` measures the build's peak.

   So do the earlier ones: a reached definition, a constant the build reads, a reached engine function, the benchmark's own build code, `getattr`, and the newly refused names. An edit to matching alone (a function no build reaches, in the store, the engine or the benchmark's query code) does not change it.
2. *Provenance no weaker than before.* Every report the gate checked before is still checked, with the same whole-file digests. The scaling report's `artifacts` still lists every file it listed. Its build phase is bound by fingerprints the gate recomputes in full from their entry points, with pinned files by whole digest; an unknown key, an unknown rule, a missing file, a changed module set or a stale digest fails. The gate's own tests count fingerprint entries among the bindings (must-fix 2). The regenerated reports reproduce D32's figures where the code is unchanged: the scaling report's row counts, candidate counts and status mixes at every size, and every per-query digest of the matcher report at every size and in the confirmation.
3. *A reused report states its origin unambiguously.* A test reuses a run, then reuses the reuse. Each size says it was reused. Both reports name the first run's report digest, generation time and environment. The second's build figures equal the first's. Reuse is refused for each named reason, including a different environment or parameter. The results page marks reused figures.
4. *This run builds afresh* (no committed report has a fingerprint yet), with `--keep`, so that the next change to matching alone can reuse it.
- A failure is fixed, never tuned away.

**Superseded (2026-11-14), kept as written above.** A second independent review of the static closure (its scripts in `/tmp/rv34_scripts`) built many more single-step bypasses. Among them:
- import-time side effects inside definitions (a walrus, default arguments, class bodies, decorators, `_unused = globals().update(...)`);
- repository code that runs in a build but was not in the map (`patterns/__init__.py`, an imported module none of whose names were reached, a new `tools/__init__.py`, a shadow module on `sys.path`);
- `run_size` able to rewrite the build figures;
- two-step lookups (`__globals__`, `builtins.getattr`, `pkgutil.resolve_name`, `inspect.getmodule`, dotted imports, a module `__getattr__`, an `if __name__ != "__main__"` guard).

The owner's delegate decided to stop hardening a static analysis of Python, which does not converge, and to redesign D34 around what a build actually loads, by whole-file digests. The design and rules above are superseded by the following; the regeneration that had started under them was stopped after 12 minutes, before any report was written.

**Redesign.**
1. *The engine split.* What the store's build uses of the engine moves out of `patterns/trajectory_graph.py` into `patterns/trajectory_times.py`:
   - patient validation, `patient_times`, `Times` and the network it solves, `Event`, `events_of`, the refusal codes (`TrajectoryError`, `require`) and their constants.

   Matching code (search, arc consistency, the order rule, the envelope, `decide*`, `match*`) stays in `trajectory_graph.py`, which imports every moved name back, so these are the same objects and no caller changes. `Times.occurrence`, which builds the order rule's `_Occurrence`, imports it when called. `patterns/execution_store.py` imports `trajectory_times` and reaches matching code through a proxy that imports `trajectory_graph` on first use, so a build process loads neither `trajectory_graph` nor `temporal_stn`.
2. *A runtime record* (`patterns/build_record.py`, rule `runtime_record_v1`). The build phase (cohort construction, the build worker, and the code that runs and measures a build: `run_phase`, `current_rss`, `limit_bytes`, `build_size`) moves into `tools/scaling_build_worker.py`. The benchmark runs that script for each build in Python's isolated mode (`-I`), with the repository first on `sys.path`. At the end of each build the script records:
   - every module loaded from the repository, with every `__init__.py` on its path, by whole-file sha256;
   - the pinned data files (`BUILD_FILES`), by whole-file sha256. An audit hook sees every file opened; a repository file read that is neither a recorded module (or its bytecode) nor pinned stops the build;
   - the versions of Python, DuckDB, pyarrow and jsonschema;
   - whether the repository is a checkout or an installed package.

   Refused at runtime: matching code loaded, a repository module the record cannot describe, and a recorded file that changed during the build. Every size of a run must record the same, and the report keeps the record as `build_provenance`. Its `modules` and `files` are artifact maps, so the provenance gate recomputes each digest and fails on a change or a missing file.
3. *Reuse.* `--reuse-builds REPORT` first runs a small build of this tree (one cohort) and requires its record to equal the earlier report's exactly. That catches:
   - any repository file loaded or read, or its digest;
   - a newly loaded module;
   - a version, or the root's kind.

   Reuse also requires the profile, the generator seed, the build parameters (threads, memory limit, staging, cohort sizes, `budget_seconds_per_size`, `rss_limit_bytes`) and the environment (platform, CPU, memory, Python, DuckDB) to be equal, and each kept snapshot to open with its identifier and file digests. Otherwise it is refused by name.
4. *Figures and origins.*
   - `build_size` returns a size's build fields, and the benchmark fails if any changes before the size's entry is written.
   - Each report records `generated_at` and `commit` (the head and whether the tree was dirty, read when the run starts).
   - `build_origin` names the report that first built the snapshots (path, sha256, commit, time, environment) and the report reused directly, unchanged through chained reuses.
   - The results page marks reused build figures and names their origin.
5. *Honesty.* The record covers repository files loaded or read, data files, the recorded versions, the environment and the limits. It does not cover the operating system, CPU microcode or library internals beyond their versions. A reused snapshot's own manifest still names the builder and converter file digests of the tree that built it (`builder_artifacts`, `converter_sha256`); the report says so (`build_covers`).
6. *A departure from rule 2, signed off.* D32's paired verdict binds `tools/benchmark_execution_store.py`, which this change edits. It is frozen in the gate's `HISTORICAL` list, as D30's attribution and D31's verdict were in D32, and the regenerated matcher report's `paired_confirmation` supersedes it for the current tree. This is a departure from rule 2 ("provenance no weaker for any report"), signed off by the owner's delegate under the policy the owner approved.

**Pre-committed rules (these supersede the four above; written and committed before any measurement).**
1. *Every bypass from both reviews is caught, by construction or by a test.* The record lists every repository file the build loaded or read, so an edit to any of them changes it. There is a test for each of:
   - the first review's five bypasses: a top-level conditional reassignment, a top-level `globals()` write, an edit in the build process's entry point, an import swapped for `_pydecimal`, a change to how `run_phase` measures a peak;
   - import-time side effects inside definitions (a walrus, a default argument, `globals().update`);
   - a package `__init__` side effect, a new repository module, a `sys.path` shadow (a package taking a module's place) and a new `tools/__init__.py`;
   - two-step lookups (`builtins.getattr`, `pkgutil.resolve_name`, a module `__getattr__`, an `if __name__ != '__main__'` guard);
   - refusals: matching code loaded, an unpinned repository file read, a file changed during the build;
   - a build field rewritten after the build.
2. *The split engine gives byte-identical results:* the 300 recorded match digests, every engine, store and adapter suite, and both parity tests are unchanged. The regenerated reports reproduce D32's figures where the matching code is the same: the scaling report's row counts, candidate counts and status mixes at every size; every per-query digest of the matcher report, at every size and in the confirmation; every MIMIC demo figure other than identifiers, digests, bindings and timings.
3. *Provenance no weaker for any report, with one stated exception* (D32's paired verdict, frozen, above):
   - every report the gate checked before is still checked;
   - the scaling and matcher reports, and the store's match context, bind `patterns/trajectory_times.py` beside `trajectory_graph.py`;
   - the scaling report's build phase is bound by a record every digest of which the gate recomputes;
   - an unknown rule, the static fingerprint's included, fails.
4. *Origins are unambiguous:* a test reuses a run and reuses the reuse; both name the first report's path, digest, commit, time and environment; every reused size says so; reuse is refused for each named reason.
5. *This run builds afresh, with `--keep`,* so that the next change to matching alone can reuse it.
- A failure is fixed, never tuned away.

**The third review's fixes** (before any measurement; the rules above are unchanged, since rule 1 already requires every bypass to be caught). A third independent review judged the engine split behaviour-identical and found three must-fixes in the record, each reproduced in scratch:
- *Bytecode is not source.* An unchecked-hash `.pyc`, and a stale-timestamp `.pyc` (a same-size edit, the source's whole-second time restored), each ran doctored code under an identical record. The build process now compiles into a fresh, empty cache outside the repository (`-X pycache_prefix`) and refuses to run otherwise, and a read of the repository's `__pycache__` is no longer exempt. A test covers both cases: the record and the snapshot equal the clean build's.
- *A `.venv` inside the checkout* made every build in the main checkout fail. The repository now excludes the Python installation's own directories. There is a test with a fake environment inside a scratch root, and one (where the interpreter lies in a checkout) rooted at that checkout. A read-only smoke test at the main checkout recorded nothing of `.venv`.
- *The parent was unrecorded.* Staging and threads were the parent's claims: ndjson with 4 threads recorded the same as parquet with 1. Fixed in three ways:
  - the build process now records the settings it saw, so a reuse compares those;
  - the benchmark checks that the build recorded the `scaling_build_worker.py` bytes it executed;
  - `build_origin` names the origin's benchmark digest. `build_covers` and the results page say the benchmark that wrote the origin's figures is bound only by that report.
- *Should-fixes, done:*
  - S1: DuckDB's native reads are confined to the build's directories (`allowed_directories`, external access disabled) in the builder's connections and its content check; DuckDB allows this with Parquet staging.
  - S2: the pinned files are digested at the start too.
  - S4: a fixed minimal environment, and `-S` with the library directories passed explicitly, so no `.pth` or `sitecustomize` runs.
  - S5: the proxy becomes the module on first use.
  - S6: the origin's git blob id is recorded, and `dirty` counts untracked files under `patterns/` and `tools/`.
  - S7: the docs say the gate re-checks the files the record lists, and only a probe sees a new shadow.
  - Nits: the moved globals' comment; `artifacts_at_ref` lists `trajectory_times.py` where a ref has it; the gate reads `build_provenance` by name, not by shape.
- *What the record now covers,* stated in the report (`build_covers`), the store doc and on the results page. Covered: repository files loaded or read through Python (sources only), pinned data files, the recorded versions, the settings the build saw, the environment and the limits, with DuckDB confined to the build's directories. Not covered: the operating system, CPU microcode, library internals beyond their versions, and the benchmark that wrote the origin's figures (bound only by the origin report).

**The re-review** (of the third review's fixes; its notes in `/tmp/rv34c/`) closed all three must-fixes and found no new one. Three should-fixes touched files the regeneration binds, so they were done before it (cdc58a0):
- the build process refuses to run, before it imports anything of the repository, if its bytecode cache already holds bytecode of the repository (`BYTECODE_CACHE_NOT_FRESH`); `fresh_bytecode_cache` is recorded only after that check. Tested with a planted `.pyc`;
- the recorded settings hold every environment variable, with `TMPDIR` and `HOME` masked, so an extra `DUCKDB_*` or `ARROW_*` variable leaves a trace. Tested;
- a fresh report's parameters (staging, threads, memory limit) must equal what its build processes recorded seeing (`BUILD_SETTINGS_DIFFER`). Tested;
- and the optional nit: the generator is executed from bytes whose digest the build compares with the recorded file, so an edit made and reverted during a build is caught (`PINNED_FILE_CHANGED_DURING_BUILD`).

**Measurement and verdicts** (after the rules, cacc34e, and the reviews' fixes, cdc58a0; one heavy job at a time, from 13:52 to 20:30):
1. the MIMIC demo (1,646 s), since the split changes files it binds;
2. the scaling run to 1,000,000 with `--keep` (9,197 s, fresh builds);
3. arm A's own 2.0 snapshots (2,276 s; the same identifiers as before);
4. the matcher comparison with the committed settings (2,734 s), its confirmation (5,662 s), and the paired replication against main, be8c975 (2,358 s).

| Rule | Measured | Verdict |
|---|---|---|
| 1. Every bypass caught | `patterns/test_build_record.py` (23 tests on a scratch copy of the repository): each bypass of the first review (5), of the second (side effects inside definitions, a package `__init__` side effect, a new module, a `sys.path` shadow, a new `tools/__init__.py`, four two-step lookups) and of the third (an unchecked-hash and a stale-timestamp `.pyc`, a `.venv` in the root, the parent's settings, a non-fresh bytecode cache, an extra environment variable) changes the record or is refused; so do matching code loaded, an unpinned read, a file changed during the build; the tool's tests cover a build field rewritten after the build, the parent's build code, and a fresh report's settings | met |
| 2. The split gives byte-identical results | The 300 recorded match digests and the store, engine, adapter, D32, value-condition and MIMIC fixture suites pass (274 tests); both parity tests unchanged (555 of 672; 336 of 336). MIMIC demo: every figure but identifiers, digests, bindings and timings equals D32's, the input digest included. Scaling: rows, candidates, status mixes and incomplete `none` counts equal D32's at every size. Matcher: all 1,170 per-query digests equal D32's; the paired codes agree on 30 of 30 | met |
| 3. Provenance no weaker, one stated exception | The gate checks every report it checked before (0 stale after the regeneration); the scaling and matcher reports, the MIMIC report (through the adapter's files) and the store's match context bind `trajectory_times.py`; the scaling report's build phase is bound by a record whose every file digest the gate recomputes. Exception: D32's paired verdict frozen as `HISTORICAL`, signed off | met, with the stated exception |
| 4. Origins unambiguous | The tool's tests reuse a run and reuse the reuse; both name the first report's path, sha256, git blob id, commit, time, environment and benchmark digest, every reused size says so, and every refusal reason is exercised | met (tests; no committed report has reused yet) |
| 5. Built afresh, kept | `/tmp/d34/scaling` (9.8 GB) holds the five snapshots | met |

*Not measured, and what remains open:*
- No production reuse has been run: reuse is shown at 100 patients in the tests, not at 1,000,000. The next change to matching alone is its first real use.
- The record covers what a build loads and reads through Python, and DuckDB is confined to the build's directories. Native reads by other libraries, the operating system and library internals beyond their versions are not covered, as `build_covers` states.
- The 8-worker paired p95 had one noisy pair (1.221, both codes slow), reported, not judged.

**Decision: D34 ships.** The scaling report binds its build phase by a runtime record, and a change to matching alone can reuse its builds. The engine is split so that a build loads no matching code, with results byte-identical everywhere.

## D35 An exact target split for undecided `first` and `last` sets, under rules fixed before measuring

D33's rises missed their usefulness bar because D30's order rule leaves a `first` step undecided when its target is uncertain and a `during` reads its end (the graph template's blocker; the parity trajectory "a seizure while taking the first blocker", 9 of its 10 candidates unresolved). The owner asked to look at that rule first and to retry rises later. The design (a note outside the repository, with a prototype that shares no decision code with the engine) is a third stage of `decide_ordered`: an exact split over the possible targets of the uncertain steps, run only on the relaxation sets D30 or D31 leave undecided. This entry records the design, a calibration, and the rules, committed before any measurement of the stage.

**The idea.** In a choice of times w, the target of a `first` step s is T exactly when every other event y of its family F satisfies t(y) − t(T) ≥ δ(T, y), with δ = 0 when T precedes y in the profile's tie order (start range, end range, id) and 1 otherwise; for `last`, mirrored, t(T) − t(y) ≥ δ with the tie order (latest start, earliest start, end range, id). These are difference constraints. A *scenario* σ gives each split step one of its possible targets; the worlds whose targets are σ form a simple temporal network N_σ (the patient's network and that region), and the scenarios partition the worlds. Within a scenario each step reads its own target's start and end variables, so a step whose end is read (a `during` from it) is covered: there is no envelope point whose end is unknown. With Σ* the scenarios whose N_σ is feasible:
- *possible* ⇔ some σ ∈ Σ* and some binding β of the other steps make N_σ with the query's constraints C(σ, β) feasible;
- *β exact* ⇔ for every σ ∈ Σ*, N_σ entails every constraint of C(σ, β) (each difference constraint's bounds over N_σ lie inside it);
- *none* ⇔ no σ ∈ Σ* has a binding.

This is the true semantics rewritten as a finite disjunction of simple temporal networks: exact, not only sound.

**Design** (the engine's stage, `_target_split`, behind a switch `_SPLIT`):
1. *Where it runs.* Only on a set D30 or D31 leaves undecided (`decide_ordered`'s second value is not None): D30's `exact`, `possible` or `none` beside an unsafe step, and D31's envelope search that found a binding. A stopped first search is not handed to it. *Corrected after the review:* on D30's path a second search (over every possible target) that stops is reported as `none` beside an unsafe step, and that set is handed to the split; this is harmless, since the split decides only what its own complete searches decide. Decided sets pay nothing.
2. *Split steps S.* The unsafe uncertain steps, closed under "families share an event" over the uncertain ordered steps: a safe step whose family meets a split family is split too. *Corrected after the review:* the reason given here was wrong. The closure is not needed for exactness: an event of the safe step's family that is not a split target already starts at least δ after that target in every world of the scenario, so keeping the safe step on its envelope point would also be exact. The closure is harmless; it only enlarges the scenario products. Other safe steps stay on their envelope points, which is exact and independent of the split; certain steps bind their events as before.
3. *Gate.* Every split family is unlinked (`_Occurrence.unlinked`), and the product of the split steps' possible targets is at most K (below). Otherwise D30's or D31's undecided result stands.
4. *Per scenario, over the steps' variables only.* Each family event no step binds is eliminated exactly (Fourier–Motzkin; it is unlinked, so this gives difference constraints only):
   - for a `first` step, T's start ≤ hi(y) − δ, with hi(y) from `Times.dom`, which already reflects y's own end;
   - for a `last` step, T's start ≥ lo(y) + δ;
   - for an event in a `first` family (target T_u) and a `last` family (target T_v), t(T_v) − t(T_u) ≥ δ_u + δ_v.

   An event another step binds (an `any` step's, a certain step's, another split step's target) keeps its own variable, with a conditional fact on the two steps' starts carrying the exact δ. The unary bounds are applied whether or not y is bound (where it is bound they are implied by its fact). The narrowed start, and the target's end re-solved from it, live in a per-scenario view of the patient's times. Feasibility of each scenario (Σ*) is computed over the targets' starts, never assumed.
5. *Possible phase.* For each scenario in Σ*, in candidate order, `decide` runs without its exact phase, each split step on its narrowed target, with the facts added; the first binding found is reported. It names real events in a feasible scenario, so it is truly possible. The scenarios share one search budget, the query's. This departs from the design note, which gave each scenario the full budget: it bounds the stage's cost, and a stopped phase concludes nothing.
6. *Exact phase* (only when the possible phase found a binding). One backtracking search over the non-split steps, with the query's budget, accepting β only if `_certain_jointly` holds in every scenario of Σ*. A constraint among non-split steps reads the same variables in every scenario, and the scenarios' worlds are all the worlds, so it is certain in every scenario exactly when the patient's pairwise `certain` says so; that prunes exactly.
7. *Results.*
   - `exact`: β, naming the picked event for a split step (as D31's envelope does).
   - `possible`: the scenario's binding (the real targets) and witness, `complete: true` when the exact phase completed, otherwise `EXACT_SEARCH_STOPPED` (still undecided).
   - `none`: every scenario's search completed empty.
   - A stopped possible phase leaves the set undecided, with D30's or D31's result.
8. *D18.* An `exact` binding names the picked event for a split step. *Reworded after the review:* the displacement test reads only one end of the picked event's start range, the upper end for `first` (an excluded record before it could have been first) and the lower end for `last`. The true target's start is at most the picked event's latest start for `first` (at least its earliest, for `last`; D30, decision 1), so that test is at least as wide as needed. A `possible` binding names a real target.
9. *Not in scope:*
   - a split family that names a shared variable;
   - a product above K;
   - stopped searches;
   - the store's own Nth and count paths (`_coupled_match` still marks a coupled Nth step's `possible` undecided);
   - D33's rises.

**Why it is exact** (the design note's argument, condensed).
1. Every world has one target per split step (the tie order is total), and it is a possible target, so the worlds are the disjoint union of the scenarios' solution sets.
2. In a scenario each split step reads its target's own variables, so scope, `after`, `during` with its grace and the anchors are ordinary constraints.
3. The projection of a simple temporal network onto some of its variables is its Fourier–Motzkin elimination, again a simple temporal network.
   - An unlinked unbound event is bounded only by its ranges, its own order and the region's edges, so eliminating it gives exactly the unary bounds and the first–last pair facts above.
   - Bound events stay explicit through their facts.
   - The patient's minimal network carries everything else between the binding's variables (as in `feasible`).
4. So the possible phase, complete, is exact; `_certain_jointly` gives the exact bounds over a scenario's network (D31, second attempt, point 4).
5. It runs only on sets the engine leaves undecided, so no decided result can change.
6. A safe step outside S has an unlinked family no other step binds, disjoint from every split family. Its true start ranges independently over its envelope (D30), so its point is exact jointly with the scenarios.

**The reference** (`trajectory_graph_reference.py`, switch `SPLIT`) writes the stage again from the profile:
- S and the gate by its own tests;
- the scenarios from its own possible targets;
- Σ* by Bellman–Ford over the whole patient, with every region constraint explicit (no elimination);
- every binding of the non-split steps enumerated per scenario.

It shares no decision code with the engine.

**Calibration (disclosed; measured before these rules, on main's engine, not the stage).** On the scaling report's kept snapshots (D34) at 1,000 and 10,000 patients, with its 50 queries and windows, every relaxation set the current engine leaves undecided was traced, and its split's product of possible targets counted (scripts outside the repository; aggregates only):
- *The design note's premise was wrong.* It took the scaling report's 38 and 375 `unresolved` results as order-uncertain. They are not: at 1,000 all 38 are `SEARCH_BUDGET_EXHAUSTED`; at 10,000, 363 are and **12** are `OCCURRENCE_ORDER_UNCERTAIN` (all seed 21). So there are **0** order-uncertain results at 1,000 and **12** at 10,000.
- *Those 12:* every one has a split whose step's end is read, every split family is unlinked, and the products are 18 to 23.
- *The other undecided results* are incomplete `none` or `relaxed` results whose relaxation sets the order rule left open (`RELAXATION_ORDER_UNCERTAIN`, `CHEAPER_RELAXATION_ORDER_UNCERTAIN`): 35 at 1,000 (seeds 11, 29, 34) and 445 at 10,000 (seeds 11, 29, 34, 42), counted on the engine's own results (the store's output shows 444 at 10,000). Their sets' products are larger:
  - at 1,000 the gate would admit every set of 13 of the 35 results at K = 64, and of 32 at K = 128;
  - at 10,000, of 157 of 445 at K = 64, and of 404 at K = 128;
  - every set has a product of at most 2,048.
- *The design note's Q1 figure* (the parity trajectory, 9 of 9 `unresolved` → `possible`) was measured by its prototype before any rule. It is disclosed here, and the usefulness rule below is judged on the engine, not the prototype.

**K = 64.** The calibration puts every order-uncertain result's set within 64 (products 18 to 23), and the design note's three graph-template patients within 5. A larger K (128) would admit most of the order-note results too, at up to twice the scenarios per set, under a latency rule whose margin is thin (D31: 8.49 s against 10 s). The order-note results are reported, not ruled. Widening K is a question for a later increment, under its own rule.

**Pre-committed rules** (written and committed before any measurement of the stage; the calibration above came first and is disclosed).
- **Soundness and exactness.**
  1. The independent worlds checker (`trajectory_graph_worlds.py`) finds **0 violations** on at least 20,000 ad hoc cases from each of three generators, with new seeds, and on the suite's own volume. The generators:
     - `tiny_case`;
     - `ordered_case`;
     - a new `during_case`: first or last steps with a `during` from them over families of exposures, points and wide points; some coupled `any` steps; some shared variables, so that the gate is exercised.
  2. On every one of those cases where the split runs and completes, the engine's status, relaxation set and certainty equal the truth by enumeration (`trajectory_graph_worlds.truth`).
  3. The reference agrees on every one of those cases: status, what is reported beside it, and every reported binding by `binding_reaches`.
  4. Planted defects are caught, each by the worlds check or the comparison with the truth:
     - the tie δ ignored (δ = 0 always);
     - the narrowing dropped (no unary bound from an unbound event);
     - exact tested on one scenario only;
     - a `during` reading the picked event's end instead of the scenario target's.
- **Movement.**
  1. Every changed engine result starts undecided (`unresolved`, or any result with `complete: false`), classified by the engine's own result (`match_prepared` or `_coupled_match`, before the store's coverage and excluded-records rules), as in D31's second attempt.
     - It ends decided, or still undecided (counted and listed apart, not a failure).
     - No decided engine result changes.
     - Checked on every small case above, on the 300 recorded digests, and in process on the kept 1,000 and 10,000 snapshots with the scaling report's 50 queries.
  2. With `_SPLIT` off this tree reproduces main's digests: the 300 recorded match digests, and the per-query digests of the in-process runs above, against main's code.
  3. MIMIC unchanged: every figure of the regenerated MIMIC demo report other than identifiers, digests, artifact bindings and timings equals D34's. Times are exact there, so no target is uncertain.
  4. The 300 recorded digests are re-recorded with a per-seed fixture, `execution_store_match_digests_d35.json`, which a test recomputes. It holds, per seed:
     - the digest with the switch off, which must be main's;
     - the digest with it on;
     - each changed result's transition, by the engine's own result and by the store's.
- **Usefulness** (the reason to ship; ship only if both hold).
  1. On the synthetic cohort, the trajectory page's "a seizure while taking the first blocker", as the store parity test translates it, has **at most 1 of its 10 candidates** (patients with a store candidate) `unresolved`. Disclosed: the prototype measured 0 of 10 before this rule.
  2. **At least 75%** of the `OCCURRENCE_ORDER_UNCERTAIN` results are decided, at 1,000 and at 10,000 (in process, with the switch off and on, on the scaling report's queries).
     - At 1,000 there are none (calibration), so the rule holds there vacuously; at 10,000 it is judged on 12 results.
     - The share of order-note results decided is reported at both sizes, not ruled.
- **Latency.** Judged under the paired protocol (`tools/benchmark_matcher.py --paired-confirm`, `paired-interleaved-1.0`) against main (39b72c7), at 300,000 patients, with 30 queries, 4 repeats, and 14 and 8 workers, in the regeneration's matcher report:
  - the 14-worker paired p95 ratio (this tree over main) is **at most 1.05 in every pair**;
  - this tree's own 14-worker p95 is **at most 10 s**;
  - the maximum is at most 30 s, and query-process memory is under 4 GB;
  - the 8-worker figures are reported.
- **Regeneration** (owner policy and D34). The change is matching only: no file a build loads changes (`trajectory_times.py` and everything the build record lists are untouched).
  - The scaling report is regenerated with `--reuse-builds` on the kept D34 snapshots, its first use at 1,000,000. Whether the reuse probe accepted, and the run's time against a fresh build, are reported. A refusal is reported with its reason, not worked around.
  - Also regenerated: the MIMIC demo (it binds `trajectory_graph.py`), the matcher comparison with its confirmation and arm A, and the paired confirmation against main.
  - One heavy job at a time.
- A failure is fixed, never tuned away. If a ship condition is missed, the stage is left switched off or reverted, and the numbers are recorded here.

**Built** (d00ffa9):
- the engine's `_target_split` and its switch, with `TargetFact`, the scenario view and the two phases;
- the reference written again;
- `trajectory_graph_worlds.truth`;
- `during_case` and `TargetSplitTests`: worlds check, truth, reference, movement, the gate, a named tie case, and the four planted defects;
- the D30 and D31 tests, which keep those increments' records, now run with the split off;
- `D35MovementTests` and its fixture, and the 300 recorded digests re-recorded;
- the store parity table and the mockups README;
- the profile's description of the split.

**Measurement** (after the rules, 0f8e3c0; on d00ffa9; one heavy job at a time, from 22:21 to 03:04).

| Rule | Measured | Verdict |
|---|---|---|
| Soundness 1: the worlds check, at least 20,000 per generator | 60,000 ad hoc cases (seeds `d35-rule-tiny`, `d35-rule-ordered`, `d35-rule-during`): **0 violations**. The split ran on 3,065, 3,277 and 6,945 of them, and decided every set it ran on in 2,457, 3,277 and 6,291; on the rest the gate refused (shared variables) somewhere. The suite repeats this on 600 cases per generator and the review cases | met |
| Soundness 2: equal to the truth where the split ran and completed | All 12,025 such cases (and every other decided case) compared with `truth` by enumeration: **0 differences** | met |
| Soundness 3: the reference | Agrees on all 60,000: status, what is reported beside it, and every reported binding (`binding_reaches`) | met |
| Soundness 4: planted defects | Over 15,000 cases (5,000 per generator, seed `d35-rule-defects`), counted as unsound / not the truth: tie δ ignored 15 / 15; narrowing dropped 492 / 452; exact on one scenario 550 / 534; a `during` reading the picked event's end 119 / 112. The suite catches each one; the tie is caught by a named case (`TIE_CASE`) | met |
| Movement 1: only from undecided, by the engine's own result | Small cases: every changed result started undecided and ended decided (2,457, 3,277 and 6,291). The 300 digests: 12 seeds and 35 engine results moved. 27 went to a decision. The other 8 stay undecided: 6 `unresolved` → `possible` with `EXACT_SEARCH_STOPPED` (seed 144, whose query has a search budget of at most 300), and 2 `unresolved` → `none` with `RELAXATION_ORDER_UNCERTAIN`. In process on the kept snapshots: 13 results at 1,000, all to a decision; 169 at 10,000, 168 to a decision and 1 within undecided (a `relaxed` result whose exact search stopped). No decided result changed anywhere | met |
| Movement 2: switch off reproduces main | The 300 recorded digests (a test), and all 50 in-process per-query digests at 1,000 and 10,000 against main's code (39b72c7, extracted) | met |
| Movement 3: MIMIC unchanged | Every figure of the regenerated demo report other than artifact bindings, timings and resources equals D34's; the snapshot identifier is unchanged too | met |
| Movement 4: the fixture | `execution_store_match_digests_d35.json`, recomputed by `D35MovementTests` | met |
| Usefulness 1: Q1 at most 1 of 10 unresolved | **0 of 10**. All ten candidates are `possible` with `complete: true` (none `exact`, none incomplete). Main's engine gave 9 `unresolved` and 1 `possible` | met |
| Usefulness 2: at least 75% of order-uncertain results decided | 1,000: none exist (vacuous). 10,000: **12 of 12** decided, every one `unresolved` → `none` with `complete: true` (no `possible` among them). Order-note results, reported: 13 of 35 decided at 1,000 and 156 of 445 at 10,000 | met |
| **Latency** (paired, 300,000, 14 workers) | Ratios by pair 0.954, 0.969, 0.966, 0.900 (reference first, candidate first, reference first, candidate first); pooled 0.973. This tree's p95 **8.97 s** (9.21, 8.53, 8.48, 8.24 by repeat), max 12.21 s, 0.75 GiB; main's code in the same session 9.22 s | met |

*What the latency figures show.* Seed 29 is slower: median 7.73 → 8.44 s over the four 14-worker pairs (+9%), the cost of the split on its order-note sets. Seed 21 is about the same (3.20 → 3.24 s). The p95 of 30 queries is the second-slowest query: in every run of both codes it is seed 26, except main's first run (seed 15). Seed 29 is third in three of this tree's four runs, within 0.1 s of seed 26, so the p95 rule had little margin against seed 29 overtaking. Seed 7 stays the maximum. Total time per run moved by 0.96 to 0.99. *At 8 workers* (reported): ratios 0.978, 1.024, 0.996, 1.055. Both candidate-first pairs are above 1, so this tree is slower there or the order matters; pooled 1.043. This tree's p95 is 10.91 s and main's 10.45 s in the same session; the 8-worker target is still missed, as it has been since D30. *The sequential confirmation* (reported, not the rule): 14 workers p95 10.01 s (D34: 8.12 s), 8 workers 9.93 s (9.94 s). The 14-worker arm runs last, after about 90 minutes of load, and drifted in the same way in D31. The paired runs govern, and in them this tree is faster than main at 14 workers in every pair, which a change that only adds work cannot cause. So the gap between 0.90 and 1.0 is noise and order, and the honest reading is "no measurable regression at 14 workers, about +4% at 8".

*Byte identity at scale.* Arm B's per-query digests differ from D34's exactly where the in-process check said they would: seeds 29 and 34 at 1,000; 21, 29, 34 and 42 at 10,000; none at 100,000 and 300,000 (their ten queries carry no undecided set); seeds 21 and 29 in the confirmation. In the paired run the two codes agree on 28 of 30 queries (seeds 21 and 29 differ), and each code's digests are stable across its runs. Arms B, `B_unpruned` and every C arm agree at every size. Arm A's digests equal D34's.

*Scaling, with the D34 builds reused* (`--reuse-builds`; its first use at 1,000,000). The reuse probe's build recorded exactly what D34's builds recorded, so reuse was accepted. The run took **1,740 s against D34's fresh 9,197 s**. By size (this run against D34's): 1,000: 24 / 39 s; 10,000: 134 / 225 s; 100,000: 109 / 666 s; 300,000: 326 / 1,913 s; 1,000,000: 1,137 / 6,354 s. Snapshot identifiers, table rows and every query's candidate count equal D34's. Each size says `build_reused`, and the results page marks its build figures (†) and names the origin. Status mixes:
- 1,000: `relaxed` 561 → 574, `none` 38,918 → 38,905; incomplete `none` with `RELAXATION_ORDER_UNCERTAIN` 35 → 22.
- 10,000:
  - `unresolved` 375 → 363 (the 12 order-uncertain results, now `none`);
  - `relaxed` 5,814 → 5,958;
  - `none` 389,606 → 389,475;
  - `not_comparable` 61,627 → 61,626 (an engine `none` with an order note that the coverage rule had read as `not_comparable`, now decided as `relaxed`, as in D31's part B1);
  - incomplete `none` with `RELAXATION_ORDER_UNCERTAIN` 431 → 288.
- 100,000, 300,000 and 1,000,000: unchanged.

**Evidence in proportion** (from the independent review):
- The 10,000 usefulness rule rests on 12 patients of a single query (seed 21).
- The Q1 bar was set after the prototype had measured 0 of 10 (disclosed in the rules).
- Every Q1 result becomes `possible`, not `exact`: the split says what is true of these records, which is that the blocker may or may not have been the first.
- Usefulness as a whole rests on two queries and about 22 patients.
- The small-case checks are broad (60,000 cases, three generators). The cohort-level gain is narrow. The order-note results, which K = 64 mostly leaves undecided (22 of 35 and 289 of 445 still undecided), are where most of the remaining undecided results are.

**Independent review** (its scripts in `/tmp/rv35`). No must-fix. About 116,000 fuzz cases against its own enumeration oracle found 0 wrong `exact`, `none` or `possible`; `_SPLIT` off is byte-identical to main; no build file was touched. Its other points, folded in here as documentation (no code changed after the rules' measurements):
- *"Decided" was undefined in the usefulness rules.* Usefulness had been measured before the review arrived, so the rule stands as written. Read strictly, decided means `exact`, `none` or `relaxed`, or `possible` with `complete: true`, and anything with `complete: false` is undecided. Under that reading the verdicts are the same: Q1's ten are all complete `possible`, and the 12 at 10,000 are all complete `none`. The 300-digest fixture does hold 6 incomplete `possible` results (seed 144), counted there as within undecided.
- *Stopped searches.* The design said a stopped search is not handed to the split; on D30's path a stopped second search is (corrected above, point 1). Harmless.
- *The closure's stated reason was wrong* (corrected above, point 2); the closure only enlarges products.
- *The scenario view shares mutable state with `Times`* (`component`, `synthetic`, `_occurrences`, `_relations`). It is safe only because nothing writes new values there through a view: the occurrences and the records' keys are computed on the patient's own `Times` first, and `_solve` rewrites `component` with the same roots. That is fragile; a later change that creates points or occurrences through a view would corrupt the patient's own cache.
- *D18 point 8* reworded above: only the upper end (for `first`) or the lower end (for `last`) of the picked event's start range is read.

*Follow-ups:*
- `trajectory_graph_worlds.check` flags `possible` with `EXACT_SEARCH_STOPPED` as unsound whenever some binding is exact, though such a result does not claim that no binding is exact. This predates D35 (the engine's own `EXACT_SEARCH_STOPPED`). No check here met it: the small cases have a 10,000,000 budget.
- The gate multiplies the possible targets of two steps on one concept and occurrence, though only their diagonal scenarios are feasible; counting them once would admit more sets.
- Planted defects to add: the first–last pair facts dropped, and the conditional target facts dropped.
- Latency watch:
  - `_split_exact` costs up to the search budget times the number of feasible scenarios;
  - `_scenario_view` rebuilds and re-sorts every kernel record per scenario;
  - seed 29's +9% is the measured cost of both.
- K: the order-note results need products up to 2,048 (91% within 128). Widening K is a later increment's question, under its own rule.

**Decision: D35 ships.** The target split decides the sets D30 and D31 left undecided, wherever its gate admits them and its searches complete. Every result it decides equals the profile's semantics, and nothing decided before D35 changed. The parity trajectory that sank D33's graph template is now decided for all ten candidates. Rises and falls can be retried under their own rule, with a usefulness test on more patients.

## D36 Rises and falls retried: D33's relative conditions on D35, in a module no build loads, with a usefulness test on 30 cohorts

D33 built rises and falls (bccee57) and they were sound on every check, but missed the usefulness bar on one template: the graph template had 3 of its 4 candidates `unresolved`, all by D30's order rule on the `first` blocker whose end a `during` reads. The owner confirmed the per-template reading of the bar, asked to fix that rule first, and to retry rises later on more patients. D35's target split now decides that shape: "a seizure while taking the first blocker" went from 9 `unresolved` of 10 candidates to 0. This entry retries rises on main after D35 (81e812c), under rules written and committed before any measurement.

**What is restored, and what is not changed.** The semantics are D33's, with the owner's decisions of 2026-11-14 unchanged:
- monthly summaries are whole-month periods, ordered by their start, with windows measured between period starts;
- the baseline is the mean of up to k (at most 3) strictly earlier readings of the same item class and unit within W, in exact arithmetic, with an explicit positive floor;
- certainty is "every configuration satisfies the condition", and an undetermined baseline counts as possible through D32's two-pass rule.

The request (profile `execution-store-conditions-1.2`, `relative`, `widen_factor`, the refusals by name), the readings and their exact strengths, the two-pass rule with relative steps counted, D18 reach by the step's scope, the reference written again, the worlds checker, the three small-case generators, the tests, the translator's rise rule (k = 2, W = 75 days, floors 1 and 10⁻⁹) and the parity templates are bccee57's, carried onto main. Nothing in them needed changing for D35: the relative path reaches the engine through `match_prepared` and `decide_ordered`, so the target split applies to rise templates as to any query.

**Design: a module of its own** (owner policy on build reuse, D34).
- The scaling report's build record hashes every repository module a build loads, among them `patterns/execution_store.py` and the converter `patterns/synthetic_ingest.py`. D33 had put rises in both. Here they live in `patterns/store_conditions_relative.py`, which no build loads. `execution_store.py`, `synthetic_ingest.py`, `trajectory_times.py`, `series_values.py`, the build worker and every other file the record lists are byte-identical to main, so `--reuse-builds` can reuse the kept D34 builds.
- The module holds `Request` (the store's `Request` with profile 1.2), the readings loader and strengths, `derive_patient`, `match_derived`, the candidate filter for relative tags, D18 reach, the context, and `match` and `candidates`. Where bccee57 had edited a function of the store, the module carries that function with bccee57's edit (the store's code plus the relative branches), since the store's own file cannot change. Everything else (loading, pruning, comparability, excluded records, worker pools) is the store's own code, called as it is.
- `store_conditions_relative.match` and `candidates` hand every request but a 1.2 one to `execution_store.match` and `candidates` unchanged. `execution_store.Request` still refuses profile 1.2 (`INVALID_CONDITIONS`), as on main. Callers that may send a rise call the module: the parity harness, the usefulness test and the MIMIC demo's relative differential. Nothing a build runs imports it.
- With workers, a 1.2 request runs in the store's own pools (`get_pool`), each unit by the module's worker function over the store the worker opened.
- The context of a 1.2 result binds the module's digest beside the engine's artifacts, with the derived and relative rules' digests, so a change to the module changes `context_id`.
- *A departure from D33: the period label.* D33 had the converter label diary summaries as period readings (`labels.period_reading_kinds`). The converter must now stay byte-identical, so the synthetic source's declaration is made by the module (`synthetic_provenance`, `synthetic_ingest`): the converter's own provenance with the label added when series are on. The label is still in the snapshot's provenance, so still part of its identity, and the store still reads it from the snapshot. Builds without series (the scaling report's) carry no label, as before.
- *The cost of the copies.* The module's copies of the store's `Request.__init__`, `derive_patient`, `match_derived`, `_candidates`, `_derived_reach` and `match` must follow any later change to the originals. Tests compare the two where they should agree: a 1.0 or 1.1 request compiles to the same request in both, and a 1.2 request without a relative step gives the 1.1 answer's results.
- *The translator.* `storeRequest(def, freeText, pids, options)` translates rises only when asked (`options.rises`, as the parity and usefulness tests do). By default it refuses them by name (`RELATIVE_CONDITION_UNSUPPORTED`; the constant `STORE_RISES`). If rises ship, the default is flipped.

**The usefulness test** (`demos/mockups/rise_usefulness.cjs`, report `verification/d36-rise-usefulness-report.json`). D33 rested on 6, 6 and 4 candidates from one cohort.
- *Cohorts.* Distinct synthetic cohorts from the generator with different seeds (`generate.py --seed`), 48 children each. Each child's timeline and records are drawn from `Random(f'{seed}:{key}')`, so two seeds share no patient. Each cohort is built by the converter emitting series, with the period label (`store_parity.py build`). Copying patients would add nothing, so none are copied.
- *Declared seeds:* **1001 to 1030** (30 cohorts, 1,440 children).
- *Extension rule:* if some template has fewer than 30 candidates pooled over those, seeds 1031, 1032, … are added in order, one at a time, until every template has at least 30. The stop depends on candidate counts only, never on a status.
- *Templates (ruled):* the trajectory page's three rise templates, through the page's own translator, as the parity test translates them: Sequence; Tree and Graph without their gene step (which the store refuses by name, `GENE_CLASSIFICATION_UNSUPPORTED`). Each has the page's window (from the second seizure, 24 months), scope (`from_start`) and free-text setting (stated findings).
- *Variants (declared now, reported, not ruled):* each template with factor 1.5 instead of 2; each with the window from the first seizure for 36 months.
- *Candidates:* patients with a store candidate (`candidates`) and an index window, as in D33.
- *Reported:* per template, per seed and pooled; status mixes and reasons; and what D35's split contributes (each cohort matched again with `_SPLIT` off).
- *Disclosed before these rules.* While porting, the parity test was run on the committed cohort (seed 20261103, D33's), and the usefulness tool was smoke-tested on it. There the three templates have 0 of 6, 0 of 6 and 0 of 4 `unresolved` (D33: 0, 0 and 3 of 4); the graph template's 3 became `possible` through the split. That cohort is not among the declared seeds; it is reported as the parity cohort, not ruled.

**The MIMIC demo relative differential** (`verify_mimic_store_ingest.relative_differential`).
- Two named requests: heart rate (item 220045, bpm) rising to at least 1.25 times the mean of up to 3 readings in the 6 hours before; and invasive mean arterial pressure (220052, mmHg) falling to at most that mean divided by 1.25, with a relaxation to 1.1.
- 60 seeded requests: a measured step of a random query given a rise or a fall (factor 1.1 to 2, a recorded unit, a floor, k from 1 to 3, W from 1 to 72 hours), a third of them with `widen_factor`.
- Every chart time on the demo is exact, so every baseline is determined, unless an excluded chart row or a reading in another unit lies among the previous readings.
- The store must equal `prepared_reference` on every query and `exhaustive_match` on the named ones and the first 4 seeded ones, with pruning on and off byte-identical and every factor relaxation contained.

**Pre-committed rules** (written and committed before any measurement of this increment; the disclosure above came first).
- **Soundness.**
  1. *The worlds checker finds 0 violations* on at least 20,000 ad hoc small cases from each of the three generators (multi, coupled, period), with new seeds (from 2,000,000), and on the suite's volume. Every status but `unresolved` is checked, and the match beside `unresolved`.
  2. *0 mismatches against the reference* (`json.dumps` without sorting keys):
     - on every one of those small cases;
     - on at least 400 seeded relative requests over snapshots (the fixtures made for it and the synthetic cohort built with series), each compared at 1 and 2 workers, at both detail levels and with pruning on and off;
     - on the MIMIC demo relative differential.
  3. *The seven D33 planted defects are caught*, each by the worlds checker, the enumeration of strengths or the containment check: the baseline window off by one; k + 1 readings; the floor ignored; unit mixing; an excluded reading ignored; certainty claimed only on a determined configuration; a relaxation that does not contain the stricter condition.
  4. *Relaxations contain:* every drawn `widen_factor` holds every event of the stricter tag at both strengths (small cases, snapshots, the MIMIC demo).
  5. *Pruning on and off are byte-identical* on every relative differential.
- **No change without the new fields.**
  - `execution_store.py`, `synthetic_ingest.py` and every file the D34 build record lists are byte-identical to main (81e812c).
  - The 300 recorded match digests are unchanged.
  - The matcher report's per-query digests equal D35's at every size, for every arm and in the confirmation.
  - The scaling report's rows, candidate counts and status mixes equal D35's at every size.
  - Every MIMIC demo figure other than identifiers, digests, artifact bindings, timings and the new `relative_differential` equals D35's.
  - Every request without profile 1.2, sent through the module, gets the store's own answer byte for byte (a test, at 1 and 2 workers).
- **Usefulness.** For each of the three templates, pooled over the declared seeds and any extension: **at least 30 candidates**, and **at most 30% of them `unresolved`**. Reported per template, per seed and pooled, with the split's contribution and the variants.
- **Latency.** Judged under the paired protocol (`tools/benchmark_matcher.py --paired-confirm`, `paired-interleaved-1.0`) against main (81e812c), at 300,000 patients, 30 queries, 4 repeats, 14 and 8 workers, in the regeneration's matcher report:
  - the 14-worker paired p95 ratio (this tree over main) is at most 1.05 in every pair;
  - this tree's own 14-worker p95 is at most 10 s;
  - the maximum is at most 30 s, and query-process memory is under 4 GB;
  - the 8-worker figures are reported.
- **Reuse.** The scaling report is regenerated with `--reuse-builds` on the kept D34 builds (`~/ptm-local/scaling-d34`). If the reuse is refused, the run stops and the refusal is reported, not worked around.
- **Regeneration, only what is stale:** the MIMIC demo; the scaling report, by reuse; the matcher comparison with its confirmation and arm A; and the paired run against main. One heavy job at a time.
- **Ship rule.** Rises ship, and the translator translates them by default, only if every rule above holds. Otherwise the translator keeps refusing them by name, and the numbers are recorded here. Whether the module then stays, unreachable from the page, or is reverted as D33's code was, is the owner's call. A failure is fixed, never tuned away.

**Amendments after the rules** (disclosed; the rules above are kept as committed).
- *The cohorts are not independent children* (the independent review, M1, which arrived after the usefulness run). The design said that two seeds share no patient. That is true of the records and wrong of the children. The generator scripts the reference child C001 (born 2019-04-12, with a fixed timeline) in every seed. Every other child takes its archetype, sex, birth year and overrides from the generator's fixed `PLAN`; only the draws vary with the seed. So the 30 cohorts are 30 redraws of one plan of 48 children, not 1,440 independent children, and the disclosed look at seed 20261103 included C001 too. The run was not repeated. The tool now reports candidates and `unresolved` results per `PLAN` key, with C001 apart, and the shares three ways (all candidates, C001 left out, distinct `PLAN` keys); the measurement section gives all three. The owner is asked how the bar's "candidates" should be counted, for the record.
- *The protocol is now enforced by the tool* (review, should-fix): `rise_usefulness.cjs` takes no seeds; it runs 1001 to 1030 and then 1031, 1032, … in order while a template is short of candidates. It reproduces the committed figures exactly.
- *What shipping means* (review, should-fix). Flipping `STORE_RISES` changes the translator only, which the parity and usefulness tools use. Nothing serves the trajectory page from the store yet; that is D29's stage 2. Shipping here means that the store's request language expresses the page's rises, soundly and usefully, and that the translator writes them by default.
- *Two tests added* (review, should-fix):
  - the sources of the fourteen store functions the module copies are pinned by digest, so a change to one fails with "update store_conditions_relative";
  - a 1.2 request without a relative step equals the store's 1.1 answer for Nth, Nth from last, span and period counts and a value condition with an Nth, with a `widen_concept`, at 1 and 2 workers and both detail levels. This replaces a loop in an earlier test that did nothing.
- *A limit stated, not changed* (review, nit). `_patient_readings` and the reference time a reading that no step loaded by its recorded start range. That equals `Times.dom` only because a series event must be a point (`SERIES_EVENT_NOT_POINT` at ingest) and a patient with shared variables has every event loaded. Called directly, the per-patient function can give a wrong `possible` for a non-point reading (the review's `cx1`); ingest refuses such a reading. The comment belongs in the two files, but both are bound by reports the regeneration was writing, so it is in the store doc now and in the code at the next change to either.

**Built** (8d7a638; tests and tools after the review, see the amendments): `patterns/store_conditions_relative.py`; the reference, the worlds checker and the small-case generators as bccee57 had them; `patterns/test_relative_conditions.py`, with the module-boundary tests; the translator's `rises` option; the parity templates; `demos/mockups/rise_usefulness.cjs`; the MIMIC demo's `relative_differential`.

**Measurement** (after the rules, fe559ab; on 8d7a638; one heavy job at a time; the regeneration from 14:19 to 16:03 on 2026-11-15, at 50b5a82).

| Rule | Measured | Verdict |
|---|---|---|
| Soundness 1: worlds checker, at least 20,000 per generator | 60,000 ad hoc cases, seeds from 2,000,000: **0 violations**. Statuses (exact, possible, relaxed, none, unresolved): multi 695, 148, 109, 16,475, 2,573; coupled 323, 112, 49, 17,360, 2,156; period 683, 419, 98, 16,216, 2,584. The suite repeats this at 1,000 per generator | met |
| Soundness 2: reference | **0 mismatches** on the 60,000 small cases. Snapshots: 500 requests (fixture 130, shared variables 120, without the period label 100, the synthetic cohort with series 150), 21,200 patient results, **0 mismatches**, and 0 differences at 2 workers (chunk 9), at `decided` and with pruning off. The MIMIC demo: below, 0 mismatches | met |
| Soundness 3: the seven planted defects | Worlds-check violations over 12,000 cases (4,000 per generator: multi, coupled, period): window off by one 191 (85, 35, 71); k + 1 readings 28 (13, 6, 9); floor ignored 64 (30, 6, 28); unit mixing 337 (92, 108, 137); excluded reading ignored 251 (78, 36, 137). Certainty only on a determined configuration: 26 tag strengths wrong against enumeration in 900 cases (0 without the defect); in these 12,000 cases it produced no wrong result claim (D33's run found one). A relaxation that does not contain the stricter condition: 185 containment failures in 1,346 checks | each caught |
| Soundness 4: containment | 1,423 factor relaxations on small cases, 90 on snapshots and 11 on the MIMIC demo, each holding the stricter tag's events at both strengths | met |
| Soundness 5: pruning | On and off byte-identical on the 500 snapshot requests and the 62 MIMIC requests | met |
| No change: build path | `execution_store.py`, `synthetic_ingest.py` and every file the build record lists are byte-identical to 81e812c (checked before the code commit); the reuse probe recorded exactly what D34's builds recorded | met |
| No change: 300 digests | Unchanged (`patterns.test_execution_store`) | met |
| No change: matcher digests | The matcher report is not regenerated: nothing it binds changed (the provenance gate does not count it stale), so its per-query digests are D35's by construction. In the paired run below, this tree and main give byte-identical results on all 30 queries | met |
| No change: scaling | Reused at every size (the probe accepted); snapshot identifiers, table rows, every query's candidate count and the status mixes equal D35's at 1,000 to 1,000,000. The run took 1,740 s | met |
| No change: MIMIC | Every figure of the regenerated demo report other than artifact bindings, timings, resources and the new `relative_differential` equals D35's, the snapshot identifier included (2,127 s) | met |
| No change: through the module | A test: no conditions, 1.0 and 1.1 requests through `store_conditions_relative.match` and `candidates` equal the store's own answers at 1 and 2 workers; after the review, also a 1.2 request without a relative step against the 1.1 answer (20 comparisons) | met |
| **Usefulness** | Seeds 1001 to 1030, no extension needed. **Sequence 0 of 189 `unresolved`; Tree without its gene step 0 of 189; Graph without its gene step 0 of 142.** Counted without C001: 0 of 159, 0 of 159, 0 of 112; over distinct `PLAN` keys: 0 of 16, 0 of 16, 0 of 7 (amendments) | met, under every reading |
| **Latency** | Paired against main (81e812c) on the scaling report's 300,000 snapshot (`verification/d36-paired-verdict.json`): 14-worker p95 ratios **1.010, 1.000, 0.957, 0.964** (reference first, candidate first, reference first, candidate first), pooled 0.961; this tree's p95 **8.64 s** (8.07, 8.13, 8.64, 8.74 by repeat), max 11.44 s, 0.82 GB; main's code 8.99 s in the same session. Byte-identical results on all 30 queries. 8 workers (reported): ratios 0.998, 0.941, 0.993, 1.006; p95 10.28 s (main 10.74 s), max 14.48 s. The run took 2,368 s | met |
| Reuse | Accepted at every size (each says `build_reused`; `build_origin` names D34's report), 1,740 s against D34's fresh 9,197 s | met |

*Usefulness, in detail* (`verification/d36-rise-usefulness-report.json`).
- Results among the candidates: Sequence 96 `possible`, 49 `relaxed`, 44 `none`; Tree 102, 45, 42; Graph 96, 31, 15. None is `exact`: the first blocker's or the first seizure's record times leave the target uncertain, and the target split decides them as `possible`, not `exact`.
- *What D35 contributes.* With `_SPLIT` off, the graph template has 67 of 142 candidates `unresolved` (47%), all `OCCURRENCE_ORDER_UNCERTAIN`; Sequence and Tree have none. Without the split, rises would miss the bar on the graph template again, as in D33.
- *What the rise contributes to the uncertainty: nothing here.* With period readings every baseline on the synthetic diary is determined, so every rise tag is certain or absent, no second pass runs, and no result is `STEP_CONDITION_UNCERTAIN`. On the MIMIC demo, where a baseline can include an excluded chart row, the condition does leave results undecided (below).
- *Variants* (reported): factor 1.5, 0 `unresolved` in each template (189, 189, 142 candidates; with the split off, Graph 97); the window from the first seizure for 36 months, 0 in each (173, 173, 126; Graph with the split off 57).
- *Per seed:* 3 to 9 candidates per cohort for Sequence and Tree, 3 to 7 for Graph; no cohort has an `unresolved` result.
- *Not independent children* (amendments): Graph's 142 candidates are C001 in all 30 cohorts and six Dravet plan slots (dravet-typo 22, dravet-ncb-2 20, dravet-ncb-3 20, dravet-preterm 18, dravet-lost 17, dravet-ncb-1 15). Sequence's and Tree's 189 are C001 and 15 slots (the Dravet six, four CDKL5 and five PCDH19).

*The MIMIC demo relative differential* (62 queries: the two named ones and 60 seeded; 6,800 results compared, `exhaustive_match` on the first 6): **0 mismatches**, pruning on and off identical, 11 factor relaxations contained. Readings tagged on their own condition: 38,265 certain and 4,329 possible only (an excluded chart row of the item class, or a reading in another unit, among the previous readings).
- *Heart rate rising to at least 1.25 times the mean of up to 3 readings in the 6 hours before* (94 results with a window): 30 `exact`, 52 `none`, 9 not comparable, 3 `unresolved` (`STEP_CONDITION_UNCERTAIN`).
- *Invasive mean arterial pressure falling to at most that mean / 1.25* (93): 19 `exact`, 14 `relaxed` (to 1.1), 39 `none`, 19 not comparable, 2 `unresolved` (`STEP_CONDITION_UNCERTAIN`).
- *Seeded* (5,714 results with a window): 70 `unresolved` (1.2%): 44 `ADMISSION_EXCLUDED_RECORDS`, 26 `STEP_CONDITION_UNCERTAIN`; 4,434 not comparable (excluded chart rows the step's scope admits, D18).
- The differential took 435 s of the demo's 2,127 s.

*Parity* (the committed cohort, D33's seed): the three rise templates give 77 of 144 agreeing results, 634 of 816 over all seventeen trajectories; of the 67 differences, 59 are coverage and 8 are letter mentions, medication records and record reconciliation together. None is named `relative-baseline`. Each template has 0 of 39 results `unresolved` (6, 6 and 4 candidates).

*Latency, in proportion.* The two codes run the same `execution_store.py`, engine and benchmark; the confirmation's queries carry no relative condition, so the candidate takes main's code path byte for byte. The ratios measure session noise, and they are within it. The 8-worker target (10 s) is still missed for both codes, as since D30.

*What the matcher report now records.* It is D35's, unchanged: its digests, confirmation and paired run are D35's. D36's latency record is its own file, `verification/d36-paired-verdict.json`, bound by artifact digests for the provenance gate. The results page reads it as the verdict run and the matcher report's paired run as D35's.

**Decision: D36 ships.** Every pre-committed rule is met. That covers soundness, no change without the new fields, usefulness (under every reading of the candidate count; amendments) and latency. Reuse was accepted. The trajectory page's translator now writes rises and falls by default (`STORE_RISES = true`); the named refusal remains for a caller that asks. The store's profile stays `execution-store-3.1` and the match profile `execution-store-match-2.0`; the request gains profile `execution-store-conditions-1.2`, answered by `patterns/store_conditions_relative.py`. The store's own file, the converter and every file a build loads are unchanged.

*What shipping does and does not mean* (amendments). The store's request language now expresses the page's three rise templates, with gene steps still refused. The parity and usefulness tools use the translator. The page itself is not yet served from the store, which is D29's stage 2.

*Evidence in proportion.*
- The usefulness bar rests on 30 redraws of one 48-child plan, not on independent children. The graph template's 142 candidates are 7 plan slots, C001 and six Dravet children, each redrawn up to 30 times.
- The 0 `unresolved` comes from D35's split. With the split off, the graph template has 47% unresolved. The rise's own tags add no uncertainty on the synthetic diary, whose period readings make every baseline determined. Its two-pass rule is exercised on the MIMIC demo (26 seeded and 5 named results `STEP_CONDITION_UNCERTAIN`) and on the small cases.
- No candidate result is `exact` on the templates. The records allow both answers for the first blocker or the first seizure, and the split says so with `possible`.
- k = 2 (the page's own) was used throughout. With monthly readings and W = 75 days, k = 3 would give the same baselines. That is argued, as in D33, not measured.

*Limitations.*
- The module carries copies of fourteen store functions. A test pins their originals, but a later change to the store's derived path must be made twice.
- The worlds check covers small grids, and relative steps over plain concepts with readings in at most two item classes. Larger cases rest on the reference and the arguments.
- A reading no step loaded is timed by its recorded range. That is safe only because ingest makes series events points (amendments).
- The MIMIC demo has no period readings, and every time there is exact.
- One machine, one session for the latency verdict.

*Follow-ups.*
- The comment on reading timing goes into `_patient_readings` and the reference at the next change to either.
- The counting question for the bar ("candidates": children, or plan slots) goes to the owner.
- Serving the page from the store (D29 stage 2) needs a server-side translator.
- A cohort generator whose children differ in plan, not only in draws, would give the usefulness tests independent children.

## D37 D29 stage 2: the trajectory page matches in the execution store by default

D29 set stage 2's trigger: the store can express everything the trajectory page offers, or refuses it by name, and the parity test is clean, every difference removed or declared, measured with the converter itself emitting series. D32 built Nth occurrences and counts, D35 the target split and D36 rises and falls; the translator (`trajectory/store_request.js`) writes all of them. This entry puts the store under the page, under rules written and committed before any measurement.

**Design.**
- *The server* (`demos/mockups/store_trajectory.py`, served by `server.py`).
  - At startup it builds the page's synthetic cohort (`demos/mockups/data/`) into a store, with the converter emitting series and the period label, as the parity test and D36 do (`store_parity.build`). The build takes about 1.3 s. It goes into a temporary directory removed at exit, or under a root outside the repository (`--trajectory-store-root`, `PTM_TRAJECTORY_STORE_ROOT`; a root inside the repository is refused), where a build of the same cohort is reused.
  - `GET /api/trajectory-store/status` names the snapshot, its profiles and its patients, or says why there is no store. `POST /api/trajectory-store/match` takes the translated store request and answers with `store_conditions_relative.match`, which hands every request but a rise's to `execution_store.match` unchanged.
  - *Per-patient answers are allowed here*, unlike the MIMIC endpoints. The data are synthetic, and the page already downloads every record of the cohort. A content check keeps it so: the snapshot's dataset id must be the generator's (`synthetic-paediatric-rare-v0`) and its patients exactly those of the cohort file the server serves. The MIMIC endpoints (`store_query.py`) and their aggregate-only rules are untouched.
  - *A named refusal, never a traceback.* A request the store refuses gets `400` with the store's or engine's code; a malformed one a named `400`; an internal failure `500 INTERNAL_ERROR`.
- *The request is the translator's, not a server-side port.* D32 and D36 said that stage 2 needs a server-side translator (the plan's `store_trajectory.py`). The page sends the request its translator writes instead: `{query, conditions, windows}`, times in whole minutes from each patient's birth, which the server converts to the store's clock exactly as the parity harness does (`store_parity.to_micros`, `conditions_micros`, `birth_micros`). The translator reads the page's own state: the index windows it places per patient (inventory item 10), the parent concept or drug class each relaxation names, and the relaxation costs. A server port would duplicate that JavaScript and could drift from it. One translator, pinned by the parity test, serves both the test and the page. The server validates the request's shape, and the store refuses what it cannot express by name.
- *The page.*
  - When a server with the store answers, the page matches in the store by default. A toggle offers the store, the engine on the page's own candidates (as before), and the page's own search. The results panel says which backend answered, with the snapshot.
  - When the translator refuses the query (`StoreRefusal`), or the store refuses it by name, the page matches that query on its own candidates instead (with the engine where it is reachable, as before) and labels it `in-browser fallback: <code>`, with the code's meaning.
  - Without a server (static hosting) the page matches in the browser, as before.
  - Store results are shown as the store gives them: `exact`, `possible`, `relaxed` with its certainty, cost and relaxations, `unresolved` with its reason and any relaxed match beside it, `none` (and its note, if a relaxed search stopped), `not_comparable` with its reason and steps, and `excluded` with the page's own reason for the missing period. The detail drawer lists the store's bound records (source, record, kind, recorded times) and its witness times. It does not show the page's own per-constraint verdicts for a store result.
  - In the drawer, the page's own answer for that patient is shown beside the store's when they differ, with the declared causes that can explain it.
- *Two new translator refusals, decided now.*
  - `FREE_TEXT_CODED_ONLY_UNSUPPORTED`: the page's "coded records only" on a query with a step that is neither derived (Nth with N > 1, a count) nor a measurement. The converter emits letter mentions as events, and the store reads them for every non-derived step (it ranks and counts coded records only). So "coded only" would silently become "also stated findings" on those steps. D32's parity trajectories accepted this inside the letter-mentions cause; the page must not. The parity test keeps D32's behaviour for its four Nth and count trajectories through a translator option (`codedOnly: 'derived'`), so its pinned tables do not move. The store's `exclude_kinds` (D32's follow-up) would lift the refusal; it is a store change and out of scope.
  - `VALUE_NOT_A_PLAIN_DECIMAL`: a threshold or factor the page holds as a number that does not print as a plain decimal (`1e+21`). The translator threw an unnamed error there.
- *Timed letter mentions are not refused.* The page times a mention from its wording, or merges it into the happening it describes; the store holds it from birth to its letter (D30). This is a difference in how the same evidence is represented, declared below. Refusing it would refuse the page's default setting ("also findings that letters state").

**Declared differences: a decision for each parity cause.**

| Cause | Where it comes from | Decision |
|---|---|---|
| Coverage | The store's comparability rule: `not_comparable` (`NO_SOURCE_COVERAGE`) when no source recording a step covers the period. | Store semantics; the store's answer is shown with its reason. Not a page shortcut, so nothing to port. |
| Letter mentions | The page merges a mention into the happening it describes, grouped by the generator's truth, or times it from the letter's wording. | Declared, page-only. The merge uses generator truth and is never ported. Timing from wording without truth could be a converter option later (follow-up); the converter is frozen here. |
| Record reconciliation | The page merges the records of one happening by the generator's truth and times it by the most precise record. | Declared, page-only, **never ported**: the store must not use generator truth. A real reconciliation would be an inferred grouping, a separate design. |
| Medication records | The page builds one course per happening (grouped by truth) from the order or the first dispensing, ending at the stop, when supply ran out, or at last contact. | Declared, page-only. A course inference from dispensings and supply that does not use truth could be a converter option later (follow-up). |
| Measurement records | The converter makes unitless measurements excluded records (D28); the page reads them by value. | Store semantics (a source that drops a value must say so); declared. Unit equivalence is D28's stage 3. |
| Order uncertain | The store's D30/D35 rule on an uncertain `first` or `last` target. | Store semantics; 0 in parity since D35. |
| Occurrence envelope | The store's Nth time ranges over every time the records allow; the page binds the happening its best record ranks Nth. | Store semantics (the page's rule is not sound under uncertain times); declared. |
| Count timing | The store binds the record at which a count is reached, exactly, with the two-pass rule; the page takes runs of N happenings by best dates. | Store semantics; declared. |
| Relative baseline | The store's baseline over recorded readings by period start; the page's over reconciled happenings. | Store semantics; 0 in parity. |
| Measurement observability | The page's own "the measurements do not cover the time around the earlier step". | Page-only; declared. The store's comparability rule stands in for it. |

One more page-side input is declared, though it causes no parity difference (both sides use the same windows): **the index windows are placed by the page**, over its reconciled view, which groups records by generator truth. The store takes windows as input (inventory item 10). So a store answer's windows rest on that page shortcut. The page says so beside the store's answer. Windows placed on the server from the store's own events would need a store-side window rule (a follow-up).

For the store-backed page the visible answer is always the store's.

**Pre-committed rules** (written and committed before any measurement of this increment).
1. **Answered or refused by name.** Every page template (Sequence, Tree, Graph, Event frequency, Blank), under each free-text option, and every step option, relation, scope and window rule the page offers, is either answered by the store or refused by a named code, from the translator or the store, which the page shows as `in-browser fallback: <code>`. Checked by the parity test's template table and by an option sweep: a fixed grid of page definitions over every option, each translated and sent to the store. A thrown error without a code, or a store failure without one, fails the rule.
2. **Clean parity.** Every store–page difference in the parity test has a cause from the closed declared list: coverage, letter mentions, record reconciliation, medication records, measurement records, order uncertain, occurrence envelope, count timing, relative baseline, measurement observability. There is no unexplained difference, and the replay holds for every trajectory. The converter itself emits series, as since D32. Every page template the store answers is among the parity trajectories, with its table pinned.
3. **No store or engine change.** `patterns/execution_store.py`, `patterns/trajectory_graph*.py`, `patterns/trajectory_times.py`, `patterns/store_conditions_relative.py` and the converter `patterns/synthetic_ingest.py` are byte-identical to main (e8ffe11), so no report is regenerated. If a store change seems needed, the increment stops and says so.
4. **The MIMIC endpoints' data rules are unchanged.** `store_query.py` is byte-identical, and `test_store_query.py` passes.
5. **The endpoint answers with the store's own results.** A test compares the endpoint's per-patient statuses, certainties, relaxations and reasons with `store_conditions_relative.match` called directly on the same request.
6. **Gates.** The mockup unit tests, `test_engine_parity.cjs`, `test_store_parity.cjs`, the provenance gate (0 stale), `check_test_counts` and the local CI script all pass.

Screens are rendered with Playwright at 1280 and 760 px for several templates and both backends, and looked at.

*Disclosed before these rules:* the existing parity test was run on main (634 of 816, as D36 records), and the store's per-patient result fields were printed for the parity trajectories, to design the answer's shape. No figure of this increment was measured.

**Amendments after the rules** (disclosed; the rules above are kept as committed).
- *No reuse of a build under a root.* The design said a build of the same cohort would be reused under `--trajectory-store-root`. It is not: the build takes about a second, and a reused snapshot could come from older converter code with the same cohort. The root only says where the fresh temporary build goes; it is removed when the server closes, as the default one is.
- *One report rebound.* Rule 3 said that no report is regenerated, on the premise that nothing a report binds would change. `verification/d36-rise-usefulness-report.json` binds `trajectory/app.js`, `trajectory/store_request.js` and `store_parity.py`, which D37 changes, so the provenance gate (rule 6) counted it stale. It was regenerated with its own tool (`rise_usefulness.cjs`, 93 s, the same fixed seeds): every figure is identical, and only the three artifact digests moved. `results/results.json` follows (only that report's digest moved).
- *A terminated server removes its build.* `server.py` turns SIGTERM, SIGINT and SIGHUP into a normal close, installed before the startup build, so the temporary build is removed on a clean close or any of those signals. Nothing can remove it on SIGKILL, which no process can catch; a build left that way is an empty-to-small directory under the system's temporary directory.
- *Wording on the page.* The store's `unresolved` is labelled *Unresolved · undecided* when the store answered (the page's own label says "search stopped", which is wrong for a store result undecided by, for example, excluded records). A missing space in the page's text for measurement thresholds ("is at most9") was fixed on the way; no result changes.

**Built** (2e3cf0a).
- `demos/mockups/store_trajectory.py`: the store's build at startup, the content check, the two endpoints, request validation, the answer per patient (the store's own fields, with bound records described from the store's rows and times in minutes from birth).
- `server.py`: the endpoints, `--trajectory-store-root`, `--no-trajectory-store`, the build closed and removed with the server.
- `trajectory/app.js`, `index.html`, `shared/style.css`: the store as the default backend, the backend banner, the labelled fallback, store statuses and reasons, the drawer's store records, witness times and the page's own answer beside the store's where they differ, with the declared causes.
- `trajectory/store_request.js`: `FREE_TEXT_CODED_ONLY_UNSUPPORTED` (with the `codedOnly: 'derived'` option for the parity test), `VALUE_NOT_A_PLAIN_DECIMAL`.
- `store_parity.py`: a `sweep` command. `test_store_parity.cjs`: the Blank template as a parity trajectory, the template table, the option sweep, the new refusals. `test_store_trajectory.py`: nine endpoint tests.

**Measurement and verdicts** (after the rules, acb0b00).

| Rule | Measured | Verdict |
|---|---|---|
| 1. Answered or refused by name | *Templates*, under each free-text option (coded only, stated, hedged): Sequence is answered with stated findings (the parity trajectory "the sequence template (a rise)") and refused otherwise (`FREE_TEXT_CODED_ONLY_UNSUPPORTED`, `FREE_TEXT_HEDGED_UNSUPPORTED`); Tree and Graph are refused for their gene step or hedged text; Event frequency is refused under each (`FREE_TEXT_CODED_ONLY_UNSUPPORTED`, `FREE_TEXT_COUNT_UNSUPPORTED`, `FREE_TEXT_HEDGED_UNSUPPORTED`); Blank is answered with stated findings (a new parity trajectory) and refused otherwise. *Option sweep:* 561 page definitions valid on the page, over every step option, relation, anchor, scope, seven index-period rules and the three free-text settings. 286 answered; 255 refused by the translator (coded only 157, counts over stated text 51, hedged text 31, gene steps 14, "while taking" from an Nth start 2); 20 refused by the store (`INVALID_FACTOR` 10: a rise "to at least 1 times"; `VALUE_RELAXATION_MUST_WIDEN` 10: the page's own relaxation of a negative "at most" narrows it). None without a code. Both tables are pinned in the test | met |
| 2. Clean parity | 643 of 864 results agree over eighteen trajectories (the seventeen of D36 unchanged, 634 of 816, and the Blank template, 9 of 48: the 9 without a period). Each of the 221 differences has a cause from the closed list: count timing 20, coverage 78, letter mentions 128, measurement records 4, medication records 72, occurrence envelope 14, record reconciliation 109. No `UNEXPLAINED`; the replay holds for every patient. Every template the store answers is a parity trajectory (the template table checks it). *In proportion:* three of the eighteen (the event-frequency template, the second seizure and the second-to-last seizure) are translated with `codedOnly: 'derived'`, D32's reading, which the page itself never sends (it refuses those queries with `FREE_TEXT_CODED_ONLY_UNSUPPORTED`); they keep D32's measurement of Nth and counts. Over the other fifteen, the requests the page can send, 522 of 720 agree | met |
| 3. No store or engine change | `git diff e8ffe11 -- patterns/` is empty. No report regenerated | met |
| 4. MIMIC endpoints unchanged | `store_query.py` is byte-identical to main, so the endpoints' data rules are unchanged. `test_store_query.py` was changed after the review, by the coordinator (08b5214), only to find the local demo store under `~/ptm-local`, and only where a snapshot exists; it passes | met |
| 5. The endpoint answers with the store's results | Five requests (a plain query with relaxations, a value condition with `widen_value`, an Nth, a span count with `widen_count`, a rise with `widen_factor`): every per-patient field equals `store_conditions_relative.match` called directly, bound event ids equal, witness times exact in minutes; counts and `context_id` equal | met |
| 6. Gates | `test_engine_parity.cjs` and `test_store_parity.cjs` pass; the mockup unit tests pass; provenance 0 stale (41 reports); `check_test_counts` consistent (1,340); `results/build.py --check` current; the local CI script passes with 0 failures and the v24 report unmodified (figures after the review's fixes, below). Before 08b5214, one mockup test (`test_the_real_demo_store_passes`) failed on a local scratch MIMIC store under `/tmp/ptm-store-mockup/` whose files the system's temporary-file cleanup had removed; 08b5214 fixed that by looking in `~/ptm-local` and only where a snapshot exists | met |

*The Blank template, in detail.* "The first seizure", from the start of the period, with the page's default period (from the first seizure, 24 months) and stated findings. The store answers 38 of the 39 patients with a period `possible`, and C004 `none`; the page answers `exact` for all 39. All 39 differ by letter mentions, 34 also by record reconciliation. A letter that states a seizure ranges from birth to the letter in the store, so which seizure came first, and whether it lies in the period, is rarely certain; the page dates the first seizure by its reconciled records and places the period on that date. This is the plainest view of what the store under the page changes: on the page's default setting the store says *possible* where the page said *exact*.

*Screens* (Playwright, Chromium, 1280 and 760 px, looked at): Sequence answered by the store (banner, chips with the records' ranges, a drawer with the bound letter mentions highlighted on the timeline and the witness times); Tree, Graph and Event frequency as labelled fallbacks (`GENE_CLASSIFICATION_UNSUPPORTED` with step B or E, `FREE_TEXT_COUNT_UNSUPPORTED`, `FREE_TEXT_CODED_ONLY_UNSUPPORTED`); Blank answered by the store, with the drawer's "this page's own search gives Exact match" note and its declared causes; weight at most 9 kg, showing `not_comparable` and `unresolved` with `ADMISSION_EXCLUDED_RECORDS` and the relaxed match beside it; the engine backend; and static hosting (no server), matched in the browser. No page errors. The 760 px layout stacks the panels, as before; the drawer scrolls.

**Decision: D37 ships.** Every pre-committed rule is met. The trajectory page matches in the store by default; the store, the engine and the converter are unchanged, and no report is regenerated. D29's stage 2 is done. Stage 3, retiring the browser path, waits for the hosting decision.

*Evidence in proportion.*
- Two of the five page templates are answered by the store at the page's default setting; Tree and Graph need gene steps; Event frequency's count needs coded records only, and its other step (the blocker) cannot be coded-only in the store. The fallback is the page's own search, labelled, as before.
- The store's answer differs from the page's own on 221 of 864 parity results, each by a declared cause. Most differences are the page's shortcuts (letter mentions, merged records, medication courses), which rest on the generator's truth.
- The index periods the store matches over are still placed by the page, over its reconciled view. They are an input to the store, so no parity difference comes from them, but the store-backed answer is not free of the page's shortcuts until windows are placed from the store's own events.
- The option sweep is a fixed grid, not every combination: one subject step with one relation, and the scope and period rules on three probes.

*Limitations.*
- "Coded records only" is refused for most queries. The store's `exclude_kinds` (D32's follow-up) would answer it; it is a store change.
- The endpoint trusts the translator for meaning and checks only the request's shape and the fields it converts; the store and the engine refuse everything else by name.
- The server builds the store from the cohort file the page downloads; regenerating the cohort needs a server restart.
- The drawer shows the store's binding and witness, not a per-constraint verdict.

*Follow-ups.*
- The store's `exclude_kinds`, so "coded only" is answered for every step.
- Index periods placed on the server from the store's own events (a store-side window rule).
- Converter options, without generator truth, for times from a letter's wording and for medication courses from dispensings and supply, if the page's semantics are to be kept in the store.
- Gene steps with classification (the first report that is not negative).
- Stage 3 with the hosting decision.

**Independent review** (after the verdict; nothing blocking on safety or correctness; scripts and screenshots in `/tmp/rv37`). The coordinator added 08b5214 (the MIMIC test finds the local demo store in `~/ptm-local`, only where a snapshot exists). Fixed on this branch:
- *Docs (must-fix):* rules 4 and 6 above no longer claim `test_store_query.py` byte-identical; the environmental failure is recorded as fixed by 08b5214.
- *Drawer:* the page's own answer shows its certainty when it differs (the C020, C025 and C033 notes for "a seizure, then weight at least 20 kg" read like the store's answer before), and keeps the page's own label for its `unresolved`. A store result's `unverified_match` (D30: a match beside an order-uncertain `unresolved`) is described like a binding and shown as an unverified match. A derived Nth step shows "no record holds this point" under Recorded and its time as the witness. The store's reason starts a capitalised sentence.
- *Banner:* it says that index periods are placed by the page over its view of the records grouped by the synthetic truth, and sent as input. When the server has no store (`STORE_NOT_CONFIGURED`, `STORE_DEPENDENCY_MISSING`, `STORE_BUILD_FAILED`, ...), the page says why instead of falling back to the engine silently. A server-side refusal (the request's shape, `TIME_OUT_OF_RANGE`) is no longer called a store refusal: the answer carries `by: server` or `by: store`. The "not fully searched" hint reads, in store mode, that unresolved patients are undecided for the reason given.
- *Server:* SIGHUP handled like SIGTERM and SIGINT, the handlers installed before the startup build; out-of-range times get `TIME_OUT_OF_RANGE`; a `count` that is not an object gets `COUNT_MUST_BE_AN_OBJECT` (a 500 before); the repository check judges the nearest existing ancestor by file identity (so another spelling on a case-insensitive disk is still inside) and refuses any git working tree, not only this checkout.
- *Drawer's declared causes:* no "letter mentions" under coded records only; "relative baseline" for rise and fall steps.
- *Docs:* the option sweep calls `store_conditions_relative.match` directly (the endpoint is tested on its own); the parity headline is put in proportion (rule 2 above); the briefing's reason for Event frequency is corrected.
- Tests: 11 endpoint tests (two added: the unverified match's description, and the repository check for another spelling and another working tree; the refusal table checks `by`).
- The page's script and `store_parity.py` changed again, so D36's usefulness report was rebound once more with its own tool: every figure identical, only the two artifact digests moved; `results/results.json` follows. A terminated server was checked to remove its build on SIGHUP, SIGINT and SIGTERM, each sent during the startup build.
- On the timeline, a store binding is marked on the page's own event holding that record, at the page's time for it; the drawer now says so.

## D38 Gene steps in the store: the first report that is not negative, timed by its recording, across reclassification

The trajectory page's Tree and Graph templates carry a gene step ("SCN1A variant reported"), and the translator refuses it by name (`GENE_CLASSIFICATION_UNSUPPORTED`), so both templates fall back to the page's own search (D37). This entry puts gene steps in the store, under rules written and committed before any measurement.

**The page's semantics** (`shared/core.js`, `makeItem`; `trajectory/app.js`, `candidates`). A genetic item is one test, grouped by the generator's truth: its order and its `genetic_result` records, the history sorted by `recorded_at`. A gene step on `gene:X` binds each test of gene X whose history holds a report that is not negative (`classification !== 'no relevant variant'`), at the *first* such report, as a point at its `recorded_at` (`dayOf` keeps hours and minutes; the engine grid is a minute). A report of uncertain significance counts. A reclassified variant (first reported VUS, then amended to pathogenic) is bound at the VUS report. A letter's mention of a result never binds: a genetic item made only of mentions has no history. The step offers no occurrence choice and no relaxation.

**Why the store could not do it before** (found while designing; reported to the coordinator, who chose option A below).
- The converter (`synthetic_ingest.py`) keeps a genetic result as an event of its gene concept, timed by the sample (`occurred`), with `recorded_at` and `available_at` as typed columns (D26). Its evidence keeps the coding but not the record's `attributes`, so the classification is not in the snapshot: a negative result ("no relevant variant", 3 records in the committed cohort) is indistinguishable from a pathogenic one (31).
- The converter keeps only a record's latest version (D20's retrospective selection). A reclassified result keeps only its amendment, recorded on the reclassification day; the first report and its classification are gone (C001's VUS report in the committed cohort).

**Design** (option A, approved by the coordinator 2026-11-16).
- *A converter wrapper in a module of its own,* `patterns/store_conditions_gene.py`, which no build loads (as D36 did for the period label). It calls the converter's own `convert_patient` unchanged and adds, to each `genetic_result` event's evidence, `gene_reports`: the reports of its record and of every record it supersedes (record id, `recorded_at` on the store's clock, classification, status), read from the source records the converter read. It adds no event, removes none, and changes no time, concept, coverage or excluded record. Its provenance is the converter's (with D36's period label when series are on), plus a label `gene_report_kinds: genetic_result` and the rule's text. `store_parity.build` (the parity test, the page's server and the usefulness tools) builds with it.
- *Identity.* The classifications and the superseded reports' times are in the `event_evidence` table, so they are snapshot content, inside the snapshot's identity; the label is in the provenance, so in its input digest. Nothing a gene step reads lies outside the snapshot.
- *A departure from D26, stated.* D26 put free-form provenance in its own table so that matching never reads it. Gene steps read `event_evidence`, for the events of the kinds the label lists, of the step's gene (through the closure), of the candidates being matched. Why it is acceptable here: the read is for gene-report events only (39 of 8,334 events in the committed cohort built with series), one row each; it is part of the module's match path, never of `execution_store.match`; and a 1.0 to 1.2 request reads no evidence, as before.
- *The proper long-term fix* (option B, declined now): the converter itself keeping the classification and the superseded reports, as an option like `series`. Any edit to `synthetic_ingest.py` moves the converter digest that the D34 build record holds, so it would cost a fresh 2.5 h scaling build and break build reuse for the two parallel tracks. It is to be folded in the next time `synthetic_ingest.py` changes for another reason, and this module's wrapper retired then.
- *The request.* Profile `execution-store-conditions-gene-1.0`: the 1.2 language (D33) plus, per step, `gene: {report: 'first_not_negative', timed_by: 'recorded_at'}` (the only values accepted now). A gene step must have occurrence `any`, carry nothing else, and be named by no relaxation (the page offers none); each is refused by name otherwise. The module answers it; every other request goes to `store_conditions_relative.match` and `candidates` unchanged, so its answer is byte-identical. `store_conditions_relative.py`, `execution_store.py` and the converter are not edited; the callers that may send a gene step (`store_parity.py`, the page's server) call the new module.
- *How it reaches the engine.* As D32's derived steps: the gene step's concept becomes the derived tag `derived-step:<step>/own`, and for each gene-report event with a finding the patient gets one derived point carrying the tag, id `derived-step:<step>/own@<event id>`, start `[t, t]`. The engine is unchanged.

**Semantics** (the rules the module and its context bind, `GENE_RULES`).
1. *What binds:* a coded event of a kind the snapshot's label `gene_report_kinds` lists (`genetic_result`), of the step's gene through the closure, never a letter mention, under every free-text setting.
2. *Negative results* are a closed vocabulary: `no relevant variant`, `benign`, `likely benign`. *Not negative* is a closed vocabulary too: `pathogenic`, `likely pathogenic`, `uncertain significance`, `homozygous deletion`. A report of uncertain significance is a finding, as on the page.
3. *Reclassification:* the reports of a record and of the records it supersedes, in order of `recorded_at` (then supersession order), are read in turn; negative ones are passed over; the first report not negative is the finding. So VUS then pathogenic binds at the VUS report; negative then pathogenic at the amendment; pathogenic then downgraded at the first report, as on the page. All negative: no finding.
4. *Time:* the finding is a point at its report's `recorded_at`, exactly. The sample's time (the event's start) is not read.
5. *A missing or unknown classification* before any finding leaves the record undetermined. For gene steps it is an excluded record (D18) of the step's gene, its start from the earliest to the latest `recorded_at` of its reports, reached by the query's scope. It is applied at match time in the module (the snapshot's own `excluded_records` are not changed); a plain concept step on the gene binds the event as before. None occurs in the committed cohort; fixtures plant them.
6. *Coverage* is the store's rule over the step's gene concept (the sources recording it), as for a plain concept step.
7. *The page and the store on time units:* checked in the page's code, a report's time on the page is minute-precise (`dayOf` keeps hours and minutes, the grid rounds to minutes), as on the store's clock. So no day-versus-minute difference is expected and none is declared; a gene step's candidates that differed between the two would be an unexplained difference, and the parity test fails on it.

**Pruning** (D25) on gene requests: a gene tag's posting summaries are the range of the patient's finding times (a point ends when it starts), read with the findings; the store's `_prune` is carried with that branch, the rest of its argument unchanged.

**Copies, as in D36.** The module subclasses `store_conditions_relative.Request` and carries copies of `store_conditions_relative`'s `_candidates`, `_derived_chunk`, `_match_derived_chunk`, `_derived_reach`, `_worker_unit`, `_run` and `match`, and of `execution_store._prune`, each with its gene branch. A test pins the originals' sources by digest, so a change to one fails with "update store_conditions_gene".

**The usefulness test** (`demos/mockups/gene_usefulness.cjs`, report `verification/d38-gene-usefulness-report.json`).
- *Templates (ruled):* the page's Tree and Graph templates with their gene step, through the page's own translator (genes asked for), at the page's default setting: window from the second seizure for 24 months, scope `from_start`, stated findings.
- *Seeds:* 1001 to 1030 (30 redraws of the generator's 48-child plan), no extension.
- *Candidates:* patients with a store candidate and an index window, as in D36.
- *Counting* (owner rule, 2026-11-16): by distinct child types, the generator's `PLAN` keys (C001 is the key `reference`); a type is unresolved when any of its candidates is. Also reported: all candidates, and candidates without C001; per seed; status mixes and reasons.
- *Variants (reported, not ruled):* each template with coded records only where the translator allows it, and with the window from the first seizure for 36 months.

**Disclosed before these rules.** While designing, the committed cohort (default seed) was generated and converted, and its genetic result records and gene mentions were counted (the figures above). No match was run and no result of this increment was measured.

**Pre-committed rules** (written and committed before any measurement of this increment).
- **Soundness.**
  1. *The worlds checker finds 0 violations* on at least 20,000 small cases from each of three new generators, seeds from 3,000,000: `plain` (a gene step with up to two concept steps under any, first or last), `derived` (a gene step beside an Nth or a count, D32) and `relative` (a gene step beside a rise or fall, D33, the Tree and Graph shape). Each case has gene-report events with chains of one to three reports drawn from both vocabularies, missing classifications, VUS, reclassification and other genes. The checker reads gene steps from the written rule (the true findings as points), independently of the module; every status but `unresolved` is checked, and the match beside `unresolved`.
  2. *0 mismatches against an independent reference* (`json.dumps`, keys unsorted), written again from the rules above and sharing no code with the module's gene reading (`patterns/gene_reference.py`, over the ingest document and `execution_store_reference`): on every small case; on at least 400 seeded gene requests over snapshots (fixtures made for it: negatives, VUS, reclassification each way, undetermined records, several tests and genes, gene mentions, shared variables; and the committed cohort built by the wrapper), each at 1 and 2 workers, at both detail levels, with pruning on and off.
  3. *Planted defects are caught*, each by the worlds checker or the reference: a negative result counted; reclassification ignored (timed at the amendment); the sample's time used instead of `recorded_at`; a VUS treated as negative; a letter mention bound; an undetermined record ignored.
  4. *Pruning on and off are byte-identical* on every snapshot gene request.
- **No change without the new fields.**
  - `execution_store.py`, `synthetic_ingest.py`, `store_conditions_relative.py`, `trajectory_graph*.py`, `trajectory_times.py` and every file the D34 build record lists are byte-identical to main (e682817).
  - The 300 recorded match digests are unchanged.
  - The scaling report's builds are still reusable: the reuse probe accepts the kept D34 builds (`~/ptm-local/scaling-d34`).
  - *The wrapper changes evidence only:* on the committed cohort and seeds 1001 to 1030, the wrapper's ingest equals the converter's except for `gene_reports` in genetic results' evidence and the provenance's label and rule (a test).
  - *A differential against main's builds:* every parity, option-sweep and D36 usefulness request without a gene step, on the snapshot the wrapper builds and on one main's `store_parity.build` builds, gives identical statuses, bindings and every result field, except snapshot and context identifiers; on the committed cohort and seeds 1001 to 1030.
  - Every request without the gene profile, through the module, gets `store_conditions_relative`'s answer byte for byte (a test, at 1 and 2 workers).
  - D36's usefulness report, which binds `store_parity.py`, is regenerated with its own tool: every figure identical except snapshot identifiers and artifact digests.
- **Usefulness.** For each of the Tree and Graph templates with their gene step, at the page's default setting, pooled over seeds 1001 to 1030: answered by the store (not refused), at least one candidate, and **at most 30% of the distinct child types with a candidate unresolved**. All candidates and the count without C001 are reported beside it.
- **Parity.** Tree and Graph with their gene step become parity trajectories; every store–page difference over all trajectories has a cause from D37's closed list (a gene step's candidates that differ have none, so fail); the replay holds for every patient; the template table shows Tree and Graph answered with stated findings.
- **Latency.** Paired against main (e682817, or the main this branch is rebased on), `tools/benchmark_matcher.py --paired-confirm`, on the scaling report's 300,000 snapshot, 30 queries, 4 repeats: the 14-worker p95 ratio (this tree over main) at most 1.05 in every pair, and this tree's own 14-worker p95 at most 10 s; 8 workers reported. The record is `verification/d38-paired-verdict.json`.
- **Regeneration, only what is stale,** one heavy job at a time under the benchmark lock.
- **Ship rule.** Gene steps ship, and the translator writes them by default (`STORE_GENES`), only if every rule above holds; otherwise the named refusal stays the default and the numbers are recorded here. A failure is fixed, never tuned away.

**Built** (371fcde): `patterns/store_conditions_gene.py`; the reference `patterns/gene_reference.py`; the small cases `patterns/gene_cases.py`; `patterns/test_gene_steps.py`; the translator's `genes` option (`STORE_GENES` false until the verdict); the parity test's tree and graph templates with their gene step and its no-change differential (`demos/mockups/gene_no_change.py`); `demos/mockups/gene_usefulness.cjs`; the page's server and drawer for gene points.

**Amendments after the rules** (an independent review of 371fcde; disclosed; the rules above are kept as committed).
- *The review's own check.* On every case the generator produces, the store's gene step equals the page's: 1,488 patients over the committed cohort and seeds 1001 to 1030, 0 mismatches. Pruning was found sound.
- *Must-fix M1: letters counted as recording a gene step.* Rule 6 took the coverage rule over the gene concept, whose recorders (`source_concepts`) include `letters`, since mentions carry the gene. So a `none` stood where the answer is `not_comparable`. On the committed cohort, C002, C003 and C016 hold no genetic result and no genetics coverage, only letters, and got `none` over a lifetime window; over the 31 cohorts, 283 children have letters coverage and no genetic result. *Fixed:* a gene step is recorded only by the sources holding an event of the label's kinds of its gene (`_gene_sources`; a copy of `execution_store._comparability` takes them, pinned like the other copies), and the reference mirrors it independently. A test is built from the three children. Rule 6 now reads: coverage is the store's rule with that set of recorders.
- *The coverage time base, accepted (coordinator's decision).* The genetics source's coverage spans its records' sample times (`occurred`), while a gene point lies at `recorded_at`, later. A window that holds the report but not the sample is then `not_comparable` for a patient without a finding. That is stricter, and sound. It is a known source of extra `not_comparable`: the review counted 24 such patients at a 24-month window from day 400 of life.
- *Composition with D39 (coordinator's decision).* `patterns/store_conditions.py` is the one entry point: it routes a request by its profile (gene steps to `store_conditions_gene`, everything else to `store_conditions_relative`) and leaves a marked place for D39's 1.3 route. `store_parity.py`, the page's server and the tools call only it; the server's status lists every profile it accepts; the no-change differential runs through it. The translator refuses a gene step under "coded records only" by name (`GENE_WITH_CODED_ONLY_UNSUPPORTED`) until a joint profile exists, and refuses an occurrence on a gene step itself (`OCCURRENCE_ON_GENE_STEP`). The exemption 371fcde gave gene steps from `FREE_TEXT_CODED_ONLY_UNSUPPORTED` is withdrawn. *Follow-up:* gene steps over profile 1.3, with `exclude_kinds` allowed.
- *A declared difference from the page: the vocabulary off the cohort.* The page binds every report but `no relevant variant`, so it would bind `benign`, `likely benign`, a missing and an unknown classification. The store passes over the first two and leaves the last two undetermined. The generator writes only four classifications (`pathogenic`, `uncertain significance`, `homozygous deletion`, `no relevant variant`), so no parity result can differ by it. The page is not changed.
- *Reclassification on the cohorts.* Only VUS then pathogenic occurs in the synthetic cohorts (C001's scripted case, and its redraws). Negative then pathogenic, pathogenic then downgraded, and undetermined chains are exercised on the fixtures and small cases only.
- *Undetermined records in the worlds check* (should-fix). The small cases now apply them as the gene step's excluded records on both sides, and the checker reads them independently. It reads every unknown classification as negative and as not negative and takes each reading's findings as points. An `exact`, `none` or `relaxed` result must hold in every reading. A result the excluded-records rule weakened claims only what the reference checks.
- *Performance* (should-fix). The all-patient findings are read once per match and passed to candidates, pruning slices, chunks (and worker units) and D18 reach; `_gene_findings` joins on the bucket. *Latency for gene requests at scale is not measured:* the paired benchmark's snapshot holds no gene label, so the latency rule shows only that requests without gene steps are unaffected.
- *Nits.* The minute claim (rule 7) rests on the generator's timestamps, written to the minute with no offset. Gene matching uses the closure, the page exact identity; they are the same on this generator, whose genes have no parents. A missing superseded record is named (`GENE_REPORT_RECORD_MISSING`), as is a gene-report event without reports in the reference. The no-change differential now compares the labels without `gene_report_kinds`, and the context without the snapshot's identifier and file-list digest.

**Measurement** (after the rules and the review fixes, at 20c6283, on main e682817; one heavy job at a time under the benchmark lock, 2026-11-16 from 19:35 to 20:35).

| Rule | Measured | Verdict |
|---|---|---|
| Soundness 1: worlds checker, at least 20,000 per generator | 60,000 small cases, seeds from 3,000,000: **0 violations**. Statuses (exact, possible, relaxed, none, not comparable, unresolved): plain 3,055, 791, 69, 13,423, 2,594, 68; derived 561, 318, 155, 15,141, 2,914, 911; relative 157, 71, 24, 16,027, 3,035, 686. Undetermined records are read in every reading of their unknown classifications (review, should-fix 3). The suite repeats this at 500 per generator | met |
| Soundness 2: independent reference | **0 mismatches** on the 60,000 small cases. Snapshots: 400 gene requests (fixture 150, shared variables 100, the committed cohort built by the wrapper 150), 17,200 patient results, **0 mismatches**; at 2 workers (chunk 9), at `decided` and with pruning off, 0 differences | met |
| Soundness 3: planted defects | Worlds-check violations over 6,000 small cases per defect (2,000 per generator: plain, derived, relative): a negative result counted 267 (193, 59, 15); reclassification ignored, timed at the amendment, 255 (173, 65, 17); the sample's time instead of `recorded_at` 363 (250, 85, 28); a VUS treated as negative 160 (98, 46, 16); a letter mention bound 306 (240, 53, 13); an undetermined record ignored 186 (112, 57, 17). Each is also caught by the reference on snapshots (40 fixture requests: 125, 144, 155, 100, 134 and 57 mismatching results, before the review's fixes) | each caught |
| Soundness 4: pruning | On and off byte-identical on the 400 snapshot requests (2,210 candidates pruned) | met |
| No change: build path | `execution_store.py`, `synthetic_ingest.py`, `store_conditions_relative.py`, `trajectory_graph*.py`, `trajectory_times.py` and the 10 files the D34 build record lists are byte-identical to e682817 | met |
| No change: 300 digests | Unchanged (`patterns.test_execution_store`, in the gates) | met |
| No change: scaling reuse | The reuse probe accepts the kept D34 builds at every size (1,000 to 1,000,000; the probe build recorded exactly what they recorded). Nothing the scaling or matcher reports bind changed, so neither is regenerated | met |
| No change: the wrapper changes evidence only | A test on the committed cohort: the wrapper's ingest equals the converter's but for `gene_reports` in the 39 genetic results' evidence and the provenance's label and rule | met |
| No change: differential against main's builds | Through the dispatcher on the wrapper's build against `store_conditions_relative` on main's build: the committed cohort, 324 parity and option-sweep requests without a gene step (14,592 patient results, 20 refused alike); seeds 1001 to 1030, D36's 9 usefulness requests each (270 requests, 12,960 results). **0 differences** in any field but the snapshot's and the context's identifiers, the context's file-list digest and the labels' `gene_report_kinds` | met |
| No change: through the module | Tests: every request without the gene profile through `store_conditions_gene` and through `store_conditions` equals `store_conditions_relative`'s answer byte for byte, at 1 and 2 workers | met |
| No change: D36's usefulness report | Regenerated with its own tool (twice: after the review's fixes, and after the translator's flip): every figure identical; only the 30 snapshot identifiers and the artifact digests of `store_parity.py`, `store_request.js` and `app.js` moved | met |
| **Usefulness** | Seeds 1001 to 1030. **Tree with its gene step: 0 of 7 distinct child types unresolved; Graph: 0 of 7.** All candidates: 0 of 122 and 0 of 122; without C001: 0 of 92 and 0 of 92. Both answered by the store at the page's default setting | met |
| **Parity** | The tree and graph templates with their gene step are parity trajectories: 22 of 48 agree in each; each of the 26 differences is coverage (24) or the existing letter mentions, medication records and record reconciliation (C015, C036). None comes from a gene step: the store's gene points equal the page's for every patient. Overall 687 of 960 over twenty trajectories (the eighteen of D37 unchanged, 643 of 864), every difference from the closed list; the replay holds. The template table shows Tree and Graph answered with stated findings | met |
| **Latency** | Paired against main (e682817) on the scaling report's 300,000 snapshot (`verification/d38-paired-verdict.json`): 14-worker p95 ratios **0.981, 0.834, 0.922, 1.021** (reference first, candidate first, reference first, candidate first); this tree's p95 **9.38 s**, max 12.31 s, 0.79 GB; main's code 10.13 s in the same session (a slower session than D36's). Byte-identical results on all 30 queries. 8 workers (reported): ratios 1.024, 1.029, 1.072, 1.036; p95 11.73 s (main 11.03 s), max 16.43 s. The run took 2,454 s | met |

*Usefulness, in detail* (`verification/d38-gene-usefulness-report.json`).
- Results among the candidates, in each template: 84 `possible`, 26 `relaxed`, 12 `none`; none `unresolved`, none `exact` (the first seizure's and the first blocker's records leave their targets uncertain, and D35's split decides them `possible`).
- The 7 child types are C001 and the six Dravet plan slots (dravet-typo 18, dravet-ncb-2 17, dravet-preterm 16, dravet-ncb-3 15, dravet-lost 13, dravet-ncb-1 13; C001 30). They are the SCN1A-positive children: the gene step narrows the candidates to them. Tree and Graph give the same status mix in every seed: on these children both shapes hold or fail together.
- Variants (reported): the window from the first seizure for 36 months, 0 of 110 unresolved in each; coded only is refused for both (`GENE_WITH_CODED_ONLY_UNSUPPORTED`).

*Latency, in proportion.* The confirmation's queries carry no gene step and the snapshot no gene label, so the candidate takes main's code path byte for byte through `execution_store.match`; the ratios measure session noise. The 8-worker target (10 s) is missed by both codes, as since D30. Latency of gene requests at scale is not measured.

**Decision: D38 ships.** Every pre-committed rule is met, with the review's must-fix fixed before the measurement. The translator writes gene steps by default (`STORE_GENES = true`), so the trajectory page's Tree and Graph templates run on the store at the page's default setting; under "coded records only" they are refused by name until a joint profile exists. The store's profile stays `execution-store-3.1`, the match profile `execution-store-match-2.0`; the request gains profile `execution-store-conditions-gene-1.0`, answered by `patterns/store_conditions_gene.py` through the entry point `patterns/store_conditions.py`. The store's file, the converter, the relative module and every file a build loads are unchanged.

*Evidence in proportion.*
- The usefulness bar rests on 7 child types, each redrawn up to 30 times; only VUS then pathogenic occurs among them. The gene step's own semantics on other chains rest on the fixtures, small cases and the reference.
- The store's gene points equal the page's on every patient the generator produces (the review: 1,488 patients over 31 cohorts), because the generator writes four classifications, all in the store's vocabularies, and minute timestamps.
- The coverage rule takes sample-time coverage for report-time points, stricter than needed (24 extra `not_comparable` at a 24-month window from day 400, the review's count).

*Limitations.*
- The module carries copies of nine functions of `store_conditions_relative` and `execution_store` (pinned, with four more it builds on); a change to one must be made twice.
- Gene steps read `event_evidence`, a departure from D26, for gene-report events only.
- The classification lives in the wrapper, not the converter, until `synthetic_ingest.py` next changes (option B).
- One machine, one session for the latency verdict; no gene request timed at scale.

*Follow-ups.*
- Option B: the converter keeps classifications and superseded reports (an option like `series`); retire the wrapper then.
- A joint profile: gene steps over D39's profile 1.3, with `exclude_kinds` allowed, so gene steps answer under "coded records only".
- Report-time coverage for gene steps, if a source declares it.
- Gene requests in the latency benchmark, on a snapshot built with the wrapper.

## D39 A store-side "coded only": letter mentions left out of any step, by request

The trajectory page offers three free-text settings: coded records only, also findings that letters state (the default), and also hedged findings. The store's converter emits each stated letter mention as an event of kind `mention`, from birth to its letter (D30). Since D32 the store ranks and counts coded records only (`MENTION_KIND`, `_mentions`), but every other step reads mentions. So D37 refused "coded only" on the page wherever a step other than an Nth occurrence or a count would still read them (`FREE_TEXT_CODED_ONLY_UNSUPPORTED`), and the Event frequency template was refused under every setting: its count needs coded records, and its blocker step could not be coded-only in the store. D32 and D37 named the store's `exclude_kinds` as the follow-up. This entry builds it, under rules written and committed before any measurement.

**The request.** A new conditions profile, `execution-store-conditions-1.3`: everything of 1.2 (D36), plus a request-level `exclude_kinds`, which 1.3 requires.
- `exclude_kinds` is a non-empty list of distinct strings; otherwise `INVALID_EXCLUDE_KINDS`. The only kind accepted is `"mention"`; any other is refused by name (`EXCLUDE_KIND_UNSUPPORTED`). The page needs no other, and each further kind would need its own argument about series rows, period readings and excluded records.
- `steps` may be empty in 1.3, so a query with no conditioned step can still be asked over coded records only.
- `execution_store.Request` and `store_conditions_relative.Request` refuse profile 1.3 (`INVALID_CONDITIONS`), as they do today; neither file changes.

**The semantics** (rule `exclude_kinds_v1`). A 1.3 request is answered as the same request as profile 1.2, without `exclude_kinds`, would be answered on the snapshot with every event of an excluded kind removed: its concepts, evidence, series rows and time variables go with it. Everything else stays: the patients, the coverage periods, the excluded records, the concept closure and the labels. Per step kind:
- *`any` steps* bind kept events only.
- *`first` and `last`* choose their target among kept events. D30's order rule and D35's split run over kept events. A mention ranges from birth to its letter, so leaving mentions out usually removes the order uncertainty they cause.
- *Nth occurrences and counts* are D32's, unchanged: they already read coded records only. Their envelopes and strengths are over kept events.
- *Value conditions*: a tag is carried by series rows of kept events only.
- *Rises and falls*: a reading is a series row of a kept event, or an excluded record of the item class. A left-out event's reading is neither the rising reading nor in a baseline.
- *Shared variables*: a left-out event's variables leave with it. A kept event that shared a variable with one keeps its own recorded range, which still holds its true time; only the constraint the left-out event added is gone.
- *Coverage*: a step is recorded by the sources that hold a kept event of one of its concepts (`source_concepts`, restricted to kept kinds), or, for a value tag, a series row of a kept event in its unit. A source that records a concept only through left-out events does not cover the step. In the synthetic cohort the letter sources (`letters`, `discharge_fr`, `referral_nl`) hold only mentions, so a `none` that rested on a letter source's coverage alone becomes `not_comparable`.
- *D18*: excluded records are unchanged; they reach, displace and weaken results as before. **A mention left out by request is not an excluded record.** An excluded record is a fact about the source: it recorded something the store does not hold. A left-out mention is a question the user asked: "what do the coded records say?" So a left-out mention never makes a result `not_comparable`, `unresolved` or incomplete; it is simply not there.
- *Candidates*: the candidate filter first reads the postings and series over every event, kept or not, which is a superset. The survivors are then refined to the patients holding, for every step, a kept event of one of the step's concepts. For a value tag, that means a kept event carrying it. For an Nth or count tag, at least N′ kept events of its concept, N′ the smallest over its widenings. For a relative tag, a kept event of its unit tag. One query runs over the survivors only, so `candidates` and the `FILTERED` results equal those on the snapshot without left-out events. The output's `plan` still counts each step's postings over every event; this is stated in the output's rule.
- *Pruning* reads the posting summaries, which cover every event. Kept events lie inside them, so `_impossible`'s argument holds unchanged: a pruned candidate's `none` is the engine's own `none` over kept events. Pruning on and off must be byte-identical.
- *Output*: `excluded_kinds: {kinds, rule}`. The context binds this module's digest, the rule's digest and, through the conditions' digest, the kinds.
- *Refusals*: `INVALID_EXCLUDE_KINDS` and `EXCLUDE_KIND_UNSUPPORTED`, and every refusal of 1.2.

**Design: a module of its own** (owner policy on build reuse, D34; the parallel-work rules of 2026-11-16).
- `patterns/store_conditions_coded.py` holds everything D39 adds. No build loads it, and nothing loaded by the store or the engine imports it. `execution_store.py`, `store_conditions_relative.py`, `synthetic_ingest.py`, `trajectory_graph*.py`, `trajectory_times.py` and every file the D34 build record lists stay byte-identical to main (e682817).
- Its `match` and `candidates` hand every request other than a 1.3 one to `store_conditions_relative.match` and `candidates` unchanged, and those hand every request other than a 1.2 one to `execution_store`.
- A 1.3 request is compiled by `store_conditions_relative.Request` as profile 1.2, so value conditions, Nth occurrences, counts and rises keep their code.
- The store's loaders are called as they are. Left-out events are dropped after loading, per patient, by event id, from one query of the events of excluded kinds over the chunk's patients. The same holds for the relative readings' rows.
- Three pieces are new code. Coverage is a copy of `_comparability` with the sources restricted to kept kinds. The candidate refinement is new. `match` is a copy of `store_conditions_relative.match` with these hooks, its own worker unit and context.
- A test pins the sources of the copied functions by digest, as D36 does.
- *Callers*: the server's endpoint (`store_trajectory.py`), `store_parity.py` (match, sweep and the candidate lists, which leave out the excluded kinds) and the usefulness tool call this module. The change to each is additive.
- *The translator*: `storeRequest(..., options)` writes "coded only" as profile 1.3 with `exclude_kinds: ["mention"]` when asked (`options.codedOnly: 'store'`). By default it keeps refusing (`FREE_TEXT_CODED_ONLY_UNSUPPORTED`) until the rules below hold; then the default flips, and the refusal remains for a caller that asks for it. D37's `codedOnly: 'derived'` stays as an option.
- *The MIMIC demo*: its events carry no letter mentions, so a 1.3 request equals the 1.2 answer there. This is argued, not measured; the MIMIC report binds none of D39's files, so it is not regenerated.

**The independent reference.** It is written in the test support module (`patterns/coded_only_cases.py`). It shares no code with `store_conditions_coded`.
- It removes every event of an excluded kind, and its series rows, from the ingest document with dictionaries and loops.
- It then calls the existing reference (`prepared_reference.PreparedReference`, or `execution_store_reference.exhaustive_match`) with the request as profile 1.2, or without conditions when `steps` is empty.
- The reference knows nothing about exclusion by request, so it tests the store's SQL filters, coverage, refinement and loaders against the definition itself.
- A second check, store against store: a store built from the filtered ingest, answering the 1.2 request, must equal the 1.3 answer on the full snapshot, result for result (the `plan` aside).

**Pre-committed rules** (written and committed before any measurement of this increment).
1. **Soundness.**
   1. *The independent reference agrees:* 0 mismatches (`json.dumps` without sorting keys, a `FILTERED` reason read as the reference's `none`).
      - At least 400 seeded 1.3 requests over snapshots: D32's derived fixtures (mentions, exposures, shared variables, excluded records, partial coverage, values) and the synthetic cohort built with series and the period label. The requests are plain steps with `any`, `first` and `last`, value conditions, Nth occurrences, counts and rises, with relaxations.
      - Each request is compared at 1 and 2 workers, at both detail levels and with pruning on and off.
      - The store-against-store check holds on the same requests.
   2. *A worlds check on at least 20,000 small cases per new generator*, with seeds apart from the suite's. A case is a tiny patient on an integer grid, in a small snapshot built from many such patients, with a 1.3 request and a window. The store's answer, through `store_conditions_coded.match`, is checked by `trajectory_graph_worlds` (`check`, or `check_derived` for derived steps) against every choice of times of the patient's **kept** events. Every status but `unresolved` is checked, and the match beside `unresolved`. Every case is also compared with the reference. The new generators:
      - `coded-plain`: coded events and mentions of x, y or both (z wider), point events and exposures, some proper. Shared variables, among them a mention sharing a variable with a coded event. Excluded records, and partial coverage, among it a letter source that alone covers a window. Requests of one to three steps with `any`, `first` and `last`, relations, relaxations and every scope.
      - `coded-derived`: the same patients, each request with an Nth occurrence or a count beside such steps.
   3. *Every planted defect is caught* (a switch, `_D39_DEFECT`), by the worlds check or the reference:
      - a mention still read: the plain path does not drop left-out events;
      - a coded record dropped: left-out events matched by event id across patients (ids are unique within a patient only);
      - D18 confused with exclusion by request: left-out events treated as excluded records;
      - coverage not restricted: a source that records a concept only through left-out events still covers it;
      - the candidate refinement on the step's own concept only, its widenings ignored.
   4. *Pruning on and off are byte-identical* on every differential above.
2. **No change without the new fields: byte-identical to main.**
   - `execution_store.py`, `store_conditions_relative.py`, `synthetic_ingest.py`, `trajectory_graph*.py`, `trajectory_times.py` and every file the D34 build record lists are byte-identical to main (e682817).
   - The 300 recorded match digests are unchanged.
   - Every request without profile 1.3, sent through `store_conditions_coded`, gets `store_conditions_relative`'s answer byte for byte, at 1 and 2 workers (a test).
   - The scaling report, regenerated with `--reuse-builds ~/ptm-local/scaling-d34`, has the same rows, candidate counts and status mixes at every size. If the reuse is refused, the run stops and the refusal is reported.
   - The paired run gives byte-identical results between this tree and main on its 30 queries.
3. **Usefulness** (`demos/mockups/coded_usefulness.cjs`, report `verification/d39-coded-usefulness-report.json`).
   - *Cohorts:* D36's protocol. Seeds **1001 to 1030**, then 1031, 1032, … in order while some template has fewer than 30 candidates pooled. The stop depends on candidate counts only.
   - *Templates (ruled):* every page template under "coded only", through the page's own translator with `codedOnly: 'store'`, each with the page's own window and scope: Sequence; Tree and Graph without their gene step (the store refuses gene steps by name, `GENE_CLASSIFICATION_UNSUPPORTED`); Event frequency; Blank.
   - *Candidates:* patients with a store candidate (`store_conditions_coded.candidates`) and an index window.
   - *The bar, per template:* counted by distinct child types (the owner's rule of 2026-11-16), at most 30% of them `unresolved`. A child type is a `PLAN` key of the generator, with C001 as the key `reference`. A type counts as a candidate when any of its children is one in some seed, and as unresolved when any of its candidates is unresolved (D36's reading, the strict one).
   - *Also reported:* the share over all candidates and without C001, status mixes and reasons, per seed and pooled. The same templates under stated findings are reported beside them, for the change.
   - *Coverage:* each of the five templates under "coded only" is answered by the store, with Tree and Graph refused by name for their gene step only.
4. **Parity.**
   - Every store–page difference in `test_store_parity.cjs` has a cause from D37's closed declared list. There is no `UNEXPLAINED` difference, and the replay holds for every trajectory.
   - The three trajectories that D37 kept on `codedOnly: 'derived'` move to the page's real coded-only request (1.3). New parity trajectories cover every template the store now answers under "coded only" (at least Sequence and Blank), so the template table's answered entries are all pinned.
   - The option sweep is re-pinned: each definition answered or refused by a named code.
5. **Latency.** Paired against main (e682817 or the main this branch is rebased on), `tools/benchmark_matcher.py --paired-confirm` (`paired-interleaved-1.0`), at 300,000 patients, 30 queries, 4 repeats, 14 workers, into `verification/d39-paired-verdict.json`. It passes only if:
   - the 14-worker p95 ratio (this tree over main) is at most **1.05 in every pair**;
   - this tree's own 14-worker p95 is at most **10 s**.

   The maximum and memory are reported. The benchmark's queries carry no 1.3 request, so the two trees run the same store code; the run checks that nothing else moved.
6. **Ship rule.** "Coded only" is translated by default, and the page stops refusing it, only if every rule above holds. Otherwise the translator keeps refusing it by name and the numbers are recorded here. A failure is fixed, never tuned away. If the scope proves too large, the increment stops after a coherent part that meets every rule, and says so.

*Disclosed before these rules:* none of this increment's figures had been measured. The committed cohort was generated locally, and its records were counted by source and kind to find which sources hold only mentions (the three letter sources).

## D40 Fewer `unresolved` results from exhausted search budgets: a stopped search settled by propagation, under rules fixed before measuring

D35's calibration found that most `unresolved` results in the scaling report are `SEARCH_BUDGET_EXHAUSTED`, not order uncertainty: 38 at 1,000 patients and 363 of 375 at 10,000 (after D35's split, 363 of 363). D31's reviews flagged two more budget costs: seed 7 leaves 19,890 results `none` with `RELAXATION_SEARCH_STOPPED` at 300,000, and at 10,000 the exact phase with order facts stopped on its budget in 1,712 of its 2,520 runs (seed 29's cost). This increment decides more of those cases, soundly, under rules written and committed before any measurement of the change.

*Outcome (details at the end of this entry):* every large-store `SEARCH_BUDGET_EXHAUSTED` and `RELAXATION_SEARCH_STOPPED` result comes from a search budget of 1 that the benchmark's query generator draws, and every one is decidable with a larger budget. Settling was built and was sound on every check, and decided all of them at 1,000 and 10,000, but missed the MIMIC rule as written, so it was reverted and does not ship. The design, the argument and the rules below are kept as written.

**Calibration (disclosed; measured before these rules, on main's engine at e682817).** In process, on the scaling report's kept D34 snapshots at 1,000 and 10,000 patients, with its 50 queries and windows, every patient whose engine run had a bounded search stop was traced: which search stopped, its domain sizes and budget, and the same patient matched again with a search budget of 1,000,000. Scripts outside the repository; aggregates only.
- *Every `SEARCH_BUDGET_EXHAUSTED` result at both sizes comes from a query whose search budget is 1.* The scaling report's queries come from the store differential's generator (`random_query`), which draws the search budget from 1, 40 and 300, so about a third have a budget of 1. A bounded search counts each event it tries for a step against the budget, so with a budget of 1 a search over two or more steps stops at its second step, whatever its domains. Such a set can only be decided by propagation, which has never counted against the budget (arc consistency, the forest methods, the point forest's joint check).
- *1,000:* 38 `SEARCH_BUDGET_EXHAUSTED`, all budget 1: seed 19 (17 patients; two steps, any seizure and any antiseizure drug, the drugs exposures), seed 32 (1; two steps) and seed 35 (20; three steps in a cycle: the first fenfluramine, any clobazam, any fenfluramine). With a budget of 1,000,000 all 38 are decided: 18 `possible`, 20 `none`.
- *10,000:* 363, all budget 1: seed 19 171, seed 32 3, seed 35 188, seed 44 1 (four steps). With a budget of 1,000,000 all are decided: 174 `possible`, 189 `none`.
- *Stopped relaxation searches* (engine results; budget 1 as well): `none` with `RELAXATION_SEARCH_STOPPED`, 80 at 1,000 (seed 7: 70, two steps; seed 40: 10, four steps) and 763 at 10,000 (seed 7: 663; seed 40: 100). The store shows 79 and 706 of them; the coverage rule reads the rest as `not_comparable`. With a budget of 1,000,000, seed 7's become complete `none` and seed 40's `relaxed`.
- *Larger budgets:* at 10,000, seed 29 (budget 40) has 4 stopped exact phases with order facts and 1 stopped exact phase of the target split, giving 1 `relaxed` result whose exact search stopped (complete with the large budget); seed 21 (budget 300) has 12 stopped searches in results that were decided anyway. No other query's search stopped.
- *Which searches stop.* At 1,000: the possible phase of the envelope search 90 times (seeds 7 and 35), each followed by the exact phase with order facts stopping too (90), and the possible phase of a plain set 31 times. At 10,000: 852, 852 and 314 at budget 1, and the 12 and 5 above. So at budget 1 the exact phase with facts runs only because the possible phase before it stopped.
- *The MIMIC-IV demo:* on main's engine (e682817, extracted), store side only, on the converted demo built locally, with the queries of the demo report's four differentials (500, 200 value, 150 derived and 62 relative requests; counts only): `SEARCH_BUDGET_EXHAUSTED` 4 times in the differential (seeds 11 and 264), 10 in the value differential (seed 100060) and 8 in the derived differential (seed 200006, an Nth request), none in the relative one; each has a search budget of 1. No other store result there carries a stopped search's note. So the demo's results can move, unlike in D35 (whose order rule exact times never meet). The store's references there call the engine, so a moved result moves on both sides of a differential alike.
- *Consequence for the design.* Searching better within the budget (variable and value order, symmetry, nogoods) cannot help a budget of 1; only decisions that spend no search steps can. And they must change nothing decided: a different order finds a different first binding, which changes the binding of a decided result.

**Design: settling a stopped search** (`_settle`, behind a switch `_SETTLE`). Where the possible phase's bounded search in `decide` stops (no binding found, the budget spent), the phase is not reported stopped at once. `_settle` runs on the domains the search had (the steps' candidates after the unary filter and arc consistency on plausibility, with the phase's order facts) and either decides the set or gives up, in which case the stopped search stands exactly as before:
1. *Pair consistency.* An event of a step is kept only if, for every other step, some event in that step's domain makes the pair jointly feasible: the two events' own network, both steps' unary constraints, every constraint between them and every order fact the pair meets (`_small_feasible`, which is `feasible` on that two-step binding, by Floyd–Warshall over its variables with the anchors as constants). Arc consistency on plausibility tests each constraint alone; this tests them together. Each event is first tested alone with its step's unary constraints.
2. *Bounds.* A network over the steps' hulls (`_bounds_network`): each step's start and end range over the union of its events' ranges (`Times.dom`), its end minus its start within the least and greatest its events allow, with every constraint of the set and every fact that each event of its conditioned step meets. No solution: no binding. Otherwise events whose ranges miss their step's minimal bounds are dropped, and 1 runs again, until nothing changes.
3. *An empty domain:* the set has no binding, a complete `none` (method `pair_consistency` or `bounds_network`).
4. *One descent.* Otherwise each step, in the search's order (fewest events first), takes its first event that is pair-consistent with every step already taken, and the whole binding is checked once, jointly (`feasible` with the facts it meets), as the point forest's binding is. It holds: a complete `possible` for the phase, with its witness (method `pair_consistency_checked`). It fails, or some step has no such event: give up. No step is revisited: at most each event is tested once against each earlier step.
5. *A limit.* The pair tests of one settling are counted; past `SETTLE_LIMIT` (20,000) it gives up.

*Why it is sound.*
- Every binding's pairs are jointly feasible (part of a feasible network is feasible), and every binding's times solve the bounds network (each step's times lie in its hull, its own end-minus-start bound holds, every constraint holds, and a fact included holds whichever event is bound; leaving out the links between shared variables only widens it). So 1 to 3 remove only events that are in no binding: a `none` is sound.
- The descent's binding is checked by the same joint test the search's `accept` uses, so a binding it reports is one the search would have accepted, with the same order facts.
- `_settle` decides the model the stopped search was searching (the envelope search's, a split scenario's, D30's second search over every possible target, or a plain set), exactly when it completes: a refutation is sound for that model, and a binding is one of its bindings. So every caller reads a settled phase as it reads a completed search: an envelope possible phase with no binding is `none`, with a binding the set stays undecided; D30's second search with none is `none`; a scenario of the split is refuted or yields its binding.

*Why no decided result moves.* `_settle` runs only where a bounded search stopped, and replaces only that search's outcome. Every search that completed within its budget is untouched, and so is every result that rests only on completed searches. Where a stopped search was part of a decided result before D40, the settled outcome agrees with it, since both are sound:
- the envelope search's exact phase found `exact` after its possible phase stopped: a settled possible phase cannot be empty there (an exact binding is a possible one, and the model over-approximates), so the exact phase runs and gives the same result;
- the target split shares its budget among scenarios and decides only if no scenario stopped; where one did, it was undecided before.

So a result changes only if it was undecided (`unresolved`, or not `complete`). It may end decided, or still undecided (for example `possible` with `EXACT_SEARCH_STOPPED`, where the exact phase stopped and the possible phase was settled).

*What settles always.* A binding of one or two steps is its pairs, and step 1 tests every pair, so every set of at most two steps whose possible search stops is decided (within the limit). Three or more steps are decided where pair consistency or the bounds refute them, or the descent's binding holds.

*Not in scope.*
- The exact phases (`decide`'s certainty search on a cycle, the exact phase with order facts, the split's exact phase). They decide nothing here that the calibration needs: no `EXACT_SEARCH_STOPPED` result at 1,000 or 10,000, and at budget 1 the exact phase with facts runs only because the possible phase before it stopped, which settling now decides.
- A second search with more budget: it would spend beyond the budget the query declares.
- Better ordering, symmetry or nogoods inside the search: they would change the first binding a completed search finds, and cannot help a budget of 1.
- A smaller budget for the exact phase with facts: it changes results wherever that phase finds a binding late. Its cost at budget 1 falls anyway where settling decides the possible phase before it (reported, not ruled).

**Development check before these rules (disclosed, not a rule measurement).** While building, the stage was run on the 121 patients of the 1,000 calibration whose searches stopped (an ad hoc script): all 38 `SEARCH_BUDGET_EXHAUSTED` results were decided (18 `possible`, 20 `none`), all 80 `RELAXATION_SEARCH_STOPPED` results too (70 complete `none`, 10 `relaxed`), and seed 11's 3 order-uncertain results were unchanged; the engine's time on seed 19's patients went from 0.3 to 2.8 ms each. A first MIMIC calibration was started on this tree by mistake (the store imports the engine on first use, which came after the code changed); it was stopped before it printed anything and rerun on main's code (above). The 10,000 snapshot has not been run with the stage. The suite's small cases were run while building (they are the tests).

**Usefulness bar (set from the calibration, disclosed).** With a budget of 1,000,000 every `SEARCH_BUDGET_EXHAUSTED` result at both sizes is decided, so the ceiling is 100%. The design's argument guarantees seeds 19 and 32 (two steps): 174 of 363 at 10,000 (47.9%) and 18 of 38 at 1,000 (47.4%). Seed 35's sets should be refuted by pair consistency, since its relation R3 puts any fenfluramine 201 to 493 days before the first fenfluramine and the order fact puts the first at or before any; that is read from the query, not measured at 10,000 (the development check saw it at 1,000). Seed 44's one patient needs three steps together and may stay. The prediction is therefore 362 of 363 and 38 of 38, and the bar is set below it, at 90%.

**Pre-committed rules** (written and committed before any measurement of the change beyond the development check above).
- **Soundness.**
  1. *The worlds check* (`trajectory_graph_worlds.check`) finds **0 violations** on at least 20,000 ad hoc cases from each of four generators that stress search budgets, with new seeds, and on the suite's volume (300 per generator):
     - `budget_case` over each of `tiny_case`, `ordered_case` and `during_case`: a search budget of 1 to 12, two to five more events at exact times (which add no choice of times), and steps made `any` now and then;
     - `search_case`: three or four steps, mostly `any`, related by a tree and one or two more relations (a cycle), on five to eight events, some sharing a variable, with a search budget of 1 to 12.

     The check is corrected first so that it reads only the claims a result makes: a `possible` certainty claims that no binding is exact only where the exact search completed, so a `possible` result with `EXACT_SEARCH_STOPPED`, an incomplete `relaxed` one and a match beside `unresolved` are checked for possibility only (D35 listed this as a follow-up; budgeted cases meet all three, with the switch off as well). `check_derived` (D32's) keeps its strict reading: a planted defect of D36's needs it, and its cases' searches never stop.
  2. On every one of those cases where the engine's result is decided, it **equals the truth** by enumeration (`trajectory_graph_worlds.truth`) and **the reference's** result (status, relaxation set and certainty; the reference has no search budget and is unchanged), and every binding the result reports reaches its certainty (`binding_reaches`). The match beside an order-uncertain `unresolved` is left out: it is read off the model of the search that left the set undecided, which a stopped split leaves the envelope's, while `binding_reaches` reads the split's.
  3. `_small_feasible` equals `feasible` on at least 100,000 random one- and two-step bindings (shared variables, exposures, proper events, anchors, both ends read, infinite bounds), and on the suite's 3,000.
  4. **Planted defects are caught**, each by the worlds check or the comparison with the truth: a stopped search taken as a complete `none`; the descent's binding accepted without its joint check; an order fact applied to a pair whatever its condition; a fact in the bounds network that some event of its step does not meet; and the narrowing off by one.
- **Movement.**
  1. Every changed engine result starts undecided (`unresolved`, or not `complete`), classified by the engine's own result (`match_prepared` or `_coupled_match`, before the store's coverage and excluded-records rules), as in D31's second attempt and D35. It ends decided, or still undecided (counted and listed apart, not a failure). No decided engine result changes. Checked on every small case above, on the 300 recorded digests, in process on the kept 1,000 and 10,000 snapshots with the scaling report's 50 queries, and on the MIMIC demo.
  2. With `_SETTLE` off this tree reproduces main: the 300 recorded match digests (a test), the per-query digests of the in-process runs at 1,000 and 10,000 against main's code (e682817, extracted), and every engine test of the earlier increments, which run with the switch off where they compare with an older engine or keep an earlier record. With the earlier increments' budget of 10,000,000 no search stops, so nothing is settled (a test).
  3. *MIMIC:* Every figure of the regenerated demo report other than identifiers, digests, artifact bindings, timings and resources equals the committed report's (D36's), except the status mixes and unresolved counts, which may move only by results that were `SEARCH_BUDGET_EXHAUSTED` (the calibration's 4, 10 and 8). Each moved result is listed by the movement check (the engine's own results with the switch off and on, on a local build of the demo, for the differentials' requests), and its new status equals the status this tree gives with the switch off and a search budget of 1,000,000. Every differential keeps 0 mismatches.
  4. The 300 recorded digests are re-recorded with a per-seed fixture, `execution_store_match_digests_d40.json`, which `D40MovementTests` recomputes: the digest with the switch off (which must be D35's, kept by D36 and D37), the digest with it on, and each changed result's transition by the engine's own result and by the store's.
- **Usefulness.** In process on the kept snapshots, with the scaling report's 50 queries and windows, switch off against on: **at least 90% of the `SEARCH_BUDGET_EXHAUSTED` results decided, at 10,000 and at 1,000** (by the engine's own result). Reported, not ruled: the stopped relaxation searches (`RELAXATION_SEARCH_STOPPED`, `CHEAPER_RELAXATION_SEARCH_STOPPED`) decided at both sizes; the status mixes at 100,000 to 1,000,000 from the regenerated scaling report (seed 7's 19,890 at 300,000).
- **Latency.** Under the paired protocol (`tools/benchmark_matcher.py --paired-confirm`, `paired-interleaved-1.0`) against main (the commit this branch is rebased on), at 300,000 patients, 30 queries, 4 repeats, 14 and 8 workers, written to `verification/d40-paired-verdict.json`:
  - the 14-worker paired p95 ratio (this tree over main) is **at most 1.05 in every pair**;
  - this tree's own 14-worker p95 is **at most 10 s**;
  - the maximum is at most 30 s, and query-process memory is under 4 GB;
  - the 8-worker figures are reported; getting under 10 s there, missed since D30, is a stretch goal.
- **Regeneration, only what is stale.** The change is matching only: `trajectory_times.py`, `execution_store.py`, the converter and every file the D34 build record lists stay byte-identical to main.
  - The MIMIC demo report (it binds `trajectory_graph.py`).
  - The scaling report with `--reuse-builds` on the kept D34 builds (`~/ptm-local/scaling-d34`). A refused reuse is reported with its reason, not worked around.
  - The paired verdict above.
  - One heavy job at a time, under the benchmark lock.
- **Ship rule.** Settling ships (the switch on) only if every rule above holds. Otherwise it is switched off or reverted, and the numbers are recorded here. A failure is fixed, never tuned away.

**The calibration finding, which is the outcome of this increment.** The large-store `unresolved` figures quoted since D35 (38 at 1,000 patients, 363 at 10,000) are an artifact of the benchmark's queries, not a property of the engine on realistic queries. Every one comes from a query whose search budget is 1, which the scaling report's query generator (`random_query`, shared with the store differential) draws for about a third of its queries. Every one is decided with a search budget of 1,000,000 (at 1,000: 18 `possible`, 20 `none`; at 10,000: 174 `possible`, 189 `none`). The same holds for the stopped relaxation searches (`none` with `RELAXATION_SEARCH_STOPPED`: 80 engine results at 1,000 and 763 at 10,000, all budget 1, all decided with the large budget), and so for seed 7's 19,890 at 300,000, which are the 10,000 figure replicated. So "unresolved at scale", as the scaling report counts it, does not measure anything a real query would meet. The default budget is 40,000.

*A gap in the calibration, found by the movement check.* On the MIMIC-IV demo the calibration counted store results only. It missed 25 engine results of the differential that are `none` with `RELAXATION_SEARCH_STOPPED` (budget 1) and that the store shows as `not_comparable` (`ADMISSION_EXCLUDED_RECORDS`): the store's excluded-records rule turns an engine `none` into `not_comparable` whether or not it is complete. Settling moved them.

**Built** (263eb5d; reverted in the next commit and kept in history): `_settle`, `_pair_test` (with a direct form for a pair whose constraints bound one term of each step, checked against the network on every pair the suite's settlings meet), `_closure` and `_small_feasible`, `_bounds_network`, the switch `_SETTLE` and `SETTLE_LIMIT`; the worlds check's correction (in `check` only; `check_derived` keeps its strict reading, since a planted defect of D36's needs it); the four budgeted generators and `SettleTests`; the earlier increments' movement tests and the frozen-engine comparison run with settling off; the 300 recorded digests re-recorded with `execution_store_match_digests_d40.json`; the profile's description. Every suite passed (`ci-local.sh`), with only the expected stale reports and test counts.

**Measurement** (after the rules, e3dcd7b; on 263eb5d; single processes, no heavy job; the regenerations were not run, since the stage did not ship).

| Rule | Measured | Verdict |
|---|---|---|
| Soundness 1: the worlds check, at least 20,000 per generator | 80,000 ad hoc cases (seeds `d40-rule-tiny`, `-ordered`, `-during`, `-search`): **0 violations**. Settlings ran 1,475, 2,168, 1,954 and 4,462 times and decided their set in 1,404, 2,070, 1,925 and 4,328. The suite repeats this on 300 per generator | met |
| Soundness 2: decided results equal the truth and the reference; reported bindings reach their certainty | Every decided result equals the truth by enumeration and the reference's result (0 differences in 80,000). 8 reported bindings fail `binding_reaches` (5, 0, 2 and 1 by generator). All 8 are in undecided results (`possible` with `EXACT_SEARCH_STOPPED`) reached through a target split the budget stopped; each holds in some world (the worlds check) and reaches its certainty in the reference's split model, while `binding_reaches` reads the envelope model, since without a budget the reference decides that set there | met as written: the clause covers decided results. Its sentence on the match beside `unresolved` suggests undecided ones were meant too; under that reading, 8 failures of the check, not of the engine |
| Soundness 3: `_small_feasible` equals `feasible` | 100,000 random bindings (half with shared variables): **0 disagreements**; the suite's 3,000 too | met |
| Soundness 4: planted defects | Over 5,000 cases per generator (seed `d40-rule-defects-*`), cases failing by generator (tiny, ordered, during, search): a stopped search taken as `none` 169, 301, 260, 776; the descent's binding unchecked 4, 10, 5, 26; a fact applied whatever its condition 2, 11, 0, 7; a fact in the bounds network some event does not meet 2, 11, 0, 7; the narrowing off by one 134, 272, 219, 725. Nearly all by the worlds check | met |
| Movement 1: only from undecided | Small cases: no decided result changed (to a decision 564, 983, 656, 1,106; within undecided 218, 385, 492, 2,350). The 300 digests: 21 seeds, 66 engine results, 59 to a decision, 7 within undecided. In process: 118 results at 1,000 and 1,126 at 10,000, all to a decision | met |
| Movement 2: switch off reproduces main | The 300 digests (a test); 50 of 50 per-query digests at 1,000 and at 10,000 against main's code (e682817, extracted); with the earlier increments' budget nothing is settled (a test) | met |
| **Movement 3: MIMIC** | On a local build of the demo, for the four differentials' requests: 47 engine results moved, none of them decided before, all still undecided after. 22 `SEARCH_BUDGET_EXHAUSTED` (the calibration's 4, 10 and 8) became `possible` with `EXACT_SEARCH_STOPPED`, since the exact phase stops too at budget 1; with a budget of 1,000,000 they are `exact`, so "its new status equals the status with a budget of 1,000,000" fails. The 25 results above (not `SEARCH_BUDGET_EXHAUSTED`) moved too, from engine `none` with `RELAXATION_SEARCH_STOPPED` to `relaxed` with its exact search stopped, which the store shows as `unresolved` (`ADMISSION_EXCLUDED_RECORDS`) instead of `not_comparable` | **missed as written**, on both counts |
| Movement 4: the fixture | Recorded and recomputed by `D40MovementTests` | met |
| Usefulness: at least 90% of `SEARCH_BUDGET_EXHAUSTED` decided | **363 of 363** at 10,000 (174 `possible`, 189 `none`, seed 44's one patient included) and **38 of 38** at 1,000. Reported: `RELAXATION_SEARCH_STOPPED` 763 of 763 and 80 of 80 decided (663 and 70 `none`, 100 and 10 `relaxed`). In-process time at 10,000: 38.21 s off, 38.35 s on; seed 19 1.60 to 1.75 s | met (on the synthetic sets) |
| Latency (paired) | Not run: the stage did not ship | not measured |

**Decision: D40 does not ship.** The ship rule requires every rule, and the MIMIC rule was missed as written; a rule is not amended after it is measured. There is a second reason, from the calibration: every result settling decides at scale comes from a search budget of 1 that the benchmark's generator draws, so deciding them has little value for real queries. The implementation is reverted (the commit after 263eb5d) and stays in history; the engine, the tests, the fixtures and every report are main's. Nothing is regenerated. What stands is the calibration finding.

*Follow-ups.*
- Make the benchmark's search budgets realistic, or report budget-1 queries apart, so that "unresolved at scale" measures something real. The store differential and the MIMIC demo draw from the same generator.
- If settling is retried, rewrite the MIMIC rule before measuring so that it asks only that each moved result goes from undecided to undecided, or to a decision that is correct (equal to the large-budget engine's), and calibrate on the engine's own results, not the store's.
- The worlds check's reading of a `possible` claim where the exact search stopped (D35's follow-up) was corrected only on the reverted branch; it still stands as a follow-up.
- `binding_reaches` chooses the reference's model by the path an unlimited budget takes, so it cannot check a binding from a path a budget forced; a check of reported bindings under small budgets should use the worlds check, or the model of the path taken.
