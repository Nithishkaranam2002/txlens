"""Load one tenant's gold rows into Postgres, as the non-superuser app role.

OMOP tables go to `cdm`; the patient identifiers (MRN, name, birth date) go to
`app.patient_identity`, and the identifiers replaced in de-identified notes to
`app.note_phi_span`. Only the clinician API path reads those two.

Row-Level Security stays on: the session is the app login (never a superuser, never
BYPASSRLS) with `app.tenant_id` set. Postgres does not allow COPY FROM into a table with
RLS, so each table is COPYed into a temporary staging table first and then moved with
INSERT ... SELECT, which passes the RLS WITH CHECK policy.

Everything for one tenant is one transaction: delete the tenant's rows, insert the new ones,
check the counts, restore the biopsy results extracted from pathology reports, and write one
audit_log row per step. A re-run therefore replaces rather than duplicates, and a failure
leaves the previous load in place.
"""

import os
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from psycopg.types.json import Jsonb

Connection = psycopg.Connection[tuple[object, ...]]
# Parents before children (foreign keys between clinical tables); deleted in reverse.
LOAD_ORDER = (
    "person",
    "observation_period",
    "visit_occurrence",
    "condition_occurrence",
    "observation",
    "drug_exposure",
    "measurement",
    "procedure_occurrence",
    "death",
    "note",
    "cdm_source",
    "patient_identity",
    "note_phi_span",
    "mapping_gap",
)
# Tables outside the OMOP schema (mapping_gap: condition texts for the review queue).
APP_TABLES = {"patient_identity", "note_phi_span", "mapping_gap"}
# Biopsy extraction and note NLP results live in app tables (their workflows' source of truth)
# and are copied back into cdm after every reload.
BIOPSY_RESULTS = "biopsy_results"
NOTE_NLP_RESULTS = "note_nlp_results"
# cdm tables holding only rows copied from app tables: emptied first (note_nlp refers to note).
APP_COPY_TABLES = ("note_nlp",)
# Ids from here up belong to rows the backend copies back (extraction results), not to the
# pipeline: tenant id ranges end below it (20 tenants x 100,000,000).
FIRST_APP_ROW_ID = 2_100_000_000


class UnsafeLoadError(RuntimeError):
    """The connection could bypass Row-Level Security."""


@dataclass(frozen=True)
class TableRows:
    """Rows for one table, in `columns` order (tenant_id first)."""

    columns: Sequence[str]
    rows: Iterable[Sequence[object]]


def database_url() -> str:
    """APP_DATABASE_URL (the app login), with host/port swapped when running in compose."""
    url = os.environ.get("APP_DATABASE_URL", "").replace("postgresql+psycopg://", "postgresql://")
    if not url:
        raise SystemExit("Set APP_DATABASE_URL (the non-superuser app login) to load Postgres.")
    overrides = {
        key: os.environ[env]
        for key, env in (("host", "TXLENS_DB_HOST"), ("port", "TXLENS_DB_PORT"))
        if os.environ.get(env)
    }
    return make_conninfo(url, **overrides)


def check_role_is_rls_bound(conn: Connection) -> None:
    """Refuse superusers and BYPASSRLS roles: RLS must apply to the load."""
    row = conn.execute(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    if row is None or row[0] or row[1]:
        raise UnsafeLoadError("load with the app login role (not a superuser, no BYPASSRLS)")


def schema_of(table: str) -> str:
    """'app' for our own tables, 'cdm' for OMOP ones."""
    return "app" if table in APP_TABLES else "cdm"


def target(table: str) -> sql.Composed:
    """The schema-qualified table name."""
    return sql.SQL("{}.{}").format(sql.Identifier(schema_of(table)), sql.Identifier(table))


def copy_into_stage(conn: Connection, table: str, data: TableRows) -> int:
    """COPY rows into a temporary table shaped like the target; returns the row count."""
    stage = sql.Identifier(f"stage_{table}")
    conn.execute(
        sql.SQL("CREATE TEMP TABLE {} (LIKE {} INCLUDING DEFAULTS) ON COMMIT DROP").format(
            stage, target(table)
        )
    )
    columns = sql.SQL(", ").join(map(sql.Identifier, data.columns))
    count = 0
    with conn.cursor().copy(sql.SQL("COPY {} ({}) FROM STDIN").format(stage, columns)) as copy:
        for row in data.rows:
            copy.write_row(row)
            count += 1
    return count


def move_from_stage(conn: Connection, table: str, columns: Sequence[str]) -> None:
    """INSERT ... SELECT from the stage: this is where RLS WITH CHECK applies."""
    names = sql.SQL(", ").join(map(sql.Identifier, columns))
    conn.execute(
        sql.SQL("INSERT INTO {} ({}) SELECT {} FROM {}").format(
            target(table), names, names, sql.Identifier(f"stage_{table}")
        )
    )


def audit(conn: Connection, table: str, batch_id: str, deleted: int, inserted: int) -> None:
    """One append-only audit row per table write."""
    conn.execute(
        "INSERT INTO app.audit_log (action, object_type, object_id, details) "
        "VALUES ('pipeline_load', %s, %s, %s)",
        (
            f"{schema_of(table)}.{table}",
            batch_id,
            Jsonb({"deleted": deleted, "inserted": inserted}),
        ),
    )


def load_tenant(
    conn: Connection, tenant_id: str, tables: dict[str, TableRows], batch_id: str
) -> dict[str, int]:
    """Replace one tenant's rows in every table, in one transaction; returns rows inserted."""
    check_role_is_rls_bound(conn)
    inserted: dict[str, int] = {}
    with conn.transaction():
        conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))
        deleted = {}
        for table in (*APP_COPY_TABLES, *reversed(LOAD_ORDER)):
            result = conn.execute(sql.SQL("DELETE FROM {}").format(target(table)))
            deleted[table] = result.rowcount
        for table in LOAD_ORDER:
            if table not in tables:
                continue
            inserted[table] = copy_into_stage(conn, table, tables[table])
            move_from_stage(conn, table, tables[table].columns)
            stored = conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(target(table))
            ).fetchone()
            if stored is None or stored[0] != inserted[table]:
                raise RuntimeError(f"{table}: {stored} rows stored, {inserted[table]} sent")
            audit(conn, table, batch_id, deleted[table], inserted[table])
        for name, function in ((BIOPSY_RESULTS, "load_biopsy_results"),
                               (NOTE_NLP_RESULTS, "load_note_nlp_results")):  # fmt: skip
            inserted[name] = restore_app_results(conn, function)
            audit(conn, name, batch_id, 0, inserted[name])
    return inserted


def restore_app_results(conn: Connection, function: str) -> int:
    """Copy app-table results (biopsies, note NLP) back into cdm after the reload removed them."""
    query = sql.SQL("SELECT app.{}()").format(sql.Identifier(function))
    row = conn.execute(query).fetchone()
    return int(str(row[0])) if row else 0
