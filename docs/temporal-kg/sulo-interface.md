# SULO conformance for the bounded temporal query interface

**Status:** Executable `temporal-interface-1.0` conformance harness. Eight frozen pairs of formal-core and SULO graphs yield the same common coordinate view and answers to 33 query comparisons. This verifies a restricted query interface, not ontology equivalence or full OWL/rational-time conformance.

The [v2 report](Temporal_Knowledge_Graph_Formal_Definition_v2.pdf) and [standalone validation package](validation/README.md) define and check the formal ontology. The [revision response](revision-response.md) identifies the mapping obligations. This increment provides executable evidence for part of Q2/Q6/Q8 while keeping the remaining obligations explicit in the [machine-readable register](sulo-conformance.json).

## Representation decision and supported fragment

The standalone core treats `TimePoint` and `TimeInterval` as primitive temporal entities. In pinned SULO 0.2.14, `TimeInstant` and `TimeInterval` specialize `Time`, which specializes `Quantity` and `InformationObject`. `StartTime` and `EndTime` are disjoint subclasses of `TimeInstant`. A SULO interval description requires start/end descriptions and has existential duration/unit obligations. These are different ontological commitments.

Consequently this interface asserts no class/property equivalence between the two ontologies. It extracts the coordinate constraints and eligible process bindings needed by a common query fragment. Start/end descriptions, primitive points, coordinate variables, and source records keep separate identities and evidence.

The common fragment is deliberately small:

- Explicit named formal witnesses, typed directly as the core's `Process`, `PatientRole`, `Patient`, `TimeInterval` and `TimePoint` classes.
- Named SULO data admitted by the existing [bounded RDF input profile](../bounded-rdf-ingestion.md), including its supported process kinds and PRO/SOLID validation rules.
- Proper bounded intervals, finite signed integer-microsecond domains, and conjunctive source difference constraints. Auxiliary variables are permitted; all source constraints are retained.
- Required distinct process slots with the common selector `sulo:Process`, and the existing `before`, `meets`, directional `overlaps` and inclusive `gap` operators.
- Explicit clock identities and scope; incomparable clocks remain incomparable. There is no clock reconciliation.

The formal RDF files use the standalone core's existing vocabulary, supplied separately from SULO instance graphs. Their external coordinate/eligibility tables are part of the fixture interface. They are not timestamps inferred from OWL. No new application properties or SULO ontology axioms are introduced.

The formal fixture reader requires explicit supported types and one named value for each structural link. It does not perform OWL inference over the core. Likewise, SULO ingestion uses the existing bounded reader's limited named subclass recognition. Antibiotic classification through an administered-drug role, general ground OWL entailment, and inferred identity remain outside this comparison.

## Executable mapping contract

Each fixture directory contains independent frozen `formal.ttl` and `sulo.ttl` inputs, an `interface.json` coordinate/eligibility table, and `queries.json`. The implementation is [patterns/temporal_interface.py](../../patterns/temporal_interface.py).

| Interface element | Formal input | SULO input | Checked correspondence |
|---|---|---|---|
| Eligible process | Explicit `Process` plus an entry in `interface.json.processes` | Supported process class and source-record event identifier | Stable event ID and patient/episode scope |
| Patient participation | `hasParticipant` → `PatientRole` → `isFeatureOf` → `Patient` | `sulo:hasParticipant` → application `PatientRole` → `sulo:isFeatureOf` → Person | Explicit role/bearer witness for every event; raw IRIs retained |
| Complete occurrence | Exactly one selected `exactExtent` link to a `TimeInterval` | Exactly one selected `sulo:atTime` link to a bounded occurrence description | Event-to-interval selection under this profile; no global functionality axiom on `atTime` |
| Beginning/end | Unique `hasBeginning` / `hasEnd` named point; coordinate table maps that point to a variable | Typed StartDescriptor / EndDescriptor direct parts, each referring to a variable | Same canonical start/end variable for each event |
| Variable domain | External coordinate table | Typed SOLID lower/upper descriptors with a unit and clock | Variable ID, patient/episode, clock and inclusive integer bounds |
| Source constraints | External coordinate table | Typed operand/operator/bound descriptors | Same named inequalities, with all shared variables retained |
| Clock | External declaration | Typed clock description | Same identifier, origin, scope and policy; no equality inferred from similar-looking timestamps |

