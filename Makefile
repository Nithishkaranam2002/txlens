# TxLens developer commands. Run `make help` to list them.
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
PY_SRC := backend data_generator pipelines evals omop
SPARK_RUN := $(COMPOSE) --profile spark run --rm spark
SEED ?= 42
VOCAB_SOURCE ?= omop/athena
N_PATIENTS ?= 2000

.PHONY: help setup up up-full down lint test db-migrate db-setup vocab-load vocab-subset vocab-check generate-data generate-data-scale pipeline pipeline-clean pipeline-report databricks-data test-pipelines spark-build

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

vocab-load: ## Load OMOP vocabularies from omop/athena (or VOCAB_SOURCE=omop/vocab_subset)
	uv run txlens-vocab load --source $(VOCAB_SOURCE)

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
