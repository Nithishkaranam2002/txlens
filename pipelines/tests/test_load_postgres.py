"""Postgres load: replaces a tenant's rows in one transaction, with RLS on (no Spark needed)."""

import os
from collections.abc import Iterator
from datetime import date

import psycopg
import pytest
from db_testutils import admin_connection, create_database, drop_database, migrate
from psycopg import sql

from txlens_backend.db_setup import ensure_login_role, ensure_tenants, libpq_url
from txlens_pipelines.load.postgres import TableRows, UnsafeLoadError, load_tenant

pytestmark = pytest.mark.db
PERSON = ["tenant_id", "person_id", "gender_concept_id", "year_of_birth", "race_concept_id",
          "ethnicity_concept_id", "person_source_value"]  # fmt: skip
DEATH = ["tenant_id", "person_id", "death_date"]
Conn = psycopg.Connection[tuple[object, ...]]


@pytest.fixture(scope="module")
def urls() -> Iterator[tuple[str, str]]:
    """(owner url, app login url) of a fresh migrated database with two tenants."""
    url = create_database("load")
    user = f"txlens_load_test_{os.getpid()}"
    try:
        migrate(url)
        with psycopg.connect(libpq_url(url), autocommit=True) as owner:
            ensure_tenants(owner, {"center_a": "A", "center_b": "B"})
        with admin_connection() as admin:
            ensure_login_role(admin, user, "test-only-password")
        app = psycopg.conninfo.make_conninfo(
            libpq_url(url), user=user, password="test-only-password"
        )
        yield libpq_url(url), app
    finally:
        drop_database(url)
        with admin_connection() as admin:
            admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(user)))


def people(tenant: str, ids: list[int]) -> dict[str, TableRows]:
    rows = [(tenant, i, 8532, 1970, 0, 0, f"MRN{i}") for i in ids]
    deaths = [(tenant, ids[0], date(2025, 1, 1))]
    return {"person": TableRows(PERSON, rows), "death": TableRows(DEATH, deaths)}


def counts(conn: Conn, tenant: str | None) -> tuple[int, int]:
    with conn.transaction():
        if tenant:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        person = conn.execute("SELECT count(*) FROM cdm.person").fetchone()
        audit = conn.execute("SELECT count(*) FROM app.audit_log").fetchone()
    assert person is not None and audit is not None
    return int(str(person[0])), int(str(audit[0]))


def test_reload_replaces_rows_and_tenants_stay_isolated(urls: tuple[str, str]) -> None:
    _, app = urls
    with psycopg.connect(app, autocommit=True) as conn:
        assert load_tenant(conn, "center_a", people("center_a", [1, 2]), "b1") == {
            "person": 2,
            "death": 1,
        }
        load_tenant(conn, "center_a", people("center_a", [1, 2, 3]), "b2")  # re-run
        load_tenant(conn, "center_b", people("center_b", [9]), "b2")
        assert counts(conn, "center_a") == (3, 4)  # no duplicates; 2 audit rows per load
        assert counts(conn, "center_b") == (1, 2)
        assert counts(conn, None) == (0, 0)  # no tenant set: nothing visible


def test_rows_for_another_tenant_are_rejected_by_rls(urls: tuple[str, str]) -> None:
    _, app = urls
    with psycopg.connect(app, autocommit=True) as conn:
        load_tenant(conn, "center_b", people("center_b", [20]), "b3")
        sneaky = {"person": TableRows(PERSON, [("center_b", 30, 8532, 1970, 0, 0, "X")])}
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            load_tenant(conn, "center_a", sneaky, "b4")
        assert counts(conn, "center_b")[0] == 1  # the failed load changed nothing


def test_superuser_connection_is_refused(urls: tuple[str, str]) -> None:
    owner, _ = urls
    with psycopg.connect(owner, autocommit=True) as conn, pytest.raises(UnsafeLoadError):
        load_tenant(conn, "center_a", people("center_a", [1]), "b5")
