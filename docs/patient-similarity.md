# Bounded patient similarity and immutable refinement

`patterns/patient_similarity.py` implements the H0 query-by-example component from
[the specification addendum](../addenda/specification-2.1.md). It ranks the full
authored candidate population, exposes component evidence and coverage, and stores
content-addressed parent-linked revisions. This is a research prototype with
explicit demonstration parameters, not a clinically validated similarity model.

## Shared authored cases and the index

The default input in `examples/patient-similarity/` uses the same P00–P10 episodes
and projected events as `examples/guided-cohort/cohort-data.json`. Its `construction_origin` records
the original file's SHA-256. The builder copies normalized measurement values,
original values and units, source-row hashes, and PRO/SOLID bindings. It adds an
**authored availability overlay**: an event is available at its occurrence time;
the manifest's age-band declaration is available one hour before the index. The
original guided fixture expressly makes no source-availability claim. The overlay
is a new synthetic assumption, not evidence about source systems or real patients.

Each patient's index is their own fixed episode-relative origin, with their own
clock. Occurrences must be strictly earlier than the index; availability must be
known and no later than the index. An unknown availability time is ineligible.
The relative origin coincides with the authored F observation; that observation is
excluded from similarity. This fixed synthetic landmark is not the clinical
study's antibiotic-derived index rule, and the cohort's F observation is not a
validated post-index outcome. Follow-up outcomes are not implemented by this engine.

The profile uses a 14-day history to retain the existing authored exposure cases,
and a separate inclusive 48-hour baseline window. This differs explicitly from the
seven-day clinical study profile. The 11 authored records do not establish clinical
eligibility, real historical availability, or clinical-scale performance.

## Components and missing information

| Component | Selector and distance | Evidence and uncertainty |
|---|---|---|
| Age band | Latest eligible declaration; 0 for equal declared bands, otherwise 1 | A band remains a band, not an exact age. Conflicting equally recent declarations are missing. |
| Baseline creatinine | Minimum eligible normalized value in the preceding 48 hours; `min(1, abs(candidate-reference)/1 mg/dL)` | Units must already be mg/dL. Original mg/L values and the existing conversion bindings remain inspectable. |
| Selected concepts | By default simGIC, the IC-weighted overlap after the pinned toy ancestor expansion; ancestor-Jaccard or best-match Resnik on request (below) | Uses recorded pre-index administration concepts in this fixture. A partial concept set is missing for distance calculation under every measure; a known positive concept can still satisfy a hard predicate. |

The pinned toy expansion adds `ex:DrugA` to `ex:DrugAChild`. `ex:DrugB` is not an
entailed `ex:DrugA`. This small reviewed authored expansion is not a general OWL
reasoner. The existing trajectory evaluator separately controls named substitutions
and temporal relaxation; similarity distance is not its relaxation cost.

An observed empty concept set requires an explicit completeness declaration.
Absent records never establish a negative clinical fact. P10 has an incomplete
source search: its concept-set component reduces similarity coverage, while its
known DrugA record can still satisfy a positive DrugA filter. A filter requiring
an unobserved DrugB is unresolved for P10.

## Information-content measures

The concept-set component is scored by one of three measures, chosen per query. Every
new query names its measure in `query.concept_measure`, and `simgic` is the default.
A query without the key was recorded before measures were named, when
ancestor-Jaccard was the only one, so it is scored by `ancestor_jaccard`
(`metadata()['legacy_concept_measure']`). Committed revisions and replay manifests
therefore reproduce exactly. That is the versioning: the key's presence says the
query names its measure, and its absence fixes ancestor-Jaccard.

