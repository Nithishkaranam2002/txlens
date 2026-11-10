"""Silver entities -> OMOP rows (before ids are assigned and columns conformed).

Every builder returns OMOP column names plus `tenant_id`, `natural_key` (for the stable id)
and the bronze lineage columns. Standard concepts come from codes the centers sent or from the
reviewed source_to_concept_map; anything unmapped gets concept 0 and keeps its source value.
"""

from pyspark.sql import Column, DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.codes import (
    BIOPSY_CODES,
    DONOR_TRANSPLANT_CODES,
    GENDER,
    OPERATORS,
    ROUTE_CODES,
    TYPE_EHR,
    TYPE_LAB,
    TYPE_NOTE,
    TYPE_PRESCRIPTION,
    TYPE_PROBLEM_LIST,
    VISIT,
)
from txlens_pipelines.gold.concepts import code_lookup, standard_ids
from txlens_pipelines.ids import natural_key
from txlens_pipelines.silver.common import LINEAGE, normalize_text
from txlens_pipelines.vocab import Vocabulary


def literal_map(values: dict[str, int]) -> Column:
    """A Spark map from a Python dict."""
    return F.create_map(*[F.lit(x) for pair in values.items() for x in pair])


def person_rows(patients: DataFrame) -> DataFrame:
    """One person per patient; natural key = tenant + MRN.

    No direct identifiers in OMOP: only the birth year is kept, and person_source_value is
    empty. MRN, name and exact birth date go to the separate identity table (identity_rows).
    """
    return patients.select(
        *LINEAGE,
        "patient_source_id",
        natural_key("tenant_id", "mrn").alias("natural_key"),
        F.coalesce(literal_map(GENDER)[F.col("sex")], F.lit(0)).alias("gender_concept_id"),
        F.year("birth_date").alias("year_of_birth"),
        F.lit(0).alias("race_concept_id"),
        F.lit(0).alias("ethnicity_concept_id"),
        F.col("gender_source_value"),
        F.lit(0).alias("gender_source_concept_id"),
        F.col("death_date"),
    )


def identity_rows(patients: DataFrame, persons: DataFrame) -> DataFrame:
    """Direct identifiers per person (gold.patient_identity -> app.patient_identity)."""
    return with_person(patients, persons).select(
        "tenant_id", "person_id", "mrn", "given_name", "family_name", "birth_date"
    )


def with_person(df: DataFrame, persons: DataFrame) -> DataFrame:
    """Adds person_id by the silver patient id (inner join: every silver row has a patient)."""
    return df.join(persons.select("tenant_id", "patient_source_id", "person_id"),
                   ["tenant_id", "patient_source_id"])  # fmt: skip


def visit_rows(encounters: DataFrame, persons: DataFrame) -> DataFrame:
    """One visit per encounter: AMB -> outpatient, IMP -> inpatient."""
    return with_person(encounters, persons).select(
        *LINEAGE,
        "encounter_source_id",
        natural_key("tenant_id", "encounter_source_id").alias("natural_key"),
        "person_id",
        F.coalesce(literal_map(VISIT)[F.col("class_code")], F.lit(0)).alias("visit_concept_id"),
        F.col("start_date").alias("visit_start_date"),
        F.col("end_date").alias("visit_end_date"),
        F.lit(TYPE_EHR).alias("visit_type_concept_id"),
        F.col("kind").alias("visit_source_value"),
        F.lit(0).alias("visit_source_concept_id"),
    )


def visit_by_day(visits: DataFrame) -> DataFrame:
    """(tenant, person, date) -> the first visit that day, for linking labs."""
    return visits.groupBy(
        "tenant_id", "person_id", F.col("visit_start_date").alias("event_date")
    ).agg(F.min("visit_occurrence_id").alias("visit_occurrence_id"))


def drug_rows(meds: DataFrame, persons: DataFrame, vocab: Vocabulary) -> DataFrame:
    """One drug_exposure per order (end date = start when the order has none)."""
    rxnorm = code_lookup(vocab, "RxNorm").select(
        F.col("concept_code").alias("rxnorm_code"), F.col("source_concept_id").alias("rx_source")
    )
    routes = route_lookup(meds, vocab)
    return (
        with_person(meds, persons)
        .join(rxnorm, "rxnorm_code", "left")
        .withColumn("route_key", F.lower(F.trim("route")))
        .join(routes, "route_key", "left")
        .select(
            *LINEAGE,
            natural_key("tenant_id", "order_source_id").alias("natural_key"),
            "person_id",
            "drug_concept_id",
            F.col("start_date").alias("drug_exposure_start_date"),
            F.coalesce("end_date", "start_date").alias("drug_exposure_end_date"),
            F.col("end_date").alias("verbatim_end_date"),
            F.lit(TYPE_PRESCRIPTION).alias("drug_type_concept_id"),
            F.col("sig_text").alias("sig"),
            F.coalesce("route_concept_id", F.lit(0)).alias("route_concept_id"),
            F.coalesce("rxnorm_code", "drug_source_text").alias("drug_source_value"),
            F.coalesce("rx_source", F.lit(0)).alias("drug_source_concept_id"),
            F.col("route").alias("route_source_value"),
            F.col("dose_unit").alias("dose_unit_source_value"),
        )
    )


