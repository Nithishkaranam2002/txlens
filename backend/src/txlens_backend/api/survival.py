"""Survival analysis of graft outcomes: run an analysis, export it as CSV or PDF.

Clinicians and researchers. The output is aggregate only and small-cell suppressed for every
role (it can be exported): counts of 1-10 are "<11" and curve steps are pooled. Every analysis
and export is audited. Results are labelled as synthetic data.
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status

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
from txlens_backend.survival import export, service
from txlens_backend.survival.analysis import Report

router = APIRouter(prefix="/survival", tags=["survival"])
Analyst = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.RESEARCHER))]
ERRORS: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such cohort in your center"},
    422: {"description": "Invalid request, or a landmark before the groups are known"},
}
CSRF = [Depends(require_csrf_header)]


@router.get("/options", summary="Groupings and Cox covariates you can choose")
def options(principal: Analyst) -> service.Options:
    """Each grouping comes with the day after transplant from which it is known."""
    return service.options()


def analysed(
    request: service.AnalysisRequest, principal: Principal, conn: TenantDb, ctx: Context,
    info: Info, action: str,
) -> Report:  # fmt: skip
    """Run the analysis and audit it; 404 / 422 for unknown cohorts and early landmarks."""
    try:
        report = service.run(conn, request, ctx.settings.small_cell_threshold)
    except service.CohortNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cohort not found") from error
    except service.LandmarkTooEarlyError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error
    audit.record(
        conn, info, principal.user_id, action, "survival_analysis", request.grouping,
        request.model_dump(mode="json") | {"landmark_days_used": report.landmark_days},
    )  # fmt: skip
    return report


@router.post("/analysis", summary="Graft survival analysis", responses=ERRORS, dependencies=CSRF)
def analysis(
    request: service.AnalysisRequest, principal: Analyst, conn: TenantDb, ctx: Context, info: Info
) -> Report:
    """Kaplan-Meier with risk table and log-rank test, cumulative incidence with death as a
    competing event (and 1 - KM), a Cox model, and a methods paragraph."""
    return analysed(request, principal, conn, ctx, info, "survival_analysis")


@router.post(
    "/report.csv",
    summary="The analysis as CSV",
    responses=ERRORS | {200: {"content": {"text/csv": {}}}},
    response_class=Response,
    dependencies=CSRF,
)
def report_csv(
    request: service.AnalysisRequest, principal: Analyst, conn: TenantDb, ctx: Context, info: Info
) -> Response:
    """Long format: section, group, day, metric, value."""
    report = analysed(request, principal, conn, ctx, info, "survival_export_csv")
    return Response(
        export.to_csv(report), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="graft-survival-synthetic.csv"'},
    )  # fmt: skip


@router.post(
    "/report.pdf",
    summary="The analysis as a PDF report",
    responses=ERRORS | {200: {"content": {"application/pdf": {}}}},
    response_class=Response,
    dependencies=CSRF,
)
def report_pdf(
    request: service.AnalysisRequest, principal: Analyst, conn: TenantDb, ctx: Context, info: Info
) -> Response:
    """Curves, risk table, forest plot, tables and the methods paragraph."""
    report = analysed(request, principal, conn, ctx, info, "survival_export_pdf")
    return Response(
        export.to_pdf(report), media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="graft-survival-synthetic.pdf"'},
    )  # fmt: skip
