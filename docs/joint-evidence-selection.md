# Joint temporal and semantic evidence selection

`joint-evidence-selection-1.0` selects temporal records and semantic assertions using **the same source-revision chains and per-patient availability cutoffs**. It then compiles the selected facts into the existing checked Rust semantic module and bounded temporal matcher. A later drug classification cannot enter an earlier source-as-known query through an unfiltered semantic sidecar.

This is a normalized evidence-archive interface, not a clinical source importer. Source availability and archive completeness remain caller declarations. Both replay modes use the current explicitly supplied rule policy; historical ontology reconstruction is not implemented.

## Run the example

```sh
python -m pip install -r patterns/requirements-semantic.lock.txt
python -m patterns.joint_evidence
python -m patterns.joint_evidence --request examples/joint-evidence/after-request.json --output verification/joint-evidence-run/after
python -m patterns.joint_evidence --request examples/joint-evidence/retrospective-request.json --output verification/joint-evidence-run/retrospective
python -m patterns.test_joint_evidence
```

The [archive](../examples/joint-evidence/archive.json) records an administration A at `[0,1)` and a collection B at `[3,4)` in constructed microsecond coordinates. A's drug-role witnesses are available on 1 January; its antibiotic classification first becomes available on 4 January, even though the source-recorded date refers to 1 January. The [query](../examples/joint-evidence/query.json) asks for antibiotic administration before collection.

| Request | Selected semantic evidence | Result |
|---|---|---|
| Source-as-known, 3 January | Drug role and bearer, no antibiotic classification | No recorded qualifying match |
| Source-as-known, 4 January | Antibiotic classification included at the inclusive cutoff | P1 is a certain match |
| Retrospective, 3 January cutoff | Later classification included from the supplied archive | P1 is a certain match; `later_evidence_used: true` |

The first outcome does not entail that the drug was not an antibiotic. It says the selected evidence does not entail the query's required class. Clinical occurrence time, source-recorded time and source-availability time have different roles here; only declared availability determines this selection.

## GEN-01: synthetic genomic release replay

GEN-01 asks a narrow replay question: for the immutable patient variant observation
in `PGEN01`/`EGEN01`, which release-specific interpretation assertions were known at
the requested source cutoff? It is a **synthetic release replay**, not a production
ClinGen integration or a clinical interpretation of a patient. The ClinGen-style
source identifiers and release dates are fixture data; all
`https://example.org/trajectory/genomics/...` terms are local application
vocabulary.

Run the two source-as-known snapshots and the acceptance cases with the supplied
JSON, writing disposable reports outside the repository:

```sh
python -m patterns.joint_evidence \
  --archive examples/joint-evidence/gen-01/archive.json \
  --request examples/joint-evidence/gen-01/before-request.json \
  --policy examples/joint-evidence/gen-01/policy.json \
  --query examples/joint-evidence/gen-01/query.json \
  --output /tmp/gen-01-before
python -m patterns.joint_evidence \
  --archive examples/joint-evidence/gen-01/archive.json \
  --request examples/joint-evidence/gen-01/after-request.json \
  --policy examples/joint-evidence/gen-01/policy.json \
  --query examples/joint-evidence/gen-01/query.json \
  --output /tmp/gen-01-after
python -m patterns.test_use_case_conformance
```

Both source-as-known runs return outer status `READY` and
`selection.later_evidence_used: false`. At the 1 March cutoff, the selected
semantic facts contain `variant_observation` and the R1 uncertain interpretation,
but not `clingen_r2_pathogenic`. At the 1 August cutoff, the R2 pathogenic
interpretation and its release identity are selected as well. The test asserts that
the complete `variant_observation` fact payload is identical in both results: the
release changes the selected external assertion, not the recorded patient
observation. Deliberately conflicting classification claims fail closed as
`BLOCKED_EVIDENCE` or `INCONSISTENT_ONTOLOGY`, rather than selecting an
interpretation by list order.

The CLI accepts `--archive`, `--request`, `--policy`, `--query`, `--output` and `--timeout-seconds`. Each output directory receives a complete `result.json`; reusing a directory replaces its report. Ready and empty selections exit 0. Blocked, inconsistent or invalid selected evidence writes the audit and exits 2. Malformed input exits 2 with `INVALID_INPUT` before producing a new report.

## Archive and policy contracts

The [joint schema](../schemas/joint-evidence.schema.json) retains the existing archive metadata, source entries and selection request. Its archive profile is `joint-evidence-archive-1.0`. Each assertion bundle contains four arrays:

- `variables`, `events` and `constraints`, with the existing bounded temporal row contracts.
- `semantic_facts`, containing named-individual declarations, named-class assertions or object-property assertions.

An assertion can contain only temporal facts, only semantic facts, or both. At least one array must be nonempty. A support entry activates the **whole assertion bundle**. A correction supersedes that support's entire earlier bundle; retaining a fact requires carrying it into the replacement or retaining independent support. This permits atomic corrections of both temporal bounds and semantic classifications.

Every semantic fact has an archive-level `id`, `patient_id`, `episode_id` and `kind`:

| Kind | Additional fields |
|---|---|
| `individual` | `local_id` |
| `class` | `subject`, `class_iri` |
| `property` | `subject`, `property_iri`, `object` |

