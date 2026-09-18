# State validity and coverage

## Motivation and runnable examples

Two low measurements thirty minutes apart do not establish a continuous low
state between them. A state query needs interval support, explicit refutation,
and a way to report the parts for which neither is available.

```sh
python -m patterns.state_validity \
  --source examples/state-validity/continuous.json \
  --query examples/state-validity/query.json \
  --graph /tmp/state-evidence.ttl
```

The query asks whether admitted interval assertions support `urn:state:low`
throughout [0,30 minutes). Substitute
one of the other committed source files to reproduce these outcomes:

| Source file | Explicit evidence | Result |
|---|---|---|
| `continuous.json` | Positive state assertions [0,15) and [15,30) | `HOLDS`; continuous support for 30 minutes |
| `samples-only.json` | Positive point observations at 0 and 30 | `UNKNOWN`; no interval support |
| `interrupted.json` | Positive [0,10), negative [10,20), positive [20,30) | `VIOLATED`; explicit refutation for 10 minutes |
| `gap.json` | Positive [0,10) and [20,30) | `UNKNOWN`; ten-minute knowledge gap |
| `conflict.json` | Positive [0,30), negative [10,20) | `BLOCKED_SOURCE_CONFLICT`; no coverage answer |
| `point-refutation.json` | Positive [0,10), admitted negative fact at 15 | `VIOLATED`; refuted although the remainder is unknown |
| `point-conflict.json` | Positive [0,30), admitted negative fact at 10 | `BLOCKED_SOURCE_CONFLICT`; no coverage answer |

All sources are synthetic. A negative record explicitly refutes **the same state
predicate**. A measurement described as normal, or a differently named state,
is not automatically interpreted as its negation.

## Contract and implementation

`state-validity-source-1.0` distinguishes proper `state_interval` records from
`point_observation` records. The latter have one coordinate and cannot be encoded
as zero-duration intervals. A `point_observation` is an **admitted point state
fact**: its polarity constrains the state's interpretation at that instant. Raw
or uninterpreted measurements must not be encoded in this kind; mapping them to a
state fact requires an explicit upstream admission rule, which this profile does
not supply. The source format is unchanged from the original release, but this
meaning is not: see the accepted decision below. Intervals and query windows are half-open with exact
integer-microsecond endpoints. Named clocks declare origin, unit and patient or
global scope. Queries select one patient, episode, state IRI and clock. State
IRIs match exactly; there is no inferred clinical state taxonomy in this profile.

The source records an `assertion_policy_id`. This is a caller declaration, **not
an independently verified acceptance decision**. Results are conditional on the
supplied state assertions. `assertion_policy_verified` and
`clinical_truth_verified` remain false. Source provenance is mandatory on every
record. This is an executable reference profile, not an admission pipeline for
clinical assertions.

The executor first validates the whole snapshot, checking every contradiction
between admitted facts within one state, patient, episode and clock. Any such
conflict blocks execution, even outside the requested window. Three kinds are
detected, each carrying its own extent fields rather than a synthesised
zero-duration interval:

| Conflict `kind` | Contradiction | Extent field |
|---|---|---|
| `interval_overlap` | Positive and negative intervals intersect | `start_us`, `end_us` |
| `point_in_interval` | An admitted point fact lies inside an interval of opposite polarity, using `start_us <= time_us < end_us` | `time_us` |
| `point_point` | Opposite admitted point facts share one instant | `time_us` |

Conflict detection is symmetric. A positive fact inside a negative interval
blocks exactly as a negative fact inside a positive interval does: reporting an
unqualified `VIOLATED` there would be as wrong as reporting an unqualified
`HOLDS`. The policy blocks rather than choosing which assertion to believe.
Different clocks are never equated because their numeric coordinates or origin
strings coincide, and facts on different clocks are never compared. Any relevant
record on another clock makes the query incomparable; this first profile has no
alignment adapter.

Within the requested window, endpoint cuts partition time into segments with
exact positive and negative evidence IDs. The algorithm unions touching or
overlapping support intervals, clips to the window, reports uncovered intervals,
and measures total and longest continuous support without double-counting.
Admitted point facts carry no extent, so they never enter that partition and
never change the segment durations, which continue to sum to the window.

The result is `VIOLATED` when a negative interval intersects the window or a
surviving admitted negative fact falls inside it, `UNKNOWN` when support is
incomplete and nothing refutes it, and `HOLDS` when positive interval support
covers the whole window. An explicit refutation suffices for `VIOLATED` even if
other parts remain unknown.

