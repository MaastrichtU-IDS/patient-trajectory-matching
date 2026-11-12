# D31 and D32 (plan): the store's step language for stage 2

*Plan, 2026-11-11; not implemented. This began as the D30 draft (design only). An independent review found defects in the engine's shipped `first` and `last` and in the synthetic converter's letter mentions, and the owner made D30 a correctness increment instead, implemented now (decision log, D30). Nth and counts move to D31, relative conditions to D32. The draft's text is kept below with the review's changes applied; where it says "D30" it means the increment that will build it (D31 for sections 1 and 2, D32 for section 3). Section 8 lists the decisions it still needs from the owner.*

D29's stage-2 trigger: the store can express everything the trajectory page offers, or refuses it by name, and the parity test is clean. Clean means every difference is removed or accepted as a declared difference, measured with the converter itself emitting `series`.

## Changes after the review

What D30 fixed, and what this plan now assumes:
- **`first` and `last` are sound since D30.** `last` takes the latest start. A `first` or `last` target that the patient's facts leave uncertain is decided on its envelope where that is exact (§1.2 below proves the envelope), and is otherwise `unresolved` (`OCCURRENCE_ORDER_UNCERTAIN`). The review's counterexamples are in the decision log. Rule R, which this draft criticised for N ≥ 2, was also wrong in three other cases: for `last` at N = 1, for any `first` or `last` step whose end a relation reads, and for one whose events another step can bind.
- **Letter mentions range from birth to their letter** (D30). Before, 2,443 of the 2,444 missed their true time. They are now sound but wide (median about four years).
- **Unresolved shares to expect.** On the synthetic store's 300-query differential, 158 of 14,400 results are `unresolved` after D30 (8 before), most of them by the order rule. On the store parity test's ten trajectories, 9 of 480 are. The same rule will apply to Nth (below), so D31 must report its unresolved share and keep it under a bar fixed before measuring.

The review's changes to this plan, applied below:
1. **Nth ranges come from `Times.dom`**, the patient's tightened ranges, not the recorded ones. A proper exposure's start is bounded by its end, so the envelope must be over what the patient's facts allow (§1.4).
2. **Coupled Nth steps are `unresolved`** (`OCCURRENCE_TIME_COUPLED`), not `possible` with a note. When another step can bind an event of the concept, or the events share variables, the envelope's own variable loses a correlation and `possible` is not sound (§1.4). This matches D30's rule for `first` and `last`.
3. **The two-pass rule** (shared mechanism):
   - Its trigger covers every tag that any relaxation set uses, not only the unrelaxed query's, because a relaxation can widen to a tag with possible-only events.
   - `first`, `last` and `nth` are refused on a step that carries a count or a relative condition (`OCCURRENCE_ON_DERIVED_STEP`): an occurrence over certain-only events would pick a different event in each pass.
   - `complete` combines both passes: it is false if either pass's search was incomplete. A stopped pass 2 makes the result `unresolved`, as an undecided set does in D30. Notes from both passes are kept.
   - The oracle checks every status the rule can return except `unresolved` (`exact`, `possible`, `relaxed`, `none`, and the matches reported beside `unresolved`), as D30's `trajectory_graph_worlds.py` does.
4. **Period counts:** a count within the index period is *certain* at e only if e's whole range lies inside the window (§2.2).
5. **Counts and Nth run over coded records only.** Mention events are left out of them, and a count or Nth step asking for stated free text is refused (`FREE_TEXT_COUNT_UNSUPPORTED`) until a mention's time is informative as well as sound. Since D30 a mention spans birth to its letter, which would make almost every count and Nth uncertain.
6. **The candidate filter uses the smallest N′** over the step and its count relaxations (posting `n` ≥ min N′), so a patient that matches only after a count relaxation is never filtered out (§2.2).
7. **Relative conditions are deferred to D32.** Two things come first:
   - a decision on how a monthly summary is timed: the diary's counts are ranges of a whole month, so is the reading's time the month, its end, or a point inside it?
   - a pre-committed usefulness bar. On the synthetic cohort the review found only 45 of 1,166 diary readings with a determined baseline, and 2 with a certain rise, so D32 ships only if a stated share of its results is decided, a share fixed before measuring.
8. **Snapshots without series refuse value and relative conditions by name** (`NO_SERIES_IN_SNAPSHOT`), rather than answering every condition `none` or not comparable.

## Latency candidates for the next increment (from the review of D30)

D30's order rule made the 300,000-patient confirmation miss the D21 target. A review profiled the p95 queries and found the added time in the repeat search of unsafe `first` and `last` steps, mostly arc consistency (`_Arc.revise`, O(n·m) per arc). That search almost never refutes. D30 built three levers that cannot change any output (a sweep for `revise`, no exact phase in the repeat search, a direct certainty test); the decision log has their measured effect. Two more change results or the store format, so they are candidates here, each to be decided under its own rule:
- **Lever 1: one search over an envelope with order facts.** An unsafe step today is searched on its picked event, then again over every possible target. Instead, search once over a point ranging over its envelope, adding the facts that tie that point to the other steps' events: the target starts no later than every event of its family (no earlier, for `last`), and it coincides with an event of the family when another step binds one. This over-approximates the true semantics less loosely than the repeat search, decides more cases, and costs one search. It changes results, so it needs a proof that it is sound for `none` and for the claims it makes, the worlds check extended to it, and the usual regeneration. It is the first lever to try if the D21 target is still missed.
- **Lever 5: pruning on order.** The posting summaries hold each concept's earliest and latest start and latest end, not the least latest start that bounds a `first` step's envelope (or the greatest earliest start, for `last`). Storing those per (concept, patient) would let time pruning bound the envelope directly and prune more candidates of `first` and `last` queries. That is a change to `concept_postings`, so a store format change and a new profile, with D25's exactness argument redone.

