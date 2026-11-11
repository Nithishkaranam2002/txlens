"""Surveillance as a Temporal workflow: nightly on a schedule, or on demand for any "as of" date.

The workflow lists the centers, then runs the engine for each center in its own activity
(retried on failure). A run is idempotent, so a retry or a re-run of the same date is safe.
Passing an old `as_of` replays the engine over history.
"""

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import Engine

    from txlens_backend.surveillance.run import list_tenants, run_tenant, transplant_configs

TASK_QUEUE = "txlens-surveillance"
NIGHTLY_SCHEDULE_ID = "surveillance-nightly"
RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=10))


@dataclass(frozen=True)
class SurveillanceRequest:
    """What to run: `as_of` as an ISO date; empty means the day the workflow starts."""

    as_of: str | None = None


@dataclass(frozen=True)
class CenterRun:
    """One center's run on one date."""

    tenant_id: str
    as_of: str


@dataclass(frozen=True)
class CenterResult:
    """What a center's run did."""

    tenant_id: str
    as_of: str
    run_id: str
    patients: int
    flags_opened: int
    flags_cleared: int


class SurveillanceActivities:
    """Activities that touch the database, bound to the app engine (RLS always applies)."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @activity.defn(name="list_centers")
    def list_centers(self) -> list[str]:
        """Every center to run."""
        return list_tenants(self.engine)

    @activity.defn(name="run_center")
    def run_center(self, run: CenterRun) -> CenterResult:
        """Run the engine for one center (one transaction)."""
        summary = run_tenant(self.engine, run.tenant_id, date.fromisoformat(run.as_of),
                             transplant_configs())  # fmt: skip
        return CenterResult(run.tenant_id, run.as_of, summary.run_id, summary.patients,
                            summary.flags_opened, summary.flags_cleared)  # fmt: skip


@workflow.defn(name="Surveillance")
class SurveillanceWorkflow:
    """Run surveillance for every center as of one date."""

    @workflow.run
    async def run(self, request: SurveillanceRequest) -> list[CenterResult]:
        """List centers, then run them in parallel."""
        # workflow.now() is replay-safe: a replay of this workflow sees the same date.
        as_of = request.as_of or workflow.now().date().isoformat()
        centers: list[str] = await workflow.execute_activity(
            "list_centers", start_to_close_timeout=timedelta(minutes=1), retry_policy=RETRY,
            result_type=list[str],
        )  # fmt: skip
        runs = [
            workflow.execute_activity(
                "run_center",
                CenterRun(center, as_of),
                result_type=CenterResult,
                start_to_close_timeout=timedelta(minutes=30),
                retry_policy=RETRY,
            )
            for center in centers
        ]
        return list(await asyncio.gather(*runs))
