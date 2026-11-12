"""Running copilot SQL: ground truth, tenants, suppression, differencing, and the DB role."""

from collections import Counter
from collections.abc import Iterator

import pytest
from api_dataset import Dataset
from sqlalchemy import Engine, text
from sqlalchemy.exc import DBAPIError

from txlens_backend.copilot import sql_exec
from txlens_backend.copilot.sql_guard import Checked, Output, check
from txlens_backend.db import tenant_transaction
from txlens_backend.services.disclosure import WITHHELD, Disclosure

pytestmark = pytest.mark.db
TENANT = "center_a"


def age(p: dict[str, str]) -> int:
    return int(p["transplant_date"][:4]) - int(p["birth_date"][:4])


@pytest.fixture
def researcher(api_owner: Engine, api_app: object) -> Iterator[str]:
    """Center A's researcher, with no counts released yet (and none left behind)."""
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.released_count"))
        user_id = conn.execute(
            text("SELECT user_id::text FROM app.app_user WHERE username = :u"),
            {"u": f"researcher.{TENANT}"},
        ).scalar_one()
    yield str(user_id)
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.released_count"))


def run(engine: Engine, user_id: str, sql: str | Checked, tenant: str = TENANT) -> sql_exec.Table:
    """Check (unless given checked) and run SQL as this user, in its own committed session."""
    checked = check(sql) if isinstance(sql, str) else sql
    with tenant_transaction(engine, tenant) as conn:
        guard = Disclosure(conn, user_id, "test", threshold=11, window_days=30)
        return sql_exec.run(engine, tenant, checked, guard)


def raw(sql: str) -> Checked:
    """SQL that did NOT pass the guard, to test the database role on its own."""
    return Checked(sql, [Output("n", "n", cell="n")], base="raw")


def test_counts_match_ground_truth(
    api_app_engine: Engine, api_data: Dataset, researcher: str
) -> None:
    table = run(api_app_engine, researcher, "SELECT organ, count(DISTINCT person_id) AS n "
                "FROM copilot.patient GROUP BY organ ORDER BY organ")  # fmt: skip
    truth = Counter(p["organ"] for p in api_data.truth("patients") if p["tenant_id"] == TENANT)
    assert table.columns == ["organ", "n"]
    assert {organ: n for organ, n in table.rows} == {
        organ: n if n >= 11 else "<11" for organ, n in truth.items()}  # fmt: skip


def test_each_center_sees_only_its_patients(
    api_app_engine: Engine, api_data: Dataset, researcher: str
) -> None:
    sql = "SELECT count(DISTINCT person_id) AS n FROM copilot.patient"
    for tenant in ("center_a", "center_b"):
        expected = sum(p["tenant_id"] == tenant for p in api_data.truth("patients"))
        assert run(api_app_engine, researcher, sql, tenant).rows == [[expected]]


def test_small_cells_hide_every_aggregate_but_keep_the_group(
    api_app_engine: Engine, api_data: Dataset, researcher: str
) -> None:
    sql = ("SELECT age_at_transplant, count(DISTINCT person_id) AS n, avg(age_at_transplant) "
           "AS mean FROM copilot.patient GROUP BY age_at_transplant")  # fmt: skip
    table = run(api_app_engine, researcher, sql)
    ages = Counter(age(p) for p in api_data.truth("patients") if p["tenant_id"] == TENANT)
    shown = {row[0]: row[1:] for row in table.rows}
    small = [a for a, n in ages.items() if n < 11]
    assert small and all(shown[a] == ["<11", "<11"] for a in small)
    assert all(shown[a][0] == n for a, n in ages.items() if n >= 11)
    assert any("<11" in notice for notice in table.notices)


def test_nested_filters_are_withheld_across_sessions(
    api_app_engine: Engine, api_data: Dataset, researcher: str, api_owner: Engine
) -> None:
    """Two questions (two sessions): 'aged >= X' then 'aged >= X+1' would reveal the few aged X."""
    ages = Counter(age(p) for p in api_data.truth("patients") if p["tenant_id"] == TENANT)
    x = next(a for a, n in sorted(ages.items()) if 1 <= n <= 10)
    sql = "SELECT count(DISTINCT person_id) AS n FROM copilot.patient WHERE age_at_transplant >= {}"
    first = run(api_app_engine, researcher, sql.format(x))
    assert isinstance(first.rows[0][0], int)
    second = run(api_app_engine, researcher, sql.format(x + 1))
    assert second.rows == [[WITHHELD]]
    assert any("withheld" in notice for notice in second.notices)
    again = run(api_app_engine, researcher, sql.format(x))
    assert again.rows == first.rows  # the same count again reveals nothing new
    with api_owner.connect() as conn:  # the clinician of the same center starts afresh
        other = conn.execute(text("SELECT user_id::text FROM app.app_user WHERE username = "
                                  f"'clinician.{TENANT}'")).scalar_one()  # fmt: skip
    assert isinstance(run(api_app_engine, other, sql.format(x + 1)).rows[0][0], int)


