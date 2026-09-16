# Running and validating

Every executable component, how to run it, and what correct output looks like. All commands run offline after setup and need no Java, clinical dataset, AI provider credentials or subscription.

The v2 formal definition has a separate [OWL validation evidence package](temporal-kg/validation/README.md) with its original 18 checks. Reproducing that optional archival package requires Java; the Python profile commands on this page retain their existing dependencies.

## Setup

Use **Python 3.12** for the full pipeline; that is the tested environment and the lock file targets it.

```sh
python3.12 -m venv .venv
. .venv/bin/activate                 # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r patterns/requirements.lock.txt
```

Installation needs network access or a local package cache. Everything after it is offline.

The standalone oracle needs **no dependencies at all** and runs on Python 3.10 or newer — CI verifies 3.10, 3.11, 3.12 and 3.13.

## Quick check

```sh
python reference_oracle.py           # 16 cases, 7 properties
python -m patterns.pro_solid         # point-anchor pipeline
python -m patterns.test_pro_solid    # 42 tests

python -m patterns.exact_intervals       # interval pipeline
python -m patterns.test_exact_intervals  # 51 tests

python -m patterns.interval_cohort       # cohort query
python -m patterns.test_interval_cohort  # 18 tests

python -m patterns.bounded_cohort         # bounded uncertainty query
python -m patterns.test_bounded_intervals # 22 tests

python -m patterns.bounded_rdf            # bounded RDF input route
python -m patterns.test_bounded_rdf       # 21 tests
python -m patterns.evidence_selection     # availability and revision selection
python -m patterns.test_evidence_selection # 29 tests
python -m patterns.temporal_interface      # paired formal/SULO fixtures
python -m patterns.test_temporal_interface # 22 tests
```

All commands above exit 0. **228 base checks:** 205 suite tests, 16 oracle cases, and seven properties. Any nonzero exit requires inspecting the reported status. For the interval profiles, exit code 2 means invalid profile input or inconsistent source constraints; the selector also uses it for blocked evidence, with its audit report preserved.

The default [evidence-selection example](evidence-selection.md) yields `READY`, with P1 certain in the nested matcher result. Repeating it with `--request examples/evidence-selection/retrospective-request.json` yields `READY` with no certain patients and `later_evidence_used: true`. Use a different `--output` directory to preserve both reports. The 29-test selection suite includes 60 seeded source histories and selected-answer comparisons with the finite-world reference.

---

## Component 1 — Reference oracle

```sh
python reference_oracle.py
```

**Verifies:** 16 declarative cases with independent expected acceptance, costs and baseline choices, plus 7 invariant properties.

**Expected output**

```json
{"cases_passed": 16, "properties_passed": 7, "report": "verification/reference-report.json"}
```

**Writes:** `verification/reference-report.json`, with a per-case `passed` flag and the actual binding.

**Properties checked:** cost decomposition · zero-cost exact equivalence · subclass direction · budget monotonicity · not-given exclusion · incomplete-source propagation · unsupported-pattern rejection.

**Dependency check.** This must work in a bare interpreter. If it fails outside the venv, a third-party import has leaked into the core:

```sh
/usr/bin/python3 reference_oracle.py
```

---

## Component 2 — PRO/SOLID pipeline

```sh
python -m patterns.pro_solid
```

**Verifies:** the full construct → derive → validate → project → match path over the synthetic fixture.

**Expected output**

```json
{"events": 3, "accepted_as": "EXACT", "total_cost": "0", "output": ".../verification/pro-solid-run"}
```

**Writes four files** to `verification/pro-solid-run/`:

| File | Contents |
|---|---|
| `graph.ttl` | The instance graph, 131 triples |
| `matcher-case.json` | Projected Event DTOs |
| `evidence.json` | Binding evidence with `assertion_id` per row |
| `match.json` | Match result with decomposed cost |

### Validating existing RDF

To validate and project committed Turtle instead of building from rows:

```sh
python -m patterns.pro_solid --graph examples/pro-solid/graph.ttl
```

This must produce the **same** result as the row-based run. A divergence means the graph and the adapter disagree about the fixture.

### Checking reproducibility

The regenerated graph should be isomorphic to the committed copy:

```sh
python .github/scripts/check_graph.py
```

