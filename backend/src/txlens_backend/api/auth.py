"""Login, token refresh and logout.

Tokens live in httpOnly cookies, so page JavaScript (and any injected script) cannot read
them. The access cookie goes to every path; the refresh cookie only to the refresh endpoint's
browser path (`REFRESH_COOKIE_PATH`: /auth, or /api/auth behind the frontend proxy). Both are
Secure and SameSite=Strict.

Logins are rate-limited per user name and per client address, counted in Postgres so the
limit holds across API replicas. A wrong user name and a wrong password give the same answer
in the same time, so the API does not reveal which user names exist.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import Connection, text

from txlens_backend import audit
from txlens_backend.api.deps import (
    ACCESS_COOKIE,
    REFRESH_COOKIE,
    AppContext,
    Context,
    Info,
    require_csrf_header,
)
from txlens_backend.config import Settings
from txlens_backend.db import tenant_transaction
from txlens_backend.security import (
    InvalidTokenError,
    TokenClaims,
    decode,
    encode,
    issue_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_csrf_header)])
INVALID_LOGIN = "Invalid user name or password"


class LoginRequest(BaseModel):
    """User name and password."""

    username: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=1, max_length=1000)


class Session(BaseModel):
    """Who is now logged in (the tokens themselves are only in cookies)."""

    user_id: str
    tenant_id: str
    role: str
    access_expires_at: str


@dataclass(frozen=True)
class LoginRow:
    """The pre-login lookup result (app.find_login)."""

    user_id: str
    tenant_id: str
    role: str
    password_hash: str
    is_active: bool


def recent_failures(conn: Connection, username: str, ip: str, settings: Settings) -> bool:
    """True if this user name or client address has too many recent failed logins.

    Failures of a user name only count after its last successful login.
    """
    row = conn.execute(
        text(
            "SELECT count(*) FILTER (WHERE username = :u AND NOT succeeded AND attempted_at > "
            "  (SELECT coalesce(max(attempted_at), '-infinity') FROM app.login_attempt "
            "   WHERE username = :u AND succeeded)) AS by_user, "
            "count(*) FILTER (WHERE ip = :ip AND NOT succeeded) AS by_ip "
            "FROM app.login_attempt WHERE attempted_at > now() - make_interval(mins => :w)"
        ),
        {"u": username, "ip": ip, "w": settings.login_window_minutes},
    ).one()
    return bool(
        row.by_user >= settings.login_max_failures_per_user
        or row.by_ip >= settings.login_max_failures_per_ip
    )


def prune_login_attempts(conn: Connection, retention_days: int) -> int:
    """Delete login attempts older than the retention period; returns rows deleted."""
    result = conn.execute(
        text(
            "DELETE FROM app.login_attempt WHERE attempted_at < now() - make_interval(days => :d)"
        ),
        {"d": retention_days},
    )
    return result.rowcount


def find_login(conn: Connection, username: str) -> LoginRow | None:
    """The user's login row, found before any tenant is known."""
    row = conn.execute(
        text("SELECT user_id, tenant_id, role, password_hash, is_active FROM app.find_login(:u)"),
        {"u": username},
    ).one_or_none()
    if row is None:
        return None
    return LoginRow(str(row.user_id), row.tenant_id, row.role, row.password_hash, row.is_active)


def start_session(
    conn: Connection, ctx: AppContext, response: Response, user_id: str, tenant: str, role: str
) -> tuple[Session, str]:
    """Issue access + refresh tokens, record the refresh token and set both cookies.

    Returns the session and the new refresh token's id.
    """
    refresh_ttl = timedelta(days=ctx.settings.refresh_token_days)
    refresh = issue_token("refresh", user_id, tenant, role, refresh_ttl)
    conn.execute(
        text("INSERT INTO app.refresh_token (jti, user_id, expires_at) VALUES (:j, :u, :e)"),
        {"j": refresh.jti, "u": user_id, "e": refresh.expires_at},
    )
    return send_tokens(ctx, response, refresh), refresh.jti


def send_tokens(ctx: AppContext, response: Response, refresh: TokenClaims) -> Session:
    """Set a fresh access cookie and the cookie for this (already recorded) refresh token."""
    settings = ctx.settings
    access_ttl = timedelta(minutes=settings.access_token_minutes)
    access = issue_token("access", refresh.user_id, refresh.tenant_id, refresh.role, access_ttl)
    refresh_ttl = refresh.expires_at - datetime.now(UTC)
    set_cookie(response, settings, ACCESS_COOKIE, encode(ctx.jwt_secret, access), access_ttl, "/")
    set_cookie(
        response, settings, REFRESH_COOKIE, encode(ctx.jwt_secret, refresh), refresh_ttl,
        settings.refresh_cookie_path,
    )  # fmt: skip
    return Session(
        user_id=refresh.user_id,
        tenant_id=refresh.tenant_id,
        role=refresh.role,
        access_expires_at=access.expires_at.isoformat(),
    )


