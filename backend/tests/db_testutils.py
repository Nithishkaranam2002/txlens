"""Database helpers shared by the backend tests (a module, so tests can import it)."""

import os
from datetime import date, datetime
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text
from sqlalchemy.engine import make_url

from txlens_backend.config import get_settings
from txlens_backend.db_setup import libpq_url
from txlens_omop import ddl

ALEMBIC_INI = Path(__file__).parents[1] / "alembic.ini"
TENANTS = {"center_a": 1, "center_b": 2}  # tenant -> id used for its seeded rows
# Parents first, so foreign keys to person and note are satisfied.
SEED_ORDER = ("person", "note", *(t for t in ddl.CLINICAL_TABLES if t not in ("person", "note")))


def owner_url() -> str:
    """Owner (migration) URL from the environment."""
    return get_settings().database_url.get_secret_value()


def admin_connection() -> psycopg.Connection:
    """Autocommit connection to the owner's default database, or skip/fail if unreachable."""
    try:
        return psycopg.connect(libpq_url(owner_url()), autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as error:
        if os.environ.get("TXLENS_REQUIRE_DB") == "1":
            pytest.fail(f"PostgreSQL is required but not reachable: {error}")
        pytest.skip("PostgreSQL not reachable (run `make up`)")


def migrate(url: str, revision: str = "head", *, down: bool = False) -> None:
    """Run Alembic against a specific database."""
    config = Config(str(ALEMBIC_INI))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    if down:
        command.downgrade(config, revision)
    else:
        command.upgrade(config, revision)


def create_database(suffix: str) -> str:
    """Create an empty database and return its SQLAlchemy URL."""
    name = f"txlens_test_{suffix}_{os.getpid()}"
    with admin_connection() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{name}"')
    return make_url(owner_url()).set(database=name).render_as_string(hide_password=False)


def drop_database(url: str) -> None:
    """Drop a test database."""
    name = make_url(url).database
    with admin_connection() as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def dummy_value(data_type: str, row_id: int) -> object:
    """A valid value for a NOT NULL column of the given upstream type."""
    if data_type in ("integer", "bigint"):
        return row_id
    if data_type == "numeric":
        return 1.5
    if data_type == "date":
        return date(2024, 1, row_id)
    if data_type == "timestamp":
        return datetime(2024, 1, row_id, 8, 0)
    return "x"


def seed_row(conn: Connection, table: str, tenant: str, row_id: int) -> None:
    """Insert one row with only the NOT NULL columns filled, for one tenant."""
    columns = [c for c in ddl.tables()[table].columns if not c.nullable]
    values = {c.name: dummy_value(c.data_type, row_id) for c in columns} | {"tenant_id": tenant}
    names = ", ".join(f'"{name}"' for name in values)
    params = ", ".join(f":{name}" for name in values)
    conn.execute(text(f"INSERT INTO cdm.{table} ({names}) VALUES ({params})"), values)
