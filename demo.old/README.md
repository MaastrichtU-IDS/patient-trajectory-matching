# Patient Trajectory Matching: presentation demo

This package contains a working local interface backed by the project's **unchanged Python reference matcher**, with optional live PRO/SOLID and Rust pipelines. It uses synthetic fixtures only.

Repository snapshot: `adeeee27de3a1915d667cce9cec10ed559456df8` (merged PR #25).

## Start the working demo

Unzip the package, open a terminal in the `trajectory-demo` folder, and run:

```sh
python3 serve.py
```

On Windows, use `py serve.py`. Python 3.10 or newer is required. The main matcher needs no third-party packages and runs without Internet access.

Open **http://127.0.0.1:8765** in your browser. The top-right label should say **Live execution · repository Python oracle**. Every change of the query controls executes the original Python matcher. Keep the terminal open. Stop it with Ctrl+C. To choose another port, use `python3 serve.py --port 8766`.

The server binds to your computer's loopback interface and serves only fixed demo endpoints. It does not accept patient uploads or arbitrary queries.

## Enable live graph rebuilding and Rust reasoning

The PRO & SOLID and Ontology & time views initially show actual recorded repository outputs. To execute those pipelines from their buttons, use Python 3.12 and install the pinned dependencies once:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r source/patterns/requirements-semantic.lock.txt
python serve.py
```

On Windows, activate with `.venv\Scripts\activate`. Package installation needs Internet access. The pipelines then run locally without an AI service, Java or clinical credentials. Native rustDL availability depends on the platform's compatible wheel or build support; core matching and the recorded replay remain usable without it. If dependencies are absent, the buttons explain the missing setup and retain the recorded results.

## Offline presentation backup

Open `Patient_Trajectory_Demo.html` directly in a browser. This is an **interactive replay of 480 recorded Python evaluations**, spanning all available query-control combinations. It does not execute Python or Rust in the browser. Its execution-mode label says so. All values, evidence and timelines are bundled, with no CDN, external font or network dependency.

Budget controls intentionally vary only the relaxation budget and the maximum number of changed constraints. The underlying three-slot exemplar stays fixed. Every case uses the global displayed budget, including C11, whose original test fixture uses its own budget override. These are 16 independent synthetic contract cases, not 16 independent clinical patients or a prevalence estimate.

## Three-minute demonstration

| Time | Action | Message |
|---|---|---|
| 0:00–0:25 | Keep budget 0. Open C01, then C02. | DrugA and its entailed subclass both match exactly at zero cost. |
| 0:25–0:55 | Change budget to 1, then 2. Open C05. | The broadened query admits a concept alternative and a temporal extension, each costing 1. |
| 0:55–1:20 | Open C07, then C09. | Relaxation never changes the hard numeric threshold. An unresolved unit remains unresolved. |
| 1:20–1:40 | Uncheck source completeness, then restore it. | All 16 results become unresolved when the search is incomplete. |
| 1:40–2:15 | Open PRO & SOLID. Expand a binding and rebuild the graph if enabled. | Process, patient role, bearer and source are preserved. 10 mg/L normalizes to 1 mg/dL. |
| 2:15–3:00 | Open Ontology & time. Run Rust reasoning if enabled. | The separate bounded profile returns P1 certain, P2 possible only, P3 no recorded match and P4 incomparable. |

Use **Export this query & result** to retain a case, the active controls and the result. Use the other views' download buttons for RDF and the complete semantic/temporal report.

## Expected totals

With source completeness checked and at most two changed constraints:

| Budget | Exact | Relaxed | Unresolved | No recorded match |
|---|---:|---:|---:|---:|
| 0 | 5 | 0 | 2 | 9 |
| 1 | 5 | 3 | 1 | 7 |
| 2 | 5 | 5 | 1 | 5 |

Unchecking completeness yields 16 unresolved cases under every budget. “No recorded match” is a conclusion within the specified record scope, not proof of clinical absence. Certain answers in the bounded profile require one fixed named binding across all feasible source timelines. A possible answer is not a probability.

## Scope

The point-anchor matcher supports a fixed DrugA/creatinine exemplar with named taxonomy entailment and explicit costed relaxation. The separate Rust demonstration uses a restricted OWL rule module, independently checked before temporal evaluation. It does not import or verify the complete SULO ontology. The PRO/SOLID view is a separate worked graph example, not an RDF claim for every displayed case.

This demo does not implement arbitrary cohort authoring, all-pairs retrieval, live EHR ingestion, treatment-effect estimation, or a validated AKI phenotype. The slide about MIMIC reports aggregate source-window planning evidence, not accepted real-source claims or clinical matches.

## Verification

The packaged repository passed all 604 suite tests plus 16 oracle cases and seven property checks. The demo's live HTTP responses matched every one of the 480 recorded control/case combinations. The live graph pipeline returned EXACT at cost 0, and the Rust pipeline returned READY with P1 certain. JavaScript rendering logic was checked without a browser. Visual browser interaction could not be inspected in the authoring environment because its browser policy blocks local pages; rehearse once in your browser before presenting.

To rerun the repository tests after installing the optional dependencies:

```sh
cd source
python reference_oracle.py
python -m unittest discover -s patterns -p 'test_*.py' -t .
```

The source repository is MIT licensed (`source/LICENSE`). The vendored SULO ontology retains its own CC0 terms. No MIMIC patient rows are redistributed in this package.
