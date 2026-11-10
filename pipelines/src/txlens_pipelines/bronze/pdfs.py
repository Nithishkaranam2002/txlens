"""Pathology PDFs -> bronze.pdf_index: which files exist, their size and fingerprint.

The bytes stay in the raw store; OCR reads them in a later step.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.bronze.files import LINEAGE, tenant_from_folder

TABLE = "pdf_index"


def ingest(files: DataFrame) -> DataFrame:
    """New PDF files -> one index row each (no content)."""
    return (
        files.drop("content")
        .withColumn("tenant_id", tenant_from_folder())
        .withColumn("source_row", F.lit(1).cast("int"))
        .select(*LINEAGE, "file_bytes", "file_modified_at")
    )
