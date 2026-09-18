# Reproducible local research release

The release candidate supports a single owner, one process, a private durable
SQLite store, retained evidence and repeatable exports. Authored fixtures are the
packaged default. This is a research release, with clinical mapping acceptance,
full-source retrieval evaluation and live-cluster acceptance tracked separately.
Do not use the unauthenticated default chart or Compose example to host private
source data. These new deployment modes deliberately keep the application bound
to loopback. They do not provide shared hosting, TLS ingress or patient-level roles.

## Pin the artifact

Use the tested Git commit and retain the built image digest with the evaluation
report. A local image ID is not the registry manifest digest used by Helm; record
the registry digest after the image is uploaded through your release process.
`patterns/requirements-semantic.lock.txt` pins Python graph/reasoner
dependencies. The default Python base image is a mutable convenience tag; for a
reproducible release, set `PYTHON_IMAGE` to a reviewed digest before building.
For a chart deployment set `image.digest=sha256:...`; the chart prefers it over
`image.tag`. No release tag is implied by the examples below.

```sh
git rev-parse HEAD
docker build --build-arg PYTHON_IMAGE="$PYTHON_IMAGE" \
  --tag patient-trajectory-matching:local .
docker image inspect patient-trajectory-matching:local --format '{{.Id}}'
```

## Linux Docker Compose: private durable local instance

`compose.research.yaml` is a standalone configuration, not an override of
`compose.yaml`. It requires Linux host networking and binds the application to
`127.0.0.1:8080`. Host port 8080 must be unused. Docker Desktop host-networking
support is not assumed. The original `compose.yaml` remains the ephemeral authored
demo. Both run with read-only root filesystems and dropped capabilities.

Prepare a private directory and owner credential with the same Unix identity
that runs the container. Do not add either to Git:

```sh
export PTM_UID="$(id -u)"
export PTM_GID="$(id -g)"
export PTM_STATE_DIR="$HOME/.ptm-research"
export PTM_AUTH_FILE="$HOME/.ptm-owner"
python - <<'PY'
import os, secrets
from pathlib import Path
state = Path(os.environ['PTM_STATE_DIR'])
state.mkdir(mode=0o700, exist_ok=True)
fd = os.open(os.environ['PTM_AUTH_FILE'], os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'w') as stream:
    stream.write('owner:' + secrets.token_urlsafe(48) + '\n')
PY
docker compose --file compose.research.yaml up --build --detach
```

Open `http://127.0.0.1:8080/journey`, use username `owner`, and retrieve the secret
from your private credential file with an editor or password manager. The launcher
copies mounted credentials into a new private regular file in temporary memory;
credentials never enter the image, process arguments or SQLite database.
The durable database lives at `$PTM_STATE_DIR/recorded/recorded-jobs.sqlite3`.
Use `docker compose --file compose.research.yaml restart` to verify history and
exports survive restart. Interrupted jobs require an explicit Resume action.

To enable the packaged pressure, heart-rate and respiratory-rate comparison,
set the optional startup pack before `up`:

```sh
export PTM_CLINICAL_FEATURES=/opt/trajectory/examples/clinical-features/authored-pack.json
export PTM_STATE_DIR="$HOME/.ptm-clinical-research"
mkdir -m 700 "$PTM_STATE_DIR"
docker compose --file compose.research.yaml up --detach
```

The path is inside the container and must be absolute. The built-in pack is
explicitly authored and technically reviewed; clinical acceptance remains pending.
Leave the variable unset for the original pressure-only mode. A feature pack is
part of the durable configuration namespace: choose this option before the first
run, or use a fresh private state directory when switching modes. HTTP requests
cannot select a filesystem pack path.

## Kubernetes: one owner through port forwarding

This is a concrete local research mode for a cluster, accessed through an
operator's authenticated Kubernetes connection. It creates no Service and rejects
Ingress. The application binds only to pod loopback; exec probes check local
health. Use one replica with `Recreate` and a ReadWriteOnce PVC. Do not scale the
SQLite writer or expose this mode through a separately created Service/proxy.

```sh
kubectl create secret generic ptm-owner --from-file=owner="$PTM_AUTH_FILE"
helm upgrade --install research-prototype deploy/helm/patient-trajectory \
  --set image.repository=YOUR_IMAGE_REPOSITORY \
  --set image.digest=sha256:YOUR_TESTED_IMAGE_DIGEST \
  --set localResearch.enabled=true \
  --set localResearch.ownerSecret=ptm-owner \
  --set localResearch.persistence.size=1Gi
kubectl port-forward --address 127.0.0.1 deployment/research-prototype-trajectory 8080:8080
```

Check the actual Deployment name with `kubectl get deployment` if a name override
is used. Access the same local URL and owner login as for Compose. The Secret key
must be named `owner`. The pod's non-root UID/GID is 10001; the volume driver must
honor `fsGroup: 10001`. Secret projection is read-only and group-readable solely
inside the pod, then copied into a mode-0600 regular file owned by the process.
A mode-0700 `recorded` child directory satisfies the store's owner-only checks.
An incompatible CSI permission policy fails startup; verify this with the intended
storage class instead of loosening the application's access checks.

