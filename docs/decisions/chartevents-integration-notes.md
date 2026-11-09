# Chartevents route (b): integration notes for after D26

The branch `feature/chartevents-route-b` adds only new files:

| File | Contents |
|---|---|
| `patterns/merkle_patients.py` | Merkle tree over patients |
| `patterns/mimic_chartevents.py` | streaming reader, admission, claim stores, bulk rows |
| `patterns/mimic_chartevents_check.py` | sampler, route (a), row differential, sampled run |
| `patterns/prepared_reference.py` | engine inputs once per patient |
| `patterns/chartevents_fixture.py` and `examples/mimic-chartevents/` | synthetic fixture |
| `patterns/test_merkle_patients.py`, `test_mimic_chartevents.py`, `test_prepared_reference.py` | tests |

It edits nothing the D26 agent owns. The hooks below are written against main 40adf3d. Re-read each against D26's version before applying it, since D26 moves the D5 fields into typed columns, adds an evidence table and end summaries, and switches to typed staging.

## 1. `patterns/execution_store.py`

1. **The patient contract.** In `validate_patient`, allow an optional `series` key: a list of `{event_id, concept, value_lexical, value_decimal, value_micro, unit}`. Refuse each of the following with a named code:
   - an `event_id` that is not one of the patient's events (`SERIES_UNKNOWN_EVENT`);
   - an event that is not a point (`SERIES_EVENT_NOT_POINT`), since stage 1 holds points only;
   - a `concept` that is not among the event's direct concepts (`SERIES_CONCEPT_MISMATCH`);
   - two rows for one event (`DUPLICATE_SERIES_EVENT`);
   - a `value_lexical` that `measurement_claims.decimal_value` refuses;
   - a `value_decimal` other than `format(Decimal(value_lexical), 'f')`;
   - a `value_micro` other than the exact ×10⁶ integer within BIGINT, or null (`INVALID_SERIES_VALUE`);
   - an empty unit, or one with surrounding spaces (`INVALID_SERIES_UNIT`).

   The canonical patient sorts `series` by `event_id`, so the patient digest, and with it the input digest, covers the series.
2. **Staging.** Add a staged kind `series` per part, beside events and event concepts (in D26's typed staging, one more typed batch). Copy `start_lo` and `start_hi` from the event at `add()`. Count series rows toward `part_rows`.
3. **Derive.** Add a table `series` with columns `patient_idx, event_id, concept, start_lo, start_hi, value_lexical, value_decimal, value_micro, unit` (BIGINT, VARCHAR, VARCHAR, BIGINT, BIGINT, VARCHAR, VARCHAR, BIGINT, VARCHAR):
   - partitioned by bucket and joined to the patient index, part by part like `events`;
   - written as `series/bucket=k/`;
   - content-digest key `patient_idx, concept, start_lo, start_hi, event_id`.

   Add it to `TABLES`, the type check, the per-bucket content digests, the manifest's table list and `files`, and `schemas/execution-store-manifest.schema.json`, if it enumerates tables.
4. **Derive-time check.** Each series row's `(patient_idx, event_id)` exists in `events` with the same `start_lo`/`start_hi`, and `concept` is one of its `event_concepts` with basis `direct`. This holds by construction; the check guards the copy.
5. **Accessor.** `Store.series(patient_id, event_ids)` returns the series rows for display and audit. `match` is unchanged in stage 1.
6. **Input digest (open question 1).** If the Merkle form is adopted:
   - `INPUT_FORM` becomes `merkle-patients-1 buckets=8`;
   - compute it from DuckDB's `ORDER BY bucket, patient_id` over the staged patient list, feeding each bucket's `(patient_id, digest)` pairs to `merkle_patients.StreamingRoot` and the eight roots to `root_of`, then `input_digest(header_bytes, ...)`. Equivalently, call `merkle_patients.input_digest(header, pairs, bucket_of=bucket_of, buckets=8)` when the pairs fit in memory;
   - record the bucket roots in the manifest, inside the identity, so an append can be checked bucket by bucket.

   If it is not adopted, keep D20's flat digest and record only the root.
7. **Profiles.** Bump the store profile, since the new table changes identity. On the ingest profile, the D25 reasoning applies: `series` is optional and additive, and older code refuses it by name (`INVALID_PATIENT`). Settle it together with D26's own profile change.
8. **D26 typed columns.** The chartevents event's `evidence` holds `claim_id`, `claim_sha256`, `record_id`, `source_record`, `source_key`, `record_status`, `selection_state`, `recorded_at_us`, `available_at_us`, `source_table`, `source_record_number`, `source_row_sha256` and `source_file_sha256`. Map these onto D26's typed columns under the same names inputevents uses. The rest goes to the evidence table: `episode_id`, `clock_*`, `concept_bases`, `acceptance_policy_id`, `origin_source_record_id`, `history_mode` and the verification flags. If D26 renames any of them, change `mimic_chartevents.store_row` and nothing else. The graph check compares whatever `store_row` emits.

## 2. `patterns/execution_store_reference.py`

- Add `series` to `COLUMNS` and build it in `build_tables` independently: one row per series entry, start copied from the event, and the same key in `table_digests`.
- `patient_digest` covers `series` sorted by `event_id`.
- If the Merkle form is adopted, `input_digest` calls `merkle_patients.input_digest(head_bytes, pairs, bucket_of=bucket_of, buckets=BUCKET_COUNT)`. The module is pure and shares no code with the store.

## 3. `patterns/mimic_store_ingest.py`

1. **Combined conversion.** In `convert(input_dir, dataset_id, workers, isolation, chartevents=False)`, when `chartevents` is set, build `mimic_chartevents.Run(input_dir, dataset_id)`. The icu directory holds `chartevents.csv.gz`; `importer.source_paths` ignores it.
2. **Closure.** Take the union of the inputevents closure (`_closure`, kind-class ontology ancestors included) and `Run.ancestors`/`Run.bases`. The namespaces are the same, `ITEM_NS` and `GROUP_NS`, and a test already checks `mimic_chartevents.GROUP_NS == msi.GROUP_NS`. Require that the bases agree wherever both define a concept. The closure version digests the union.
3. **Merge per patient.** Keep inputevents' `store_patients` output in a dict by id; it is small. Stream `Run.batches(work_dir)` and, for each batch:
   - `events` = inputevents intervals + chartevents points, with event ids disjoint by prefix (`input_…` and `chart_…`); `DUPLICATE_EVENT_ID` would catch a clash;
   - `coverage` = both lists (sources `inputevents:continuous_segments` and `chartevents:numeric_measurements`);
   - `excluded` = both lists;
   - `series` = the batch's series;
   - `clock_id` = `patient_<id>` for both.

   Patients present in only one source keep that source's parts. Feed `StoreBuilder.add()` instead of building a document (D20's pending item): header first, from the union closure, then patients.
