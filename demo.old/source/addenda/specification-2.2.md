# Version 2.2 UI design addendum

Status: specified, not implemented. The initial v2.0 schemas and executable oracle are unchanged. Figure tokens resolve from this pack root. The normative product document embeds the images with captions and text alternatives.

## 20 Patient centred workspace and interaction design

The interface begins with a patient history and lets the researcher turn a comparison into an explicit, reproducible cohort definition. Its proposed distinguishing feature is a continuous connection between the events selected, the criteria being edited, the patients entering or leaving the result, and the evidence explaining each decision. A design is successful when users can predict and explain those changes. Visual novelty and clinical usefulness remain hypotheses to test.

The workspace offers three entry points: find patients like a selected patient, open a saved trajectory query, or load a supplied exemplar. Project, dataset snapshot, semantic bundle, index rule, revision and execution scope remain visible. A data-quality summary identifies missing mappings, uncertain clocks and incomplete observations before execution. The default D01 journey uses pre-index similarity and a separate follow-up view; the section 15 exemplar is explicitly a retrospective trajectory query.

### Progressive disclosure

| Stage | Primary interaction | Information exposed next |
|---|---|---|
| Start | Select a patient and index rule; find similar patients | Feature contributions, coverage and ranked candidates |
| Focus | Mark a feature Must match, Prefer similar or Ignore | Eligibility changes versus ranking changes |
| Define | Promote selected events into trajectory slots | Order, gaps, values, context and observation scope |
| Relax | Enable reviewed concept alternatives or bounded time extensions | Exact cohort, additions and per-constraint costs |
| Explain and save | Inspect membership changes and freeze a revision | Alignment, source evidence and replay manifest |

The initial screen shows clinically meaningful controls, not ontology syntax or storage details. Expression Studio provides synchronized guided controls, a pattern graph and YAML or JSON for advanced users. All views project the same canonical AST. Opening a simpler view MUST preserve unsupported constructs and explain which ones require the advanced editor; it cannot silently remove them.

### Delivery boundary

H0 implements the journey with a notebook, a fixed similarity profile and the bounded matcher. A fifth team member may deliver the annotated screen prototype after core integration passes. R1 implements the coordinated web workspace, direct manipulation and full accessibility checks. R2 adds neighbour-graph exploration. The following drawings are design specifications with constructed records and illustrative counts; they are not screenshots of a running product.

[[PAGEBREAK]]
### Workspace layout and initial patient comparison

[[FIGURE:ui/wireframe-overview.png|Figure 1 Proposed patient comparison workspace using constructed data and illustrative rankings|Three coordinated regions show the reference patient history, explicit comparison criteria and twenty displayed ranked candidates from an eligible population of one hundred twenty. The index and pre-index feature boundary are visible.]]

The history region provides the reference context. The criteria region explains what the system will compare. The results region answers who was retrieved and how much relevant information is available. Selecting a result opens pairwise evidence without replacing the reference patient. Persistent context prevents a researcher from mistaking the selected candidate for a new query source.

The display names the result type as ranked candidates. Twenty displayed neighbours are not the population eligible for a later cohort query. Coverage appears beside ranking so a sparsely observed patient cannot look persuasive through a score alone. An evidence drawer opens on demand; important controls and the active patient remain visible.

At wide desktop widths the regions use approximately 44%, 26% and 30% of available space after gutters. Users can resize panels within readable minimum widths. At intermediate widths, criteria and results share a region; narrow screens use labelled tabs and an equivalent event list. Layout preferences are saved separately from query meaning.

[[PAGEBREAK]]
### From selected events to explicit criteria

Clicking an event selects it and highlights its source, clinical context and precision. A keyboard-accessible action menu offers Add to comparison, Create criterion and Inspect evidence. Selecting two events offers a relation editor. Dragging can suggest order or a gap, but a labelled, typed control must display the proposed meaning before it is applied. The text alternative supports every operation without dragging.

