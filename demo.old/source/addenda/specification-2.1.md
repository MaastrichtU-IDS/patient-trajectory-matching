# Specification addendum version 2.1

The v2.0 cohort schemas, OpenAPI and 16-case oracle retain their original scope. This addendum and the new JSON examples specify extensions; they are not implemented API operations or a tested temporal normalizer. The synthetic QBE profile is a concrete demonstration default awaiting domain review before clinical use.

## 2 Driving use cases and clinical workflows

The product is organized around a researcher’s clinical question, followed by progressively more explicit definitions of similarity. UC01 retrieves people like an index patient and describes what happened next. UC02 converts those similarities into a reproducible trajectory cohort. UC03 harmonizes the source evidence so that both questions have consistent, inspectable meaning across data sources.

| Use case | User and input | Required output and success |
|---|---|---|
| UC01 People like this patient | Researcher selects a patient, index rule, history window and similarity profile | Ranked histories with component explanations; follow-up summaries use separate outcomes and denominators |
| UC02 Patients following this trajectory | Researcher promotes selected similarities into hard criteria, event relations and allowed relaxations | Reproducible cohort, per-patient alignment and membership changes explained at every revision |
| UC03 Comparable evidence across sources | Data and ontology engineers supply typed mappings, clocks, units and provenance | Same supported clinical predicate has the declared source meaning; unknowns and incompatible records remain visible |

### D01 Kidney function during antibiotic treatment

The lead question is “Among patients with similar renal function, comorbidities and antibiotic exposure, what treatment changes and renal outcomes were subsequently observed?” A researcher opens an adult inpatient record at a specified point in an antibiotic course. The first view shows comparable pre-index histories and explains which renal measurements, medication classes and recorded conditions contributed to similarity. Later panels display observed treatment changes, subsequent creatinine values and discharge outcomes.

The same domain supports a more precise cohort: an eligible antibiotic administration precedes a qualifying creatinine rise, supported by an earlier baseline measurement. Exposure need not follow the baseline. Section 15 supplies the bounded numerical exemplar; the clinical study replaces toy medication concepts only through reviewed mappings and a named policy. This is an exposure-associated trajectory, without an automatic claim that the medication caused injury.

The source plan uses MIMIC-IV demo for integration, an available full MIMIC-IV subset for clinical questions and constructed examples for exact expected answers. Success requires a completed patient-to-pattern journey, explainable semantic and temporal near matches, and a report of clinical cohort yield, missingness and reviewer judgments. Success does not require an attractive treatment effect or a non-empty result for every query.


### Further clinical examples

### D02 Heart failure with impaired kidney function

The question is “Among patients with similar heart failure histories and early hospital trajectories, which treatment sequences were observed, and how did renal function and discharge outcomes evolve?” The index is a declared point such as 24 hours after admission. Matching uses only eligible earlier history: recorded conditions, age band, renal measurements and treatment events. Discharge diagnosis codes without historical availability cannot silently become features known at admission.

Refinements may require repeated diuretic administrations, renal measurements within named windows and selected coexisting conditions. Exact matching preserves the distinction between an order and an administration. A permitted medication alternative or wider interval is visible as a separate relaxation. Outcomes include observed renal change and discharge status; fluid balance or dialysis is added only after the corresponding source mapping and ascertainment rules are verified.

Use the full MIMIC-IV subset after the kidney example’s extraction and normalization are stable. The demo supplies examples only where eligible records actually exist. This scenario tests repeated treatment, overlapping processes and differences between similarly diagnosed patients. It is the next clinical demonstration rather than a second mandatory hackathon implementation.

### D03 Type 2 diabetes and progressive kidney dysfunction

The question is “Among patients with similar glycaemic and renal histories, which medication changes preceded improvement, stability or further deterioration?” The index is a defined outpatient review. The history spans months or years and can include HbA1c, renal measurements, recorded conditions and prescriptions. The refinement may require persistent change across repeated observations rather than a single abnormal value.

Use pinned Synthea modules or explicitly labelled constructed overlays to exercise this longitudinal workflow. Record generator assumptions and event provenance. The presence of a diabetes module does not guarantee every required renal measurement or treatment sequence. Synthetic outcomes test retrieval behaviour and cannot establish clinical treatment effectiveness. Full MIMIC-IV contributes only suitably observed hospital trajectories; it does not establish a complete community care history.

