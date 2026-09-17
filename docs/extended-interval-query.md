# Extended interval queries

## Motivation and example

The bounded query profile only exposes before, meets, overlaps and bounded gap.
Containment, duration, and a minimum amount of shared time are also useful for
trajectory queries. The new `extended-interval-query-1.0` profile supports all 13
basic Allen relations, signed gap bounds, inclusive duration bounds, and a
strictly positive minimum overlap. Existing profiles retain their contracts.

```sh
python -m patterns.extended_interval_query \
  --source examples/extended-interval-query/source.json \
  --query examples/extended-interval-query/query.json
```

The synthetic example has an infusion [0,10) and a collection whose start is
in [3,4] and end in [6,7], in integer microseconds. The infusion contains the
collection, has duration exactly 10, and shares at least 2 microseconds with it
in every feasible source timeline. The result identifies patient P as certain.
These deliberately tiny times illustrate the arithmetic, not clinical practice.

## Implementation and semantics

The input source remains `bounded-interval-1.0`. Its existing validation rejects
inconsistent sources before query execution. The new JSON Schema describes
binary predicates and unary duration predicates. Selectors use the built-in
bounded-event class hierarchy. Events in different slots must be distinct.

Each Allen relation is a conjunction of endpoint comparisons. Strict order is
encoded with a one-microsecond separation on the declared discrete grid. Duration
bounds use end minus start. Minimum overlap d > 0 becomes four constraints:
each of the two end points must be at least d after each of the two start points.
This is exactly `min(end1,end2)-max(start1,start2) >= d`; merely requiring overlap
and long individual durations would be insufficient.

The executor enumerates named bindings within represented patient/episode scopes
and calls the existing STN classifier. It retains shared source constraints,
fixed-witness possible/certain semantics, clock incomparability, negative cycles,
entailment paths, possible witnesses, and counterexamples. Result contexts hash
the query, source context, compiler, schema and solver artifacts. Complete search
means all represented bindings, not complete clinical records.

## Boundaries and open questions before deployment

- **OPEN — source policy:** Which clock alignments, endpoint precision, performed
  event assertions, and missing-record policies are acceptable for the deployment?
- **OPEN — scale:** This reference executor enumerates candidate bindings. Set and
  validate workload limits or prove a pruning/batching strategy before large jobs.
- **OPEN — selector integration:** Should the new grammar be exposed through the
  semantic-support gate and service API? This increment uses only built-in classes.
- **OPEN — disjunction:** Only conjunctions of basic relations are supported;
  disjunctive Allen networks need a separate solver/profile and complexity policy.
- Minimum overlap zero is intentionally excluded: zero shared time includes
  disjoint intervals under the conventional nonnegative overlap-length definition.
- This is external arithmetic reasoning over an OWL-compatible representation;
  it neither adds OWL constructors nor claims OWL 2 DL can compare date literals.

## Validation

`python -m unittest patterns.test_extended_interval_query
patterns.test_bounded_intervals patterns.test_exact_intervals` (one shell line).
The new suite checks every basic relation and metric predicate against independent
endpoint arithmetic on a finite grid; uncertainty against enumerated feasible
worlds; returned witnesses and counterexamples; validation; clock mismatch;
fixed binding semantics; legacy profile rejection; and the example CLI.
