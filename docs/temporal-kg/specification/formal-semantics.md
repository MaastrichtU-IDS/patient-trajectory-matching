# Appendix A: Formal semantics in Lean

**Version v0.3, 17 September 2026. Proposed normative appendix for review.**

This appendix defines the semantics of the [temporal query proposal](syntax-and-semantics.md)
in Lean. It specifies the admissible combined interpretations, their satisfaction
relation and the meaning of query answers. The
[GFO-Time supplement](gfo-time-and-integration.md) records the literature and
motivates the selected temporal axioms. Sections A.2–A.7 below are proposed
normative definitions; the examples and verification account are informative.

## A.1 Choice of metalanguage and reading order

Lean is the metalanguage. OWL expressions denote predicates on an object or data
domain; GFO chronoids and boundaries inhabit separate types embedded into that
same object domain. One model must satisfy the OWL axioms, temporal axioms,
shared-symbol equations and admitted source constraints simultaneously. Lean's
kernel checks the definitions and the accompanying proofs.

The implementation consists of three files:

| File | Role |
|---|---|
| [OWLDirectSemantics.lean](formal/OWLDirectSemantics.lean) | OWL expression interpretation, normalized axiom satisfaction, datatype extensions and anonymous-individual models |
| [IntegratedSemantics.lean](formal/IntegratedSemantics.lean) | Typed BT_C axioms, exact interpretation bridge and conditional projection theorems |
| [ProposalSemantics.lean](formal/ProposalSemantics.lean) | Combined worlds, chart/source constraints, eligible bindings, query answers, relaxation and state coverage |

The earlier abstract OWL satisfaction parameter remains useful for the general
bridge lemmas. The proposal now instantiates OWL satisfaction through the explicit
recursive definitions in the first file. Mathematical existence of a model is
separate from an algorithm that decides whether one exists.

## A.2 Signature, domains and identity

Fix a well-formed OWL 2 DL axiom closure K, a standard datatype map, a bridge
signature map v and an admitted finite source S. Import resolution, anonymous
name standardization, source selection and OWL 2 DL structural validation take
place before semantic interpretation. The closure includes the selected bridge
module. These inputs remain fixed across all worlds of a query.

A combined world has the following components:

| Symbol | Lean component | Meaning |
|---|---|---|
| ΔO | `World.O` | Nonempty OWL object domain |
| ΔD | `World.Data` | Nonempty OWL data domain |
| I | `World.owl` | OWL interpretation on those domains |
| C, B | `World.C`, `World.B` | Primitive chronoids and dependent boundaries |
| T | `World.time`, `World.btc` | Temporal signature satisfying BT_C |
| jC, jB | `World.bridge` | Injective, disjoint embeddings into ΔO |
| ρC, ρB | `World.chronoid`, `World.boundary` | Denotations of finite temporal descriptions |
| κf | `World.chart` | Exact partial rational chart for each clock f |
| θ | `World.assignment` | Integer coordinates of finite source variables |

The mathematical total universe is a tagged sum: OWL objects and data values
are disjoint. The temporal subdomain of ΔO is the disjoint union of the images
of C and B. Objects outside those images remain available for patients, events
and records. Quantifiers in BT_C range over C or B.

Record identifiers are indices of finite source maps, so different identifiers
remain different records. Description maps ρC and ρB may be many-to-one. Event
records select named OWL individuals through `eventIRI`; admitted aliases may
select the same object. A binding is injective on records. Process identity,
when required by an application, must be established by the admission map.

OWL individual names have their usual interpretation without a unique-name
assumption. Distinctness of actual objects requires the corresponding OWL or
admission condition. Class, property and individual interpretations use separate
functions, preserving OWL punning. Temporal identity is governed by BT_C: A25
identifies chronoids with coincident corresponding endpoints, while oriented
meeting boundaries remain distinct.

The Lean structures quantify over arbitrary domains in `Type`; the collection
of worlds consequently lives in `Type 1`. These are mathematical types, with no
finite enumeration assumption on the semantic domains. Finite `Fin n` types
are used only for source and request identifiers.

## A.3 OWL interpretation and satisfaction

### A.3.1 Semantic normal form

The OWL module uses a semantic abstract syntax. Built-in top and bottom symbols
have dedicated constructors. Named classes, properties and datatypes are
resolved identifiers. Literals are keys for validated lexical-form/datatype
pairs; keys that decode to the same value have the same denotation.

