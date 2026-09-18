# Proposed temporal-precedence scenarios — not adopted, not executable

**These fixtures are illustrative sketches only.** They are not consumed by any
module under `patterns/`, not registered with any JSON Schema, not wired into
`.github/workflows/contracts.yml`, and not asserted by any test file. Nothing
here executes, and nothing here should be treated as a preview of committed
behavior.

They exist to make the "Acceptance scenarios for adoption" table in
[`docs/decisions/temporal-precedence.md`](../../docs/decisions/temporal-precedence.md)
concrete enough that a future adopter has a starting sketch, without
pre-empting the decision itself. The decision document's proposed
`directlyPrecedes`/`isDirectlyPrecededBy` structure remains a candidate for a
future SULO release and executable profile, not current behavior.

## Scenario index

| File | Table row | What it illustrates |
|---|---|---|
| `s1-transitive-not-shortcut.json` | TP-S1 | Transitive closure gives `precedes`, never the `directlyPrecedes` shortcut |
| `s2-strict-precedence-with-gap.json` | TP-S2 | A positive gap satisfies strict precedence regardless of size; direct succession is separate |
| `s3-temporal-contact-not-precedence.json` | TP-S3 | Endpoint equality is contact (`meets`), never strict precedence |
| `s4-start-order-not-whole-interval.json` | TP-S4 | Start-anchor ordering alone does not establish whole-interval precedence |
| `s5-intervening-measurement-changes-adjacency.json` | TP-S5 | Direct succession is scope-relative; widening the scope can break an adjacency |
| `s6-incomplete-scope-no-certainty.json` | TP-S6 | An incomplete scope cannot certify direct succession, only a possible one |
| `s7-cycle-rejected.json` | TP-S7 | A direct-edge cycle is a strict-order violation that validation must catch explicitly |
| `s8-unreconciled-clocks-no-order.json` | TP-S8 | Unreconciled clocks must block comparison, not default to an ordering |

## What remains before any of this could become executable

See "Remaining decisions" in the decision document: the canonical name for
the direct-succession property, the sequence/context representation for a
declared scope `S`, and whether the intended use is strict succession or
zero-gap contact. Each scenario file repeats the subset of these questions
that bears on it, so a reader does not have to cross-reference the decision
document to see what is still open.
