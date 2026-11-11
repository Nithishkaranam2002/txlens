"""The surveillance workflow on Temporal's test server: one activity per center, the "as of"
date passed through (or defaulted to the start date), and a real run against the database.

The test server binary is downloaded on first use; without network these tests skip, unless
TXLENS_REQUIRE_DB=1 (CI) makes that a failure.
"""

import asyncio
import os
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date

import pytest
from sqlalchemy import Engine
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from txlens_backend.workflows.surveillance import (
    TASK_QUEUE,
    CenterResult,
    CenterRun,
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
        assert [(r.tenant_id, r.as_of, r.flags_opened) for r in results] == [
            ("center_a", "2024-03-31", 2), ("center_b", "2024-03-31", 2)
        ]  # fmt: skip

    asyncio.run(with_worker([fake_list_centers, fake_run_center], body))
    assert sorted(r.tenant_id for r in SEEN) == ["center_a", "center_b"]


def test_as_of_defaults_to_the_day_the_workflow_starts() -> None:
    SEEN.clear()

    async def body(env: WorkflowEnvironment) -> None:
        today = (await env.get_current_time()).astimezone(UTC).date().isoformat()
        results = await execute(env, None)
        assert {r.as_of for r in results} == {today}

    asyncio.run(with_worker([fake_list_centers, fake_run_center], body))


@pytest.mark.db
def test_real_run_against_the_database(api_app_engine: Engine, api_flags: date) -> None:
    activities = SurveillanceActivities(api_app_engine)

    async def body(env: WorkflowEnvironment) -> None:
        results = await execute(env, api_flags.isoformat())
        assert {r.tenant_id for r in results} == {"center_a", "center_b", "center_c"}
        assert all(r.patients > 0 for r in results)
        assert all(r.flags_opened == 0 for r in results)  # same date as api_flags: idempotent

    asyncio.run(with_worker([activities.list_centers, activities.run_center], body))
