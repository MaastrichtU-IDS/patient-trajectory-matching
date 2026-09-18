# Record and validate clinical review

The [review package](../data/clinical-review/pressure-review.pending.json) is
**pending**. Its [protocol](../data/clinical-review/pressure-protocol.json) turns
the existing worksheet into nine explicit decisions. It binds the public-demo
release, exact source checksums, candidate plan and mapping candidates. None of
these files records clinical approval. The original candidate plan remains
`PROPOSED_FOR_REVIEW`.

This package is for the descriptive pressure pilot. It does not approve the
separate full-source antibiotic/renal study, demonstrate useful retrieval, or
establish treatment effects. An automated source-fidelity review is not a
substitute for this review.

## Separate multivariable review package

The [multivariable protocol](../data/clinical-review/multivariable-protocol.pending.json)
and [pending decisions](../data/clinical-review/multivariable-review.pending.json)
extend the public-demo question with an exact
[variable definition](../data/clinical-review/public-demo-multivariable-definition.json).
All nine decisions and final sign-off remain pending. Pressure-only approval
cannot authorize this extension: the validator rejects the earlier protocol hash
both in copied attestations and in a report claiming multivariable approval.

| Feature | Public-demo source | Literal unit | Fixed illustrative scale | Weight |
|---|---|---|---:|---:|
| Latest pressure | 220052, 220181 or 225312, in separate strata | mmHg | 10 | 1 |
| Latest heart rate | 220045, Heart Rate | bpm | 10 | 1 |
| Latest respiratory rate | 220210, Respiratory Rate | insp/min | 4 | 1 |

The profile requires all three latest measurements in the same patient/stay,
strictly before segment start within 30 minutes. Scales are fixed technical
choices requiring review, not clinical recommendations. Its protocol binds the
source pin, extraction/admission implementation and exact profile definition.
The source dictionary confirms item labels and units; it does not confirm the
clinical suitability of the variables or their scales.

The separately bound authored pack is an implementation example only. Its
fictitious IDs `3000`/`3001` and literal units `beats/min`/`breaths/min` are explicitly
distinguished from recorded IDs `220045`/`220210` and units `bpm`/`insp/min`.
Approving fixture mechanics cannot substitute for reviewing clinical evidence.
Generated recorded CSVs and source packs remain outside the repository. Reviewers
inspect those controlled inputs and source-fidelity evidence in the authorized
environment; the committed review package contains definitions and checksums.

```sh
python -m tools.check_clinical_review data/clinical-review/multivariable-review.pending.json
```

This succeeds as a pending package; adding `--require-accepted` must fail until
actual review decisions and final sign-off are supplied. Any change to its bound
profile, source or implementation invalidates old artifact bindings and requires
review against the new protocol version.

## What the clinical collaborator receives

Provide the protocol, pending decision file, the bound candidate/mapping files,
and [the evaluation worksheet and observed technical results](recorded-workflow-evaluation.md).
Use the application to inspect the actual admitted treatment segments and
measurements in an authorized environment. Do not put patient-level evidence into
the review JSON; refer to controlled evidence separately where necessary.

The reviewer decides on the question, treatment anchor, measurement identity,
eligibility, baseline, follow-up, time/availability, similarity and evaluation
target. An acceptance applies to the exact protocol bytes and all artifacts
bound inside it. The protocol must describe the intended query/profile, not just
the application's capabilities. A different threshold, temporal window, variable,
scale or source release needs an appropriate revised protocol and review.

For distinct variables, include a versioned definition artifact in both the
protocol's `artifacts` and review's `artifacts`. It must specify source item IDs,
dictionary labels/version, literal units, pre-index windows and availability
rules, transformations, aggregation/tie rules, weights/scales, missingness and
minimum coverage. Review measurement identity and similarity against these exact
definitions. Pending proposed mappings must not silently become accepted because
the code can extract their values. The provided pressure-only package explicitly
does not approve an unspecified extended profile.

## Fill the decisions

1. Copy `pressure-review.pending.json` to a review file in the appropriate access
   controlled project location. Give it a unique `review_id`.
2. For each decision, choose `ACCEPT`, `REVISE` or `REJECT`; leave unreviewed items
   `PENDING`. Record actual reviewer `name`, `role`, `organization`, ISO `date`,
   `rationale` and the reviewed `protocol_sha256` in `reviewer`. A pending item has
   `reviewer: null`. `REVISE` requires concrete `requested_changes`.
3. An accepted item has `requested_changes: null`. Acceptance subject to a future
   edit is a revision, not approval. Incorporate requested changes into a new
   protocol version, update artifact hashes, and have decisions recorded against
   that new protocol. Do not transfer old signatures to a changed hash.
4. Set overall status to `REJECTED` if any decision is rejected, otherwise
   `REVISION_REQUIRED` if any needs revision. It remains `PENDING` until all nine
   decisions are accepted and a final reviewer fills `signoff` with the same six
   attestation fields. Then set `status: "ACCEPTED"`. The final sign-off date must
   be on or after every decision date.

For example, an attestation has this shape; the placeholders below are not a
valid signed review:

```json
{
  "name": "ACTUAL REVIEWER NAME",
  "role": "ACTUAL CLINICAL ROLE",
  "organization": "ACTUAL ORGANIZATION",
  "date": "YYYY-MM-DD",
  "protocol_sha256": "SHA256 OF EXACT REVIEWED PROTOCOL FILE",
  "rationale": "Decision rationale and relevant limitations."
}
```

The validator checks presence, dates, decisions and artifact integrity. It cannot
authenticate a person's identity, verify clinical qualifications, or establish
that a review took place. The project owner must verify those facts through the
normal review process and preserve that record. A typed name is not a digital
signature.

## Validate without blocking technical work

Run from the repository root:

```sh
python -m tools.check_clinical_review data/clinical-review/pressure-review.pending.json
python -m unittest tools.test_clinical_review
```

The template passes structural and artifact validation with `status: PENDING`
and `clinical_approval_recorded: false`. A report can still truthfully describe
technical correctness, coverage, timing, memory or replay checks.

Before describing a protocol as clinically approved:

```sh
python -m tools.check_clinical_review /path/to/completed-review.json --require-accepted
```

This exits with code 2 for a pending, rejected, stale or malformed review. To
bind an evaluation report to that approval, the report must contain these exact
fields from the validated package:

| Field | Meaning |
|---|---|
| `dataset_id` | Exact source release identifier |
| `clinical_protocol_sha256` | SHA256 of the reviewed protocol file |
| `source_file_sha256` | Complete mapping from pinned source names to file SHA256 values |

Then run:

```sh
python -m tools.check_clinical_review /path/to/completed-review.json --report /path/to/report.json
```

Code can call `validate_review(review, root=repository_root)` for structural
validation, or `clinical_claim_gate(review, report, root=repository_root)` before
emitting the specific claim `reviewed_descriptive_protocol`. The latter refuses
missing approval and source/protocol mismatches. Technical evaluators need not
call the approval gate and must continue to label unreviewed work as technical.
Binding declarations in a report is not independent proof that an execution used
those inputs; the evaluator must also verify its actual source and configuration.

Even a successful gate returns `clinical_usefulness_established: false`. Useful
retrieval needs adjudicated relevance labels and the planned patient-disjoint
evaluation; clinical effectiveness and causal inference require separate study
designs and evidence. Full-source access, clinical reviewer participation and
independent labels remain external prerequisites.
