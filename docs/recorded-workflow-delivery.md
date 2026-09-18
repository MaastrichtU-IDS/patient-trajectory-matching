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
| Explicit similarity profiles | Pressure-history profiles plus startup-admitted distinct measurement packs; exact units/windows, weighted contributions, source-bound replay and missingness | Clinical approval of variables and weights/scales remains pending |
| Meaningful MIMIC-IV use case | Public-demo source admission and application-level pattern, comparison, retention and replay evaluation; fixed clinical-review packages | Clinical question/mapping approval and authorized full-source validation |
| Usefulness and performance | Public-demo workflow evaluation, authored scaling through 5,000 patients, and held-out metric tooling with explicit missing judgments | Independent relevant-peer labels, full-source retrieval quality and representative throughput |
| Durable operation and access | Private SQLite, owner access, resume, backup/restore utility, private Compose/Helm configurations and container plus disposable-Kubernetes lifecycle CI | Successful container/Kubernetes CI, intended-cluster recovery, institutional access policy and multi-user authorization |

The original four-feature profile still describes one pressure stream. The optional
`recorded-clinical-features-1` profile independently admits additional measurement
streams, with authored heart-rate/respiratory-rate examples and a reproducible
pinned public-demo extraction. Both remain descriptive, clinically unvalidated
similarity functions. Source fidelity does not approve the scientific protocol.

The [recorded three-variable evaluation](clinical-feature-source-evaluation.md)
checks all 944 public-demo anchors against the original source rows, then verifies
ranking, durable restart and source-backed replay. Its ART pressure stream yields
32 complete three-variable anchors, two ranked peers and 97 unresolved patients
for the selected reference. This technical result does not establish retrieval
usefulness; explicit coverage is part of the result.

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


## Distinct variables and public sources

Enable the authored distinct-variable example with a fresh private state directory:

```sh
python -m app.server --host 127.0.0.1 --port 8080 \
  --state-dir "$HOME/.ptm-clinical-example" \
  --clinical-features examples/clinical-features/authored-pack.json
```

The interface offers a distinct-variable profile after the recorded query completes.
Feature-aware reference selection shows which segments have every requested value.
Completed retained evidence remains readable after the supplemental CSV changes;
new execution and fresh replay require the exact admitted source again.

For pinned public-demo pressure strata use `--public-demo-dir /path/to/demo/icu`.
This source mode is explicit, literal-only and bound to the committed public source
pin. It does not enable unreviewed ontology mappings or select filesystem paths
from a downloaded report. Replay uses the same explicit option, and adds
`--clinical-features /path/to/pack.json` for exports with supplemental variables.
The [clinical-variable pack guide](../examples/clinical-features/README.md) documents
source extraction and the reviewed input contract. Keep generated source rows and
packs outside the repository.

The [research release guide](research-release.md) provides optional private Compose
and Helm modes, owner credentials, consistent backups and restoration checks.
A release tag remains conditional on the stated environment acceptance gates.
