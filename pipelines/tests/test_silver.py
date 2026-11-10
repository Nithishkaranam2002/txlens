"""Silver transforms on hand-written raw rows (see rawfiles.py for what each row tests)."""

from datetime import date, datetime
from pathlib import Path

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from pyspark.sql import Row, SparkSession
from rawfiles import write_raw
from testvocab import write_vocab

from txlens_pipelines.bronze.run import run_bronze
from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.quality import ContractViolationError
from txlens_pipelines.runtime import LocalRuntime
from txlens_pipelines.silver.run import run_silver

pytestmark = pytest.mark.spark
CR = 88.4


def rows(runtime: LocalRuntime, table: str) -> list[Row]:
    found: list[Row] = runtime.read("silver", table).collect()
    return found


def lab(runtime: LocalRuntime, tenant: str, row: int) -> Row:
    found = [
        r for r in rows(runtime, "labs") if (r.tenant_id, r.bronze_source_row) == (tenant, row)
    ]
    assert len(found) == 1, (tenant, row)
    return found[0]


def test_lab_values_are_in_standard_units(built: LocalRuntime) -> None:
    assert lab(built, "center_a", 1).value_as_number == pytest.approx(1.2)
    assert lab(built, "center_a", 2).value_as_number == pytest.approx(1.3)  # whitespace
    assert lab(built, "center_a", 6).value_as_number == pytest.approx(0.31)  # '0.31%'
    creatinine_b = lab(built, "center_b", 1)
    assert creatinine_b.value_as_number == pytest.approx(159 / CR)
    assert (creatinine_b.unit_source_value, creatinine_b.standard_unit) == ("µmol/L", "mg/dL")
    assert lab(built, "center_b", 3).value_as_number == pytest.approx(0.19)  # 0,0019 fraction
    assert lab(built, "center_c", 1).value_as_number == pytest.approx(139 / CR)


def test_lab_concepts_come_from_loinc_local_code_or_name(built: LocalRuntime) -> None:
    assert lab(built, "center_a", 1).measurement_source_concept_id == 3016723  # coded LOINC
    assert lab(built, "center_a", 2).lab_concept_id == 3016723  # name variant
    assert lab(built, "center_c", 1).measurement_source_value == "CRE"  # local code
    dsa = lab(built, "center_c", 6)
    assert (dsa.lab, dsa.lab_concept_id, dsa.ucum_code) == ("dsa_mfi", 2_000_000_001, None)


def test_censored_values_keep_operator_and_text(built: LocalRuntime) -> None:
    egfr = lab(built, "center_b", 4)
    assert (egfr.value_operator, egfr.value_as_number, egfr.value_source_value) == (">", 90, ">90")
    dd = lab(built, "center_c", 2)
    assert (dd.value_operator, dd.value_as_number) == ("<", 0.05)
    assert dd.collected_at == datetime(2022, 6, 9, 19, 31)  # 7:31 PM


def test_dates_follow_each_center_format(built: LocalRuntime) -> None:
    assert lab(built, "center_a", 1).collected_at == datetime(2024, 8, 9, 7, 22)
    assert lab(built, "center_b", 1).collected_at == datetime(2023, 9, 4, 9, 22)  # day first
    assert lab(built, "center_c", 1).collected_at == datetime(2022, 6, 9, 7, 31)


def test_bad_rows_are_quarantined_with_a_reason(built: LocalRuntime) -> None:
    found = {
        (r.tenant_id, r.source_row): r.reason_code
        for r in rows(built, "quarantine")
        if r.entity == "labs"
    }
    assert found == {
        ("center_a", 3): "bad_date",  # N/A
        ("center_a", 5): "missing_value",
        ("center_b", 2): "bad_date",  # 31/02
        ("center_c", 3): "unmapped_code",
        ("center_c", 4): "unknown_unit",
        ("center_c", 5): "unknown_patient",
    }
    quarantined = next(r for r in rows(built, "quarantine") if r.reason_code == "unknown_unit")
    assert quarantined.detail == "furlongs"
    assert '"raw' not in quarantined.row_json and '"mrn":"730000063"' in quarantined.row_json


def test_exact_duplicates_are_removed_and_recorded(built: LocalRuntime) -> None:
    removed = rows(built, "lab_duplicates")
    assert [(r.tenant_id, r.bronze_source_row, r.duplicate_of_row) for r in removed] == [
        ("center_a", 4, 3)
    ]


