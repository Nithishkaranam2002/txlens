"""Helpers shared by the silver transforms.

Parsing uses `try_*` functions: Databricks serverless runs with ANSI SQL mode on, where plain
casts throw on bad input. A failed parse gives NULL, and the quarantine rules catch it.
"""

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

SNOMED_SYSTEM = "http://snomed.info/sct"
RXNORM_SYSTEM = "http://www.nlm.nih.gov/research/umls/rxnorm"
CODING = "array<struct<system: string, code: string>>"
LINEAGE = ["tenant_id", "bronze_source_file", "bronze_source_row"]


def latest_version(bronze: DataFrame) -> DataFrame:
    """Rows of the most recently ingested version of each source file."""
    latest = bronze.groupBy("tenant_id", "source_file").agg(
        F.max_by("file_sha256", "ingested_at").alias("file_sha256")
    )
    return bronze.join(latest, ["tenant_id", "source_file", "file_sha256"])


def with_lineage(df: DataFrame) -> DataFrame:
    """Rename bronze lineage columns to their silver names."""
    return df.withColumnRenamed("source_file", "bronze_source_file").withColumnRenamed(
        "source_row", "bronze_source_row"
    )


def normalize_text(column: Column) -> Column:
    """Lower case, trimmed, inner whitespace collapsed (matches omop's normalize_source_code)."""
    return F.regexp_replace(F.lower(F.trim(column)), r"\s+", " ")


def blank_to_null(column: Column) -> Column:
    """Trimmed text, or NULL when empty."""
    trimmed = F.trim(column)
    return F.when(F.length(trimmed) > 0, trimmed)


def try_double(name: str) -> Column:
    """Column `name` as a number, or NULL when its text is not one (PySpark 3.5 has no
    Column.try_cast, so this uses the SQL function)."""
    return F.expr(f"try_cast(`{name}` AS DOUBLE)")


def try_timestamp(column: Column, pattern: str) -> Column:
    """A timestamp parsed with `pattern`, or NULL (impossible dates such as 31/02 included)."""
    return F.try_to_timestamp(column, F.lit(pattern))


def fhir_date(column: Column) -> Column:
    """A FHIR date or dateTime ('2024-05-16' or '2024-05-16T00:00:00') as a date, or NULL."""
    return F.to_date(try_timestamp(F.substring(column, 1, 10), "yyyy-MM-dd"))


def coding_code(coding: Column, system: str) -> Column:
    """The code of the first coding in `system`, or NULL."""
    return F.try_element_at(F.filter(coding, lambda c: c["system"] == system), F.lit(1))["code"]


def patient_reference(reference: Column) -> Column:
    """'Patient/<id>' -> '<id>'."""
    return F.regexp_extract(reference, r"^Patient/(.+)$", 1)


def fhir_resources(bronze: DataFrame, resource_type: str, schema: str) -> DataFrame:
    """Latest bronze rows of one FHIR resource type, parsed with `schema` into column `r`."""
    rows = latest_version(bronze).where(F.col("resource_type") == resource_type)
    return with_lineage(rows).withColumn("r", F.from_json("resource_json", schema))
