"""Surveillance runs and flags (clinician worklist), tenant-isolated.

- `app.surveillance_run`: one row per engine run per center and "as of" date, with the config
  versions used and how many flags it opened or cleared. Its flags reference it with a
  deferred foreign key, so the run row (with final counts) is written last in the same
  transaction.
- `app.surveillance_flag`: a reason to review a patient. `evidence_key` (rule + the exact
  results) is unique per patient, so a re-run inserts nothing new for evidence already flagged
  and never reopens a resolved flag. `cleared_at` is set by the engine when the rule stops
  firing; `status` is changed only by clinicians (new -> acknowledged -> resolved).

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
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
        CREATE TABLE app.surveillance_run (
            run_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            as_of date NOT NULL,
            started_at timestamptz NOT NULL DEFAULT now(),
            configs jsonb NOT NULL,
            patients integer NOT NULL CHECK (patients >= 0),
            flags_opened integer NOT NULL CHECK (flags_opened >= 0),
            flags_cleared integer NOT NULL CHECK (flags_cleared >= 0)
        )
        """
    )
    isolate("surveillance_run")
    op.execute(
        f"""
        CREATE TABLE app.surveillance_flag (
            flag_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            person_id integer NOT NULL,
            run_id uuid NOT NULL REFERENCES app.surveillance_run (run_id)
                DEFERRABLE INITIALLY DEFERRED,
            config_id text NOT NULL,
            config_version integer NOT NULL,
            rule_id text NOT NULL,
            rule_group text NOT NULL,
            severity text NOT NULL CHECK (severity IN ('high', 'medium', 'low')),
            reason text NOT NULL,
            evidence jsonb NOT NULL,
            evidence_key text NOT NULL,
            as_of date NOT NULL,
            status text NOT NULL DEFAULT 'new'
                CHECK (status IN ('new', 'acknowledged', 'resolved')),
            status_changed_by uuid REFERENCES app.app_user (user_id),
            status_changed_at timestamptz,
            note text CHECK (length(note) <= 2000),
            cleared_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, person_id, evidence_key)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_surveillance_flag_open ON app.surveillance_flag "
        "(tenant_id, person_id) WHERE status <> 'resolved' AND cleared_at IS NULL"
    )
    isolate("surveillance_flag")
    op.execute("GRANT SELECT, INSERT ON app.surveillance_run TO txlens_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.surveillance_flag TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.surveillance_flag")
    op.execute("DROP TABLE app.surveillance_run")
