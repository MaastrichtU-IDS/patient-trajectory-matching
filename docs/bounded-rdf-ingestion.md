# Bounded RDF ingestion: profile 1.0

**Status:** Executable input profile `bounded-rdf-1.0`, feeding the existing
`bounded-interval-query-1.0` matcher. The temporal semantics remain bounded integer
microseconds with shared variables and fixed-witness possible/certain answers.
This adds a validated RDF input route; it does not add temporal operators or RDF
properties. The three new identifier classes use `sulo:hasValue`.

## Run

Use Python 3.12 and the pinned repository dependencies:

```sh
# Export the constructed bounded JSON fixture, validate its RDF, and match it.
python -m patterns.bounded_rdf

# Load the emitted graph through the external RDF input route.
python -m patterns.bounded_rdf --graph verification/bounded-rdf-run/graph.ttl --output verification/bounded-rdf-run/reloaded

python -m patterns.test_bounded_rdf
```

Both paths yield certain patient P1 and possible patients P1/P2, with P3 having no
recorded match and P4 incomparable. The first run writes `graph.ttl` and `result.json`
under `verification/bounded-rdf-run/`; the second run produces the same full result,
including evidence and context IDs. `--source` accepts the existing bounded JSON
contract; `--graph` accepts Turtle. These flags are mutually exclusive. `--query`
and `--output` select the existing query contract and output directory.

Python callers use `export_source(source_json)` to generate this RDF profile,
`prepare_graph(graph)` to validate and compile it, then the existing
`bounded_cohort.execute(snapshot, query)`. The prepared snapshot owns a copy of the
input graph. Editing the caller's graph after preparation does not alter it.

## Explicit identity and vocabulary

The original bounded JSON exporter encoded variable and constraint identifiers in
generated IRIs. That is insufficient for arbitrary external RDF. The new
[class module](../ontology/bounded-rdf-profile.ttl) adds these direct-part descriptors:

| Resource | Descriptor class | Required value |
|---|---|---|
| Single `bt:Snapshot` | `br:ProfileIdentifier` | `"bounded-rdf-1.0"^^xsd:string` |
| Every `bt:TemporalVariable` | `br:VariableIdentifier` | Unique variable identifier string |
| Every `bt:DifferenceConstraint` | `br:ConstraintIdentifier` | Unique constraint identifier string |

`bt:` is `https://example.org/trajectory/bounded/`; `br:` is
`https://example.org/trajectory/bounded-rdf/`. All descriptors carry their scalar
through `sulo:hasValue`. Existing record event IDs, record IDs, patient/episode IDs,
and clock IDs are read from their typed descriptors. No identifier is inferred from
an IRI's path, fragment, or suffix. Replacing every instance IRI consistently preserves
matching outcomes while changing the graph context and evidence identities.

The old `bounded_cohort` exporter retains its original format. Its output needs the
new identifier descriptors before this RDF reader will accept it; the new
`bounded_rdf --source` route adds them automatically from valid bounded source JSON.

## Validation contract

The [reader](../patterns/bounded_rdf.py) implements a closed procedural RDF contract.
Its ontology inputs are the pinned SULO and local PRO/SOLID, exact-interval, bounded,
and bounded-RDF class modules. It follows their named subclass paths to recognize
supported types. It accepts explicitly asserted supported supertypes alongside a
required concrete type. It does not perform general OWL consistency checks,
sameAs reasoning, arbitrary class inference, or SHACL evaluation.

| Structure | Enforced requirement |
|---|---|
| RDF terms | Named subjects and object resources; literals only through `hasValue`; no blank nodes |
| Types | The supported concrete type and only its named supertypes; conflicting or unsupported types fail |
| Snapshot | Exactly one marker, dataset ID, and snapshot ID; all variables and source constraints are direct members |
| Process | Exactly one supported Infusion or SpecimenCollection type, one participant role, and one occurrence interval |
| PRO patient binding | A PatientRole with exactly one Person bearer; `isFeatureOf` or its `hasFeature` inverse is accepted |
| Identity | Distinct roles, records, occurrence intervals, and endpoint descriptors per process; patient IDs cannot identify multiple people |
| Boundaries | Distinct StartDescriptor and EndDescriptor, each referring to one known variable; shared variables across descriptors are allowed |
| Variables | Unique explicit ID, one lower/upper bound, patient/episode IDs, one clock binding, and provenance |
| Source constraints | Unique explicit ID, typed left/right bindings, `left_minus_right_le` operator, one upper bound, and provenance |
| Units | Direct `bt:Microsecond` on integer bounds; direct `ei:Second` on clock origins; unit resources explicitly typed Unit |
| Scalars | Exactly one value with the specified datatype: string metadata, integer bounds, dateTimeStamp clock origins |
| Coverage | Every input triple must be consumed by a supported field or type assertion |

