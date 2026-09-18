# Authored patient-to-cohort research workspace

The workspace implements one connected H0 journey over the existing eleven authored PRO/SOLID histories: select an index patient, rank eligible earlier features, apply two successive refinements, compare an exact trajectory with declared near matches, inspect source evidence, and download a replayable analysis. It is a bounded, single-process research prototype. It has no clinical data upload, multi-user isolation or clinical-validation claim. Optional single-owner authentication and durable recorded-job storage are documented in [the operations guide](durable-recorded-workspace.md).

## Run and reproduce

From the repository root:

```sh
python -m app.server --host 127.0.0.1 --port 8080
```

Open `http://127.0.0.1:8080`. The default host is loopback. A container must explicitly supply `--host 0.0.0.0`; see [deployment](deployment.md). The service writes nothing to disk. Revisions and comparisons exist only in process memory and disappear on restart.

The **Temporal uncertainty** link opens the separate [bounded temporal demonstration](temporal-workspace.md) at `/temporal`: four authored histories, possible/certain/incomparable results, one explicit widening and replayable solver evidence. It uses its own fixture and does not change the kidney cohort or its selected revision.

A downloaded analysis can be checked against the same implementation and authored fixture:

```sh
python -m app.replay research-revision.json
```

The verifier checks the outer fingerprint, the complete similarity revision chain, admitted dataset/profile/source fingerprints, both existing oracle replay bundles, and their agreement with the combined comparison. Editing an export and merely recomputing its outer hash does not make altered results valid. This is reproducibility checking, not a digital signature or a claim of publisher authenticity. Replay requires the matching checkout; changing the implementation or admitted authored fixture can require the earlier checkout.

## A two-minute guided journey

1. Keep reference **P00** and choose **Find comparable histories**. Ten other patients are eligible; only five are displayed. Open the scoring explanation to see the component distances, weights and missingness rule.
2. Choose **Give renal similarity more weight**. This creates a child revision with weights age 1, creatinine 3 and concepts 1. Eligibility remains unchanged; ranking and rankability may change.
3. Keep maximum baseline creatinine **1.1 mg/dL** and choose **Add baseline criterion**. The second child revision requires a qualifying baseline feature between 0 and 1.1. P08 has no baseline within the required 48 hours, so its hard eligibility becomes unresolved; it is not silently accepted or discarded as a known negative.
4. Choose relaxation budget **2**, then **Run trajectory comparison**. The full hard-filter-eligible population is evaluated, including patients outside the displayed top five. Exact members are **P01, P02 and P03**. The authored permitted relaxations add **P04, P05 and P06**. P10 remains unresolved because its source search is incomplete.
5. Inspect **P03**. The original baseline is **8.0 mg/L**, with its existing PRO/SOLID binding preserving normalized **0.80 mg/dL**, patient role, source record and provenance. Then download the reproducible analysis.

Returning to a parent revision restores its saved query and results. Starting or changing a revision clears the earlier trajectory comparison and export link, so a result is never displayed as belonging to a newly selected revision. A failed trajectory run has no successful export link.

## The connection between similarity and trajectory matching

Both stages use the same P00–P10 authored patients and the same source snapshot. The similarity fixture pins the raw SHA-256 of `demo/cohort-data.json`; the server checks that pin during startup before becoming ready. The selected index patient is excluded in both stages, including when it is not P00.

Similarity uses only records whose occurrence **and authored availability** precede each patient's own index. The fixed profile uses a fourteen-day pre-index history, the minimum eligible creatinine in the preceding 48 hours, an age band, and a concept set derived from earlier toy medication records. The similarity fixture adds an explicit authored availability overlay. The older guided snapshot itself makes no source-availability or historical-replay claim. This overlay and the fourteen-day window are demonstration assumptions; they are not the seven-day clinical-study extraction protocol.

The page makes the scoring rule inspectable: age-band mismatch, scaled absolute creatinine difference, and Jaccard distance after the authored DrugAChild-to-DrugA expansion. Missing components contribute worst-case distance and reduce weighted coverage; at least half the weighted evidence must be observed for ranking. A hard criterion with missing evidence produces unresolved eligibility. Ranking never uses the follow-up creatinine value at the index.

