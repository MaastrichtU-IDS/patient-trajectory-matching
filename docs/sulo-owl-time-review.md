# SULO temporal representation and reasoning

**Developer review and implementation recommendations — 15 September 2026**

**Status:** Design guidance. The [v2.4 addendum](../addenda/specification-2.4.md) remains the current executable contract. The recommendations below do not activate new ontology axioms or interval/uncertainty support. The [proposed precedence decision](decisions/temporal-precedence.md) develops the follow-up discussion about direct succession and temporal contact.

## 1. Recommendation and scope

Retain SULO's small property vocabulary and its PRO/SOLID representation. Define a versioned temporal application profile using classes and individuals, then compile that representation into a typed temporal constraint system and compact event indexes. Use OWL reasoning for supported ontological consequences and a dedicated temporal evaluator for ordering, metric constraints, uncertainty and temporal applicability. Query correctness must be established across these components, rather than inferred from any one component's capabilities.

The product can make substantial progress without changing SULO's core axioms. The immediate work is to remove ambiguity from temporal descriptions, extend the executable profile beyond point anchors, and make the reasoning and projection contracts testable. Changes to the core should follow demonstrated representational needs and migration tests.

This review inspected:

| Baseline | Exact scope |
|---|---|
| SULO | `sulo.ttl` at commit `1a4abc1699471187e94fbc59591101b2b635d6ea`; declared version 0.2.14; Git blob `419e825e1ea232efab5fc0b6d7d3e90223392421` |
| Core bytes | SHA-256 `433c83980ff2c37f241f8150d5525b84caeed9fa87825ff30341716c93c25a5e`; identical to the core vendored in contract pack 2.4 |
| Formal design | `Temporal_Knowledge_Graph_Formal_Definition.tex`, read in full, including time-indexed interpretation, PRO and named bindings |
| Product design | `Patient_Trajectory_Matching_Product_Specification_v2.3.docx`, 53 pages, including temporal normalization, semantic bundles, replay and exact/relaxed execution |
| Latest encoding | `Patient_Trajectory_Matching_Contract_Pack_v2.4.zip`, dated 15 September 2026; its 2.4 addendum supersedes the earlier ontology encoding examples |
| Implementation evidence | Active PRO/SOLID adapter, shapes, fixtures and oracle in that pack; current rustDL README; SULO CI and the pinned test-harness documentation |

The latest contract is important: application extensions declare **no new object or datatype properties**, and instance literals use only `sulo:hasValue`. Domain-specific patient shortcuts are prohibited. The archived `ontology/legacy-2.3/` files are not the current contract. This review preserves these decisions. JSON fields and internal engine operators are computational structures, not additions to SULO's RDF vocabulary.

This repository edition carries the review into the project documentation. Proposed classes and interpreters below describe future profiles; they are not installed by this document. The source product specification and formal definition listed above were reviewed separately and are not included in this repository snapshot.

