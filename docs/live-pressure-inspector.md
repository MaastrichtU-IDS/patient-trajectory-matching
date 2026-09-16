# Live reviewed pressure queries and evidence inspection

The local demo now exposes the reviewed mixed interval/measurement engine through background HTTP jobs and an interactive pressure page. Select one measurement stratum, change the pressure threshold or narrow the recorded-time windows, inspect the resulting patient/stay/segment membership, and open the exact source and review evidence behind an anchor.

The [existing guided journey](../demo/NARRATIVE.md) remains the short synthetic demonstration of exact matching and priced relaxation. The pressure page uses the separate reviewed-record profile. It performs complete source-query execution and can take several minutes; it has no offline replay or implicit fallback.

## Start

From the repository root, use Python 3.12 with the existing pinned graph/Rust dependencies:

```sh
python -m pip install -r patterns/requirements-semantic.lock.txt
python demo/serve.py --mimic-dir /path/to/mimic-demo/icu
```

Open [the local pressure page](http://127.0.0.1:8765/pressure), or choose **Live pressure queries** in the guided demo header. Supply the original `inputevents.csv.gz`, `chartevents.csv.gz`, `icustays.csv.gz` and `d_items.csv.gz` files from the [public MIMIC-IV demo 2.2 release](https://physionet.org/content/mimic-iv-demo/2.2/), matching the [repository pin](../data/clinical-source-demo-pin.json). Unpinned sources are refused before review preparation. Full MIMIC and other datasets are outside this route.

For a fast, entirely authored example, explicitly select:

```sh
python demo/serve.py --pressure-synthetic
```

This uses the existing synthetic source fixture and a [separate explicit declaration](../demo/pressure-synthetic-review.json). It is visibly labeled synthetic and returns three eligible patients under the default controls. Without either startup flag, the pressure page provides setup instructions. The original guided demo remains usable without the optional graph/Rust packages.

## Supported controls and review scope

| Control | Supported range | Interpretation |
|---|---|---|
| Stratum | Arterial 220052, non-invasive 220181, ART label 225312 | Separate item-specific queries and reviews |
| Pressure threshold | Decimal greater than 0 and at most 300 mmHg | Strictly below; demonstrative parameter range, not a clinical recommendation |
| Baseline window | 1–30 whole minutes | Before recorded segment start; at least one microsecond strictly earlier |
| Follow-up window | 0–120 whole minutes | From segment start through the specified upper bound; optional |

The default request uses threshold 65, baseline 30 minutes and follow-up 120 minutes. Every eligible baseline/segment pair and every qualifying same-item/unit follow-up is retained. Follow-up can occur after the recorded segment ends. No age filter, closest-baseline rule, treatment-course reconstruction, clinical improvement filter or mixed-item query is added.

A session prepares and independently audits the original reviewed window envelope, validates the existing declaration, and compiles its acceptance decisions. The window selector admits records by item and time before applying the pressure threshold, so changing that threshold does not require new source claims. Narrower time windows remain subsets of the original selected windows. The original pair-covering batches therefore remain complete for every admitted baseline/follow-up pair under these controls.

Requerying creates a new query context over the same accepted record view. It neither creates new acceptance decisions nor claims that the new query parameters have been clinically reviewed. A wider time window would require another reviewed source envelope and is refused. Source files, implementation hashes and on-disk review/request inputs are checked before reuse; source/review changes during a run prevent successful publication.

## Inspect a result

After a query completes, the page shows distinct patient, stay and treatment-segment counts, eligible baseline/segment pairs, follow-up bindings and pairs lacking follow-up. All source stays remain selectable, including stays with no admitted treatment anchor. The heading records the actual controls that produced the displayed result.

Select a patient, an ICU stay and a treatment segment. The inspector shows:

- Recorded segment start/end, original pressure literals and charted timestamps.
- A relative-time plot of same-unit observations, queried windows and selected baseline/follow-up roles.
- Every eligible baseline/follow-up combination and its exact recorded difference, including absent follow-up.
- Other reviewed observations with numeric, unit or baseline-window exclusions.
- Original table/row identity, file/CSV/row hashes, claim hashes and the original acceptance decisions.
- Actual treatment process, patient-role and bearer witnesses returned by the matcher, with temporal eligibility evidence and query context.

The role witness follows PRO: process → `hasParticipant` → patient role → `isFeatureOf` → person. The inspector introduces no new ontology relation. Original measurement values remain SOLID-compatible literals; this pressure profile performs no unit conversion. Records excluded before admission appear in aggregate source-ledger counts, rather than being promoted into trajectory observations.

Patient/stay identifiers and record evidence are returned only by the configured local server. They are not embedded in the committed page or generated offline demo. The repository contains an aggregate verification report, not the live source rows or bindings.

## Execution and failure behavior

[`reviewed_pressure_session.py`](../patterns/reviewed_pressure_session.py) uses the existing reviewed stores, explicit alignment and mixed matcher for each bounded batch, merges complete bindings and checks each anchor against the independent unpartitioned SQL query using the actual current controls. Counts become available only when every anchor succeeds. A failed anchor suppresses all cohort metrics and inspection results for that run.

[`demo/pressure.py`](../demo/pressure.py) supports one active job, one current preparation cache and the three most recent jobs. Completed jobs retain their session evidence while available; old jobs expire. Jobs and evidence are in memory and are lost when the server stops. There is no cancellation, restart/resume, multi-user access control or production deployment service in this increment.

The browser polls progress without showing partial cohort counts. A failed or incomplete query retains the previous completed view with an error; it never relabels the old results as the new query. Stale query/evidence responses cannot replace a newer selection. The server binds to loopback, checks the pressure routes' Host/Origin, accepts bounded JSON controls and never accepts source filesystem paths through HTTP.

| Route | Purpose |
|---|---|
| `GET /api/pressure/config` | Source mode, strata and controls |
| `POST /api/pressure/jobs` | Start a bounded query; returns a job ID |
| `GET /api/pressure/jobs/{id}` | Progress, failure or complete cohort summary |
| `GET /api/pressure/jobs/{id}/anchors/{token}` | Evidence bound to that completed query and anchor |

These are local demonstration routes, separate from the broader product OpenAPI. Preparation and execution timings are displayed to support later performance work; they are not a benchmark or latency guarantee.

## Verification and rehearsal

The [live HTTP verification report](../verification/live-pressure-demo-report.json) records a fresh arterial query: 13 patients, 15 stays, 66 matching segments, 86 eligible pairs, 340 follow-up bindings and one pair lacking follow-up. All 944 anchors agree with SQL; all 140 stays remain represented. HTTP inspection succeeded for an eligible anchor, a non-matching anchor and a missing-follow-up case. The report binds the engine/session/query and source-review contexts and contains no source patient/stay/anchor identifiers.

Fifteen additional Python tests cover differential default execution, altered controls, immutable reviews, source/review invalidation, SQL corruption detection, incomplete-run suppression, exact source/role inspection, bounded job retention, live HTTP behavior and provenance. The demo suite totals 29 tests, separate from the 687 contract checks. Node DOM-state tests exercise actual synthetic engine responses, selections, missing follow-up, non-matches, empty stays, progress, failures and stale responses. CI runs both the original guided and new pressure-page checks.

```sh
python -m unittest discover -s demo -p 'test_*.py'
node demo/test_guided_ui.cjs
node demo/test_pressure_ui.cjs
```

Visual browser/layout inspection remains unverified in the authoring environment. Rehearse the page in the presentation browser: run the authored default query, inspect patient 2's missing follow-up, reduce the threshold to 59, inspect an excluded baseline, then select patient 4's stay without an admitted anchor. For real-source use, allow the source query to finish before presenting its result.

Clinical interpretation, treatment-course initiation, source-as-known history, physical elapsed-time verification, causal effects and full mixed OWL reasoning remain outside this recorded-source demonstration.
