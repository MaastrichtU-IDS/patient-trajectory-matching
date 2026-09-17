ARG PYTHON_IMAGE=python:3.12-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /opt/trajectory

COPY patterns/requirements.lock.txt patterns/requirements-semantic.lock.txt ./patterns/
RUN python -m pip install --no-cache-dir --only-binary=:all: -r patterns/requirements-semantic.lock.txt \
    && python -c "import rdflib, pyshacl, rustdl" \
    && groupadd --gid 10001 trajectory \
    && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /tmp trajectory

# Explicit runtime inputs; local datasets and verification outputs are not copied.
COPY app/ ./app/
COPY patterns/ ./patterns/
COPY schemas/ ./schemas/
COPY ontology/ ./ontology/
COPY examples/ ./examples/
COPY demo/ ./demo/
COPY ui/ ./ui/
COPY data/terminology/ ./data/terminology/
COPY data/clinical-source-demo-pin.json ./data/clinical-source-demo-pin.json
COPY reference_oracle.py LICENSE ./

USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2).read()"
CMD ["python", "-m", "app.server", "--host", "0.0.0.0", "--port", "8080"]
