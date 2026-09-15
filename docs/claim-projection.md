# Structured claims and controlled assertion projection

**Status:** executable bounded `claim-projection-1.0`, with a separate claim-description RDF contract and explicit caller-supplied acceptance policy. This implements the first prototype of [D2](sulo-development/record-and-occurrence.md). It adds application classes and individuals, with no new object or datatype properties and no change to pinned SULO.

The synthetic example describes an infusion and a specimen collection. Its accepted semantic facts support an antibiotic-administration classification through a connected drug-role/bearer witness, followed by the existing bounded temporal query. The claim store also retains an unaccepted description. These are constructed examples, not clinical approvals or a MIMIC mapping.

## Run

```sh
python -m pip install -r patterns/requirements-semantic.lock.txt
python -m patterns.claim_projection
python -m patterns.test_claim_projection
```

The committed [synthetic verification report](../verification/claim-projection-report.json) records the example and its artifact hashes. The default run returns `READY`, accepts `admin_classified` and `collection_original`, and finds P1 certain under the selected synthetic assertions. `admin` remains pending. The result is written atomically to `verification/claim-projection-run/result.json` and contains both graphs, selection decisions, source/semantic support, the checked reasoner run, and the claim-isolation certificate.

Inputs are [store.json](../examples/claim-projection/store.json), [policy.json](../examples/claim-projection/policy.json), [semantic-policy.json](../examples/claim-projection/semantic-policy.json), and [query.json](../examples/claim-projection/query.json). Override them with `--store`, `--policy`, `--semantic-policy`, and `--query`. Use `--graph claims.ttl` instead of `--store` to execute from the closed RDF encoding. The RDF recovered content must match the store hash in the policy. `--output` selects the result file.

## Describing a claim does not assert its content

Every instance in the claim graph belongs to a class below `sulo:InformationObject`. A claimed `infusion` is a string value in an event description, not an `rdf:type bt:Infusion` assertion. A claimed `sulo:hasParticipant` relation is a string-valued predicate description, not an edge using that predicate.

The encoding is a structured information tree, with this fixed position vocabulary:

| Component | Encoding |
|---|---|
| Object description | `cp:ObjectDescription` with `sulo:hasDirectPart` field bindings |
| Field position | A typed binding such as `cp:Field_subject`, `cp:Field_object`, `cp:Field_property_iri`, `cp:Field_start_var` or `cp:Field_patient_id` |
| Field content | The binding `sulo:refersTo` its value/description node |
| Ordered array | `cp:ArrayDescription` with item bindings, each referring to content and carrying a separate index datum |
| Scalar content | `cp:StringDatum` or `cp:IntegerDatum`, with exactly one `sulo:hasValue` literal |
| Document identity | A `cp:Document` root under the canonical document SHA-256, with deterministic descendant identities |

These bindings preserve the subject/object positions of the supported semantic statements and every temporal row field. They are not a universal proposition language. The [schema](../schemas/claim-store.schema.json) and [decoder](../patterns/claim_rdf.py) define their supported interpretation. Identifiers refer to declared content positions during projection; the decoder never dereferences a string as a live ontology term or loads an import.

The class module is [claim-description-profile.ttl](../ontology/claim-description-profile.ttl). Its new terms are **classes**, including field-position classes. Instances use only `rdf:type`, `sulo:hasDirectPart`, `sulo:refersTo` and `sulo:hasValue`. Literal strings/integers preserve source content; a date string is not automatically a SULO TimeInstant, and a numeric bound is not a Duration.

The closed reader checks named nodes, scalar datatypes, unique positions, contiguous array indices, absence of cycles/aliasing, complete graph consumption and regenerated content-addressed identities. Extra occurrence edges, ontology axioms, anonymous nodes, altered literals under old identities and duplicate field values fail validation. It accepts the canonical encoding, not arbitrary equivalent RDF graphs.

## What the isolation certificate establishes

On every run, [claim_isolation.py](../patterns/claim_isolation.py) checks a constructive interpretation of the claim graph together with the complete pinned SULO ontology and the claim class module. The SULO file's reviewed hash is mandatory; the claim module must contain exactly the declared information-object subclasses. Unreviewed changes or imports block the claim.

The interpretation is deliberately simple:

- The object domain consists of all claim-description instance nodes. Every node is an InformationObject, Feature and Object. Each application class contains exactly its explicitly typed nodes.
- Process, Time, Quantity, Unit, Role and the other remaining SULO named classes have empty extensions.
- `hasPart`, `isPartOf`, `contains` and `isIn` are universal over this domain. This satisfies their reflexive/transitive axioms and the information-object parthood restrictions.
- `hasFeature` and `isFeatureOf` are identity relations, supplying each Feature's required bearer. No clinical bearer or process is thereby asserted.
- Direct parthood and reference relations use the encoded edges and their inverses; `hasValue` uses the emitted scalar literals. Participation, temporal and precedence relations are empty.

The checker evaluates the pinned subclass restrictions, domains/ranges, property hierarchy, inverses, property characteristics, disjointness and PRO chain, plus every instance assertion. The default fixture checks **134 logical axioms and 1,164 instance triples over 483 nodes**. It reports `VERIFIED_EMPTY_PROCESS_MODEL`.

