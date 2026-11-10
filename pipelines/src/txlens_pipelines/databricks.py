"""Entry points for Databricks (Free Edition, serverless): the same layers, Unity Catalog storage.

Raw files, ground truth and the vocabulary subset live in one Unity Catalog volume, unzipped
from the upload made with `make databricks-data`:

    /Volumes/<catalog>/txlens/data/raw/...
    /Volumes/<catalog>/txlens/data/ground_truth/...
    /Volumes/<catalog>/txlens/data/vocab_subset/...

Tables go to <catalog>.txlens_bronze / txlens_silver / txlens_gold. There is no Postgres load
here: the local database is not reachable from Databricks.
"""

import zipfile
from dataclasses import dataclass
from pathlib import Path

from pyspark.sql import SparkSession

from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.pipeline import run
from txlens_pipelines.runtime import DatabricksRuntime

SCHEMA = "txlens"
VOLUME = "data"
LAYERS = ("bronze", "silver", "gold")


@dataclass(frozen=True)
class VolumePaths:
    """Where the uploaded data lives in the volume."""

    catalog: str

    @property
    def root(self) -> str:
        return f"/Volumes/{self.catalog}/{SCHEMA}/{VOLUME}"

    @property
    def raw(self) -> str:
        return f"{self.root}/raw"

    @property
    def ground_truth(self) -> str:
        return f"{self.root}/ground_truth"

    @property
    def vocab(self) -> str:
        return f"{self.root}/vocab_subset"

    @property
    def reports(self) -> str:
        return f"{self.root}/reports"


def setup(spark: SparkSession, catalog: str) -> VolumePaths:
    """Create the schema and the volume that holds the uploaded files."""
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{SCHEMA}")
    spark.sql(f"CREATE VOLUME IF NOT EXISTS {catalog}.{SCHEMA}.{VOLUME}")
    return VolumePaths(catalog)


def unpack(zip_path: str, paths: VolumePaths) -> list[str]:
    """Extract the uploaded zip into the volume; returns the top-level folders found."""
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(paths.root)
    return sorted(p.name for p in Path(paths.root).iterdir() if p.is_dir())


def run_pipeline(
    spark: SparkSession,
    catalog: str,
    layers: list[str] | None = None,
    tenants: list[str] | None = None,
) -> dict[str, dict[str, int]]:
    """Run bronze -> silver -> gold (or a subset) on Unity Catalog."""
    chosen = layers or list(LAYERS)
    if "load" in chosen:
        raise ValueError("the Postgres load runs locally only (make pipeline)")
    paths = VolumePaths(catalog)
    runtime = DatabricksRuntime(spark, paths.raw, paths.vocab, catalog)
    return run(runtime, PipelineConfig(paths.raw, paths.vocab).only(tenants), chosen)
