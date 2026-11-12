"""Why a note mention went to review, when a rule (not a missing concept) sent it there.

Step 2.3: a rejection the rules call current, in a sentence with an earlier date or a
treatment cue, is sent to review with a reason; the review queue shows and prioritises it.

Revision ID: 0013
Revises: 0012
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE app.note_mention ADD COLUMN review_reason text")


def downgrade() -> None:
    op.execute("ALTER TABLE app.note_mention DROP COLUMN review_reason")
