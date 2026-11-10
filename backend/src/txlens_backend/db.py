"""Database access. App queries always run inside a tenant-scoped transaction.

Row-Level Security policies compare each row's `tenant_id` with the session setting
`app.tenant_id`. `tenant_transaction` sets it with `set_config(..., is_local => true)`, so it
lasts only for that transaction and never leaks to the next user of a pooled connection.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Connection, Engine, create_engine, text

TENANT_SETTING = "app.tenant_id"
APP_ROLE = "txlens_app"


def make_engine(url: str) -> Engine:
    """A pooled engine; `pool_pre_ping` drops dead connections after a database restart."""
    return create_engine(url, pool_pre_ping=True)


def set_tenant(conn: Connection, tenant_id: str) -> None:
    """Scope the current transaction to one tenant."""
    conn.execute(
        text("SELECT set_config(:name, :tenant, true)"),
        {"name": TENANT_SETTING, "tenant": tenant_id},
    )


@contextmanager
def tenant_transaction(engine: Engine, tenant_id: str) -> Iterator[Connection]:
    """A transaction that can only see and write rows of `tenant_id` (commits on success)."""
    with engine.begin() as conn:
        set_tenant(conn, tenant_id)
        yield conn
