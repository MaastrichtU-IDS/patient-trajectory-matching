# Measurement source catalogue and claim audit

Status: executable `measurement-source-catalogue-1.0`, with synthetic source-to-Rust/literal/SQL verification. Clinical mapping acceptance and prepared pressure-service integration remain pending.

The [reviewed measurement selector](reviewed-measurement-mappings.md) requires a trustworthy correspondence between a source item code and the records carrying that code. This adapter derives catalogue labels and application source classes from pinned `d_items` rows, then reproduces every supplied measurement claim and patient clock from pinned `chartevents` and `icustays` bytes before invoking the selector.

## Inputs and evidence

The closed [request schema](../schemas/measurement-source-catalogue.schema.json) requires the profile, dataset identifier, declared release label, 1–16 unique numeric item codes, and SHA-256 digests of all three source files. Exactly one `.csv` or `.csv.gz` file must exist per table. Compressed and expanded bytes are bounded; compressed files require their own byte pins. The release label is supplied metadata, not publisher authentication.

Selected dictionary rows must belong to `chartevents`. The mapping catalogue preserves the exact label, uses the existing hashed dataset namespace, and assigns each code the measurement selector's application class IRI. The report retains full selected dictionary rows and their row/file evidence, plus counts of admitted, duplicate, unsupported and invalid rows. Every chart row contributes to reconciliation, including rows outside the requested item scope. Aggregate catalogue reports contain no patient rows or identifiers.

| Check | Evidence established |
|---|---|
| Source pins | The three supplied files have the declared byte digests |
| Dictionary catalogue | Selected codes, labels and table membership reproduce from the pinned dictionary |
| Supplied measurement claims | All claim fields, including patient/stay, source identity, item, decimal spelling, unit spelling and recorded timestamp, reproduce from admitted CSV rows |
| Patient clocks | Origin and origin evidence reproduce from the earliest admitted chart row per patient before item filtering |
| Query handoff | The outer context binds the successful audit context and the original reviewed-selector query context |

The auditor shares the importer's admission, exact-duplicate and claim-description functions. Only the first exact duplicate can supply a claim. Warning-flagged, qualified textual and other unsupported rows cannot be promoted into admitted claims. Chart time remains the recorded measurement label; store time does not become the measurement time or evidence-availability time.

The minimum clock origin can come from an unselected item or another stay belonging to the same patient. It must match the importer's policy, rather than the earliest timestamp remaining in a filtered store. All supplied claims are audited before mapping or source-policy selection, including unaccepted claims. No source review decisions are generated, revised or rebound.

## Query execution and limits

`build(folder, request)` returns aggregate source catalogue evidence. `audit_store(folder, request, catalogue, store)` checks the supplied measurement store. `execute(folder, request, catalogue, terminology, proposals, review, selector, interval_store, interval_policy, semantic_policy, measurement_store, measurement_policy, alignment, query)` audits first and then calls the existing reviewed measurement adapter. The optional `timeout_seconds` argument passes through to that adapter.

The execution result contains `source_audit` and `query_result`; its status mirrors the latter and its own context binds both. Check the outer status before using `query_result` memberships. Pending mappings still block reasoning; failed reasoning still blocks the cohort result. A successful source audit never overrides either gate. The nested query's generic source-fidelity flag remains false; the separate audit states precisely which correspondence checks succeeded.

This is a bounded **subset-fidelity check**. It does not certify complete source coverage, an importer-generated snapshot identifier, clinical correctness, publisher identity, source acceptance, interval fidelity, clock alignment or physical elapsed time. The caller's valid snapshot identifier is retained and bound in the store digest. Dictionary unit metadata is evidence, not a unit conversion rule or an override of recorded units. A unit metadata change changes the source audit context even if catalogue labels and the mapping context remain identical; clinical reviewers must consider that evidence separately.

The existing source readers bound each raw and expanded file to 64 MiB and each table to 100,000 rows. Catalogue accounting can exceed a single store's 32-claim limit; the audit reconstructs only the supplied bounded store's claims. It neither partitions oversized inputs nor certifies a full MIMIC export. There is no new performance claim: source files are reread for each call. [Prepared measurement sessions](prepared-measurement-session.md) now reuse this audit and checked query views, while retaining source-byte digest checks for every query.

## Run and verify

```bash
python -m patterns.measurement_source_catalogue \
  --input-dir examples/source-mixed-query \
  --request examples/measurement-source-catalogue/request.json \
  --output /tmp/measurement-catalogue-report.json

# Add both options to audit a supplied store instead of only building a catalogue:
# --catalogue examples/measurement-source-catalogue/catalogue.json --store /path/to/store.json

python -m patterns.verify_measurement_source_catalogue
python -m unittest patterns.test_measurement_source_catalogue
```

CLI output is atomic; invalid input preserves an existing output file and exits 2. Input files cannot be overwritten. The [authored mapping pack](../examples/measurement-source-catalogue/) reuses the existing synthetic source CSVs and explicit synthetic source-review journal without modifying them.

The [verification report](../verification/measurement-source-catalogue-report.json) checks six measurement claims by direct CSV field comparison, then reproduces the three synthetic patient memberships against unchanged literal executions and a SQL control using reread raw CSV values. Both catalogue selection and treatment classification use actual Rust with the existing reference checks. PRO witnesses and optional follow-up are preserved. Admission and policy selection are shared by the reference: this is a bounded fixture cross-check, not an independent clinical audit.

Twenty tests cover byte pins, schema limits, dictionary revisions, duplicate and rejected rows, scalar/source tampering, pre-filter clocks, subsets, exact units, gzip, resource limits, policy separation, blocked mapping review, atomic CLI behavior and report reproduction. The [clinical terminology candidates](clinical-terminology-candidates.md) remain pending; this increment does not accept a LOINC mapping or run mapped clinical records.
