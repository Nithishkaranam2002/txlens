"""Request dependencies: who is calling, which roles may proceed, and the tenant transaction.

The tenant comes only from the signed access token, never from the request. Every
data-access dependency opens `tenant_transaction` as the non-superuser app login, so
Row-Level Security applies to every query.
"""

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy import Connection, Engine

from txlens_backend.audit import RequestInfo
from txlens_backend.config import Settings
from txlens_backend.db import tenant_transaction
from txlens_backend.security import InvalidTokenError, decode

ACCESS_COOKIE = "txlens_access"
REFRESH_COOKIE = "txlens_refresh"
CSRF_HEADER = "X-Requested-With"
CSRF_HEADER_EXAMPLE = "XMLHttpRequest"


class Role(StrEnum):
    """The four roles. Clinicians see identified data; researchers and stewards do not."""

    CLINICIAN = "clinician"
    RESEARCHER = "researcher"
    DATA_STEWARD = "data_steward"
    ADMIN = "admin"


@dataclass(frozen=True)
class AppContext:
    """Process-wide state: settings, the app-role engine and the validated secrets."""

    settings: Settings
    engine: Engine
    jwt_secret: str
    deid_key: str


@dataclass(frozen=True)
class Principal:
    """The authenticated caller."""

    user_id: str
    tenant_id: str
    role: Role

    @property
    def sees_identifiers(self) -> bool:
        """Only clinicians see names, MRNs, birth dates and real dates."""
        return self.role is Role.CLINICIAN


def context(request: Request) -> AppContext:
    """The app's shared context."""
    ctx: AppContext = request.app.state.ctx
    return ctx


def request_info(request: Request) -> RequestInfo:
    """The request id (set by middleware) and the client address, for audit rows."""
    host = request.client.host if request.client else "unknown"
    return RequestInfo(request.state.request_id, host)


Context = Annotated[AppContext, Depends(context)]
Info = Annotated[RequestInfo, Depends(request_info)]


def current_principal(request: Request, ctx: Context) -> Principal:
    """The caller from the access-token cookie; 401 if missing, expired or invalid."""
    try:
        claims = decode(ctx.jwt_secret, request.cookies.get(ACCESS_COOKIE), "access")
        return Principal(claims.user_id, claims.tenant_id, Role(claims.role))
    except (InvalidTokenError, ValueError) as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated") from error


CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def require_roles(*roles: Role) -> Callable[[Principal], Principal]:
    """A dependency that lets only these roles through (403 for the others)."""

    def check(principal: CurrentPrincipal) -> Principal:
        if principal.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot do this")
        return principal

    return check


def tenant_db(principal: CurrentPrincipal, ctx: Context) -> Iterator[Connection]:
    """A transaction scoped to the caller's tenant; commits before the response is sent."""
    with tenant_transaction(ctx.engine, principal.tenant_id) as conn:
        yield conn


TenantDb = Annotated[Connection, Depends(tenant_db, scope="function")]


def require_csrf_header(
    x_requested_with: Annotated[
        str | None,
        Header(
            alias=CSRF_HEADER,
            description="Any non-empty value; requests without it get 403 (CSRF defence).",
        ),
    ] = None,
) -> None:
    """State-changing requests must carry a custom header.

    Browsers cannot add a custom header to a cross-site form post, and a cross-site fetch with
    one needs a CORS preflight that only the frontend's origin passes. Together with
    SameSite=Strict cookies this blocks cross-site request forgery.
    """
    if not x_requested_with:
        raise HTTPException(status.HTTP_403_FORBIDDEN, f"Missing {CSRF_HEADER} header")
