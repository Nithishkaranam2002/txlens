"""Faster copilot views: look the few concept ids up once instead of joining the vocabulary.

The views of 0016 found transplants, graft failures and lab kinds by joining every row to
vocab.concept and vocab.concept_ancestor (1.3 million concepts). A join of two views, such as
measurements of kidney recipients, then took about 16 s and hit the copilot's 5 s timeout.
Here the needed concept ids are uncorrelated subqueries (computed once per query, "InitPlan")
and rows are matched by id. Same columns, same results.

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-13
"""

import importlib.util
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def ids(vocabulary: str, codes: str) -> str:
    """concept_id of these codes and all their descendants, as an array computed once."""
    return (
        "ARRAY(SELECT ca.descendant_concept_id FROM vocab.concept_ancestor ca "
        "JOIN vocab.concept a ON a.concept_id = ca.ancestor_concept_id "
        f"WHERE a.vocabulary_id = '{vocabulary}' AND a.concept_code IN ({codes}))"
    )


def one(vocabulary: str, code: str) -> str:
    """One concept_id, computed once."""
    return (f"(SELECT concept_id FROM vocab.concept WHERE vocabulary_id = '{vocabulary}' "
            f"AND concept_code = '{code}')")  # fmt: skip


# Codes as in txlens_backend.clinical_codes (repeated: a migration must not change later).
TRANSPLANTS = ids("SNOMED", "'70536003', '32413006'")
KIDNEY = ids("SNOMED", "'70536003'")
GRAFT_FAILURE = ids("SNOMED", "'236583003', '233934000'")
TRANSPLANT = f"""
    SELECT DISTINCT ON (po.person_id)
        po.person_id, po.procedure_date AS tx,
        CASE WHEN po.procedure_concept_id = ANY ({KIDNEY}) THEN 'kidney' ELSE 'heart' END
            AS organ,
        CASE WHEN po.procedure_concept_id = {one("SNOMED", "175901007")} THEN 'living'
             WHEN po.procedure_concept_id = {one("SNOMED", "175902000")} THEN 'deceased'
        END AS donor_type
    FROM cdm.procedure_occurrence po
    WHERE po.procedure_concept_id = ANY ({TRANSPLANTS})
    ORDER BY po.person_id, po.procedure_date
"""
PATIENT = f"""
CREATE OR REPLACE VIEW copilot.patient AS
WITH transplant AS ({TRANSPLANT}),
graft_failure AS (
    SELECT co.person_id, min(co.condition_start_date) AS failure_date
    FROM cdm.condition_occurrence co
    WHERE co.condition_concept_id = ANY ({GRAFT_FAILURE})
    GROUP BY co.person_id
)
SELECT
    p.person_id,
    CASE p.gender_concept_id WHEN 8507 THEN 'male' WHEN 8532 THEN 'female' END AS sex,
    t.organ, t.donor_type,
    extract(year FROM t.tx)::int AS transplant_year,
    (extract(year FROM t.tx) - p.year_of_birth)::int AS age_at_transplant,
    g.failure_date IS NOT NULL AS graft_loss,
    (g.failure_date - t.tx) AS graft_loss_day,
    d.death_date IS NOT NULL AS died,
    (d.death_date - t.tx) AS death_day
FROM cdm.person p
JOIN transplant t ON t.person_id = p.person_id
LEFT JOIN graft_failure g ON g.person_id = p.person_id
LEFT JOIN cdm.death d ON d.person_id = p.person_id
"""
TRANSPLANT_DAY = f"JOIN ({TRANSPLANT}) t ON t.person_id = x.person_id"
MEASUREMENT = f"""
CREATE OR REPLACE VIEW copilot.measurement AS
SELECT x.person_id, x.measurement_concept_id,
       CASE WHEN x.measurement_source_concept_id = {one("TxLens", "DSA_MAX_MFI")} THEN 'dsa_mfi'
            WHEN x.measurement_concept_id = {one("LOINC", "2160-0")} THEN 'creatinine'
            WHEN x.measurement_concept_id = {one("LOINC", "98979-8")} THEN 'egfr'
            WHEN x.measurement_concept_id = {one("LOINC", "77348-1")} THEN 'tacrolimus_trough'
            WHEN x.measurement_concept_id = {one("LOINC", "103142-6")} THEN 'dd_cfdna'
       END AS lab,
       x.value_as_number, x.value_as_concept_id, x.unit_concept_id,
       (x.measurement_date - t.tx) AS day, extract(year FROM x.measurement_date)::int AS year
FROM cdm.measurement x
{TRANSPLANT_DAY}
"""
EVENT_VIEWS = {
    "condition": ("cdm.condition_occurrence", "condition_start_date",
                  "x.condition_concept_id, x.condition_type_concept_id"),
    "procedure": ("cdm.procedure_occurrence", "procedure_date", "x.procedure_concept_id"),
    "drug_exposure": ("cdm.drug_exposure", "drug_exposure_start_date",
                      "x.drug_concept_id, x.days_supply"),
    "observation": ("cdm.observation", "observation_date",
                    "x.observation_concept_id, x.value_as_concept_id, x.value_as_number"),
    "visit": ("cdm.visit_occurrence", "visit_start_date", "x.visit_concept_id"),
}  # fmt: skip


def event_view(name: str, table: str, date_column: str, columns: str, join: str) -> str:
    """A cdm event table as person, concepts/values, day after transplant and year."""
    return (
        f"CREATE OR REPLACE VIEW copilot.{name} AS SELECT x.person_id, {columns}, "
        f"(x.{date_column} - t.tx) AS day, extract(year FROM x.{date_column})::int AS year "
        f"FROM {table} x {join}"
    )


def upgrade() -> None:
    op.execute(PATIENT)
    op.execute(MEASUREMENT)
    for name, (table, date_column, columns) in EVENT_VIEWS.items():
        op.execute(event_view(name, table, date_column, columns, TRANSPLANT_DAY))


def previous() -> ModuleType:
    """Migration 0016, whose view definitions a downgrade restores."""
    path = Path(__file__).with_name("0016_copilot_views_and_disclosure.py")
    spec = importlib.util.spec_from_file_location("migration_0016", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def downgrade() -> None:
    old = previous()
    op.execute(old.PATIENT.replace("CREATE VIEW", "CREATE OR REPLACE VIEW"))
    op.execute(old.MEASUREMENT.replace("CREATE VIEW", "CREATE OR REPLACE VIEW"))
    for name, (table, date_column, columns) in old.EVENT_VIEWS.items():
        op.execute(old.event_view(name, table, date_column, columns)
                   .replace("CREATE VIEW", "CREATE OR REPLACE VIEW"))  # fmt: skip
