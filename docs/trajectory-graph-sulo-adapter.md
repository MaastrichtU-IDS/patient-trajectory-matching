# Trajectory-graph SULO adapter

**Profile:** `trajectory-graph-sulo-1.0` · **Code:** [`patterns/trajectory_graph_sulo.py`](../patterns/trajectory_graph_sulo.py) · **Tests:** `python -m patterns.test_trajectory_graph_sulo` · **Example:** [`examples/trajectory-graph-sulo/`](../examples/trajectory-graph-sulo/)

The [trajectory-graph engine](trajectory-graph-profile.md) reads a projected form: events listing concepts, with integer start and end ranges. This adapter produces that form from a `bounded_intervals.Snapshot`, runs the engine, checks each binding against the source, and returns the result with each bound step's evidence. Bounded JSON, bounded RDF, patient-local graphs and claim selections all produce a snapshot. Where a JSON route and a graph route both exist, the graph route is the canonical one.

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

Each helper returns a `SourceView`: the snapshot plus any semantic facts and supports that come with it. `match_snapshot(view, request)` is the single entry point. The request is:

```json
{"profile": "trajectory-graph-sulo-request-1.0",
 "query": {"profile": "trajectory-graph-1.0", "steps": [{"id": "A", "concept": "<class IRI>"}], "...": "..."},
 "unit": "day",
 "windows": {"P1": {"start": 0, "end": 730}, "P5": null},
 "window": {"start": 0, "end": 730, "clock_id": "optional"},
 "semantic_policy": {"profile": "joint-semantic-policy-1.0", "...": "optional"}}
```

The query's times and the windows are integers in `unit`, and each window is relative to the patient's clock origin. A per-patient window overrides the shared one, and a `null` window excludes the patient. At least one of `windows` or `window` must be present: the adapter never infers the index window from the data.

The claim route builds the source just as `claim_projection` does. Only accepted claims contribute rows, so pending, rejected, superseded and withdrawn claims disappear. A conflicting accepted view is refused (`BLOCKED_ACCEPTED_CONFLICT`), and so is an empty one (`EMPTY_ACCEPTED_VIEW`). The modules bound by Linux-only reports (`claim_projection`, `temporal_stn`, `mimic_*`) are called, never edited.

Bounded JSON has no slot for semantic facts. On the JSON and RDF routes, a caller may supply them in a facts document, `{profile: "trajectory-graph-sulo-facts-1.0", vocabulary, facts}`. The vocabulary is a joint semantic policy declaring the classes the facts use. Each fact has the claim store's `semantic_facts` row shape. The example declares its concepts this way.

## Evidence

Engine event identifiers are snapshot event identifiers. Each patient result carries `bound_evidence` for every step it binds, and `relaxed_bound_evidence` for a relaxed match reported beside an unresolved status. Each entry holds the snapshot's whole evidence item: evidence identifier, claim, patient role and bearer, source record, source hash and context identifier, plus the interval descriptors and variables. On the claim route it also holds the claim supports: the claim, the decision that accepted it, and the claim's digest. Recomputing `profile:sha256(canonical(item without evidence_id))` gives the evidence identifier back, and the tests check that every claim IRI is in the snapshot graph.

The result `context` binds:
- the snapshot (its context identifier, graph and source digests, time domain);
- the request, the query as given and the query on the engine grid;
- the unit;
- the vocabulary and semantic policy digests;
- the route's provenance (store, acceptance policy and accepted claims on the claim route);
- the artifacts' SHA-256 digests;
- `clinical_mapping_verified: false`.

The `context_id` is the digest of the context.

The JSON and RDF routes give identical output except for route identity: contexts, evidence identifiers and context identifiers, and the RDF reader's provenance descriptors. The tests check this on the example.

## Concepts

Each event's concepts carry a list of bases:

| Basis | Source |
|---|---|
| `event_kind_closure` | The named ancestors of the event kind's class. These come from one parse of the pinned ontology per run, by the same walk as `bounded_intervals.selected_classes`, and the tests check that the two agree |
| `semantic_fact` | A class fact on the event carried with the snapshot (a claim's selected facts, or a caller's facts document), with its supports. MIMIC item classes arrive this way |
| `semantic_fact_closure` | The ontology ancestors of an asserted class, when the class is in the ontology |
| `rustdl_entailed` | Membership entailed by the checked rustDL route (`semantic_support.check`) under the request's semantic policy, for the policy classes the query names. Each entry records the policy, model and ontology digests and the asserted axioms about that claim |

The vocabulary is the ontology's named classes plus the classes declared by the view's vocabulary and the request's semantic policy. A step or `widen_concept` concept outside it is refused (`UNKNOWN_CONCEPT:<iri>`), rather than matching nothing. So is a fact class outside it (`UNDECLARED_FACT_CLASS`). A query concept that the facts' own policy derives by a rule is refused without a semantic policy (`ENTAILMENT_REQUIRED`), because matching it on asserted facts alone would miss members. A semantic run that does not reach `READY` refuses the request (`SEMANTIC_SUPPORT_UNRESOLVED`).

## Time

