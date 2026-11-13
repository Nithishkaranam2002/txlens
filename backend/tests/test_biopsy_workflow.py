"""Biopsy document and batch workflows on Temporal's time-skipping test server.

Fake activities (same names as the real ones) script each file's fate; the last test runs the
real activities against the database with OCR fixed and the LLM faked. The test server is
downloaded on first use; without network these tests skip, unless TXLENS_REQUIRE_DB=1 (CI).
"""

import asyncio
import hashlib
import os
import uuid
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from document_testutils import KIDNEY_ANSWER, KIDNEY_OCR_LINES, MODEL, FakeCompletion
from sqlalchemy import Engine, text
from temporalio import activity
from temporalio.client import WorkflowFailureError, WorkflowHandle
from temporalio.exceptions import ApplicationError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from txlens_backend.config import Settings
from txlens_backend.documents.llm import LiteLlmExtractor, LlmUnavailableError
from txlens_backend.documents.service import ReviewDecision
from txlens_backend.workflows.biopsy import (
    TASK_QUEUE,
    BatchRequest,
    BiopsyBatchWorkflow,
    BiopsyDocumentWorkflow,
    DocumentActivities,
    DocumentReport,
    DocumentRequest,
    FailInput,
    PassCost,
    PassInput,
    ReadInput,
    Registered,
    RegisterInput,
    ReviewInput,
    StepInput,
    document_workflow_id,
)

DAY = 24 * 3600


@dataclass
class Script:
    """What happens to one file."""

    registered_status: str = "processing"
    assessed: str = "extracted"
    budget_exceeded: bool = False
    offline: bool = False  # no API key and no cached answer
    unavailable_times: int = 0


@dataclass
class Seen:
    """What the fake activities were asked to do."""

    calls: list[str] = field(default_factory=list)
    reviews: list[ReviewDecision] = field(default_factory=list)
    extract_attempts: int = 0


SCRIPTS: dict[str, Script] = {}
SEEN = Seen()


def script_of(document_id: str) -> Script:
    return SCRIPTS[document_id]


@activity.defn(name="document_register")
def fake_register(request: RegisterInput) -> Registered:
    SEEN.calls.append(f"register:{request.path}")
    return Registered(request.path, SCRIPTS[request.path].registered_status)


@activity.defn(name="document_read")
def fake_read(request: ReadInput) -> str | None:
    SEEN.calls.append(f"read:{request.path}")
    return "kidney"


@activity.defn(name="document_extract")
def fake_extract(request: PassInput) -> PassCost:
    script = script_of(request.step.document_id)
    SEEN.extract_attempts += 1
    if script.budget_exceeded:
        raise ApplicationError("budget", type="BudgetExceededError", non_retryable=True)
    if script.offline:
        raise ApplicationError("no key", type="LlmOfflineError", non_retryable=True)
    if script.unavailable_times > 0:
        script.unavailable_times -= 1
        raise LlmUnavailableError("rate limited")
    return PassCost(0.001, False, False)


@activity.defn(name="document_assess")
def fake_assess(step: StepInput) -> str:
    return script_of(step.document_id).assessed


@activity.defn(name="document_load")
def fake_load(step: StepInput) -> int:
    SEEN.calls.append(f"load:{step.document_id}")
    return 11


@activity.defn(name="document_review")
def fake_review(request: ReviewInput) -> str:
    SEEN.reviews.append(request.decision)
    return "rejected" if request.decision.action == "reject" else "approved"


@activity.defn(name="document_expire")
def fake_expire(step: StepInput) -> str:
    return "review_expired"


@activity.defn(name="document_fail")
def fake_fail(request: FailInput) -> str:
    SEEN.calls.append(f"fail:{request.step.document_id}:{request.reason}")
    return "failed"


FAKES: list[Callable[..., object]] = [fake_register, fake_read, fake_extract, fake_assess,
                                      fake_load, fake_review, fake_expire, fake_fail]  # fmt: skip


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
        with ThreadPoolExecutor(max_workers=8) as pool:
            async with Worker(env.client, task_queue=TASK_QUEUE,
                              workflows=[BiopsyDocumentWorkflow, BiopsyBatchWorkflow],
                              activities=activities, activity_executor=pool):  # fmt: skip
                await body(env)


