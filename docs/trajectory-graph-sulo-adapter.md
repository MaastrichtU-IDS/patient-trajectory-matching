# Trajectory-graph SULO adapter

**Profile:** `trajectory-graph-sulo-1.0` · **Code:** [`patterns/trajectory_graph_sulo.py`](../patterns/trajectory_graph_sulo.py) · **Tests:** `python -m patterns.test_trajectory_graph_sulo` · **Example:** [`examples/trajectory-graph-sulo/`](../examples/trajectory-graph-sulo/)

The [trajectory-graph engine](trajectory-graph-profile.md) reads a projected form: events listing concepts, with integer start and end ranges. This adapter produces that form from a `bounded_intervals.Snapshot`, runs the engine, checks each binding against the source, and returns the result with each bound step's evidence. Bounded JSON, bounded RDF, patient-local graphs and claim selections all produce a snapshot. Where a JSON route and a graph route both exist, the graph route is the canonical one.

It has two entry points:
- `ingest_rows(view)` projects a snapshot with no request. This is the execution store's contract.
- `match_snapshot(view, request)` answers one query.

```sh
python -m patterns.trajectory_graph_sulo                    # the example, through the graph route
python -m patterns.trajectory_graph_sulo --route json       # the same source read directly
python -m patterns.test_trajectory_graph_sulo
```

## Routes

| Helper | Input | Snapshot from |
|---|---|---|
| `from_bounded_source(source, facts)` | Bounded JSON (canonical route) | `bounded_rdf.export_source`, then `bounded_rdf.prepare_graph` |
| `from_bounded_json(source, facts)` | Bounded JSON | `bounded_intervals.prepare` |
| `from_bounded_rdf(graph, facts)` | Bounded RDF | `bounded_rdf.prepare_graph` |
| `from_patient_local(source_or_graph, facts)` | Patient-local source or graph | `patient_local.prepare_graph` |
| `from_claims(store, policy, semantic_policy)` | Bounded or patient-local claim store, JSON or graph | `claim_projection.select` or `local_claim_projection.select`, then the graph route |

Each helper returns a `SourceView`: the snapshot, any semantic facts and supports that come with it, and the route's own modules. The result context binds those modules (`bounded_rdf`, `patient_local`, `claim_projection`, `claim_rdf`, `joint_evidence`, `semantic_support` and so on, whichever the route used) by SHA-256.

The claim route builds the source just as `claim_projection` does. Only accepted claims contribute rows, so pending, rejected, superseded and withdrawn claims disappear. A conflicting accepted view is refused (`BLOCKED_ACCEPTED_CONFLICT`), and so is an empty one (`EMPTY_ACCEPTED_VIEW`). The modules bound by Linux-only reports (`claim_projection`, `temporal_stn`, `mimic_*`) are called, never edited.

Bounded JSON has no slot for semantic facts. On the JSON and RDF routes, a caller may supply them in a facts document, `{profile: "trajectory-graph-sulo-facts-1.0", vocabulary, facts}`. The vocabulary is a joint semantic policy declaring the classes the facts use. Each fact has the claim store's `semantic_facts` row shape. The example declares its concepts this way.

## Store ingest contract: `ingest_rows(view)`

`ingest_rows` returns one row per snapshot event (`profile: trajectory-graph-sulo-ingest-1.1`; 1.1 adds `proper` and stops counting shared variables against the box, D24). It depends on the snapshot alone, and the tests check that it is identical with and without a request. A row has:

| Group | Fields |
|---|---|
| Identity | `snapshot_context_id`, `time_domain`, `patient_id`, `event_id`, `episode_id`, `record_id`, `record_status`, `event_kind`, `kind_class` |
| Clock | `clock_id`, `clock_policy`, `clock_origin`: the event's own clock, never rebased |
| Times | `declared_start_us` and `declared_end_us`; `start_us` and `end_us`, the minimal bounds in the source network, on the event's own clock; `ongoing: false` |
| Variables | `start_var`, `end_var`, the source variable ids; `proper: true`, since every bounded interval ends at least a microsecond after it starts |
| Box verdict (per patient) | `box_representable`, false exactly when `non_box_constraints` (source edge ids the tightened box does not imply) is not empty; `shared_variables` (variables referenced by more than one event), listed but no longer counted against the box |
| Evidence | `evidence_id`, `evidence_context_id`, `claim`, `patient_bearer`, `source_record`, `source_hash`, `source_key` |
| Selection | `selection_state` (`accepted` or `declared_source`), `claim_supports` (claim, decision, claim and source digests) |
| Concepts | `concepts: [{concept, basis: [...]}]`, the kind closure and the asserted facts with their closure |
| Semantics | `entailment: "not_materialized"`; `vocabulary_disjointness` (`none_declared` or `not_checked`) |

