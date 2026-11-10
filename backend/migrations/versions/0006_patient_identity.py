"""app.patient_identity: direct identifiers (MRN, name, exact birth date), kept out of OMOP.

`cdm.person` holds no identifiers (only year of birth). The pipeline loads this table in the
same transaction as `cdm`, from silver. Only the clinician API path reads it. Same tenant
isolation as the clinical tables.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE app.patient_identity (
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            person_id integer PRIMARY KEY REFERENCES cdm.person (person_id),
            mrn text NOT NULL,
            given_name text,
            family_name text,
            birth_date date NOT NULL,
            UNIQUE (tenant_id, mrn)
        )
        """
    )
    op.execute("ALTER TABLE app.patient_identity ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.patient_identity FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON app.patient_identity "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON app.patient_identity TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.patient_identity")
