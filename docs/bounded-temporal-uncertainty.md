# Bounded temporal uncertainty: executable profile 1.0

**Status:** Executable synthetic-source profile `bounded-interval-1.0`, with query
profile `bounded-interval-query-1.0`. It supports finite, inclusive integer-microsecond
domains, shared variables, and conjunctive difference constraints. It adds classes
and one unit individual, with no new object or datatype properties. The existing
[exact interval](exact-interval-profile.md), [exact cohort](interval-cohort-matching.md),
and v2.4 point-anchor contracts remain independent.

## Run and inspect

Use Python 3.12 and the existing pinned dependencies:

```sh
python -m patterns.bounded_cohort
python -m patterns.test_bounded_intervals
```

Inputs are the constructed [source snapshot](../examples/bounded-interval/source.json)
and [query](../examples/bounded-interval/query.json). The CLI accepts `--source`,
`--query`, and `--output`; defaults write `graph.ttl` and `result.json` under
`verification/bounded-interval-run/`.

| Patient | Infusion-before-collection result | Reason |
|---|---|---|
| P1 | CERTAIN_MATCH | Both intervals move with the same uncertain anchor, preserving a two-microsecond gap |
| P2 | POSSIBLE_MATCH | Collection may meet or strictly follow the infusion |
| P3 | NO_RECORDED_MATCH | The fixed collection precedes the infusion |
| P4 | INCOMPARABLE | Candidate intervals use different clock resources |

`possible_patient_ids` includes P1 and P2; `certain_patient_ids` includes only P1.
The tiny microsecond domains support exhaustive test enumeration; these are not
realistic clinical durations or clinical terminology/source mappings.

Invalid syntax or profile data produce `INVALID_INPUT` and exit code 2. A source
network without a feasible timeline produces `INCONSISTENT_SOURCE`, exit code 2,
and a negative-cycle certificate. It never produces a vacuously certain match.
An unsuccessful run does not clear older output files; consumers must check the
exit code. Python callers use `bounded_intervals.prepare(source)` followed by
`bounded_cohort.execute(snapshot, query)`.

## Source contract and clock scope

The [schema](../schemas/bounded-interval.schema.json) describes source JSON at its
root and the query under `$defs.query`. Semantic validation supplements the schema.
The source envelope has `profile`, `dataset_id`, `snapshot_id`, `clocks`, `variables`,
`events`, and `constraints`. Extra fields are rejected.

| Record | Required fields and meaning |
|---|---|
| Clock | `clock_id`, explicitly offset `origin`, `scope`, `policy`; policy is `offset-datetime-microseconds-v1`, scope is `global` or `patient:<id>` |
| Variable | `id`, `patient_id`, `episode_id`, `clock_id`, `lower_us`, `upper_us`, `source_key`; inclusive bounds on one coordinate relative to that clock origin |
| Event | `id`, `record_id`, `patient_id`, `episode_id`, `event_kind`, `status`, `start_var`, `end_var`, `source_key` |
| Source constraint | `id`, `left_var`, `right_var`, `upper_us`, `source_key`; asserts `left_var - right_var <= upper_us` |

Kinds are `infusion` and `specimen_collection`; status is `performed`. Identifiers
are unique within their record category, event record identifiers are unique,
and all references must resolve. All bound values are signed 64-bit JSON integers;
booleans, floats, fractional microseconds, unbounded domains, and reversed bounds
are rejected. Internal negation and path sums use unbounded Python integers, so
valid input at an int64 boundary does not wrap or silently saturate.

An event's two variables must belong to its patient and episode and share one clock.
The compiler adds `start_var - end_var <= -1`, requiring a proper half-open occurrence
interval in every admitted source timeline. It does not assume that every pair
from independent marginal ranges is admissible. Shared variables can be referenced
by several distinct event-boundary descriptors; an event cannot use the same
variable as both endpoints because that makes its proper-interval constraint
inconsistent.

Source constraints may connect variables only within the same patient, episode,
and clock. Anchor variables without an event are allowed. Their bounds and
constraints still participate in source feasibility. Every source group is checked
before matching, including groups without selected events. A contradiction anywhere
rejects the snapshot; an unrelated query cannot hide it.

For example, `Bstart = Astart + 3` uses two source constraints:

```json
[
  {"id":"offset_upper", "left_var":"Bstart", "right_var":"Astart",
   "upper_us":3, "source_key":"synthetic:shared-anchor"},
  {"id":"offset_lower", "left_var":"Astart", "right_var":"Bstart",
   "upper_us":-3, "source_key":"synthetic:shared-anchor"}
]
```

This retains correlation even when Astart varies over a very wide range. An exact
coordinate is simply a variable whose lower and upper bounds coincide. A fixed
duration can likewise be represented by two constraints between an event's endpoints;
it is not fabricated as a recorded duration datum.

