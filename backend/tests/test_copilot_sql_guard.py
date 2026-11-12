"""The copilot SQL guard: aggregate-only SELECTs over the copilot views, nothing else."""

import re

import pytest

from txlens_backend.copilot.sql_guard import MAX_ROWS, SqlRejectedError, check

N = "count(DISTINCT person_id)"
P = "copilot.patient"

ACCEPTED = [
    f"SELECT {N} FROM {P}",
    f"SELECT organ, {N} AS n FROM {P} GROUP BY organ",
    f"SELECT organ, donor_type, {N} FROM {P} WHERE age_at_transplant >= 18 "
    "GROUP BY organ, donor_type ORDER BY organ",
    f"SELECT transplant_year, {N}, count(*) FROM {P} GROUP BY transplant_year",
    f"SELECT {N}, avg(age_at_transplant), stddev(age_at_transplant) FROM {P}",
    "SELECT count(DISTINCT m.person_id) n, round(avg(m.value_as_number), 2) mean, "
    "percentile_cont(0.5) WITHIN GROUP (ORDER BY m.value_as_number) median "
    "FROM copilot.measurement m WHERE m.lab = 'dd_cfdna' AND m.day BETWEEN 0 AND 365",
    "SELECT p.organ, count(DISTINCT m.person_id) FROM copilot.measurement m "
    "JOIN copilot.patient p ON p.person_id = m.person_id WHERE m.lab IN ('egfr', 'creatinine') "
    "GROUP BY p.organ",
    "WITH k AS (SELECT person_id FROM copilot.patient WHERE organ = 'kidney') "
    "SELECT count(DISTINCT c.person_id) FROM copilot.condition c "
    "JOIN k ON k.person_id = c.person_id",
    "SELECT v.concept_name, count(DISTINCT c.person_id) FROM copilot.condition c "
    "JOIN vocab.concept v ON v.concept_id = c.condition_concept_id GROUP BY v.concept_name",
    "SELECT count(DISTINCT c.person_id) FROM copilot.condition c WHERE c.condition_concept_id IN "
    "(SELECT descendant_concept_id FROM vocab.concept_ancestor WHERE ancestor_concept_id = 1)",
    f"SELECT {N} FROM {P} p WHERE EXISTS (SELECT 1 FROM copilot.measurement m "
    "WHERE m.person_id = p.person_id AND m.lab = 'dsa_mfi' AND m.value_as_number > 1000)",
    f"SELECT sex, {N} FROM {P} WHERE graft_loss AND graft_loss_day <= 365 GROUP BY sex LIMIT 10",
    f"SELECT {N} FROM {P} WHERE CASE WHEN died THEN death_day ELSE 0 END > 30",
]

