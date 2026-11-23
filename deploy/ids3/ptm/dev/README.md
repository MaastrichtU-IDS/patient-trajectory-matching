# ptm / dev

The store-backed interface mockups of patient-trajectory-matching: `demos/mockups/server.py` serving the pages, the
trajectory-graph engine and an execution store of the synthetic cohort, with per-dataset access control against the
owner's shared Keycloak realm. Source: <https://github.com/MaastrichtU-IDS/patient-trajectory-matching>
(`docs/hosting-plan.md` is the design). Project `ptm`, env `dev` → namespace **`ptm-dev`** (created by the projects
ApplicationSet).

Kept in the app repo under `deploy/ids3/ptm/dev/` and copied into `ids3/projects/ptm/dev/` by a services-repo PR.

## Phase 1 (owner's decision, 2026-11-23)

**What is deployed now:** the synthetic-only public demo, with no sign-in, no Keycloak and no secret. The image is
public on GHCR.
- **The server.** It runs with no realm (no `PTM_OIDC_*` setting) and serves the public synthetic cohort only. Every
  hosted bound stays on:
  - the rate limits and the per-client match limits at the front door;
  - the body limit and HSTS;
  - the CSP;
  - the child-process matcher with its time limit and search budget;
  - the NetworkPolicies;
  - JSON logs.
- **In the kustomization:** `_base`, `allow-traefik-ingress.yaml`, `configmap.yaml`, `app.yaml`, `middleware.yaml`,
  `ingress.yaml`, `ingress-match.yaml`.

**Kept for phase 2 and not in the kustomization:**
- `allow-keycloak-egress.yaml`: the realm's key fetch, with no realm configured;
- `vault.yaml`: no secret exists;
- `KEYCLOAK-REALM.md`: the realm checklist.

Sign-in is phase 2 (the sections below marked "phase 2"). It needs the realm, the `PTM_OIDC_*` settings in
`configmap.yaml` and those two files listed in the kustomization.

**URL once deployed:** <https://ptm.k8s.semanticscience.org>

**Data on dev: synthetic only** (owner, 2026-11-20). No MIMIC data of any kind is in the image, the ConfigMaps or
any volume.

## Components

- **ptm-mockups** — `ghcr.io/maastrichtu-ids/patient-trajectory-matching/mockups:<version>@sha256:<digest>`
  (built from `deploy/ids3/mockups.Dockerfile`), :8080, one replica, behind the Ingress. Python standard-library
  HTTP server; DuckDB for the store; PyJWT and cryptography for the token check (idle in phase 1). The synthetic store
  is built at start (about 1.3 s) into an `emptyDir`, and the root filesystem is read-only; matches run in a child
  process with a spare (match_worker.py).
- No database, no PVC, no migration, no sidecar.
- **Keycloak** (phase 2) — not deployed here: the shared user realm `ids-apps` on the platform Keycloak, set up by
  `KEYCLOAK-REALM.md`. Not used in phase 1.

## Auth wiring (phase 2)

In phase 1 there is no sign-in: `GET /api/auth-config` answers `{"enabled": false}` and the pages show no sign-in.

The page signs users in with the public client `ptm-mockups` (authorization code + PKCE S256, no secret) and sends
the access token as a bearer token. The server verifies each token itself, offline, against the realm's public keys:
signature (RS256 only), `iss` equal to `PTM_OIDC_ISSUER`, `aud` containing `PTM_OIDC_AUDIENCE`, `exp` (and `nbf`,
`iat` when present), and `typ` = `Bearer` so an ID token cannot stand in. Keys come from `PTM_OIDC_JWKS_URI`, the
in-cluster Keycloak Service, opened by `allow-keycloak-egress.yaml`; the issuer is configured separately because
the browser-facing URL is not the address the pod fetches keys from (the sulo-schema-builder pattern).

Access per dataset (`configmap.yaml`, `ptm-mockups-datasets`):

| Tier | Who | Answers | On dev |
|---|---|---|---|
| public | anyone, no login | per patient (synthetic only) | `synthetic-paediatric-rare` |
| approved | signed in, in group `approved` | aggregate counts only | not configured |
| role | approved and holding e.g. `dataset:mimic-iv:read` | per patient | refused by the server until the access review |

`GET /api/datasets` lists the datasets the caller may use; the pages show only those. A dataset the caller may not
use is a 404, like one that does not exist. `GET /api/auth-config` gives the page the issuer and client id (public
facts).

