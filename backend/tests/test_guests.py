"""Guest demo accounts: guest.clinician and guest.researcher log in with one shared password
until their expiry; after it, login and refresh are refused, and a guest with the right
password is told that guest access has ended (other accounts keep the generic message).
A new password ends existing guest sessions. The public demo endpoint never shows a secret."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from api_testutils import BASE_URL, PASSWORDS, login
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from txlens_backend.api.app import create_app
from txlens_backend.api.auth import GUEST_EXPIRED, INVALID_LOGIN
from txlens_backend.api.deps import CSRF_HEADER, Role
from txlens_backend.config import Settings
from txlens_backend.seed_users import GUESTS, seed_guests

pytestmark = pytest.mark.db

PASSWORD = "guest-demo-password-1"


def expire(owner: Engine, username: str, when: datetime | None) -> None:
    """Set one account's expiry (None: never)."""
    with owner.begin() as conn:
        conn.execute(text("UPDATE app.app_user SET expires_at = :w WHERE username = :u"),
                     {"w": when, "u": username})  # fmt: skip


@pytest.fixture
def guests(api_app_engine: Engine, api_owner: Engine) -> Iterator[None]:
    """Guest accounts valid for a day; expired again afterwards."""
    seed_guests(api_app_engine, PASSWORD, datetime.now(UTC) + timedelta(days=1))
    yield
    for username in GUESTS:
        expire(api_owner, username, datetime.now(UTC) - timedelta(days=1))


def guest_login(client: TestClient, username: str, password: str = PASSWORD) -> tuple[int, str]:
    """(status, detail or role) of a guest login."""
    response = client.post("/auth/login", json={"username": username, "password": password})
    body = response.json()
    return response.status_code, body.get("detail") or body.get("role", "")


def test_both_guests_log_in_with_their_roles_in_center_a(client: TestClient, guests: None) -> None:
    assert guest_login(client, "guest.clinician") == (200, "clinician")
    assert client.get("/me").json()["tenant_id"] == "center_a"
    assert guest_login(client, "guest.researcher") == (200, "researcher")


def test_an_expired_guest_is_told_only_with_the_right_password(
    client: TestClient, guests: None, api_owner: Engine
) -> None:
    expire(api_owner, "guest.clinician", datetime.now(UTC) - timedelta(minutes=1))
    assert guest_login(client, "guest.clinician") == (401, GUEST_EXPIRED)
    assert guest_login(client, "guest.clinician", "wrong-password-123") == (401, INVALID_LOGIN)
    with api_owner.connect() as conn:
        actions = conn.execute(text(
            "SELECT action FROM app.audit_log WHERE details->>'username' = 'guest.clinician' "
            "ORDER BY audit_id DESC LIMIT 2"
        )).scalars().all()  # fmt: skip
        succeeded = conn.execute(text(
            "SELECT bool_or(succeeded) FROM app.login_attempt WHERE username = 'guest.clinician'"
        )).scalar_one()  # fmt: skip
    assert actions == ["login_expired", "login_expired"] and succeeded is False


def test_other_expired_accounts_keep_the_generic_message(
    client: TestClient, api_owner: Engine
) -> None:
    expire(api_owner, "data_steward.center_c", datetime.now(UTC) - timedelta(minutes=1))
    try:
        body = {"username": "data_steward.center_c", "password": PASSWORDS[Role.DATA_STEWARD]}
        response = client.post("/auth/login", json=body)
    finally:
        expire(api_owner, "data_steward.center_c", None)
    assert (response.status_code, response.json()["detail"]) == (401, INVALID_LOGIN)


def test_refresh_is_refused_once_the_guest_expires(
    client: TestClient, guests: None, api_owner: Engine
) -> None:
    assert guest_login(client, "guest.researcher")[0] == 200
    assert client.post("/auth/refresh").status_code == 200
    expire(api_owner, "guest.researcher", datetime.now(UTC) - timedelta(minutes=1))
    assert client.post("/auth/refresh").status_code == 401


def test_a_new_password_keeps_the_accounts_and_ends_their_sessions(
    client: TestClient, guests: None, api_app_engine: Engine, api_owner: Engine
) -> None:
    assert guest_login(client, "guest.clinician")[0] == 200
    ids = "SELECT username, user_id FROM app.app_user WHERE username LIKE 'guest.%'"
    with api_owner.connect() as conn:
        before: dict[str, object] = dict(conn.execute(text(ids)).tuples().all())
    seed_guests(api_app_engine, "a-new-guest-password", datetime.now(UTC) + timedelta(days=30))
    assert client.post("/auth/refresh").status_code == 401  # the old session ended
    assert guest_login(client, "guest.clinician")[0] == 401  # the old password is gone
    assert guest_login(client, "guest.clinician", "a-new-guest-password")[0] == 200
    with api_owner.connect() as conn:
        assert dict(conn.execute(text(ids)).tuples().all()) == before


def test_a_short_guest_password_is_refused(api_app_engine: Engine) -> None:
    with pytest.raises(SystemExit, match="GUEST_PASSWORD"):
        seed_guests(api_app_engine, "short", datetime.now(UTC) + timedelta(days=1))


def test_the_demo_endpoint_shows_the_guide_and_end_date_only_when_enabled(
    api_app: FastAPI, api_settings: Settings, api_app_engine: Engine
) -> None:
    with TestClient(api_app, base_url=BASE_URL) as plain:
        assert plain.get("/demo").json() == {"guide": False, "guest_until": None}
    until = datetime(2026, 12, 13, 12, 0, tzinfo=UTC)
    public = api_settings.model_copy(update={
        "demo_guide": True, "guest_expires_at": until, "guest_password": "x" * 20,
    })  # fmt: skip
    with TestClient(create_app(public, api_app_engine), base_url=BASE_URL,
                    headers={CSRF_HEADER: "t"}) as demo:  # fmt: skip
        body = demo.get("/demo").json()
    assert body["guide"] is True and body["guest_until"].startswith("2026-12-13T12:00")
    assert "x" * 20 not in str(body)


def test_demo_users_are_unaffected_by_guest_seeding(client: TestClient, guests: None) -> None:
    assert login(client, Role.CLINICIAN).status_code == 200
