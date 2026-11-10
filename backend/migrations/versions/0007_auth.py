"""Users, refresh tokens and login attempts for API authentication.

- `app.app_user`: one user belongs to one tenant and has one role. Tenant-isolated by RLS.
- `app.find_login(username)`: the one pre-login lookup. Before login there is no tenant, so
  RLS would hide every user. The function runs as `txlens_auth`, a NOLOGIN role whose only
  power is a SELECT policy on the login columns. Only txlens_app may execute it.
- `app.refresh_token`: issued refresh tokens (by id), for rotation and logout. Tenant-isolated.
- `app.login_attempt`: failed and successful logins for rate limiting. It has no tenant,
  because a failed login may name no existing user. It holds no patient data.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"
ROLES = ("clinician", "researcher", "data_steward", "admin")


def isolate(table: str) -> None:
    """Enable and force the standard tenant policy on an app table."""
    op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON app.{table} "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )


def upgrade() -> None:
    roles = ", ".join(f"'{r}'" for r in ROLES)
    op.execute(
        f"""
        CREATE TABLE app.app_user (
            user_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            username text NOT NULL UNIQUE CHECK (username = lower(username)),
            display_name text NOT NULL,
            role text NOT NULL CHECK (role IN ({roles})),
            password_hash text NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    isolate("app_user")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.app_user TO txlens_app")

    # The login lookup role: no login, no RLS bypass; it can read only the login columns.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'txlens_auth') THEN
                CREATE ROLE txlens_auth NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA app TO txlens_auth")
    op.execute(
        "GRANT SELECT (user_id, tenant_id, username, role, password_hash, is_active) "
        "ON app.app_user TO txlens_auth"
    )
    op.execute("CREATE POLICY login_lookup ON app.app_user FOR SELECT TO txlens_auth USING (true)")
    op.execute(
        """
        CREATE FUNCTION app.find_login(p_username text)
        RETURNS TABLE (
            user_id uuid, tenant_id text, role text, password_hash text, is_active boolean
        )
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, pg_temp
        AS $$
            SELECT u.user_id, u.tenant_id, u.role, u.password_hash, u.is_active
            FROM app.app_user u
            WHERE u.username = lower(p_username)
        $$
        """
    )
    op.execute("ALTER FUNCTION app.find_login(text) OWNER TO txlens_auth")
    op.execute("REVOKE EXECUTE ON FUNCTION app.find_login(text) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION app.find_login(text) TO txlens_app")

    op.execute(
        f"""
        CREATE TABLE app.refresh_token (
            jti uuid PRIMARY KEY,
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            user_id uuid NOT NULL REFERENCES app.app_user (user_id),
            issued_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NOT NULL,
            revoked_at timestamptz,
            replaced_by uuid
        )
        """
    )
    op.execute("CREATE INDEX ix_refresh_token_user ON app.refresh_token (user_id)")
    isolate("refresh_token")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.refresh_token TO txlens_app")

    op.execute(
        """
        CREATE TABLE app.login_attempt (
            attempt_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            username text NOT NULL,
            ip text NOT NULL,
            succeeded boolean NOT NULL,
            attempted_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_login_attempt_user ON app.login_attempt (username, attempted_at)")
    op.execute("CREATE INDEX ix_login_attempt_ip ON app.login_attempt (ip, attempted_at)")
    op.execute("GRANT SELECT, INSERT, DELETE ON app.login_attempt TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.login_attempt")
    op.execute("DROP TABLE app.refresh_token")
    op.execute("DROP FUNCTION app.find_login(text)")
    op.execute("DROP TABLE app.app_user")
    op.execute("REVOKE USAGE ON SCHEMA app FROM txlens_auth")
    # txlens_auth is cluster-wide and may be used by other databases: it is kept.