This example tests irregular sampling, calendar-based age and follow-up, missing observations and prescription-based patterns. Its medication predicates remain distinct from the administration predicates in D01. Success is preservation of the declared query meaning across time scales, with missing follow-up and uncertain timing surfaced rather than filled with invented events.


### Progressive refinement from a patient to a cohort

User complexity and implementation complexity are separate. A simple initial interaction may use substantial retrieval machinery. The initial product therefore offers a bounded, transparent patient search without waiting for the R2 all-pairs graph. The researcher can move forward, undo a refinement or branch an analysis at any stage.

| Stage | Researcher action | System behaviour |
|---|---|---|
| L0 Find similar patients | Select an index patient and accept or edit visible defaults | Rank eligible pre-index histories; disclose profile, feature coverage, limits and search scope |
| L1 Focus the comparison | Choose important conditions, measurements or treatments | Update component weights or hard filters; show which action changes ranking versus eligibility |
| L2 Define a trajectory | Promote selected events into slots and add order, gaps, values or age criteria | Generate a typed pattern and run it across the declared eligible population |
| L3 Inspect near matches | Enable named semantic alternatives or bounded time extensions | Show exact membership and separately explained additions for each relaxation |
| L4 Freeze the cohort | Name the question and save the selected revision | Preserve population, index rules, query, policies, results and replay manifest |

A refinement revision stores its parent revision, patient and episode references, each patient’s index rule, history and outcome windows, base population, retrieval profile, query AST reference, edit operation and result reference. A patient reference is protected by the same access rules as the source record. Explicit defaults are versioned; “like this one” never means an undisclosed universal similarity function.

The H0 start uses a fixed, reviewed small feature profile and deterministic exhaustive ranking on a bounded eligible pool. Each component specifies its distance, scale, weight, missing-value rule and evidence. Candidate and reference histories use the same index definition. A fixed profile may combine age band, pre-index creatinine and selected clinical concept sets; weights are demonstration parameters until clinically reviewed. Missing features reduce reported coverage and cannot silently improve similarity through imputation.

Promoting a preference to a hard criterion changes eligibility and is shown as such. L2 executes against the base eligible population by default, not just the first displayed top-k neighbours. Restriction to a saved result set is an explicit alternative with that scope recorded. An added hard conjunction yields a subset only under unchanged population, semantics and complete search; reranking a top-k list does not have that guarantee. Each revision reports added, removed and unresolved patients with reasons. Outcome-informed refinements are labelled exploratory and evaluated on a separate held-out population before any validation claim.


### Users and end to end workflows

| User | Task | Evidence of success |
|---|---|---|
| Clinical researcher | Define a trajectory and review its exact cohort and near misses | Can explain an inclusion using source events and constraints |
| Clinical informatician | Approve terminology mappings and relaxation policies | Can distinguish entailment from an allowed clinical substitution |
| Data engineer | Convert source tables into a temporal graph | Can trace an indexed event back to its source row and mapping rule |
| Ontology engineer | Maintain SULO extension and semantic bundles | Can identify unsupported constructs and inspect inference evidence |
| Data scientist | Compare histories and measure retrieval quality | Can reproduce a ranking and quantify candidate loss |
| Operator or steward | Publish snapshots and control access | Can recover jobs and audit authorized reads and exports |

**Cohort workflow.** Select a project, purpose, dataset snapshot and semantic bundle. Start from a patient or load a saved pattern. Promote chosen similarities into explicit criteria and review the resulting pattern. Validate its types, units, time relations and relaxation policy. Review the estimated work and any incomplete semantics. Run exact matching, then optionally compare the additional patients admitted by each relaxation. Open an alignment and its source evidence; save the pattern, result and manifest.

**Data workflow.** Inventory source files and their meaning. Run a pinned adapter into staging. Inspect rejected rows, mapping coverage and timestamp quality. Validate the graph and its index projection. Publish the snapshot atomically only after the required checks pass. A corrected input produces a successor snapshot and a difference report.

**Patient similarity workflow.** Define an index rule, a history window and an eligible population. Generate neighbours from histories ending at the index cutoff. Open a pairwise alignment. Inspect subsequent treatments and outcomes in a separate follow-up panel that states ascertainment and censoring rules. Displayed associations carry no causal interpretation.

