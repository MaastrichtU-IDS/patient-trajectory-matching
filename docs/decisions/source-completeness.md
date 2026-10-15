# Decision: what establishes that a source search was complete

**Status:** **Accepted 15 October 2026.** Completeness is derived from a declared coverage
warrant and is no longer an input. A bare `source_search_complete` boolean is refused.

**Related:** [reference oracle](../../reference_oracle.py); [PRO/SOLID adapter](../../patterns/pro_solid.py);
[outstanding issues S2](../issues.md); [clinical source preflight](../clinical-source-preflight.md);
[state validity and coverage](../state-validity-coverage.md).

## Problem

`source_search_complete` arrived from the fixture manifest and nothing checked it. The
matcher's `UNRESOLVED` semantics depend entirely on that flag being truthful, so a caller
could assert a completeness that was never established and receive a confident answer.

Investigating it turned up a sharper defect than the one recorded in S2. Completeness is
not a property of an extract. It is a relation between the extract and the question — and
**relaxation changes the question**. The exemplar's exposure window is seven days; the
priced extension reads back nine. A search bounded by the window the question originally
asked for is genuinely complete for the exact query and silently incomplete for the same
query relaxed:

```
exposure placed 8.0 days before the follow-up
  budget 0: NONE      BUDGET_EXCEEDED, NO_OBSERVED_BINDING
  budget 2: RELAXED   accepted at cost 0.5

the same case with that record never retrieved, because it lies outside the 7-day window
  budget 0: NONE      source_search_complete = True
  budget 2: NONE      source_search_complete = True   <- a negative about an unsearched region
```

The semantic relaxation has the same shape. Admitting `ex:DrugB` reads a concept that a
search executed for `ex:DrugA` never looked for. In both cases a *truthfully* asserted flag
becomes false the moment a budget is applied, and nothing notices.

## Decision

A case carries a **warrant** describing what was searched, and the matcher **derives**
whether that covers what the pattern reads.

```json
"source_search": {
  "basis": "coverage",
  "clock_id": "P1-relative",
  "from_us": -864000000000,
  "to_us": 0,
  "concepts": ["ex:Creatinine", "ex:DrugA", "ex:DrugAChild", "ex:DrugB"]
}
```

| Basis | Meaning | Result |
|---|---|---|
| `coverage` | A declared window and concept scope | Checked against the pattern's demands at the budget in force |
| `stipulated` | An authored fixture says so | Accepted, and `completeness_basis` records that it was stipulated |
| absent | No warrant at all | `ValueError` — a defective case, never a permissive default |

The demands are computed from the pattern and the budget:

- **Temporal.** The deeper of the lab window and the exposure window, with the exposure
  window extended by what the budget can actually reach. The temporal relaxation is priced
  continuously, so a budget buys a specific depth rather than the whole extension, and the
  demand grows with it.
- **Semantic.** Both pattern concepts, plus the reviewed alternative when the budget can
  pay for it.
- **Clock.** The searched clock must be the clock the events are recorded on.

An uncovered demand yields `UNRESOLVED` with `INCOMPLETE_SOURCE_SEARCH`, and the specific
gap is named in `uncovered_demands` — *"2.00 days before the searched window"*, *"concept
ex:DrugB never searched for"* — rather than reported as a bare flag.

## Why stipulation survives

Authored contract fixtures describe constructed data. For them, coverage is a fiction
either way, and a manufactured coverage record would be worse than an honest stipulation:
it would look like evidence. The stipulated basis is therefore kept, and made visible —
`completeness_basis` appears in every outcome, so a reader can tell a checked completeness
from an asserted one without opening the case.

The guarantee is not that every answer rests on verified coverage. It is that **no answer
can quietly claim coverage it does not have.**

## What is *not* claimed

Declaring a window does not establish that the search of that window was exhaustive. The
warrant records a scope and the matcher checks the pattern against it; whether the
retrieval within that scope was faithful is a separate obligation, which the
[source preflight](../clinical-source-preflight.md) addresses for the pinned public demo
and which remains open for any other source. `D-DATA` and `D-CLINICAL` are unchanged by
this decision.

This also does not unify the four matchers. Only the point-anchor oracle derives
completeness; the interval profiles carry their own coverage treatment, and
[T1](../issues.md) remains open.

## Precedent

The project already answers a structurally identical question this way. Under
[state validity and coverage](../state-validity-coverage.md), `HOLDS` is not asserted but
derived from whether positive support covers the queried window, with `UNKNOWN` as the
remaining conflict-free case. The Lean theorem `coverage_iff_positive` proves that
equivalence. Source completeness is the same shape — support against a window — and is now
decided the same way.
