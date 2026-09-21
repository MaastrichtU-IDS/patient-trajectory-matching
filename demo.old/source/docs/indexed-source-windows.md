# Indexed source windows and pending anchor batches

`indexed-source-windows-1.0` implements the source-selection step identified by the [clinical preflight](clinical-source-preflight.md). It streams the original chart file, indexes admitted records by patient, ICU stay, item and time, and selects the complete baseline/follow-up window for **every admitted treatment anchor**. It preserves original CSV row identities and the complete stay/anchor roster.

Selection is conditional on a declared same-release, same-patient calendar assumption. It neither accepts clinical claims nor performs a real-source mixed query. Its optional batch exporter produces pending claim stores and an undecided query-alignment template. The [clinical candidate plan](../data/clinical-candidate-plan.json) remains proposed for review.

## Selection contract

The [request schema](../schemas/indexed-source-windows.schema.json) wraps the existing mixed query with a dataset identity and an explicit `calendar_basis` declaration. Treatment selection is one literal imported input-item class. Baseline and follow-up must use the same single measurement item. Different measurement items require separate requests; no method or item pooling is inferred.

For a recorded segment `[s,e)`, baseline window bounds `bmin,bmax`, and follow-up anchor `a` with bounds `fmin,fmax`, select every admitted point of that item and patient/stay in this union:

| Window | Inclusive source-label range |
|---|---|
| Baseline | `[s − bmax, s − bmin]` |
| Follow-up | `[a + fmin, a + fmax]`, where `a` is start or end |
| Follow-up with interval membership | Intersect the follow-up range with `[s,e − 1 microsecond]` |

Selection does **not** filter by numeric threshold, unit, improvement, follow-up presence, or whether a baseline subsequently qualifies. Numeric/unit predicates still belong to the mixed query after explicit record selection. Existing source-admission rules, including numeric agreement and warning handling, remain in force. A changed numeric value can affect admission or duplicate identity, but does not otherwise change time-window membership.

The two range lookups use a SQLite covering index `(patient, stay, item, position, number)`. Their union removes repeated membership within one anchor while preserving the same source point in multiple overlapping anchor batches. Positions use exact integer calendar coordinates. Large legal gaps are clipped to the representable calendar before SQLite binding; no floating-point conversion or timestamp overflow is used.

For every anchor, a second implementation directly compares original datetime labels and integer gaps without calling SQL, the coordinate normalizer, or the window compiler. All selected original record numbers must agree. The reference shares admission and source scope; it independently checks window membership, not source truth or the admission policy. Tests also inspect the SQLite query plan to confirm index use. No runtime speedup has been benchmarked or claimed.

For this exact, single-item source profile, every point that could satisfy a baseline or follow-up predicate lies in the selected union. The source importers provide exact point labels and no cross-record measurement constraints. This supports query-preserving time preselection within the stated profile. The selector must not be reused as a completeness argument for uncertain bounds, cross-record constraints, arbitrary ontology rules, or cross-stay queries without extending and verifying the required context.

## Original identities and accounting

`run(folder, request)` returns a summary plus the detailed local selection: all requested stays, every candidate anchor, original interval segments, admitted candidate measurement records, origins, and source ledgers.

- Each anchor retains its original source record ID, compressed-file hash, expanded CSV hash, row hash and record number.
- Each candidate measurement ledger entry retains its original CSV record number, row hash, admission outcome, exclusion reasons and duplicate link. The complete chart manifest supplies its original table/file identity.
- Every chart row outside the one requested item is counted as `OUTSIDE_ITEM_SCOPE`; those unrelated rows are not clinically validated or copied into the candidate ledger. All requested-item rows are reconciled as admitted, duplicate, unsupported or invalid, and admitted records additionally record whether any anchor window selected them.
- The input ledger covers every source row. Every requested ICU stay remains in the roster, including `NO_ADMITTED_ANCHOR` stays. Anchors with zero selected measurements remain `EMPTY_WINDOW`; they are not discarded or classified as clinically negative.
- A selected point may belong to several anchor windows. The report distinguishes unique source records from total window memberships; these are not interchangeable counts.

The code uses the bounded full-chart streaming reader introduced by preflight. It does not rewrite a filtered CSV and pass new row numbers off as original source provenance. Source hashes and request/implementation hashes bind the selection context. Detailed outputs include source records and must remain in the authorized local environment; only aggregate reports are committed.

## Pending batch export

`pending_batch(selection, anchor_id)` is an internal handoff from an in-memory `run` result. Do not treat externally edited selection JSON as an ingestion interface. The CLI reruns source selection before exporting a requested anchor.

A batch contains one interval claim and all selected measurement claims, preserving the existing importers' original record IDs and source hashes. Interval origins are chosen from all admitted input segments for that patient before item filtering. Measurement origins are chosen from the admitted requested measurement item for that patient before anchor-window filtering. Thus a different anchor window does not redefine the patient calendar. The chosen origin may refer to a source point outside the exported window; its original identity is retained.