```
graph: isomorphic to committed example (131 triples)
```

This compares RDF triples, not text, so serialization order and prefix choice do not matter. On failure it prints the differing triples on both sides.

### Checking input integrity

```sh
python .github/scripts/check_manifest.py
```

```
manifest: checked 9 files, 0 problem(s)
```

Verifies the nine SHA-256 digests in `verification/v24-release-manifest.json`. These cover adapter inputs — code, ontology, shapes, pin and fixtures — not generated reports.

---

## Component 3 — Acceptance suite

```sh
python -m patterns.test_pro_solid
```

**Verifies:** 42 tests across projection and schema compatibility, PRO derivation, role integrity, SOLID literal discipline, values and units, time handling, observation identity, matching semantics, evidence and unsupported statuses.

**Expected:** `Ran 42 tests`, `OK`, exit 0.

**Writes:** `verification/v24-pro-solid-report.json` with `passed: true` and `production_readiness_claim: false`. Exits nonzero on any failure.

> The report records the runner's Python version, so it changes when your interpreter patch version differs from the release. This is expected and does not affect manifest integrity.

### Checking a single behaviour

```sh
python -m unittest patterns.test_pro_solid.PatternAcceptance.test_subclass_entailment_is_exact -v
```

Useful checks when changing the profile:

| To confirm | Test |
|---|---|
| Role binding survives derivation | `test_pro_chain_is_derived_without_losing_role_binding` |
| Generic participation is insufficient | `test_generic_participation_is_insufficient` |
| Literals cannot bypass SOLID | `test_literal_directly_on_person_rejected` |
| Timezones are never assumed | `test_unzoned_datetime_not_silently_utc` |
| Entailment costs nothing | `test_subclass_entailment_is_exact` |
| Alternatives cost one | `test_reviewed_semantic_relaxation_has_cost` |

---

## Component 3 — Exact-interval pipeline

```sh
python -m patterns.exact_intervals
```

**Verifies:** interval construction, PRO/SOLID validation, clock handling and nine explicit temporal comparisons.

**Expected output**

```json
{"profile": "exact-interval-1.0", "intervals": 4, "comparisons": 9,
 "statuses": {"contact": "SATISFIED", "contact_is_not_before": "NOT_SATISFIED",
              "before": "SATISFIED", "overlap": "SATISFIED",
              "reverse_overlap": "NOT_SATISFIED", "ten_minute_gap": "SATISFIED",
              "zero_gap": "SATISFIED", "gap_too_large": "NOT_SATISFIED",
              "overlap_has_no_nonnegative_gap": "NOT_SATISFIED"}, ...}
```

**Writes** to `verification/exact-interval-run/`: `graph.ttl`, `intervals.json`, `evidence.json`, `comparisons.json`. This directory is gitignored; the stable example graph is committed at `examples/exact-interval/graph.ttl`.

The nine results are not arbitrary. They pin the distinctions that matter: `A meets B` is satisfied while `A before B` is not; `A overlaps C` is satisfied while `C overlaps A` is not, because the relation is directional; and `A → C nonnegative gap` fails because overlapping intervals have a negative signed gap.

### From committed RDF

```sh
python -m patterns.exact_intervals --graph examples/exact-interval/graph.ttl
```

Must produce identical interval records and evidence to the row-based run. The suite checks this.

### Conformance suite

```sh
python -m patterns.test_exact_intervals   # 51 tests, writes verification/exact-interval-report.json
```

> Invalid profile input exits with **code 2** and an `INVALID_INPUT` diagnostic, and does **not** overwrite existing output files. Check the exit code before reading output.

---

## Component 4 — Interval cohort matching

```sh
python -m patterns.interval_cohort
```

**Expected output**

```json
{"profile": "interval-cohort-1.0", "matched_patient_ids": ["P1"], "trajectories": 3,
 "output": ".../verification/interval-cohort-run/result.json"}
```

The three-patient example is designed so each outcome appears once:

| Patient | Verdict | Why |
|---|---|---|
| P1 | `MATCH` | Bindings A → D and C → D satisfy every constraint |
| P2 | `NO_RECORDED_MATCH` | Its infusion occurs after its collection |
| P3 | `INCOMPARABLE` | Its candidate events use different clock resources |

