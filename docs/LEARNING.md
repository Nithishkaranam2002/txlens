# LEARNING.md

Plain-English notes on what was built, why, and how to explain it in an interview.

## Phase 1, Step 1.1 — Project skeleton

### What was built
- The folder layout for the whole project.
- One **uv workspace** with four Python packages: `backend`, `data_generator`, `pipelines`,
  `evals`. They share one lockfile (`uv.lock`), so every package uses the same library versions.
- Shared quality tools: **ruff** (style and common bugs), **mypy strict** (types), **pytest**.
- A **Next.js** app in `frontend/` with **Vitest** for tests.
- `infra/docker-compose.yml`: Postgres 16 + pgvector, Temporal dev server (with its web UI),
  Langfuse v2, and a Spark runner container.
- A **Makefile** so every common task is one command, and **GitHub Actions** CI that runs
  lint + tests on every push.

### Why these tools
- **uv workspace**: very fast installs, one lockfile, reproducible builds.
- **Spark in Docker**: Spark needs Java 17; the container brings it, so the Mac does not.
  See `docs/adr/0001-spark-in-docker.md`.
- **Temporal dev server**: one small container (SQLite inside) with the UI built in; no separate
  Temporal database or UI container needed locally.
- **Langfuse v2**: only needs Postgres, so it shares our Postgres. See `docs/adr/0002`.

### Trade-offs
- Langfuse v2 is older than v3 but needs far less disk.
- Spark tests do not run in plain `make test` on the Mac (they need Java); they run in the
  container (`make test-pipelines`) and in CI.
- We capped `importlib-metadata` below 10 because its newest release added an unknown,
  unverified dependency — a small example of supply-chain caution.

### Likely interview questions
1. **Why a monorepo with a uv workspace?** Backend, pipelines and evals share code and
   versions. One lockfile means "works on my machine" also works in CI.
2. **Why run Spark in Docker?** Spark needs a specific Java version. The container pins
   Python 3.12 + Java 17 + Spark 3.5, the same on every machine.
3. **How do you keep secrets safe?** Only in `.env`, which git ignores. The repo has
   `.env.example` with placeholders, and settings use `SecretStr` so keys never show in logs.
4. **What does CI check?** Formatting, lint, strict types, Python tests including Spark,
   and frontend lint, types and tests on every push and pull request.
5. **Why pin Docker image versions?** So the stack doesn't change under you when a new image
   is published; upgrades become deliberate, reviewable changes.
