"""app.note_phi_span: the identifiers replaced in de-identified notes.

`cdm.note.note_text` holds only the de-identified copy (surrogate names, shifted dates, tags;
pipelines/.../deid_notes.py). Each row here is one replaced span: its offsets in the
de-identified and in the original text, its kind, and the original words. The clinician API
path uses it to show a note sentence as written; no other role reads it. The full identified
note text never enters the database. Loaded by the pipeline in the same transaction as cdm.

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE app.note_phi_span (
            tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id),
            note_id integer NOT NULL REFERENCES cdm.note (note_id),
            span_no integer NOT NULL,
            deid_start integer NOT NULL,
            deid_end integer NOT NULL,
            orig_start integer NOT NULL,
            orig_end integer NOT NULL,
            kind text NOT NULL,
            original text NOT NULL,
            PRIMARY KEY (note_id, span_no)
        )
        """
    )
    op.execute("ALTER TABLE app.note_phi_span ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE app.note_phi_span FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON app.note_phi_span "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )
    op.execute("GRANT SELECT, INSERT, DELETE ON app.note_phi_span TO txlens_app")


def downgrade() -> None:
    op.execute("DROP TABLE app.note_phi_span")
