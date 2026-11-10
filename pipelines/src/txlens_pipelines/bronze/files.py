"""Reading raw files with lineage: one row per file, with its bytes, text and fingerprint."""

import re

from pyspark.errors import AnalysisException
from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.runtime import Runtime

BINARY_FILE = "path string, modificationTime timestamp, length long, content binary"
LINEAGE = ["tenant_id", "source_file", "source_row", "file_sha256", "ingested_at", "batch_id"]
FILE_KEYS = ["tenant_id", "source_file", "file_sha256", "source_row"]


def relative_path(raw_root: str) -> Column:
    """The file path below the raw root ('labs/center_a_labs.csv'), however Spark prints it."""
    root = raw_root.rstrip("/").split(":", 1)[-1].lstrip("/")
    return F.regexp_extract(F.col("path"), rf"{re.escape(root)}/(.*)$", 1)


def load_existing(runtime: Runtime, patterns: list[str]) -> DataFrame:
    """binaryFile rows for every pattern; a pattern matching nothing (an empty feed, a center
    without biopsies) contributes no rows instead of failing the run."""
    result = runtime.spark.createDataFrame([], BINARY_FILE)
    for pattern in patterns:
        try:
            files = runtime.spark.read.format("binaryFile").load(runtime.raw(pattern))
            _ = files.schema  # Spark Connect resolves paths lazily; this forces it
        except AnalysisException as error:
            if "PATH_NOT_FOUND" not in str(error):
                raise
            continue
        result = result.unionByName(files)
    return result


def read_files(runtime: Runtime, patterns: list[str], batch_id: str) -> DataFrame:
    """Files matching `patterns` (relative to the raw root): path, bytes, sha256 and metadata."""
    files = load_existing(runtime, patterns)
    return files.select(
        relative_path(runtime.raw_root).alias("source_file"),
        F.sha2(F.col("content"), 256).alias("file_sha256"),
        F.col("length").alias("file_bytes"),
        F.col("modificationTime").alias("file_modified_at"),
        "content",
        F.current_timestamp().alias("ingested_at"),
        F.lit(batch_id).alias("batch_id"),
    )


def tenant_from_folder(position: int = 1) -> Column:
    """Tenant id from the folder in a relative path ('fhir/center_a/x.json' -> 'center_a')."""
    return F.split(F.col("source_file"), "/").getItem(position)


def only_new(runtime: Runtime, files: DataFrame, table: str) -> DataFrame:
    """Drop files whose exact version (path + sha256) is already in the bronze table."""
    if not runtime.exists("bronze", table):
        return files
    seen = runtime.read("bronze", table).select("source_file", "file_sha256").distinct()
    return files.join(seen, ["source_file", "file_sha256"], "left_anti")


def file_text(df: DataFrame) -> DataFrame:
    """Decode the bytes as UTF-8 text and drop them."""
    return df.withColumn("text", F.decode(F.col("content"), "UTF-8")).drop("content")
