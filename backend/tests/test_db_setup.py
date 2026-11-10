"""`make db-setup` creates a real login role that Row-Level Security applies to."""

import os

import psycopg
import pytest
from db_testutils import admin_connection
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import make_url

from txlens_backend.db import tenant_transaction
from txlens_backend.db_setup import center_tenants, ensure_login_role, ensure_tenants, libpq_url

pytestmark = pytest.mark.db


def test_login_role_is_rls_bound_member_of_app_role(database_url: str) -> None:
    user = f"txlens_api_test_{os.getpid()}"
    with admin_connection() as admin:
        ensure_login_role(admin, user, "test-only-password")
        ensure_login_role(admin, user, "test-only-password")  # idempotent
    try:
        url = make_url(database_url).set(username=user, password="test-only-password")
        engine = create_engine(url, poolclass=pool.NullPool)
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, "
                    "pg_has_role(current_user, 'txlens_app', 'MEMBER') "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            ).one()
            assert tuple(row) == (False, False, True)
            assert conn.execute(text("SELECT count(*) FROM cdm.person")).scalar() == 0
        with tenant_transaction(engine, "center_a") as conn:
            setting = conn.execute(text("SELECT current_setting('app.tenant_id')")).scalar()
            assert setting == "center_a"
        engine.dispose()
    finally:
        with admin_connection() as admin:
            admin.execute(
                psycopg.sql.SQL("DROP OWNED BY {} CASCADE").format(psycopg.sql.Identifier(user))
            )
            admin.execute(psycopg.sql.SQL("DROP ROLE {}").format(psycopg.sql.Identifier(user)))


def test_tenants_are_upserted(database_url: str) -> None:
    with psycopg.connect(libpq_url(database_url)) as conn:
        ensure_tenants(conn, {"center_x": "Old name"})
        ensure_tenants(conn, {"center_x": "New name"})
        rows = conn.execute("SELECT name FROM app.tenant WHERE tenant_id = 'center_x'").fetchall()
        assert rows == [("New name",)]
        conn.rollback()


def test_center_tenants_are_the_three_centers() -> None:
    assert sorted(center_tenants()) == ["center_a", "center_b", "center_c"]
