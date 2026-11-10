"""`make db-setup`: create (or update) the app's login role after migrations have run.

The login user named in APP_DATABASE_URL becomes a member of `txlens_app`, the non-superuser
role that the Row-Level Security policies apply to. Passwords come from `.env` only.
"""

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

from txlens_backend.config import get_settings
from txlens_backend.db import APP_ROLE


def libpq_url(url: str) -> str:
    """Turn a SQLAlchemy URL (`postgresql+psycopg://`) into a plain libpq one."""
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def ensure_login_role(conn: psycopg.Connection, user: str, password: str) -> None:
    """Create the login user if missing, set its password, and grant it the app role."""
    exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (user,)).fetchone()
    verb = sql.SQL("ALTER") if exists else sql.SQL("CREATE")
    conn.execute(
        sql.SQL("{} ROLE {} LOGIN NOSUPERUSER NOBYPASSRLS PASSWORD {}").format(
            verb, sql.Identifier(user), sql.Literal(password)
        )
    )
    conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(APP_ROLE), sql.Identifier(user)))


def main() -> int:
    """Create the app login role named in APP_DATABASE_URL."""
    settings = get_settings()
    app_url = make_url(settings.app_database_url.get_secret_value())
    if not app_url.username or not app_url.password:
        raise SystemExit("APP_DATABASE_URL must include a user name and password.")
    owner_url = libpq_url(settings.database_url.get_secret_value())
    with psycopg.connect(owner_url, autocommit=True) as conn:
        ensure_login_role(conn, app_url.username, app_url.password)
    print(f"Login role '{app_url.username}' is ready (member of {APP_ROLE}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