Primary repository evidence: [pinned SULO](https://github.com/AIDAVA-DEV/sulo/blob/1a4abc1699471187e94fbc59591101b2b635d6ea/sulo.ttl), [regression workflow](https://github.com/AIDAVA-DEV/sulo/blob/1a4abc1699471187e94fbc59591101b2b635d6ea/.github/workflows/regression.yml).

## 2. Principal findings

The following conclusions distinguish defects from deliberate modelling choices. The recommendations are proposed changes or engineering decisions; they are not claims that the present ontology already implements them.

| Finding | Assessment | Specific recommendation |
|---|---|---|
| Times are quantities and information objects | Coherent with the chosen representation | Define temporal individuals explicitly as descriptions and specify what their identity tracks |
| StartTime and EndTime are disjoint | Appropriate for separate contextual descriptors | Retain disjointness; represent coincident positions with distinct descriptors and a normalized comparison |
| `hasValue` is functional | Essential SOLID constraint | One scalar value per datum; separate source, normalized, revised and bounded values |
| A TimeInterval requires start, end, duration and unit through existing restrictions | Logical existence is useful, but does not ensure explicit usable data | Add versioned exact/partial profile validation, without imposing exact-data obligations on all SULO graphs |
| Generic parthood connects different component functions | Efficient vocabulary, potentially ambiguous access | Use typed direct parts for field extraction and scalar unit selection |
| `precedes` has no transitivity axiom | A missing consequence if strict precedence is intended | Define its interval semantics before adding transitivity; keep cycles and arithmetic in validation |
| `atTime` accepts all Time subclasses, including Duration | A broad characterization relation | Specify how each typed filler is interpreted and how occurrence differs from claim validity |
| TimeInstant values are datetime datatypes | Appropriate for dated positions, insufficient for raw coordinates | Use a separate numeric coordinate quantity and a declared frame; do not put numeric literals on TimeInstant |
| Duration requires a non-negative decimal | Useful safeguard | Represent signed offsets and gaps separately from Duration |
| Time is a disjoint union of three classes | A deliberate closed conceptual partition | Model uncertainty and reference metadata as descriptions outside that partition |
| Full SULO contains expressive OWL constructs | An EL-only path cannot be presumed sufficient | Declare completeness per operation and pinned axiom set |
| Active adapter accepts one RecordedAnchorTime and a narrow predicate list | Deliberately bounded implementation | Introduce a separate interval profile; do not silently widen the current adapter |
| Source graph and execution projection coexist | Necessary for efficient matching | Prove scoped query preservation and retain all role, time, value and provenance bindings |

The ontology observations above were checked against the [pinned SULO source](https://github.com/AIDAVA-DEV/sulo/blob/1a4abc1699471187e94fbc59591101b2b635d6ea/sulo.ttl); adapter observations refer to contract pack 2.4, `patterns/pro_solid.py` and its addendum.

## 3. What OWL-Time contributes

OWL-Time separates temporal entities, position descriptions and duration descriptions. Boundary relationships are explicit; its interval vocabulary covers Allen relations. Reference systems are first-class descriptions. These distinctions provide a useful interoperability target. The published document returned by `/TR/owl-time/` is the 15 November 2022 Candidate Recommendation Draft. Its text and the subsequently edited RDF source should be pinned separately. [OWL-Time](https://www.w3.org/TR/owl-time/)

| Information to preserve | OWL-Time vocabulary | SULO profile strategy |
|---|---|---|
| Position | Instant and position descriptions | Typed datum plus reference-frame binding |
| Interval boundary | hasBeginning / hasEnd | Contextual StartTime / EndTime direct parts |
| Extent | Duration, numericDuration, unitType | Duration datum, hasValue, direct unit |
| Topology | Before, meets, overlaps and related predicates | Typed AST operators and evidence-backed derived views |

This comparison concerns representational choices. Neither importing an ontology nor copying its labels establishes the arithmetic, identity, completeness or query guarantees required by this product. The current RDF source also evolves independently of the published prose: its `before` and `after` properties are declared transitive. Bind bridge tests to the actual chosen bytes. [OWL-Time RDF source](https://github.com/w3c/sdw-time/blob/gh-pages/rdf/time.ttl)

## 4. Clarify what temporal individuals denote

I recommend adopting the following intended readings. These are proposed definitions for review, not quotations from SULO.

- **Temporal quantity:** an information object representing a temporal coordinate, an elapsed amount, or a structured temporal extent under a stated interpretation.
- **Time instant:** a temporal quantity representing a position on a reference axis. Its record identity may distinguish the context, source and representation of that position.
- **Time interval:** a temporal quantity representing a connected bounded extent through its boundary descriptions. Its duration alone does not determine that extent's position.
- **Duration:** a temporal quantity representing a non-negative elapsed amount, expressed in a compatible unit.
- **Start time / end time:** a time-instant description functioning as the respective boundary of an identified interval or process description.

A coordinate is a legitimate scalar quantity once an origin and unit are supplied. There is no need to reject the quantity treatment of temporal position. The qualification needed is that a located interval is structured: equal duration does not imply equal interval.

The current Quantity definition should either explicitly accommodate structured quantities or explain why TimeInterval is included. This is primarily conceptual clarification. Renaming the existing class hierarchy would impose migration costs without automatically improving execution.

Identity must be explicit at three levels:

1. Identity of the process or observation being described.
2. Identity of its temporal description in a source/context/revision.
3. Equality or coincidence of represented coordinates or extents.

The third level does not entail either of the first two. Avoid keys or `owl:sameAs` rules based only on a timestamp, value or endpoint pair. Two records of the same time may have different sources; two simultaneous processes remain distinct.

The original concern about disjoint StartTime and EndTime therefore has a specific resolution. Keep two descriptors when A ends where B begins. The temporal evaluator establishes coincidence after checking compatible clocks. Do not turn a boundary description into a SULO Role: Role and InformationObject are disjoint in the current core. A distinct role individual would be required if an explicit role model were introduced; that extra structure is unnecessary for the current endpoint convention. [SULO hierarchy and disjointness](https://github.com/AIDAVA-DEV/sulo/blob/1a4abc1699471187e94fbc59591101b2b635d6ea/sulo.ttl)

## 5. A class-only temporal profile

The existing `RecordedAnchorTime` is a recorded anchor for a clinical process, not automatically a source-recording timestamp. Clarify that in its label and documentation. An anchor used to match a process does not establish that the process has zero duration.

The proposed profile should offer explicit entry points:

| Profile | Input guarantee | Execution |
|---|---|---|
| Point-anchor 2.4 | One supported explicit-offset anchor per projected process | Preserve the existing runner and its tests |
| Exact occurrence interval | Explicit start/end descriptors, frame and selected duration/unit | Direct arithmetic and interval indexing |
| Bounded temporal support | Endpoint variables, bounds and dependency constraints | Joint feasibility and entailment checks |
| Calendar / relative normalization | Typed source form, anchor and transformation policy | A declared normalizer before metric execution |

For an exact interval, use `process atTime interval`, with StartTime and EndTime as direct parts of the interval. A selected elapsed-duration datum is another part. Direct parts carrying a ClockBinding distinguish the frame without introducing a dedicated property. Join processes through distinct PatientRole individuals borne by the same person, preserving the PRO witness for each process.

The proposed OWL additions for the exact subclass include qualified cardinality one on **hasDirectPart** for StartTime and EndTime. These axioms describe logical identity constraints; SHACL separately requires named, explicit, correctly typed data. Endpoint order and duration agreement are procedural constraints.

An application interval description need not carry its own scalar `hasValue`. The recommended exact profile prohibits it, while allowing its constituent datums to carry values. This prevents a single literal from being interpreted inconsistently as the interval's location, duration or serialization.

For partial data, retain the known description and a reason for missing or unresolved components. An existential OWL restriction can be satisfied without a named endpoint. Conversely, an ingestion profile can require an explicit endpoint before projection. These are different obligations. Do not reinterpret failure of an exact-profile requirement as a negative patient match. [OWL direct semantics](https://www.w3.org/TR/owl2-direct-semantics/)

A date-only observation usually supplies support for an unknown position within a day under a known calendar/clock interpretation. It does not assert that the observation occupied the whole day. Keep an uncertain point's one time variable distinct from the two boundary variables of an uncertain proper interval.

The earlier inline expression `hasValue some xsd:dateTimeStamp[>= start, < end]` needs this same distinction. It requires a value within a bounded datatype range; it does not define the enclosing individual's interval boundaries or say that a process occupies the whole range. Store boundary descriptors explicitly. Use the datatype restriction for a constrained point value only when its intended uncertainty semantics are declared. With functional hasValue, it cannot mean that the datum carries every timestamp in the interval.

For generic numeric coordinates and signed offsets, define application subclasses of Quantity. Do not make them extra disjoint children of Time: the existing covering axiom would force membership in its three existing branches. Do not type a negative offset as Duration. Proposed NumericTemporalCoordinate and SignedTemporalOffset classes illustrate this separation; they are not implemented by the current point-anchor adapter.

## 6. Scalar values, units and frames

The core's transitive `hasPart` is deliberately broad. Suppose an interval has start/end datums expressed with Second, and a duration datum expressed with Minute. Parthood propagation makes both units reachable from the interval. Counting all reachable units as competing scalar units is incorrect.

Use one explicit convention per adapter version:

- For newly designed scalar datums, use one typed direct unit part, retrieved through `hasDirectPart`.
- For the existing 2.4 data, its asserted `hasPart` unit link remains valid within its declared graph view. An adapter can compile that profile explicitly rather than pretending it already uses direct parts.
- Do not run a global exactly-one-unit shape on every Quantity in a fully expanded graph.
- Define whether validation sees asserted edges, named-class closure, subproperty closure or a broader materialization. Changing this view changes the data seen by cardinality checks.

Retrieve the unit from the selected duration datum to avoid that ambiguity. The ontology's reflexive parthood also means a Unit can meet its inherited unit-existence restriction through itself; do not infer that every unit needs an endless chain of newly generated unit nodes. This is an analytical consequence of the [current axioms](https://github.com/AIDAVA-DEV/sulo/blob/1a4abc1699471187e94fbc59591101b2b635d6ea/sulo.ttl).

A frame is not a unit. A frame contract identifies origin, scale, unit, scope and precision policy, and may include a calendar and timezone policy. Equal numeric coordinates on different patient clocks are not comparable until a valid mapping is available. Relative matching can align each patient to that patient's own index without claiming absolute synchronization. MIMIC's documented patient-specific date shifts make this distinction operationally necessary. [MIMIC-IV documentation](https://mimic.mit.edu/docs/iv/about/)

Use exact integer microseconds where the source and supported operation are representable at that resolution. Retain finer precision or conservatively bound it; do not truncate and then advertise an exact result. Use checked integer arithmetic and keep decimals out of binary floating-point threshold comparisons.

Canonical normalization should produce a separate datum or execution field while retaining the original lexical value. Never put an original-offset timestamp and its UTC rendering on one functional hasValue datum. OWL's dateTime semantics distinguish equality on the timeline from data-value identity when offsets differ; functional-property consequences need dedicated tests. [OWL time datatypes](https://www.w3.org/TR/owl2-syntax/#Time_Instants)

The union of dateTime and dateTimeStamp in TimeInstant's value restriction is redundant under their datatype relationship. Simplifying it to dateTime is a reasonable core cleanup to test, while the exact profile continues to require dateTimeStamp. Do not globally narrow the core to timezone-bearing values: that would exclude legitimate local source descriptions. [XSD dateTimeStamp](https://www.w3.org/TR/xmlschema11-2/#dateTimeStamp)

## 7. Keep occurrence, validity and evidence history distinct

The product already distinguishes these axes. Carry them into the RDF projection through typed information objects rather than new predicates.

| Axis | Describes | Proposed class-only encoding |
|---|---|---|
| Occurrence | When a process happened | Process `atTime` its typed occurrence description |
| Assertion validity | When a proposition is claimed to hold | Claim direct part ValidityBinding, which `refersTo` a temporal support description |
| Source recording | When the source recorded it | Source-record direct part SourceRecordingTime datum |
| Source availability | When information became available under the source policy | Separate SourceAvailabilityTime datum |
| Product transaction history | When a version entered/left a published product view | Typed publication/version metadata and immutable snapshot manifest |

These proposed bindings are information objects. Their interpretation is specified by the snapshot compiler; arbitrary `refersTo` edges do not assert the represented proposition.

A class-only claim encoding can use an AssertionRecord with typed SubjectReference, PredicateIdentifier, ObjectReference or ObjectDatum, PolarityDatum and ValidityBinding parts. Reference components use `refersTo`; the predicate IRI is an `xsd:anyURI` value on a typed information datum. The compiler resolves it against the pinned signature and checks argument types. This avoids treating property-as-individual punning as an assertion mechanism. Revisions can similarly use typed predecessor/successor reference components. This is a proposed interpreter format and requires its own schema and semantics before adoption.

Source temporal constraints can use the same discipline. A TemporalConstraint information object has typed LeftOperandBinding and RightOperandBinding parts referring to time descriptors, an OperatorDatum identifying a supported operator, and typed lower/upper bound quantities with units and inclusivity data. Distinguish an absent bound from an infinite value; identify the shared variable or anchor explicitly. The snapshot compiler interprets eligible source constraints as part of Gamma. The canonical JSON AST remains the authority for query constraints; an RDF serialization of a query is a projection of that AST, not a competing language. These constraint classes and their interpreter are proposed future work, beyond the current executable profile.

Select the product snapshot and source-as-known/reconstruction policy before deriving the semantic view. A corrected assertion must not erase the original observation or a different later observation. A statement that a specimen was collected on Monday remains a historical statement on Tuesday. A validity-qualified relationship asserted throughout an interval does not imply its negation outside that interval.

Do not take the timeless union of mutually exclusive state assertions or corrected literals on one datum. The core's functional hasValue operates within the selected interpretation; a snapshot compiler must preserve that scope. General pointwise temporal OWL semantics remain a research extension. The executable profile should certify only the selected finite, record-grounded view it actually implements.

## 8. Core changes worth considering

The recommended immediate release can leave core logical behaviour unchanged while adding definitions, profile examples and regression cases. Two axiom changes merit separate review:

**A. Simplify the TimeInstant datatype union.** Replace the redundant union with dateTime in the universal restriction, preserving the ability to represent local datetime values. Verify supported lexical/value behaviour and source-to-axiom conversion. This is a datatype-normalization cleanup, not an expansion to numeric coordinates.

**B. Make precedes transitive after fixing its meaning.** I recommend the intended meaning “the first process's complete occurrence interval ends strictly before the second begins”. Under that interpretation, transitivity is justified. The proposed axiom would be `TransitiveObjectProperty(sulo:precedes)`; the inverse inherits the consequence. This should be a deliberate semantic release because downstream ontologies may use precedes in constructs that require a simple property. The [proposed precedence decision](decisions/temporal-precedence.md) also considers a non-transitive direct subproperty, explains why strict precedence excludes Allen meets, and records the unresolved sequence-context semantics.

Do not add OWL asymmetry or irreflexivity to the same transitive property in an OWL 2 DL profile. Likewise, qualified cardinalities belong on the simple hasDirectPart property, not transitive hasPart or chain-derived hasParticipant. Check cycles and strictness procedurally; general property chains cannot compare endpoint literals. [OWL 2 global restrictions](https://www.w3.org/TR/owl2-syntax/#Global_Restrictions_on_Axioms_in_OWL_2_DL)

If existing users interpret precedes as start-order or an observation-anchor order, retain the current core until migration is resolved. The product can execute a precisely defined AST relation without first changing the core. In particular, a strict relation between point anchors must not automatically become precedes between the complete processes.

Keep atTime broad, but revise its definition to explain its typed temporal-characterization uses, including Duration. Typed fillers and the application profile supply the distinction. A value restriction alone does not identify a full occurrence extent.

Retain StartTime/EndTime disjointness under the contextual reading. Retain functional hasValue. Do not add clinical shortcuts, universal endpoint cardinalities on all TimeInterval data, or a blanket OWL-Time import to achieve the application profile.

## 9. Allocate reasoning responsibilities explicitly

| Layer | Owns | Required output |
|---|---|---|
| OWL parser/model | Syntax, imports, axiom inventory | Pinned model with loss diagnostics |
| rustDL semantic compiler | Supported subclass/instance/property consequences | Positive proofs, operation coverage, unresolved results |
| Profile validator | Explicit shape, datatype, unit and context requirements | Field-specific invalid/unsupported diagnostics |
| Temporal normalizer | Clocks, anchors, precision, calendar and unit conversions | Coordinates/bounds plus dependency and provenance records |
| Temporal evaluator | Joint feasibility, ordering and metric entailments | Temporal status and witnesses/counterexamples |
| Snapshot interpreter | Active assertion versions and temporal applicability | A versioned supported reasoning context |
| Matcher | Named bindings, conjunctions, selectors and permitted relaxation | Membership, alignment, cost and search-completeness status |

Preserve the Rust/Python stack. Use horned-owl as the authoritative OWL model, py-horned-owl for Python administration where useful, and rustDL through a pinned compatible dependency graph. Pin parser format support as well as package versions. A successful RDF parse does not establish lossless conversion of OWL-in-RDF axioms. [py-horned-owl](https://github.com/ontology-tools/py-horned-owl)

The rustDL documentation inspected for this review reports sound positive consequences, completeness on its declared EL/Horn fragment, and broader operations with incompleteness and dropped-axiom diagnostics. Its README also contains a shorthand “unbounded = complete” example that must not override the qualified completeness statement. The existing SULO harness explicitly separates proven results, unrefuted negatives and indeterminate cases. Treat those distinctions as requirements for product activation. [rustDL](https://github.com/MaastrichtU-IDS/rustdl/blob/main/README.md), [pinned SULO test harness](https://github.com/MaastrichtU-IDS/sulo-testharness/blob/v0.1.0/README.md)

Full SULO is not automatically covered by an EL-only execution path: inspect its disjoint unions, universal restrictions, complements, inverses, functionality and datatype expressions. An extracted Horn subset may support sound positives while missing consequences of the original ontology. A false lookup is therefore not enough to establish complete non-entailment.

For each advertised operation, record the source axiom inventory, normalized inventory, dropped/rejected constructs, timeout status and completeness scope. Unsupported relevant content blocks complete exact execution; already proven matches can still be labelled as sound partial results when the requested output policy permits this.

The SULO repository still contains a ROBOT/HermiT workflow alongside its Rust harness. That existing repository practice does not require adding Java to this product. Expand the Rust conformance path and keep application, supported builds and deployment JVM-free. [Current reasoning workflow](https://github.com/AIDAVA-DEV/sulo/blob/1a4abc1699471187e94fbc59591101b2b635d6ea/.github/workflows/reasoning.yml)

## 10. An efficient temporal execution kernel

Use direct endpoint arithmetic for exact coordinates. Use a Simple Temporal Network (STN) for conjunctions of supported uncertain metric constraints. An STN represents bounds on differences between time variables and admits polynomial consistency algorithms; it does not make event-binding search or arbitrary temporal disjunction polynomial. [Hunsberger and Posenato, 2021](https://drops.dagstuhl.de/entities/document/10.4230/LIPIcs.TIME.2021.1)

The proposed internal constraint form is:

$$
\ell \le x_j-x_i \le u.
$$

Represent it as two directed upper-bound edges. A point anchor has one variable; a proper interval has two, with start strictly less than end. A known offset from a shared uncertain anchor remains a difference constraint, preserving correlation.

For the formal rational-time design, preserve strictness in edge bounds. Do not implement a strict comparison by subtracting one microsecond unless the selected profile explicitly constrains all relevant variables to that discrete grid. Exact microsecond data can use checked integer operations; dense uncertain domains need strict-bound semantics or a justified enclosure.

Recommended execution paths:

1. **Exact data:** evaluate endpoint comparisons directly and batch compatible candidates. No STN object is needed per trivial timestamp comparison.
2. **Bounded conjunctive uncertainty:** maintain source constraints, compute feasibility, then test query constraints against that feasible set. A small Floyd–Warshall implementation is a useful reference; use incremental propagation or sparse shortest-path methods only after differential tests establish equivalence.
3. **Explicit bounded alternatives:** branch over the AST's allowed alternatives and solve each supported conjunction. Preserve branch identity. Arbitrary relation disjunction is not accepted as an ordinary STN edge.
4. **Calendar/relative expressions:** resolve through their declared normalizer; reject or mark unresolved when dependencies or conventions are unavailable.

For a fixed proper-interval relation, compile its endpoint equalities and inequalities. “Overlap” must distinguish directional Allen overlap from non-empty temporal intersection. “During” is strict containment in the project definition; inclusive containment is a separate operator. “Before” excludes meets. Half-open episode membership includes the start and excludes the end. An instant at a process boundary is not a zero-length proper interval.

Do not materialize every pairwise temporal relation. Retain endpoints and requested derived edges with evidence. An all-pairs relation table over m events can require quadratic space even when the source representation is linear. Computing a relation on demand is often cheaper than storing and invalidating its closure.

Temporal evidence should identify the input assertions, clock mapping, source constraints, rule and endpoint/bound calculation. A path establishing a difference bound can provide a compact justification; a negative cycle can explain inconsistent constraints. These are proposed evidence contracts, beyond the current exemplar oracle.

## 11. Uncertainty, certain answers and relaxation

For a fixed named binding b, let F be the jointly feasible assignments of its temporal variables under the selected **source knowledge**, including dependencies and interval geometry. Let Q(b,t) be its query constraints.

$$
\begin{aligned}
\mathrm{certain}(b)&\iff F\ne\varnothing\ \land\ \forall t\in F:Q(b,t),\\
\mathrm{possible}(b)&\iff \exists t\in F:Q(b,t).
\end{aligned}
$$

An empty F is inconsistent input, not a vacuously certain match. Do not add the query to F and then claim that the surviving assignments prove certainty. That computes compatibility with the query, not entailment from the evidence.

Three counterexamples should remain permanent regression cases:

- X can be before 4 and X can be after 6 when X ranges from 0 to 10; both conditions cannot hold in one assignment.
- B = A + 24 establishes a 24-unit gap even if A itself is uncertain. Replacing them by independent marginal bounds destroys this information.
- An uncertain point cannot precede itself. Duplicating its single time variable into independently varying start and end would create spurious possibilities.

The product's fixed-witness condition is also a meaningful commitment. Requiring one named binding that works in every completion has quantifier order **exists binding, for all completions**. It can be stronger than a patient-level existential query whose witness may differ between models. The formal design and UI should name the fixed-witness result explicitly rather than implying completeness for every broader notion of certain existential answer.

For robust relaxed matching, use:

$$
\min_b\ \sup_{t\in F_b}\mathrm{cost}(b,t),
$$

subject to all hard constraints and the declared caps. For finite discrete feasible sets, the supremum is a maximum. Hard failure in any feasible completion prevents definite robust acceptance for that binding. Possible-only acceptance remains separate.

The maximum of a sum of costs is not generally the sum of their separate maxima. For x in [0,10], costs x and 10−x always sum to 10, whereas their separate maxima sum to 20. The latter is a conservative upper bound, not an exact robust cost. It may be used to certify acceptance below a budget, but cannot justify optimality or definitive rejection above the budget without further evaluation.

For incomplete fields, preserve the existing separation of knowledge status, computation status, record-query status and search completeness. A fully searched record scope with no observed binding may be a record-query FAIL while the clinical proposition remains undetermined. Timeouts and unsupported constructs are not clinical negatives.

## 12. Representation should compile into indexes

SULO's explicit graph is valuable for semantics, source attribution and inspection. Repeated traversal of its component structure should not be the inner loop of every candidate comparison.

Build one immutable materialization per selected graph/snapshot/semantic bundle. Retain identifiers for process, patient-role witness, bearer, occurrence description, endpoint datums, result datum, unit, source assertions and normalization decisions.

| Execution structure | Purpose | Correctness condition |
|---|---|---|
| Concept postings | Candidate events for reviewed semantic predicates | Include every entailed eligible concept in the certified profile |
| Patient/episode arrays | Chronological joins and bounded scans | Preserve named event identity and declared episode membership |
| Start/end indexes | Interval containment and gap lookup | Respect endpoint roles, inclusivity and clock scope |
| Variable/dependency records | Correlated uncertain times | Do not flatten shared anchors into independent bounds |
| Value columns | Numeric thresholds and selectors | Preserve exact normalized values, comparators and unit policies |
| Evidence references | Explain accepted bindings | Resolve to the same immutable source and semantic snapshot |

Concept matching may depend on a role's bearer. In PRO, a DrugAdministration is not itself an instance of DrugA. Compile the administration predicate through AdministeredDrugRole and its bearer, or through a certified inferred administration class. Similarly, preserve the measurement-result role and measured-quality subject; a generic participant link alone cannot establish patient participation.

Keep taxonomy preparation outside the hot loop. Cache expansions for requested clinical concepts and store sparse postings; avoid a dense concept-by-patient closure. Resolve instance-level predicates against the certified semantic bundle, then cache the event classification with provenance and version.

For query-to-cohort execution, select a selective mandatory anchor, intersect necessary patient/episode postings, use time bounds to narrow candidate bindings, and verify complete bindings. A branch union, optional slot or allowed relaxation changes what constitutes a necessary filter. Pruning must account for those changes. Exact execution must remain exhaustive after lossless pruning.

For best relaxed matches, use admissible lower bounds and deterministic tie-breaking. Under temporal correlation or selectors, a naïve sum of local costs may not be an admissible or exact bound in the intended optimization; establish the bound's direction and use explicitly.

All-pairs patient matching remains R2, as in the existing product scope. Start with an exhaustive small-cohort baseline and only then add blocking or approximate proposals with declared recall. A requested dense all-pairs output itself has quadratic size; directed top-k neighbours are a different output contract. Pairwise verification of returned candidates does not establish that omitted pairs or true top-k neighbours were found.

The central engineering obligation is scoped query preservation: the supported graph interpreter and its projection return the same eligible named bindings, statuses, selected alignments and costs, with equivalent evidence references. Test this against independent small fixtures and randomized perturbations. Do not claim preservation for arbitrary OWL or SPARQL.

## 13. Coordinate the ontology and temporal engines

A modular architecture does not automatically imply modular semantics. Define the interface between engines explicitly.

The first supported profile should use a static, pinned ontology for structural/clinical consequences and a finite temporal constraint system for source-derived variables. Ontological class tests narrow named candidates; temporal tests evaluate those candidates. No temporal operation generates anonymous clinical evidence.

If a derived temporal relation becomes input to further OWL classification, record the dependency and evaluate the resulting stages in a declared order. A one-pass pipeline is insufficient when later temporal facts enable new classifications used by the same query. For the first profile, permit only acyclic, stratified dependencies or reject the construct. A general fixed point over expressive OWL plus arithmetic requires its own semantics and termination/completeness argument.

The proposed proof obligations are:

1. **Encoding:** each admitted graph pattern has one documented interpretation; ambiguous fillers are rejected or retained as unresolved.
2. **Normalization:** accepted conversions preserve temporal meaning or return a sound declared enclosure.
3. **Semantic coverage:** advertised OWL operations are sound and complete for the activated profile, or report incomplete coverage.
4. **Temporal evaluation:** certain/possible status uses the same nonempty feasible source-model set and joint dependencies.
5. **Projection:** supported graph queries and execution records agree.
6. **Pruning/optimization:** discarded candidates cannot change the advertised exact result or certified optimum.
7. **Revision:** graph, indexes, caches and evidence use one compatible snapshot and interpretation policy.

These obligations turn the formal TKG definition into an implementable contract. They do not establish a general temporal description logic by themselves.

## 14. An OWL-Time bridge without changing canonical SULO

Implement a separate, versioned transformation view. Export each selected occurrence interval and its boundary positions with their intended meaning; preserve links back to source descriptors in the bridge manifest. A shared exported coordinate may correspond to several contextual SULO datums, while the canonical source identities remain distinct.

Guard every transformation by type, frame and profile. A generic hasDirectPart relation cannot globally become a boundary relation. A Duration target of atTime cannot automatically become the same kind of target as an occurrence interval. A Unit is not a Duration instance in SULO's current disjointness regime. Prefer named transformation rules over blanket class or property equivalence.

Round-trip checks should compare normalized temporal meaning, boundary function and evidence, while allowing representation-specific node identities. A lossy conversion must identify discarded precision, clock context or provenance. Do not silently import the bridge's richer property vocabulary into the class-only canonical instance graph.

## 15. Recommended implementation sequence

| Priority | Deliverable | Concrete acceptance gate |
|---|---|---|
| P0 | Temporal meaning and identity ADR | End/start coincidence, point-anchor versus full interval, and occurrence versus validity examples agreed |
| P0 | Profile capability and graph-view declaration | Existing 2.4 graph still works; interval input routes to a new profile; unsupported inputs are explicit |
| P0 | Interval classes, shapes and scalar-access rules | Positive and negative fixtures; direct unit access; no new object/data properties |
| P0 | rustDL/parser operation inventory | No silent axiom loss or incomplete negative promoted to complete exact output |
| P1 | Exact interval adapter and indexed DTO | Same PRO bearer join and evidence identity; endpoint arithmetic agrees with source graph |
| P1 | Bounded uncertainty evaluator | Joint-feasibility, shared-anchor and fixed-witness counterexamples pass |
| P1 | Snapshot and assertion interpreter | Original/corrected/repeated records and cutoff replay remain distinguishable |
| P1 | Optimized query-to-cohort matcher | Differential agreement with an independent reference executor |
| P2 | OWL-Time bridge | Guarded mappings and semantic round-trip fixtures |
| P2 | Core cleanup / precedes release | Downstream compatibility and OWL profile checks, updated regression expectations |
| R2 | Directed patient neighbours / all-pairs jobs | Exhaustive subset comparison; retrieval and verification completeness reported separately |

The most useful next code change is the separate exact-interval adapter, not a wholesale core redesign. It will expose which temporal conventions need ontology-level support and which are appropriately part of a profile. Expand the existing SULO test-harness suite with these cases rather than replacing its current coverage.

Benchmark exact arrays, interval joins, uncertain constraint solving and full graph traversal separately. Report ingestion/compilation cost, p50/p95 query latency, memory, candidate reduction, evidence-construction cost, snapshot rebuild cost, and agreement with the reference. Vary patients, events per patient, ontology expansion size, interval concurrency and uncertainty dependencies. No production performance measurements were made in this review.

## 16. Verification boundary and required evidence

The repository includes the [v2.4 profile report](../verification/v24-pro-solid-report.json) and [reference-oracle report](../verification/reference-report.json). They record 42 profile tests, 16 matcher cases and seven property checks for the bounded contracts. The [SULO pin](../ontology/sulo-pin.json) identifies the vendored core used by that adapter.

Those reports do not certify the extensions recommended here. Before activating a new temporal profile, add independent positive and negative cases for missing endpoints, duplicate scalar values, merged boundary classes, multiple clocks, reversed intervals, duration disagreement, temporal coincidence with distinct descriptor identity, transitive unit propagation, shared-anchor correlation, jointly impossible query atoms, point-variable identity, fixed-witness quantification and correlated relaxation cost. Check the interval relation partition and its inverses on a finite grid as a supplemental regression exercise.

Full OWL DL consistency, rustDL operation coverage, general constraint-solver correctness, clinical ETL validation and production performance need their own gates. RDFLib parsing and SHACL conformance do not establish those capabilities. This documentation update adds no executable interval adapter, temporal solver or new benchmark result.

## 17. Recommended decisions for project review

The recommended defaults are:

1. A temporal individual is an information object whose identity can be contextual; coordinate coincidence is evaluated separately.
2. Retain disjoint start/end descriptor classes and functional hasValue.
3. Use typed direct parts for new scalar fields; compile legacy asserted hasPart fields under their declared profile.
4. Keep the current point-anchor adapter and introduce an explicit interval profile.
5. Preserve source temporal variables and correlations; never narrow uncertainty with the query when testing certainty.
6. Keep occurrence, validity, recording, availability and transaction history separate.
7. Compile semantic and temporal information into immutable indexes with source bindings and a query-preservation contract.
8. Use a dedicated temporal evaluator with direct arithmetic and a bounded STN fragment; retain rustDL for certified ontology operations.
9. Treat richer OWL-Time graphs as a bridge view, preserving the canonical SULO vocabulary.
10. Advance core transitivity or datatype changes through separate regression-tested releases.

These decisions preserve SULO's minimal vocabulary while making the temporal knowledge graph's meaning precise enough for efficient, explainable and verifiable matching.
