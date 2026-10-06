# Temporal trajectory queries: structural specification and semantics

**Project review draft, 17 September 2026**

**Version:** v0.3

**Review status:** R1–R12 answered by Robert Hoehndorf; revised GFO-Time and
semantic-integration text remains a draft for review.

**Specification identifier:** `temporal-trajectory-semantics-0.3`

## Abstract

This document specifies a finite temporal query language for recorded patient
trajectories. It takes GFO-Time chronoids as primitive intervals with dependent,
oriented boundaries that can coincide. It defines temporal constraints,
ontology-supported event selection, possible and certain answers,
finite costed relaxation, and coverage by explicit state assertions. A
functional-style notation describes the structure independently of the JSON and
RDF representations used by individual application profiles. The semantics
integrates OWL 2 DL interpretations and GFO-Time models through explicit shared
interpretation maps. A finite metric execution profile supports query evaluation
under a stated model-extension obligation.

The running examples concern administration and specimen collection, baseline
measurements around treatment, and a state asserted over thirty minutes. Every
answer is relative to a named, admitted snapshot and its declared time profile.

## Status of this document

This revision implements the reviewer’s R1–R12 answers. R1 additionally requires
semantic integration and R3 replaces the original discrete-time foundation with
GFO-Time. The revised formalization remains a draft for inspection; accepted
design choices and completed implementation proofs have separate status. The
words MUST, MUST NOT, SHOULD and MAY below express requirements **conditional on
adoption of this draft**. Here MUST expresses a requirement, MUST NOT a
prohibition, SHOULD a recommendation with documented exceptions, and MAY an
option. These meanings are defined locally for this document.

Sections 2–11 and Sections A.2–A.7 of the separate
[formal-semantics appendix](formal-semantics.md) are proposed normative material.
The abstract, this status section,
Sections 1 and 12–15, and all paragraphs labelled **Example** are informative.
Accepted decisions and remaining operational obligations are collected in
Section 14 and referenced at their point of use.
A profile can implement a declared subset without implementing the whole language.

The organisation follows the distinction between structure and interpretation
used in the W3C [OWL 2 Structural Specification][owl-syntax] and
[OWL 2 Direct Semantics][owl-semantics]. This document is an independent project
publication. Its requirements and temporal operators belong to this project.

