"""Free-text drug names -> RxNorm ingredient concept, without substring matching.

Order (the first that gives exactly one standard ingredient wins):
1. the RxNorm code the center sent ("Maps to" if that concept is not standard)
2. a reviewed source_to_concept_map row (TXLENS_DRUG): a person checked it, so it beats
   automatic matching ("mycophenolate" would otherwise exactly match a different ingredient)
3. an exact ingredient name or synonym
4. an exact RxNorm Brand Name, followed through "Brand name of" to its ingredient
Anything else gets concept 0, keeps its text, and goes to the mapping-gaps report.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.silver.common import normalize_text
from txlens_pipelines.vocab import Vocabulary

METHODS = ("rxnorm_code", "reviewed_map", "name", "synonym", "brand", "unmapped")


def standard_ingredients(vocab: Vocabulary) -> DataFrame:
    """Valid standard RxNorm ingredients."""
    return vocab.concept.where(
        (F.col("vocabulary_id") == "RxNorm")
        & (F.col("concept_class_id") == "Ingredient")
        & (F.col("standard_concept") == "S")
        & F.col("invalid_reason").isNull()
    ).select("concept_id", "concept_name")


def name_lookup(vocab: Vocabulary) -> DataFrame:
    """key (normalized name) -> concept_id, method; only keys with one unambiguous answer."""
    ingredients = standard_ingredients(vocab)
    reviewed = vocab.source_to_concept_map.where(
        F.col("source_vocabulary_id") == "TXLENS_DRUG"
    ).select(
        F.col("source_code").alias("key"),
        F.col("target_concept_id").alias("concept_id"),
        F.lit("reviewed_map").alias("method"),
        F.lit(1).alias("priority"),
    )
    names = ingredients.select(
        normalize_text(F.col("concept_name")).alias("key"),
        "concept_id",
        F.lit("name").alias("method"),
        F.lit(2).alias("priority"),
    )
    synonyms = vocab.concept_synonym.join(ingredients.select("concept_id"), "concept_id").select(
        normalize_text(F.col("concept_synonym_name")).alias("key"),
        "concept_id",
        F.lit("synonym").alias("method"),
        F.lit(3).alias("priority"),
    )
    brand_names = vocab.concept.where(
        (F.col("vocabulary_id") == "RxNorm")
        & (F.col("concept_class_id") == "Brand Name")
        & F.col("invalid_reason").isNull()
    ).select(F.col("concept_id").alias("concept_id_1"), "concept_name")
    brands = (
        brand_names.join(
            vocab.concept_relationship.where(F.col("relationship_id") == "Brand name of"),
            "concept_id_1",
        )
        .join(ingredients.select(F.col("concept_id").alias("concept_id_2")), "concept_id_2")
        .select(
            normalize_text(F.col("concept_name")).alias("key"),
            F.col("concept_id_2").alias("concept_id"),
            F.lit("brand").alias("method"),
            F.lit(4).alias("priority"),
        )
    )
    candidates = reviewed.unionByName(names).unionByName(synonyms).unionByName(brands).distinct()
    best = candidates.groupBy("key").agg(F.min("priority").alias("priority"))
    top = candidates.join(best, ["key", "priority"])
    unique = top.groupBy("key").agg(
        F.count_distinct("concept_id").alias("answers"),
        F.first("concept_id").alias("concept_id"),
        F.first("method").alias("method"),
    )
    return unique.where(F.col("answers") == 1).select("key", "concept_id", "method")


def code_lookup(vocab: Vocabulary) -> DataFrame:
    """RxNorm code -> standard concept (itself, or its "Maps to" target)."""
    rxnorm = vocab.concept.where(
        (F.col("vocabulary_id") == "RxNorm") & F.col("invalid_reason").isNull()
    )
    direct = rxnorm.where(F.col("standard_concept") == "S").select(
        F.col("concept_code").alias("rxnorm_code"), "concept_id"
    )
    mapped = (
        rxnorm.where(F.col("standard_concept").isNull())
        .select(
            F.col("concept_code").alias("rxnorm_code"), F.col("concept_id").alias("concept_id_1")
        )
        .join(
            vocab.concept_relationship.where(F.col("relationship_id") == "Maps to"), "concept_id_1"
        )
        .select("rxnorm_code", F.col("concept_id_2").alias("concept_id"))
    )
    return direct.unionByName(mapped).dropDuplicates(["rxnorm_code"])


def normalize_drugs(meds: DataFrame, vocab: Vocabulary) -> DataFrame:
    """`meds` (with rxnorm_code and drug_source_text) plus drug_concept_id and mapping_method."""
    by_code = code_lookup(vocab).select("rxnorm_code", F.col("concept_id").alias("code_concept_id"))
    by_name = name_lookup(vocab).select(
        "key", F.col("concept_id").alias("name_concept_id"), F.col("method").alias("name_method")
    )
    joined = (
        meds.withColumn("key", normalize_text(F.col("drug_source_text")))
        .join(by_code, "rxnorm_code", "left")
        .join(by_name, "key", "left")
    )
    return joined.select(
        *meds.columns,
        F.coalesce("code_concept_id", "name_concept_id", F.lit(0)).alias("drug_concept_id"),
        F.when(F.col("code_concept_id").isNotNull(), F.lit("rxnorm_code"))
        .when(F.col("name_concept_id").isNotNull(), F.col("name_method"))
        .otherwise(F.lit("unmapped"))
        .alias("mapping_method"),
    )
