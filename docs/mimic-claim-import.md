# MIMIC source records as pending claims

`mimic-claim-import-1.0` connects the [inputevents staging adapter](mimic-inputevents-staging.md) to the [patient-local recorded claim store](local-claim-projection.md). It describes admitted records, computes their provenance from the supplied files, and creates an empty acceptance policy. **Every imported claim remains pending.** Importing does not run a temporal query or construct an accepted occurrence graph.

## Run and inspect

```sh
python -m patterns.mimic_claim_import
python -m patterns.test_mimic_claim_import
```

The default uses synthetic CSVs and writes `verification/mimic-claim-import-run/result.json`. For supplied demo 2.2 files, provide the directory and an explicit request:

```sh
python -m patterns.mimic_claim_import \
  --input-dir /path/to/local/mimic-demo-2.2 \
  --request examples/mimic-claim-import/demo-request.json \
  --output verification/mimic-claim-import-run/demo-result.json
```

The [request schema](../schemas/mimic-claim-import.schema.json) fixes retrospective source-record mode and `recorded-label-exact-v1`. Select one to sixteen literal inputevents item codes and either explicit patients or the complete ICU-stay roster. A query is not required. Item selection defines import scope, not clinical eligibility. Unknown selectors and unknown patients fail validation.

Output fields:

| Field | Meaning |
|---|---|
| `summary` | Complete row accounting, selected/described counts, limits and implementation/source hashes |
| `admission` | Original staging ledger, raw input rows, rejection reasons and admitted segments |
| `import_reconciliation` | Exactly one outcome for each input CSV record, with duplicate link and optional claim/store link |
| `episodes` | Every in-scope ICU stay, including empty and blocked stays; each successful nonempty stay has one bounded store |
| `episodes[].claim_provenance` | Claim hash; input, item, stay and origin row hashes/locators; recorded timestamp and unknown availability |
| `episodes[].acceptance_policy` | Hash-bound policy with **no decisions** |
| `episodes[].isolation` | Checked model of the description graph plus the pinned SULO/class modules, with empty Process and Time extensions |
| `semantic_policy` | Item-code classes only, with no rules, drug mappings or clinical classifications |
| `origins` | Minimum admitted start for each requested patient, selected before item filtering and shared across that patient's stays |

The detailed result contains source data. Keep it local; only the aggregate verifier output is intended for this repository. The CLI writes a single atomic JSON bundle. Invalid inputs preserve the previous result; a partially blocked import writes its full ledger and returns exit code 2. A complete pending import returns 0. Neither exit code constitutes a clinical interpretation.

## What each claim describes

One admitted source segment becomes one claim bundle containing two endpoint variables, one `recorded_input_segment` event description and one source-item class description. Shared order/link IDs do not merge components or rate segments. Exact duplicate source rows retain separate ledger entries and point to the original record; they do not create extra claims.

Start/end labels are preserved verbatim as equal local lower/upper bounds. This is exactness of **recorded labels**, not evidence of exact physical occurrence time. No UTC offset, elapsed physical time, missing timestamp, source availability or revision history is invented. `storetime` remains recording/validation evidence; it is not used as an availability cutoff. Clinical knowledge remains `UNKNOWN`.

The original dataset label is retained in the request/context. A deterministic `dataset_` plus canonical-JSON SHA-256 namespace satisfies the narrower claim-store identifier syntax, including when the original label contains periods. Source identity also binds that original dataset label. A source record identifier binds the uncompressed CSV hash and one-based CSV record number; it is a snapshot locator, **not a stable cross-revision clinical identifier**. This adapter does not establish a correction chain between different exports.

Claim `source_sha256` is computed from the input file bytes actually read, including compressed bytes for `.gz`. Provenance separately preserves the uncompressed CSV hash and canonical row hash. The run context binds all three file manifests, the request, the admission implementation and the claim stack. Its hash feeds store snapshot identities: a dictionary or clock-origin dependency change invalidates the previous store-bound acceptance policy even when the event bundle is otherwise unchanged. Dimensions reread for roster/selector construction must match their admission manifests.

Unlike the generic claim-store reader, which receives caller-declared provenance, this importer computes those hashes from files. It does not authenticate the source system or verify the clinical truth of its rows. The generic projection still describes its inputs as caller supplied when a store is later passed to it; retain this import bundle to establish the file-to-claim link.

## SULO description boundary and deliberate acceptance

