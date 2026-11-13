"""Surveillance as a Temporal workflow: nightly on a schedule, or on demand for any "as of" date.

The workflow lists the centers, then runs the engine for each center in its own activity
(retried on failure). A run is idempotent, so a retry or a re-run of the same date is safe.
Passing an old `as_of` replays the engine over history. With a data cut-off configured, a date
after it is refused here too (non-retryable), whoever started the run.

After the rule engine, each center's patients are scored by the rejection-risk model (each
score stored with the model version). Scoring never blocks surveillance: with no registered
model it is skipped, and a scoring failure is logged and leaves the flags in place.
"""

import asyncio
import dataclasses
from dataclasses import dataclass
from datetime import date, timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError

with workflow.unsafe.imports_passed_through():
    from sqlalchemy import Engine

    from txlens_backend.surveillance.cutoff import AfterCutoffError, check_as_of
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
    risk_scored: int = 0
    risk_model_version: str = ""  # empty: no model registered, or scoring failed


@dataclass(frozen=True)
class RiskResult:
    """What a center's risk scoring did."""

    scored: int
    model_version: str


class SurveillanceActivities:
    """Activities that touch the database, bound to the app engine (RLS always applies).
    `tracking_uri` is the MLflow registry of the risk model (None: no risk scoring);
    `data_cutoff` is the last day of the data (None: any date may run)."""

    def __init__(
        self, engine: Engine, tracking_uri: str | None = None, data_cutoff: date | None = None
    ) -> None:
        self.engine = engine
        self.tracking_uri = tracking_uri
        self.data_cutoff = data_cutoff

    @activity.defn(name="list_centers")
    def list_centers(self) -> list[str]:
        """Every center to run."""
        return list_tenants(self.engine)

    @activity.defn(name="run_center")
    def run_center(self, run: CenterRun) -> CenterResult:
        """Run the engine for one center (one transaction). A date after the data cut-off
        fails without retries, so a stray schedule cannot write flags the data cannot have."""
        as_of = date.fromisoformat(run.as_of)
        try:
            check_as_of(as_of, self.data_cutoff)
        except AfterCutoffError as error:
            raise ApplicationError(str(error), type="AfterCutoff", non_retryable=True) from error
        summary = run_tenant(self.engine, run.tenant_id, as_of, transplant_configs())
        return CenterResult(run.tenant_id, run.as_of, summary.run_id, summary.patients,
                            summary.flags_opened, summary.flags_cleared)  # fmt: skip

    @activity.defn(name="score_center")
    def score_center(self, run: CenterRun) -> RiskResult:
        """Score every patient of one center with the champion model (one transaction)."""
        from txlens_backend.risk.registry import champion_for
        from txlens_backend.risk.serve import score_tenant

        champion = champion_for(self.tracking_uri) if self.tracking_uri else None
        if champion is None:
            activity.logger.warning("No rejection-risk model registered: scoring skipped")
            return RiskResult(0, "")
        done = score_tenant(self.engine, run.tenant_id, date.fromisoformat(run.as_of), champion)
        return RiskResult(done.scored, done.model_version)


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
        results = list(await asyncio.gather(*runs))
        return list(await asyncio.gather(*(self.with_risk(r) for r in results)))

    @staticmethod
    async def with_risk(result: CenterResult) -> CenterResult:
        """The center's result with its risk scoring (a failure is logged, never raised)."""
        try:
            risk: RiskResult = await workflow.execute_activity(
                "score_center", CenterRun(result.tenant_id, result.as_of),
                result_type=RiskResult, start_to_close_timeout=timedelta(minutes=30),
                retry_policy=RETRY,
            )  # fmt: skip
        except ActivityError as error:
            workflow.logger.error("Risk scoring failed for %s: %s", result.tenant_id, error)
            return result
        return dataclasses.replace(result, risk_scored=risk.scored,
                                   risk_model_version=risk.model_version)  # fmt: skip
