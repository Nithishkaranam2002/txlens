"""Note NLP "history of" observations: past conditions found in clinic notes.

An affirmed, patient-experienced condition mention that the note places in the past (such as
"history of delayed graft function") is a fact recorded at the note date about an earlier,
undated episode. OMOP keeps such facts in observation with the standard pattern
observation_concept_id = 1340204 "History of event" and value_as_concept_id = the condition,
dated when it was recorded (the note date), the same as "history of kidney transplant" from
the problem list. A condition_occurrence would need an onset date the note does not give.

`app.note_cdm_row` now also holds these rows (cdm_table = 'observation'), keeping the link to
the mention and its sentence; `app.load_note_nlp_results()` copies them into cdm.observation.

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NLP_TYPE = 32858  # OMOP Type Concept "NLP": derived from document text
HISTORY_OF_EVENT = 1340204  # OMOP Extension, standard Observation concept

DELETE_COPIES = """
            DELETE FROM cdm.condition_occurrence WHERE condition_occurrence_id IN (
                SELECT cdm_id FROM app.note_cdm_row
                WHERE cdm_table = 'condition_occurrence'
                  AND (p_note IS NULL OR note_source_value = p_note));
            DELETE FROM cdm.note_nlp WHERE note_nlp_id IN (
                SELECT mention_id FROM app.note_mention
                WHERE p_note IS NULL OR note_source_value = p_note);"""
DELETE_HISTORY_COPIES = """
            DELETE FROM cdm.observation WHERE observation_id IN (
                SELECT cdm_id FROM app.note_cdm_row
                WHERE cdm_table = 'observation'
                  AND (p_note IS NULL OR note_source_value = p_note));"""
COPY_NOTE_NLP = """
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
            GET DIAGNOSTICS mentions = ROW_COUNT;"""
COPY_CONDITIONS = f"""
            INSERT INTO cdm.condition_occurrence (
                condition_occurrence_id, person_id, condition_concept_id, condition_start_date,
                condition_type_concept_id, visit_occurrence_id, condition_source_value,
                condition_source_concept_id)
            SELECT r.cdm_id, n.person_id, r.concept_id, n.note_date, {NLP_TYPE},
                   n.visit_occurrence_id, left(m.lexical_variant, 50), m.source_concept_id
            FROM app.note_cdm_row r
            JOIN app.note_mention m ON m.mention_id = r.mention_id
            JOIN cdm.note n ON n.note_source_value = r.note_source_value
            WHERE r.cdm_table = 'condition_occurrence'
              AND (p_note IS NULL OR r.note_source_value = p_note);
            GET DIAGNOSTICS conditions = ROW_COUNT;"""
COPY_HISTORIES = f"""
            INSERT INTO cdm.observation (
                observation_id, person_id, observation_concept_id, observation_date,
                observation_type_concept_id, value_as_concept_id, visit_occurrence_id,
                observation_source_value, observation_source_concept_id)
            SELECT r.cdm_id, n.person_id, {HISTORY_OF_EVENT}, n.note_date, {NLP_TYPE},
                   r.concept_id, n.visit_occurrence_id, left(m.lexical_variant, 50),
                   m.source_concept_id
            FROM app.note_cdm_row r
            JOIN app.note_mention m ON m.mention_id = r.mention_id
            JOIN cdm.note n ON n.note_source_value = r.note_source_value
            WHERE r.cdm_table = 'observation'
              AND (p_note IS NULL OR r.note_source_value = p_note);
            GET DIAGNOSTICS histories = ROW_COUNT;"""


def load_function(with_histories: bool) -> str:
    """The body of app.load_note_nlp_results(), with or without the observation copies."""
    declare = "mentions integer; conditions integer;" + (
        " histories integer;" if with_histories else ""
    )
    body = DELETE_COPIES + (DELETE_HISTORY_COPIES if with_histories else "")
    body += COPY_NOTE_NLP + COPY_CONDITIONS
    if with_histories:
        body += COPY_HISTORIES
        body += "\n            RETURN mentions + conditions + histories;"
    else:
        body += "\n            RETURN mentions + conditions;"
    return f"""
        CREATE OR REPLACE FUNCTION app.load_note_nlp_results(p_note text DEFAULT NULL)
        RETURNS integer LANGUAGE plpgsql AS $$
        DECLARE {declare}
        BEGIN{body}
        END
        $$
        """


def upgrade() -> None:
    op.execute("ALTER TABLE app.note_cdm_row DROP CONSTRAINT note_cdm_row_cdm_table_check")
    op.execute(
        "ALTER TABLE app.note_cdm_row ADD CONSTRAINT note_cdm_row_cdm_table_check "
        "CHECK (cdm_table IN ('condition_occurrence', 'observation'))"
    )
    op.execute(load_function(with_histories=True))


def downgrade() -> None:
    op.execute("""
        DELETE FROM cdm.observation WHERE observation_id IN
            (SELECT cdm_id FROM app.note_cdm_row WHERE cdm_table = 'observation')
    """)
    op.execute("DELETE FROM app.note_cdm_row WHERE cdm_table = 'observation'")
    op.execute(load_function(with_histories=False))
    op.execute("ALTER TABLE app.note_cdm_row DROP CONSTRAINT note_cdm_row_cdm_table_check")
    op.execute(
        "ALTER TABLE app.note_cdm_row ADD CONSTRAINT note_cdm_row_cdm_table_check "
        "CHECK (cdm_table = 'condition_occurrence')"
    )
