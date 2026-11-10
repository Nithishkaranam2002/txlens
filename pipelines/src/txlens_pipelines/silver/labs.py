"""bronze.lab_rows -> silver.labs: one row per lab result in standard units.

Steps: keep the latest file version; remove exact duplicate lines; read each center's columns;
parse dates per dialect; clean values (whitespace, decimal comma, '<'/'>' censoring, '%');
map the test to a concept (LOINC code, then local code, then test name, via the reviewed
source_to_concept_map); convert units; find the patient by MRN. Rows that fail go to
quarantine with a reason.
"""

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F

from txlens_pipelines.codes import LAB_RULES
from txlens_pipelines.quality import Rule, apply_rules
from txlens_pipelines.silver.common import (
    LINEAGE,
    blank_to_null,
    latest_version,
    normalize_text,
    try_double,
    try_timestamp,
    with_lineage,
)
from txlens_pipelines.vocab import Vocabulary

TABLE = "labs"
DUPLICATES = "lab_duplicates"
# Per dialect: MRN, test code (LOINC or local), test name, value, unit, timestamp, date pattern.
COLUMNS = {
    "a": ("MRN", "LOINC", "TEST_NAME", "RESULT", "UNITS", "COLLECTED_DT", "yyyy-MM-dd HH:mm"),
    "b": ("patient_id", None, "analyte", "value", "unit", "collection_date", "dd/MM/yyyy HH:mm"),
    "c": ("PatientMRN", "LabCode", "LabDescription", "ResultValue", "ResultUnit", "DrawnAt",
          "M/d/yyyy h:mm a"),
}  # fmt: skip
CENSORED = r"^([<>]=?)\s*(.*)$"


def drop_duplicates(lines: DataFrame) -> tuple[DataFrame, DataFrame]:
    """(first copy of each exact line, the removed copies with the row they duplicate)."""
    same = Window.partitionBy("tenant_id", "source_file", "raw_line").orderBy("source_row")
    ranked = lines.withColumn("copy", F.row_number().over(same)).withColumn(
        "kept_row", F.first("source_row").over(same)
    )
    removed = ranked.where("copy > 1").select(
        "tenant_id",
        F.col("source_file").alias("bronze_source_file"),
        F.col("source_row").alias("bronze_source_row"),
        F.col("kept_row").alias("duplicate_of_row"),
    )
    return ranked.where("copy = 1").drop("copy", "kept_row"), removed


def read_columns(lines: DataFrame) -> DataFrame:
    """Each dialect's columns under common names, plus its timestamp parsed."""

    def pick(name: str | None) -> F.Column:
        return F.col("fields").getItem(name) if name else F.lit(None).cast("string")

    def field(position: int) -> F.Column:
        return F.coalesce(
            *[F.when(F.col("dialect") == d, pick(c[position])) for d, c in COLUMNS.items()]
        )

    collected = F.coalesce(
        *[
            F.when(F.col("dialect") == d, try_timestamp(F.trim(pick(c[5])), c[6]))
            for d, c in COLUMNS.items()
        ]
    )
    return with_lineage(lines).select(
        *LINEAGE,
        "dialect",
        F.trim(field(0)).alias("mrn"),
        blank_to_null(field(1)).alias("test_code"),
        field(2).alias("test_name"),
        field(3).alias("value_raw"),
        field(4).alias("unit_raw"),
        field(5).alias("collected_source"),
        collected.alias("collected_at"),
    )


def clean_value(df: DataFrame) -> DataFrame:
    """value_source_value (trimmed), operator for censored values, and the number as written."""
    text = blank_to_null(F.col("value_raw"))
    percent = text.endswith("%")
    plain = F.regexp_replace(F.regexp_replace(text, "%$", ""), ",", ".")
    operator = F.regexp_extract(plain, CENSORED, 1)
    number = F.trim(F.when(operator != "", F.regexp_extract(plain, CENSORED, 2)).otherwise(plain))
    unit = F.coalesce(blank_to_null(F.col("unit_raw")), F.when(percent, F.lit("%")))
    return (
        df.withColumn("value_source_value", text)
        .withColumn("value_operator", F.when(operator != "", operator))
        .withColumn("number_text", number)
        .withColumn("number_as_written", try_double("number_text"))
        .withColumn("unit_source_value", unit)
        .drop("number_text")
    )


def normalize_unit(unit: F.Column) -> F.Column:
    """'µmol/L' -> 'umol/l', 'mL/min/1.73m²' -> 'ml/min/1.73m2' (micro sign, Greek mu, ²)."""
    return F.lower(F.translate(F.trim(unit), "µμ²", "uu2"))


