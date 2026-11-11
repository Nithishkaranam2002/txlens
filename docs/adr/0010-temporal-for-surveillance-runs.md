# ADR 0010: Temporal for surveillance runs (nightly schedule and manual replays)

**Status:** accepted (2026-11-10)

## Context
Surveillance must run every night for every center. It must also run on demand for any "as
of" date, so the engine can be replayed over history. A run that fails halfway, for example
because the database restarted, must be safe to retry. CLAUDE.md names Temporal for
multi-step jobs; until now it was planned for document processing (Step 2.1).

## Options compared

| | **Temporal workflow + schedule** (chosen) | cron + a script | APScheduler in the API | Celery beat |
|---|---|---|---|---|
| Retries per center, visible history | Yes (UI shows every run) | Hand-written | Hand-written | Yes |
| Survives API restarts | Yes | Yes | No (runs inside the API) | Yes |
| Extra infrastructure | Already in docker-compose | None | None | Redis (not allowed) |
| Same tool as the later document workflows | Yes | No | No | No |

## Decision
- `SurveillanceWorkflow` runs one activity to list the centers, then one `run_center`
  activity per center in parallel. Each activity is retried up to 3 times. A center's run is
  one database transaction, so it is all-or-nothing, and it is idempotent, so a retry is safe.
- `as_of` defaults to `workflow.now()`, the workflow's start time. Temporal replays a workflow
  with that same value, so its history stays deterministic.
- `txlens-worker` runs the worker as the RLS-bound app login. `txlens-surveillance schedule`
  creates or updates the nightly schedule (02:00 UTC). `txlens-surveillance run --as-of
  YYYY-MM-DD` runs any date on demand.
- Tests use Temporal's time-skipping test server. They cover both fake activities and a real
  run against the test database.

## Consequences
- Temporal arrives one step earlier than planned. The same worker will host the document
  workflows in Phase 2.
- The evaluation's day-by-day replay runs the engine in memory, not through Temporal: about
  1,700 patients × 1,500 days through a workflow would be slow and adds nothing. The
  database path is checked against the replay path by a test.
- The workflow test server is downloaded on first use. Without network the tests skip,
  except in CI, where `TXLENS_REQUIRE_DB=1` makes that a failure.