- **Box.** Each variable is tightened to its minimal bounds in the snapshot's network. An event's engine range is its start and end variables' tightened bounds, on the patient's reference clock, in microseconds. The engine grid is always microseconds.
- **Constraints a box cannot represent.** A source constraint the tightened box already implies changes nothing and is accepted. Any other one makes the patient `refused` with `SOURCE_CONSTRAINTS_NOT_BOX_REPRESENTABLE`, listing the constraints. Patient P1 of the patient-local example, tied to a shared uncertain anchor, is refused this way. The decision is per patient, so other patients are still matched.
- **Witness check.** Every binding the engine reports is replayed against the patient's full source network, with its proper-interval edges, shared variables and clock offsets, plus the query's constraints with any relaxations applied. This covers exact, possible and relaxed results, and a relaxed match beside an unresolved status.
  - A `possible` binding the network refutes becomes `unresolved` with `POSSIBLE_BINDING_REFUTED_BY_SOURCE_NETWORK` and the refuting negative cycle.
  - An `exact` binding the network does not entail drops to `possible` with `EXACT_NOT_ENTAILED_BY_SOURCE_NETWORK`.
  - An engine witness outside the network is replaced by the network's own witness (`ENGINE_WITNESS_REPLACED_BY_SOURCE_WITNESS`).

  The engine's original result is kept under `engine_result`.

  The check matters in two cases. The engine gives each step its own time variables, so one event bound to two steps can take two different times. And two events may share a source variable. In both cases the box admits a binding the source does not, and the tests build each case. The other difference, the bounded profile's strict start < end against the engine's end ≥ start, cannot change a decision on its own, because no query constraint bounds an end from above. The check would catch it anyway.
- **Soundness of the rest.** The engine's region contains the source region: the box contains the feasible set, end ≥ start is weaker than start < end, and the engine's step variables are independent. So `none` is sound, and exactness over the box implies exactness over the source.

## Clocks and units

- **One clock per patient.** The reference clock is the one the window names, or else the patient's first clock. A second clock whose origin and the reference's are both offset datetimes (`offset-datetime-microseconds-v1`) is rebased by the exact integer difference of the origins. Any other second clock, such as a second patient-local calendar clock, leaves its events out (`comparability: incomparable_clock`), and the patient is `incomparable` with `CLOCKS_NOT_REBASABLE`, never `none`.
- **Patients missing from the snapshot.** A patient with a window but no events in the snapshot is `incomparable` (`PATIENT_NOT_IN_SNAPSHOT`).
- **Units.** Declared units (`microsecond` through `week`) convert to microseconds exactly. Calendar months and years are refused (`CALENDAR_UNIT_REFUSED`). On patient-local clocks a day is a local calendar-coordinate difference, as in [patient-local clocks](patient-local-clocks.md), and the result records the snapshot's time domain.

## Statuses

The engine's statuses (`exact`, `possible`, `relaxed`, `unresolved`, `none`, `excluded`) are kept. Each patient result says who decided it (`decided_by: engine | adapter`). The adapter adds two statuses of its own: `refused` (non-box source constraints, or a window clock the patient cannot use) and `incomparable` (clocks that cannot be rebased, or a patient missing from the snapshot). Its downgrades reuse `unresolved` and `possible` under an `adapter.code`. Errors that affect the whole request raise `AdapterError` with a stable code.

## Store ingest rows

`result['events']` holds one row per snapshot event: the projection the execution store ingests. A row has:
- identity: snapshot context, patient, event, episode, record and record status;
- kind and kind class;
- clock: clock, policy, origin, reference clock and rebase offset;
- times: tightened start and end bounds on the reference clock, declared bounds, and `ongoing: false`;
- the start and end variables;
- comparability;
- evidence: evidence identifier and context, claim, bearer, source record, hash and key;
- selection state and claim supports;
- concepts with their bases.

## Example

[`examples/trajectory-graph-sulo/`](../examples/trajectory-graph-sulo/) re-expresses [`examples/trajectory-graph/`](../examples/trajectory-graph/) as a bounded source, a facts document and a request:
- times in microseconds on one offset-datetime clock;
- a point event becomes the shortest proper interval, ending one microsecond after it starts;
- each listed concept becomes a declared class fact.

The tests check that the committed files equal that conversion. Through the JSON route, the RDF route and the canonical graph route, the adapter's decisions equal the engine's on the original example: status, binding, relaxations, cost, certainty, completeness and notes. The witness and method differ by design, because the re-expressed events are intervals rather than points. The same decisions also come back through rustDL, with `seizure` entailed from `febrile_seizure` by a rule instead of being listed.

The MIMIC test runs the committed synthetic `examples/mimic-inputevents` fixture through the pending-claim import and a caller-declared accept-all analysis policy. It then runs the patient-local claim selection and graph route, and matches on the source item code class. The result reports `clinical_mapping_verified: false`.

## Not done

- **Source coverage.** The adapter cannot tell whether a source covers a patient's window. A patient with events is matched on the events it has. The store's coverage table is meant to answer that.
- **Complete search after a refuted binding.** When the witness check refutes the engine's possible binding, the adapter reports `unresolved` rather than searching for another binding.
- **Entailment for every policy class.** rustDL entailment is computed only for the policy classes the query names, at most eight, and within `semantic_support`'s model limits (64 individuals, about two per event). Entailment for every policy class, needed to index concepts in the store, is not done.
- **Non-box source networks.** These are refused rather than decided. Deciding them needs the engine to take source edges between events, not just per-event ranges.
- **Multiple stores.** Several stores, such as the MIMIC import's per-stay stores, are matched one snapshot at a time. Merging them is the store's job.
- **Recorded and available times.** These are not in a snapshot, and remain with the importers' claim provenance.
- **Clinical mappings.** None are verified.
