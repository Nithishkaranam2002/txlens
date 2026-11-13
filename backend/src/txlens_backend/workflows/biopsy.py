"""Biopsy pathology PDFs -> reviewed OMOP data, as Temporal workflows.

`BiopsyDocumentWorkflow` (one per file, id "biopsy-<tenant>-<sha256>"): register, OCR and link,
two extraction passes in parallel, assess. A confident, valid result is loaded into OMOP at
once; otherwise the workflow waits for a human's `submit_review` signal (approve / edit /
reject) up to a timeout, after which nothing is loaded. The same file is processed once: the
workflow id comes from its hash, and a document already finished returns immediately.

`BiopsyBatchWorkflow` starts document workflows, a few at a time. Each document reports back
(a signal) as soon as it is assessed, so the batch never waits for human reviews; the
documents keep waiting on their own after the batch ends. When the LLM budget runs out the
batch starts no more documents.

Retries: LLM rate limits and outages are retried with exponential backoff (2 s up to 60 s);
an exhausted budget or a misconfigured model is not retried.
"""

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import ActivityError, ApplicationError, WorkflowAlreadyStartedError

with workflow.unsafe.imports_passed_through():
    from pathlib import Path

    from sqlalchemy import Engine

    from txlens_backend.config import Settings
    from txlens_backend.documents import service
    from txlens_backend.documents.budget import Budget, BudgetExceededError
    from txlens_backend.documents.llm import (
        Extractor,
        LlmNotConfiguredError,
        LlmOfflineError,
        Variant,
        make_extractor,
    )
    from txlens_backend.documents.service import ReviewDecision
    from txlens_backend.documents.store import FINAL_STATUSES

TASK_QUEUE = "txlens-documents"
VARIANTS: tuple[Variant, Variant] = ("a", "b")
BUDGET_EXCEEDED = "BudgetExceededError"
LLM_OFFLINE = "LlmOfflineError"  # no API key and no cached answer: the document stays pending
RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))
LLM_RETRY = RetryPolicy(
    initial_interval=timedelta(seconds=2),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=60),
    maximum_attempts=6,
    non_retryable_error_types=[BUDGET_EXCEEDED, LLM_OFFLINE, "LlmNotConfiguredError", "ValueError"],
)
SHORT = timedelta(minutes=1)
LONG = timedelta(minutes=10)


def document_workflow_id(tenant_id: str, sha256: str) -> str:
    """One workflow per tenant and file content."""
    return f"biopsy-{tenant_id}-{sha256}"


@dataclass(frozen=True)
class DocumentRequest:
    """One file to process. `path` is where the worker reads it."""

    tenant_id: str
    sha256: str
    path: str
    source_path: str
    batch_id: str
    review_timeout_seconds: int
    parent_workflow_id: str | None = None


@dataclass(frozen=True)
class DocumentReport:
    """How far a document got: sent to the batch when assessed, and returned at the end."""

    workflow_id: str
    document_id: str
    status: str
    cost_usd: float = 0.0
    reason: str | None = None


@dataclass(frozen=True)
class BatchRequest:
    """Files to process with one budget."""

    batch_id: str
    documents: list[DocumentRequest]
    concurrency: int = 4


@dataclass(frozen=True)
class BatchResult:
    """What happened to every file of a batch."""

    reports: list[DocumentReport]
    not_started: int = 0
    statuses: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0


@dataclass(frozen=True)
class StepInput:
    """The document a step works on, and who asked (the workflow id, for the audit log)."""

    tenant_id: str
    document_id: str
    workflow_id: str


@dataclass(frozen=True)
class ReadInput:
    step: StepInput
    path: str


@dataclass(frozen=True)
class PassInput:
    step: StepInput
    variant: str
    batch_id: str


@dataclass(frozen=True)
class PassCost:
    cost_usd: float
    cached: bool
    failed: bool


@dataclass(frozen=True)
class ReviewInput:
    step: StepInput
    decision: ReviewDecision


@dataclass(frozen=True)
class FailInput:
    step: StepInput
    reason: str


@dataclass(frozen=True)
class RegisterInput:
    tenant_id: str
    path: str
    source_path: str
    workflow_id: str


@dataclass(frozen=True)
class Registered:
    document_id: str
    status: str


