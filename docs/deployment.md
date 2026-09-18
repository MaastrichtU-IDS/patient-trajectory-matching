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
