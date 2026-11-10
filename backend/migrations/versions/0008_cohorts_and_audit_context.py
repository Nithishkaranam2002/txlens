"""Saved cohorts, audit request context, and pgcrypto for pseudonym lookups.

- `app.cohort` + `app.cohort_member`: a cohort's rules and a snapshot of its members at
  creation, so a saved cohort stays reproducible. Members have no foreign key to cdm.person:
  a pipeline reload replaces cdm rows, while person ids stay stable (gold.id_map).
- `app.audit_log` gets the API context: the app user, the request id and the client IP.
- pgcrypto's `hmac()` lets the API find a patient by pseudonym in SQL. The key is passed in
  per query and never stored in the database.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"


def isolate(table: str) -> None:
    """Enable and force the standard tenant policy on an app table."""
    op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON app.{table} "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute(
        f"""
        CREATE TABLE app.cohort (
            cohort_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            name text NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
            rules jsonb NOT NULL,
            created_by uuid NOT NULL REFERENCES app.app_user (user_id),
            created_at timestamptz NOT NULL DEFAULT now(),
            member_count integer NOT NULL CHECK (member_count >= 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_cohort_tenant_created ON app.cohort (tenant_id, created_at)")
    isolate("cohort")
    op.execute(
        f"""
        CREATE TABLE app.cohort_member (
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            cohort_id uuid NOT NULL REFERENCES app.cohort (cohort_id) ON DELETE CASCADE,
            person_id integer NOT NULL,
            PRIMARY KEY (cohort_id, person_id)
        )
        """
    )
    isolate("cohort_member")
    op.execute("GRANT SELECT, INSERT ON app.cohort, app.cohort_member TO txlens_app")

    op.execute("ALTER TABLE app.audit_log ADD COLUMN user_id uuid")
    op.execute("ALTER TABLE app.audit_log ADD COLUMN request_id text")
    op.execute("ALTER TABLE app.audit_log ADD COLUMN ip text")


def downgrade() -> None:
    op.execute("ALTER TABLE app.audit_log DROP COLUMN ip")
    op.execute("ALTER TABLE app.audit_log DROP COLUMN request_id")
    op.execute("ALTER TABLE app.audit_log DROP COLUMN user_id")
    op.execute("DROP TABLE app.cohort_member")
    op.execute("DROP TABLE app.cohort")
    # pgcrypto stays: other objects in the database may use it.
