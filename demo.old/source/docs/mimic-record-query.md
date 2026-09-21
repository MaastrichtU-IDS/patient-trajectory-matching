# End-to-end MIMIC recorded-source queries

**Completed implementation:** `mimic-record-query-1.0` connects inputevents admission, patient-local coordinates, closed RDF ingestion, checked rustDL class support, temporal matching and an independent record oracle. It supports retrospective questions about admitted source records. It does not certify administrations, clinical occurrence times, physical elapsed durations or historical source availability.

## Run it

Install the existing optional Rust dependency lock, then run the synthetic example:

```sh
python -m pip install -r patterns/requirements-semantic.lock.txt
python -m patterns.mimic_record_query
python -m patterns.test_mimic_record_query
```

The example returns one recorded-pattern match: two distinct admitted component rows meet at their recorded boundary. It writes a single `verification/mimic-record-query-run/result.json` bundle with the full admission/reconciliation ledger, query outcomes for every input row, the ICU-stay roster, per-episode RDF and semantic modules, inference and temporal evidence, origin provenance and matched patient IDs. Detailed outputs are ignored by git.

For the public demo, obtain the three original demo 2.2 ICU files as described in [staging](mimic-inputevents-staging.md), then run:

```sh
python -m patterns.mimic_record_query \
  --input-dir data/local-mimic/icu \
  --request examples/mimic-record-query/demo-request.json

python -m patterns.verify_mimic_record_query --input-dir data/local-mimic/icu
```

The second command verifies the published file hashes and writes only the [aggregate verification report](../verification/mimic-record-query-demo-report.json). CI runs synthetic examples and tests; it does not download patient data. It checks the committed aggregate report against the current implementation and request hashes.

## The executable query contract

The [request schema](../schemas/mimic-record-query.schema.json) fixes `mode` to `retrospective_source_records` and `time_policy` to `recorded-label-exact-v1`. A request declares its dataset namespace, all roster patients or an explicit patient list, one to four required distinct slots, item-code sets for each slot, and conjunctive before/meets/overlaps/gap constraints. Unknown input item codes, unknown patients, duplicates, extra fields and unsupported policies fail validation.

The public example asks whether an admitted record with item `225152` strictly precedes an admitted record with item `222056` within the same ICU stay. The pinned source dictionary labels these Heparin Sodium and Nitroglycerin. The query is an illustrative code-order question, not a treatment protocol, diagnosis, safety recommendation or clinical phenotype. Labels do not control selection.

Each source item is assigned a generated **recorded-process** class. Explicit rules infer membership in a slot's requested item-code group; no label substring or medical taxonomy is inferred. The checked Rust engine's complete membership results are compared with the finite semantic evaluator before temporal execution. The additional `patient_local.execute_semantic` entry point supports the existing restricted semantic module, including role-scoped existential premises, over local bounded snapshots. It has distinct local semantic/query profile IDs and preserves local-calendar interpretation in its evidence context.

## Representation and time policy

A projected event has kind `recorded_input_segment`, status `recorded`, and class `bt:RecordedInputSegment`, a subclass of `bt:IntervalProcess`. It denotes a process as described by one admitted component row. It is not inferred to be `bt:Infusion`, a confirmed medication administration, or a physical recording action. Existing infusion/specimen kinds still require `performed`. The new kind/status pair is admitted by the separate `patient-local-record-interval-1.0` source profile, with `patient-local-record-rdf-1.0` and `patient-local-record-query-1.0` companions. The original `patient-local-interval-1.0` and offset source schemas retain their earlier kinds and statuses.

The source mapping preserves separate rows, components, rate segments, source statuses and order identifiers. PRO binds each recorded process through its patient role to the person identified by the validated stay join. All literals use SOLID information objects. No object or datatype properties are introduced.

For this record-query mode, the local timestamp labels themselves are the queried endpoints. Lower and upper coordinate bounds therefore coincide **as source labels**. This does not assert that real clinical occurrence is known exactly. A query requiring uncertain clinical occurrence needs separately justified bounds and a reviewed clinical mapping. No one-second/minute error model is guessed.