REJECTED = {
    # not a single SELECT
    "DROP TABLE cdm.person": "only SELECT",
    "DELETE FROM copilot.patient": "only SELECT",
    "UPDATE copilot.patient SET organ = 'x'": "only SELECT",
    "INSERT INTO app.audit_log (action) VALUES ('x')": "only SELECT",
    "TRUNCATE app.audit_log": "only SELECT",
    "CREATE TABLE t (a int)": "only SELECT",
    "GRANT ALL ON app.patient_identity TO PUBLIC": "only SELECT",
    "SET ROLE txlens_app": "only SELECT",
    "COPY (SELECT 1) TO '/tmp/x'": "only SELECT",
    f"SELECT {N} FROM {P}; DROP TABLE cdm.person": "exactly one statement",
    f"SELECT {N} FROM {P}; SELECT 1": "exactly one statement",
    "": "exactly one statement",
    "SELEC count(*) FROM copilot.patient": "",
    f"SELECT {N} FROM {P} UNION SELECT 1": "only SELECT",
    f"SELECT {N} FROM {P} WHERE person_id IN (SELECT person_id FROM {P} EXCEPT "
    f"SELECT person_id FROM {P})": "UNION, INTERSECT and EXCEPT",
    # tables outside the allow-list
    "SELECT count(*) FROM app.patient_identity": "not allowed",
    "SELECT count(*) FROM app.note_phi_span": "not allowed",
    "SELECT count(*) FROM app.app_user": "not allowed",
    "SELECT count(DISTINCT person_id) FROM cdm.person": "not allowed",
    "SELECT count(*) FROM cdm.note": "not allowed",
    "SELECT count(*) FROM pg_catalog.pg_roles": "not allowed",
    "SELECT count(*) FROM information_schema.tables": "not allowed",
    "SELECT count(*) FROM patient": "not allowed",
    "SELECT count(*) FROM vocab.concept_synonym": "not allowed",
    f"SELECT {N} FROM {P} p JOIN app.patient_identity i USING (person_id)": "not allowed",
    f"SELECT {N} FROM {P} WHERE person_id IN (SELECT 1 FROM app.cohort)": "not allowed",
    "SELECT count(*) FROM generate_series(1, 10)": "",
    # columns outside the views
    f"SELECT {N} FROM {P} WHERE transplant_date > '2024-01-01'": "could not be resolved",
    f"SELECT {N} FROM {P} WHERE mrn = '123'": "could not be resolved",
    "SELECT count(*) FROM vocab.concept WHERE invalid_reason IS NULL": "could not be resolved",
    # person-level output
    f"SELECT person_id FROM {P}": "neither an aggregate nor a GROUP BY",
    f"SELECT * FROM {P}": "SELECT *",
    f"SELECT person_id, {N} FROM {P} GROUP BY person_id": "one row per record",
    f"SELECT age_at_transplant FROM {P}": "neither an aggregate nor a GROUP BY",
    f"SELECT {N}, (SELECT age_at_transplant FROM {P} LIMIT 1) FROM {P}": "neither an aggregate",
    f"SELECT DISTINCT organ FROM {P}": "neither an aggregate",
    f"SELECT organ, {N} FROM {P}": "neither an aggregate nor a GROUP BY",
    f"SELECT {N} FROM {P} GROUP BY age_at_transplant / 10": "plain columns",
    "SELECT value_as_number, count(DISTINCT person_id) FROM copilot.measurement "
    "GROUP BY value_as_number": "individual values",
    f"SELECT age_at_transplant >= 18 AS adult, {N} FROM {P} GROUP BY 1": "plain columns",
    f"SELECT {N} + age_at_transplant FROM {P} GROUP BY age_at_transplant": "neither an aggregate",
    # aggregates that leak individuals or hide counts
    f"SELECT max(age_at_transplant), {N} FROM {P}": "max",
    f"SELECT min(death_day), {N} FROM {P}": "min",
    f"SELECT sum(CASE WHEN age_at_transplant = 18 THEN 1 ELSE 0 END), {N} FROM {P}": "sum",
    f"SELECT avg(CASE WHEN age_at_transplant = 18 THEN 1 ELSE 0 END), {N} FROM {P}": "plain column",
    f"SELECT avg(age_at_transplant * 1), {N} FROM {P}": "plain column",
    f"SELECT count(death_day), {N} FROM {P}": "count only patients",
    f"SELECT {N} FILTER (WHERE age_at_transplant = 18) FROM {P}": "filter",
    f"SELECT string_agg(sex, ','), {N} FROM {P}": "not allowed",
    f"SELECT array_agg(person_id), {N} FROM {P}": "not allowed",
    f"SELECT percentile_disc(0.5) WITHIN GROUP (ORDER BY age_at_transplant), {N} FROM {P}": "",
    f"SELECT mode() WITHIN GROUP (ORDER BY age_at_transplant), {N} FROM {P}": "",
    f"SELECT count(*) FROM {P}": "include count(DISTINCT person_id)",
    f"SELECT avg(age_at_transplant) FROM {P}": "include count(DISTINCT person_id)",
    f"WITH a AS (SELECT count(*) AS n FROM {P} WHERE age_at_transplant = 18) "
    f"SELECT a.n, {N} FROM a, {P} GROUP BY a.n": "outermost SELECT",
    f"SELECT {N} FROM {P} WHERE 9 < (SELECT avg(death_day) FROM {P})": "outermost SELECT",
    f"SELECT row_number() OVER (), {N} FROM {P}": "not allowed",
    f"SELECT {N}, count(*) OVER () FROM {P}": "not allowed",
    # functions, locks, escapes from the session
    f"SELECT set_config('app.tenant_id', 'center_b', false), {N} FROM {P}": "SET_CONFIG",
    f"SELECT {N} FROM {P} WHERE current_setting('app.tenant_id') = 'center_a'": "not allowed",
    f"SELECT {N} FROM {P} WHERE pg_sleep(30) IS NULL": "PG_SLEEP",
    f"SELECT pg_read_file('/etc/passwd'), {N} FROM {P}": "not allowed",
    f"SELECT {N} FROM {P} WHERE dblink('host=x', 'select 1') IS NULL": "not allowed",
    f"SELECT lo_import('/etc/passwd'), {N} FROM {P}": "not allowed",
    f"SELECT {N} FROM {P} FOR UPDATE": "not allowed",
    f"SELECT {N} INTO TEMP t FROM {P}": "not allowed",
    f"SELECT {N} FROM {P}, LATERAL (SELECT 1) x": "",
    f"SELECT {N} FROM {P} WHERE organ = $1": "not allowed",
    f"SELECT {N} FROM {P} WHERE organ::regclass IS NULL": "casts to",
    f"SELECT {N} FROM {P} WHERE organ = chr(107)": "not allowed",
    f"SELECT {N} FROM {P} WHERE version() LIKE '%'": "not allowed",
}


