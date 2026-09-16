# Patient Trajectory Matching: presentation demo

This directory contains a **guided, live cohort-selection demonstration** backed by the project's unchanged Python reference matcher. Five stops take about 2½ minutes. Ten distinct synthetic candidates are evaluated against a pattern illustrated by an eleventh reference patient, who is excluded from the cohort.

The [timed presenter narrative](NARRATIVE.md) contains the key features, spoken script, audience choice, exact clicks, expected counts, and a two-minute cut.

## Start the guided cohort demo

From the repository root, with Python 3.10 or newer:

```sh
python3 demo/serve.py
```

Open **http://127.0.0.1:8765**. Keep the terminal running. No additional Python packages are needed for the guided journey. Each query runs the Python matcher over committed records projected through actual, validated PRO/SOLID graphs. Graph rebuilding is a separate build-time action.

Use the five numbered stops or the bottom **Next** button. The cost controls and patient rows remain interactive. The audience can choose whether to inspect the concept alternative or the longer time gap. The optional presenter narrative follows the current stop. **Restart** returns to the reference, clears the cohort and resets the timer; it also invalidates any outstanding request.

| Guided stop | Included candidates | What it demonstrates |
|---|---:|---|
| Reference P00 | Not yet searched | Explicit clinical and temporal conditions |
| Exact, budget 0 | 3 | Entailed subtypes qualify at zero cost |
| Budget 1 | 5 | P04's Drug B alternative and P05's extra two days each cost 1 |
| Budget 2 | 6 | P06 needs both changes; hard limits and unresolved evidence remain |
| Evidence and export | 6 | P03's normalization, PRO/SOLID bindings, reproducible result |

P10 remains unresolved under every budget. The count of patients not selected is 6, 4 and 3 respectively. These totals describe constructed candidates, not clinical prevalence. The budget is a cost for query modifications, not a probability.

**Export cohort & query** downloads the active pattern and budget, all candidate outcomes, selected IDs, taxonomy, synthetic source rows, graph bindings, manifests and fingerprints. Reproduce it with:

```sh
python3 demo/cohort.py --verify-export /path/to/trajectory-cohort-budget-2.json
```

The verifier requires the same oracle hash and checks the query, dataset and re-evaluated results. The constructed snapshot has no source-availability cutoff or temporal replay history; the export states that explicitly.

For an offline backup, open **`demo/Guided_Cohort_Demo.html`** directly. It contains the complete guided experience and recorded results for all three budgets. It is labeled **Offline · recorded replay** and does not execute Python in the browser. A failed live request preserves the last displayed query and reports the failure; it does not switch silently to this backup.

## Reviewed pressure queries

