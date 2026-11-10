"""Gold OMOP tables built from hand-written raw rows (see rawfiles.py)."""

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from pyspark.sql import Row, SparkSession
from rawfiles import write_raw
from testvocab import write_vocab

from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.gold.conform import check_omop_contract, conform
from txlens_pipelines.omop_schema import OMOP_TABLES
from txlens_pipelines.pipeline import run
from txlens_pipelines.quality import ContractViolationError
from txlens_pipelines.runtime import LocalRuntime

pytestmark = pytest.mark.spark
LAYERS = ["bronze", "silver", "gold"]


@pytest.fixture(scope="module")
def built(spark: SparkSession, tmp_path_factory: pytest.TempPathFactory) -> LocalRuntime:
    root = tmp_path_factory.mktemp("gold")
    runtime = LocalRuntime(
        spark, str(write_raw(root / "raw")), str(write_vocab(root / "vocab")), str(root / "lake")
    )
    config = PipelineConfig(runtime.raw_root, runtime.vocab_root, max_quarantine_rate=1.0)
    run(runtime, config, LAYERS)
    return runtime


def gold(runtime: LocalRuntime, table: str) -> list[Row]:
    found: list[Row] = runtime.read("gold", table).collect()
    return found


def person_ids(runtime: LocalRuntime) -> dict[str, int]:
    return {r.person_source_value: r.person_id for r in gold(runtime, "person")}


def test_tables_have_exactly_the_omop_columns(built: LocalRuntime) -> None:
    for table, columns in OMOP_TABLES.items():
        stored = built.read("gold", table).columns
        assert stored == ["tenant_id", *[name for name, _, _ in columns]], table


def test_person_ids_are_in_tenant_ranges_with_gender_concepts(built: LocalRuntime) -> None:
    by_mrn = {r.person_source_value: r for r in gold(built, "person")}
    assert by_mrn["A1"].person_id == 100_000_001
    assert by_mrn["MRN-B-1"].person_id == 200_000_001
    assert by_mrn["A1"].gender_concept_id == 8532
    assert by_mrn["730000063"].gender_concept_id == 8507
    assert (by_mrn["MRN-B-1"].year_of_birth, by_mrn["MRN-B-1"].gender_source_value) == (1965, "F")


def test_measurements_carry_concepts_units_operators_and_source_values(
    built: LocalRuntime,
) -> None:
    rows = gold(built, "measurement")
    creatinine_b = next(r for r in rows if r.measurement_source_value == "Creatinine (enzymatic)")
    assert (creatinine_b.measurement_concept_id, creatinine_b.unit_concept_id) == (3016723, 8840)
    assert creatinine_b.value_as_number == pytest.approx(159 / 88.4)
    assert (creatinine_b.unit_source_value, creatinine_b.value_source_value) == ("µmol/L", "159")
    censored = next(r for r in rows if r.value_source_value == "<0.05")
    assert (censored.operator_concept_id, censored.unit_concept_id) == (4171756, 8554)
    dsa = next(r for r in rows if r.measurement_source_value == "DSAMFI")
    assert (dsa.measurement_concept_id, dsa.measurement_source_concept_id) == (0, 2_000_000_001)
    assert dsa.unit_concept_id == 0
    assert {r.measurement_type_concept_id for r in rows} == {32856}


def test_drugs_and_routes(built: LocalRuntime) -> None:
    rows = {r.drug_source_value: r for r in gold(built, "drug_exposure")}
    coded = rows["42316"]
    assert (coded.drug_concept_id, coded.route_concept_id, coded.route_source_value) == (
        950637,
        4132161,
        "oral",
    )
    assert coded.drug_exposure_end_date == date(2024, 2, 5)
    assert rows["Xyzumab"].drug_concept_id == 0
    assert rows["Xyzumab"].route_concept_id == 4171047  # "iv"
    assert rows["Prograf"].sig == "prograf 3.5mg oral bid"


