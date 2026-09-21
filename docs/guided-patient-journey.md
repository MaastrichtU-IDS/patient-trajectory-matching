# Guided patient-to-cohort journey

The application route `/journey` connects reference-patient selection, a temporal question, an explicit relaxation, source explanation and replay in one bounded workflow. It uses the same authored T01–T04 population throughout. The reference is excluded from the candidate cohort. The separate P00–P10 kidney example is not joined to these histories.

Start the application after the [dependency setup](../README.md) and open <http://127.0.0.1:8080/journey>:

```sh
python -m app.server --host 127.0.0.1 --port 8080
```

Use the [2–3-minute presenter script](../demo/GUIDED_JOURNEY.md). Docker/Compose and Helm packaging are described in [deployment.md](deployment.md); this route uses the existing application server.

## What this journey demonstrates

Select T03 as the reference, retain the full baseline eligibility range, and display one nearest baseline neighbour. Baseline proximity orders the preview; it does not establish temporal membership and its display limit does not truncate temporal evaluation. All eligible candidates are evaluated. The comparison is an authored demonstration, not a validated clinical similarity model.

The overlap question asks for a ten-minute infusion that strictly contains a specimen collection, with at least three minutes of shared time. A separately declared option reduces only the shared-time minimum to two minutes, at an illustrative cost of 1.25. Budget 0 excludes that option; budget 1.25 admits it. The cost is neither clinical risk nor probability. Choosing the alternative sequential question keeps the same patient records and source times. The fixed journey source also includes a second specimen collection at minutes 20–22 for each patient, so the sequential preset can match that later collection.

For T01, the infusion is fixed at minutes 0–10. Collection starts between minutes 3 and 4 and ends between minutes 6 and 7. Its shared time can therefore be as short as two minutes or as long as four. Three minutes is possible, but not guaranteed. Two minutes holds throughout the admitted uncertainty. The relation and duration stay unchanged, and the original result remains visible.

T04 uses unaligned clocks for the two events. A looser shared-time threshold does not align those clocks: the temporal relation remains incomparable. “No recorded match” is a statement about the admitted records and query, not proof that an event never happened.

Observed infusion and specimen-collection records can be inspected. Clinical outcomes are unrecorded in these authored histories, so the application must not invent response, survival or treatment-effect estimates.

The [configurable trajectory builder](configurable-trajectory-builder.md) adds custom questions with 2–3 event slots and 1–6 Allen, gap, duration or minimum-overlap constraints. Custom questions evaluate the original query with budget 0 and preserve the same source histories, reference exclusion and full eligible evaluation pool.

## HTTP and replay contract

`GET /api/journey` describes the admitted population, question presets, limits and default controls. Execute a complete analysis with:

```http
POST /api/journey/run
Content-Type: application/json

{"reference_patient_id":"T03","top_k":1,"maximum_baseline":null,"question":"overlap","budget":"1.25"}
```

`GET /api/journey/export/<report_id>` downloads the completed report. It contains the reference selection, complete eligible cohort, baseline preview, query, original and relaxed classifications, source evidence and execution provenance. Reports have format `patient-journey-export-1`. Retention is bounded and in memory; download before restarting the server or evicting the report with further runs.

Save the downloaded JSON as `patient-journey.json` and replay against the same admitted checkout and dependencies:

```sh
python -m app.temporal_replay patient-journey.json
```

Replay recomputes the admitted analysis and compares the complete report. A verified replay prints `verified: true` and exits 0; a refusal prints `verified: false` with a reason and exits 2, matching the contract-failure convention used by the interval profiles. The importable `verify()` raises instead, which is what its callers expect. A digest alone does not establish that an altered report is valid. Changing the implementation or authored fixtures can require a fresh export.

## Acceptance checks

```sh
python -m unittest app.test_journey_http app.test_pattern_builder_http
python deploy/smoke.py --url http://127.0.0.1:8080
```

The HTTP tests exercise the live loopback server: reference exclusion, full eligible-pool evaluation despite a one-row neighbour preview, preservation of the original answer, explicit budget admission, witness arithmetic, evidence/export/replay and malformed/cross-origin/oversized request rejection. The deployment smoke retains existing application checks and additionally executes and exports the guided journey.

The implementation remains a bounded synthetic research prototype. Patterns beyond the bounded 2–3-event builder, custom relaxation policies, validated patient similarity, clinical outcomes, real-data extraction and target-cluster operation are separate work.
