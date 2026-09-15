# Patient Trajectory Matching Contract Pack

[![contracts](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/actions/workflows/contracts.yml/badge.svg)](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/actions/workflows/contracts.yml)

Version 2.4, 15 September 2026. Companion to product specification v2.3, extended by the v2.4 addendum in this pack.

This pack fixes the initial cohort query contract and provides executable examples. It is not the production matcher, a complete OWL reasoner, a full ETL implementation or a clinical terminology release. All patient examples and DrugA/DrugB alternatives are constructed. No MIMIC patient rows are redistributed here.

## Start here: PRO and SOLID

Read `addenda/specification-2.4.md` for the current modeling contract and a worked graph. `patterns/pro_solid.py` implements synthetic source rows → PRO/SOLID RDF → validation → matcher projection → exact/relaxed exemplar matching. Patient participation uses the PRO role/bearer path. Literal values, timestamps and source metadata use typed information objects with `sulo:hasValue`.

Use Python 3.12 for the tested environment. From this folder:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -r patterns/requirements.lock.txt
python -m patterns.pro_solid
python -m patterns.test_pro_solid
python reference_oracle.py
```

On Windows, activate with `.venv\Scripts\Activate.ps1` in PowerShell. Package installation needs network access or a local package cache. The subsequent runs are offline and require no Java, AI subscription or clinical data access.

The first command writes `verification/pro-solid-run/graph.ttl`, `matcher-case.json`, `evidence.json` and `match.json`. The default fixture returns **EXACT, cost 0**. Tests also exercise the cost-1 semantic alternative. A copy of the generated graph is provided at `examples/pro-solid/graph.ttl`. To validate and project that RDF directly, run:

```sh
python -m patterns.pro_solid --graph examples/pro-solid/graph.ttl
```

The new files are `ontology/pro-solid-profile.ttl`, `ontology/pro-solid-shapes.ttl`, the pinned SULO ontology and manifest, `examples/pro-solid/`, `patterns/`, and `verification/v24-pro-solid-report.json`. The v2.4 gate certifies only the executable profile described in the addendum. Historical ontology drafts have moved to `ontology/legacy-2.3/` and must not be loaded as current ontology contracts.

## Version 2.3 additions

ADR23 adds the Zep/Graphiti assessment, observation-versus-correction semantics, temporal replay and a six-hour synthetic comparison protocol. `addenda/specification-2.3.md` contains the new design. `ui/temporal-replay-2.3.json` specifies controls and identity. `examples/temporal-replay-cases-2.3.json` contains eight declarative case families. `evaluation/graphiti-comparison-2.3.json` is the planned experiment. Eleven requirements and gates AC17/AC18 extend traceability. These are specifications, not implemented replay or measured Graphiti results. All prior executable contracts remain unchanged.

## Version 2.2 additions

Section 20 now specifies the coordinated patient workspace, semantic and time controls, asynchronous states, near-match explanations, revision history, accessible layouts and a formative usability study. `addenda/specification-2.2.md` contains the detailed design. `ui/` contains two annotated wireframes, design tokens, an eight-state storyboard and interaction contracts. These are specified assets, not a running UI or measured usability results. Fourteen UI requirements and gate AC16 extend traceability. The v2.0 executable oracle, schemas and fixtures are unchanged.

## Version 2.1 additions

The document now leads with clinical examples and an iterative patient-to-cohort workflow. The pack adds `addenda/specification-2.1.md`, a specified toy query-by-example profile, refinement-session example, 16 declarative time-normalization cases and a full MIMIC-IV study plan. These extensions are specified, not implementation-tested. Existing v2.0 schemas and OpenAPI still describe only their original executable cohort profile. The 16 passing matcher cases are separate from the 16 new normalization expectations.

## Contents

- `schemas/contracts.schema.json`: JSON Schema draft 2020-12 definitions for initial event, pattern, request, result, evidence and job resources. Cross-field invariants still require semantic validation.
- `schemas/openapi.json`: OpenAPI 3.1 initial cohort API, with the same embedded schemas. Later similarity, workspace and bundle-administration resources remain in the product specification.
- `examples/exemplar.pattern.json`: canonical exemplar AST. All listed constraints are hard unless explicitly relaxed. All slots in this initial profile are required and distinct.
- `examples/cases.json`: 16 input cases with independent expected acceptance, costs and baseline choices.
- `reference_oracle.py`: deliberately small, exhaustive exemplar oracle with no third-party dependencies. Supports exact baseline and follow-up point times and bounded uncertain exposure point times. Rejects unsupported patterns instead of silently approximating them.
- `ontology/`: a toy subclass hierarchy, PRO/SOLID application classes, runnable SHACL profile and hashed SULO 0.2.14 source. The `ex:` prefix in current examples denotes `https://example.org/trajectory/toy/`. The JSON hierarchy remains the oracle's only reasoning input. The new adapter executes the narrower derivation profile specified in v2.4; complete SULO/rustDL capability verification remains release work.
- `data/`: dataset roles and an inspected demo source inventory with hashes of decompressed CSVs.
- `requirements.csv`: original specified requirements plus PS-001–PS-010, which are implemented and fixture-verified only within the bounded v2.4 profile.
- `verification/`: report from running the supplied reference cases and structural validation. These results certify this pack's internal consistency only.

