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

All sources are synthetic. A negative record explicitly refutes **the same state
predicate**. A measurement described as normal, or a differently named state,
is not automatically interpreted as its negation.

## Contract and implementation

`state-validity-source-1.0` distinguishes proper `state_interval` records from
`point_observation` records. The latter have one coordinate and cannot be encoded
as zero-duration intervals. Intervals and query windows are half-open with exact
integer-microsecond endpoints. Named clocks declare origin, unit and patient or
global scope. Queries select one patient, episode, state IRI and clock. State
IRIs match exactly; there is no inferred clinical state taxonomy in this profile.

The source records an `assertion_policy_id`. This is a caller declaration, **not
an independently verified acceptance decision**. Results are conditional on the
supplied state assertions. `assertion_policy_verified` and
`clinical_truth_verified` remain false. Source provenance is mandatory on every
record. This is an executable reference profile, not an admission pipeline for
clinical assertions.

The executor first validates the whole snapshot, including positive/negative
interval conflicts within each state, patient, episode and clock. Any such
conflict blocks execution, even outside the requested window. Different clocks
are never equated because their numeric coordinates or origin strings coincide.
Any relevant record on another clock makes the query incomparable; this first
profile has no alignment adapter.

Within the requested window, endpoint cuts partition time into segments with
exact positive and negative evidence IDs. The algorithm unions touching or
overlapping support intervals, clips to the window, reports uncovered intervals,
and measures total and longest continuous support without double-counting.
The result is `HOLDS` when positive support covers the entire window,
`VIOLATED` when any non-conflicting negative interval intersects it, and `UNKNOWN`
otherwise. An explicit refutation suffices for `VIOLATED` even if other parts
remain unknown. Point observations, including negative ones, are retained as
records; they are not interval assertions and establish no interval coverage.

In particular, a positive interval [0,30) and a negative point observation at 10
still return `HOLDS`. The point is retained in the evidence but contributes
neither interval refutation nor an interval conflict. Here `HOLDS` means
**complete interval-assertion support**, not agreement with every observation
or verified clinical truth. User interfaces must preserve that qualification.

Before composing this result with clinical trajectory eligibility, reviewers
must decide whether point observations remain uninterpreted sample descriptions
or are admitted as point refutations/conflicts. Point refutation would not require
assuming persistence between samples. That semantic extension is not implemented
by this profile.

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
