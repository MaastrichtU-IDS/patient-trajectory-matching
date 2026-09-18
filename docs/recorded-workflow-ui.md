# Recorded workflow interface verification

The `/journey` recorded section now follows one completed source evaluation through typed pattern editing, reference comparison, segment evidence, export, and retained history. The authored four-history demonstration remains a separate population.

## Implemented behavior

- The editor keeps measurements as points and treatment as an interval. It permits baseline scalar comparisons and bounded timing windows, start/end follow-up anchors, and an optional within-treatment restriction. Measurement items, units, treatment selection, and admitted source records remain fixed. The server rejects windows outside the reviewed admission envelope.
- A successful pattern creates a completed child evaluation and becomes the active query for references, similarity, segment evidence, and export. The summary renders its actual canonical operator and timing windows. The source-control button explicitly starts a new base evaluation; it does not claim those simpler controls describe the edited pattern.
- Explicit similarity profiles select one to four available pre-index features: latest pressure, pressure change, observation count, and recency. Positive decimal scales and weights are validated before comparison. Coverage, per-feature contributions, missing feature evidence, and source provenance are visible. Follow-up measurements do not enter ranking.
- History restores completed evaluations and requests resumption of interrupted ones. The interface distinguishes process-local retention from configured durable storage. Restoring evidence does not imply a source refresh.
- Inputs have associated labels, buttons and disclosure controls are native keyboard controls, status changes use polite live regions, and visible focus outlines are provided. Completed base evaluations and restored history focus the status region. Feature and pattern edits invalidate stale pending results.

## Automated verification performed

Run from the repository root with the installed semantic dependencies:

```sh
PYTHON=python3.12 node app/test_recorded_journey_ui.cjs
```

The harness executes the actual interface JavaScript with real completed source-service and Rust-reviewed selector responses. It verifies original and edited queries, missing follow-ups, complete-only cohort counts, reference exclusion, weighted feature contributions, invalid weights/scales, feature-aware exports, child-query adoption, history restore/resume, mismatch rejection, stale-response isolation, and escaping of source text. It is a DOM harness, not a graphical browser or screen-reader test.

## Browser rehearsal status

On 2026-09-18 the supported cloud Chrome browser connected successfully. Opening the locally running `/journey` at `http://127.0.0.1:8089/journey` failed with `net::ERR_BLOCKED_BY_CLIENT`. No application page rendered. Visual layout, actual keyboard traversal, browser downloads, and assistive-technology behavior remain unverified. No alternate host, tunnel, or separate browser automation was used to bypass this restriction.

## Manual acceptance checklist

Run the application in an environment whose browser can access its configured origin. Use authored synthetic records for the initial rehearsal.

1. Open `/journey` at desktop width and a narrow mobile width. Confirm labels, controls, tables, and evidence disclosures remain readable without page-wide horizontal scrolling. Check at 200% zoom.
2. Using only Tab, Shift+Tab, arrow keys, Space, and Enter, select the reviewed source profile and run a complete evaluation. Confirm visible focus and a clear completion announcement; no partial cohort totals should appear while running.
3. Select a reference and compare peers. Confirm the reference patient is excluded, unresolved patients remain listed, and highlighting does not filter the complete temporal population. Inspect a segment with missing follow-up and confirm it is explicitly missing.
4. Enable latest pressure and observation count, set positive scales and weights, compare, and inspect both contributions. Enter zero or invalid weights and confirm comparison is disabled with a useful message. Confirm source units and normalized distance are distinguishable.
5. Change the baseline operator to “At most”, choose 64, and evaluate the edited pattern. Confirm the summary reads “≤ 64”, references and comparisons refer to the new evaluation, and segment observations agree with the executed query. Try a follow-up window beyond the admission envelope and confirm rejection retains the previous complete result.
6. Download the complete query and an explicit-feature peer comparison. Replay both with the repository replay command and verify their query, feature definitions, evidence, and source versions.
7. Restore the original and edited evaluations from history. With durable storage configured, restart the server and restore each again. Resume an interrupted evaluation and confirm no incomplete evidence or cohort count is shown.
8. Check the workflow with a screen reader: input names, table headers, disclosure states, result announcements, and focus after completion. Confirm source evidence text is navigable without repeated unexpected focus movement.

Record browser/version, viewport, source mode, authentication mode, observed defects, and pass/fail for every step. These checks are pending; automated success does not mark them as performed.
