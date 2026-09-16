# Pending MIMIC demo terminology pack

The four-document pack proposes an ingredient-level record selector associated with RxNorm 7512 for source item 221906. **Its review journal is empty. Compilation returns `BLOCKED_MAPPING_REVIEW`; no clinical mapping is accepted.**

| File | Content |
|---|---|
| `catalogue.json` | Source catalogue reproduced from pinned MIMIC-IV demo 2.2 CSVs |
| `terminology.json` | Norepinephrine concept reference and a distinct application record-query class; observed RxNorm data version 08-Sep-2026 |
| `mappings.json` | One proposed source-record subclass implication, bound to catalogue and terminology hashes |
| `review.json` | Mapping-document hash and no decisions |

Review the [candidate dossier](../../../docs/clinical-terminology-candidates.md), [source evidence](../../../verification/source-record-catalogue-demo-report.json), [NLM lookup evidence](../evidence/rxnorm-7512-2026-09-16.json) and [mapping contract](../../../docs/reviewed-record-mappings.md). The captured API metadata is not an atomic full-release snapshot. Revalidate the terminology evidence before acceptance if necessary.

```bash
python -m patterns.reviewed_record_mappings \
  --catalogue data/terminology/mimic-demo-2.2-pending/catalogue.json \
  --terminology data/terminology/mimic-demo-2.2-pending/terminology.json \
  --mappings data/terminology/mimic-demo-2.2-pending/mappings.json \
  --review data/terminology/mimic-demo-2.2-pending/review.json \
  --output /tmp/pending-clinical-mapping.json
```

Expected exit: 2, with an explicit blocked report. Do not add acceptance as a workaround. A reviewer must decide the proposed recorded-content meaning. Source-claim acceptance must separately bind any accepted compiled policy.

The [three pressure candidates](../../clinical-measurement-mapping-candidates.json) remain a separate non-executable review document. Preserve their source strata. Neither an ingredient name nor a blood-pressure label establishes actual treatment occurrence or course initiation.
