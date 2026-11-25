# Hosting plan for the store-backed mockups, on the ids3 cluster

A plan for the owner and the cluster admin. Nothing here has been deployed or published. The manifests are drafts in
[`deploy/ids3/ptm/dev/`](../deploy/ids3/ptm/dev/), laid out as `ids3/projects/ptm/dev/` in the
`maastrichtu-ids/services` repo, ready to copy into a services-repo PR. This replaces the Cloud Run plan of the held
PR #153: the target is now ids3 (owner decisions of 2026-11-17 to 2026-11-20, recorded as D50 in the
[decision log](decisions/engine-increment-log.md)).

## Owner decisions this plan implements

- **Target:** ids3 (k3s, ArgoCD GitOps from `maastrichtu-ids/services`), project/env `ptm/dev`, namespace `ptm-dev`,
  host `ptm.k8s.semanticscience.org`.
- **Data on dev:** synthetic only. The design allows several datasets, each with its own access policy.
- **Observability:** logs only. Tracing is deferred; section 5 says how to turn it on.
- **Accounts:** one shared, open Keycloak realm for the owner's user-facing apps, separate from the infrastructure
  realm `ids3`. Self-registration by email or via ORCID, GitHub and Google; several providers per account; no roles
  until the owner adds the account to group `approved`; the owner emailed at each registration; a backup realm admin
  still to be named.
- **Dataset tiers:** small synthetic, public without login; MIMIC-IV demo, login plus `approved`; full MIMIC-IV, login
  plus `approved` plus a grant held in ptm (not a Keycloak role: the shared realm keeps broad grants only, D56),
  given by the owner after verifying and recording the user's own PhysioNet credentialing.
- **Full MIMIC-IV** is hosted only after a storage and backup access review. Credentialed users may then see
  per-patient results. Until then no credentialed data on hosting, and the aggregate-only endpoint is not disclosure
  control.

## Phase 1 (owner's decision, 2026-11-23)

The owner approved phase 1:
- the synthetic-only public demo at `ptm.k8s.semanticscience.org`, with no sign-in, no Keycloak and no secrets;
- **the image public** on GHCR (`ghcr.io/maastrichtu-ids/patient-trajectory-matching/mockups:0.1.0`, deployed by
  digest).

The server runs with no realm and serves only the public synthetic cohort. Every hosted bound in this plan stays:
- the front door's rate limits, per-client match limits, body limit and HSTS;
- the CSP;
- the child-process matcher with its time limit and search budget;
- the NetworkPolicies and JSON logs.

`allow-keycloak-egress.yaml`, `vault.yaml` and the realm checklist are kept for **phase 2 (sign-in)**, which is
deferred. Phase 2 adds the realm `ids-apps`, the `PTM_OIDC_*` settings and the two files back into the kustomization.
Everything below describes the full design; the parts about accounts and tokens apply from phase 2.

## 1. Architecture

```
browser ──TLS──> Traefik (front door, cert-manager letsencrypt)
                   │  Middlewares: HSTS, per-client rate limits (match routes: ~1/s), 256 KiB body limit
                   v
            Service ptm-mockups:8080 ──> Pod ptm-mockups (namespace ptm-dev; egress-locked; ingress: Traefik only)
                                          server.py: static pages, engine, dataset registry,
                                          bearer-token check, per-dataset policy
                                          synthetic store built at start into an emptyDir
                                          │ JWKS (public keys), in-cluster, allow-keycloak-egress
                                          v
                                     platform/keycloak:8080, shared user realm
browser ──TLS──> keycloak.k8s.semanticscience.org (sign-in: PKCE; ORCID, GitHub, Google brokered)
```

- **One Deployment, one container**, the standard-library HTTP server in `demos/mockups/server.py`. It serves the
  pages, the trajectory-graph engine endpoint, and the dataset endpoints. No database, no PVC, no sidecar.
- **Stateless.** Every pod builds the same synthetic store from the cohort baked into the image; the page keeps its
  session in the browser. One replica on dev; more need no other change.
- **Sign-in happens in the browser.** The page uses the public client `ptm-mockups` (authorization code with PKCE,
  no secret) and sends the access token as a bearer token. The server verifies it itself, offline, against the
  realm's public keys (section 3). There is no server-side session, cookie or client secret.
- **Datasets are a registry** (`demos/mockups/datasets.py`, section 3): each has a store, a kind, an access policy and
  a results rule. On dev the registry holds the synthetic cohort only.

## 2. The image

- **Build file:** [`deploy/ids3/mockups.Dockerfile`](../deploy/ids3/mockups.Dockerfile), from the repository root.
  Its allow-list, `mockups.Dockerfile.dockerignore`, replaces the root `.dockerignore` for this build (a BuildKit
  feature; the image workflow builds with buildx), so the context holds only the engine, its contracts, the server
  modules, the pages and the generator. `data/`, `verification/` and any local store cannot reach the build,
  whatever is in the working tree. With the legacy builder the root `.dockerignore` applies instead (it also
  excludes `data/local-mimic` and `verification/`), and the explicit COPY lists still copy nothing else.
- **Not yet built.** The local Docker daemon here has no route to Docker Hub or PyPI, so the image has not been
  built. Instead the image's layout (its COPY lists) was reproduced in a scratch directory and the server started
  from it with `python -I`: the store built in 1.2 s, `/api/health`, `/api/ready`, `/api/datasets` and the pages
  answered, `/api/store/status` said no store is configured, and SIGTERM removed the store's build. The image
  workflow's checks are the first real build.
