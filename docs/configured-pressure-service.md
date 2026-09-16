# Configured mapped pressure service

Status: executable startup configuration for supplied records and explicit reviews. The committed example is authored and synthetic; clinical terminology decisions remain pending.

The mapped pressure launcher now accepts a source directory, pressure request, source-fidelity declaration and mapping pack through one local JSON file. This removes the fixed source/item assumption from PR #41 while retaining its prepared queries, complete-result cache, source evidence and independent anchor SQL checks.

## Run the supplied example

Install the pinned dependencies described in [validation](validation.md), then run from the repository root:

```sh
python demo/serve_mapped_pressure.py --config examples/configured-pressure-service/config.json --port 8765
```

Open `http://127.0.0.1:8765/pressure`. The page identifies the authored supplied records, recorded input item 1000 and reviewed measurement item 2001. Its defaults are threshold 65 mmHg, baseline 15 minutes and follow-up 60 minutes. The default query has one eligible synthetic patient; all five stays remain represented. The fixture's eligible baseline lies exactly 15 minutes before its recorded segment start. No selected follow-up is required for eligibility.

| Setting | Default authored route | Configured example |
|---|---|---|
| Source directory | `examples/source-mixed-query` | `examples/prepared-measurement-session` |
| Measurement item | 2000 | 2001 |
| Baseline / follow-up envelope | 30 / 120 minutes | 15 / 60 minutes |
| Source review | Built-in declaration | Explicit configuration reference |
| Page labels and defaults | Fixed fixture | Supplied label and request-derived item/defaults/limits |
| Matching and verification | Reviewed mapped batches and SQL | Same execution path |

## Configuration contract

The [example configuration](../examples/configured-pressure-service/config.json) follows the closed [schema](../schemas/pressure-service-config.schema.json):

```json
{
  "profile": "mapped-pressure-service-config-1.0",
  "id": "authored-configured-pressure",
  "label": "Authored supplied records · item 2001",
  "source_dir": "../prepared-measurement-session",
  "pressure_request": "request.json",
  "source_review": "source-review.json",
  "mapping_dir": "mapping"
}
```

Paths resolve relative to the configuration file, independent of the working directory. Absolute paths are also accepted. The server reads these trusted local paths only at startup; HTTP clients cannot submit paths or choose mappings. `--config` and the fixed-fixture `--mapping-dir` option are mutually exclusive. Omitting both retains the original authored route.

The source directory must provide exactly one CSV or CSV.gz for each of `inputevents`, `chartevents`, `icustays` and `d_items`. The existing audit limits and source hash checks apply. The configuration is capped at 64 KiB; its request, declaration and each mapping JSON file retain the 256 KiB limit.

The pressure request uses `indexed-source-windows-1.0`. This launcher supports one literal treatment item, one identical baseline/follow-up measurement item, exactly `mmHg`, a strict less-than baseline threshold, and whole-minute windows. Baselines are strictly before segment start; follow-ups start at offset zero from segment start and need not remain within the segment. Baseline limits are 1–30 minutes and follow-up limits 0–120 minutes, further narrowed to the configured parent request. Threshold controls remain greater than zero and at most 300. Changing the threshold searches the same explicitly reviewed source envelope; it does not extend source coverage.

The mapping directory contains `request.json`, `catalogue.json`, `terminology.json`, `mappings.json`, `review.json` and `selector.json`, under the existing [measurement catalogue](measurement-source-catalogue.md) and [reviewed selector](reviewed-measurement-mappings.md) contracts. Its pinned source files, dataset, single supported item and unit must agree with the pressure request. The source declaration must match the exact independently audited package and coverage. Startup checks structure and file availability; the first job performs source and semantic admission. A syntactically valid configuration can therefore still produce a failed first job if its review is stale, pending or unsupported.

The example's declaration explicitly accepts only authored synthetic fixture records. It was supplied with the fixture; the launcher does not author acceptance decisions. For another dataset, prepare the bounded source-fidelity package, obtain the corresponding explicit declaration and reviewed mapping pack, then configure those files. Existing [source review](reviewed-arterial-demo.md) and mapping procedures apply. A source-fidelity decision does not establish clinical terminology equivalence or reviewer identity.

## Lifetime and provenance

The loader freezes the request, declaration and mapping-file byte digests at startup. The configuration context also binds the configuration bytes, supplied label, profile and loader/schema artifacts. It is incorporated into the pressure session identity and retained in inspected evidence. Local configuration paths are absent from the public configuration metadata; source mode is `configured-records`, which makes no publisher-authenticity claim.

Every job checks frozen inputs before work and again before publishing a successful result, including complete-result cache hits. Detected configuration/request/review/mapping changes clear preparation and result caches and invalidate that configured server until restart, even if the old bytes are restored. Source and implementation checks remain in force. Files are not watched between requests: detection occurs at validation boundaries. Previously completed jobs remain inspectable under their original evidence as historical snapshots.

The service retains one worker, existing local-origin restrictions and bounded prepared-batch storage. It supports supplied records within the existing source and query limits, not arbitrary schemas, unit conversion, pooled measurement items, publisher authentication or row-qualified clinical mappings. A recorded segment start still does not establish treatment-course initiation, causality or clinical absence.

## Verification

```sh
python -m unittest discover -s demo -p 'test_configured_pressure.py'
node demo/test_configured_pressure_ui.cjs
```

Thirteen integration tests cover input structure/limits, alternate source and item, immutable configuration, prepared/fresh equivalence, startup and mid-job review changes, cache invalidation, pending/stale reviews, actual HTTP inspections, local-origin checks and request envelope enforcement. The JavaScript test runs the exact concatenated page script with actual service metadata and checks one initialization, labels, escaping, defaults, limits and submitted controls. It is a DOM-state test, not visual browser verification.

The real HTTP test writes aggregate evidence to `verification/configured-pressure-run/report.json`; demo CI uploads it as `configured-pressure-http-evidence`. It records configuration and implementation provenance, aggregate metrics, five retained stays and three HTTP inspections equal to fresh execution, without patient rows or identifiers. The full demo suite is 80 tests; the existing contract suite remains 781 checks. No new performance claim is made for supplied clinical records.
