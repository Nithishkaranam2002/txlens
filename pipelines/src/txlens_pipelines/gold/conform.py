"""Make a DataFrame exactly an OMOP table: DDL columns, order and types, plus tenant_id.

Columns the transform did not fill become NULL. Text is cut to the column's varchar length
(Postgres would otherwise reject the row). NOT NULL columns and the primary key are checked:
a violation is a broken contract and stops the run.
"""

import re

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.omop_schema import OMOP_TABLES, PRIMARY_KEYS
from txlens_pipelines.quality import ContractViolationError, check_primary_key

SPARK_TYPES = {"integer": "int", "date": "date", "timestamp": "timestamp", "numeric": "double"}
VARCHAR = re.compile(r"varchar\((\d+)\)")


def typed(df: DataFrame, name: str, data_type: str) -> Column:
    """One OMOP column, cast to its type (NULL if the transform did not produce it)."""
    value = F.col(name) if name in df.columns else F.lit(None)
    length = VARCHAR.fullmatch(data_type)
    if length:
        return F.substring(value.cast("string"), 1, int(length.group(1))).alias(name)
    if data_type == "text":
        return value.cast("string").alias(name)
    return value.cast(SPARK_TYPES[data_type]).alias(name)


def conform(df: DataFrame, table: str) -> DataFrame:
    """tenant_id plus the table's OMOP columns, in DDL order and types."""
    columns = OMOP_TABLES[table]
    return df.select("tenant_id", *(typed(df, name, kind) for name, kind, _ in columns))


def check_omop_contract(df: DataFrame, table: str) -> None:
    """NOT NULL columns are filled; the primary key is unique."""
    required = [name for name, _, nullable in OMOP_TABLES[table] if not nullable]
    nulls = df.select(*[F.sum(F.col(c).isNull().cast("int")).alias(c) for c in required]).first()
    broken = {c: nulls[c] for c in required if nulls is not None and nulls[c]}
    if broken:
        raise ContractViolationError(f"gold.{table}: NULL in NOT NULL columns {broken}")
    if PRIMARY_KEYS[table]:
        check_primary_key(df, f"gold.{table}", list(PRIMARY_KEYS[table]))
