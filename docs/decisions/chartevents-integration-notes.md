# Chartevents route (b): integration notes for after D26

*Status: done in D27 (see [the engine-increment log](engine-increment-log.md)). Kept as the record of the hooks; the code is the reference now.*

The branch `feature/chartevents-route-b` adds only new files:

| File | Contents |
|---|---|
| `patterns/merkle_patients.py` | Not wired in (decision 1) |
| `patterns/mimic_chartevents.py` | Reader, admission, claim stores, bulk rows |
| `patterns/mimic_chartevents_check.py` | Sampler, round trip, route (a), census and sampled runs |
| `patterns/mimic_chartevents_raw_check.py` | Independent raw-row check |
| `patterns/prepared_reference.py` | Reference with engine inputs built once per patient |
| `patterns/chartevents_fixture.py` and `examples/mimic-chartevents/` | Synthetic fixture |
| Three test modules | Tests for the above |

It edits nothing the D26 agent owns. The hooks below are written against main 40adf3d; re-read each one against D26's version before applying it.

## 1. `patterns/execution_store.py`

1. **Patient contract.** `validate_patient` accepts an optional `series` key: a list of `{event_id, item_class, value_lexical, value_decimal, value_micro, unit}`. It refuses each of these with a named code:
   - an unknown event (`SERIES_UNKNOWN_EVENT`);
   - an event that is not a point (`SERIES_EVENT_NOT_POINT`);
   - an `item_class` that is not one of the event's direct concepts (`SERIES_ITEM_CLASS_MISMATCH`);
   - two rows for one event (`DUPLICATE_SERIES_EVENT`);
   - a `value_lexical` that `measurement_claims.decimal_value` refuses;
   - a `value_decimal` other than `format(Decimal(value_lexical), 'f')`;
   - a `value_micro` other than `mimic_chartevents.micro_value(Decimal(value_lexical))` (`INVALID_SERIES_VALUE`). Use that function, or a copy kept equal to it by a test; never `scaleb`. Null means not exact at 10⁻⁶ or outside BIGINT, and is never a refusal;
   - an empty unit, or one with surrounding spaces (`INVALID_SERIES_UNIT`).

   The canonical patient sorts `series` by `event_id`, so the patient digest covers it.
2. **Staging.** Add a `series` staged kind per part, using D26's typed staging. Copy `start_lo` and `start_hi` from the event at `add()`, and count series rows toward `part_rows`.
3. **Derive.** Add a table `series`:
   - columns `patient_idx, event_id, item_class, start_lo, start_hi, value_lexical, value_decimal, value_micro, unit`, typed BIGINT, VARCHAR, VARCHAR, BIGINT, BIGINT, VARCHAR, VARCHAR, BIGINT, VARCHAR;
   - partitioned by bucket and derived part by part;
   - content-digest key `patient_idx, item_class, start_lo, start_hi, event_id`.

   Register it in `TABLES`, the type check, the content digests, the manifest and the manifest schema.
4. **Derive-time check.** Each series row's event exists with the same start, and `item_class` is one of its `event_concepts` with basis `direct`.
5. **Accessor.** Add `Store.series(patient_id, event_ids)` for display. `match` is unchanged in stage 1.
6. **Input digest.** Keep D20's flat input digest as the identity (decision 1). Do not wire `merkle_patients` and do not record a root.
7. **Profiles.** Bump the store profile, since a new table changes identity. Settle the ingest profile together with D26's change; D25's reasoning applies, because `series` is optional and additive.
8. **D26 typed columns.** The chartevents evidence fields are `claim_id`, `claim_sha256`, `record_id`, `source_record`, `source_key`, `record_status`, `selection_state`, `recorded_at_us`, `available_at_us`, `source_table`, `source_record_number`, `source_row_sha256` and `source_file_sha256`. Map them onto D26's columns under inputevents' names. The rest goes to the evidence table: `episode_id`, `clock_id`, `clock_origin`, `clock_origin_us`, `concept_bases`, `acceptance_policy_id`, `origin_source_record_id`, `history_mode` and the flags. Adapt only `mimic_chartevents.store_row`, and the raw check if a field it reads moves (`source_record_number`, `recorded_at_us`).
9. **Clocks.** D26 must not assume any of the following:
   - **One origin per clock id.** Both sources name the patient's clock `patient_<id>`, but each importer chooses its own origin (earliest admitted inputevents start, earliest admitted charttime). Store times are absolute microseconds from 0001-01-01 on the patient-local calendar, and `clock_origin` is per-event provenance. So `clock_origin` must not become a patient-level or clock-level typed column, and nothing may rebase store times by a clock's origin.
   - **That a clock id identifies one origin.** If D26 needs that, name chartevents' clock in the store `patient_<id>` and record the importer clock id and origin per event. The claim keeps `patient_<id>`, since that is `describe()`'s identity.

## 2. `patterns/execution_store_reference.py`

