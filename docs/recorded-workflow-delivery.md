# Recorded workflow delivery and acceptance

The recorded workflow now connects one admitted source population through typed
pattern revisions, explicit pre-index comparisons, source evidence, exact replay,
and optional durable history. The six requested work areas have different evidence
requirements; the status below distinguishes implemented behavior from acceptance
that needs a browser, clinical reviewer, or representative deployment environment.

| Work area | Delivered | Acceptance still outstanding |
|---|---|---|
| Usability and accessibility | Connected interface, labels, focus styles, live statuses, stale-result isolation, actual-engine DOM tests | Rendered visual, keyboard and screen-reader rehearsal; supported browser cannot reach the local origin |
| Patterns over recorded sources | Typed point/interval compiler; exact bounds and scalar predicates; reviewed source envelope; immutable revisions feeding comparison/evidence/export | Additional clinical event types require their own admitted data and reviewed selectors |
| Explicit similarity profiles | Four pressure-history features, positive scales/weights, exact rational ranking, missing-feature coverage, contributions, bound profile hash | Independent clinical variables and clinically chosen weights/scales require review and data |
| Meaningful MIMIC-IV use case | Reproducible public-demo norepinephrine/pressure query, separate source strata, complete anchor accounting, independent SQL and enumeration | Clinical question/mapping approval and authorized full-source validation |
| Usefulness and performance | Fresh public-demo cold/warm/changed/cache evaluation, complete-result equality, sampled process-tree RSS, clinical evaluation protocol | Adjudicated relevant-peer labels, held-out retrieval quality and representative scale/concurrency measurements |
| Durable operation and access | Private SQLite state, automatic completion persistence, restart restoration, explicit resume, bounded retention, owner authentication and audit metadata | Institutional access policy, multi-user authorization, shared production deployment and operational acceptance |

The four similarity features derive from one reviewed pressure item and unit:
latest value, value change, measurement count and recency. They are an explicit
extension of the original measurement feature, not a claim that multiple clinical
variables or a clinically validated similarity function have been implemented.

## Run and verify

After the dependency setup in the README, start an authored local instance:

```sh
python -m app.server --host 127.0.0.1 --port 8080 --state-dir "$HOME/.ptm-state"
```

Open `http://127.0.0.1:8080/journey`. Run a recorded evaluation, edit its recorded
pattern, select a reference and explicit feature profile, inspect contributions,
and download the completed analysis. Restart with the same state directory to
restore history. The [operations guide](durable-recorded-workspace.md) adds the
private owner credential and explains retention, backup and resume semantics.

```sh
python -m unittest discover -s app -p 'test_*.py'
PYTHON=python3.12 node app/test_recorded_journey_ui.cjs
PYTHON=python3.12 node app/test_journey_ui.cjs
python -m unittest discover -s tools -p 'test_*.py'
python tools/check_completion.py
```

The [independent review](completion-review.md) covers the actual HTTP lifecycle,
including two successive pattern revisions and exact replay after restart.
The [interface checklist](recorded-workflow-ui.md) records automated coverage and
the precise browser restriction. The [evaluation guide](recorded-workflow-evaluation.md)
documents the public-demo protocol, report interpretation, clinical review
worksheet and patient-disjoint full-source study requirements.

These additions do not change the original 104-requirement completion register
into a finished-product claim. They advance the bounded research implementation;
the remaining clinical and operational acceptance requires the evidence listed
above.
