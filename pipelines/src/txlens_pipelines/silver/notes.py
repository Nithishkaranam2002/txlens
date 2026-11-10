"""bronze.note_files -> silver.notes: who the note is about and when, read from its header.

The header is fixed text ("MRN: ...", "Date of service: YYYY-MM-DD"), so plain regular
expressions read it; the body is left for the NLP step. The visit is the patient's encounter
on the date of service, when there is one.
"""

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.quality import Rule, apply_rules
from txlens_pipelines.silver.common import LINEAGE, latest_version, try_timestamp, with_lineage

TABLE = "notes"
MRN = r"MRN:\s*(\S+)"
SERVICE_DATE = r"Date of service:\s*(\S+)"
NOTE_TYPES = {"TRANSPLANT CLINIC NOTE": "clinic", "BIOPSY RESULT NOTE": "biopsy_result"}


def extract(text: Column, pattern: str) -> Column:
    """First match group, or NULL."""
    found = F.regexp_extract(text, pattern, 1)
    return F.when(found != "", found)


def note_type(title: Column) -> Column:
    """'clinic' or 'biopsy_result' from the title line; NULL if unknown."""
    result = F.lit(None).cast("string")
    for suffix, kind in NOTE_TYPES.items():
        result = F.when(F.upper(title).endswith(suffix), F.lit(kind)).otherwise(result)
    return result


def build(
    bronze_notes: DataFrame, patients: DataFrame, encounters: DataFrame
) -> tuple[DataFrame, DataFrame]:
    """(clean notes, rows for quarantine)."""
    rows = with_lineage(latest_version(bronze_notes))
    title = F.trim(F.split(F.col("text"), "\r?\n").getItem(0))
    parsed = rows.select(
        *LINEAGE,
        F.regexp_extract("bronze_source_file", r"([^/]+)\.txt$", 1).alias("note_source_id"),
        title.alias("note_title"),
        note_type(title).alias("note_type"),
        extract(F.col("text"), MRN).alias("mrn"),
        extract(F.col("text"), SERVICE_DATE).alias("date_source"),
        F.to_date(try_timestamp(extract(F.col("text"), SERVICE_DATE), "yyyy-MM-dd")).alias(
            "note_date"
        ),
        F.col("text").alias("note_text"),
    )
    with_patient = parsed.join(
        patients.select("tenant_id", "mrn", "patient_source_id"), ["tenant_id", "mrn"], "left"
    )
    visits = (
        encounters.select(
            "tenant_id",
            "patient_source_id",
            F.col("start_date").alias("note_date"),
            "encounter_source_id",
        )
        .groupBy("tenant_id", "patient_source_id", "note_date")
        .agg(F.min("encounter_source_id").alias("encounter_source_id"))
    )
    linked = with_patient.join(visits, ["tenant_id", "patient_source_id", "note_date"], "left")
    rules = [
        Rule("missing_value", F.col("mrn").isNull(), F.lit("no MRN in header")),
        Rule("bad_date", F.col("note_date").isNull(), F.col("date_source")),
        Rule("unknown_patient", F.col("patient_source_id").isNull(), F.col("mrn")),
    ]
    good, bad = apply_rules(linked, rules)
    return good.drop("date_source"), bad
