# Running and validating

Every executable component, how to run it, and what correct output looks like. All commands run offline after setup and need no Java, clinical dataset, AI provider credentials or subscription.

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
```

All eleven exit 0. **177 checks in total:** 154 suite tests, 16 oracle cases, and seven properties. Any nonzero exit is a real failure — and for the interval profiles, exit code 2 means invalid profile input or, in the bounded profile, inconsistent source constraints.

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
