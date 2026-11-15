"""Copilot outcome "demo": the public demo's reply (no API key) to a question it cannot answer.

It points to the suggested questions (or explains the copilot) instead of an error.

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def set_outcomes(outcomes: str) -> None:
    op.execute("ALTER TABLE app.copilot_message DROP CONSTRAINT copilot_message_outcome_check")
    op.execute("ALTER TABLE app.copilot_message ADD CONSTRAINT copilot_message_outcome_check "
               f"CHECK (outcome IN ({outcomes}))")  # fmt: skip


def upgrade() -> None:
    set_outcomes("'answered', 'refused', 'error', 'demo'")


def downgrade() -> None:
    op.execute("UPDATE app.copilot_message SET outcome = 'error' WHERE outcome = 'demo'")
    set_outcomes("'answered', 'refused', 'error'")
