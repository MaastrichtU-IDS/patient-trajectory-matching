# Interface mockups

Exploratory interfaces for clinical researchers defining and matching patient trajectories, for design discussion. Nothing here is product code, a supported profile, or a clinical claim; the product is the research workspace in [`app/`](../../app/). The test data is a synthetic paediatric rare-disease cohort.

```sh
python demos/mockups/synthetic/generate.py                 # writes demos/mockups/data/ (git-ignored)
python -m http.server 8090 --bind 127.0.0.1 --directory demos/mockups
# from another machine: ssh -N -L 8090:127.0.0.1:8090 <user>@<host>, then open http://localhost:8090/
```

Opening the HTML files directly will not work; the pages fetch the dataset over HTTP.

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
2. **Trajectory.** Each step is an event found by searching name or code in any terminology the table holds (ICD-10, ICD-10-CM, SNOMED CT, ORPHA, OMIM, HPO, ATC, LOINC, HGNC, local); the concept also matches its narrower concepts through every coding, and its kind decides the condition. A finding or diagnosis is any occurrence, the first, the last recorded, the Nth, the Nth from last, or *at least N times* within a span or within the index period (the step then happens when the count is reached). A medicine or class takes the same choice of start. The index period's event takes first, last, Nth or Nth from last too. Occurrences are counted over reconciled events across the whole record, so a seizure entered by two sources counts once; "last" means last recorded and moves as follow-up grows. A gene is the first report that is not negative. A measurement (the seizure diary's monthly count, weight, height, head circumference) is a condition on the series: rises or falls by a factor against the mean of the one or two measurements before, or crosses a threshold. Timing constraints between steps are *starts between X and Y after* or *happens while taking*, optionally up to some time after stopping, each in its own unit. The page detects the shape and says what it means: a **sequence** (each step has at most one before and one after), a **tree** (branches from one root with no order between branches), or a **graph** (a step after several others). Cycles, dangling references and "while taking" on something that is not a medicine are rejected with a plain message. Sequence, Tree and Graph templates load worked examples.
3. **Matching patients.** Every patient is searched. Each step is bound to one of the patient's events, and the constraints are solved jointly as a simple temporal network: a step's time is bounded by its event's recorded range, a medicine also has an end, and constraints bound the differences. A binding is *possible* if some choice of times inside the recorded ranges satisfies every constraint at once, which is checked exactly with shortest paths, and *exact* if every choice does. Results are exact, possible, relaxed with a declared cost (a broader concept, one fewer occurrence, the same drug class, a smaller change or a looser threshold, or a 1.5× longer gap), no recorded match, not comparable (the measurable steps match but the measurements cannot answer the rest), or no index period. Result types can be shown together; *no recorded match* and *no index period* are hidden until chosen, and *no recorded match* is then shown on its own. The detail drawer tags each bound step on the patient's timeline and gives a verdict for each constraint.

### Shortcuts these mockups take

- Records are grouped into events using the generator's ground truth. A real system would have to infer the grouping; the inspector says so on every event.
- Data-quality cases are known because they were injected. With real data they would be detections, and some would be missed.
- Event frequency counts recorded events, which are only the notable ones (an ED visit, a letter). Day-to-day frequency is in the diary's monthly counts, a measurement, so "4 seizures in a month" is a measurement condition here, not an event count. A real system would need both and would have to say which it used.
- SNOMED CT codes were added for a handful of concepts to make search across terminologies concrete. Like every code here they are illustrative and unverified.
- Trajectory search is backtracking with a budget of 40,000 attempts per search. A patient whose unrelaxed search exhausts it without a binding is *Unresolved*, shown by default, not a non-match; a found binding or a non-match that a stopped search leaves open carries a note saying what may have been missed. Relaxations are tried one or two at a time within a total cost of 1, and "exact" uses a per-constraint check over the recorded ranges, which is sound but can call a jointly certain binding merely possible. Absence ("no X between A and B") and alternatives ("A or B") are not yet expressible.
- Similarity weights are equal and the distance rules are simple. They are placeholders for design discussion, not a proposal. Clinical findings and medication exposures can be compared by simGIC (the default), ancestor-Jaccard or best-match Resnik, as in the workspace engine. Information content comes from every patient's whole record in the 48-patient synthetic cohort, not from a searched database, and does not change with the window.
- Layout is three columns at 1280 px and wider, stacked below. The narrow-screen tab layout, measured accessibility checks and browser testing beyond Chromium are not done.

## Deployment

The `Dockerfile` serves these pages with nginx and regenerates the dataset during the build, so the generated file is never uploaded. No hosted copy is currently running; the Cloud Run service `ptm-mockups` was removed on 4 November 2026. To host it again behind IAP:

```sh
gcloud run deploy ptm-mockups --source demos/mockups --region us-central1 --project dumontier-charm --iap --no-allow-unauthenticated
```

Then grant `roles/iap.httpsResourceAccessor` to the people who should see it.
