"""Accounts that expire: app.app_user.expires_at (guest demo accounts).

NULL means the account never expires. After `expires_at`, login and token refresh are
refused. The login lookup (app.find_login, run before any tenant is known) returns the
expiry so the API can refuse the login.

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def find_login(columns: str, returns: str) -> str:
    """The login lookup function returning `columns`."""
    return f"""
        CREATE FUNCTION app.find_login(p_username text)
        RETURNS TABLE ({returns})
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT {columns}
            FROM app.app_user u
            WHERE u.username = lower(p_username)
        $$
        """


def replace_find_login(sql: str) -> None:
    """Drop and recreate app.find_login (its result type changes) with its owner and grants."""
    op.execute("DROP FUNCTION app.find_login(text)")
    op.execute(sql)
    op.execute("ALTER FUNCTION app.find_login(text) OWNER TO txlens_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION app.find_login(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.find_login(text) TO txlens_app")


def upgrade() -> None:
    op.execute("ALTER TABLE app.app_user ADD COLUMN expires_at timestamptz")
    op.execute("GRANT SELECT (expires_at) ON app.app_user TO txlens_auth")
    replace_find_login(find_login(
        "u.user_id, u.tenant_id, u.role, u.password_hash, u.is_active, u.expires_at",
        "user_id uuid, tenant_id text, role text, password_hash text, is_active boolean, "
        "expires_at timestamptz",
    ))  # fmt: skip


def downgrade() -> None:
    replace_find_login(find_login(
        "u.user_id, u.tenant_id, u.role, u.password_hash, u.is_active",
        "user_id uuid, tenant_id text, role text, password_hash text, is_active boolean",
    ))  # fmt: skip
    op.execute("REVOKE SELECT (expires_at) ON app.app_user FROM txlens_auth")
    op.execute("ALTER TABLE app.app_user DROP COLUMN expires_at")
