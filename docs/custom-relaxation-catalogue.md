# Custom relaxation catalogue

The `/journey` two- or three-event trajectory builder can evaluate a small catalogue of explicitly permitted metric changes. The original query remains visible and is always evaluated at cost zero. Options change the question, never the authored T01–T04 source records, baseline ranking or eligibility. Every eligible patient except the reference is evaluated, including patients outside the nearest-neighbour preview.

## Authoring and limits

Select **Build a two- or three-event trajectory**, define the original pattern, then select **Enable custom relaxation options**. Under **Targets permitted to relax**, permit individual metric constraint IDs. Set **Maximum changed targets per option**, use **Add relaxation option**, give each option an **Option ID** and **Cost (0–100)**, and use **Add change to this option** to select a **Permitted metric target** and enter its relaxed bounds. Set **Maximum relaxation cost (0–100)** for the run. **Validate and view query** displays the canonical query and policy preview.

| Rule | Accepted scope |
| --- | --- |
| Options | At most three, with distinct IDs; `original` is reserved. |
| Changes | One to six distinct targets per option; at most six permitted targets. |
| Changed-target limit | Integer 0–6, applied to each complete option. Zero excludes every option that changes a target. |
| Cost and budget | Decimal strings from `"0"` to `"100"`, with at most six fractional digits. These are authored policy costs, not clinical measurements. |
| Gap or duration change | Supply both minimum and maximum minutes. The lower bound cannot increase, the upper bound cannot decrease, and at least one bound must strictly widen. Duration bounds remain positive. |
| Minimum-overlap change | Supply only a strictly smaller, positive minimum. |
| Protected constraints | Allen relations cannot be relaxed. Metric targets must be explicitly permitted. Slots, event kinds, constraint operators and source records cannot be changed by an option. |

Minute bounds retain the builder limits: signed gaps within −1440 to 1440, duration and minimum overlap within the positive range up to 1440, and at most six fractional digits. Compilation converts decimal minutes exactly to integer microseconds. Duplicate targets, unknown fields, stale constraint IDs, no-op changes and tightening changes are rejected.

The whole catalogue is validated before filtering by cost or changed-target limit and before source preparation. A malformed or protected change is rejected even at budget zero. Valid excluded options remain in the report with no evaluated status. The existing `excluded_by_budget` field covers both the cost budget and the changed-target limit.

Each declared option is applied independently to the original query. There is no automatic combination of options and no sum of costs across separate options. To evaluate two changes together, declare one option containing both changes and its own cost. An empty catalogue is valid and evaluates only the original, even with a nonzero budget. Omitting `catalogue` preserves the earlier custom API behavior: the budget must be `"0"`.

## Reproducible example

T01 has an infusion lasting exactly 10 minutes and a contained specimen collection with uncertain shared duration between 2 and 4 minutes. The following original question requires an infusion of exactly 9 minutes, containment, and at least 3 shared minutes. It has no recorded match for T01. Widening duration to 9–10 minutes makes T01 possible, but not certain. Lowering overlap alone leaves the 9-minute duration mismatch. Only the explicitly declared combined option is certain.

Save this as the body of `POST /api/journey/run`:

```json
{
  "reference_patient_id": "T03",
  "top_k": 1,
  "maximum_baseline": null,
  "question": "custom",
  "budget": "1.25",
  "pattern": {
    "slots": [
      {"id": "infusion", "event_kind": "infusion"},
      {"id": "collection", "event_kind": "specimen_collection"}
    ],
    "constraints": [
      {"id": "contains", "operator": "contains", "left": "infusion", "right": "collection"},
      {"id": "duration", "operator": "duration", "slot": "infusion",
       "minimum_minutes": "9", "maximum_minutes": "9"},
      {"id": "shared", "operator": "minimum_overlap", "left": "infusion", "right": "collection",
       "minimum_minutes": "3"}
    ]
  },
  "catalogue": {
    "relaxable_targets": ["duration", "shared"],
    "max_changed_targets": 2,
    "options": [
      {"id": "duration_only", "cost": "0.5", "changes": [
        {"target": "duration", "minimum_minutes": "9", "maximum_minutes": "10"}
      ]},
      {"id": "overlap_only", "cost": "0.5", "changes": [
        {"target": "shared", "minimum_minutes": "2"}
      ]},
      {"id": "combined", "cost": "1.25", "changes": [
        {"target": "duration", "minimum_minutes": "9", "maximum_minutes": "10"},
        {"target": "shared", "minimum_minutes": "2"}
      ]}
    ]
  }
}
```