| Control | Meaning | Required feedback |
|---|---|---|
| Must match | Adds or changes an eligibility predicate | Draft criterion, scope and estimated or computed membership change |
| Prefer similar | Changes a named ranking component | Component weight, distance rule, coverage and ranking change |
| Ignore | Removes that feature from comparison | Explicit removal revision if it was a hard criterion |
| Hide from view | Changes only the display | Hidden-event indicator; unchanged query and results |
| Use as reference patient | Starts a branch using another patient | New reference, index rule and affected criteria preview |

The patient timeline groups medications, measurements, diagnoses and procedures into labelled lanes. Orders, administrations and inferred exposures use distinct text and shapes. A density control aggregates crowded events, displaying the number represented; expanding the group reveals individual records. A small overview locates the current history window, while an event list supplies precise values and timestamps. A visually shortened gap is explicitly marked and cannot be interpreted as a proportional duration.

The index appears as a labelled boundary with history on one side and follow-up on the other. Changing it opens a preview of the new rule, affected feature windows and unavailable information. Applying the change recomputes each candidate patient's corresponding index, not just the reference patient's date. All similarity features must remain available by that patient's index under the source policy. Follow-up events cannot become matching features through a drag operation.

Converting selected history into a trajectory creates a draft with visible defaults for event context, distinctness, order, gaps and value predicates. The user can review its plain-language interpretation before running it. Measurements show their selector, such as the minimum eligible baseline, so a visually convenient observation is not substituted for the declared rule. Required events cannot be deleted through relaxation in H0.

Validation appears at the affected control and in a linked summary. Unsupported features remain visible with the release capability that is missing. An invalid draft does not overwrite the last valid query. Undo restores the prior semantic revision; selection, zoom and panel resizing use independent view history.

[[PAGEBREAK]]
### Semantic and temporal editing

Concept search shows a clinical label, identifier, terminology version, event context and source coverage. An exact descendant is labelled Included by subclass entailment with zero relaxation cost. A different clinical concept requires an explicit reviewed alternative, including policy, rationale, reviewer status and cost. A path through SULO's upper classes provides structural context and does not authorize a clinical substitution. Missing mappings are visible in the data-quality summary.

The semantic editor offers Exact concept and entailed subclasses, then a separately enabled list of Allowed alternatives. Each alternative is a named choice. The user sees the affected slot and the cost before applying it. There is no undifferentiated clinical similarity slider. A medication order cannot become an administration by selecting a related concept.

| Time input | Visible meaning | Editor behaviour |
|---|---|---|
| 48 hours | Elapsed duration | Convert supported fixed units without changing meaning or adding cost |
| 2 calendar days | Calendar arithmetic | Show calendar, zone policy and boundary rule |
| Day 2 of admission | Ordinal relative to an anchor | Show day numbering and the admission that supplies the anchor |
| Age 65 to 74 years | Age predicate at a named reference | Retain source precision and any top coding |
| Local date or time | Source clock and precision | Show unknown zone or ambiguity; do not invent UTC |

Every temporal relation names its endpoints, operator, inclusive or strict boundaries, and elapsed or calendar semantics. A value control accepts units such as ms, minutes, hours, days or weeks where compatible. Converting 120 minutes to two elapsed hours is normalization, not relaxation. A calendar year is not silently converted to 365 days. The interface displays the raw value and normalized interpretation together in evidence. [S17–S20]

Imprecise dates appear as uncertainty ranges, with a textual description of earliest and latest feasible time. They do not resemble precise instants. A daylight-saving overlap requests a supported disambiguation policy or remains unresolved. MIMIC patient-shifted clocks are labelled and aligned by each patient's clinical anchor; an absolute calendar overlay cannot imply synchronization across patients.

A permitted extension has separate controls for the original limit, maximum extension, cost function and total budget. Editing a display unit does not rescale the budget. The interface states which constraints remain hard. Changing a normalization policy is a versioned semantic change and triggers revalidation rather than retroactively relabelling existing results.

[[PAGEBREAK]]
### Result previews and asynchronous behaviour

Edits first update a draft. Local validation should respond within 150 ms on the reference client; an optional count preview starts after roughly 400 ms without further edits. These are interaction targets, not measured service commitments. Expensive searches run only through Run or an explicitly enabled preview mode. The last confirmed cohort remains inspectable while the next computation proceeds.

