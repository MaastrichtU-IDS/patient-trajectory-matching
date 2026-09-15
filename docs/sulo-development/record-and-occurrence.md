# D2. Records, claims and occurrence

**Status: proposed general pattern with a bounded executable prototype.** The [current record-query profile](../mimic-record-query.md) remains an explicitly interpreted application projection. This document makes its portability limit visible and specifies the next acceptance gates.

The [claim-projection prototype](../claim-projection.md) now implements structured information-object claims, explicit acceptance/replacement/withdrawal, and a checked empty-Process model for its description graph plus pinned SULO. The accepted view still uses restricted semantic support; clinical mapping and general contextual semantics remain open.

## The distinction we need

| Entity or view | What it represents | What does not follow merely from it |
|---|---|---|
| Source record | A versioned information object containing source content | Every described clinical process occurred |
| Claim description | Structured content describing a process, participants, times or result | The content has been accepted as an assertion about clinical occurrence |
| Recording process, if known | The act of creating, entering or validating information | Its time equals the described clinical time |
| Accepted occurrence view | Assertions admitted under an explicit evidence and interpretation policy | Causation, perfect source completeness or independently verified clinical truth |

These distinctions can share identifiers and provenance without sharing entity identity. Two rows may describe segments of one treatment, different components, corrections, or duplicate observations. Row identity alone cannot decide which clinical process identities should be unified.

## Current implementation and the unresolved semantic issue

The current graph has a `SourceRecord` referring to a `bt:RecordedInputSegment`. That class is a subclass of `sulo:Process`; the process also has PRO participation and an interval description. The result explicitly says recorded-label semantics, with clinical mapping and physical elapsed-time verification false.

This is useful within the declared application interface. However, `rdf:type` and the accompanying property assertions remain ordinary assertions when consumed as an OWL ontology. Naming the class “Recorded” or attaching a status flag does not turn all its assertions into quoted or defeasible claims. The class axiom still entails Process membership. A consumer that discards the interface contract may interpret the export too strongly.

Likewise, a named graph does not by itself specify which assertions an OWL reasoner should accept. Context selection and promotion need an explicit processing contract. This issue is not resolved by the existing finite semantic checker, which verifies consequences of the supplied projection rather than the epistemic justification for that projection.

## Recommended design to prototype

Maintain structured **claim descriptions as information objects**, using application classes and existing SULO relations to identify the subject, participant descriptions, temporal descriptions, values and provenance. A claim describing a process should not itself be typed as that process. Represent an unaccepted process referent through a description/identifier structure rather than automatically asserting its clinical process type and participation edges.

Produce an accepted assertion view through a separate, versioned projection policy. Retain links from every projected assertion or support bundle to the selected source claims and the policy. “Accepted” means accepted for that declared analysis; it must not be displayed as “clinically verified” unless separate evidence supports that claim.

This proposal requires a precise encoding, including how relation positions and claim targets are bound using existing SULO properties. The prototype now specifies binding shapes, selected-dependency checks and a closed RDF round trip for the existing bounded temporal/semantic bundles. General proposition encoding and clinical source mappings remain open. It is not a claim that generic `refersTo` alone encodes a proposition. Do not introduce a parallel ad hoc property vocabulary to avoid specifying these structures.

Use the existing [evidence-selection](../evidence-selection.md) and [joint-selection](../joint-evidence-selection.md) work as starting points for revision and support management. Those profiles do not currently establish trustworthy MIMIC availability times or integrate historical local-clock replay.

## Promotion contract

A promotion record should identify:

- Source dataset, record/revision identities, immutable source hashes and selected support set.
- Mapping and ontology versions, exact asserted conclusions, and their support dependencies.
- Time interpretation and any justified uncertainty bounds, independently of lexical precision.
- Analysis mode, cutoff and history coverage when relevant, and explicit reasons for admission/exclusion.
- How withdrawal or correction invalidates and recomputes dependent assertions and answers.

Source selection is separate from monotonic OWL inference over a selected view. Rebuilding that view after a correction does not make OWL itself a nonmonotonic truth-maintenance system. Retrospective analysis may use a frozen extract without claiming what was known at an earlier clinical time.

## Proposed acceptance cases

| Case | Required outcome |
|---|---|
| Only an unaccepted claim describes an infusion | Claim remains inspectable; accepted occurrence view contains no infusion assertion supported solely by it |
| One source supports a claim and another contradicts it | Both sources retained; resolution follows the declared policy or remains unresolved |
| A selected source claim is withdrawn | Dependent view assertions and answers are recomputed; independent surviving support is preserved |
| A rate-change row begins | No first-ever treatment-initiation assertion without additional evidence and policy |
| A record has a documentation timestamp | No availability-at-source or occurrence-time equivalence is inferred automatically |
| Exact source labels are available | Exact record-coordinate answer may be returned; physical occurrence certainty is not upgraded |
| Export is consumed outside this application | The claim/occurrence distinction and required interpretation remain explicit in the exchange contract |

Before adopting the pattern, check entailments over its full pinned import closure as well as the restricted execution projection. In particular, test whether domain/range axioms, class restrictions or property chains leak an unaccepted description into the accepted occurrence vocabulary. An operation that the available Python/Rust backend cannot certify remains an open gate; do not describe a parser success as a full OWL proof.