| T01 evaluation | Cost | Budget `"1"` | Budget `"1.25"` |
| --- | --- | --- | --- |
| Original | 0 | No recorded match | No recorded match |
| `duration_only` | 0.5 | Possible | Possible |
| `overlap_only` | 0.5 | No recorded match | No recorded match |
| `combined` | 1.25 | Excluded | Certain; selected |

Budget `"1"` produces no robust patient match: although both cheaper options fit individually, the engine does not combine them. Budget `"1.25"` adds T01 as a robust match. T04 is incomparable under the duration-only and combined options because its collection clock is unaligned; its original and overlap-only evaluations have no recorded match because the duration mismatch still holds. The original result is identical at both budgets. Patient-level `NO_RECORDED_MATCH` is distinct from `INCOMPARABLE`; the solver's impossible bindings remain available in the evidence.

A three-event version can add a `followup` specimen-collection slot and a gap constraint from `collection` to `followup`, with minimum `"0"` and maximum `"30"`. Keep that gap protected. The same T01 option outcomes hold; the two collection slots select distinct recorded events. Deployment smoke exercises this version.

## API, evidence and replay

`POST /api/journey/catalogue` accepts exactly `{"pattern": ..., "catalogue": ..., "budget": "1.25"}` and returns `{"query": ..., "policy": ...}`. It validates the entire catalogue without executing a cohort. `POST /api/journey/run` accepts the same catalogue only when `question` is `"custom"`. Existing compile/decompile endpoints continue to round-trip the original pattern and query. Catalogue authoring does not admit arbitrary source paths, files or query profiles.

The results table keeps **Original**, **Option results**, **Least-cost certain match** and **Evidence** separate. Each patient has an `option_results` entry for every declared option: `option_id`, `cost`, `status` (or null when excluded), `excluded_by_budget` and `selected`. `selected_option` and `selected_cost` describe the least-cost certain match, including `original` when appropriate. A possible option is never selected as a robust match.

Selection orders certain matches by numeric cost. Equal costs prefer the original, then option ID, then deterministic binding and episode ordering. Catalogue display order is not a user-assigned priority. Certainty requires one fixed option and one named binding to satisfy the query throughout every feasible source timeline; it cannot switch options or bindings between timelines. Possibility is not a probability.

Export retains the submitted catalogue, compiled policy, original result, all evaluated option results, exclusions, source evidence and solver witnesses/counterexamples. `GET /api/journey/export/<report_id>` downloads the exact retained completed report, including after another run changes the current controls. Reports remain in bounded server memory; download before a restart. Verify a downloaded report with the same admitted checkout and dependencies:

```sh
python -m app.temporal_replay patient-journey.json
python -m unittest app.test_relaxation_catalogue_http app.test_pattern_builder_http app.test_journey_http
python deploy/smoke.py --url http://127.0.0.1:8080
```

Replay recomputes the report, including all options and exclusions. HTTP acceptance checks the budget comparison, no implicit composition, full eligible cohort, original/source preservation, malformed and protected changes at budget zero, original-only compatibility, and retained export replay. Existing same-origin, JSON body, body-size and bounded-workload limits still apply.

This is a bounded synthetic research prototype. It does not calibrate costs clinically, establish treatment benefit or observational outcomes, extract real records, support arbitrary vocabularies or branching trajectories, or model context-dependent costs. Multiple explicit fixed-cost options are supported; broader cost models and clinical calibration remain future work.
