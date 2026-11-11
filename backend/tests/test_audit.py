"""Audit rows: who, tenant, action, resource, time and request id for every audited action."""

from datetime import UTC, datetime, timedelta

import pytest
from api_dataset import Dataset
from api_testutils import login
from fastapi.testclient import TestClient
from httpx import Response
from sqlalchemy import Engine, Row, text

from txlens_backend.api.deps import Role

pytestmark = pytest.mark.db


def audit_row(owner: Engine, response: Response) -> Row[tuple[object, ...]]:
    """The single audit row written by this request (matched on X-Request-ID)."""
    with owner.connect() as conn:
        return conn.execute(
            text(
                "SELECT tenant_id, user_id::text AS user_id, action, object_type, object_id, "
                "occurred_at, request_id, ip FROM app.audit_log WHERE request_id = :r"
            ),
            {"r": response.headers["X-Request-ID"]},
        ).one()


def check(row: Row[tuple[object, ...]], tenant: str, user_id: str, action: str, kind: str) -> None:
    """The row says who, where, what, on which resource, and when."""
    assert (row.tenant_id, row.user_id, row.action, row.object_type) == (
        tenant, user_id, action, kind
    )  # fmt: skip
    assert isinstance(row.occurred_at, datetime)
    assert datetime.now(UTC) - row.occurred_at < timedelta(minutes=1)
    assert row.ip


def test_login_and_logout(client: TestClient, api_owner: Engine) -> None:
    response = login(client, Role.RESEARCHER, "center_c")
    user_id = response.json()["user_id"]
    check(audit_row(api_owner, response), "center_c", user_id, "login", "session")
    logout = client.post("/auth/logout")
    check(audit_row(api_owner, logout), "center_c", user_id, "logout", "session")


def test_failed_login(client: TestClient, api_owner: Engine) -> None:
    user_id = login(client, Role.ADMIN, "center_c").json()["user_id"]
    failed = client.post("/auth/login", json={"username": "admin.center_c", "password": "nope"})
    assert failed.status_code == 401
    check(audit_row(api_owner, failed), "center_c", user_id, "login_failed", "session")


def test_failed_login_for_unknown_user_is_kept_as_an_attempt(
    client: TestClient, api_owner: Engine
) -> None:
    client.post("/auth/login", json={"username": "ghost.user", "password": "nope"})
    with api_owner.connect() as conn:
        attempts = conn.execute(
            text("SELECT succeeded FROM app.login_attempt WHERE username = 'ghost.user'")
        ).scalars()
        assert list(attempts) == [False]


def test_phi_views(client: TestClient, api_owner: Engine, api_data: Dataset) -> None:
    user_id = login(client, Role.CLINICIAN, "center_b").json()["user_id"]
    listing = client.get("/patients", params={"limit": 3})
    row = audit_row(api_owner, listing)
    check(row, "center_b", user_id, "phi_view", "patient_list")
    person_id = listing.json()["items"][0]["patient_id"]
    timeline = client.get(f"/patients/{person_id}/timeline")
    row = audit_row(api_owner, timeline)
    check(row, "center_b", user_id, "phi_view", "patient")
    assert row.object_id == str(person_id)


def test_cohort_create_and_member_view(client: TestClient, api_owner: Engine) -> None:
    user_id = login(client, Role.CLINICIAN, "center_b").json()["user_id"]
    created = client.post("/cohorts", json={"name": "audit", "rules": {"organ": "heart"}})
    row = audit_row(api_owner, created)
    check(row, "center_b", user_id, "cohort_create", "cohort")
    assert row.object_id == created.json()["cohort_id"]
    members = client.get(f"/cohorts/{row.object_id}/members")
    check(audit_row(api_owner, members), "center_b", user_id, "phi_view", "cohort_members")


def test_deidentified_views_are_recorded_too(client: TestClient, api_owner: Engine) -> None:
    user_id = login(client, Role.DATA_STEWARD, "center_b").json()["user_id"]
    listing = client.get("/patients", params={"limit": 3})
    check(audit_row(api_owner, listing), "center_b", user_id, "deid_view", "patient_list")
