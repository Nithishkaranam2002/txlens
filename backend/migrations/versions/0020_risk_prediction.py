"""Rejection-risk predictions and nightly scoring runs, per tenant.

Every prediction records the model name and registry version that produced it, the visit it
scored, its inputs and its top contributions, so any score shown can be traced to the exact
model and data. Rows are never updated; scoring the same visit with the same version again
keeps the first row. A scoring run stores score and feature drift (PSI) against the same
population (each active patient's latest visit) at training time.

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
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
        CREATE TABLE app.risk_run (
            run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            as_of date NOT NULL,
            model_name text NOT NULL,
            model_version text NOT NULL,
            scored integer NOT NULL CHECK (scored >= 0),
            score_psi double precision,
            feature_psi jsonb NOT NULL DEFAULT '{{}}',
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE app.risk_prediction (
            prediction_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            person_id integer NOT NULL,
            visit_date date NOT NULL,
            model_name text NOT NULL,
            model_version text NOT NULL,
            score double precision NOT NULL CHECK (score BETWEEN 0 AND 1),
            threshold double precision NOT NULL,
            band text NOT NULL CHECK (band IN ('lower', 'elevated')),
            contributions jsonb NOT NULL,
            features jsonb NOT NULL,
            source text NOT NULL CHECK (source IN ('nightly', 'on_demand')),
            run_id uuid REFERENCES app.risk_run (run_id),
            scored_by uuid REFERENCES app.app_user (user_id),
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, person_id, visit_date, model_name, model_version)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_risk_prediction_person ON app.risk_prediction "
        "(tenant_id, person_id, visit_date DESC, created_at DESC)"
    )
    for table in ("risk_run", "risk_prediction"):
        isolate(table)
        op.execute(f"GRANT SELECT, INSERT ON app.{table} TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.risk_prediction")
    op.execute("DROP TABLE app.risk_run")
