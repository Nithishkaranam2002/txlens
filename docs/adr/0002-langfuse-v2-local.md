# ADR 0002: Self-host Langfuse v2, sharing the app's Postgres

**Status:** accepted (2026-11-10)

## Context
We need LLM tracing (Langfuse). Disk space on the development machine is limited.
Langfuse v3 needs ClickHouse, Redis and S3-compatible storage in addition to Postgres.

## Decision
Run `langfuse/langfuse:2.95.11` (v2), which only needs Postgres. It uses the same Postgres
container as the app, in a separate database called `langfuse` (created by
`infra/postgres/init-langfuse-db.sh`). It is behind the compose profile `observability`
(`make up-full`) and listens on port 3001 so it does not clash with the Next.js dev server.

## Alternatives
- Langfuse v3 self-hosted: newest features, but ~4 extra containers and several GB of disk.
- Langfuse Cloud free tier: zero disk, but sends traces to an external service.

## Consequences
- v2 no longer gets new features; the tracing SDK API is the same, so moving to v3 or Cloud
  later only changes the `LANGFUSE_HOST` and keys.
- App and Langfuse share one Postgres instance; fine locally, separate them in production.
