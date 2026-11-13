"""The app factory refuses unsafe setups; health and request ids work."""

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine

from txlens_backend.api.app import build_app, create_app
from txlens_backend.config import InsecureSettingError, Settings
from txlens_backend.db import UnsafeRoleError

pytestmark = pytest.mark.db


def test_refuses_a_superuser_database_login(api_settings: Settings, engine: Engine) -> None:
    with pytest.raises(UnsafeRoleError):
        create_app(api_settings, engine)  # `engine` is the migration owner (superuser)


def test_refuses_weak_secrets(api_settings: Settings, app_engine: Engine) -> None:
    weak = api_settings.model_copy(update={"jwt_secret": SecretStr("short")})
    with pytest.raises(InsecureSettingError):
        create_app(weak, app_engine)


def test_health_and_request_id(api_settings: Settings, app_engine: Engine) -> None:
    with TestClient(create_app(api_settings, app_engine), base_url="https://testserver") as client:
        first, second = client.get("/health"), client.get("/health")
    assert first.status_code == 200
    assert first.json() == {"status": "ok", "database": "ok"}
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_openapi_schema_builds_without_a_database() -> None:
    paths = build_app(Settings(), None).openapi()["paths"]
    assert {"/concepts/conditions", "/surveillance/configs/{program}", "/worklist"} <= set(paths)


def test_security_headers_on_every_response(api_settings: Settings) -> None:
    with TestClient(build_app(api_settings, None), base_url="https://testserver") as client:
        api, docs = client.get("/no-such-route"), client.get("/docs")  # errors are headed too
    assert api.status_code == 404
    assert api.headers["Cache-Control"] == "no-store"
    assert api.headers["X-Content-Type-Options"] == "nosniff"
    assert api.headers["X-Frame-Options"] == "DENY"
    assert api.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "Content-Security-Policy" not in docs.headers  # Swagger UI needs its scripts
    assert docs.headers["X-Frame-Options"] == "DENY"
