"""Append-only audit log: who did what to which object, isolated per tenant.

The app role may only INSERT and SELECT its own tenant's rows; it can never change or delete
history. Step 1.4's pipeline load writes one row per table per tenant; Step 1.5 adds API
writes and PHI views.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE app.audit_log (
            audit_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            occurred_at timestamptz NOT NULL DEFAULT now(),
            actor text NOT NULL DEFAULT session_user,
            action text NOT NULL,
            object_type text NOT NULL,
            object_id text,
            details jsonb NOT NULL DEFAULT '{{}}'::jsonb
        )
        """
    )
    op.execute("CREATE INDEX ix_audit_log_tenant_time ON app.audit_log (tenant_id, occurred_at)")
    op.execute("ALTER TABLE app.audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.audit_log FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON app.audit_log "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )
    op.execute("GRANT SELECT, INSERT ON app.audit_log TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.audit_log")
