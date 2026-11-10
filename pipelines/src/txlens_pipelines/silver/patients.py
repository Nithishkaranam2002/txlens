"""FHIR Patient -> silver.patients: one row per patient with MRN, sex, birth and death dates.

Center B writes gender as M/F and dates with a time part; both are normalized here.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.quality import Rule, apply_rules
from txlens_pipelines.silver.common import LINEAGE, blank_to_null, fhir_date, fhir_resources

TABLE = "patients"
SCHEMA = (
    "id string, identifier array<struct<system: string, value: string>>, gender string, "
    "birthDate string, deceasedDateTime string"
)
SEX = {"female": "female", "f": "female", "male": "male", "m": "male"}


def sex(gender: F.Column) -> F.Column:
    """'M', 'male', 'F', 'female' (any case) -> 'male' / 'female'; anything else -> NULL."""
    mapping = F.create_map(*[F.lit(x) for pair in SEX.items() for x in pair])
    return mapping[F.lower(F.trim(gender))]


def build(bronze_fhir: DataFrame) -> tuple[DataFrame, DataFrame]:
    """(clean patients, rows for quarantine)."""
    rows = fhir_resources(bronze_fhir, "Patient", SCHEMA)
    mrn_system = F.concat(F.lit("urn:txlens:"), F.col("tenant_id"), F.lit(":mrn"))
    mrn = F.try_element_at(F.filter("r.identifier", lambda i: i["system"] == mrn_system), F.lit(1))
    parsed = rows.select(
        *LINEAGE,
        F.col("r.id").alias("patient_source_id"),
        blank_to_null(mrn["value"]).alias("mrn"),
        sex(F.col("r.gender")).alias("sex"),
        F.col("r.gender").alias("gender_source_value"),
        F.col("r.birthDate").alias("birth_date_source"),
        fhir_date(F.col("r.birthDate")).alias("birth_date"),
        F.col("r.deceasedDateTime").alias("death_date_source"),
        fhir_date(F.col("r.deceasedDateTime")).alias("death_date"),
    )
    rules = [
        Rule("missing_value", F.col("mrn").isNull(), F.lit("no MRN identifier")),
        Rule("bad_date", F.col("birth_date").isNull(), F.col("birth_date_source")),
        Rule(
            "bad_date",
            F.col("death_date_source").isNotNull() & F.col("death_date").isNull(),
            F.col("death_date_source"),
        ),
    ]
    good, bad = apply_rules(parsed, rules)
    return good.drop("birth_date_source", "death_date_source"), bad