The first demo MUST complete a bounded query-by-example search, two successive refinements, an exact versus relaxed cohort comparison and a source-evidence inspection. A notebook is sufficient for H0. The complete analyst workspace and population-wide neighbour graph follow in R1 and R2.


## 6 Time and constraint semantics

Time is represented using an explicit clock, origin, unit and precision. For supported numeric instants and elapsed durations, the canonical execution unit is integer microseconds relative to that clock’s origin. Calendar values, ages and unresolved relative expressions retain their own typed representation until a valid conversion is available. Source timestamps and offsets remain in provenance. A known UTC timestamp can be converted to UTC; a shifted, timezone-unspecified MIMIC timestamp retains a dataset-local clock. The product never adds a fictitious Z suffix to an unknown timezone.

For a point, one time variable has lower and upper bounds. Its start and end are that same variable, not independent uncertain endpoints. For a proper interval, start and end have bounds plus the invariant start < end. The feasible endpoint set must be non-empty. Missing endpoints are null, with a reason, rather than zero, infinity encoded as a number or an imputed admission boundary.

Metric gaps specify their endpoints explicitly. The default gap from interval A to B is start(B) minus end(A). A day means 24 elapsed hours, not a calendar date transition. Equality at an inclusive maximum passes. Before is strict; meets means equality. Record episodes use half-open membership windows [start, end); proper-interval Allen relations use endpoint comparisons as below. Point predicates are separately typed.

| Relation | Definition for proper intervals A and B |
|---|---|
| before | end(A) < start(B) |
| meets | end(A) = start(B) |
| overlaps | start(A) < start(B) < end(A) < end(B) |
| during | start(B) < start(A) and end(A) < end(B) |
| starts | start(A) = start(B) and end(A) < end(B) |
| finishes | start(B) < start(A) and end(A) = end(B) |
| equals | Both starts and both ends are equal |

The other six Allen relations are inverses. Validation rejects applying a proper-interval relation to an uncertain point by silently inflating it into an interval. H0 supports before and endpoint gaps; the complete interval catalogue belongs to R1. OWL-Time is an interoperability reference for temporal vocabulary, not the execution engine. [S02]

Default definite acceptance requires one fixed event binding whose hard constraints hold for every jointly feasible assignment of its uncertain times. Possible-only matches are shown separately. All constraints must refer to a common feasible assignment; atomwise possibility under incompatible assignments is insufficient. For relaxed acceptance, use the maximum cost over feasible assignments and minimize that cost over event bindings. General numeric uncertainty follows the same conservative rule.



### Temporal input classification and normalization

The ETL pipeline MUST classify the meaning of a time-related field before converting its value. A date, a time of day, an instant, a duration, an age and an ordinal day label are distinct types. A database column called timestamp does not establish its timezone, precision, epoch or clinical role. FHIR provides one source typing model; each adapter still declares the actual source profile and interpretation. [S20]

| Source form | Required interpretation | Canonical treatment |
|---|---|---|
| Date or partial date | Calendar, granularity and event role | Retain the date domain; when used as uncertain occurrence time, derive bounded support in a declared clock |
| Time of day | Date anchor and timezone context if present | Keep local time until a compatible date and clock resolve an instant |
| Datetime or timestamp | Offset or zone, precision, time scale and role | Normalize to an instant only when justified; preserve original representation |
| Numeric epoch value | Epoch, unit and time scale from source metadata | Convert with a named rule; never infer milliseconds versus seconds from digit count alone |
| Elapsed duration | Value, unit and elapsed semantics | Exact decimal conversion to integer microseconds when representable |
| Relative time | Anchor identity, scope, unit, direction and counting convention | Preserve the relation and, if resolvable, derive time bounds with anchor provenance |
| Age or calendar period | Reference date, calendar, precision and age convention | Calendar-aware comparison; do not treat age in years as a fixed elapsed duration |

The normalized envelope retains raw_value, source_datatype, semantic_kind, source_unit, calendar, timezone or offset, epoch or anchor reference, source precision, lower and upper bounds with inclusivity, normalization status, policy version and provenance. Time origin and clock identifiers are mandatory for numeric execution coordinates. The compact event projection points to this envelope; it is not the sole representation of source temporal meaning.