Rebasing, comparability and entailment depend on the request, so they are not in the rows. A store rebases at query time, as `match_snapshot` does. Shared variables no longer make a patient not box-representable (D24). The execution store keeps each event's variables and `proper` and passes them to the engine, so it holds such patients. It still refuses, at ingest, a patient with a non-box source constraint (`INGEST_PATIENT_NOT_BOX_REPRESENTABLE`). Entailed classes are not materialized: `semantic_support` checks at most eight classes and 64 individuals per run, so materializing every policy class would need batching, which is not done.

## Matching: `match_snapshot(view, request)`

```json
{"profile": "trajectory-graph-sulo-request-1.0",
 "query": {"profile": "trajectory-graph-1.0", "steps": [{"id": "A", "concept": "<class IRI>"}], "...": "..."},
 "unit": "day",
 "windows": {"P1": {"start": 0, "end": 730, "clock_id": "optional"}, "P5": null},
 "window": {"start": 0, "end": 730, "clock_id": "c1"},
 "semantic_policy": {"profile": "joint-semantic-policy-1.0", "...": "optional"}}
```

The query's times and the windows are integers in `unit`. A per-patient window overrides the shared one, and a `null` window excludes the patient. At least one of `windows` or `window` must be present: the adapter never infers the index window from the data.

The result holds:
- `results`, one row per patient. Each row has the engine's fields, plus `decided_by`, `source_check`, `bound_evidence`, `clock`, `projection` and `engine_events`. The engine events are the request-specific input the engine received.
- `summary`: counts by status and by adapter code.
- `request_entailed_concepts`: request-specific.
- `context` and `context_id`.

The result has no event table; `ingest_rows` is the event table.

## Evidence

Engine event identifiers are snapshot event identifiers. Each patient result carries `bound_evidence` for every step it binds, and `relaxed_bound_evidence` for a relaxed match reported beside an unresolved status. Each entry holds:
- the snapshot's whole evidence item: evidence identifier, claim, patient role and bearer, source record, source hash and context identifier, plus the interval descriptors and variables;
- on the claim route, the claim supports;
- the concept the step matched, after relaxation, with its bases.

Recomputing `profile:sha256(canonical(item without evidence_id))` gives the evidence identifier back, and the tests check that every claim IRI is in the snapshot graph.

The result `context` binds:
- the snapshot (context identifier, graph and source digests, time domain);
- the request, the query as given and the query on the engine grid;
- the unit;
- the vocabulary and semantic policy digests;
- the semantic run's identity (status, model and ontology digests, backend);
- the route's provenance (store, acceptance policy and accepted claims on the claim route);
- the artifacts' SHA-256 digests;
- `clinical_mapping_verified: false`.

The `context_id` is the digest of the context. The JSON and RDF routes give identical output, including ingest rows, except for route identity: contexts, evidence identifiers and context identifiers, the RDF reader's provenance descriptors, and the route's bound modules.

## Concepts

| Basis | Source |
|---|---|
| `event_kind_closure` | The named ancestors of the event kind's class. These come from one parse of the pinned ontology per run, by the same walk as `bounded_intervals.selected_classes`, and the tests check that the two agree |
| `semantic_fact` | A class fact on the event carried with the snapshot (a claim's selected facts, or a caller's facts document), with its supports. MIMIC item classes arrive this way |
| `semantic_fact_closure` | The ontology ancestors of an asserted class, when the class is in the ontology |
| `rustdl_entailed` | Matching only. Membership entailed by the checked rustDL route (`semantic_support.check`) under the request's semantic policy, for the policy classes the query names. Each entry records the policy, model and ontology digests and the asserted axioms about that claim |

The vocabulary is the ontology's named classes plus the classes declared by the view's vocabulary and the request's semantic policy. The adapter refuses:
- `UNKNOWN_CONCEPT:<iri>`: a step or `widen_concept` concept outside the vocabulary. It is refused rather than matching nothing.
- `UNDECLARED_FACT_CLASS`: a fact class outside the vocabulary.
- `ENTAILMENT_REQUIRED`: a query concept that the facts' own policy derives by a rule, with no semantic policy. Matching on asserted facts alone would miss members.
- `SEMANTIC_RUN_REQUIRED_FOR_DISJOINTNESS`: facts under a vocabulary that declares disjointness, with no semantic policy. Only the checked run can show the facts are consistent.
- `SEMANTIC_SUPPORT_UNRESOLVED`: a semantic run that does not reach `READY`.

## Time

