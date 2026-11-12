"""The FastAPI application factory (`uvicorn txlens_backend.api.app:create_app --factory`)."""

import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from sqlalchemy import Engine
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from txlens_backend.api import (
    auth,
    cohorts,
    concepts,
    documents,
    patients,
    review,
    surveillance,
    survival,
    system,
)
from txlens_backend.api.deps import CSRF_HEADER, CSRF_HEADER_EXAMPLE, AppContext
from txlens_backend.api.documents import TemporalReviewSender
from txlens_backend.config import Settings, get_settings, require_secret
from txlens_backend.db import check_role_is_rls_bound, make_engine

REQUEST_ID_HEADER = "X-Request-ID"
DESCRIPTION = """
Clinical data API for transplant programs (synthetic data only).

**Authentication.** `POST /auth/login` sets two httpOnly cookies: a short-lived access token
and a refresh token. Every POST also needs the header `X-Requested-With` (any value).

**Tenancy.** Every request runs inside the caller's tenant (transplant center), enforced by
Postgres Row-Level Security. No endpoint accepts a tenant id. Ids from other tenants give 404.

**Roles.** Clinicians see identified data, and every view is audited. Researchers and data
stewards see de-identified data: pseudonymous ids, shifted dates, age bands and suppressed
small counts. Admins see no patient data.
"""


def document_csrf_header(app: FastAPI) -> None:
    """Show the CSRF header as required, pre-filled with "XMLHttpRequest", in the OpenAPI docs.

    Only the documentation changes, so Swagger UI's "Try it out" sends the header. The check
    in `require_csrf_header` is untouched: a request without the header still gets 403.
    """
    generate = app.openapi

    def openapi() -> dict[str, Any]:
        schema = generate()  # cached by FastAPI after the first call
        for operations in schema["paths"].values():
            for operation in operations.values():
                for parameter in operation.get("parameters", []):
                    if parameter["in"] == "header" and parameter["name"] == CSRF_HEADER:
                        parameter["required"] = True
                        parameter["schema"] = {"type": "string", "default": CSRF_HEADER_EXAMPLE}
        return schema

    app.openapi = openapi  # type: ignore[method-assign]


def create_app(settings: Settings | None = None, engine: Engine | None = None) -> FastAPI:
    """Build the app. Refuses to start with weak secrets or an RLS-bypassing database role."""
    settings = settings or get_settings()
    engine = engine or make_engine(settings.app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    ctx = AppContext(
        settings=settings,
        engine=engine,
        jwt_secret=require_secret("jwt_secret", settings.jwt_secret),
        deid_key=require_secret("deid_hmac_key", settings.deid_hmac_key),
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        engine.dispose()

    app = build_app(settings, lifespan)
    app.state.ctx = ctx
    app.state.review_sender = TemporalReviewSender(settings.temporal_address)
    return app


def build_app(
    settings: Settings, lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None
) -> FastAPI:
    """The routes and middleware, without a database (enough to export the OpenAPI schema)."""
    app = FastAPI(title="TxLens API", version="0.1.0", description=DESCRIPTION, lifespan=lifespan)
    # Only the configured proxy may set the client address (run uvicorn with --no-proxy-headers
    # so this is the one place that reads X-Forwarded-For).
    app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=settings.trusted_proxy_ips)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_origin],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", CSRF_HEADER],
        expose_headers=[REQUEST_ID_HEADER],
    )

    @app.middleware("http")
    async def request_id(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """A fresh id per request: stored for audit rows and returned to the client."""
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request.state.request_id
        return response

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(patients.router)
    app.include_router(cohorts.router)
    app.include_router(concepts.router)
    app.include_router(surveillance.router)
    app.include_router(documents.router)
    app.include_router(review.router)
    app.include_router(survival.router)
    document_csrf_header(app)
    return app


def export_openapi() -> None:
    """Print the OpenAPI schema as JSON (`txlens-export-openapi > frontend/openapi.json`).

    This is the schema before the Swagger tweak, so the CSRF header stays optional in the
    generated TypeScript types: the frontend's fetch wrapper adds it to every POST.
    """
    app = build_app(Settings(), None)
    schema = get_openapi(
        title=app.title, version=app.version, description=app.description, routes=app.routes
    )
    print(json.dumps(schema, indent=2, sort_keys=True))