def test_conditions_from_codes_only_and_dated(built: LocalRuntime) -> None:
    rows = {r.condition_source_value: r for r in gold(built, "condition_occurrence")}
    assert rows["46177005"].condition_concept_id == 193782
    assert rows["46177005"].condition_start_date == date(2024, 1, 5)  # dated at transplant
    assert rows["236583003"].condition_start_date == date(2025, 1, 1)  # graft failure onset
    assert rows["Hypertension"].condition_concept_id == 0  # uncoded: text kept, concept 0


def test_procedures_transplants_and_biopsies(built: LocalRuntime) -> None:
    rows = {(r.tenant_id, r.procedure_source_value): r for r in gold(built, "procedure_occurrence")}
    assert rows[("center_a", "70536003")].procedure_concept_id == 4322471
    uncoded = rows[("center_b", "Kidney transplant - living donor")]
    assert uncoded.procedure_concept_id == 4322471  # reviewed source_to_concept_map row
    biopsy = rows[("center_a", "biopsy of kidney")]
    assert biopsy.procedure_concept_id == 1244937
    assert biopsy.visit_occurrence_id is not None
    assert rows[("center_c", "32413006")].procedure_concept_id == 4137127


def test_death_notes_and_observation_periods(built: LocalRuntime) -> None:
    ids = person_ids(built)
    assert [(r.person_id, r.death_date) for r in gold(built, "death")] == [
        (ids["A1"], date(2025, 3, 1))
    ]
    notes = {r.person_id: r for r in gold(built, "note")}
    assert notes[ids["A1"]].note_class_concept_id == 3000735
    assert notes[ids["A1"]].visit_occurrence_id is not None
    assert notes[ids["730000063"]].visit_occurrence_id is None
    periods = {r.person_id: r for r in gold(built, "observation_period")}
    a = periods[ids["A1"]]
    assert (a.observation_period_start_date, a.observation_period_end_date) == (
        date(2024, 1, 5),
        date(2025, 3, 1),  # transplant .. death
    )


def test_lineage_links_omop_rows_to_bronze_rows(built: LocalRuntime) -> None:
    rows = gold(built, "lineage")
    measurement = next(r for r in gold(built, "measurement") if r.value_source_value == "<0.05")
    link = [r for r in rows if (r.omop_table, r.omop_id) == ("measurement", r.omop_id)
            and r.omop_id == measurement.measurement_id]  # fmt: skip
    assert [(r.bronze_source_file, r.bronze_source_row) for r in link] == [
        ("labs/center_c_labs.csv", 2)
    ]


def test_mapping_gaps_list_every_unmapped_source_value(built: LocalRuntime) -> None:
    gaps = {(r.omop_table, r.source_value, r.reason) for r in gold(built, "mapping_gaps")}
    assert gaps == {
        ("drug_exposure", "Xyzumab", "unmapped_drug"),
        ("condition_occurrence", "Hypertension", "unmapped"),
        ("measurement", "DSAMFI", "no_standard_concept"),
        ("measurement", "MFI", "no_standard_unit"),
        ("measurement", "XYZ / Mystery test", "quarantined_unmapped_code"),
        ("measurement", "furlongs", "quarantined_unknown_unit"),
    }


def test_rerun_is_idempotent(built: LocalRuntime) -> None:
    before = {t: sorted(map(tuple, gold(built, t))) for t in ("person", "measurement")}
    config = PipelineConfig(built.raw_root, built.vocab_root, max_quarantine_rate=1.0)
    run(built, config, LAYERS)
    after = {t: sorted(map(tuple, gold(built, t))) for t in ("person", "measurement")}
    assert after == before


def test_not_null_omop_column_breaks_the_contract(spark: SparkSession, tmp_path: Path) -> None:
    rows = spark.createDataFrame([("center_a", 1, None)], "tenant_id string, person_id int, x int")
    with pytest.raises(ContractViolationError, match="gender_concept_id"):
        check_omop_contract(conform(rows, "person"), "person")