The baseline inspected for this draft is upstream commit `511fdce`, together
with the proposed implementations in [PR #45][pr45], [PR #46][pr46] and
[PR #47][pr47]. Those PRs retain their own review status. This draft adds no
runtime syntax parser or service endpoint. The
[formal-semantics appendix](formal-semantics.md) defines the combined semantics in
Lean, extending the [GFO-Time integration contract](gfo-time-and-integration.md).
Section 12 identifies the executable subsets and proposed integrations.

## Table of contents

1. [Purpose and example](#1-purpose-and-example)
2. [Preliminary definitions](#2-preliminary-definitions)
3. [Structural specification and syntax](#3-structural-specification-and-syntax)
4. [OWL representation and semantic support](#4-owl-representation-and-semantic-support)
5. [Temporal interpretations](#5-temporal-interpretations)
6. [Interpretation of temporal expressions](#6-interpretation-of-temporal-expressions)
7. [Trajectory queries and answers](#7-trajectory-queries-and-answers)
8. [State validity and coverage](#8-state-validity-and-coverage)
9. [Controlled relaxation](#9-controlled-relaxation)
10. [Results, evidence and conformance](#10-results-evidence-and-conformance)
11. [Compilation obligations](#11-compilation-obligations)
12. [Implementation correspondence](#12-implementation-correspondence)
13. [Worked examples and distinguishing cases](#13-worked-examples-and-distinguishing-cases)
14. [Accepted decisions and remaining obligations](#14-accepted-decisions-and-remaining-obligations)
15. [References and change history](#15-references-and-change-history)

[Appendix A: Formal semantics in Lean](formal-semantics.md)

## 1 Purpose and example

A trajectory query first identifies eligible named events, then evaluates their
temporal relationships. Consider an antibiotic administration A and a blood
specimen collection B for one patient and episode. The original example asks
for B to begin strictly after A finishes, with a gap of at most 48 hours.
The landmarks, strict lower boundary, patient participation, and event types
are all part of the query meaning.

Three related questions require additional definitions:

1. Does one interval contain another, last long enough, or overlap another for
   a specified duration?
2. Does a permitted wider window make one named match certain despite uncertain
   source times, and what is the least listed cost of doing so?
3. Does explicit state evidence cover an entire window, refute the requested
   state somewhere in it, or leave a gap in knowledge?

This document provides one structural vocabulary for these questions. Its
execution profiles preserve the distinction between an event's occurrence,
a point observation, a state assertion's validity, and evidence availability.
The last of these belongs to snapshot admission, before the query semantics.

## 2 Preliminary definitions

### 2.1 Names, sorts and values

An **IRI** is an absolute IRI in angle brackets. A **Name** is a nonempty identifier
matching `[A-Za-z_][A-Za-z0-9_-]*`. Names in the notation are local handles;
IRIs identify ontology entities, evidence resources or policies. A **String**
is a JSON string, including its escaping conventions. An **Integer** is an
optional minus sign followed by decimal digits. Leading zeros are immaterial
to its mathematical value. A **Cost** is a nonnegative finite decimal numeral,
interpreted exactly as a rational number; exponent notation is excluded.

The notation distinguishes the following sorts:

| Sort | Meaning | Example |
|---|---|---|
| Coordinate variable | An uncertain numerical coordinate | `x_end` |
| Boundary | An oriented dependent boundary of a primitive interval | `administrationEnd` |
| Interval description | An identified description referring to a primitive chronoid | `administrationExtent` |
| Event handle | An admitted record-level witness associated with an ontology individual | `administration1` |
| State assertion | An explicit polarity and validity interval for a state key | `low1` |
| Observation | A recorded state-related point observation | `sample1` |
| Patient / episode / clock handle | An identifier fixing the comparison scope | `P`, `E`, `c` |

These sorts are structural. OWL classes have their ordinary Direct Semantics.
A structural sort check is an input validation operation.

### 2.2 GFO-Time foundation and metric execution profile

The ontological time structure satisfies **BT_C**, the chronoid/boundary theory
in Baumann, Loebe and Herre (2014), §§7–8. Chronoids are primitive intervals.
Their left and right extremal boundaries are dependent temporal entities of
exclusive orientations. Meeting chronoids have distinct but coincident right
and left boundaries. Coincidence classes are densely ordered. The normative
integration and its literature basis are given in the
[GFO-Time contract](gfo-time-and-integration.md).

The execution profile `GFOBoundaryMicrosecond` uses a rational metric chart whose
named boundary coordinates are integers in microseconds. Input bounds and metric
parameters are integers in [−2⁶³, 2⁶³−1]; intermediate arithmetic is exact.
Unnamed subchronoids and boundaries remain available between named coordinates.
The integer grid constrains the admitted description, not the entire time domain.

A chronoid's endpoints have ordered chart coordinates s<e. Its duration is e−s.
The coverage operator uses the half-open coordinate footprint `[s,e)` on the
dense chart axis. This footprint is a query convention, separate from the
primitive chronoid and its two oriented boundaries. A point-like occurrence
uses a boundary extent; a zero-duration chronoid is invalid.

Charts add metric information to the qualitative foundation. Clock calibration,
precision and alignment remain admission obligations. Equal rounded timestamps
need an uncertainty policy before they can establish exact coincidence.
**Accepted R3; source-specific chart policies remain to be validated.**

### 2.3 Identity and structural equivalence

Declarations are finite maps keyed by their identifiers. Repeated identifiers
across source declarations are invalid. Request identifiers are unique within
the document; slot and condition identifiers are unique within their query.
A relaxation reference MUST resolve to a trajectory query in the same document. Each reference MUST resolve to a
declaration of the required sort. Sequence order has meaning only in grammar
argument positions; the ordering of declarations, slots, constraints, state
assertions and catalogue options is otherwise immaterial to their truth conditions.
A repeated constraint identifier is invalid even if its contents agree.

Distinct event and record identifiers retain their identity when their extents
coincide. Equality conflicts involving OWL individuals are resolved by the
admission policy or block the mapping. The specification adds no global unique
name assumption to OWL.

Temporal description identifiers denote distinct description records. Their
maps to chronoids or boundaries can be many-to-one: two events or descriptions
may select the same chronoid. In BT_C, coincidence of both pairs of extremal
boundaries entails chronoid identity (A25). A right boundary and a left boundary
can coincide while remaining distinct; coincident boundaries of the same
orientation are identical (C3–C4). These consequences apply to temporal referents,
while their source descriptions preserve identifier-based provenance.
**Accepted R2, interpreted with the GFO-Time foundation required by R3.**

## 3 Structural specification and syntax

### 3.1 Notation

The productions below define the complete proposed functional-style syntax.
Quoted text is a terminal, `::=` defines a production, `|` separates alternatives,
`*` means zero or more repetitions, and `+` means one or more. Parentheses inside
quoted terminals are literal. Whitespace separates adjacent lexical values.
The lexical categories are defined in Section 2.1. Comments and abbreviated IRIs
are outside this syntax. Examples containing comments are explanatory layouts.

A document contains one snapshot and zero or more requests:

```ebnf
Document ::= 'TemporalDocument(' Snapshot Request* ')'
Snapshot ::= 'Snapshot(' Name 'GFOBoundaryMicrosecond'
               'OntologyRef(' IRI ')'
               'AdmissionPolicy(' IRI ')'
               Declaration* ')'
Declaration ::= Clock | Variable | Boundary | Interval | Event
              | Difference | StateAssertion | Observation
Clock ::= 'Clock(' Name String ClockScope ')'
ClockScope ::= 'Global' | 'Patient(' Name ')'
Variable ::= 'Variable(' Name Scope Integer Integer IRI ')'
Scope ::= 'Scope(' Name Name Name ')'
Interval ::= 'Interval(' Name ')'
Boundary ::= 'Boundary(' Name Side Name Name ')'
Side ::= 'Left' | 'Right'
Event ::= 'Event(' Name IRI Name Name Name IRI ')'
Difference ::= 'Difference(' Name Name Name Integer IRI ')'
Polarity ::= 'Positive' | 'Negative'
StateAssertion ::= 'StateAssertion(' Name Name Name Name IRI Polarity Name IRI ')'
Observation ::= 'Observation(' Name Name Name Name IRI Polarity Name IRI ')'
Request ::= TrajectoryQuery | CoverageQuery | Relaxation
```

### 3.2 Source structures

| Construct | Ordered arguments | Meaning |
|---|---|---|
| `Clock` | clock, origin, scope | A declared microsecond coordinate system |
| `Scope` | patient, episode, clock | Scope of one coordinate variable |
| `Variable` | variable, scope, lower, upper, evidence | Inclusive coordinate bounds |
| `Interval` | interval-description | Reference to a primitive proper chronoid |
| `Boundary` | boundary-description, side, interval-description, variable | Dependent oriented boundary and its coordinate |
| `Event` | handle, entity-IRI, patient, episode, extent, evidence | Eligible event with boundary or interval extent |
| `Difference` | constraint, left-variable, right-variable, upper, evidence | Left minus right is at most upper |
| `StateAssertion` | assertion, patient, episode, clock, state-IRI, polarity, interval, evidence | State-validity assertion |
| `Observation` | observation, patient, episode, clock, state-IRI, polarity, boundary, evidence | Point observation record with explicit boundary context |

Bounds MUST satisfy lower ≤ upper. Each interval description MUST have exactly
one Left and one Right boundary declaration. Their variables MUST have the
same scope. An event’s patient and episode MUST agree with its extent’s scope.
The declared clock MUST permit that patient. Difference constraints MUST relate
variables in the same patient, episode and clock scope. State and observation
scope arguments MUST agree with the referenced temporal entity.

Boundary descriptions may share a variable. A right and left boundary sharing
one variable remain distinct referents while being coincident. Same-side
descriptions at an exact common position denote one boundary referent. Repeated
references to one variable preserve that variable’s identity and all its constraints. Admission MUST preserve source
correlations that affect the query. Independent copies of a shared variable
would define a different snapshot.

A snapshot is an immutable selected view. The admission policy MUST identify
source selection, accepted assertions, ontology and mapping versions, clock
conversion and identity decisions. Historical requests MUST declare whether
they use the ontology/mappings available at the cutoff or an explicitly
identified retrospective version. `OntologyRef` resolves to a pinned import
closure and associated semantic-support contract. An identifier alone records
a declaration of policy; verification evidence is a separate requirement of
the admission profile. **Accepted decisions R1, R2, R6 and R7.**

### 3.3 Query structures

```ebnf
TrajectoryQuery ::= 'TrajectoryQuery(' Name Name Name Slots Conditions ')'
Slots ::= 'Slots(' Slot+ ')'
Slot ::= 'Slot(' Name ExtentKind IRI ')'
ExtentKind ::= 'BoundaryExtent' | 'IntervalExtent'
Conditions ::= 'Conditions(' Condition+ ')'
Condition ::= 'Condition(' Name Atom ')'
Atom ::= AllenAtom | Gap | Duration | MinimumOverlap | Offset | Within
AllenAtom ::= AllenName '(' Name Name ')'
AllenName ::= 'Before' | 'Meets' | 'Overlaps' | 'Starts' | 'During'
            | 'Finishes' | 'SameTime' | 'After' | 'MetBy'
            | 'OverlappedBy' | 'StartedBy' | 'Contains' | 'FinishedBy'
Gap ::= 'Gap(' Name Name Integer Integer ')'
Duration ::= 'Duration(' Name Integer Integer ')'
MinimumOverlap ::= 'MinimumOverlap(' Name Name Integer ')'
Offset ::= 'Offset(' Landmark Landmark Integer Integer ')'
Landmark ::= 'Start(' Name ')' | 'End(' Name ')' | 'At(' Name ')'
Within ::= 'Within(' Name Name ')'
CoverageQuery ::= 'CoverageQuery(' Name Name Name Name IRI Integer Integer ')'
```

A trajectory query gives its query identifier, patient and episode, followed by
slots and a conjunction of named conditions. A slot gives its identifier,
extent kind and required class IRI. Its binding is an eligible event handle.
Allen, gap and overlap atoms require two interval slots. Duration requires one
interval slot. Start and end landmarks require an interval slot; an at landmark
requires a boundary slot. Within takes a boundary slot followed by an interval slot.
Different slots MUST bind different event handles.

A coverage query gives its identifier, patient, episode, clock, state IRI,
window beginning and window end. It asks for validity throughout that proper
exact window. A state IRI acts as an exact-match predicate key under this profile.
Its biological meaning is established by the admission policy.

All referenced slots MUST be declared. Binary interval atoms MUST reference
different slots. Duration bounds MUST satisfy 1 ≤ lower ≤ upper. Gap and offset
bounds MUST satisfy lower ≤ upper. Minimum overlap MUST be at least 1.
An empty conjunction is outside this surface grammar; specialised adapters may
use it internally for candidate selection under a separately specified contract.

### 3.4 Relaxation structures

```ebnf
Relaxation ::= 'Relaxation(' Name Name Cost Integer
                 'Relaxable(' Name* ')' Option* ')'
Option ::= 'Option(' Name Cost Change+ ')'
Change ::= 'Widen(' Name Integer Integer ')'
```

A relaxation gives its identifier, a referenced trajectory query identifier,
maximum cost, maximum number of changed conditions, relaxable condition
identifiers, and a finite catalogue of complete options. A change replaces the
lower and upper bounds of one named gap or offset condition. Each option has
one fixed cost independent of the source timeline and binding.

Each change MUST widen the original bounds: lower′ ≤ lower, upper′ ≥ upper,
and at least one comparison MUST be strict. Each changed condition MUST appear
in `Relaxable`. A condition may occur at most once in an option. Every other
query component is protected. The original query is an implicit option named
`original`, at cost zero, with no changes. Explicit options MUST have distinct
names different from `original`. The changed-condition budget is nonnegative.

Options specify complete alternatives. Their implicit composition is excluded
from this catalogue semantics. The complete catalogue MUST be validated before
budget filtering. **Accepted decision R10.**

## 4 OWL representation and semantic support

### 4.1 Ontology interpretations

Let K be the pinned OWL 2 DL ontology closure associated with the snapshot.
An OWL interpretation I and the satisfaction relation `I ⊨ K` have the meaning
given by [OWL 2 Direct Semantics][owl-semantics]. This document preserves that
meaning. `K ⊨ α` means that every OWL model of K satisfies the ground axiom α.
A request consuming OWL semantic support requires an established consistency
result for the admitted ontology fragment. An inconsistent ontology blocks semantic query answers.
An unsupported or unfinished consistency check produces an execution status.

A named event handle e has an associated ontology individual `entity(e)`.
For an OWL-supported selector C, `Supported(C,e)` holds exactly when
`K ⊨ ClassAssertion(C entity(e))`. A conforming restricted profile MUST identify
its supported ontology fragment and provide sound and complete support checks
within that fragment. A profile using a built-in class table MUST identify that
table and its closure rules as its selection contract.

Required patient participation and other semantic conditions belong to event
admission or to an explicitly declared support template. In the administration
example, admission supplies the named patient-role/bearer path and the process
typing evidence. The two identifiers in a numerical scope record alone provide
scope agreement; clinical participation requires its declared evidence mapping.

### 4.2 Primitive chronoids and dependent boundaries

The foundation has disjoint chronoid and boundary sorts, unique first/last
boundary functions, temporal parthood and boundary coincidence. Its temporal
universe is relativised to those sorts. Patients, event records and OWL data
values remain in their appropriate domains.

The new [standalone bridge module](formal/bridge-vocabulary.ofn) supplies a
concrete OWL 2 DL projection of the interface. The historical
[standalone v2 core][core] supplies additional candidate class and endpoint-role
mappings for comparison. The [SULO interface][sulo-interface] remains a separately
versioned representation. Full BT_C, including density and A25, is specified in
the metatheory; OWL 2 DL serialization and profile checking continue to apply
to the OWL component. New bridge maps must explicitly identify every class,
property and description/occurrence interpretation. **Accepted R6.**

A finite named description is a partial view of a temporal model. Boundary
existence in that model extends beyond named declarations. Conversely, the
finite query interface requires named, unambiguous support for each selected
extent; its structural checks remain explicit admission operations.

### 4.3 Integrated semantics with separate execution stages

The [integration contract, §§4–5](gfo-time-and-integration.md#4-one-integrated-interpretation),
defines one compatible interpretation `M=(I,T,jC,jB,rho,kappa)`. I satisfies the
OWL ontology K; T satisfies the relativised BT_C theory. The embeddings jC and
jB identify temporal entities with objects in I. The map rho interprets admitted
identifiers, and kappa supplies order-faithful metric charts. Shared class and
property interpretations obey explicit bridge equations.

Let `Models(K,S)` be the interpretations satisfying K, BT_C, the bridges and
admitted source S together. This set must be nonempty before integrated answers
are returned. Independent consistency of the OWL component and of the numerical
network is necessary but does not establish compatibility of their shared objects.

Semantic selection remains the approved OWL entailment test against K. The
execution stages remain one-way: establish supported named bindings, then evaluate
temporal conditions. Their soundness and completeness for integrated semantics
require the model-extension and projection properties in Section 11.4.
**Accepted R1 with the requested integration; accepted R8 for execution order.**

[IntegratedSemantics.lean](formal/IntegratedSemantics.lean) gives a checked typed
metatheory for BT_C, the object bridge, boundary-identity consequences and the
conditional projection theorem. Full OWL satisfaction is an explicit semantic
parameter. The artifact is not an OWL parser or a complete mechanized Direct
Semantics; those implementation obligations remain named in Section 14.

### 4.4 State descriptions

A state assertion record and its described clinical content are distinct objects.
The state-description RDF profile represents the record as an information object.
The coverage semantics in Section 8 evaluates the admitted polarity and validity
region. A negative state assertion refutes its exact state key over its region;
it is not an OWL negative object-property assertion or an automatic complement
of every other state class. A domain-specific OWL situation translation requires
a separately reviewed template. **Accepted decision R5.**

## 5 Temporal interpretations

### 5.1 Temporal models and coordinate projections

A compatible model interprets each interval description i as a chronoid
`rhoC(i)` and each boundary description p as an oriented boundary `rhoB(p)`.
For its declared chart kappa and variable map var, the observed assignment obeys
`θ(var(p)) = kappa([rhoB(p)])`, where brackets denote coincidence class.
Define `timeθ(p)=θ(var(p))`. For an interval’s declared left and right
descriptions b(i), e(i), define:

```text
startθ(i) = θ(var(b(i)))
endθ(i)   = θ(var(e(i)))
footprintθ(i) = [startθ(i), endθ(i))
```

The footprint is a set on the dense chart axis. The referent is a primitive
chronoid. Under BT_C and the exact chart, coincident endpoint pairs select the
same chronoid even if their description identifiers differ.

### 5.2 Source constraints and compatible assignments

Let X be the finite named coordinate variables. The finite network has candidate
assignments `θ:X→ℤ` satisfying all admitted numerical constraints Gamma:

| Source structure | Satisfaction condition |
|---|---|
| Variable x with bounds l,u | l ≤ θ(x) ≤ u |
| Difference x,y,k | θ(x) − θ(y) ≤ k |
| Interval description i | startθ(i) < endθ(i) |

Call their set `W_STN(S)`. Every admitted variable and constraint participates,
including shared variables outside the eventual binding. The integrated set
`W(S)` consists of projections of `Models(K,S)` onto X. In general only
`W(S) ⊆ W_STN(S)` is guaranteed. Equality requires the extension proof for the
admitted ontology/bridge/description profile in Section 11.4.

All possibility and certainty definitions below use W(S). An existing engine
that has verified only the finite numerical profile MUST label its results with
that profile; integrated conformance requires the additional bridge certificate.
An empty W_STN blocks the source immediately. A nonempty W_STN with an empty
compatible model set also blocks integrated answers. An unfinished compatibility
check yields unsupported or incomplete execution, not vacuous certainty.

Independent scope components may be solved separately only when the profile
proves that the bridges and ontology preserve the claimed decomposition.

### 5.3 Comparability

A temporal atom is comparable when its required landmarks use one declared
clock after admission. Comparability is fixed for a binding and independent of θ.
A comparison on different unaligned clocks is undefined.

For a conjunction, let its comparable part contain all its comparable atoms.
If that part is infeasible with Γ, the binding is impossible. Otherwise, any
undefined atom gives the binding status `INCOMPARABLE`. This precedence permits
an already contradictory comparable condition to refute a conjunction, while
preserving clock uncertainty in every other case.

## 6 Interpretation of temporal expressions

The qualitative foundation defines meeting by coincidence of a chronoid’s last
boundary and another’s first boundary. The metric tables below are its expression
in an exact order-faithful chart. Same-side coordinate equality uses the same
boundary referent; meeting compares different oriented boundary referents.

### 6.1 Basic interval relations

For intervals i and j in one clock, abbreviate their endpoints under θ as
`s = startθ(i)`, `e = endθ(i)`, `u = startθ(j)`, `v = endθ(j)`. Each row defines the
truth condition of its expression. The conditions presuppose s < e and u < v.

| Expression | Truth condition |
|---|---|
| `Before(i j)` | e < u |
| `Meets(i j)` | e = u |
| `Overlaps(i j)` | s < u < e < v |
| `Starts(i j)` | s = u and e < v |
| `During(i j)` | u < s and e < v |
| `Finishes(i j)` | u < s and e = v |
| `SameTime(i j)` | s = u and e = v |
| `After(i j)` | v < s |
| `MetBy(i j)` | v = s |
| `OverlappedBy(i j)` | u < s < v < e |
| `StartedBy(i j)` | s = u and v < e |
| `Contains(i j)` | s < u and v < e |
| `FinishedBy(i j)` | s < u and e = v |

These are the thirteen basic Allen relations, named for Allen’s
[interval-reasoning formalism][allen]. `SameTime` compares the extents of the bound events. In BT_C the two
chronoid referents are identical when both corresponding boundaries coincide;
the event identifiers remain distinct. The legacy JSON spelling is `equals`. The table defines the query operator independently of any asserted
OWL property bearing a similar name. Using asserted qualitative facts as source
constraints requires a declared and validated compiler bridge.

### 6.2 Metric expressions and point membership

For a slot binding, interval terms denote the bound event's interval extent and
boundary terms denote its boundary extent. Landmark expressions select the corresponding
coordinate. For landmarks a and b, `Offset(a b l u)` measures b minus a.

| Expression | Truth condition |
|---|---|
| `Gap(i j l u)` | l ≤ startθ(j) − endθ(i) ≤ u |
| `Duration(i l u)` | l ≤ endθ(i) − startθ(i) ≤ u |
| `MinimumOverlap(i j d)` | min(endθ(i),endθ(j)) − max(startθ(i),startθ(j)) ≥ d |
| `Offset(a b l u)` | l ≤ valueθ(b) − valueθ(a) ≤ u |
| `Within(p i)` | startθ(i) ≤ timeθ(p) < endθ(i), using p’s chart position |

Gap bounds are signed. An overlapping collection can therefore have a negative
completion-to-start gap. `Gap(i j 0 u)` includes contact at completion.
A strictly positive gap in this time profile requires lower bound 1, or an
additional `Before` condition. The historical 48-hour query uses the strict
lower boundary. **Accepted decision R9.**

Minimum overlap requires d > 0. This avoids assigning the zero-threshold meaning
from a signed endpoint difference to conventional nonnegative overlap length.
A future zero-threshold operator would need an explicit separate convention.

### 6.3 Conjunction

For a comparable binding μ and assignment θ, write `θ ⊨ Q[μ]` iff every condition
in Q is true under the tables above. Every atom is evaluated under the **same**
assignment. Feasibility of individual atoms under different assignments is
insufficient to establish feasibility of their conjunction.

## 7 Trajectory queries and answers

### 7.1 Eligible bindings

For snapshot S and query Q, let `B(S,Q)` be the finite set of assignments μ from
query slots to admitted event handles satisfying these conditions:

1. The event's patient and episode equal the query's patient and episode.
2. The event extent has the requested boundary or interval sort.
3. The required class is supported under Section 4.1.
4. The binding is injective on event handles.
5. Every required admission/support check completed under the declared profile.

Selection is independent of which feasible timeline will be used. Requiring
injectivity on handles is the current record-level distinctness convention.
A process-identity distinctness convention requires reviewed canonicalisation.
**Accepted decision R2.**

### 7.2 Binding results

Assume a consistent admitted snapshot and a fully comparable eligible binding μ.
Define:

```text
Possible(S,Q,μ)  iff  ∃θ ∈ W(S) : θ ⊨ Q[μ]
Certain(S,Q,μ)   iff  ∀θ ∈ W(S) : θ ⊨ Q[μ]
```

The exclusive reported statuses are:

| Status | Condition |
|---|---|
| `CERTAIN` | Certain(S,Q,μ) |
| `POSSIBLE` | Possible(S,Q,μ) and not Certain(S,Q,μ) |
| `IMPOSSIBLE` | not Possible(S,Q,μ) |

`POSSIBLE` in this table means possible-only. A result field called
`possible_patient_ids` includes both certain and possible-only matches.
Incomparable bindings follow the precedence in Section 5.3.

### 7.3 Patient projection and fixed witnesses

For a patient p, possibility holds when an eligible binding for p is possible.
Fixed-witness certainty holds when an eligible binding for p is certain:

```text
PossiblePatient(p) iff ∃μ ∈ Bp(S,Q) ∃θ ∈ W(S) : θ ⊨ Q[μ]
CertainPatient(p)  iff ∃μ ∈ Bp(S,Q) ∀θ ∈ W(S) : θ ⊨ Q[μ]
```

Here `Bp` restricts bindings to p and the requested episode, or to the explicit
union of episodes requested by a cohort service. The query interface MUST state
which episode scopes were enumerated.

A different property, `∀θ ∃μ : θ ⊨ Q[μ]`, allows the witness to vary with time.
This draft chooses fixed-witness certainty. Section 13.4 gives a case separating
the two properties. **Accepted decision R4.**

A complete empty match set describes eligible represented records. Missing
support, incomparable clocks and incomplete enumeration MUST remain identifiable
in the result evidence. Source completeness is a separate admission claim.

### 7.4 Mixed baseline and follow-up queries

A mixed-profile adapter selects an interval treatment, a baseline point and,
for follow-up inspection, a distinct follow-up point. Item, unit and exact scalar
filters belong to its declared selection predicate. A baseline-before-start
window [l,u] translates to
`Offset(At(baseline) Start(treatment) l u)`.
A follow-up window [l,u] anchored at treatment end translates to
`Offset(End(treatment) At(followup) l u)`.
Optional within-treatment membership adds `Within(followup treatment)`.

Eligibility is evaluated on the treatment/baseline binding. Follow-up inspection
classifies the conjunction of eligibility and follow-up conditions against the
original source timelines. A patient remains eligible when no follow-up is
selected. The adapter MUST retain this distinction in its results and preserve
the source when inspecting a follow-up. Response values are reported attributes
of selected records. **Accepted decisions R8 and R9.**

## 8 State validity and coverage

### 8.1 Exact state-assertion profile

This section defines exact coverage over the declared half-open chart footprints.
Boundary orientation belongs to the underlying ontology; a question about the
state specifically on a left or right boundary requires a separate predicate. Coordinates used by state assertions and
observations MUST have singleton declared bounds. The coverage query window
W = [a,b) is proper and exact. State keys, patients, episodes and clocks match
exactly. A point observation contributes evidence at its named point and contributes
no interval support to the coverage operator.

For one patient, episode, state key and clock, let P be the union of validity
regions of positive state assertions and N the corresponding union for negative
assertions. Duplicate or overlapping support contributes once to the union.
Adjacent regions [a,b) and [b,c) have union [a,c).

Coverage consumes the exact state keys and polarities in the admitted view;
class entailment is required only if an admission template explicitly uses it.

The source has a state conflict when `P ∩ N ≠ ∅` for any such scope, including
outside the requested window. This profile blocks every coverage request against that supplied state
snapshot on conflict. A trajectory request consuming only event evidence has
its own temporal and semantic-support gates. This is a validation policy, separate from OWL ontology
consistency. **Accepted decision R5.**

Any state assertion or observation for the queried patient, episode and state
on a different unaligned clock makes the coverage query `INCOMPARABLE` in this
profile. Whole-source conflict detection precedes this comparability check.

### 8.2 Partial truth and completion semantics

Let D be the dense coordinate domain of the admitted chart. For a
conflict-free, comparable scope, define the partial state function h by
`h(t)=true` when t ∈ P, `h(t)=false` when t ∈ N, and `h(t)=unknown` otherwise.
Let H be the set of all total functions `f : D → {true,false}` extending h.
A statement that the state holds throughout W has the following semantics:

| Coverage result | Set condition | Equivalent completion condition |
|---|---|---|
| `HOLDS` | W ⊆ P | Every f ∈ H is true at every t ∈ W |
| `VIOLATED` | W ∩ N ≠ ∅ | Every f ∈ H is false at some t ∈ W |
| `UNKNOWN` | W ∩ N = ∅ and W ∖ P ≠ ∅ | Some completions satisfy throughout-W and some refute it |

This equivalence assumes unrestricted completion of unknown coordinates after
admission. Ontology-derived temporal rules or dynamics constraints would change
H and require an extended profile. **Accepted decisions R5 and R8.**

Explicit refutation anywhere suffices for `VIOLATED`, even when some other
coordinates remain unknown. Unknown coverage is epistemic incompleteness in
state assertions; possible-only trajectory matching concerns uncertainty in
coordinates. The two classifications MUST retain separate result labels.

### 8.3 Coverage measures and evidence

For a finite union U of bounded half-open chart regions, let `length(U)` be
the sum of endpoint differences in its disjoint maximal interval decomposition.
This measures chart duration; the dense set of positions has no finite
cardinality interpretation. Report:

```text
positive region = W ∩ P
negative region = W ∩ N
unknown region  = W ∖ (P ∪ N)
```

The three lengths sum to `b−a`. The longest supported duration is the maximum
length of a maximal interval in `W ∩ P`, or zero when that set is empty.
Continuous positive support holds exactly when the result is `HOLDS`.

A conforming evidence result MUST retain the assertion identifiers supporting
or refuting each segment. Merging adjacent covered segments for length
calculation MUST preserve the underlying evidence segmentation or an equivalent
replayable mapping. Monitoring completeness is a distinct property; the union
of state assertions establishes state-knowledge coverage in the admitted view.

## 9 Controlled relaxation

### 9.1 Admissible catalogue

Let D be the finite catalogue including the original option, c(δ) the fixed
cost of option δ, and n(δ) its number of changed conditions. For cost budget B
and changed-condition budget M, define

`D(B,M) = {δ ∈ D | c(δ) ≤ B and n(δ) ≤ M}`.

Write Qδ for the complete query obtained from option δ. Every source variable,
source constraint, admission decision, semantic selector, clock mapping and
protected condition is retained. Widening only metric windows leaves the
eligible binding set unchanged.

### 9.2 Robust feasibility and optimum

A patient p has a robust catalogue match iff

```text
∃δ ∈ D(B,M) ∃μ ∈ Bp(S,Q) ∀θ ∈ W(S) : θ ⊨ Qδ[μ].
```

Both δ and μ are selected before θ varies. The robust optimum is

```text
min { c(δ) | δ ∈ D(B,M), μ ∈ Bp(S,Q), Certain(S,Qδ,μ) }.
```

An empty set has no certified robust match in the catalogue. A report MUST
preserve any incomparable evaluations or execution failures that qualify that
outcome. Possible-only matches MAY be reported separately under the analogous
existential timeline condition. They MUST NOT be labelled robust.

The cost is exact and independent of the world. The stated optimum ranges over
the listed options and enumerated records. Increasing either budget weakly
expands the admissible set. For complete evaluations, robust membership is
therefore monotone in either budget.

### 9.3 Completion and ties

If any in-budget evaluation is blocked or incomplete, the catalogue result MUST
be marked incomplete and MUST NOT claim a certified optimum. Retained partial
evaluations are diagnostic evidence.

Equal-cost alternatives are semantically co-optimal. A service choosing one
representative MUST publish a deterministic ordering and identify the selected
option and binding. PR #46 orders by exact cost, option identifier, canonical
serialized binding and episode identifier. That ordering is an implementation
presentation rule; an approved interoperable serialization/tie convention
remains an **implementation obligation under accepted R10**.

## 10 Results, evidence and conformance

### 10.1 Result categories

Implementations MUST distinguish the following categories, even where their
serialized labels differ:

| Category | Examples | Interpretation |
|---|---|---|
| Invalid input | Ill-typed atom, missing reference, reversed bounds | Outside the admitted syntax/profile |
| Blocked source | Empty feasible time set, inconsistent required ontology, state conflict | Preconditions for query answers failed |
| Unsupported / incomplete execution | Unsupported axiom, limit, timeout, failed dependency | Answer completeness unavailable |
| Incomparable | Required unaligned clock comparison | Temporal truth unavailable for that comparison |
| Binding answer | Certain, possible-only, impossible | Section 7 predicates for one admitted binding |
| State answer | Holds, violated, unknown | Section 8 partial-state interpretation |
| Catalogue answer | Certified robust matches and optimum, or none certified | Section 9 over the declared finite search |

Requests in a document have separate results. Shared malformed declarations or
a temporally inconsistent source block all dependent requests. Semantic-support
and state-conflict gates apply to the request families specified in Sections 4
and 8.

An invalid request MAY be rejected before constructing a result object. A
conforming service MUST make that rejection distinguishable from a successful
empty answer. A blocked underlying evaluation MUST remain visible through
wrappers and aggregators.

### 10.2 Evidence and reproducibility

A result MUST identify its snapshot, query, selected ontology/mapping version,
admission policy, clock policy, numerical profile and implementation artifacts.
It MUST identify the scope of candidate enumeration and whether that enumeration
completed. A hash identifies content under a declared serialization; it does
not itself verify source truth or clinical acceptance.

For supported temporal bindings, evidence SHOULD include:

- a feasible assignment for a possible answer;
- source entailment paths for a certain answer;
- a source-feasible counterexample for a possible-only answer;
- an inconsistent constraint cycle for an impossible conjunction;
- the event, endpoint and semantic-support witnesses used by the binding.

State results MUST include positive, negative and unknown regions and their
source assertions. Relaxation results MUST identify option costs, budget
exclusions, changed conditions and the underlying binding evidence.

### 10.3 Conformance claims

A conformance statement MUST name the syntax/profile version, admitted ontology
fragment, supported request forms, admission assumptions and completeness scope.
Full OWL 2 DL representation conformance is checked on the chosen import closure.
Correct execution of temporal arithmetic is a separate conformance obligation.

A service MAY implement only interval queries, only exact coverage, or another
explicit subset. It MUST reject unsupported constructs before returning a
complete answer. Claimed parser conformance requires accepting the syntax in
Section 3; JSON-only adapters currently claim their own profile syntax instead.

## 11 Compilation obligations

### 11.1 Difference constraints

A difference edge `(x,y,k)` denotes x − y ≤ k. Variable bounds compile to
`x−0 ≤ upper` and `0−x ≤ −lower`, where the distinguished zero variable has
value zero. A proper interval compiles to `start−end ≤ −1`.
On the integer grid, x < y is equivalent to x − y ≤ −1; equality compiles to
the two opposite non-strict inequalities. The tables in Section 6 therefore
compile to conjunctions of difference constraints.

For positive d, minimum overlap has the useful equivalent form

```text
start(i) − end(i) ≤ −d    start(i) − end(j) ≤ −d
start(j) − end(i) ≤ −d    start(j) − end(j) ≤ −d.
```

This follows because `min(end(i),end(j))−max(start(i),start(j)) ≥ d` precisely
requires each end to be at least d after each start. It permits a longer interval
to contain a shorter interval, provided their shared duration reaches d.

### 11.2 Possibility and certainty

For the finite numerical profile, possibility is satisfiability of `Γ ∧ Cμ`,
where Cμ is the compiled query conjunction. For integrated semantics this test
is justified when the profile meets Section 11.4’s extension obligations.
Certainty holds exactly when Γ entails every edge of Cμ. For an edge x − y ≤ k,
its integer negation is y − x ≤ −k−1. Testing this negated edge against Γ gives
a source-feasible counterexample when the edge is unentailed.

Within a profile certified to have W(S)=W_STN(S), certainty MUST use Γ as its
premise. Replacing Γ by `Γ ∧ Cμ` would condition the
source on the queried answer and yield an invalid certainty test.

### 11.3 Preservation requirements

A compiler or batching scheme MUST preserve, for its declared query fragment:

1. admitted named bindings and their support;
2. the feasible projections of the original source timelines onto queried variables;
3. atom truth and clock comparability on every retained binding;
4. complete enumeration, or a separately proved aggregation rule;
5. replayable provenance for each result.

With these conditions, possibility and fixed-witness certainty are preserved
because both quantify over the same bindings and feasible assignments.
For exact state coverage, aggregation additionally MUST preserve the union of
positive and negative regions and all conflicts. Pair coverage for measurement
batches alone supplies no proof of this interval-union property.

### 11.4 Integrated model-extension obligation

The STN algorithm is a complete evaluator of its finite integer constraints.
Its use as an evaluator of this GFO/OWL specification additionally requires:

1. Every compatible model projects to a feasible network assignment.
2. Every feasible assignment extends to a compatible OWL/BT_C model while
   preserving admitted identities, oriented boundaries and semantic support.
3. Query atoms and eligible named bindings agree under that projection.

The [Lean artifact](formal/IntegratedSemantics.lean) proves preservation of
possible and certain answers under these assumptions. The particular adapter
extension proof remains outstanding. A right/left boundary merge and three
different coincident boundary referents are counterexamples to an unrestricted
extension claim; the file also checks their relevant impossibility lemmas.

## 12 Implementation correspondence

This table is an implementation inventory. Acceptance of R1–R12 fixes the
design directions; it does not certify the existing engines against the new
GFO/OWL bridge.
The new notation is a review language; links below identify the executable JSON
schemas and algorithms from which particular subsets were derived.

| Draft component | Existing or proposed implementation | Coverage and boundary |
|---|---|---|
| Finite source timelines and binding certainty | [Bounded temporal profile][bounded] | Integer microseconds, shared constraints, certificates, built-in event classes |
| OWL support gate | [Semantic support][semantic-support] | Restricted checked rule module; full OWL import reasoning is a separate gate |
| All Allen relations, duration, overlap | [PR #45][pr45] | Interval slots and built-in selectors; no parser for this document's notation |
| Mixed point/interval offsets and membership | [Mixed queries][mixed] | Specific baseline/follow-up shapes, scalar/item/unit filters and explicit clock alignment |
| Finite robust relaxation | [PR #46][pr46] | Existing bounded gap windows or mixed baseline windows; at most 16 explicit options; general `Offset` rewriting remains proposed |
| Exact state coverage | [PR #47][pr47] | Separate exact state source, maximum 256 records; point observations supply no interval persistence |
| Historical OWL point/interval distinction | [Standalone v2 core][core] | Earlier local ontology; GFO-oriented boundary mapping and full BT_C conformance remain additional obligations |
| GFO/OWL integration metatheory | [Lean artifact](formal/IntegratedSemantics.lean) | Typed BTC assumptions, object bridge and checked conditional preservation/identity lemmas; full application extension proof remains open |
| One document combining all request kinds | Section 3 proposal | Unified parser, adapters and composed service conformance await implementation |
| State-constrained trajectory query | Future extension | Coverage is currently a separate request, with no state atom inside `Conditions` |

The revised source notation declares primitive interval descriptions and
left/right boundary descriptions with explicit ownership. Bounded
JSON events refer directly to endpoint variables; their adapter supplies the
structural extent mapping. State JSON assertions store exact coordinates; their
adapter must supply singleton variables and reviewed interval/boundary descriptions.
These transformations require a reviewed identity and provenance mapping.
A legacy shared-endpoint fixture that identifies one object as both a last and
first boundary requires two coincident oriented referents for GFO conformance.
The current numeric engines can reuse its coordinate variable, but their old
object mapping is not thereby certified.

The bounded selector table and the OWL support gate are alternative declared
profiles. Implementing the former does not claim the latter's reasoning scope.
The GFO foundation is dense. `GFOBoundaryMicrosecond` is a restriction on
selected named coordinates and has its own strict-order compilation rule.
Finer/rational-coordinate execution requires another numerical solver profile.

## 13 Worked examples and distinguishing cases

### 13.1 Administration followed by collection

**Example.** With one microsecond as the unit, 48 hours is 172800000000 units.
Assume that admission supports the two named classes and patient participation.
The query is:

```text
TrajectoryQuery(administrationCollection P E
  Slots(
    Slot(a IntervalExtent <https://example.org/temporal-kg/v2#AntibioticAdministration>)
    Slot(b IntervalExtent <https://example.org/temporal-kg/v2#BloodSpecimenCollection>)
  )
  Conditions(
    Condition(completionGap Gap(a b 1 172800000000))
  )
)
```

Exact administration end at hour 1 and collection start at hour 25 give a
24-hour gap and a certain match. If the collection start ranges from hour 48
to hour 50, the gap ranges from 47 to 49 hours. The same named binding is
possible-only. A collection beginning exactly at completion violates the
strict lower boundary; a gap of exactly 48 hours satisfies the upper boundary.
These are timing calculations conditional on admission of the event meanings.

### 13.2 Containment and minimum overlap

**Example.** Let A=[0,10), B's start be in [3,4], and B's end be in [6,7], in
microseconds. Every feasible timeline satisfies `Contains(A B)` and
`MinimumOverlap(A B 2)`. A's duration is exactly 10. The shortest feasible
B=[4,6) has shared duration 2, so threshold 3 is possible-only.
This is the synthetic geometry of PR #45.

### 13.3 Costed robust widening

**Example.** To reproduce the scaled fixture in PR #46, use minutes: A ends at
minute 1, B begins at minutes 48–50, and the original gap window is [0,48]
minutes. In microseconds the bounds are [0,2880000000]. An option with cost 1.25
widens the upper bound to 3000000000 (50 minutes).

```text
Relaxation(reviewedWindows minuteQuery 2.00 1
  Relaxable(completionGap)
  Option(widen 1.25 Widen(completionGap 0 3000000000))
)
```

The [companion source](example.ttq) defines `minuteQuery` with gap
[0,2880000000] and supplies its source snapshot. Section 13.1 uses the separate
hour-scale query identifier `administrationCollection`.
The original binding is possible-only and the widened binding is certain.
A budget below 1.25 excludes this option. The hour/minute scaling and inclusive
zero boundary are explicit differences between the original clinical example
and the implementation fixture. A clinical catalogue still requires the validation
and approval recorded as deployment obligations under R9/R10.

### 13.4 Witness and modification quantifier order

**Example: witness choice.** A1 ends at 1, A2 ends at 2, and B begins at either
1 or 2. Every timeline has an administration meeting B. Each fixed
administration meets B in only some timelines. Patient membership under
`∀θ ∃μ` is true; fixed-witness certainty under `∃μ ∀θ` is false.

**Example: modification choice.** A gap ranges over {0,1,2}. The original query
requires [1,1]. The catalogue permits either [0,1] or [1,2]. In every timeline
some option works, but neither fixed option works in every timeline. The
catalogue therefore has no robust match. Combining options by a world-dependent
choice would change the semantics.

### 13.5 Joint feasibility

**Example.** Source variables x and y each range over {0,1}. Conditions x < y and
y < x are each possible separately. Their conjunction is impossible. A
per-condition feasibility test followed by Boolean conjunction is unsound.

### 13.6 Thirty-minute state window

**Example.** Let W=[0,30 minutes). Convert minutes to microseconds before input.
The state key is `urn:state:low`; its clinical interpretation remains an admission
choice. The following snapshots have different semantics:

| Admitted evidence | Partial state on W | Result |
|---|---|---|
| Positive intervals [0,15), [15,30) | True throughout | `HOLDS` |
| Positive observations at 0 and 30 | Unknown throughout for coverage | `UNKNOWN` |
| Positive [0,10), negative [10,20), positive [20,30) | Explicit false middle | `VIOLATED` |
| Positive [0,10), [20,30) | Unknown middle | `UNKNOWN` |
| Positive [0,30), negative [10,20) | Conflicting support | Blocked |

An observation at 30 lies outside W under the half-open convention. A negative
assertion starting at 30 has no intersection with W. A normal-valued measurement
becomes a negative low-state assertion only through an approved admission or
inference rule. PR #47 supplies these five example snapshots.

### 13.7 Identity, unknown support and source inconsistency

**Example.** The right boundary of [0,15) and the left boundary of [15,30)
are different coincident objects. Two events with the same interval footprint
retain different event identifiers while selecting the same BT_C chronoid.
Identifying the right boundary with the left boundary violates the new bridge.

**Example.** If a completed class-entailment check establishes that K fails to
entail the required membership, the event is absent from that slot’s eligible
bindings. Entailed support requires the membership in every model of K.
An unsupported or unfinished entailment operation instead yields an incomplete
execution status.

**Example.** If one source says x ≤ 0 and another admitted constraint says x ≥ 1,
then W(S) is empty. The result is blocked, including when the query itself would
be vacuously true under universal quantification over an empty set.

## 14 Accepted decisions and remaining obligations

Robert Hoehndorf answered R1–R12 on 17 September 2026. This table records those
answers. Implementation proofs, source policies and clinical validation remain
separate obligations; their absence does not reopen the accepted design choice.
The GFO-specific formalization of R1/R3 is the substantive revision for inspection.

| ID | Review disposition | Rule applied in v0.3 | Outstanding implementation or deployment evidence | Earlier item |
|---|---|---|---|---|
| R1 | Accepted with integration required | OWL 2 DL and temporal evaluators share one compatible-model semantics; Lean defines OWL satisfaction, combined worlds and answer semantics | OWL correspondence proof, concrete datatype maps and adapter extension proof | Q1 |
| R2 | Accepted: identity by identifiers | Event/record identifiers remain distinct at the same time; temporal descriptions can share a BT_C referent | Canonicalisation, aliases and OWL equality-conflict cases | Q2 |
| R3 | Revised by reviewer: GFO-Time | Primitive chronoids, dependent oriented boundaries and coincidence; finite microsecond coordinates are an execution projection | Frame calibration, precision policies, oriented-boundary admission and chart/adapter proofs | Q3 |
| R4 | Accepted | One fixed binding across compatible source models | Preserve witness identity in every adapter | Q8 |
| R5 | Accepted | Explicit positive/negative interval support; unknown gaps; whole-snapshot coverage conflict blocking | Reviewed state templates, acceptance and any sample-persistence rules | Q5 |
| R6 | Accepted | Standalone and SULO representations remain separate with explicit mappings | Pinned full-closure DL checks and mapping evidence | Q6 |
| R7 | Accepted | Immutable selected snapshots; explicit historical versus retrospective mapping mode | Acceptance, revision, cutoff and historical-policy tests | Q7 |
| R8 | Accepted | One-way entailed support then temporal execution; coverage is a separate request | Unified parser/adapters and any later composition or feedback proof | Q8 |
| R9 | Accepted | Strict administration-completion to collection-start gap, at most 48 hours | Clinical validation and source landmark interpretation | Q9 |
| R10 | Accepted | Finite fixed-cost alternatives; fixed option and binding; explicit budgets/protected conditions; deterministic representative among co-optima | Approved clinical catalogue and published tie serialization | Q10 |
| R11 | Accepted | Profile-specific limits; explicit incomplete status; partial evidence is diagnostic | Workload/latency limits, timeout tests, completeness and batching proofs | Q11 |
| R12 | Accepted | Boundary extents and bounded proper chronoids; ongoing/disconnected/recurrent extents deferred | Separate profiles if required | Q4 |

**R5 interpretation:** the answer “yes” accepts the proposed whole-snapshot
conflict rule; it is not taken as a request for the alternative query-local rule.
No numerical service limits were supplied for R11; profiles must publish theirs.
R3 establishes the literature-based foundation, while the new formalization and
its conformance obligations remain visible for the draft PR review.

## 15 References and change history

### 15.1 Normative dependencies

- [OWL 2 Web Ontology Language: Structural Specification and Functional-Style
  Syntax, Second Edition][owl-syntax]. W3C Recommendation, 11 December 2012.
  Used for OWL representation and global OWL 2 DL restrictions.
- [OWL 2 Web Ontology Language: Direct Semantics, Second Edition][owl-semantics].
  W3C Recommendation, 11 December 2012. Used for OWL interpretation and entailment.

- Baumann, Loebe and Herre. [Axiomatic theories of the ontology of time in GFO][gfo-time],
  2014, §§7–8, BT_C. Relativised to temporal entities under the
  [integration contract](gfo-time-and-integration.md).

The grammar and application metric semantics in this document are original project
specification text. The W3C documents supply the OWL dependency and structural
presentation model.

### 15.2 Informative references

- James F. Allen. [Maintaining knowledge about temporal intervals][allen].
  *Communications of the ACM* 26(11), 832–843, 1983.
- [Revised temporal KG definition v2][v2] and its [review response][v2-response].
- [Standalone OWL validation package][owl-validation].
- [SULO interface and conformance scope][sulo-interface].
- [Bounded temporal profile][bounded], [semantic support][semantic-support], and
  [mixed record queries][mixed].
- Implementation proposals: [interval operators][pr45], [robust relaxation][pr46],
  [state validity and coverage][pr47].
- [Companion functional-style example](example.ttq) and
  [review guide](README.md).

### 15.3 Change history

- **v0.3, 17 September 2026:** Add the normative formal-semantics appendix and
  explicit Lean definitions for OWL interpretation/satisfaction, datatype-domain
  extensions, combined worlds, chart/source constraints, eligibility, guarded
  certainty, robust relaxation and dense-axis state coverage. Add eleven checked
  results to the seven bridge lemmas. Normalizer/adapter correctness and concrete
  datatype instantiation remain separately flagged obligations.

- **v0.2, 17 September 2026:** Apply R1–R12 review answers. Replace the discrete
  foundation with GFO-Time chronoids and oriented dependent boundaries; distinguish
  description/event identifiers from temporal referents; add shared-model OWL/GFO
  semantics, literature rationale and checked Lean conditional theorems. Retain
  the microsecond grid as a declared finite observation profile.

- **v0.1, 17 September 2026:** Initial review draft. Defines proposed functional
  syntax, typed finite structures, separated OWL/temporal semantics, exact state
  completion semantics, robust catalogue quantifiers and implementation boundaries.

[owl-syntax]: https://www.w3.org/TR/2012/REC-owl2-syntax-20121211/
[owl-semantics]: https://www.w3.org/TR/2012/REC-owl2-direct-semantics-20121211/
[core]: ../ontology/temporal-core.ofn
[owl-validation]: ../validation/README.md
[sulo-interface]: ../sulo-interface.md
[v2]: ../Temporal_Knowledge_Graph_Formal_Definition_v2.pdf
[v2-response]: ../revision-response.md
[bounded]: ../../bounded-temporal-uncertainty.md
[semantic-support]: ../../semantic-support.md
[mixed]: ../../mixed-record-query.md
[pr45]: https://github.com/MaastrichtU-IDS/patient-trajectory-matching/pull/45
[pr46]: https://github.com/MaastrichtU-IDS/patient-trajectory-matching/pull/46
[pr47]: https://github.com/MaastrichtU-IDS/patient-trajectory-matching/pull/47

[allen]: https://doi.org/10.1145/182.358434

[gfo-time]: https://www.onto-med.de/sites/www.onto-med.de/files/files/uploads/Publications/2014/gfo-time.pdf