Fixed elapsed conversion uses ms = 1,000 microseconds, s = 1,000,000, min = 60 s, h = 3,600 s, d = 86,400 s and wk = 604,800 s. Source labels such as hours are mapped through a versioned unit dictionary. UCUM is the unit reference, including its explicitly defined year variants; a UCUM year duration does not automatically mean a calendar birthday or anniversary. Months and years require either a named fixed-duration convention or calendar arithmetic anchored to a date. [S17]

Calendar addition names its calendar and end-of-month policy. The default clamps an invalid target day to the last day of the target month and records that operation; clinical protocols may require another rule. Leap-day birthdays require a declared age policy. Elapsed time and calendar date arithmetic use distinct operators in the AST. Unit normalization is mandatory before matching and never consumes a fuzzy-match budget.


### Ages relative anchors and clock comparability

Age is an observation or derived quantity at a specified reference time. An age of 65 in completed calendar years means an age range under that convention, not exactly 65 multiplied by 365 days. Preserve whether age is exact, rounded, estimated, binned or top-coded. Derive age from birth and reference dates only when their precision and privacy treatment support it; never reconstruct an exact birth date from a coarse age field.

MIMIC-IV anchor_age is tied to anchor_year and groups people older than 89 under the value 91. Treat this as a de-identification category, not exact age 91. The adapter uses a versioned anchor-age derivation, reports its precision and retains the top-coded status when calculating age at another admission. It never creates an exact birthday. [S16]

“Days since first admission” requires a patient-scoped anchor rule: first recorded admission in the selected dataset and observation window, or an independently documented first-ever admission. These are not interchangeable. Select the anchor from the full permitted history before applying a downstream cohort subset; record its ID and coverage. If the extract starts late, the field cannot silently be labelled days since first-ever admission.

“Hospital day 3” under a one-based calendar convention is the third local date, whereas an elapsed offset of 3 d is exactly 72 hours after its anchor. A source integer may also be rounded or binned. The rule must state counting origin, rounding and inclusivity; an unspecified convention remains unresolved. Different candidate patients may be aligned at their own index time for relative comparison, without claiming their absolute dates are synchronized.

For a known offset, preserve the original value and compute UTC. For an IANA zone, pin the time-zone database version and resolve the offset for the stated historical date. Repeated local times during a daylight-saving transition require the recorded offset, fold or an explicit unresolved set; nonexistent local times are flagged rather than moved forward silently. If both a zone and offset are present they must agree, unless a source policy explicitly resolves the discrepancy. A local calendar day can span 23 or 25 elapsed hours. [S18, S19]

Unknown zones remain unknown. MIMIC’s shifted timestamps use a dataset-and-patient-scoped clock; the same apparent year in two patients is not a common real calendar period. Do not apply historical America/New_York daylight-saving rules to shifted years or infer an offset from hospital location. Use documented within-patient differences and align patients through their own clinical anchors. [S16]


### Temporal normalization validation and failure behaviour

Normalization follows parse, classify, resolve units and clocks, resolve anchors, derive bounds, validate and publish. Anchor dependencies form an acyclic graph; missing, cyclic or contradictory anchors produce a diagnostic. Repeated expressions using the same uncertain anchor retain their shared dependency, so subtracting two offsets from that anchor does not create artificial independent uncertainty.

Normalization statuses are EXACT, BOUNDED, UNRESOLVED, INVALID or UNSUPPORTED. These describe conversion, separately from clinical truth and query computation. An exact conversion does not make a date-only observation an exact instant. Each failure has a field path, source reference and reason such as UNKNOWN_UNIT, UNKNOWN_EPOCH, MISSING_ANCHOR, AMBIGUOUS_LOCAL_TIME, NONEXISTENT_LOCAL_TIME, INCONSISTENT_ZONE_OFFSET or UNSUPPORTED_PRECISION.