Each request carries session, revision, canonical request hash, dataset snapshot and capability profile. A response also identifies its job and attempt. A late response for an older draft MUST NOT replace current results. Cancellation marks the abandoned attempt; it cannot turn partial verification into a complete cohort. Result refresh preserves the selected patient's identity and keyboard focus, or explains that the patient no longer appears. Rows do not reorder beneath a focused control.

| Visible state | What it means | Permitted presentation |
|---|---|---|
| Draft or invalid | Changes have not produced a valid result | Last confirmed revision labelled out of date |
| Queued or running | Computation is unfinished | Stage and verified progress; no final zero count |
| Partial | Only the stated scope is verified | Observed counts and unprocessed scope, separately labelled |
| Complete with unresolved records | Search finished but some evidence cannot decide eligibility | Accepted, unresolved and excluded totals shown separately |
| Complete with no matches | Declared scope was searched and none accepted | Empty result with constraint diagnostics |
| Failed or cancelled | Attempt did not complete | Error or cancellation reason and last confirmed result |

An estimated count MUST state its estimation method, denominator, scope and timestamp. Sampling or ANN estimates cannot be displayed as exact membership. The run summary keeps knowledge status, computation status, exact status and accepted-as status distinct, with plain-language labels. A zero patient-similarity distance does not prove an exact trajectory match.

Membership comparison uses patient or episode identifiers under the same snapshot and declared population. It reports additions, removals, unchanged and unresolved transitions with reasons. If scope changes, the difference is labelled non-comparable until both revisions are replayed on a shared scope. A cohort query normally searches the base eligible population; restricting it to displayed neighbours requires an explicit scope change.

Saved counts bind to the result manifest. A change in preference ranking can change the displayed top-k list without changing cohort membership. Adding a hard condition permits a subset claim only when population and semantics are unchanged and both searches are complete. Cost and evidence are retrieved lazily with a clear pending state; unavailable evidence cannot be represented as a completed explanation.

[[PAGEBREAK]]
### Cohort changes and near match explanations

[[FIGURE:ui/wireframe-refinement.png|Figure 2 Proposed refinement and evidence view with illustrative totals and the constructed C05 case|The completed illustrative cohort contains fourteen exact and eight relaxed matches from one hundred twenty patients, with three unresolved and ninety-five excluded. C05 uses DrugB and a nine-day exposure gap, costing one for semantic relaxation and one for time relaxation.]]

The illustration partitions 120 patients into 14 exact, eight relaxed, three unresolved and 95 excluded. The eight additions comprise three semantic-only, four time-only and one combined case. Categories follow the deterministic selected minimum-cost alignment; alternative valid alignments remain inspectable. These are design numbers, not measured cohort yields. The C05 alignment is grounded in the supplied toy contract.

Selecting an addition links each pattern slot to its observed event, source row and normalized values. The constraint table states pass, fail or unresolved; cost rows show policy, charge and remaining budget. The concept path and raw time interpretation open beside the alignment. This exposes why the result entered without requiring the user to read the AST.

The near-match explorer proposes changes to permitted query constraints and previews their effect. It never edits the patient record or invents a missing event. A proposed change must be rechecked against all hard constraints and the full target scope. Use Smallest permitted change only when optimality is proved within the stated policy and search space; otherwise label the suggestion Best found, with its search limits. This is query refinement, not a causal recommendation.

[[PAGEBREAK]]
### Follow up comparison and reproducible analysis

The Outcomes tab uses a separately shaded follow-up region and repeats its index, window and censoring rules. It shows observed treatment sequences, measurement distributions and discharge outcomes with group sizes, missingness and denominators. D01 uses the source and analysis protocol in section 12. The comparison is descriptive; the interface cannot award a best-treatment badge or interpret similarity as causal adjustment.

Outcome summaries do not enter pre-index neighbour selection. If a researcher changes eligibility after viewing outcomes, the revision records outcome-informed exploration and shows the applicable evaluation boundary. Aggregate differences must retain their observation and censoring context. A missing follow-up measurement is not displayed as a normal result or treatment success.

### Revision history and collaboration

