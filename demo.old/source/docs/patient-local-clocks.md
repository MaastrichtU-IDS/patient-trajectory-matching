# Patient-local clock bridge

**Implemented:** `patient-local-interval-1.0` source, `patient-local-rdf-1.0` RDF, and `patient-local-query-1.0` query profiles. The bridge converts explicitly supplied local datetime bounds into signed integer microseconds relative to a declared fixed origin. It reuses the bounded temporal compiler, PRO/SOLID representation, closed RDF reader, and fixed-witness matcher. It does not infer a timezone or the clinical precision of a recorded timestamp.

## Run

```sh
python -m patterns.patient_local
python -m patterns.patient_local --graph verification/patient-local-run/graph.ttl
python -m patterns.test_patient_local
```

The synthetic [source](../examples/patient-local/source.json) and [query](../examples/patient-local/query.json) yield P1 certain, P2 possible-only, P3 no recorded match, and P4 incomparable. The CLI writes `graph.ttl` and `result.json`. Invalid input or an inconsistent source exits 2 before replacing outputs; read the exit code before using an existing result. No MIMIC patient rows are involved.

The 27 tests include 24 generated small-domain scenarios, each checked for before, meets, directional overlaps, and a bounded gap against independent finite-world enumeration. The scenarios are nested checks, not additional top-level tests. Existing source and RDF suites also exercise the shared code.

## Source and normalization contract

The [schema](../schemas/patient-local.schema.json) follows the existing bounded source layout, with two deliberate changes:

| Component | Required local fields and interpretation |
|---|---|
| Clock | `clock_id`, `origin`, `scope`, `policy`, `origin_source_key` |
| Clock scope | `patient:<patient_id>`; global local clocks are not admitted |
| Clock policy | Exactly `patient-local-calendar-microseconds-v1` |
| Origin | Fixed naive datetime with `T` separator; a coordinate reference, not an inferred first-ever clinical event |
| Variable bounds | `local_lower` and `local_upper`, both supplied explicitly, plus the existing variable, patient, episode, clock and source identifiers |
| Events and constraints | Existing named event/record identities, patient roles, shared variable references and difference constraints |

A local datetime has a complete date and time, seconds, and at most six fractional digits. Bound labels accept either the MIMIC-style space or `T` separator. A supplied offset, timezone suffix, invalid date, leap second, missing bound, or greater precision is rejected. The original spelling survives normalization and RDF export.

`normalize(source)` retains the labels and derives `lower_us` and `upper_us`; it neither rounds nor adds uncertainty. `prepare(source)` validates the complete network and builds a snapshot. The source API accepts label bounds; the additional numeric fields in a prepared snapshot are its derived representation, not alternate caller inputs.

The scalar utility is useful before a clinical mapping is available:

```python
from patterns.patient_local import coordinate
coordinate('2150-01-01 10:00:00', '2150-01-01T00:00:00')
# 36000000000 local calendar microseconds
```

That calculation locates a recorded label. It does not justify setting both occurrence bounds to that value. A source adapter must supply a reviewed precision/uncertainty policy and retain its evidence in the source keys. No default one-second or one-minute uncertainty window is assumed.

An uncertain clinical anchor belongs in a shared variable and constraints relating other endpoints to it. It must not be replaced by an exact clock origin. The origin is a fixed coordinate reference; its source key records the declaration. Origin keys and other provenance declarations are retained, not independently certified as clinical evidence.

## Reasoning and clock isolation

All variables must use a clock scoped to their patient. A source edge requires the same patient, episode and clock. Query bindings stay inside patient episodes. Distinct clocks remain incomparable even when origins and displayed dates agree; the bridge does not infer clock equivalence or merge snapshots. Clock identifiers are local to the enclosing dataset/snapshot context, so concatenating graphs is not a supported identity-resolution operation.

