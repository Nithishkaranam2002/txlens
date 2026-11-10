"""FHIR MedicationRequest -> silver.medications: normalized drug, dose, route, dates.

Center A sends structured dosage, center B only text such as "prograf 3mg oral bid", center
C both. Structured fields win; the text is parsed with one fixed pattern otherwise.
"""

from pyspark.sql import DataFrame
from pyspark.sql import functions as F

from txlens_pipelines.quality import Rule, apply_rules
from txlens_pipelines.silver.common import (
    CODING,
    LINEAGE,
    RXNORM_SYSTEM,
    coding_code,
    fhir_date,
    fhir_resources,
    patient_reference,
)
from txlens_pipelines.silver.drug_normalize import normalize_drugs
from txlens_pipelines.silver.encounters import known_patient, unknown_patient_rule
from txlens_pipelines.vocab import Vocabulary

TABLE = "medications"
DOSAGE = (
    "array<struct<text: string, timing: struct<code: struct<text: string>>, "
    "route: struct<text: string>, "
    "doseAndRate: array<struct<doseQuantity: struct<value: double, unit: string>>>>>"
)
SCHEMA = (
    "id string, status string, subject struct<reference: string>, "
    f"medicationCodeableConcept struct<coding: {CODING}, text: string>, authoredOn string, "
    f"dosageInstruction {DOSAGE}, "
    "dispenseRequest struct<validityPeriod: struct<start: string, end: string>>"
)
# "<name> <dose><unit> <route> <frequency>", e.g. "pdn 7.5mg oral daily".
SIG = r"^(.*?)\s+(\d+(?:\.\d+)?)\s*([a-z/%]+)\s+(\S+)\s+(.+)$"


def sig_part(text: F.Column, group: int) -> F.Column:
    """One part of a free-text dosage, or NULL when the text does not follow the pattern."""
    part = F.regexp_extract(F.lower(F.trim(text)), SIG, group)
    return F.when(part != "", part)


def build(
    bronze_fhir: DataFrame, patients: DataFrame, vocab: Vocabulary
) -> tuple[DataFrame, DataFrame]:
    """(clean medication orders, rows for quarantine)."""
    rows = fhir_resources(bronze_fhir, "MedicationRequest", SCHEMA)
    dose = F.try_element_at("r.dosageInstruction", F.lit(1))
    quantity = F.try_element_at(dose["doseAndRate"], F.lit(1))["doseQuantity"]
    start = F.coalesce("r.dispenseRequest.validityPeriod.start", "r.authoredOn")
    parsed = rows.select(
        *LINEAGE,
        F.col("r.id").alias("order_source_id"),
        patient_reference(F.col("r.subject.reference")).alias("patient_source_id"),
        F.col("r.medicationCodeableConcept.text").alias("drug_source_text"),
        coding_code(F.col("r.medicationCodeableConcept.coding"), RXNORM_SYSTEM).alias(
            "rxnorm_code"
        ),
        F.coalesce(quantity["value"], sig_part(dose["text"], 2).cast("double")).alias("dose_value"),
        F.coalesce(quantity["unit"], sig_part(dose["text"], 3)).alias("dose_unit"),
        F.coalesce(dose["route"]["text"], sig_part(dose["text"], 4)).alias("route"),
        F.coalesce(dose["timing"]["code"]["text"], sig_part(dose["text"], 5)).alias("frequency"),
        dose["text"].alias("sig_text"),
        F.col("r.status").alias("status"),
        start.alias("start_source"),
        fhir_date(start).alias("start_date"),
        fhir_date(F.col("r.dispenseRequest.validityPeriod.end")).alias("end_date"),
    )
    rules = [
        unknown_patient_rule(),
        Rule("bad_date", F.col("start_date").isNull(), F.col("start_source")),
        Rule(
            "missing_value",
            F.col("drug_source_text").isNull() & F.col("rxnorm_code").isNull(),
            F.lit("no drug code or name"),
        ),
    ]
    good, bad = apply_rules(known_patient(parsed, patients), rules)
    return normalize_drugs(good.drop("patient_known", "start_source"), vocab), bad
