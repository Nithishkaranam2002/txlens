"""Code -> concept lookups against the vocabulary subset."""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.vocab import Vocabulary


def code_lookup(vocab: Vocabulary, vocabulary_id: str) -> DataFrame:
    """concept_code -> source_concept_id and standard concept_id (itself or "Maps to")."""
    concepts = vocab.concept.where(
        (F.col("vocabulary_id") == vocabulary_id) & F.col("invalid_reason").isNull()
    ).select("concept_code", "concept_id", "standard_concept")
    maps_to = vocab.concept_relationship.where(F.col("relationship_id") == "Maps to").select(
        F.col("concept_id_1").alias("concept_id"), F.col("concept_id_2").alias("mapped_id")
    )
    return (
        concepts.join(maps_to, "concept_id", "left")
        .groupBy("concept_code", "concept_id", "standard_concept")
        .agg(F.min("mapped_id").alias("mapped_id"))
        .select(
            "concept_code",
            F.col("concept_id").alias("source_concept_id"),
            F.when(F.col("standard_concept") == "S", F.col("concept_id"))
            .otherwise(F.col("mapped_id"))
            .alias("standard_concept_id"),
        )
    )


def concept_id(vocab: Vocabulary, vocabulary_id: str, code: str) -> int:
    """The id of one concept by code (0 when the vocabulary subset lacks it)."""
    row = (
        vocab.concept.where(
            (F.col("vocabulary_id") == vocabulary_id) & (F.col("concept_code") == code)
        )
        .select("concept_id")
        .first()
    )
    return int(row.concept_id) if row else 0


def standard_ids(vocab: Vocabulary) -> DataFrame:
    """Ids of valid standard concepts (to tell local, non-standard targets apart)."""
    return vocab.concept.where(
        (F.col("standard_concept") == "S") & F.col("invalid_reason").isNull()
    ).select("concept_id")
