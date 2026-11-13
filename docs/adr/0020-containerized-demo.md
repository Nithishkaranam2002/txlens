# ADR 0020: One-command demo in containers, with snapshot and restore

**Status:** accepted (2026-11-13)

## Context
Showing TxLens took a dozen manual steps: Docker for Postgres and Temporal, then `uv` and `pnpm`
on the host for the API, worker and frontend, then vocabulary, generator, Spark pipeline,
users, risk model, surveillance, biopsy and note processing and the literature index. Three of
those steps call a paid LLM. A demo must come up with one command, must never print a secret,
and must not depend on an API key to show what was already paid for.

## Options compared

| | **App in containers + scripts** (chosen) | Host processes started by `make demo` | Small offline seed (like the e2e seed) |
|---|---|---|---|
| Host needs | Docker, `.env`, vocabulary subset | Docker, uv, pnpm, Node, Tesseract | Docker, uv, pnpm |
| Real data (2,000 patients, AI layers) | Yes | Yes | No: scripted LLM, tiny dataset |
| Hardened images to deploy later | Yes (non-root, read-only root) | No | No |
| Restart after a reboot | `make demo` (containers already built) | Several processes to restart | Rebuilds every time |

## Decision
- **Images.** `infra/backend.Dockerfile`: one image for the API, the Temporal worker and one-off
  commands. It is built in two stages (the compiler stays in the build stage), runs as uid
  10001 and has a read-only root filesystem in compose. `infra/frontend.Dockerfile`: Next.js
  standalone server as the `node` user. Base images are pinned by digest.
- **Compose `demo` profile:** `api`, `worker`, `web`, plus a `tools` profile for one-off
  commands. Every port is on 127.0.0.1 and can be overridden, as can the project name, so a
  second stack can run beside the developer one.
- **`make demo`** (`infra/demo/demo.sh`) is idempotent. Each step checks whether its work is
  done (vocabulary loaded? patients loaded? model registered? surveillance run?). It checks the
  secrets by name (`txlens-demo check`) and never reads or echoes a value. It ends with
  `txlens-demo status`: counts, pending AI work, URLs and user names. Passwords are referred to
  by their `.env` name.
- **LLM steps use the cache first.** Without `OPENAI_API_KEY`, `make_extractor` returns an
  offline extractor. Cached answers are still served (the cache is checked before any model
  call). Anything else raises `LlmOfflineError`. Each consumer then marks its item pending:
  - Notes are saved rules-only, so a later run with a key processes them again.
  - Biopsy documents report `pending_llm`, and their workflow ends as failed on purpose, so a
    later batch may start them again. The batch goes on with the remaining documents.
  - The literature CLI reports how many abstracts wait for an embedding.
- **Snapshot and restore.** `make demo-snapshot` writes `demo_snapshot/` (git-ignored):
  - a custom-format `pg_dump`
  - the cluster roles without passwords
  - `mlruns/` and `data/raw/` as tarballs
  - a manifest with checksums, the schema revision, row counts and folder fingerprints

  `make demo-restore` checks all of these, restores the roles and then the database **keeping
  owners**. The copilot views are owned by a non-superuser role so that RLS applies through
  them; `--no-owner` would silently break that. It then migrates forward, sets the login
  password from `.env` and starts the stack. Folders that differ from the snapshot are moved
  aside, never deleted.

## Consequences
- A first `make demo` takes as long as the pipeline (minutes). A restore takes a few minutes,
  most of them spent rebuilding the vocabulary indexes.
- The snapshot holds the licensed OMOP vocabulary: it is local only, like `omop/athena/`.
- MLflow stores absolute artifact paths, so a snapshot restores into the same checkout path.
- Inside the demo network the API trusts `X-Forwarded-For` from the frontend container's
  private range. As ADR 0011 notes, Next.js passes on a client-supplied header, so a local
  client could pick its own per-address login bucket. The per-user-name limit still holds,
  and the ports are on 127.0.0.1 only.
- The Spark and R images keep their bind-mounted checkout. On Linux hosts that checkout must
  be writable by uid 10001; Docker Desktop maps ownership itself.