## PRO/SOLID representation

The [class module](../ontology/bounded-interval-profile.ttl) reuses the pinned SULO,
PRO/SOLID, and clock vocabulary. The synthetic adapter constructs this graph:

| Information | Graph pattern |
|---|---|
| Patient participation | Process → `hasParticipant` → PatientRole → `isFeatureOf` → Person |
| Occurrence | Process → `atTime` → `bt:OccurrenceInterval` |
| Boundary | Interval → `hasDirectPart` → StartDescriptor / EndDescriptor → `refersTo` → TemporalVariable |
| Variable bounds | Variable → `hasDirectPart` → LowerBound / UpperBound → `hasValue` integer, with direct Microsecond unit |
| Variable clock | Variable → `hasDirectPart` → ClockBinding → `refersTo` → clock resource |
| Difference constraint | Typed left/right operand bindings refer to variables; an OperatorDatum and unit-bearing UpperBound specify the supported inequality |
| Provenance | Typed source-key/hash/location descriptors on source records, variables, and constraints; dataset/snapshot identifiers on a snapshot information object |

The `bt:` namespace is `https://example.org/trajectory/bounded/`. All instance
literals use `sulo:hasValue`. An uncertain boundary or interval receives no sampled
exact timestamp. Distinct descriptors may refer to the same temporal variable
without becoming identical RDF resources. Feasible timelines appear only in the
result's explicitly labeled witness fields; they are not asserted as observed facts.

For the original `bounded_cohort` input route, **validated source JSON is authoritative**
and RDF is the generated evidence projection. The separate [bounded RDF input
profile](bounded-rdf-ingestion.md) now validates externally supplied graphs with
explicit variable/constraint identifiers and preserves their original evidence. It
uses a closed procedural contract, not general SHACL or OWL validation. The shared
compiler interprets source constraints; OWL axioms alone do not perform arithmetic. Ontology selection uses named subclass paths from the pinned local
modules, with no general OWL consistency, existential-generation, or rustDL claim.

## Query and answer semantics

Queries have the same required/distinct slot and conjunctive structure as the exact
cohort contract, but use profile `bounded-interval-query-1.0` and these selectors:
`bt:Infusion`, `bt:SpecimenCollection`, `bt:IntervalProcess`, `sulo:Process`, as full
IRIs. One to eight slots bind distinct named processes within one patient episode.
No optional slots, result limits, relaxation costs, unknown selectors, or arbitrary
OWL expressions are accepted. Empty constraint arrays impose no temporal comparison.

Let a binding select A with endpoints s,e and B with endpoints u,v:

| Operator | Compiled conjuncts |
|---|---|
| `before` | `e - u <= -1` |
| `meets` | `e - u <= 0` and `u - e <= 0` |
| `overlaps` | `s - u <= -1`, `u - e <= -1`, `e - v <= -1` |
| `gap` | `e - u <= -min_gap_us`, `u - e <= max_gap_us` |

Gap bounds are required, inclusive nonnegative int64 integers with min <= max.
They are prohibited for the other operators. Strictness uses one microsecond
**because this profile is discrete**. It is not a dense rational-time solver, a
calendar normalizer, a probability model, or a treatment of unknown time zones.

For the nonempty feasible source set F and a fixed named binding b:

\[
\operatorname{possible}(b) \iff \exists t\in F:\ Q(b,t),\qquad
\operatorname{certain}(b) \iff \forall t\in F:\ Q(b,t).
\]

Possibility adds **all** query atoms to the source network and checks joint
feasibility. Certainty checks each query conjunct against the **original source**
network. It never narrows F by the query before testing certainty. A possible but
not certain binding includes a source-feasible counterexample that violates Q.

The patient-level certain result requires one fixed named binding that works in
every feasible timeline: `exists binding, forall timelines`. It does not claim
the broader `forall timelines, exists binding` result. Tests include a case where
every timeline has a matching event, but the successful named event changes; the
profile correctly reports possible without a fixed certain witness.

Temporal edges across different clock resources are INCOMPARABLE even if origins
and coordinates look equal. Comparable query atoms are still solved jointly. If
they are impossible, the whole binding is IMPOSSIBLE; otherwise an incomparable
edge leaves both possible and certain flags null. No clock mapping is invented.

Each binding has status CERTAIN, POSSIBLE (possible but not certain), IMPOSSIBLE,
or INCOMPARABLE. All bindings are retained, including rejected ones and their
certificates. Episode status prefers CERTAIN_MATCH, then POSSIBLE_MATCH, then
INCOMPARABLE, otherwise NO_RECORDED_MATCH. The detailed bindings preserve mixed
outcomes. Patient lists deduplicate across episodes. Patients with no represented
events are outside scope. `search_complete: true` describes exhaustive named-binding
search within this declared profile, not complete clinical records or full temporal
OWL certain-answer semantics. Resource failure does not return a complete result.

