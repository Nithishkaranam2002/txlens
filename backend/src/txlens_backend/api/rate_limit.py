"""Per-user rate limits for expensive endpoints (paid LLM calls, CPU-heavy statistics).

A fixed window per (user, bucket), counted in Postgres (`app.rate_limit`) with one atomic
upsert, so every API process shares the same count without Redis. The user's expired windows
for the bucket are deleted in the same transaction, so the table stays tiny.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException, status
from sqlalchemy import Engine, text

from txlens_backend.api.deps import Context, CurrentPrincipal
from txlens_backend.config import Settings


class Bucket(StrEnum):
    """What is being limited. Each bucket has its own allowance (Settings)."""

    COPILOT = "copilot"  # paid LLM calls
    ANALYSIS = "analysis"  # survival statistics, PDF reports, on-demand risk scoring


@dataclass(frozen=True)
class Limit:
    """At most `requests` per `window_seconds`."""

    requests: int
    window_seconds: int


def limit_for(bucket: Bucket, settings: Settings) -> Limit:
    """The configured allowance of a bucket."""
    if bucket is Bucket.COPILOT:
        return Limit(settings.rate_limit_copilot_per_hour, 3600)
    return Limit(settings.rate_limit_analysis_per_minute, 60)


def hit(engine: Engine, user_id: str, bucket: Bucket, limit: Limit) -> int | None:
    """Count one request. None if allowed, else the seconds until the window resets."""
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "WITH win AS (SELECT to_timestamp(floor(extract(epoch FROM now()) / :w) * :w) "
                "AS start) "
                "INSERT INTO app.rate_limit (user_id, bucket, window_start, hits) "
                "SELECT :u, :b, start, 1 FROM win "
                "ON CONFLICT (user_id, bucket, window_start) "
                "DO UPDATE SET hits = app.rate_limit.hits + 1 "
                "RETURNING hits, ceil(extract(epoch FROM window_start + make_interval(secs => :w) "
                "- now()))::int AS retry_after, window_start"
            ),
            {"u": user_id, "b": bucket.value, "w": limit.window_seconds},
        ).one()
        conn.execute(
            text(
                "DELETE FROM app.rate_limit WHERE user_id = :u AND bucket = :b "
                "AND window_start < :start"
            ),
            {"u": user_id, "b": bucket.value, "start": row.window_start},
        )
    return None if row.hits <= limit.requests else max(int(row.retry_after), 1)


def rate_limited(bucket: Bucket) -> Callable[[CurrentPrincipal, Context], None]:
    """A dependency that counts the caller's request and answers 429 over the limit."""

    def check(principal: CurrentPrincipal, ctx: Context) -> None:
        retry_after = hit(ctx.engine, principal.user_id, bucket, limit_for(bucket, ctx.settings))
        if retry_after is not None:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many requests; try again later",
                headers={"Retry-After": str(retry_after)},
            )

    return check
