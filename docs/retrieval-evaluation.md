# Held-out retrieval evaluation and authored scaling

`tools/evaluate_retrieval.py` provides evaluation mechanics. It does not establish
clinical retrieval quality. No authorized full MIMIC-IV extraction or independent
clinical relevance judgments were supplied to this execution. The bounded search
of the current and preceding project workspace found the pinned public MIMIC-IV
demo 2.2 source files and earlier project checkouts. The full-source study remains
`specified_not_executed` in `data/full-mimic-study-plan-2.1.json`.

## Evaluation contract

In the authorized data environment:

1. Assign patients to development and evaluation before selecting variables,
   weights, scales, thresholds, or reviewed source mappings. Keep all episodes of
   each patient in one partition; use a stable pseudonym per patient. The evaluator
   cannot detect an upstream identity-resolution failure or repeated pseudonyms.
2. Freeze the source context and feature profile, with an externally recorded
   timestamp and approved review protocol. Apply the frozen profile to held-out
   reference patients and held-out candidate patients. The input rankings must be
   full ordered patient lists from `ranked_patients`, not only highlighted peers.
   Include unresolved patients in the candidate pool: missing feature coverage must
   not quietly improve recall by removing relevant patients from its denominator.
3. Exclude all episodes of the reference patient. The evaluator rejects reference
   IDs in candidate pools, duplicate ranked patients, candidates outside the
   held-out split, and overlap with development patients. Supply one evaluated
   reference per patient; multiple reference anchors require a prespecified
   selection policy rather than counting the patient repeatedly.
4. Obtain independently assessed patient relevance grades: 0 = not relevant,
   1–3 = increasing relevance. The clinical protocol defines the actual grade
   rubric. Reviewers should be blinded to retrieval order and distances. Reconcile
   multiple reviewers under the declared protocol and report reviewer agreement
   separately; this tool does not invent consensus labels.
5. Seal the evaluation bundle hashes and run the evaluator. Hash consistency
   binds the artifacts but cannot prove a freeze occurred before label inspection
   or that reviewers were independent. Preserve those external records.

An input bundle has this structure (identifiers below are authored examples):

```json
{
  "schema": "heldout-retrieval-evaluation-1",
  "label_origin": "independent_reviewer",
  "review_protocol_id": "approved-protocol-version",
  "source_context": {"source_files": {"example": "actual-source-hash"}},
  "feature_profile": {"id": "actual-frozen-profile", "sha256": "actual-profile-hash"},
  "split": {
    "development_patient_ids": ["development-patient"],
    "evaluation_patient_ids": ["reference-patient", "candidate-a", "candidate-b"]
  },
  "queries": [{
    "reference_patient_id": "reference-patient",
    "candidate_patient_ids": ["candidate-a", "candidate-b"],
    "ranked_patient_ids": ["candidate-a"],
    "judgments": {"candidate-a": 3, "candidate-b": 1}
  }],
  "frozen_hashes": {
    "source_context": "canonical-sha256",
    "feature_profile": "canonical-sha256",
    "split": "canonical-sha256",
    "queries": "canonical-sha256"
  }
}
```

`source_context` and `feature_profile` must contain the actual complete retained
contexts; abbreviated objects above illustrate shape only. `frozen_hashes` values
are calculated with `tools.evaluate_retrieval.digest` (UTF-8, sorted keys, compact
JSON, `ensure_ascii=False`, finite JSON values only). Freeze profile/source/split
before evaluation; seal queries after reviewers supply judgments. A new bundle
cannot reuse earlier hashes after inputs change. For metric unit fixtures only,
use `label_origin: "authored_metric_fixture"`. Algorithm-generated relevance is
not an accepted label origin.

```bash
python -m tools.evaluate_retrieval \
  --input /secure/evaluation-bundle.json --k 10 \
  --output /secure/retrieval-aggregate.json
```

The evaluator outputs aggregate counts, artifact hashes and metrics without
patient IDs. Input files contain patient-level information and belong in the
authorized environment; do not commit them. A metadata-only aggregate is not an
automatic disclosure approval for small clinical cohorts.

## Metrics and missing judgments

- Precision@k uses relevant retrieved patients divided by **k**, including empty
  slots when fewer than k patients are returned. All nonzero grades count as
  relevant. If a retrieved top-k patient is unjudged, precision is undefined and
  the report provides lower/upper bounds obtained by treating those unknown
  judgments as all irrelevant/all relevant.
- Recall@k uses all relevant patients in the complete candidate pool as its
  denominator. It is undefined for incomplete candidate judgments or zero
  relevant candidates. Relevant unresolved/unreturned patients reduce recall.
- nDCG@k uses gain `2^grade - 1` and logarithmic rank discount `log2(rank + 1)`
  with ranks starting at 1. Its ideal ordering considers the full candidate pool.
  It is undefined for incomplete judgments or zero ideal gain.
- Unjudged patients are omitted from `judgments`; they are never implicitly given
  grade 0. Macro means include only queries where the metric is defined. Always
  report the corresponding defined/undefined query counts and judgment coverage.

Authored unit cases check hand-calculated graded nDCG, missing labels, short and
empty rankings, omitted relevant patients, all-negative pools, split leakage,
invalid grades, and frozen artifact changes. These tests establish arithmetic and
contract behavior, not clinical validity.

## Exact comparison scaling

```bash
python -m tools.evaluate_retrieval --benchmark --sizes 100 1000 5000 \
  --output verification/authored-retrieval-scale.json
python -m unittest tools.test_retrieval_evaluation
```

Each size runs in a fresh process, with one timed comparison through the actual
`app.recorded_similarity.compare_snapshot` implementation and an explicit weighted
four-feature profile. Every patient has three pre-index measurements and one
post-index measurement; the reference patient has two episodes. Checks verify
patient-wide reference exclusion, exact rational contribution totals, and
unchanged ranking/contributions after changing post-index values and treatment
end time. Fixture labels are not used to claim relevance.

The report records first-comparison wall time and process high-water RSS. RSS
includes fixture construction and later invariant checks, so it is not memory
attributed solely to one comparison. These single shared-host trials omit ETL,
source admission/reasoning, disk persistence, missingness distributions and
concurrent requests. They do not establish production throughput. The current
patient comparison repeatedly scans anchors/roster; these trials can expose its
scaling cost, not certify it for larger cohorts.

The four measured features describe one pressure stream. Separate reviewed
clinical variables require their own source admission, unit and missingness
validation and subsequently a separate benchmark.

## Remaining acceptance evidence

Full-source retrieval acceptance requires the authorized extraction and pinned
source context, approved clinical variables and grading rubric, a recorded split
and pre-evaluation freeze, independent relevance judgments, coverage and reviewer
agreement, and executed held-out metrics. Dataset access asserted in the study
plan is not an executed extraction. None of these clinical results can be inferred
from the public-demo query checks or authored scaling report.

Use [the clinical review workflow](clinical-review-workflow.md) to record and
validate protocol decisions. Its pending pressure-pilot package does not approve
the full-source antibiotic/renal study or an unspecified multivariable profile.
An accepted descriptive protocol still does not establish retrieval usefulness;
the external judgments and executed held-out evaluation remain necessary.
