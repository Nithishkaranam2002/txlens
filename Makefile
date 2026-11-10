# TxLens developer commands. Run `make help` to list them.
COMPOSE := docker compose -f infra/docker-compose.yml --env-file .env
PY_SRC := backend data_generator pipelines evals omop
SPARK_RUN := $(COMPOSE) --profile spark run --rm spark
SEED ?= 42
N_PATIENTS ?= 2000

.PHONY: help setup up up-full down lint test generate-data generate-data-scale pipeline test-pipelines spark-build

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
	uv run mypy $(PY_SRC)
	pnpm --dir frontend lint
	pnpm --dir frontend typecheck

test: ## Python tests (Spark tests are skipped here) and frontend tests
	uv run pytest
	pnpm --dir frontend test

generate-data: ## Generate synthetic data into data/ (override: SEED=7 N_PATIENTS=500)
	uv run txlens-generate --seed $(SEED) --n-patients $(N_PATIENTS) --out data

generate-data-scale: ## 20,000 patients, no PDFs or note text, into data_scale/ (Spark volume tests)
	uv run txlens-generate --seed $(SEED) --n-patients 20000 --out data_scale --no-pdfs --no-note-text

spark-build: ## Build the Spark runner image (Python 3.12 + Java 17 + PySpark + Delta)
	$(COMPOSE) --profile spark build spark

pipeline: ## Run the medallion pipelines inside the Spark container
	$(SPARK_RUN) python -m txlens_pipelines

test-pipelines: ## Run the Spark tests inside the Spark container
	$(SPARK_RUN) pytest -p no:cacheprovider -m spark pipelines/tests
