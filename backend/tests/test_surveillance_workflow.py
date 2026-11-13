"""The surveillance workflow on Temporal's test server: one activity per center, the "as of"
date passed through (or defaulted to the start date), a real run against the database, and a
run past the data cut-off refused.

The test server binary is downloaded on first use; without network these tests skip, unless
TXLENS_REQUIRE_DB=1 (CI) makes that a failure.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, timedelta

import pytest
from sqlalchemy import Engine, text
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from txlens_backend.workflows.surveillance import (
    TASK_QUEUE,
    CenterResult,
    CenterRun,
    RiskResult,
    SurveillanceActivities,
    SurveillanceRequest,
    SurveillanceWorkflow,
)

SEEN: list[CenterRun] = []


@activity.defn(name="list_centers")
def fake_list_centers() -> list[str]:
    return ["center_a", "center_b"]


@activity.defn(name="run_center")
def fake_run_center(run: CenterRun) -> CenterResult:
    SEEN.append(run)
    return CenterResult(run.tenant_id, run.as_of, "run", 1, 2, 0)


@activity.defn(name="score_center")
def fake_score_center(run: CenterRun) -> RiskResult:
    return RiskResult(5, "7")


@activity.defn(name="score_center")
def failing_score_center(run: CenterRun) -> RiskResult:
    raise RuntimeError("registry unreachable")


async def with_worker(
    activities: list[Callable[..., object]], body: Callable[[WorkflowEnvironment], Awaitable[None]]
) -> None:
    """Start the test server and a worker, then run `body` (skips if the server is missing)."""
    try:
        env = await WorkflowEnvironment.start_time_skipping()
    except Exception as error:  # the test server could not be downloaded or started
        if os.environ.get("TXLENS_REQUIRE_DB") == "1":
            raise
        pytest.skip(f"Temporal test server unavailable: {error}")
    async with env:
        with ThreadPoolExecutor(max_workers=4) as pool:
            async with Worker(env.client, task_queue=TASK_QUEUE, workflows=[SurveillanceWorkflow],
                              activities=activities, activity_executor=pool):  # fmt: skip
                await body(env)


async def execute(env: WorkflowEnvironment, as_of: str | None) -> list[CenterResult]:
    """Run the workflow to completion."""
    return await env.client.execute_workflow(
        SurveillanceWorkflow.run, SurveillanceRequest(as_of),
        id=f"test-{uuid.uuid4()}", task_queue=TASK_QUEUE,
    )  # fmt: skip


def test_one_activity_per_center_with_the_requested_date() -> None:
    SEEN.clear()

    async def body(env: WorkflowEnvironment) -> None:
        results = await execute(env, "2024-03-31")
        assert [(r.tenant_id, r.as_of, r.flags_opened, r.risk_scored, r.risk_model_version)
                for r in results] == [("center_a", "2024-03-31", 2, 5, "7"),
                                      ("center_b", "2024-03-31", 2, 5, "7")]  # fmt: skip

    asyncio.run(with_worker([fake_list_centers, fake_run_center, fake_score_center], body))
    assert sorted(r.tenant_id for r in SEEN) == ["center_a", "center_b"]


def test_as_of_defaults_to_the_day_the_workflow_starts() -> None:
    SEEN.clear()

    async def body(env: WorkflowEnvironment) -> None:
        today = (await env.get_current_time()).astimezone(UTC).date().isoformat()
        results = await execute(env, None)
        assert {r.as_of for r in results} == {today}

    asyncio.run(with_worker([fake_list_centers, fake_run_center, fake_score_center], body))


def test_a_scoring_failure_never_blocks_surveillance() -> None:
    async def body(env: WorkflowEnvironment) -> None:
        results = await execute(env, "2024-03-31")
        assert [(r.flags_opened, r.risk_scored, r.risk_model_version) for r in results] == [
            (2, 0, ""), (2, 0, "")
        ]  # fmt: skip

    asyncio.run(with_worker([fake_list_centers, fake_run_center, failing_score_center], body))


@pytest.mark.db
def test_real_run_against_the_database(api_app_engine: Engine, api_flags: date) -> None:
    activities = SurveillanceActivities(api_app_engine)  # no registry: scoring is skipped

    async def body(env: WorkflowEnvironment) -> None:
        results = await execute(env, api_flags.isoformat())
        assert {r.tenant_id for r in results} == {"center_a", "center_b", "center_c"}
        assert all(r.patients > 0 for r in results)
        assert all(r.flags_opened == 0 for r in results)  # same date as api_flags: idempotent
        assert all(r.risk_model_version == "" for r in results)

    asyncio.run(with_worker(
        [activities.list_centers, activities.run_center, activities.score_center], body
    ))  # fmt: skip


def test_a_run_past_the_data_cutoff_fails_and_writes_nothing(
    api_app_engine: Engine, api_owner: Engine, api_flags: date
) -> None:
    """A schedule without the pinned date (as of today) on data that ends earlier."""
    cutoff = api_flags - timedelta(days=1)
    activities = SurveillanceActivities(api_app_engine, None, cutoff)
    count = text("SELECT count(*) FROM app.surveillance_run WHERE as_of > :c")
    with api_owner.connect() as conn:
        before = conn.execute(count, {"c": cutoff}).scalar_one()

    async def body(env: WorkflowEnvironment) -> None:
        with pytest.raises(WorkflowFailureError):
            await execute(env, None)

    asyncio.run(with_worker(
        [activities.list_centers, activities.run_center, activities.score_center], body
    ))  # fmt: skip
    with api_owner.connect() as conn:
        assert conn.execute(count, {"c": cutoff}).scalar_one() == before