## Run

From this folder, use Python 3.10 or newer:

```sh
python reference_oracle.py
```

The command checks the fixed expectations and invariants and writes `verification/reference-report.json`. It requires no network, Java, clinical dataset, AI API or subscription. Python `jsonschema` and `rdflib` were additionally used during authoring to validate schemas and RDF syntax; those are not required by the oracle.

## Semantics

Use decimal values and costs, integer microseconds and a declared clock. Exact has cost zero. An entailed subclass is exact. An explicitly reviewed synthetic alternative costs one. Seven days is the exact exposure limit; up to two extra days cost linearly up to one. The value rise and 48-hour lab window are hard. A prescription or not-given event cannot satisfy administration.

The oracle searches recorded evidence. Missing baseline in a complete selected record scope is a record-query failure, not proof of clinical absence. An unresolved eligible value or time is separately surfaced. It never claims that this creatinine branch is a complete AKI phenotype or that the exposure caused the lab change.

For uncertain exposure time, definite relaxed acceptance uses the worst cost over feasible point times. This pack does not implement general interval-constraint solving, arbitrary selectors, NOT_RECORDED, optionality, deletion, ANN, patient similarity, raw clinical source transformation or complete temporal DL certain-answer semantics. The v2.4 adapter adds only the declared synthetic source transformation and precise anchor-time profile.

## Implementation handoff

Begin with the runnable PRO/SOLID slice, then implement clinical source mapping and reconciliation, fuller ontology/time reasoning and an optimized matcher. Compare against these cases and add independently authored clinical cases. Review the pinned SULO version and actual terminology mappings before integrating the Rust reasoning stack. The v2.4 addendum distinguishes implemented behavior from remaining product work. Do not infer production readiness from passing fixture reports.

## Continuous integration

`.github/workflows/contracts.yml` runs on every push and pull request. It verifies the release manifest hashes, runs the reference oracle, executes the PRO/SOLID pipeline from both the synthetic source rows and the committed RDF graph, checks that the regenerated graph is isomorphic to `examples/pro-solid/graph.ttl`, and runs the 42-test acceptance suite on Python 3.12. A second job runs the dependency-free oracle on Python 3.10, 3.11 and 3.13.

Report files under `verification/` record the runner's Python patch version, so they are expected to differ from the committed release after a local run. CI reports that drift as a notice rather than a failure.

## License

Pack contents are MIT licensed; see `LICENSE`. The vendored SULO ontology at `ontology/vendor/sulo-0.2.14.ttl` is CC0, as recorded in `ontology/sulo-pin.json`, and is redistributed under its own terms. All patient examples and DrugA/DrugB alternatives are constructed. No MIMIC patient rows are redistributed here, and the MIMIC-IV dataset carries its own access requirements independent of this license.
