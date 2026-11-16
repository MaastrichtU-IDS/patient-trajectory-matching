# Hosting plan for the store-backed mockups

A decision memo for the owner. Nothing here has been deployed, built in the cloud or pushed. The config files named below are drafts in `deploy/cloudrun/`. No secret was read; the owner secret (`ptm-demo-owner`) is referred to by name only.

## Recommendation

**Option (b): a new Cloud Run service, `ptm-mockups`, running `demos/mockups/server.py` with the synthetic store only, behind Cloud Run's own IAM sign-in.** Keep `ptm-demo` as it is. Do not put any MIMIC-derived store on hosting until the owner has made the licence call in section 2. Option (c) adds one environment variable and one data copy to (b), so deciding it later costs little. Stage 3 should keep the in-browser path as a labelled fallback for now (section 6).

No server change is needed to start. Three follow-ups would make a *public* URL reasonable (an owner-secret launcher, an in-app rate limit and a smaller body limit; section 3). With IAM sign-in in front, none blocks a first deployment.

## 1. What is deployed today, and what the store-backed mockups need

**Today.**
- `ptm-demo` (Cloud Run, project `dumontier-charm`, `us-central1`) runs the root `Dockerfile`: the research application `python -m app.server`, wrapped by `deploy/cloud_run.py` (a loopback relay), with the owner secret mounted from Secret Manager, one instance, `--no-iap --no-invoker-iam-check` ([deployment.md](deployment.md), "Cloud Run with an owner password"). It does not serve `demos/mockups/`, and its image installs the semantic lock, not duckdb.
- `demos/mockups/Dockerfile` is a *static* image (nginx, the pages, and the cohort regenerated from the committed generator). It has no Python server, so it cannot serve `/api/trajectory-store/*`. It is what option (a) uses. Nothing in the repository says it is deployed.

**What the store-backed mockups need** (from `server.py`, `store_trajectory.py`, D37):

