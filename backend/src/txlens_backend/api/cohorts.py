"""Cohorts: preview counts, save, list, get, members. Clinicians and researchers only.

For de-identified roles (researchers):
- counts of 1-10 are shown as "<11" (small-cell suppression), so a narrow rule cannot
  single out a patient by its count;
- transplant date rules must cover whole calendar years. Otherwise narrowing the date range
  day by day and watching the count change would reveal true transplant dates, which the
  shifted dates are meant to hide.
"""

from typing import Annotated, Any

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
from txlens_backend.api.patients import PatientPage, viewer
from txlens_backend.services import cohorts as svc
from txlens_backend.services import patients

router = APIRouter(prefix="/cohorts", tags=["cohorts"])
Builder = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.RESEARCHER))]
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "No such cohort in your center"}}


class NewCohort(BaseModel):
    """A cohort to save."""

    name: str = Field(min_length=1, max_length=200)
    rules: svc.CohortRules


class CohortPage(BaseModel):
    """One page of saved cohorts."""

    items: list[svc.Cohort]
    total: int
    limit: int
    offset: int


def shown(count: int, principal: Principal, threshold: int) -> int | str:
    """The count as this caller may see it."""
    return count if principal.sees_identifiers else deid.suppress(count, threshold)


def check_date_granularity(rules: svc.CohortRules, principal: Principal) -> None:
    """De-identified roles: transplant date bounds must be Jan 1 / Dec 31 (422 otherwise)."""
    bounds = rules.transplant_date
    if principal.sees_identifiers or bounds is None:
        return
    if (bounds.start and (bounds.start.month, bounds.start.day) != (1, 1)) or (
        bounds.end and (bounds.end.month, bounds.end.day) != (12, 31)
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Your role can filter transplant dates by whole years only "
            "(start on Jan 1, end on Dec 31).",
        )


def present(cohort: svc.Cohort, principal: Principal, threshold: int) -> svc.Cohort:
    """The cohort with its member count suppressed where needed."""
    count = cohort.member_count
    visible = shown(count, principal, threshold) if isinstance(count, int) else count
    return cohort.model_copy(update={"member_count": visible})


@router.post(
    "/preview",
    summary="Count the patients matching the rules, step by step",
    dependencies=[Depends(require_csrf_header)],
)
def preview(
    rules: svc.CohortRules, principal: Builder, conn: TenantDb, ctx: Context
) -> svc.Preview:
    """Nothing is saved. Each step shows how many patients remain after that rule."""
    check_date_granularity(rules, principal)
    threshold = ctx.settings.small_cell_threshold
    count, steps = svc.preview(conn, rules)
    return svc.Preview(
        count=shown(count, principal, threshold),
        steps=[svc.Step(rule=label, count=shown(n, principal, threshold)) for label, n in steps],
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Save a cohort and a snapshot of its members",
    dependencies=[Depends(require_csrf_header)],
)
def create(
    body: NewCohort, principal: Builder, conn: TenantDb, ctx: Context, info: Info
) -> svc.Cohort:
    """The member list is fixed at creation, so the cohort can be reproduced later."""
    check_date_granularity(body.rules, principal)
    cohort_id = svc.save(conn, body.name, body.rules, principal.user_id)
    cohort = svc.get(conn, cohort_id)
    assert cohort is not None  # just inserted in this transaction
    audit.record(
        conn, info, principal.user_id, "cohort_create", "cohort", cohort_id,
        {"name": body.name, "member_count": cohort.member_count},
    )  # fmt: skip
    return present(cohort, principal, ctx.settings.small_cell_threshold)


@router.get("", summary="Saved cohorts of your center, newest first")
def list_cohorts(
    principal: Builder,
    conn: TenantDb,
    ctx: Context,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CohortPage:
    """Member counts are suppressed for de-identified roles."""
    items, total = svc.list_cohorts(conn, limit, offset)
    threshold = ctx.settings.small_cell_threshold
    return CohortPage(
        items=[present(c, principal, threshold) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{cohort_id}", summary="One saved cohort", responses=NOT_FOUND)
def get_cohort(cohort_id: str, principal: Builder, conn: TenantDb, ctx: Context) -> svc.Cohort:
    """404 for unknown ids and for cohorts of other centers alike."""
    cohort = svc.get(conn, cohort_id)
    if cohort is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cohort not found")
    return present(cohort, principal, ctx.settings.small_cell_threshold)


@router.get("/{cohort_id}/members", summary="Members of a saved cohort", responses=NOT_FOUND)
def members(
    cohort_id: str,
    principal: Builder,
    conn: TenantDb,
    ctx: Context,
    info: Info,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PatientPage:
    """Identified for clinicians (audited as phi_view), de-identified for researchers."""
    if svc.get(conn, cohort_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cohort not found")
    view = viewer(principal, ctx)
    items, total = patients.list_patients(
        conn, view, patients.Filters(cohort_id=cohort_id.lower()), limit, offset
    )
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "cohort_members", cohort_id, {"patients": [p.patient_id for p in items]},
    )  # fmt: skip
    total_shown = shown(total, principal, ctx.settings.small_cell_threshold)
    return PatientPage(items=items, total=total_shown, limit=limit, offset=offset)
