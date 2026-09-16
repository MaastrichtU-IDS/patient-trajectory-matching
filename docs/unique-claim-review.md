# Review each source claim once

Status: executable `unique-claim-review-package-1.0`, with 28 tests. It creates a package of unique source claims, compiles explicit review into the existing partition policies, and supports controlled selected-record execution. All three pinned demo packages and their pending policies have been validated locally. No real-source claim was accepted and no real-source mixed query ran.

The [partitioned window executor](partitioned-window-query.md) deliberately repeats records across overlapping batches to preserve every baseline/follow-up pair. Reviewing each occurrence separately creates unnecessary work and risks inconsistent decisions. This package exposes each claim once **within one request/measurement stratum**, together with every affected batch and anchor. A decision is then copied consistently to all occurrences. Separate measurement strata retain separate review packages.

## What the package contains

Preparation scans the original source files, verifies complete partition coverage and builds the existing stores. A blocked partition plan cannot become a complete review package. The package context binds the request, source manifests, source accounting, partition plan, evidence and software hashes.

| Location in the output | Contents |
|---|---|
| `package.context.claims` | One entry per unique claim: original identity, claim hash and content, source evidence, clock, affected batch IDs and anchor IDs |
| Claim `source_record` | Admitted input segment with source/dictionary/stay provenance, or original measurement row with its numeric/time/unit fields |
| `package.context.calendars` | One review entry for each patient with a nonempty interval/measurement batch, showing both clock descriptors and all affected batches |
| `package.context.batch_templates` | Existing pending policies, store hashes and claim membership for each batch |
| `package.context.source_selection_summary` | Complete source admission and window accounting, including original whole-window count blockers |
| `package.summary` | Unique claim counts, repeated occurrences avoided, batches, anchors, stays and pending status |
| `review_template` | Editable decisions with the package context ID and exact claim hashes; no acceptance or alignment defaults |

Source labels describe recorded evidence. Input segments do not establish clinical course starts, and numeric measurement records do not establish a validated phenotype. Preparation performs store/profile validation; it does not run the Rust semantic or temporal matcher.

## Prepare, review, compile and execute

The default command uses fabricated source files:

```sh
python -m patterns.unique_claim_review
```

It writes `verification/unique-claim-review-run/result.json`. For supplied sources, pass `--input-dir /path/to/icu` and `--request /path/to/request.json` on every invocation. The three existing demo requests remain separate. Detailed source packages and review files belong in local run directories; the committed demo report contains aggregate evidence only.

Extract the editable template, preserving the original package for inspection:

```python
import json
from pathlib import Path

folder = Path('verification/unique-claim-review-run')
prepared = json.loads((folder/'result.json').read_text())
(folder/'review.json').write_text(json.dumps(prepared['review_template'], indent=2) + '\n')
```

Set `reviewer` to the reviewer attribution. It is recorded as supplied; the software does not authenticate that identity. Inspect the request, admission scope and claim evidence. For each claim, add a decision with a unique ID, action, reason and revision parent. The existing claim hash remains in the enclosing entry:

```json
{
  "id": "review_reading_001",
  "action": "accept",
  "supersedes": null,
  "reason": "The selected claim faithfully represents this source record under the declared admission policy."
}
```

`accept` and `reject` can start a history. To withdraw an earlier decision, append `withdraw` with `supersedes` naming that decision. A subsequent decision may revise the same claim again. Preserve earlier entries: the compiler validates the existing linear lifecycle, including forks, cycles, multiple roots and invalid withdrawal parents. Every decision ID is unique throughout the package, and revision parents must belong to the same claim. This package does not represent competing cross-claim source variants.

For each calendar entry, choose `same_patient_calendar` only when the two displayed source clocks have a justified common patient calendar, and record the reason. Choose `not_aligned` when that compatibility has not been established. Unresolved calendar entries remain `pending` with a null reason. Empty measurement windows require no invented calendar alignment.

Compile the supplied review without running a query:

```sh
python -m patterns.unique_claim_review \
  --review verification/unique-claim-review-run/review.json \
  --output verification/unique-claim-review-run/compiled.json
```

Compilation rebuilds the package from the original sources. It rejects stale package/claim hashes, missing, extra, duplicated or reordered review entries, blank decision reasons and missing reviewer attribution for any explicit decision. It propagates each complete claim history and each applicable calendar declaration to all affected batches, then rebuilds every store and runs the existing partition review validator. This checks the expanded policies before returning them.

