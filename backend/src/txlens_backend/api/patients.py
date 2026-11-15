"""Patients: list, as-you-type search and timeline.

Clinicians get identified data and every view is audited as `phi_view`. Researchers and data
stewards get de-identified data (audited as `deid_view`). Admins get 403. A patient id from
another center, or one that does not exist, is a 404: RLS makes the two indistinguishable,
so the API cannot even confirm that the id exists elsewhere.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from txlens_backend import audit, deid
from txlens_backend.api.deps import Context, Info, Principal, Role, TenantDb, require_roles
from txlens_backend.api.rate_limit import Bucket, rate_limited
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


class SearchResults(BaseModel):
    """At most ten patients matching a search, shaped like the patient list for this role."""

    items: list[svc.IdentifiedPatient] | list[svc.DeidentifiedPatient]


def viewer(principal: Principal, ctx: Context) -> svc.Viewer:
    """How to shape results for this caller."""
    return svc.Viewer(
        identified=principal.sees_identifiers,
        deid_key=ctx.deid_key,
        sees_evidence=principal.role in (Role.CLINICIAN, Role.DATA_STEWARD),
    )


def search_details(principal: Principal, ctx: Context, q: str | None) -> dict[str, object]:
    """What the audit log keeps of a search: its length and keyed fingerprint, never the text
    (it may be a patient's name)."""
    if q is None:
        return {}
    fingerprint = deid.search_fingerprint(ctx.deid_key, principal.tenant_id, q)
    return {"q_length": len(q.strip()), "q_fingerprint": fingerprint}


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
        "patient_list", None, {"patients": shown, **search_details(principal, ctx, q),
                               "organ": organ, "donor_type": donor_type, "status": status_},
    )  # fmt: skip
    return PatientPage(items=items, total=total, limit=limit, offset=offset)


@router.get(
    "/search",
    summary="Find patients as you type (top-bar search)",
    dependencies=[Depends(rate_limited(Bucket.SEARCH))],
    responses={429: {"description": "Too many searches; try again shortly"}},
)
def search_patients(
    principal: Reader,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    q: Annotated[
        str,
        Query(
            min_length=svc.SEARCH_MIN,
            max_length=100,
            description="Clinicians: part of a name or the start of an MRN. "
            "Others: the start of a pseudonym (R-...).",
        ),
    ],
) -> SearchResults:
    """Up to ten patients of your center. Researchers and data stewards match pseudonyms only:
    names and MRNs are never searched or returned for them."""
    if len(q.strip()) < svc.SEARCH_MIN:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            f"Type at least {svc.SEARCH_MIN} characters")  # fmt: skip
    view = viewer(principal, ctx)
    items = svc.search(conn, view, q)
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "patient_search", None,
        {"patients": [p.patient_id for p in items], **search_details(principal, ctx, q)},
    )  # fmt: skip
    return SearchResults(items=items)


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
