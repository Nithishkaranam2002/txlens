"""FHIR Encounter and Procedure -> silver.encounters and silver.procedures.

Procedures are the transplant (a FHIR Procedure) and every graft biopsy (a biopsy encounter;
the organ is the one the patient received).
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

ENCOUNTERS = "encounters"
PROCEDURES = "procedures"
ENCOUNTER_SCHEMA = (
    "id string, class struct<code: string>, type array<struct<text: string>>, "
    "subject struct<reference: string>, period struct<start: string, end: string>"
)
PROCEDURE_SCHEMA = (
    f"id string, subject struct<reference: string>, code struct<coding: {CODING}, text: string>, "
    "performedDateTime string, extension array<struct<url: string, valueString: string>>"
)
ORGAN_BY_CODE = {"70536003": "kidney", "32413006": "heart"}


def known_patient(df: DataFrame, patients: DataFrame) -> DataFrame:
    """Adds `patient_known`: the referenced patient is in silver.patients."""
    ids = patients.select("tenant_id", "patient_source_id", F.lit(True).alias("patient_known"))
    return df.join(ids, ["tenant_id", "patient_source_id"], "left").fillna(False, ["patient_known"])


def unknown_patient_rule() -> Rule:
    """Rows referring to a patient we do not have."""
    return Rule("unknown_patient", ~F.col("patient_known"), F.col("patient_source_id"))


def build_encounters(bronze_fhir: DataFrame, patients: DataFrame) -> tuple[DataFrame, DataFrame]:
    """(clean encounters, rows for quarantine)."""
    rows = fhir_resources(bronze_fhir, "Encounter", ENCOUNTER_SCHEMA)
    parsed = rows.select(
        *LINEAGE,
        F.col("r.id").alias("encounter_source_id"),
        patient_reference(F.col("r.subject.reference")).alias("patient_source_id"),
        F.lower(F.try_element_at("r.type", F.lit(1))["text"]).alias("kind"),
        F.col("r.class.code").alias("class_code"),
        F.col("r.period.start").alias("start_source"),
        fhir_date(F.col("r.period.start")).alias("start_date"),
        fhir_date(F.col("r.period.end")).alias("end_date"),
    )
    rules = [
        unknown_patient_rule(),
        Rule("bad_date", F.col("start_date").isNull(), F.col("start_source")),
    ]
    good, bad = apply_rules(known_patient(parsed, patients), rules)
    good = good.withColumn("end_date", F.coalesce("end_date", "start_date"))
    return good.drop("patient_known", "start_source"), bad


def organ_of(code: F.Column, text: F.Column) -> F.Column:
    """The transplanted organ from the SNOMED code, else from the leading word of the text."""
    by_code = F.create_map(*[F.lit(x) for pair in ORGAN_BY_CODE.items() for x in pair])[code]
    by_text = F.regexp_extract(F.lower(F.trim(text)), r"^(kidney|heart)\b", 1)
    return F.coalesce(by_code, F.when(by_text != "", by_text))


def build_transplants(bronze_fhir: DataFrame, patients: DataFrame) -> tuple[DataFrame, DataFrame]:
    """(transplant procedures, rows for quarantine)."""
    rows = fhir_resources(bronze_fhir, "Procedure", PROCEDURE_SCHEMA)
    code = coding_code(F.col("r.code.coding"), SNOMED_SYSTEM)
    donor = F.try_element_at(
        F.filter("r.extension", lambda e: e["url"] == "urn:txlens:donor-type"), F.lit(1)
    )["valueString"]
    parsed = rows.select(
        *LINEAGE,
        F.col("r.id").alias("procedure_source_id"),
        patient_reference(F.col("r.subject.reference")).alias("patient_source_id"),
        F.lit("transplant").alias("kind"),
        code.alias("snomed_code"),
        F.col("r.code.text").alias("source_text"),
        organ_of(code, F.col("r.code.text")).alias("organ"),
        donor.alias("donor_type"),
        F.col("r.performedDateTime").alias("date_source"),
        fhir_date(F.col("r.performedDateTime")).alias("procedure_date"),
        F.lit(None).cast("string").alias("encounter_source_id"),
    )
    rules = [
        unknown_patient_rule(),
        Rule("bad_date", F.col("procedure_date").isNull(), F.col("date_source")),
        Rule("missing_value", F.col("organ").isNull(), F.col("source_text")),
    ]
    good, bad = apply_rules(known_patient(parsed, patients), rules)
    return good.drop("patient_known", "date_source"), bad


def build_biopsies(encounters: DataFrame, transplants: DataFrame) -> DataFrame:
    """One biopsy procedure per biopsy encounter, for the patient's transplanted organ."""
    organs = transplants.select("tenant_id", "patient_source_id", "organ").dropDuplicates(
        ["tenant_id", "patient_source_id"]
    )
    return (
        encounters.where(F.col("kind") == "biopsy")
        .join(organs, ["tenant_id", "patient_source_id"])
        .select(
            *LINEAGE,
            F.concat("encounter_source_id", F.lit("-biopsy")).alias("procedure_source_id"),
            "patient_source_id",
            F.lit("biopsy").alias("kind"),
            F.lit(None).cast("string").alias("snomed_code"),
            F.lit("biopsy encounter").alias("source_text"),
            "organ",
            F.lit(None).cast("string").alias("donor_type"),
            F.col("start_date").alias("procedure_date"),
            "encounter_source_id",
        )
    )