For the same built-in distinct-variable example, add
`--set localResearch.clinicalFeaturesPath=/opt/trajectory/examples/clinical-features/authored-pack.json`
to the Helm command. This defaults to empty and follows the same fresh-state
requirement when changing feature configuration.

For an existing restored PVC use
`--set localResearch.persistence.existingClaim=YOUR_CLAIM`. Chart-created PVCs
have Helm's `keep` annotation to avoid automatic evidence deletion on uninstall;
operators own retention and eventual cleanup. Existing claims are never created
or removed by this chart. Kubernetes Secret encryption at rest, RBAC and storage
snapshots are cluster responsibilities. Rotate the Secret and restart the pod to
load a new credential.

## Backup and restore

`deploy.state_archive` uses SQLite's online backup API and verifies the copied
SQLite structure. It accepts an owner-private database and destination directory,
creates mode-0600 copies without overwriting, and restores only into a new
mode-0700 state directory. It does not copy credentials or lock files. Store backups
with the same data restrictions as source evidence; checksums are not encryption.

```sh
mkdir -m 700 "$HOME/.ptm-backups"
python -m deploy.state_archive backup \
  --state-dir "$PTM_STATE_DIR/recorded" \
  --output "$HOME/.ptm-backups/recorded-001.sqlite3"
docker compose --file compose.research.yaml down
mkdir -m 700 "$HOME/.ptm-restored"
python -m deploy.state_archive restore \
  --backup "$HOME/.ptm-backups/recorded-001.sqlite3" \
  --state-dir "$HOME/.ptm-restored/recorded"
export PTM_STATE_DIR="$HOME/.ptm-restored"
docker compose --file compose.research.yaml up --detach
```

The restored server must use the same application version and source configuration
namespace. Startup verifies the namespace; reading retained records verifies
payload integrity. Check history and compare an exported report byte-for-byte with
its original before treating recovery as accepted. Restore reopens completed
historical evidence; fresh replay additionally requires the admitted source files.
The packaged local modes currently use authored defaults; mounting a configured
clinical source needs a separate reviewed deployment configuration.

On Kubernetes, first scale the Deployment to zero and wait for termination. Mount
the PVC in an operator-controlled maintenance pod with the same UID/GID, use the
same SQLite backup utility included in the tested application image, and restore to a new PVC before selecting
`existingClaim`. Do not run an independent application writer against the original
PVC during restore. A live CSI snapshot/backup procedure is storage-provider
specific and must be rehearsed in the intended cluster.

## Acceptance gates

The integrated research CI builds the image and executes
`python -m deploy.container_acceptance --image patient-trajectory-matching:test`.
It verifies missing/wrong owner credentials are rejected, runs an authored recorded
query and revised pattern with a weighted pressure/heart-rate/respiratory-rate
comparison and export, restarts the container, and restores
a private backup into a different bind mount. Job and export equality are checked
after both restart and restore. CI also lints/renders both Helm modes, rejects
multiple replicas, missing private-mode credentials and private-mode Ingress, and
validates both Compose configurations.

Local unit checks are `python -m unittest tools.test_release_packaging`; these cover
credential projection, private consistent live backups, restoration and refusal
to overwrite. Docker, Helm and kubectl were unavailable in the implementation
workspace, so authored Python tests are local evidence; container and cluster checks
must be green in CI before tagging.

The same CI job also runs `python -m deploy.kubernetes_acceptance` against a newly
created, randomly named **disposable kind cluster**. It installs this Helm chart with
an owner Secret, PVC and built-in authored clinical pack; verifies owner access,
actual three-variable comparison and export; replaces the pod; and verifies the
same PVC, retained job, comparison and identical export bytes. It asserts that no
Service or Ingress was created. The helper uses a separate private kubeconfig and
explicit context on every command, refuses an existing cluster name, and deletes
only its own namespace/cluster in cleanup. It needs no deployment credentials and
accepts no existing cluster or clinical data configuration.

Tool setup follows the [official Helm kind action](https://github.com/helm/kind-action)
and [kind quick-start instructions](https://kind.sigs.k8s.io/docs/user/quick-start/).
CI pins kind v0.33.0, kubectl v1.37.0 and the Kubernetes node image digest from the
[official kind release](https://github.com/kubernetes-sigs/kind/releases/tag/v0.33.0).
The aggregate `kubernetes-acceptance.json` artifact is written only after checks
and cleanup succeed. A green run establishes the behavior of this authored kind
environment; it does not establish acceptance of the institution's cluster.
Its storage class, RBAC, credential rotation and operational recovery still need
an intended-environment rehearsal.
Only publish a version tag after the container/Helm CI and these intended-environment
checks pass, with clinical limitations stated in the release notes.
