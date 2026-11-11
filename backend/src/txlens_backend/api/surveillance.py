"""Surveillance worklist and flag review. Clinical decision support: a flag means "review this
patient", never a diagnosis.

- Clinicians see identified patients and acknowledge / resolve flags.
- Data stewards see the worklist de-identified (to check the engine's behaviour), read-only.
- Researchers and admins get 403.

Every view is audited (`phi_view` / `deid_view`) and every status change is audited
(`flag_acknowledge` / `flag_resolve`) with the old and new status.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from txlens_backend import audit
from txlens_backend.api.deps import (
    Context,
    Info,
    Principal,
    Role,
    TenantDb,
    require_csrf_header,
    require_roles,
)
from txlens_backend.api.patients import viewer
from txlens_backend.services import patients
from txlens_backend.services import surveillance as svc
from txlens_backend.surveillance.repository import InvalidTransitionError, Status, set_status

router = APIRouter(tags=["surveillance"])
Reader = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.DATA_STEWARD))]
Reviewer = Annotated[Principal, Depends(require_roles(Role.CLINICIAN))]
FLAG_ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such flag in your center"},
    409: {"description": "The flag cannot move to that status (e.g. it is already resolved)"},
}


class WorklistPage(BaseModel):
    """One page of the ranked worklist."""

    items: list[svc.WorklistRow]
    total: int
    limit: int
    offset: int


class Review(BaseModel):
    """An optional note kept with the flag (clinicians only see it)."""

    note: str | None = Field(default=None, max_length=2000)


@router.get("/worklist", summary="Patients with open surveillance flags, highest priority first")
def get_worklist(
    principal: Reader,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorklistPage:
    """Open flags (not resolved, still firing) grouped by patient and ranked by priority score."""
    view = viewer(principal, ctx)
    rows = svc.worklist(conn, view, principal.tenant_id)
    page = rows[offset : offset + limit]
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "worklist", None, {"patients": [r.patient.patient_id for r in page],
                           "limit": limit, "offset": offset},
    )  # fmt: skip
    return WorklistPage(items=page, total=len(rows), limit=limit, offset=offset)


@router.get(
    "/patients/{patient_id}/flags",
    summary="All surveillance flags of one patient, newest first",
    responses={404: {"description": "No such patient in your center"}},
)
def get_patient_flags(
    patient_id: str, principal: Reader, conn: TenantDb, ctx: Context, info: Info
) -> svc.PatientFlags:
    """`patient_id` is the person id for clinicians and the pseudonym (`R-...`) for others."""
    view = viewer(principal, ctx)
    person_id = patients.find_person(conn, view, patient_id)
    if person_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    result = svc.patient_flags(conn, view, principal.tenant_id, person_id)
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "patient_flags", str(person_id), {"requested_as": patient_id},
    )  # fmt: skip
    return result


def change_status(
    flag_id: str, new: Status, note: str | None, principal: Principal, conn: TenantDb,
    ctx: Context, info: Info,
) -> svc.FlagView:  # fmt: skip
    """Move a flag to `new`, audit it, and return the updated flag."""
    try:
        uuid.UUID(flag_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found") from None
    try:
        change = set_status(conn, flag_id, new, principal.user_id, note)
    except InvalidTransitionError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, str(error)) from None
    if change is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Flag not found")
    action = "flag_acknowledge" if new is Status.ACKNOWLEDGED else "flag_resolve"
    audit.record(
        conn, info, principal.user_id, action, "surveillance_flag", flag_id,
        {"person_id": change.person_id, "from": change.old.value, "to": change.new.value,
         "note": note},
    )  # fmt: skip
    return svc.one_flag(conn, viewer(principal, ctx), principal.tenant_id, flag_id)


@router.post(
    "/flags/{flag_id}/acknowledge",
    summary="Acknowledge a flag (I have seen it and am looking into it)",
    responses=FLAG_ERRORS,
    dependencies=[Depends(require_csrf_header)],
)
def acknowledge(
    flag_id: str,
    principal: Reviewer,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    review: Review | None = None,
) -> svc.FlagView:
    """new -> acknowledged."""
    note = review.note if review else None
    return change_status(flag_id, Status.ACKNOWLEDGED, note, principal, conn, ctx, info)


@router.post(
    "/flags/{flag_id}/resolve",
    summary="Resolve a flag (reviewed; no further action from this flag)",
    responses=FLAG_ERRORS,
    dependencies=[Depends(require_csrf_header)],
)
def resolve(
    flag_id: str,
    principal: Reviewer,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    review: Review | None = None,
) -> svc.FlagView:
    """new or acknowledged -> resolved. A resolved flag is never reopened."""
    note = review.note if review else None
    return change_status(flag_id, Status.RESOLVED, note, principal, conn, ctx, info)
