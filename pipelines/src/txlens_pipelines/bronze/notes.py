"""Clinical note text files -> bronze.note_files: one row per file with its full text."""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.bronze.files import LINEAGE, file_text, tenant_from_folder

TABLE = "note_files"


def ingest(files: DataFrame) -> DataFrame:
    """New note files -> bronze rows (source_row is always 1: the whole file)."""
    return (
        file_text(files)
        .withColumn("tenant_id", tenant_from_folder())
        .withColumn("source_row", F.lit(1).cast("int"))
        .select(*LINEAGE, "file_bytes", "text")
    )
