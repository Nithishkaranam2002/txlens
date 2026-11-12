"""Copilot conversations and questions: persistence, audit, budget, and the event stream.

`ask` yields the events the browser receives: status, tool, token, citations, refusal, then
one `done` (or `error`). Every question is audited (`copilot_question`) before any model
sees it, every tool call by the tool itself. The budget is per center and calendar month
(LLM_BUDGET_USD). A conversation belongs to the user who started it.
"""

import logging
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import Connection, Engine, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql.expression import bindparam

from txlens_backend import audit
from txlens_backend.audit import RequestInfo
from txlens_backend.config import Settings
from txlens_backend.copilot.agent import Agent, State
from txlens_backend.copilot.embeddings import Embedder
from txlens_backend.copilot.llm import CopilotLlm
from txlens_backend.copilot.tools import ToolContext
from txlens_backend.copilot.tracing import start_trace
from txlens_backend.db import tenant_transaction
from txlens_backend.documents.budget import Budget, BudgetExceededError

log = logging.getLogger(__name__)
BUDGET_USED_UP = ("The copilot's budget for this month is used up; ask your administrator "
                  "to raise LLM_BUDGET_USD.")  # fmt: skip
FAILED = "Something went wrong while answering; nothing was changed. Please try again."


@dataclass(frozen=True)
class CopilotDeps:
    """What a question needs from the app (tests swap in a fake model and embedder)."""

    settings: Settings
    engine: Engine
    embedder: Embedder
    completion: Callable[..., Any] | None = None  # None: litellm.completion
    budget_batch: str | None = None  # None: the center's monthly copilot budget
    budget_cap_usd: float | None = None  # None: LLM_BUDGET_USD
    read_cache: bool = True  # False: ask the model again (evaluation of run-to-run variance)


class Message(BaseModel):
    """One question or answer, as stored."""

    message_id: str
    role: str
    content: str
    trace: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    outcome: str | None
    cost_usd: float
    latency_ms: int
    created_at: datetime


class Conversation(BaseModel):
    """A conversation with its messages, oldest first."""

    conversation_id: str
    title: str
    created_at: datetime
    messages: list[Message] = []


class ConversationSummary(BaseModel):
    conversation_id: str
    title: str
    created_at: datetime


def create_conversation(conn: Connection, user_id: str, title: str) -> ConversationSummary:
    """A new, empty conversation of this user."""
    row = conn.execute(
        text("INSERT INTO app.copilot_conversation (user_id, title) VALUES (:u, :t) "
             "RETURNING conversation_id::text, title, created_at"),
        {"u": user_id, "t": title[:200]},
    ).one()  # fmt: skip
    return ConversationSummary(**row._mapping)


def list_conversations(
    conn: Connection, user_id: str, limit: int = 50
) -> list[ConversationSummary]:
    """This user's conversations, newest first."""
    rows = conn.execute(
        text("SELECT conversation_id::text, title, created_at FROM app.copilot_conversation "
             "WHERE user_id = :u ORDER BY created_at DESC LIMIT :l"),
        {"u": user_id, "l": limit},
    ).all()  # fmt: skip
    return [ConversationSummary(**r._mapping) for r in rows]


def get_conversation(conn: Connection, user_id: str, conversation_id: str) -> Conversation | None:
    """One of this user's conversations with its messages (None if not theirs or unknown)."""
    head = conn.execute(
        text("SELECT conversation_id::text, title, created_at FROM app.copilot_conversation "
             "WHERE conversation_id::text = :c AND user_id = :u"),
        {"c": conversation_id.lower(), "u": user_id},
    ).one_or_none()  # fmt: skip
    if head is None:
        return None
    rows = conn.execute(
        text("SELECT message_id::text, role, content, trace, citations, outcome, "
             "cost_usd::float AS cost_usd, latency_ms, created_at FROM app.copilot_message "
             "WHERE conversation_id::text = :c ORDER BY created_at, role DESC"),
        {"c": head.conversation_id},
    ).all()  # fmt: skip
    return Conversation(**head._mapping, messages=[Message(**r._mapping) for r in rows])


def history(conversation: Conversation) -> list[tuple[str, str]]:
    """(question, answer) pairs of earlier turns."""
    pairs, question = [], None
    for message in conversation.messages:
        if message.role == "user":
            question = message.content
        elif question is not None:
            pairs.append((question, message.content))
            question = None
    return pairs


