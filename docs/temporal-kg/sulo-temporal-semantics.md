# Temporal semantics for SULO's time classes and relations

Status: proposed formalisation for review, 5 November 2026. It gives the temporal vocabulary of the pinned SULO 0.2.14 core a formal interpretation in the BT_C time structure used by the [syntax and semantics specification](specification/syntax-and-semantics.md) and its [GFO-Time contract](specification/gfo-time-and-integration.md). It adds no axiom to SULO and no property to the application profiles. Where it adopts a meaning SULO leaves open, it says so and cites the decision that proposes it. The executable fragment that already maps SULO graphs onto the formal core is the [SULO interface](sulo-interface.md); this document supplies the semantics behind that mapping.

## 1. Scope

SULO 0.2.14 declares six temporal classes (`Time`, `TimeInstant`, `TimeInterval`, `StartTime`, `EndTime`, `Duration`) and four temporal relations (`atTime`, `isTimeOf`, `precedes`, `isPrecededBy`). Its axioms give their place in the class hierarchy, their domains and ranges, and some existential and datatype restrictions. They do not order instants, connect an interval to the positions of its endpoints, or say what `precedes` means in time. The repository's formal specification, by contrast, gives temporal queries a rigorous semantics over BT_C, but deliberately keeps SULO as a separately versioned representation (decision R6).

This document closes that gap for the SULO terms themselves:

1. what SULO states (§2);
2. what kind of thing a SULO temporal individual is (§3);
3. an interpretation structure (§4) and the conditions each term must satisfy in it (§5);
4. what follows, with proofs (§6);
5. which statements are SULO axioms, consequences of the interpretation, proposals, or procedural checks (§7);
6. uncertainty (§8), the correspondence with executable code (§9), and the open tensions (§10).

## 2. What SULO 0.2.14 states

From `ontology/vendor/sulo-0.2.14.ttl`, pinned by sha256 in `ontology/sulo-pin.json`:

| Term | SULO axioms |
|---|---|
| `Time` | `SubClassOf Quantity`; `DisjointUnionOf(Duration, TimeInstant, TimeInterval)`; disjoint with `Unit` |
| `TimeInstant` | `SubClassOf Time`; `hasValue only (xsd:dateTime ∪ xsd:dateTimeStamp)` |
| `StartTime` | `SubClassOf TimeInstant` |
| `EndTime` | `SubClassOf TimeInstant`; disjoint with `StartTime` |
| `TimeInterval` | `SubClassOf Time`; `hasDirectPart some StartTime`; `hasDirectPart some EndTime`; `hasPart some Duration`; `hasPart some Unit` |
| `Duration` | `SubClassOf Time`; `hasValue some xsd:decimal[≥ 0.0]` |
| `atTime` | domain `owl:Thing`, range `Time`; inverse of `isTimeOf` |
| `isTimeOf` | domain `Time`, range `owl:Thing` |
| `precedes` | domain `Process`, range `Process` |
| `isPrecededBy` | domain `Process`, range `Process`; inverse of `precedes` |

`hasValue` is functional in SULO, and `Quantity` specialises `InformationObject`. No temporal relation carries a property characteristic: `precedes` is not declared transitive, irreflexive or asymmetric.

## 3. SULO temporal individuals are descriptions