def test_a_group_row_is_compared_with_a_filtered_count(
    api_app_engine: Engine, api_data: Dataset, researcher: str
) -> None:
    """GROUP BY values are filters too: 'by organ' then 'kidney and aged >= X+1'."""
    ages = Counter(age(p) for p in api_data.truth("patients")
                   if p["tenant_id"] == TENANT and p["organ"] == "kidney")  # fmt: skip
    youngest = min(ages)
    if not 1 <= ages[youngest] <= 10:
        pytest.skip("the youngest kidney age group is not small in this dataset")
    run(api_app_engine, researcher,
        "SELECT organ, count(DISTINCT person_id) FROM copilot.patient GROUP BY organ")  # fmt: skip
    narrowed = run(api_app_engine, researcher,
                   "SELECT count(DISTINCT person_id) FROM copilot.patient "
                   f"WHERE organ = 'kidney' AND age_at_transplant > {youngest}")  # fmt: skip
    assert narrowed.rows == [[WITHHELD]]


@pytest.mark.parametrize(
    ("sql", "error"),
    [
        ("SELECT count(*) AS n FROM app.patient_identity", "permission denied"),
        ("SELECT count(*) AS n FROM app.note_phi_span", "permission denied"),
        ("SELECT count(*) AS n FROM app.app_user", "permission denied"),
        ("SELECT count(*) AS n FROM cdm.person", "permission denied"),
        ("SELECT count(*) AS n FROM cdm.note", "permission denied"),
        ("SELECT count(*) AS n FROM vocab.concept_synonym", "permission denied"),
        ("SELECT count(*) AS n FROM set_config('app.tenant_id', 'center_b', true)",
         "permission denied for function set_config"),
        ("SELECT count(*) AS n FROM (SELECT 1) x WHERE "
         "set_config('role', 'txlens_app', true) IS NOT NULL", "permission denied"),
        ("SELECT count(*) AS n FROM (SELECT lo_create(0)) x", "read-only transaction"),
        # the query is wrapped as a subquery: no data-modifying CTE, no second statement
        ("WITH d AS (DELETE FROM copilot.patient RETURNING 1) SELECT count(*) AS n FROM d",
         "data-modifying statement"),
        ("SELECT count(*) AS n FROM copilot.patient; DELETE FROM app.audit_log", "syntax error"),
    ],
)  # fmt: skip
def test_the_database_role_stops_sql_that_skipped_the_guard(
    api_app_engine: Engine, researcher: str, sql: str, error: str
) -> None:
    with pytest.raises(DBAPIError, match=error):
        run(api_app_engine, researcher, raw(sql))


def test_slow_queries_time_out(api_app_engine: Engine, researcher: str) -> None:
    checked = raw("SELECT count(*) AS n FROM copilot.patient, pg_sleep(2)")
    with tenant_transaction(api_app_engine, TENANT) as conn:
        guard = Disclosure(conn, researcher, "test", threshold=11, window_days=30)
        with pytest.raises(DBAPIError, match="statement timeout"):
            sql_exec.run(api_app_engine, TENANT, checked, guard, timeout_ms=200)


def test_without_a_tenant_the_views_are_empty(api_app_engine: Engine) -> None:
    with api_app_engine.connect() as conn, conn.begin():
        conn.execute(text("SET LOCAL ROLE txlens_copilot"))
        assert conn.execute(text("SELECT count(*) FROM copilot.patient")).scalar_one() == 0
        assert conn.execute(text("SELECT count(*) FROM copilot.measurement")).scalar_one() == 0


def test_guard_schema_matches_the_views(api_owner: Engine) -> None:
    """The columns the guard allows are exactly the views' columns (no drift either way)."""
    from txlens_backend.copilot.sql_guard import SCHEMA

    with api_owner.connect() as conn:
        rows = conn.execute(text(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'copilot'")).all()  # fmt: skip
    found: dict[str, set[str]] = {}
    for table, column in rows:
        found.setdefault(table, set()).add(column)
    assert found == {t: set(cols) for t, cols in SCHEMA["copilot"].items()}
