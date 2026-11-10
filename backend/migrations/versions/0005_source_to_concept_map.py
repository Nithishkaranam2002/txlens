"""vocab.source_to_concept_map from the pinned OHDSI DDL, for reviewed local mappings.

Holds the mappings no standard vocabulary gives us: lab local codes and name variants, and
clinical drug abbreviations. Rows come from omop/mappings/ and are loaded with the vocabulary.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

from txlens_omop import ddl

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CDM_RELEASE = "v5.4.3"


def upgrade() -> None:
    for statement in ddl.render_tables(CDM_RELEASE, ddl.MAPPING_TABLES):
        op.execute(statement)
    for statement in ddl.render_indices(CDM_RELEASE, ddl.MAPPING_TABLES):
        op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE vocab.source_to_concept_map")
