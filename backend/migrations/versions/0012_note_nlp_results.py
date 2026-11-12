"""Note NLP results: processed notes, every mention, and the OMOP rows they produce.

- `app.note_processed`: one row per note the NLP has read, with the text hash and the NLP
  system (rules + prompt version). The batch skips a note whose hash and system are unchanged.
- `app.note_mention`: every mention (condition, medication, lab, procedure) with its offsets
  and sentence in the de-identified text, section, negation, temporality, experiencer, concept,
  method (curated / vocabulary / ambiguous / llm), confidence and status (accepted or review).
- `app.note_cdm_row`: the OMOP rows notes produce. One condition_occurrence per note and
  concept, only for affirmed, current, patient-experienced mentions with a concept and a
  confidence of at least 0.70. `mention_id` is the first such mention: the evidence the API
  shows. OMOP v5.4 condition_occurrence has no link to a note, so this table is that link.
These app tables are the source of truth, as for biopsies: the pipeline load replaces every
cdm row of a tenant, then `app.load_note_nlp_results()` copies them into cdm.note_nlp and
cdm.condition_occurrence again. Notes are joined by note_source_value (a stable natural key).
Ids come from the same sequence as biopsy results, above every tenant's pipeline range.

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')"
TENANT = f"tenant_id text NOT NULL DEFAULT {CURRENT_TENANT} REFERENCES app.tenant (tenant_id)"
NLP_TYPE = 32858  # OMOP Type Concept "NLP": derived from document text
TABLES = ("note_processed", "note_mention", "note_cdm_row")


def isolate(table: str) -> None:
    """Enable and force the standard tenant policy on an app table."""
    op.execute(f"ALTER TABLE app.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE app.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY tenant_isolation ON app.{table} "
        f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
    )


def create_tables() -> None:
    """Processed notes, mentions and the OMOP rows they produce."""
    op.execute(
        f"""
        CREATE TABLE app.note_processed (
            {TENANT},
            note_source_value text NOT NULL,
            text_sha256 text NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{{64}}$'),
            nlp_system text NOT NULL,
            mentions integer NOT NULL CHECK (mentions >= 0),
            processed_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, note_source_value)
        )
        """
    )
    op.execute(
        f"""
        CREATE TABLE app.note_mention (
            mention_id integer PRIMARY KEY DEFAULT nextval('app.extracted_cdm_id'),
            {TENANT},
            note_source_value text NOT NULL,
            start_offset integer NOT NULL CHECK (start_offset >= 0),
            end_offset integer NOT NULL CHECK (end_offset > start_offset),
            lexical_variant text NOT NULL,
            sentence_start integer NOT NULL,
            sentence_end integer NOT NULL CHECK (sentence_end >= sentence_start),
            snippet text NOT NULL,
            section text,
            category text NOT NULL CHECK (
                category IN ('condition', 'medication', 'lab', 'procedure')),
            concept_id integer NOT NULL,
            source_concept_id integer NOT NULL,
            negated boolean NOT NULL,
            temporality text NOT NULL CHECK (
                temporality IN ('current', 'historical', 'hypothetical')),
            experiencer text NOT NULL CHECK (experiencer IN ('patient', 'family')),
            method text NOT NULL CHECK (method IN ('curated', 'vocabulary', 'ambiguous', 'llm')),
            confidence numeric(4, 3) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
            status text NOT NULL CHECK (status IN ('accepted', 'review')),
            FOREIGN KEY (tenant_id, note_source_value)
                REFERENCES app.note_processed (tenant_id, note_source_value) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_note_mention_note ON app.note_mention (tenant_id, note_source_value)"
    )
    op.execute(
        f"""
        CREATE TABLE app.note_cdm_row (
            cdm_id integer PRIMARY KEY DEFAULT nextval('app.extracted_cdm_id'),
            {TENANT},
            note_source_value text NOT NULL,
            mention_id integer NOT NULL REFERENCES app.note_mention (mention_id)
                ON DELETE CASCADE,
            cdm_table text NOT NULL CHECK (cdm_table = 'condition_occurrence'),
            concept_id integer NOT NULL CHECK (concept_id <> 0),
            mentions integer NOT NULL CHECK (mentions >= 1),
            UNIQUE (tenant_id, note_source_value, cdm_table, concept_id)
        )
        """
    )


def create_load_function() -> None:
    """Copy NLP results (all, or one note's) into cdm, replacing earlier copies.

    SECURITY INVOKER: Row-Level Security limits it to the caller's tenant. A note the pipeline
    no longer loads is left out (the join on cdm.note finds nothing).
    """
    op.execute(
        f"""
        CREATE FUNCTION app.load_note_nlp_results(p_note text DEFAULT NULL)
        RETURNS integer LANGUAGE plpgsql AS $$
        DECLARE
            mentions integer;
            conditions integer;
        BEGIN
            DELETE FROM cdm.condition_occurrence WHERE condition_occurrence_id IN (
                SELECT cdm_id FROM app.note_cdm_row
                WHERE p_note IS NULL OR note_source_value = p_note);
            DELETE FROM cdm.note_nlp WHERE note_nlp_id IN (
                SELECT mention_id FROM app.note_mention
                WHERE p_note IS NULL OR note_source_value = p_note);

            INSERT INTO cdm.note_nlp (
                note_nlp_id, note_id, section_concept_id, snippet, "offset", lexical_variant,
                note_nlp_concept_id, note_nlp_source_concept_id, nlp_system, nlp_date,
                term_exists, term_temporal, term_modifiers)
            SELECT m.mention_id, n.note_id, 0, left(m.snippet, 250),
                   m.start_offset || '-' || m.end_offset, left(m.lexical_variant, 250),
                   m.concept_id, m.source_concept_id, p.nlp_system, p.processed_at::date,
                   CASE WHEN m.negated THEN 'N' ELSE 'Y' END, m.temporality,
                   concat_ws(';', 'experiencer=' || m.experiencer, 'section=' || m.section,
                             'method=' || m.method, 'confidence=' || m.confidence,
                             'status=' || m.status)
            FROM app.note_mention m
            JOIN app.note_processed p USING (tenant_id, note_source_value)
            JOIN cdm.note n ON n.note_source_value = m.note_source_value
            WHERE p_note IS NULL OR m.note_source_value = p_note;
            GET DIAGNOSTICS mentions = ROW_COUNT;

            INSERT INTO cdm.condition_occurrence (
                condition_occurrence_id, person_id, condition_concept_id, condition_start_date,
                condition_type_concept_id, visit_occurrence_id, condition_source_value,
                condition_source_concept_id)
            SELECT r.cdm_id, n.person_id, r.concept_id, n.note_date, {NLP_TYPE},
                   n.visit_occurrence_id, left(m.lexical_variant, 50), m.source_concept_id
            FROM app.note_cdm_row r
            JOIN app.note_mention m ON m.mention_id = r.mention_id
            JOIN cdm.note n ON n.note_source_value = r.note_source_value
            WHERE p_note IS NULL OR r.note_source_value = p_note;
            GET DIAGNOSTICS conditions = ROW_COUNT;
            RETURN mentions + conditions;
        END
        $$
        """
    )


def upgrade() -> None:
    create_tables()
    for table in TABLES:
        isolate(table)
    create_load_function()
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON app.{table} TO txlens_app")
    op.execute("GRANT EXECUTE ON FUNCTION app.load_note_nlp_results(text) TO txlens_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION app.load_note_nlp_results(text)")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE app.{table}")
