# Recorded treatment and measurement evidence in the guided journey

The `/journey` page now connects to the existing recorded-pressure pipeline. It shows recorded input segments, eligible baseline measurements, optional follow-up measurements, their arithmetic differences, missing follow-up and the underlying source evidence. A separate selector profile applies the existing reviewed measurement mappings through Rust reasoning.

## Run

Install the pinned Python dependencies following [validation](validation.md), then start the application from the repository root:

```sh
python -m app.server --port 8080
```

Open `http://127.0.0.1:8080/journey` and use the recorded-evidence section. Both default selector profiles use the authored synthetic records in `examples/source-mixed-query`. The recorded population has four patients and five stays. It is distinct from the T01–T04 interval-uncertainty population above it; identifiers, similarity rankings and eligibility are not transferred between these datasets.

Choose the reviewed measurement category, run the default query, then inspect a matching segment. The default baseline threshold is strictly below 65 mmHg, within 30 minutes before segment start; follow-ups are selected within 120 minutes from segment start. Baselines are strictly before the anchor, while the follow-up window includes offset zero. Follow-up records need not remain within the recorded segment.

The authored example produces three matching patients and three eligible baseline pairs. Patient 1 has baseline 58 mmHg and follow-ups 68 and 72 mmHg, with differences +10 and +14 mmHg. Patient 2 has baseline 60 mmHg and no selected follow-up. Patient 3 has baseline 62 mmHg and follow-up 55 mmHg, a difference of −7 mmHg. All five stays remain visible, including the two without an admitted anchor. A missing follow-up stays missing; it is neither zero change nor evidence of clinical absence.

## Selector profiles

| Profile | Selection | Evidence |
|---|---|---|
| Literal source item | Fixed recorded input item 1000 and measurement item 2000 | Source records, accepted source claims and temporal/value witnesses |
| Reviewed measurement category | Same recorded input item; the reviewed pressure-family class selects supported measurement item 2000 | Source evidence plus mapping decisions, class/concept identifiers, mapping context and actual Rust semantic check |

The default mapping is an authored fixture decision, not verified clinical equivalence. It is a one-way implication from a source record class to the reviewed measurement category. The service checks the supported item and exact unit against the admitted query. Pending, withdrawn, stale or unsupported mappings block the reviewed path; they do not fall back to literal selection. The client chooses from the configured profiles and strata, and cannot submit arbitrary classes, item IDs, units, mappings or source paths. This bounded selector does not add ontology classes to the separate configurable interval builder.

## Supplied recorded data

The guided application accepts the existing [configured pressure service](configured-pressure-service.md) contract at startup:

```sh
python -m app.server --recorded-config examples/configured-pressure-service/config.json --port 8080
```

This supplied example is also authored synthetic data. It activates only the reviewed recorded profile, with measurement item 2001 and narrower defaults of 15 minutes before and 60 minutes after segment start. The configured label, selected item and permitted windows appear in the page. Configured recorded sources require a loopback bind. Configuration paths resolve relative to the configuration file; HTTP callers cannot change them. Real supplied records require their own bounded source-fidelity declaration and reviewed mapping pack under the existing contracts.

Source, mapping, review and implementation checks remain in force for every job, including cache hits. Detected changes prevent new completed membership from being published. Previously completed jobs remain available as historical evidence while retained; each underlying profile retains at most three jobs. The [recorded query-by-example and replay workflow](recorded-query-by-example.md) adds downloadable evidence exports that remain available after this in-memory history expires.

## API and interpretation

| Route | Purpose |
|---|---|
| `GET /api/journey/recorded/config` | Configured profiles, strata, controls and selectors |
| `POST /api/journey/recorded/jobs` | Start a bounded query with exactly `profile`, `stratum` and `controls` |
| `GET /api/journey/recorded/{profile}/jobs/{id}` | Poll completion and read verified cohort summary |
| `GET /api/journey/recorded/{profile}/jobs/{id}/anchors/{token}` | Inspect source treatment, measurement observations, witnesses and mapping evidence |

Example request:

```json
{"profile":"reviewed","stratum":"synthetic","controls":{"threshold":"65","baseline_minutes":30,"followup_minutes":120}}
```

Only completed, independently checked queries expose a cohort summary. The existing complete-result cache and prepared queries remain in use; results are checked against the independent anchor SQL calculation. Inspection preserves source value strings, units, timestamps, record provenance, claim hashes and review decisions. The presentation adds baseline/follow-up pairs without recalculating or rounding their engine-produced differences.

Recorded input segment starts do not establish treatment-course initiation. The displayed differences are observations around those starts, not treatment effects. Clinical mapping, source history and physical elapsed time retain their explicit unverified status. The [recorded query-by-example workflow](recorded-query-by-example.md) declares a bounded baseline-measurement feature profile for ranking peers. Its distance does not establish clinical similarity.

## Verification

```sh
python -m unittest app.test_recorded_journey_http
python deploy/smoke.py --url http://127.0.0.1:8080
```

The HTTP acceptance tests execute both real selector profiles, check source values and missing follow-up, verify the Rust mapping evidence, retain prior results across changed controls, and exercise the configured source and request boundaries. Deployment smoke runs and inspects an authored reviewed query. Interface state checks cover the matching page controls; they do not establish visual browser verification.

The pressure-service import changes preserve the existing function and class bodies while allowing use from both the demo launcher and application package. Historical benchmark timing samples remain retained; their artifact hashes and derived contexts are updated for these import-only changes. Regression checks verify behavior, and no new clinical benchmark is claimed.
