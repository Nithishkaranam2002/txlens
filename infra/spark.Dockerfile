# Spark runner: Python 3.12 + Java 17 + PySpark + Delta Lake, so the host needs no Java.
# Bookworm (not trixie) because it ships Java 17, which Spark 3.5 supports.
FROM python:3.12-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends openjdk-17-jre-headless procps \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.7.22 /uv /usr/local/bin/uv

WORKDIR /build
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/
COPY data_generator/pyproject.toml data_generator/
COPY pipelines/pyproject.toml pipelines/
COPY evals/pyproject.toml evals/
COPY omop/pyproject.toml omop/

# Install every workspace package's third-party dependencies (locked versions) plus pytest:
# the pipeline tests generate small datasets and check gold against the pinned OMOP DDL.
RUN uv export --frozen --all-packages --all-extras --no-emit-workspace \
        --no-dev --no-hashes -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt "pytest>=8.4" \
    && rm -rf /build

# Download the Delta Lake jars at build time (into ~/.ivy2), not on every run.
COPY pipelines/src /tmp/pipelines-src
RUN PYTHONPATH=/tmp/pipelines-src python -c \
        "from txlens_pipelines.spark import build_spark_session; build_spark_session().stop()" \
    && rm -rf /tmp/pipelines-src

# Source code is mounted at /app by docker compose, so code edits need no rebuild.
WORKDIR /app
ENV PYTHONPATH=/app/pipelines/src:/app/omop/src:/app/data_generator/src:/app/evals/src:/app/data_generator/tests:/app/backend/src:/app/backend/tests:/app/pipelines/tests \
    PYTHONDONTWRITEBYTECODE=1 \
    PYSPARK_PYTHON=python
