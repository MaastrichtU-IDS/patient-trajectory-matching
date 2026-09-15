# Exact interval cohort matching: query contract 1.0

**Status:** Executable Python profile `interval-cohort-1.0`, using the validated
[exact interval projection](exact-interval-profile.md). It searches named recorded
events across represented patient episodes in one graph/snapshot. It adds no RDF
classes or properties and leaves the v2.4 point-anchor API and oracle independent.
All examples are constructed; clinical terminology and source mappings remain work.

## Run

Use the pinned dependencies and Python 3.12 from the repository README:

```sh
python -m patterns.interval_cohort
python -m patterns.interval_cohort --engine reference --output verification/interval-cohort-run/reference.json
python -m patterns.test_interval_cohort
```

The default [source](../examples/interval-cohort/source-rows.json),
[manifest](../examples/interval-cohort/manifest.json), and
[query](../examples/interval-cohort/query.json) yield:

| Patient / episode | Result | Bindings |
|---|---|---|
| P1 / E1 | MATCH | A → D and C → D |
| P2 / E1 | NO_RECORDED_MATCH | Its infusion occurs after its collection |
| P3 / E1 | INCOMPARABLE | The two candidate events use different clock resources |

The result is written to `verification/interval-cohort-run/result.json`. The
reference engine writes the same semantic result, with different execution counters.
The CLI also accepts `--source`, `--graph` (Turtle instead of synthetic rows),
`--manifest`, `--query`, and `--output`. Profile/schema failures exit 2; consumers
must check successful completion before reading output. Failure does not clear an
older output file. Other parser/runtime errors also fail the run rather than return
a negative match.

## Query semantics

The [JSON Schema](../schemas/interval-cohort.schema.json) and semantic checks in
[`validate_query`](../patterns/interval_cohort.py) jointly define the contract:

```json
{
  "profile": "interval-cohort-1.0",
  "id": "infusion_before_collection",
  "slots": [
    {"id": "a", "class_iri": "https://example.org/trajectory/interval/Infusion"},
    {"id": "b", "class_iri": "https://example.org/trajectory/interval/SpecimenCollection"}
  ],
  "constraints": [
    {"id": "order", "left": "a", "right": "b", "operator": "before"}
  ]
}
```

There are one to eight required slots, with unique names. Each slot binds one
distinct process; different slots cannot reuse a process even if their selectors
overlap. All slots share the patient bearer, patient identifier, and episode.
All constraints are conjunctive. An empty constraint array permits all injective
class-selected bindings within that scope, without asserting a temporal relation.
Constraint identifiers are unique; endpoints must name different existing slots.

| Field / operator | Contract |
|---|---|
| `class_iri` | One of `ei:Infusion`, `ei:SpecimenCollection`, `ei:IntervalProcess`, `sulo:Process`, written as its full IRI |
| `before` | `left.end_us < right.start_us` |
| `meets` | `left.end_us == right.start_us` |
| `overlaps` | `left.start_us < right.start_us < left.end_us < right.end_us` |
| `gap` | Inclusive `min_gap_us <= right.start_us - left.end_us <= max_gap_us` |

Only gap constraints admit and require `min_gap_us` and `max_gap_us`: integer
microseconds with `0 <= min <= max <= 2^63-1`. Booleans, floats, rounding, unknown
operators/selectors, extra fields, optional slots, and result limits are rejected.
The eight-slot bound limits recursion depth, not execution cost.

Class selection uses the existing named subclass derivation on the pinned local
ontology modules. For example, an explicitly typed Infusion can satisfy
IntervalProcess or Process. The original process/role/bearer and source evidence
remain attached. This is a finite capability profile; it does not accept arbitrary
OWL expressions or treat unsupported entailment as a failed match. It generates
no existential witnesses and makes no full OWL consistency claim.

## Scope, comparability, and completeness

A temporal edge requires exactly the same clock resource and descriptor. Equal
coordinate values or equivalent-looking origins do not establish a clock mapping.
No comparison crosses patient episodes. Distinct episodes of the same patient are
searched separately; `matched_patient_ids` deduplicates patients with at least one
matching episode. Patients with no projected events are outside the result scope;
there is no external cohort roster in this profile.

Within a complete named binding:

- Every constraint satisfied yields a match.
- At least one comparable false constraint rejects the conjunction, even when
  another edge is incomparable.
