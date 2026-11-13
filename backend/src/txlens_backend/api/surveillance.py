"""Surveillance worklist and flag review. Clinical decision support: a flag means "review this
patient", never a diagnosis.

- Clinicians see identified patients and acknowledge / resolve flags.
- Data stewards see the worklist de-identified (to check the engine's behaviour), read-only.
- Researchers and admins get 403.

Every view is audited (`phi_view` / `deid_view`) and every status change is audited
(`flag_acknowledge` / `flag_resolve`) with the old and new status.
"""

import uuid
from datetime import date
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
from txlens_backend.surveillance.config import Severity, SurveillanceConfig, load_configs
from txlens_backend.surveillance.repository import InvalidTransitionError, Status, set_status

router = APIRouter(tags=["surveillance"])
Reader = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.DATA_STEWARD))]
Reviewer = Annotated[Principal, Depends(require_roles(Role.CLINICIAN))]
PatientReader = Annotated[
    Principal, Depends(require_roles(Role.CLINICIAN, Role.RESEARCHER, Role.DATA_STEWARD))
]
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
    # The data date of the latest surveillance run; clinicians only. De-identified viewers see
    # each flag's own shifted date instead: the real date next to a shifted one would reveal
    # the patient's date shift.
    data_as_of: date | None


class Review(BaseModel):
    """An optional note kept with the flag (clinicians only see it)."""

    note: str | None = Field(default=None, max_length=2000)


@router.get("/worklist", summary="Patients with open surveillance flags, highest priority first")
def get_worklist(
    principal: Reader,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    organ: patients.Organ | None = None,
    severity: Annotated[
        list[Severity] | None,
        Query(description="Rows whose highest open severity is one of these", max_length=3),
    ] = None,
    status_: Annotated[
        Status | None, Query(alias="status", description="Rows with an open flag in this status")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorklistPage:
    """Open flags (not resolved, still firing) grouped by patient and ranked by priority score."""
    view = viewer(principal, ctx)
    severities = frozenset(severity) if severity else None
    filters = svc.WorklistFilters(organ=organ, severities=severities, status=status_)
    rows = svc.worklist(conn, view, principal.tenant_id, filters)
    page = rows[offset : offset + limit]
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "worklist", None, {"patients": [r.patient.patient_id for r in page],
                           "organ": organ, "severity": severity, "status": status_,
                           "limit": limit, "offset": offset},
    )  # fmt: skip
    as_of = svc.data_as_of(conn) if view.identified else None
    return WorklistPage(items=page, total=len(rows), limit=limit, offset=offset, data_as_of=as_of)


@router.get(
    "/surveillance/configs/{program}",
    summary="The surveillance rules of a program (thresholds, target ranges, schedule)",
    responses={404: {"description": "No config for this program"}},
)
def get_config(program: str, _: PatientReader) -> SurveillanceConfig:
    """The config charts draw threshold lines and target bands from. It holds no patient
    data, so every role that sees patients may read it."""
    config = next((c for c in load_configs().values() if c.program == program), None)
    if config is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No surveillance config for this program")
    return config


@router.get(
    "/patients/{patient_id}/flags",
    summary="All surveillance flags of one patient, newest first, with status history",
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
    record_status(flag_id, new, note, principal, conn, info)
    return svc.one_flag(conn, viewer(principal, ctx), principal.tenant_id, flag_id)


def record_status(
    flag_id: str, new: Status, note: str | None, principal: Principal, conn: TenantDb,
    info: Info,
) -> None:  # fmt: skip
    """Move one flag to `new` and audit the change (404 / 409 if it cannot)."""
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


@router.post(
    "/patients/{patient_id}/flags/acknowledge",
    summary="Acknowledge every new flag of one patient",
    responses={404: {"description": "No such patient in your center"}},
    dependencies=[Depends(require_csrf_header)],
)
def acknowledge_all(
    patient_id: str,
    principal: Reviewer,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    review: Review | None = None,
) -> svc.PatientFlags:
    """new -> acknowledged for each of the patient's open flags, one audit row per flag, all in
    one transaction. Flags already acknowledged or resolved are left as they are."""
    view = viewer(principal, ctx)
    person_id = patients.find_person(conn, view, patient_id)
    if person_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    note = review.note if review else None
    for flag_id in svc.new_flag_ids(conn, person_id):
        record_status(flag_id, Status.ACKNOWLEDGED, note, principal, conn, info)
    result = svc.patient_flags(conn, view, principal.tenant_id, person_id)
    audit.record(conn, info, principal.user_id, "phi_view", "patient_flags", str(person_id),
                 {"requested_as": patient_id})  # fmt: skip
    return result
