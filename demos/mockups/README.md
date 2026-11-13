# Interface mockups

Exploratory interfaces for clinical researchers defining and matching patient trajectories, for design discussion. Nothing here is product code, a supported profile, or a clinical claim; the product is the research workspace in [`app/`](../../app/). The test data is a synthetic paediatric rare-disease cohort.

```sh
python demos/mockups/synthetic/generate.py                 # writes demos/mockups/data/ (git-ignored)
python demos/mockups/server.py                             # pages plus the trajectory-graph engine on 127.0.0.1:8090
# from another machine: ssh -N -L 8090:127.0.0.1:8090 <user>@<host>, then open http://localhost:8090/
```

Opening the HTML files directly will not work; the pages fetch the dataset over HTTP.

`server.py` serves the pages as `python -m http.server` would, and adds the trajectory-graph engine (`patterns/trajectory_graph.py`, [profile](../../docs/trajectory-graph-profile.md)). The trajectory page then matches with the engine: it resolves each step's candidate events, including relaxed alternatives, and the engine decides timing, relaxation and search budgets. A toggle switches to the page's own in-browser search, which is also used wherever no engine is reachable, as on static hosting. `test_engine_parity.cjs` checks that both give every patient the same status, certainty and relaxation set. Given a locally built MIMIC-IV demo store, `server.py` also serves the store query page ([below](#store-query-page-stage-1)).

## Store query page (stage 1)

`store-query/` runs trajectory queries in the [execution store](../../docs/execution-store.md) on the server, against a store built on this machine from the open MIMIC-IV demo. It sits beside the other pages, not under them: the trajectory page still resolves candidates in the browser (decision D29 sets out the three stages).

```sh
python -m pip install -r patterns/requirements-store.lock.txt
python -m patterns.mimic_store_ingest --input-dir data/local-mimic/icu --dataset-id mimic-iv-demo-2.2 --chartevents --workers 8
python demos/mockups/server.py --port 8091 --mimic-store verification/execution-store-mimic-run/store \
  --mimic-d-items data/local-mimic/icu/d_items.csv.gz
```

The build takes about two minutes and writes into the git-ignored `verification/execution-store-mimic-run/`. The demo files come from `data/local-mimic/` (D11). Neither is ever committed. `PTM_MIMIC_STORE` and `PTM_MIMIC_D_ITEMS` can stand in for the two flags. Without a store, the page says so and shows these commands. Without a server (static hosting, or the file opened directly), it says the query needs one.

- **Endpoints** (`store_query.py`):
  - `GET /api/store/status` says whether a store is available, and if not, why;
  - `GET /api/store/catalogue` lists the store's item classes and dictionary categories, with patient and event counts and the units recorded for each;
  - `POST /api/store/match` takes the page's query and runs `execution_store.match`.
- **Aggregate counts only** (D17 and the demo convention), enforced on the server. The match answer is built field by field from counts: counts by status, `not_comparable` and `unresolved` reasons, notes, relaxations used, candidate, filtered and pruned counts, and timings. It is then checked against a closed schema: only known keys, integer counts from 0 to the store's patient count, and strings from fixed vocabularies (statuses, upper-case reason codes, the server's own relaxation identifiers). The reason, note and certainty keys are also compared with the patient identifiers. Nothing the page sent is echoed. The status and catalogue answers are checked against closed schemas too. An answer that fails its check is never sent; the server answers `500 AGGREGATE_RULE_VIOLATION` instead. A malformed request gets a named 400, and an internal failure `500 INTERNAL_ERROR`, with no traceback. No patient identifier, time, value or per-patient row leaves the server.
- **Only the open demo, by content.** The manifest's dataset id (`mimic-iv-demo-2.2:source_item_class`) is a first, cheap filter only: it is a label outside the snapshot identity, and the build command above writes it whatever the input. Before anything is served, the store must hold exactly 100 patients whose sorted identifiers hash to `DEMO_SUBJECTS_SHA256` in `store_query.py`. That digest is pinned from the demo's own `icu/icustays.csv.gz` (itself pinned in `data/mimic-inputevents-demo-pin.json`); `python demos/mockups/store_query.py demo-pin data/local-mimic/icu` recomputes it. A relabelled snapshot, or full MIMIC-IV built with the demo's dataset id, is refused.
- **Counts-only is not disclosure control.** There is no small-cell suppression, and differencing works: a run of queries that halve a threshold can isolate one patient's value. That is acceptable only because the data are the open demo. A credentialed deployment would need real disclosure control (suppression, noise or query auditing), which is not built; the content check is what keeps credentialed data out of these endpoints.
- **The query builder.**
  - *Steps* are item classes or whole `d_items` categories. They are source item classes labelled with the public dictionary's terms, not clinical concepts, and the page says so.
  - *Each step* takes an occurrence (any, first or last) and an optional value condition: a unit chosen from those recorded for that item, a lower bound (`>` or `≥`) and an upper bound (`<` or `≤`).
  - *Relaxations*, optional: a wider threshold (`widen_value`, which the store refuses unless it contains the stricter one), or any item in the step's category (`widen_concept`).
  - *Constraints* are *starts between min and max after* (minutes, hours or days, with `extend_max` as a relaxation) or *happens while* an infusion runs, with a grace (with `extend_grace`).
  - *Thresholds* are kept and sent as the strings typed. The server refuses a JSON number (`THRESHOLD_MUST_BE_A_STRING`), so no threshold passes through a float.
- **The index period** is placed on the server from the store's coverage table. That table holds one period per ICU stay: `icustays` in-time to out-time, widened to the stay's own records. The page offers three rules: the first ICU stay, its first 24 hours, or from the first stay's start to the last stay's end. This is a simple choice, not the demo differential's seeded random windows. A patient with no stay has no period.
- **Results** explain each status and why most results are `not_comparable`. Only numeric chart rows with a unit and continuous infusion segments are admitted. An excluded row of a step's item that the scope admits might have matched (D18), whatever the value condition (D28).
- **Examples.** Each is checked against the catalogue before it is offered. Their aggregate results on the demo store (snapshot `c4f41d29…` since D30; the results are those of `9730e87b…`, unchanged):

| Example | Exact | Relaxed | Unresolved | None | Not comparable | Candidates, pruned |
|---|---|---|---|---|---|---|
| Non-invasive mean blood pressure below 65 mmHg, then norepinephrine 0–6 h later, first ICU stay (relax to below 70, or any Medications item) | 13 | 0 | 38 | 6 | 43 | 70, 15 |
| Heart rate above 120 bpm while the first norepinephrine infusion runs, first ICU stay (relax to above 110) | 3 | 0 | 1 | 81 | 15 | 24, 6 |
| First temperature of at least 100.4 °F, then respiratory rate above 24 within 4 h, first 24 hours (relax to 8 h) | 11 | 1 | 0 | 85 | 3 | 28, 14 |

  Every `not_comparable` and `unresolved` result above has reason `ADMISSION_EXCLUDED_RECORDS`. In the first example, 36 patients match only if norepinephrine is widened to any Medications item, and 7 only if the threshold is relaxed. Excluded rows could hold an unrelaxed match for those patients, so their relaxed match is reported beside an `unresolved` status. Each query takes 0.1 to 0.2 s.
- **Tests** (`test_store_query.py`) build stores from the synthetic MIMIC fixture, never from real data. The fixture is served only with its own pin passed in as a test hook. Under the real pin, every one of these is refused: the fixture built under the demo's dataset id, the fixture under a credentialed id, a relabelled snapshot, and 100 patients that are not the demo's. A real demo store passes when one is built locally (the test is skipped otherwise). The tests also check:
  - a failed open leaves nothing open, and missing DuckDB is named;
  - malformed requests (lists for strings, enormous numbers, deep nesting) get named 400s, and an internal failure a 500 without detail;
  - the answer's keys and values are counts only, bounded by the patient count, with no patient identifier, event time or value, and the status and catalogue answers pass their own schemas;
  - a deliberately leaky answer becomes `500 AGGREGATE_RULE_VIOLATION`;
  - the counts are `execution_store.match`'s own;
  - thresholds must be strings and are compared exactly;
  - units come from the store;
  - each refusal is by name.

## The synthetic cohort in the store: parity with the trajectory page

`test_store_parity.cjs` (Node, run in CI) puts the 48-child cohort into a store. It compares, patient by patient, the store's results with the engine's results on the trajectory page's own candidate resolution. The fourteen trajectories are ones the page can express in the store's language, translated by `trajectory/store_request.js` (D32):
- concept steps with any, first or last; since D32, Nth and Nth-from-last occurrences and "at least N times within a span" or "in the index period", with the one-fewer relaxation (four trajectories, among them the page's *Event frequency* template), over coded records only (the page's "coded only" setting);
- `after` and `during`, and an anchor on the period's start;
- the page's index windows;
- absolute value conditions, one with a threshold equal to recorded values (whole monthly counts), so reading "at least" as "above" changes 10 results;
- the page's relaxations: a parent concept, the drug class, a 1.5× longer gap, 30 more days after stopping, and a looser threshold.

How it works:
- **Node.** The page's candidate resolution is JavaScript. The test runs the page's own `core.js` and `trajectory/app.js` in a stub DOM, as `test_engine_parity.cjs` does, rather than a port that could drift from it. Node is not installed on the development machine, so the test was run there with a portable Node 20, downloaded into a scratch directory and checked against nodejs.org's published SHA-256. CI runs it with `actions/setup-node`.
- **The store side** (`store_parity.py`) builds the cohort with `patterns/synthetic_ingest.py` emitting `series` (its option since D32): each measured record with a number and a unit gets one `series` row, the value spelled as the cohort's JSON writes it, and the 31 measured records without a unit become excluded records. Windows are the page's, sent in its engine's minutes from birth, which map exactly onto the store's clock.
- **The translator** (`trajectory/store_request.js`) refuses by name what the store cannot express: gene steps (`GENE_CLASSIFICATION_UNSUPPORTED`), rises and falls (`RELATIVE_CONDITION_UNSUPPORTED`), hedged free text (`FREE_TEXT_HEDGED_UNSUPPORTED`), an Nth occurrence or a count over stated free text (`FREE_TEXT_COUNT_UNSUPPORTED`: the store ranks and counts coded records only), and "while taking" from an Nth step (`NTH_STEP_HAS_NO_END`). The test asserts each code, on the page's own templates where they apply. A server-side port, for the page on the store by default, is stage 2's switch.
- **A replay** runs the engine alone on the candidate events the explanation reads, deriving Nth and count steps from them by the store's own rules. It must give the store's own result, up to the store's comparability and excluded-records rules. It does, for every patient: the store matched exactly those candidates. So each difference comes from which events are candidates, from the comparability rules, or from a derived step's rule. None comes from translating the query or the windows.

**Results** on the committed seed (D32): 555 of 672 patient results agree over the fourteen trajectories. The ten of D29 give 397 of 480 (401 before D32: four weight results moved, below); the four with Nth occurrences and counts give 158 of 192. The 117 that differ each have a named cause, from a closed list the test checks. The causes are pinned per trajectory and patient in the test, so a new or vanished difference fails. The classifier lists every step whose candidates differ between the two sides, so a patient can have several causes, and the counts below are patients per cause.
- *Coverage* (19 patients, one trajectory). This one is the store's comparability rule, not a page shortcut. The store says `not_comparable` (`NO_SOURCE_COVERAGE`) when no source recording a step covers the period; here that source is the seizure diary. The page says *no recorded match* when the steps that can be measured do not match, and *not comparable* only when they do.
- *Letter mentions* (60). The store holds each finding a letter states as its own event. Since D30 that event ranges from the patient's birth to the letter, since the letter says only that the finding happened by then; before D30 it was timed by the letter itself, which missed the true time of 2,443 of the 2,444 mentions. The page merges a mention into the event it describes, or times it from the letter's wording ("about 3 weeks ago"). A letter's mention of a medicine counts here too.
- *Record reconciliation* (42). The store holds every record as an event. The page merges the records of one happening, grouped by the generator's ground truth (a shortcut listed below), and times the merged event by its most precise record. So the first or last event can differ, and so can whether a time is certain. In C004, for example, the most precise record of the first febrile seizure has a year typo, which moves the page's merged event to 2091. The store still holds the registry's record of that seizure.
- *Medication records* (43). The store's exposures run from each order to its stop, and an ongoing one has an open end; dispensings are separate point events. The page builds one exposure per course, from the order or else the first dispensing. It ends at the stop or when the supply ran out, and an ongoing one ends at last contact or later.
- *Measurement records* (4, D32). The converter makes the 31 weight, height and head-circumference records without a unit excluded records (a source that drops a value must say so, D28). The page reads them by value alone. So "weight at most 9 kg" is not comparable in the store for three patients (an excluded record of the item could have matched) and unresolved for one (it could have supplied the unrelaxed match).
- *Occurrence envelope* (14, D32). For an Nth step the store's time is the Nth occurrence's, ranging over every time the coded records allow, and unresolved where another step can bind the ranked events. The page binds the happening its most precise record ranks Nth, which is not sound under uncertain times (D32 plan, 1.2).
- *Count timing* (20, D32). For a count the store binds the record at which the count is reached, certainly or only possibly, with the two-pass rule (unresolved where it cannot decide). The page takes each run of N consecutive happenings, by their best dates, that may fit the span. Records against happenings (record reconciliation) adds to it: the store counts recorded events, so a happening recorded twice counts twice.
- *Order uncertain* (9, D30). A `first` or `last` step whose target the records leave uncertain is decided in the store on the range the true first (or last) time can take, where that is exact, and is otherwise `unresolved` (here the first blocker in "a seizure while taking the first blocker", whose end the relation reads). The page binds the event its reconciled records date, without that test.

D30 moved 57 of the store's results: 53 at the mention-timing stage and 4 at the order-rule stage (none at the corrected `last`). Each was localized to a stage by running the store as before D30, then with each change in turn; the stages are cumulative, so the order rule acting on the wider mentions is counted at the mention timing. With the mention timing localized before the order rule instead, 59 results move at some stage: 45 at the mention timing only, 2 at the order rule only and 12 at both. The localization does not by itself show that a change is a fix; the engine's worlds check does that. The page now also takes `last` by the latest possible start, then the earliest, as the engine does.

With D32 the counts per cause are: count timing 20, coverage 19, letter mentions 83, measurement records 4, medication records 66, occurrence envelope 14, order uncertain 9 and record reconciliation 69. The Nth and count trajectories ask for coded records on the page, while the store's other steps still read letter mentions, which adds letter mentions there.

*Unresolved in the store*, per trajectory: 9 of 39 results with a window for "a seizure while taking the first blocker" (D30), 1 of 39 for the weight trajectory, 1 of 38 for the *Event frequency* template, 0 of 38 for the second seizure and the second-to-last seizure, and 8 of 38 for "at least 2 febrile seizures in the index period" (the count reached only possibly); 0 elsewhere.

A patient can have several causes. None is a defect of either side: they come from the page's shortcuts and the store's literal records. They are documented here and in D29 and D32, not fixed. Each moves into the converter (reconciled events, timed mentions, medication courses) or is declared as a difference, as is the comparability rule. Since D32 the parity is measured with the synthetic converter itself emitting `series`.

### What the store cannot express yet (the stage 2 inventory)

These trajectory-page features have no store form, so the translator refuses them by name or the parity test leaves them out:

1. ~~Nth and Nth-from-last occurrence~~ for steps: built in D32 (over coded records). For the index period's event, see 10.
2. ~~"At least N times within a span" or "within the index period"~~, with the one-fewer relaxation: built in D32 (over coded records, counting recorded events).
3. **Rises and falls** against the mean of the one or two measurements in the 75 days before, and their relaxation, a smaller factor. Only absolute thresholds have a store form (D28).
4. **"The measurements do not cover the time around the earlier step."** This is the page's *not comparable* for a measurement that cannot be observed after the event it follows. The store's comparability rule uses coverage by source and excluded records instead.
5. **Gene steps: the first report that is not negative**, timed by when it was recorded, with reclassification. The store holds genetic results as events of the gene, timed by occurrence, with no classification to filter on.
6. **Free text as a query option.** The page offers coded records only, also stated findings, or also hedged findings. The store's converter fixes it at findings stated about the patient.
7. **Reconciled events.** The page counts a seizure entered by two sources once and times it by its most precise record. The store has one event per record. This changes first, last and counts.
8. **Inferred medication courses.** The page builds a course from dispensings and their supply, and ends an ongoing one at last contact. The store has order-to-stop exposures, with dispensings as points.
9. **Times from a letter's wording** ("about 3 weeks ago").
10. **Index periods placed per patient by rule**: an event's first, last or Nth occurrence with an offset, an age, a date or a span, and "any diagnosis" or "any genetic result". The store takes windows as input. The page computes them in the browser, and the store query page offers only stay-based rules on the server.
11. **Values without a unit, or in another unit.** The page reads any numeric value of a series; 12 weight, 12 height and 7 head-circumference records have no unit. The store reads one literal unit (D28; unit equivalence is stage 3).
12. **In neither yet:** absence ("no X between A and B") and alternatives ("A or B").

## Results page

`results/` summarises the execution-store and matching evidence: scale, matching latency at 300,000 patients against the D21 target, and the MIMIC-IV demo census. Every figure is read from a committed report in `verification/` by `results/build.py`, which writes `results/results.json`; run it again after those reports are regenerated (`--check` reports a stale file). The page lists each source report's digest.

## Roadmap page

`roadmap/` lists the key capabilities by status (built, in progress, next, later only if needed, needs a decision from the project), each with the decisions or documents behind it. It is written by hand and dated; its four headline figures follow `results/results.json`. Update it when a decision lands.

## The synthetic cohort

`synthetic/generate.py` is standard-library Python and deterministic for a given seed (`--check` regenerates and compares). It writes 48 invented children across Dravet syndrome, GEFS+, febrile seizures, PCDH19 clustering epilepsy, CDKL5 deficiency disorder, tuberous sclerosis complex, Rett syndrome and spinal muscular atrophy.

Each child has two layers:

- **truth**: what happened, in days of age, with no recording artefacts
- **records**: what nine sources hold (hospital EHR, clinic letters, a Dutch referral letter, a Belgian discharge summary in French, the genetics laboratory, the pharmacy, a parent seizure diary, an ERN registry and newborn screening)

Every record keeps three times apart: when it happened (bounds and precision), when it was written down, and when a researcher's system could first see it. Every deliberate departure from a clean capture is listed in the child's `quality` array with the records and truth event it touches. The 32 kinds fall into five groups:

| Group | Kinds |
|---|---|
| Missing | measurement, unit, result, follow-up gap, lost to follow-up |
| Incorrect | unit error, decimal shift, implausible value, year typo, day–month swap, duplicate, copy-forward, wrong code, sibling's record |
| Timing | approximate onset, conflicting onset, recorded late, start known only from dispensing, approximate stop, result reported weeks after collection, reclassified variant, corrected age |
| Coding | broader parent code, local code only, several terminologies, free text only |
| Free text | negation, family history, plans, hedging, Dutch or French, misspelling |

The reference child, C001, is scripted: a girl with Dravet syndrome whose first seizure was at a regional hospital, who was given carbamazepine there before her SCN1A variant of uncertain significance was reclassified as pathogenic, and whose records carry one or more instances of most kinds. Sodium-channel blockers raise seizure counts only in the Dravet children, so a rise after exposure carries signal.

Terminology codes are illustrative and were written from memory to make coding variation concrete. They have not been checked against a terminology release.

## B · Start from a patient

`patient-first/` follows the progressive disclosure in [specification 2.2 §20](../../addenda/specification-2.2.md): reference history, explicit criteria, ranked candidates, pairwise evidence and revisions. It computes everything in the browser from the records:

- **Index window.** Each patient is compared over their own window, placed by one rule. Each end is set at birth, at an age, on a calendar date, at the first occurrence of any event chosen by terminology search (with an offset), or as a length of time from the other end. Every amount of time takes a unit from seconds to years; ages are fractional days, so a time of day on a record counts. Findings count if they occurred inside the window; diagnoses and genetic results are states and count if established by its end, using the classification reported by then; medicines count if taken at any time during it. By default only evidence *available* by the end counts, so a letter signed later or a result filed later is excluded. A patient whose window cannot be placed (no start event, born after it ends, not followed to its end) is listed as not eligible with the reason. The pairwise drawer can align two patients by age, window start or window end.
- **Must match, Prefer similar, Ignore, Hide from view.** Only Must match changes who is eligible; only Prefer similar changes the order; Hide changes the page and never the computation. Unrecorded features score zero and are shown as coverage beside the score.
- **Hide from view is one rule everywhere.** Hiding an event hides that concept, for every patient, on the timelines, the event list, the criteria summary, the results, the pairwise table, its data-quality column, hover tooltips and annotated mentions in letters. Hiding a feature hides that whole kind of information the same way (diagnoses, genetic results, medicines, findings, sex), so a researcher can judge similarity blind. Hidden values leave a visible placeholder ("2 hidden from view"), because scores and coverage still include them. Letters are masked only where a mention was annotated; unannotated text, such as a gene name in "panel sent (SCN1A)", can still show.
- **Terminology.** Diagnoses match through any coding of the concept or a narrower one. A broader code (ICD-10 G40.4 for Dravet syndrome) does not count unless a letter refines it, and the criteria panel says which codes count.
- **Free text.** Stated findings, optionally hedged findings; never negated, hypothetical or family-history mentions. A hedged diagnosis written before anything was coded is kept as its own earlier assertion.
- **Pairwise evidence.** For each feature the drawer splits values into *shared exactly*, *only in the reference* and *only in the match*, marking related-but-different values with ≈ (broader, narrower, same drug class). Hovering an event on either timeline gives the same verdict for that event and highlights its counterpart on the other timeline; hovering a value in the table highlights the events behind it.
- **Synthetic truth lens.** Shows what happened, including events never recorded and events after last contact, so the interface can be judged against a known answer.

## Define a trajectory

`trajectory/` is a cohort question with no reference patient, kept apart from the record view. It is defined in order:

1. **Index period.** The same two-ended window as the record view, placed per patient. It anchors the trajectory: every step can be required to fall anywhere, from its start onward, inside it, or after it ends, and constraints can start from its start or end.
2. **Trajectory.** Each step is an event found by searching name or code in any terminology the table holds (ICD-10, ICD-10-CM, SNOMED CT, ORPHA, OMIM, HPO, ATC, LOINC, HGNC, local); the concept also matches its narrower concepts through every coding, and its kind decides the condition. A finding or diagnosis is any occurrence, the first, the last recorded, the Nth, the Nth from last, or *at least N times* within a span or within the index period (the step then happens when the count is reached). A medicine or class takes the same choice of start. The index period's event takes first, last, Nth or Nth from last too. Occurrences are counted over reconciled events across the whole record, so a seizure entered by two sources counts once; "last" means last recorded and moves as follow-up grows, and among candidates it counts back from the latest possible start (D30). A gene is the first report that is not negative. A measurement (the seizure diary's monthly count, weight, height, head circumference) is a condition on the series: rises or falls by a factor against the mean of the one or two measurements before, or crosses a threshold. Timing constraints between steps are *starts between X and Y after* or *happens while taking*, optionally up to some time after stopping, each in its own unit. The page detects the shape and says what it means: a **sequence** (each step has at most one before and one after), a **tree** (branches from one root with no order between branches), or a **graph** (a step after several others). Cycles, dangling references and "while taking" on something that is not a medicine are rejected with a plain message. Sequence, Tree and Graph templates load worked examples.
3. **Matching patients.** Every patient is searched. Each step is bound to one of the patient's events, and the constraints are solved jointly as a simple temporal network: a step's time is its event's time, bounded by the event's recorded range, a medicine also has an end, and constraints bound the differences. Steps bound to the same event share its time, so one seizure cannot stand in for two seizures a month apart. A binding is *possible* if some choice of times inside the recorded ranges satisfies every constraint at once, which is checked exactly with shortest paths, and *exact* if every choice does. Results are exact, possible, relaxed with a declared cost (a broader concept, one fewer occurrence, the same drug class, a smaller change or a looser threshold, or a 1.5× longer gap), no recorded match, not comparable (the measurable steps match but the measurements cannot answer the rest), or no index period. Result types can be shown together; *no recorded match* and *no index period* are hidden until chosen, and *no recorded match* is then shown on its own. The detail drawer tags each bound step on the patient's timeline and gives a verdict for each constraint.

