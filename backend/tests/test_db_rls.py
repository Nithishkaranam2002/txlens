"""Row-Level Security: each tenant sees and writes only its own clinical rows."""

from collections.abc import Callable

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, ProgrammingError

from txlens_omop import ddl

pytestmark = pytest.mark.db
AsApp = Callable[[Connection, str | None], None]


def visible(conn: Connection, table: str) -> list[str]:
    """tenant_id of every row the current role can see."""
    return list(conn.execute(text(f"SELECT tenant_id FROM cdm.{table}")).scalars())


@pytest.mark.parametrize("table", ddl.CLINICAL_TABLES)
def test_each_tenant_sees_only_its_own_rows(db: Connection, as_app: AsApp, table: str) -> None:
    as_app(db, "center_a")
    assert visible(db, table) == ["center_a"]
    as_app(db, "center_b")
    assert visible(db, table) == ["center_b"]


@pytest.mark.parametrize("table", ddl.CLINICAL_TABLES)
def test_no_tenant_set_means_no_rows(db: Connection, as_app: AsApp, table: str) -> None:
    as_app(db, None)
    assert visible(db, table) == []


def test_empty_tenant_setting_means_no_rows(db: Connection, as_app: AsApp) -> None:
    as_app(db, "")
    assert visible(db, "person") == []


def test_direct_select_of_other_tenants_row_returns_nothing(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    other = db.execute(text("SELECT * FROM cdm.person WHERE person_id = 2")).all()
    assert other == []
    count = db.execute(text("SELECT count(*) FROM cdm.measurement WHERE person_id = 2")).scalar()
    assert count == 0


def test_insert_defaults_to_current_tenant(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_b")
    db.execute(
        text(
            "INSERT INTO cdm.person (person_id, gender_concept_id, year_of_birth, "
            "race_concept_id, ethnicity_concept_id) VALUES (10, 0, 1970, 0, 0)"
        )
    )
    tenant = db.execute(text("SELECT tenant_id FROM cdm.person WHERE person_id = 10")).scalar()
    assert tenant == "center_b"


def test_cannot_insert_rows_for_another_tenant(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    with pytest.raises(ProgrammingError, match="row-level security"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO cdm.person (person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id, tenant_id) "
                "VALUES (11, 0, 1970, 0, 0, 'center_b')"
            )
        )


def test_insert_without_tenant_fails(db: Connection, as_app: AsApp) -> None:
    as_app(db, None)
    with pytest.raises(DBAPIError, match=r"row-level security|tenant_id"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO cdm.person (person_id, gender_concept_id, year_of_birth, "
                "race_concept_id, ethnicity_concept_id) VALUES (12, 0, 1970, 0, 0)"
            )
        )


def test_cannot_move_a_row_to_another_tenant(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    with pytest.raises(ProgrammingError, match="row-level security"), db.begin_nested():
        db.execute(text("UPDATE cdm.person SET tenant_id = 'center_b' WHERE person_id = 1"))


def test_cannot_update_or_delete_other_tenants_rows(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    updated = db.execute(text("UPDATE cdm.person SET year_of_birth = 1900 WHERE person_id = 2"))
    deleted = db.execute(text("DELETE FROM cdm.measurement WHERE person_id = 2"))
    assert updated.rowcount == 0
    assert deleted.rowcount == 0


def test_app_role_cannot_write_the_shared_vocabulary(db: Connection, as_app: AsApp) -> None:
    as_app(db, "center_a")
    assert db.execute(text("SELECT count(*) FROM vocab.concept")).scalar() == 0
    with pytest.raises(ProgrammingError, match="permission denied"), db.begin_nested():
        db.execute(
            text(
                "INSERT INTO vocab.domain (domain_id, domain_name, domain_concept_id) "
                "VALUES ('X', 'X', 0)"
            )
        )


def test_policies_are_enabled_and_forced_on_every_clinical_table(db: Connection) -> None:
    rows = db.execute(
        text(
            "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, count(p.polname) "
            "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "LEFT JOIN pg_policy p ON p.polrelid = c.oid "
            "WHERE n.nspname = 'cdm' AND c.relkind = 'r' "
            "GROUP BY c.relname, c.relrowsecurity, c.relforcerowsecurity"
        )
    ).all()
    assert {r[0] for r in rows} == set(ddl.CLINICAL_TABLES)
    for name, enabled, forced, policies in rows:
        assert (enabled, forced, policies) == (True, True, 1), name


def test_app_role_is_not_privileged(db: Connection) -> None:
    row = db.execute(
        text(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles WHERE rolname = 'txlens_app'"
        )
    ).one()
    assert tuple(row) == (False, False, False)