The guided page links to a separate [live pressure-query inspector](../docs/live-pressure-inspector.md). It uses the mixed interval/measurement engine, supports narrower windows and a changed pressure threshold, and shows complete patient/stay/segment counts plus source, review and PRO witness evidence. Configure `python demo/serve.py --mimic-dir /path/to/demo/icu` with the pinned dependencies and original public-demo files, or explicitly choose `--pressure-synthetic` for authored examples. Open [the local pressure page](http://127.0.0.1:8765/pressure). Repeating identical controls uses a [bounded result cache](../docs/pressure-query-cache.md) after rechecking the original source and review; the page distinguishes reuse from fresh execution.

Real-source queries run as background jobs and can take several minutes. This page has no offline replay. Failed requests retain the previous completed result, and no partial cohort counts are shown. The short guided journey above remains a separate synthetic demonstration.

## Optional technical examples and full pipeline setup

The original contract-case, graph and Rust views remain at **http://127.0.0.1:8765/lab**, reached through **Technical examples**. They are useful for questions after the guided journey. The 16 contract cases there are independent fixtures, not the ten guided patients.

To install and check the graph and Rust dependencies:

From the repository root, run:

```sh
python3.12 demo/start_demo.py
```

On Windows, use `py -3.12 demo/start_demo.py`. The launcher creates `.venv`, installs the pinned graph and Rust packages, checks both pipelines, and starts the server with that same interpreter. Python 3.12 must already be installed. The first setup needs Internet access. The pipelines then run locally without an AI service, Java or clinical credentials.

Open **http://127.0.0.1:8765**. Keep the terminal open. Stop with Ctrl+C. To choose another port, use `python3.12 demo/start_demo.py --port 8766`. For setup without starting the server, add `--setup-only`.

If installation or a pipeline fails, the launcher stops and prints the actual cause. Do not substitute a different rustDL version: the semantic profile is checked against 0.4.28. Compatible native packages depend on your platform. The replay is available if your platform cannot run the Rust package.

### Fix an existing installation

Stop the old server with Ctrl+C. From the `demo/` folder:

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r ../patterns/requirements-semantic.lock.txt
.venv/bin/python serve.py
```

On Windows, use `py -3.12` for the first command and `.venv\Scripts\python.exe` for the other two. Explicitly naming the environment's Python ensures that installation and execution use the same interpreter. If `.venv` already uses another Python version, create a differently named Python 3.12 environment and use its executable in both commands.

The revised interface shows the actual pipeline error, Python executable, package versions and an installation command under **Setup and diagnostic details**. A Rust gate failure retains its native backend report. The server also exposes these environment details at `/api/environment`. Failed runs never replace the displayed result with a success or silently substitute the replay.

### Core matcher without extra packages

`python3 demo/serve.py` from the repository root still runs the core matcher on Python 3.10 or newer with no extra packages. Graph rebuilding and live Rust reasoning require the full setup above. Those views otherwise display explicitly labeled recorded outputs.

The server binds to your computer's loopback interface and serves only fixed demo endpoints. It does not accept patient uploads or arbitrary queries.

## Technical-view offline backup

Open `demo/Patient_Trajectory_Demo.html` directly in a browser. This is an **interactive replay of 480 recorded Python evaluations**, spanning all available query-control combinations. It does not execute Python or Rust in the browser. Its execution-mode label says so. All values, evidence and timelines are bundled, with no CDN, external font or network dependency.

Budget controls intentionally vary only the relaxation budget and the maximum number of changed constraints. The underlying three-slot exemplar stays fixed. Every case uses the global displayed budget, including C11, whose original test fixture uses its own budget override. These are 16 independent synthetic contract cases, not 16 independent clinical patients or a prevalence estimate.

## Optional technical-view walkthrough

Use [NARRATIVE.md](NARRATIVE.md) for the new guided presentation. The older walkthrough below tours separate technical profiles in `/lab`.

| Time | Action | Message |
|---|---|---|
| 0:00–0:25 | Keep budget 0. Open C01, then C02. | DrugA and its entailed subclass both match exactly at zero cost. |
| 0:25–0:55 | Change budget to 1, then 2. Open C05. | The broadened query admits a concept alternative and a temporal extension, each costing 1. |
| 0:55–1:20 | Open C07, then C09. | Relaxation never changes the hard numeric threshold. An unresolved unit remains unresolved. |
| 1:20–1:40 | Uncheck source completeness, then restore it. | All 16 results become unresolved when the search is incomplete. |
| 1:40–2:15 | Open PRO & SOLID. Expand a binding and rebuild the graph if enabled. | Process, patient role, bearer and source are preserved. 10 mg/L normalizes to 1 mg/dL. |
| 2:15–3:00 | Open Ontology & time. Run Rust reasoning if enabled. | The separate bounded profile returns P1 certain, P2 possible only, P3 no recorded match and P4 incomparable. |

Use **Export this query & result** to retain a case, the active controls and the result. Use the other views' download buttons for RDF and the complete semantic/temporal report.

## Technical-view fixture totals

With source completeness checked and at most two changed constraints:

| Budget | Exact | Relaxed | Unresolved | No recorded match |
|---|---:|---:|---:|---:|
| 0 | 5 | 0 | 2 | 9 |
| 1 | 5 | 3 | 1 | 7 |
| 2 | 5 | 5 | 1 | 5 |

Unchecking completeness yields 16 unresolved cases under every budget. “No recorded match” is a conclusion within the specified record scope, not proof of clinical absence. Certain answers in the bounded profile require one fixed named binding across all feasible source timelines. A possible answer is not a probability.

## Scope

The point-anchor matcher supports a fixed DrugA/creatinine exemplar with named taxonomy entailment and explicit costed relaxation. All eleven guided histories have actual PRO/SOLID source projections and evidence; the cohort selects among ten candidates. It permits only the existing exemplar's budget controls, not arbitrary query construction. Source completeness is part of each patient's fixed snapshot and is not inferred from the visible rows.

The separate Rust demonstration uses a restricted OWL rule module, independently checked before temporal evaluation. It does not import or verify the complete SULO ontology. In the technical view, the PRO/SOLID tab remains a separate worked graph example; those 16 contract fixtures do not all claim RDF projections.

This demo does not implement arbitrary cohort authoring, all-pairs retrieval, live EHR ingestion, treatment-effect estimation, or a validated AKI phenotype. The slide about MIMIC reports aggregate source-window planning evidence, not accepted real-source claims or clinical matches.

## Verification

Fourteen Python integration tests cover the existing technical demo and the guided cohort: expected membership, all eleven graph reconstructions, role/bearer identity, source hashes, live HTTP queries and export, offline parity, reproduction and tamper detection, plus pipeline diagnostics. `test_guided_ui.cjs` checks both execution modes, every patient at each stop, export, custom controls, live failures, stale-response handling and reset using a DOM test double. It is not a browser layout test.

```sh
python -m unittest discover -s demo -p 'test_*.py'
node demo/test_guided_ui.cjs
```

The original packaged snapshot passed 604 suite tests plus 16 oracle cases and seven property checks. The demo's live HTTP responses matched every one of the 480 recorded control/case combinations. The live graph pipeline returned EXACT at cost 0, and the Rust pipeline returned READY with P1 certain. JavaScript rendering logic was checked without a browser. Visual browser interaction could not be inspected in the authoring environment because its browser policy blocks local pages; rehearse once in your browser before presenting.

To rerun the repository tests after installing the optional dependencies:

```sh
python reference_oracle.py
python -m unittest discover -s patterns -p 'test_*.py' -t .
```

The source repository is MIT licensed ([LICENSE](../LICENSE)). The vendored SULO ontology retains its own CC0 terms. No MIMIC patient rows are redistributed in this package.

## Rebuild the recorded demonstration

With the full dependencies installed, run from the repository root:

```sh
python -m patterns.pro_solid --output demo/evidence/pro-solid
python -m patterns.semantic_support --output demo/evidence/semantic
python demo/build_demo.py
python demo/build_guided.py
python -m unittest discover -s demo -p 'test_*.py'
node demo/test_guided_ui.cjs
```

`build_demo.py` rebuilds the technical replay and requires a successful semantic gate. `build_guided.py` constructs eleven independent graphs, validates and projects each with `patterns.pro_solid`, computes every guided result with `reference_oracle.py`, and bundles the CSS, JavaScript, data and `story.json` into `Guided_Cohort_Demo.html`. Rebuilding requires the pinned graph dependencies; serving the committed guided artifacts does not.

The builders record source fingerprints and their generation-base commit. Generated examples are committed so the core server and replay work immediately after cloning. The evidence reports identify the native build used to generate them; they are recorded results, not a claim about the visitor's runtime. The complete product UI remains specified in `ui/`; this demonstration exposes only the existing bounded examples.
