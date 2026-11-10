"""Where tables live: path-based Delta locally, Unity Catalog tables on Databricks.

Transforms never touch storage; they take and return DataFrames. Only a Runtime reads and
writes tables, so the same transform code runs in both places.

Write modes:
- `append_new`: insert-only MERGE on key columns. Bronze uses it, so bronze rows are never
  updated or deleted, and re-ingesting the same file adds nothing.
- `replace_tenants`: overwrite only the given tenants' rows (Delta `replaceWhere`). Silver and
  gold use it, so re-running one tenant leaves the others untouched.
"""

from abc import ABC, abstractmethod
from pathlib import Path

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, SparkSession

TENANT_COLUMN = "tenant_id"


def tenant_predicate(tenants: list[str]) -> str:
    """SQL predicate selecting these tenants (ids are validated as [a-z0-9_]+)."""
    for tenant in tenants:
        if not tenant.replace("_", "").isalnum() or not tenant.islower():
            raise ValueError(f"invalid tenant id: {tenant!r}")
    return f"{TENANT_COLUMN} IN ({', '.join(repr(t) for t in tenants)})"


class Runtime(ABC):
    """Reads and writes the medallion tables."""

    def __init__(self, spark: SparkSession, raw_root: str, vocab_root: str) -> None:
        self.spark = spark
        self.raw_root = raw_root.rstrip("/")
        self.vocab_root = vocab_root.rstrip("/")

    @abstractmethod
    def exists(self, layer: str, name: str) -> bool:
        """True if the table has been created."""

    @abstractmethod
    def read(self, layer: str, name: str) -> DataFrame:
        """The whole table."""

    @abstractmethod
    def delta_table(self, layer: str, name: str) -> DeltaTable:
        """The table as a DeltaTable (for MERGE)."""

    @abstractmethod
    def create(self, df: DataFrame, layer: str, name: str) -> None:
        """Create the table from `df`, partitioned by tenant."""

    @abstractmethod
    def overwrite_where(self, df: DataFrame, layer: str, name: str, predicate: str) -> None:
        """Replace the rows matching `predicate` with `df`."""

    def raw(self, *parts: str) -> str:
        """Path of a raw file or folder."""
        return "/".join([self.raw_root, *parts])

    def vocab(self, file_name: str) -> str:
        """Path of a vocabulary subset file."""
        return f"{self.vocab_root}/{file_name}"

    def read_tenants(self, layer: str, name: str, tenants: list[str]) -> DataFrame:
        """Rows of some tenants."""
        return self.read(layer, name).where(tenant_predicate(tenants))

    def replace_tenants(self, df: DataFrame, layer: str, name: str, tenants: list[str]) -> None:
        """Overwrite these tenants' rows with `df` (every row must belong to one of them)."""
        if not self.exists(layer, name):
            self.create(df, layer, name)
            return
        self.overwrite_where(df, layer, name, tenant_predicate(tenants))

    def append_new(self, df: DataFrame, layer: str, name: str, keys: list[str]) -> None:
        """Insert rows whose key is not in the table yet; never update or delete."""
        if not self.exists(layer, name):
            self.create(df.dropDuplicates(keys), layer, name)
            return
        condition = " AND ".join(f"t.{k} = s.{k}" for k in keys)
        (
            self.delta_table(layer, name)
            .alias("t")
            .merge(df.dropDuplicates(keys).alias("s"), condition)
            .whenNotMatchedInsertAll()
            .execute()
        )


class LocalRuntime(Runtime):
    """Delta tables as folders: <lake_root>/<layer>/<name>."""

    def __init__(self, spark: SparkSession, raw_root: str, vocab_root: str, lake_root: str) -> None:
        super().__init__(spark, raw_root, vocab_root)
        self.lake_root = lake_root.rstrip("/")

    def path(self, layer: str, name: str) -> str:
        """Folder of one table."""
        return f"{self.lake_root}/{layer}/{name}"

    def exists(self, layer: str, name: str) -> bool:
        return (Path(self.path(layer, name)) / "_delta_log").is_dir()

    def read(self, layer: str, name: str) -> DataFrame:
        return self.spark.read.format("delta").load(self.path(layer, name))

    def delta_table(self, layer: str, name: str) -> DeltaTable:
        return DeltaTable.forPath(self.spark, self.path(layer, name))

    def create(self, df: DataFrame, layer: str, name: str) -> None:
        writer = df.write.format("delta").mode("overwrite")
        writer.partitionBy(TENANT_COLUMN).save(self.path(layer, name))

    def overwrite_where(self, df: DataFrame, layer: str, name: str, predicate: str) -> None:
        df.write.format("delta").mode("overwrite").option("replaceWhere", predicate).save(
            self.path(layer, name)
        )


class DatabricksRuntime(Runtime):
    """Unity Catalog tables: <catalog>.txlens_<layer>.<name>; raw files in a UC volume."""

    def __init__(self, spark: SparkSession, raw_root: str, vocab_root: str, catalog: str) -> None:
        super().__init__(spark, raw_root, vocab_root)
        self.catalog = catalog

    def table(self, layer: str, name: str) -> str:
        """Three-part table name."""
        return f"{self.catalog}.txlens_{layer}.{name}"

    def exists(self, layer: str, name: str) -> bool:
        return bool(self.spark.catalog.tableExists(self.table(layer, name)))

    def read(self, layer: str, name: str) -> DataFrame:
        return self.spark.read.table(self.table(layer, name))

    def delta_table(self, layer: str, name: str) -> DeltaTable:
        return DeltaTable.forName(self.spark, self.table(layer, name))

    def create(self, df: DataFrame, layer: str, name: str) -> None:
        self.spark.sql(f"CREATE SCHEMA IF NOT EXISTS {self.catalog}.txlens_{layer}")
        writer = df.write.format("delta").mode("overwrite")
        writer.partitionBy(TENANT_COLUMN).saveAsTable(self.table(layer, name))

    def overwrite_where(self, df: DataFrame, layer: str, name: str, predicate: str) -> None:
        df.write.format("delta").mode("overwrite").option("replaceWhere", predicate).saveAsTable(
            self.table(layer, name)
        )