A negative fact inside the window therefore yields `VIOLATED` **with
`refuted_duration_us` of zero**, because no interval was refuted. Consumers
reading `coverage` alone would otherwise see an `UNKNOWN`-shaped profile beside a
`VIOLATED` status, so `coverage.refuting_point_count` and the top-level
`refuting_point_ids` and `points` fields record the reason. Each in-window point
appears in `points` with role `REFUTING` or `RETAINED`, and in
`point_observation_ids` as before.

Positive point facts alone never establish truth throughout an interval: three
positive facts across a window still return `UNKNOWN`. No persistence between
samples is inferred anywhere in this profile.

Window membership is half-open. An admitted negative fact at the window start
refutes; one at the window end lies outside the window and does not. A fact
outside the window is still checked for conflicts against the whole snapshot.
`HOLDS` continues to mean **complete interval-assertion support**, not verified
clinical truth. User interfaces must preserve that qualification.

### Accepted decision: admitted point facts constrain state

Earlier releases treated point observations as uninterpreted sample descriptions
that contributed neither refutation nor conflict, so a positive interval [0,30)
with a negative point at 10 returned `HOLDS`. That question was recorded as open.
It has since been decided: an observation explicitly admitted as a positive or
negative fact about a state at a point constrains that state's interpretation at
that point. The four consequences above — conflict under contradicting support,
refutation without it, no throughout-truth from positive points, and half-open
window membership — are that decision.

The query profile is therefore `state-validity-query-1.1`. The source profile
remains `state-validity-source-1.0` because the record format is unchanged, but
the documented meaning of `point_observation` is not: a snapshot that encoded
mapped samples in that kind will now block or refute where it previously did
not. Queries declaring `state-validity-query-1.0` are **refused** with a schema
validation error rather than executed under the superseded semantics. Results
carry the query profile and artifact digests in `context`, so the semantics that
produced any result remain identifiable.

“Coverage” here means coverage by explicit state knowledge. It does **not** prove
that a monitor ran continuously, all records were imported, or no other clinical
state occurred. Unknown gaps are not negative facts. Evidence identifies original
records for each segment and binds the source, query, policy identifier and
implementation artifacts to a result context.

## OWL 2 DL boundary

The optional RDF projection reuses the checked claim-description information-tree
encoder and decoder. The new ontology module adds only named subclasses of SULO
`InformationObject`; it uses the existing `hasDirectPart`, `refersTo` and
`hasValue` vocabulary and introduces no properties, rules or network imports.
Load it with the repository's pinned SULO core and claim-description module.
These additions use OWL 2 DL constructs; the tests check the restricted extension
and lossless round trip, not a new full-import reasoner conformance claim.

The graph describes the asserted records. It does not turn a report of a state
into an asserted clinical occurrence. Distinct point and interval descriptions
remain distinct through RDF round trips. Interval union, arithmetic, validation,
and the three outcome semantics execute outside OWL. No persistence rule is
silently added to the ontology.

## Open questions before operationalization

- **OPEN — state meaning and admission:** Which state predicates, explicit
  negations and assertion producers are trusted? Connect the policy identifier
  to checked acceptance decisions before consuming real records.
- **OPEN — sample-to-state inference:** If samples should imply persistence,
  specify thresholds, units, interpolation/hold policy, maximum gap, sensor
  quality and retraction behavior. Neither positive nor negative samples acquire
  interval validity in this implementation.
- **OPEN — time uncertainty:** Extend the exact-endpoint contract to bounded
  state boundaries and define possible/certain *coverage*, preserving shared
  source constraints. Exact union cannot simply be applied to bound extremes.
- **OPEN — acquisition completeness:** Define monitoring/record completeness
  separately from state truth before interpreting absence as negative evidence.
- **OPEN — conflict and revisions:** Choose acceptance, priority, availability
  cutoff and correction/retraction policies. The present whole-snapshot conflict
  check blocks rather than choosing an assertion to believe.
- **OPEN — ontology mapping:** Agree how admitted state occurrences relate to
  SULO qualities/processes and distinct time points/intervals. Current RDF is an
  information-object description, not a clinical occurrence projection.
- **OPEN — composition and scale:** Integrate state windows into trajectory
  matching and prove coverage for any batching strategy. Existing measurement-pair
  batching does not prove continuous state coverage. This bounded reference
  profile accepts at most 256 records; RDF export also retains claim-tree limits.

## Validation

Run `python -m unittest patterns.test_state_validity patterns.test_claim_projection`
with the pinned semantic dependencies installed. Tests check a finite-grid
coverage oracle, all five examples, overlapping and touching intervals,
clipping and half-open boundaries, exact provenance, state/episode/clock
isolation, conflicts, unknowns, invalid inputs, distinct point/interval records,
input ownership, RDF round trips, the class-only extension and CLI export.