| Case | Required result |
|---|---|
| 90 min and 5,400,000 ms | Same elapsed duration of 5,400,000,000 microseconds |
| 1 wk and 168 h | Same elapsed duration; neither is an age or recurring calendar schedule |
| 2026-09-14T10:00+02:00 and 08:00Z that day | Same instant with distinct retained source strings |
| Amsterdam midnight to next midnight on 29 March 2026 | One calendar day and 23 elapsed hours |
| Amsterdam 02:30 on 25 October 2026 | Two possible instants unless offset or fold resolves the repeated time |
| Amsterdam 02:30 on 29 March 2026 | Invalid local time; no silent forward shift |
| Age 65 in completed years and age 780 months | Not automatically equivalent; preserve each measurement convention and precision |
| Hospital day 3 and elapsed 72 h | Require distinct ordinal-calendar and elapsed interpretations |
| Source date 2026-09-14 | No invented measurement at midnight; occurrence support follows its declared date and clock semantics |
| Two shifted MIMIC clocks or an unspecified numeric epoch | No invented absolute alignment or guessed epoch unit |

Use exact arithmetic and checked ranges. If a source precision is finer than one microsecond, preserve the raw value and conservatively bound it or reject the unsupported exact operation; never truncate silently. The execution profile uses a declared POSIX-like scale without leap-second instants. An explicit leap-second value is unsupported until a named conversion with a pinned leap-second table is implemented; no silent collapse to an adjacent second is allowed.

Publication checks compare equivalent representations, interval boundaries and normalization idempotence. Rewriting units or display timezone must preserve query membership and relaxation costs. Source-local and UTC display changes cannot alter age dates, relative anchors or source meaning. Changing a normalization policy creates a new derived snapshot and invalidates affected caches; prior results remain replayable with their original policy.


## 12 MIMIC IV source and clinical study contracts

The initial adapter pins the demo 2.2 schema. It uses hosp.patients, admissions, labevents, d_labitems, emar and emar_detail. Diagnosis and prescription tables are contextual extensions. ICU inputevents is an R1 extension for interval exposure, with a separate mapping and reconciliation policy.

| Source fields | Target or use | Required interpretation |
|---|---|---|
| patients.subject_id | Patient | Dataset-scoped identifier |
| admissions.hadm_id, admittime, dischtime | Admission episode | Preserve admission boundaries and missing values |
| labevents.labevent_id, itemid | Record and local assay concept | Resolve itemid through d_labitems; review analyte, specimen and unit |
| labevents.charttime, storetime | Measurement-associated time and storage time | Retain distinct roles; charttime is not automatically specimen collection time |
| labevents.value, valuenum, valueuom | Original and normalized result | Preserve comparators and text; no silent numeric coercion |
| emar.emar_id, charttime, event_txt | Medication record | Map status through an explicit allow-list |
| emar_detail.emar_id and detail fields | Administration evidence | Aggregate detail without multiplying the parent administration |
| diagnoses_icd.icd_code and seq_num | Admission-linked diagnosis record | Sequence number is not clinical onset order |
| prescriptions.starttime and stoptime | Order or prescription interval | An order is not an administration |

The minimum medication point-event profile accepts a documented Administered status with compatible detail and a reviewed medication mapping. Additional statuses such as delayed or partial administration require their own rules; not-given and held doses do not qualify as administrations. Started, Stopped and Rate Change are preserved as distinct records until an interval-construction policy is implemented. Do not merge EMAR and ICU inputevents into a single exposure without a validated reconciliation rule.

Admission-linked exemplar queries require explicit hadm_id membership. A lab without hadm_id is not assigned to an admission merely because its timestamp is nearby. A later heuristic linker may produce an uncertain association with provenance; it cannot create exact membership without an approved rule.

The creatinine catalogue selects the intended serum or plasma assay and accepted units. A local item identifier remains a local concept until the mapping is reviewed. Medication names and product descriptions are lookup inputs, not ontology entailments. No LLM-generated terminology mapping becomes active without review.

Use relative within-patient timing for histories. Derive age bands through a pinned MIMIC policy using anchor metadata; preserve uncertainty and de-identification effects. Do not derive precise birth dates or compare shifted timestamps across patients as real calendar dates.



### Full MIMIC IV subset and clinical exploration

Full MIMIC-IV is an available clinical research source for this project. Begin with a selected extract in the team’s authorized environment, while retaining the public demo and synthetic fixtures for portable development. Pin the actual dataset release; version 3.1 is the default reference. The extraction and analysis protocol is a deliverable, not a claim that clinical data have already been analyzed. [S16]