Rebasing a fixed origin changes coordinates without changing within-clock relations. Shifting every label and its origin by the same number of calendar days likewise preserves coordinates. Neither operation synchronizes different patients.

The temporal kernel retains proper intervals, inclusive supplied variable bounds, shared-variable correlations, joint feasibility, and the fixed-witness quantifier order: one named binding must satisfy the query throughout all feasible source timelines for certainty. Source inconsistency remains a separate failure; unknown clock comparability never becomes absence. Before is strict, meets requires endpoint equality, and overlaps is directional. Gap queries retain the existing nonnegative inclusive bounds on `start(B) - end(A)`.

**Gap values in this profile are local calendar-coordinate differences.** They are not verified physical elapsed durations. The calculation uses uniform 24-hour calendar-day arithmetic and applies no real-world DST rules to shifted dates. Results and their hashed query context identify `bounded-patient-local-calendar-microseconds`, `gap_semantics: local_calendar_coordinate_difference`, and `physical_elapsed_time_verified: false`. A physical-time interpretation needs a separate justified source clock mapping.

The distinct query profile prevents quietly applying the existing offset-time contract. Original JSON/RDF loaders reject local profiles, and the original bounded and Rust-semantic query entry points reject local snapshots. Named-class selection remains available. The separate `execute_semantic` entry point now supports the [checked local-clock semantic extension](mimic-record-query.md), including inferred item groups and role-scoped premises. Joint availability/revision replay remains outside the local-clock profile.

## RDF and evidence

The graph uses the existing patient-role chain and SULO predicates. It adds three information-object classes and no object or datatype properties:

| Class | Value |
|---|---|
| `pl:OriginSourceKey` | Source key for the fixed-origin declaration |
| `pl:LocalLowerLexical` | Original lower-bound string |
| `pl:LocalUpperLexical` | Original upper-bound string |

Clock origins carry naive `xsd:dateTime` values, not `xsd:dateTimeStamp`. Numeric bounds remain integer-microsecond information objects. Every scalar uses `sulo:hasValue`; a process or person never carries a literal directly. No sampled exact occurrence timestamp is asserted for an uncertain endpoint.

RDF ingestion consumes every triple, retains arbitrary named instance IRIs and PRO witnesses, and checks both representations of each bound. A raw label and coordinate that disagree cause `LOCAL_COORDINATE_MISMATCH`; neither overwrites the other. Wrong datatypes, offsets, policies, missing raw labels, missing origin provenance and unexpected triples fail admission. Negative cycles retain RDF source evidence. Contexts bind the local schema, classes, implementation and shared temporal artifacts. As with the original RDF profile, source-hash declarations are preserved without claiming access to the original clinical rows.

## MIMIC handoff and next work

[Inputevents staging](mimic-inputevents-staging.md) can now use the scalar conversion after choosing an explicit patient-local origin. Its status remains `STAGED_NOT_MATCHER_READY`: the bridge does not supply clinical process classes, performed-status mapping, occurrence bounds, or trustworthy source availability/history. The existing aggregate demo report remains an admission result; this PR does not rerun it as a patient cohort.

The [retrospective recorded-source pipeline](mimic-record-query.md) now connects admitted MIMIC records using explicit item-code groups and exact recorded-label semantics. Clinically interpreted occurrence still needs a reviewed item/category/component/status mapping and endpoint-bound policy. That work should connect staged component records to the new local profile while retaining row reconciliation. It must state whether any gap interpretation is merely local-calendar arithmetic or justified physical elapsed time. Full OWL reasoning, the original 18 formal obligations, historical ontology replay, and patient-to-patient similarity remain outside this extension.

## Claim acceptance bridge

The separate [patient-local claim projection](local-claim-projection.md) now preserves these raw-label and clock contracts while adding information-object claim descriptions and explicit hash-bound acceptance policies. It supports both the occurrence and recorded-segment source variants. It does not alter this module's input schemas, infer clinical approval, or implement source-as-known history.
