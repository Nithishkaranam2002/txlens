"""Data quality: row-level quarantine and table-level contracts.

- A bad *row* (unparseable date, unknown unit, unmapped code, ...) goes to the quarantine table
  with a reason code, and the run continues.
- A broken *table* contract stops the run: schema mismatch (pandera), null or duplicate primary
  keys, or a quarantine rate above the configured limit. These mean the pipeline or the feed is
  broken, so loading anything downstream would spread the damage.
"""

from dataclasses import dataclass

import pandera.pyspark as pa
from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

QUARANTINE = ("silver", "quarantine")
REASONS = (
    "bad_date",
    "missing_value",
    "unparseable_value",
    "unknown_unit",
    "unmapped_code",
    "unknown_patient",
)


class ContractViolationError(RuntimeError):
    """A table broke its contract; the run stops."""


@dataclass(frozen=True)
class Rule:
    """A row is bad when `failed` is true; the first failing rule names the reason."""

    reason: str
    failed: Column
    detail: Column

    def __post_init__(self) -> None:
        if self.reason not in REASONS:
            raise ValueError(f"unknown quarantine reason: {self.reason}")


def apply_rules(df: DataFrame, rules: list[Rule]) -> tuple[DataFrame, DataFrame]:
    """(good rows, bad rows with reason_code and detail columns)."""
    reason = F.lit(None).cast("string")
    detail = F.lit(None).cast("string")
    for rule in reversed(rules):  # the first rule in the list wins
        hit = F.coalesce(rule.failed, F.lit(False))
        reason = F.when(hit, F.lit(rule.reason)).otherwise(reason)
        detail = F.when(hit, rule.detail.cast("string")).otherwise(detail)
    marked = df.withColumn("reason_code", reason).withColumn("detail", detail)
    good = marked.where(F.col("reason_code").isNull()).drop("reason_code", "detail")
    bad = marked.where(F.col("reason_code").isNotNull())
    return good, bad


def to_quarantine(bad: DataFrame, entity: str, batch_id: str) -> DataFrame:
    """Bad rows in the shared quarantine layout; the full row is kept as JSON."""
    payload = [c for c in bad.columns if c not in ("reason_code", "detail")]
    return bad.select(
        "tenant_id",
        F.lit(entity).alias("entity"),
        "reason_code",
        "detail",
        F.col("bronze_source_file").alias("source_file"),
        F.col("bronze_source_row").alias("source_row"),
        F.to_json(F.struct(*payload)).alias("row_json"),
        F.lit(batch_id).alias("batch_id"),
    )


def check_quarantine_rate(entity: str, good: int, bad: int, max_rate: float) -> None:
    """Fail when more than `max_rate` of an entity's rows were quarantined."""
    total = good + bad
    if total and bad / total > max_rate:
        raise ContractViolationError(
            f"{entity}: {bad:,} of {total:,} rows quarantined ({bad / total:.1%}), "
            f"limit {max_rate:.0%}"
        )


def check_primary_key(df: DataFrame, table: str, columns: list[str]) -> None:
    """Fail on null or duplicate primary keys."""
    nulls = df.where(" OR ".join(f"{c} IS NULL" for c in columns)).limit(1).count()
    if nulls:
        raise ContractViolationError(f"{table}: null primary key in {columns}")
    duplicates = df.groupBy(*columns).count().where("count > 1").limit(3).collect()
    if duplicates:
        raise ContractViolationError(f"{table}: duplicate primary keys, e.g. {duplicates}")


def validate_schema(df: DataFrame, schema: type[pa.DataFrameModel], table: str) -> DataFrame:
    """Run a pandera model; schema-level errors (columns, types, checks) fail the run."""
    validated = schema.validate(df)
    errors = dict(validated.pandera.errors)
    if errors:
        raise ContractViolationError(f"{table}: pandera contract failed: {errors}")
    return validated
