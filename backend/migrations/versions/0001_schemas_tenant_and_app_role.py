"""Schemas cdm/vocab/app, the tenant registry and the non-superuser app role.

Revision ID: 0001
Revises:
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA cdm")
    op.execute("CREATE SCHEMA vocab")
    op.execute("CREATE SCHEMA app")
    op.execute(
        """
        CREATE TABLE app.tenant (
            tenant_id text PRIMARY KEY CHECK (tenant_id ~ '^[a-z0-9_]+$'),
            name text NOT NULL
        )
        """
    )
    # Roles are cluster-wide, so create the app role only if another database already did.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'txlens_app') THEN
                CREATE ROLE txlens_app NOLOGIN NOSUPERUSER NOBYPASSRLS;
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA cdm, vocab, app TO txlens_app")
    op.execute("GRANT SELECT ON app.tenant TO txlens_app")
    # Tables created later by the migration owner get these grants automatically.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA cdm "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO txlens_app"
    )
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA vocab GRANT SELECT ON TABLES TO txlens_app")


def downgrade() -> None:
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA cdm "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM txlens_app"
    )
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA vocab REVOKE SELECT ON TABLES FROM txlens_app")
    op.execute("DROP SCHEMA app CASCADE")
    op.execute("DROP SCHEMA vocab CASCADE")
    op.execute("DROP SCHEMA cdm CASCADE")
    # The txlens_app role is cluster-wide and may be used by other databases: it is kept.