P3 is the important one. Different clocks mean the query **could not be decided**, which is neither a match nor an absence.

### Differential check

```sh
python -m patterns.interval_cohort --engine reference --output verification/interval-cohort-run/reference.json
python -m patterns.test_interval_cohort   # 18 tests
```

The indexed and reference engines must agree on the semantic result while reporting different execution counters. If they diverge, the index is wrong — the reference engine is the specification. This is the strongest correctness check in the repository; run it after any change to the matcher or its indexes.

Other flags: `--source`, `--graph`, `--manifest`, `--query`, `--output`.


---

## Bounded uncertainty pipeline

```sh
python -m patterns.bounded_cohort
python -m patterns.test_bounded_intervals  # 22 tests
```

**Expected:** `certain_patient_ids: ["P1"]`, `possible_patient_ids: ["P1", "P2"]`. The four episodes yield CERTAIN_MATCH, POSSIBLE_MATCH, NO_RECORDED_MATCH, and INCOMPARABLE respectively. The output directory `verification/bounded-interval-run/` contains `graph.ttl` and `result.json`.

The suite independently enumerates small source-variable domains and replays returned certificates. It includes 250 seeded network comparisons, 200 query conjunctions, 144 singleton/operator comparisons with the exact evaluator, shared-anchor and fixed-witness counterexamples, and CLI failures. These nested scenarios are included within the 22 tests, not additional entries in the top-level total.

A contradictory source emits `INCONSISTENT_SOURCE` and exits 2 with a negative-cycle certificate. Other source/query violations emit `INVALID_INPUT`. Neither overwrites prior successful results; check exit status before consuming files. This command accepts source JSON; the RDF input route is described below. See the [contract](bounded-temporal-uncertainty.md).

---

## Bounded RDF ingestion

```sh
python -m patterns.bounded_rdf
python -m patterns.bounded_rdf --graph verification/bounded-rdf-run/graph.ttl --output verification/bounded-rdf-run/reloaded
python -m patterns.test_bounded_rdf  # 21 tests
```

The export and RDF reload routes produce the same full result: certain P1, possible P1/P2, with preserved PRO and source evidence. The suite covers arbitrary instance IRIs, lexical forms, declared hashes, inverse role links, complete triple coverage, malformed RDF, scope violations, and inconsistency certificates. Outputs are `graph.ttl` and `result.json`; invalid or inconsistent inputs exit 2. See the [input contract](bounded-rdf-ingestion.md).

---

## Component 5 — Ontology and shapes

The pipeline loads and enforces these on every run, so a successful `pro_solid` run already validates them. To check syntax independently:

```sh
python -c "
from rdflib import Graph
for f in ['ontology/pro-solid-profile.ttl','ontology/pro-solid-shapes.ttl','ontology/vendor/sulo-0.2.14.ttl','examples/pro-solid/graph.ttl']:
    print(f, len(Graph().parse(f)), 'triples')
"
```

The SULO digest is checked at load. A mismatch raises `ContractError('SULO_PIN_MISMATCH')`.

Do **not** load `ontology/legacy-2.3/` with the current profile; those drafts are non-normative.

---

## Component 6 — SPARQL projection

`examples/pro-solid/measurement-bindings.rq` demonstrates a directly executable projection that retains patient and result role bindings:

```sh
python -c "
from rdflib import Graph
g = Graph().parse('examples/pro-solid/graph.ttl')
for row in g.query(open('examples/pro-solid/measurement-bindings.rq').read()):
    print(row)
"
```

Covered by `test_sparql_example_keeps_subject_and_result_context`.

---

## Component 7 — Schemas

Structurally validated at authoring time; results are in `verification/structural-report.json`. To re-check:

```sh
python -c "
import json, jsonschema
s = json.load(open('schemas/contracts.schema.json'))
jsonschema.Draft202012Validator.check_schema(s)
print('contracts.schema.json: valid draft 2020-12,', len(s.get('\$defs', {})), 'definitions')
o = json.load(open('schemas/openapi.json'))
print('openapi.json:', o['openapi'], '-', len(o['paths']), 'paths')
"
```

No service implements these paths.

---

## Optional checked Rust semantic support

```sh
python -m pip install -r patterns/requirements-semantic.lock.txt
python -m patterns.semantic_support
python -m patterns.test_semantic_support
```

