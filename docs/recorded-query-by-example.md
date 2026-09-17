# Recorded query by example and replay

The recorded-evidence section of `/journey` can select a reference patient and recorded input segment, rank other patients on a declared baseline feature, and retain their treatment and measurement evidence in a replayable export. This uses the recorded source population and its reviewed selector; it does not transfer the separate T01–T04 interval demonstration's rankings or identifiers.

## Run the example

Start the application with the pinned dependencies installed:

```sh
python -m app.server --port 8080
```

Open `http://127.0.0.1:8080/journey`, choose the reviewed measurement category and run the default recorded query. Choose patient 2's segment as the reference and highlight one peer. Patient 2 has a baseline value of 60 mmHg. Patients 1 and 3 have values of 58 and 62 mmHg: both are 2 mmHg away, so the deterministic patient identifier tie-break highlights patient 1. Patient 3 remains in the full ranked result. All stays belonging to the reference patient are excluded from the peer pool.

Inspect patient 1's recorded follow-ups of 68 and 72 mmHg and patient 3's follow-up of 55 mmHg. Patient 2's missing follow-up remains explicit in its reference evidence. Patient 4 has no admitted reference anchor and cannot be selected as a reference. Missing features and unmatched stays remain visible in the roster.

These authored records demonstrate the mechanics. A one-measurement distance is not a validated clinical similarity measure, and observed changes around a recorded segment start are not treatment effects.

## Declared feature and ranking

The feature profile selects the latest finite measurement of the admitted item and exact unit strictly before each recorded input segment start, within the configured baseline window. Feature selection does not use the query's value threshold or any follow-up measurement. The threshold still controls the recorded query's matching cohort; it does not become a similarity feature. A same-time tie is resolved deterministically using the measurement identifier.

Distances use exact decimal absolute differences in mmHg. Each patient contributes their closest eligible segment to the reference; ties are deterministic. The reference patient's other stays are excluded as well as its selected segment. `top_k` controls highlighting, not query membership, source inspection or whether other ranked patients are retained. Measurements with missing or incompatible feature data are not silently replaced with zero.

Only source timestamps are available for this calculation. Being recorded before a segment does not establish that a measurement was available to a clinical decision maker at that historical time. The interface and export retain this limitation along with the source and mapping review status.

## HTTP contract

| Route | Request or result |
|---|---|
| `GET /api/journey/recorded/{profile}/jobs/{id}/references` | Reference segments, declared feature profile and full roster for a completed recorded query |
| `POST /api/journey/recorded/compare` | Exactly `profile`, `job_id`, `reference_token`, `top_k` |
| `POST /api/journey/recorded/export` | Exactly `profile`, `job_id`, `reference_token`, `top_k`; returns a JSON attachment |

A comparison uses the opaque reference token supplied by the references route. Callers cannot provide source files, feature values, alternate units, mapping classes or arbitrary ranking weights. Job and reference identifiers are validated and scoped to their recorded profile. Requests enforce the same origin and bounded JSON admission rules as the existing recorded query routes.

Export without a comparison uses `null` for both `reference_token` and `top_k`. Export with a comparison uses the selected token and an admitted positive integer `top_k`. Export retains the completed query, source and review context, observed evidence and, when selected, the comparison. Download it before its in-memory job expires: the service retains up to three jobs per profile, and a server restart removes that history.

## Replay

```sh
python -m app.temporal_replay recorded-query.json
```

Replay recomputes the recorded query and optional comparison against the locally admitted source and implementation. The report fingerprint detects accidental edits, while replay also rejects a changed result whose fingerprint has been recomputed. An exported report is an evidence record, not permission to run arbitrary embedded source paths or queries. Source and reviewed mapping changes must be addressed through their normal admission process rather than hidden by replay.

The default command supports the authored sources. Configured-source replay requires explicitly supplying the admitted startup configuration; it does not silently replace supplied records with the authored demonstration:

```sh
python -m app.temporal_replay recorded-query.json --recorded-config examples/configured-pressure-service/config.json
```

The export is capped at 8 MiB and contains evidence and source fingerprints, not a copy of the source files. Replay needs those admitted local records and the matching implementation.

## Verification

```sh
python -m unittest app.test_recorded_qbe_http
python deploy/smoke.py --url http://127.0.0.1:8080
```

HTTP acceptance runs the actual recorded engine, checks deterministic ties and patient-wide exclusion, tests threshold/follow-up isolation, verifies exports and replay, and exercises request boundaries. Deployment smoke runs the authored reviewed query, compares its reference patient and downloads the evidence export. Interface state tests are separate from visual browser verification.

## Browser rehearsal

The automated browser attempt to open the local `/journey` page was blocked with `net::ERR_BLOCKED_BY_CLIENT`; visual browser verification is outstanding. Rehearse the following locally:

1. Run the default reviewed recorded query and select patient 2, whose baseline is 60 mmHg.
2. Compare with `top_k` set to one. Patient 1 (58 mmHg) is highlighted; patient 3 (62 mmHg) remains listed. Both distances are 2 mmHg, and patient 4 remains unresolved.
3. Inspect the peer observations and the reference's missing follow-up. Increase `top_k` and confirm only highlighting changes.
4. Change the value threshold or follow-up window, rerun, and confirm those controls change temporal membership/observations without entering the baseline feature calculation.
5. Download the recorded export and run the replay command above. Restart the server and replay the saved file again against the same checkout and admitted inputs.
