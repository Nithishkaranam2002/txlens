"""Database fixtures: a throwaway database, migrated to head, per test session.

DB tests are skipped when PostgreSQL is not reachable, unless TXLENS_REQUIRE_DB=1 (set in CI)
makes that a failure, so CI can never pass by silently skipping them.
"""

from collections.abc import Callable, Iterator

import pytest
from db_testutils import SEED_ORDER, TENANTS, create_database, drop_database, migrate, seed_row
from sqlalchemy import Connection, Engine, create_engine, pool, text


@pytest.fixture(scope="session")
def database_url() -> Iterator[str]:
    """A fresh database migrated to head; dropped after the session."""
    url = create_database("main")
    try:
        migrate(url)
        yield url
    finally:
        drop_database(url)


@pytest.fixture(scope="session")
def engine(database_url: str) -> Iterator[Engine]:
    """Engine connected as the owner (a superuser locally and in CI)."""
    eng = create_engine(database_url, poolclass=pool.NullPool)
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine: Engine) -> Iterator[Connection]:
    """Owner connection in a transaction seeded with one row per clinical table per tenant.

    Everything is rolled back afterwards, so tests never see each other's data.
    """
    with engine.connect() as conn:
        transaction = conn.begin()
        for tenant in TENANTS:
            conn.execute(
                text("INSERT INTO app.tenant (tenant_id, name) VALUES (:t, :n)"),
                {"t": tenant, "n": f"Test {tenant}"},
            )
        for table in SEED_ORDER:
            for tenant, row_id in TENANTS.items():
                seed_row(conn, table, tenant, row_id)
        yield conn
        transaction.rollback()


AsApp = Callable[[Connection, str | None], None]


def act_as_app(conn: Connection, tenant: str | None) -> None:
    """Switch this transaction to the non-superuser app role, scoped to a tenant (or none)."""
    conn.execute(text("SET LOCAL ROLE txlens_app"))
    if tenant is not None:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})


@pytest.fixture
def as_app() -> AsApp:
    """`as_app(conn, tenant)`: continue the transaction as txlens_app for that tenant."""
    return act_as_app
