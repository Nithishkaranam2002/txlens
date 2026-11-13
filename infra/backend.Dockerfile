# API and Temporal worker image (one image, two commands). Non-root, no compilers or uv at
# runtime. Data, documents and the MLflow store are mounted by docker compose, never baked in.
FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df AS build

# A compiler for dependencies without a wheel for every platform (pysimstring, via medspaCy).
# Build stage only: the runtime image has no compiler.
RUN apt-get update \
    && apt-get install -y --no-install-recommends g++ \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.7.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/
COPY data_generator/pyproject.toml data_generator/
COPY pipelines/pyproject.toml pipelines/
COPY evals/pyproject.toml evals/
COPY omop/pyproject.toml omop/
# Third-party dependencies first (cached layer), then the workspace packages themselves.
RUN uv sync --frozen --no-dev --package txlens-backend --no-install-workspace
COPY backend backend
COPY data_generator data_generator
COPY pipelines pipelines
COPY omop omop
RUN uv sync --frozen --no-dev --package txlens-backend --no-editable

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# Tesseract for scanned biopsy reports; libgomp for LightGBM.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --create-home txlens

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
# Alembic migrations and the surveillance configs are read from the source tree.
COPY backend/alembic.ini backend/alembic.ini
COPY backend/migrations backend/migrations
COPY infra/container-env.sh /usr/local/bin/container-env
ENV PATH=/app/.venv/bin:$PATH PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

USER 10001
ENTRYPOINT ["container-env"]
# --no-proxy-headers: the app trusts X-Forwarded-For only from TRUSTED_PROXY_IPS itself.
CMD ["uvicorn", "txlens_backend.api.app:create_app", "--factory", "--host", "0.0.0.0", \
     "--port", "8000", "--no-proxy-headers"]