The origin is the minimum admitted start label for that patient across the entire supplied staging snapshot, chosen before item or patient query filtering, with its source-record key retained. It is a fixed coordinate reference, not a first-ever clinical event. It stays consistent across that patient's ICU stays. No query binding crosses stays or patients. Gaps remain local-calendar differences; physical elapsed-time verification is false.

## Complete accounting and failure behavior

Every input record retains its original admission outcome and receives one query outcome: not admitted, outside patient scope, outside item scope, or selected. Rejected and unsupported rows remain visible. The ICU-stay roster includes stays with no selected records, so they do not disappear from result denominators.

| Episode outcome | Meaning |
|---|---|
| `RECORDED_PATTERN_MATCH` | At least one distinct binding of admitted records satisfies every constraint |
| `NO_ADMITTED_RECORD_MATCH` | No qualifying binding in the admitted record scope; no clinical absence claim |
| `BLOCKED_RESOURCE_LIMIT` | A complete execution exceeds an explicit bound; records were not truncated |
| `BLOCKED_SEMANTICS` | Rust/finite-model support could not be established |
| `BLOCKED_REFERENCE_DISAGREEMENT` | Temporal results differ from direct record evaluation |

If any episode is blocked, the overall status is `BLOCKED_INCOMPLETE_EXECUTION`; authoritative matched-patient IDs and counts are null, and the CLI exits 2. Completed episodes remain available for diagnosis. `SELECTED_RECORD` indicates query selection, not successful execution of a blocked episode. If every episode completes, the status is `COMPLETED_RECORDED_QUERY`, with completeness explicitly restricted to admitted records in the requested snapshot. Neither outcome establishes source-history completeness.

Input failures exit 2 before replacing a result. Completed or blocked execution results replace the bundle atomically as one file. Consumers must read the exit status and the result status before using an existing output.

## Bounded execution and independent verification

The pipeline selects item codes and partitions by ICU stay before graph/reasoner construction. An episode with a missing required selector is an analytically empty candidate set under the explicit item-group rules, so it needs no reasoner invocation. Other episodes are capped at 30 selected records and 20,000 candidate bindings before distinctness; the request roster is capped at 256 stays. The underlying staging file limits and semantic fragment caps also apply. This is a bounded demonstrator, not a streaming full-MIMIC engine.

Every completed episode agrees with an independent evaluator that enumerates source-record bindings and compares naive datetimes directly, without the STN, normalized coordinate projection or OWL engine. The Rust semantic engine also retains its separate finite-model cross-check. A disagreement suppresses the complete-cohort claim.

The public demonstration produced:

| Check | Result |
|---|---:|
| Source input records accounted for | 20,404 |
| Not admitted by the staging profile | 9,424 |
| Admitted records outside selected item codes | 10,690 |
| Selected component records | 290 |
| Roster patients / ICU stays | 100 / 140 |
| Episodes requiring Rust/temporal evaluation | 3 |
| Candidate bindings before distinctness | 94 |
| Matching bindings / stays / patients | 1 / 1 / 1 |
| Stays with no admitted-record match | 139 |

All three original compressed hashes match the pinned public release. The committed report contains aggregate counts and provenance only. No patient rows, detailed graphs, or patient identifiers are redistributed. Full MIMIC has not been analyzed.

## Completion boundary

This completes the bounded recorded-evidence path from CSV files to an explainable, checked temporal query result, including local-clock semantic inference, row/stay accounting and a reproducible public-data demonstration. The 24 new tests exercise successful inference, role witnesses, bounded certainty/possibility, source reconciliation, no-match scope, invalid requests, resource limits, backend failures, independent-oracle disagreement, and CLI output behavior.

Clinically interpreted trajectories and source-as-known replay remain separate capabilities. They require reviewed clinical classes and occurrence bounds, plus trustworthy availability and revision coverage. The original staging status continues to signal those unresolved clinical handoff requirements; this explicitly retrospective record-query profile does not clear them by relabelling records as verified clinical events. General OWL imports, clinical phenotype validation, patient-to-patient similarity and a production analyst UI are outside this completed demonstrator.
