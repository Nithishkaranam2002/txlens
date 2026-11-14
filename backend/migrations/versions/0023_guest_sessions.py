"""Guest copilot history per login session: app.copilot_conversation.guest_session.

Guest demo accounts are shared by everyone who gets the link, so a guest sees only the
copilot conversations started in its own login session (the token's guest session id).
NULL for everyone else, whose history stays per user. Older guest conversations (NULL)
are hidden from guests.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.copilot_conversation ADD COLUMN guest_session uuid")
    op.execute(
        "CREATE INDEX ix_copilot_conversation_guest_session ON app.copilot_conversation "
        "(guest_session) WHERE guest_session IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.copilot_conversation DROP COLUMN guest_session")