4. **Labels.** The flat map's keys collide (`source`, `source_table`, `admission_profile`). Use these:
   - `sources: chartevents:numeric_measurements|inputevents:continuous_segments`;
   - keep the inputevents keys as they are;
   - add `chartevents_` + each key of `mimic_chartevents.LABELS` other than those shared (`clinical_mapping_verified`, `concept_basis`, `ancestor_basis`, `history_mode`, `acceptance`, `excluded_records`), which must agree.
5. **Provenance.** Add a `chartevents` block: `mimic_chartevents.PROFILE`, the route steps (`mimic_chartevents_check.ROUTE_STEPS`), the sample rule and summary (`Sampler.summary()`), the graph-check counts and its statement sentence, and the run context id. `SELECTION.note` mentions chartevents.
6. **Graph check.** Run it in `convert` after the bulk pass: phase two re-spills the selected stores' patients, as `mimic_chartevents_check.sampled_run` does. It may use the existing worker pool, one job per selected patient. Refuse the conversion if any store mismatches (`CHARTEVENTS_GRAPH_CHECK_MISMATCH`).
7. **`FILES`.** Add `patterns/mimic_chartevents.py`, `patterns/mimic_chartevents_check.py`, `patterns/merkle_patients.py` and `measurement_claims.FILES`.
8. **Unchanged.** `store_patients`, `_refusal` and `_evidence` stay as they are: chartevents never passes through them, and they still require intervals.

## 4. `patterns/verify_mimic_store_ingest.py`

- **Points in the query generator.** `random_query` picks first/last targets with `x['end'][0]`, which raises on points. Use `x.get('end', x['start'])[0]`.
- **Reference.** Replace `ref.exhaustive_match(ingest, ...)` with one `PreparedReference(ingest)` built before the loop and `.match(query, windows)` per query.
- **Raw-value check.** Extend `raw_time_check` to chart events: start = charttime, recorded time = storetime, and the series value and unit equal to the raw row's `valuenum` and `valueuom`.
- **Report.** Add the chartevents counts, the graph-check summary and the statement *rows are tied to the graph by claim identity and checked on a sample, not each validated through the graph*. Set `route.choice` per source: `a` for inputevents, `b` for chartevents.
- **Pins.** Pin the chartevents file as `data/clinical-source-demo-pin.json` already does (sha256 `90f096ad…`, 668,862 rows), in the store's demo pin.

## 5. Gates and workflows

- **Test counts.** `tools/check_test_counts.py` needs `LABELS` entries for `test_merkle_patients`, `test_mimic_chartevents` and `test_prepared_reference`. Then regenerate `docs/status.md` and `verification/test-count-report.json` after the rebase (D10). The gate fails on this branch until then.
- **Workflow.** Add `.github/workflows/execution-store.yml` steps for the three test modules. `test_mimic_chartevents.StoreSmokeTests` needs DuckDB.
- **Documentation.** `docs/execution-store.md`: rewrite "Chartevents path" from the design note, add `series` to the table list, and update the demo section after the full run. Add D27 to the engine-increment log, from `docs/decisions/chartevents-route-b.md`.

## 6. The full demo run, once D26 has merged

1. Rebase, apply sections 1 to 5, and run the unit tests.
2. `python -m patterns.verify_mimic_store_ingest --input-dir data/local-mimic/icu --workers N`, with chartevents on. Estimated single-process cost, from measurements on 10 patients:

| Step | Estimate |
|---|---|
| Pass one | about 0.6 s |
| Route (b) | about 25 s |
| Sample through route (a) | about 130 to 180 stores, 2.5 to 3.5 min (or about 15 s on 14 workers) |
| inputevents route (a), unchanged | 62 s on 8 workers |
| Store build | about 820,000 staged rows (150,051 events, 150,051 series rows, 518,809 excluded records, plus inputevents): 0.5 to 1 min |
| Differential (500 queries, store path plus prepared reference) | 5 to 10 min |

   In total, about 10 to 15 minutes, with the reference holding the ingest in memory (under 1 GB expected). The optional census of all 4,753 chart stores through route (a) takes about 87 CPU-minutes (about 7 min on 14 workers).
3. Regenerate the committed demo report with aggregates only (D17).
