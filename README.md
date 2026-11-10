# TxLens

TxLens is a multi-tenant clinical data and AI platform for organ transplant programs
(a personal portfolio project, not affiliated with any company).

- Turns messy transplant data (clinical notes, lab feeds, scanned biopsy PDFs) into
  standardized **OMOP CDM v5.4** data.
- Monitors patients on a post-transplant **dd-cfDNA surveillance** schedule and builds a
  clinician worklist.
- Gives researchers a **cohort and survival-analysis** workbench on de-identified data.
- Offers an **AI copilot**: guarded text-to-SQL over patient data and cited answers over
  transplant literature. The AI extracts and summarizes; it never makes clinical decisions.

> **All data is synthetic**, created by `data_generator/`. No real patient data is used.

**Status:** skeleton, synthetic data generator and OMOP database (Steps 1.1–1.3). Features arrive
step by step.

## Tech stack

| Area | Tools |
|---|---|
| Frontend | Next.js, React, TypeScript, Tailwind, Vitest |
| Backend | Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2 |
| Database | PostgreSQL 16 + pgvector, OMOP CDM v5.4 (official OHDSI DDL), Alembic, Row-Level Security per tenant |
| Workflows | Temporal |
| Data platform | PySpark + Delta Lake (bronze/silver/gold), runs in Docker and on Databricks |
| AI | LiteLLM, Instructor, LangGraph, Langfuse |

## Repo layout

```
backend/         FastAPI app, models, RLS, auth, Temporal workers
data_generator/  synthetic patients, notes, labs, PDFs + ground truth
pipelines/       PySpark medallion jobs + Databricks job configs
omop/            OMOP DDL, vocabulary subset, mappings
evals/           evaluation scripts and reports
r_parity/        R survival script + Python port + parity tests
frontend/        Next.js app
infra/           docker-compose, Dockerfiles
docs/            architecture, ADRs, LEARNING.md
```

## How to run

You need: [uv](https://docs.astral.sh/uv/), [pnpm](https://pnpm.io/), Node 22 and Docker Desktop.
**No Java needed**: Spark runs inside a Docker container.

```bash
make setup          # install dependencies, create .env from .env.example
# edit .env: put your real OPENAI_API_KEY there (never commit it)

make up             # Postgres + Temporal   (Temporal UI: http://localhost:8233)
make up-full        # ...plus Langfuse      (http://localhost:3001)
make lint           # ruff, mypy, eslint, tsc
make test           # pytest + vitest

make db-migrate     # OMOP schemas (cdm, vocab), tenant RLS, indexes
make db-setup       # ...plus the app login role from APP_DATABASE_URL
make vocab-load     # load the Athena vocabulary zip from omop/athena/ (see omop/README.md)
make vocab-check    # check every code we use against the vocabulary

make generate-data        # 2,000 synthetic patients -> data/raw + data/ground_truth (~240 MB)
make generate-data-scale  # 20,000 patients, no PDFs/notes -> data_scale/ (Spark volume tests)

make spark-build    # once: build the Spark image (~1 GB)
make pipeline       # run pipelines inside the Spark container
make test-pipelines # run Spark tests inside the container

pnpm --dir frontend dev   # frontend at http://localhost:3000
make down           # stop containers (data is kept)
```

Run `make help` to see every command.

The OMOP vocabulary is licensed and never committed: download it yourself from Athena as
described in [omop/README.md](omop/README.md). Tests use a fake TEST ONLY fixture instead.
