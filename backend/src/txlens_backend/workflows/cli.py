"""Command-line entry points for the surveillance workflow (and the worker for all workflows).

- `txlens-worker`: the Temporal worker for surveillance, biopsy document and note NLP workflows.
- `txlens-surveillance schedule`: create or update the nightly schedule (02:00 UTC).
- `txlens-surveillance run [--as-of YYYY-MM-DD]`: run now and print what each center did.
"""

import argparse
import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import date

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleSpec,
    ScheduleUpdate,
    ScheduleUpdateInput,
)
from temporalio.worker import Worker

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine
from txlens_backend.workflows.biopsy import TASK_QUEUE as DOCUMENTS_QUEUE
from txlens_backend.workflows.biopsy import (
    BiopsyBatchWorkflow,
    BiopsyDocumentWorkflow,
    DocumentActivities,
)
from txlens_backend.workflows.notes import NoteActivities, NoteNlpBatchWorkflow
from txlens_backend.workflows.surveillance import (
    NIGHTLY_SCHEDULE_ID,
    TASK_QUEUE,
    SurveillanceActivities,
    SurveillanceRequest,
    SurveillanceWorkflow,
)

NIGHTLY_CRON = "0 2 * * *"


async def connect() -> Client:
    """A client for the configured Temporal server."""
    return await Client.connect(get_settings().temporal_address)


async def serve() -> None:
    """Run the workers until interrupted. Activities use the app login, so RLS applies."""
    settings = get_settings()
    engine = make_engine(settings.app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    activities = SurveillanceActivities(engine)
    documents = DocumentActivities(engine, settings)
    notes = NoteActivities(engine, settings)
    # Two LLM passes per document: room for `llm_concurrency` documents at once.
    llm_slots = 2 * settings.llm_concurrency
    client = await connect()
    with ThreadPoolExecutor(max_workers=4) as pool, ThreadPoolExecutor(llm_slots) as doc_pool:
        surveillance = Worker(
            client, task_queue=TASK_QUEUE, workflows=[SurveillanceWorkflow],
            activities=[activities.list_centers, activities.run_center], activity_executor=pool,
        )  # fmt: skip
        biopsies = Worker(
            client, task_queue=DOCUMENTS_QUEUE,
            workflows=[BiopsyDocumentWorkflow, BiopsyBatchWorkflow, NoteNlpBatchWorkflow],
            activities=[*documents.all(), *notes.all()], activity_executor=doc_pool,
            max_concurrent_activities=llm_slots,
        )  # fmt: skip
        await asyncio.gather(surveillance.run(), biopsies.run())


def nightly() -> Schedule:
    """The nightly schedule: run as of the day it starts."""
    return Schedule(
        action=ScheduleActionStartWorkflow(
            SurveillanceWorkflow.run,
            SurveillanceRequest(),
            id="surveillance-nightly-run",
            task_queue=TASK_QUEUE,
        ),
        spec=ScheduleSpec(cron_expressions=[NIGHTLY_CRON]),
    )


async def schedule() -> None:
    """Create the nightly schedule, or update it if it already exists."""
    client = await connect()
    try:
        await client.create_schedule(NIGHTLY_SCHEDULE_ID, nightly())
        print(f"Created schedule {NIGHTLY_SCHEDULE_ID} ({NIGHTLY_CRON} UTC)")
    except ScheduleAlreadyRunningError:

        def replace(_: ScheduleUpdateInput) -> ScheduleUpdate:
            return ScheduleUpdate(schedule=nightly())

        await client.get_schedule_handle(NIGHTLY_SCHEDULE_ID).update(replace)
        print(f"Updated schedule {NIGHTLY_SCHEDULE_ID} ({NIGHTLY_CRON} UTC)")


async def run_now(as_of: date | None) -> None:
    """Start a run, wait for it, and print one line per center."""
    client = await connect()
    label = as_of.isoformat() if as_of else "today"
    results = await client.execute_workflow(
        SurveillanceWorkflow.run,
        SurveillanceRequest(as_of.isoformat() if as_of else None),
        id=f"surveillance-manual-{label}-{uuid.uuid4().hex[:8]}",
        task_queue=TASK_QUEUE,
    )
    for result in results:
        print(asdict(result))


def worker_main() -> None:
    """`txlens-worker`."""
    asyncio.run(serve())


def main() -> None:
    """`txlens-surveillance schedule | run [--as-of DATE]`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("schedule", help="create or update the nightly schedule")
    run = commands.add_parser("run", help="run now (for replays, pass an earlier --as-of)")
    run.add_argument("--as-of", type=date.fromisoformat, default=None)
    args = parser.parse_args()
    if args.command == "schedule":
        asyncio.run(schedule())
    else:
        asyncio.run(run_now(args.as_of))