| `concept_measure` | Distance | Symmetric |
|---|---|---|
| `ancestor_jaccard` | 1 − \|A ∩ B\| / \|A ∪ B\| over ancestor-expanded sets | Yes |
| `simgic` (default) | 1 − Σ IC(A ∩ B) / Σ IC(A ∪ B) over the same expanded sets | Yes |
| `resnik_bma` | 1 − Σ over the reference's recorded concepts *t* of max Resnik(*t*, *u*) over the candidate's recorded concepts *u*, divided by the same sum with the reference as its own candidate | No: reference to candidate |

Resnik(*t*, *u*) is the IC of the most informative term in both *t*'s and *u*'s
ancestor-or-self sets, and 0 when they share none. Dividing by the reference's
self-match keeps `resnik_bma` in [0, 1]; it measures how much of the reference the
candidate explains, so a candidate with more recorded concepts is not penalized for
them. Ties pick the term with the highest IC, then the lexicographically first
ancestor, then the first candidate concept.

IC(*c*) = −ln *p*(*c*), with *p*(*c*) = (count(*c*) + 1) / (*N* + 1):

- The population is the admitted snapshot: every patient whose concept set is
  observed, expanded as for distance. *N* is their number, and count(*c*) is how many
  carry *c*. A patient whose set is partial or has no eligible record is left out of
  both, because an absent record is not evidence of absence. The population does not
  depend on the reference patient, the base pool or hard filters.
- Add-one smoothing keeps IC finite and bounded. A reviewed concept that no observed
  patient carries has IC ln(*N* + 1), the largest possible value. A concept every
  observed patient carries has IC 0. An empty population (*N* = 0) gives IC 0 for every
  concept.
- A root, meaning a term in the ancestor-or-self set of every reviewed concept, has
  IC 0 regardless of counts. The authored profile has no root.
- IC is a natural logarithm, computed with `Decimal.ln` at 28 significant digits and
  rounded half-even to 12 decimal places. That exact decimal is used in the measures,
  so the table does not depend on the platform or the caller's decimal context.
- If the denominator is 0 because every term involved has IC 0, the distance is 0, as
  an empty union is under Jaccard. Because the reference and candidate are both in the
  population, an IC-0 union implies equal sets. Two declared empty sets are at distance
  0 under `simgic`, and an empty set against a non-empty one is at distance 1.
- Under `resnik_bma`, a declared empty **reference** set leaves nothing to explain, so
  every candidate would be a perfect match. The concept component is missing for that
  comparison instead, with `explanation.undefined = 'EMPTY_REFERENCE_SET'`. It then
  counts as worst-case distance and reduces coverage, equally for every candidate, as
  any missing component does. A declared empty candidate set explains nothing and is
  at distance 1.

