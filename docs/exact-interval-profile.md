# Exact occurrence intervals: executable profile 1.0

**Status:** Executable synthetic adapter and pairwise temporal evaluator, `exact-interval-1.0`. This is a separate profile alongside the [v2.4 point-anchor contract](../addenda/specification-2.4.md). It uses the same pinned SULO 0.2.14 core and Python dependencies. It introduces classes and individuals, with no new object or datatype properties.

## Run the complete example

Use Python 3.12 and the dependencies in `patterns/requirements.lock.txt`, as described in the repository README:

```sh
python -m patterns.exact_intervals
python -m patterns.test_exact_intervals
```

The default inputs are [source rows](../examples/exact-interval/source-rows.json), a [snapshot manifest](../examples/exact-interval/manifest.json), and [comparison requests](../examples/exact-interval/queries.json). All data are constructed. The source adapter demonstrates process timing and patient participation; it is not a clinical infusion, medication, specimen, MIMIC, or FHIR mapping.

The pipeline writes four files under `verification/exact-interval-run/`:

| File | Contents |
|---|---|
| `graph.ttl` | Canonical named PRO/SOLID instances, preserving literal spelling |
| `intervals.json` | Normalized exact intervals and identifiers for their evidence |
| `evidence.json` | Source, role, endpoint, duration, clock, snapshot, and implementation bindings |
| `comparisons.json` | Requested temporal comparisons, statuses, arithmetic rules, and evidence references |

To validate and evaluate the [committed graph](../examples/exact-interval/graph.ttl) directly:

```sh
python -m patterns.exact_intervals --graph examples/exact-interval/graph.ttl
```

The test suite checks that the source and RDF entry points produce identical interval records and evidence. Its report is [exact-interval-report.json](../verification/exact-interval-report.json). Invalid profile input exits with code 2 and an `INVALID_INPUT` diagnostic. Consumers must check the exit code; an unsuccessful invocation does not replace existing output files.

## Representation and validation

The [class module](../ontology/exact-interval-profile.ttl), [SHACL shapes](../ontology/exact-interval-shapes.ttl), and [adapter](../patterns/exact_intervals.py) jointly define this profile. The module is loaded locally alongside the existing PRO/SOLID classes and hashed SULO core. There are no network imports.

| Information | Representation and explicit-data requirement |
|---|---|
| Process | An `ei:IntervalProcess`, with exactly one `atTime` occurrence interval |
| Patient participation | Process → `hasParticipant` → `ex:PatientRole` → `isFeatureOf` → `ex:Person` |
| Patient identity | Person → `hasFeature` → `ex:PatientIdentifier` → `hasValue` string |
| Occurrence interval | `ei:ExactOccurrenceInterval`, with distinct typed start/end direct parts and no scalar `hasValue` |
| Boundaries | `ei:ExactStartTime` / `ei:ExactEndTime`, each with one `xsd:dateTimeStamp` value and a direct Second unit |
| Optional elapsed duration | A direct `ei:ElapsedDuration` part with one decimal value and one supported direct unit |
| Clock | Interval → `hasDirectPart` → `ei:ClockBinding` → `refersTo` → `ei:TemporalReferenceSystem` |
| Source record | `ex:SourceRecord` → `refersTo` → process, with typed direct parts for event/record/episode identifiers, source key/location/hash, and performed status |

All application instance literals occur through `sulo:hasValue`. The `ei:` namespace is `https://example.org/trajectory/interval/`; the reused `ex:` classes are from `https://example.org/trajectory/toy/`. JSON keys are execution/source fields, not new RDF predicates.

Validation applies to a separate graph containing named subclass consequences, feature inverses, and the existing PRO participation derivation. It performs no general OWL consistency check or existential witness generation. SHACL runs without additional inference. The adapter admits only named individuals, declared classes, and its documented set of SULO predicates. It rejects mixed point/interval process inputs and requires explicit, unambiguous selected fields. The input should not contain a separately expanded transitive `hasPart` view.

Each projected process needs a distinct patient role, source record, interval, and boundary descriptors. Generic participation alone does not establish the patient role. Different people cannot share the same patient identifier in one projection. Temporal contact compares normalized positions while retaining distinct boundary identities. Direct unit lookup on the selected scalar avoids confusing endpoint seconds with duration minutes.

Optional duration means that an explicit recorded duration datum is not required. SULO's existential duration restriction still applies; an omitted record does not assert the absence of duration.

The source builder accepts the demonstrated `infusion` and `specimen_collection` labels and `performed` status. Planned, prescribed, not-given, uncertain, date-only, and incomplete interval records require other profiles. Rejection is an ingestion failure, not a negative clinical match.

## Clocks, normalization, and precision

Every clock resource has four typed direct parts: identifier, origin, scope, and policy. The origin is an explicitly zoned timestamp with a Second unit. Clock identifiers must be unique within a graph. The only implemented policy is `offset-datetime-microseconds-v1`:

- Convert explicit-offset timestamps to integer microseconds relative to the declared origin using exact integer arithmetic.
- Admit at most six fractional second digits. Reject missing/unknown offsets, invalid dates/offsets, leap-second literals, and unsupported precision.
- Require `start_us < end_us`. A point anchor is not a zero-duration proper interval.
- Support elapsed durations in seconds, minutes, or hours. Decimal-to-microsecond conversion is exact, with no floating-point rounding or calendar-unit assumptions.
- If duration is recorded, require exact agreement with endpoint subtraction. Otherwise compute elapsed duration in the execution record without creating a source duration datum.
- Check signed 64-bit bounds on normalized coordinates and calculated differences.