INSERT_MESSAGE = text(
    "INSERT INTO app.copilot_message (message_id, conversation_id, role, content, trace, "
    "citations, outcome, cost_usd, latency_ms) VALUES (:id, :c, :role, :content, :trace, "
    ":citations, :outcome, :cost, :latency)"
).bindparams(bindparam("trace", type_=JSONB), bindparam("citations", type_=JSONB))


def save_message(conn: Connection, conversation_id: str, role: str, content: str, *,
                 message_id: str | None = None, trace: list[dict[str, Any]] | None = None,
                 citations: list[dict[str, Any]] | None = None, outcome: str | None = None,
                 cost: float = 0.0, latency_ms: int = 0) -> str:  # fmt: skip
    """Store one message; returns its id."""
    message_id = message_id or str(uuid.uuid4())
    conn.execute(INSERT_MESSAGE, {
        "id": message_id, "c": conversation_id, "role": role, "content": content,
        "trace": trace or [], "citations": citations or [], "outcome": outcome,
        "cost": cost, "latency": latency_ms,
    })  # fmt: skip
    return message_id


def budget_for(deps: CopilotDeps, tenant_id: str) -> Budget:
    """This center's copilot budget for the current calendar month (or the deps' own batch,
    e.g. an evaluation run with its own cap)."""
    month = datetime.now(UTC).strftime("%Y-%m")
    batch = deps.budget_batch or f"copilot:{tenant_id}:{month}"
    budget = Budget(deps.engine, batch, deps.budget_cap_usd or deps.settings.llm_budget_usd)
    budget.open()
    return budget


def ask(deps: CopilotDeps, user_id: str, tenant_id: str, info: RequestInfo,
        conversation: Conversation, question: str) -> Iterator[dict[str, Any]]:  # fmt: skip
    """Answer one question; yields the events of the answer as they happen."""
    started = time.monotonic()
    with tenant_transaction(deps.engine, tenant_id) as conn:
        question_id = save_message(conn, conversation.conversation_id, "user", question)
        details = {"conversation_id": conversation.conversation_id, "question": question}
        audit.record(conn, info, user_id, "copilot_question", "copilot_message", question_id,
                     details)  # fmt: skip
    answer_id = str(uuid.uuid4())
    trace = start_trace(deps.settings, question, {
        "tenant": tenant_id, "conversation_id": conversation.conversation_id,
        "message_id": answer_id})  # fmt: skip
    budget = budget_for(deps, tenant_id)
    llm = CopilotLlm(
        deps.settings.llm_model, engine=deps.engine, tenant_id=tenant_id, budget=budget,
        trace=trace, reasoning_effort=deps.settings.llm_reasoning_effort,
        timeout_seconds=deps.settings.llm_timeout_seconds,
        api_base=deps.settings.ollama_base_url, completion=deps.completion,
        read_cache=deps.read_cache,
    )  # fmt: skip
    ctx = ToolContext(deps.engine, tenant_id, user_id, deps.settings, info, answer_id,
                      deps.embedder, budget)  # fmt: skip
    state: State = {"question": question, "history": history(conversation)}
    final: dict[str, Any] = {}
    steps: list[dict[str, Any]] = []
    citations: list[dict[str, Any]] = []
    try:
        for mode, event in Agent(llm, ctx).graph().stream(state, stream_mode=["custom", "values"]):
            if mode == "values":
                final = event
                continue
            if event["type"] == "tool":
                steps.append(event)
            elif event["type"] == "citations":
                citations = event["citations"]
            yield event
        outcome, answer = final.get("outcome", "error"), final.get("answer", "")
    except BudgetExceededError:
        outcome, answer = "error", BUDGET_USED_UP
        yield {"type": "error", "message": answer}
    except Exception:
        log.exception("copilot question failed")
        outcome, answer = "error", FAILED
        yield {"type": "error", "message": answer}
    latency = round((time.monotonic() - started) * 1000)
    with tenant_transaction(deps.engine, tenant_id) as conn:
        save_message(conn, conversation.conversation_id, "assistant", answer,
                     message_id=answer_id, trace=steps, citations=citations, outcome=outcome,
                     cost=llm.spend.cost_usd, latency_ms=latency)  # fmt: skip
    trace.end({"outcome": outcome, "answer": answer, "filter": final.get("filter")})
    yield {
        "type": "done", "message_id": answer_id, "outcome": outcome, "answer": answer,
        "refusal": final.get("refusal"), "filter": final.get("filter"),
        "cost_usd": llm.spend.cost_usd, "cost_without_cache_usd": llm.spend.cost_without_cache_usd,
        "llm_calls": llm.spend.calls, "cached_calls": llm.spend.cached, "latency_ms": latency,
    }  # fmt: skip