- **Pinned base:** `python:3.12-slim-bookworm@sha256:935c3c5b…` (resolved 2026-11-20). Python packages from
  `demos/mockups/requirements-hosting.lock.txt` (the store lock plus the token check's pins), binary wheels only.
- **Cohort:** regenerated at build from the committed generator and checked by a second run, as the static image and
  CI do. Never copied from a working tree.
- **Non-root** (uid 10001), read-only root filesystem in the pod, exec-form `CMD` so Python is PID 1 and receives
  SIGTERM (which removes the store's build).
- **Tags: never `latest`.** The image is published as `ghcr.io/maastrichtu-ids/patient-trajectory-matching/mockups:X.Y.Z`
  by a workflow triggered by a tag `mockups-vX.Y.Z`, and deployed by `X.Y.Z@sha256:<digest>` in `app.yaml`. A
  deploy is then a one-line, reviewable, revertable services-repo PR. The workflow is
  [`.github/workflows/mockups-image.yml`](../.github/workflows/mockups-image.yml), activated on 2026-11-23 for phase 1
  after each pinned action SHA was checked against the release named beside it. Before publishing, it starts the image
  read-only and checks health, readiness, the anonymous dataset list, that no MIMIC store is configured, and that no
  MIMIC file or variable is in the image. On a pull request touching the image's inputs it builds and checks only.

## 3. Accounts and access

### The realm

Set up from [`KEYCLOAK-REALM.md`](../deploy/ids3/ptm/dev/KEYCLOAK-REALM.md): import
`deploy/ids3/keycloak/ids-apps-realm.json` (no secret, no user, placeholder provider client ids), then enter the
provider secrets in the admin console, so no provider secret is ever in a file. Realm **`ids-apps`**, shared by
external-facing apps (design discussion: MaastrichtU-IDS/services#751). In short:

- Registration on, verify email on, login with email, no duplicate emails, brute-force detection, a password policy.
- Default roles stripped to the account console's `view-profile` and `manage-account`, so a new account holds no
  app role but can link providers. Realm role `approved`, granted only through group `approved`, and no other
  realm role: detailed grants live in each app (D56, services#751).
- Identity providers: GitHub, Google and ORCID, all with trust-email off (security review of phase 2); the built-in first
  broker login flow, which links to an existing account only after email verification or re-authentication. Users
  link more providers themselves in the account console.
- Client `ptm-mockups`: public, PKCE S256, exact redirect URIs and web origin, an audience mapper adding `ptm-api` to
  the access token, Full scope allowed off with `approved` its only scoped role (D56).
- MFA for admins: a browser flow that requires OTP for holders of `realm-management/realm-admin`. The owner and a
  backup admin (**to be named by the owner**) administer the realm from its own admin console, without `master`.
- Events: user events (90 days) and admin events (one year) saved, so registrations and approvals are recorded.
  Full MIMIC-IV grants and the credentialing checks behind them are recorded in ptm, in an append-only log kept at
  least a year, when that dataset is built (D56).
- **Registration notice (revised 2026-11-25, D56):** a platform-side poller reading a read-only database view of
  `ids-apps` `REGISTER` events, built by the platform admin (services#751); no Keycloak credential, no plugin.
  `KEYCLOAK-REALM.md` section 6 says what ptm asks of it. The platform Keycloak must still reach
  `smtp.maastrichtuniversity.nl:25` for verification emails.

### The token check (server side)

- `Authorization: Bearer <access token>` on any API request. No header: an anonymous caller.
- Verified locally with PyJWT: signature with a key from the realm's JWKS, **RS256 only** (no `none`, no HMAC, so no
  algorithm confusion); `iss` equal to `PTM_OIDC_ISSUER`; `aud` containing `PTM_OIDC_AUDIENCE`; `exp` and `iat` required,
  `nbf` checked when present, 30 s leeway; `typ` claim `Bearer`, so an ID token cannot stand in.
- **JWKS from `PTM_OIDC_JWKS_URI`**, the in-cluster Keycloak Service, configured separately from the issuer (the
  browser-facing URL); deriving one from the other is the failure sulo-schema-builder documents. Fetched without following redirects, with a
  5 s timeout and a 64 KiB cap, cached 10 minutes, re-fetched at most once every 30 s when a token names an unknown
  `kid` (key rotation); one fetch at a time, outside the lock. Keys not refreshed for an hour are no longer trusted
  (503 `AUTH_UNAVAILABLE`). `allow-keycloak-egress.yaml` opens exactly that destination.
- **The in-cluster key fetch is plain HTTP.** The platform Keycloak's Service offers only HTTP on :8080 in-cluster
  (`KC_HTTP_ENABLED`, TLS ends at the front door; `ids3/platform/keycloak/keycloak.yaml`), as sulo-schema-builder
  also uses. Risk: whoever can rewrite pod-to-pod traffic could substitute keys and forge tokens. Mitigations: the
  egress policy admits only Keycloak's pod on :8080; the cluster is a single node, so the traffic does not leave
  it; redirects are refused; only RSA signing keys are taken; held keys expire after an hour. **Stronger option, one
  configmap line:** set `PTM_OIDC_JWKS_URI` to the public `https://keycloak.k8s.semanticscience.org/realms/ids-apps/
  protocol/openid-connect/certs`. Python's `urllib` honours `HTTPS_PROXY`, so the fetch goes through the egress-proxy
  with TLS checked end to end, and `allow-keycloak-egress.yaml` becomes unnecessary (unlike sulo-schema-builder,
  whose `jose` ignores the proxy). The cost is a dependency on the front door and squid for key refreshes.
- `azp` must equal `PTM_OIDC_CLIENT_ID` when configured (a token issued to another client of the shared realm is
  refused), and `sub` is required.
- A present but invalid token gets `401 TOKEN_INVALID` (with `WWW-Authenticate: Bearer error="invalid_token"`), never a
  silent downgrade to anonymous. Keys unreachable: `503 AUTH_UNAVAILABLE` for that request; public datasets keep
  working for anonymous callers.
- Roles from `realm_access.roles`. `approved` is the realm role the group grants (`PTM_APPROVED_ROLE`).

### The dataset registry and the policy check

A dataset has an **id**, a **kind** (which backend serves it), an **access** policy and a **results** rule:

| Field | Values |
|---|---|
| `kind` | `synthetic-trajectory` (the trajectory store, D37), `mimic-demo-aggregate` (the store query service, D29), `mimic-iv` (reserved; refused) |
| `access` | `public`; `approved`; `{"role": "dataset:<id>:read"}` (a role also requires `approved`) |
| `results` | `per_patient` or `aggregate` |

- **Each kind has a floor the configuration cannot lower:** synthetic is at least public and answers per patient;
  the MIMIC-IV demo is at least `approved` and answers aggregates only; full MIMIC-IV is at least
  `{"role": "dataset:mimic-iv:read"}` (a placeholder: D56 moves this grant from the token into ptm before the kind
  is hostable). A registry entry may raise a policy (for instance a synthetic dataset behind a
  role, which the tests use), never lower it. An entry whose `results` differs from what its backend can produce is
  refused, since the field describes the backend and does not change it.
- **`mimic-iv` is refused outright** (`DATASET_KIND_NOT_HOSTABLE`) until the access review and a backend exist; the
  tier is reserved so the policy model is in place.
- **The content gates stay** and are independent of the policy: the MIMIC demo service serves only a snapshot whose
  patients hash to the open demo's 100 subjects, and the trajectory store only the generator's cohort, checked
  against the file served.
- **Per request:** the caller (anonymous, a verified token, or the local owner) against the dataset's policy. A
  dataset the caller may not use is `404 DATASET_NOT_FOUND`, exactly like one that does not exist, and the old routes
  answer as if no store of that kind were configured, so no response reveals a hidden dataset. The page learns why
  (sign in, awaiting approval) from the caller summary in `/api/datasets`. An invalid token is still
  `401 TOKEN_INVALID`.
- **Endpoints.** `GET /api/datasets` lists only the datasets the caller may use (id, title, kind, results, its
  endpoints, availability) and whether the caller is signed in and approved, so the page can say "awaiting
  approval". `GET /api/auth-config` gives the page the issuer and client id. Each dataset is reachable at
  `/api/datasets/<id>/status`, `.../catalogue` (aggregate kinds) and `.../match`. The existing routes stay as aliases
  of the first dataset of each kind the caller may use: `/api/trajectory-store/*` (synthetic) and `/api/store/*` (MIMIC demo), so the
  pages and tests keep working.
- **Configuration:** a JSON file named by `PTM_DATASETS` (on ids3, a ConfigMap mounted at `/etc/ptm/datasets.json`).
  Without one, the registry is built from the existing flags, as today: the synthetic cohort unless
  `--no-trajectory-store`, and the MIMIC demo if `--mimic-store` names one, at its floor `approved`.

### Without Keycloak: local development and the owner

- **There was no owner-secret path in `server.py`.** The owner secret of the earlier plans (`ptm-demo-owner`) belongs
  to the Cloud Run deployment of the research workspace (`app/access_control.py`, `deploy/cloud_run.py`); the
  mockup server never had one. The Cloud Run plan's follow-up "an owner-secret launcher for `server.py`" is
  superseded: on ids3 the owner signs in to the realm like anyone else, and locally nothing changes.
- **Local use is unchanged.** On a loopback bind with no realm configured, the person at the machine is the owner
  (a loopback port is reachable only from that machine), and is served every configured dataset, as before D50: a
  locally built MIMIC-IV demo store named by `--mimic-store` keeps working without any credential, and stays
  content-gated to the open demo's 100 patients (D29). A bearer token then gets `401 AUTH_NOT_CONFIGURED`.
- **With a realm configured**, the loopback owner is off and tokens decide, on loopback too (that is how the tests
  exercise the realm).
- **Fail-loud guard:** on any non-loopback bind, a non-public dataset without a realm means the server does not
  start (`NON_PUBLIC_DATASET_NEEDS_OIDC_ON_A_NON_LOOPBACK_BIND`). An incomplete realm configuration (one or two of
  the three `PTM_OIDC_*` variables) is refused too, never ignored.
- **On ids3 there is no owner secret.** The owner holds `approved`, the dataset roles and realm admin in `ids-apps`.
  Nothing is mounted, so nothing can leak from the pod. No `--owner-auth-file` was added: nothing needs it.

### The pages' sign-in

`shared/auth.js` (no library vendored): the authorization-code flow with PKCE S256 against the public client named
by `/api/auth-config`. **The access token is kept in memory only; the refresh token in `sessionStorage`.** Memory
alone breaks every page change, since each mockup page is a separate document, and would send the user back through
Keycloak each time; `sessionStorage` is per tab and cleared when the tab closes, and holds only the refresh token,
from which each page load gets a fresh access token. The `state` is checked before a code is exchanged, and the
verifier never leaves the tab. The index page lists only the datasets the caller may use (the store query card is
hidden unless a MIMIC-IV demo dataset is in the caller's list) and says when an account awaits approval; the store
query page explains a refusal (sign in, awaiting approval, role needed). Without a realm all of it is inert.
`test_auth_page.cjs` runs it in Node with the browser stubbed.

## 4. Kubernetes manifests (`deploy/ids3/ptm/dev/`)

Following the services repo's skill (`converting-compose-to-ids3`) and its ontoexplorer and sulo-schema-builder
examples:

| File | What |
|---|---|
| `kustomization.yaml` | `../../_base` (the `restrict-egress` NetworkPolicy), then the files below. The namespace `ptm-dev` comes from the projects ApplicationSet. |
| `configmap.yaml` | `ptm-mockups-config`: the OIDC issuer, JWKS URI, audience, client id and approved role; limits; log format; proxy variables; tracing variables commented out. `ptm-mockups-datasets`: the registry file, synthetic only. |
| `app.yaml` | Deployment and Service `ptm-mockups`: pinned image, non-root, read-only root, all capabilities dropped, no service-account token, an `emptyDir` for the store, probes and resources (section 6). |
| `ingress.yaml` | Traefik Ingress for `ptm.k8s.semanticscience.org`, `cert-manager.io/cluster-issuer: letsencrypt` with a `tls:` section, and the two Middlewares. No `router.entrypoints` annotation. |
| `middleware.yaml` | Traefik `rateLimit` (20 requests/s per client address, burst 60), a match `rateLimit` (1/s, burst 5), `buffering` (`maxRequestBodyBytes` 256 KiB) and HSTS (one year, no subdomains). |
| `ingress-match.yaml` | A second Ingress with Exact paths for the match routes (the two aliases and one per dataset), adding the match rate limit; it reuses the main Ingress's certificate. |
| `allow-keycloak-egress.yaml` | Egress to `platform/keycloak:8080` for the JWKS fetch only. |
| `allow-traefik-ingress.yaml` | Ingress to the pod from Traefik only (the front-door node addresses `minio-data/netpol.yaml` uses, and `kube-system`). |
| `vault.yaml` | VaultAuth and VaultStaticSecret for `secret/ptm/dev`, **ready but not listed**: ptm-dev has no secret today. |
| `README.md` | The owner and admin checklists, including the Vault policy, role and kv steps for the first secret. |
| `KEYCLOAK-REALM.md` | The realm checklist (section 3). It belongs beside `ids3/platform/keycloak/` once the realm is shared. |

- **Egress.** The namespace is egress-locked; the server makes no external call. The proxy variables are set for any
  later one, and `NO_PROXY` keeps the in-cluster JWKS fetch direct.
- **Secrets.** None today: the browser client is public, keys are public, synthetic data need no credential. The
  skill's Vault and VSO wiring is ready for the first secret (README, "Vault").
- **Rate limiting and body limits** (follow-ups 2 and 3 of the Cloud Run plan). At the front door: Traefik's
  per-client rate limit and body limit, so abuse is refused before the pod. Behind it, in the server: `PTM_MAX_BODY`
  (256 KiB on ids3; the local default stays 20 MiB) and `PTM_MATCH_CONCURRENCY` (4 matches at once per pod; one more
  waits briefly, then gets `503 BUSY`), which bounds CPU whatever the client address. The admin confirms Traefik's
  `kubernetescrd` provider is on (first Middleware in the services repo) and that Traefik sees client addresses
  directly.
- **Security headers** added by the server on every response: a Content-Security-Policy (below),
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`, a `Server` header
  without versions; `Cache-Control: no-store` on API answers as today. HSTS comes from Traefik (`ptm-hsts`).

### The security review (2026-11-20) and what changed

An independent security review of the first implementation (`1af36d7`) found no critical or high issue: the token
check, routing and authorization held under every probe, and the loopback owner could not be reached remotely. It
found one medium and nine low issues, all fixed:

- **M1, anonymous CPU and memory exhaustion.** The anonymous engine route and the public store match read and
  parsed the body before the concurrency gate, on unbounded threads with no socket timeout: a valid 2 MiB engine
  request cost 0.44 s of CPU, so a few requests a second within the Traefik limit would saturate a CPU, and queued
  parsed bodies could exhaust the pod's memory. Now:
  - the match slot is taken **before** the body is read;
  - hosted, the anonymous engine route is off (404), since the page matches in the store and falls back to the
    browser;
  - every match route caps steps (12), relations and relaxations (40 each), and the engine route patients (500)
    and events (50,000);
  - requests in flight are capped at 64 (`PTM_MAX_THREADS`, 503 `BUSY` at once beyond, without a thread);
  - sockets time out after 30 s;
  - hosted, the gate defaults to 4 and the body cap is 256 KiB;
  - a second Ingress puts the match routes under a 1/s, burst-5 rate limit.
  A test reproduces the flood, scaled down: 14 concurrent valid requests against 2 match slots and 6 threads read
  at most 2 bodies at once and leave the server answering.
- **L1:** `azp` must match the client id. **L9:** `sub` is required.
- **L2:** held keys expire after an hour without a successful refresh; one fetch at a time, outside the lock; the
  plain-HTTP in-cluster fetch is documented with its mitigations and the https alternative (above).
- **L3:** the logging paths tolerate a request with no parsed path (for example `GET / HTTP/9.9.9`), and the server
  logs an unexpected failure as its exception class only, never with the client's address or a traceback.
- **L4:** an ingress NetworkPolicy admits only Traefik.
- **L5:** a Content-Security-Policy on every response:
  - `default-src 'self'`;
  - scripts from this origin plus the hashes of the pages' inline scripts, computed at startup;
  - styles may be inline (the pages use `<style>` blocks and style attributes);
  - `connect-src` limited to self and the realm's origin;
  - `frame-ancestors 'none'`, `base-uri 'none'`, `object-src 'none'`.
  A test checks every page against it: each inline script's hash is present, and no inline event handler,
  `javascript:` URL, `eval` or external resource appears. No browser was available here to load the pages under
  it. Refresh-token rotation is required in the realm checklist, with DPoP noted as an option. Sign-out revokes the
  refresh token and passes `id_token_hint` (or the client id) to the logout endpoint.
- **L6:** exact redirect and post-logout URIs instead of `/*`. The page sends its canonical address, so
  `/index.html` signs in as `/`.
- **L7:** the image workflow takes the tag only through `env:` and checks it against `^[0-9]+\.[0-9]+\.[0-9]+$`. Its
  actions are pinned by commit SHA, and `packages: write` is held only by the publish job, which pushes the very
  image the check job tested.
- **L8:** `/api/ready` returns `{"ready": ...}` only, with counts for the owner. It never opens a store: hosted,
  MIMIC stores open at startup; locally, an unused one is "idle" and does not hold readiness back. A dataset the
  caller may not use is a 404.
- **Owner mode:** only a request whose `Host` is `localhost`, `127.0.0.1` or `[::1]` (with any port) is the owner,
  so DNS rebinding and same-host tunnels get anonymous access.
- **Also fixed:**
  - hosted, no directory listings, and only page asset types are served (no `.py` sources or READMEs);
  - the `Server` header carries no version;
  - HSTS is sent through a Traefik headers middleware.
- **Queued:** `--require-hashes` for the lock files.
- **The re-check (of `42a3c6e`), M1 residual: one small request could hold the synthetic store.**
  - *Finding:* an anonymous 3.7 KB request with 12 chained `seizure` steps took 28.7 s under the store's lock at the
    default search budget, and minutes at 1,000,000. The engine accepts budgets up to 10,000,000, and the step cap
    bounded the steps, not the work.
  - *Measured, at one worker on the 48-patient synthetic store (M4 Pro; probes in `/tmp/ptm-m1/`, outside the
    repository):*
    - The page's own requests use at most 5 steps, 4 relations, 4 relaxations (16 relaxation sets, the graph
      template), a relaxation cost budget of 1 and a search budget of 40,000. Requests counted: all 313 option-sweep
      cases plus the 4 answered templates; the worst costs 0.07 s.
    - Clamping their search budget to 1,000 or to 500 changes **none** of their 317 answers.
    - Re-measured after rebasing onto D49 (coded records only by default): 486 requests (477 option-sweep cases,
      9 answered templates), with the same maxima. At the hosted clamp of 1,000 **none** of their answers changes,
      and the worst costs 0.09 s.
    - Adversarial shapes under the caps chosen below (6 steps, 8 relations, 6 relaxations, cost budget 1):
      - a chain of `any` steps with 1 to 2-day `after` windows: 0.15 s without relaxations;
      - the same chain with 4 tiny-cost relaxations (all 16 sets tried): 1.6 s at a budget of 1,000, 3.7 s at 5,000
        and above;
      - `during` and `after` with grace relaxations: 2.1 s;
      - counts on every step: 0.06 s;
      - 4 concept widenings: 0.8 s;
      - **first, last and any occurrences in a chain with 4 relaxations: 6.5 s even at a budget of 500.** This one
        is not bounded by the search budget: each relaxation set does occurrence and order checks the budget does
        not count.
    - The page's graph template has that same structure and costs 0.07 s only because its concepts are sparse. No
      cap on structure separates the two.
  - *Fix (server only; the engine is unchanged):*
    - When hosted, each synthetic-store match runs in a child process (`match_worker.py`: the same
      `TrajectoryStore`, its own copy of the store) and is killed at `PTM_MATCH_TIME_LIMIT` (2 s), answering
      `503 MATCH_TIME_LIMIT`. The page then matches in the browser, as it does for any refusal.
    - Restarting the child costs a 1.3 s rebuild.
    - The search budget is clamped to 1,000, so a long search ends as the engine's own `unresolved`
      (`SEARCH_BUDGET_EXHAUSTED`).
    - Hosted caps: 6 steps, 8 relations, 6 relaxations, and a relaxation cost budget of at most 1 (400 above it).
    - The trajectory page says so on a hosted copy (`/api/limits`, `shared/limits.js`).
  - **Measured bound: no hosted request holds the store for more than the 2 s limit.** The page's own requests take
    under 0.1 s, with answers identical to the in-process store's at 40,000.
  - *Tests:* the costliest shape, at a 1 s limit, is cut with 503 and the store answers the next request; the
    page-shaped requests answered through the child equal the in-process answers; the caps and the clamp; the
    reviewer's 6-step chain now answers in 0.15 s, and its 12-step chain gets 400.
- **The final check (of `f4bf811`).** It confirmed the per-request bound: no request held the store for more than
  about 2.2 s, plus a 1.1 s restart. It found no injection and no leak. It raised one medium-low and two lows, now
  fixed.
  - *Medium-low: one client kept the worker's queue full.*
    - *Finding:* a costly match cost about 3.2 s (the kill plus the restart), and the rate limit allowed one a
      second, so legitimate matches waited 12 to 16 s.
    - *Fix, in the pod:*
      - a spare worker is kept built, so after a kill the next match goes to it and the rebuild runs off the request
        path;
      - readiness waits for the spare;
      - a match waits at most `GATE_WAIT` (now 3 s) **in all**, for a match slot and then for the worker, before
        `503 BUSY`. A legitimate match is therefore answered or refused within 3 s plus one time limit;
      - each kill is logged as `match_killed` (reason only).
    - *Fix, at the front door:* `ptm-match-inflight` allows one match in flight per client address, and the match
      rate limit is about one per 3 s with a burst of 6. The page sends one match per edit, without debouncing, and
      the burst covers a run of quick edits. Clients behind one NAT address share this allowance, which is noted
      for the admin.
    - *Measured* in a Linux container (python 3.11, one CPU, the same wheels), running the reviewer's 30 s storm:
      - with only the pod's bounds (an attacker sending a costly match every second), every legitimate match is
        answered or refused BUSY within 3.04 s, never 12 to 16;
      - with the front door's bounds as well (one costly match in flight, one per 3 s), all 10 legitimate matches
        are answered, the slowest in 2.04 s.
    - *Tests (scaled down):* the in-pod flood keeps legitimate matches within the bound and logs the kills;
      one client within the front door's bounds cannot starve others.
  - *Low: `PTM_MATCH_TIME_LIMIT` was not validated.* When hosted it must be a finite number with
    0 < x <= 30, or the server refuses to start (tested: `inf`, `nan`, 0, -1, 31 and text). After the write, any
    exception kills the worker. A crash, an EOF or a line that is not an answer is `503 MATCH_WORKER_FAILED`, not
    `MATCH_TIME_LIMIT` (tested).
  - *Low and informational:*
    - The worker's protocol runs on a duplicate of its original stdout. Its fd 1 and `sys.stdout` point at the
      discarded stderr, so nothing printed (a DuckDB progress bar, say) can corrupt an answer.
    - The worker limits its own address space (RLIMIT_AS, `PTM_MATCH_WORKER_MAX_MIB`, 400 MiB, on Linux) and caps
      malloc arenas at 2.
    - *Measured at first:* DuckDB 2.0 starts `async_threads`, 4 per CPU it sees (by the cgroup limit, else every
      CPU), on top of the store's `threads=1`. A worker therefore used 305 MiB of address space at one CPU but 484 MiB
      at six, which a 400 MiB limit refuses. That is what failed the PR's CI run: the GitHub runner has several CPUs
      and no cgroup limit, the worker could not start, and readiness waited for it.
    - *Fixed:* the worker opens every DuckDB database with `threads=1` and `async_threads=1`, by defaulting the
      connection config in the worker process only (`confine_duckdb`; the engine and the store are unchanged, and the
      answers do not depend on it). Measured in a Linux container, a worker now holds 2 threads, about 145 MiB
      resident and 268 MiB of address space, the same at one CPU, at four (cgroup-limited) and at six (no limit).
      400 MiB holds on any host.
    - A worker that cannot start leaves the pod unready and logs `match_worker_start_failed` with its reason (the
      worker's own error, or the class of the exception it died of). The tests print that reason instead of a bare
      "not ready".
    - *The next CI run* then failed only the scaled-down flood test's timing (2.74 s against 2.5). On the slower
      runner a flood had used the spare before its replacement was built, and the next match built a worker on its
      own path. Now no build ever runs on a request's path: with no worker built, a match is refused at once
      (503 BUSY), so the bound (the wait plus one time limit) holds on any machine. Passed 3 runs out of 3 in a
      1.5-CPU Linux container.
    - The pod's memory limit is raised to 1 GiB: the server, the active worker and its spare.
- **The re-check's lows, fixed:**
  - *Probes:* 4 threads beyond `PTM_MAX_THREADS` serve only `/api/health` and `/api/ready`, and liveness is a TCP
    probe.
  - *IPv6:* the front door listens on IPv4 only (`traefik-config.yaml`), so no `ipv6Subnet` is needed; this is
    noted.
  - *Ingress policy:* the `kube-system` rule is narrowed to Traefik's chart label, and the Deployment is pinned to
    front-door nodes (`ids.maastrichtu/front-door=true`), so probes come from listed addresses.
  - *Realm:* when hosted, a realm requires `PTM_OIDC_CLIENT_ID`, so `azp` is always checked.
  - *Paths:* a path holding a NUL byte is a 404.
  - *Sign-in:* `auth.js` refreshes once at a time and adopts a refresh token rotated by another refresh, instead of
    signing out.
  - *Workflow:* before activating the image workflow, the pinned action SHAs and the releases named beside them are
    to be checked; this is a first-deploy step.

## 5. Logs, and tracing later

- **Logs (now).** With `PTM_LOG_FORMAT=json`, one JSON line per API request on stdout: time, method, route, status,
  duration, dataset, auth kind, and the token's `sub` for a signed-in caller (pseudonymous; it lets access to
  non-public datasets be audited). Never a body, a token, an `Authorization` header, a query value or an IP
  address. Static files are not logged, as today. The k8s-infra daemonset ships stdout to SigNoz; the pod label
  `app.kubernetes.io/name: ptm-mockups` becomes `service.name`. Retention is SigNoz's; the `sub` makes these lines
  personal data, which the retention setting should reflect.
- **Tracing (deferred).** There is no zero-code path for this server: OpenTelemetry's Python auto-instrumentation
  covers WSGI/ASGI frameworks and client libraries, not `http.server`. To turn it on:
  1. Add `opentelemetry-sdk` and `opentelemetry-exporter-otlp-proto-http` (pinned) to the hosting lock.
  2. Add a small `tracing.py`: when `OTEL_SDK_DISABLED` is not `true`, configure a `TracerProvider` with an OTLP/HTTP
     exporter from the standard `OTEL_*` variables; the handler opens one span per API request with the route,
     dataset, status and duration as attributes (never the `sub`, a body or a token). When disabled, or when the
     packages are absent, it does nothing, and an unreachable collector never fails a request.
  3. Uncomment the `OTEL_*` block in `configmap.yaml` and set `OTEL_SDK_DISABLED: "false"`. The endpoint is the
     central collector, `http://signoz-otel-collector.platform.svc.cluster.local:4318`, already allowed by
     `restrict-egress`. No collector sidecar, and no browser tracing (it would need a public ingest endpoint).

## 6. Store, health and resources

- **The synthetic store is built at startup, not at image build.** It takes about 1.3 s for the 48-patient cohort,
  on a background thread, into an `emptyDir` (`PTM_TRAJECTORY_STORE_ROOT=/tmp/ptm-store`). Reasons: every start
  re-runs the content check against the cohort the pod serves, so a stale or mismatched snapshot cannot be served;
  the build is deterministic, so baking would save about a second and add a second artefact to keep in step; the
  image stays free of a generated database; and the root filesystem can be read-only. Baking becomes worth it only
  for a store too large to build in seconds, which a MIMIC-IV demo store is (about two minutes, from files the image
  must not contain); that one would be delivered as a prepared snapshot, not built in the pod (section 7).
- **Health during the build (follow-up 4 of the Cloud Run plan, checked, and fixed).** The socket is bound before
  the build starts and the build runs on its own thread, but `main()` used to wait for the build's status line
  before entering `serve_forever`, so requests queued until the build ended (about a second). The status lines now
  come from a background thread, and `/api/health` answers during the build; a test holds the build open and checks
  it. `/api/ready` answers `{"ready": true}` (200)
  only once every started dataset is ready, else 503; only the owner also gets the counts, and the probe never
  opens a store. Probes: startup and liveness on
  `/api/health`, readiness on `/api/ready`. A pod whose store fails its content check never takes traffic, and with
  `maxUnavailable: 0` the old pod keeps serving. Found while measuring: on a machine whose reverse DNS is slow, the
  standard library's `server_bind` spent 35 s in `getfqdn()` before the socket answered; the server now skips it.
- **Resources.** Measured locally (M4 Pro, Python 3.12): about 180 MB peak resident memory with the store built and
  matches served. Requests `100m` CPU and `256Mi`; limits `1` CPU and `768Mi`; `emptyDir` capped at `256Mi`.
  Re-measure on the cluster after the first deploy (follow-up below).

## 7. What may be hosted

- **Dev: the synthetic cohort only.** No MIMIC data of any kind in the image, the ConfigMaps or a volume. The image
  workflow checks this before publishing.
- **The MIMIC-IV demo** (open data, ODbL; tier `approved`, aggregates only): phase 2. It is fetched from PhysioNet
  at start, checked against pinned sums and built into the pod; it is never in the image ("Phase 2: delivering the
  MIMIC-IV demo", below). Its content gate admits only the open demo's 100 patients, and the page carries the
  attribution and the note that counts-only is not disclosure control.
- **Full MIMIC-IV** only after the storage and backup access review: where the store lives, who can read the volume
  and its backups (cluster admins can read any Secret and any hostpath volume), encryption at rest, and retention.
  Then a backend, the `mimic-iv` kind enabled with a ptm-side grant (D56: grant store, append-only log kept at least
  a year and backed up, the credentialing record beside each grant), per-patient results for credentialed users.
  The review also requires an off-site backup of Keycloak's database (services#751: not in place today). Until then the server refuses the kind, and nothing
  credentialed goes near hosting.
- **The aggregate-only endpoint is not disclosure control** (D29: differencing isolated one patient's value in 17
  queries). It is acceptable for the open demo only; the role tier exists because credentialed data need credentialed
  users, not because counts protect anyone.

### Phase 2: delivering the MIMIC-IV demo (draft, 2026-11-23)

The owner wants the MIMIC-IV demo for approved users in phase 2: tier `approved`, aggregate counts only.

**Why it is not in the image.** The demo is open data (ODbL 1.0, no credentialing), but the image is public. Baking
the demo in would hand it to anyone who pulls the image, and the policy is approved users only.

**How the pod gets it.** The server fetches it at start (`demos/mockups/mimic_demo_fetch.py`, registry setting
`"fetch": "physionet:mimic-iv-demo-2.2"`):
1. **Download** the four `icu/` files the store's ingest reads (chartevents, d_items, icustays, inputevents; 6.4 MB)
   and PhysioNet's `SHA256SUMS.txt`, from `https://physionet.org/files/mimic-iv-demo/2.2/`. The fetch goes through the
   egress proxy (`HTTPS_PROXY`), with a 60 s timeout and a 64 MiB cap per file, 300 s for the whole download, and no
   redirects followed (`PHYSIONET_REDIRECT`): the files come from the pinned address only.
2. **Check** every file against the sha256 pinned in the module. The pins are the authority; three of them are also
   in `data/mimic-inputevents-demo-pin.json`, and the chartevents pin was added from PhysioNet's sums on 2026-11-23.
   PhysioNet's published sums must agree with the pins, or nothing is used.
3. **Build** the store with the existing ingest (`patterns.mimic_store_ingest --chartevents`, one worker) in a child
   process at the lowest CPU priority, with an address-space limit of 1600 MiB (`BUILD_MAX_MIB`, RLIMIT_AS), so a
   runaway build fails by itself (`STORE_BUILD_FAILED`) instead of getting the pod OOM-killed. A build that is not
   the pinned snapshot is never published (`SNAPSHOT_ID_NOT_PINNED`). The downloads are then deleted; `d_items` is
   kept for the catalogue's labels.
4. The store service opens the snapshot through its **content gate**: exactly the open demo's 100 subjects, by a
   pinned digest, and exactly the pinned snapshot id (`expected_snapshot_id`). It opens nothing until the fetch has
   finished: before that it answers `STORE_BUILDING` and remembers nothing.

**Behaviour while building or on failure.**
- **Readiness does not wait for it.** A fetched dataset is optional, so the public synthetic cohort serves from the
  first seconds.
- **Until the build ends,** the demo's routes answer `503 STORE_BUILDING`.
- **If a step fails,** they answer `503` with its code:
  - `PHYSIONET_UNREACHABLE`, `PHYSIONET_REDIRECT`, `FETCH_DEADLINE_EXCEEDED`;
  - `PHYSIONET_SUMS_DISAGREE`, `CHECKSUM_MISMATCH`, `FILE_TOO_LARGE`;
  - `STORE_BUILD_FAILED`, `STORE_BUILD_TIMED_OUT`, `SNAPSHOT_ID_NOT_PINNED`.

  These are a fixed list (`mimic_demo_fetch.FETCH_ERRORS`); any other failure is answered as
  `MIMIC_DEMO_FETCH_FAILED`, so no other exception text reaches a response.
- **Retries:** three attempts, at start, then after 1 and 10 minutes. After that the dataset stays unavailable until
  the pod restarts.
- **Logging:** each attempt is logged as a `mimic_demo_fetch` event, with the outcome, the code and the duration.
- `/api/datasets` shows an approved caller the demo with `ready: false` until it is built.

**Measured.**
- *Build* (one worker, an M4 Pro, the demo's real files): 493 s and 1.23 GB peak resident; the store is 47 MB on
  disk.
- *End to end* (`mimic_demo_fetch.py`, from the development machine on 2026-11-23): fetched from physionet.org, every
  check passed, built in 522 s at 1.27 GB peak. The store opened through the content gate under the real pin: 100
  patients, dictionary labels present.
- *Build memory:* can DuckDB's `memory_limit` and threads bring the peak under 1 GB? Measured with one worker and
  one DuckDB thread, the build already passes them, and every run took about 495 s with the same snapshot id:

  | DuckDB `memory_limit` | Peak resident |
  |---|---|
  | 256MB | 1.30 GB |
  | 512MB | 1.23 GB |
  | the store's default, 4GB | 1.27 GB |

  The peak is in the ingest's Python stages (the claim route), not in DuckDB, so no DuckDB setting brings it under
  1 GB. Lowering it would mean changing `patterns/`, which needs its own measured engine increment.
  - The build uses `512MB`, which keeps DuckDB's share bounded at no cost in time.
  - In a Linux container (one CPU, Python 3.11, DuckDB confined to one worker thread and one async thread as in
    the match workers, `MALLOC_ARENA_MAX=2`), the build child peaked at 858 MiB resident and 1228 MiB of
    address space, in 538 s, with the pinned snapshot id. macOS's figures above are higher for the same
    build; the pod runs Linux. Its limit, `BUILD_MAX_MIB` = 1600, is that address space times 1.3; the build passes under it with
    the same snapshot id. The pod's memory limit is in "The phase 2 security review" below.
  - With the volume, the build happens only at the first start, after the volume is lost, or after an ingest change
    alters the pinned snapshot id; routine restarts reuse the store.
  - The cluster's CPU may be slower than the M4 Pro.
**Kept across restarts (a persistent volume).** A rebuild at every start would cost a ten-minute demo outage and a
memory peak on every restart and rollout, so the store lives on a small PVC (`pvc.yaml`: `ptm-mimic-demo`, 1Gi,
Longhorn, RWO, mounted at `/var/lib/ptm-mimic-demo`).
- **At start,** `mimic_demo_fetch.prepare` reuses the store on the volume only if it passes every check:
  - it opens with every table's content re-derived from its files (`verify='content'`);
  - it is exactly the snapshot id pinned in the module (`0282b797…`, measured from the build of the pinned files);
  - `d_items` matches its pinned sha256.

  The store service's content gate then applies again. A verified store is ready in seconds (measured on a real
  demo store under the real pins: 2 to 3 s, content verification included); anything else is fetched, checked and
  built as above. **Nothing on the volume is trusted on its word.**
- **Publishing:** a new build is made in a scratch directory on the volume and moved into place only once complete,
  so no reader ever sees a half-built store.
- **Changing the ingest:** a change that alters the snapshot id fails closed. The build is never published
  (`SNAPSHOT_ID_NOT_PINNED`) and the store service would refuse it anyway, so the demo stays unavailable until the pin
  is updated with the change.
- **Leftovers:** an interrupted run's scratch directories (`mimic-demo-incoming-*`) and set-aside stores
  (`.stale-*`) are removed at the next start, before anything else.
- **Fallback:** without the PVC (the registry's `store` under `/tmp`), the store goes to the emptyDir and is rebuilt
  at every start.
- **Why RWO and `Recreate`:** RWO because Longhorn's RWX volumes are NFS-backed, where the pod's `fsGroup` does not
  apply, so uid 10001 could not write without a root chown step. RWO in turn means `strategy: Recreate`: a new pod on
  another node could not attach the volume while the old one held it. A rollout therefore has a short gap, of
  seconds, where phase 1 had none.
- **Not `longhorn-retain`:** the store is rebuildable from PhysioNet.

**Security consequence of the volume.** The PVC holds MIMIC-IV demo data. The data are open (ODbL), but the policy
serves them to approved users only, so who can read the volume now matters, as it will for full MIMIC-IV:
- *Cluster admins* (any kubeconfig, `kubectl port-forward`) can read every volume.
- *The Longhorn UI* can snapshot, attach and clone volumes. It sits behind oauth2-proxy and the `ids3` realm, and
  admits only holders of the role `longhorn-admins` (`ids3/platform/longhorn/oauth2-proxy.yaml`).
- *Backups:* Longhorn has no backup target configured today (`ids3/platform/longhorn/README.md`), so no copy leaves
  the node. Once a backup target exists, its backups carry the demo too, and who can read the target becomes part of
  this question.

For the open demo this is acceptable and recorded here. For full MIMIC-IV it is exactly the storage and backup access
review the owner asked for, and must be settled first.

**Egress.** The services repo's egress proxy (`ids3/platform/egress-proxy/configmap.yaml`) has no destination
allowlist on port 3128: any HTTPS destination is allowed for cluster pods and forwarded to the university parent proxy
`proxy.unimaas.nl`. So `physionet.org` needs **no services-repo change**. The admin confirms only that the university
proxy lets `physionet.org` through, which held from the development machine on 2026-11-23. The namespace's
`restrict-egress` policy already allows the egress proxy.

**Attribution.** The store query page now carries the attribution PhysioNet asks for:
- the dataset citation, Johnson et al. (2023), MIMIC-IV Clinical Database Demo (version 2.2), PhysioNet,
  https://doi.org/10.13026/dp1f-ex47;
- the licence, Open Database License v1.0, linked to PhysioNet's licence page;
- PhysioNet's standard citation, Pollard et al. (2026), Nature Health;
- the note that aggregate counts are not disclosure control.

The counts the page shows are produced works of the database. If the store itself (a derived database) were ever
published, ODbL's share-alike would apply; it is not published, since it lives only in the pod.

**The phase 2 security review (2026-11-23).** An independent reviewer reviewed `9bcd327` (D50 has the record): no
critical or high finding; three medium and six low, all fixed.
- **M1, startup race:** the startup report opened the store before the fetch had finished, and the failure was
  remembered. Now nothing opens a fetched store before its fetch is done (the store service's gate), it opens only
  the pinned snapshot, and a build that is not that snapshot is never published.
- **M2, admin OTP through a provider:** see `deploy/ids3/ptm/dev/KEYCLOAK-REALM.md`, section 5.
- **M3, one MIMIC request held the store for 73.7 s:** hosted, the demo's matches now run in child processes killed
  at the 2 s time limit (`match_worker.IsolatedStoreQuery`), at a search budget of 1,000, with at most 6 steps, 8
  relations, 4 relaxations and a cost budget of 1. Measured at one DuckDB thread: the page's examples 0.10 to 0.26 s
  (answers unchanged by the clamp); within the caps, the costliest of 75 adversarial shapes 1.23 s; beyond them,
  8 relaxations cost 5 s to over 40 s. The store query page explains each refusal.
- **Pod memory, with the MIMIC workers (L2).** Measured in a Linux container:

  | Process | Peak resident | Address-space limit |
  |---|---|---|
  | server, with both stores open | about 230 MiB (180 with the synthetic store, plus 48 for the demo's) | none |
  | synthetic match worker (×2: active and spare) | 145 MiB each | 400 MiB |
  | MIMIC match worker (×2: active and spare), over 132 matches (the examples and the worst in-cap shapes, repeated) | 404 MiB each | 600 MiB (545 measured) |
  | demo build child (first start only) | 858 MiB | 1,600 MiB (1,228 measured, ×1.3) |

  The build ends before the store opens, and the MIMIC workers start only after that, so the two never overlap.
  - *Measured:* about 1,330 MiB while building (server without the demo's store, two synthetic workers, the
    build) and about 1,330 MiB while serving (server and all four workers).
  - *The limits alone* (the re-check's low 2: no process can push the pod over even at its own limit):
    - while building, 1,600 + 2 × 400 + the server (about 180) = 2,580 MiB;
    - while serving, 2 × 600 + 2 × 400 + the server (about 230) = 2,230 MiB.
  - **The pod's limit is therefore 3Gi** (3,072 MiB): 16% above the first sum and 27% above the second. 2Gi fits
    neither, and no lower setting would help. The build's own limit cannot go much below its measured 1,228 MiB,
    and even a single MIMIC worker leaves the build phase at 2,580 MiB. So the MIMIC workers keep their spare, and
    their limit is 600 MiB (all 132 matches passed under it).
  - The server itself has no limit; its 180 to 230 MiB are measured. The request stays 512Mi, since scheduling
    needs only what the pod uses, and that is re-measured on the cluster after the first deploy.
- **Lows:** leftovers removed at start (L1); the build's address-space limit (L2); Google does not trust email
  (L3); no `offline_access`, no default group, forensic events audited (L4); no redirects and a 300 s download
  deadline (L5); fetch failures named from a fixed list (L6).

**The re-check (of `a3d994a`, 2026-11-23).** The reviewer confirmed M1 and M3 fixed, run through `main()` itself and
with the reviewer's own shapes, and M2 fixed for identity-provider logins. One medium and five lows, all addressed:
- **Medium, password reset gave admin access without the old OTP.** Keycloak's default reset flow ends in "Reset -
  Conditional OTP", whose unconfigured `reset-otp` only adds "Configure OTP". So a mailbox holder could set a
  password, enrol their own OTP and reach the console. The realm now binds its own reset flow, which denies holders
  of the admin-capable roles after the link is followed and before anything changes, and has no OTP reset. Admins
  recover through another admin (`KEYCLOAK-REALM.md`, section 5); `check_realm.py` checks the denial.
- **Low 1:** the admin-capable roles now include `manage-clients`, `manage-identity-providers` and `impersonation`,
  in the OTP conditions and the reset denial. `check_realm.py` refuses any other granted `realm-management` role
  but `view-*` and `query-*`.
- **Low 2:** the per-process limits alone fit the pod: MIMIC workers at 600 MiB, the pod at 3Gi (above).
- **The reviewer's shapes, re-run on the fixed tree:** the 73.7 s shape is refused (`TOO_MANY_RELAXATIONS`); the
  in-cap shapes answered in 0.2 to 1.8 s, and one (four zero-cost widenings, any occurrences, gaps of 1 to 2 h)
  passed 2 s and was killed at the limit, as designed. So within the caps the time limit, not the caps, is the
  bound.
- **Low 3:** a fetched store's gate is closed in every state but `ready`, before the fetch starts included.
- **Low 4:** the download's deadline is checked after every read of at most 64 KiB (`read1`), so a server that
  trickles bytes cannot hold it past the deadline by more than one socket read.
- **Low 5, accepted: an approved client can occupy the MIMIC worker.** There is one active MIMIC worker, so while
  one approved user's match runs (at most 2 s) the others wait, up to 3 s in all, then get `503 BUSY`. A client
  that sends costly matches back to back can keep it busy. This is inherent to one worker. It is bounded by the
  front door's `ptm-match-inflight`, one match in flight per client address on every match path (the MIMIC demo's
  included), so one client holds the worker for one match at a time; the spare takes over at once after a kill.
  Only approved users can reach the route at all, and each kill is logged (`match_killed`).

## 8. Deploying

1. Owner: name the backup admin, register the OAuth apps. Platform admin: the registration-notice provider.
2. Realm admin: `KEYCLOAK-REALM.md`.
3. Check each pinned action SHA in `.github/workflows/mockups-image.yml` against the release named beside it (done
   2026-11-23, all six matched), push `mockups-v0.1.0`, and take the digest from the workflow's log.
4. Services-repo PR: copy `deploy/ids3/ptm/dev/` to `ids3/projects/ptm/dev/`, put the digest into `app.yaml`, complete
   the README checklists, merge (ArgoCD syncs at once; with no secret there is nothing to wait for in Vault).
5. Verify (README, "Verify after deploy"). Roll back by reverting the image line; remove by deleting the directory.

## 9. Open questions

1. The backup realm admin (owner to name).
2. ~~Registration-notice provider~~: the platform admin builds a database-view poller instead (services#751, D56).
3. Whether the platform Keycloak can already reach the SMTP relay (verification emails need it).
4. Publishing: may the image workflow move into `.github/workflows/`, and should the GHCR package be public?
5. Whether the MIMIC-IV demo should be hosted at all on dev, and by which delivery route (section 7).

## Follow-ups (not done)

1. Measure memory and CPU on the cluster after the first deploy and adjust `resources`.
2. The registration-notice provider (platform admin, from its specification).
3. Tracing, when wanted (section 5).
4. For the MIMIC-IV demo, if hosted: the delivery route, the attribution footer, the not-disclosure-control note.
5. The storage and backup access review, before any full MIMIC-IV hosting.
6. A Content-Security-Policy header: the pages use inline styles and one inline script, so a policy needs hashes
   or a move to files first.
7. The first real image build and its checks (the image workflow), since none could run here.
8. `--require-hashes` for the lock files (hosting lock, store lock, base lock), from the security review.
9. Load every page in a browser under the Content-Security-Policy once hosted (checked statically here).
10. The MIMIC-IV demo kind, if hosted: its match uses the server's fixed search budget of 40,000 and runs
    in-process; give it the same child process and time limit first.
11. Re-measure the hosted time limit on the cluster's CPU (the 2 s figure and the 6.5 s worst shape are from an
    M4 Pro).
12. Retire or keep the in-browser path (D29 stage 3): unchanged by this plan, which keeps it as the labelled
    fallback the static image and an unreachable server still need.