- **Box.** Each variable is tightened to its minimal bounds in the snapshot's network. An event's engine range is its tightened bounds, rebased to the patient's reference clock, in microseconds. The engine grid is always microseconds.
- **Source constraints.** A source constraint the tightened box already implies changes nothing. Any other one refuses the patient with `SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE`, whether or not the patient also shares variables. Patient P1 of the patient-local example, tied to a shared uncertain anchor, is refused this way. The refusal is per patient, so other patients are still matched.
- **Shared variables (D22).** A variable referenced by several events is an equality between them, which the engine models exactly (D13). Every engine event names its source variables as `start_var` and `end_var`, using the source variable ids themselves; they are unique in a snapshot, and every event naming one is sent that variable's one tightened range. So events that share a source time share one engine time. The reviewer's probe, X and Y sharing a start and `after` from 0 to 0, used to be refused with `SHARED_SOURCE_VARIABLE`; it is now `exact`, decided by the engine and confirmed by the binding check.
- **Strict intervals (D24).** Every interval event goes to the engine with `proper: true`, so the engine's events end at least a microsecond after they start, as the bounded profile's do. Once no source constraint is lost, the engine's network is the source region exactly: the tightened bounds, the proper edges and one variable per shared source variable. Box tightening alone could not give that once a shared variable links one event's end to another's start, since the missing fact is the joint relation through the variable; the engine's own network carries it. The engine may still refuse a patient's time network: a linked group beyond its size limit (`SHARED_VARIABLE_COMPONENT_LIMIT`), shared variables no time satisfies (`EMPTY_SHARED_VARIABLE`), or a proper event that cannot end after it starts (`EMPTY_PROPER_EVENT`, D25). Each becomes that patient's `refused` result with the engine's code. A validated snapshot cannot produce the last two, since an inconsistent source is refused when prepared and the tightened box holds start + 1 ≤ end; they are mapped all the same, and the tests inject both.
- **Binding check.** Once no source constraint is lost, the patient's feasible region is exact: each variable's tightened bounds, each event's proper-interval edge (start + 1 ≤ end), and one variable per shared source variable. Every binding the engine reports is replayed against that region, plus the query's constraints with any relaxations applied. The replay covers the bound events and every event linked to them through shared variables; events linked to none of them are independent of the binding. Steps bound to the same event share its variables. This covers exact, possible and relaxed results, and a relaxed match beside an unresolved status. The check is equivalent to replaying against the patient's whole source network (`full_network_check`, O(V³), kept as the test reference), and the tests compare the two on the fixtures, including shared variables and the refuted case.
  Since the engine's model equals the region (D24), the check confirms the engine's decisions; the paths below are safety nets that do not fire on such patients. A test runs the D22 probes and chains through six relations, two units and both projections, and finds no adapter code and every result decided by the engine and complete.
  - A `possible` binding the check refutes becomes `unresolved` with `POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK` and the refuting negative cycle. It was added when the engine gave each step its own time variables (before D13), and then caught the strict start before end the engine did not model (before D24): with Y starting as X ends, the engine let X be empty, called the two starts equal possible, and the check refuted it. The engine now decides that `none` itself.
  - An `exact` binding that is not entailed drops to `possible` with `EXACT_NOT_ENTAILED_BY_SOURCE_NETWORK`.
  - A `possible` binding the source entails becomes `exact` with `EXACT_BY_SOURCE_ENTAILMENT`, and so does a relaxed result's or relaxed match's `possible` certainty. Before D24 this lifted the review's repro (Y starts when X ends, `after` at least 1 µs), which the engine called possible; the engine now decides it exact. The tests show that both of these paths still fire against an engine stripped of `proper`.
  - Retired (D24): on a patient with a shared variable in an end role, a `possible` binding the source did not entail was marked `complete: false` with `EXACTNESS_NOT_DECIDED_STRICT_INTERVALS`, since another binding might have been exact over the source only. With equal models the engine's `possible` is complete, and the code is gone.
  - An engine witness outside the region is replaced by the region's own witness (`ENGINE_WITNESS_REPLACED_BY_SOURCE_WITNESS`).

  The engine's original result is kept under `engine_result`.