def lab_concepts(df: DataFrame, vocab: Vocabulary) -> DataFrame:
    """lab_concept_id from the LOINC code, then the local code, then the test name."""
    loinc = vocab.concept.where(F.col("vocabulary_id") == "LOINC").select(
        F.col("concept_code").alias("test_code"), F.col("concept_id").alias("loinc_id")
    )
    stcm = vocab.source_to_concept_map

    def reviewed(vocabulary: str, alias: str) -> DataFrame:
        return stcm.where(F.col("source_vocabulary_id") == vocabulary).select(
            F.col("source_code").alias(f"{alias}_key"), F.col("target_concept_id").alias(alias)
        )

    return (
        df.withColumn("code_key", normalize_text(F.col("test_code")))
        .withColumn("name_key", normalize_text(F.col("test_name")))
        .join(F.broadcast(loinc), "test_code", "left")
        .join(F.broadcast(reviewed("TXLENS_LAB_CODE", "code")), "code_key", "left")
        .join(F.broadcast(reviewed("TXLENS_LAB_NAME", "name")), "name_key", "left")
        .withColumn("lab_concept_id", F.coalesce("loinc_id", "code", "name"))
        .withColumn(
            "measurement_source_value",
            F.coalesce(F.when(F.col("dialect") == "c", F.col("test_code")), F.trim("test_name")),
        )
        .withColumn(
            "measurement_source_concept_id",
            F.coalesce(
                F.col("loinc_id"),
                F.when(F.col("lab_concept_id") >= 2_000_000_000, F.col("lab_concept_id")),
                F.lit(0),
            ),
        )
        .drop("code_key", "name_key", "loinc_id", "code", "name")
    )


def rule_table(df: DataFrame, vocab: Vocabulary) -> DataFrame:
    """lab_concept_id -> lab, standard unit, UCUM code; and (lab, unit) -> factor."""
    spark = df.sparkSession
    labs = spark.createDataFrame(
        [(r.lab, r.vocabulary_id, r.concept_code, r.standard_unit, r.ucum_code) for r in LAB_RULES],
        "lab string, vocabulary_id string, concept_code string, standard_unit string, "
        "ucum_code string",
    )
    return labs.join(
        vocab.concept.select("concept_id", "vocabulary_id", "concept_code"),
        ["vocabulary_id", "concept_code"],
    ).select(F.col("concept_id").alias("lab_concept_id"), "lab", "standard_unit", "ucum_code")


def factors(df: DataFrame) -> DataFrame:
    """(lab, normalized unit) -> conversion factor."""
    rows = [(r.lab, unit, f) for r in LAB_RULES for unit, f in r.factors.items()]
    return df.sparkSession.createDataFrame(rows, "lab string, unit_key string, factor double")


def build(
    bronze_labs: DataFrame, patients: DataFrame, vocab: Vocabulary
) -> tuple[DataFrame, DataFrame, DataFrame]:
    """(clean lab results, rows for quarantine, removed duplicate lines)."""
    unique, duplicates = drop_duplicates(latest_version(bronze_labs))
    df = lab_concepts(clean_value(read_columns(unique)), vocab)
    df = (
        df.join(F.broadcast(rule_table(df, vocab)), "lab_concept_id", "left")
        .withColumn("unit_key", normalize_unit(F.col("unit_source_value")))
        .join(F.broadcast(factors(df)), ["lab", "unit_key"], "left")
        .withColumn("value_as_number", F.col("number_as_written") * F.col("factor"))
    )
    mrns = patients.select("tenant_id", "mrn", "patient_source_id")
    df = df.join(mrns, ["tenant_id", "mrn"], "left")
    rules = [
        Rule("missing_value", F.col("value_source_value").isNull(), F.lit("empty result")),
        Rule("unparseable_value", F.col("number_as_written").isNull(), F.col("value_source_value")),
        Rule("unmapped_code", F.col("lab").isNull(),
             F.concat_ws(" / ", "test_code", "test_name")),
        Rule("unknown_unit", F.col("factor").isNull(), F.col("unit_source_value")),
        Rule("bad_date", F.col("collected_at").isNull(), F.col("collected_source")),
        Rule("unknown_patient", F.col("patient_source_id").isNull(), F.col("mrn")),
    ]  # fmt: skip
    good, bad = apply_rules(df, rules)
    clean = good.select(
        *LINEAGE,
        "patient_source_id",
        "mrn",
        "lab",
        "lab_concept_id",
        "measurement_source_value",
        "measurement_source_concept_id",
        "collected_at",
        "value_as_number",
        "value_operator",
        "value_source_value",
        "standard_unit",
        "ucum_code",
        "unit_source_value",
    )
    return clean, bad, duplicates