## 0. Summary

| Item | Recommendation | Where | Engine |
|---|---|---|---|
| 1. Nth, Nth from last | The **time of the Nth occurrence**: a derived point whose start range is exactly the set of times the Nth occurrence can have (the order-statistic envelope). Not "the record ranked N", which is unsound for N ≥ 2 (§1.2) | Store request, compiled per patient after loading | Unchanged |
| 2. At least N times within a span or the period | The step binds the event at which the count is reached. The count is *certain* or only *possible* at that event, decided exactly from the recorded ranges. Matching uses the certain events, then a second pass with the possible ones turns a would-be `none` or `relaxed` into `unresolved` | Store request, compiled after loading | Unchanged |
| 3. Rises and falls | Mean of the previous k readings of the same item class within W, with an explicit floor. Exact where the baseline is determined, otherwise *possible*, using the same two-pass rule as counts. Built last in D30, under its own ship-only-if rule, with a named refusal if it misses | Store request | Unchanged |
| 4a. Coverage (19) | Declared: the store's rule stands. The page takes the store's answer when it runs on the store | — | — |
| 4b. Letter mentions (27) | Merging is declared (it needs reconciliation). Timing from the wording could become a converter option later. Letter-timed mentions were not a sound bound on when the finding happened; D30 fixed that (§4.2) | Converter (later) | — |
| 4c. Reconciliation (25) | Declared. The store never reads ground truth, so on the store counts and occurrences are over **recorded events**, and the page must say so | — | — |
| 4d. Medication courses (11) | Declared in D30. A record-only course rule could be a later converter option | Converter (later) | — |
| 5. Converter `series` | An option in `synthetic_ingest.py`, off by default (benchmark), on for parity and for the store under the page. Any edit to the converter changes `converter_sha256`, so every synthetic input digest and snapshot identifier changes even with the option off; table contents do not | Converter | — |
| 6. Rest of the inventory | Gene steps, the hedged free-text option, letter-wording times, index periods placed per patient, and unitless or other-unit values: page-only, refused by name in D30. "Coded records only" is a candidate small addition (§6) | Translator refusals | — |
| 7. Scope | One pull request, one six-hour regeneration (twice if a review must-fix lands), about 8 to 11 agent-days | | |

The engine and its profile stay `trajectory-graph-1.0`. The profile already says counts and measurement conditions are supplied by callers as derived concepts or events. Under this design the store derives Nth occurrences, counts and relative conditions the way D28 derives value tags.

## Facts measured for this note

These come from read-only scripts on the committed seed (48 children). They are analysis only: the scripts read the generator's truth to measure against it, and no design here reads truth.

- **Parity baseline.** Re-run here: 427 of 480 agree. The differences are coverage 19, letter mentions 27, medication records 11 and record reconciliation 25, with some patients having several causes.
- **Records against happenings.** The synthetic store holds 754 seizure-family events, against 215 true seizure happenings: 504 letter mentions, 172 EHR conditions and 78 registry entries.
- **"At least 3 seizures within 183 days".** Reached by 39 patients over the store's records (36 certainly), by 15 over coded records only (13 certainly), and by 11 over true happenings. So whatever the timing rules, counts over unreconciled records answer a different question, and letter mentions cause most of the inflation.
- **Order uncertainty.** Under recorded ranges, the rank of the first seizure in the engine's order is certain for only 23 of 43 patients, the 2nd for 20 of 42 and the last for 16 of 43. For febrile seizure the figures are 13, 12 and 4 of 24. Over coded records only, the first seizure is still certain for only 23 of 42: the EHR and the registry record one happening at different precisions. Seizure events have 273 adjacent pairs whose order is unknown, spread over 37 patients.
- **Measurements.** The helper's 3,484 series rows break down as 1,166 diary counts (`/month`, start ranges 27 to 30 days), 981 weights (kg), 983 heights (cm) and 354 head circumferences (cm). Each concept has one unit. 31 numeric records have no unit (12 weight, 12 height, 7 head circumference), and no value is non-numeric. Only 20 adjacent pairs of measurement events have an uncertain order.
- **What binds what.** `verification/execution-store-mimic-demo-report.json`, `execution-store-scaling-report.json` and `matcher-comparison-report.json` bind `patterns/execution_store.py`, `trajectory_graph.py`, `temporal_stn.py` and `execution_store_reference.py`. The scaling report also binds `synthetic_ingest.py` and `generate.py`. `tools/check_report_provenance.py` fails a report whose bound file changed. So any change to the store, the engine, the reference or the converter forces the six-hour regeneration (D28's run: demo 1,364 s, scaling 9,076 s, matcher main 2,506 s and confirmation about 4,800 s, plus arm A's builds). No report binds the mockup pages.
- **Synthetic identity.** `synthetic_provenance` records `converter_sha256`, which is part of the input digest. `PerBucketDeriveTests` pins the synthetic input digests in `patterns/fixtures/execution_store_content_digests_3_0.json`.

## A shared mechanism: derived step events

D28 compiles a value condition into a per-query tag concept that the engine treats as any other concept. D30 extends the same idea. The store computes **derived step events** per patient, after loading a chunk's events, and passes them to the engine as concepts or events. Tags live in no table, so snapshot content is unchanged.

