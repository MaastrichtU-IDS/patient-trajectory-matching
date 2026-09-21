# Patient-local claim projection

**Status:** executable local-clock extension to [structured claims and controlled projection](claim-projection.md). It connects the claim acceptance layer to the existing [patient-local representation](patient-local-clocks.md), preserving its raw labels, bounded uncertainty and clock isolation. The separate [MIMIC claim importer](mimic-claim-import.md) supplies source-derived recorded claims. Measurement points use a [separate record-selection profile](measurement-claims.md); they remain unsupported by this interval projection.

## Separate profiles

| Claim-store profile | Projection profile | Selected source profile |
|---|---|---|
| `patient-local-claim-store-1.0` | `patient-local-claim-projection-1.0` | `patient-local-interval-1.0`: caller-accepted infusion/specimen assertions |
| `patient-local-record-claim-store-1.0` | `patient-local-record-claim-projection-1.0` | `patient-local-record-interval-1.0`: recorded input segments |

Both use the existing `patient-local-semantic-query-1.0` query language, restricted semantic policy and hash-bound `claim-acceptance-policy-1.0` decisions. The original offset claim entry point, schema and default RDF reader retain their earlier input boundaries. A store cannot mix recorded segments and the occurrence-profile event kinds, or local labels and precomputed numeric bounds.

## Run

```sh
python -m pip install -r patterns/requirements-semantic.lock.txt
python -m patterns.local_claim_projection
python -m patterns.test_local_claim_projection

python -m patterns.local_claim_projection \
  --store examples/local-claim-projection/record-store.json \
  --policy examples/local-claim-projection/record-policy.json \
  --semantic-policy examples/local-claim-projection/record-semantic-policy.json \
  --query examples/local-claim-projection/record-query.json \
  --output verification/local-claim-projection-run/record-result.json
```

Both constructed examples return `READY` and P1 certain. The default example uses role-mediated antibiotic classification; the recorded variant uses explicit synthetic item classes and a group rule. Neither is a reviewed clinical mapping. See the [fixtures](../examples/local-claim-projection/) and the [synthetic verification report](../verification/local-claim-projection-report.json).

The same CLI also accepts `--graph` with a closed local claim-description Turtle graph, and the usual policy/query/output paths. It retains the original input-size protections, atomic output replacement and failure statuses. The result contains the description graph; users can save its `claim_graph_turtle` field and pass that file through the RDF route. Do not use the accepted assertion graph as a claim-store input.

## Representation and normalization

Every clock explicitly identifies its patient scope, a naive local date-time origin, the `patient-local-calendar-microseconds-v1` policy, and the source key for the origin. Each temporal variable contains `local_lower` and `local_upper`. Bounds allow complete date-time labels with up to six fractional digits. Origins use `T`; bound labels can use `T` or a space, following the existing patient-local contract. Offset suffixes, global clocks, partial dates, invalid calendar dates and reversed bounds are rejected.

The origin is a declared coordinate reference, not an inferred first event or a clinically verified onset. Claim acceptance covers its interpretation because the policy binds the entire store hash. Changing a clock origin or its provenance invalidates an old policy binding, even when no individual claim body changed.

Three new **classes** describe `local_lower`, `local_upper` and `origin_source_key` field bindings. Their [class module](../ontology/local-claim-description-profile.ttl) places each below InformationObject. The description graph still contains only information objects and literal content; it asserts no TimeInstant, interval occurrence or participation relation. The original claim class module is unchanged.

On every local run, the constructive model checker verifies the pinned SULO closure, original claim class module and this exact three-class extension with empty Process and Time extensions. The model checks **137 logical axioms**. Unknown module axioms or added occurrence assertions block the claim. This remains a fixed model check for the description encoding, not full-SULO consistency checking of the accepted view.

After selection, the accepted source passes through local RDF export and the existing closed local RDF reader. That reader checks agreement between original labels and derived integer coordinates before semantic reasoning. The result preserves:

- `source`: the selected raw local source, including original labels and source constraints.
- `normalized_source`: the same source with derived integer microsecond bounds, paired to the semantic module's source hash.
- `accepted_graph_turtle`: selected assertions, with local lexical evidence and derived coordinates, plus admitted semantic facts.
- Selection, row support, decision hashes, exact generated semantic module and nested reasoning evidence.

`normalized_source` is an evidence representation; it is not an input to the raw local schema, which rejects precomputed numeric bounds. Recompute it from `source` using the pinned normalizer. Both remain null when the accepted view cannot be released.

## Meaning of a local-time answer

Time-domain and gap interpretation are bound into the outer execution context, semantic context and matching context. Gaps are local-calendar coordinate differences. `physical_elapsed_time_verified` remains false, as do clinical mapping and source-history verification.

Distinct clocks remain incomparable even when their origins and date labels coincide. Events from different patients or episodes cannot form one trajectory binding. Uniform date shifts and consistent origin rebasing preserve temporal answers. The implementation does not infer time zones, daylight-saving corrections, shared physical clocks or historical availability.

Joint source constraints remain attached to the selected variables. They can change a possible binding into a certain binding by restricting the feasible source timelines. If withdrawal removes a variable needed by a surviving constraint or semantic fact, the projection blocks rather than discarding that dependency. Independent accepted support can preserve the dependent event. The existing fixed named witness semantics remain unchanged.

The recorded source profile does not itself imply Infusion or a confirmed clinical administration. The provided recorded policy only derives a source-item group. Any additional semantic assertions or rules are explicit caller-supplied policy content, bound by hashes and checked within the supported fragment; acceptance is not independent clinical verification.

## Verification and next boundary

The **32 new tests** cover both variants, cross-profile rejection, raw/normalized evidence, RDF ingestion, the extended isolation model, clock rebasing/date shifts, incomparable clocks, patient isolation, correlated constraints, corrections, withdrawal, surviving support and backend failure. The original 45 claim tests remain unchanged and continue to pass.

The existing limits on claims, rows, decisions, events and candidate combinations still apply. This is the local-clock bridge for the claim layer. The MIMIC importer provides source-derived pending claims through a separate entry point. The separate [mixed record-query profile](mixed-record-query.md) now combines explicitly aligned interval/point records. Reviewed clinical policies remain future work. No source approval, source-history completeness or clinical truth is fabricated by this extension.

## Source adapter

The [MIMIC claim importer](mimic-claim-import.md) now emits this recorded claim-store profile from admitted inputevents, with file/row provenance and a complete reconciliation ledger. It emits empty acceptance policies and proves description isolation. Deliberate acceptance and querying remain separate operations.