Clock scope is either `global` or `patient:<patient_id>`. A patient-scoped clock can only describe that patient's processes. Explicit timezone offsets do not independently establish cross-patient comparability for deidentified clinical dates. This policy describes fixed-offset coordinate arithmetic; it is not a leap-second-aware physical timescale or a calendar/relative-time normalizer.

The pairwise evaluator requires the same graph/snapshot/profile context, the same patient bearer and identifier, the same episode identifier, and the same clock resource and descriptor. It returns `INCOMPARABLE` when these conditions differ. It does not infer mappings between clock resources, even when their coordinates or labels look equivalent.

## Temporal operations and worked results

Let A and B be proper intervals `[sA,eA)` and `[sB,eB)` under one compatible clock:

| Requested operator | Satisfied exactly when |
|---|---|
| `before` | `eA < sB` |
| `meets` | `eA == sB` |
| `overlaps` | `sA < sB < eA < eB` (directional Allen overlap) |
| `gap` | `min_gap_us <= sB - eA <= max_gap_us`, with explicit integer bounds `0 <= min <= max` |

Gap bounds are inclusive; zero admits contact. Overlapping intervals have a negative signed endpoint gap and do not satisfy a nonnegative-gap request. A computed gap is a signed difference, not a SULO Duration assertion.

The example contains A at 14:00–14:30, B at 14:30–14:35 (its start is written as 15:30+01:00), C at 14:20–14:40, and D at 14:45–15:00. The common date is 2 September 2026 on the synthetic patient clock.

| Request | Expected outcome |
|---|---|
| A meets B | Satisfied; distinct endpoint descriptors coincide |
| A before B | Not satisfied; the intervals meet |
| A before D | Satisfied; signed gap is 15 minutes |
| A overlaps C | Satisfied |
| C overlaps A | Not satisfied; the directional relation is `overlapped_by` |
| B → D gap exactly 10 minutes | Satisfied |
| A → B gap exactly zero | Satisfied |
| A → D gap at most 10 minutes | Not satisfied |
| A → C nonnegative gap | Not satisfied |

For comparable inputs the evaluator also reports their single basic Allen relation, including containment, equality, and inverse relations. The request interface currently exposes only the four operators above. `SATISFIED` and `NOT_SATISFIED` describe one exact temporal constraint on the selected recorded pair; they do not establish cohort membership or clinical absence. Unknown operators, events, or invalid gap bounds are errors.

`precedes`, `directlyPrecedes`, and `immediatelyPrecedes` are not materialized into SULO by this evaluator. The [precedence proposal](decisions/temporal-precedence.md) remains a separate upstream decision.

## Evidence and execution interface

Python callers use `build_graph(source)`, `project(graph, manifest)`, and `evaluate(left, right, operator, ...)` or `run_queries(intervals, requests)`. The evaluator consumes immutable `ExactInterval` records returned by the validated projection. Serialized records are outputs, not a new trusted JSON ingestion API or a replacement for the existing v2.0 Event DTO schema.

Each result identifies both input evidence records and includes normalized endpoints. Comparable results include the arithmetic rule, actual relation, and signed gap. Incomparable results provide reason codes and no computed relation/gap. Evidence retains source record identifiers and hashes, original endpoint spellings, selected units, optional recorded duration, clock binding, and PRO process/role/bearer witnesses.

The execution context fingerprints the instance graph, selected dataset/snapshot, normalization policy, and relevant implementation/schema bytes. Derived evidence identifiers include that context. Graph order does not affect identifiers; changes to evidence, clock interpretation, snapshot, or implementation do. The graph digest is specific to this all-named profile and is not a general RDF dataset canonicalization algorithm. On the RDF input route, source-row hashes are preserved declarations; the original rows are unavailable for rehashing.

The writer emits explicit Turtle statements with quoted typed literals. This avoids numeric shorthand changing source decimal spelling (for example, `"30"` to `"30.0"`) and thereby changing evidence across the graph round trip. Other serializers may rewrite lexical forms.

Projection reads the graph once per execution context. Requests resolve event identifiers through an in-memory map and compare normalized endpoints without further graph traversal. No all-pairs closure, optimized cohort joins, approximate retrieval, semantic relaxation, interval AST extension, or uncertainty solver is implemented here.

## Verification and next implementation boundary

The separate suite checks the nine independently specified example results, source/RDF/CLI equivalence, PRO and source evidence, clock/snapshot incompatibility, exact duration arithmetic, and malformed inputs. An independent table of weak endpoint orders checks all 441 pairs of proper intervals on a seven-point grid against the 13 Allen relations.

CI runs this suite alongside the unchanged 42-test PRO/SOLID suite and reference oracle's 16 cases/seven property checks. The v2.4 release manifest and pinned ontology are preserved.

The separate [interval cohort matcher](interval-cohort-matching.md) now defines versioned interval slots and joins, retains role/evidence bindings through candidate search, and checks indexed execution against an exhaustive reference matcher. Bounded uncertainty requires a separate joint-feasibility and certain/possible-answer contract. Passing this exact fixture suite does not establish either capability, full OWL reasoning, or production readiness.
