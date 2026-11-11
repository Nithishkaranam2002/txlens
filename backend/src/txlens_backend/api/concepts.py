"""Vocabulary search for the cohort builder (clinicians and researchers)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from txlens_backend.api.deps import Principal, Role, TenantDb, require_roles
from txlens_backend.services import concepts as svc

router = APIRouter(prefix="/concepts", tags=["cohorts"])
Builder = Annotated[Principal, Depends(require_roles(Role.CLINICIAN, Role.RESEARCHER))]


@router.get("/conditions", summary="Search standard SNOMED condition concepts")
def search_conditions(
    _: Builder,
    conn: TenantDb,
    q: Annotated[str, Query(min_length=2, max_length=100, description="Name part or code")],
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[svc.ConceptHit]:
    """Vocabulary only, no patient data, so nothing is audited."""
    return svc.search_conditions(conn, q, limit)