class DocumentActivities:
    """Activities bound to the app engine (RLS always applies) and the settings."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        extractor_for: Callable[[Variant], Extractor] | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.extractor_for = extractor_for or (lambda v: make_extractor(settings, v))

    @activity.defn(name="document_register")
    def register(self, request: RegisterInput) -> Registered:
        row = service.register_document(
            self.engine,
            request.tenant_id,
            Path(request.path),
            request.source_path,
            request.workflow_id,
        )
        return Registered(row.document_id, row.status)

    @activity.defn(name="document_read")
    def read(self, request: ReadInput) -> str | None:
        s = request.step
        return service.read_document(
            self.engine,
            s.tenant_id,
            s.document_id,
            Path(request.path),
            self.settings.ocr_engine,
            s.workflow_id,
        )

    @activity.defn(name="document_extract")
    def extract(self, request: PassInput) -> PassCost:
        s = request.step
        variant: Variant = "a" if request.variant == "a" else "b"
        budget = Budget(self.engine, request.batch_id, self.settings.llm_budget_usd)
        budget.open()
        try:
            outcome = service.extraction_pass(
                self.engine,
                s.tenant_id,
                s.document_id,
                self.extractor_for(variant),
                budget,
                variant,
                s.workflow_id,
            )
        except (BudgetExceededError, LlmNotConfiguredError, LlmOfflineError) as error:
            raise ApplicationError(
                str(error), type=type(error).__name__, non_retryable=True
            ) from error
        return PassCost(outcome.cost_usd, outcome.cached, outcome.error is not None)

    @activity.defn(name="document_assess")
    def assess(self, step: StepInput) -> str:
        models: dict[str, str] = {v: self.extractor_for(v).model for v in VARIANTS}
        return service.assess_document(
            self.engine,
            step.tenant_id,
            step.document_id,
            models,
            self.settings.review_threshold,
            step.workflow_id,
            self.settings.qc_sample_rate,
        )

    @activity.defn(name="document_load")
    def load(self, step: StepInput) -> int:
        return service.load_document(
            self.engine, step.tenant_id, step.document_id, step.workflow_id
        )

    @activity.defn(name="document_review")
    def review(self, request: ReviewInput) -> str:
        s = request.step
        return service.apply_review(
            self.engine, s.tenant_id, s.document_id, request.decision, s.workflow_id
        )

    @activity.defn(name="document_expire")
    def expire(self, step: StepInput) -> str:
        return service.expire_review(
            self.engine, step.tenant_id, step.document_id, step.workflow_id
        )

    @activity.defn(name="document_fail")
    def fail(self, request: FailInput) -> str:
        s = request.step
        service.mark_failed(self.engine, s.tenant_id, s.document_id, request.reason, s.workflow_id)
        return "failed"

    def all(self) -> list[Callable[..., object]]:
        """Every activity, for the worker."""
        return [
            self.register,
            self.read,
            self.extract,
            self.assess,
            self.load,
            self.review,
            self.expire,
            self.fail,
        ]


def failed_with(error: ActivityError, error_type: str) -> bool:
    """True if an activity failed with this application error type."""
    cause = error.cause
    return isinstance(cause, ApplicationError) and cause.type == error_type


@workflow.defn(name="BiopsyDocument")
class BiopsyDocumentWorkflow:
    """One biopsy report from file to reviewed OMOP rows."""

    def __init__(self) -> None:
        self.decision: ReviewDecision | None = None
        self.status = "processing"

    @workflow.signal(name="submit_review")
    def submit_review(self, decision: ReviewDecision) -> None:
        """A reviewer's decision; only the first one counts."""
        if self.decision is None:
            self.decision = decision

    @workflow.query(name="status")
    def current_status(self) -> str:
        """Where the document is now."""
        return self.status

    async def step(
        self,
        name: str,
        argument: object,
        result_type: type,
        timeout: timedelta = SHORT,
        retry: RetryPolicy = RETRY,
    ) -> object:
        """Run one activity."""
        return await workflow.execute_activity(
            name,
            argument,
            result_type=result_type,
            start_to_close_timeout=timeout,
            retry_policy=retry,
        )

    async def report(self, request: DocumentRequest, report: DocumentReport) -> DocumentReport:
        """Tell the batch (if any) how far this document got."""
        self.status = report.status
        if request.parent_workflow_id:
            parent = workflow.get_external_workflow_handle(request.parent_workflow_id)
            await parent.signal("document_assessed", report)
        return report

    @workflow.run
    async def run(self, request: DocumentRequest) -> DocumentReport:
        """Process the file; returns its final status."""
        me = workflow.info().workflow_id
        registered = await self.step(
            "document_register",
            RegisterInput(request.tenant_id, request.path, request.source_path, me),
            Registered,
        )
        assert isinstance(registered, Registered)
        step = StepInput(request.tenant_id, registered.document_id, me)
        if registered.status in FINAL_STATUSES:
            return await self.report(
                request, DocumentReport(me, step.document_id, registered.status)
            )
        await self.step("document_read", ReadInput(step, request.path), str, LONG)
        try:
            costs = await asyncio.gather(
                *(
                    self.step(
                        "document_extract",
                        PassInput(step, variant, request.batch_id),
                        PassCost,
                        LONG,
                        LLM_RETRY,
                    )
                    for variant in ("a", "b")
                )
            )
        except ActivityError as error:
            if failed_with(error, LLM_OFFLINE):
                # Reported, then failed on purpose: a later batch (with an API key) may run this
                # document again, which a completed workflow id would not allow.
                await self.report(request, DocumentReport(
                    me, step.document_id, "pending_llm", 0.0, str(error.cause)))  # fmt: skip
                raise ApplicationError("pending: no API key and no cached answer",
                                       type=LLM_OFFLINE, non_retryable=True) from error  # fmt: skip
            if not failed_with(error, BUDGET_EXCEEDED):
                raise
            await self.step("document_fail", FailInput(step, "LLM budget exhausted"), str)
            return await self.report(
                request,
                DocumentReport(me, step.document_id, "budget_exceeded", 0.0, str(error.cause)),
            )
        cost = sum(c.cost_usd for c in costs if isinstance(c, PassCost))
        status = await self.step("document_assess", step, str)
        await self.report(request, DocumentReport(me, step.document_id, str(status), cost))
        if status == "extracted":
            await self.step("document_load", step, int)
            return DocumentReport(me, step.document_id, "extracted", cost)
        return await self.wait_for_review(request, step, cost)

    async def wait_for_review(
        self, request: DocumentRequest, step: StepInput, cost: float
    ) -> DocumentReport:
        """Wait for a reviewer; apply the decision, or expire after the timeout."""
        try:
            await workflow.wait_condition(
                lambda: self.decision is not None,
                timeout=timedelta(seconds=request.review_timeout_seconds),
            )
        except TimeoutError:
            self.status = str(await self.step("document_expire", step, str))
            return DocumentReport(step.workflow_id, step.document_id, self.status, cost)
        assert self.decision is not None
        self.status = str(await self.step("document_review", ReviewInput(step, self.decision), str))
        return DocumentReport(step.workflow_id, step.document_id, self.status, cost)


