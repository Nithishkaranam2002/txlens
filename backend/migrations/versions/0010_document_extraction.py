"""Biopsy document extraction: documents, LLM passes, evidenced fields, reviews, budget.

- `app.document`: one scanned pathology PDF per tenant and file hash (re-sending the same file
  finds the same row). Holds the OCR result (words, boxes, confidence) and the review status.
- `app.extraction`: one row per LLM pass (A and B) with model, prompt version, tokens and cost.
- `app.extracted_field`: the final value of each field with its evidence (page, bbox, exact
  source text, OCR confidence), whether the two passes agreed, and validation problems.
- `app.document_review`: a human decision (approve / edit / reject) on a document.
- `app.document_cdm_row`: the OMOP rows a document produces (conditions, measurements).
  These app tables are the source of truth: the pipeline load replaces every cdm row of a
  tenant, so `app.load_biopsy_results()` copies approved results into cdm again. Both the
  document workflow and the pipeline loader call it. Ids come from a sequence above every
  tenant's pipeline range (tenants own [ordinal * 1e8, (ordinal + 1) * 1e8), ordinals <= 20),
  so they never collide with pipeline ids and stay stable across reloads.
- `app.llm_cache` (tenant-isolated): LLM responses by file hash + prompt version + model + pass,
  so a re-run costs nothing.
- `app.llm_budget`: spending per batch, shared by all tenants (an operator's bill, no PHI).

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"
TENANT = f"tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id)"
FIRST_EXTRACTED_ID = 2_100_000_000
NLP_TYPE = 32858  # OMOP Type Concept "NLP": derived from document text
PROCEDURE_ID_FIELD = 1147082  # CDM field concept procedure_occurrence.procedure_occurrence_id
LOADED_STATUSES = "('extracted', 'approved')"


def isolate(table: str) -> None:
    """Enable and force the standard tenant policy on an app table."""
    op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON app.{table} "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )


def create_tables() -> None:
    """Documents, passes, fields, reviews and the OMOP rows they produce."""
    op.execute(
        f"""
        CREATE TABLE app.document (
            document_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {TENANT},
            sha256 text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{{64}}$'),
            source_path text NOT NULL,
            workflow_id text NOT NULL,
            organ text CHECK (organ IN ('kidney', 'heart')),
            status text NOT NULL DEFAULT 'processing' CHECK (status IN (
                'processing', 'in_review', 'extracted', 'approved', 'rejected',
                'review_expired', 'failed')),
            person_id integer,
            procedure_occurrence_id integer,
            confidence numeric(4, 3) CHECK (confidence BETWEEN 0 AND 1),
            review_reasons jsonb NOT NULL DEFAULT '[]',
            ocr_engine text,
            ocr jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (tenant_id, sha256)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_document_procedure ON app.document (tenant_id, procedure_occurrence_id)"
    )
    op.execute(
        f"""
        CREATE TABLE app.extraction (
            extraction_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {TENANT},
            document_id uuid NOT NULL REFERENCES app.document (document_id) ON DELETE CASCADE,
            pass text NOT NULL CHECK (pass IN ('a', 'b')),
            model text NOT NULL,
            prompt_version text NOT NULL,
            output jsonb,
            error text,
            input_tokens integer NOT NULL DEFAULT 0 CHECK (input_tokens >= 0),
            output_tokens integer NOT NULL DEFAULT 0 CHECK (output_tokens >= 0),
            cost_usd numeric(12, 6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
            latency_ms integer NOT NULL DEFAULT 0 CHECK (latency_ms >= 0),
            cached boolean NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            UNIQUE (document_id, pass, prompt_version, model)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE app.extracted_field (
            {TENANT},
            document_id uuid NOT NULL REFERENCES app.document (document_id) ON DELETE CASCADE,
            field text NOT NULL,
            value text,
            extracted_value text,
            page integer CHECK (page >= 1),
            bbox jsonb,
            line_no integer,
            source_text text,
            ocr_confidence numeric(4, 3) CHECK (ocr_confidence BETWEEN 0 AND 1),
            passes_agree boolean NOT NULL,
            confidence numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            problems jsonb NOT NULL DEFAULT '[]',
            PRIMARY KEY (document_id, field)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE app.document_review (
            review_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            {TENANT},
            document_id uuid NOT NULL REFERENCES app.document (document_id) ON DELETE CASCADE,
            reviewer_id uuid NOT NULL REFERENCES app.app_user (user_id),
            decision text NOT NULL CHECK (decision IN ('approve', 'edit', 'reject')),
            changes jsonb NOT NULL DEFAULT '{{}}',
            note text CHECK (length(note) <= 2000),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        f"CREATE SEQUENCE app.extracted_cdm_id START {FIRST_EXTRACTED_ID} "
        f"MINVALUE {FIRST_EXTRACTED_ID} MAXVALUE 2147483647 NO CYCLE"
    )
    op.execute(
        f"""
        CREATE TABLE app.document_cdm_row (
            cdm_id integer PRIMARY KEY DEFAULT nextval('app.extracted_cdm_id'),
            {TENANT},
            document_id uuid NOT NULL REFERENCES app.document (document_id) ON DELETE CASCADE,
            field text NOT NULL,
            cdm_table text NOT NULL CHECK (cdm_table IN ('condition_occurrence', 'measurement')),
            concept_id integer NOT NULL,
            source_concept_id integer NOT NULL,
            source_value text NOT NULL,
            value_as_number numeric,
            value_source_value text,
            FOREIGN KEY (document_id, field) REFERENCES app.extracted_field (document_id, field)
                ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_document_cdm_row_document ON app.document_cdm_row (document_id)")
    op.execute(
        f"""
        CREATE TABLE app.llm_cache (
            {TENANT},
            cache_key text NOT NULL,
            model text NOT NULL,
            prompt_version text NOT NULL,
            response jsonb NOT NULL,
            input_tokens integer NOT NULL CHECK (input_tokens >= 0),
            output_tokens integer NOT NULL CHECK (output_tokens >= 0),
            cost_usd numeric(12, 6) NOT NULL CHECK (cost_usd >= 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, cache_key)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE app.llm_budget (
            batch_id text PRIMARY KEY CHECK (length(batch_id) BETWEEN 1 AND 100),
            cap_usd numeric(10, 4) NOT NULL CHECK (cap_usd > 0),
            spent_usd numeric(12, 6) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
            reserved_usd numeric(12, 6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def create_load_function() -> None:
    """Copy a document's (or every document's) approved results into cdm, replacing old copies.

    SECURITY INVOKER (the default): it runs as the caller, so Row-Level Security limits it to
    the caller's tenant. The procedure must still belong to the linked person: if pipeline ids
    were ever rebuilt, a document is left out rather than attached to someone else's biopsy.
    """
    op.execute(
        f"""
        CREATE FUNCTION app.load_biopsy_results(p_document_id uuid DEFAULT NULL)
        RETURNS integer LANGUAGE plpgsql AS $$
        DECLARE
            loaded integer;
            added integer;
        BEGIN
            DELETE FROM cdm.condition_occurrence WHERE condition_occurrence_id IN (
                SELECT cdm_id FROM app.document_cdm_row
                WHERE p_document_id IS NULL OR document_id = p_document_id);
            DELETE FROM cdm.measurement WHERE measurement_id IN (
                SELECT cdm_id FROM app.document_cdm_row
                WHERE p_document_id IS NULL OR document_id = p_document_id);

            INSERT INTO cdm.condition_occurrence (
                condition_occurrence_id, person_id, condition_concept_id, condition_start_date,
                condition_type_concept_id, visit_occurrence_id, condition_source_value,
                condition_source_concept_id)
            SELECT r.cdm_id, p.person_id, r.concept_id, p.procedure_date, {NLP_TYPE},
                   p.visit_occurrence_id, r.source_value, r.source_concept_id
            FROM app.document_cdm_row r
            JOIN app.document d ON d.document_id = r.document_id
            JOIN cdm.procedure_occurrence p
                ON p.procedure_occurrence_id = d.procedure_occurrence_id
                AND p.person_id = d.person_id
            WHERE r.cdm_table = 'condition_occurrence' AND d.status IN {LOADED_STATUSES}
              AND (p_document_id IS NULL OR d.document_id = p_document_id);
            GET DIAGNOSTICS loaded = ROW_COUNT;

            INSERT INTO cdm.measurement (
                measurement_id, person_id, measurement_concept_id, measurement_date,
                measurement_type_concept_id, value_as_number, visit_occurrence_id,
                measurement_source_value, measurement_source_concept_id, value_source_value,
                measurement_event_id, meas_event_field_concept_id)
            SELECT r.cdm_id, p.person_id, r.concept_id, p.procedure_date, {NLP_TYPE},
                   r.value_as_number, p.visit_occurrence_id, r.source_value,
                   r.source_concept_id, r.value_source_value, p.procedure_occurrence_id,
                   {PROCEDURE_ID_FIELD}
            FROM app.document_cdm_row r
            JOIN app.document d ON d.document_id = r.document_id
            JOIN cdm.procedure_occurrence p
                ON p.procedure_occurrence_id = d.procedure_occurrence_id
                AND p.person_id = d.person_id
            WHERE r.cdm_table = 'measurement' AND d.status IN {LOADED_STATUSES}
              AND (p_document_id IS NULL OR d.document_id = p_document_id);
            GET DIAGNOSTICS added = ROW_COUNT;
            RETURN loaded + added;
        END
        $$
        """
    )


def upgrade() -> None:
    create_tables()
    for table in (
        "document",
        "extraction",
        "extracted_field",
        "document_review",
        "document_cdm_row",
        "llm_cache",
    ):
        isolate(table)
    create_load_function()
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.document TO txlens_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.extraction TO txlens_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON app.extracted_field TO txlens_app")
    op.execute("GRANT SELECT, INSERT ON app.document_review TO txlens_app")
    op.execute("GRANT SELECT, INSERT, DELETE ON app.document_cdm_row TO txlens_app")
    op.execute("GRANT SELECT, INSERT ON app.llm_cache TO txlens_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON app.llm_budget TO txlens_app")
    op.execute("GRANT USAGE ON SEQUENCE app.extracted_cdm_id TO txlens_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.load_biopsy_results(uuid) TO txlens_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION app.load_biopsy_results(uuid)")
    for table in (
        "llm_budget",
        "llm_cache",
        "document_cdm_row",
        "document_review",
        "extracted_field",
        "extraction",
        "document",
    ):
        op.execute(f"DROP TABLE app.{table}")
    op.execute("DROP SEQUENCE app.extracted_cdm_id")
