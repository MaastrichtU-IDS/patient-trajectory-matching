# Syntax and semantics: reviewed decisions and revised formalization

**Status: v0.3 draft. Robert Hoehndorf's R1–R12 answers are incorporated.**

The [specification](syntax-and-semantics.md) defines the syntax and meaning of
the temporal examples. The revised [GFO-Time integration contract](gfo-time-and-integration.md)
implements the two substantive review directions: primitive chronoids with
oriented dependent boundaries, and a shared semantics integrating OWL 2 DL
with temporal interpretations. Source-specific policies and conformance proofs
remain explicit obligations in Section 14.

## Read first

The new [Appendix A: Formal semantics in Lean](formal-semantics.md) is the
formal account of the proposal, including OWL satisfaction and combined worlds.

1. [Literature findings and GFO-Time model](gfo-time-and-integration.md#1-literature-findings).
2. [One integrated interpretation](gfo-time-and-integration.md#4-one-integrated-interpretation).
3. [Solver projection obligation](gfo-time-and-integration.md#5-integrated-query-answers-and-solver-projection).
4. Main specification Sections 7–9 for fixed-witness certainty, state completion
   and fixed-option robust relaxation.
5. Section 14 for accepted decisions and implementation/clinical obligations.

GFO-Time has an important distinction: different events and source descriptions
can share one chronoid, while chronoids with the same extremal boundaries are
identical under BT_C A25. Meeting uses distinct right/left boundaries that
coincide. These distinctions preserve identifier-based event and record identity.

## Companion example

[example.ttq](example.ttq) is a complete document in the revised proposed notation.
[example.ofn](example.ofn) supplies its class-support ontology, combined with
the standalone [bridge module](formal/bridge-vocabulary.ofn) under the fixture
admission policy. The source now
declares primitive interval descriptions and owned Left/Right boundary
descriptions. At minute 15, `lowRight` and `nextLowLeft` share a coordinate
variable but describe two different coincident boundary referents.

The admission policy and evidence IRIs are synthetic declarations. Current
command-line tools retain their JSON contracts; this draft does not install a
new application parser or silently change their temporal semantics.

| Request | Expected mathematical outcome | Reason |
|---|---|---|
| `minuteQuery` | Possible-only | Gap ranges over 47–49 minutes; the window is 0–48 |
| `reviewedWindows` | Robust match at cost 1.25 | One fixed widening to 50 minutes works throughout |
| `continuousLow` | Holds | Positive footprints cover [0,30 minutes) on the dense chart |

Remove only the two `StateAssertion` declarations to obtain unknown coverage.
The observations and their boundary contexts remain. The observation at minute
30 lies outside the half-open coverage footprint.

## Formal semantics and checked results

[Appendix A](formal-semantics.md) connects the syntax to three Lean modules:

- [OWLDirectSemantics.lean](formal/OWLDirectSemantics.lean): recursive expression
  interpretation, normalized axiom satisfaction, datatype extensions and
  anonymous-individual models.
- [IntegratedSemantics.lean](formal/IntegratedSemantics.lean): typed BT_C axioms,
  shared-object bridge and conditional solver-projection results.
- [ProposalSemantics.lean](formal/ProposalSemantics.lean): combined worlds,
  source/chart constraints, fixed-witness answers, relaxation and coverage.

Run the checks from the repository root:

```sh
sh docs/temporal-kg/specification/formal/check.sh
python3 docs/temporal-kg/specification/check_examples.py
```

The toolchain pins Lean v4.34.0. All 18 named theorems are checked and their axiom
reports are printed. Dependencies are confined to Lean's standard `propext` and
`Quot.sound` where needed; the files contain no proof placeholders or added global
axioms. GitHub Actions runs all three modules and the documentation/example checker.

The appendix flags the remaining obligations: correspondence of the OWL
transcription/normalization to the standard, concrete datatype maps and decoders,
BT_C model construction, source/query adapter correctness, and model extension
for the numerical solver. Runtime query contracts retain their existing scope.

Markdown defines the review contract and the Lean files give its checked formal
companion. The original clinical example retains its strict 48-hour deadline;
the companion uses the separately named minute-scale variant.
