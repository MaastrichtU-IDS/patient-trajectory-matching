# GFO-Time foundation and the OWL integration contract

**Version v0.2, 17 September 2026.** Implements Robert Hoehndorf's R1–R3
review directions. Sections 2–6 specify the integration used by the main
[syntax and semantics document](syntax-and-semantics.md). Sections 1 and 7
record literature and verification scope.

## 1 Literature findings

The selected foundation is **BT_C**, from Baumann, Loebe and Herre,
[*Axiomatic theories of the ontology of time in GFO*](https://doi.org/10.3233/AO-140136),
*Applied Ontology* 9 (2014), 171–215. The
[author-hosted paper](https://www.onto-med.de/sites/www.onto-med.de/files/files/uploads/Publications/2014/gfo-time.pdf)
was downloaded and read, especially §§5.1, 7 and 8. Its formal vocabulary
comprises chronoids, first/last boundaries, coincidence and temporal parthood.
The following source locations determine the adaptation:

| Source | Consequence for this specification |
|---|---|
| A13–A17 | Chronoids have unique extremal boundaries; boundaries depend on chronoids |
| A18–A22 | Boundary coincidence is an equivalence relation with two members per class |
| C2–C4 | First/last orientation is exclusive; coincident boundaries of the same orientation are identical |
| A25 | Chronoids whose respective endpoints coincide are identical |
| A26 / C5 | One chronoid's endpoints cannot coincide |
| D6 | Meeting uses coincidence of last and first boundaries |
| §8.1 | Coincidence classes have a dense, unbounded linear order |
| §7, footnote 14 | Combining the time theory with other domains requires relativisation |

These facts govern the distinction between event identifiers, temporal descriptions
and their chronoid referents. The metric chart and execution grid below are
application additions.

For semantic integration, we also consulted Flügel, Glauer, Neuhaus and Hastings,
[*When one logic is not enough: Integrating first-order annotations in OWL
ontologies*](https://doi.org/10.3233/SW-243440), 2025, especially §§2–3. It provides
an OWL-to-first-order integration approach using Gavel-OWL and CLIF/TPTP.
The present specification chooses a typed metatheory with a Lean artifact;
Common Logic interchange remains an available serialization direction.

## 2 Primitive time structure

A temporal structure has nonempty chronoid sort C, boundary sort B, functions
`first,last : C → B`, temporal parthood on C and coincidence `≈` on B.
Its temporal universe is the disjoint union `C ⊎ B`. It satisfies the typed
relativisation of BT_C A1–A34; source axiom numbers are attached to fields in
[IntegratedSemantics.lean](formal/IntegratedSemantics.lean).

All time quantifiers range over C or B. Patients, processes, records and OWL data
values inhabit their own domains. This prevents the time theory's domain-closure
axiom from classifying every patient as a temporal entity.

A chronoid is an ontological primitive. Its numerical representation is an
interpretation used for queries. Define:

```text
Meets(c,d)    iff last(c) ≈ first(d)
SameTime(c,d) iff first(c) ≈ first(d) and last(c) ≈ last(d).
```

In BT_C, `SameTime(c,d)` entails `c=d`. Different events can therefore share one
chronoid. They retain distinct identifiers. Different interval-description
records can likewise refer to one chronoid; their description identifiers retain
their provenance. This is the agreed meaning of “intervals coinciding” in the
strict BT_C foundation. Allowing distinct *chronoids* at exactly the same extent
would require changing A25 and naming a different theory.

Meeting uses two different oriented boundaries: the right boundary of the first
chronoid and the left boundary of the second. Their coincidence expresses the
absence of a temporal gap. The two boundaries' identity remains distinct even
under an exact chart assigning both the same coordinate.

A `Boundary` declaration in the query language describes an oriented boundary
of a declared interval. Every admitted point-like occurrence or observation
MUST identify this boundary context, or have a reviewed admission mapping that
supplies it. A bare timestamp alone leaves that mapping unresolved.

## 3 Metric charts and finite descriptions

Let D be the ordered set of boundary coincidence classes. The base temporal
structure is qualitative. A **metric chart** for a declared comparison frame
assigns classes in its domain to rational coordinates in microseconds. The chart
is order-preserving and reflects equality of classes. Consequently, within one
exact chart, boundaries coincide iff their coordinates agree, although their
orientations can differ. Duration and gap use subtraction in the chart.

The chart is an additional assumption. Its existence, frame calibration and
units belong to the declared metric profile. GFO-Time alone determines order
and coincidence, without selecting elapsed-time units or a calibrated rate.
For this profile, chart domains contain the named entities and the temporal
regions needed by a request. Comparisons across frames require an admitted
alignment. Equal printed timestamps in different frames leave alignment open.

`GFOBoundaryMicrosecond` restricts the coordinates of **named admitted boundary
descriptions** to integer microseconds. Unnamed boundaries and subchronoids
remain available between them in the dense temporal structure. For example,
a named interval from 0 to 1 microsecond can contain an unnamed subchronoid
from 1/3 to 2/3 microseconds. A finite integer network represents observations
about that structure; it is not the complete temporal universe.

An exact chart must also distinguish exact equality from rounded measurements.
A source label rounded to a microsecond requires an uncertainty policy; the
adapter MUST NOT promote the printed equality to exact boundary coincidence.
Policies can supply appropriate grid bounds or require a denser metric profile.
The named-coordinate lattice is an explicit execution assumption. The same
bounds interpreted over all rational coordinates admit additional timelines;
the integer solver's strict-inequality compilation does not establish answers
for that different profile.

A finite description map assigns each interval handle to C and each boundary
handle to B. These maps may be many-to-one. They preserve side, ownership and
chart coordinates. Reusing a source coordinate variable for a right and a left
boundary records coincidence while retaining two boundary referents.

**Realisation illustration.** A doubled rational line represents each chart
position r by `(r,Right)` and `(r,Left)`. Coincidence forgets the side. A chronoid
between s<t has first boundary `(s,Left)` and last boundary `(t,Right)`.
Temporal parthood uses inclusion of the represented extents. This supplies
intuition for extending finite endpoint observations into a dense model; a
particular OWL mapping must still satisfy the bridge and extension conditions
below. The Lean artifact axiomatizes BT_C rather than constructing this model.

## 4 One integrated interpretation

### 4.1 Components and shared signature

Fix an OWL 2 DL ontology K under [Direct Semantics](https://www.w3.org/TR/owl2-direct-semantics/),
a BT_C temporal structure T, the admitted source S and a reviewed signature map.
An integrated interpretation is

```text
M = (I, T, jC, jB, rho, kappa),
```

where I is an OWL interpretation of the extended signature; `jC:C→ΔI` and
`jB:B→ΔI` are injective embeddings with disjoint images; rho interprets finite
event/description handles and source variables; and kappa supplies the declared metric charts.

The standalone [bridge vocabulary](formal/bridge-vocabulary.ofn) gives concrete
OWL IRIs to `Chronoid`, `Boundary`, `LeftBoundary`, `RightBoundary`, `hasLeft`,
`hasRight`, `coincides` and `temporalPart`, all under
`urn:temporal-trajectory:bridge#`. It declares the class partition, endpoint
typing/cardinality/dependence and selected relation characteristics using OWL
2 DL axioms. BT_C supplies the stronger temporal theory through the bridge.
For the companion fixture, the admission policy selects `example.ofn` together
with this module as its local ontology context, without network imports.

Other signature maps bind these interface symbols to explicit OWL class/property
expressions and must meet the same interpretation conditions. In the historical
standalone core, `TimeInterval`, `TimePoint`, `hasBeginning` and `hasEnd` supply
four candidate bindings. Its old axioms alone leave additional BT_C obligations.
The SULO map is separately versioned; application modules retain their class-only
policy. Symbol resolution MUST use explicit IRIs and expressions.

### 4.2 Bridge conditions

Write `C^I` and `R^I` for class and object-property interpretation in I. The
following equations constrain the **same** temporal entities and OWL objects:

| Interface | Required interpretation |
|---|---|
| `Chronoid^I` | image(jC) |
| `Boundary^I` | image(jB) |
| `LeftBoundary^I` | {jB(first(c)) : c∈C} |
| `RightBoundary^I` | {jB(last(c)) : c∈C} |
| `hasLeft^I(jC(c),jB(b))` | iff first(c)=b |
| `hasRight^I(jC(c),jB(b))` | iff last(c)=b |
| `coincides^I(jB(a),jB(b))` | iff a≈b |
| `temporalPart^I(jC(c),jC(d))` | iff c is a temporal part of d |

The mapped relations have the indicated domain/range typing. Left and right
boundary classes are disjoint, reflecting BT_C consequence C2. For each admitted
actual temporal individual, the signature/admission map fixes its denotation to
the appropriate j-image. Description records instead refer to those individuals;
the records retain their own identities. Event identity comes from the admitted
identifier map, with equality conflicts handled by admission, never by comparing
temporal coordinates.

The OWL data domain and its datatype interpretation retain their Direct
Semantics. Numerical source descriptors are decoded through a declared datatype,
unit and clock adapter to kappa/rho constraints. Object identity is kept separate
from numerical-value equality. The current Lean file exposes the object bridge;
full datatype-map formalization remains an implementation obligation.

### 4.3 Satisfaction and entailment

An integrated model satisfies all of the following simultaneously:

```text
I satisfies K under OWL 2 Direct Semantics;
T satisfies the typed BT_C theory;
(jC,jB,rho,kappa) satisfy the signature, identity and chart bridges;
rho and kappa satisfy the admitted source bounds, constraints and declarations.
```

Let `Models(K,S)` denote these compatible models. Consistency requires this set
to be nonempty. For an integrated sentence phi, define

```text
(K,S) entails_int phi iff every M in Models(K,S) satisfies phi.
```

This is a semantic integration, not simply a pair of independent reasoner
results. Both components can be individually consistent while their bridge is
inconsistent. For example, three explicitly different OWL boundary individuals
asserted mutually coincident contradict BT_C A22 once their interpretations are
connected. Merging a last boundary with the next first boundary contradicts C2,
although assigning them equal coordinates is correct.

The reduct of each compatible model satisfies K. Thus OWL entailments remain
true in compatible models. The converse extension property is an additional
obligation: extra temporal axioms can remove OWL models and strengthen integrated
consequences. No general conservative-extension or decidability claim is made
for arbitrary OWL plus BT_C plus arbitrary bridges.

### 4.4 Semantic selectors and execution order

The approved service still uses `K entails C(entity(e))` for semantic selection.
A class supported only by additional integrated inference is outside that
selector contract until the interface is widened. Computation can therefore
remain one-way: checked semantic support, then temporal query evaluation.
Semantic coupling is expressed by the bridge; it does not require runtime
feedback from every numerical result into the OWL graph.

## 5 Integrated query answers and solver projection

For a fixed supported binding mu, define possible/certain answers over compatible
models, with temporal atoms interpreted through their charts. The nonempty-model
precondition blocks vacuous certainty:

```text
Possible(K,S,Q,mu) iff some M in Models(K,S) satisfies Q[mu]
Certain(K,S,Q,mu)  iff every M in Models(K,S) satisfies Q[mu].
```

Let W_int be the projection of Models(K,S) onto the coordinates of the named
variables. Let W_STN be the integer assignments admitted by the finite difference
constraints. An STN implementation may claim integrated answer preservation only
for a profile establishing all of:

1. **Projection soundness:** each compatible model projects into W_STN.
2. **Extension:** each assignment in W_STN extends to a compatible model,
   retaining admitted identities, endpoint orientation and support.
3. **Atom preservation:** query truth depends on those projected coordinates
   exactly as the declared operator table specifies.
4. **Binding preservation:** the same eligible named bindings are enumerated.

These conditions give `W_int = W_STN`, hence preserve both existential possibility
and universal fixed-binding certainty. The Lean file proves the projection
statements with assumptions 1–2 explicit and a coordinate predicate representing
condition 3. It does not supply the extension proof for the repository's adapters.

Legacy microsecond engines establish answers in W_STN. An integrated profile must
supply the extra admission/bridge certificate or report its integration check as
unsupported. Standalone OWL consistency and an STN feasibility check alone are
insufficient evidence of this certificate.

## 6 State coverage and oriented boundaries

For the current coverage service, the metric chart maps an admitted chronoid
into the half-open **query footprint** `[start,end)` on its rational coordinate
axis. This is a coverage convention applied after the ontological interpretation.
It is not a set-theoretic definition of the chronoid itself.

At contact, the closing boundary of one chronoid and the opening boundary of
the next remain different objects. A state-specific question about a right-sided
versus left-sided boundary requires an oriented-boundary state predicate. The
current throughout-window coverage operator instead uses the declared footprint
convention, which assigns the contact coordinate to the following footprint.

Positive and negative footprints determine the partial truth function and its
completions. The truth domain is the dense chart axis, including between named
grid coordinates. Unions of finitely many exact intervals compute coverage there.
Duration is the sum of endpoint differences in a fixed calibrated chart, rather
than the cardinality of its dense set of positions. Uncovered regions remain
unknown; point observation records supply no interval persistence.

## 7 Verification scope and remaining obligations

Run the checked artifact with the pinned toolchain:

```sh
cd docs/temporal-kg/specification/formal
lean IntegratedSemantics.lean
```

Lean v4.34.0 checks seven named theorems and one distinct-event example. The
`#print axioms` commands report kernel dependencies; the checked theorems use no
custom global axioms or proof placeholders. Their structure parameters are
explicit hypotheses, including the BTC axioms and the intended OWL satisfaction
predicate. This is a checked conditional metatheory, not a mechanized full OWL
semantics or a proof that arbitrary input ontologies have compatible models.

The BT_C field-to-axiom mapping is a reviewed transcription; machine-checking
that transcription against a separately formalized published theory, building
an infinite model, formalizing full OWL datatypes and proving the application
adapter's extension property remain separate obligations. The C2-based boundary
lemma explicitly assumes disjoint left/right OWL classes rather than claiming
to reproduce the paper's derivation of C2.

The bridge module and companion example passed the OWL 2 DL profile check
with ROBOT v1.9.5. A second fixture, [three-boundaries.ofn](formal/three-boundaries.ofn),
was merged with the bridge module and remained consistent under HermiT. The
Lean theorem `three_coincident_boundaries_impossible` proves that adding the
BT_C bridge eliminates its compatible models. This makes the integration
boundary directly reviewable: the OWL component accepts the fixture, while
the joint theory rejects it.

To reproduce these OWL-side checks with ROBOT v1.9.5 from the repository root:

```sh
java -jar "$ROBOT_JAR" merge \
  --input docs/temporal-kg/specification/formal/bridge-vocabulary.ofn \
  --input docs/temporal-kg/specification/example.ofn \
  validate-profile --profile DL --output /tmp/temporal-bridge-profile.txt
java -jar "$ROBOT_JAR" merge \
  --input docs/temporal-kg/specification/formal/bridge-vocabulary.ofn \
  --input docs/temporal-kg/specification/formal/three-boundaries.ofn \
  reason --reasoner HermiT --output /tmp/temporal-three-boundaries.owl
```

`ROBOT_JAR` denotes the locally installed release JAR. These optional Java checks
are separate from the Python/Lean CI job. Full import/mapping conformance still
requires the chosen deployment ontologies and the joint extension proof.

The main specification records the user's accepted decisions separately from
these proof and deployment obligations. Ongoing and disconnected regions remain
outside the request profile, consistently with R12.
