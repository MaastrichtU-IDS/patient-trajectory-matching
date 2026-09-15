# Recorded measurement claims

The measurement increment adds a bounded `chartevents` importer, a scalar/point claim store, and explicit selection of measurement record descriptions. **Imported claims remain pending.** Selected records preserve one point-time variable, the original numeric spelling, its unit and source evidence. This increment does not perform mixed point/interval matching or generate an accepted occurrence graph.

## Run the synthetic example

```sh
python -m patterns.mimic_measurement_import
python -m patterns.test_measurement_claims
python -m patterns.verify_measurement_claims
```

The importer writes `verification/mimic-measurement-run/result.json`. Its six synthetic rows comprise three admitted readings, one exact duplicate, one source-warning row and one qualified value. All six receive ledger outcomes; three become pending claims. The [verification report](../verification/measurement-claim-report.json) reproduces the empty pending selection and a separately constructed acceptance policy used only for testing.

The example's admitted records are:

| Patient-local chart label | Recorded value | Recorded unit |
|---|---:|---|
| 2150-01-01 09:40:00 | 58 | mmHg |
| 2150-01-01 10:30:00 | 68 | mmHg |
| 2150-01-01 11:30:00 | 72 | mmHg |

These synthetic pressure readings are the measurement side of a possible treatment-and-response example. The importer neither identifies a treatment nor establishes a response, improvement criterion or causal effect. Each timestamp describes a recorded point; it is not expanded into a one-microsecond or one-minute interval.

For locally supplied files:

```sh
python -m patterns.mimic_measurement_import \
  --input-dir /path/to/local/files \
  --request /path/to/explicit-request.json \
  --output verification/mimic-measurement-run/local-result.json
```

The directory must contain exactly one `.csv` or `.csv.gz` for each of `chartevents`, `icustays` and `d_items`. Use the [request fixture](../examples/mimic-measurement/request.json) and [schema](../schemas/mimic-measurement-import.schema.json), with your dataset identifier, patients and one to sixteen literal item codes. The mode is retrospective source records and the timestamp policy is `recorded-point-label-exact-v1`. Selecting an item code is an import-scope decision, not a verified clinical mapping.

Only synthetic `chartevents` files have been evaluated in this increment. The public-demo inputevents report was reproduced because shared claim code changed; that does **not** constitute a public-demo measurement evaluation.

## Source interpretation and admission

