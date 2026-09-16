# Pending MIMIC demo terminology pack

Only the source catalogue is available. It was reproduced from pinned MIMIC-IV demo 2.2 dictionary and inputevents staging; see [the evidence](../../../verification/source-record-catalogue-demo-report.json) and [runbook](../../../docs/source-record-catalogue.md).

This directory is not an executable or clinically accepted mapping pack. To complete it, add:

| File | Required content |
|---|---|
| `terminology.json` | Target system/version, code, concept IRI and distinct application record-query class |
| `mappings.json` | Justified source-record subclass implications, bound to catalogue and terminology hashes |
| `review.json` | Explicit review decisions, bound to the mapping proposals |

Follow the [four-document contract](../../../docs/reviewed-record-mappings.md) and answer the [clinical worksheet](../../clinical-terminology-review.json). Distinguish recorded input content from actual treatment occurrence. Do not infer dose equivalence or course initiation from a dictionary label.

This catalogue covers only input item 221906. The three measurement items remain separate pending a measurement-mapping extension. Source-claim acceptance must separately bind the compiled semantic policy before execution.
