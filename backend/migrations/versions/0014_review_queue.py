"""The human review queue: decisions, labelled examples, mapping gaps, curated-mapping history.

- `app.review_decision`: every reviewer decision on any queue item (biopsy document, note
  mention, condition mapping gap, AI-curated mapping row): action, reason code (required for
  edit and reject), changes, when the item was queued and decided. Append-only: the app role
  may insert and read, never change or delete, so the record of who decided what is kept.
- `app.labelled_example`: one row per field per decision, the model's value next to the
  reviewer's, with the de-identified evidence only. The feedback loop: agreement rates and new
  evaluation examples come from here. Append-only as well.
- `app.mapping_gap`: condition source values the pipeline could not map (concept 0) or mapped
  with too little confidence, copied from gold.mapping_gaps at every load.
- `app.curated_mapping_history`: approve / correct / revert decisions on the AI-curated rows
  of SOURCE_TO_CONCEPT_MAP.csv. These rows are one shared vocabulary for every center, so this
  table has no tenant: one clinician's review counts for all centers. The reviewer's center is
  recorded, and the audit row goes to that center's audit_log. Append-only: the current state
  of a row is its latest history entry, and a revert is a new entry.
Note mentions may now also be rejected by a reviewer, and remember who reviewed them.

Revision ID: 0014
Revises: 0013
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"
TENANT = f"tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id)"
ITEM_TYPES = "'biopsy_document', 'note_mention', 'condition_mapping', 'curated_mapping'"
REASON_CODES = (
    "'ocr_error', 'wrong_value', 'wrong_concept', 'wrong_negation', 'wrong_temporality', "
    "'wrong_experiencer', 'not_a_finding', 'insufficient_evidence', 'other'"
)
TENANT_TABLES = ("review_decision", "labelled_example", "mapping_gap")


def isolate(table: str) -> None:
    """Enable and force the standard tenant policy on an app table."""
    op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON app.{table} "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )


def create_tables() -> None:
    op.execute(
        f"""
        CREATE TABLE app.review_decision (
            decision_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {TENANT},
            item_type text NOT NULL CHECK (item_type IN ({ITEM_TYPES})),
            item_key text NOT NULL,
            action text NOT NULL CHECK (action IN ('approve', 'edit', 'reject', 'revert')),
            reason_code text CHECK (reason_code IN ({REASON_CODES})),
            changes jsonb NOT NULL DEFAULT '{{}}',
            note text CHECK (length(note) <= 2000),
            reviewer_id uuid NOT NULL REFERENCES app.app_user (user_id),
            qc_sample boolean NOT NULL DEFAULT false,
            queued_at timestamptz NOT NULL,
            decided_at timestamptz NOT NULL DEFAULT now(),
            CHECK (action = 'approve' OR reason_code IS NOT NULL),
            CHECK (reason_code <> 'other' OR length(trim(note)) > 0)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_review_decision_item ON app.review_decision (tenant_id, item_type, "
        "item_key)"
    )
    op.execute(
        f"""
        CREATE TABLE app.labelled_example (
            example_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            {TENANT},
            decision_id uuid NOT NULL REFERENCES app.review_decision (decision_id),
            item_type text NOT NULL CHECK (item_type IN ({ITEM_TYPES})),
            field text NOT NULL,
            model_value text,
            reviewer_value text,
            agrees boolean NOT NULL,
            qc_sample boolean NOT NULL,
            evidence_deid text,
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE app.mapping_gap (
            {TENANT},
            source_value text NOT NULL,
            reason text NOT NULL,
            rows bigint NOT NULL CHECK (rows >= 0),
            suggested_concept_id integer,
            suggestion_score double precision,
            review_reason text,
            loaded_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, source_value)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE app.curated_mapping_history (
            history_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            source_vocabulary_id text NOT NULL,
            source_code text NOT NULL,
            action text NOT NULL CHECK (action IN ('approve', 'correct', 'revert')),
            old_concept_id integer NOT NULL,
            new_concept_id integer NOT NULL,
            reviewer_id uuid NOT NULL REFERENCES app.app_user (user_id),
            reviewer_tenant text NOT NULL REFERENCES app.tenant (tenant_id),
            reason_code text CHECK (reason_code IN ({REASON_CODES})),
            note text CHECK (length(note) <= 2000),
            decided_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            CHECK (action = 'approve' OR reason_code IS NOT NULL),
            CHECK (reviewer_tenant = {CURRENT_TENANT})
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_curated_history_row ON app.curated_mapping_history "
        "(source_vocabulary_id, source_code, history_id)"
    )


def upgrade() -> None:
    create_tables()
    for table in TENANT_TABLES:
        isolate(table)
    for table in ("review_decision", "labelled_example", "curated_mapping_history"):
        op.execute(f"GRANT SELECT, INSERT ON app.{table} TO txlens_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON app.mapping_gap TO txlens_app")
    op.execute("ALTER TABLE app.note_mention DROP CONSTRAINT note_mention_status_check")
    op.execute(
        "ALTER TABLE app.note_mention ADD CONSTRAINT note_mention_status_check "
        "CHECK (status IN ('accepted', 'review', 'rejected'))"
    )
    op.execute(
        "ALTER TABLE app.note_mention ADD COLUMN reviewed_by uuid "
        "REFERENCES app.app_user (user_id), ADD COLUMN reviewed_at timestamptz"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE app.note_mention DROP COLUMN reviewed_by, DROP COLUMN reviewed_at")
    op.execute("DELETE FROM app.note_mention WHERE status = 'rejected'")
    op.execute("ALTER TABLE app.note_mention DROP CONSTRAINT note_mention_status_check")
    op.execute(
        "ALTER TABLE app.note_mention ADD CONSTRAINT note_mention_status_check "
        "CHECK (status IN ('accepted', 'review'))"
    )
    for table in ("curated_mapping_history", "mapping_gap", "labelled_example", "review_decision"):
        op.execute(f"DROP TABLE app.{table}")