There are two kinds:
- **Own-variable events** (Nth occurrence). A synthetic point event with its own time variable and a computed start range, carrying a tag. Its id lives in a reserved namespace, checked like `VALUE_TAG_NAME_TAKEN`.
- **Tags on existing events** (counts, relative conditions). The event that satisfies the condition lists the tag, so the step binds the record itself and shares its time variables. A tag has two strengths, *certain* (the condition holds under every choice of the patient's times) and *possible* (under some choice).

**The two-pass rule** for tags with strengths. A tag that is only possible cannot simply be handed to the engine: the engine would call a binding exact when the condition holds only under some times, and the choice that satisfies the condition might not be the one that satisfies the trajectory. So:

| Pass 1 (certain tags only) | Pass 2 (certain and possible tags), run only when pass 1 is not exact or possible and some tag that the query or any of its relaxation sets uses has an event that is possible but not certain | Result |
|---|---|---|
| `exact` | — | `exact`. A certain condition holds under every choice, so the binding is exact |
| `possible` | — | `possible`. No binding can be exact: an exact binding needs a certain condition, and pass 1 searched every certain event |
| `unresolved` | — | `unresolved` |
| `relaxed` | `relaxed` with the same relaxation set | `relaxed` |
| `relaxed` | anything else | `unresolved`, reason `STEP_CONDITION_UNCERTAIN`, with pass 1's relaxed match beside it (D18's convention) |
| `none` | `none` | `none`. Pass 2's candidates are a superset of every true binding's, so its refutation is sound |
| `none` | anything else | `unresolved`, reason `STEP_CONDITION_UNCERTAIN`, `uncertain_steps` listed, no match beside it |

Order of rules: engine passes, then this rule, then coverage (on `none`), then D18's excluded records. The rule is per patient and independent of chunks and workers. The parent and the workers both run it, because pass 2 runs where the events are loaded.
- *`complete`* is false if either pass's search was incomplete, and a stopped pass 2 makes the result `unresolved`. Notes from both passes are kept.
- *Occurrences on derived steps:* a `first`, `last` or `nth` occurrence on a step that carries a count or a relative condition is refused (`OCCURRENCE_ON_DERIVED_STEP`).

**Verification shared by all three features.**
- An independent reference in `execution_store_reference.py` computes the derived events and tags from the ingest, written again.
- A **semantic oracle**, extending D30's `trajectory_graph_worlds.py`: on small integer grids, a brute force enumerates every choice of the patient's times. It checks that:
  - each envelope equals the exact set of Nth-occurrence times;
  - every certain tag holds under every choice;
  - every possibly satisfied condition carries a possible tag;
  - every status the rule returns other than `unresolved` (`exact`, `possible`, `relaxed`, `none`, and the matches beside `unresolved`) agrees with the statuses defined by enumeration.
- Each feature has planted defects.

## 1. Nth and Nth-from-last occurrence

### 1.1 How the engine treats first and last today

`trajectory_graph.candidates` and `_Plan.candidates` sort the events carrying the step's concept by (recorded start range, recorded end range, id) and keep the first or the last. This is rule **R**: a record, chosen by a total order on recorded ranges, before the scope applies. D18 adds the displacement test: an excluded record whose `start_lo` is at or before the bound event's `start_hi` (for `first`) makes the result not comparable. D28 applies first and last among a tag's events.

The page does the same in its own way. `pickOccurrence` takes the Nth of the step's candidates sorted by `time.best.e`, the earliest time of the reconciled item's most precise record, and the engine then sees that candidate alone.

### 1.2 What rule R guarantees, and where it fails

Let the events carrying K have recorded start ranges [aᵢ, bᵢ] and true starts tᵢ in them. Write a₍N₎ and b₍N₎ for the N-th smallest aᵢ and bᵢ.

- **Envelope lemma.** The true N-th occurrence time, the N-th smallest tᵢ, lies in [a₍N₎, b₍N₎]. When the tᵢ are free, every integer in that range is attained. Proof: order statistics are monotone in each argument, so t = a gives a₍N₎ and t = b gives b₍N₎. Moving one tᵢ by one grid step moves the N-th order statistic by at most one step, so every value in between is reached.
- **For first, R is sound for `exact` and `none` on a time constraint, in a narrow case.** R picks the record A with a_A = a₍1₎ and b_A ≥ b₍1₎, so the true first time lies in [a_A, b_A]. Where A's variable is unlinked, a constraint that holds for every time in A's range holds at the true first time (`exact`), and one that holds for none fails there (`none`). *The review showed the limits:* this needs no relation to read the step's end (the true first may be another event, with another end), and no other step to bind A or an event of its concept. `last` was not symmetric before D30: it took the greatest earliest start, not the greatest latest. D30 fixed all three (decision log).
- **R over-claims `possible`.** A's range can reach past b₍1₎, the latest time the first occurrence can have. Example: A = [1 Mar, 31 Dec] and B = 5 Mar exactly. R binds A, so "the first seizure after April" is `possible`, yet the first occurrence is certainly on or before 5 Mar.
- **For N ≥ 2, R is unsound for `exact`.** Example: e₁ = [1 Mar, 31 Dec] and e₂ = 5 Mar exactly. R ranks e₂ second, so "the 2nd seizure before April" is `exact`. If e₁ in fact happened in November, the 2nd seizure is in November. The envelope gives [5 Mar, 31 Dec] and the answer `possible`, which is correct.

### 1.3 Options

- **R in the engine** (`trajectory-graph-1.1`, `occurrence: nth`, `n`). About ten lines, and `nth 1 ≡ first` byte for byte. *Declined*, because of the §1.2 counterexample.
- **Order-aware ranks with the two-pass rule** (a record that is certainly ranked N, or only possibly). Sound, and it keeps the record's identity. *Declined:* the uncertainty comes mostly from one happening recorded twice at different precisions, so on this cohort about half of all results would be `unresolved` (only 23 of 43 first seizures have a certain rank). The envelope handles that case well: for [1–31 Mar] and 14 Mar, the first occurrence is in [1 Mar, 14 Mar], so "the first seizure before April" is `exact`.
- **Envelope (recommended).** For each patient and each concept K the step can bind (its concept, each widening, and each D28 tag), add one derived point event `occurrence:<key>:nth:N` with start [a₍N₎, b₍N₎], its own variable and the tag as its concept, if K has at least N events. Nth from last uses the N-th largest. The engine's step has occurrence `any` on the tag.