def request(path: str, script: Script, timeout: int = 14 * DAY) -> DocumentRequest:
    SCRIPTS[path] = script
    return DocumentRequest("center_a", uuid.uuid4().hex * 2, path, path, "batch-1", timeout)


async def start(env: WorkflowEnvironment, document: DocumentRequest) -> WorkflowHandle[Any, Any]:
    return await env.client.start_workflow(
        BiopsyDocumentWorkflow.run, document, id=f"test-{uuid.uuid4()}", task_queue=TASK_QUEUE
    )


@pytest.fixture(autouse=True)
def fresh() -> None:
    SCRIPTS.clear()
    SEEN.calls.clear()
    SEEN.reviews.clear()
    SEEN.extract_attempts = 0


def test_a_confident_document_is_loaded_without_review() -> None:
    async def body(env: WorkflowEnvironment) -> None:
        handle = await start(env, request("ok.pdf", Script()))
        report = await handle.result()
        assert (report.status, report.cost_usd) == ("extracted", pytest.approx(0.002))

    asyncio.run(with_worker(FAKES, body))
    assert SEEN.calls == ["register:ok.pdf", "read:ok.pdf", "load:ok.pdf"]


async def wait_for(handle: WorkflowHandle[Any, Any], status: str) -> None:
    for _ in range(200):
        if await handle.query("status") == status:
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"workflow never reached {status}")


def test_a_review_signal_applies_the_reviewers_decision() -> None:
    decision = ReviewDecision("edit", "user-1", "req-1", {"banff_t": "3"}, "t3 on the slide")

    async def body(env: WorkflowEnvironment) -> None:
        handle = await start(env, request("unsure.pdf", Script(assessed="in_review")))
        await wait_for(handle, "in_review")
        await handle.signal("submit_review", decision)
        assert (await handle.result()).status == "approved"

    asyncio.run(with_worker(FAKES, body))
    assert SEEN.reviews == [decision]
    assert "load:unsure.pdf" not in SEEN.calls  # the review activity loads, not the auto path


def test_an_unanswered_review_expires_after_the_timeout() -> None:
    async def body(env: WorkflowEnvironment) -> None:
        handle = await start(env, request("late.pdf", Script(assessed="in_review"), 3 * DAY))
        assert (await handle.result()).status == "review_expired"  # time skips three days

    asyncio.run(with_worker(FAKES, body))
    assert SEEN.reviews == []


def test_a_finished_document_is_not_processed_again() -> None:
    async def body(env: WorkflowEnvironment) -> None:
        handle = await start(env, request("done.pdf", Script(registered_status="approved")))
        assert (await handle.result()).status == "approved"

    asyncio.run(with_worker(FAKES, body))
    assert SEEN.calls == ["register:done.pdf"]


def test_rate_limits_are_retried_with_backoff() -> None:
    async def body(env: WorkflowEnvironment) -> None:
        handle = await start(env, request("busy.pdf", Script(unavailable_times=3)))
        assert (await handle.result()).status == "extracted"

    asyncio.run(with_worker(FAKES, body))
    assert SEEN.extract_attempts == 5  # 3 rate-limited attempts, then both passes succeed


def test_an_exhausted_budget_fails_the_document_without_retrying() -> None:
    async def body(env: WorkflowEnvironment) -> None:
        handle = await start(env, request("broke.pdf", Script(budget_exceeded=True)))
        assert (await handle.result()).status == "budget_exceeded"

    asyncio.run(with_worker(FAKES, body))
    assert SEEN.extract_attempts == 2  # one attempt per pass, no retries
    assert "fail:broke.pdf:LLM budget exhausted" in SEEN.calls


def test_a_batch_stops_starting_documents_when_the_budget_runs_out() -> None:
    documents = [request("b1.pdf", Script(budget_exceeded=True)),
                 *(request(f"b{i}.pdf", Script()) for i in range(2, 6))]  # fmt: skip

    async def body(env: WorkflowEnvironment) -> None:
        result = await env.client.execute_workflow(
            BiopsyBatchWorkflow.run, BatchRequest("batch-1", documents, concurrency=1),
            id=f"batch-{uuid.uuid4()}", task_queue=TASK_QUEUE,
        )  # fmt: skip
        assert result.statuses == {"budget_exceeded": 1}
        assert result.not_started == 4

    asyncio.run(with_worker(FAKES, body))


