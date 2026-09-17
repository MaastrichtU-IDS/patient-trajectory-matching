# Bounded interval-query editor

Start the research workspace with `python -m app.server` and open
`http://127.0.0.1:8080/temporal/editor`. The temporal demonstration also links to
**Edit interval constraints**.

The editor compiles form controls into `extended-interval-query-1.0` and executes
the existing interval solver. Both event slots remain fixed: an infusion and a
specimen collection. Two four-history authored fixtures are available. No clinical
data or terminology decisions are introduced.

## Try an edited question

1. Keep **Overlapping intervals** and **contains**, then evaluate. Infusion
   contains collection in every feasible timeline for T01 and in some timelines
   for T02. T03 has no recorded match; T04 has incomparable clocks.
2. Enable infusion duration and keep **10–10 minutes**. Enable minimum shared
   time and enter **3 minutes**, then evaluate again. The same event pair must
   satisfy all three constraints. T01 is now possible only: its shared time can
   range from two to four minutes. T02 cannot share three minutes and is excluded.
3. Set **Relaxation** to **Author one combined option**. Choose **Lower minimum
   shared time**, keep **2 minutes**, and admit cost **1.25**. T01 remains
   possible only in the original column and becomes certain in the edited-option
   column. Inspect both certificates, the compiled query, and explicit policy.
4. Change the maximum cost to **0** and evaluate. The option is shown as excluded
   by budget, with no relaxed classification or newly certain match. Download the
   complete report to reproduce either execution.
5. Turn relaxation off. Switch to **Sequential intervals**, choose **gap**, disable the two optional
   constraints, and enter **0–48 minutes**. T01 is certain, T02 possible only,
   T03 non-matching and T04 incomparable. This lower bound includes touching
   endpoints; the separate robust-widening demo instead requires a strictly
   positive gap.

## Controls and meaning

| Control | Executed meaning |
|---|---|
| One of 13 basic Allen relations | Infusion is the left interval; collection is the right interval. Before/after are strict on the existing one-microsecond grid. |
| Signed gap | Collection start minus infusion end lies in the inclusive range. Negative values are allowed. |
| Optional duration | Infusion end minus start lies in the inclusive positive range. |
| Optional minimum overlap | `min(infusion end, collection end) − max(infusion start, collection start)` is at least the positive minimum. |

All enabled predicates are conjunctive. The fixed-witness certainty rule remains
unchanged; the solver does not select a different event pair for each possible
timeline. A contradictory comparable constraint can exclude a pair even when a
different predicate has incomparable clocks. Incomparability alone is never
displayed as a known negative.

Minute values must be decimal **strings**, with no exponent and at most six
fractional digits. They are converted exactly to integer microseconds with
`Decimal`, not through floating point. Signed gaps range from −1440 to 1440
minutes; duration greater than zero to 1440; minimum overlap must be greater than zero and
at most 1440. Six decimal places in minutes provide a 60-microsecond control
resolution; this does not change the solver's one-microsecond time grid.

## API and reproducibility

`GET /api/editor` returns the admitted fixtures, relations, default controls,
limits, the option cost, budgets and source fingerprints. `POST /api/editor/run`
accepts these required fields and an optional `relaxation` field:

```json
{
  "fixture": "overlap",
  "relation": "contains",
  "gap": null,
  "duration": {"minimum_minutes": "10", "maximum_minutes": "10"},
  "minimum_overlap_minutes": "3",
  "relaxation": {
    "max_cost": "1.25",
    "gap": null,
    "duration": null,
    "minimum_overlap_minutes": "2"
  }
}
```

For `gap`, supply `gap: {"minimum_minutes":"0","maximum_minutes":"48"}`.
For other relations `gap` must be null. Disabled duration and overlap controls
must also be null. Unknown fields, numeric JSON values, malformed decimals,
reversed bounds and unsupported relations are rejected. Requests cannot provide
source paths, event classes, arbitrary queries or arbitrary relaxation catalogues.

Omit `relaxation` or set it to null for the original query only. Otherwise all four
nested fields above are required. `gap` and `duration` accept the same minute-range
shape as their original controls. Each selected range must strictly widen its
original; duration remains positive. A selected overlap minimum must strictly
decrease and remain positive. A metric can only change if present in the original
query. At least one change is required. All selected changes are one explicit
option, `edited-option`, with fixed demonstration cost `1.25`. There is no implicit
composition of alternatives, change to an Allen predicate, or clinical cost model.
The budget must be the string `0` or `1.25`. All changes are validated even if the
budget excludes the option.

`GET /api/editor/export/<report_id>` downloads the executed controls, canonical
query, policy, source, original and option solver reports, patient classifications
and artifact hashes. `result` and each patient's `status` always describe the
original question; `relaxation` contains all admitted evaluations. `option_status`
is null when no option was requested or it was excluded. `selected_option` and
`selected_cost` identify the least-cost certain match, preferring the unchanged
original at cost zero. Histories without any certain evaluation have no selected
match. Replay uses the shared command:

```sh
python -m app.temporal_replay temporal-analysis.json
```

Replay recompiles validated controls on the local admitted fixture. It never
executes the supplied source or query. It compares the entire report, so edited
results or an alternate source fail even if the outer hash is recomputed.
Use the same checkout and fixture for reproduction.

## Limits and acceptance

Before solver preparation, admission checks at most four patients/episodes,
eight events, sixteen endpoint variables, five clocks, two slots, three
constraints, one option and four candidate products per evaluation. At most two
complete evaluations run, including the original; the source is prepared once.
There is no automatic relaxation. These are bounded-work checks, not a latency
or memory guarantee. Source fixtures are read once at startup and copied per run.
The sixteen latest complete reports remain in memory; evicted exports return
404. Existing request-size, same-origin and serialized-workspace guards apply.

Editing any input clears earlier results, evidence, query JSON and download
links. Disabled controls are omitted through null values. Controls are locked
during execution. Blocked or incomplete execution produces no successful export.

```sh
python -m unittest app.test_interval_editor
node app/test_editor_ui.cjs
```

Acceptance tests execute every Allen relation through HTTP, check metric
translation and signed gaps, independently evaluate returned witnesses and
counterexamples for the three-conjunct query, and verify exports, rejection,
limits and retention. Relaxation tests cover combined changes, budget exclusion,
original-first selection, invalid changes before source preparation, incomplete
option execution and tampered report replay. The actual UI script is exercised with engine-produced
responses. CI also runs the earlier workflows and container smoke route checks.
Visual/accessibility review remains unverified; the cloud browser could not reach
the local server when the preceding temporal demo was developed.

This is a two-slot editor over authored snapshots. Arbitrary event selection,
additional slots, disjunction, state coverage, source uploads, multiple editable
catalogue options and clinical deployment remain open. The PR #48 draft semantics is
not adopted by this interface.