- **Series.** Build `series` independently in `build_tables`, add it to `COLUMNS` and `table_digests`, and have `patient_digest` cover `series`.
- **Coverage rule.** Make `prepared_reference.coverage_rule` (or a copy moved here) the only implementation, and have `exhaustive_match` call it. It is copy-pasted today: `prepared_reference.py` against `execution_store_reference.py` lines 305 to 322.
- **No Merkle.** Do not change the input digest (decision 1).

## 3. `patterns/mimic_store_ingest.py`

1. **Combined conversion.** `convert(..., chartevents=False)` builds `mimic_chartevents.Run(input_dir, dataset_id)` when chartevents is enabled.
2. **Closure.** Take the union of the inputevents closure and `Run.ancestors`/`Run.bases`. Both use the same namespaces, and a test checks `GROUP_NS` and `group_iri`. Require the bases to agree.
3. **Merging patients.** Hold inputevents' patients by id, stream `Run.batches`, and merge each patient's parts:
   - events: event ids are disjoint by prefix, `input_…` and `chart_…`;
   - coverage: per source, with sources `inputevents:continuous_segments` and `chartevents:numeric_measurements`;
   - excluded records;
   - series.

   Then feed `StoreBuilder.add()`, with the header first. **Clocks:** the `CLOCKS_NOT_REBASABLE` check (lines 402 to 406) runs inside `store_patients`, over inputevents adapter rows only. Chartevents events never pass through it and are merged afterwards with absolute times, so it must not be extended to the merged patient (see §1.9).
4. **Labels.** Replace the colliding `source` key with `sources: chartevents:numeric_measurements|inputevents:continuous_segments`. Add each chartevents-only key of `mimic_chartevents.LABELS` with the prefix `chartevents_`. The shared keys must agree.
5. **Provenance.** Record the chartevents profile, the route steps, the census or sample summary, the raw-check result and `mimic_chartevents_check.STATEMENT`. Record no Merkle root, and no digest of row-level data for credentialed data (D17).
6. **Required: compare graph rows with the store's rows.** For every graph-checked store (every store on the demo), compare its route-(a) rows field for field with the rows read back from the **built** store for those event ids: `events`, the evidence table and `series`, after D26's mapping. At minimum, digest exactly what is handed to `StoreBuilder.add` for those events. Until this is done, reports say "re-derivable", not "ingested".
7. **Required: refuse a conversion that fails.** Refuse if the raw-row check does not pass (`CHARTEVENTS_RAW_ROW_CHECK_FAILED`), if any round trip fails, or if any graph-checked store mismatches (`CHARTEVENTS_GRAPH_CHECK_MISMATCH`).
8. **`FILES`.** Add `mimic_chartevents.py`, `mimic_chartevents_check.py`, `mimic_chartevents_raw_check.py` and `measurement_claims.FILES`.

## 4. `patterns/verify_mimic_store_ingest.py`

- **Points in `random_query`.** It picks first and last with `x['end'][0]`, which raises on points. Use `x.get('end', x['start'])[0]`.
- **Reference.** Use one `PreparedReference(ingest)` per run for most queries, and keep `ref.exhaustive_match` on a deterministic slice, the first 50 of the 500 seeds. The store's event filter and the prepared reference make the same argument, so the slice is what tests it independently.
- **Raw check.** Run `mimic_chartevents_raw_check` over every chart row, as part of the census. Its fields are listed in that module's docstring.
- **Census.** Run the demo census: parts (i), (ii) and (iii) of decision 2.
- **Report.** Include the chartevents counts, the census summary, the raw-check result and `STATEMENT`. Set `route.choice` per source: `a` for inputevents, `b` for chartevents with a census.
- **Pin.** Pin the chartevents file (sha256 `90f096ad…`, 668,862 rows).

## 5. Gates and workflows

- **Test counts.** Add `test_merkle_patients`, `test_mimic_chartevents` and `test_prepared_reference` to `LABELS` in `tools/check_test_counts.py`, then regenerate `docs/status.md` and the test-count report. Do this only in the integration step.
- **Workflow.** Add the three test modules to `.github/workflows/execution-store.yml`.
- **Documentation.** Update `docs/execution-store.md`, then add D27 to the log from `docs/decisions/chartevents-route-b.md`.

## 6. The full demo run, once D26 has merged

Single-process estimates, measured ad hoc on 3 to 10 patients:

| Step | Estimate |
|---|---|
| Pass one | 0.6 s |
| Route (b) | 25 to 35 s |
| Raw-row check | about 3 s |
| Round trip on all 4,753 stores | about 7 min |
| Route-(a) census | about 56 CPU-min (4 to 5 min on 14 workers) |
| inputevents route (a) | 62 s on 8 workers |
| Store build | 0.5 to 1 min |
| Differential | 5 to 10 min, plus the exhaustive slice |

`measurement_claims.select` re-reads its artifact files to digest them on every call. That is a few milliseconds per store and is left as it is, since the module is not this branch's to change.
