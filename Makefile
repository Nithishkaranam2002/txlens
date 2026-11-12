# TxLens developer commands. Run `make help` to list them.
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
PY_SRC := backend data_generator pipelines evals omop
SPARK_RUN := $(COMPOSE) --profile spark run --rm spark
SEED ?= 42
VOCAB_SOURCE ?= omop/athena
N_PATIENTS ?= 2000

.PHONY: help setup up up-full down lint test db-migrate db-setup seed-users api openapi frontend e2e screenshots worker surveillance-schedule surveillance-run prune-login-attempts notes-report review-report rejection-report cohort-report surveillance-report biopsy-report ocr-report vocab-load vocab-export-curation vocab-load-local vocab-subset vocab-check generate-data generate-data-scale pipeline pipeline-clean pipeline-report databricks-data test-pipelines spark-build

help: ## List available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-20s %s\n", $$1, $$2}'

setup: ## Install Python + frontend dependencies and create .env from the example
	@test -f .env || (cp .env.example .env && echo "Created .env - add your real OPENAI_API_KEY there.")
	uv sync
	pnpm --dir frontend install

up: ## Start Postgres + Temporal (UI: http://localhost:8233)
	$(COMPOSE) up -d

up-full: ## Start everything above plus Langfuse (http://localhost:3001)
	$(COMPOSE) --profile observability up -d

down: ## Stop all containers (data volumes are kept)
	$(COMPOSE) --profile observability --profile spark down

lint: ## Ruff, mypy, ESLint and TypeScript checks
	uv run ruff check .
	uv run ruff format --check .
	@# One mypy run per package: each tests/ folder has its own conftest.py.
	for pkg in $(PY_SRC); do uv run mypy $$pkg || exit 1; done
	pnpm --dir frontend lint
	pnpm --dir frontend typecheck

test: ## Python tests (Spark tests are skipped here) and frontend tests
	uv run pytest
	pnpm --dir frontend test

db-migrate: ## Apply database migrations (OMOP schemas, tenant RLS, indexes)
	uv run alembic -c backend/alembic.ini upgrade head

db-setup: db-migrate ## Migrate, then create the app login role from APP_DATABASE_URL
	uv run txlens-db-setup

seed-users: ## Create/update one demo user per role per center (passwords from .env)
	uv run txlens-seed-users

api: ## Run the API with auto-reload (docs: http://localhost:8000/docs)
	@# --no-proxy-headers: the app itself trusts X-Forwarded-For only from TRUSTED_PROXY_IPS.
	uv run uvicorn txlens_backend.api.app:create_app --factory --reload --port 8000 --no-proxy-headers

frontend: ## Run the frontend dev server on http://localhost:3000 (proxies /api to `make api`)
	pnpm --dir frontend dev

e2e: ## Playwright end-to-end tests (seeds a throwaway txlens_e2e database; needs `make up`)
	pnpm --dir frontend e2e

screenshots: ## README screenshots of the main pages (needs `make api` and `make frontend` running)
	pnpm --dir frontend screenshots

openapi: ## Export the API's OpenAPI schema and regenerate the typed frontend client
	uv run txlens-export-openapi > frontend/openapi.json
	pnpm --dir frontend gen:api

worker: ## Run the Temporal worker that executes surveillance runs (needs `make up`)
	uv run txlens-worker

surveillance-schedule: ## Create/update the nightly surveillance schedule (02:00 UTC)
	uv run txlens-surveillance schedule

# The demo runs "as of" the synthetic data's extract date (data cutoff), so the worklist shows
# the latest results; `make surveillance-run AS_OF=YYYY-MM-DD` replays any other date.
EXTRACT_DATE = $(shell python3 -c "import json; print(json.load(open('data/raw/extract.json'))['extract_date'])" 2>/dev/null)
AS_OF ?= $(EXTRACT_DATE)