This adds 22 tests using rustDL 0.4.28 and the independent finite evaluator: **250 checks with semantic support**, comprising 227 suite tests, 16 oracle cases and seven properties. The semantic example and tests run in their own CI job. See [semantic-support.md](semantic-support.md) for the admitted fragment, source pairing, failure statuses and evidence. A backend problem returns no cohort answer. The original 18 standalone Java/OWL checks remain a separate evidence set and count.

## Joint temporal and semantic evidence selection

```sh
python -m patterns.joint_evidence
python -m patterns.test_joint_evidence
```

These use the same optional Rust dependency lock and add 28 tests, bringing the repository total to **278 checks**: 255 suite tests, 16 oracle cases and seven properties. The 24 generated revision histories are nested scenarios, not additional top-level checks. [Joint selection](joint-evidence-selection.md) runs in the Rust CI job with three replay examples; the result audit is uploaded alongside the semantic reasoning evidence. The standalone 18 OWL checks remain separate.

## MIMIC inputevents source staging

```sh
python -m patterns.mimic_inputevents
python -m patterns.test_mimic_inputevents
```

This adds 28 tests: **306 checks in total**, comprising 283 suite tests, 16 oracle cases and seven properties. Synthetic cases exercise CSV/schema failures, identity joins, duplicate reconciliation, component/segment separation, unsupported records, shifted patient clocks, precision and availability boundaries, and provenance. CI also verifies that the committed aggregate demo report binds the current adapter and pinned source hashes. It does not download patient data.

The optional public-demo reproduction command is documented in [MIMIC staging](mimic-inputevents-staging.md). That local run reconciles 20,404 input records; those records are not additional top-level tests. Staging success exits 0 while explicitly returning no matcher answer. The original 18 formal OWL checks remain a separate evidence set.

## Patient-local clocks

```sh
python -m patterns.patient_local
python -m patterns.patient_local --graph verification/patient-local-run/graph.ttl
python -m patterns.test_patient_local
```

This adds 27 tests, bringing the total to **333 checks**: 310 suite tests, 16 oracle cases and seven properties. The 24 generated domain scenarios, each checked with four operators, are nested checks. Tests cover exact local coordinate conversion, patient isolation, origin rebasing, date-shift invariance, raw/RDF coordinate agreement, finite-world and proof checks, shared uncertainty, profile separation and CLI round trips. They run in the base CI job. No patient data or new optional dependency is required; see the [contract](patient-local-clocks.md).

## End-to-end MIMIC recorded-source query

```sh
python -m patterns.mimic_record_query
python -m patterns.test_mimic_record_query
```

These require the existing optional Rust dependency lock and run in the Rust CI job. The 24 new tests bring the total to **357 checks**: 334 suite tests, 16 oracle cases and seven properties. The [record-query contract](mimic-record-query.md) documents the independently verified public-demo run and its reproduction command. CI validates synthetic cases and committed aggregate provenance without downloading patient data.

## Interpreting results

| Outcome | Meaning |
|---|---|
| `accepted_as: EXACT`, cost 0 | Pattern satisfied with no relaxation |
| `accepted_as: RELAXED`, cost > 0 | Satisfied using a priced relaxation within budget |
| `accepted_as: NONE` | No accepted trajectory in the recorded evidence |
| `accepted_as: UNRESOLVED` | Insufficient information to decide |
| `ContractError` | Profile violation — **not** a statement about the patient |
| `SATISFIED` / `NOT_SATISFIED` | One exact temporal constraint on a selected recorded pair |
| `MATCH` / `NO_RECORDED_MATCH` | Cohort verdict for a patient episode |
| `INCOMPARABLE` | Clocks do not establish a mapping — undecided, not absent |
| `CERTAIN_MATCH` / `POSSIBLE_MATCH` | A fixed binding holds in every source timeline / some source timeline |
| `INCONSISTENT_SOURCE` | No feasible bounded source timeline; not a clinical no-match |
| exit code 2 | Invalid input or inconsistent bounded source; existing output files are left untouched |

