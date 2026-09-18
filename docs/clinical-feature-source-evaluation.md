# Recorded distinct-variable technical evaluation

`tools/prepare_demo_clinical_features.py` extracts heart rate (MIMIC item 220045,
`bpm`) and respiratory rate (220210, `insp/min`) from the pinned public MIMIC-IV
demo 2.2. These exact labels, numeric types and units must agree with its original
`d_items` dictionary. Respiratory Rate (Total), item 224690, is a separate concept
and is not substituted.

The extractor reuses the existing input-segment and charted-measurement admission
rules: patient/stay identity, numeric/text agreement, timestamps, warning state,
unsupported records and duplicate rows. It selects same-patient/stay measurements
within thirty minutes strictly before **any** of the 944 admitted norepinephrine
recorded segment starts. Selection does not depend on pressure eligibility or
subsequent outcomes. All 100 patients and 140 stays remain in the parent population.

The generated pack records automated technical source-fidelity acceptance and
`clinical_status: PENDING`. This is not a clinician's review or an approval of
similarity criteria. Its event IDs include the original source-file hash and
one-based `chartevents` data-record number. The evaluator independently checks the
extracted values and identities against those original records.

## Reproduce locally

Use a private output directory outside the Git repository. The extractor rejects
source-pin drift, output symlinks, nonprivate output directories, existing output
files and any extraction exceeding its row/byte bounds. It never truncates the
population to make a run fit.

```sh
python3.12 tools/prepare_demo_clinical_features.py \
  --mimic-dir /local/mimic-iv-demo-2.2 \
  --output-dir /private/demo-clinical-features \
  > /private/demo-clinical-extraction-summary.json

python3.12 tools/evaluate_demo_clinical_features.py \
  --mimic-dir /local/mimic-iv-demo-2.2 \
  --clinical-features /private/demo-clinical-features/pack.json \
  --extraction-summary /private/demo-clinical-extraction-summary.json \
  --output verification/public-demo-clinical-features.json
```

Install the repository's pinned semantic dependencies in the Python environment
before running these commands. Only the aggregate evaluation report belongs in
version control; generated CSV, source pack, job snapshots and exported reports
contain source-level patient evidence and stay outside it.

The explicit three-variable profile uses equal weights and illustrative scales
of 10 mmHg, 10 bpm and 4 insp/min. It requires all three values and performs no
imputation. These settings are candidates for clinical review, not optimized or
validated measures of clinical similarity.

The evaluator runs the full ART pressure stratum, independently checks selected
features for every anchor, recalculates exact distances and each patient's best
anchor, preserves reference-patient exclusion and temporal results, then checks
durable restart and exact source-backed export replay. Its aggregate output binds
the source hashes, extractor, implementation and profile.

The committed report is technical evidence for a recorded, multi-variable
workflow. It does not establish relevant-peer retrieval quality, treatment effects,
full MIMIC performance or production throughput. Timings are descriptive and may
be affected by other jobs on the shared host. Clinical acceptance remains governed
by the separately pending multivariable review package in `data/clinical-review/`.

## Recorded result

The verified extraction contains 1,287 observations: 639 heart-rate and 648
respiratory-rate rows. All were checked against their original chart records.
Across the full 944 anchors, a pre-index heart rate is available for 553 anchors,
respiratory rate for 557 and ART pressure for 33. All three features are present
for **32 anchors**. For the selected reference, only **two other patients** have a
complete profile; **97 patients remain explicitly unresolved**.

This sparse pressure stratum demonstrates why feature coverage must accompany a
similarity result. The small ranked set is not evidence of useful clinical
retrieval. The report verifies exact weighted ranking, unchanged temporal evidence,
patient-wide reference exclusion, durable restart and source-backed replay.
