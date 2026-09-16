# Reusing completed reviewed pressure queries

The local pressure service now reuses a completed result when the same controls are submitted again against the same current reviewed session. The result includes the entire cohort, optional follow-up bindings, source evidence and checked treatment witnesses. A new job identifies this as reuse and rechecks source and review validity before publishing it.

This first performance increment avoids repeating an identical graph/SQL query. A previously unseen threshold or window still runs the full existing matcher and independent SQL checks. Reusing prepared graph projections and semantic support across different queries remains future work. A local diagnostic profile of three non-empty arterial batches found substantial repeated work in claim projection, ontology parsing, semantic-model construction and claim isolation. That small sample motivates the next investigation; it does not establish whole-query cost percentages.

## Cache identity and validity

The exact cache key contains the session ID, validated full query, literal controls and service implementation fingerprint. The session ID binds the original files, selected claims, acceptance decisions, declaration, parent request, calendar alignment and engine/schema/ontology artifact hashes. Numerically equivalent literals such as `65` and `65.0` retain distinct query contexts and therefore distinct entries.

Before lookup, the service checks its own implementation, original source-file hashes, engine artifacts and on-disk request/declaration. After execution or retrieval it repeats these checks before making a result available. A changed input refuses the job and clears reusable results; it does not silently accept replacement records or reviews. A changed repository implementation requires restarting the server. Run with the pinned dependencies and restart after changing the installed runtime; the fingerprints cover repository artifacts, not arbitrary installed-package files. Checks detect differences at the validation boundaries; this is not a transactional filesystem snapshot or a defense against files deliberately changed and restored between checks.

Only complete, SQL-reconciled results enter the cache. Failed or blocked jobs clear it and expose no new counts or evidence. Selecting another stratum replaces the current preparation and clears its cached results. Prior completed jobs retain their historical evidence while those jobs remain available; they are never relabeled as a result for the changed inputs.

The cache keeps at most three entries and 32 MiB of serialized JSON payload, evicting the least recently used entry first. Larger results execute successfully without being retained. The byte limit measures serialized payload, not Python heap size. The existing three-job retention is a separate bound; both stores can hold evidence. Cache insertions, retrievals and public inspection responses copy their snapshots so callers cannot mutate later results. There is one worker and no concurrent cache mutation.

No result, index or review cache is written to disk. Restarting loses the cache. Cache hits reuse the original query/evidence context and original proof; they do not claim that Rust or SQL ran again.

## API and display

Completed jobs expose `execution.mode` as `fresh_query` or `cached_complete_result`, an `origin_job_id`, a source/review recheck flag, and whether the result was retained. The origin identifies the computation even if its original job has since expired. It is not a promise that the origin job remains retrievable.

| Timing field | What it measures |
|---|---|
| `prepare_seconds` | Preparation or reuse checks on the current reviewed source session |
| `execute_seconds` | Fresh session execution; zero on a cache hit |
| `final_validation_seconds` | Source, engine, review and service checks before publication |
| `cache_seconds` | Key construction, cache lookup/copy and insertion/copy |
| `total_seconds` | Server job time through validation and cache handling, before response assembly |
| `original_execution_seconds` | Duration recorded by the original full session execution |

The result's existing `elapsed_seconds` remains the original execution duration. The page uses the new job timings and explicitly labels reused results. HTTP polling, network transfer, response assembly and browser rendering are outside the measured server job time.

## Local measurements

| Source | Cold preparation and query | Fresh query over prepared source | Three checked cache hits |
|---|---:|---:|---:|
| Authored synthetic | 1.239 s | 1.183 s | 0.0046–0.0057 s |
| Public arterial demo | 313.842 s | 301.442 s | 0.0273–0.0295 s |

These are server-job durations from the committed reports, including source/review rechecks on hits. Both fresh arterial executions and all three reuses preserve 13 patients, 15 stays, 66 matching segments, 86 eligible pairs, 340 follow-up bindings and one pair without follow-up. All 944 anchor inspections match exactly; all 140 source stays remain represented. This measures avoiding an identical completed query, not faster reasoning over new controls.


## Reproduce the evidence

Use the same pinned dependencies as the [live pressure inspector](live-pressure-inspector.md):

```sh
python demo/benchmark_pressure_cache.py --output verification/pressure-cache-synthetic-report.json
python demo/benchmark_pressure_cache.py --mimic-dir /path/to/public-demo/icu --output verification/pressure-cache-arterial-report.json
python -m unittest discover -s demo -p 'test_*.py'
node demo/test_pressure_ui.cjs
```

The benchmark runs one cold preparation/query, deliberately clears the result cache for a second fresh query over the prepared source, then repeats the request three times. It compares every result field except execution duration and every anchor inspection exactly, including source decisions and PRO/temporal witnesses. It also retrieves representative evidence through HTTP. Only aggregate counts, timings and provenance hashes are exported; source identifiers and binding rows remain local.

The [synthetic report](../verification/pressure-cache-synthetic-report.json) and [public arterial report](../verification/pressure-cache-arterial-report.json) are single local measurement sequences, not a statistical performance study or latency guarantee. CI verifies provenance and behavioral invariants without imposing timing thresholds or downloading public patient records.

Cache safety tests cover changed controls and literal contexts, source/review/implementation invalidation before and during a hit, blocked and failed executions, switching preparations, bounded eviction, response mutation isolation and exact evidence equivalence. Browser layout and clinical interpretation remain unverified.
