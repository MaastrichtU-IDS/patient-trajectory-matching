# Robust temporal relaxation

## Motivation and example

A temporal query can be possible without being certain because recorded times
are uncertain. A reviewed wider window can sometimes make it robust, without
changing the records or pretending that their uncertainty has disappeared.

```sh
python -m patterns.robust_relaxation \
  --source examples/robust-relaxation/source.json \
  --query examples/robust-relaxation/query.json \
  --policy examples/robust-relaxation/policy.json
```

The synthetic infusion ends at minute 1; collection starts between minutes 48
and 50. The gap is 47–49 minutes. The original maximum gap of 48 minutes is only
possible. A listed option widening it to 50 minutes costs 1.25 and is certain.
With a cost budget below 1.25, no robust match is returned; the possible match is
still reported. Costs are policy units, not probabilities or clinical utility.

## Implementation and semantics

`robust-temporal-relaxation-1.0` wraps the existing bounded cohort executor and
mixed point/interval record executor. Each catalogue option is a **complete**
modification with an exact nonnegative decimal cost; options are not implicitly
combined. An option can widen several explicitly relaxable targets. Up to 16
options are supported, with both a cost budget and a changed-target budget.
All options are validated, including those excluded by budget. The unchanged
query is always evaluated at cost zero.

For bounded queries only named gap constraints may be widened. For mixed
queries only the baseline-before-treatment window may be widened. Source bounds,
source selection policies, clock alignments, class/scalar/unit selectors,
non-relaxable constraints and follow-up windows remain fixed. Mixed eligibility
still depends only on baseline and treatment, never on observed response.

The quantifiers are:

`exists listed modification, exists named binding, forall feasible source timelines`.

A different witness in each world does not establish robustness. Neither does a
world-dependent choice of modification. The underlying solver classifies each
whole binding against the original source, and only CERTAIN bindings qualify.
The result retains every evaluation, its certificates and provenance, plus the
least-cost robust binding per patient, using deterministic ties. POSSIBLE is
reported separately. A blocked underlying execution blocks the aggregate answer;
partial evaluations are diagnostic evidence only.

The optimum is only over the explicit catalogue within budget and the represented
records. It is not an optimum over every possible rewriting or missing clinical
record. External arithmetic and catalogue optimization make no additional OWL
entailment claims.

## Open questions before operational use

- **OPEN — authority:** Who approves relaxable predicates, widened limits, costs,
  budgets and the catalogue version for a particular use?
- **OPEN — utility:** What domain meaning should the costs have? They are currently
  fixed penalties; no learned preference or clinical equivalence is assumed.
- **OPEN — search:** Is a finite catalogue sufficient, or is automatic generation
  and composition required? The latter needs an explicit search and optimality contract.
- **OPEN — integration:** Should extended Allen/duration queries also be relaxable?
  This increment deliberately supports the existing bounded and mixed profiles.
- **OPEN — workload:** Validate the cost of up to 17 executions on deployment data.
  This wrapper does not establish a new partitioning/coverage proof.
- **OPEN — records:** Existing source acceptance, semantic-support, timestamp and
  clinical-mapping limitations still apply to every relaxed result.

## Validation

Run `python -m unittest patterns.test_robust_relaxation patterns.test_bounded_intervals patterns.test_mixed_record_query`.
Install `patterns/requirements-semantic.lock.txt` for the real Rust semantic gate.
Tests cover the uncertain-gap example, exact decimal cost ordering, budgets,
zero-cost original, immutable input, fixed witnesses, protected constraints,
invalid changes, inconsistent sources, blocked variants, the mixed backend and CLI.
