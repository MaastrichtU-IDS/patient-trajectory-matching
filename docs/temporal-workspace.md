# Temporal uncertainty in the research workspace

Run `python -m app.server --host 127.0.0.1 --port 8080` and open
`http://127.0.0.1:8080/temporal`, or follow **Temporal uncertainty** from the
patient-to-cohort page. Use the existing pinned Python 3.12 dependencies.

The page executes the merged `bounded-interval-query-1.0` and
`robust-temporal-relaxation-1.0` contracts through the actual executor. It uses
a separate, authored four-patient fixture, not the kidney cohort or clinical
records. Its minute-scale example must not be confused with the 48-hour clinical
example in the draft semantics proposal. No decisions in PR #48 are adopted.

## Two-minute narrative

1. **0:00–0:30 — Ask a precise temporal question.** Keep widening set to **None**
   and click **Evaluate all four histories**. “We want an infusion followed by
   collection, with a strictly positive completion-to-start gap of at most 48
   minutes. Uncertain timestamps remain uncertain during matching.”
2. **0:30–1:10 — Separate possible from certain.** Inspect **T02**. “Its actual
   gap can be 47–49 minutes. This satisfying timeline makes it possible; this
   counterexample prevents certainty. Certainty needs the same named event pair
   to work in every feasible source timeline.” The cards convert the returned
   certificate assignments into minutes; the full integer-microsecond proof and
   PRO/SOLID provenance remain available below.
3. **1:10–1:40 — Make the relaxation explicit.** Select **Allow 50 minutes · cost
   1.25**, then rerun. “Now T02 has a certain option at cost 1.25. T01 keeps its
   original zero-cost option. We changed the question, not the source or its
   uncertainty.” The original classifications remain in the table.
4. **1:40–2:00 — Explain the boundaries.** Inspect **T04**. “No accepted clock
   alignment means incomparable, not non-matching. T03 has no represented match
   even with widening. The result includes source records, options and evidence;
   download it to reproduce the calculation.”

| History | Completion-to-start gap | Original 48-minute question | Budget 1.25 |
|---|---|---|---|
| T01 | 29–31 minutes | Certain | Original, cost 0 |
| T02 | 47–49 minutes | Possible only | 50-minute widening, cost 1.25 |
| T03 | 59–61 minutes | No recorded match | No certain option |
| T04 | No admitted inter-clock comparison | Incomparable | No certain option |

The lower gap bound is one microsecond on the existing integer grid, so touching
endpoints do not satisfy this query. The widening preserves that bound. The
catalogue contains exactly one modification, and costs are policy penalties,
not probabilities or clinical utility. The executor's `possible_patient_ids`
includes certain patients; the page explicitly displays **possible only** as the
difference for this one-episode-per-patient fixture.

## API, replay and execution limits

| Route | Contract |
|---|---|
| `GET /api/temporal` | Source fingerprint, original query, catalogue and workload limits; no matching |
| `POST /api/temporal/run` | Exactly `{"budget":"0"}` or `{"budget":"1.25"}` |
| `GET /api/temporal/export/<report_id>` | Retained complete report with source, query, catalogue, results and hashes |

The same origin checks, strict JSON parsing, 8 KiB body limit and workspace lock
as the kidney workflow apply. Paths, sources, queries and catalogue changes are
not admitted from requests. Inputs are loaded at startup and copied per run;
changing fixture files requires a restart. Cached exports are defensive copies.

Before the solver runs, the server checks at most four patients/episodes, eight
events, sixteen variables, five clocks, zero extra source constraints, two query
slots, one query constraint and one catalogue option. It counts the full slot
Cartesian products within patient/episode scopes: at most four candidate pairs
per evaluation and two evaluations. These are admission limits, not truncation,
a wall-time guarantee, a memory measurement or evidence of clinical scalability.
The fixed fixture has one episode and one candidate pair per patient.

A blocked or incomplete evaluation publishes no successful report. The UI clears
results, inspected evidence and export links on budget change, new evaluation or
failure. Controls are disabled while execution is pending. Two complete results
are retained in process memory; unavailable exports return 404. Restart loses
saved reports. The original kidney workflow and this temporal workflow have
separate results and exports; neither filters the other.

```sh
python -m app.temporal_replay temporal-analysis.json
```

Replay recomputes both the original and permitted variant on the locally admitted
fixture and compares the complete report. Supplied source/query inputs are not
executed. Rehashing edited results, sources or artifact fingerprints cannot make
them pass. Reproduction requires the matching fixture and implementation; hashes
are integrity evidence, not publisher signatures.

## Validation and remaining scope

```sh
python -m unittest app.test_temporal app.test_server
node app/test_temporal_ui.cjs
node app/test_ui.cjs
```

The temporal HTTP test runs both budgets, checks witness/counterexample arithmetic
against source bounds and the window independently of returned labels, verifies
clock mismatch and non-match evidence, and downloads and replays both reports.
Other checks cover invalid requests, blocked execution, pre-execution limits,
defensive copies, retention and rehashed export tampering. The JavaScript test
uses actual engine-produced responses and checks state, escaping and failures.
CI uploads `verification/research-prototype-run/temporal-journey.json` alongside
the existing research-journey evidence. Container smoke includes the new page,
execution and download routes.

Visual browser review could not be completed in the authoring environment because
the cloud browser blocked access to the local server. DOM-state and HTTP tests
are not a visual or accessibility audit.

This increment exposes a fixed bounded-gap question. General editing of Allen,
duration and overlap predicates, unified extended-profile relaxation, clinical
selector integration, state-coverage UI, and the integrated OWL/GFO runtime remain
future work. Source and mapping reviews remain independent admission gates.
