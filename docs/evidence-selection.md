# Evidence selection for bounded temporal matching

**Status:** Executable `evidence-selection-1.0`, a closed selection layer feeding the existing bounded interval matcher. It adds no RDF properties, temporal operators, general OWL reasoning, or historical ontology replay. The input examples are constructed normalized assertions, not clinical source imports.

This profile implements a subset of [formal definition v2](temporal-kg/Temporal_Knowledge_Graph_Formal_Definition_v2.pdf) Q7 and the [replay addendum](../addenda/specification-2.3.md): a supplied immutable archive, explicit source-support revisions, per-patient availability cutoffs, and retained evidence. Broader Q7 decisions remain open in the [revision response](temporal-kg/revision-response.md).

## Run and observe a correction

```sh
python -m patterns.evidence_selection
python -m patterns.test_evidence_selection
python -m patterns.evidence_selection --request examples/evidence-selection/retrospective-request.json
```

The CLI accepts `--archive`, `--request`, `--query` and `--output`. Its default output is `verification/evidence-selection-run/result.json`; use separate output directories to retain several runs. A subsequent invocation with the same output directory replaces that file. Each result contains the archive, request, selection decisions, supporting entries, version fingerprints, and any matcher result.

The default archive contains an administration A at `[0,1)` and an original collection B at `[3,4)` in constructed microsecond coordinates. A later source correction changes B to `[0,1)`. The clinical coordinates are separate from the January dates used for source availability.

| Request | Selected collection assertion | Matching result |
|---|---|---|
| Source-as-known, 3 January cutoff | Original, available 2 January | P1 is a certain match for A strictly before B |
| Retrospective, same cutoff | Correction, available 4 January | P1 has no recorded match; `later_evidence_used` is true |
| Source-as-known, 4 January cutoff | Correction, eligible at the inclusive cutoff | P1 has no recorded match |

The correction changes an assertion about the same event. It does not create an additional collection or remove the separate administration. These are computational examples, not an approved clinical cohort definition.

## Archive and request contracts

The [schema](../schemas/evidence-selection.schema.json) validates the archive and its `$defs.request`. The [implementation](../patterns/evidence_selection.py) additionally checks identities, timestamp values, integer bounds, revision structure and the selected source's consistency.

An archive identifies its dataset, archive version, publication time, declared history coverage, availability axis, mapping policy and clinical clocks. It contains:

- **Assertions:** each has an identifier, patient/episode scope, and a finite bundle of normalized bounded-profile event, variable and constraint rows. The bundle is activated or removed as a whole.
- **Support entries:** each identifies a source channel, patient/episode scope, source availability, optional source recording time, and archiving time. An assertion entry references an assertion; a withdrawal has no assertion and explicitly targets a preceding support entry.
- **Revision links:** an entry may `supersedes` one preceding support entry in the same source channel and patient/episode. The mapping must establish these links; later ingestion never creates them automatically.

`source_id` identifies the source channel whose revision chain is being followed. Entry IDs identify individual support operations. Assertion IDs identify normalized bundles. Event IDs identify the represented processes. These identities have distinct roles, even when two sources support identical content.

The request selects `source_as_known` or `retrospective`, supplies an inclusive absolute cutoff for every represented patient, and explicitly lists admissible source channels. This profile fixes the policies to `explicit-source-chain-1.0`, missing availability `block`, and semantic policy `current-pinned-retrospective`. Unknown fields and other policy names fail validation.

Availability and archiving dates require explicit UTC offsets and at most microsecond precision. They use one absolute availability axis; clinical clocks can remain separate. Cutoffs cannot exceed archive publication, entries cannot be archived after publication, and known availability cannot postdate archiving. Source recording time never substitutes for missing availability. Shifted patient availability clocks, derived index cutoffs and calendar-time policies need a different mapping/profile.

The archive is supplied as a complete artifact for one publication. Entries beyond its publication date are invalid input; this profile is not a product-archive database that can reconstruct older archive versions automatically. Replaying another product snapshot requires its separate archive artifact.

## Selection algorithm and evidence

1. Validate the archive and request, including all normalized row shapes, identifiers, support references and revision chains. Cycles, cross-source/scope corrections and successors with an earlier known availability time fail validation. An assertion with no support entry anywhere in the archive is invalid.
2. Exclude inadmissible sources. In source-as-known mode, admit known availability at or before each patient's cutoff. Retrospective mode admits known availability throughout the supplied archive and marks use of evidence after the requested cutoff.
3. Apply eligible revision links. An entry is superseded even when its successor is itself later superseded or withdrawn. Thus withdrawing a correction does not resurrect an ancestor. Reassertion needs a new explicit assertion entry.
4. Activate assertions with at least one remaining eligible support entry. Withdrawing one source's support does not remove independent support from another source.
5. Coalesce identical complete normalized rows and retain all contributing assertion/support IDs. Different rows claiming the same row kind and identifier block the projection. Missing dependencies, scope violations and inconsistent temporal constraints never become ordinary no-match results.
6. Compile a valid selected source with the existing bounded compiler and execute the unchanged fixed-witness matcher.

