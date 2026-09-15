# Checked semantic support for bounded temporal queries

`semantic-support-1.0` connects **rustDL 0.4.28** to the bounded temporal matcher. It derives named process-class membership from an explicit semantic module, checks the entire requested class-support table against an independent finite-rule evaluator, then performs the existing fixed-witness temporal join. It is an optional Python/Rust integration; no Java runtime is used.

The example derives a synthetic antibiotic-administration class through a drug role and its bearer. No antibiotic-administration type is asserted directly. Its temporal query returns P1 certain, P2 possible, P3 no recorded match, and P4 incomparable. These are constructed cases, not an approved clinical phenotype.

## Run

```sh
python -m pip install -r patterns/requirements-semantic.lock.txt
python -m patterns.semantic_support
python -m patterns.test_semantic_support
```

The CLI reads the existing [bounded source](../examples/bounded-interval/source.json), a [semantic module](../examples/semantic-support/module.json), and a [query](../examples/semantic-support/query.json). It writes `verification/semantic-support-run/result.json` and the exact reasoner input `module.ofn`. All 22 tests require the pinned native package; missing Rust bindings fail the suite rather than skipping integration tests. The original dependency lock and entry points still work without this optional package.

`--source`, `--module`, `--query`, `--output` and `--timeout-seconds` override these inputs. Invalid inputs and temporally inconsistent sources exit 2 before generating a new report. Semantic inconsistency or an unresolved backend also exits 2, but preserves the current gate report with `matching: null`.

## Logical fragment and its limits

The semantic module is a restricted JSON representation of these OWL axioms:

| Admitted form | Meaning |
|---|---|
| Named class and individual declarations | A finite vocabulary, with no punning |
| Named class assertions | Explicit semantic evidence about named individuals |
| Object property assertions | Only existing `sulo:hasParticipant` and `sulo:isFeatureOf` |
| `SubClassOf(L C)` | `C` is named; `L` is a named class, intersection, or existential restriction, recursively |
| Pairwise named `DisjointClasses` | An individual cannot belong to both classes |

Only the **left** side of a rule can contain existential restrictions. No rule creates a new individual or property edge. Empty antecedents, right-side existentials, unions, negation, nominals, cardinalities, identity axioms, property characteristics, property chains, datatypes and imports are rejected. The interface adds no application object or datatype properties.

The combined projection/module is limited to 32 classes, 64 named individuals, 256 class assertions, 256 property assertions, 64 rules, 32 disjoint pairs, 256 expression nodes and expression depth four. Queries admit up to eight slots and the existing `before`, `meets`, directional `overlaps` and bounded `gap` operators. Existing distinct-slot, patient/episode and integer-microsecond restrictions still apply. These limits define a small conformance integration, not a clinical-scale performance claim.

The JSON module has exactly `profile`, `source_sha256`, `classes`, `individuals`, `class_assertions`, `property_assertions`, `rules` and `disjoint`. A rule has `id`, `if` and `then`; expressions use `{"class": IRI}`, `{"all": [expressions]}` or `{"some": {"property": IRI, "filler": expression}}`. See the complete example above. Unknown fields fail validation, so an unsupported axiom cannot be silently omitted.

## Projection and source responsibility

The prepared bounded snapshot supplies each process's admitted named types, its original patient-role and bearer IRIs, their profile types, and the two explicit PRO edges. The existing pinned named-subclass traversal supplies the process types; this is recorded projection policy, not newly claimed full OWL reasoning. Additional declared classes, individuals, assertions and rules come from the semantic module. Neither the original RDF graph nor its temporal constraints is modified.

The module must carry the SHA-256 of the canonical selected bounded source. A correction to that source invalidates the pairing until semantic evidence has been selected again. **This hash checks pairing, not historical availability or clinical truth.** The caller must supply reviewed, appropriately selected semantic assertions; this integration does not select drug-role evidence from the archive or establish its availability at a cutoff. Source-specific mappings and semantic evidence replay remain separate work.

The generated ontology is an explicit projection plus an explicit module. It does **not** import the full SULO ontology, its anonymous restrictions or the standalone formal temporal core. A consistent projection does not establish consistency of those larger ontologies. All output records `full_owl_mapping_verified: false`. The selected source context, module, model hash, OFN hash, backend evidence and implementation hashes are included in the execution context.

## Example: a role-mediated class inference

The example's sufficient condition is:

```text
DrugAdministration
  and (sulo:hasParticipant some
    (AdministeredDrugRole
      and (sulo:isFeatureOf some Antibiotic)))
  subClassOf AntibioticAdministration
```

The role and its antibiotic bearer must form **one connected witness**. A typed drug role on one participant and an antibiotic bearer on another do not satisfy the condition. Likewise, generic participation without the role type is insufficient. This is a one-way sufficient condition, not an equivalence or a complete definition of clinical antibiotic administration.

