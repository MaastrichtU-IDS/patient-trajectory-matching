# From one patient to a cohort

A guided **2 minute 35 second** demonstration. The audience follows one investigation and makes one small choice. The presenter advances five stops; nothing advances automatically.

## What to demonstrate

1. **A patient history becomes an explicit trajectory pattern.** Clinical meaning, order, elapsed time, measured change and admission scope all matter.
2. **Entailment gives an exact match.** DrugAChild satisfies DrugA through the declared taxonomy, at zero relaxation cost.
3. **Refinement changes a real candidate cohort.** Ten distinct synthetic patients are evaluated live. The included cohort grows **3 → 5 → 6**, with new members and their costs visible.
4. **Boundaries and uncertainty stay visible.** A hard numeric threshold remains fixed. An incomplete source search remains unresolved.
5. **Every inclusion has evidence and a reproducible context.** Inspect actual PRO/SOLID bindings, unit normalization, source records and the exported query/snapshot/results.

The guided path uses the existing point-anchor exemplar and named-class taxonomy. General OWL reasoning, bounded-time uncertainty, and the separate Rust example are available in the technical view for questions after the demonstration.

## Prepare

From the repository root:

```sh
python3 demo/serve.py
```

Open **http://127.0.0.1:8765**. This guided path requires Python 3.10+ and no optional packages. Matching runs in Python on each query; the PRO/SOLID graphs were validated and projected when the dataset was built.

Use a desktop browser and keep the terminal running. Click **Restart**, then start the **Presenter timer** when you begin speaking. The bottom guide keeps the next action available on desktop. Its **Presenter narrative** disclosure contains the current script; leave it closed when projecting and use these notes separately. Rehearse once in the presentation browser.

Keep `demo/Guided_Cohort_Demo.html` available as the offline backup. Opening it directly uses a visibly labeled replay of recorded results. If the live server fails, the live page preserves the previous view and reports the failure; switch explicitly to the backup if needed.

## Run of show

### 0:00–0:25 · Start with a patient

**On screen:** Reference P00; Drug A at −7 days; baseline 1.00 and follow-up 1.30 mg/dL, 24 hours apart. Point to the pattern and the fixed numeric rule. The reference is excluded from the ten candidate patients.

**Say:**

> Start with one patient's history. Drug A was administered seven days before this follow-up. Creatinine rose from 1.0 to 1.3 within twenty-four hours. We turn that history into an explicit pattern: the right exposure, the right order, and a measured change within a defined window. Can we find patients who satisfy it?

**Click:** **Find the exact cohort**.

### 0:25–0:55 · Match meaning and time

**On screen:** **3 / 10 included**, all exact. P02 is selected and its DrugAChild subtype explanation is visible.

**Say:**

> Three of the ten candidates match exactly. Notice P02: the record says Drug A Child. The declared taxonomy establishes that it is a kind of Drug A, so this remains an exact match at zero cost. The matcher also checks the same patient, the admission, the laboratory window, and the order of exposure and follow-up.

**Click:** **Allow a cost of 1**.

### 0:55–1:30 · Broaden with a reason

**On screen:** **5 / 10 included**: 3 exact and 2 relaxed. P04 and P05 are marked **NEW**. P04 is initially selected.

**Say:**

> Now we allow one unit of relaxation cost. Two patients enter. Which shall we inspect: the alternative drug, or the longer time gap? P04 uses the explicitly permitted Drug B alternative, costing one. P05 has a nine-day gap, two days beyond the original limit, also costing one. These satisfy a deliberately broadened query; they remain visibly separate from exact matches.

**Interact:** Take one quick audience choice. Use **P04 · alternative** or **P05 · longer gap** in the explanation panel. Show the cost breakdown. If nobody chooses, click P05. Allow about five seconds for the choice; do not start a general discussion yet.

**Click:** **Test the boundaries**.

### 1:30–2:05 · Keep the boundaries visible

**On screen:** **6 / 10 included**. P06 enters at cost 2; P07 is selected, showing the hard numeric failure. One patient remains unresolved.

**Say:**

> A budget of two admits P06, which needs both changes. But P07 still does not qualify: its rise is only 0.29, and we have not permitted that threshold to change. P10 is different again. Its source search is incomplete, so membership is unresolved. We preserve that distinction instead of treating missing evidence as a negative result.

**Interact:** Click **P10 · unresolved** when you mention it. If time permits, inspect **P06 · both changes** first to show 1 + 1 = 2.

**Click:** **Inspect evidence & save**.

### 2:05–2:35 · Explain it. Reproduce it.

**On screen:** P03's evidence opens. Its original baseline **8.0 mg/L → 0.80 mg/dL** supports a rise of 0.40 mg/dL. Scroll the history if necessary; the next action stays available.

**Say:**

> For any included patient, we can inspect the actual records behind the match. Here, eight milligrams per litre becomes 0.8 milligrams per decilitre. The graph preserves the process, its patient role, the person bearing that role, and the source. Finally, we save the query, the selected cohort, and the evidence needed to reproduce this result.

**Click:** **Export cohort & query**. The downloaded file contains six selected IDs, all ten candidate results, the query, taxonomy, original synthetic rows, manifests, PRO/SOLID bindings and fingerprints.

## Rehearsal checkpoints

| Budget | Exact | Relaxed | Included | Unresolved | Not selected |
|---|---:|---:|---:|---:|---:|
| 0 | 3 | 0 | 3 | 1 | 6 |
| 1 | 3 | 2 | 5 | 1 | 4 |
| 2 | 3 | 3 | 6 | 1 | 3 |

P00 is never counted. Included IDs are P01–P03, then P04/P05, then P06. P07 fails the numeric rule; P08 has no baseline within 48 hours; P09 exceeds the maximum nine-day exposure window; P10 remains unresolved.

The maximum cost is a budget over explicit query modifications, not a similarity probability. Drug A and Drug B are synthetic concepts and their substitution is an illustrative policy. No causal effect, clinical equivalence, or validated AKI phenotype is claimed.

To reproduce a downloaded result:

```sh
python3 demo/cohort.py --verify-export /path/to/trajectory-cohort-budget-2.json
```

This checks the query and dataset fingerprints, requires the same oracle implementation, re-evaluates all ten candidates and compares the complete results. The same command verifies an explicitly labeled offline replay export.

If you need a **two-minute cut**, omit the audience choice, keep P04 selected, mention P10 without opening it, and show only the unit normalization before export. If you have **three minutes**, inspect P06's two cost components and expand the full source binding. Keep the broader technical examples for questions.
