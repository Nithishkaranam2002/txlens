"""The human review queue: one queue for clinicians and data stewards.

- `GET /review/items`: pending items in priority order (or decided ones, newest first), with
  the number pending per type. Filters: type, status.
- `GET /review/items/{type}/{key}`: one item with its form fields and evidence: identified
  for clinicians (audited as phi_view), de-identified for data stewards (deid_view).
- `POST /review/items/{type}/{key}`: approve, edit, reject (or revert, for curated mapping
  rows). Edit, reject and revert need a reason code. Biopsy decisions go to the document's
  Temporal workflow. AI-curated mapping rows are reviewed by clinicians only.
- `GET /review/metrics`: queue size, reviews per day, time to review, agreement. Aggregates
  only, so admins may see them too.
Researchers have no access: the queue holds identifiable evidence and unreviewed data.
"""

from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from txlens_backend import audit, deid
from txlens_backend.api.deps import (
    Context,
    Info,
    Principal,
    Role,
    TenantDb,
    require_csrf_header,
    require_roles,
)
from txlens_backend.api.documents import Sender, deliver
from txlens_backend.review import decide
from txlens_backend.review.detail import NotFoundError, ReviewDetail, Viewer, detail, find_item
from txlens_backend.review.metrics import ReviewMetrics, metrics
from txlens_backend.review.queue import QueuePage, Status, queue_page
from txlens_backend.review.reasons import (
    Action,
    DecisionError,
    ItemType,
    ReasonCode,
    check_decision,
)
from txlens_backend.services import biopsies

router = APIRouter(prefix="/review", tags=["review"])
Reviewer = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.DATA_STEWARD))]
MetricsViewer = Annotated[
    Principal, Depends(require_roles(Role.CLINICIAN, Role.DATA_STEWARD, Role.ADMIN))
]
ERRORS: dict[int | str, dict[str, Any]] = {
    403: {"description": "Your role cannot review this item"},
    404: {"description": "No such item in your center"},
    409: {"description": "The item is not waiting for this decision"},
    422: {"description": "A missing reason code, or an edit the item does not allow"},
}


class DecisionRequest(BaseModel):
    """`changes` (field -> value) only for edit. Edit, reject and revert need a reason."""

    action: Action
    reason_code: ReasonCode | None = None
    note: str | None = Field(default=None, max_length=2000)
    changes: dict[str, str | None] = Field(default_factory=dict)


class DecisionResult(BaseModel):
    """`submitted`: a waiting workflow got the decision and applies it; otherwise applied."""

    item_type: ItemType
    item_key: str
    status: Literal["submitted", "applied"]


def viewer_of(principal: Principal, ctx: Context) -> Viewer:
    """Clinicians see real ids and dates; others see pseudonyms and shifted dates."""
    if principal.sees_identifiers:
        return Viewer(True, lambda p: None if p is None else str(p), lambda _: timedelta(0), True)
    key, tenant = ctx.deid_key, principal.tenant_id
    return Viewer(
        identified=False,
        patient=lambda p: None if p is None else deid.pseudonym(key, tenant, p),
        shift=lambda p: timedelta(0 if p is None else deid.date_shift_days(key, tenant, p)),
        can_review_curated=False,
    )


@router.get("/items", summary="The review queue, in priority order")
def list_items(
    principal: Reviewer,
    conn: TenantDb,
    ctx: Context,
    item_type: Annotated[ItemType | None, Query(alias="type")] = None,
    item_status: Annotated[Status, Query(alias="status")] = "pending",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> QueuePage:
    """Pending: rejection/high severity first, then the QC sample, then the rest."""
    viewer = viewer_of(principal, ctx)
    return queue_page(conn, viewer.patient, item_type, item_status, limit, offset)


@router.get(
    "/items/{item_type}/{item_key:path}",
    summary="One item with its form fields and evidence",
    responses={404: ERRORS[404]},
)
def get_item(
    item_type: ItemType, item_key: str, principal: Reviewer, conn: TenantDb, ctx: Context,
    info: Info,
) -> ReviewDetail:  # fmt: skip
    """Evidence is identified for clinicians and de-identified for data stewards."""
    try:
        result = detail(conn, item_type, item_key, viewer_of(principal, ctx))
    except NotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found") from error
    action = "phi_view" if principal.sees_identifiers else "deid_view"
    audit.record(conn, info, principal.user_id, action, f"review_{item_type}", item_key,
                 {"view": "review"})  # fmt: skip
    return result


@router.post(
    "/items/{item_type}/{item_key:path}",
    summary="Approve, edit, reject or revert an item",
    responses=ERRORS,
    dependencies=[Depends(require_csrf_header)],
)
def decide_item(
    item_type: ItemType, item_key: str, body: DecisionRequest, principal: Reviewer,
    conn: TenantDb, ctx: Context, info: Info, sender: Sender,
) -> DecisionResult:  # fmt: skip
    """Check, record (with labelled examples), audit and apply the decision."""
    if item_type == "curated_mapping" and principal.role is not Role.CLINICIAN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only clinicians review curated mappings")
    try:
        check_decision(item_type, body.action, body.reason_code, body.note)
        item = find_item(conn, item_type, item_key, viewer_of(principal, ctx))
    except DecisionError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except NotFoundError as error:
        if item_type == "biopsy_document" and biopsies.document_row(conn, item_key):
            raise HTTPException(status.HTTP_409_CONFLICT, "Document is not in review") from error
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Item not found") from error
    if item.status != "pending" and item_type != "curated_mapping":
        raise HTTPException(status.HTTP_409_CONFLICT, f"Item was already decided ({item.status})")
    decision = decide.Decision(body.action, body.reason_code, body.note, body.changes)
    outcome: Literal["submitted", "applied"] = "applied"
    try:
        if item_type == "biopsy_document":
            decide.biopsy(conn, item, decision, principal.user_id)
            outcome = deliver(conn, ctx, principal, info, sender, item_key, decision)
        elif item_type == "note_mention":
            decide.mention(conn, item, decision, principal.user_id)
        elif item_type == "condition_mapping":
            decide.gap(conn, item, decision, principal.user_id)
        else:
            decide.curated_mapping(conn, item, decision, principal.user_id)
    except decide.InvalidChangeError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(error)) from error
    except decide.ConflictError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from error
    audit.record(conn, info, principal.user_id, "review_decision", f"review_{item_type}",
                 item_key, {"action": body.action, "reason_code": body.reason_code,
                            "fields": sorted(body.changes)})  # fmt: skip
    return DecisionResult(item_type=item_type, item_key=item_key, status=outcome)


@router.get("/metrics", summary="Queue size, throughput, time to review and agreement")
def review_metrics(principal: MetricsViewer, conn: TenantDb) -> ReviewMetrics:
    """Aggregates for the caller's center (no patient data)."""
    return metrics(conn)
