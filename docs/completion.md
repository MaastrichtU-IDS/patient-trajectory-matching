# Completion and release scope

The repository contains a working family of bounded research profiles. **It does not yet implement the complete product described by the 104 requirements.** The original requirement descriptions, release targets and legacy statuses remain unchanged in [`requirements.csv`](../requirements.csv).

The [completion register](../verification/completion-register.json) maps every original requirement ID to a current assessment, concrete evidence, remaining work and applicable external dependencies. It also lists newer executable profiles separately, including interval matching, checked semantic support, reviewed source mappings, configured pressure workloads and memory sampling. A new profile does not automatically satisfy every clause of a broader requirement.

## Read the assessment correctly

| Status | Meaning |
|---|---|
| `supported` | The original requirement is supported within its declared profile and has executable acceptance references. The ten PS requirements retain their original bounded PRO/SOLID scope. |
| `partial` | Code supports part of the original requirement. The remaining clauses are explicitly listed. |
| `blocked` | Acceptance depends on named external access, review or evaluation that has not been supplied. |
| `specification-only` | The design is present, but implementation evidence does not establish the requirement. |

These categories are not a percentage-complete metric: requirements vary greatly in scope. Passing a small synthetic profile cannot establish general OWL reasoning, clinical validity, a production all-pairs index or multi-user operation.

Reproduce the current counts and verify every evidence reference:

```sh
python tools/check_completion.py --output verification/research-prototype-run/completion.json
python -m unittest discover -s tools -p 'test_*.py'
```

Exit zero means **the account is internally consistent**, including its unresolved requirements. The output explicitly preserves `project_complete: false`, `production_ready: false` and `clinical_validity_claim: false`. It checks exact legacy row content and source-file digest, complete ID coverage, valid statuses, named dependencies, acceptance-check references and repository-local evidence files. It emits deterministic hashes of the examined evidence. It does not execute the referenced test commands or certify that source code meets its requirements. The acceptance command catalogue identifies the suites that must be run separately; CI evidence is distinct from this structural audit.

## H0 integration versus full product

[Addendum 2.1](../addenda/specification-2.1.md) requires one bounded demonstration containing all of the following:

1. Query by example with a declared feature profile, pre-index history, candidate pool and missing-feature coverage.
2. A first refinement that shows whether ranking or eligibility changed.
3. A second refinement, evaluated against the declared base population unless an explicit saved subset is selected.
4. Exact versus explicitly allowed relaxed cohort membership, with costs and unresolved states retained.
5. Source-evidence inspection preserving the patient role, process, bearer and recorded assertion context.

The register records this integration gate separately from the full product. A notebook suffices for H0; a local research web application is another delivery form. Synthetic feature weights and toy semantic alternatives remain demonstration assumptions. Displayed top-k neighbours must never silently become the cohort population. Outcome data must not enter pre-index similarity features. Two refinements and a passing cache benchmark do not establish clinical usefulness.

The H0 source QA run is conditional on an extraction owner supplying authorized data before the demonstration. Synthetic data cannot substitute for that access or for clinical validation. The larger R1 workspace, R2 population-wide neighbour graph and R3 governed multi-user deployment remain separate milestones. A Dockerfile or rendered Helm chart does not establish a deployed release, a measured uptime target, resumability or a tested production rollback.

The five-step synthetic H0 integration is verified by the [committed HTTP journey report](../verification/research-prototype-journey.json), including export replay. The register records `bounded_synthetic_integration_verified`; the full product remains incomplete. The current assessment covers **104 requirements: 10 supported in their original bounded PS profile, 64 partial, four externally blocked and 26 specification-only**. The checker regenerates these counts and validates the explicit journey report; it does not infer acceptance from implementation file presence. Manual browser visual/accessibility verification and clinical evaluation have not been established.

## Remaining implementation work

The register retains the exact requirement-level gaps. The main work packages are:

