# Reviewed record mappings

Status: executable `reviewed-record-mappings-1.0` compiler and synthetic mixed-query integration. Clinical terminology mappings and pressure-inspector integration remain pending.

The compiler converts an explicitly accepted, versioned mapping set into the bounded semantic policy used by the checked Rust backend. It preserves separate source-claim acceptance and the existing temporal, measurement and PRO role/bearer checks.

## Input contract

All documents use the closed [schema](../schemas/reviewed-record-mappings.schema.json).

| Document | Required content | Binding |
|---|---|---|
| `catalogue.json` | Dataset/release, source codes, labels and application source record classes | Canonical SHA-256 in mappings |
| `terminology.json` | System/version, target codes, concept IRIs and distinct application record-query classes | Canonical SHA-256 in mappings |
| `mappings.json` | One directional proposal per selected catalogue code, target code and rationale | Whole document hash in review; row hash in each decision |
| `review.json` | Explicit accept/withdraw decisions, reviewer metadata, reason and linear supersession | Latest decision for every mapping must accept |

Canonical hashes use `patterns.claim_rdf.digest`, which hashes canonical JSON rather than file bytes. A change to the catalogue, terminology or proposals requires corresponding new review bindings. Review changes produce a different compiled policy identity. Source policies must explicitly bind that exact policy; the adapter never updates their acceptance decisions or hashes automatically.

A pending or withdrawn mapping returns `BLOCKED_MAPPING_REVIEW`, with no semantic policy and no query execution. Invalid hashes, duplicate identifiers, incomplete catalogue coverage, unsupported relations and invalid review chains raise a profile error. Compilation is atomic for the selected catalogue: there is no silently reduced cohort.

## Meaning of a rule

Each accepted row generates only:

`SourceRecordClass ⊑ TargetRecordQueryClass`

The target concept IRI remains mapping evidence. It is not used to type a process, person or ingredient. The application wrapper means that accepted recorded content belongs to the reviewed query category. It supplies no occurrence, causality or treatment-initiation inference. Wrapper classes are separate from both the source classes and concept IRIs; reserved SULO/W3C class namespaces are rejected.

Several source codes can imply one target record-query class. Each source code has exactly one target in this bounded profile. Equivalence, reverse implications, `sameAs`, imported ontologies, hierarchy expansion, many-to-many mappings and arbitrary OWL are unsupported. No SULO axiom changes are made.

The execution adapter requires exactly one catalogue source-class fact per interval event and accepts only event-class semantic facts in these source bundles. Its treatment selector must be a target record-query class. Measurement selection remains item-specific; the adapter does not introduce terminology grouping for measurements.

## Run the example

Install the repository's Python and Rust semantic dependencies as described in [validation](validation.md), then run from the repository root:

```bash
python -m patterns.reviewed_record_mappings --output /tmp/reviewed-record-policy.json
python -m patterns.verify_reviewed_record_mappings
python -m unittest patterns.test_reviewed_record_mappings
```

The compiler defaults to the four documents in [the example pack](../examples/reviewed-record-mappings/). Supply `--catalogue`, `--terminology`, `--mappings` and `--review` for another pack. It writes a compiled report: the embedded `semantic_policy` is the matcher input. Exit 0 means ready; exit 2 means blocked or invalid. A valid blocked run replaces output with an explicit blocked report. Invalid input preserves previous output; always inspect the exit code. Input paths cannot be overwritten.

The Python `execute(...)` adapter accepts those four documents followed by the existing interval store/policy, measurement store/policy, alignment and mixed query. Source policies remain caller-supplied. The synthetic fixture has explicitly authored source policies for both mapped and no-rule control runs; this is test-data authoring, not propagation of clinical acceptance.

The [reproducible report](../verification/reviewed-record-mappings-report.json) shows:

- Authored codes `item-a` and `item-b` both imply the authored `input-family` record selector.
- The actual Rust-backed mixed query returns synthetic patients P1, P2 and P3 as certain and possible. The selector is derived, never asserted in their source facts.
- The separate no-rule control returns no matching patients.
- Treatment PRO role and bearer witnesses survive; P2 remains eligible despite missing optional follow-up.

The report binds all fixture files, compiler/schema/core artifacts, verifier and query context. Eighteen tests cover review lifecycle, stale bindings, concept/record separation, atomic blocking, source acceptance, CLI behavior, the pending clinical worksheet and report reproduction.

## Clinical handoff

The [pending worksheet](../data/clinical-terminology-review.json) identifies input item 221906 and three separate pressure items from the existing candidate plan, bound to the pinned demo 2.2 dictionary hash. Target vocabulary, release, codes, evidence and decisions are deliberately unfilled. Dictionary labels alone do not establish clinical equivalence. No real SNOMED, RxNorm or LOINC mapping has been accepted or executed.

A domain reviewer should supply exact target definitions and evidence, assess all worksheet questions, and record the supported one-way implication or explain why extra context is needed. Put the resulting four-document pack under `data/terminology/<reviewed-release>/` using the filenames above. Keep pending worksheets outside executable packs. The source catalogue should contain only the selected input codes admitted by this adapter; the three pressure rows require a subsequent measurement-mapping extension before they can be executed. Preserve their separate strata meanwhile.

[The source catalogue adapter](source-record-catalogue.md) now reproduces catalogue classes, supplied interval claims and recorded clock origins from pinned source extraction. It includes an extracted public-demo catalogue and synthetic mapped-query SQL comparison. The remaining follow-up is to obtain clinical mapping review, add any required qualified mappings, and explicitly bind source-acceptance policies to the compiled semantic policy. Then integrate with reviewed batch preparation and compare complete memberships, anchor evidence and SQL reconciliation before exposing a terminology selector in the pressure UI. No existing live result or clinical review declaration changes in this increment.

## Limits

The compiler checks supplied document consistency and decision lifecycle, not reviewer identity, clinical correctness, terminology publisher authenticity, source-catalogue fidelity or historical availability. Those verification flags remain false even for a syntactically accepted pack. A mutable JSON journal is not an authenticated audit log or bitemporal replay engine.

Each document is limited to 256 KiB; catalogues, terms and mappings to 16 entries each; journals to 64 decisions. Existing downstream semantic and event limits still apply, including classes added by the runtime. A ready mapping policy does not guarantee that an arbitrarily large query fits the matcher. This increment adds no large-terminology performance claim and does not reduce cold source preparation time.
