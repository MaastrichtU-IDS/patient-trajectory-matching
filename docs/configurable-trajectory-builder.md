# Configurable trajectory builder

Open `/journey`, select **Build a two- or three-event trajectory**, and define a temporal question with two or three event slots. Event types are infusion, specimen collection and recorded input segment. Queries use the same authored T01–T04 histories as the presets; these histories contain no recorded input segments, so selecting that type yields no recorded match. Slot names identify roles in the question; they do not add or alter patient records. Every slot requires a distinct recorded event, including a slot that has no temporal constraint. Repeated event types are supported, but records cannot be reused: three specimen-collection slots have no recorded match in these histories, which contain only two collections per patient.

Choose a reference patient and baseline eligibility limit, then add constraints between the slots. The nearest-neighbour display limit controls the preview only: the query evaluates every eligible patient except the reference. The reference defaults to T03. With no baseline filter, T01, T02 and T04 are evaluated even when the preview shows one neighbour.

## Controls and temporal meaning

A pattern contains 2–3 slots and 1–6 constraints. Constraints are combined with AND. The builder uses the existing `extended-interval-query-1.0` query format and executor.

| Constraint | Controls | Meaning |
| --- | --- | --- |
| Allen relation | Left slot, operator, right slot | One of `before`, `meets`, `overlaps`, `starts`, `during`, `finishes`, `equals`, `after`, `met_by`, `overlapped_by`, `started_by`, `contains`, `finished_by`. |
| Signed gap | Left slot, right slot, minimum and maximum minutes | Right start minus left end, within inclusive bounds. Negative values permit overlapping intervals. |
| Duration | One slot, minimum and maximum minutes | End minus start, within inclusive positive bounds. |
| Minimum overlap | Left slot, right slot, minimum minutes | Shared time must be at least the positive minimum. |

Metric values are decimal minute strings, with at most six fractional digits. Gaps range from −1440 to 1440 minutes; duration and overlap values must be positive and at most 1440. Bounds must be ordered. Query export converts these values exactly to integer microseconds. AST import requires the canonical builder query ID (`authored-configurable-trajectory-query`) and integer times that are multiples of 60 microseconds, the exact resolution of six fractional minute digits. Allen relations retain their strict/equality semantics: for example, `contains` requires both boundaries of the inner event to lie strictly inside the outer event, while `meets` requires equal adjoining boundaries.

Use the compiled query display to inspect the AST. Compile/decompile preserves supported patterns and produces canonical query JSON; unsupported classes, malformed ASTs, unexpected fields, dangling slot references and values outside the builder's bounds are rejected. This builder does not admit arbitrary source records or arbitrary query profiles.

Custom patterns can execute the original query alone or use an [explicit custom relaxation catalogue](custom-relaxation-catalogue.md) with up to three fixed-cost options. Metric constraints must be individually permitted before an option can strictly weaken their bounds; Allen relations remain protected. Options are evaluated independently against the original, with no implicit combination. Without a catalogue, set budget to the string `"0"` to retain original-only behavior. The two preset questions retain their separately declared relaxation options.

## Three-event example

Use these slots and constraints:

```json
{
  "slots": [
    {"id": "infusion", "event_kind": "infusion"},
    {"id": "collection", "event_kind": "specimen_collection"},
    {"id": "followup", "event_kind": "specimen_collection"}
  ],
  "constraints": [
    {"id": "c1", "operator": "contains", "left": "infusion", "right": "collection"},
    {"id": "c2", "operator": "gap", "left": "collection", "right": "followup",
     "minimum_minutes": "0", "maximum_minutes": "30"}
  ]
}
```

The authored histories each include a second specimen collection at minutes 20–22. With T03 excluded as the reference, this query makes T01 certain and T02 possible. T04 remains incomparable because its earlier collection uses an unaligned clock. Certainty means that one named, distinct-event binding satisfies every constraint throughout all feasible source timelines. Possibility means at least one feasible source timeline satisfies a binding; it is not a probability.

Change the gap maximum from 30 to 1 minute and rerun. No eligible patient then has an established possible match. T04 remains incomparable; the unaligned clocks do not establish a negative result. The source records, baseline ranking and eligibility remain the same. Changing a question or its constraints never manufactures a different patient history.

## HTTP, export and replay

`GET /api/journey` supplies the population, presets, builder metadata, defaults and execution limits. The builder endpoints use JSON objects:

| Endpoint | Request | Response |
| --- | --- | --- |
| `POST /api/journey/compile` | `{"pattern": <form pattern>}` | `{"pattern": <canonical form>, "query": <canonical AST>}` |
| `POST /api/journey/decompile` | `{"query": <supported AST>}` | `{"pattern": <canonical form>, "query": <canonical AST>}` |
| `POST /api/journey/catalogue` | `{"pattern": <form pattern>, "catalogue": <catalogue>, "budget": <decimal string>}` | `{"query": <canonical AST>, "policy": <validated policy>}` |
| `POST /api/journey/run` | Complete journey request below | Completed analysis report |
| `GET /api/journey/export/<report_id>` | No body | The completed report as a JSON attachment |

Run a custom pattern by sending the example above as `pattern` in this complete request:

```json
{
  "reference_patient_id": "T03",
  "top_k": 1,
  "maximum_baseline": null,
  "question": "custom",
  "budget": "0",
  "pattern": {
    "slots": [
      {"id": "infusion", "event_kind": "infusion"},
      {"id": "collection", "event_kind": "specimen_collection"},
      {"id": "followup", "event_kind": "specimen_collection"}
    ],
    "constraints": [
      {"id": "c1", "operator": "contains", "left": "infusion", "right": "collection"},
      {"id": "c2", "operator": "gap", "left": "collection", "right": "followup",
       "minimum_minutes": "0", "maximum_minutes": "30"}
    ]
  }
}
```

Invalid requests return HTTP 400. The existing same-origin, JSON content-type, body-size and bounded-workload checks apply. Candidate bindings are bounded at 32 per evaluation, counted before rejecting bindings that reuse an event; this limit rejects excessive work instead of truncating answers.

Reports use `patient-journey-export-1` and include the submitted request, canonical pattern and compiled query, full eligible cohort, baseline preview, source evidence, per-patient classifications, solver evidence and execution provenance. Download retained reports before restarting the server. Replay the downloaded report against the same admitted checkout and dependencies:

```sh
python -m app.temporal_replay patient-journey.json
```

Replay recomputes the complete report; recomputing a digest does not make an edited export valid. The fixed source extension and implementation changes require fresh exports from this version.

## Acceptance checks and boundaries

```sh
python -m unittest app.test_pattern_builder_http app.test_relaxation_catalogue_http app.test_journey_http
python deploy/smoke.py --url http://127.0.0.1:8080
```

HTTP acceptance covers a three-event run, a changed cohort after editing a constraint, full-pool evaluation beyond the baseline preview, reference exclusion, unchanged source across custom and preset questions, metric/query round trips, explicit catalogue outcomes and budget exclusions, export replay and rejected malformed forms/ASTs/catalogues. Deployment smoke retains the existing routes and preset checks and adds custom compile/run/decompile/export and catalogue checks.

This remains a bounded synthetic research prototype. It does not provide clinical similarity validation, observed treatment outcomes, real-data extraction, arbitrary event vocabularies, branching patterns or clinical cost calibration. Custom relaxation is limited to explicit fixed-cost metric options; broader cost models remain future work. The admitted events are authored records; collection is not an efficacy outcome.
