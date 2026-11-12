"""Cohorts: preview counts, save, list, get, members. Clinicians and researchers only.

For de-identified roles (researchers):
- counts of 1-10 are shown as "<11" (small-cell suppression), so a narrow rule cannot
  single out a patient by its count;
- transplant date rules must cover whole calendar years. Otherwise narrowing the date range
  day by day and watching the count change would reveal true transplant dates, which the
  shifted dates are meant to hide;
- a count is withheld when, with a count shown to the same user earlier over nested rules,
  it would reveal fewer than 11 patients (differencing guard, services/disclosure.py).
"""

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
from txlens_backend.api.patients import PatientPage, viewer
from txlens_backend.services import cohorts as svc
from txlens_backend.services import disclosure, patients

router = APIRouter(prefix="/cohorts", tags=["cohorts"])
Builder = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.RESEARCHER))]
NOT_FOUND: dict[int | str, dict[str, Any]] = {404: {"description": "No such cohort in your center"}}
# Every cohort count is "patients passing the rules": one kind of count, compared by its rules.
FAMILY, BASE, CELL = "cohort", disclosure.base_key(counts="patients"), "patients"


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


def released(
    counts: list[tuple[disclosure.Constraints, int]], principal: Principal, conn: TenantDb,
    ctx: Context, source: str,
) -> tuple[list[int | str], str | None]:  # fmt: skip
    """The counts as this caller may see them, and why any were withheld. Clinicians see exact
    counts; for de-identified roles they go through suppression and the differencing guard."""
    if principal.sees_identifiers:
        return [n for _, n in counts], None
    guard = disclosure.Disclosure(
        conn, principal.user_id, source, threshold=ctx.settings.small_cell_threshold,
        window_days=ctx.settings.disclosure_window_days,
    )  # fmt: skip
    cells = [disclosure.Cell(CELL, constraints, n) for constraints, n in counts]
    return guard.release(FAMILY, BASE, cells), guard.notice


def shown(count: int, rules: svc.CohortRules, principal: Principal, conn: TenantDb,
          ctx: Context, source: str) -> int | str:  # fmt: skip
    """One count of patients passing these rules, as this caller may see it."""
    constraints = svc.step_constraints(rules)[-1]
    return released([(constraints, count)], principal, conn, ctx, source)[0][0]


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


def present(cohort: svc.Cohort, principal: Principal, conn: TenantDb, ctx: Context) -> svc.Cohort:
    """The cohort with its member count as this caller may see it."""
    count = cohort.member_count
    if isinstance(count, int):
        count = shown(count, cohort.rules, principal, conn, ctx, "cohort_saved")
    return cohort.model_copy(update={"member_count": count})


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
    _, steps = svc.preview(conn, rules)
    constraints = svc.step_constraints(rules)
    counts, notice = released(
        [(c, n) for c, (_, n) in zip(constraints, steps, strict=True)], principal, conn, ctx,
        "cohort_preview",
    )  # fmt: skip
    return svc.Preview(
        count=counts[-1],
        steps=[svc.Step(rule=label, count=n) for (label, _), n in zip(steps, counts, strict=True)],
        notices=[notice] if notice else [],
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
    return present(cohort, principal, conn, ctx)


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
    return CohortPage(
        items=[present(c, principal, conn, ctx) for c in items],
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
    return present(cohort, principal, conn, ctx)


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
    cohort = svc.get(conn, cohort_id)
    if cohort is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cohort not found")
    view = viewer(principal, ctx)
    items, total = patients.list_patients(
        conn, view, patients.Filters(cohort_id=cohort_id.lower()), limit, offset
    )
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "cohort_members", cohort_id, {"patients": [p.patient_id for p in items]},
    )  # fmt: skip
    total_shown = shown(total, cohort.rules, principal, conn, ctx, "cohort_members")
    return PatientPage(items=items, total=total_shown, limit=limit, offset=offset)