def test_without_an_api_key_uncached_documents_stay_pending_and_the_batch_goes_on() -> None:
    documents = [request("o1.pdf", Script(offline=True)), request("ok2.pdf", Script()),
                 request("o3.pdf", Script(offline=True))]  # fmt: skip

    async def body(env: WorkflowEnvironment) -> None:
        result = await env.client.execute_workflow(
            BiopsyBatchWorkflow.run, BatchRequest("batch-3", documents, concurrency=1),
            id=f"batch-{uuid.uuid4()}", task_queue=TASK_QUEUE,
        )  # fmt: skip
        assert result.statuses == {"pending_llm": 2, "extracted": 1}
        assert result.not_started == 0
        # A pending document's workflow failed on purpose, so a later batch may run it again.
        child = env.client.get_workflow_handle(
            document_workflow_id("center_a", documents[0].sha256)
        )
        with pytest.raises(WorkflowFailureError):
            await child.result()

    asyncio.run(with_worker(FAKES, body))
    assert SEEN.extract_attempts == 6  # no retries: both passes once per document
    assert not [c for c in SEEN.calls if c.startswith("fail:")]  # nothing marked failed


def test_a_batch_does_not_wait_for_reviews_and_skips_duplicates() -> None:
    same = request("dup.pdf", Script())
    documents = [request("r1.pdf", Script(assessed="in_review")), same, same,
                 request("ok1.pdf", Script())]  # fmt: skip

    async def body(env: WorkflowEnvironment) -> None:
        result = await env.client.execute_workflow(
            BiopsyBatchWorkflow.run, BatchRequest("batch-2", documents, concurrency=2),
            id=f"batch-{uuid.uuid4()}", task_queue=TASK_QUEUE,
        )  # fmt: skip
        assert result.statuses == {"already_processed": 1, "extracted": 2, "in_review": 1}
        assert result.cost_usd == pytest.approx(0.006)
        assert isinstance(result.reports[0], DocumentReport)

    asyncio.run(with_worker(FAKES, body))


REVIEWER = "00000000-0000-0000-0000-0000000000a1"


def real_request(path: Path) -> DocumentRequest:
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return DocumentRequest("center_a", sha, str(path), path.name, f"wf-{uuid.uuid4()}", 14 * DAY)


@pytest.mark.db
def test_real_activities_extract_wait_for_review_and_load(
    clinic: None, reviewer: None, report: Any, documents_engine: Engine, documents_owner: Engine
) -> None:
    fake = FakeCompletion(KIDNEY_ANSWER)
    activities = DocumentActivities(
        # No QC sample: it picks reports by file hash, and the generated PDFs differ per run, so
        # the confident report was sometimes sent to review (and expired in skipped time).
        documents_engine, Settings(llm_budget_usd=1.0, review_threshold=0.85, qc_sample_rate=0.0),
        extractor_for=lambda _: LiteLlmExtractor(MODEL, completion=fake),
    )  # fmt: skip
    confident, blurry = report(KIDNEY_OCR_LINES), report(KIDNEY_OCR_LINES, confidence=0.5)
    reports: list[DocumentReport] = []

    async def body(env: WorkflowEnvironment) -> None:
        reports.append(await (await start(env, real_request(confident))).result())
        handle = await start(env, real_request(blurry))
        await wait_for(handle, "in_review")
        await handle.signal("submit_review", ReviewDecision("approve", REVIEWER, "req-9"))
        reports.append(await handle.result())

    asyncio.run(with_worker(activities.all(), body))
    assert [r.status for r in reports] == ["extracted", "approved"]
    with documents_owner.connect() as conn:
        for r in reports:
            loaded = conn.execute(
                text(
                    "SELECT count(*) FROM cdm.measurement WHERE measurement_id IN "
                    "(SELECT cdm_id FROM app.document_cdm_row WHERE document_id = :d)"
                ),
                {"d": r.document_id},
            ).scalar_one()
            assert loaded == 10
        approved_by = conn.execute(
            text("SELECT user_id FROM app.audit_log WHERE action = 'document_review_approve' "
                 "AND object_id = :d"), {"d": reports[1].document_id},
        ).scalar_one()  # fmt: skip
    assert str(approved_by) == REVIEWER