def route_lookup(df: DataFrame, vocab: Vocabulary) -> DataFrame:
    """Lower-case route text -> SNOMED route concept."""
    rows = [(key, code) for key, (_, code) in ROUTE_CODES.items()]
    keys = df.sparkSession.createDataFrame(rows, "route_key string, concept_code string")
    snomed = code_lookup(vocab, "SNOMED").select(
        "concept_code", F.col("standard_concept_id").alias("route_concept_id")
    )
    return keys.join(snomed, "concept_code").select("route_key", "route_concept_id")


def measurement_rows(
    labs: DataFrame, persons: DataFrame, visits: DataFrame, vocab: Vocabulary
) -> DataFrame:
    """One measurement per lab result; non-standard (local) concepts become 0."""
    ucum = code_lookup(vocab, "UCUM").select(
        F.col("concept_code").alias("ucum_code"), F.col("standard_concept_id").alias("unit_id")
    )
    standard = standard_ids(vocab).select(
        F.col("concept_id").alias("lab_concept_id"), F.lit(True).alias("is_standard")
    )
    df = (
        with_person(labs, persons)
        .withColumn("event_date", F.to_date("collected_at"))
        .join(visit_by_day(visits), ["tenant_id", "person_id", "event_date"], "left")
        .join(F.broadcast(ucum), "ucum_code", "left")
        .join(F.broadcast(standard), "lab_concept_id", "left")
    )
    source_file = F.element_at(F.split("bronze_source_file", "/"), -1)
    return df.select(
        *LINEAGE,
        # The result itself, not its row number: stable across re-sent or reordered files.
        F.concat_ws(
            "|", "tenant_id", source_file, "mrn", "measurement_source_value",
            F.date_format("collected_at", "yyyy-MM-dd'T'HH:mm"), "value_source_value",
            F.coalesce("unit_source_value", F.lit("")),
        ).alias("natural_key"),
        "person_id",
        F.when(F.col("is_standard"), F.col("lab_concept_id")).otherwise(0).alias(
            "measurement_concept_id"
        ),
        F.col("event_date").alias("measurement_date"),
        F.col("collected_at").alias("measurement_datetime"),
        F.lit(TYPE_LAB).alias("measurement_type_concept_id"),
        literal_map(OPERATORS)[F.col("value_operator")].alias("operator_concept_id"),
        "value_as_number",
        F.coalesce("unit_id", F.lit(0)).alias("unit_concept_id"),
        "visit_occurrence_id",
        "measurement_source_value",
        "measurement_source_concept_id",
        "unit_source_value",
        "value_source_value",
    )  # fmt: skip


def condition_rows(
    conditions: DataFrame, persons: DataFrame, transplants: DataFrame, vocab: Vocabulary
) -> DataFrame:
    """One condition per coded or uncoded FHIR Condition (uncoded: concept 0, text kept).

    Problem-list entries carry no date: they are dated at the transplant, when the problem
    list was documented. Graft failure has its own onset date.
    """
    snomed = code_lookup(vocab, "SNOMED").select(
        F.col("concept_code").alias("snomed_code"),
        F.col("source_concept_id").alias("source_id"),
        F.col("standard_concept_id").alias("standard_id"),
    )
    tx_date = transplants.groupBy("tenant_id", "patient_source_id").agg(
        F.min("procedure_date").alias("transplant_date")
    )
    return (
        with_person(conditions, persons)
        .join(tx_date, ["tenant_id", "patient_source_id"], "left")
        .join(snomed, "snomed_code", "left")
        .select(
            *LINEAGE,
            natural_key("tenant_id", "condition_source_id").alias("natural_key"),
            "person_id",
            F.coalesce("standard_id", F.lit(0)).alias("condition_concept_id"),
            F.coalesce("onset_date", "transplant_date").alias("condition_start_date"),
            F.lit(TYPE_PROBLEM_LIST).alias("condition_type_concept_id"),
            F.coalesce("snomed_code", "source_text").alias("condition_source_value"),
            F.coalesce("source_id", F.lit(0)).alias("condition_source_concept_id"),
        )
    )