- With no false constraints and at least one incomparable edge, the binding is
  returned under `unresolved_bindings`. This describes missing comparability,
  not a proven possible realization of an uncertain temporal system.

Each episode reports MATCH if any matching binding exists; otherwise INCOMPARABLE
if any unresolved binding remains; otherwise NO_RECORDED_MATCH. A MATCH episode
can still contain unresolved bindings, which remain visible. Clock compatibility
is required on temporal edges; disconnected slots have no implied comparison.

`search_complete: true` means all matching and unresolved bindings were enumerated
over the represented, validated snapshot and supported selectors. There is no
truncation, approximate retrieval, or early stop. It does not assert complete
clinical records, absence of an event in reality, or full temporal certain-answer
semantics. Invalid ingestion aborts the query. A timeout or resource failure does
not produce a complete result.

As an additional cohort arithmetic precondition, the total endpoint span within
each patient/episode/clock group must fit a signed 64-bit positive difference.
This is checked before either search, including for unselected events. It ensures
index pruning cannot hide an overflow that pair evaluation would expose. It is
stricter than admitting arbitrary individually valid int64 coordinates.

## Evidence and execution

Python callers use `prepare(graph, manifest)` once, then
`execute(snapshot, query, engine='indexed')` for each query. PreparedSnapshot is
an internal trusted projection; modifying its fields or loading arbitrary
serialized records bypasses the contract and is not a supported ingestion route.
Preparation retains the exact adapter's validated type view and evidence bundle.

Each binding identifies the selected class, event, process, PatientRole, bearer,
and interval evidence ID. Each constraint includes the pair evaluator's exact
endpoints, arithmetic rule, relation or incomparability reasons, and evidence
references. The embedded evidence preserves source records, original lexical
values, normalization, duration, clock, and snapshot context.

The query context hashes the complete query, the source projection context, and
both matcher implementations plus the query schema. Reordering input graph triples
or interval records preserves results. Query array order is retained in the hash;
this is reproducible execution identity, not logical query equivalence. Both
engines share this semantic identity; the `execution` field identifies the engine
and its counters. Results are sorted deterministically and retain all named
bindings, including multiple witnesses for one patient.

The indexed engine partitions by patient episode, selects candidates by class,
builds sorted start-coordinate indexes per slot/clock, and joins the smallest
candidate domain first. A constraint whose left slot is already bound narrows the
right slot by binary search; reverse edges and residual overlap conditions receive
incremental checks. Incompatible-clock candidates stay eligible. No all-pairs
precedence closure is materialized. Indexes are rebuilt for each query; persistent
indexes, plan optimization, streaming, and result pagination remain future work.

The [reference matcher](../patterns/interval_cohort_reference.py) enumerates the
Cartesian product of slot candidates, removes repeated processes, and evaluates
the endpoint inequalities independently. It shares validated projection and result
rendering with the optimized engine, but does not use its search, indexes, or
predicate evaluator to decide membership. Differential checks therefore cover
search correctness; the adapter's own conformance suite separately covers ingestion.

## Verification and next steps

The suite includes independently specified cohort outcomes, contact/gap/overlap
boundaries, subclass selectors, required/distinct slots, patient-episode isolation,
false-plus-incomparable conjunctions, simultaneous matches and unresolved bindings,
invalid queries, evidence preservation, RDF round trips, context changes, CLI
parity, and pre-search overflow rejection. It checks 300 seeded three-slot scenarios
against exhaustive search, including reverse constraints and mixed clocks.

A constructed 100-by-100 contact join returns the same 100 matches with 200 indexed
candidate extensions, versus 10,000 complete tuples in the exhaustive engine.
These are operation counts on a fixture, not clinical-scale timing results. Worst
case search and output remain exponential in the slot count, particularly with
broad selectors, unconstrained slots, or incompatible clocks.

The separate [bounded uncertainty profile](bounded-temporal-uncertainty.md) now adds
shared-variable feasibility and fixed-witness certain/possible semantics. It uses
exhaustive binding search with a temporal network solver; these exact cases remain
regression gates for the independently optimized exact engine.
Before clinical scaling, establish source coverage and terminology mappings,
benchmark representative distributions, and define cancellation/partial-result
semantics. Patient-to-patient similarity, relaxation costs, general interval ASTs,
and the planned Rust ontology stack remain separate implementation work.