| Work package | Representative requirements | Acceptance still needed |
|---|---|---|
| General expression and semantic compilation | FR-010–015, FR-020–022 | Versioned import closures/bundles, explicit reasoning-operation guarantees, generalized operators and independent execution references. |
| Patient similarity and neighbour graphs | FR-012, FR-031–043 | General semantic-temporal alignment, canonical trajectory identity, scalable all-pairs candidates, governed recall and incremental reconciliation. Bounded exhaustive QBE is only one component. |
| Typed time and replay | TIME-001–004, TRP-001–008 | Calendar age/time/duration and relative-anchor semantics, trustworthy historical availability, historical semantic snapshots, derived-index replay and full unresolved-membership handling. |
| Clinical adapters and evaluation | ETL-001–002, CLIN-001–002, UC-001 | Authorized source-specific adapters, reviewed clinical interpretations, kidney/heart-failure/diabetes definitions and held-out evaluation. ADNI/FHIR adapters are not implemented. |
| Coordinated analyst workspace | FR-070–075, UI-001–014 | General visual/form/DSL/JSON synchronization, complete keyboard/screen-reader workflows, controlled sharing/exports and formative user evidence. |
| Governed operations | FR-060–061, FR-080–084, NFR-003–016 | Identity/purpose/access policy, audit/storage/queues, independently scalable workers, restart/upgrade/retry/rollback tests, signed/scanned releases and measured capacity/availability. |

[The original issue register](issues.md) contains historical incremental notes. This completion register assesses the full requirement wording rather than treating those chronological notes as current acceptance certificates. The broader ontology contract remains separate from the bounded Rust/finite-reference integration; archived collaborator Java checks are not dependencies of the supported Python/Rust runtime.

## Inputs and decisions required from collaborators

These are precise unsatisfied dependencies, not requests already sent or permissions already obtained. The intended output locations below are proposals; they are not evidence that artifacts exist. Keep raw clinical data and participant-level evaluation records local and access-controlled. Publish only reviewed, non-sensitive summaries in GitHub.

| Dependency | Required contribution | Existing instruction or worksheet | Proposed accompanying evidence |
|---|---|---|---|
| `D-DATA` | Extraction owner supplies authorized MIMIC release tables, checksums, extraction code, coverage/exclusion counts and population manifest. | [`data/full-mimic-study-plan-2.1.json`](../data/full-mimic-study-plan-2.1.json); local input `data/local-mimic/` | Local `verification/local-clinical-study/`; publish approved aggregate findings only. |
| `D-CLINICAL` | Qualified reviewer records explicit item/unit/method/context and target-release decisions. Source fidelity and candidate IRIs do not constitute acceptance. | [`data/clinical-terminology-review.json`](../data/clinical-terminology-review.json), [`data/terminology/`](../data/terminology/) | Local `verification/local-clinical-study/mapping-review.json`; accepted non-sensitive terminology decisions may be committed after review. |
| `D-STUDY` | Domain team approves landmark/index, baseline, outcomes, censoring and patient-level development/held-out split; evaluates independently. | [`data/full-mimic-study-plan-2.1.json`](../data/full-mimic-study-plan-2.1.json) | Local `verification/local-clinical-study/protocol-and-validation.json`. |
| `D-HISTORY` | Source owner establishes availability semantics, revision links and historical coverage, or explicitly selects a retrospective-only contract. | [Evidence-selection contract](evidence-selection.md); local input `data/local-history/` | Local `verification/local-history/coverage.json`. |
| `D-USABILITY` | Six to eight target researchers/informaticians and two domain reviewers complete declared formative tasks; manual keyboard/screen-reader verification and critical-defect retesting. | [Addendum 2.2](../addenda/specification-2.2.md) | Local `evaluation/local-usability/report.json`; publish anonymized reviewed summary. |
| `D-ADNI-FHIR` | Select authorized ADNI releases/FHIR profiles and approve source, specimen, observation and context mappings before adapter implementation. | [Source gaps](issues.md); local input `data/local-adni-fhir/` | Local `verification/local-adni-fhir/adapter-validation.json`. |
| `D-SULO` | Maintainers decide proposed temporal relation meanings and wider ontology-interface commitments. | [Precedence decision](decisions/temporal-precedence.md) | Proposed `docs/sulo-development/temporal-adoption-decision.md`. |
| `D-DEPLOY` | Supply a container runtime, target Kubernetes test cluster, release registry and signing setup; execute smoke/upgrade/rollback and release checks. | Deployment files under `deploy/` when integrated | Proposed `verification/deployment-runtime-report.json`, tied to actual image/chart digests and environment. |

Graphiti and narrative retrieval remain optional. The bounded comparison in [addendum 2.3](../addenda/specification-2.3.md) must precede any adoption claim, but it is not an H0 delivery dependency. No external service credentials are needed to run the synthetic core profiles.
