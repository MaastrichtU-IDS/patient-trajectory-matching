# D1. Candidate normative PRO/SOLID pattern catalogue

**Status: proposal.** This specifies the content and acceptance gates of a future catalogue. Existing executable contracts remain authoritative for their own inputs. See the [development proposal](README.md) for evidence and scope.

## A pattern is a contract

Each catalogue entry should contain: a stable identifier/version; purpose and competency questions; required and optional nodes; permitted relations; identity rules; intended assertions; supported entailments; explicit non-entailments; validation rules; reasoning capability requirements; positive and negative examples; source/RDF round-trip expectations; and migration notes.

Separate three responsibilities in every entry:

- **OWL semantics:** what follows from the supplied axioms under the declared entailment regime.
- **Data validation:** what must be explicitly present in an admitted document, including closed-world cardinality and datatype checks.
- **Computation:** arithmetic, temporal feasibility, normalization, source selection and other application operations.

An OWL existential restriction does not require a named filler in the file. A validator that requires a named filler is imposing an additional input contract. Record this distinction rather than describing both as ontology validation.

## Initial entries

| ID | Pattern and intended structure | Acceptance examples |
|---|---|---|
| P01 | Patient participation: process `hasParticipant` patient role; role `isFeatureOf` person | One connected role/bearer witness qualifies; a role on one participant and a person connected elsewhere cannot be combined |
| P02 | Other participation capacities: care provider, administered drug, measurement result | Patient and drug roles remain distinguishable; reasoning cannot change the patient binding |
| P03 | Typed scalar information: an information object carries a literal through `hasValue`; a quantity carries its declared unit through the pattern's existing SULO links | Missing/incompatible units fail the relevant input profile; conversions preserve original value, unit and policy |
| P04 | Source identity and provenance: a source record has typed identifier/hash/location descriptions and refers to its represented content or subject | Row identity is distinct from process identity; hashes identify bytes/context, not truth or historical availability |
| P05 | Temporal descriptions: process `atTime` interval description, with distinct start/end descriptions and explicit coordinate/clock bindings | Coincident endpoints may share a coordinate while retaining different descriptor identities; all joint constraints survive translation |

P01–P05 consolidate [existing executable structures](../../addenda/specification-2.4.md), including [bounded RDF](../bounded-rdf-ingestion.md). The stronger claim representation in [D2](record-and-occurrence.md) now has a [bounded prototype](../claim-projection.md) with executable bindings, round trips and model checks. A [measurement claim profile](../measurement-claims.md) now describes and selects bounded scalar/point records with a separate chartevents importer. Clinical source mappings and mixed point/interval matching remain extensions to be specified and tested.

For P03, pinned SULO declares `hasValue` functional. Separate descriptions are needed for original and normalized values. A profile may require exactly one explicit literal term; that is stricter than OWL functionality, which concerns data values and may identify differently written literals as the same value. Do not place numeric and textual versions of a result indiscriminately on the same node.

For P05, pinned `TimeInstant` restricts `hasValue` to date-time datatypes. The bounded profile therefore places integer coordinates on separate information objects. Do not add an integer `hasValue` directly to a `TimeInstant` and assume compatibility with the full pinned ontology.

## Evidence for adoption

For every entry, publish a small fixture matrix covering valid structure, missing required structure, ambiguous/multiple bindings, incompatible roles/types, and unsupported constructs. Expected outcomes must distinguish validation failure, inconsistency, non-entailment and unknown source evidence. Run both graph checks and the advertised reasoning operation; parsing alone is insufficient.

Retain the existing connected-witness tests in [semantic support](../semantic-support.md). Add measurements with equal values from different processes so neither equality of literals nor shared patient identity merges results. Include source and normalized unit representations with an explicit, reproducible conversion policy.

A proposed catalogue index should point to actual fixture files, commands and reports only after they exist. Its version must pin the SULO dependency and each executable profile. Publishing these documents does not itself satisfy those adoption gates.

## Candidate upstream clarification

SULO documentation should explain the relationship between a small property vocabulary and the richer structures built with it. Provide complete graph examples and query recipes, including the boundaries of generic participation inference. Measure graph size, query length and authoring errors as well as property count.

The existing StartTime/EndTime disjointness deserves an explanatory example: two descriptions can denote the same coordinate without being the same information object. This is an observation about the pinned modelling choice, not a recommendation to identify disjoint classes or to change their axioms without evaluation.