- **Points.** When the query has no `during` relation, no constraint reads an event's end. After tightening, every start in its range has a feasible end, so events go to the engine as points (`projection: points`), each naming its start variable. The engine can then use its polynomial point-forest method instead of budgeted backtracking, and the binding check still uses the ends. A start variable shared by several events stays one engine variable, so the projection onto starts is still exact. A shared variable that is some event's end is different: it orders other events' starts through that event (Y starts when X ends, so Y starts after X does), and points would drop that order. Such a patient keeps its intervals. Under `first` or `last` occurrence, events whose start ranges tie keep their intervals, since the engine breaks those ties by end. `point_projection=False` turns this off, and the tests show the method changes while the decisions do not, with and without shared starts.
- **Soundness of the rest.** With no source constraint lost, the engine's region is the source region: the tightened box, the proper edge of every interval event, and one engine variable per shared source variable. So `none`, `possible` and `exact` all hold over the source. Points drop the ends, which the projection argument above covers.

## Clocks and units

- **One clock per patient.** The reference clock is the one the window names, or else the patient's only clock. A second clock whose origin and the reference's are both offset datetimes (`offset-datetime-microseconds-v1`) is rebased by the exact integer difference of the origins. Any other second clock, such as a second patient-local calendar clock, leaves its events out. The patient is then `incomparable` with `CLOCKS_NOT_REBASABLE`, listing the excluded events, and never `none`.
- **Window clocks.**
  - A shared window must name `clock_id` if the patients it applies to use more than one clock between them. Otherwise it would cover a different period on each clock, and the request is refused with `SHARED_WINDOW_CLOCK_REQUIRED`.
  - A per-patient window over a patient with several clocks must name its clock (`WINDOW_CLOCK_REQUIRED`).
  - A window clock the patient cannot use is refused (`WINDOW_CLOCK_NOT_PATIENT_CLOCK`).
  - Per-patient windows on patient-local clocks are relative to each patient's own origin, as patient-local clocks intend.
- **Patients missing from the snapshot.** A patient with a window but no events in the snapshot is `incomparable` (`PATIENT_NOT_IN_SNAPSHOT`).
- **Units.** Declared units (`microsecond` through `week`) convert to microseconds exactly. Calendar months and years are refused (`CALENDAR_UNIT_REFUSED`). On patient-local clocks a day is a local calendar-coordinate difference, as in [patient-local clocks](patient-local-clocks.md), and the result records the snapshot's time domain.

## Statuses

The engine's statuses (`exact`, `possible`, `relaxed`, `unresolved`, `none`, `excluded`) are kept. Each patient result says who decided it (`decided_by: engine | adapter`). The adapter adds two statuses of its own:
- `refused`: a non-box source constraint, an engine refusal of the patient's shared variables, or a window clock that is missing or unusable.
- `incomparable`: clocks that cannot be rebased, or a patient missing from the snapshot.

The adapter's downgrades reuse `unresolved` and `possible` under an `adapter.code`. `summary.adapter_codes` counts both refusals and downgrades. Errors that affect the whole request raise `AdapterError` with a stable code.

## Example

[`examples/trajectory-graph-sulo/`](../examples/trajectory-graph-sulo/) re-expresses [`examples/trajectory-graph/`](../examples/trajectory-graph/) as a bounded source, a facts document and a request:
- times in microseconds on one offset-datetime clock;
- a point event becomes the shortest proper interval, ending one microsecond after it starts;
- each listed concept becomes a declared class fact.

The tests check that the committed files equal that conversion. Through the JSON route, the RDF route and the canonical graph route, the adapter's decisions equal the engine's on the original example: status, binding, relaxations, cost, certainty, completeness and notes. The witness and method differ by design: the query has a `during` relation, so the re-expressed events stay intervals. The same decisions also come back through rustDL, with `seizure` entailed from `febrile_seizure` by a rule instead of being listed.

The MIMIC test runs the committed synthetic `examples/mimic-inputevents` fixture through the pending-claim import and a caller-declared accept-all analysis policy. It then runs the patient-local claim selection and graph route, and matches on the source item code class. The result reports `clinical_mapping_verified: false`.

## Not done

- **Source coverage.** The adapter cannot tell whether a source covers a patient's window. A patient with events is matched on the events it has. The store's coverage table is meant to answer that.
- **Complete search after a refuted binding.** When the binding check refutes the engine's possible binding, the adapter reports `unresolved` rather than searching for another binding. Since D24 the engine's model equals the source region, so this is a safety net that no known case reaches.
- **Entailed classes in the ingest rows.** These are not materialized (`entailment: "not_materialized"`), and matching entails only the policy classes the query names, within `semantic_support`'s limits.
- **Non-box source networks.** These are refused rather than decided; they need the engine to take source edges between events. Shared variables are decided (D22), and the store holds them (D24).
- **Multiple stores.** Several stores, such as the MIMIC import's per-stay stores, are matched one snapshot at a time. Merging them is the store's job.
- **Recorded and available times.** These are not in a snapshot, and remain with the importers' claim provenance.
- **Clinical mappings.** None are verified.
