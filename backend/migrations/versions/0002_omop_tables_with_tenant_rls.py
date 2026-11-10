"""OMOP CDM v5.4.3 tables, plus tenant_id and Row-Level Security on clinical tables.

Tables, primary keys and clinical-to-clinical foreign keys come from the pinned official
OHDSI DDL (rendered by txlens_omop.ddl for this exact release). Our deliberate extension of
standard OMOP: every clinical table gets `tenant_id`, and a policy so a session only sees and
writes rows of the tenant in its `app.tenant_id` setting. Vocabulary tables are shared.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

from txlens_omop import ddl

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CDM_RELEASE = "v5.4.3"
# NULLIF: an empty setting (possible after RESET) counts as "no tenant", which matches no row.
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"


def add_tenant_isolation(table: str) -> None:
    """tenant_id column (defaults to the session's tenant) and an enforced RLS policy."""
    op.execute(
        f"ALTER TABLE cdm.{table} ADD COLUMN tenant_id text NOT NULL "
        f"DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id)"
    )
    op.execute(f"ALTER TABLE cdm.{table} ENABLE ROW LEVEL SECURITY")
    # FORCE: the policy applies to the table owner too (superusers still bypass it).
    op.execute(f"ALTER TABLE cdm.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON cdm.{table} "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )


def upgrade() -> None:
    for statement in ddl.render_tables(CDM_RELEASE):
        op.execute(statement)
    for statement in ddl.render_primary_keys(CDM_RELEASE):
        op.execute(statement)
    for statement in ddl.render_foreign_keys(CDM_RELEASE):
        op.execute(statement)
    for table in ddl.CLINICAL_TABLES:
        add_tenant_isolation(table)


def downgrade() -> None:
    for table in ddl.CLINICAL_TABLES:
        op.execute(f"DROP TABLE cdm.{table} CASCADE")
    for table in ddl.VOCAB_TABLES:
        op.execute(f"DROP TABLE vocab.{table} CASCADE")
