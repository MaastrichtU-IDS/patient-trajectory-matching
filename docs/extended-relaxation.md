# Explicit metric relaxation for extended interval queries

The finite-catalogue optimizer now accepts `kind: "extended"` with an
`extended-interval-query-1.0` query. This connects the query language emitted by
the two-slot editor to fixed-option robust matching through the Python API and
CLI. It does not automatically relax editor requests or adopt a clinical policy.

```sh
python -m patterns.robust_relaxation \
  --source examples/extended-relaxation/source.json \
  --query examples/extended-relaxation/query.json \
  --policy examples/extended-relaxation/policy.json
```

## Example and expected result

The existing tiny arithmetic fixture has infusion `[0,10)` and collection start
in `[3,4]`, end in `[6,7]`, measured in integer microseconds. The query requires
infusion **contains** collection, infusion duration exactly 9, and minimum shared
time 3. This intentionally inconsistent duration question illustrates repair;
these numbers have no clinical interpretation.

| Catalogue option | Metric changes | Cost | Result for P |
|---|---|---|---|
| original | None | 0 | Impossible |
| duration-only | Duration range 9–10 | 0.5 | Possible, not certain |
| overlap-only | Minimum shared time 2 | 0.5 | Impossible |
| both-metrics | Both changes as one listed option | 1.25 | Certain |

The optimum is `both-metrics`, cost `1.25`. A cost budget below 1.25 or a
changed-target budget of one excludes it, leaving no robust match. Removing it
also leaves no robust match: the optimizer never implicitly combines the two
cheaper options. All comparisons use exact decimal costs.

The containment requirement, source endpoints and chosen event pair remain
unchanged. Minimum shared time 2 is guaranteed in all four feasible integer
worlds. Minimum shared time 3 holds in some of them, with both a satisfying
assignment and a counterexample retained in the evaluation report.

## Allowed changes

Only declared entries in `relaxable_targets` are eligible:

| Target operator | Change fields | Required weakening |
|---|---|---|
| `gap` | `target`, `lower_us`, `upper_us` | Lower bound no greater, upper bound no smaller, at least one strictly wider; signed bounds allowed |
| `duration` | `target`, `lower_us`, `upper_us` | Same widening rule, with a strictly positive lower bound |
| `minimum_overlap` | `target`, `minimum_us` | Strictly lower than the original minimum and still positive |

Example complete catalogue option:

```json
{
  "id": "both-metrics",
  "cost": "1.25",
  "changes": [
    {"target": "duration", "lower_us": 9, "upper_us": 10},
    {"target": "shared", "minimum_us": 2}
  ]
}
```

Every change is validated before budget filtering. An invalid or protected change
cannot be hidden in an excluded option. The emitted extended query is validated
again before execution. The existing bounded and mixed profiles retain their
range-change syntax and reject overlap-only changes.

All thirteen Allen predicates are protected. Selectors, slot identity, constraint
identity, clock alignment and source evidence cannot be changed by the catalogue.
No predicate is dropped and no zero-overlap relaxation is admitted. Contradictory
hard constraints still prevent a match.

## Certainty, evidence and failure behavior

The quantifier order remains **one listed modification, one fixed named binding,
every feasible source timeline**. Covering different worlds with different
options or different bindings is insufficient. Equal-cost ties continue to prefer
the unchanged original; all evaluations remain in the report. Clock mismatch
remains incomparable unless a comparable condition already rules out the pair.

The wrapper prepares the source once and evaluates every admitted complete option
against that same source. Its context includes the extended compiler/schema
hashes, and each evaluation retains its source context, witness, counterexample
or contradiction evidence. An inconsistent source or incomplete underlying
execution blocks the aggregate: membership fields remain null, with any partial
evaluations retained only for diagnosis.

The catalogue limit remains sixteen options plus the original. This is exhaustive
reference execution over represented bindings, not an automatic modification
generator, a scalable search guarantee or a clinical-equivalence claim.

## Relation to the editor

`app.interval_editor.compile_query(controls)` emits the compatible extended query.
An explicit policy can be passed with that query and its admitted source to
`patterns.robust_relaxation.execute(source, query, policy)`. Integration tests
exercise an actual editor-produced duration/overlap conjunction. The HTTP editor
still runs the original question; displaying or accepting relaxation catalogues
in that interface is the next separate step.

The editor now also rejects nonpositive duration bounds as HTTP 400. The original
engine schema requires positive duration bounds; the form previously advertised
zero, which could leak a schema error as HTTP 500. The form and documentation now
match that contract, with regression cases for zero lower/upper bounds.

## Verification

```sh
python -m unittest patterns.test_extended_relaxation patterns.test_robust_relaxation
python -m unittest app.test_interval_editor
```

The new suite independently enumerates all four source worlds for the example,
checks certificates against endpoint arithmetic, and covers budgets, signed gaps,
both duration widening directions, positive-overlap limits, protected Allen
predicates, clock mismatch, original-first ties, fixed witnesses/options,
incomplete execution and legacy rejection. CI runs it with the legacy bounded
and mixed suites. No draft semantics from PR #48 are adopted.