Revision forks block once the competing successors are eligible, including branches with later descendants. This initial policy supports linear source revision chains and requires forks to be resolved upstream through a reviewed policy. An unavailable ancestor also blocks use of a successor.

Row coalescing uses exact normalized content, including `source_key` and event `record_id`; it does not infer identity from equal values. Sources can support one assertion through separate support entries. Different assertion IDs can also contribute identical rows. Different metadata on otherwise similar rows remains a structural projection conflict requiring reviewed normalization, not proof of a clinical contradiction.

All selected constraint rows are retained, including constraints outside a query's candidate events. Shared variables remain shared. No endpoint sampling, independent-marginal substitution or query-based deletion of inconvenient source evidence occurs.

The result's `selection.row_supports` maps keys such as `variables:Bs`, `events:B` and `constraints:C1` to assertion IDs and active support IDs. Those IDs resolve into the included archive. The nested bounded matcher retains its existing event evidence and source-bound/query certificates. The selection envelope joins that normalized evidence to the support archive without claiming the bounded graph is an RDF serialization of the revision history.

## Outcomes and completeness

| Selection status | Meaning | Matcher invoked? |
|---|---|---|
| `READY` | Selected rows satisfy the bounded source contract and source constraints are feasible | Yes |
| `EMPTY_SELECTED_EVIDENCE` | No rows remain under the declared selection policy | No; no clinical absence conclusion |
| `BLOCKED_EVIDENCE` | Unknown admissible-source availability, partial/unavailable declared archive history, revision fork/ancestor problem, or structural row/scope conflict | No |
| `INVALID_SELECTED_SOURCE` | Selected rows cannot form the supported bounded source, for example a dangling endpoint reference | No |
| `TEMPORAL_INCONSISTENCY` | Accepted rows compile to contradictory source constraints | No; the negative-cycle evidence is retained |

Malformed archive/request/query input raises a contract error; the CLI reports `INVALID_INPUT`. Successful and empty selections exit 0. Blocked or inconsistent selections write their audit report and exit 2. Runtime failure does not generate a completed result.

`selection_complete` means the finite selection procedure completed. `replay_coverage` is `COMPLETE`, `PARTIAL` or `UNAVAILABLE`: it starts from caller-declared archive history coverage and degrades to `PARTIAL` for unknown availability in an admissible source. Completeness of source history is not independently verified. Even `COMPLETE` refers only to the declared archive scope, not all clinical events or what clinicians knew.

Unknown availability or incomplete history conservatively blocks the entire run, including retrospective mode. This avoids presenting known-subset matches as definitive replay answers. Per-patient unresolved membership with unaffected-patient execution is a later capability. An intentionally excluded source does not create an unknown-availability blocker.

The bounded schema requires at least one variable and one event. A selection with only leftover variables/constraints is `INVALID_SELECTED_SOURCE`, not silently pruned to empty. A future profile could explicitly admit those cases.

## Reproducibility and implementation limits

Archive and request inputs are copied; evaluating another cutoff does not mutate the caller's archive or earlier returned results. Returned Python dictionaries are ordinary mutable objects, not tamper-proof storage. Persisted result bytes, their context and the original archive define the reproducible artifact.

The selection identity includes the complete archive digest, request, declared mapping policy and relevant runtime/schema/ontology file hashes. The execution identity additionally binds the query and matcher context. Reordering archive arrays can change the archive/context hash while leaving selection and matching equivalent; these hashes identify execution inputs, not semantic equivalence classes.

`mapping_policy_id` is a caller declaration, not a verified source mapping. The normalized assertion rows and correction links are input claims. Source-as-known filters availability, but both modes use the current pinned implementation and semantics; neither reconstructs which ontology or mapping was historically available. `current-pinned-retrospective` makes that distinction explicit in every request/result. No rustDL or full OWL consistency claim is added.

The normalized row bundles feed the existing PRO/SOLID graph builder. This profile adds no classes or RDF properties and does not admit arbitrary OWL axiom bundles, source RDF archives, persistent database storage, general state validity, clinical ETL, derived-index replay, relaxed interval matching or a replay UI.

The 29-test suite covers correction timing, repeated events, independent duplicate support, withdrawals without resurrection, row conflicts, eligible forks, revision cycles, malformed scope/precision/policies, unknown availability, incomplete history, detached results, temporal inconsistency and CLI outcomes. Sixty seeded linear histories are compared with an independent chronological-chain reference. Selected temporal answers are checked against the finite-world reference, including shared uncertainty whose marginal ranges overlap while its gap is certain. These scenarios are nested within the 29 top-level tests.

Next work is source-specific mapping and history-coverage evidence, finer-grained unresolved results, and explicit integration with the ontology support interface. The complete replay requirements and Q7 remain broader than this bounded contract.


The separate [joint evidence-selection profile](joint-evidence-selection.md) extends this shared revision algorithm to semantic fact bundles and the checked Rust matcher. It uses the same patient cutoffs and current-rule policy; this original archive profile remains temporal-only.
