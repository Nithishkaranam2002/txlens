"""gold.mapping_gaps: every source value that did not reach a standard concept, with counts.

This is the human review queue for mappings: a reviewer adds a source_to_concept_map row (or
accepts the gap), and the next run picks it up. Free-text conditions also carry the matcher's
best suggestion, its score and why it was not accepted (condition_normalize.py).
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

# (OMOP table, concept column, source value column, reason when the concept is 0)
CHECKS = (
    ("measurement", "measurement_concept_id", "measurement_source_value", "no_standard_concept"),
    ("measurement", "unit_concept_id", "unit_source_value", "no_standard_unit"),
    ("drug_exposure", "drug_concept_id", "drug_source_value", "unmapped_drug"),
    ("drug_exposure", "route_concept_id", "route_source_value", "unmapped_route"),
    ("condition_occurrence", "condition_concept_id", "condition_source_value", "unmapped"),
    ("procedure_occurrence", "procedure_concept_id", "procedure_source_value", "unmapped"),
)  # fmt: skip
QUARANTINED = ("unmapped_code", "unknown_unit")


def gaps_in(df: DataFrame, table: str, concept: str, source: str, reason: str) -> DataFrame:
    """Rows of one table whose concept is 0 but which do have a source value."""
    return df.where((F.col(concept) == 0) & F.col(source).isNotNull()).select(
        "tenant_id",
        F.lit(table).alias("omop_table"),
        F.lit(concept).alias("concept_field"),
        F.col(source).alias("source_value"),
        F.lit(reason).alias("reason"),
    )


def condition_suggestions(conditions: DataFrame) -> DataFrame:
    """(tenant, condition source value) -> suggested concept, score and review reason."""
    return (
        conditions.where(F.col("mapping_method") == "unmapped")
        .select(
            "tenant_id",
            F.lit("condition_occurrence").alias("omop_table"),
            F.coalesce("snomed_code", "source_text").alias("source_value"),
            "suggested_concept_id",
            F.col("mapping_confidence").alias("suggestion_score"),
            "review_reason",
        )
        .dropDuplicates(["tenant_id", "omop_table", "source_value"])
    )


def mapping_gaps(
    gold: dict[str, DataFrame], quarantine: DataFrame, conditions: DataFrame
) -> DataFrame:
    """One row per (tenant, field, source value, reason) with how many rows it affects, plus
    the suggestion for free-text conditions."""
    frames = [gaps_in(gold[t], t, c, s, r) for t, c, s, r in CHECKS]
    frames.append(
        quarantine.where(F.col("reason_code").isin(*QUARANTINED)).select(
            "tenant_id",
            F.lit("measurement").alias("omop_table"),
            F.lit("quarantined").alias("concept_field"),
            F.col("detail").alias("source_value"),
            F.concat(F.lit("quarantined_"), F.col("reason_code")).alias("reason"),
        )
    )
    combined = frames[0]
    for frame in frames[1:]:
        combined = combined.unionByName(frame)
    counted = combined.groupBy(
        "tenant_id", "omop_table", "concept_field", "source_value", "reason"
    ).agg(F.count("*").cast("long").alias("rows"))
    return counted.join(
        condition_suggestions(conditions), ["tenant_id", "omop_table", "source_value"], "left"
    )
