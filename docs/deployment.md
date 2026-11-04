# Deploy the authored research prototype

The container runs the research application (`python -m app.server`) with authored
examples. Its API, matching features and limitations are described in the research
application documentation. Deployment does not authorize clinical use or admit
locally supplied patient records. The default container has in-memory state. The local server also supports optional
single-owner authentication and durable recorded jobs, described in
[the durable workspace guide](durable-recorded-workspace.md). These options do not
provide shared or distributed state. Keep access local or within an access-controlled
research environment. The Compose and Helm examples below retain their default
in-memory configuration.

The image uses CPython 3.12 and the existing pinned
`patterns/requirements-semantic.lock.txt`, including rustDL. No Java runtime is
installed. Package installation requires binary wheels and fails if the chosen
platform lacks a wheel. The runtime image includes the repository's authored
examples, schemas and ontology inputs. Local MIMIC files, Git metadata and
verification outputs are excluded from the build context.

## Local Docker Compose

From a clean checkout of the reviewed commit, with Docker and Compose installed:

```sh
docker compose config --quiet
docker compose build
docker compose up --detach --wait --wait-timeout 120
python deploy/smoke.py --url http://127.0.0.1:8080
```

Open [the research application](http://127.0.0.1:8080). Compose binds only to
`127.0.0.1` by default. Set `PTM_PORT=8081` when port 8080 is already occupied, and
use the matching URL for the smoke check. Stop and remove the container with:

```sh
docker compose down
```

The process runs as UID/GID 10001 with a read-only root filesystem, dropped Linux
capabilities and no privilege escalation. `/tmp` is a bounded writable memory
filesystem. Compose limits the service to one CPU, 1 GiB of memory and 128
processes. These are starting limits for authored examples, not a measured
clinical-scale capacity recommendation. Restarting discards in-memory state.

For a reproducible image, record the source commit, image digest and platform.
The default `python:3.12-slim-bookworm` tag can receive updates. Pin an approved
base-image digest before an evaluation run using the `PYTHON_IMAGE` build argument:

```sh
docker build --build-arg PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:REPLACE_WITH_APPROVED_DIGEST -t patient-trajectory-matching:reviewed .
docker image inspect patient-trajectory-matching:reviewed --format '{{.Id}}'
git rev-parse HEAD
```

The placeholder digest must be replaced; it is not executable as written. Python
package versions are pinned, but the existing lock files do not contain wheel
hashes. Retain the built image by digest for repeated evaluation.

## Kubernetes with Helm

The chart lives at `deploy/helm/patient-trajectory`. First build the reviewed image
and make it available to the target cluster's container runtime, either by loading
it into a local cluster or through your authorized image registry. Validate the
chart before installing it:

```sh
helm lint deploy/helm/patient-trajectory
helm template research deploy/helm/patient-trajectory --namespace trajectory-research > /tmp/trajectory-manifests.yaml
kubectl apply --dry-run=server --namespace trajectory-research -f /tmp/trajectory-manifests.yaml
```

Server-side dry-run requires an existing namespace and cluster access. An operator
can then install in the intended research namespace, supplying their image:

```sh
helm upgrade --install research deploy/helm/patient-trajectory --namespace trajectory-research --create-namespace --set image.repository=YOUR_REGISTRY/patient-trajectory-matching --set image.digest=sha256:YOUR_IMAGE_DIGEST --wait --timeout 120s
kubectl --namespace trajectory-research port-forward service/research-trajectory 8080:8080
```

Run the smoke command from another terminal. The default service is `ClusterIP`;
there is no external load balancer and ingress is disabled. A cluster's own network
policies determine which other workloads can access the service. Ingress fields
support an operator's existing ingress controller and TLS configuration, but this
chart does not provide authentication, certificates or a public deployment.

The values schema requires **one replica**, and the deployment uses the `Recreate`
strategy because the application does not synchronize in-memory state. This is not
distributed persistence or a high-availability deployment. Configure CPU/memory
requests and limits in a values file for your measured workload. The Pod runs as a
non-root user, uses a read-only root filesystem and a 64 MiB memory-backed `/tmp`,
drops all capabilities, disables service-account token mounting, and requests the
runtime's default seccomp profile.

`/healthz` is a lightweight liveness endpoint. `/readyz` indicates application
readiness; it does not certify clinical mappings or dataset validity. Startup,
liveness and readiness probes keep these concerns separate. Remove an installation
with `helm uninstall research --namespace trajectory-research`.

## Cloud Run with an owner password

The hosted demonstration runs the same image on Cloud Run, protected by the workspace's own single-owner login (username `owner`, a random secret of 32 to 256 characters) instead of Google sign-in.

The workspace refuses owner authentication on anything but a loopback address, so its Basic credential never crosses a network in the clear. Cloud Run terminates TLS at Google's front end and delivers requests to `0.0.0.0:$PORT`. [`deploy/cloud_run.py`](../deploy/cloud_run.py) keeps the workspace on `127.0.0.1` and relays the platform's connections to it byte for byte; it parses no HTTP, so authentication, routing and errors stay with the workspace. The secret is mounted from Secret Manager and copied into an owner-private file before the workspace starts, as in the local release. `/healthz` and `/readyz` stay open for the platform; every other route needs the login.

What this trades away, compared with IAP:

- one shared credential: anyone who has it is the owner, and revoking one person means rotating it for everyone
- no record of who signed in, only that the owner did
- the URL is public; only the secret stands between it and the workspace
- state lives on the instance's in-memory filesystem and ends with it

Deploy with exactly one instance. The workspace is single-owner and single-process, and keeps a session's revisions in that process; several instances would spread one session across processes that do not share it.

Create the secret without printing it, keep a private local copy for yourself, and let the service read it:

```sh
umask 077
python3 -c "import secrets; print('owner:' + secrets.token_urlsafe(48))" > ~/.ptm-demo-owner
gcloud secrets create ptm-demo-owner --data-file="$HOME/.ptm-demo-owner" --project dumontier-charm
gcloud secrets add-iam-policy-binding ptm-demo-owner --project dumontier-charm \
  --member="serviceAccount:$(gcloud run services describe ptm-demo --region us-central1 --project dumontier-charm --format='value(spec.template.spec.serviceAccountName)')" \
  --role=roles/secretmanager.secretAccessor
```

Deploy from the repository root:

```sh
gcloud run deploy ptm-demo --source . --region us-central1 --project dumontier-charm \
  --command python --args=-m,deploy.cloud_run \
  --set-secrets=/run/secrets/ptm-owner/owner=ptm-demo-owner:latest \
  --max-instances 1 --no-iap --no-invoker-iam-check
```

`--no-invoker-iam-check` makes the service reachable without granting `allUsers` the invoker role. `--allow-unauthenticated` would grant it, and this organisation's domain-restricted sharing policy (`iam.allowedPolicyMemberDomains`) rejects `allUsers` bindings.

Sign in with username `owner` and the part of `~/.ptm-demo-owner` after `owner:`. To rotate, write a new file the same way, add it with `gcloud secrets versions add ptm-demo-owner --data-file=...`, and redeploy so a new instance reads it.

## Validation gates and evidence limits

Before accepting a deployment, run the application and engine tests documented in
the repository, build the image, then run `deploy/smoke.py` against the container
under the read-only settings above. The smoke check verifies HTTP success and
response types for health, readiness, capabilities and the page. Matching
correctness is established by the application/engine tests; this smoke check alone
does not establish it.

For Kubernetes, additionally run `helm lint`, render both the default chart and any
custom values, use server-side dry-run against the intended cluster, and verify
readiness after installation. A Docker build does not validate Kubernetes resource
admission, and a Helm render does not demonstrate a running Pod.

During authoring, Docker and Helm were unavailable in the local workspace. Local
structural checks therefore did not execute an image build, Compose startup, Helm
render/lint or Kubernetes deployment. The four HTTP smoke checks did pass against
the application running directly as a local Python subprocess. The repository's
CI and an operator's target environment must provide the container and cluster
runtime gates; inspect their actual results before claiming deployment validation.