Bounds (security review of D50): Traefik rate limits (general, and about 1/s for the match routes through
`ingress-match.yaml`), a 256 KiB body limit and HSTS; in the pod, `PTM_MATCH_CONCURRENCY` (a slot is taken before
a body is read), `PTM_MAX_THREADS`, a 30 s socket timeout, and caps on patients, events, steps and relations.
Hosted, the anonymous engine route is off, only page asset types are served, and every response carries a
Content-Security-Policy. Each synthetic-store match runs in a child process killed after `PTM_MATCH_TIME_LIMIT`
(2 s; a spare is kept built; kills are logged as `match_killed`; a match waits at most 3 s in all, then 503 BUSY), searches are clamped to 1,000 attempts per patient, and requests are capped at 6 steps, 8 relations and 6
relaxations. `allow-traefik-ingress.yaml` admits only Traefik to the pod, and the pod runs on a front-door node.
Liveness is a TCP probe; readiness is served from threads reserved for the probes.

## Secrets

**None.** The browser client is public (PKCE), the token check uses public keys, and synthetic data need no
credential, so there is nothing to put in Vault today. `vault.yaml` is ready but not listed in the kustomization; the
Vault steps below are for the first secret (likely candidates: a read credential for a MIMIC-IV demo store, if one is
ever delivered from object storage, or a client secret for the fallback registration-notice poller of
`KEYCLOAK-REALM.md` section 6, if the platform provider is not built).

## Logs and tracing

Logs only: one JSON line per API request on stdout (`PTM_LOG_FORMAT=json`), collected by the k8s-infra daemonset into
SigNoz as service `ptm-mockups`. A line holds the route, status, duration, dataset, auth kind (`anonymous`, `bearer`)
and, for a signed-in caller, the token's `sub` (a pseudonymous Keycloak id), so access to non-public datasets can be
audited. Never a body, a token, a header or an IP address. Tracing is deferred (owner); how to turn it on is in the
hosting plan, section 5.

---

## Owner checklist (project owner)

Phase 1:
- [x] Publish the image: tag `mockups-v0.1.0` in the app repo (the image workflow); the digest is in `app.yaml`, and
      the package is public. Never `latest`.
- [ ] **To deploy a new build:** a services-repo PR that changes only that image line.
- [ ] **After deploy:** verify as below; read memory use in SigNoz after a day and adjust `resources`.

Phase 2 (sign-in; not needed now):
- [ ] Name the backup realm admin for `ids-apps`.
- [ ] Register the OAuth apps at ORCID, GitHub and Google (redirect URIs in `KEYCLOAK-REALM.md`, section 4) and hand
      their client ids and secrets to the realm admin out of band; they go into Keycloak, not Vault, not git.
- [ ] Approve users by adding them to group `approved`. Grant `dataset:mimic-iv:read` only after verifying and
      recording the user's own PhysioNet credentialing (`KEYCLOAK-REALM.md`, section 2) — and only once full
      MIMIC-IV hosting has passed its storage and backup access review.

## Admin checklist — before merging

### Cluster (phase 1)

- [ ] Traefik's `kubernetescrd` provider is on, so the five Middlewares in `middleware.yaml` load (first use in this
      repo). `kubectl -n ptm-dev get middlewares.traefik.io` after sync.
- [ ] `allow-traefik-ingress.yaml`: its front-door addresses are copied from `ids3/platform/minio-data/netpol.yaml`;
      confirm they are current, and that the kubelet's probes still pass after it lands (see its header).
- [ ] `ingress-match.yaml` reuses `ptm-mockups-tls` without a second cert-manager annotation; confirm Traefik serves
      the certificate on the match paths (`curl -sI -X POST https://ptm.k8s.semanticscience.org/api/store/match`).
- [ ] Traefik sees the client's own address (it is the front door since 2026-09-24), so the rate limit is per client.
- [ ] GHCR: the package `patient-trajectory-matching/mockups` is public, so the node pulls it with or without the
      Harbor `ghcr.io` mirror. No `imagePullSecrets` configured.
- [ ] The certificate: `kubectl -n ptm-dev get certificate` → `READY=True` within a minute or two of the first sync.

### Keycloak (phase 2 only; nothing to do for phase 1)

- [ ] Everything in `KEYCLOAK-REALM.md`, sections 1 to 5 (realm, roles and the `approved` group, client
      `ptm-mockups` with the `ptm-api` audience mapper, identity providers, admin OTP).
- [ ] **Platform change (platform admin):** the registration-notice event-listener provider in the platform
      Keycloak, `KEYCLOAK-REALM.md` section 6. Not needed for the first deploy; until it exists the owner checks
      new accounts in the admin console.

### Vault (only when the first secret arrives; nothing to do for phase 1)

UI at <https://vault.k8s.semanticscience.org> (Token login, or OIDC as a `vault-admins` member).

- [ ] **Policy** — Policies → ACL Policies → Create ACL policy, name `ptm-dev`:
      ```hcl
      path "secret/data/ptm/dev" {
        capabilities = ["read"]
      }
      ```
      (the `data/` segment is required for KV v2; without it VSO gets `permission denied`)