Patient capacity remains the original Process → PatientRole → Person path in the temporal snapshot. A newly inferred type on another named individual does not turn it into a candidate process. Drug-role assertions cannot change the patient's recorded binding. Every accepted temporal slot retains the process, patient role, bearer and temporal evidence identifier, together with its semantic support context.

## Why the finite checker can decide this fragment

The independent [reference evaluator](../patterns/semantic_reference.py) starts with the asserted class memberships and property edges. It repeatedly adds each rule's named conclusion for every individual satisfying its antecedent. Intersections require all conjuncts; existentials traverse one actual edge to a suitable filler. It never parses the generated OFN or calls Rust or the temporal compiler.

There are finitely many individual/class pairs. Rules only add memberships, so saturation terminates. Each addition follows an OWL-valid implication, establishing soundness. If no disjoint pair clashes, interpret named individuals distinctly, properties as the asserted edges and classes as the saturated memberships. This interpretation satisfies every admitted rule and disjointness axiom. For an empty individual set, add one untyped domain element. A missing membership is false in this model and therefore is not entailed. A disjointness clash is inconsistent because both memberships were derived soundly.

This countermodel argument establishes completeness for **ground named-class entailment in the admitted fragment**. It does not impose an OWL unique-name assumption: choosing distinct names constructs a permitted countermodel. It establishes neither entailed inequality nor identity normalization. A non-entailed class assertion is not an entailed class complement and is not clinical absence.

The report retains asserted memberships, a deterministic sequence of rule derivations with premises, and any disjointness clashes. These are reference derivations, not rustDL proof certificates. The tests additionally enumerate all class and property extensions on small finite cases, including unasserted edges, and compare their common consequences to the checker and Rust.

## Backend gate

A separate process runs the pinned Python bindings using horned-owl inside rustDL to parse generated Functional Syntax. It checks `dropped_axioms`, then `is_consistent`, then `instances_of` once per distinct requested class. This avoids one native call per candidate/slot pair; results are reused across every episode and binding in that execution. There is no persistent cache. The process has a configurable wall timeout (20 seconds by default, maximum 60), and inherited `RUSTDL_*` options are removed. Native and Python-wrapper file hashes identify the actual installed implementation.

The backend is pinned because the Python instance-query surface discards a truncation flag that exists in the Rust API. Source inspection at commit [`fe3b3218`](https://github.com/MaastrichtU-IDS/rustdl/tree/fe3b3218a3c41d4e9030eda7a3ce347e20ec906f) found this distinction in [Python query bindings](https://github.com/MaastrichtU-IDS/rustdl/blob/fe3b3218a3c41d4e9030eda7a3ce347e20ec906f/crates/owl-dl-py/src/queries.rs) and [Rust realization](https://github.com/MaastrichtU-IDS/rustdl/blob/fe3b3218a3c41d4e9030eda7a3ce347e20ec906f/crates/owl-dl-reasoner/src/realize.rs). The wheel's recorded hashes identify the executed binary; the inspected source SHA alone does not attest its build provenance.

Consequently, a Boolean or a missing instance from that Python API is never sufficient evidence of completeness. Every run must agree with the finite checker on consistency and **all named instances of every requested class**, including non-process individuals. A warning, stderr diagnostic, dropped axiom, malformed response, wrong version, execution failure or disagreement blocks the entire semantic gate. Even plausible positive answers are withheld on a blocked run. An undetected internal cut that happens to return the exact checked table does not compromise this fragment's answer; the checker supplies the completeness evidence. This is not a claim that Rust's general instance API is complete.

| Gate status | Matching behaviour |
|---|---|
| `READY` | Complete checked support table; execute the bounded join |
| `INCONSISTENT_ONTOLOGY` | Both evaluators agree on inconsistency; retain clashes, return no cohort answer |
| `UNRESOLVED_SEMANTICS` | Retain reason and backend evidence; return no cohort answer |

A completed join still distinguishes certain, possible, incomparable and no recorded match. Certainty is evaluated for a fixed named binding across all feasible source timelines before projecting patient IDs. The semantic gate never narrows the source timeline with the query.

## Verification and next boundary

The 22 new tests cover real Rust inference, role-witness correlation, cycles, conjunctions, existential antecedents, disjointness, absent membership, finite countermodels, preserved PRO evidence, temporal results, stale-source rejection and failure handling. They also compare the new entry point to the existing bounded matcher for existing selectors. The existing temporal and paired-interface suites remain regression gates after extracting the shared join.

This adds operation-specific evidence for the integration path relevant to formal checks C03 and C05. It does not rerun, replace or close the [original 18 OWL checks](temporal-kg/validation/README.md) or their [SULO mapping obligations](temporal-kg/sulo-conformance.json). The `temporal-interface-1.0` paired mapping still admits only its common `Process` selector; this new profile is a separate extension.

The next semantic milestone is a backend API that retains per-operation completeness diagnostics, followed by independently tested fragment expansion and full import-closure/identity handling. The next source milestone is to select and reconcile semantic role evidence alongside temporal evidence under the same availability policy. Any scale optimization must preserve this checked support table and the existing temporal differential results.
