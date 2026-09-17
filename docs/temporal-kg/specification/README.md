# Syntax and semantics: review package

**Status: draft for Robert Hoehndorf's review; no semantic decisions adopted.**

The [specification](syntax-and-semantics.md) provides a structural language and
formal meaning for the temporal examples and extensions. It follows the
organisation of the W3C OWL structural and direct-semantics documents: document
status, notation, grammar, interpretations, satisfaction tables, inference
problems, conformance and informative examples.

## Suggested review order

1. Read Sections 1–2 for scope, primitive points/intervals and identity.
2. Review Sections 4–5 for the OWL-to-temporal execution boundary.
3. Review Sections 7–9 for the quantifiers governing certainty, state completion
   and robust relaxation. These choices directly affect returned answers.
4. Use Section 13 to check the choices against counterexamples.
5. Record acceptance or revisions against R1–R12 in Section 14.
6. Check Section 12 before attributing a capability to an implementation.

## Companion example

[example.ttq](example.ttq) is a complete document in the **proposed** notation.
[example.ofn](example.ofn) supplies the ontology identified by its `OntologyRef`.
It asserts the two class memberships used for event selection. The fixture's
admission policy and evidence IRIs are synthetic declarations. The example is
intended for reading and adapter design; the current command-line tools accept
their existing JSON profiles.

Expected mathematical outcomes:

| Request | Outcome | Reason |
|---|---|---|
| `minuteQuery` | Possible-only | Collection begins 47–49 minutes after administration ends; the allowed gap is 0–48 |
| `reviewedWindows` | Robust match at cost 1.25 | Widening to 50 minutes admits the same binding in every timeline |
| `continuousLow` | Holds | Two adjacent positive assertions cover [0,30 minutes) |

Remove the two `StateAssertion` declarations, retaining the observations, to
obtain an unknown coverage result. The samples at 0 and 30 carry no interval
persistence policy. The second sample is outside the half-open query window.

The companion is a minute-scale arithmetic illustration. Section 13.1 separately
preserves the original strict completion-to-start query with a 48-hour deadline.
The two query identifiers and parameter sets are deliberately separate.

## Review and validation scope

This PR is documentation-only. It leaves executable query contracts unchanged.
Checks cover local links, notation examples and independently calculated boundary
and quantifier cases. Existing tests in PRs #45–#47 provide evidence for their
specific implemented profiles. Parser conformance, full-import OWL reasoning,
clinical admission and combined state/trajectory execution remain separate gates.

Markdown is the authoritative review source. GitHub renders its headings, tables,
code blocks and Unicode mathematics without a documentation build dependency.
