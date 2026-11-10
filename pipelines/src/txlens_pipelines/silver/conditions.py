"""FHIR Condition -> silver.conditions: the SNOMED code when the center sent one, plus text.

Problem-list conditions carry no date in these feeds; gold dates them at the transplant (when
the problem list was documented). Graft failure carries its onset date.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.quality import Rule, apply_rules
from txlens_pipelines.silver.common import (
    CODING,
    LINEAGE,
    SNOMED_SYSTEM,
    coding_code,
    fhir_date,
    fhir_resources,
    patient_reference,
)
from txlens_pipelines.silver.encounters import known_patient, unknown_patient_rule

TABLE = "conditions"
SCHEMA = (
    f"id string, subject struct<reference: string>, code struct<coding: {CODING}, text: string>, "
    "onsetDateTime string"
)


def build(bronze_fhir: DataFrame, patients: DataFrame) -> tuple[DataFrame, DataFrame]:
    """(clean conditions, rows for quarantine)."""
    rows = fhir_resources(bronze_fhir, "Condition", SCHEMA)
    parsed = rows.select(
        *LINEAGE,
        F.col("r.id").alias("condition_source_id"),
        patient_reference(F.col("r.subject.reference")).alias("patient_source_id"),
        coding_code(F.col("r.code.coding"), SNOMED_SYSTEM).alias("snomed_code"),
        F.col("r.code.text").alias("source_text"),
        F.col("r.onsetDateTime").alias("onset_source"),
        fhir_date(F.col("r.onsetDateTime")).alias("onset_date"),
    )
    rules = [
        unknown_patient_rule(),
        Rule(
            "bad_date",
            F.col("onset_source").isNotNull() & F.col("onset_date").isNull(),
            F.col("onset_source"),
        ),
        Rule(
            "missing_value",
            F.col("snomed_code").isNull() & F.col("source_text").isNull(),
            F.lit("no code and no text"),
        ),
    ]
    good, bad = apply_rules(known_patient(parsed, patients), rules)
    return good.drop("patient_known", "onset_source"), bad
