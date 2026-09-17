# Syntax and semantics: reviewed decisions and revised formalization

**Status: v0.2 draft. Robert Hoehndorf's R1–R12 answers are incorporated.**

The [specification](syntax-and-semantics.md) defines the syntax and meaning of
the temporal examples. The revised [GFO-Time integration contract](gfo-time-and-integration.md)
implements the two substantive review directions: primitive chronoids with
oriented dependent boundaries, and a shared semantics integrating OWL 2 DL
with temporal interpretations. Source-specific policies and conformance proofs
remain explicit obligations in Section 14.

## Read first

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

## Checked metatheory

[IntegratedSemantics.lean](formal/IntegratedSemantics.lean) contains the typed
BT_C axiomatic interface, the shared OWL-object bridge and seven named theorems.
The core results make the model-extension condition for solver equivalence
explicit. Full OWL satisfaction is a parameter; this artifact has no claim to
implement a full OWL translator, prove BT_C consistency or discharge the
application adapter's extension obligation.

```sh
cd docs/temporal-kg/specification/formal
lean IntegratedSemantics.lean
```

The local `lean-toolchain` pins Lean v4.34.0. The command prints theorem axiom
dependencies so proof placeholders or unrecorded global postulates are visible.
The conditional results have explicit structure assumptions and no proof holes.

Run the independent documentation/example checks from the repository root:

```sh
python3 docs/temporal-kg/specification/check_examples.py
```

These checks cover links/anchors, example structure/scope/ownership, oriented
contact boundaries, numerical and quantifier examples, and state-completion
cases. GitHub Actions runs both this checker and the Lean file. Neither check
is a claim of integrated clinical-engine conformance.

Markdown remains the authoritative review source; the Lean file gives a checked
companion to the explicitly identified metatheory. The original clinical example
retains its strict 48-hour deadline; the companion uses the separately named
minute-scale variant.