The distinction in the last two rows matters. A `ContractError` means the data fell outside the supported profile and projection stopped. `UNRESOLVED` means the search could not reach a decision — for example when `source_search_complete` is false. Neither is evidence of clinical absence. See [architecture.md §3](architecture.md#3-the-three-level-constraint-model).

## Continuous integration

[`.github/workflows/contracts.yml`](../.github/workflows/contracts.yml) runs everything above on each push and pull request:

1. Verify the nine release manifest digests
2. Reference oracle — 16 cases, 7 properties
3. Point-anchor pipeline from synthetic source rows
4. Graph isomorphism against the committed copy
5. Point-anchor pipeline from the committed graph
6. Acceptance suite — 42 tests
7. Exact-interval pipeline and conformance suite — 51 tests
8. Interval cohort example and differential suite — 18 tests
9. Bounded uncertainty example and finite-world/certificate suite — 22 tests
10. Bounded RDF example and graph-validation/source-equivalence suite — 21 tests
11. Report generated-file drift as a notice
12. Upload `verification/` as a build artifact

A second job runs the dependency-free oracle on Python 3.10, 3.11 and 3.13.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `SULO_PIN_MISMATCH` | The vendored ontology does not match `sulo-pin.json` |
| `UNSUPPORTED_OR_UNZONED_TIME` | Datetime lacks an explicit offset, or exceeds six fractional digits |
| `SOLID_LITERAL_PROPERTY` | A literal on a predicate other than `sulo:hasValue` |
| `ROLE_CARDINALITY:PatientRole` | Not exactly one patient role on a process |
| `MEASUREMENT_SUBJECT_MISMATCH` | The measured quality's bearer is not the process's patient |
| Exit code 2 from an interval command | Read `INVALID_INPUT`, or the bounded profile's `INCONSISTENT_SOURCE` certificate |
| Indexed and reference engines disagree | The index is wrong; the reference engine is the specification |
| `pip install` fails on 3.13+ | The lock file targets 3.12; use `python3.12` |
| Dirty tree after a run | Expected: report files record the interpreter version |

## Structured claims and controlled projection

```sh
python -m patterns.claim_projection
python -m patterns.test_claim_projection
```

These use the existing optional Rust dependency lock and run in the Rust CI job. The 45 new tests bring the total to **402 checks**: 379 suite tests, 16 oracle cases and seven properties. The default synthetic run returns `READY`, P1 certain, and `VERIFIED_EMPTY_PROCESS_MODEL` for the separate claim graph. The [contract](claim-projection.md) explains the pinned-SULO model check, accepted-view limitations, decisions, support dependencies, bounds and RDF input route. No clinical approval or original source-history verification is implied.

## Patient-local claim projection

```sh
python -m patterns.local_claim_projection
python -m patterns.test_local_claim_projection
```

The 32 new tests bring the total to **434 checks**: 411 suite tests, 16 oracle cases and seven properties. Both synthetic local and recorded examples run in the Rust CI job. The [contract](local-claim-projection.md) includes the recorded example command, source/normalized evidence distinction and local-time interpretation. The original 45 claim tests continue to validate the offset profile and base isolation closure.

## MIMIC pending claim import

```sh
python -m patterns.mimic_claim_import
python -m patterns.test_mimic_claim_import
```

The 25 import tests bring the current total to **459 checks**: 436 suite tests, 16 oracle cases and seven properties. They cover the file-to-description boundary, full row accounting, provenance and limits, plus explicit synthetic acceptance and withdrawal through the checked Rust route. CI runs the synthetic import and checks the committed public-demo aggregate hashes; patient data are not downloaded. See the [import contract](mimic-claim-import.md) for local public-demo reproduction and interpretation limits.

## Measurement point claims

```sh
python -m patterns.mimic_measurement_import
python -m patterns.test_measurement_claims
python -m patterns.verify_measurement_claims
```

The 35 measurement tests bring the current total to **494 checks**: 471 suite tests, 16 oracle cases and seven properties. They cover exact decimal preservation, point bounds, explicit selection/correction/withdrawal, profile rejection, the 141-axiom isolation closure, bounded chartevents admission, source evidence and complete ledger outcomes. The [measurement contract](measurement-claims.md) describes the synthetic-only evaluation and the remaining mixed point/interval query work. The original claim reports and public-demo inputevents claim report were reproduced to bind the updated shared encoding/checker hashes.

## Mixed interval and point record queries

```sh
python -m patterns.mixed_record_query
python -m patterns.test_mixed_record_query
python -m patterns.verify_mixed_record_query
```

The 34 mixed-query tests bring the current total to **528 checks**: 505 suite tests, 16 oracle cases and seven properties. They include real Rust treatment support, independent exhaustive uncertain timelines, explicit clock alignment, exact scalar predicates, half-open interval membership, fixed-witness certainty, and eligibility that survives missing or lower follow-up values. The [contract](mixed-record-query.md) defines the selected-record scope and remaining clinical/coverage limits.

## Source mixed query and independent SQL comparison

Requires the same pinned semantic dependencies as the mixed query.

```sh
python -m patterns.source_mixed_query
python -m patterns.source_mixed_query --review examples/source-mixed-query/synthetic-review.json
python -m patterns.test_source_mixed_query
python -m patterns.verify_source_mixed_query
```

Expected: preparation accepts no claims; the explicitly constructed review yields `COMPLETED_VERIFIED_SOURCE_QUERY`, synthetic patients 1, 2 and 3 eligible, all five stays retained, and agreement on all baseline/follow-up bindings. Both source ledgers reconcile all 5 inputevent and 9 measurement rows. The 28 tests cover review/source staleness, selection/withdrawal, alignment, failure suppression, independent SQL arithmetic and source projection corruption. The total is now 556 checks: 533 suite tests, 16 oracle cases and seven properties. Detailed local run output is ignored; the committed [synthetic report](../verification/source-mixed-query-report.json) is reproducible. See [scope and limitations](source-mixed-query.md).

## Clinical source coverage preflight

```sh
python -m patterns.clinical_source_preflight
python -m patterns.test_clinical_source_preflight
# Original public-demo ICU files must be supplied locally:
python -m patterns.verify_clinical_source_preflight --input-dir /path/to/demo/icu
```

Expected synthetic scan: 5 inputevent and 9 chart rows accounted for, three two-sided stays within the conservative count bounds, no claim acceptance or mixed query. The 20 tests cover streaming CSV/gzip completeness, source changes, limits, unchanged admission, aggregate-only output, pin verification and atomic failure behavior. Total: 576 checks (553 suite tests, 16 oracle cases, seven properties). The public-demo [aggregate report](../verification/clinical-source-preflight-demo-report.json) records all 20,404 inputevent and 668,862 chart rows. It demonstrates coverage and capacity barriers, not a completed clinical query. See [the interpretation and next gate](clinical-source-preflight.md).

## Indexed source windows and pending batches

```sh
python -m patterns.indexed_source_windows
python -m patterns.test_indexed_source_windows
# Original demo ICU files supplied locally:
python -m patterns.verify_indexed_source_windows --input-dir /path/to/demo/icu
```

Expected synthetic selection: three anchors, all windows count-bounded, original source identities retained. Twenty-four tests include real Rust and raw-row SQL agreement after explicit synthetic batch acceptance. The complete suite totals 600 checks: 577 suite tests, 16 oracle cases and seven properties. [The committed aggregate report](../verification/indexed-source-windows-demo-report.json) records 2,832 agreeing indexed/direct windows over three separate measurement strata; blocked anchors remain explicit, with no real-source mixed query or claim acceptance. See [the contract](indexed-source-windows.md).

## Partitioned window execution

```sh
python -m patterns.partitioned_window_query --prepare-review
python -m patterns.test_partitioned_window_query
# Original pinned demo files, aggregate plan verification only:
python -m patterns.verify_partitioned_windows --input-dir /path/to/demo/icu
```

Preparation emits pending policies and no accepted records. The 27 tests check all window sizes 0–128, missing cross-block pairs, exact Rust/SQL execution of a 38-point window, duplicate and missing-follow-up merging, review consistency, stale hashes, withdrawals, blocked batches, complete stay accounting, CLI atomicity and report provenance. The complete suite totals **627 checks: 604 suite tests, 16 oracle cases and seven properties**. The [aggregate report](../verification/partitioned-window-demo-report.json) verifies 2,870 planned batches for all 2,832 demo anchors across three separate strata, with no real-source acceptance or mixed query. See [review instructions and scope](partitioned-window-query.md).

## Unique source-claim review

```sh
python -m patterns.unique_claim_review
python -m patterns.unique_claim_review --review examples/unique-claim-review/synthetic-review.json --execute
python -m patterns.test_unique_claim_review
# Original pinned demo files; aggregate package and pending-policy verification only:
python -m patterns.verify_unique_claim_review --input-dir /path/to/demo/icu
```

The 28 tests cover source evidence, unique claim membership across batches and anchors, revision propagation, deterministic compilation, stale inputs, pending/incomparable decisions, resource and lifecycle gates, and actual Rust/SQL execution. The full suite totals **655 checks: 632 suite tests, 16 oracle cases and seven properties**. The [synthetic report](../verification/unique-claim-review-synthetic-report.json) verifies nine explicitly selected fabricated claims. The [demo report](../verification/unique-claim-review-demo-report.json) validates all 2,870 expanded pending batch policies without real-source acceptance or query execution. See [the review workflow](unique-claim-review.md).

## Source-fidelity audit and reviewed arterial query

```sh
python -m patterns.test_reviewed_source_query
# Supply the original pinned demo files; writes aggregate evidence only:
python -m patterns.reviewed_source_query --input-dir /path/to/demo/icu --request examples/indexed-source-windows/demo-arterial-request.json --declaration data/arterial-source-fidelity-review.json --output verification/reviewed-source-query-run/arterial-report.json
```

Fifteen tests check independent source parsing/mapping, tampering and stale contexts, explicit declaration coverage, synthetic Rust/SQL execution, aggregate failure semantics, CLI protection and real-demo report provenance. The full suite totals **670 checks: 647 suite tests, 16 oracle cases and seven properties**. CI verifies synthetic execution and the committed report's hashes without downloading real source records. [The demonstration](reviewed-arterial-demo.md) explains the explicit automated source-fidelity review and the separate, unverified clinical interpretation.

## Three separately reviewed pressure strata

```sh
python -m patterns.test_reviewed_pressure_strata
```

Three additional tests verify all three execution reports against current implementation hashes, pinned source manifests, exact separate declarations and requests, common query criteria and completed accounting. Total: **673 checks: 650 suite tests, 16 oracle cases and seven properties**. The [comparison and reproduction commands](reviewed-pressure-strata.md) cover each separate real-source execution; CI checks committed provenance and synthetic execution without downloading the source files.

## Pressure-cohort overlap

```sh
python -m patterns.test_pressure_overlap
# Original pinned demo files; reruns all three separately declared source queries:
python -m patterns.verify_pressure_overlap --input-dir /path/to/demo/icu
```

Fourteen tests check actual synthetic item strata, all 4,096 membership combinations over four members, patient/stay/segment scope, completeness and comparability failures, deterministic aggregation, CLI atomicity and committed provenance. Total: **687 checks: 664 suite tests, 16 oracle cases and seven properties**; the 4,096 combinations are nested within one test. The [reproduced overlap report and interpretation](pressure-cohort-overlap.md) distinguish separate-query membership from a pooled measurement query. CI does not download source records.

## Local pressure-query UI and API

```sh
python -m unittest discover -s demo -p 'test_*.py'
node demo/test_guided_ui.cjs
node demo/test_pressure_ui.cjs
```

The initial pressure UI/API added 15 Python tests; the current demo suite has 40 tests including the cache increment below, separate from the 687 contract checks. Pressure tests cover altered-window execution and SQL equivalence, source/review changes, exact evidence, complete-result gates, local HTTP controls and the committed public-demo aggregate. DOM-state checks include failed and stale requests. The [runbook](live-pressure-inspector.md) documents source setup, live arterial verification and the remaining browser rehearsal.


## Completed pressure-query cache

The [pressure cache contract](pressure-query-cache.md) adds 11 Python tests to the 29-test demo suite (40 total), separate from the 687 contract checks. It checks exact result/evidence reuse, fresh execution for changed controls, pre- and post-lookup source/review/implementation invalidation, failure suppression, bounded eviction, preparation switches and inspector response isolation. The pressure DOM test checks the reuse label and zero fresh-execution time.

`demo/benchmark_pressure_cache.py` reproduces cold preparation, a second fresh graph/SQL query over the prepared source, and three cache hits. It compares all result fields except elapsed time and all anchor inspections exactly. The committed synthetic and public arterial reports contain aggregate evidence and timing samples. CI checks their current implementation hashes and public-source pin without downloading source records or asserting a speed threshold. No additional ontology or temporal-core contract is introduced.


## Prepared pressure queries

The [prepared executor](prepared-pressure-queries.md) adds 11 demo tests (51 demo tests total). Differential checks compare full results and source/proof contexts; existing finite-world fixtures independently test uncertain temporal semantics. Further tests cover changed key inputs, fresh SQL evaluation, corrupted prepared outputs, invalidation, cache bounds and copied certificates. Current synthetic/public arterial benchmark reports bind implementation hashes and compare every anchor inspection. The live and prior result-cache reports are reproduced against the new session hook; source reviews are unchanged.


## Reviewed record mappings

```bash
python -m unittest patterns.test_reviewed_record_mappings
python -m patterns.verify_reviewed_record_mappings
python -m patterns.reviewed_record_mappings --output /tmp/reviewed-record-policy.json
```

Eighteen tests cover mapping decisions and hashes, record/concept boundaries, source-policy separation, real Rust mixed-query execution, no-rule control, pending clinical worksheet and committed report reproduction. The contract total is 705 checks: 682 suite tests, 16 oracle cases and seven properties. The separate demo suite remains 51 tests. See [the profile](reviewed-record-mappings.md) for compiler exit semantics and its clinical integration limits.


## Source-verified record catalogues

```bash
python -m unittest patterns.test_source_record_catalogue
python -m patterns.verify_source_record_catalogue
```

Eighteen tests cover pinned source correspondence, dictionary/class identity, claim/clock tampering, review separation, CLI atomicity, synthetic Rust/SQL equality and aggregate public-demo provenance. The contract total is 723 checks: 700 suite tests, 16 oracle cases and seven properties. The demo suite remains 51 tests. See [the runbook](source-record-catalogue.md) for public-source reproduction and the pending terminology handoff.


## Reviewed measurement selectors

```bash
python -m unittest patterns.test_reviewed_measurement_mappings
python -m patterns.verify_reviewed_measurement_mappings
python -m patterns.reviewed_measurement_mappings --output /tmp/mapped-measurements.json
```

Eighteen tests cover review/context gates, checked catalogue support, separate item/unit strata, source policy and alignment boundaries, uncertainty, failure suppression, CLI behavior and exact literal/SQL reproduction. The contract total is 741 checks: 718 suite tests, 16 oracle cases and seven properties. See [the profile](reviewed-measurement-mappings.md) for its declared reasoning scope and pending clinical mappings.

## Measurement source catalogue and claim audit

```bash
python -m unittest patterns.test_measurement_source_catalogue
python -m patterns.verify_measurement_source_catalogue
```

Twenty tests check pinned files, dictionary provenance, exact claim reproduction, clock origins before item filtering, unsupported/duplicate rows, subset semantics, source-policy separation, CLI failure behavior and committed raw-CSV/Rust/literal/SQL evidence. The current contract total is 761 checks: 738 suite tests, 16 oracle cases and seven properties. See [the profile](measurement-source-catalogue.md).

## Prepared measurement session equivalence and invalidation

```bash
python -m unittest patterns.test_prepared_measurement_session
python -m patterns.verify_prepared_measurement_session
```

Twenty tests cover cold admission, eliminated repeated work, defensive copies, source/implementation changes, review/backend gates, budgets, fixed selector scope, uncertainty, alignment, partial failure and closure. The report compares 12 prepared results with fresh source queries and raw-CSV SQL. The current contract total is 781 checks: 758 suite tests plus 23 oracle checks. See [the session profile](prepared-measurement-session.md).

## Live mapped pressure jobs and cache lifecycle

```bash
python -m unittest discover -s demo -p 'test_mapped_pressure.py'
python demo/benchmark_mapped_pressure.py --output verification/mapped-pressure-run/local-report.json
```

Sixteen tests cover reviewed mapping gates, live/fresh query and inspection equality, cache invalidation on review/source/implementation changes, SQL disagreement, query envelopes, malformed input, budgets, eviction and defensive copies. Demo CI uploads the aggregate result of the real HTTP test. The demo suite totals 67 tests; the contract total remains 781. [The runbook](mapped-pressure-service.md) distinguishes local timing observations from a performance guarantee.
