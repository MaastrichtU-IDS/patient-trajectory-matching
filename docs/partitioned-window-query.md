# Partitioned exact-record window execution

Status: executable `partitioned-window-query-1.0`, with 27 tests. The pinned public demo has complete verified partition plans for all three measurement strata. Actual mixed-query execution is verified on synthetic sources; real-source acceptance, clock review and execution remain pending.

This profile follows [indexed source windows](indexed-source-windows.md). Some complete windows exceed the existing mixed matcher's conservative capacity bounds. Partitioning now retains those windows and covers every measurement record and every distinct baseline/follow-up record pair, while each batch remains within the existing bounds. It does not truncate records or change the whole-window export guard.

## Contract and coverage argument

The supported query has one literal treatment item and one literal measurement item, exact source timestamps, patient/stay scope, baseline scalar eligibility and optional follow-up. Every batch contains the same treatment anchor and a subset of its original complete point window. Claims retain original file hashes, CSV record numbers, identities and patient clock origins. The treatment remains a recorded input segment, with no inferred course start or clinical occurrence.

| Window size | Plan |
|---|---|
| 0 | One empty measurement batch, retaining the anchor |
| 1–16 | One batch containing the complete window |
| 17–128 | Split original record numbers into blocks of eight; take the union of every two distinct blocks |
| Above 128 | Retain anchor as `BLOCKED_PLAN`; no complete cohort answer |

For an oversized window there are at least three blocks. Two records in different blocks occur together in their block-pair batch. Two records in the same block occur together in every batch containing that block. Thus every ordered baseline/follow-up pair of distinct records occurs together somewhere. Every baseline record also appears, including one with no eligible follow-up. The implementation checks the actual record union and pair union against the complete original window before review.

A 38-point window has five blocks and ten batches. Every batch has at most 16 measurements, one interval, at most 18 temporal variables, 16 baseline candidates and 240 distinct follow-up candidate pairs. These fit the existing 32-claim, 64-variable, 128-baseline and 256-follow-up bounds. No scalar-value filter is used to fit the plan.

There are at most 4,096 batches per request and 128 records per anchor. Exceeding the global batch budget blocks the plan for the whole request; it does not retain a convenient subset as complete. Indexed/reference disagreement remains blocking. Existing source scan, row, byte, candidate, stay and anchor limits still apply.

**The decomposition relies on exact, independent source records.** It is not an algorithm for arbitrary uncertain temporal networks, shared cross-batch constraints, full OWL entailment or queries requiring three measurement witnesses jointly. Such extensions require a new completeness argument. The existing uncertain-time profiles remain separate.

## Explicit review and replay

The CLI always scans the original source files and rebuilds the selection, plan and stores. It does not trust a supplied serialized plan. A [closed review document](../schemas/partitioned-window-review.schema.json) must contain the current plan context ID and the exact ordered roster of batches, with an interval policy, measurement policy and alignment for each applicable store.

```sh
python -m patterns.partitioned_window_query --prepare-review
```

The default synthetic request produces `verification/partitioned-window-query-run/result.json`, containing `plan` and `review_template`. Extract the template into a separate local review file. It has empty decision histories and no clock bindings. Preparation never accepts records or asserts that clocks are compatible.

To inspect the stores supporting a decision, use the Python API with the same original source folder and request:

```python
from pathlib import Path
import json
from patterns import partitioned_window_query as p

folder = Path('examples/source-mixed-query')
request = json.loads(Path('examples/indexed-source-windows/synthetic-request.json').read_text())
selection = p.windows.run(folder, request)
planned = p.plan(selection)
review = p.prepare_review(selection, planned)
batch = p.build_batch(selection, planned, planned['batches'][0])
# Inspect batch['interval_store'] and batch['measurement_store'].
# Record reviewed decisions and any justified alignment in review['batches'].
```

Review uses the existing [claim policy lifecycle](claim-projection.md) and [clock alignment contract](mixed-record-query.md). Each decision names its claim and exact claim hash; policy hashes bind the rebuilt stores and fixed semantic policy. Same-patient calendar alignment remains explicit and bound to both stores. Acceptance selects recorded evidence; it does not certify clinical truth. No automatic source acceptance helper is provided by the production module. Test-only synthetic acceptance fixtures are not a clinical review workflow.

Repeated records must have identical content and the same final selected/not-selected state wherever they occur, including across different anchors. Repeated treatment claims follow the same rule. A patient's alignment must be consistently declared or consistently absent across applicable batches. Review histories may differ where the final selected state agrees. Missing, extra, reordered or stale batches and conflicting repeated selections are rejected before any matcher execution.

```sh
python -m patterns.partitioned_window_query \
  --input-dir /path/to/source/icu \
  --request /path/to/request.json \
  --review /path/to/review.json \
  --output /path/to/private-result.json
```

A review with all policies still pending can produce a completed query over an empty selected view. That result means no records were selected, not that treatment or abnormal measurements were clinically absent. All accepted records require the declared alignment to support a cross-store temporal binding; incomparable candidates block completion.