The first study asks whether progressively specified pre-index histories yield clinically coherent comparison cohorts for adults receiving antibiotics, and how observed subsequent treatment changes and renal trajectories differ across those cohorts. Its primary retrieval endpoints are reviewer-rated relevance, cohort yield, missingness and membership stability under refinement. Treatment and outcome summaries are descriptive exploratory endpoints.

**Population and index.** Start with adult admission episodes with a mapped, documented antibiotic administration and at least one eligible serum or plasma creatinine observation within the 48 hours preceding the index. The initial index is 24 hours after the first observed qualifying antibiotic administration in that admission. State first observed in the available record, not first-ever antibiotic exposure. The patient must still be admitted at index. These choices define a 24-hour landmark population and exclude earlier discharge or death; report those exclusions and the resulting scope.

Use a seven-day pre-index look-back limited to recorded history, with a separately declared context window for conditions. Never require a subsequent creatinine measurement, renal deterioration or survival through follow-up to enter the base population. Patient matching uses only pre-index observations and appropriately available condition evidence. Exclude the index patient’s other episodes from their candidate pool by default.

**Extraction size.** Inventory eligible patients before choosing a fixed sample size. Use a deterministic patient-level hash sample of up to 500 patients for source QA, then up to 10,000 eligible patients for the first clinical exploration, or all eligible patients if fewer. Use the first eligible admission per patient for this initial study; a sensitivity analysis may include repeated admissions with patient-level dependence accounted for. Retain reference rows and the history required to resolve anchors even when they lie outside the displayed episode.

Record source version, extraction code, selection seed or hash rule, patient and admission counts, time coverage, missingness, concept mappings, units and exclusions. Keep the QA sample in the development partition. Split the remaining patients into development and held-out evaluation partitions before tuning similarities or relaxation policies; all episodes of a patient stay together. The full-study sample is distinct from the constructed benchmark and cannot inherit its ground-truth labels.


### Clinical outcomes review and study outputs

**Measured follow-up.** Follow patients from the index until the earliest of seven elapsed days, discharge, recorded death or the applicable data boundary. Report the number at risk, number observed and observation duration for every endpoint. Discharge and death are explicit outcomes or competing events; an absent subsequent laboratory value is not renal recovery.

For the initial renal description, use the minimum eligible creatinine in the 48-hour pre-index baseline window. Report the maximum observed post-index creatinine within 48 hours, its change from that baseline, and the count of available measurements. A patient without a post-index measurement remains in the cohort with the endpoint missing. This observed change is not a complete AKI phenotype or a treatment-effect estimate. A separately reviewed phenotype protocol may add staged injury, recovery or dialysis endpoints.

Treatment summaries show subsequent documented antibiotic administrations, starts or changes supported by the available record. A gap in administrations alone cannot establish a deliberate discontinuation. Discharge status and time from index to discharge use admission records. Add renal replacement therapy only after a procedure or ICU-event mapping is validated; do not infer it from creatinine values.

**Comparison design.** For a fixed set of held-out index patients, compare the broad similarity profile, successive hard refinements and exact versus semantic-only, time-only and combined relaxation. Report eligible population size, cohort sizes, added and removed patients, feature balance, missingness, review-rated relevance and computation time. Use two domain reviewers for a prespecified feasible sample, record agreement and adjudicate differences. Review the source evidence as well as the alignment.

Inspect outcomes only after freezing the retrieval definition for the evaluation partition. Outcome-guided exploratory revisions are allowed, recorded as such, and require a fresh validation plan. Any later comparative effectiveness analysis must separately address treatment assignment, confounding, time-dependent exposure and censoring. This retrieval study does not claim that similar patients establish the best treatment.

**Deliverables and boundaries.** Produce a versioned extraction script, data dictionary, eligibility flow counts, temporal quality report, clinical mapping catalogue, saved refinement journeys and an aggregate clinical exploration report. Release source code and synthetic examples; keep restricted records, patient alignments and derived patient-level graphs under the source access conditions. Full-data access already available to the team does not make patient data eligible for public notebooks or external AI services. [S16]

H0 delivers the extraction protocol and a bounded authorized source QA run when an extraction owner can prepare it before the event. The clinical exploration is an explicit follow-on work package using the available full data, independent of the R3 multi-user deployment milestone. No clinical finding is asserted until the extract, mappings and analysis have actually been run and reviewed.