def procedure_rows(
    procedures: DataFrame, persons: DataFrame, visits: DataFrame, vocab: Vocabulary
) -> DataFrame:
    """Transplants and biopsies (by organ).

    A transplant gets the donor-specific concept when one exists for its organ and donor type,
    else the code the center sent, else the reviewed text mapping.
    """
    snomed = code_lookup(vocab, "SNOMED")
    sent = snomed.select(
        F.col("concept_code").alias("snomed_code"), F.col("standard_concept_id").alias("by_code")
    )
    reviewed = vocab.source_to_concept_map.where(
        F.col("source_vocabulary_id") == "TXLENS_PROCEDURE"
    ).select(F.col("source_code").alias("text_key"), F.col("target_concept_id").alias("by_text"))
    biopsy_keys = procedures.sparkSession.createDataFrame(
        [(organ, code) for organ, (_, code) in BIOPSY_CODES.items()],
        "organ string, concept_code string",
    )
    biopsy = biopsy_keys.join(snomed, "concept_code").select(
        "organ", F.col("standard_concept_id").alias("by_organ")
    )
    donor_keys = procedures.sparkSession.createDataFrame(
        [(organ, donor, code) for (organ, donor), (_, code) in DONOR_TRANSPLANT_CODES.items()],
        "organ string, donor_type string, concept_code string",
    )
    by_donor = donor_keys.join(snomed, "concept_code").select(
        "organ", "donor_type", F.col("standard_concept_id").alias("by_donor")
    )
    visit_ids = visits.select("tenant_id", "encounter_source_id", "visit_occurrence_id")
    df = (
        with_person(procedures, persons)
        .withColumn("text_key", normalize_text(F.col("source_text")))
        .join(sent, "snomed_code", "left")
        .join(reviewed, "text_key", "left")
        .join(biopsy, "organ", "left")
        .join(by_donor, ["organ", "donor_type"], "left")
        .join(visit_ids, ["tenant_id", "encounter_source_id"], "left")
    )
    is_biopsy = F.col("kind") == "biopsy"
    concept = F.when(is_biopsy, F.col("by_organ")).otherwise(
        F.coalesce("by_donor", "by_code", "by_text")
    )
    return df.select(
        *LINEAGE,
        natural_key("tenant_id", "procedure_source_id").alias("natural_key"),
        "person_id",
        F.coalesce(concept, F.lit(0)).alias("procedure_concept_id"),
        "procedure_date",
        F.lit(TYPE_EHR).alias("procedure_type_concept_id"),
        "visit_occurrence_id",
        F.when(is_biopsy, F.concat(F.lit("biopsy of "), F.col("organ")))
        .otherwise(F.coalesce("snomed_code", "source_text"))
        .alias("procedure_source_value"),
        F.lit(0).alias("procedure_source_concept_id"),
    )


def death_rows(persons: DataFrame) -> DataFrame:
    """One death row per person who died."""
    return persons.where(F.col("death_date").isNotNull()).select(
        *LINEAGE,
        "person_id",
        "death_date",
        F.lit(TYPE_EHR).alias("death_type_concept_id"),
    )


def note_rows(
    notes: DataFrame,
    persons: DataFrame,
    visits: DataFrame,
    note_class_id: int,
    language_id: int,
    encoding_id: int,
) -> DataFrame:
    """One note per clinical note file (full text; NLP comes later)."""
    visit_ids = visits.select("tenant_id", "encounter_source_id", "visit_occurrence_id")
    return (
        with_person(notes, persons)
        .join(visit_ids, ["tenant_id", "encounter_source_id"], "left")
        .select(
            *LINEAGE,
            natural_key("tenant_id", "note_source_id").alias("natural_key"),
            "person_id",
            "note_date",
            F.lit(TYPE_NOTE).alias("note_type_concept_id"),
            F.lit(note_class_id).alias("note_class_concept_id"),
            F.col("note_title"),
            F.col("note_text"),
            F.lit(encoding_id).alias("encoding_concept_id"),
            F.lit(language_id).alias("language_concept_id"),
            "visit_occurrence_id",
            F.col("note_source_id").alias("note_source_value"),
        )
    )


def observation_period_rows(persons: DataFrame, events: list[DataFrame]) -> DataFrame:
    """One period per person: from the first to the last clinical event (death included)."""
    dates = events[0]
    for frame in events[1:]:
        dates = dates.unionByName(frame)
    deaths = persons.where(F.col("death_date").isNotNull()).select(
        "tenant_id", "person_id", F.col("death_date").alias("event_date")
    )
    span = (
        dates.unionByName(deaths)
        .groupBy("tenant_id", "person_id")
        .agg(F.min("event_date").alias("start"), F.max("event_date").alias("end"))
    )
    return persons.join(span, ["tenant_id", "person_id"]).select(
        *LINEAGE,
        "natural_key",
        "person_id",
        F.col("start").alias("observation_period_start_date"),
        F.col("end").alias("observation_period_end_date"),
        F.lit(TYPE_EHR).alias("period_type_concept_id"),
    )