In the authored snapshot *N* = 10 (P10's set is incomplete). Eight patients carry
`ex:DrugA`, one `ex:DrugAChild` and two `ex:DrugB`, giving IC 0.200670695462,
1.704748092238 and 1.29928298413. P00 {DrugA} against P02 {DrugA, DrugAChild} is 0.5
under Jaccard and 0.8947 under simGIC. Under `resnik_bma` it is 0 from P00 to P02,
and 0.8823 from P02 to P00.

A result records its measure, and under an IC measure its population.
`results.concept_measure` carries the measure and, for `simgic` and `resnik_bma`,
`ic_population`, which gives the basis, dataset and profile digests, *N*, the number
of unobserved patients, a digest of the per-concept counts, the roots, the smoothing
rule, the logarithm and the rounding. Because these sit inside `results`,
`result_sha256` binds them. The concept component of each comparison adds `measure`
and a term-by-term `explanation`. For `simgic` that is each expanded term with its IC
and whether it is shared or on one side only. For `resnik_bma` it is each reference
concept with its best candidate concept, shared ancestor, matched IC and self IC. An
ancestor-Jaccard comparison carries neither field, and a query recorded without a
measure adds nothing to its results.

Not implemented: per-axis declared weights (findings, diagnoses, medicines), IC
from ontology postings at MIMIC scale, sparse or approximate retrieval, and a recall
benchmark. Scoring stays exhaustive over the admitted snapshot.

## Ranking

For weights `w`, the observed distance averages available component distances,
coverage is the observed weight divided by total configured weight, and ranking
uses `1 - coverage * (1 - observed_distance)`. Equivalently, each missing component
receives distance 1 in the ranking numerator. Losing a feature cannot improve this
ranking distance when the profile and weights stay fixed. No observed pair means
unrankable; coverage below the profile's 0.5 threshold is explicitly unrankable.
Hard eligibility and rankability are separate fields.

Weights, measurement values and distances use exact rational arithmetic for
ordering. Response decimal strings use a fixed 28-significant-digit presentation;
`ranking_fraction` preserves the exact numerator and denominator. Ties use patient
ID, independently of input ordering or the process's decimal context.

## Python interface

```python
from patterns.patient_similarity import SimilarityEngine

engine = SimilarityEngine()  # Admits immutable authored dataset and profile.
initial = engine.initial('P00', top_k=2)
focused = engine.refine(initial['revision_id'], {
    'type': 'set_weights',
    'weights': {'age_band': '1', 'baseline_creatinine': '1', 'clinical_concepts': '4'},
})
filtered = engine.refine(focused['revision_id'], {
    'type': 'add_filter',
    'predicate': {
        'component': 'clinical_concepts', 'operator': 'contains', 'value': 'ex:DrugA',
    },
})
weighted = engine.refine(filtered['revision_id'], {
    'type': 'set_concept_measure', 'concept_measure': 'simgic',
})
asymmetric = engine.initial('P00', top_k=2, concept_measure='resnik_bma')
table = engine.information_content()  # Population and per-concept count and IC.
evidence = engine.inspect(filtered['revision_id'], 'P02')
manifest = engine.manifest(filtered['revision_id'])
replayed = SimilarityEngine.replay(manifest)
assert replayed.get_revision(filtered['revision_id']) == filtered
```

`metadata()` returns the patient list, component names, default weights, limits,
source SHA, availability policy, the admitted index rule and history windows, and a
few example concepts, the available concept measures and the default.
`get_revision(id)` retrieves a defensive copy. `initial` takes an optional
`concept_measure`, defaulting to `simgic`, and always records it;
`set_concept_measure` changes it in a refinement. An unknown measure raises
`UNSUPPORTED_CONCEPT_MEASURE`. Over HTTP, `/api/initial` accepts an optional
`concept_measure` and `/api/refine` passes the operation through. The workspace
offers a measure selector: it starts a query with the chosen measure, and changing it
on a revision records a `set_concept_measure` refinement, which can be undone.

A constructor may receive dictionaries or local JSON paths, and admits two scopes:

| `scope` | required `mapping_review` | what it admits |
|---|---|---|
| `authored_synthetic` | `authored_synthetic_review` | the authored cohort |
| `recorded_public_demo` | `pinned_public_demo_source_fidelity` | the pinned open-access MIMIC-IV demo |

This is still not an arbitrary clinical data import endpoint. A `recorded_public_demo`
dataset must carry a `source_pin` whose digests equal
`data/clinical-source-demo-pin.json` exactly, compared against that file rather than
against anything the dataset says about itself, so a cohort cannot name some other
extract as its source. That checks the declaration, not the derivation: what ties the
rows to the release is `tools/build_demo_cohort.py`, which digests all four files before
reading any of them and refuses on a mismatch. A recorded dataset may declare the four
ICU digests, or those plus `hosp/patients.csv.gz` from
`data/clinical-demographics-demo-pin.json` when the cohort derives `age_band` -- those
two sets and no other, so a cohort cannot drop a table it found inconvenient. Both scopes
must declare
`clinical_validity_claim: false`; a dataset that claims clinical validity is refused in
either. `tools/build_demo_cohort.py` derives an admissible demo cohort, and
`age_band` is derived only when the builder is given `--hosp-dir` -- see issue S3.

Supported hard predicates are age-band `eq`, creatinine inclusive `between` with
`min`/`max` decimal strings, and reviewed concept `contains`. Unknown concepts,
unrecognized fields, incompatible units or clocks, inconsistent index rules,
non-finite numbers and unsupported operations fail admission or validation.

Each revision records its parent, dataset/profile/implementation hashes, reference
patient and episode, immutable full base pool, index rule and history window,
query and result hashes, operation and result, and under an IC measure the IC
population described above. Its `scope` is derived from the
admitted dataset -- `bounded_authored_synthetic_exhaustive_ranking` or
`bounded_recorded_public_demo_exhaustive_ranking` -- because a revision is read away
from the server that produced it, and a fixed string there would describe pinned
recorded stays as authored synthetic to every later reader of the export. The replay manifest embeds the full
dataset, including each patient's clock/index/episode, profile and complete ancestor
revision chain. Replaying recomputes every revision and rejects altered context,
parents, queries or results. Changing implementation requires the recorded version;
a digest proves content identity, not trusted authorship.

## What two refinements change

| Stage | Hard eligibility | Display and evidence |
|---|---|---|
| Initial P00, top 2 | All ten other authored patients | P01 and P05 display first; every candidate is evaluated. |
| Concept weight raised to 4 | Same ten patients | Ranking/coverage may change. P10 becomes unrankable because the incomplete concept set now carries most weight. |
| Require recorded `ex:DrugA` | Eight patients; P04 and P06 removed | Positive source evidence admits P02 through the authored ancestor expansion. P08 and P10 remain eligible despite other missingness. |

A hard conjunction evaluates the **full base pool**, never only the displayed
neighbours. P02 can enter a refined eligible cohort even though it was not in the
initial top two. A baseline range filter makes P08 unresolved because its only
baseline is outside the 48-hour window. A known false conjunct excludes a candidate
even when another conjunct is unknown.

`changes.added` and `changes.removed` describe hard-eligibility membership; the
initial revision reports its full eligible pool as added. `changes.unresolved`
reports the current unresolved patients, with reasons. Separate `displayed_added`
and `displayed_removed` fields describe top-k changes, which have no conjunction
subset guarantee. Reweighting cannot alter hard eligibility. Branching from an
older revision preserves all descendants, and callers cannot mutate stored results
through returned objects.

## Evidence and bounds

Inspection shows reference and candidate selected values, source rows and hashes,
PRO process → participant role → bearer bindings, SOLID `hasValue` data, original
units, authored availability assumptions, ignored records with reasons, individual
distances and hard-filter outcomes. It does not assert a `hasPatient` ontology
relation, clinical causality or validated terminology mappings.

Admission is limited to 1,000 patients, 20,000 records and 8 MiB of JSON per input;
file reads stop at the bound plus one byte before parsing. A session stores at most
256 revisions, each with at most 16 hard filters. These are defensive prototype
bounds, not demonstrated service capacity. The engine is an in-memory repository;
the web workspace owns request serialization and persistence/deployment policy.

Run the focused verification:

```bash
python -m unittest patterns.test_patient_similarity -v
```

The tests cover shared-source reconstruction, patient-specific cutoffs, future and
unknown availability, baseline boundaries/minimum selection, source units and roles,
ancestor expansion, incomplete/empty concept sets, missingness penalties, full-pool
refinement, unknown predicates, branching, defensive copies, replay tampering,
resource admission and extreme decimal exponents. For the IC measures they cover a
hand-computed IC table, simGIC against Jaccard, `resnik_bma` asymmetry, smoothing of
unseen terms, the root, an empty population and an IC-0 union, option validation,
recorded population and replay, simGIC as the named default, and a query without a
measure that is byte-identical to the results before the measures were added. Clinical review, source-specific
availability policies, held-out retrieval relevance, outcomes, all-pairs retrieval,
per-axis similarity weights, and production security remain separate work.