`interface.json` contains exactly `profile`, `clocks`, `variables`, `constraints`, `points` and `processes`. Each process entry declares its canonical ID, formal process IRI and patient/episode. Each point entry names a formal point and its coordinate variable. Variable and constraint fields are the normalized bounded fields without source metadata. The reader validates references, scopes, finite integer bounds and unique identities. Those explicit eligibility/canonical-ID declarations are fixture inputs; this harness does not establish their correctness against clinical sources.

The common view contains clocks, variable domains, process endpoints/scopes and source inequalities. It intentionally omits source metadata, graph-specific IRIs and process subclasses beyond the common `Process` selector. Complete raw process, role, bearer, interval and boundary IRIs are retained alongside the view so the correspondence can be inspected. Equal views do not establish equivalence of the complete graphs, their provenance, or their OWL model sets.

The declared event ID pairs processes and their unique role/interval witnesses; the patient identifier pairs bearers. Endpoint correspondence concerns coordinate variables and may map one primitive point to several SULO descriptions. Endpoint-identity queries are outside the common fragment. These correspondences add no `owl:sameAs` assertions between source resources.

### Why the temporal answers are preserved within this fragment

For equal admitted views, both routes have the same coordinate variables, domains, source inequalities and proper-interval conditions. They therefore define the same feasible coordinate assignments. Both routes enumerate the same distinct event-ID bindings within the same patient/episode scopes. Each supported query atom has the same endpoint inequality and clock-comparability condition on those bindings.

Thus possible and fixed-witness certain results agree within the declared fragment: possibility tests the whole query conjunction over the feasible set; certainty quantifies over the original feasible set; patient projection occurs after binding-level certainty. This argument assumes the declared eligibility and ID correspondence and does not prove an OWL interpretation mapping.

The tests compare two implementations: the formal route directly enumerates finite assignments using the existing independent reference evaluator; the SULO route uses RDF ingestion, the production temporal network and matcher. The formal route never calls the production constraint compiler. Conformance execution reads frozen SULO graphs and never regenerates them through the writer. The finite reference has an explicit 100,000-assignment cap; exceeding it fails rather than returning a truncated complete answer.

## Paired fixtures and deliberate differences

| Fixture | Main observation |
|---|---|
| `ordered` | Strict ordering and a positive gap agree across representations |
| `coincident_boundaries` | Distinct formal points are explicitly declared different but have equal coordinates; corresponding SULO descriptions/variables remain distinct |
| `shared_boundary` | One formal point serves as A's end and B's beginning; two distinct SULO boundary descriptions refer to one shared variable |
| `correlated` | Shared constraints establish a certain gap despite overlapping marginal endpoint ranges |
| `possible_only` | A boundary can meet or strictly follow another; strict precedence is possible-only |
| `incomparable` | Distinct clock resources prevent a supported comparison |
| `chain_order` | Shared source inequalities A-before-B and B-before-C, together with proper intervals, establish A-before-C despite overlapping input bounds; no transitive ontology property is added |
| `joint_impossibility` | Individually feasible order conditions cannot jointly hold; the conjunction is impossible |

The paired negative tests distinguish ontology permissibility from operational admissibility. Missing named endpoints, unknown complete extent and point occurrences can be allowed by the formal ontology while being unsupported by this interval interface. Multiple named endpoints are rejected instead of invoking identity merging. Explicit type conflicts are rejected procedurally without claiming a general OWL inconsistency proof.

