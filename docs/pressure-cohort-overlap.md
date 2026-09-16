# Overlap of the separately reviewed pressure cohorts

The three [separate pressure queries](reviewed-pressure-strata.md) match **23 distinct patients across 29 ICU stays and 196 recorded treatment segments** when their memberships are compared. These are unions of the memberships of three separately executed queries. Measurements have not been pooled into a new query, and baseline/follow-up observations must still share the item required by their own stratum.

The [aggregate overlap report](../verification/reviewed-pressure-overlap-report.json) follows fresh source execution of all three declared queries. Every resulting stratum report exactly reproduces its committed predecessor, including all source, review, implementation and execution hashes. Each run verifies 944 anchors against the unpartitioned SQL reference and retains all 140 stays.

## Different questions at different levels

Patient overlap asks whether the same person has at least one eligible baseline/segment pair in each selected stratum. Those pairs can occur in different stays or at different segment starts. Stay overlap requires the same patient and ICU stay. Segment overlap additionally requires the same recorded treatment anchor. None of these requires the same measurement record across items.

| Result | Patients | ICU stays | Treatment segments |
|---|---:|---:|---:|
| Source population | 100 | 140 | 944 |
| Match in any separate query | 23 | 29 | 196 |
| Match in no separate query | 77 | 111 | 748 |
| Match in all three queries | 1 | 1 | 0 |
| Arterial ∩ Non-invasive (including triple intersection) | 9 | 9 | 4 |
| Arterial ∩ ART label (including triple intersection) | 1 | 1 | 0 |
| Non-invasive ∩ ART label (including triple intersection) | 2 | 2 | 0 |

The per-stratum patient counts were 13, 19 and 2. Their sum, 34, counts membership appearances; the actual union contains 23 distinct patients. Nine patients match both the arterial and non-invasive queries, but only four recorded treatment segments match both. One patient and one stay match all three strata, while no single treatment segment matches all three.

Both ART-label patients also match the non-invasive query somewhere in the same respective ICU stay. However, all three ART-label treatment segments are outside the other two segment sets. Thus, ART contributes no additional patient to the union, while contributing three additional recorded treatment anchors. The ART stratum therefore adds distinct segment matches despite adding no new patients or stays.

## Complete, disjoint accounting

Every row below is an exclusive cell: membership in exactly the listed strata. The eight cells sum to the complete source population at each level. The pairwise intersections above include the triple intersection; the pair-only cells below exclude it.

| Membership in separate queries | Patients | ICU stays | Treatment segments |
|---|---:|---:|---:|
| None | 77 | 111 | 748 |
| Arterial only | 4 | 6 | 62 |
| Non-invasive only | 9 | 13 | 127 |
| Arterial + Non-invasive only | 8 | 8 | 4 |
| ART label only | 0 | 0 | 3 |
| Arterial + ART label only | 0 | 0 | 0 |
| Non-invasive + ART label only | 1 | 1 | 0 |
| All three | 1 | 1 | 0 |

The patient and stay populations come from the full ICU stay roster: 100 patients and 140 stays. The segment population contains the 944 admitted treatment anchors selected by the common treatment query. These are different denominators. A “None” cell means no eligible selected-record pair in these queries; it is not absence of low pressure, treatment or a clinical event. Source admission, recording coverage, selected item and illustrative query windows all constrain the result.

Membership is determined by baseline/segment eligibility. Missing follow-up, negative deltas and repeated follow-up bindings do not remove an eligible member. Counts at the three levels must not be interpreted as independent treatment outcomes or success rates.

## What this establishes for trajectory matching and SULO

A shared patient identifier alone does not establish a shared temporal trajectory. The implementation must preserve the process/role participant binding, the stay scope and the particular event witnesses throughout matching and aggregation. This comparison supplies a concrete example: a patient can satisfy all three item-specific queries without any single treatment segment satisfying all three.

The existing PRO/SOLID representation and bounded matcher retain the identities needed for these distinctions. The overlap verifier adds set comparisons after the separately checked queries; it adds no ontology predicate, clinical mapping or mixed-item temporal inference. It does not establish full mixed OWL reasoning.

## Execution and evidence

[`verify_pressure_overlap.py`](../patterns/verify_pressure_overlap.py) rebuilds each source selection, independent source-fidelity audit and explicitly declared review, and executes the existing Rust/temporal/SQL route. It requires exact equality with each committed stratum report before extracting membership. It then checks common query criteria except the measurement item and request identity, common source files, and identical populations at all three levels.

The internal membership extraction checks completed batches, source/context binding, anchor/reference agreement, full binding accounting, patient sets and the stay/anchor roster. A missing or incomplete stratum, changed source, changed temporal/numeric criteria, repeated measurement item or inconsistent population prevents an overlap report. Existing output survives an invalid run; no partial union is published.

Only the aggregate counts, source manifests, implementation hashes, stratum-report hashes, execution-context hashes and membership-snapshot digests are published. Membership sets, patient/stay/anchor identifiers, source rows and query bindings are not included. The original source is the [open-access MIMIC-IV demo 2.2 release](https://physionet.org/content/mimic-iv-demo/2.2/), with the four files verified against the [published checksums](https://physionet.org/files/mimic-iv-demo/2.2/SHA256SUMS.txt). No credentialed full-MIMIC dataset is used.

```sh
# Existing pinned Python/Rust dependencies and the four original demo ICU files:
python -m patterns.verify_pressure_overlap \
  --input-dir /path/to/mimic-demo/icu \
  --output verification/reviewed-source-query-run/pressure-overlap.json
python -m patterns.test_pressure_overlap
```

The public verifier executes the strata sequentially and retains only their membership sets and aggregate reports between runs. The development reproduction ran the same per-stratum function in independent processes and compared their newly produced memberships. There is no public result-file ingestion, cached acceptance or bypass of the explicit declarations.

Fourteen tests include actual synthetic execution of three distinct item queries, all 4,096 membership-set combinations over a four-member population, patient/stay/segment distinctions, missing-follow-up eligibility, source/query/population mismatch, incomplete-result rejection, provenance, deterministic aggregation and atomic CLI behavior. The 4,096 combinations are nested checks within one test. CI validates synthetic execution and committed aggregate provenance without downloading source records.

## Remaining clinical review

The [candidate plan](../data/clinical-candidate-plan.json) remains proposed for clinical review. These counts now provide a concrete basis for deciding the intended analysis unit and reviewing item meaning, segment-start interpretation, admission rules and temporal windows. They do not authorize a mixed-item query, establish clinical occurrence, compare measurement accuracy or estimate causal treatment effects.
