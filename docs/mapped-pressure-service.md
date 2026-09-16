# Live mapped pressure service

Status: executable opt-in local HTTP service, with an authored default and explicit startup configuration for supplied records. Public clinical mappings remain pending.

The pressure service can now execute reviewed measurement-class selection through the [prepared measurement sessions](prepared-measurement-session.md). It retains the existing single-item strata, reviewed time-window envelope, complete-result cache, per-anchor SQL checks and source-backed inspection interface. The new launcher uses the same `/pressure` page and local-origin HTTP routes.

## Run the authored example

From the repository root, with the pinned graph/Rust dependencies installed:

```bash
python demo/serve_mapped_pressure.py --port 8765
```

Open `http://127.0.0.1:8765/pressure`. The source mode and stratum label identify the authored synthetic example. Run the default query, then change the threshold to 59, baseline window to 25 minutes and follow-up window to 60 minutes. The changed query reuses three prepared batches and still checks all three anchors against SQL. Inspect an anchor through the page or API; its response includes the checked measurement selector and catalogue evidence.

The default mapping pack is [`examples/measurement-source-catalogue`](../examples/measurement-source-catalogue/). `--mapping-dir` selects an alternative startup mapping pack for these fixed authored source records. It does not select a new dataset or accept mappings. HTTP clients cannot supply file paths, units, arbitrary item sets, treatment classes or mapping decisions. Use [`--config`](configured-pressure-service.md) to select supplied source records, a bounded pressure request and explicit reviews. Clinical mapping approval remains separate.

## Execution and review lifetime

[`mapped_pressure_session.Session`](../patterns/mapped_pressure_session.py) first checks the pinned measurement catalogue and reviewed selector. The selector must support exactly the pressure request's one source item and literal unit. The original pressure-session constructor then performs its existing source-fidelity audit and applies the explicit source review declaration. The session context binds both the original pressure preparation and the mapping files, catalogue and checked selector evidence.

[`MappedPressureService`](../demo/mapped_pressure.py) passes a bounded cache of prepared measurement sessions into the existing pressure batch interface. Each nonempty batch is admitted through a successful source-audited mapped query. Changed numeric/temporal controls reuse that preparation; the original pressure-session controls still enforce 1–30 baseline minutes, 0–120 follow-up minutes, and the reviewed parent envelope. Baseline/follow-up comparisons remain within the same item and unit. Follow-up is optional for eligibility.

| Condition | Result |
|---|---|
| Pending/withdrawn mapping or unsupported selector | Preparation fails; no literal-item fallback |
| Identical controls | Complete result may be reused after source, source-review, mapping-file and implementation checks |
| Changed controls inside the reviewed envelope | Prepared batches reused; temporal matching and anchor SQL rerun |
| Mapping/source review changes, including during a job | Job fails, memberships are withheld and preparation/result caches are cleared |
| Source or implementation changes | Existing digest checks prevent exposing an obsolete successful result |
| Incomplete batch or SQL disagreement | Cohort result is blocked; both caches are cleared |
| Cache eviction | Evicted session is closed; revisiting the batch requires fresh admission |

A mapping-file change is detected by its byte digest, including on a complete-result cache hit. Previously completed jobs remain inspectable as historical snapshots with their original mapping evidence. They do not become results under the later review. For the fixed authored route, a subsequent job must construct a new session from the current review. The configured route instead freezes review inputs until restart and remains invalidated after a detected change. No acceptance decision is rewritten automatically.

The cache holds at most 1,024 prepared batches and 128 MiB of serialized prepared payload by default. Individual sessions retain their existing 16 MiB default budget and source/store/query limits. These are serialized-payload limits, not resident-memory bounds. A batch too large for the cache blocks explicitly. The service remains single-worker and local-only.

## Verification and local measurements

The [committed synthetic report](../verification/mapped-pressure-synthetic-report.json) records this single local sequence:

| Operation | Observed time |
|---|---:|
| Initial mapped service job and preparation | 1.628 s |
| Changed query through prepared service | 0.025 s |
| Identical changed query from complete-result cache | 0.006 s |
| Fresh mapped session execution for the changed query | 1.472 s |

The service timings are its internal job durations, not browser-perceived latency. The fresh baseline is a direct mapped session execution, not a second HTTP job. These are observations on a tiny authored fixture without repeated sampling or hardware control. They establish no clinical-data latency guarantee.

The changed prepared query performs zero source audits, mapping plans or network compilations, while running three anchor SQL comparisons. Every result field except duration and all three HTTP anchor inspections equal fresh mapped execution. All five source stays remain represented. The default query selects three synthetic patients; the changed query selects one. The report contains only aggregate metrics and provenance, not patient rows or identifiers.

Sixteen new demo tests cover live/fresh equivalence, pending/withdrawn review, mapping changes before/during cached work, source-review and source changes, SQL corruption, window/item scope, malformed packs, cache budgets, eviction, response isolation and implementation changes. The real HTTP test writes a fresh aggregate report to `verification/mapped-pressure-run/report.json`, uploaded by demo CI. CI compares behavior and provenance without timing thresholds.

```bash
python -m unittest discover -s demo -p 'test_mapped_pressure.py'
python demo/benchmark_mapped_pressure.py --output verification/mapped-pressure-run/local-report.json
```

With the configured route, the demo suite now has 80 tests; the contract suite remains 781 checks (758 suite tests and 23 oracle checks). The existing literal pressure route and its public-demo evidence remain separately available. The [configured route](configured-pressure-service.md) now accepts supplied sources and frozen review inputs. Next steps are explicit adoption of clinical reviews and representative workload measurements under the existing batch-coverage requirements.
