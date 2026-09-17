# Bounded patient similarity and immutable refinement

`patterns/patient_similarity.py` implements the H0 query-by-example component from
[the specification addendum](../addenda/specification-2.1.md). It ranks the full
authored candidate population, exposes component evidence and coverage, and stores
content-addressed parent-linked revisions. This is a research prototype with
explicit demonstration parameters, not a clinically validated similarity model.

## Shared authored cases and the index

The default input in `examples/patient-similarity/` uses the same P00–P10 episodes
and projected events as `demo/cohort-data.json`. Its `construction_origin` records
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
| Selected concepts | 1 minus Jaccard similarity after the pinned toy ancestor expansion | Uses recorded pre-index administration concepts in this fixture. A partial concept set is missing for distance calculation; a known positive concept can still satisfy a hard predicate. |

The pinned toy expansion adds `ex:DrugA` to `ex:DrugAChild`. `ex:DrugB` is not an
entailed `ex:DrugA`. This small reviewed authored expansion is not a general OWL
reasoner. The existing trajectory evaluator separately controls named substitutions
and temporal relaxation; similarity distance is not its relaxation cost.

An observed empty concept set requires an explicit completeness declaration.
Absent records never establish a negative clinical fact. P10 has an incomplete
source search: its concept-set component reduces similarity coverage, while its
known DrugA record can still satisfy a positive DrugA filter. A filter requiring
an unobserved DrugB is unresolved for P10.

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
evidence = engine.inspect(filtered['revision_id'], 'P02')
manifest = engine.manifest(filtered['revision_id'])
replayed = SimilarityEngine.replay(manifest)
assert replayed.get_revision(filtered['revision_id']) == filtered
```

`metadata()` returns the patient list, component names, default weights, limits,
source SHA and availability policy. `get_revision(id)` retrieves a defensive copy.
A constructor may receive dictionaries or local JSON paths; the scope remains
explicitly authored synthetic. This constructor is not an arbitrary clinical data
import endpoint.

Supported hard predicates are age-band `eq`, creatinine inclusive `between` with
`min`/`max` decimal strings, and reviewed concept `contains`. Unknown concepts,
unrecognized fields, incompatible units or clocks, inconsistent index rules,
non-finite numbers and unsupported operations fail admission or validation.

Each revision records its parent, dataset/profile/implementation hashes, reference
patient and episode, immutable full base pool, index rule and history window,
query and result hashes, operation and result. The replay manifest embeds the full
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
resource admission and extreme decimal exponents. Clinical review, source-specific
availability policies, held-out retrieval relevance, outcomes, all-pairs retrieval,
general semantic similarity, and production security remain separate work.