A revision strip shows the parent, a concise edit description, execution scope and linked result state. Users can compare two revisions, undo to a prior revision, branch a question or freeze a named cohort. Changing a policy, index rule, population or normalization version creates a semantic revision. Panel arrangement, timeline zoom, selected row and density are view preferences. Neither sharing a layout nor adding an annotation changes eligibility.

Freeze requires a named question, canonical query or similarity profile, population and index rules, data and semantic versions, normalization policy, result and replay manifest. The interface distinguishes a saved draft from a frozen complete cohort. An unresolved subset remains stated in the frozen artifact; finality of computation does not establish certainty of every record.

Annotations link to protected evidence and preserve author and revision. Shared links check authorization on access. Export first presents the selected fields, unit of analysis, purpose, suppression policy and versions. Access revocation removes protected content from the active view; an error message must not disclose whether an unauthorized patient exists.

Autosave stores authorized drafts through the workspace API with an expected version. A conflict opens the local and server revisions for comparison rather than silently merging clinical criteria. During a connection loss, an unsent draft remains in memory with an explicit state. Patient data must not enter public analytics, third-party session replay, persistent browser caches or AI prompts through the interface. Restoring a connection repeats authorization before replay. Error reports contain diagnostic IDs and field paths rather than source rows.

The R2 neighbour graph is a secondary exploration view with a tabular equivalent. Default client limits remain 200 nodes and 1,000 edges; expansion is an explicit server request. It preserves directional similarity semantics, feature coverage and search completeness. A graph cluster cannot silently become a cohort definition; saving it records the explicit selection and its scope.

[[PAGEBREAK]]
### Visual system and accessible operation

Use a quiet, high-density research workspace with neutral surfaces, clear lane labels and stable alignment. The proposed tokens use 16 px body text, at least 14 px dense table text, 24 px main headings and a 4, 8, 12, 16 and 24 px spacing scale. Status colours accompany labels and shapes: teal for exact, amber for relaxed, purple for unresolved, red for errors and blue for selection. Colour never carries meaning alone. Contrast must be measured on the implemented combinations.

At 1440 CSS px and above use three coordinated regions; at 1024–1439 use two, and below that use labelled tabs with persistent patient, revision and scope. At 200% zoom and a 320 CSS px viewport, controls reflow and the event list replaces the visual timeline where needed. Data visualizations may retain necessary two-dimensional navigation, with an equivalent usable text view. Cursor pagination provides an accessible alternative to virtualized tables.

Core workflows target WCAG 2.2 AA. Interactive targets meet the applicable 24 by 24 CSS px minimum or spacing exception; primary actions should be larger. Every drag action has buttons or numeric entry. Sliders have labelled bounds, units, current value and keyboard operation; numeric entry remains available. Focus is visible and not obscured, errors link to affected fields, loading messages are announced without moving focus, and reduced-motion preferences suppress animated transitions. Modal dialogs follow focus containment and return rules; a non-modal evidence drawer does not trap focus. [S15, S21, S22]

### Prototype and usability acceptance

The R1 prototype covers eight connected states: select patient, focus similarity, promote events, edit semantics and time, compare membership, explain C05, inspect follow-up, and branch then freeze. It includes empty, invalid, running, partial, unresolved, conflict and access-revoked variants. The companion storyboard specifies these states; it is not an executable prototype.

Conduct a formative study with six to eight target researchers or clinical informaticians and two domain reviewers. Counterbalance equivalent tasks against a conventional form-based query builder. Record unassisted completion, errors, time, explanation accuracy and confidence; report every participant's result and limitations of the small sample. Proposed targets are at least 80% unassisted completion of the core journey and a median of at most five minutes for two specified refinements, after the same brief orientation.

Critical tasks ask users to explain a semantic alternative, distinguish 48 elapsed hours from two calendar days, identify incomplete computation, recognize that top-20 neighbours are not the full cohort, and avoid interpreting outcome associations as causal. All observed critical interpretation defects must be corrected and retested before the R1 usability gate passes. Manual keyboard and screen-reader completion accompanies automated accessibility checks. These are acceptance objectives; an outstanding or innovative user experience is not established by this specification alone.