Formal endpoint identity and coordinate equality also differ: using the same point individual for one interval's beginning and end violates the standalone core's disjoint endpoint-property condition. Distinct SULO start/end descriptions can refer to the same variable, but the temporal layer then rejects the zero-duration interval. Distinct formal points at equal coordinates likewise fail positive-duration validation when they bound one interval, even though point distinctness alone holds.

Only explicit named point `owl:differentFrom` facts are admitted as additional formal fixture assertions; they are preserved as evidence and do not imply different coordinates. `owl:sameAs`, arbitrary qualitative order facts and other unconsumed triples are rejected. The positive qualitative-assertion compiler contemplated in v2 §8 remains a separate extension. Source constraints in this harness enter through the external coordinate table or the existing SULO constraint descriptors.

The correction test additionally selects the existing evidence archive before and after its explicit correction, maps both selected SULO graphs, and compares them to independently adjusted formal coordinate tables. Process/role/bearer/boundary bindings remain stable while coordinates and query answers change. This checks the selected source interface; the fixture interface itself is not an archive selector.

## Register for the original 18 checks

The original [18 OWL checks](validation/checks.json) retain their separate reproduced results. The following classification does not rename procedural validation as OWL reasoning. Exact test links, original expected values and rationale are recorded in [sulo-conformance.json](sulo-conformance.json).

| Original checks | Responsibility | SULO interface coverage |
|---|---|---|
| C01–C02: OWL 2 DL profiles | OWL tooling | Standalone core/example verified separately; full SULO alignment/import-closure profile checking remains open |
| C03–C04: consistency and class satisfiability | OWL reasoner | Not implemented by this interface |
| C05: antibiotic administration inference | OWL reasoner | Outside the common `Process` selector; pending engine integration |
| C06: participation chain | OWL reasoning and ingestion | Explicit PRO path preservation checked; the existing PRO/SOLID profile's limited chain closure remains separate |
| C07: ExtendedProcess inference | OWL reasoner | Proper intervals validated, but this formal class inference is not implemented |
| C08: point/interval conflict | OWL reasoning and ingestion | Conflicting explicit types rejected by the profiles; no general consistency claim |
| C09: identical interval endpoints | OWL identity and temporal evaluator | Deliberate identity/description distinction; zero-duration rejection is checked separately |
| C10–C12: anonymous endpoints, unknown extent, point occurrence | Ingestion | Ontologically permitted cases can be outside this named interval interface |
| C13: point and interval extents | OWL reasoning and ingestion | Unique selected extent is a profile requirement; SULO `atTime` is not made functional |
| C14: two-way pointBefore | OWL reasoning and temporal evaluator | Qualitative OWL fact compilation is outside this fragment |
| C15–C16: functional endpoint identity/conflict | OWL reasoner and ingestion | Inferred identity remains open; ambiguous endpoint fields are rejected |
| C17: absence of OWL transitive closure | Temporal evaluator | Numeric precedence evaluated; no transitive RDF property or closure facts added |
| C18: reversed endpoint order | Temporal evaluator | Non-proper numeric intervals rejected before answers; general qualitative compilation remains open |

## Run, evidence and next gate

```sh
python -m patterns.temporal_interface
python -m patterns.test_temporal_interface
```

Expected: eight paired cases and 33 query comparisons pass; 22 top-level tests pass. Outputs go to `verification/temporal-interface-run/result.json`. They include common views, raw witness mappings, normalized binding/patient answers, input and implementation hashes, and `full_owl_mapping_verified: false`. The 33 comparisons and mutation scenarios are nested within the 22 tests, not added again to the repository test total. The optional standalone Java checks remain a separate count and dependency; this harness uses Python and the existing pinned packages.

The next ontology gate is operation-specific support for ground class entailment, consistency and identity normalization through the planned Rust reasoning interface. Add paired cases for each supported operation and prove any new compiler bridges before widening the conformance claim. Source-specific eligibility/canonicalization, rational strict constraints, temporal situations and clinical pattern approval remain independent obligations.