Each batch includes its selection context, source evidence, query, source-item semantic policy, hash-bound empty acceptance policies, and the existing 137-/141-axiom claim-isolation certificates. Its snapshot and policy hashes change when the source, query or selection context changes. All claims remain pending. A query-alignment template, when both stores exist, has **empty bindings**: the source-window assumption does not silently become an approved query alignment.

For an empty measurement window, export the pending interval claim and no measurement store or alignment template. No measurement, duration, or arbitrary empty-store clock is fabricated. The exporter applies the existing store, serialization and isolation checks; a count-bounded window can still fail those checks. Successful export is `PENDING_REVIEW`, never a clinical acceptance or cohort answer.

The synthetic tests explicitly accept a fabricated batch and supply a constructed alignment, then run the real Rust semantic gate and mixed matcher. Their complete bindings agree with the independent raw-row SQL reference. This validates the technical handoff; it does not constitute real-patient acceptance or clinical review.

## Limits and incomplete selections

The profile allows at most 2,000 admitted treatment anchors, 256 stays and 100,000 requested-item chart rows, retaining the preflight reader's complete-file row/byte/line limits. A scan, source-change or global-limit failure returns no completed selection and preserves prior CLI output.

Each anchor keeps its complete selected record list. With `n` selected points, the conservative batch screen checks 32 measurement claims, `2+n` temporal variables, `n` baseline candidates and `n*(n−1)` follow-up candidates against existing limits. It does not inspect scalar values to make a batch fit. Because these are upper bounds, a blocked count screen does not prove that an exact predicate-aware execution would exceed the matcher limits.

| Anchor status | Meaning |
|---|---|
| `EMPTY_WINDOW` | No admitted point of the requested item in either window |
| `COUNT_BOUNDED_WINDOW` | The complete selected window meets the conservative count bounds |
| `BLOCKED_WINDOW` | A count bound is exceeded or indexed/reference membership disagrees |

Any blocked anchor makes the overall status `BLOCKED_INCOMPLETE_WINDOW_SELECTION`. Its complete record list, reasons and roster position remain available. Exporting that anchor is refused. A valid individual batch from another anchor can still be inspected, but must not be presented as complete cohort coverage. This layer emits no cohort membership in any outcome.

## Pinned public-demo results

The [aggregate report](../verification/indexed-source-windows-demo-report.json) verifies the same four [pinned demo files](../data/clinical-source-demo-pin.json) as preflight. Each stratum scans all 20,404 inputevent and 668,862 chart rows and retains all 140 stays and 944 admitted norepinephrine anchors. The query parameters are the illustrative 30-minute baseline and 120-minute follow-up windows; the record-calendar assumption is explicit in each request.

| Measurement item, evaluated separately | Count-bounded nonempty windows | Empty windows | Blocked windows | Largest window | Unique points in any window |
|---|---:|---:|---:|---:|---:|
| 220052 arterial mean | 534 | 407 | 3 | 38 | 1,078 |
| 220181 non-invasive mean | 490 | 445 | 9 | 22 | 840 |
| 225312 ART mean | 67 | 877 | 0 | 5 | 98 |

All **2,832 indexed windows agree with the direct timestamp reference**. The arterial stratum has two windows above the measurement-claim limit and three above the conservative follow-up bound; those reason counts overlap. The non-invasive stratum has nine above the conservative follow-up bound. All three requests retain the same treatment-anchor roster; these results do not select or rank measurement methods by downstream outcomes.

The third stratum completes window selection within count bounds, but its empty windows and sparse record coverage do not establish clinical suitability. No demo claim batches have been accepted and no real-source mixed query has run. The subsequent [partitioned window profile](partitioned-window-query.md) supplies complete pair-covering plans for the oversized windows, explicit review validation and a complete execution ledger. It preserves the whole-window exporter and its guards. Real-source review and mixed execution remain pending; the legacy results above continue to describe the whole-window route.

## Run and validate

```sh
python -m patterns.indexed_source_windows
python -m patterns.indexed_source_windows --aggregate-only
python -m patterns.test_indexed_source_windows

# Supply original demo ICU files locally; publishes only aggregate verification evidence:
python -m patterns.verify_indexed_source_windows --input-dir /path/to/demo/icu
```

Use `--input-dir`, `--request`, and `--output` for supplied files. `--anchor-id` exports that original anchor as a pending batch; it is mutually exclusive with `--aggregate-only`. The default detailed output is `verification/indexed-source-windows-run/result.json`, ignored by Git. Request files are limited to 256 KiB. Output is atomic and protected from overwriting sources or the request. A complete window selection exits 0; a blocked selection writes diagnostics and exits 2. Invalid inputs or a refused batch preserve existing output.

The 24 tests cover original identities, empty and overlapping windows, all inclusive/strict/half-open boundaries, large gaps, patient/stay isolation, independent microsecond cases, actual index use, source accounting, explicit pending selection, origin stability, resource limits, mismatch suppression, raw-row SQL agreement after synthetic acceptance, report pins and CLI behavior. CI runs synthetic tests and verifies the committed report's provenance without downloading patient data.
