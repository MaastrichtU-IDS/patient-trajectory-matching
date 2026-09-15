# Version 2.4: executable PRO and SOLID contracts

15 September 2026. This addendum extends product specification v2.3 and supersedes its initial ontology encoding examples. It leaves the user-approved temporal knowledge graph definition intact and specifies how its entities, roles and literal-bearing assertions are encoded in the first runnable profile.

## Decision

Use PRO for participation in a process and SOLID for literal-bearing information. SOLID means **Single Object Literal Information Datum**. The patterns follow the [SULO paper, sections 4.4.1–4.4.2](https://ceur-ws.org/Vol-4176/foust-7.pdf). The implementation resolves the [SULO ontology](https://w3id.org/sulo/sulo.ttl) from a local, hashed copy whose declared version is 0.2.14; see `ontology/sulo-pin.json`.

The application extension introduces classes and individuals. It declares **no new object properties or datatype properties**. Domain distinctions belong in classes: PatientRole, AdministeredDrugRole, CreatinineResult, SourceRecord, and typed identifier or status data. In instance graphs, the only literal-bearing predicate is `sulo:hasValue`. Ontology annotations and SHACL configuration are separate from instance data and may contain their own literals.

## 1. Composition and meaning

| Requirement | Graph representation | Executed behavior |
|---|---|---|
| Person participating as patient | Process → `hasParticipant` → PatientRole → `isFeatureOf` → Person | Preserve the process, role and bearer binding; project the bearer's identifier. |
| Substance participating in administration | Administration → `hasParticipant` → AdministeredDrugRole → `isFeatureOf` → drug individual | Read the drug individual's clinical class; never type the process itself as DrugA. |
| Measurement result | Measurement → `hasParticipant` → MeasurementResultRole → `isFeatureOf` → result information object | Bind one result to its measurement process. This role class is this project's modeling choice. |
| Measured property and subject | Result → `refersTo` → quality → `isFeatureOf` → person | Require the quality's bearer to be the person in the measurement's patient role. |
| Numeric value and unit | Result → `hasValue` → decimal; result → `hasPart` → Unit | Validate and convert supported units while preserving original values and units. |
| Recorded time anchor | Process → `atTime` → RecordedAnchorTime → `hasValue` → datetime | Normalize the explicitly zoned anchor into integer microseconds relative to the declared origin. |
| Source metadata | SourceRecord → `refersTo` → process; record → `hasPart` → typed metadata datum → `hasValue` → literal | Preserve source key, record identifier and source-row hash. |
| Patient identifier | Person → `hasFeature` → PatientIdentifier → `hasValue` → string | Build the matcher join key without asserting a patient-specific relation. |

A patient's role can differ between processes. The trajectory joins the **same person**, not the same role individual. This adapter requires a distinct role individual per process and exactly one patient-role binding for each projected process. Those are explicit ingestion constraints for this profile; they are not global restrictions on every SULO graph.

The PRO chain can derive generic participation from participation through a feature. Generic participation alone does not establish which role the bearer had. Query evaluation retains the role witness, including when the graph also contains the derived direct participation triple.

SOLID does not mean that every information object must have a literal. A source record can be a composite information object whose parts carry its fields. `hasValue` is functional in SULO: an information object has at most one value under ontology semantics. The adapter additionally requires exactly one explicitly recorded literal for each selected scalar datum. Its RDF-term count is a validation rule, not a complete implementation of OWL datatype equality.

## 2. Worked graph

The following fragment uses the pack's synthetic namespace. The complete, parseable graph is `examples/pro-solid/graph.ttl`.

```turtle
@prefix ex: <https://example.org/trajectory/toy/> .
@prefix sulo: <https://w3id.org/sulo/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

ex:measurement1 a ex:Measurement ;
    sulo:hasParticipant ex:patientRole1, ex:resultRole1 ;
    sulo:atTime ex:time1 .

ex:patientRole1 a ex:PatientRole ; sulo:isFeatureOf ex:person1 .
ex:person1 a ex:Person .

ex:resultRole1 a ex:MeasurementResultRole ; sulo:isFeatureOf ex:result1 .
ex:result1 a ex:CreatinineResult ;
    sulo:hasValue "10.0"^^xsd:decimal ;
    sulo:hasPart ex:MilligramPerLitre ;
    sulo:refersTo ex:quality1 .
ex:quality1 a ex:CreatinineConcentration ; sulo:isFeatureOf ex:person1 .
ex:MilligramPerLitre a sulo:Unit .

ex:time1 a ex:RecordedAnchorTime ;
    sulo:hasValue "2024-01-07T01:00:00+01:00"^^xsd:dateTimeStamp ;
    sulo:hasPart ex:Second .
ex:Second a sulo:Unit .
```

The quality is represented at patient level for this constructed adapter. A specimen-based laboratory adapter must explicitly model the specimen, sampling process and relevant roles, rather than assuming the specimen and patient are the same bearer. That mapping is not implemented here.

Equal observed values do not justify merging observations. Each record, result datum and process retains a distinct identifier in the adapter. Subsequent observations create additional data. A revision to a recorded observation requires the versioning policy specified in v2.3; this runner consumes one already selected snapshot and does not implement revision selection.

## 3. Executable slice

`patterns/pro_solid.py` implements:

1. **Construct:** read the supplied synthetic JSON rows and create an RDF graph. This is a concrete source-adapter example, not a raw MIMIC or FHIR importer.
2. **Derive:** compute named subclass closure, the inverse of `hasFeature`, and the PRO participation chain in a separate graph. Asserted triples remain available separately.
3. **Validate:** execute the supplied SHACL shapes plus procedural checks for instance predicates, supported codes, named upper-class disjointness, metadata, role context, record identity, time and units.
4. **Project:** produce v2.0 Event DTOs and a separate evidence file linking projected rows to process, role, bearer, result, quality, unit and source-record resources.
5. **Match:** run the existing exhaustive three-slot oracle on the projected events. No matcher semantics or relaxation prices are changed by this adapter.

The Event DTO's `patient_id`, `value`, `unit` and other fields are an execution representation. They are not SULO properties. The source graph and binding evidence remain the semantic record. The evidence identifier is a deterministic identifier for an extracted binding, not a new assertion of clinical truth or a complete OWL proof.

Dependencies and setup are in the README. SULO is vendored for offline execution and checked against its SHA-256 digest at load time. `patterns/requirements.lock.txt` records the tested dependency versions. Importing the full ontology does not imply the runner reasons over all its axioms: arbitrary OWL expressions, existential witness generation and complete consistency checking are out of scope.

## 4. Exemplar query and semantic relaxation

The source fixture contains DrugA administration, a baseline creatinine value of 10.0 mg/L, and a follow-up value of 1.30 mg/dL. The baseline is 24 hours before follow-up; administration is seven days before follow-up. The baseline datetime includes `+01:00`; the other timestamps use `Z`.

The matcher asks for the same patient's administration followed by a creatinine increase of at least 0.3 mg/dL within a 48-hour lab window, with administration no more than seven days before follow-up. The normalization yields a baseline of 1.0 mg/dL and an exact, zero-cost match. This is a constructed computational exemplar, not a complete AKI phenotype or evidence of causation.

| Observed drug class | Declared relationship to DrugA | Expected result |
|---|---|---|
| DrugA | Same class | Exact, cost 0 |
| DrugAChild | Subclass of DrugA | Exact, cost 0 |
| DrugB | Explicitly reviewed toy alternative, with no subclass entailment | Relaxed, semantic cost 1 |
| DrugB with relaxation budget 0 | Alternative excluded by policy | No accepted match |

These four cases run in acceptance tests. The existing oracle's JSON toy taxonomy remains its reasoning input; it is not a general OWL classifier. `examples/pro-solid/measurement-bindings.rq` also demonstrates a directly executable SPARQL projection that retains the patient and result role bindings.

## 5. Temporal and value normalization

The implemented unit policy supports decimal concentrations in mg/L and mg/dL, converting mg/L to mg/dL by an exact factor of 0.1. Unknown units, multiple units and time units used as concentration units are rejected. Original numeric lexical values and unit resources remain in evidence. Decimal precision is increased as needed for this conversion.

The time adapter accepts a complete datetime with a known explicit offset and at most six fractional second digits. It rejects timezone-free datetimes, unknown `-00:00` offsets, invalid offsets, leap seconds and unsupported precision. It does not infer UTC from an absent timezone. Source datetime text remains in evidence; the output uses a declared clock and exact integer arithmetic. The serialized fractional digit count is retained in that source text. The Event DTO's `precision` field describes the implemented serialization category, not uncertainty in clinical occurrence time.

An occurrence anchor does not establish that an entire clinical process has zero duration. The first matcher profile uses point anchors for its temporal comparisons. Proper process intervals, uncertain endpoints, event time versus assertion validity, and snapshot/cutoff selection remain specified in the wider product and are not solved by this adapter.

The full product must also represent ages, calendar dates, relative offsets and duration units through typed information objects. A calendar age in years cannot be normalized by assuming every year has a fixed number of seconds. A signed relative offset requires an identified anchor and frame; it should not be typed as SULO Duration when negative. Those inputs must use separately declared adapters and uncertainty policies. This runner rejects unsupported time forms. It does not apply timezone assumptions to MIMIC's deidentified dates or claim calendar comparability between patients.

The fixture manifest supplies query scope, age, snapshot identity, completeness and clock origin as operational inputs. Age derivation from graph data and source-completeness verification are not implemented. A `source_search_complete=false` input produces an unresolved matcher result.

## 6. Constraints and failure semantics

Three levels remain distinct:

- **Ontology axioms:** class meaning, property meaning, disjointness and logical entailment. Defined in the pinned SULO ontology and application class extension.
- **Ingestion/profile constraints:** required explicit role/value/unit/time information and supported encodings. Defined in SHACL and named procedural checks in the adapter.
- **Query constraints:** patient join, ordering, metric gaps, value change and permitted relaxation. Defined in the existing pattern AST and evaluated by the oracle.

A validation failure is reported as `ContractError` and stops projection. It is not returned as “patient does not match.” A valid graph may still yield no accepted trajectory. Missing data in a selected profile, absence of recorded evidence, ontology inconsistency and clinical absence must not be conflated.

The closed profile accepts performed measurements and administrations. Not-given, planned, refused and prescription records need a dedicated information-record model; they are rejected here and never created as completed administrations. This intentionally narrower ingestion contract does not remove the original oracle's independent not-given exclusion check.

## 7. Product and UI integration

The result inspector should present “person as patient in measurement” with the exact role and process available in expanded evidence. Show the normalized value and unit beside the original source value, the recorded datetime beside the normalized offset, and the source-record link. A semantic relaxation explanation must name the changed clinical class and its policy cost. Unit conversion does not incur a semantic relaxation cost.

The canonical graph drives these explanations; the compact Event DTO supports indexing and scoring. New patient similarity, refinement, and UI features must carry these evidence identifiers through their result contracts. This release provides the graph and projection evidence; it does not implement those UI panels.

## 8. Acceptance and remaining implementation work

`python -m patterns.test_pro_solid` runs the bounded acceptance suite, writes `verification/v24-pro-solid-report.json`, and exits nonzero on failure. It covers valid projection and Event-schema compatibility; PRO derivation and role preservation; incorrect or ambiguous roles; prohibited literal placement; missing or multiple values/units; unit conversion; timestamp equivalence and rejection; subject identity; duplicate observation preservation; exact subclass matching; costed semantic relaxation; source evidence; and unsupported statuses. The original 16 oracle cases and seven property checks run separately.

For the 3–5-person team, the next implementation assignments are: ontology/domain mapping and review; source adapters with reconciliation; matcher integration; and evidence-driven UI. With three hackers, combine matcher integration and UI. Any AI-assisted coding must pass these contracts and retain the declared supported profile. This release needs no AI provider credentials.

The canonical new ontology files are `ontology/pro-solid-profile.ttl` and `ontology/pro-solid-shapes.ttl`. Earlier proposal, assertion and shape drafts are archived in `ontology/legacy-2.3/` for historical traceability and are **non-normative**. They must not be loaded with the current profile. Full SULO/temporal reasoning, specimen mapping, raw clinical ETL, bitemporal reconstruction and generalized matching remain implementation work; passing this fixture suite does not establish those capabilities.