The official [MIMIC chartevents documentation](https://mimic.mit.edu/docs/iv/modules/icu/chartevents.html) describes `charttime` as the observation timestamp and `storetime` as recording or validation time. It distinguishes numeric `valuenum` from potentially textual `value`, identifies `valueuom` as the unit, and describes `warning` as a manually documented flag. The reader uses the documented eleven-column layout, including `caregiver_id`.

The adapter deliberately admits a narrow scalar subset:

| Condition | Outcome |
|---|---|
| Valid patient/admission/stay/item relationship; complete chart label; matching numeric `value` and `valuenum`; explicit unit; `warning=0` | Admitted measurement record |
| Missing numeric/text value or unit; textual/qualified value; `warning=1` or absent | Unsupported row, with explicit reason |
| Invalid identifiers, dictionary reference or timestamp; nonfinite/malformed number; numeric value disagreement; invalid warning flag | Invalid row |
| Exact duplicate of an earlier row | Duplicate ledger entry linked to the original; no additional claim |
| Valid admitted row outside requested patients or items | Explicit out-of-scope outcome |

The warning rule is this application's admission policy, not a MIMIC guarantee that unflagged values are correct. No clinical range check is inferred. Negative and zero values are syntactically admissible scalars. Qualified expressions such as `<5` and scored text such as `15 Alert` are retained in the ledger but are not treated as exact numeric measurements.

Decimal comparisons use exact arithmetic on the **exported numeric spelling**, without binary-float conversion. Numerals have at most 64 characters and a decimal tuple exponent between −308 and 308. Equivalent spellings such as `58` and `58.00` can pass the input cross-check while their original strings remain available. This does not recover precision lost upstream or establish physical measurement precision.

Units are retained literally. Missing units are not filled from `d_items`, and no conversion or equivalence between unit spellings is performed. Text, numeric value, unit, warning and all other input fields remain in the raw ledger. Missing `storetime` is allowed; if supplied it must parse. A recording timestamp earlier than `charttime` is retained without inventing an availability time.

## Claim shape and SULO boundary

The [measurement claim schema](../schemas/measurement-claim-store.schema.json) uses the existing claim envelope and decision machinery, with a separately versioned profile:

```json
{
  "event_kind": "recorded_measurement",
  "status": "recorded",
  "time_var": "observation_time",
  "value_lexical": "58.00",
  "unit_lexical": "mmHg",
  "item_id": "1000"
}
```

This is a partial event description: the complete schema also requires event/record identity, patient/episode scope and source key. The referenced time variable carries local lower/upper labels and an explicit patient clock. Imported labels have equal bounds; manually supplied claims can instead bound an uncertain point. An uncertain point is still not a process-duration interval. Cross-variable constraints and semantic facts are excluded from this first measurement profile.

Four new **information-object classes** encode the four field positions `time_var`, `value_lexical`, `unit_lexical` and `item_id`. There are no new object/datatype properties. The original claim vocabularies remain closed; measurement fields require the new explicit reader mode:

```python
from patterns import claim_rdf, claim_isolation

graph = claim_rdf.encode(store, fields=claim_rdf.MEASUREMENT_FIELDS)
recovered = claim_rdf.decode(graph, fields=claim_rdf.MEASUREMENT_FIELDS)
certificate = claim_isolation.check(graph, local=True, measurement=True)
```

The model checker verifies **141 logical axioms**: the previous local closure's 137 plus the four subclass axioms. Process and Time can both have empty extensions in the checked model. This proves non-entailment of process/time existence by these descriptions, not global absence of processes or full-ontology correctness of an accepted occurrence view. Unreviewed changes to the field-class module and injected live process assertions fail the closed checks. The original base/local modes still check 134/137 axioms.

## Explicit selection and its evidence

Use the existing hash-bound decision format when deliberately selecting records:

```python
from patterns import measurement_claims

view = measurement_claims.select(episode["store"], episode["acceptance_policy"])
assert view["status"] == "EMPTY_SELECTED_RECORDS"  # Importer's empty policy
```

A separate policy can accept, reject, correct or withdraw claims, with reasons and supersession links as described in the [claim contract](claim-projection.md). The semantic-policy hash binds a fixed empty policy: **measurement selection performs no semantic inference**. The importer and selector never generate acceptance decisions. The verifier creates explicitly labeled synthetic test decisions.

Successful explicit acceptance returns `SELECTED_MEASUREMENT_RECORDS`. Each record includes the original event and time variable, full clock description, derived lower/upper integer coordinates, an exact decimal rendering and all surviving event/time claim supports. Equal numeric values do not merge different observations. Independent support can survive withdrawal; conflicting selected values block the entire view. A missing or cross-scope selected time dependency also blocks the entire view, with no partial record result.

`matching` and `accepted_graph_turtle` remain null, and `temporal_query_supported` and `semantic_inference_performed` remain false. Selection is a record-description operation, not a clinical assertion graph or a temporal cohort answer. Existing interval and interval-claim entry points reject measurement stores. Stale store/policy hashes cannot silently select changed data.

## Provenance, clocks and bounds

Input evidence binds the actual file bytes, decompressed CSV bytes, canonical row and one-based CSV record number. Claim provenance also identifies the exact item dictionary row, stay row and origin row. The run context binds all file manifests, the request, policies and implementation hashes; its digest feeds each store snapshot identity. Changing the dictionary changes the store hash even if its event descriptions are unchanged.

The original dataset label is retained, and the namespace transformation matches the [inputevents claim importer](mimic-claim-import.md). Record locators are snapshot identities, not a claim of stable revision identity across exports. Source availability, revision history, clinical mapping and physical elapsed-time verification remain unknown or false.

A patient's origin is the minimum admitted chart label **before item filtering**, shared across that patient's stays in this import. The raw dates and origin source remain present alongside derived coordinates. A measurement import and an inputevents import can choose different origins: **matching clock IDs alone does not make their numeric offsets directly comparable**. A future cross-store temporal join must verify dataset/patient identity and reconcile origins before computing differences.

The reader bounds `chartevents` at 100,000 rows and 64 MiB per raw/expanded file; dimension tables retain their existing reader limits. At most 256 stays and 32 claims per stay are supported, with existing claim string/document/tree/graph limits also enforced. Files and roster errors invalidate the whole run. An oversized stay exports no claims and gives every selected row an explicit block outcome; other stays may retain pending stores, but the summary is `BLOCKED_INCOMPLETE_IMPORT`. No rows are truncated or silently partitioned.

Detailed results contain supplied source rows and remain local. CLI writes are atomic; invalid input preserves the previous output. Successful pending import returns 0; an incomplete import writes its ledger and returns 2. The committed measurement report is synthetic only. This bounded reader is not a full-release loader.

## Next contract to implement

Combine explicitly selected treatment intervals and measurement points under a new mixed temporal profile. It needs endpoint-specific gaps, point membership in windows, unit-compatible scalar predicates, a reviewed item-to-clinical-concept policy, and a source completeness contract. It must preserve fixed-witness certainty and patient/episode isolation. Select baseline/treatment eligibility before examining follow-up outcomes, so absence of a follow-up remains unknown instead of excluding the patient or proving treatment failure.

The present 35-test suite verifies the measurement building block; it does not claim that this next mixed query is already executable.