## Execution and reconciliation

Every planned batch has an execution entry. Empty measurement windows complete with no bindings and no invented measurement clock. Nonempty batches invoke the existing checked Rust semantic and temporal pipeline. A matcher block or contract failure is retained in the batch ledger while the other planned batches are attempted.

Bindings retain seven columns: patient, stay, treatment event, baseline record, follow-up record or null, exact delta or null, and unit or null. Merging deduplicates complete source identities. A local null follow-up row is removed if another batch finds a follow-up for the same treatment/baseline pair; otherwise the null row is retained. Conflicting values for the same binding block that anchor.

For each otherwise completed anchor, an independent [raw-row SQLite reference](../patterns/source_mixed_reference.py) evaluates the **entire original window** with the globally selected records. The merged result must equal that unpartitioned result exactly. This comparison verifies both positive bindings and missing-follow-up rows, rather than merely comparing patient membership.

| Result field or status | Interpretation |
|---|---|
| `source_selection_summary` | Original full-scan and admission accounting, including legacy whole-window count blockers |
| `partition_plan_summary` | Record/pair coverage and batch counts before review; its acceptance count remains zero |
| `batches`, `anchors`, `roster` | Every planned batch, original anchor and requested stay, including empty and blocked entries |
| `VERIFIED_RECORD_MATCH` | Selected-record bindings agree with the unpartitioned reference |
| `VERIFIED_NO_SELECTED_RECORD_MATCH` | Complete selected-record search has no eligible binding |
| `NO_ADMITTED_ANCHOR` | Stay retained with no treatment anchor under source admission and item scope |
| `BLOCKED_INCOMPLETE_PARTITIONED_QUERY` | At least one anchor is incomplete; top-level bindings and patient lists are null |
| `COMPLETED_PARTITIONED_QUERY` | All anchors verified; the complete selected-record result is available |
| `unique_accepted_claims` | Accepted source claims counted once despite overlapping batches |

Patient membership is determined by baseline/treatment eligibility; follow-up is not required. A completed exact-record run has equal certain and possible patient sets. Partial batch/anchor bindings remain diagnostic only when the whole result is blocked. The result context binds the source plan, artifact hashes, review and per-batch timeout.

Detailed results contain source identities and accepted-record evidence. Keep real-source review files and run outputs local; the committed public-demo verification contains aggregate evidence only.

## Pinned public-demo evidence

The [aggregate partition report](../verification/partitioned-window-demo-report.json) uses the same four [pinned original files](../data/clinical-source-demo-pin.json) and three separate requests as indexed selection. Each stratum scans 20,404 inputevent rows and 668,862 chart rows, retaining all 140 stays and 944 admitted treatment anchors.

| Measurement item | Anchors needing multiple batches | Planned batches | Empty anchors | Maximum points per batch | Blocked plans |
|---|---:|---:|---:|---:|---:|
| 220052 arterial mean | 3 | 964 | 407 | 16 | 0 |
| 220181 non-invasive mean | 9 | 962 | 445 | 16 | 0 |
| 225312 ART mean | 0 | 944 | 877 | 5 | 0 |

All 2,832 anchors have verified complete record and pair coverage in 2,870 planned batches. This resolves the planning barrier for the 12 windows blocked by the legacy conservative count screen. The original whole-window reports are unchanged. Items remain separate strata, with no pooling or method selection based on query outcomes.

No real-source claims were accepted and no real-source mixed query ran. The [candidate plan](../data/clinical-candidate-plan.json) still has no clinical review. The subsequent [unique-claim review package](unique-claim-review.md) now exposes each claim once, propagates explicit histories consistently and validates the complete expansion before execution. The remaining step is supplying the real-source review decisions. Clinical item/window approval and patient calendar compatibility remain explicit inputs.

## Validation and operational limits

```sh
python -m patterns.test_partitioned_window_query
python -m patterns.verify_partitioned_windows --input-dir /path/to/demo/icu
```

The 27 tests include exhaustive partition coverage for sizes 0–128; deliberate missing cross-block pairs; real Rust execution of a 38-point, ten-batch window against unpartitioned SQL; repeated binding deduplication; follow-up found outside a locally empty batch; consistent withdrawal; stale reviews; conflicting repeated decisions; missing alignment; global/window limits; backend failure; reference disagreement; source and stay accounting; and CLI input protection. CI verifies the aggregate report's pinned provenance without downloading patient data.

The CLI limits request files to 256 KiB and review files to 32 MiB. It writes atomically and refuses to overwrite source, request or review files. A valid pending plan or completed execution exits 0. A blocked plan or execution writes diagnostics and exits 2; invalid input preserves previous output. The Python execution timeout is per nonempty batch, defaults to 20 seconds and is bounded at 60 seconds. This version executes sequentially, retains detailed evidence in memory, and has no global wall-clock deadline, persistent resume or production-scale performance claim.

Clinical mapping, historical availability, physical elapsed time, causal effect and full mixed OWL reasoning remain unverified. Pair coverage and SQL agreement establish correctness within the declared exact selected-record profile.
