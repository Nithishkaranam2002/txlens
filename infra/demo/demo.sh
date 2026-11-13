#!/usr/bin/env bash
# `make demo`: bring up the whole stack with demo data loaded, then print the URLs and users.
#
# Idempotent: every step checks whether its work is already done, so a second run only starts
# the containers. Everything runs in Docker (the app images, the Spark image), so the host needs
# Docker, `.env` and the vocabulary subset (omop/vocab_subset, built once from the licensed
# Athena download). LLM steps use the answer cache first; without OPENAI_API_KEY, anything not
# cached is skipped and reported as pending. Secrets are checked by name, never printed.
#
# Offline (no paid calls even with a key in .env): DEMO_OPENAI_API_KEY= make demo
# Overridable: TXLENS_PROJECT (compose project), DEMO_WEB_PORT, DEMO_API_PORT, POSTGRES_PORT,
# TEMPORAL_PORT, TEMPORAL_UI_PORT, LLM_MAX_COST (dollars for the literature embeddings).
set -euo pipefail
cd "$(dirname "$0")/../.."
source infra/demo/common.sh

preflight
if [[ ! -f omop/vocab_subset/CONCEPT.csv ]]; then
  fail "omop/vocab_subset/ is missing. Download the vocabulary from Athena into omop/athena/
       (see omop/README.md), then run: make vocab-subset"
fi

step "Building images and starting Postgres and Temporal"
compose --profile demo --profile spark build
compose up -d --wait postgres temporal
tools txlens-demo check || fail "fix the .env values above, then run make demo again"

step "Database: migrations, app login role, tenants"
tools alembic -c backend/alembic.ini upgrade head
tools txlens-db-setup

if [[ "$(sql "SELECT count(*) FROM vocab.concept")" == "0" ]]; then
  step "Vocabulary subset"
  tools txlens-vocab load --source omop/vocab_subset
fi

if [[ ! -f data/raw/extract.json ]]; then
  step "Generating synthetic patients (2,000, seed 42)"
  tools txlens-generate --seed 42 --n-patients 2000 --out data
fi
AS_OF=$(python3 -c "import json; print(json.load(open('data/raw/extract.json'))['extract_date'])")

if [[ "$(sql "SELECT count(*) FROM cdm.person")" == "0" ]]; then
  step "Medallion pipeline: bronze -> silver -> gold (OMOP) -> Postgres"
  compose --profile spark run --rm spark python -m txlens_pipelines
fi

step "Demo users (passwords from .env)"
tools txlens-seed-users

if ! tools txlens-demo has-model; then
  step "Training the rejection-risk model (MLflow registry in mlruns/)"
  tools txlens-risk train --as-of "$AS_OF"
fi

step "Starting the API, worker and frontend"
compose --profile demo up -d --wait api worker web

if [[ "$(sql "SELECT count(*) FROM app.surveillance_run")" == "0" ]]; then
  step "Surveillance run and nightly risk scoring as of $AS_OF"
  api txlens-surveillance run --as-of "$AS_OF"
fi

step "Biopsy reports (cached answers first; the rest needs OPENAI_API_KEY)"
api txlens-biopsies process /data/raw/pathology || warn "biopsy batch did not finish"

step "Clinical notes (rules, plus the LLM for leftover sentences when cached or keyed)"
api txlens-notes process || warn "notes batch did not finish"

if [[ -f data/literature/abstracts.jsonl ]]; then
  step "Literature index for the copilot"
  tools txlens-literature index --source data/literature/abstracts.jsonl \
    --max-cost "${LLM_MAX_COST:-0.10}" || warn "literature index did not finish"
fi

summary
