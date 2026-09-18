# Distinct-variable similarity admission example

`authored-pack.json` is an explicitly enabled **authored technical fixture**. Its
heart-rate and respiratory-rate records are invented examples, not MIMIC records
or clinically accepted mappings. The pack retains `clinical_status: PENDING`.

Run the workspace from the repository root:

```sh
python3.12 -m app.server --clinical-features examples/clinical-features/authored-pack.json
```

Select the distinct-variable feature family after completing an authored recorded
query. Select pressure, heart rate and respiratory rate, then set the weights and
scales explicitly. The schema is `recorded-clinical-features-1`:

```json
{
  "schema": "recorded-clinical-features-1",
  "features": [
    {"id": "latest_value", "weight": "1", "scale": "10"},
    {"id": "heart_rate", "weight": "1", "scale": "10"},
    {"id": "respiratory_rate", "weight": "1", "scale": "4"}
  ]
}
```

These illustrative scales are not validated clinical choices. Ranking uses exact
rational arithmetic for `sum(weight * abs(peer - reference) / scale) / sum(weight)`.
The display rounds to twelve decimal places; exact numerators and denominators
remain available. All selected features must be present; missing values are not
imputed. Supplemental-only profiles may rank an anchor with no pressure reading.
The reference patient's other stays and anchors remain excluded.

## Evidence boundary

Each supplemental variable has a fixed item ID, exact lexical unit and maximum
lookback. Each observation belongs to one patient and ICU stay, with a patient-local
charted timestamp. The effective window is the intersection of the variable's
lookback and the query's baseline window, ending strictly before treatment start.
Only the latest finite admitted observation is selected; timestamp ties use the
ascending event ID. No unit conversion or terminology inference occurs here.
Charted time does not establish that the value was available to a clinician at index.

The CSV includes a follow-up value and a value from another stay to demonstrate
exclusion. It has no clinical events for the fourth roster patient, who remains
unresolved. The existing pressure stream and its temporal matching evidence remain
separate from these additional comparison features.

## Supplying a reviewed local pack

A startup-only pack pins the supplemental CSV SHA-256 and **all** parent source-file
hashes. HTTP requests can choose only admitted feature IDs, weights and scales;
they cannot supply source rows, paths, item IDs or units. The pack records technical
review identity and rationale; admission requires `TECHNICAL_ACCEPTED` with clinical
review still `PENDING`. This application does not issue clinical approvals.

For a recorded source, prepare and independently review a local extraction with
the exact CSV columns shown in `authored-measurements.csv`, document its source and
transformation in the review rationale, use `source_kind: RECORDED`, and pin its
bytes and parent source files. A hash binds evidence; it does not authenticate the
reviewer, verify an extraction against original records, or confer clinical validity.
Do not copy this fixture's review identity or invented item mappings to real data.
No recorded heart-rate/respiratory-rate pack has been clinically approved here.

Technical bounds are four MiB per input, twenty thousand CSV rows, eight additional
variables and thirty-minute maximum lookback. Finite numeric values require plain
decimals with at most twelve integer and twelve fractional digits, protecting exact
arithmetic from excessive exponents. Nonnumeric/nonfinite values, wrong units,
unselected items and incompatible clocks do not become feature evidence. Duplicate
identities and a known stay assigned to the wrong patient cause admission failure.
These are research-prototype bounds, not a full-cohort deployment claim.

Retained snapshots contain the admitted review, source hashes, query binding and
selected evidence. Source or pack drift blocks new work. A durable completed job can
still be inspected, compared and exported after its CSV changes or disappears;
replay requires the original admitted source and explicit local pack again.
