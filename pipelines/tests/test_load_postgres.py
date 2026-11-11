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
          "ethnicity_concept_id"]  # fmt: skip
DEATH = ["tenant_id", "person_id", "death_date"]
IDENTITY = ["tenant_id", "person_id", "mrn", "given_name", "family_name", "birth_date"]
PROCEDURE = ["tenant_id", "procedure_occurrence_id", "person_id", "procedure_concept_id",
             "procedure_date", "procedure_type_concept_id"]  # fmt: skip
DOCUMENT = "00000000-0000-0000-0000-00000000d0c1"
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
    rows = [(tenant, i, 8532, 1970, 0, 0) for i in ids]
    deaths = [(tenant, ids[0], date(2025, 1, 1))]
    identity = [(tenant, i, f"MRN{i}", "Given", "Family", date(1970, 1, 2)) for i in ids]
    return {
        "person": TableRows(PERSON, rows),
        "death": TableRows(DEATH, deaths),
        "patient_identity": TableRows(IDENTITY, identity),
    }


def counts(conn: Conn, tenant: str | None) -> tuple[int, int, int]:
    """(persons, identity rows, audit rows) visible to the tenant."""
    with conn.transaction():
        if tenant:
            conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant,))
        found = [
            conn.execute(f"SELECT count(*) FROM {table}").fetchone()
            for table in ("cdm.person", "app.patient_identity", "app.audit_log")
        ]
    persons, identities, audits = (int(str(row[0])) if row else -1 for row in found)
    return persons, identities, audits


def test_reload_replaces_rows_and_tenants_stay_isolated(urls: tuple[str, str]) -> None:
    _, app = urls
    with psycopg.connect(app, autocommit=True) as conn:
        assert load_tenant(conn, "center_a", people("center_a", [1, 2]), "b1") == {
            "person": 2,
            "death": 1,
            "patient_identity": 2,
            "biopsy_results": 0,
        }
        load_tenant(conn, "center_a", people("center_a", [1, 2, 3]), "b2")  # re-run
        load_tenant(conn, "center_b", people("center_b", [9]), "b2")
        assert counts(conn, "center_a") == (3, 3, 8)  # no duplicates; 4 audit rows per load
        assert counts(conn, "center_b") == (1, 1, 4)
        assert counts(conn, None) == (0, 0, 0)  # no tenant set: nothing visible


def test_rows_for_another_tenant_are_rejected_by_rls(urls: tuple[str, str]) -> None:
    _, app = urls
    with psycopg.connect(app, autocommit=True) as conn:
        load_tenant(conn, "center_b", people("center_b", [20]), "b3")
        sneaky = {"person": TableRows(PERSON, [("center_b", 30, 8532, 1970, 0, 0)])}
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            load_tenant(conn, "center_a", sneaky, "b4")
        assert counts(conn, "center_b")[0] == 1  # the failed load changed nothing


def test_identity_rows_for_another_tenant_are_rejected_by_rls(urls: tuple[str, str]) -> None:
    _, app = urls
    with psycopg.connect(app, autocommit=True) as conn:
        tables = people("center_a", [40])
        tables["patient_identity"] = TableRows(
            IDENTITY, [("center_b", 40, "MRN40", None, None, date(1970, 1, 2))]
        )
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            load_tenant(conn, "center_a", tables, "b6")


def test_superuser_connection_is_refused(urls: tuple[str, str]) -> None:
    owner, _ = urls
    with psycopg.connect(owner, autocommit=True) as conn, pytest.raises(UnsafeLoadError):
        load_tenant(conn, "center_a", people("center_a", [1]), "b5")


def add_approved_biopsy_result(owner: str) -> None:
    """An approved document for center_a person 50's biopsy, with one TCMR condition row."""
    with psycopg.connect(owner, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.document (document_id, tenant_id, sha256, source_path, workflow_id, "
            "organ, status, person_id, procedure_occurrence_id) VALUES "
            "(%s, 'center_a', repeat('a', 64), 'x.pdf', 'wf', 'kidney', 'approved', 50, 500)",
            (DOCUMENT,),
        )
        conn.execute(
            "INSERT INTO app.extracted_field (tenant_id, document_id, field, value, "
            "passes_agree, confidence) VALUES ('center_a', %s, 'diagnosis', 'TCMR', true, 1)",
            (DOCUMENT,),
        )
        conn.execute(
            "INSERT INTO app.document_cdm_row (tenant_id, document_id, field, cdm_table, "
            "concept_id, source_concept_id, source_value) VALUES "
            "('center_a', %s, 'diagnosis', 'condition_occurrence', 4127550, 0, 'TCMR IA')",
            (DOCUMENT,),
        )


def test_reviewed_biopsy_results_survive_a_reload(urls: tuple[str, str]) -> None:
    owner, app = urls
    tables = people("center_a", [50]) | {
        "procedure_occurrence": TableRows(
            PROCEDURE, [("center_a", 500, 50, 4322471, date(2024, 5, 6), 32817)]
        )
    }
    with psycopg.connect(app, autocommit=True) as conn:
        load_tenant(conn, "center_a", tables, "b7")
    add_approved_biopsy_result(owner)
    for batch in ("b8", "b9"):  # every reload restores the row, never duplicates it
        with psycopg.connect(app, autocommit=True) as conn:
            assert load_tenant(conn, "center_a", tables, batch)["biopsy_results"] == 1
    with psycopg.connect(owner) as conn:
        rows = conn.execute(
            "SELECT person_id, condition_concept_id, condition_start_date, "
            "condition_type_concept_id, condition_source_value FROM cdm.condition_occurrence "
            "WHERE condition_occurrence_id >= 2100000000"
        ).fetchall()
    assert rows == [(50, 4127550, date(2024, 5, 6), 32858, "TCMR IA")]