A `TimeInstant`, `TimeInterval` or `Duration` is a `Quantity`, and so an `InformationObject`. Following the [SULO and OWL-Time review](../sulo-owl-time-review.md#4-clarify-what-temporal-individuals-denote), this document reads them as **descriptions** of temporal entities, not as the temporal entities themselves:

| SULO class | Intended reading |
|---|---|
| `TimeInstant` | A description of a position on a reference axis |
| `StartTime`, `EndTime` | A time-instant description functioning as the left or right boundary of an identified interval description |
| `TimeInterval` | A description of a connected, bounded extent, through its boundary descriptions |
| `Duration` | A description of a non-negative elapsed amount, in a stated unit |

Three identities are therefore kept apart, as the review requires: the process or observation described; its temporal description in a source, context and revision; and the coincidence of the positions or extents described. The third never entails either of the first two. Two descriptions of the same time may come from different sources; two simultaneous processes remain distinct; no `owl:sameAs` follows from equal values.

## 4. Interpretation structure

A **SULO temporal interpretation** is a tuple

```text
M = (I, T, K, rho, occ)
```

- `I` is an interpretation of SULO 0.2.14 and the selected application modules under the [OWL 2 Direct Semantics](https://www.w3.org/TR/owl2-direct-semantics/). `X^I` is the extension of class or property `X`.
- `T` is a BT_C time structure, exactly as in the GFO-Time contract §2: a chronoid sort `C`, a boundary sort `B`, functions `first, last : C → B`, temporal parthood `⊑` on `C`, and boundary coincidence `≈` on `B`, satisfying the relativised axioms A1–A34 of Baumann, Loebe and Herre (2014). Write `[b]` for the coincidence class of `b`, and `D` for the set of coincidence classes, which BT_C orders densely and linearly by `<` (§8.1 of that paper). For every chronoid `c`, `[first(c)] < [last(c)]`.
- `K` assigns to each declared clock `k` a metric chart `κ_k : D_k → ℚ` (microseconds), order-preserving and reflecting equality on its domain, as in the GFO-Time contract §3. Charts exist only for declared clocks; descriptions under different clocks are comparable only through an admitted alignment.
- `rho` is a partial **reference map** from temporal descriptions in `ΔI` to temporal entities and amounts:
  - `rho(t) ∈ D` for `t ∈ TimeInstant^I` (a position),
  - `rho(t) ∈ B` for `t ∈ StartTime^I ∪ EndTime^I` (an oriented boundary),
  - `rho(t) ∈ C` for `t ∈ TimeInterval^I` (a chronoid),
  - `rho(t) ∈ ℚ≥0 × Unit^I` for `t ∈ Duration^I` (an amount).
- `occ` is a partial **occurrence map** from `Process^I` to `C`: `occ(p)` is the chronoid process `p` occupies.

`rho` and `occ` are partial because SULO's existential restrictions can be satisfied without named, valued descriptions, and because sources omit times. Partiality is never read as absence: an undefined `occ(p)` leaves temporal questions about `p` unresolved, not false.

## 5. Semantic conditions

A SULO temporal interpretation must satisfy the following conditions for every individual concerned. Each is labelled with its status in §7.

**S1 — Instants describe positions.** If `t ∈ TimeInstant^I` has a value `v` under clock `k`, and the admission policy declares that value exact, then `rho(t)` is defined and `κ_k(rho(t))` equals the coordinate of `v` in that clock. If the policy declares a precision or rounding, `v` only bounds `κ_k(rho(t))` (§8). A value never establishes coincidence across clocks.

**S2 — Start and end descriptions are oriented boundaries.** If `i ∈ TimeInterval^I` with `(i, s) ∈ hasDirectPart^I`, `s ∈ StartTime^I`, and `(i, e) ∈ hasDirectPart^I`, `e ∈ EndTime^I`, and `rho(i)` is defined, then

```text
rho(s) = first(rho(i))      rho(e) = last(rho(i)).
```

As an instant, a start or end description describes the position of that boundary: its S1 position is `[rho(s)]` or `[rho(e)]`.

**S3 — Intervals describe chronoids.** `rho(i)`, where defined, is a chronoid. A `TimeInterval` describing a zero-length extent has no referent: BT_C chronoids have non-coincident endpoints (A26, C5), and a point-like occurrence is described by an instant, not an interval.

**S4 — Durations describe lengths.** If `i ∈ TimeInterval^I` has a `hasPart` duration `d` and unit `u`, both `rho(i)` and `rho(d)` are defined, and the chart of `i`'s clock covers both endpoints, then the amount of `rho(d)`, converted to the chart's unit through `u`, equals `κ(rho(e)) − κ(rho(s))`.

**S5 — The temporal partition.** Instants, intervals and durations describe entities of different kinds (positions, chronoids, amounts), so `DisjointUnionOf(Duration, TimeInstant, TimeInterval)` is consistent with `rho`. A description outside the three branches — a signed offset, an uncertain bound, a clock — is not a `Time` (§10).

**S6 — `atTime` relates a thing to the description of when it occurs.** For a process `p` and `(p, t) ∈ atTime^I`:

| Filler | Condition |
|---|---|
| `t ∈ TimeInterval^I` | `occ(p) = rho(t)` — complete occurrence (the reading every executable interval profile selects) |
| `t ∈ TimeInstant^I` | The position of an identified boundary of `occ(p)`, or of a declared anchor, is `rho(t)`; which boundary must be stated by the profile. A bare instant does not imply that `p` has zero duration |
| `t ∈ Duration^I` | The length of `occ(p)` is `rho(t)`; nothing is said about its position |

SULO's comment ("the time in which it occurs at") also admits a containment reading, `occ(p) ⊑ rho(t)`. That reading is not the one used here; a profile adopting it must say so, because it changes every temporal answer. For things other than processes, `atTime` is a characterisation this interpretation does not constrain.

**S7 — `isTimeOf`** is interpreted as the inverse of `atTime`.

**S8 — `precedes` is strict whole-interval precedence.** For processes `a` and `b` whose occurrences are defined and lie on comparable clocks:

```text
(a, b) ∈ precedes^I   iff   [last(occ(a))] < [first(occ(b))].
```

This adopts the meaning proposed in the [temporal precedence decision](../decisions/temporal-precedence.md#recommended-semantics) (`e_A < s_B`). Where either occurrence is undefined or the clocks are incomparable, the interpretation assigns `precedes` no truth value: the result is unresolved or incomparable, never a non-match.

**S9 — `isPrecededBy`** is interpreted as the inverse of `precedes`.

The other twelve basic interval relations (`meets`, `overlaps`, `during`, …) have no SULO vocabulary. They are query operators, defined over the referents of occurrence descriptions by the table in [specification §6.1](specification/syntax-and-semantics.md#61-basic-interval-relations), with `startθ` and `endθ` the chart coordinates of `first(occ(p))` and `last(occ(p))`.

## 6. Consequences

**P1. A described interval is proper.** Under S2 and S3, for a valued interval description with start `s` and end `e` on one chart, `κ(rho(s)) < κ(rho(e))`.

*Proof.* `rho(s) = first(c)` and `rho(e) = last(c)` for the chronoid `c = rho(i)`. BT_C gives `[first(c)] < [last(c)]`, and the chart is order-preserving. ∎

**P2. Disjoint start and end descriptions are consistent with meeting.** If process `a` ends exactly where `b` begins, `Meets(occ(a), occ(b))` holds with two distinct descriptions — an `EndTime` for `a` and a `StartTime` for `b` — whose referents are distinct boundaries in one coincidence class.

*Proof.* `Meets(c, d)` iff `last(c) ≈ first(d)` (GFO-Time contract §2). `last(c)` and `first(d)` have opposite orientations and are distinct boundaries (BT_C C2–C4), so they need distinct descriptions; SULO's disjointness of `StartTime` and `EndTime` forbids only one description being both. ∎

This is the formal form of the review's resolution: keep two descriptors where one interval ends and the next begins, and establish coincidence by evaluation, not by identity of descriptors.

**P3. Same extent, same chronoid; descriptions stay distinct.** If two interval descriptions `i₁, i₂` on one clock have coincident start and end referents, then `rho(i₁) = rho(i₂)`, but `i₁` and `i₂` remain distinct individuals.

*Proof.* BT_C A25: chronoids whose respective endpoints coincide are identical. Nothing in the interpretation equates the descriptions, and SULO declares no key on `TimeInterval`. ∎

**P4. `precedes` is a strict partial order on processes with defined occurrences.**

*Proof.* Irreflexive: `(a, a) ∈ precedes^I` would require `[last(c)] < [first(c)]` for `c = occ(a)`, contradicting `[first(c)] < [last(c)]`. Transitive: `[last(occ(a))] < [first(occ(b))] < [last(occ(b))] < [first(occ(c))]`, so `[last(occ(a))] < [first(occ(c))]` by transitivity of `<` on `D`. Asymmetry follows from irreflexivity and transitivity. ∎

OWL 2 DL can state transitivity, as the precedence decision proposes, but a transitive property is not simple, and OWL 2 DL forbids declaring a non-simple property irreflexive or asymmetric. Under this interpretation `precedes` has all three properties; OWL can carry only transitivity, and the other two remain validation obligations.

**P5. Precedence needs order, not metric.** S8 uses only the order on coincidence classes. A chart is needed to evaluate gaps and durations, not to decide `precedes`. Two processes on clocks with no admitted alignment have no precedence relation under this interpretation, whatever their printed timestamps.

**P6. Durations agree with endpoints.** Under S4, a valued duration part equals the chart distance between the referents of the start and end parts. A disagreement makes the description inconsistent with every interpretation and must be rejected at admission, not silently corrected.

**P7. A duration filler fixes length, not position.** Under S6, `atTime` with a `Duration` constrains `occ(p)` to chronoids of that length; it entails no precedence, meeting or overlap with any other process.

**P8. Ordered anchors do not establish precedence.** If processes `a` and `b` are described only by instants — the point-anchor profile's `RecordedAnchorTime` — with `rho(t_a) < rho(t_b)`, `precedes(a, b)` is not entailed.

*Counterexample.* Let `occ(a)` and `occ(b)` overlap, with `a`'s anchor at its first boundary and `b`'s anchor at its last boundary. The anchors are ordered and S8 is false. ∎

## 7. Status of each statement

| Statement | Status | Where it lives |
|---|---|---|
| The class hierarchy, disjointness, existential and datatype restrictions of §2 | SULO axiom | `sulo-0.2.14.ttl` |
| Inverse pairs `atTime`/`isTimeOf`, `precedes`/`isPrecededBy` | SULO axiom | `sulo-0.2.14.ttl` |
| Temporal individuals are descriptions (§3) | Adopted reading, proposed by the review | This document; [review §4](../sulo-owl-time-review.md#4-clarify-what-temporal-individuals-denote) |
| S1–S7 | Interpretation conditions | This document |
| S8 (`precedes` iff `e_A < s_B`) | Proposed meaning, not adopted in SULO | [Temporal precedence decision](../decisions/temporal-precedence.md); adoption needs the SULO maintainers (D-SULO) |
| P1–P8 | Consequences of the interpretation over BT_C | §6 proofs; P1–P4 machine-checked (§11) |
| Exactly one start and one end per exact occurrence interval | Profile axiom, application module | `ei:ExactOccurrenceInterval` in `ontology/exact-interval-profile.ttl` |
| `precedes` transitive; `directlyPrecedes` | Proposed SULO change | Temporal precedence decision |
| `precedes` irreflexive and asymmetric | Consequence (P4), not OWL-expressible with transitivity | Validation |
| Endpoint order, duration agreement, clock compatibility, adjacency for direct succession | Procedural admission checks | Profile adapters and compilers |

## 8. Uncertainty and bounded descriptions

Recorded times are often known only within bounds: a date without a time, a parent's "about three weeks ago", a value rounded to a minute. SULO's `TimeInstant` admits only `xsd:dateTime` values and has no vocabulary for bounds. The bounded profile describes them with information objects outside the `Time` partition (`bt:LowerBound`, `bt:UpperBound`, with units and a clock) attached to the boundary descriptions `bt:StartDescriptor` and `bt:EndDescriptor`.

Under this interpretation, bounds do not fix `rho`; they constrain it. For a boundary description `b` with bounds `[lo, hi]` under clock `k`:

```text
lo ≤ κ_k(rho(b)) ≤ hi.
```

A set of bounded descriptions therefore has many compatible interpretations, one for each admissible choice of positions. The bounded engine's answers are exactly the two quantifications over them:

| Engine result | Over compatible interpretations |
|---|---|
| Possible | Some compatible interpretation satisfies the query |
| Certain | Every compatible interpretation satisfies it |
| Impossible | None does |
| Incomparable | The query relates descriptions under clocks with no admitted alignment |

The execution profile restricts named boundary coordinates to integer microseconds (`GFOBoundaryMicrosecond`); unnamed boundaries remain dense, as the specification §2.2 requires. A rounded value bounds a position; it never establishes exact coincidence (S1).

## 9. Correspondence with executable code

| Condition | Where it is realised |
|---|---|
| S2 (start and end as oriented boundaries) | `ei:ExactStartTime`/`ei:ExactEndTime` and `bt:StartDescriptor`/`bt:EndDescriptor` subclass `sulo:StartTime`/`sulo:EndTime`; the [SULO interface](sulo-interface.md#executable-mapping-contract) maps "typed StartDescriptor / EndDescriptor direct parts" to one canonical variable each |
| S3, S6 for intervals (complete occurrence) | The interface's "exactly one selected `sulo:atTime` link to a bounded occurrence description"; `ei:ExactOccurrenceInterval` with qualified cardinality one on start and end |
| S1, §8 (values and bounds) | The bounded RDF reader's typed SOLID lower and upper descriptors with unit and clock; `patterns/bounded_intervals.py` |
| P5 and clock scope | Explicit clock descriptions; "no equality inferred from similar-looking timestamps"; incomparable clocks reported as incomparable |
| S8 and the §6.1 operators | `patterns/bounded_cohort.py` compiles `before` as `end(A) < start(B)`; `patterns/temporal_stn.py` decides it with path certificates |
| P8 | The point-anchor profile does not claim whole-interval ordering (temporal precedence decision; architecture §5) |

The eight paired fixtures under `examples/temporal-interface/` are evidence that, for the common fragment, SULO graphs and formal-core graphs yield the same coordinates and answers. This document states the semantics under which that agreement is the expected result; the fixtures do not prove it in general.

## 10. Tensions and open questions

1. **Structured quantities.** A located `TimeInterval` is a `Quantity`, but equal durations do not make equal intervals. The quantity reading needs to accommodate structured quantities, or the class's inclusion needs explaining (review §4).
2. **`atTime` is broad.** Its domain is any thing and its range includes `Duration`. S6 interprets each filler kind separately; a profile must choose one reading per use, and the containment reading must be declared where intended.
3. **Instants accept only datetimes.** Numeric coordinates, signed offsets and bounds need classes outside `Time`; the disjoint union forbids adding them as further `Time` branches (S5).
4. **`precedes` is restricted to processes.** Ordering of states, observations or descriptions has no SULO relation.
5. **No interval relations.** SULO has `precedes` only; meeting, overlap and containment exist as query operators, not vocabulary.
6. **Precedence semantics is proposed, not adopted.** S8 depends on the SULO maintainers' decision (D-SULO). Until then it is this interpretation's choice, stated as such.
7. **Point-anchor descriptions.** `RecordedAnchorTime` identifies an anchor, not a boundary; S6 for instants requires the profile to say which boundary, or none, an anchor locates.

## 11. Verification and next steps

[`specification/formal/SULOTemporal.lean`](specification/formal/SULOTemporal.lean) machine-checks the core of this document against the BT_C axioms of [`IntegratedSemantics.lean`](specification/formal/IntegratedSemantics.lean). It is checked by `formal/check.sh` in the `temporal-semantics` workflow, with warnings treated as errors, and none of its theorems depends on an axiom beyond its stated hypotheses.

| Statement | Lean | Checked |
|---|---|---|
| S2, S6 for interval fillers, S8 | Fields of `Interpretation` | As hypotheses |
| SULO axioms used (StartTime and EndTime are instants and disjoint; `precedes` relates processes) | Fields of `SULOAxioms` | As hypotheses |
| P1, intervals are proper | `interval_proper` | Yes |
| P2, meeting with distinct start and end descriptions | `meeting_descriptions_distinct` | Yes |
| P3, equal extents give one chronoid | `same_extent_same_chronoid` | Yes |
| P4, `precedes` irreflexive, transitive and asymmetric | `precedes_irrefl`, `precedes_trans`, `precedes_asymm` | Yes |
| Precedence from `atTime` interval descriptions (S6 with S8) | `precedes_from_atTime` | Yes |
| P5, precedence needs order, not metric | — | By construction: S8 mentions no chart |
| P6–P8 | — | Not checked: P6 needs charts, and P8 needs a constructed BT_C model |

Two limits are explicit in the artifact. First, the integrated interface axiomatises BT_C's A1–A34 but not the §8.1 order on boundary positions, so the order used by P1 and P4 is a separate, named assumption (`BTCOrder`: strict, respecting coincidence, each chronoid's first boundary before its last); density and linearity are not needed. Second, as in the existing development, BT_C is an axiomatic interface, not a constructed model, so the hypotheses are not shown to be jointly satisfiable in Lean. The model is a single comparable clock; clocks without an admitted alignment remain outside these theorems.

Next steps: carry the order into the integrated interface itself; add charts so that S1 and P6 can be checked; and add representative SULO graphs that exercise S2–S8 to the OWL consistency check.

## References

- T. Baumann, F. Loebe and H. Herre. Axiomatic theories of the ontology of time in GFO. *Applied Ontology* 9(3–4), 2014.
- J. F. Allen. Maintaining knowledge about temporal intervals. *Communications of the ACM* 26(11), 1983.
- W3C. *OWL 2 Web Ontology Language Direct Semantics*, second edition, 2012.
- W3C. *Time Ontology in OWL*, 2017.
- SULO 0.2.14, `ontology/vendor/sulo-0.2.14.ttl`, pinned in `ontology/sulo-pin.json`.