The trajectory step applies the existing fixed kidney pattern from `examples/exemplar.pattern.json`, through `demo.cohort.run_cohort` and `reference_oracle.py`, **after** the selected revision's hard eligibility filters. It searches the full eligible population, not just top-k. It is not a general editor that converts every similarity feature into an arbitrary event-pattern AST. Follow-up values are used explicitly by this trajectory step; this is an exploratory authored pattern, not an outcome-blind clinical validation.

DrugAChild membership under DrugA is existing toy taxonomy entailment. A DrugB substitution and a bounded exposure-window extension are separately declared relaxation costs; they are not ontology entailment and do not activate any pending clinical mapping. Hard rise and baseline-window requirements remain hard. The UI separately reports unresolved hard eligibility and unresolved trajectory evidence. Source inspection includes later rows for the trajectory, clearly separated from the eligible pre-index similarity features.

## API and operational boundaries

All JSON mutations require the exact supported fields; unknown keys, duplicate JSON keys, non-finite constants, wrong primitive types and bodies over 8 KiB are rejected. Client-supplied paths and uploads are not supported. API requests reject cross-site browser context and mismatched Origin/Host. No CORS permissions are emitted. Browser assets use a same-origin content security policy and escaped content.

| Route | Purpose |
|---|---|
| `GET /healthz` | Cheap process liveness; no ranking or reasoning |
| `GET /readyz` | Successful startup admission; no per-request ranking |
| `GET /api/capabilities` | Explicit supported/unsupported scope and authored profile metadata |
| `POST /api/initial` | `{patient_id, top_k}` with top-k from 1 through 20 |
| `POST /api/refine` | `{revision_id, operation}` using the bounded engine operations |
| `GET /api/revisions/<id>` | Defensive copy of an existing revision |
| `POST /api/trajectory` | `{revision_id, budget}` with budget string `"0"`, `"1"` or `"2"` |
| `GET /api/evidence?revision_id=<id>&patient_id=<id>` | Feature eligibility, authored source rows and PRO/SOLID bindings |
| `GET /api/export/<comparison_id>` | Combined revision chain, results, contexts and replay bundles |

The engine admits at most 256 revisions; the app retains at most 64 recent trajectory comparisons. An evicted comparison returns an explicit missing-result response. An expired export must be regenerated from its retained revision. The HTTP service serializes workspace operations; it is not a scalable asynchronous job service. Origin checks and bounded requests do not provide authentication. The optional owner credential protects this single-owner local instance; it does not provide patient-level or multi-user authorization. Use restricted clinical datasets only after the separate access-control, clinical mapping, extraction and deployment work is completed in an authorized environment.

The research path still lacks clinical evaluation, general trajectory editing, outcome summaries with study denominators, all-pairs similarity, persistent projects and authenticated multi-user operations. These remain explicit capabilities outside this release.

## Acceptance evidence

```sh
python -m unittest discover -s app -p 'test_*.py'
node app/test_ui.cjs
```

`app/test_server.py` executes the whole journey over real HTTP: initial search, two successive refinements, full-population exact/relaxed comparison, source inspection, export and replay. It also tests a non-default reference, unresolved evidence, top-k separation, tamper detection, source-fingerprint mismatch, bounded comparison retention, strict request validation, browser origin checks and health/readiness separation.

`app/test_ui.cjs` executes the actual browser script with engine-produced responses. It checks both refinements and their parent IDs, cohort additions, unresolved patients, source escaping, export state, undo and failed evaluation. This is DOM behaviour verification; it does not claim a full visual or accessibility audit.

The committed [aggregate journey evidence](../verification/research-prototype-journey.json) records the five completed stages, three replayed revisions, aggregate cohort counts, source/profile/implementation hashes and exact artifact hashes. The HTTP acceptance test regenerates `verification/research-prototype-run/journey.json` and checks it against the committed evidence. The report covers this authored journey; it does not establish clinical performance or completion of the broader product.
