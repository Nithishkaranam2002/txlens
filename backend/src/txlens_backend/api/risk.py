"""Rejection-risk score of one patient. Trained on synthetic data, not clinically validated,
decision support only: the score asks for a review, it is never a diagnosis.

- Clinicians see the score and may re-score the latest visit on demand.
- Data stewards see it de-identified (shifted dates), read-only, to check the model's behaviour.
- Researchers and admins get 403: individual scores are never shown to researchers.

Every view is audited (`phi_view` / `deid_view`) and every on-demand score is audited
(`risk_score`) with the model version that produced it.
"""

from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

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
from txlens_backend.api.rate_limit import Bucket, rate_limited
from txlens_backend.risk import LABEL_TEXT
from txlens_backend.risk.registry import champion_for
from txlens_backend.risk.serve import latest, latest_visits, score, store
from txlens_backend.services import patients
from txlens_backend.services.patients import Viewer

router = APIRouter(tags=["risk"])
Reader = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.DATA_STEWARD))]
Scorer = Annotated[Principal, Depends(require_roles(Role.CLINICIAN))]
NOT_FOUND: dict[int | str, dict[str, Any]] = {
    404: {"description": "No such patient in your center"}
}


class RiskContribution(BaseModel):
    """One feature's push on this score (log-odds; positive raises the risk)."""

    feature: str
    label: str
    value: float | None
    log_odds: float
    direction: Literal["raises", "lowers"]


class RiskScore(BaseModel):
    """The latest stored score of a patient, or `available: false` if never scored."""

    available: bool
    label: str = LABEL_TEXT
    score: float | None = None
    band: Literal["lower", "elevated"] | None = None
    threshold: float | None = None
    visit_date: date | None = None
    scored_at: datetime | None = None
    source: Literal["nightly", "on_demand"] | None = None
    model_name: str | None = None
    model_version: str | None = None
    contributions: list[RiskContribution] = []


def shaped(row: object | None, view: Viewer, tenant_id: str, person_id: int) -> RiskScore:
    """A stored prediction as this viewer may see it (dates shifted for de-identified views)."""
    if row is None:
        return RiskScore(available=False)
    m = row._mapping  # type: ignore[attr-defined]
    move = timedelta(days=view.shift_days(tenant_id, person_id))
    return RiskScore(
        available=True, score=m["score"], band=m["band"], threshold=m["threshold"],
        visit_date=m["visit_date"] + move, scored_at=m["created_at"] + move, source=m["source"],
        model_name=m["model_name"], model_version=m["model_version"],
        contributions=[RiskContribution(**c, direction="raises" if c["log_odds"] > 0 else "lowers")
                       for c in m["contributions"]],
    )  # fmt: skip


def person(conn: TenantDb, view: Viewer, patient_id: str) -> int:
    found = patients.find_person(conn, view, patient_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Patient not found")
    return found


@router.get(
    "/patients/{patient_id}/risk",
    summary="The patient's latest rejection-risk score with its top contributing features",
    responses=NOT_FOUND,
)
def get_risk(
    patient_id: str, principal: Reader, conn: TenantDb, ctx: Context, info: Info
) -> RiskScore:
    """`patient_id` is the person id for clinicians and the pseudonym (`R-...`) for others."""
    view = viewer(principal, ctx)
    person_id = person(conn, view, patient_id)
    result = shaped(latest(conn, person_id), view, principal.tenant_id, person_id)
    audit.record(
        conn, info, principal.user_id, "phi_view" if view.identified else "deid_view",
        "patient_risk", str(person_id),
        {"requested_as": patient_id, "model_version": result.model_version},
    )  # fmt: skip
    return result


@router.post(
    "/patients/{patient_id}/risk/score",
    summary="Score the patient's latest surveillance visit now with the champion model",
    responses={
        **NOT_FOUND,
        409: {"description": "The patient has no surveillance visit to score"},
        503: {"description": "No risk model is registered"},
    },
    dependencies=[Depends(require_csrf_header), Depends(rate_limited(Bucket.ANALYSIS))],
)
def score_now(
    patient_id: str, principal: Scorer, conn: TenantDb, ctx: Context, info: Info
) -> RiskScore:
    """Stores the score with the model version (a visit already scored by this version keeps
    its first score) and returns the patient's latest score."""
    view = viewer(principal, ctx)
    person_id = person(conn, view, patient_id)
    champion = champion_for(ctx.settings.mlflow_tracking_uri)
    if champion is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No risk model is registered")
    predictions = score(champion, latest_visits(conn, principal.tenant_id, date.today(), person_id))
    if not predictions:
        raise HTTPException(status.HTTP_409_CONFLICT, "No surveillance visit to score")
    store(conn, predictions, "on_demand", scored_by=principal.user_id)
    p = predictions[0]
    audit.record(
        conn, info, principal.user_id, "risk_score", "patient_risk", str(person_id),
        {"model": p.model_name, "version": p.model_version, "visit_date": p.visit_date.isoformat(),
         "score": p.score, "band": p.band},
    )  # fmt: skip
    return shaped(latest(conn, person_id), view, principal.tenant_id, person_id)
