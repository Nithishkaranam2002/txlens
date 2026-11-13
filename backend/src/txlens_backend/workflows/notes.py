"""Clinic notes -> note_nlp and NLP-derived conditions, as one Temporal batch workflow.

`NoteNlpBatchWorkflow` lists each tenant's notes, then processes them in chunks, one after
another, under one LLM budget (the batch id). A chunk is one activity: rules on every note,
the LLM only for unresolved sentences, then one transaction that saves the chunk. Notes whose
text and NLP system are unchanged are skipped, so a retried or repeated batch redoes nothing.
When the budget runs out, the fallback stops asking and the rest of the batch is rules-only;
those notes are saved with the rules system name, so a later batch with budget redoes them.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from collections.abc import Callable
    from functools import cached_property

    from sqlalchemy import Engine

    from txlens_backend.config import Settings
    from txlens_backend.db import tenant_transaction
    from txlens_backend.documents.budget import Budget
    from txlens_backend.documents.llm import Extractor, make_extractor
    from txlens_backend.notes.fallback import PROMPT, Fallback
    from txlens_backend.notes.nlp import NoteNlp
    from txlens_backend.notes.service import ChunkResult, note_source_values, process_notes
    from txlens_backend.notes.vocabulary import load_vocabulary
    from txlens_backend.surveillance.run import list_tenants

RETRY = RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=5))
CHUNK_TIMEOUT = timedelta(minutes=30)
SHORT = timedelta(minutes=1)


@dataclass(frozen=True)
class NotesBatchRequest:
    """Which tenants (empty: all), how many notes each (0: all), and whether to use the LLM."""

    batch_id: str
    tenants: list[str] = field(default_factory=list)
    limit: int = 0
    llm: bool = True
    chunk_size: int = 250


@dataclass(frozen=True)
class ChunkInput:
    tenant_id: str
    source_values: list[str]
    batch_id: str
    llm: bool
    workflow_id: str


@dataclass(frozen=True)
class NotesBatchResult:
    """Totals over every chunk."""

    processed: int = 0
    skipped: int = 0
    mentions: int = 0
    review: int = 0
    conditions: int = 0
    llm_cost_usd: float = 0.0
    budget_exhausted: bool = False
    llm_pending: int = 0

    def plus(self, chunk: ChunkResult) -> "NotesBatchResult":
        """These totals with one more chunk."""
        return NotesBatchResult(
            self.processed + chunk.processed, self.skipped + chunk.skipped,
            self.mentions + chunk.mentions, self.review + chunk.review,
            self.conditions + chunk.conditions, self.llm_cost_usd + chunk.llm_cost_usd,
            self.budget_exhausted or chunk.budget_exhausted,
            self.llm_pending + chunk.llm_pending,
        )  # fmt: skip


class NoteActivities:
    """Activities bound to the app engine (RLS always applies) and the settings."""

    def __init__(
        self,
        engine: Engine,
        settings: Settings,
        extractor: Extractor | None = None,
        nlp: NoteNlp | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.given_extractor = extractor
        self.given_nlp = nlp

    @cached_property
    def nlp(self) -> NoteNlp:
        """One medspaCy pipeline per worker (the vocabulary is shared by every tenant)."""
        if self.given_nlp is not None:
            return self.given_nlp
        with tenant_transaction(self.engine, list_tenants(self.engine)[0]) as conn:
            return NoteNlp(load_vocabulary(conn))

    @cached_property
    def extractor(self) -> Extractor:
        return self.given_extractor or make_extractor(self.settings, "a", PROMPT)

    @activity.defn(name="notes_tenants")
    def tenants(self) -> list[str]:
        return list_tenants(self.engine)

    @activity.defn(name="notes_list")
    def list_notes(self, tenant_id: str) -> list[str]:
        return note_source_values(self.engine, tenant_id)

    @activity.defn(name="notes_process")
    def process(self, chunk: ChunkInput) -> ChunkResult:
        fallback = None
        if chunk.llm:
            budget = Budget(self.engine, chunk.batch_id, self.settings.llm_budget_usd)
            budget.open()
            fallback = Fallback(self.engine, chunk.tenant_id, self.extractor, budget,
                                self.nlp.vocabulary)  # fmt: skip
        return process_notes(
            self.engine, chunk.tenant_id, chunk.source_values, self.nlp, fallback,
            self.settings.review_threshold, chunk.workflow_id,
        )  # fmt: skip

    def all(self) -> list[Callable[..., object]]:
        """Every activity, for the worker."""
        return [self.tenants, self.list_notes, self.process]


def chunks(items: list[str], size: int) -> list[list[str]]:
    """`items` in consecutive pieces of at most `size`."""
    return [items[i : i + size] for i in range(0, len(items), size)]


@workflow.defn(name="NoteNlpBatch")
class NoteNlpBatchWorkflow:
    """Every requested note through the NLP, one chunk at a time."""

    @workflow.run
    async def run(self, request: NotesBatchRequest) -> NotesBatchResult:
        me = workflow.info().workflow_id
        tenants = request.tenants or await workflow.execute_activity(
            "notes_tenants", result_type=list[str], start_to_close_timeout=SHORT,
            retry_policy=RETRY,
        )  # fmt: skip
        total = NotesBatchResult()
        for tenant in tenants:
            notes: list[str] = await workflow.execute_activity(
                "notes_list", tenant, result_type=list[str], start_to_close_timeout=SHORT,
                retry_policy=RETRY,
            )  # fmt: skip
            if request.limit:
                notes = notes[: request.limit]
            for piece in chunks(notes, request.chunk_size):
                result: ChunkResult = await workflow.execute_activity(
                    "notes_process", ChunkInput(tenant, piece, request.batch_id, request.llm, me),
                    result_type=ChunkResult, start_to_close_timeout=CHUNK_TIMEOUT,
                    retry_policy=RETRY,
                )  # fmt: skip
                total = total.plus(result)
        return total