| Question | Answer |
|---|---|
| Runtime | CPython 3.12, `patterns/` (the engine and store), `jsonschema`, and `duckdb==2.0.0` (`patterns/requirements-store.lock.txt`). No Java, no rustDL. `server.py` uses the standard library's HTTP server. |
| Build or bake the store | **Build at startup.** About 1.3 s for the page's cohort (48 patients), on a background thread; the page reads `GET /api/trajectory-store/status` and falls back to the browser until it is ready. The build is deterministic (same cohort, same snapshot), so baking would save about a second and add a stale-snapshot risk. If wanted later, `--trajectory-store-root` reuses a build of the same cohort, but the root must be outside the repository. |
| Cohort | Regenerated at image build from the committed generator (`synthetic/generate.py`), as the static image does. Never copied from a working tree. |
| Memory | Start at 1 GiB. The synthetic store is small; most memory is Python, DuckDB and the in-memory `/tmp` (Cloud Run's filesystem counts against memory). **Not measured.** The first deployment should read the memory metric and adjust. D19 shows the store's build memory grows steeply with cohort size, so this holds only for the 48-patient cohort. |
| CPU | 1 vCPU, startup CPU boost on. Matching is per request and short at this size. |
| Cold start | Python start, duckdb import and the store build: expected a few seconds, **not measured**. With `minScale: 0` the first visitor after idle waits; the page keeps working meanwhile through its fallback. `minScale: 1` removes the wait for an idle charge. |
| State | Nothing must survive. Each instance builds the same store and the page holds its session in the browser, so autoscaling is safe here (unlike `ptm-demo`, which must stay single-instance). A temporary store is removed on SIGTERM, SIGINT and SIGHUP, not on SIGKILL; on Cloud Run it lives in memory and disappears with the instance anyway. |
| Port and host | `--host 0.0.0.0 --port $PORT`. `server.py` defaults to 127.0.0.1. |
| Static surface | `server.py` serves its own directory as static files. The image copies only the pages and server modules, not tests, results or a MIMIC store. |

## 2. Options

Costs are orders of magnitude to check against the current Cloud Run price calculator; nothing was measured.

### (a) Static hosting only, keeping the in-browser path

- *What.* The existing `demos/mockups/Dockerfile` (nginx) on Cloud Run, or any static host (a bucket with a CDN, GitHub Pages).
- *Cost.* Near zero (a demo fits the free tier; a bucket is cents a month).
- *Effort.* Smallest: the image exists. It needs a deploy command and an access decision.
- *Risk.* It shows the page as it worked before D37: the store is not under the page, so visitors see something other than what the research uses. The store-backed matches, the engine endpoint and the store query page are absent. Stages 1 and 2 of D29 are not demonstrated. A static site is public unless fronted by auth; the cohort is synthetic, so that is a reputational risk, not a data risk.

### (b) Cloud Run with `server.py` and the synthetic store only (recommended)

- *What.* `deploy/cloudrun/Dockerfile.mockups`, one service, `ptm-mockups`, 1 vCPU and 1 GiB, 0 to 2 instances, concurrency 20.
- *Cost.* With `minScale: 0`, billed only while serving: probably inside the free tier at demo traffic. With `minScale: 1`, an always-on instance of this size is an idle charge, probably in the low tens of dollars a month (verify). Artifact Registry and Cloud Build add small amounts.
- *Effort.* About half a day for the first deployment, mostly access and checking. Draft files exist. A source deploy cannot be used (the Dockerfile is not at the root), so the draft builds with Cloud Build and `-f`.
- *Risk.* Low on data: the image holds only the synthetic cohort, which the page already downloads. Medium on exposure: `server.py` has no authentication, rate limit or request logging of its own (section 3); IAM sign-in covers the first. A visible cold start. Memory sizing is a guess until measured.

### (c) (b) plus the MIMIC demo store page

- *What.* A store built from the open MIMIC-IV demo (100 patients) goes into the image or a bucket, and `PTM_MIMIC_STORE` points at it, so `store-query/` answers aggregate counts.
- *Cost.* (b) plus a bucket or a larger image. The demo store is small; storage is cents.
- *Effort.* (b) plus building and shipping the store (a manual local step: `patterns.mimic_store_ingest --chartevents` from `data/local-mimic/`), a separate image or a mounted bucket, and a deploy-time check.
- *Risk.* **A licence question, not a technical one.**

**The question for the owner: may an image or bucket carry a store built from the open MIMIC-IV demo?** This memo does not decide it.

What is known:
- The demo is open-access, and the repository already commits digests derived from it (D17: acceptable for open data, unlike credentialed MIMIC).
- Its licence is the Open Database Licence (ODbL): copying and derived databases are allowed with attribution and share-alike terms, so a published database derived from it may have to be offered under the same licence. Whether a DuckDB store built from it, or the aggregates the endpoint serves, count as a derived database or a produced work under ODbL is a legal reading.
- PhysioNet asks for a citation of the dataset and of the PhysioNet platform. A hosted page would have to carry them.
- The aggregate-only endpoint is **not disclosure control** (D29's review isolated one patient's maximum heart rate to 0.01 bpm in 17 queries by halving a threshold). That is acceptable only because the data are the open demo. A hosted (c) lets anyone with access reconstruct row-level values of the demo, which the demo's own licence permits but the page's "aggregate counts" wording does not suggest. The page should not be described as protecting the patients.
- The content gate (exactly 100 patients whose subject ids hash to `DEMO_SUBJECTS_SHA256`) keeps *credentialed* data out of the endpoint. It is not a licence check.

Needed from the owner: (1) whether hosting a derived store is within the ODbL terms for this project, and the attribution and share-alike wording; (2) whether PhysioNet's terms for the demo add anything; (3) who may reach the page (a named group behind IAM is the cautious reading). Until then, ship (b). The (b) draft cannot carry MIMIC data: it never COPYs `data/local-mimic`, the root `.dockerignore` excludes it, and `PTM_MIMIC_STORE` is not set. Step 5 of the checklist checks the built image.

## 3. Security

- **Authentication.** Two patterns exist.
  1. *The existing owner-secret pattern* (`ptm-demo`): Basic login, secret in Secret Manager, service public through `--no-invoker-iam-check` (the organisation's domain-restricted sharing policy rejects `allUsers`). It needs a launcher that does the authentication. `deploy/cloud_run.py` is tied to `app.server`, and `server.py` has no authentication, so this means a new launcher around `server.py` (follow-up 1).
  2. *Cloud Run IAM* (recommended for the first deployment): the default for a new service (`--no-allow-unauthenticated`). Only identities holding `roles/run.invoker` reach it, through `gcloud run services proxy` or an identity token. No code, no secret, and Cloud Audit Logs record who called, which the owner-secret pattern cannot. Its limit: someone without a Google identity holding that role cannot open it. If a shareable link is wanted, pattern 1 is the way, with its stated trade-offs (one shared credential, no record of who).
  The owner secret is not mounted in `ptm-mockups`, and nothing here touches it.
- **Rate limiting.** Cloud Run has none of its own. Available now: `maxScale: 2` and concurrency 20 bound cost and CPU. A per-client limit needs Cloud Armor behind a load balancer (paid, more setup) or an in-app limiter (follow-up 2). With IAM in front the caller set is small and this can wait; if the link becomes public it cannot.
- **Request size.** `server.py` accepts bodies up to 20 MiB (`MAX_BODY`). The real requests are small; a public service should lower this (follow-up 3).
- **Content gates (server-side, unchanged).**
  - The synthetic store is refused unless its dataset id and patients are exactly the generator's cohort's.
  - The MIMIC endpoints serve only a snapshot of the open demo, by content (100 patients, pinned digest), answer aggregate counts only against a closed schema, and refuse anything else.
  - In (b) the second gate is dormant: no MIMIC store is configured, and `/api/store/status` says so.
- **No credentialed data, ever.** No credentialed MIMIC and no local patient records in any image, bucket or secret. D17: committed reports on credentialed data carry aggregate counts only. D29: a credentialed deployment would need real disclosure control (suppression, noise or query auditing), which is not built. This plan and any later one stop at the open demo. The build copies explicit paths only, and the context excludes `data/local-mimic`.
- **Logging.** `server.py` silences its own access log unless a flag is set. Cloud Run's platform request logs (path, status, latency, and the caller's identity under IAM) go to Cloud Logging by default and are enough. Do not add request-body logging. Set a retention period on the log bucket.
- **Image hygiene.** Non-root user (10001), explicit COPY lists, binary wheels only, `duckdb==2.0.0` pinned. The base image should be pinned by digest as deployment.md describes (follow-up 6). The draft does not enforce a read-only root.

## 4. Deploy checklist for option (b)

For the owner, from the repository root of a clean checkout of the reviewed commit. None of it has been run.

```sh
# 0. Check the tree, and run the server locally (no cloud). Stop it afterwards.
git status --short && git rev-parse HEAD
/Users/dumontier/code/ptm/.venv/bin/python demos/mockups/server.py --port 8091 &
curl -s http://127.0.0.1:8091/api/trajectory-store/status ; kill %1

# 1. Enable the services (once).
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com --project dumontier-charm

# 2. An Artifact Registry repository (once).
gcloud artifacts repositories create ptm --repository-format=docker --location=us-central1 --project dumontier-charm

# 3. Build on Google from the draft Dockerfile.
gcloud builds submit --config deploy/cloudrun/cloudbuild.mockups.yaml --project dumontier-charm .

# 4. Read the digest and put it into deploy/cloudrun/service.mockups.yaml (REPLACE_WITH_IMAGE_DIGEST).
gcloud artifacts docker images describe us-central1-docker.pkg.dev/dumontier-charm/ptm/ptm-mockups:draft --format='value(image_summary.digest)'

# 5. Check the image carries no MIMIC data. The find and the env lines should print nothing.
docker pull us-central1-docker.pkg.dev/dumontier-charm/ptm/ptm-mockups:draft
docker run --rm --entrypoint sh us-central1-docker.pkg.dev/dumontier-charm/ptm/ptm-mockups:draft -c 'find / -xdev \( -iname "*local-mimic*" -o -iname "*.duckdb" -o -iname "*.parquet" \) 2>/dev/null; env | grep -i mimic'

# 6. Deploy, IAM-gated (no allUsers, no secret, no MIMIC variable).
gcloud run services replace deploy/cloudrun/service.mockups.yaml --region us-central1 --project dumontier-charm
#    or the same as flags: deploy/cloudrun/deploy-flags.mockups.sh.txt

# 7. Let named people in (replace the address), then reach the service through the proxy.
gcloud run services add-iam-policy-binding ptm-mockups --region us-central1 --project dumontier-charm \
  --member="user:OWNER_EMAIL" --role=roles/run.invoker
gcloud run services proxy ptm-mockups --region us-central1 --project dumontier-charm --port 8092
#    then, in another terminal, and by opening http://127.0.0.1:8092/trajectory/ :
curl -s http://127.0.0.1:8092/api/health
curl -s http://127.0.0.1:8092/api/trajectory-store/status   # ready, 48 patients, the generator's snapshot
curl -s http://127.0.0.1:8092/api/store/status              # must say there is no MIMIC store

# 8. Read memory and cold-start numbers (Console, Cloud Run, Metrics), adjust the draft,
#    set a log retention period, and write the measured figures into this document.

# Roll back, or remove:
gcloud run services update-traffic ptm-mockups --to-revisions=PREVIOUS_REVISION=100 --region us-central1 --project dumontier-charm
gcloud run services delete ptm-mockups --region us-central1 --project dumontier-charm
```

`ptm-demo` and its secret are not touched by any step.

## 5. Draft config files

All are drafts, never built or applied, in `deploy/cloudrun/`:

- `Dockerfile.mockups`: Python 3.12 slim, the store lock, explicit COPY lists, the cohort regenerated in a first stage, non-root, `CMD` binds `0.0.0.0:$PORT`. It sets no MIMIC variable and copies no MIMIC data.
- `service.mockups.yaml`: the Cloud Run service (1 vCPU, 1 GiB, 0 to 2 instances, concurrency 20, startup CPU boost, startup probe on `/api/health`, no secret).
- `deploy-flags.mockups.sh.txt`: the same as `gcloud run deploy` flags (a text file so it cannot be run by accident).
- `cloudbuild.mockups.yaml`: the Cloud Build step that builds with `-f`.

To settle at the first build: whether the COPY list is complete (`patterns/_resources.py` finds inputs from the repository root, so `schemas/` and `ontology/` are copied whole); whether the root `.dockerignore`'s `deploy/*` needs an exception for `deploy/cloudrun/` (Docker and Cloud Build normally still read an ignored `-f` file, but this was not tried); whether `/api/health` answers while the store builds; and the memory figure.

For option (c) a *separate* Dockerfile would add the store and set `PTM_MIMIC_STORE` and `PTM_MIMIC_D_ITEMS`. It is deliberately not drafted, so nothing in the repository can be built with MIMIC data by mistake before the licence call.

## 6. What stage 3 then means

D29's stage 3: once a server with the store is deployed, the page talks to it, and the in-browser path is retired or kept as a demo.

- **Recommended: keep the in-browser path as a labelled fallback; do not retire it yet.** D37 declared page-only behaviours (reconciliation by generator truth, letter timing, medication courses) that the store does not port; retiring the path removes the only place they exist. It also serves static hosting (option (a) stays available) and an instance that is still cold-starting. The store-backed answer is already the default and the page names the backend. Retire it when those differences are resolved or accepted.
- **If retired**, what changes:
  1. The page's own candidate resolution and the `in-browser fallback: <code>` route go. A refusal then ends as a named refusal. Static hosting no longer serves a working trajectory page.
  2. `test_engine_parity.cjs` and `test_store_parity.cjs` lose their comparison target; parity becomes a regression test of the translator alone.
  3. The 25 reconciliation, 27 letter-mention and 11 medication-record differences cease to be "declared page-only": the page's behaviour is gone and the store's literal reading is the only one.
  4. The index windows, placed by the page over its reconciled view (D37), need a server-side placement rule from the store's own events (a store change).
  5. The research workspace reaches the store through a job API (counts interactively, full results as an asynchronous job), as D29 says. That needs a persistent, shared store, so it is not the stateless service of this plan: durable storage, an access design and a single writer are separate hosting decisions.
  6. A decision on `ptm-demo`: keep it as the research application, or fold the two into one service.
- **If hosting stays static** (option (a) only), D29 already says the in-browser path stays the demo path.

## Open questions for the owner

1. Is (b) behind IAM acceptable for a first deployment? Who holds `roles/run.invoker`?
2. Is a shareable link without a Google identity needed? If so, `server.py` needs an owner-secret launcher (follow-up 1), with its one-shared-credential trade-offs.
3. **MIMIC demo store on hosting (option (c)): is a store built from the open demo, or the aggregates it serves, a derived database or produced work under ODbL; what attribution and share-alike wording applies; do PhysioNet's terms add anything?** A legal or data-use call, not decided here.
4. A new service (`ptm-mockups`) or replacing what `ptm-demo` serves? The plan assumes new, so the research application and its secret stay untouched.
5. Cold start: accept `minScale: 0`, or pay for `minScale: 1`?
6. Region and project: `us-central1` and `dumontier-charm`, as for `ptm-demo`?

## Follow-ups (none done; `server.py` was not changed)

1. An owner-secret launcher for `server.py`, if a shareable link is wanted (the `deploy/cloud_run.py` pattern).
2. An in-app rate limit per client, for any public deployment.
3. A smaller `MAX_BODY` (now 20 MiB) when public.
4. Confirm `/api/health` answers while the store builds, so the platform probe does not depend on it.
5. A measured memory and cold-start report from the first deployment, written into this document.
6. Pin the base image by digest, as deployment.md describes.
7. For (c) only, after the licence call: a separate Dockerfile, an attribution and citation footer on `store-query/`, and a note on the page that counts-only is not disclosure control.
