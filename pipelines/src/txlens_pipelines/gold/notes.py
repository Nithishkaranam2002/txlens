"""Gold notes: de-identified text for cdm.note, the replacement list for app.note_phi_span.

The original note text stays in bronze/silver (the lake, like the raw files). OMOP gets only
the de-identified copy; the identifiers that were replaced go to a separate app table that
only the clinician API path reads (ADR 0008, ADR 0014).
"""

from datetime import date

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType,
    IntegerType,
    StringType,
    StructField,
    StructType,
)

from txlens_pipelines.deid_notes import (
    Identity,
    date_shift_days,
    deidentify,
    surrogate_names,
)

SPAN = StructType(
    [
        StructField("deid_start", IntegerType(), False),
        StructField("deid_end", IntegerType(), False),
        StructField("orig_start", IntegerType(), False),
        StructField("orig_end", IntegerType(), False),
        StructField("kind", StringType(), False),
        StructField("original", StringType(), False),
    ]
)
RESULT = StructType(
    [StructField("text", StringType(), False), StructField("spans", ArrayType(SPAN), False)]
)


class MissingDeidKeyError(RuntimeError):
    """Notes cannot be de-identified without DEID_HMAC_KEY."""


def deidentified_notes(
    notes: DataFrame, patients: DataFrame, persons: DataFrame, key: str
) -> DataFrame:
    """Silver notes with person_id, note_text de-identified, and `phi_spans`."""
    if not key:
        raise MissingDeidKeyError("set DEID_HMAC_KEY: gold never stores identified note text")

    @F.udf(RESULT)
    def deid(
        text: str, tenant_id: str, person_id: int, mrn: str, given: str | None,
        family: str | None, birth_date: date | None, note_date: date,
    ) -> tuple[str, list[tuple[int, int, int, int, str, str]]]:  # fmt: skip
        identity = Identity(mrn, given, family, birth_date)
        result = deidentify(
            text,
            identity,
            surrogate_names(key, tenant_id, person_id, identity),
            date_shift_days(key, tenant_id, person_id),
            note_date,
        )
        return result.text, [
            (s.deid_start, s.deid_end, s.orig_start, s.orig_end, s.kind, s.original)
            for s in result.spans
        ]

    ids = patients.select(
        "tenant_id", "patient_source_id", "given_name", "family_name", "birth_date",
        F.col("mrn").alias("identity_mrn"),
    )  # fmt: skip
    joined = (
        notes.join(persons.select("tenant_id", "patient_source_id", "person_id"),
                   ["tenant_id", "patient_source_id"])
        .join(ids, ["tenant_id", "patient_source_id"])
        .withColumn("deid", deid("note_text", "tenant_id", "person_id", "identity_mrn",
                                 "given_name", "family_name", "birth_date", "note_date"))
    )  # fmt: skip
    return joined.select(
        *[c for c in notes.columns if c != "note_text"],
        "person_id",
        F.col("deid.text").alias("note_text"),
        F.col("deid.spans").alias("phi_spans"),
    )


def phi_span_rows(notes: DataFrame) -> DataFrame:
    """One row per replaced span (gold.note_phi_span -> app.note_phi_span)."""
    return notes.select(
        "tenant_id", "note_id", F.posexplode("phi_spans").alias("span_no", "span")
    ).select(
        "tenant_id",
        "note_id",
        F.col("span_no").cast("int").alias("span_no"),
        "span.deid_start",
        "span.deid_end",
        "span.orig_start",
        "span.orig_end",
        "span.kind",
        "span.original",
    )
