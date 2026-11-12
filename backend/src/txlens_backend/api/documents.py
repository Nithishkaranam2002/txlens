"""Pathology documents: the evidence behind each extracted value, and delivering review
decisions (made in the review queue, api/review.py) to the document's workflow.

- `GET /documents/{id}/evidence`: clinicians see every field with page, box and full OCR
  line (audited as phi_view); data stewards see redacted evidence lines only (deid_view);
  researchers get 403.
- `GET /documents/{id}/pages/{n}`: the page image, clinicians only.
- `GET /documents/{id}/fields/{field}/crop`: one evidence line as an image with identifiers
  painted over, refused for any line in the report's identity header (review/crop.py).
A document of another center, or a malformed id, is a 404.
"""

from datetime import timedelta
from pathlib import Path
from typing import Annotated, Any, Literal, Protocol

import pymupdf
from anyio import from_thread
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import Connection
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from txlens_backend import audit, deid
from txlens_backend.api.deps import (
    AppContext,
    Context,
    Info,
    Principal,
    Role,
    TenantDb,
    require_roles,
)
from txlens_backend.audit import RequestInfo
from txlens_backend.documents import service, store
from txlens_backend.documents.service import ReviewDecision
from txlens_backend.review import crop
from txlens_backend.review.decide import Decision
from txlens_backend.services import biopsies

router = APIRouter(prefix="/documents", tags=["documents"])
Reviewer = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.DATA_STEWARD))]
Clinician = Annotated[Principal, Depends(require_roles(Role.CLINICIAN))]
PAGE_DPI = 110
ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such document in your center"},
}


class ReviewSender(Protocol):
    """Delivers a decision to a document's workflow; False if none is waiting."""

    async def send(self, workflow_id: str, decision: ReviewDecision) -> bool:
        """True if a running workflow received the signal."""
        ...


class TemporalReviewSender:
    """Sends review decisions as Temporal signals (one client, connected on first use)."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.client: Client | None = None

    async def send(self, workflow_id: str, decision: ReviewDecision) -> bool:
        self.client = self.client or await Client.connect(self.address)
        handle = self.client.get_workflow_handle(workflow_id)
        try:
            described = await handle.describe()
            if described.status != WorkflowExecutionStatus.RUNNING:
                return False
            await handle.signal("submit_review", decision)
        except RPCError as error:
            if error.status == RPCStatusCode.NOT_FOUND:
                return False
            raise
        return True


def review_sender(request: Request) -> ReviewSender:
    """The app's review sender (tests replace it)."""
    sender: ReviewSender = request.app.state.review_sender
    return sender


Sender = Annotated[ReviewSender, Depends(review_sender)]


def find(conn: TenantDb, document_id: str) -> store.DocumentRow:
    """The document in the caller's center, or 404."""
    row = biopsies.document_row(conn, document_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return row


def deliver(
    conn: Connection, ctx: AppContext, principal: Principal, info: RequestInfo,
    sender: ReviewSender, document_id: str, decision: Decision,
) -> Literal["submitted", "applied"]:  # fmt: skip
    """Send a recorded decision to the document's waiting workflow as a Temporal signal; if
    none is waiting (e.g. a document from an evaluation run), apply it here."""
    row = find(conn, document_id)
    if decision.action == "revert":
        raise ValueError("a document decision is approve, edit or reject")
    changes = dict(decision.changes) if decision.action == "edit" else {}
    signal = ReviewDecision(decision.action, principal.user_id, info.request_id, changes,
                            decision.note, decision.reason_code)  # fmt: skip
    audit.record(conn, info, principal.user_id, "document_review_submit", "document",
                 document_id, {"action": decision.action, "fields": sorted(changes),
                               "reason_code": decision.reason_code})  # fmt: skip
    if from_thread.run(sender.send, row.workflow_id, signal):
        return "submitted"
    service.apply_review(ctx.engine, principal.tenant_id, document_id, signal, "api")
    return "applied"


@router.get(
    "/{document_id}/evidence",
    summary="Extracted fields with their evidence (full for clinicians, redacted otherwise)",
    responses={404: ERRORS[404]},
)
def document_evidence(
    document_id: str, principal: Reviewer, conn: TenantDb, ctx: Context, info: Info
) -> biopsies.DocumentEvidence:
    """Clinicians: page, box and OCR line per field. Data stewards: redacted lines only."""
    row = find(conn, document_id)
    redacted = not principal.sees_identifiers
    shift = 0
    if redacted and row.person_id is not None:
        shift = deid.date_shift_days(ctx.deid_key, principal.tenant_id, row.person_id)
    result = biopsies.evidence(conn, row, redacted, timedelta(days=shift))
    audit.record(conn, info, principal.user_id, "deid_view" if redacted else "phi_view",
                 "document", document_id, {"view": result.view})  # fmt: skip
    return result


def page_png(path: Path, page: int) -> bytes:
    """One page of a PDF as a PNG image."""
    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
        if not 1 <= page <= doc.page_count:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such page")
        return bytes(doc[page - 1].get_pixmap(dpi=PAGE_DPI).tobytes("png"))


@router.get(
    "/{document_id}/pages/{page}",
    summary="A page of the scanned report as an image (clinicians only)",
    responses={404: ERRORS[404]},
    response_class=Response,
)
def document_page(
    document_id: str, page: int, principal: Clinician, conn: TenantDb, ctx: Context, info: Info
) -> Response:
    """The page image, read from the raw document store."""
    row = find(conn, document_id)
    root = ctx.settings.documents_root.resolve()
    path = (root / row.source_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document file not found")
    image = page_png(path, page)
    audit.record(conn, info, principal.user_id, "phi_view", "document_page", document_id,
                 {"page": page})  # fmt: skip
    return Response(image, media_type="image/png")


@router.get(
    "/{document_id}/fields/{field}/crop",
    summary="One evidence line as an image, identifiers painted over (no header lines)",
    responses={404: {"description": "No such document or field, or the line may not be shown"}},
    response_class=Response,
)
def field_crop(
    document_id: str, field: str, principal: Reviewer, conn: TenantDb, ctx: Context, info: Info
) -> Response:
    """The cropped, redacted line image behind one field (what data stewards may see)."""
    row = find(conn, document_id)
    root = ctx.settings.documents_root.resolve()
    path = (root / row.source_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document file not found")
    redact = biopsies.redactor(store.identity_of(conn, row.person_id))
    try:
        image = crop.field_crop(conn, row, field, path, redact)
    except crop.CropRefusedError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No image: {error}") from error
    action = "phi_view" if principal.sees_identifiers else "deid_view"
    audit.record(conn, info, principal.user_id, action, "document_crop", document_id,
                 {"field": field})  # fmt: skip
    return Response(image, media_type="image/png")