A satisfying model with no processes establishes that the description graph does **not entail the existence of a process**. This is stronger evidence than simply searching for the absence of a Process type triple. The universal parthood relation is a permissible countermodel choice, not the intended operational tree structure or a claim about real information objects.

This is a checker for one fixed family of models, not a general OWL satisfiability engine. It is limited to the exact pinned closure and the emitted string/integer description encoding. It does not prove that the accepted occurrence view is consistent with full SULO, nor that a union with arbitrary external axioms remains safe. Adding such axioms requires new analysis.

## Explicit acceptance and replacement

The store contains up to 32 immutable claim descriptions. Each has a dataset-scoped identity, patient/episode scope, source and record identifiers, a supplied source hash, and a bundle of temporal rows and semantic facts. Source hashes are caller declarations; the prototype does not verify original hospital records or clinical truth.

The acceptance policy binds the exact store and semantic-policy hashes. Every decision binds a claim hash and includes an action and reason. Supported actions are `accept`, `reject` and `withdraw`; absence of a decision leaves a claim pending.

Decisions form explicit single-successor chains for one source-record identity and patient/episode scope. The final decision in each chain controls selection. An accepted successor can refer to a new claim revision. Rejecting or withdrawing a successor does not reactivate an ancestor. Withdrawal refers to the same claim as its immediate parent. Forks, cycles, multiple roots for one source record, cross-source revisions and stale hashes fail validation.

This is an explicit **policy revision order**, not a source-availability timeline. There is no inferred decision timestamp, source-as-known mode, digital-signature verification or automatic clinical approval. A caller can declare a policy; its authority and evidence remain their responsibility.

Independent accepted claims can support identical complete rows. Their provenance is retained separately. Conflicting accepted versions of the same row identifier block the entire projection. All rows in an accepted bundle move together; constraints and semantic dependencies cannot be cherry-picked. A dependent semantic fact whose event is no longer selected blocks rather than manufacturing an event. Deleting a last support rebuilds the view and downstream reasoning from the surviving claims.

## The accepted analysis view

Selected temporal rows enter the existing offset-clock bounded profile. Selected semantic facts enter the existing restricted Horn module through the joint evidence compiler. The existing rustDL/finite-checker gate runs before releasing an accepted graph or a match. Patient binding retains Process → PatientRole → Person, and temporal certainty retains one fixed named binding across feasible source timelines.

The result's accepted graph contains the projected named assertions. Rules and inferred class-support evidence are retained in the semantic run's exact OFN and reference derivations. The result also retains row-level claim/decision support and semantic axiom support. The context binds the complete policy, query, selected source and implementation artifacts through this result and its nested execution contexts. All selected claims jointly support the view; this prototype does not claim a minimal justification for every generated RDF triple.

An accepted assertion is an ordinary assertion admitted **for the specified analysis**. Acceptance is not independently verified clinical truth. Read `clinical_mapping_verified: false`, `clinical_knowledge_status: UNKNOWN`, and `accepted_view_full_owl_verified: false` accordingly. The description graph and accepted view remain separate result components; consumers should retain the result context and evaluate one selected view at a time. This is not contextual OWL semantics for arbitrary unions of views.

| Status | Result |
|---|---|
| `READY` | Checked assertion view and temporal answer available |
| `EMPTY_ACCEPTED_VIEW` | No selected content; no occurrence graph or clinical-absence answer |
| `BLOCKED_ACCEPTED_CONFLICT` | Conflicting supported row content; no accepted graph or match |
| `INVALID_ACCEPTED_VIEW` | Missing dependencies, invalid scope, unsupported projection or resource bound; detailed reason retained |
| `INCONSISTENT_ACCEPTED_TIME` | Source constraints are infeasible; negative-cycle evidence retained |
| `INCONSISTENT_ONTOLOGY` / `UNRESOLVED_SEMANTICS` | Existing semantic gate blocks release; diagnostic input remains inspectable |

Malformed stores, policies, RDF or queries fail before replacing an existing result. Completed and blocked results replace the output atomically. Only `READY` and `EMPTY_ACCEPTED_VIEW` exit 0; other statuses exit 2. Consumers must check status before interpreting an old output file.

## Bounds, verification and remaining work

Limits include 256 KiB canonical store content, 32 claims, 512 total bundle rows, 128 decisions, 30 accepted events, 20,000 candidate combinations (conservative event-count power bound), and the existing semantic fragment caps. RDF has explicit size/depth bounds. Exceeding a limit never silently truncates the analysis. The claim store permits multiple descriptions that conflict; only the accepted subset must compose into a consistent supported view.

The **45 tests** cover acceptance, corrections, withdrawal, independent supports, scope isolation, conflicts, stale decisions, invalid dependencies, RDF round trips and injection, checked model construction, backend failure, resource gates and atomic CLI output. Fifty seeded decision-chain scenarios independently compare selected claims with the last-decision rule. Existing Rust, joint-selection and temporal suites remain regression gates.

This completes a bounded prototype for information-object claim descriptions and policy-controlled projection. The separate [patient-local claim extension](local-claim-projection.md) now supports local clocks and recorded-segment claims. The original offset profile does not migrate the MIMIC record pipeline, add measurement timestamps, implement clinical conflict adjudication or close the original 18 full formal mapping obligations. Those are separately versioned extensions. Clinical mapping review and the broader upstream pattern adoption remain open.
