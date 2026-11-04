# Patient Trajectory Matching

[![contracts](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/actions/workflows/contracts.yml/badge.svg)](https://github.com/MaastrichtU-IDS/patient-trajectory-matching/actions/workflows/contracts.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](docs/validation.md#setup)

Finding patients whose clinical history follows a specified trajectory — an exposure followed by a measured change — over a **temporal knowledge graph** that keeps time, provenance and uncertainty explicit.

The hard part is not the search. It is saying precisely what a match means when records are incomplete, timestamps are ambiguous, clinical concepts are hierarchical, and "we found nothing" might mean the patient is healthy, the data is missing, or the clocks were never comparable. This repository is a set of **executable contracts** that pin those distinctions down.

```
Given: an administration of DrugA, followed within 48 hours by a creatinine
       rise of at least 0.3 mg/dL over a baseline, exposure no more than
       seven days before follow-up

Find:  patients whose recorded evidence satisfies that trajectory — and say
       exactly which relaxations, if any, were used and what they cost
```

## Scope

- **Is:** a modeling contract for patient trajectories, a family of executable temporal profiles with their contract checks, a bounded research workspace, and the specification of the full product with implementation status marked throughout.
- **Is not:** a production matcher, a complete OWL reasoner, a clinical ETL or terminology release, or a validated clinical phenotype.

Passing fixtures do not establish production readiness; the acceptance report records `production_readiness_claim: false` deliberately. All patient examples are constructed, and no MIMIC patient rows are redistributed. Current test counts are in [status](docs/status.md); the 104 original requirements are traced in [completion](docs/completion.md).

## Quick start

Python 3.12 for the pipelines; the dependency-free oracle runs on 3.10 or newer.

```sh
python3.12 -m venv .venv
. .venv/bin/activate                  # Windows: .venv\Scripts\Activate.ps1
python -m pip install -r patterns/requirements.lock.txt

python reference_oracle.py            # the contract oracle
python -m patterns.test_pro_solid     # one profile's acceptance suite
python -m app.server                  # the research workspace on http://127.0.0.1:8080
```

Open `/journey` for the guided patient-to-cohort journey, or `/` for the similarity and refinement workspace. [Validation](docs/validation.md) lists every suite, its command and its expected output.

## Temporal profiles

Four independent executable contracts share the pinned SULO core and the PRO/SOLID representation discipline; none changes the semantics of another. Later profiles for evidence selection, claims, recorded sources and relaxation build on them; see [specifications](docs/specifications.md).

| Profile | Entry point | What it decides |
|---|---|---|
| **Point anchor** (v2.4) | `patterns/pro_solid.py` | Three-slot exemplar matching with priced relaxation, over point-in-time events |
| **Exact interval** `1.0` | `patterns/exact_intervals.py` | Pairwise temporal operators over recorded start/end intervals |
| **Interval cohort** `1.0` | `patterns/interval_cohort.py` | Conjunctive slot queries across patient episodes |
| **Bounded interval** `1.0` | `patterns/bounded_cohort.py` | Certain and possible bindings over bounded uncertain times |

## Core ideas

- **The graph wins.** The RDF graph is the semantic record; the projection is an execution representation. Every projected row carries an evidence identifier back to the process, role, bearer, result and source record it came from.
- **Participation goes through roles**, and literals through typed information objects. A trajectory joins on the same person, never the same role individual.
- **Kinds of "no" stay distinct.** An inconsistency, a profile violation, an unmatched trajectory, an incomplete search and an incomparable clock are different results, never collapsed into "does not match".

Full detail, including matching costs: [architecture](docs/architecture.md).

## Repository layout

```
patterns/        the engine: executable temporal profiles and their suites
reference_oracle.py   dependency-free contract oracle
schemas/  ontology/  conformance/   contracts: JSON Schemas, SULO and extensions, use-case conformance
examples/        committed fixtures the profiles, workspace and demos read
data/            pins, terminology evidence, clinical review packages (no patient rows)
app/             the research workspace (HTTP server and pages)
services/        runtime services the workspace uses: guided cohort, reviewed pressure queries
demos/           runnable demonstrations: presentation/ and mockups/
deploy/          container, Compose and Helm support
tools/           checkers, evaluators and builders
verification/    committed reports, each bound to the files that produced it
docs/            documentation; docs/specification/ holds design assets
addenda/         product specification addenda 2.1–2.4
```

## Documentation

Start with the [documentation guide](docs/README.md), or the [design document](docs/design.md) for the project as it stands and what remains. The most used pages are [architecture](docs/architecture.md), [components](docs/components.md), [status](docs/status.md), [validation](docs/validation.md), [issues](docs/issues.md) and [deployment](docs/deployment.md).

## Contributing

The contracts are the specification. Any change — human or AI-assisted — must keep them passing and preserve the declared supported profile.

1. Read the [architecture](docs/architecture.md), then the contract for the profile you are changing.
2. Run the suites in [validation](docs/validation.md) before and after the change.
3. If you widen a supported profile, add tests that pin the new boundary and say so.
4. Keep implemented behaviour distinct from specified behaviour in every document you touch.

CI runs every profile on each pull request, and `tools/check_report_provenance.py` fails if a committed report no longer matches, or no longer finds, the files it binds.

## Citation and provenance

Companion to product specification v2.3, extended by the v2.4 addendum in [addenda/](addenda/). The PRO and SOLID patterns follow [the SULO paper, sections 4.4.1–4.4.2](https://ceur-ws.org/Vol-4176/foust-7.pdf). The [SULO ontology](https://w3id.org/sulo/sulo.ttl) is vendored at version 0.2.14 and verified against its SHA-256 digest at load time; see `ontology/sulo-pin.json`.

## License

MIT; see [LICENSE](LICENSE). The vendored SULO ontology is CC0 under its own terms. MIMIC-IV carries its own access requirements.
