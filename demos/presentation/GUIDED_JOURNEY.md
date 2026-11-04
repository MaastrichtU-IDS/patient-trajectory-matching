# Patient-to-cohort demo: 2–3 minutes

Open <http://127.0.0.1:8080/journey> using the [runbook](../docs/guided-patient-journey.md). The numbered guide button sets reference **T03**, the overlap question, no baseline restriction, **one** highlighted neighbour and budget **0**. Click it again to execute the original question, then advance through the remaining steps. The page is a live analysis over four authored histories.

| Time | Action | Presenter cue |
| --- | --- | --- |
| 0:00–0:25 | Select T03 with the first guide step. | “Starting from this patient, we ask which other recorded histories meet a temporal question. The reference is excluded. Showing one nearest neighbour does not limit the population we evaluate.” |
| 0:25–0:55 | Evaluate the original question. Inspect the baseline preview and T01’s original result. | “T02 is closest at baseline. We nevertheless evaluate every eligible history. We require a ten-minute infusion containing a specimen collection, with at least three minutes shared. T01 is a possible match: some timelines satisfy the question, while others do not.” |
| 0:55–1:25 | Admit the explicit option with budget 1.25 and run again. | “This option reduces only the shared-time requirement from three minutes to two. T01 now qualifies with certainty across the recorded uncertainty. Its original possible-only classification remains visible. We changed the question, not the records.” |
| 1:25–1:55 | Expand T01’s explanation and source evidence. | “The infusion runs from minute zero to ten. Collection starts between three and four and ends between six and seven. Shared time is between two and four minutes. These source bounds explain both answers; example timelines and detailed certificates can be inspected.” |
| 1:55–2:15 | Inspect T04 and observed-event/outcome summaries. | “T04 remains incomparable because the event clocks are not aligned. Relaxation does not repair that uncertainty. We can show the recorded infusion and collection, but this fixture does not record clinical outcomes.” |
| 2:15–2:40 | Download the completed report. | “The export preserves the selected reference, full eligible cohort, original question, explicit option, results and evidence. We can replay it against the same implementation.” |

If asked to prove reproduction, save the download as `patient-journey.json` and run:

```sh
python -m app.temporal_replay patient-journey.json
```

A successful replay prints `"verified": true` and exits 0. A refused replay — an export that does not match the admitted fixtures and this implementation — prints `"verified": false` with a reason and exits 2, rather than failing with a traceback. For an alternate question after the timed demo, select the sequential gap preset. It asks about the same recorded histories, so changing the question can remove matches; it does not replace their timelines. Do not switch questions midway through the overlap explanation.

The take-away is an explained, reproducible cohort decision. The synthetic baseline score, option cost and temporal certainty must not be presented as clinical similarity validation, treatment benefit or statistical confidence.
