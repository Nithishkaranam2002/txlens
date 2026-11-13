"""Service health and the current user."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from txlens_backend.api.deps import Context, CurrentPrincipal, TenantDb

router = APIRouter(tags=["system"])


class Health(BaseModel):
    """Liveness of the API and its database."""

    status: str
    database: str


@router.get("/health", summary="Is the API up and can it reach the database?")
def health(ctx: Context) -> Health:
    """No login needed. 503 when the database is unreachable."""
    try:
        with ctx.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "database unavailable") from error
    return Health(status="ok", database="ok")


class DemoInfo(BaseModel):
    """What the login page's demo guide shows (public demo only)."""

    guide: bool
    guest_until: datetime | None = None


@router.get("/demo", summary="Is this the public demo, and until when do guest accounts work?")
def demo(ctx: Context) -> DemoInfo:
    """No login needed. Names and dates only: never a password."""
    if not ctx.settings.demo_guide:
        return DemoInfo(guide=False)
    return DemoInfo(guide=True, guest_until=ctx.settings.guest_expires_at)


class Me(BaseModel):
    """The logged-in user and their center."""

    user_id: str
    username: str
    display_name: str
    role: str
    tenant_id: str
    tenant_name: str


@router.get("/me", summary="The logged-in user", responses={401: {"description": "Not logged in"}})
def me(principal: CurrentPrincipal, conn: TenantDb) -> Me:
    """Any role. Read through RLS, so it can only ever return the caller's own tenant."""
    row = conn.execute(
        text(
            "SELECT u.username, u.display_name, u.role, t.tenant_id, t.name "
            "FROM app.app_user u JOIN app.tenant t USING (tenant_id) WHERE u.user_id = :u"
        ),
        {"u": principal.user_id},
    ).one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return Me(
        user_id=principal.user_id,
        username=row.username,
        display_name=row.display_name,
        role=row.role,
        tenant_id=row.tenant_id,
        tenant_name=row.name,
    )
