# Use-case conformance tests

This directory is the contributor entry point for the synthetic GEN-01 and COH-01
acceptance cases. The executable tests use the repository's current engines and live
in [`patterns/test_use_case_conformance.py`](../patterns/test_use_case_conformance.py);
they do not use the older standalone replay/cohort implementation from the preserved
local branch. Run all commands below from the repository root.

## Setup and run

Use Python 3.12 and install the normal project dependencies as described in
[`docs/validation.md`](../docs/validation.md#setup). Run the use-case suite:

```sh
python -m patterns.test_use_case_conformance -v
```

Expected: **4 tests pass**. The complete repository suite is:

```sh
python -m unittest discover
```

For the engine-specific contracts used by these cases, also run:

```sh
python -m patterns.test_joint_evidence -v
python -m patterns.test_interval_cohort -v
```

The use-case module runs both the indexed and independent reference cohort matchers
and compares their semantic results. It does not require patient records, MIMIC data,
BGSI data, or a hospital connection; all use-case data is synthetic.

## GEN-01: genomic source-as-known replay

**Question tested:** At two source-availability cutoffs, which synthetic genomic
evidence is selected for patient `PGEN01`? The archive contains a patient variant
observation, two release-specific classification assertions, and a later clinical
surveillance record. The query is a selector for the genomic study process; it is
not a variant-matching query.

The exact query in
[`examples/joint-evidence/gen-01/query.json`](../examples/joint-evidence/gen-01/query.json)
is:

```json
{
  "profile": "semantic-bounded-query-1.0",
  "id": "genomic_study_selection",
  "slots": [
    {"id": "study", "class_iri": "https://example.org/trajectory/bounded/SpecimenCollection"}
  ],
  "constraints": []
}
```

Run the selector at each cutoff and then run its acceptance tests:

```sh
python -m patterns.joint_evidence \
  --archive examples/joint-evidence/gen-01/archive.json \
  --request examples/joint-evidence/gen-01/before-request.json \
  --policy examples/joint-evidence/gen-01/policy.json \
  --query examples/joint-evidence/gen-01/query.json \
  --output /tmp/gen-01-before

python -m patterns.joint_evidence \
  --archive examples/joint-evidence/gen-01/archive.json \
  --request examples/joint-evidence/gen-01/after-request.json \
  --policy examples/joint-evidence/gen-01/policy.json \
  --query examples/joint-evidence/gen-01/query.json \
  --output /tmp/gen-01-after
```

Expected: both commands report `READY`. The 1 March request selects the observation
and R1 uncertain classification, but not R2 pathogenicity. The 1 August request
also selects R2. `selection.later_evidence_used` remains `false` for both source-as-
known runs, and the observation fact payload is unchanged between them.

The tests also inject conflicting synthetic classifications. Reusing one fact ID
with a changed payload must produce `BLOCKED_EVIDENCE`; distinct incompatible claims
must produce `INCONSISTENT_ONTOLOGY`. No classification is selected by JSON order.

**Important boundary:** GEN-01 verifies source/release-history selection only. It
does not encode an explicit edge from a classification to a stable variant identity,
test variant matching, ingest ClinGen data, or validate a clinical action. See the
[joint-evidence guide](../docs/joint-evidence-selection.md#gen-01-synthetic-genomic-release-replay)
for why the current admitted semantic profile cannot represent that integration
relation without a versioned application-profile extension.

## COH-01: broad-to-refined cohort query

**Question tested:** How does research cohort membership change when a temporal
condition is added to the same constructed snapshot?

The broad query in
[`examples/interval-cohort/coh-01-broad-query.json`](../examples/interval-cohort/coh-01-broad-query.json)
selects two distinct `sulo:Process` slots without a temporal condition. It returns
`["P1", "P2", "P3"]`.

The refined query in
[`examples/interval-cohort/coh-01-refined-query.json`](../examples/interval-cohort/coh-01-refined-query.json)
selects `Infusion` followed by `SpecimenCollection` using the existing `before`
operator:

```json
{
  "profile": "interval-cohort-1.0",
  "id": "coh-01-refined-infusion-before-collection",
  "slots": [
    {"id": "infusion", "class_iri": "https://example.org/trajectory/interval/Infusion"},
    {"id": "collection", "class_iri": "https://example.org/trajectory/interval/SpecimenCollection"}
  ],
  "constraints": [
    {"id": "infusion-before-collection", "left": "infusion", "right": "collection", "operator": "before"}
  ]
}
```

Run both query fixtures and the reference implementation:

```sh
python -m patterns.interval_cohort \
  --query examples/interval-cohort/coh-01-broad-query.json \
  --output /tmp/coh-01-broad.json
python -m patterns.interval_cohort \
  --query examples/interval-cohort/coh-01-refined-query.json \
  --output /tmp/coh-01-refined.json
python -m patterns.interval_cohort \
  --query examples/interval-cohort/coh-01-refined-query.json \
  --engine reference --output /tmp/coh-01-refined-reference.json
```

Expected: the refined membership is `["P1"]`; the delta removes P2 and P3. P2 has
no recorded infusion-before-collection match. P3's event clocks are incomparable.
Indexed and reference executions must agree semantically (their execution counters
may differ). The test also checks that returned bindings retain process, patient
role/bearer, source-record, and context evidence.

Here `before` means strict whole-interval precedence (`left.end < right.start`).
Endpoint contact (`meets`, equal end/start) is a distinct relation; this test does
not implement direct succession or the proposed `directlyPrecedes`/`immediatelyPrecedes`
vocabulary. See [`temporal-precedence.md`](../docs/decisions/temporal-precedence.md).
COH-01 is a constructed process cohort, not HPO/rare-disease matching, diagnosis, or
treatment advice.

## Expected scope of a passing result

A green test result means these synthetic selection and interval-query contracts
reproduce under the current codebase. It does not establish clinical validity,
complete hospital history, production ClinGen integration, treatment effectiveness,
or full OWL temporal semantics. The current limits and proposed next steps are in
[`docs/validation.md`](../docs/validation.md) and the linked engine guides.