### Shortcuts these mockups take

- Records are grouped into events using the generator's ground truth. A real system would have to infer the grouping; the inspector says so on every event.
- Data-quality cases are known because they were injected. With real data they would be detections, and some would be missed.
- Event frequency counts recorded events, which are only the notable ones (an ED visit, a letter). Day-to-day frequency is in the diary's monthly counts, a measurement, so "4 seizures in a month" is a measurement condition here, not an event count. A real system would need both and would have to say which it used.
- SNOMED CT codes were added for a handful of concepts to make search across terminologies concrete. Like every code here they are illustrative and unverified.
- Trajectory search is backtracking with a budget of 40,000 attempts per search. A patient whose unrelaxed search exhausts it without a binding is *Unresolved*, shown by default, not a non-match; a found binding or a non-match that a stopped search leaves open carries a note saying what may have been missed. Relaxations are tried one or two at a time within a total cost of 1, and "exact" checks each constraint against the recorded ranges, each medicine's end being at or after its start, and the shared time of an event bound to two steps. Absence ("no X between A and B") and alternatives ("A or B") are not yet expressible.
- Similarity weights are equal and the distance rules are simple. They are placeholders for design discussion, not a proposal. Clinical findings and medication exposures can be compared by simGIC (the default), ancestor-Jaccard or best-match Resnik, as in the workspace engine. Information content comes from every patient's whole record in the 48-patient synthetic cohort, not from a searched database, and does not change with the window.
- The store query page uses the open MIMIC-IV demo, not synthetic data. It needs a store built locally and shows aggregate counts only.
- Layout is three columns at 1280 px and wider, stacked below. The narrow-screen tab layout, measured accessibility checks and browser testing beyond Chromium are not done.

## Deployment

The `Dockerfile` serves these pages with nginx and regenerates the dataset during the build, so the generated file is never uploaded. No hosted copy is currently running; the Cloud Run service `ptm-mockups` was removed on 4 November 2026. To host it again behind IAP:

```sh
gcloud run deploy ptm-mockups --source demos/mockups --region us-central1 --project dumontier-charm --iap --no-allow-unauthenticated
```

Then grant `roles/iap.httpsResourceAccessor` to the people who should see it.
