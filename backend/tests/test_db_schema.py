"""The migrated database matches the pinned official OMOP DDL (plus our tenant extension)."""

import pytest
from sqlalchemy import Connection, text

from txlens_omop import ddl

pytestmark = pytest.mark.db

PG_TYPES = {
    "integer": "integer",
    "bigint": "bigint",
    "numeric": "numeric",
    "text": "text",
    "date": "date",
    "timestamp": "timestamp without time zone",
}


def expected_type(data_type: str) -> tuple[str, int | None]:
    """Postgres information_schema type (and max length) for an upstream DDL type."""
    if data_type.startswith("varchar("):
        return "character varying", int(data_type[8:-1])
    return PG_TYPES[data_type], None


def actual_columns(
    db: Connection, schema: str, table: str
) -> list[tuple[str, str, int | None, bool]]:
    rows = db.execute(
        text(
            "SELECT column_name, data_type, character_maximum_length, is_nullable = 'YES' "
            "FROM information_schema.columns WHERE table_schema = :s AND table_name = :t "
            "ORDER BY ordinal_position"
        ),
        {"s": schema, "t": table},
    ).all()
    return [(r[0], r[1], r[2], r[3]) for r in rows]


def test_tables_exist_in_the_right_schemas(db: Connection) -> None:
    rows = db.execute(
        text(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_schema IN ('cdm', 'vocab')"
        )
    ).all()
    assert {(r[0], r[1]) for r in rows} == {(t.schema, t.name) for t in ddl.tables().values()}


@pytest.mark.parametrize("table", sorted(ddl.tables()))
def test_columns_match_the_official_ddl(db: Connection, table: str) -> None:
    spec = ddl.tables()[table]
    expected = [(c.name, *expected_type(c.data_type), c.nullable) for c in spec.columns]
    if spec.schema == "cdm":
        expected.append(("tenant_id", "text", None, False))
    assert actual_columns(db, spec.schema, table) == expected


def test_primary_keys_match_the_official_ddl(db: Connection) -> None:
    rows = db.execute(
        text(
            "SELECT tc.table_name, string_agg(k.column_name, ',' ORDER BY k.ordinal_position) "
            "FROM information_schema.table_constraints tc "
            "JOIN information_schema.key_column_usage k "
            "  ON k.constraint_name = tc.constraint_name AND k.table_schema = tc.table_schema "
            "WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema IN ('cdm', 'vocab') "
            "GROUP BY tc.table_name"
        )
    ).all()
    actual = {r[0]: tuple(r[1].split(",")) for r in rows}
    expected = {name: t.primary_key for name, t in ddl.tables().items() if t.primary_key}
    assert actual == expected


def test_foreign_keys_are_clinical_plus_tenant_only(db: Connection) -> None:
    rows = db.execute(
        text(
            "SELECT conrelid::regclass::text, confrelid::regclass::text, conname "
            "FROM pg_constraint WHERE contype = 'f' "
            "AND connamespace IN ('cdm'::regnamespace, 'vocab'::regnamespace)"
        )
    ).all()
    official = {(f"cdm.{k.table}", f"cdm.{k.ref_table}", k.name) for k in ddl.foreign_keys()}
    clinical = {(r[0], r[1], r[2]) for r in rows if r[1] != "app.tenant"}
    to_tenant = {(r[0], r[1]) for r in rows if r[1] == "app.tenant"}
    assert clinical == official
    assert to_tenant == {(f"cdm.{t}", "app.tenant") for t in ddl.CLINICAL_TABLES}
    assert not [r for r in rows if r[1].startswith("vocab.")]


def test_official_and_extra_indexes_exist(db: Connection) -> None:
    names = set(db.execute(text("SELECT indexname FROM pg_indexes")).scalars())
    official = {statement.split()[2] for statement in ddl.render_indices()}
    assert official <= names
    extras = {"ix_measurement_person_concept_date", "ix_concept_vocabulary_code"}
    extras |= {f"ix_{t}_tenant" for t in ddl.CLINICAL_TABLES}
    assert extras <= names


def test_vocabulary_tables_are_shared(db: Connection) -> None:
    rows = db.execute(
        text(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'vocab' AND column_name = 'tenant_id'"
        )
    ).scalar()
    assert rows == 0
