# The store-backed mockups for the ids3 cluster (docs/hosting-plan.md): demos/mockups/server.py with the synthetic
# cohort's execution store, and nothing else. DRAFT: not yet built (no registry access here) and never pushed.
#
# Build from the repository root, which is the context. BuildKit (buildx) reads the allow-list beside this file
# (mockups.Dockerfile.dockerignore) instead of the root .dockerignore, so only the paths listed there reach the build
# (the legacy builder would apply the root .dockerignore instead; the explicit COPY lists below still copy nothing else):
#   docker build -f deploy/ids3/mockups.Dockerfile -t ghcr.io/maastrichtu-ids/patient-trajectory-matching/mockups:<version> .
# Publish by a version tag and deploy by digest, never `latest` (hosting plan, "The image").
#
# What this image does not contain, by construction:
#   - any MIMIC data or MIMIC-derived store: data/local-mimic and verification/ are outside the allow-list, and no
#     PTM_MIMIC_* variable is set, so /api/store/status answers that no store is configured;
#   - any secret: the browser client is public (PKCE), the token check needs only Keycloak's public keys, and the
#     synthetic data need no credential.
#
# The base is pinned by digest (python:3.12-slim-bookworm, resolved from Docker Hub on 2026-11-20). Bump it in a
# reviewed change, like any other pin.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# Stage 1: the synthetic cohort, regenerated from the committed generator (deterministic for its seed) and checked by
# a second run, as demos/mockups/Dockerfile and CI do. It is never copied in from a working tree.
FROM ${PYTHON_IMAGE} AS cohort
WORKDIR /src/demos/mockups
COPY demos/mockups/synthetic/ synthetic/
RUN python synthetic/generate.py && python synthetic/generate.py --check

FROM ${PYTHON_IMAGE}
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1
WORKDIR /opt/ptm

# The mockup server's hosting lock: the store lock (base lock plus duckdb) and the pinned token-check dependencies.
# Binary wheels only, as in the root Dockerfile.
COPY patterns/requirements.lock.txt patterns/requirements-store.lock.txt ./patterns/
COPY demos/mockups/requirements-hosting.lock.txt ./demos/mockups/
RUN python -m pip install --only-binary=:all: -r demos/mockups/requirements-hosting.lock.txt \
    && python -c "import duckdb, jsonschema, jwt, cryptography" \
    && groupadd --gid 10001 ptm \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp --shell /usr/sbin/nologin ptm

# The engine and the contracts it reads (patterns/_resources.py finds schemas/ and ontology/ from the root).
COPY patterns/ ./patterns/
COPY schemas/ ./schemas/
COPY ontology/ ./ontology/
# server.py serves its own directory as static files, so only the pages and the server's modules go there.
COPY demos/mockups/server.py demos/mockups/access.py demos/mockups/datasets.py demos/mockups/store_query.py \
     demos/mockups/store_trajectory.py demos/mockups/store_parity.py demos/mockups/index.html ./demos/mockups/
COPY demos/mockups/shared/ ./demos/mockups/shared/
COPY demos/mockups/patient-first/ ./demos/mockups/patient-first/
COPY demos/mockups/trajectory/ ./demos/mockups/trajectory/
COPY demos/mockups/store-query/ ./demos/mockups/store-query/
COPY demos/mockups/roadmap/ ./demos/mockups/roadmap/
COPY demos/mockups/results/index.html demos/mockups/results/results.json ./demos/mockups/results/
COPY --from=cohort /src/demos/mockups/data/ ./demos/mockups/data/

# The synthetic store is built at startup (about 1.3 s, on a background thread) into PTM_TRAJECTORY_STORE_ROOT, an
# emptyDir in the pod, so the root filesystem can be read-only. /api/health answers from the first second;
# /api/ready answers 200 once every configured dataset is ready.
USER 10001:10001
ENV PTM_TRAJECTORY_STORE_ROOT=/tmp/ptm-store \
    PTM_LOG_FORMAT=json
EXPOSE 8080
# exec form: Python is PID 1 and receives SIGTERM, which closes the server and removes the store's build.
CMD ["python", "demos/mockups/server.py", "--host", "0.0.0.0", "--port", "8080"]
