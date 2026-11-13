"""Per-user request counts for rate-limited endpoints (api/rate_limit.py).

One row per (user, bucket, fixed window). It holds no patient data and no tenant column:
counts are per user, and a user belongs to one tenant. The API deletes a user's expired
windows as it counts, so the table stays small.

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE app.rate_limit (
            user_id uuid NOT NULL REFERENCES app.app_user (user_id) ON DELETE CASCADE,
            bucket text NOT NULL,
            window_start timestamptz NOT NULL,
            hits integer NOT NULL CHECK (hits > 0),
            PRIMARY KEY (user_id, bucket, window_start)
        )
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.rate_limit TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.rate_limit")
