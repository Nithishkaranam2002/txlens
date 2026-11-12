"""Pandera contracts for the silver tables: columns, types and nullability.

A failure here means the pipeline (or a feed's structure) is broken, so the run stops.
Row-level problems never reach this point: they were quarantined before.
"""

import pandera.pyspark as pa
from pyspark.sql.types import (
    DateType,
    DoubleType,
    IntegerType,
    StringType,
    TimestampType,
)


class Lineage(pa.DataFrameModel):  # type: ignore[type-arg]
    """Columns every silver table has."""

    tenant_id: StringType = pa.Field(nullable=False)
    bronze_source_file: StringType = pa.Field(nullable=False)
    bronze_source_row: IntegerType = pa.Field(nullable=False)


class Patients(Lineage):
    patient_source_id: StringType = pa.Field(nullable=False)
    mrn: StringType = pa.Field(nullable=False)
    given_name: StringType = pa.Field(nullable=True)
    family_name: StringType = pa.Field(nullable=True)
    sex: StringType = pa.Field(nullable=True, isin=["male", "female"])
    birth_date: DateType = pa.Field(nullable=False)
    death_date: DateType = pa.Field(nullable=True)


class Encounters(Lineage):
    encounter_source_id: StringType = pa.Field(nullable=False)
    patient_source_id: StringType = pa.Field(nullable=False)
    kind: StringType = pa.Field(nullable=True)
    start_date: DateType = pa.Field(nullable=False)
    end_date: DateType = pa.Field(nullable=False)


class Procedures(Lineage):
    procedure_source_id: StringType = pa.Field(nullable=False)
    patient_source_id: StringType = pa.Field(nullable=False)
    kind: StringType = pa.Field(nullable=False, isin=["transplant", "biopsy"])
    organ: StringType = pa.Field(nullable=False, isin=["kidney", "heart"])
    procedure_date: DateType = pa.Field(nullable=False)


class Conditions(Lineage):
    condition_source_id: StringType = pa.Field(nullable=False)
    patient_source_id: StringType = pa.Field(nullable=False)
    snomed_code: StringType = pa.Field(nullable=True)
    source_text: StringType = pa.Field(nullable=True)
    onset_date: DateType = pa.Field(nullable=True)
    condition_concept_id: IntegerType = pa.Field(nullable=False)
    value_concept_id: IntegerType = pa.Field(nullable=True)
    domain_id: StringType = pa.Field(nullable=False, isin=["Condition", "Observation"])
    source_concept_id: IntegerType = pa.Field(nullable=False)
    mapping_method: StringType = pa.Field(
        nullable=False, isin=["code", "curated", "exact", "normalized", "fuzzy", "unmapped"]
    )
    mapping_confidence: DoubleType = pa.Field(nullable=True)
    suggested_concept_id: IntegerType = pa.Field(nullable=True)
    review_reason: StringType = pa.Field(nullable=True)


class Medications(Lineage):
    order_source_id: StringType = pa.Field(nullable=False)
    patient_source_id: StringType = pa.Field(nullable=False)
    drug_concept_id: IntegerType = pa.Field(nullable=False)
    mapping_method: StringType = pa.Field(nullable=False)
    start_date: DateType = pa.Field(nullable=False)
    dose_value: DoubleType = pa.Field(nullable=True)


class Labs(Lineage):
    patient_source_id: StringType = pa.Field(nullable=False)
    lab: StringType = pa.Field(nullable=False)
    lab_concept_id: IntegerType = pa.Field(nullable=False)
    collected_at: TimestampType = pa.Field(nullable=False)
    value_as_number: DoubleType = pa.Field(nullable=False)
    value_source_value: StringType = pa.Field(nullable=False)
    standard_unit: StringType = pa.Field(nullable=False)


class Notes(Lineage):
    note_source_id: StringType = pa.Field(nullable=False)
    patient_source_id: StringType = pa.Field(nullable=False)
    note_date: DateType = pa.Field(nullable=False)
    note_text: StringType = pa.Field(nullable=False)


class Duplicates(Lineage):
    duplicate_of_row: IntegerType = pa.Field(nullable=False)
