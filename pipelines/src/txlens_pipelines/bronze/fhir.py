"""FHIR bundles -> bronze.fhir_resources: one row per bundle entry, the resource as JSON text.

The JSON content is unchanged (Spark re-serializes it compactly, so only whitespace differs);
the original bytes stay in the raw store and `file_sha256` identifies them.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.bronze.files import LINEAGE, file_text, tenant_from_folder

TABLE = "fhir_resources"
# Declaring `resource` as a string keeps each resource as JSON text rather than parsing it.
BUNDLE = "struct<resourceType: string, entry: array<struct<resource: string>>>"


def ingest(files: DataFrame) -> DataFrame:
    """New FHIR files -> one bronze row per resource (source_row = 1-based entry index)."""
    bundles = file_text(files).withColumn("bundle", F.from_json("text", BUNDLE))
    entries = bundles.select(
        "*", F.posexplode(F.col("bundle.entry")).alias("entry_index", "entry")
    ).withColumn("source_row", F.col("entry_index") + 1)
    resource = F.col("entry.resource")
    return entries.withColumn("tenant_id", tenant_from_folder()).select(
        *LINEAGE,
        F.get_json_object(resource, "$.resourceType").alias("resource_type"),
        F.get_json_object(resource, "$.id").alias("resource_id"),
        resource.alias("resource_json"),
    )
