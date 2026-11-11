"""Patients: list/search and timeline.

Clinicians get identified data and every view is audited as `phi_view`. Researchers and data
stewards get de-identified data (audited as `deid_view`). Admins get 403. A patient id from
another center, or one that does not exist, is a 404: RLS makes the two indistinguishable,
so the API cannot even confirm that the id exists elsewhere.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from txlens_backend import audit
from txlens_backend.api.deps import Context, Info, Principal, Role, TenantDb, require_roles
from txlens_backend.services import patients as svc

router = APIRouter(prefix="/patients", tags=["patients"])
Reader = Annotated[
    Principal, Depends(require_roles(Role.CLINICIAN, Role.RESEARCHER, Role.DATA_STEWARD))
]
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such patient in your center"}
}


class PatientPage(BaseModel):
    """One page of patients: identified for clinicians, de-identified for everyone else.

    `total` is a string such as "<11" where small counts are suppressed.
    """

    items: list[svc.IdentifiedPatient] | list[svc.DeidentifiedPatient]
    total: int | str
    limit: int
    offset: int


def viewer(principal: Principal, ctx: Context) -> svc.Viewer:
    """How to shape results for this caller."""
    return svc.Viewer(
        identified=principal.sees_identifiers,
        deid_key=ctx.deid_key,
        sees_evidence=principal.role in (Role.CLINICIAN, Role.DATA_STEWARD),
    )


@router.get("", summary="List and search patients")
def list_patients(
    principal: Reader,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    q: Annotated[
        str | None,
        Query(max_length=100, description="Clinicians: MRN or name. Others: pseudonym."),
    ] = None,
    organ: svc.Organ | None = None,
    donor_type: svc.DonorType | None = None,
    status_: Annotated[svc.Status | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PatientPage:
    """Patients of your center, filtered by organ, donor type and status."""
    view = viewer(principal, ctx)
    filters = svc.Filters(q=q, organ=organ, donor_type=donor_type, status=status_)
    items, total = svc.list_patients(conn, view, filters, limit, offset)
    shown = [p.patient_id for p in items]
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "patient_list", None, {"patients": shown, "q": q, "organ": organ,
                               "donor_type": donor_type, "status": status_},
    )  # fmt: skip
    return PatientPage(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/{patient_id}/timeline",
    summary="One patient's labs, drugs, conditions, procedures, death and note dates",
    responses=NOT_FOUND,
)
def patient_timeline(
    patient_id: str, principal: Reader, conn: TenantDb, ctx: Context, info: Info
) -> svc.Timeline:
    """`patient_id` is the person id for clinicians and the pseudonym (`R-...`) for others."""
    view = viewer(principal, ctx)
    person_id = svc.find_person(conn, view, patient_id)
    if person_id is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    result = svc.timeline(conn, view, person_id)
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "patient", str(person_id), {"requested_as": patient_id},
    )  # fmt: skip
    return result