### 1.4 Exact semantics (recommended)

- **The step's time** is the time of the N-th occurrence of K among the patient's recorded events carrying K (a D28 tag counts its events), N ≥ 1. It binds no record. Its evidence is the records whose range meets the envelope.
- **Ranges.** The ranges are the patient's tightened ranges (`Times.dom`), as in D30's envelope for `first` and `last`. True times lie in them, and they are tighter than the recorded ranges wherever the patient's facts say more (a proper exposure's start is bounded by its end).
- **`exact` and `none` are sound** for every patient, since every choice of times includes the true one.
- **`possible` is exact** when no other step (or widening) can bind an event carrying K and K's events share no variable. Otherwise the envelope's independent variable loses a correlation. Example: with two events, "the 2nd seizure at least 10 days before the last seizure" is impossible, but the envelope would call it possible.
  - *Coupling* is decided as in D30: another step whose concept an event of K carries, or a `first`, `last` or `nth` target that is one; or K's events sharing a variable.
  - *A coupled step is decided as D30 decides an unsafe `first` or `last` step.* A binding found proves nothing, so the result is `unresolved` (`OCCURRENCE_TIME_COUPLED`), with the binding beside it. If none is found, the search is repeated with every event that can be the Nth as the step's candidates, and only a `none` from that search stands.
- **Exposures.** The envelope is over starts. A step whose events have ends is still well defined as a time, but its end is not: the Nth exposure by start is an unknown record. So a `during` relation *from* an Nth step is refused (`NTH_STEP_HAS_NO_END`). Every other use takes the start.
- **First and last.** Since D30 they bind their event when it is certainly first or last, and use the same envelope otherwise, so `nth 1` and `first` agree wherever the envelope applies. The page translator still maps the page's "Nth, N = 1" to `first`.
- **Relaxations.** `widen_concept` and `widen_value` give the envelope of the wider K, with the same N. As with first and last today, the Nth of a wider concept does not contain the Nth of a narrower one, so no containment is required. The page offers no N ± 1 relaxation.
- **Excluded records (D18).** The Nth is not monotone: an extra record shifts ranks.
  - *Displacement:* an excluded record of K's concepts with `start_lo` ≤ b₍N₎ (for Nth), or `start_hi` ≥ the envelope's lower end (for Nth from last), could move the occurrence. Any result with a binding then becomes `not_comparable`, whatever the scope.
  - *`none`:* for Nth steps, reach ignores the scope. An excluded record before the window can shift an in-window event into rank N, so D18's scoped reach, which is sound for first and last, would be unsound here. This is a new finding; first and last are unaffected.
- **Candidates.** A plain concept needs posting `n` ≥ N, which is exact, since `n` counts the events the engine lists under the concept. A tag needs at least N tag rows (one SQL group). Over coded records only (change 5), the posting's `n` also counts mentions, so there it is a sound superset.
- **Pruning.** The envelope lies inside [`min_start_lo`, `max_start_hi`] of K's posting or tag summaries, so D25's argument holds unchanged.
- **Index period anchor.** Out of scope: windows stay an input (§6).

### 1.5 Location, identity, tests, cost

- **Where:** the store's match request (§7.1), compiled in `execution_store.py`, with the reference written independently. The engine is unchanged.
- **Identity:** no table changes. Editing `execution_store.py` changes the builder digest, so every snapshot identifier changes and the three heavy reports are regenerated (once for all of D30). The context adds the request's digest and rule names, as D28 did.
- **Tests:**
  - The reference differential on seeded fixtures with wide and overlapping ranges, exact ties, duplicates, excluded records before, inside and after the window, and shared variables, at 1 and 2 workers, both detail levels, with pruning on and off.
  - The oracle (envelope = exact range).
  - The demo differential extended with Nth queries.
  - Planted defects: N off by one; the record ranked N by `start_lo` (rule R), which the oracle and the §1.2 case catch; Nth from last counted from the front; scoped reach for `none`.
- **Cost:** about 1.5 agent-days with the request framework.

## 2. At least N times within a span or within the index period

### 2.1 The page today

`candidates` in `trajectory/app.js` works as follows:
- It sorts the reconciled items of the concept by `time.best.e`.
- For a span S, it takes every run of N consecutive items with `last.e − first.l ≤ S` (possibly within the span) and emits a point at the last item's range.
- For the period, the pool is the items that may overlap the window and the span is unlimited, so every item from the N-th onward is a candidate.
- The relaxation is N − 1 (at least 1), cost 0.5.
- The count's own timing never enters the network, so a count reached only under some times is treated as reached.

### 2.2 Exact semantics (recommended)

- **What the step binds.** A recorded event e carrying K (concept or D28 tag) at which the count is reached. The step's time is e's start, so e can also be another step's event and share its time, as any record can.
- **Timing.** For a choice of times t, the count is reached at e when at least N − 1 *other* events x carrying K satisfy t(e) − S ≤ t(x) ≤ t(e). Simultaneous events count. For the index period, the condition is w.start ≤ t(x) ≤ t(e) ≤ w.end. There the count is *certain* at e only if e's whole range lies inside the window and the sweep below also says certain.
- **Certain and possible**, exactly, when K's events have no shared variables. With τ ranging over e's range (cut to the window for the period):
  - forced(τ) = #{x : bₓ ≤ τ ≤ aₓ + S};
  - allowed(τ) = #{x : aₓ ≤ τ and bₓ ≥ τ − S};
  - the count is certain at e when min over τ of forced(τ) ≥ N − 1, and possible when max over τ of allowed(τ) ≥ N − 1.

  The sweep is O(m log m) per event set, and its exactness follows from independence at each fixed τ. With shared variables, a pairwise test on the patient's distances (`Times.gap`) gives a sound under-approximation of certain and over-approximation of possible. The rule names say which applied (`count_sweep_v1`, `count_pairwise_linked_v1`).
