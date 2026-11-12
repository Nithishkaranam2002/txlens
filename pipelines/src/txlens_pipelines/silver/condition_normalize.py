"""FHIR conditions -> standard concept, OMOP domain and how it was mapped (no LLM).

A SNOMED code the center sent wins ("Maps to" when it is not standard). Without a code, the
text goes through the shared matcher (textmatch/matcher.py: curated map, exact name or
synonym, normalized, fuzzy, with the clinical-qualifier guard). The matched concept is then
taken to its standard form: itself, or its "Maps to" target plus "Maps to value" (e.g. a
center's code for "History of renal transplant" -> History of event, value Transplant of
kidney). A curated "history of" text names its standard pair directly: History of event in
TXLENS_CONDITION and the procedure in TXLENS_COND_VALUE. The standard concept's domain
decides the OMOP table in gold. Anything unresolved keeps concept 0, its text,
and the best suggestion for the review list.

The distinct texts are few (dozens), so matching runs on the driver in plain Python.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.textmatch.matcher import ConceptMatcher, Match, Term, exact_key
from txlens_pipelines.vocab import Vocabulary

TARGET_DOMAINS = ("Condition", "Observation")
CURATED_VOCABULARY = "TXLENS_CONDITION"
CURATED_VALUE_VOCABULARY = "TXLENS_COND_VALUE"
ENGLISH = 4180186
MAPPED = (  # one row per distinct uncoded (tenant, text)
    "tenant_id string, source_text string, matched_concept_id int, method string, "
    "confidence double, suggested_concept_id int, review_reason string, curated_value_id int"
)


def candidate_concepts(vocab: Vocabulary) -> DataFrame:
    """Valid SNOMED concepts in the domains a problem-list text can name."""
    return vocab.concept.where(
        (F.col("vocabulary_id") == "SNOMED")
        & F.col("domain_id").isin(*TARGET_DOMAINS)
        & F.col("invalid_reason").isNull()
    ).select("concept_id", "concept_name")


def standard_forms(vocab: Vocabulary) -> DataFrame:
    """concept_id -> standard_concept_id, value_concept_id, domain_id (one row per concept).

    Standard concepts stand for themselves; others follow exactly one "Maps to" (plus an
    optional "Maps to value"). Concepts with no or several "Maps to" targets are left out.
    """
    concepts = vocab.concept.where(F.col("invalid_reason").isNull()).select(
        "concept_id", "standard_concept", "domain_id"
    )
    rel = vocab.concept_relationship
    maps_to = (
        rel.where(F.col("relationship_id") == "Maps to")
        .groupBy("concept_id_1")
        .agg(F.count("*").alias("targets"), F.min("concept_id_2").alias("mapped_id"))
        .where(F.col("targets") == 1)
    )
    value = (
        rel.where(F.col("relationship_id") == "Maps to value")
        .groupBy("concept_id_1")
        .agg(F.min("concept_id_2").alias("value_concept_id"))
    )
    domains = concepts.select(
        F.col("concept_id").alias("standard_concept_id"), F.col("domain_id").alias("domain")
    )
    standard = concepts.where(F.col("standard_concept") == "S").select(
        "concept_id",
        F.col("concept_id").alias("standard_concept_id"),
        F.lit(None).cast("int").alias("value_concept_id"),
    )
    mapped = (
        concepts.where(F.col("standard_concept").isNull() | (F.col("standard_concept") != "S"))
        .join(maps_to.withColumnRenamed("concept_id_1", "concept_id"), "concept_id")
        .join(value.withColumnRenamed("concept_id_1", "concept_id"), "concept_id", "left")
        .select("concept_id", F.col("mapped_id").alias("standard_concept_id"), "value_concept_id")
    )
    return standard.unionByName(mapped).join(domains, "standard_concept_id")


def build_matcher(vocab: Vocabulary) -> ConceptMatcher:
    """A matcher over candidate names, English synonyms and curated TXLENS_CONDITION rows."""
    candidates = candidate_concepts(vocab)
    preferred = {int(r.concept_id): r.concept_name for r in candidates.collect()}
    synonyms = (
        vocab.concept_synonym.where(F.col("language_concept_id") == ENGLISH)
        .join(candidates.select("concept_id"), "concept_id")
        .collect()
    )
    terms = [Term(cid, name) for cid, name in preferred.items()]
    terms += [Term(int(r.concept_id), r.concept_synonym_name) for r in synonyms]
    return ConceptMatcher(terms, preferred, curated_map(vocab, CURATED_VOCABULARY))


def curated_map(vocab: Vocabulary, source_vocabulary_id: str) -> dict[str, int]:
    """Source text -> target concept for one of our source_to_concept_map vocabularies."""
    rows = vocab.source_to_concept_map.where(
        F.col("source_vocabulary_id") == source_vocabulary_id
    ).collect()
    return {r.source_code: int(r.target_concept_id) for r in rows}


def match_texts(conditions: DataFrame, vocab: Vocabulary) -> DataFrame:
    """(tenant, text) -> matched concept, method, confidence, suggestion, for uncoded rows."""
    texts = (
        conditions.where(F.col("snomed_code").isNull() & F.col("source_text").isNotNull())
        .select("tenant_id", "source_text")
        .distinct()
        .collect()
    )
    matcher = build_matcher(vocab) if texts else None
    values = {exact_key(k): v for k, v in curated_map(vocab, CURATED_VALUE_VOCABULARY).items()}
    rows = []
    for row in texts:
        result: Match = matcher.match(row.source_text) if matcher else Match(None, "review", 0.0)
        rows.append(
            (
                row.tenant_id,
                row.source_text,
                result.concept_id,
                result.method,
                float(result.confidence),
                result.suggestion,
                result.reason,
                values.get(exact_key(row.source_text)) if result.method == "curated" else None,
            )
        )
    return conditions.sparkSession.createDataFrame(rows, MAPPED)


def code_matches(vocab: Vocabulary) -> DataFrame:
    """SNOMED code -> its concept id (any validity status the pipeline accepts)."""
    return vocab.concept.where(
        (F.col("vocabulary_id") == "SNOMED") & F.col("invalid_reason").isNull()
    ).select(F.col("concept_code").alias("snomed_code"), F.col("concept_id").alias("code_id"))


def normalize_conditions(conditions: DataFrame, vocab: Vocabulary) -> DataFrame:
    """`conditions` plus concept, value concept, domain, mapping method and review details."""
    forms = standard_forms(vocab).withColumnRenamed("concept_id", "source_concept_id")
    by_text = match_texts(conditions, vocab).withColumn("uncoded", F.lit(True))
    joined = (
        conditions.withColumn("uncoded", F.col("snomed_code").isNull())
        .join(code_matches(vocab), "snomed_code", "left")
        .join(by_text, ["tenant_id", "source_text", "uncoded"], "left")
        .withColumn(
            "source_concept_id",
            F.when(F.col("snomed_code").isNotNull(), F.col("code_id")).otherwise(
                F.col("matched_concept_id")
            ),
        )
        .join(forms, "source_concept_id", "left")
    )
    resolved = F.col("standard_concept_id").isNotNull()
    supported = F.col("domain").isin(*TARGET_DOMAINS)
    ok = resolved & supported
    method = (
        F.when(F.col("snomed_code").isNotNull() & ok, F.lit("code"))
        .when(F.col("snomed_code").isNotNull(), F.lit("unmapped"))
        .when(F.col("matched_concept_id").isNotNull() & ok, F.col("method"))
        .otherwise(F.lit("unmapped"))
    )
    reason = (
        F.when(ok, F.lit(None).cast("string"))
        .when(F.col("source_concept_id").isNotNull() & ~resolved, F.lit("no_standard_concept"))
        .when(F.col("source_concept_id").isNotNull(), F.lit("unsupported_domain"))
        .when(F.col("snomed_code").isNotNull(), F.lit("unknown_code"))
        .otherwise(F.col("review_reason"))
    )
    return joined.select(
        *conditions.columns,
        F.when(ok, F.col("standard_concept_id")).otherwise(F.lit(0)).alias("condition_concept_id"),
        F.when(ok, F.coalesce("curated_value_id", "value_concept_id")).alias("value_concept_id"),
        F.when(ok, F.col("domain")).otherwise(F.lit("Condition")).alias("domain_id"),
        F.coalesce("source_concept_id", F.lit(0)).alias("source_concept_id"),
        method.alias("mapping_method"),
        # Accepted: the match confidence. Unmapped: the score of the rejected suggestion.
        F.when(ok, F.coalesce("confidence", F.lit(1.0)))
        .otherwise(F.col("confidence"))
        .alias("mapping_confidence"),
        F.when(~ok, F.coalesce("suggested_concept_id", "source_concept_id")).alias(
            "suggested_concept_id"
        ),
        reason.alias("review_reason"),
    )