A subject or object is exactly `{"event_id": "A"}` or `{"local_id": "drug_role"}`. Event references resolve to the selected process IRI and must belong to the fact's patient/episode. Local identifiers require an active declaration in that patient/episode and map to deterministic, scoped IRIs. Raw individual IRIs and cross-scope event references are rejected. Reusing `drug_role` in another patient's episode therefore does not share a role individual.

Only `sulo:hasParticipant` and `sulo:isFeatureOf` are admitted object properties. The archive adds no RDF properties. The generated module uses the [semantic-support fragment](semantic-support.md), including its finite limits and prohibition on inferred identity, right-side existentials, datatypes and imports. The archive is not a complete RDF serialization of the source history.

The separate [rule policy](../examples/joint-evidence/policy.json), profile `joint-semantic-policy-1.0`, contains exactly `id`, `classes`, `rules` and `disjoint`, in addition to `profile`. It cannot contain individual assertions. The request must retain `semantic_policy: current-pinned-retrospective`. Changing this explicit policy changes the result context; it does not purport to recover the ontology used historically.

## Selection and compilation

The temporal-only and joint entry points share the revision implementation in [evidence_selection.py](../patterns/evidence_selection.py). Both validate their own schemas first. The original archive schema and entry point remain restricted to their original profile.

1. Validate stored row syntax, bundle scope, entry references, timestamps and revision chains. Source-as-known mode then excludes entries available after the patient's cutoff; retrospective mode includes eligible later entries and labels their use.
2. Compute active supports under the existing explicit-source-chain policy. No automatic latest-ingestion choice is introduced. Unknown availability in an admissible source, incomplete declared history, forks and ineligible ancestors block the run.
3. Select complete active bundles. Identical rows with the same kind and ID coalesce with all support IDs retained. Different payloads claiming one fact ID block as `CONFLICTING_ROW`. Distinct fact IDs remain distinct claims, even when their class assertions contradict each other.
4. Validate the full selected temporal network. No source constraint is removed because it is inconvenient for the query.
5. Resolve selected semantic references, construct the module and automatically bind it to the selected source digest. A missing event or local declaration blocks semantic compilation; it is never silently dropped. References in later unselected bundles are not resolved against an earlier snapshot.
6. Check the generated module with rustDL and the independent finite evaluator. Only a ready semantic gate can invoke the existing temporal join.

Withdrawing one source does not erase independent support from another. Withdrawing a correction does not resurrect its ancestor. A semantic-only support can qualify a separately supported event, but if that event is withdrawn, the dangling semantic reference blocks the run until the history is reconciled. Different source claims about one canonical event require explicit upstream normalization; equality is not inferred from similar values or labels.

## Evidence and outcomes

The result contains the complete supplied archive, request, policy, selection decisions, selected semantic facts, generated module, semantic reasoning report and any temporal result. `selection.row_supports` traces each selected row or fact to assertion/support IDs. `axiom_supports` traces each generated semantic axiom to all contributing fact IDs and their supports; identical logical axioms can retain several independent sources.

The semantic evaluator retains its assertion and rule-derivation evidence. The matcher retains the original process, patient-role and bearer bindings, source temporal constraints and fixed-witness certainty proofs. The outer context links the selection and semantic contexts and fingerprints the implementation and policy. No new patient relation is introduced.

| Result status | Consequence |
|---|---|
| `READY` | A complete checked match result over the represented selected evidence |
| `EMPTY_SELECTED_EVIDENCE` | No selected rows; no cohort answer or clinical absence claim |
| `BLOCKED_EVIDENCE` | History, availability, revision or row-conflict problem; no reasoning/matching result |
| `INVALID_SELECTED_SOURCE` / `TEMPORAL_INCONSISTENCY` | Temporal compilation fails; no semantic reasoning or match result |
| `INVALID_SELECTED_SEMANTICS` | Dangling, cross-scope or otherwise unsupported selected semantic input; no match result |
| `INCONSISTENT_ONTOLOGY` / `UNRESOLVED_SEMANTICS` | The Rust/reference gate blocks matching and retains its evidence |

`selection.status` describes the temporal selection stage. The outer `status` is authoritative for the whole run: a ready temporal selection can still fail semantic compilation or reasoning. `matching` is null in every blocked outcome. A selection containing semantic facts but no usable temporal source is invalid for this trajectory profile, rather than a semantic-only reasoning service.

## Verification and remaining work

The 28 new tests cover cutoff inclusivity, retrospective labelling, atomic corrections, withdrawals without resurrection, independent supports, duplicate logical axioms, scope isolation, missing dependencies, temporal and ontology contradictions, backend failure and provenance. They include 24 seeded revision histories checked against an independent latest-eligible-chain evaluator, plus a comparison with the original temporal-only entry point for built-in selectors. Every positive end-to-end case executes the pinned Rust backend and its finite semantic checker.

This closes the specific gap in which temporal evidence was selected by cutoff while semantic assertions were supplied separately without selection. It does not verify source-system history, implement clinical ETL, approve the antibiotic phenotype, add historical terminology replay, or establish consistency of full SULO/imports. Reports retain `historical_ontology_replay: false` and `full_owl_mapping_verified: false`.

The next source milestone is a reviewed clinical adapter that produces these bundles and reconciles every input row, including unavailable and unsupported records. The next reasoning milestones remain broader operation-specific backend diagnostics, import-closure handling and identity normalization. Scale work must preserve the shared selection decisions, scoped references, checked semantic support and fixed-witness temporal answers.