@pytest.mark.parametrize("sql", ACCEPTED)
def test_aggregate_queries_over_the_views_are_accepted(sql: str) -> None:
    checked = check(sql)
    assert any(o.kind == "n" for o in checked.outputs)
    assert "LIMIT" in checked.sql


@pytest.mark.parametrize(("sql", "reason"), REJECTED.items())
def test_anything_else_is_rejected(sql: str, reason: str) -> None:
    with pytest.raises(SqlRejectedError, match=f"(?i){re.escape(reason)}" if reason else None):
        check(sql)


def test_the_suite_covers_at_least_sixty_attacks() -> None:
    assert len(REJECTED) >= 60


@pytest.mark.parametrize(("limit", "forced"), [("", MAX_ROWS), ("LIMIT 10", 10),
                                               ("LIMIT 100000", MAX_ROWS)])  # fmt: skip
def test_limit_is_forced(limit: str, forced: int) -> None:
    checked = check(f"SELECT organ, {N} FROM {P} GROUP BY organ {limit}")
    assert checked.sql.endswith(f"LIMIT {forced}")


def test_outputs_say_what_each_column_is() -> None:
    checked = check(f"SELECT organ, {N} AS n, count(*) AS rows, avg(age_at_transplant) AS age "
                    f"FROM {P} GROUP BY organ")  # fmt: skip
    assert [(o.name, o.kind) for o in checked.outputs] == [
        ("organ", "group"), ("n", "n"), ("rows", "count"), ("age", "value")]  # fmt: skip
    assert checked.outputs[0].dim == "copilot.patient.organ"


def test_filters_become_constraints_whatever_the_alias() -> None:
    a = check(f"SELECT {N} FROM {P} p WHERE p.organ = 'kidney' AND p.age_at_transplant >= 18")
    b = check(f"SELECT count(DISTINCT x.person_id) FROM {P} x WHERE 18 <= x.age_at_transplant "
              "AND x.organ = 'kidney'")  # fmt: skip
    assert a.base == b.base
    assert (
        a.constraints
        == b.constraints
        == {
            "copilot.patient.organ": {"eq": "kidney"},
            "copilot.patient.age_at_transplant": {"range": [18.0, True, None, True]},
        }
    )


def test_filters_in_ctes_and_join_conditions_count_too() -> None:
    plain = check(f"SELECT count(DISTINCT c.person_id) FROM copilot.condition c JOIN {P} p "
                  "ON p.person_id = c.person_id WHERE p.age_at_transplant >= 19")  # fmt: skip
    in_join = check(f"SELECT count(DISTINCT c.person_id) FROM copilot.condition c JOIN {P} p "
                    "ON p.person_id = c.person_id AND p.age_at_transplant >= 19")  # fmt: skip
    assert plain.base == in_join.base and plain.constraints == in_join.constraints
    cte = check(f"WITH a AS (SELECT person_id FROM {P} WHERE age_at_transplant >= 19) "
                "SELECT count(DISTINCT a.person_id) FROM a")  # fmt: skip
    assert cte.constraints == {
        "copilot.patient.age_at_transplant": {"range": [19.0, True, None, True]}}  # fmt: skip


def test_group_by_columns_are_not_part_of_the_base() -> None:
    grouped = check(f"SELECT organ, {N} FROM {P} GROUP BY organ")
    filtered = check(f"SELECT {N} FROM {P} WHERE organ = 'kidney'")
    assert grouped.base == filtered.base