Class and data-range expressions have recursive interpretations `ce` and `dr`.
Object properties use `op`, with inverse interpreted by argument reversal;
data properties use `dp`. Boolean constructors use conjunction, disjunction
and complement in the appropriate domain. Quantified restrictions quantify
over the full domain. These definitions transcribe the
[OWL 2 Direct Semantics](https://www.w3.org/TR/2012/REC-owl2-direct-semantics-20121211/),
Sections 2.2–2.4, into typed predicates.

Cardinality uses an injective family of domain elements:

```lean
def AtLeast {A : Type} (n : Nat) (p : A → Prop) : Prop :=
  ∃ f : Fin n → A, Function.Injective f ∧ ∀ k, p (f k)
def AtMost {A : Type} (n : Nat) (p : A → Prop) : Prop :=
  ¬AtLeast (n + 1) p
```

This definition counts denotations, including unnamed objects. For example,
`minObject n r c` holds at x when at least n distinct r-successors satisfy c.
Exact cardinality is the conjunction of the corresponding minimum and maximum.

The following normalization rules cover the logical forms accepted by standard
OWL 2 DL with unary datatypes. Validate the original OWL ontology against its
global restrictions before applying these semantic rewrites. Restrictions such
as property simplicity belong to the original input; the internal predicate
language can express the truth conditions independently of those restrictions.

| Surface form | Semantic normalization |
|---|---|
| N-ary intersection / union | Fold binary intersection / union |
| Unqualified object / data cardinality | Use top class / top data range as filler |
| Exact cardinality | Minimum and maximum with the same number and filler |
| Subclass | `subClass C D` |
| Equivalent classes | Subclass inclusions in both directions |
| Disjoint classes | Each pair's intersection is a subclass of bottom |
| Disjoint union | Equality with the union, plus pairwise disjointness |
| Subproperty / property chain | `subObject [R] S` / `subObject [R₁,…,Rₙ] S` |
| Equivalent object properties | Both inclusions for each pair |
| Disjoint object properties | `disjointObject` for every pair |
| Object property domain | `someObject R top` is a subclass of the domain |
| Object property range | Top is a subclass of `allObject R C` |
| Inverse properties | Both inclusions between R and the inverse of S |
| Functional / inverse-functional object property | Top has maximum one successor under R / inverse R |
| Reflexive / irreflexive property | `reflexive R` / `irreflexive R` |
| Symmetric / asymmetric property | R is a subproperty of inverse R / disjoint from inverse R |
| Transitive property | `subObject [R,R] R` |
| Sub-, equivalent or disjoint data properties | `subData`, both inclusions, or pairwise `disjointData` |
| Data property domain / range | Existential data restriction implies domain / `dataRange` |
| Functional data property | Top has maximum one data value under the property |
| Datatype definition | `datatypeDefinition` equates the two extensions |
| Key | `key C objectProperties dataProperties` |
| Same / different individuals | Pairwise equality / inequality, preserving argument positions |
| Class assertion | `classAssertion` |
| Positive / negative property assertion | `objectAssertion` or `dataAssertion` with polarity |
| Declaration / annotation | Resolve typing / retain metadata; contribute no logical axiom |

Object and data enumerations, value restrictions, self restrictions and datatype
facets have direct constructors. Standard unary datatypes yield unary data
quantifiers. An extension introducing n-ary arithmetic data ranges requires an
additional semantics and lies outside this normalization contract.

### A.3.2 Datatypes and anonymous individuals

A standard datatype map fixes built-in value spaces, decoded literals and facets.
Its base carrier contains the required values. `DataExtension` embeds these
values injectively into each world's data domain and preserves built-in spaces,
literal denotations and facet membership exactly. Extra data values remain
available. User-defined datatype extensions are interpreted by I and constrained
by datatype-definition axioms. `dr.top` denotes the entire data domain.

The standard map is a mathematical input, with the meanings prescribed by the
selected datatype standard. Its concrete XSD value spaces and lexical decoders
remain to be implemented and verified. The source's integer microseconds arise
through its declared unit/clock admission adapter; interpreting an arbitrary
OWL data literal as a temporal coordinate requires that adapter.

`Satisfies dm I K` requires datatype correctness and satisfaction of every axiom
in K. `Models dm I K` existentially reassigns anonymous individuals while keeping
the named denotations and other interpretation components fixed. In an integrated
world, I stores one satisfying assignment of those anonymous witnesses. Bridge
and source references use named individuals, so that witness choice preserves
their identity. The checked theorem `satisfies_models` establishes the OWL reduct.

Keys retain the distinguished `NAMED` predicate. Both subjects, and common
object-property key values, must belong to it. Its extension may include extra
objects beyond the named-IRI denotations. Cardinalities and ordinary existential
restrictions range over all objects. The `named_key_identifies` theorem checks
the named-witness inference explicitly.

### A.3.3 Status of the OWL transcription

The appendix fixes the normalization rules and the Lean file defines the truth
conditions. A structural-induction argument relates the recursive expression
interpretations to the standard; axiom cases then use the normalization table.
A parser, executable normalizer and machine-checked correspondence proof against
an independently formalized W3C semantics remain conformance obligations.

## A.4 Temporal structure and exact shared interpretation

### A.4.1 Primitive chronoids and boundaries

`TemporalSignature C B` consists of `first,last : C → B`, chronoid parthood and
boundary coincidence. `BTC time` contains the typed A1–A34 transcription from
Baumann, Loebe and Herre's [GFO-Time theory](https://doi.org/10.3233/AO-140136).
The [literature supplement](gfo-time-and-integration.md#1-literature-findings)
gives source locations and the rationale for relativization.

The input vocabulary map v selects four OWL class expressions and four
object-property expressions. `view dm I v` evaluates those expressions using
the OWL definitions above. `Bridge T (view dm I v)` requires:

```text
Chronoidᴵ       = image(jC)
Boundaryᴵ       = image(jB)
LeftBoundaryᴵ   = { jB(first(c)) | c ∈ C }
RightBoundaryᴵ  = { jB(last(c))  | c ∈ C }
hasLeftᴵ(jC(c),jB(b))       ↔ first(c) = b
hasRightᴵ(jC(c),jB(b))      ↔ last(c) = b
coincidesᴵ(jB(a),jB(b))     ↔ coinc(a,b)
temporalPartᴵ(jC(c),jC(d))  ↔ part(c,d).
```

The embeddings are injective and have disjoint images. The relations have the
specified domain and range, making these exact interpretations. The world also
requires disjoint left/right classes, recording published consequence C2
explicitly. No new choice of temporal ontology is hidden in an OWL predicate:
all eight symbols are interpreted through these equations.

### A.4.2 Order, charts and measurements

Define `Earlier(a,b)` when a chronoid has a first boundary coincident with a and
a last boundary coincident with b. This compares coincidence classes. A chart
has a boundary domain and rational coordinates. Its domain is closed under
coincidence and contains intermediate boundary classes between any two of its
members. Inside that domain:

```text
κ(a) = κ(b)  ↔ coinc(a,b)
κ(a) < κ(b)  ↔ Earlier(a,b).
```

The metric profile selects those BT_C structures admitting the required charts.
It adds the calibration needed for subtraction and duration. Comparisons on
unaligned clocks retain the incomparable status defined in the main document.

Named source variables have integer coordinates, embedded exactly into the
rationals by `Rat.ofInt`. The temporal universe retains its unnamed boundaries
and subchronoids. Rational coordinate equality transfers to the mapped OWL
coincidence relation by `equal_coordinates_owl_coincidence`. Boundary identity
still follows from its orientation and the temporal axioms.

## A.5 Source satisfaction and compatible worlds

The finite source normal form retains variables, intervals, boundary ownership,
event handles, scopes and clocks. `Source.WellFormed` enforces one left and one
right description per interval, endpoint scope agreement, event/extent scope
agreement and same-scope difference constraints. Lexical uniqueness, provenance,
clock admission and profile-specific limits follow the main specification.

For each temporal description, a world assigns a referent through ρC or ρB.
Boundary ownership fixes that referent to the selected chronoid's first or last
boundary. The boundary lies in the declared chart, where its coordinate equals
θ of its source variable. θ satisfies every source bound and difference
constraint, and all declared intervals are proper. Optional admitted OWL names
for actual temporal entities denote their j-images. A description record's own
IRI must be kept distinct from the IRI of the temporal entity it describes.

An event's IRI selects its OWL denotation; its source extent selects its temporal
referent. This association is part of the admitted source interpretation. An
adapter that also exposes the association through an OWL property must add and
validate those assertions in K. The bridge vocabulary fixes temporal relations;
the admission contract fixes how the clinical records use them.

Write `World(dm,K,v,S)` for the Lean type of such satisfying structures. Define:

```text
Consistent(dm,K,v,S) ↔ WellFormed(S) ∧ ∃ M : World(dm,K,v,S).
```

The ontology, its standard datatype meanings, source, identifiers and signature
map remain fixed. Object/data carriers, unnamed denotations, temporal referents,
charts and uncertain source coordinates may vary subject to all the conditions.
Quantification over worlds therefore includes the uncertainty left by both
components and their bridge.

An integrated sentence denotes a predicate φ on worlds, using the OWL and
temporal interpretations defined above. `Entails(dm,K,v,S,φ)` holds exactly when
φ holds in every compatible world. This is classical semantic consequence.
Reported query certainty additionally requires consistency, as defined in A.6.2;
the distinction makes inconsistent input a blocked service outcome.

Each combined world has an OWL model as its reduct (`integrated_owl_reduct`).
Consequently, standard OWL-supported class selection remains true in every
combined world (`supported_in_world`). Restricting to compatible worlds can
strengthen integrated consequences. OWL-only class selection remains the
approved eligibility rule; additional integrated consequences require an
explicitly extended selector interface.

## A.6 Query interpretation

### A.6.1 Eligible fixed bindings and atom normalization

For a request Q, `Eligible` requires an injective binding μ from slots to admitted
event records, the requested scope and extent sorts, and OWL-entailment support
for each required class at its named individual. Support quantifies over OWL
models and all data-domain extensions of the fixed standard map. It is
independent of the temporal world used to answer Q.

Resolve the slot landmarks under μ before evaluating any world. Start/end
landmarks select the variables of the corresponding owned boundaries; `At`
selects a boundary variable. Normalize temporal atoms into these five forms:

| Internal atom | Meaning under θ |
|---|---|
| `le x y` | θ(x) ≤ θ(y) |
| `lt x y` | θ(x) < θ(y) |
| `eq x y` | θ(x) = θ(y) |
| `window x y l u` | l ≤ θ(y)−θ(x) ≤ u |
| `overlap s e u v d` | d ≤ min(θ(e),θ(v))−max(θ(s),θ(u)) |

The thirteen Allen operators expand into the corresponding conjunctions in
main Section 6.1. `Gap(i,j,l,u)` becomes `window end(i) start(j) l u`;
`Duration(i,l,u)` becomes `window start(i) end(i) l u`; `Offset` uses its two
resolved landmarks. `MinimumOverlap` uses `overlap`, and `Within(p,i)` becomes
`le start(i) at(p)` together with `lt at(p) end(i)`.

All atoms use the same θ. `Conjunction` means every normalized atom holds.
This definition applies to comparable bindings. The main specification's
incomparable/contradictory-part precedence remains the operational wrapper.

### A.6.2 Possible and certain answers

For the normalized conjunction q of one eligible binding, the Lean definitions
include the consistency guard:

```text
Possible(q) ↔ WellFormed(S) ∧ ∃ M : World(dm,K,v,S), Conjunction(θM,q)
Certain(q)  ↔ Consistent(dm,K,v,S) ∧
               ∀ M : World(dm,K,v,S), Conjunction(θM,q).
```

The checked `certain_possible` theorem establishes that certainty supplies a
possible witness. `inconsistent_never_certain` excludes certainty for an empty
compatible-model set. Invalid input, unsupported checks and timeouts retain
their distinct execution statuses.

Patient certainty fixes μ outside the world quantifier:

```text
CertainPatient(Q) ↔ ∃ μ, Eligible(μ,Q) ∧ Certain(normalize(Q,μ)).
```

The same convention fixes both option and binding for relaxation. `Robust`
requires membership of the admitted finite catalogue, an exact nonnegative
cost within budget, the change-count limit, eligibility and `Certain` for that
candidate's conjunction. `Optimal` selects robust candidates of least cost.
Equal-cost candidates are co-optimal; presentation follows the declared tie
ordering. Catalogue construction must preserve protected conditions and verify
that each changed atom is an allowed widening.

### A.6.3 State completion and coverage

For each exact patient/episode/key/clock group, convert admitted state intervals
to proper half-open rational footprints. Positive and negative support are
unions of those footprints. Observations supply no validity footprint. Check
all groups for conflicts before reporting any coverage result, as required by
the whole-snapshot policy.

Coverage uses the rational coordinate axis as its application truth domain.
This extends the footprint convention to every coordinate in the window,
including unnamed positions. It adds no new primitive to BT_C.

A completion is a predicate `f : Rat → Prop` satisfying positive support and
excluding negative support. `Holds` requires conflict freedom, a proper window
and truth throughout that window in every completion. `Violated` requires
negative support somewhere in the window; `Unknown` is the remaining
conflict-free case. The checked theorem `coverage_iff_positive` proves that
`Holds` is equivalent to positive support covering the window. This proof
quantifies over the rational axis, independently of the finite-cell example
checker. Instantiating f with positive support supplies a completion witnessing
any uncovered point.

## A.7 Relation to the finite solver

Let F(S) be the integer assignments satisfying the finite numerical constraints.
The checked theorem `projection_sound` gives:

```text
M : World(dm,K,v,S)  ⇒  θM ∈ F(S).
```

A solver claiming the integrated answer semantics must additionally establish:

```text
∀ θ ∈ F(S), ∃ M : World(dm,K,v,S), θM = θ.
```

Together with the specified atom normalization and eligible-binding preservation,
this extension property yields equality of the projected world set and F(S).
The earlier `possible_projection` and `certain_projection` theorems then apply.
Every admitted OWL restriction and named temporal equality must survive the
extension. Separate OWL satisfiability and integer-network feasibility alone
leave that obligation open.

## A.8 Distinguishing examples

**Meeting without identity.** Suppose A ends at 15 and B starts at 15 in an exact
common chart. The chart entails coincidence of their two boundaries, and the
bridge transfers this to OWL. The right/left orientation classes entail that
the boundary objects are different. Reusing a numerical variable for the two
boundary descriptions preserves this distinction.

**Shared extent.** Two event identifiers may select the same chronoid. If their
chronoids have coincident corresponding endpoints, BT_C A25 identifies those
chronoids. The event-record identifiers and their provenance remain distinct.

**Incompatible joint interpretation.** The
[three-boundary OWL fixture](formal/three-boundaries.ofn) is consistent with the
standalone OWL projection. Its three pairwise distinct coincident boundaries
contradict BT_C A22 through the exact bridge. The checked
`three_coincident_boundaries_impossible` lemma isolates this conflict. An integer
assignment giving all three a coordinate of 15 leaves that conflict undetected.

**World-dependent witnesses.** Take two worlds and two events, with event e
matching exactly world e. Every world has a matching event; no event matches
both worlds. `fixed_witness_counterexample` checks this finite separation in
Lean. Patient certainty uses one event binding across the worlds.

**Unknown coverage.** Two observations at 0 and 30 give empty positive interval
support. The all-false completion is available on [0,30), so the window is
unknown. Positive footprints [0,15) and [15,30) instead establish `Holds`.

## A.9 Verification and outstanding obligations

Run all three modules with the pinned Lean v4.34.0 toolchain:

```sh
sh docs/temporal-kg/specification/formal/check.sh
python3 docs/temporal-kg/specification/check_examples.py
```

The shell runner builds imports in a temporary directory and removes its build
artifacts. CI runs the same commands. There are **18 named checked theorems**:
seven existing bridge/projection results, three OWL results and eight combined
semantics/coverage results. Axiom reports contain only Lean's standard
`propext` and `Quot.sound` where required; the files have no proof placeholders
or added global axioms. The BT_C fields and datatype-map conditions are explicit
model assumptions. Four additional finite OWL fixtures check name co-denotation,
cardinality including an unnamed successor, the `NAMED` guard on keys and an
explicit negative data assertion.

The following obligations remain flagged for review and operationalization:

| Obligation | Current evidence | Required next evidence |
|---|---|---|
| OWL transcription and normalization | Recursive Lean definitions and complete normalization contract | Independent semantic review; verified parser/normalizer and correspondence proof |
| Standard datatypes | Explicit value-space, literal, facet and extension interface | Concrete supported datatype maps and validated lexical/unit decoders |
| BT_C transcription and realizability | Typed source-axiom mapping and checked consequences | Independent mapping review and constructed compatible models for the admitted profile |
| Source and query compilation | Defined source constraints and atom-lowering contract | Executable adapters with identity, scope, chart and atom-preservation proofs |
| Solver extension | Projection direction checked; extension stated explicitly | Proof or certificate procedure for the selected ontology/bridge profile |
| Runtime completeness | Nonvacuous mathematical answer conditions | Resource limits, timeout propagation and complete catalogue/binding enumeration checks |
| Clinical deployment | Fixed state/landmark and admission contracts | Validated source policies, clocks, state templates and relaxation catalogue |

These obligations extend the accepted-decision register in main Section 14.
The mathematical definitions and checked conditional results are available for
review now; an operational conformance claim requires the evidence in the
third column.
