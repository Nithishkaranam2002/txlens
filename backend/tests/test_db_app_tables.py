"""App tables (identity, users, tokens, cohorts): tenant isolation and the login lookup."""

from collections.abc import Callable
from datetime import date

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import ProgrammingError

pytestmark = pytest.mark.db
AsApp = Callable[[Connection, str | None], None]
ISOLATED = ("patient_identity", "app_user", "refresh_token", "cohort", "cohort_member")
USERS = {"center_a": "00000000-0000-0000-0000-00000000000a",
         "center_b": "00000000-0000-0000-0000-00000000000b"}  # fmt: skip
COHORTS = {"center_a": "00000000-0000-0000-0000-0000000000ca",
           "center_b": "00000000-0000-0000-0000-0000000000cb"}  # fmt: skip


@pytest.fixture
def app_rows(db: Connection) -> Connection:
    """One row per isolated app table per tenant, inserted by the owner."""
    for tenant, person_id in (("center_a", 1), ("center_b", 2)):
        user, cohort = USERS[tenant], COHORTS[tenant]
        params = {"t": tenant, "p": person_id, "u": user, "c": cohort, "d": date(1970, 1, 2)}
        for statement in (
            "INSERT INTO app.patient_identity (tenant_id, person_id, mrn, birth_date) "
            "VALUES (:t, :p, 'MRN' || :p, :d)",
            "INSERT INTO app.app_user (user_id, tenant_id, username, display_name, role, "
            "password_hash) VALUES (:u, :t, 'user.' || :t, 'Test User', 'clinician', 'hash')",
            "INSERT INTO app.refresh_token (jti, tenant_id, user_id, expires_at) "
            "VALUES (gen_random_uuid(), :t, :u, now() + interval '1 day')",
            "INSERT INTO app.cohort (cohort_id, tenant_id, name, rules, created_by, member_count) "
            "VALUES (:c, :t, 'c', '{}', :u, 1)",
            "INSERT INTO app.cohort_member (tenant_id, cohort_id, person_id) VALUES (:t, :c, :p)",
        ):
            db.execute(text(statement), params)
    return db


def tenants_of(conn: Connection, table: str) -> list[str]:
    return list(conn.execute(text(f"SELECT tenant_id FROM app.{table}")).scalars())


@pytest.mark.parametrize("table", ISOLATED)
def test_each_tenant_sees_only_its_own_rows(
    app_rows: Connection, as_app: AsApp, table: str
) -> None:
    as_app(app_rows, "center_a")
    assert tenants_of(app_rows, table) == ["center_a"]
    as_app(app_rows, "center_b")
    assert tenants_of(app_rows, table) == ["center_b"]
    as_app(app_rows, "")  # tenant cleared: nothing visible
    assert tenants_of(app_rows, table) == []


def test_find_login_works_before_a_tenant_is_known(app_rows: Connection, as_app: AsApp) -> None:
    as_app(app_rows, None)
    row = app_rows.execute(text("SELECT * FROM app.find_login('USER.center_b')")).mappings().one()
    assert list(row.keys()) == ["user_id", "tenant_id", "role", "password_hash", "is_active"]
    assert (str(row["user_id"]), row["tenant_id"], row["role"]) == (
        USERS["center_b"], "center_b", "clinician"
    )  # fmt: skip
    assert (row["password_hash"], row["is_active"]) == ("hash", True)
    assert app_rows.execute(text("SELECT * FROM app.find_login('nobody')")).all() == []


def test_find_login_does_not_open_the_users_table(app_rows: Connection, as_app: AsApp) -> None:
    as_app(app_rows, None)
    app_rows.execute(text("SELECT * FROM app.find_login('user.center_a')")).all()
    assert tenants_of(app_rows, "app_user") == []


def test_only_the_app_role_may_call_find_login(app_rows: Connection) -> None:
    app_rows.execute(text("CREATE ROLE txlens_test_outsider NOLOGIN"))
    app_rows.execute(text("GRANT USAGE ON SCHEMA app TO txlens_test_outsider"))
    app_rows.execute(text("SET LOCAL ROLE txlens_test_outsider"))
    with pytest.raises(ProgrammingError, match="permission denied"), app_rows.begin_nested():
        app_rows.execute(text("SELECT * FROM app.find_login('user.center_a')"))


def test_find_login_is_not_executable_by_public(db: Connection) -> None:
    acl = db.execute(
        text("SELECT proacl::text FROM pg_proc WHERE oid = 'app.find_login(text)'::regprocedure")
    ).scalar()
    assert acl is not None
    assert "txlens_app=X" in acl
    assert not [entry for entry in acl.strip("{}").split(",") if entry.startswith("=")]


def test_login_attempts_can_be_added_read_and_pruned_not_changed(
    db: Connection, as_app: AsApp
) -> None:
    as_app(db, None)
    db.execute(
        text("INSERT INTO app.login_attempt (username, ip, succeeded) VALUES ('x', '1', false)")
    )
    assert db.execute(text("SELECT count(*) FROM app.login_attempt")).scalar() == 1
    db.execute(
        text("DELETE FROM app.login_attempt WHERE attempted_at < now() - interval '30 days'")
    )
    with pytest.raises(ProgrammingError, match="permission denied"), db.begin_nested():
        db.execute(text("UPDATE app.login_attempt SET succeeded = true"))


@pytest.mark.parametrize(
    "statement", ["UPDATE app.cohort SET name = 'x'", "DELETE FROM app.cohort"]
)
def test_saved_cohorts_cannot_be_changed(
    app_rows: Connection, as_app: AsApp, statement: str
) -> None:
    as_app(app_rows, "center_a")
    with pytest.raises(ProgrammingError, match="permission denied"), app_rows.begin_nested():
        app_rows.execute(text(statement))


def test_audit_log_has_request_context_columns(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    db.execute(
        text(
            "INSERT INTO app.audit_log (action, object_type, user_id, request_id, ip) "
            "VALUES ('login', 'session', :u, 'req-1', '127.0.0.1')"
        ),
        {"u": USERS["center_a"]},
    )
    row = db.execute(text("SELECT user_id, request_id, ip FROM app.audit_log")).one()
    assert (str(row.user_id), row.request_id, row.ip) == (USERS["center_a"], "req-1", "127.0.0.1")
