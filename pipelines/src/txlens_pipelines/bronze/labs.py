"""Lab CSV feeds -> bronze.lab_rows: one row per data line, every field kept as a string.

Lines are numbered by splitting each file's text, so `source_row` is the exact 1-based data
row (header excluded) and matches what a person sees in the file. Quoted fields never contain
line breaks in these feeds, so splitting on line breaks is safe.
"""

from dataclasses import dataclass

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.bronze.files import LINEAGE, file_text
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.quality import ContractViolationError

TABLE = "lab_rows"


@dataclass(frozen=True)
class LabDialect:
    """How one center writes its lab CSV."""

    code: str
    delimiter: str
    columns: tuple[str, ...]


DIALECTS = {
    "a": LabDialect("a", ",", ("MRN", "TEST_NAME", "LOINC", "RESULT", "UNITS", "COLLECTED_DT")),
    "b": LabDialect("b", ";", ("patient_id", "analyte", "value", "unit", "collection_date")),
    "c": LabDialect(
        "c",
        ",",
        ("PatientMRN", "LabCode", "LabDescription", "ResultValue", "ResultUnit", "DrawnAt"),
    ),
}


def split_lines(files: DataFrame) -> DataFrame:
    """One row per line: pos 0 is the header, pos n the n-th data row. Blank lines are dropped."""
    return (
        file_text(files)
        .select("*", F.posexplode(F.split(F.col("text"), "\r?\n")).alias("source_row", "line"))
        .drop("text")
        .where(F.length("line") > 0)
    )


def check_header(lines: DataFrame, dialect: LabDialect, file_name: str) -> None:
    """A feed whose header changed is a contract violation, not a few bad rows."""
    expected = dialect.delimiter.join(dialect.columns)
    headers = {r.line.lstrip("﻿") for r in lines.where("source_row = 0").collect()}
    if headers and headers != {expected}:
        raise ContractViolationError(f"{file_name}: header {headers} != expected {expected!r}")


def parse_fields(lines: DataFrame, dialect: LabDialect) -> DataFrame:
    """Data lines with their fields as a map column -> raw string (CSV quoting understood)."""
    schema = ", ".join(f"`{c}` string" for c in dialect.columns)
    parsed = F.from_csv(
        F.col("line"), schema, {"sep": dialect.delimiter, "quote": '"', "mode": "PERMISSIVE"}
    )
    fields = F.create_map(*[x for c in dialect.columns for x in (F.lit(c), parsed[c])])
    return lines.where("source_row > 0").withColumn("fields", fields)


def ingest(files: DataFrame, config: PipelineConfig) -> DataFrame:
    """New lab files -> bronze rows for every tenant's feed."""
    frames = []
    for tenant in config.tenants:
        dialect = DIALECTS[tenant.lab_dialect]
        mine = files.where(F.col("source_file") == f"labs/{tenant.lab_file}")
        lines = split_lines(mine).withColumn("tenant_id", F.lit(tenant.tenant_id))
        check_header(lines, dialect, tenant.lab_file)
        frames.append(
            parse_fields(lines, dialect).select(
                *LINEAGE,
                F.lit(dialect.code).alias("dialect"),
                F.col("line").alias("raw_line"),
                "fields",
            )
        )
    result = frames[0]
    for frame in frames[1:]:
        result = result.unionByName(frame)
    return result
