"""The research copilot: conversations and streamed answers. Clinicians and researchers.

Answers stream as server-sent events (text/event-stream), one JSON object per event:
`status` (screening / planning / answering), `tool` (a tool call with its SQL, notices and
chart data), `token` (answer text, sentence by sentence), `citations`, `refusal`, `error`,
and finally `done`. The copilot only ever sees aggregate, de-identified data, for every
role. Every question and tool call is audited.
"""

import json
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from txlens_backend.api.deps import (
    Context,
    Info,
    Principal,
    Role,
    TenantDb,
    require_csrf_header,
    require_roles,
)
from txlens_backend.api.rate_limit import Bucket, rate_limited
from txlens_backend.copilot import service
from txlens_backend.copilot.embeddings import make_embedder
from txlens_backend.copilot.suggested import SUGGESTED
from txlens_backend.db import tenant_transaction
from txlens_backend.documents.llm import key_missing

router = APIRouter(prefix="/copilot", tags=["copilot"])
Asker = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.RESEARCHER))]
CSRF = [Depends(require_csrf_header)]
RATE_LIMITED = [*CSRF, Depends(rate_limited(Bucket.COPILOT))]
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "No such conversation of yours"}}


def copilot_deps(request: Request, ctx: Context) -> service.CopilotDeps:
    """The model and embedder (built once per app; tests override this dependency)."""
    deps: service.CopilotDeps | None = getattr(request.app.state, "copilot_deps", None)
    if deps is None:
        # Without an API key (the public demo) only cached answers can be given.
        offline = key_missing(ctx.settings, ctx.settings.llm_model)
        deps = service.CopilotDeps(ctx.settings, ctx.engine, make_embedder(ctx.settings),
                                   offline=offline)  # fmt: skip
        request.app.state.copilot_deps = deps
    return deps


Deps = Annotated[service.CopilotDeps, Depends(copilot_deps)]


class NewConversation(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=200)


class Question(BaseModel):
    question: str = Field(min_length=2, max_length=2000)


@router.get("/suggestions", summary="Suggested questions (their answers are cached)")
def suggestions(principal: Asker) -> list[str]:
    """The same for every user; they work without an API key too."""
    return list(SUGGESTED)


@router.post("/conversations", status_code=status.HTTP_201_CREATED, dependencies=CSRF,
             summary="Start a conversation")  # fmt: skip
def create(body: NewConversation, principal: Asker, conn: TenantDb) -> service.ConversationSummary:
    """An empty conversation of the caller."""
    return service.create_conversation(conn, principal.user_id, body.title, principal.guest_session)


@router.get("/conversations", summary="Your conversations, newest first")
def list_conversations(principal: Asker, conn: TenantDb) -> list[service.ConversationSummary]:
    """Only the caller's own; a guest account sees only those of its current login session."""
    return service.list_conversations(conn, principal.user_id, principal.guest_session)


@router.get("/conversations/{conversation_id}", summary="One of your conversations",
            responses=NOT_FOUND)  # fmt: skip
def get_conversation(
    conversation_id: str, principal: Asker, conn: TenantDb
) -> service.Conversation:
    """404 for unknown ids and for other users' conversations alike."""
    found = service.get_conversation(conn, principal.user_id, conversation_id,
                                     principal.guest_session)  # fmt: skip
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    return found


def sse(events: Iterator[dict[str, Any]]) -> Iterator[str]:
    """Events in server-sent-events format."""
    for event in events:
        yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


@router.post(
    "/conversations/{conversation_id}/messages",
    summary="Ask a question; the answer streams as server-sent events",
    dependencies=RATE_LIMITED,
    response_class=StreamingResponse,
    responses=NOT_FOUND | {200: {"content": {"text/event-stream": {}}}},
)
def ask(conversation_id: str, body: Question, principal: Asker, ctx: Context, info: Info,
        deps: Deps) -> StreamingResponse:  # fmt: skip
    """The conversation is checked first (404); then the answer streams."""
    with tenant_transaction(ctx.engine, principal.tenant_id) as conn:
        conversation = service.get_conversation(conn, principal.user_id, conversation_id,
                                                principal.guest_session)  # fmt: skip
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversation not found")
    events = service.ask(deps, principal.user_id, principal.tenant_id, info, conversation,
                         body.question.strip())  # fmt: skip
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # no proxy buffering
    return StreamingResponse(sse(events), media_type="text/event-stream", headers=headers)
