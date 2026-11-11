"""The OMOP vocabulary subset (Athena-format files) as Spark DataFrames.

Both local runs and Databricks read the same subset folder (`make vocab-subset`), which also
holds our reviewed `SOURCE_TO_CONCEPT_MAP.csv` and `LOCAL_CONCEPT.csv`. Athena files are
tab-separated with a header row, no quoting, and YYYYMMDD dates.
"""

from dataclasses import dataclass

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.runtime import Runtime

NO_QUOTE = "\u0000"


def read_athena(runtime: Runtime, file_name: str) -> DataFrame:
    """One Athena-format file, every column a string."""
    return (
        runtime.spark.read.option("sep", "\t")
        .option("header", True)
        .option("quote", NO_QUOTE)
        .csv(runtime.vocab(file_name))
    )


@dataclass(frozen=True)
class Vocabulary:
    """The vocabulary tables the pipeline needs."""

    concept: DataFrame
    concept_relationship: DataFrame
    concept_synonym: DataFrame
    source_to_concept_map: DataFrame


def load_vocabulary(runtime: Runtime) -> Vocabulary:
    """Concepts (standard + our local ones), relationships, synonyms and reviewed mappings."""
    concept = (
        read_athena(runtime, "CONCEPT.csv")
        .unionByName(read_athena(runtime, "LOCAL_CONCEPT.csv"))
        .withColumn("concept_id", F.col("concept_id").cast("int"))
    )
    relationship = (
        read_athena(runtime, "CONCEPT_RELATIONSHIP.csv")
        .where(F.col("invalid_reason").isNull())
        .select(
            F.col("concept_id_1").cast("int").alias("concept_id_1"),
            F.col("concept_id_2").cast("int").alias("concept_id_2"),
            "relationship_id",
        )
    )
    synonym = read_athena(runtime, "CONCEPT_SYNONYM.csv").select(
        F.col("concept_id").cast("int").alias("concept_id"),
        "concept_synonym_name",
        F.col("language_concept_id").cast("int").alias("language_concept_id"),
    )
    stcm = (
        read_athena(runtime, "SOURCE_TO_CONCEPT_MAP.csv")
        .where(F.col("invalid_reason").isNull())
        .select(
            "source_code",
            "source_vocabulary_id",
            F.col("target_concept_id").cast("int").alias("target_concept_id"),
        )
    )
    return Vocabulary(concept, relationship, synonym, stcm)
