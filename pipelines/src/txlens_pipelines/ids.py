"""Stable OMOP ids from natural keys, kept in a persistent id map (`gold.id_map`).

OMOP ids are 32-bit integers, so hashing a key into one would collide. Instead every
(tenant, OMOP table, natural key) gets an id the first time it is seen and keeps it forever:
new keys take the next free numbers inside the tenant's range. Ids therefore do not change
when new data arrives or rows come in a different order. Rows of the map are never deleted,
so an id is never reused for a different key.
"""

from pyspark.sql import Column, DataFrame, Window
from pyspark.sql import functions as F

from txlens_pipelines.config import ID_RANGE, PipelineConfig
from txlens_pipelines.runtime import Runtime

ID_MAP = ("gold", "id_map")
KEY = "natural_key"
MAP_KEYS = ["tenant_id", "omop_table", KEY]


class IdRangeExhaustedError(RuntimeError):
    """A tenant used up its id range for one table."""


def natural_key(*columns: str) -> Column:
    """One string key from several columns ('|'-joined; nulls become empty strings)."""
    return F.concat_ws("|", *(F.coalesce(F.col(c).cast("string"), F.lit("")) for c in columns))


def tenant_bases(df: DataFrame, config: PipelineConfig) -> DataFrame:
    """tenant_id -> first id of the tenant's range minus one, and the range end."""
    rows = [(t.tenant_id, t.ordinal * ID_RANGE, (t.ordinal + 1) * ID_RANGE) for t in config.tenants]
    return df.sparkSession.createDataFrame(rows, "tenant_id string, base long, range_end long")


def existing_ids(runtime: Runtime, omop_table: str, tenants: list[str]) -> DataFrame | None:
    """Id map rows of one table for some tenants, or None before the first run."""
    if not runtime.exists(*ID_MAP):
        return None
    return runtime.read_tenants(*ID_MAP, tenants).where(F.col("omop_table") == omop_table)


def new_ids(
    keys: DataFrame, known: DataFrame | None, omop_table: str, config: PipelineConfig
) -> DataFrame:
    """Ids for keys not in the map yet: max known id (or the range start) + 1, 2, ..."""
    bases = tenant_bases(keys, config)
    if known is None:
        unseen = keys
        highest = bases.select("tenant_id", F.col("base").alias("highest"))
    else:
        unseen = keys.join(known.select("tenant_id", KEY), ["tenant_id", KEY], "left_anti")
        highest = bases.join(
            known.groupBy("tenant_id").agg(F.max("omop_id").alias("used")), "tenant_id", "left"
        ).select("tenant_id", F.coalesce("used", "base").alias("highest"))
    order = Window.partitionBy("tenant_id").orderBy(KEY)
    return (
        unseen.join(highest, "tenant_id")
        .join(bases.select("tenant_id", "range_end"), "tenant_id")
        .withColumn("omop_id", F.col("highest") + F.row_number().over(order))
        .select(
            "tenant_id",
            F.lit(omop_table).alias("omop_table"),
            KEY,
            "omop_id",
            "range_end",
        )
    )


def assign_ids(
    runtime: Runtime, df: DataFrame, omop_table: str, id_column: str, config: PipelineConfig
) -> DataFrame:
    """`df` (with tenant_id and natural_key columns) plus a stable integer `id_column`."""
    tenants = [t.tenant_id for t in config.tenants]
    keys = df.select("tenant_id", KEY).distinct()
    fresh = new_ids(keys, existing_ids(runtime, omop_table, tenants), omop_table, config)
    overflow = fresh.where(F.col("omop_id") >= F.col("range_end")).limit(1).collect()
    if overflow:
        raise IdRangeExhaustedError(f"{overflow[0].tenant_id} has no ids left for {omop_table}")
    runtime.append_new(
        fresh.drop("range_end").withColumn("assigned_at", F.current_timestamp()), *ID_MAP, MAP_KEYS
    )
    mapping = existing_ids(runtime, omop_table, tenants)
    assert mapping is not None  # the map exists now
    return df.join(
        mapping.select("tenant_id", KEY, F.col("omop_id").cast("int").alias(id_column)),
        ["tenant_id", KEY],
    )