def test_patients_normalize_sex_dates_and_death(built: LocalRuntime) -> None:
    by_mrn = {r.mrn: r for r in rows(built, "patients")}
    assert (by_mrn["A1"].sex, by_mrn["A1"].death_date) == ("female", date(2025, 3, 1))
    b = by_mrn["MRN-B-1"]
    assert (b.sex, b.gender_source_value, b.birth_date) == ("female", "F", date(1965, 6, 9))
    assert by_mrn["730000063"].death_date is None


def test_patients_keep_the_name_for_the_identity_table(built: LocalRuntime) -> None:
    names = {r.mrn: (r.given_name, r.family_name) for r in rows(built, "patients")}
    assert names == {
        "A1": ("Ysolde", "Quorrin"),
        "MRN-B-1": (None, "Brennick"),  # no given name; whitespace trimmed
        "730000063": (None, None),  # no name element at all
    }


def test_drugs_are_normalized_without_substring_matching(built: LocalRuntime) -> None:
    meds = {
        r.order_source_id: (r.drug_concept_id, r.mapping_method) for r in rows(built, "medications")
    }
    assert meds == {
        "RX-A-1": (950637, "rxnorm_code"),
        "RX-B-1": (950637, "brand"),  # Prograf -> Brand name of -> tacrolimus
        "RX-B-2": (950637, "reviewed_map"),  # tacro
        "RX-B-3": (19003999, "reviewed_map"),  # reviewed map beats the exact name match
        "RX-B-4": (950637, "name"),
        "RX-B-5": (0, "unmapped"),
        "RX-B-6": (950637, "synonym"),
        "RX-C-1": (19003999, "rxnorm_code"),
    }


def test_free_text_dosage_is_parsed(built: LocalRuntime) -> None:
    order = next(r for r in rows(built, "medications") if r.order_source_id == "RX-B-1")
    assert (order.dose_value, order.dose_unit, order.route, order.frequency) == (
        3.5,
        "mg",
        "oral",
        "bid",
    )
    structured = next(r for r in rows(built, "medications") if r.order_source_id == "RX-A-1")
    assert (structured.dose_value, structured.route) == (3.0, "oral")


def test_procedures_hold_transplants_and_biopsies(built: LocalRuntime) -> None:
    found = {
        r.procedure_source_id: (r.kind, r.organ, r.snomed_code, r.procedure_date)
        for r in rows(built, "procedures")
    }
    assert found == {
        "pa-tx": ("transplant", "kidney", "70536003", date(2024, 1, 5)),
        "pb-tx": ("transplant", "kidney", None, date(2023, 8, 28)),  # organ from text
        "pc-tx": ("transplant", "heart", "32413006", date(2022, 5, 1)),
        "E-A-2-biopsy": ("biopsy", "kidney", None, date(2024, 3, 1)),
    }


def test_conditions_keep_codes_text_and_onset(built: LocalRuntime) -> None:
    found = {
        r.condition_source_id: (r.snomed_code, r.onset_date) for r in rows(built, "conditions")
    }
    assert found == {
        "pa-c1": ("46177005", None),
        "pa-graft-loss": ("236583003", date(2025, 1, 1)),
        "pb-c1": (None, None),
    }


def test_notes_are_linked_to_patient_and_same_day_visit(built: LocalRuntime) -> None:
    found = {
        (r.tenant_id, r.note_source_id): (r.patient_source_id, r.note_type, r.encounter_source_id)
        for r in rows(built, "notes")
    }
    assert found == {
        ("center_a", "N-000001-001"): ("pa", "biopsy_result", "E-A-1"),
        ("center_b", "N-000001-001"): ("pb", "clinic", "E-B-1"),
        ("center_c", "N-000001-001"): ("pc", "clinic", None),  # no visit that day
    }
    reasons = {r.entity: r.reason_code for r in rows(built, "quarantine") if r.entity != "labs"}
    assert reasons == {"notes": "unknown_patient", "medications": "unknown_patient"}


def test_quarantine_rate_above_limit_fails_the_run(spark: SparkSession, tmp_path: Path) -> None:
    runtime = LocalRuntime(
        spark,
        str(write_raw(tmp_path / "raw")),
        str(write_vocab(tmp_path / "v")),
        str(tmp_path / "l"),
    )
    config = PipelineConfig(runtime.raw_root, runtime.vocab_root)  # default limit 5%
    run_bronze(runtime, config)
    with pytest.raises(ContractViolationError, match="quarantined"):
        run_silver(runtime, config)
    assert not runtime.exists("silver", "labs")  # nothing past the failure was written
