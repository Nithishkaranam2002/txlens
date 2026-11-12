"""Copilot conversations: each user's questions and the copilot's answers, per tenant.

A message keeps what was shown: the question or answer text, the tool trace (tool, arguments,
SQL, aggregate results and chart data; never patient rows), citations, cost and latency, so a
conversation can be reopened and audited. Only the user who asked sees a conversation (the
API filters by user); RLS keeps tenants apart. Messages are never edited.

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
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
    op.execute(
        f"""
        CREATE TABLE app.copilot_conversation (
            conversation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            user_id uuid NOT NULL REFERENCES app.app_user (user_id),
            title text NOT NULL CHECK (length(title) BETWEEN 1 AND 200),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_copilot_conversation_user ON app.copilot_conversation "
        "(tenant_id, user_id, created_at)"
    )
    op.execute(
        f"""
        CREATE TABLE app.copilot_message (
            message_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            conversation_id uuid NOT NULL
                REFERENCES app.copilot_conversation (conversation_id) ON DELETE CASCADE,
            role text NOT NULL CHECK (role IN ('user', 'assistant')),
            content text NOT NULL,
            trace jsonb NOT NULL DEFAULT '[]',
            citations jsonb NOT NULL DEFAULT '[]',
            outcome text CHECK (outcome IN ('answered', 'refused', 'error')),
            cost_usd numeric(10, 6) NOT NULL DEFAULT 0,
            latency_ms integer NOT NULL DEFAULT 0,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_copilot_message_conversation ON app.copilot_message "
        "(conversation_id, created_at)"
    )
    for table in ("copilot_conversation", "copilot_message"):
        isolate(table)
        op.execute(f"GRANT SELECT, INSERT ON app.{table} TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.copilot_message")
    op.execute("DROP TABLE app.copilot_conversation")