def set_cookie(
    response: Response, settings: Settings, name: str, token: str, ttl: timedelta, path: str
) -> None:
    """An httpOnly, Secure, SameSite=Strict cookie holding a signed token."""
    response.set_cookie(
        name,
        token,
        max_age=int(ttl.total_seconds()),
        path=path,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def clear_cookies(response: Response, settings: Settings) -> None:
    """Remove both token cookies."""
    for name, path in ((ACCESS_COOKIE, "/"), (REFRESH_COOKIE, settings.refresh_cookie_path)):
        response.delete_cookie(
            name, path=path, httponly=True, secure=settings.cookie_secure, samesite="strict"
        )


@router.post(
    "/login",
    summary="Log in; sets the access and refresh cookies",
    responses={401: {"description": INVALID_LOGIN}, 429: {"description": "Too many failures"}},
)
def login(body: LoginRequest, response: Response, ctx: Context, info: Info) -> Session:
    """Check the password and start a session. Every success and failure is recorded."""
    username = body.username.strip().lower()
    settings = ctx.settings
    with ctx.engine.begin() as conn:
        prune_login_attempts(conn, settings.login_attempt_retention_days)
        if recent_failures(conn, username, info.ip, settings):
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                "Too many failed logins; try again later",
                headers={"Retry-After": str(settings.login_window_minutes * 60)},
            )
        user = find_login(conn, username)
    ok = verify_password(user.password_hash if user and user.is_active else None, body.password)
    with ctx.engine.begin() as conn:
        conn.execute(
            text("INSERT INTO app.login_attempt (username, ip, succeeded) VALUES (:u, :ip, :ok)"),
            {"u": username, "ip": info.ip, "ok": ok},
        )
    if user is None:  # no tenant to audit in; the attempt row above is the record
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)
    with tenant_transaction(ctx.engine, user.tenant_id) as conn:
        action = "login" if ok else "login_failed"
        audit.record(conn, info, user.user_id, action, "session", details={"username": username})
        if ok:
            session, _ = start_session(conn, ctx, response, user.user_id, user.tenant_id, user.role)
            return session
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, INVALID_LOGIN)


@router.post(
    "/refresh",
    summary="Swap the refresh cookie for new access and refresh cookies",
    responses={401: {"description": "No valid refresh token"}},
)
def refresh(request: Request, response: Response, ctx: Context, info: Info) -> Session:
    """Rotate the refresh token. Re-use of an already-rotated token revokes all of the user's
    sessions (a sign that the token was stolen), except within a short grace period after
    the rotation, which returns the replacement (a reload or a second tab raced the refresh)."""
    try:
        claims = decode(ctx.jwt_secret, request.cookies.get(REFRESH_COOKIE), "refresh")
    except InvalidTokenError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated") from error
    with tenant_transaction(ctx.engine, claims.tenant_id) as conn:
        session = rotate(conn, ctx, response, info, claims)
    if session is None:
        clear_cookies(response, ctx.settings)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    return session


def rotate(
    conn: Connection,
    ctx: AppContext,
    response: Response,
    info: audit.RequestInfo,
    claims: TokenClaims,
) -> Session | None:
    """New tokens for a live refresh token; None (after revoking on re-use) otherwise."""
    token = conn.execute(
        text(
            "SELECT t.revoked_at, t.expires_at > now() AS live, u.role, u.is_active, "
            "  r.jti::text AS replacement, r.expires_at AS replacement_expires, "
            "  t.revoked_at > now() - make_interval(secs => :grace) "
            "  AND r.revoked_at IS NULL AND r.expires_at > now() AS in_grace "
            "FROM app.refresh_token t JOIN app.app_user u USING (user_id) "
            "LEFT JOIN app.refresh_token r ON r.jti = t.replaced_by "
            "WHERE t.jti = :j AND t.user_id = :u FOR UPDATE OF t"
        ),
        {"j": claims.jti, "u": claims.user_id,
         "grace": ctx.settings.refresh_reuse_grace_seconds},
    ).one_or_none()  # fmt: skip
    if token is None or not token.live or not token.is_active:
        return None
    if token.in_grace:
        # Rotated moments ago and its replacement is still live: hand out that same refresh
        # token (same id and expiry) with a new access token, instead of a theft alarm.
        audit.record(conn, info, claims.user_id, "refresh_grace", "session", claims.jti)
        replacement = TokenClaims(
            claims.user_id, claims.tenant_id, token.role, token.replacement, "refresh",
            token.replacement_expires,
        )  # fmt: skip
        return send_tokens(ctx, response, replacement)
    if token.revoked_at is not None:
        conn.execute(
            text(
                "UPDATE app.refresh_token SET revoked_at = now() "
                "WHERE user_id = :u AND revoked_at IS NULL"
            ),
            {"u": claims.user_id},
        )
        audit.record(conn, info, claims.user_id, "refresh_reuse", "session", claims.jti)
        return None
    # The role is read again, so a changed role takes effect at the next refresh.
    session, new_jti = start_session(
        conn, ctx, response, claims.user_id, claims.tenant_id, token.role
    )
    conn.execute(
        text("UPDATE app.refresh_token SET revoked_at = now(), replaced_by = :n WHERE jti = :j"),
        {"n": new_jti, "j": claims.jti},
    )
    return session


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke the refresh token and clear the cookies",
)
def logout(request: Request, ctx: Context, info: Info) -> Response:
    """Always succeeds; revokes and audits when a valid refresh token was sent."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    try:
        claims = decode(ctx.jwt_secret, request.cookies.get(REFRESH_COOKIE), "refresh")
    except InvalidTokenError:
        claims = None
    if claims is not None:
        with tenant_transaction(ctx.engine, claims.tenant_id) as conn:
            conn.execute(
                text(
                    "UPDATE app.refresh_token SET revoked_at = now() "
                    "WHERE jti = :j AND revoked_at IS NULL"
                ),
                {"j": claims.jti},
            )
            audit.record(conn, info, claims.user_id, "logout", "session", claims.jti)
    clear_cookies(response, ctx.settings)
    return response
