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

`ingest_rows` returns one row per snapshot event (`profile: trajectory-graph-sulo-ingest-1.0`). It depends on the snapshot alone, and the tests check that it is identical with and without a request. A row has:

| Group | Fields |
|---|---|
| Identity | `snapshot_context_id`, `time_domain`, `patient_id`, `event_id`, `episode_id`, `record_id`, `record_status`, `event_kind`, `kind_class` |
| Clock | `clock_id`, `clock_policy`, `clock_origin`: the event's own clock, never rebased |
| Times | `declared_start_us` and `declared_end_us`; `start_us` and `end_us`, the minimal bounds in the source network, on the event's own clock; `ongoing: false` |
| Variables | `start_var`, `end_var` |
| Box verdict (per patient) | `box_representable`; `non_box_constraints` (source edge ids the tightened box does not imply); `shared_variables` (variables referenced by more than one event) |
| Evidence | `evidence_id`, `evidence_context_id`, `claim`, `patient_bearer`, `source_record`, `source_hash`, `source_key` |
| Selection | `selection_state` (`accepted` or `declared_source`), `claim_supports` (claim, decision, claim and source digests) |
| Concepts | `concepts: [{concept, basis: [...]}]`, the kind closure and the asserted facts with their closure |
| Semantics | `entailment: "not_materialized"`; `vocabulary_disjointness` (`none_declared` or `not_checked`) |

Rebasing, comparability and entailment depend on the request, so they are not in the rows. A store rebases at query time, as `match_snapshot` does. Entailed classes are not materialized: `semantic_support` checks at most eight classes and 64 individuals per run, so materializing every policy class would need batching, which is not done.

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
- **Box verdict.** A patient is matched only if a box per event represents its source:
  - Source constraints. One the tightened box already implies changes nothing. Any other one refuses the patient with `SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE`. Patient P1 of the patient-local example, tied to a shared uncertain anchor, is refused this way.
  - Shared variables. A variable referenced by two events is an equality between them that the engine would treat as independent. That would understate exactness, so the patient is refused with `SHARED_SOURCE_VARIABLE`. This lasts until engine events can carry their variables (D13).

  Both verdicts are per patient, so other patients are still matched.
- **Binding check.** Once a patient passes the box verdict, its feasible region is each event's tightened box with its proper-interval edge (start + 1 ≤ end), events independent. Every binding the engine reports is replayed against that region, restricted to the bound events, plus the query's constraints with any relaxations applied. Steps bound to the same event share its variables. This covers exact, possible and relaxed results, and a relaxed match beside an unresolved status. The check is equivalent to replaying against the patient's whole source network (`full_network_check`, O(V³), kept as the test reference), and the tests compare the two on the fixtures, including the refuted case.
  - A `possible` binding the check refutes becomes `unresolved` with `POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK` and the refuting negative cycle. It was added when the engine gave each step its own time variables, so one event bound to two steps could take two different times. The engine now shares an event's variables (D13) and decides that case itself; the check remains as a safety net, chiefly for the bounded profile's strict start before end, which the engine's boxes do not encode.
  - An `exact` binding that is not entailed drops to `possible` with `EXACT_NOT_ENTAILED_BY_SOURCE_NETWORK`.
  - An engine witness outside the region is replaced by the region's own witness (`ENGINE_WITNESS_REPLACED_BY_SOURCE_WITNESS`).

  The engine's original result is kept under `engine_result`.
- **Points.** When the query has no `during` relation, no constraint reads an event's end. After tightening, every start in its range has a feasible end, so events go to the engine as points (`projection: points`). The engine can then use its polynomial point-forest method instead of budgeted backtracking, and the binding check still uses the ends. Under `first` or `last` occurrence, events whose start ranges tie keep their intervals, since the engine breaks those ties by end. `point_projection=False` turns this off, and the tests show the method changes while the decisions do not.
- **Soundness of the rest.** The engine's region contains the source region: the box contains the feasible set, end ≥ start is weaker than start < end, and the engine's step variables are independent. So `none` is sound, and exactness over the box implies exactness over the source.

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
- `refused`: a non-box source constraint, a shared variable, or a window clock that is missing or unusable.
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
- **Complete search after a refuted binding.** When the binding check refutes the engine's possible binding, the adapter reports `unresolved` rather than searching for another binding.
- **Entailed classes in the ingest rows.** These are not materialized (`entailment: "not_materialized"`), and matching entails only the policy classes the query names, within `semantic_support`'s limits.
- **Shared variables and non-box source networks.** These are refused rather than decided. Shared variables wait for engine events with variables (D13); other non-box networks need the engine to take source edges between events.
- **Multiple stores.** Several stores, such as the MIMIC import's per-stay stores, are matched one snapshot at a time. Merging them is the store's job.
- **Recorded and available times.** These are not in a snapshot, and remain with the importers' claim provenance.
- **Clinical mappings.** None are verified.