No ontology extension is added. The existing claim encoding represents all described identifiers and predicates as string values on information objects, using the existing SULO relations and `hasValue`. A source item code becomes a proposed class assertion inside that description; it does not become a live process class assertion during import. No item label is interpreted as a drug or clinical class.

Each emitted store is schema-validated, encoded with the closed local claim vocabulary, and checked against all **137 logical axioms** in the pinned isolation closure. A model with empty Process and Time extensions proves that these descriptions do not entail process or time existence. It does not entail the global absence of processes, certify an accepted view against full SULO, or cover arbitrary ontology unions.

An empty policy passed to `local_claim_projection.execute` yields `EMPTY_ACCEPTED_VIEW`, even when the stored record would satisfy a query if accepted. For a deliberate later analysis:

1. Extract one episode's `store`, `acceptance_policy` and the top-level `semantic_policy`.
2. Review the source evidence and intended record-level interpretation. Add explicit decisions with the claim hash, action, reason and supersession link under the [acceptance contract](claim-projection.md). Changing the semantic policy requires rebinding its hash in the acceptance policy.
3. Supply a `patient-local-semantic-query-1.0` query to the existing local claim projection. Both claim selection and the existing semantic/temporal failure gates apply.

The importer generates no acceptance decisions. Tests use clearly labeled synthetic decisions to verify the end-to-end Rust route and withdrawal behavior. Acceptance selects source descriptions for an analysis assertion view; it does not retroactively verify clinical truth or source history.

To materialize only a description graph locally:

```python
from patterns import claim_rdf

graph = claim_rdf.encode(episode["store"], fields=claim_rdf.LOCAL_FIELDS)
graph.serialize(destination="pending-claims.ttl", format="turtle")
```

The JSON store is the persisted representation; Turtle is reproducibly derived when needed. The importer checks the derived graph without duplicating it in the result bundle.

## Bounds and complete accounting

The admission limits remain 100,000 rows per table and 64 MiB per raw/expanded file. Import requests allow at most 256 ICU stays and 32 claims per stay, with the existing 256 KiB claim document, string, tree and graph limits also enforced.

An oversized stay receives `BLOCKED_RESOURCE_LIMIT`; **none** of its selected claims are exported. Every selected row in that stay receives the same explicit block outcome and reason. Other stays can still produce pending stores, but the summary is `BLOCKED_INCOMPLETE_IMPORT`. No sharding, truncation or automatic acceptance hides that incompleteness. File, schema, roster or isolation-integrity failures invalidate the run entirely.

Complete reconciliation and complete description are separate assertions. The ledger can be complete while some selected stays are blocked. `NO_SELECTED_ADMITTED_RECORDS` means only that the stay has no admitted record in the requested item scope. It is not evidence of clinical absence. Likewise, excluded/invalid source rows have not become claim descriptions.

The downstream accepted-view engine has its own limits, including 30 selected events and 20,000 conservative candidate bindings. Successfully describing up to 32 records does not guarantee that accepting all of them will fit a given query. Larger stores, cross-stay query composition, streaming full-release ingestion, measurements and clinical interpretation remain future work.

## Verification

Twenty-five tests cover provenance, deterministic identities, raw dates, patient/stay/origin scope, complete reconciliation, duplicates, empty stays, the 32/33-claim boundary, partial blocking, schema and dimension failures, RDF isolation, empty acceptance, explicit synthetic Rust reasoning, withdrawal and atomic CLI output.

The [aggregate public-demo report](../verification/mimic-claim-import-demo-report.json) is reproducible from the three separately supplied, checksum-pinned demo 2.2 ICU files:

```sh
python -m patterns.verify_mimic_claim_import --input-dir /path/to/local/mimic-demo-2.2
```

| Public-demo outcome | Count |
|---|---:|
| Input records reconciled | 20,404 |
| Not admitted by the existing staging contract | 9,424 |
| Admitted but outside selected item scope | 10,690 |
| Pending claims | 290 |
| ICU stays in roster | 140 |
| Stays with pending stores | 31 |
| Stays with no selected admitted records | 109 |
| Accepted claims / blocked stays | 0 / 0 |

The maximum store contains 28 claims. All 31 description models passed the 137-axiom isolation check. This is a public-demo source-description result, with no patient rows in the committed report, no full-MIMIC evaluation and no clinical cohort result. CI exercises synthetic files and validates the aggregate report's provenance without downloading patient data.