Instance graphs must contain the source representation, without separately expanded
participation/part closures. Additional participants, unknown predicates, ontology
axioms/imports, annotations, orphaned data, exact values on uncertain boundaries,
and unused structures fail validation. This prevents silent data omission. This
strict input contract is narrower than all RDF that could be consistent with SULO.

After structural validation, the shared bounded compiler checks identifiers,
performed status, finite int64 bounds, source/operator scope, proper intervals,
and complete source-network feasibility. Source constraints cannot cross a patient,
episode, or clock. An event's endpoint variables must agree with its PRO patient and
episode and share a clock. Different clocks between query candidates remain
INCOMPARABLE under the existing query semantics.

No supplied ontology imports are followed. Extending the accepted class vocabulary
or graph shape requires a new explicit capability, not an inferred clinical no-match.

## Evidence and literal handling

Matching uses a normalized internal record projection extracted from the graph.
That projection is not asserted to be the original source JSON. The supplied RDF
remains the source record for this input route, and the compiler does not reconstruct
synthetic IRIs or replace its metadata with newly generated source hashes.

Event evidence preserves process, PatientRole, Person, source record, interval, and
endpoint IRIs. Temporal edge evidence links to the actual variable/bound or source
constraint resources, including operand-binding IRIs, original bound spellings,
datatypes, units, and provenance descriptors. For example, `"+0001"^^xsd:integer`
compiles to integer 1 while retaining `+0001` in evidence. Integer spelling must
match `[+-]?[0-9]+`; whitespace, fractional forms, unsupported integer datatypes,
and out-of-range values are rejected.

Source hashes must be 64 lowercase hexadecimal characters and are preserved as
declarations. **They are not verified against unavailable original rows.** Evidence
labels this policy explicitly and retains source locations. A declared hash does
not become an independently verified authenticity claim.

The CLI parses after disabling RDFLib literal normalization through the existing
adapter initialization, and writes explicit quoted/datatype Turtle terms. Python
callers must retain literal spelling when parsing their graph; a spelling already
normalized by an upstream parser cannot be recovered. The graph digest records
the terms received, with triple order ignored. Blank-node canonicalization, logical
equivalence hashing, and OWL datatype-identity normalization are outside scope.

Contexts fingerprint the graph, normalized projection, profile, and relevant
implementation/schema/ontology bytes. Source JSON and its equivalent RDF can have
different profile context IDs; both routes through the new RDF adapter agree on the
complete result. Graph order does not change IDs. Changes to graph IRIs, lexical
forms, source declarations, or snapshot identifiers do.

An inconsistent source still yields `INCONSISTENT_SOURCE`, now with RDF context and
edge evidence attached to the negative-cycle certificate. Other invalid inputs,
including malformed Turtle, yield `INVALID_INPUT`. The CLI exits 2 in either case
and does not overwrite previous successful output. Check the exit code before
reading results. Ingested data never turns an error into a clinical absence claim.

## Verification and remaining scope

The 21-test suite checks source/RDF matching equivalence, full-result Turtle round
trips, arbitrary instance IRIs, literal/provenance preservation, input-copy isolation,
inverse role bindings, supported superclass assertions, required identifiers,
cardinality, descriptor identity, units, datatypes, references, patient/clock scope,
unconsumed triples, inconsistent-source certificates, and CLI failures. The existing
finite-world checker independently verifies decisions and certificates after RDF
compilation. Existing temporal and PRO/SOLID suites remain CI gates.

The shared temporal compiler was separated from the JSON graph writer so both input
routes use the same validation and solver semantics without fabricating graph
evidence. The RDF route closes the bounded profile's external graph ingestion gap
only for this declared structure. Full OWL support, rational strict inequalities,
identity normalization, clinical mappings, clock reconciliation, revision selection,
and optimized uncertainty search remain separate work. See the [formal-definition
alignment](bounded-temporal-uncertainty.md#relationship-to-formal-definition-v2).