Inspect `compilation.summary` for unique accept/reject/withdraw/pending counts, calendar outcomes and the number of validated batches. The output retains the supplied review and both its hash and the compiled review hash. `compilation.compiled_review` is compatible with the existing partition executor. Neither a complete review nor an accepted source claim certifies clinical truth.

Execution is a separate, explicit action:

```sh
python -m patterns.unique_claim_review \
  --review verification/unique-claim-review-run/review.json --execute \
  --output verification/unique-claim-review-run/executed.json
```

This workflow refuses execution while any claim or applicable calendar entry is pending. All-rejected evidence is a resolved review and can produce an empty selected-record answer. A `not_aligned` decision is also resolved, but any resulting incomparable candidate still blocks a complete query answer. These meanings remain distinct. The earlier partition API still supports pending selected views; this wrapper adds the complete-review gate.

Actual execution uses the unchanged partition engine, checked Rust support and unpartitioned SQL reconciliation. The result retains every batch, anchor and stay; any blocked anchor withholds top-level cohort membership. Review completeness, selected-record query completeness and clinical validity remain separate states.

## Reproducible synthetic example

A [committed review](../examples/unique-claim-review/synthetic-review.json) explicitly selects nine fabricated source claims and aligns three constructed patient calendars. Its [report](../verification/unique-claim-review-synthetic-report.json) retains exact bindings and review hashes.

```sh
python -m patterns.unique_claim_review \
  --review examples/unique-claim-review/synthetic-review.json --execute
python -m patterns.test_unique_claim_review
```

This reproduces P1's two follow-ups, P2's missing follow-up and P3's negative measurement change, while retaining all five requested stays. Source, query or software changes invalidate the review package; this fixture is not reusable approval for another dataset.

## Pinned demo package validation

The [aggregate report](../verification/unique-claim-review-demo-report.json) verifies the same four [original source files](../data/clinical-source-demo-pin.json) and complete anchor plans as the preceding profile. Each request retains 944 anchors and 140 stays.

| Separate measurement stratum | Claim occurrences across batches | Unique claims to review | Repeated entries avoided | Patient calendar reviews | Batches validated |
|---|---:|---:|---:|---:|---:|
| 220052 arterial mean | 3,260 | 2,022 | 1,238 | 17 | 964 |
| 220181 non-invasive mean | 2,823 | 1,784 | 1,039 | 25 | 962 |
| 225312 ART mean | 1,125 | 1,042 | 83 | 4 | 944 |

Every package includes 944 treatment claims, plus respectively 1,078, 840 and 98 measurement claims. Calendar counts cover patients with nonempty measurement windows, not every patient in the retained stay roster. All 2,870 expanded batch policies validate with every claim pending, zero accepted claims and no alignment declarations. These are review-preparation results, not clinical query outcomes.

```sh
python -m patterns.verify_unique_claim_review --input-dir /path/to/demo/icu
```

The [clinical candidate plan](../data/clinical-candidate-plan.json) still has no clinical review. The subsequent [arterial demonstration](reviewed-arterial-demo.md) now supplies an explicit automated source-fidelity declaration and executes that stratum. Reviews and execution for the other strata remain pending. Clinical interpretation of the item mapping and illustrative windows requires separate review; acceptance here does not perform it.

## Limits and checks

The 28 tests cover unique membership within and across anchors, preserved source evidence, deterministic compilation, actual 38-point partition execution, history propagation and withdrawal, hash staleness, closed schemas, reviewer/reason validation, lifecycle errors, unchanged per-batch decision limits, pending/incomparable distinctions, empty scopes, atomic CLI behavior and both committed reports.

Request files are bounded at 256 KiB, supplied and compiled reviews at 32 MiB, and serialized CLI output at 128 MiB. Existing source, partition, claim and per-policy limits remain enforced. Long unique histories can exceed the 128-decision limit after expansion into one batch; compilation then fails rather than trimming history. This implementation retains the package and execution evidence in memory and has no review UI, authenticated approval, persistent resume or production-scale performance claim.

The CLI writes atomically and refuses source/request/review overwrites. Preparation and valid pending or complete compilation exit 0. Incomplete review execution, invalid input and blocked query outcomes exit 2; invalid input preserves previous output. Clinical mapping, physical elapsed time, historical availability, causality and full mixed OWL reasoning remain unverified.