@workflow.defn(name="BiopsyBatch")
class BiopsyBatchWorkflow:
    """Process many files with one budget, a few at a time."""

    def __init__(self) -> None:
        self.reports: dict[str, DocumentReport] = {}  # by workflow id, as documents report
        self.skipped: list[DocumentReport] = []  # duplicates and files processed before

    @workflow.signal(name="document_assessed")
    def document_assessed(self, report: DocumentReport) -> None:
        """A document finished its automatic steps."""
        self.reports[report.workflow_id] = report

    @workflow.query(name="progress")
    def progress(self) -> dict[str, int]:
        """Documents per status so far."""
        return count_statuses([*self.reports.values(), *self.skipped])

    @workflow.run
    async def run(self, batch: BatchRequest) -> BatchResult:
        """Start documents up to the concurrency limit; stop starting when the budget is out."""
        pending, running = self.unique(batch.documents), set[str]()
        stopped = False
        while (pending and not stopped) or running:
            while pending and not stopped and len(running) < batch.concurrency:
                started = await self.start(batch, pending.pop(0))
                if started:
                    running.add(started)
            await workflow.wait_condition(
                lambda: any(w in self.reports for w in running) or not running
            )
            for done in [w for w in running if w in self.reports]:
                running.discard(done)
                stopped = stopped or self.reports[done].status == "budget_exceeded"
        reports = [*self.reports.values(), *self.skipped]
        cost = round(sum(r.cost_usd for r in reports), 6)
        return BatchResult(reports, len(pending), count_statuses(reports), cost)

    def unique(self, documents: list[DocumentRequest]) -> list[DocumentRequest]:
        """Each file once; repeats of a file in the batch are reported as already processed."""
        seen: set[str] = set()
        first = []
        for document in documents:
            workflow_id = document_workflow_id(document.tenant_id, document.sha256)
            if workflow_id in seen:
                self.skipped.append(DocumentReport(workflow_id, "", "already_processed"))
            else:
                seen.add(workflow_id)
                first.append(document)
        return first

    async def start(self, batch: BatchRequest, document: DocumentRequest) -> str | None:
        """Start one document workflow; None if it already ran (its own record stands)."""
        workflow_id = document_workflow_id(document.tenant_id, document.sha256)
        request = DocumentRequest(
            document.tenant_id,
            document.sha256,
            document.path,
            document.source_path,
            batch.batch_id,
            document.review_timeout_seconds,
            workflow.info().workflow_id,
        )
        try:
            await workflow.start_child_workflow(
                BiopsyDocumentWorkflow.run,
                request,
                id=workflow_id,
                task_queue=TASK_QUEUE,
                parent_close_policy=workflow.ParentClosePolicy.ABANDON,
                id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY,
            )
        except WorkflowAlreadyStartedError:  # started by an earlier batch
            self.skipped.append(DocumentReport(workflow_id, "", "already_processed"))
            return None
        return workflow_id


def count_statuses(reports: Iterable[DocumentReport]) -> dict[str, int]:
    """Status -> number of documents."""
    counts: dict[str, int] = {}
    for report in reports:
        counts[report.status] = counts.get(report.status, 0) + 1
    return dict(sorted(counts.items()))