## Relationship to formal definition v2

The [formal definition v2](temporal-kg/Temporal_Knowledge_Graph_Formal_Definition_v2.pdf)
§8.1–8.2 separates source eligibility and ontological support from temporal
possibility/certainty. This profile implements a discrete specialization of its
temporal checks and preserves the fixed-witness quantifier order. It is not a
conformance implementation of the full formal definition.

| Formal-definition capability | This executable profile |
|---|---|
| Rational coordinates and exact strict inequalities | Finite integer-microsecond domains; strict comparisons use the declared grid |
| OWL 2 DL support and identity normalization | Four named class selectors using pinned subclass paths; no complete OWL consistency or identity normalization |
| Eligible named endpoints and shared variables | Explicit source-backed endpoint descriptors and shared variable references |
| SAT(source and query) / UNSAT(source and negated query) | Joint feasibility plus source-bound entailment and counterexamples |
| Fixed witness before patient projection | Implemented; changing-witness counterexample in the suite |
| Evidence selection, revisions, as-of queries | One supplied snapshot; no revision/cutoff selection service |
| Primitive temporal core and richer bridges | Existing SULO descriptors and class-only extension; no adoption of the PDF's separate endpoint properties or temporal-to-OWL feedback |

Q1, Q3, Q6, Q7, Q8, and Q11 therefore remain broader operational decisions. The
PDF's reported OWL checks concern its own standalone core; they do not certify
this adapter. Our source, solver, and evidence tests establish only the narrower
contract documented here.

## Solver, evidence, and verification

The [temporal network](../patterns/temporal_stn.py) uses exact-integer Floyd–Warshall
closure with a distinguished zero node, finite-domain bound edges, source-difference
edges, and proper-interval edges. It detects inconsistency through a negative cycle.
Simple Temporal Networks are a standard basis for reasoning over conjunctive time
difference bounds; see [Hunsberger and Posenato (2021)](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.TIME.2021.1).

Source closure is computed once per patient episode and reused across bindings and
queries on that prepared snapshot. When it already entails every comparable query
atom, its witness and paths suffice without another closure. Otherwise, a combined
network checks possibility. A source network plus one negated unentailed atom
constructs a counterexample to certainty. Numeric closure takes cubic time and
quadratic space in the number of variables; retained path certificates add storage
and copying overhead. Binding search is exhaustive and can be exponential in slot
count. This is a correctness baseline, not an optimized uncertain cohort index.

Results include source-context and query hashes, full original source assertions,
PRO witnesses, event source hashes, compiled edges, and certificates:

- A possible witness assigns every variable in the episode and satisfies source
  constraints plus all comparable query atoms.
- A certain proof lists source edge paths whose summed bounds entail each query atom.
- An impossible result lists a closed negative-weight edge walk in source plus query.
- A possible-only result includes a source-feasible counterexample and the violated atom.

Source evidence paths refer to original bound/constraint rows or explicitly identified
proper-interval rules. All implementation, schema, and ontology bytes relevant to the
source and query contexts are fingerprinted. Identical ordered inputs reproduce
identical outputs. Hashes preserve source/query array ordering and are execution
identities, not semantic-equivalence identifiers. Prepared snapshots are trusted
internal objects; mutating them or substituting arbitrary serialized objects is not
a supported ingestion route.

The separate [finite-world oracle](../patterns/bounded_reference.py) enumerates the
original input domains and evaluates source and query inequalities directly. It
does not use the STN, query compiler, or shortest paths to decide truth. Its explicit
100,000-assignment cap fails rather than truncates. It is for small verification
cases; normal matcher execution does not enumerate time assignments.

The 22-test suite covers the four example outcomes, correlations, joint impossibility,
fixed-witness quantification, shared variable identity, inconsistent input, clocks,
patient/episode isolation, distinct slots, named subclass selection, RDF/PRO/SOLID
structure, evidence context, CLI errors, and wide/int64-boundary domains. It compares
250 seeded networks and their tight bounds with finite-world enumeration, checks
200 seeded query conjunctions, and compares 144 singleton-domain/operator cases
with the exact pair evaluator. Certificate checks independently replay paths and
cycles and verify witnesses/counterexamples against enumerated worlds.

The separate RDF input profile now handles validation and compilation of supported
graphs. Next steps are measured incremental or sparse temporal propagation and
safe uncertain candidate pruning with these
cases retained as differential gates. Clinical source coverage, terminology mappings,
clock reconciliation, dense-time semantics, probabilistic uncertainty, and complete
semantic reasoning each require explicit additional contracts.