- **Statuses** follow the two-pass rule.
- **Distinctness** is by event id. Duplicates count separately: the store counts **recorded events**. On this cohort, that makes "3 within 183 days" true for 39 patients against 11 by happening (15 over coded records only). The store is exact for what it says, but the page must label it.
- **Relaxation** `widen_count` to N′ < N, compiled to a `widen_concept` to the N′ tag. Containment is exact (certain at N implies certain at N′, and the same for possible), so a value-style containment check holds. One relaxation per step, as in the engine: the page's combination of a parent concept with one fewer occurrence (cost 1) can be offered as one combined relaxation, though at equal cost it ranks before two-element sets, which the page does not (a documented difference).
- **Excluded records.** The count is monotone: more records only add counts. So `exact` stands; `possible` gets D18's note; `relaxed` becomes `unresolved`; `none` becomes `not_comparable`. Reach ignores the scope for span counts, since a witness need not satisfy the scope, and uses window overlap for period counts.
- **Coverage.** The step's recorders are K's (or the tag's), under the same overlap rule. Partial coverage weakens a count, which is a limitation the doc already lists for overlap.
- **Candidates:** posting `n` ≥ min N′ over the step's N and its `widen_count` relaxations, a sound superset. A filter on N alone would drop a patient that matches only with one fewer.
- **Pruning:** posting or tag summaries, which are sound.

### 2.3 Alternatives

- **Expanding a count into N engine steps.** *Declined:* it needs steps to bind distinct events, which is NP-hard (engine design §7.3) and is not offered.
- **SQL window functions over sorted events.** *Declined:* correct only when every time is exact.
- **Exact `possible` without `unresolved`,** by splitting e's range into the intervals where the count is possible, each a derived event. *Later:* measure the `STEP_CONDITION_UNCERTAIN` share first.
- **The page's consecutive-run rule.** *Declined:* it is not exact under uncertain times.

### 2.4 Location, identity, tests, cost

- **Where and identity:** as in §1.5.
- **Tests:**
  - The reference differential (fixtures as in §1, plus counts on the span's boundaries and N above the number of events).
  - The oracle (certain implies certain under every choice; possible under some choice implies the possible tag; the two-pass statuses are exact where the variables are unlinked).
  - Relaxation containment on every drawn relaxation.
  - Planted defects: the span's boundary off by one; e counted as its own witness; forced and allowed swapped; the period's containment test read as overlap.
- **Cost:** about 2 agent-days, including the two-pass rule, which §3 reuses.

## 3. Relative conditions (rises and falls)

### 3.1 The page today

`measureEvents` and `measureObservable` in `shared/core.js` work as follows:
- **Series.** One reading per reconciled happening (ground truth), numeric values only, any unit (units are ignored), sorted by earliest day with ties in insertion order.
- **Baseline.** For reading c at index i, take the readings at indices i − 2 and i − 1 and keep those whose earliest day is within 75 days of c's. If none is left, there is no baseline. Otherwise the baseline is the mean of the values kept.
- **Conditions.** A rise holds when v(c) ≥ f · max(1, base). A fall holds when v(c) ≤ max(10⁻⁹, base) / f.
- **Relaxation.** The factor becomes max(1.1, round(0.75 · f, 1 decimal)).
- **Observability.** A per-binding check that some reading meets [prev.e, prev.l + span], and, for a relative step, that a reading lies within 75 days before the earlier step. If no binding is found and the check failed, the page answers *not comparable*.

### 3.2 Decisions, one by one

- **Baseline.** Options:
  - the previous reading;
  - the mean of the previous k within W (the page: k = 2, W = 75 days);
  - the mean over a time window [t(c) − W, t(c));
  - the minimum over the previous W (KDIGO's AKI baseline);
  - the first reading in the period.

  *Recommended for D30:* the mean of the previous k within W, with k and W in the request (page: 2 and 75 days). The `baseline` object is extensible, so the window mean and the minimum can follow without a new profile.
  - *Per item class.* The baseline uses readings of c's own item class only. A condition on a grouping reads each item class separately, and never mixes, for example, non-invasive and arterial means.
  - *Strictly earlier.* A reading simultaneous with c is not previous, so a same-time duplicate never becomes the baseline. Ties at the k-th place are broken by event id.
- **Floor, explicit.** `floor` is a decimal in the condition's unit and must be greater than 0 (page: "1" for rises, "0.000000001" for falls). A positive baseline makes the factor relaxation's containment exact: v ≥ f·B implies v ≥ f′·B for f′ < f, and v ≤ B/f implies v ≤ B/f′. Without the floor, a negative baseline would reverse it.
- **Factor.** A decimal string greater than 1 (`INVALID_FACTOR`). `widen_factor` takes a smaller factor, still greater than 1, and containment is checked exactly.
- **Exact arithmetic.** v(c) ≥ f · max(φ, mean) is compared in rationals, on the values the D28 rule stores exactly (`value_micro`, or `value_decimal` where it is null). No float.
- **Uncertain times and ordering.** For c and a choice t, prev(c, t) is defined above. The tag is certain when the condition holds under every t and possible when it holds under some t.
  - *Computation.* When the configuration is *determined* (c's order against every reading that could be among its previous k is certain, and their W membership is certain), there is one baseline and an exact verdict. Otherwise the store enumerates the candidate configurations, at most C(m, k) for the m readings whose position is uncertain, which is small for k ≤ 3.
  - *Strength.* The tag is possible if any configuration satisfies the condition, and certain only if the configuration is determined and satisfies it.
  - *Exactness.* Certain is sound, possible is an over-approximation, and the two-pass rule makes both safe. The rule is named (`relative_previous_k_v1`), so the reference implements the same rule.
  - *Scale.* On MIMIC every time is exact, so only ties matter. On this cohort, 20 adjacent pairs of measurements are uncertain.
- **Units.** c must be in the condition's literal unit (D28). For the baseline, the recommendation is that a reading of the same item class in **another unit** among the previous k makes the baseline undetermined (possible only), rather than being skipped. The previous reading is the previous reading, whatever its unit: skipping it would silently reach further back.
  - This differs deliberately from D28's literal reading for absolute conditions, where another unit simply does not satisfy "value in U ≥ t".
  - The cost is small: only items with mixed units, of which neither cohort has any.
  - The alternative, "previous readings in the same unit", is also exact for what it says (§8, question 3).
- **Inexact and null values.** D28's rule applies: a null `value_micro` is compared on the exact decimal, and a value-less event carries no tag. A source that drops a value must record an excluded record.
- **Excluded readings (D18).** An excluded record of c's item class carries no value. If it could be strictly earlier than c, within W, and among the previous k, c's baseline is undetermined and the tag is possible only. This is computed inside the tag, from the patient's excluded records, so a bound reading whose baseline an excluded record could have changed never counts as certain. An excluded record that could itself be the rising reading is reached through D18's ordinary scoped reach (`none` becomes not comparable, and so on). This is sound. On the demo, where 78% of chart rows are excluded, most relative results will be not comparable or unresolved, as D18 predicts.
- **Coverage, and the page's "measurements do not cover the time around the earlier step".** The store keeps coverage and D18 as its comparability rules. The page's check is binding-specific and looks at only one binding of the other steps (`engineSteps`), so it is not a sound rule as written. *Declared page-only.* A sound version, "some binding of the other steps leaves the measured step's span uncovered", would need the bindings to be enumerated (later).
- **Store request or engine.** Store request. Relative conditions read values and units, which D27 decided the engine does not hold.

### 3.3 Location, identity, tests, cost

- **Where:** the store request, compiled after loading.
  - *Loading.* Matching needs a new load of the candidates' series rows for K's item classes, in any unit, plus their excluded records.
  - *Candidates.* Patients with at least two readings of an item class under K, which is a sound superset.
  - *Pruning.* The (K, U) series summaries, which are sound.
- **Identity:** as in §1.5.
- **Tests:**
  - The reference differential on fixtures with values on and beside every threshold, rational means (k = 2 and 3), null `value_micro`, mixed units within an item class, simultaneous duplicates, uncertain diary-like ranges, excluded readings before, among and after the previous k, and shared variables.
  - The oracle.
  - Factor containment.
  - Planted defects: k + 1 readings; a simultaneous reading counted as previous; the floor ignored; W off by one micro-unit; another unit skipped instead of undetermined; the mean in floats.
  - The demo differential.
- **Cost:** about 3 agent-days. This is the largest single part.

### 3.4 Staging

The design is sound and exact for the rule it states, but it is the largest part and has three decisions the owner should take: the baseline, the unit rule and the floor. Recommended plan:
1. Build it last in D30, under its own ship-only-if rule (§7.3). If it misses, it ships as a named refusal (`RELATIVE_CONDITION_UNSUPPORTED`) and the rest of D30 ships. Either way there is one regeneration.
2. Later: the window-mean and minimum baselines, absolute deltas (KDIGO's "+0.3 mg/dL within 48 h"), exact `possible` without `unresolved`, and unit equivalence (D28 stage 3).

The page's Sequence, Tree and Graph templates all use rises. If relative conditions are refused, the page on the store cannot run its headline templates and would fall back to the browser for them. Whether a refusal is acceptable for the default switch is question 8 in §8.

## 4. The four record-level parity differences

**4.1 Coverage (19): declared; the store's rule stands.**
- *The cause.* The store answers `NO_SOURCE_COVERAGE` when no source recording the diary count covers the period. The page searches the other steps and answers `none` if they do not match.
- *The page's answer is sound but incomplete.* Dropping a step only loosens the query. But the page searches the other steps unrelaxed only (`search(..., new Set())`, or `ask(rest, ids, false)`), so it answers `none` even when a relaxed match of them exists, and the full query's relaxed answer is then unknown.
- *A sound store refinement exists,* "partial refutation": on an uncovered step, run the query without it, with every relaxation set; `none` there gives `none`. *Not in D30.* It changes existing results (the synthetic differential's 2,107 `not_comparable`, the demo's figures), which would blur D30's exactness rule. Its own small increment later is possible (§8, question 4).
- *On the store, the page shows the store's answer,* and the in-browser fallback keeps its own rule, labelled.

**4.2 Letter mentions (27): merging declared; timing a later converter option; a soundness question.**
- *The cause.* The page merges a mention into the event it describes by its `truth_event_id`, which is ground truth, or times it from the wording.
- *Merging needs reconciliation* (4.3).
- *Timing from the wording* is a deterministic function of the record (`time_expression` and the letter's date), so it could become a converter option (`mention_time: wording_v1`). The page's windows ("about 3 weeks ago" read as 2 to 4 weeks before the letter; a 120-day fallback) are heuristics, though, and the option should say so.
- **Finding, fixed in D30.** The converter timed a mention *by its letter*. But the letter's date is when the finding was written down, not when it happened ("seizure about 3 weeks ago"). So the mention's recorded range did not contain the occurrence time, which the engine's soundness assumes (2,443 of the 2,444 missed). Since D30 a mention ranges from birth, or an earlier record, to the letter's latest time, and all 2,444 hold their true time.
  - Changing the converter's default changes the synthetic store's events, the 300 recorded match digests and the synthetic differential's figures.
  - The recommendation is not to change it inside D30, whose exactness rule depends on those fixtures staying put. Instead, state it in the store doc as a known unsound bound and decide it separately (§8, question 5).
  - The MIMIC route has no mentions.

**4.3 Record reconciliation (25): declared. The store never uses ground truth.**
- *Implications:*
  - On the store, first, last, Nth and counts are over **recorded events**. Duplicates of one happening count separately, and the earliest-starting record (often a coarser record of the same happening) is "first". The page's labels must say "recorded events".
  - A record-only reconciliation would be an inference: same concept family, overlapping times, different sources, perhaps expressed as shared start variables (D24) plus a happening key for counting. It would need its own evaluation against the generator's truth, with precision and recall measured and never used as input. That is a research item, not stage 2.
  - The page's ground-truth grouping stays a declared shortcut of the in-browser path.
- *Better parity evidence, optional:* a page **literal mode** that turns off the page's three shortcuts. In it, records are grouped by record, mentions are timed by letter, and medications run from order to stop, as the converter does. Parity in literal mode must then be 0 differences apart from coverage. That turns "declared" into "shown to be exactly the declared causes". It costs about 1 agent-day of JavaScript in `evidenceFor` (§8, question 7).

**4.4 Medication courses (11): declared in D30; a converter option later.**
- *Why not now.* The page's course rule links order, dispensings and stop through `truth_event_id`, so it is not record-only.
- *A record-only rule would be acceptable.* The dispensing-gap method links consecutive dispensings of one drug into a course when the next comes within `supply_days` plus a grace. It is deterministic from records and a standard pharmacoepidemiology method, so it could become a converter option (`medication: courses_v1`). Ending an ongoing course "at last contact or later" is an inference too, since an order that was never closed is not proof of use.
- *For D30:* declared. The store's order-to-stop exposures stand.

## 5. The converter emitting `series`

- **What it emits.** Each measured point event whose record has a number and a unit gets one series row: the value spelled as the cohort's JSON writes it, refused by name if that spelling is not a plain decimal. This is what `store_parity.with_series` does today, moved into the converter. The 31 measurement records *without a unit* become **excluded records** (reason `MEASUREMENT_WITHOUT_UNIT`) instead of value-less events, as the MIMIC route treats chart rows without a unit. That is D28's contract ("a source that drops a value must record an excluded record"). Value steps on weight, height or head circumference then become honestly not comparable where such a record is reachable, and the parity test's unitless-value difference is declared (inventory 11).
- **On by default.** Every synthetic snapshot changes in identity, in the series table (+3,484 rows per 48-patient cohort, about 0.42 rows per event row, about 72.6 million rows at 1,000,000 patients) and in the events and excluded tables (the 31 records), and so in size and build time (not measured). The scaling report's row counts would stop being comparable with D23 to D28. The matcher benchmark's queries carry no conditions, so their results would not move.
- **Off by default (recommended).** The benchmark builds the same tables. Parity, and the store under the page, set it on, and `provenance.series` records the rule only when it is on.
  - *Caveat:* editing `synthetic_ingest.py` changes `converter_sha256`, so every synthetic input digest and snapshot identifier changes even with the option off. The table content digests do not change.
  - *What moves:* the two synthetic input digests in `execution_store_content_digests_3_0.json` must be re-recorded (the tests still require every table digest). The scaling report is regenerated anyway. Arm A uses its own converter, so D27's arm A identifiers still tie the matcher evidence together.
  - *Alternative:* a sibling module (`patterns/synthetic_series.py`) wrapping `iter_patients` would leave every default identity untouched. The cost is that the converter lives in two files (§8, question 6).
- **Tests:**
  - The converter's rows equal `with_series`'s for every event that has a unit.
  - The 31 records become excluded records.
  - A non-plain spelling is refused.
  - The parity test switches from the helper to the converter.
  - The default build's tables are unchanged, with every recorded table digest reproduced.
- **Cost:** about 0.5 agent-day.

## 6. The rest of the inventory

| Inventory item | D30 | Why |
|---|---|---|
| 4. Measurements not covering the time around the earlier step | Page-only, declared | §3.2: the page's check is binding-specific; the store uses coverage and D18 |
| 5. Gene steps: first non-negative report, timed by recording, with reclassification | Page-only; refused (`GENE_CLASSIFICATION_UNSUPPORTED`) | Needs categorical values (D28 stage 3) and an event timed by `recorded_at`, a second time role |
| 6. Free text: coded only / stated / hedged | "Stated" is today's converter. "Coded only" is a **candidate addition**: a request-level `exclude_kinds: ["mention"]`, with the recorders recomputed from the remaining kinds by a scan like D28's tag recorders. Candidates and pruning stay sound supersets; first, last, Nth and counts read only the loaded kinds. "Hedged" is refused (`FREE_TEXT_HEDGED_UNSUPPORTED`; it needs a converter change) | Counts over coded records are much closer to the truth (15 against 39 records against 11 happenings) |
| 7. Reconciled events | Declared (§4.3) | No ground truth |
| 8. Medication courses | Declared (§4.4) | Later converter option |
| 9. Times from a letter's wording | Later converter option (§4.2) | Heuristic |
| 10. Index periods placed per patient by rule | Page-computed windows stay the input, as in the parity test. Server-side placement moves to stage 3 (job API), with the §1 envelope as the anchor rule for uncertain events | For real data nothing can reach the browser, so placement must move to the server then. On the synthetic cohort, windows still use the page's reconciled evidence (declared) |
| 11. Unitless or other-unit values | Refused by the literal unit (D28). Unitless synthetic records become excluded (§5) | Unit equivalence is stage 3 |
| Absence, alternatives | Neither side | — |

**Refusals live in a translator.** The store's request has no field for these features, so a page query cannot reach the store with them. A server-side translator from the page's definition to the store request does the refusing. It would be `demos/mockups/store_trajectory.py`, replacing `storeCase` in the test and serving the page. It refuses each feature by name, and the parity test asserts every code.

## 7. Proposed scope, order, acceptance rules and cost

### 7.1 Request

The D28 request is extended additively as `execution-store-conditions-1.1`, and a 1.0 request is answered identically. Per step there are optional fields:
- `occurrence: {nth: N} | {nth_last: N}`;
- `count: {at_least: N, within: {span: micros} | "window"}`;
- `relative: {kind: rise|fall, factor, unit, baseline: {previous: k, within: micros}, floor}`, alongside D28's absolute condition, which is not allowed on the same step;
- new relaxations `widen_count` and `widen_factor`.

Refusals are named, among them `OCCURRENCE_CONFLICT` (an engine `first` or `last` on the same step), `NTH_STEP_HAS_NO_END`, `INVALID_COUNT`, `INVALID_FACTOR`, `COUNT_RELAXATION_MUST_NARROW` and `RELATIVE_WITH_ABSOLUTE`. Output adds `derived_steps` (the rules applied); the context adds the request digest and the rule names; the store profile stays `execution-store-3.1`.

### 7.2 Order (one pull request)

1. Pre-committed rules, committed before any measurement (as D25 to D28).
2. The converter's `series` option and unitless records as excluded records (§5). Parity is re-measured with the converter.
3. The request framework and Nth (§1), with the D18 extensions.
4. Counts and the two-pass rule (§2).
5. Optional: "coded only" (§6).
6. The translator with named refusals; parity extended with Nth, from-last, both count forms and (if built) the three rise templates; the causes pinned; optionally literal mode.
7. Relative conditions (§3), under their own rule.
8. Docs: the D30 entry, the store doc, the README, the profile's note on derived events.
9. Regeneration: demo, scaling to 1,000,000 with `--keep`, arm A's builds, and the matcher comparison with the confirmation. An independent review follows; if a must-fix lands, the regeneration runs again, as in D28.

### 7.3 Pre-committed acceptance rules (proposed)

- **Exactness for requests without the new fields.** No result changes at any detail level:
  - the 300 recorded match digests are unchanged;
  - every per-query digest in the matcher comparison equals D28's, for every arm and size and in the confirmation;
  - the scaling report's row counts, candidate counts and status mixes equal D28's (the converter's option is off by default);
  - every demo figure other than identifiers, digests, timings and the new differentials equals D28's;
  - a 1.0 request's output is identical key for key, apart from the context's artifact digests.
- **Each feature ships only if all of these hold:**
  1. Store against reference: 0 mismatches (`json.dumps` without sorting keys) on seeded synthetic requests (at least 400 per feature, at 1 and 2 workers, both detail levels) and on the demo differential extended with the feature.
  2. The oracle finds 0 violations on at least 10,000 small-grid cases per feature.
  3. Each planted defect listed in §1 to §3 fails the differential or the oracle.
  4. Every drawn count and factor relaxation contains the stricter tag's events.
  5. Pruning on against off is byte-identical on the feature's differential.

  A failure is fixed, never tuned away. Relative conditions that miss ship as a named refusal.
- **Parity target.** Measured with the converter emitting `series`:
  - no `UNEXPLAINED` difference;
  - every difference's cause comes from a closed, declared list: coverage, letter mentions, record reconciliation, medication records, unitless values, and a new `count-timing` for the page's consecutive-run rule;
  - the replay holds for every trajectory, including derived steps;
  - every unbuilt inventory feature is refused by its code;
  - the table is pinned per trajectory and patient.
  - With literal mode, if adopted: 0 differences apart from coverage.
- **Regeneration.** One run of about 6 hours, one job at a time, or two runs (about 12 hours) if a review must-fix lands. The cost of the unchanged path is reported against D28. No performance gate is set for the new features; they are measured and reported (§8, question 9).

### 7.4 Cost

| Part | Agent-days |
|---|---|
| Converter `series` option | 0.5 |
| Request framework, Nth and its D18 extensions | 1.5 |
| Counts and the two-pass rule | 2 |
| Coded only (optional) | 0.5 |
| Translator, parity extension, refusals | 1.5 |
| Literal mode (optional) | 1 |
| Relative conditions | 3 |
| Docs, review fixes | 1 |
| **Total** | **8 to 11**, plus 6 to 12 hours of regeneration compute |

## 8. Decisions for the owner

1. **Nth semantics.** The envelope (recommended), rule R (unsound for N ≥ 2), or order-aware ranks (sound, but about half of results unresolved here)? *Decided in part by D30:* `first` and `last` now use the envelope where it is exact and are unresolved otherwise; D31 extends the same rule to Nth.
2. **Counts over recorded events.** Ship them as exact over records with the page labelling them "recorded events" (recommended), knowing they are inflated on this cohort (39 against 11)? Include "coded only" in D30 to reduce the inflation (15)?
3. **Relative conditions.** Include them in D30 under their own rule (recommended), or refuse them now? Confirm the baseline (mean of previous k within W, per item class, strictly earlier), the explicit positive floor, and the unit rule (another unit among the previous k makes the baseline undetermined, recommended; or "previous readings in the same unit").
4. **Coverage.** Keep the store's rule as a declared difference (recommended), or add sound partial refutation, which changes existing results?
5. **Letter mentions.** *Decided in D30:* a mention ranges from birth to its letter. Still open: whether to time them from their wording (heuristic, a converter option), which would make counts and Nth over mentions informative again (change 5 above).
6. **Converter `series`.** An option inside `synthetic_ingest.py` (recommended; synthetic identifiers move, tables do not), or a sibling module (identifiers kept)? Off by default (recommended)?
7. **Literal mode** on the page, as the evidence that declared differences are exactly the declared causes (+1 agent-day)?
8. **The default switch.** If relative conditions are refused, is a refusal acceptable for the page's three headline templates when the page moves onto the store by default?
9. **Performance.** No gate for the new features (recommended), or a ceiling on their per-candidate cost?