surveillance-run: ## Run surveillance for every center as of the data extract date (AS_OF=... to override)
	uv run txlens-surveillance run $(if $(AS_OF),--as-of $(AS_OF))

prune-login-attempts: ## Delete login attempts older than 30 days (logins also do this)
	uv run txlens-prune-login-attempts

vocab-load: ## Load OMOP vocabularies from omop/athena (or VOCAB_SOURCE=omop/vocab_subset)
	uv run txlens-vocab load --source $(VOCAB_SOURCE)

vocab-export-curation: ## Write review-queue mapping decisions into SOURCE_TO_CONCEPT_MAP.csv
	uv run txlens-vocab export-curation

vocab-load-local: ## Reload only our local concepts and reviewed mappings (after editing them)
	uv run txlens-vocab load-local

vocab-subset: ## Extract a small local vocabulary subset into omop/vocab_subset/
	uv run txlens-vocab subset --source omop/athena --out omop/vocab_subset

vocab-check: ## Check every code we use against the loaded vocabulary
	uv run txlens-vocab check

generate-data: ## Generate synthetic data into data/ (override: SEED=7 N_PATIENTS=500)
	uv run txlens-generate --seed $(SEED) --n-patients $(N_PATIENTS) --out data

generate-data-scale: ## 20,000 patients, no PDFs or note text, into data_scale/ (Spark volume tests)
	uv run txlens-generate --seed $(SEED) --n-patients 20000 --out data_scale --no-pdfs --no-note-text

spark-build: ## Build the Spark runner image (Python 3.12 + Java 17 + PySpark + Delta)
	$(COMPOSE) --profile spark build spark

pipeline: ## Run bronze -> silver -> gold -> Postgres load in the Spark container (LAYERS=...)
	$(SPARK_RUN) python -m txlens_pipelines $(if $(LAYERS),--layers $(LAYERS))

notes-report: ## Note NLP vs ground truth -> evals/reports/notes_nlp.md (ARGS="--llm --max-cost 1")
	uv run python -m txlens_evals.notes_nlp $(ARGS)

review-report: ## Reviewer vs model agreement + eval set -> evals/reports/review_agreement.md
	uv run python -m txlens_evals.review_feedback

rejection-report: ## Rejection records vs ground-truth episodes -> evals/reports/rejection_records.md
	uv run python -m txlens_evals.rejection_records

cohort-report: ## Cohort rules on the loaded database vs ground truth -> evals/reports/cohort_rules.md
	uv run python -m txlens_evals.cohort_reconciliation

surveillance-report: ## Replay surveillance day by day vs ground truth -> evals/reports/surveillance.md
	uv run python -m txlens_evals.surveillance_replay

ocr-report: ## OCR setups on the biopsy PDFs vs ground truth (no LLM) -> evals/reports/ocr.md
	uv run python -m txlens_evals.ocr_benchmark

biopsy-report: ## Biopsy PDFs through OCR + LLM extraction vs ground truth -> evals/reports/biopsy_extraction.md (paid LLM calls, cached)
	uv run python -m txlens_evals.biopsy_extraction

pipeline-report: ## Reconcile gold/Postgres with data/ground_truth -> evals/reports/data_quality.md
	$(SPARK_RUN) python -m txlens_evals.pipeline_reconciliation

databricks-data: ## Zip data/raw, data/ground_truth and the vocab subset for a Databricks upload
	rm -f dist/txlens_databricks_data.zip && mkdir -p dist
	cd data && zip -qr ../dist/txlens_databricks_data.zip raw ground_truth
	cd omop && zip -qr ../dist/txlens_databricks_data.zip vocab_subset
	@ls -lh dist/txlens_databricks_data.zip

pipeline-clean: ## Delete the local Delta lake (bronze/silver/gold); Postgres is untouched
	rm -rf lake

test-pipelines: ## Run the Spark tests inside the Spark container
	$(SPARK_RUN) pytest -p no:cacheprovider -m spark pipelines/tests
