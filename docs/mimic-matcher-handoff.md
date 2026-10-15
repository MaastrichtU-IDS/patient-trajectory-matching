# MIMIC staging to matcher handoff

**Implemented profile:** `mimic-matcher-handoff-1.0`, standard-library Python, 14 tests.
It carries staged MIMIC input segments to the point-anchor matcher, or refuses and names
who must supply what is missing. It establishes no clinical validity and makes no cohort
claim.

## What this closes

[`mimic_inputevents`](mimic-inputevents-staging.md) stages rows and stops at
`STAGED_NOT_MATCHER_READY`, listing five blockers. Nothing carried those segments
further: [`pro_solid.build_graph`](../patterns/pro_solid.py) is the only route to the
matcher and it reads constructed synthetic JSON. **No source row had ever reached a
cohort answer.** That was [S1](issues.md).

The mechanism now exists. The judgements stay where they belong.

| Staging blocker | Discharged by | Supplied by |
|---|---|---|
| `PATIENT_LOCAL_CLOCK_BRIDGE_REQUIRED` | `clock_bridge` | this repository |
| `OCCURRENCE_PRECISION_POLICY_REQUIRED` | `occurrence_policy` | this repository |
| `CLINICAL_MAPPING_REVIEW_REQUIRED` | `clinical_review` | **D-CLINICAL** |
| `SOURCE_AVAILABILITY_UNVERIFIED` | `source_availability` | **D-HISTORY** |
| `SOURCE_REVISION_HISTORY_UNAVAILABLE` | `revision_history` | **D-HISTORY** |

Two are dischargeable here because the repository already owns what they need: the
[patient-local clock bridge](patient-local-clocks.md) exists, and an occurrence policy is
a statement someone writes down. The other three require a clinical reviewer or the
source owner. **No declaration written in this repository can stand in for them**, which
is why the committed declaration refuses.

## Run

```sh
python -m patterns.mimic_inputevents
python -m patterns.mimic_matcher_handoff
python -m patterns.test_mimic_matcher_handoff
```

The committed [declaration](../examples/mimic-matcher-handoff/declaration.json) refuses,
because it is honest about what has not been reviewed:

```json
{"status": "REFUSED_NOT_MATCHER_READY", "discharged": 2,
 "outstanding": ["CLINICAL_MAPPING_REVIEW_REQUIRED", "SOURCE_AVAILABILITY_UNVERIFIED",
                 "SOURCE_REVISION_HISTORY_UNAVAILABLE"],
 "rows": 0, "review_basis": null}
```

A refusal emits no rows, no manifest and no warrant. There is no partial bundle a caller
could mistake for a handoff.

## Executing the path

A review may declare itself `synthetic`. The path then runs end to end so that it can be
tested, and every artifact carries `review_basis: synthetic` and
`clinical_validity_claim: false`:

```sh
python -m patterns.mimic_matcher_handoff \
  --declaration examples/mimic-matcher-handoff/synthetic-review-declaration.json \
  --output verification/mimic-matcher-handoff-run/synthetic.json
```

This mirrors the `stipulated` basis in
[source completeness](decisions/source-completeness.md): the mechanism stays honest by
naming what decided it, never by pretending no decision was made.

The emitted `rows` and per-patient `manifests` are admitted directly by the existing
projection — `project(build_graph(rows), manifest)` — with no further field invented in
between. That is the claim this profile makes, and `test_the_handoff_reaches_the_matcher_and_carries_its_warrant_there`
is the check.

## The answer is UNRESOLVED, and that is correct

Running the synthetic path to the matcher gives:

```
patient 1: UNRESOLVED  reason_codes=['INCOMPLETE_SOURCE_SEARCH']
           uncovered=['concept ex:Creatinine never searched for',
                      'concept ex:DrugB never searched for']
```

This is the right answer, not a shortcoming. `inputevents` holds administrations; it was
never searched for creatinine. The exemplar needs a creatinine rise, so no conclusion
about it is available from this extract — and adding the concept to the mapping does not
help, because the table holds no measurement rows. The gap is a property of the extract.

Before this path existed that gap was invisible. The derived coverage warrant is what
makes it speak. Answering the exemplar from real source needs the measurement side as
well; see [measurement claims](measurement-claims.md) and
[mixed record queries](mixed-record-query.md).

## The warrant is derived, not declared

[Source completeness](decisions/source-completeness.md) left one thing open: declaring a
window does not establish that the search of that window was exhaustive. **For this
source it can be established**, because staging gives every input row an outcome and
reports `reconciliation_complete`. The warrant is therefore computed from the
reconciliation and refuses to exist when that flag is false — the one case where the
window would be a claim rather than a measurement.

The span covers **every reconciled row, not only the staged ones**. An unsupported or
invalid row was still looked at. A window drawn around the staged segments alone would
narrow each time a row was rejected, which inverts what coverage means.

## Scope and limits

- The occurrence policy is a stated reading of recorded endpoints. MIMIC-IV documents
  charttime as an observation-time proxy and storetime as entry or validation time;
  neither establishes historical availability or physical administration times.
- A recorded component segment is admitted as `performed` **only** under that declared
  policy. Staging itself never makes this claim.
- `age` is taken from the declaration, never derived. MIMIC-IV top-codes ages above 89 as
  91, which is a de-identification category rather than an age.
- A declaration is bound to one `staging_context_id`. A reviewed mapping is a judgement
  about one reconciled extract and cannot be reused against another.
- Only `inputevents` is handled. Other tables, FHIR ingestion, and the measurement side
  of the exemplar remain unimplemented.
- `S1` stays open as **[partial]**: the code is no longer the blocker, the review is.