- [ ] **Role** — Access → `kubernetes/` → Roles → Create role: name `ptm-dev`; bound service account names
      `default`; bound service account namespaces `ptm-dev`; policies `ptm-dev`; token TTL `1h`.
- [ ] **KV** — Secrets → `secret/` → Create secret, path `ptm/dev`, the keys the owner hands over out of band.
      Do not set `delete_version_after` on the mount. Rotate with `vault kv patch`, never `put`.
- [ ] Then add `vault.yaml` to `kustomization.yaml` and the `secretRef` to `app.yaml` (header of `vault.yaml`), and
      check `kubectl -n ptm-dev get vaultstaticsecret` → `SYNCED=True`.

CLI equivalent:

```bash
kubectl -n vault exec -it vault-0 -- sh -c '
vault policy write ptm-dev - <<EOF
path "secret/data/ptm/dev" { capabilities = ["read"] }
EOF
vault write auth/kubernetes/role/ptm-dev \
  bound_service_account_names=default bound_service_account_namespaces=ptm-dev \
  policies=ptm-dev ttl=1h
vault kv put secret/ptm/dev <KEY>=<value from the owner>
'
```

### Sanity

- [ ] No `latest` or mutable tag; `grep -rnE 'password|secret|BEGIN .*PRIVATE KEY|github_pat_' .` finds no value.

## Verify after deploy

```bash
kubectl -n ptm-dev get pods,svc,ingress,certificate,middlewares.traefik.io
kubectl -n ptm-dev logs deploy/ptm-mockups | head          # startup lines, then one JSON line per API request
curl -s https://ptm.k8s.semanticscience.org/api/health      # {"profile": "trajectory-graph-1.0"}
curl -s https://ptm.k8s.semanticscience.org/api/ready       # {"ready": true}
curl -sI https://ptm.k8s.semanticscience.org/ | grep -i -E 'strict-transport|content-security'   # both present
curl -s -o /dev/null -w '%{http_code}\n' https://ptm.k8s.semanticscience.org/server.py              # 404
curl -s https://ptm.k8s.semanticscience.org/api/datasets    # the synthetic dataset only, caller anonymous
curl -s https://ptm.k8s.semanticscience.org/api/store/status   # no MIMIC store configured
curl -s https://ptm.k8s.semanticscience.org/api/auth-config # phase 1: {"enabled": false}
curl -s https://ptm.k8s.semanticscience.org/api/limits      # hosted, search budget 1000, time limit 2
```

| Symptom | Cause |
|---|---|
| Every bearer request answers `503 AUTH_UNAVAILABLE`, public pages work | `allow-keycloak-egress.yaml` missing, or the JWKS URI names the wrong realm |
| Signed in, but every request `401 TOKEN_INVALID` | issuer mismatch (`PTM_OIDC_ISSUER` vs the token's `iss`), or the `ptm-api` audience mapper missing or not on the access token |
| Signed in and approved, but the page says "awaiting approval" and approved datasets are 404 | `approved` group lacks the `approved` realm role mapping, or the `roles` scope is not on the client |
| Every bearer request `401 TOKEN_INVALID`, though issuer and audience are right | `azp` is not `ptm-mockups` (token from another client), or `sub` missing (the `basic` scope removed) |
| `503 MATCH_TIME_LIMIT` for ordinary page queries | the cluster CPU is much slower than measured; raise `PTM_MATCH_TIME_LIMIT` a little and re-measure |
| Pod stays Pending | no schedulable front-door node (`ids.maastrichtu/front-door=true`), see `app.yaml`'s affinity |
| Pod never Ready, logs show `match_worker_start_failed` | the match worker cannot start; the event's `reason` says why (for example a store build failure, or an exception class under `PTM_MATCH_WORKER_MAX_MIB`) |
| `429` on matches for many people at once | they share one NAT address, and `ptm-match-inflight` allows one match in flight per address; raise its `amount` |
| Many `503 BUSY` | `PTM_MATCH_CONCURRENCY` or `PTM_MAX_THREADS` too low for real use, or a flood the rate limits let through |
| Probes fail after `allow-traefik-ingress.yaml` | the pod runs on a node whose cni0 address is not listed (see the file's header) |
| `invalid_redirect_uri` at Keycloak | client redirect URIs or web origins do not match `https://ptm.k8s.semanticscience.org` exactly |
| Pod never Ready, `/api/ready` 503 | the synthetic store failed its build or content check; see the pod's log |
| Router missing in Traefik, 404 at the host | the Middlewares did not load (kubernetescrd provider off, or a name typo in the annotation) |
| 429 for a normal user | `ptm-ratelimit` too tight, or many users behind one address |
