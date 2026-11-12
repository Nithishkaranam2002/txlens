"""Gold OMOP tables built from hand-written raw rows (see rawfiles.py)."""

from datetime import date
from pathlib import Path

import pytest

pytest.importorskip("pyspark", reason="PySpark runs in the Spark container or CI")

from pyspark.sql import Row, SparkSession

from txlens_pipelines.config import PipelineConfig
from txlens_pipelines.gold.conform import check_omop_contract, conform
from txlens_pipelines.gold.gaps import condition_gaps
from txlens_pipelines.omop_schema import OMOP_TABLES
from txlens_pipelines.pipeline import run
from txlens_pipelines.quality import ContractViolationError
from txlens_pipelines.review_gaps import REVIEW_GAP_COLUMNS
from txlens_pipelines.runtime import LocalRuntime

pytestmark = pytest.mark.spark
LAYERS = ["bronze", "silver", "gold"]


def gold(runtime: LocalRuntime, table: str) -> list[Row]:
    found: list[Row] = runtime.read("gold", table).collect()
    return found


def person_ids(runtime: LocalRuntime) -> dict[str, int]:
    """MRN -> person_id, through the identity table (OMOP itself holds no MRN)."""
    return {r.mrn: r.person_id for r in gold(runtime, "patient_identity")}


def test_tables_have_exactly_the_omop_columns(built: LocalRuntime) -> None:
    for table, columns in OMOP_TABLES.items():
        stored = built.read("gold", table).columns
        assert stored == ["tenant_id", *[name for name, _, _ in columns]], table


def test_person_ids_are_in_tenant_ranges_with_gender_concepts(built: LocalRuntime) -> None:
    ids = person_ids(built)
    by_mrn = {mrn: next(r for r in gold(built, "person") if r.person_id == i)
              for mrn, i in ids.items()}  # fmt: skip
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


def test_open_orders_run_to_the_extract_date_or_death(built: LocalRuntime) -> None:
    ids = person_ids(built)
    mmf = {(r.person_id, r.drug_exposure_start_date): r for r in gold(built, "drug_exposure")
           if r.drug_source_value == "68149"}  # fmt: skip
    alive = mmf[(ids["730000063"], date(2022, 5, 1))]
    assert (alive.drug_exposure_end_date, alive.verbatim_end_date) == (date(2025, 6, 30), None)
    died = mmf[(ids["A1"], date(2024, 3, 1))]  # died 2025-03-01, before the extract
    assert (died.drug_exposure_end_date, died.verbatim_end_date) == (date(2025, 3, 1), None)
    closed = next(r for r in gold(built, "drug_exposure") if r.drug_source_value == "42316")
    assert closed.verbatim_end_date == closed.drug_exposure_end_date == date(2024, 2, 5)
    sources = gold(built, "cdm_source")
    assert {r.source_release_date for r in sources} == {date(2025, 6, 30)}


def test_conditions_from_codes_and_text_and_dated(built: LocalRuntime) -> None:
    rows = {r.condition_source_value: r for r in gold(built, "condition_occurrence")}
    assert rows["46177005"].condition_concept_id == 193782
    assert rows["46177005"].condition_start_date == date(2024, 1, 5)  # dated at transplant
    assert rows["236583003"].condition_start_date == date(2025, 1, 1)  # graft failure onset
    assert rows["Hypertension"].condition_concept_id == 316866  # uncoded, mapped by synonym
    assert rows["Hypertensive crisis"].condition_concept_id == 0  # text kept, concept 0
    assert "History of prior kidney transplant" not in rows  # routed to observation


def test_observation_domain_conditions_go_to_observation(built: LocalRuntime) -> None:
    (row,) = gold(built, "observation")
    assert row.observation_source_value == "History of prior kidney transplant"
    assert (row.observation_concept_id, row.value_as_concept_id) == (1340204, 4322471)
    assert row.observation_source_concept_id == 1340204
    assert row.observation_date == date(2023, 8, 28)  # dated at transplant


def test_note_text_is_de_identified_with_a_replacement_list(built: LocalRuntime) -> None:
    from txlens_pipelines.deid_notes import Replacement, reidentify

    ids = {r.mrn: r.person_id for r in gold(built, "patient_identity")}
    note = next(r for r in gold(built, "note") if r.person_id == ids["A1"])
    for identifier in ("Quorrin", "Ysolde", "A1 ", "01/02/1970", "2024-02-01"):
        assert identifier not in note.note_text
    assert "MRN: [MRN]" in note.note_text
    spans = [r for r in gold(built, "note_phi_span") if r.note_id == note.note_id]
    assert {s.kind for s in spans} == {"name", "mrn", "date"}
    restored = reidentify(note.note_text, [
        Replacement(s.deid_start, s.deid_end, s.orig_start, s.orig_end, s.kind, s.original)
        for s in spans
    ])  # fmt: skip
    assert "Patient: Quorrin, Ysolde    MRN: A1    DOB: 01/02/1970" in restored


def test_gold_notes_need_the_deid_key(built: LocalRuntime) -> None:
    from txlens_pipelines.gold.notes import MissingDeidKeyError, deidentified_notes

    silver = built.read("silver", "notes")
    with pytest.raises(MissingDeidKeyError):
        deidentified_notes(silver, built.read("silver", "patients"), silver, "")


def test_procedures_transplants_and_biopsies(built: LocalRuntime) -> None:
    rows = {(r.tenant_id, r.procedure_source_value): r for r in gold(built, "procedure_occurrence")}
    # Donor type extension -> donor-specific concept; the code the center sent stays the source.
    assert rows[("center_a", "70536003")].procedure_concept_id == 4021107  # cadaveric
    uncoded = rows[("center_b", "Kidney transplant - living donor")]
    assert uncoded.procedure_concept_id == 4022805  # reviewed source_to_concept_map row
    biopsy = rows[("center_a", "biopsy of kidney")]
    assert biopsy.procedure_concept_id == 1244937
    assert biopsy.visit_occurrence_id is not None
    assert rows[("center_c", "32413006")].procedure_concept_id == 4137127


def test_person_holds_no_direct_identifiers(built: LocalRuntime) -> None:
    for row in gold(built, "person"):
        assert row.year_of_birth is not None
        assert (row.person_source_value, row.month_of_birth, row.day_of_birth) == (None, None, None)
        assert row.birth_datetime is None


def test_identity_table_holds_mrn_name_and_birth_date(built: LocalRuntime) -> None:
    rows = {r.mrn: r for r in gold(built, "patient_identity")}
    a = rows["A1"]
    assert (a.tenant_id, a.given_name, a.family_name, a.birth_date) == (
        "center_a", "Ysolde", "Quorrin", date(1970, 1, 2)
    )  # fmt: skip
    assert (rows["730000063"].given_name, rows["730000063"].family_name) == (None, None)
    assert sorted(r.person_id for r in rows.values()) == sorted(
        r.person_id for r in gold(built, "person")
    )


def test_identity_person_ids_match_the_id_map(built: LocalRuntime) -> None:
    id_map = {(r.tenant_id, r.natural_key): r.omop_id
              for r in gold(built, "id_map") if r.omop_table == "person"}  # fmt: skip
    for r in gold(built, "patient_identity"):
        assert id_map[(r.tenant_id, f"{r.tenant_id}|{r.mrn}")] == r.person_id


def test_death_notes_and_observation_periods(built: LocalRuntime) -> None:
    ids = person_ids(built)
    assert [(r.person_id, r.death_date) for r in gold(built, "death")] == [
        (ids["A1"], date(2025, 3, 1))
    ]
    notes = {r.person_id: r for r in gold(built, "note")}
    assert notes[ids["A1"]].note_class_concept_id == 3000735
    assert notes[ids["A1"]].note_date == date(2024, 2, 1)  # the column keeps the real date
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


def test_mapping_gaps_carry_the_condition_suggestion(built: LocalRuntime) -> None:
    (gap,) = [r for r in gold(built, "mapping_gaps") if r.source_value == "Hypertensive crisis"]
    assert (gap.suggested_concept_id, gap.review_reason) == (316866, "low_score")
    assert 0 < gap.suggestion_score < 0.9


def test_condition_gaps_for_the_review_queue(built: LocalRuntime) -> None:
    rows = condition_gaps(built.read("gold", "mapping_gaps")).collect()
    assert [(r.source_value, r.reason, r.suggested_concept_id, r.review_reason) for r in rows] == [
        ("Hypertensive crisis", "unmapped", 316866, "low_score")
    ]
    assert tuple(rows[0].asDict()) == REVIEW_GAP_COLUMNS


def test_mapping_gaps_list_every_unmapped_source_value(built: LocalRuntime) -> None:
    gaps = {(r.omop_table, r.source_value, r.reason) for r in gold(built, "mapping_gaps")}
    assert gaps == {
        ("drug_exposure", "Xyzumab", "unmapped_drug"),
        ("condition_occurrence", "Hypertensive crisis", "unmapped"),
        ("measurement", "DSAMFI", "no_standard_concept"),
        ("measurement", "MFI", "no_standard_unit"),
        ("measurement", "XYZ / Mystery test", "quarantined_unmapped_code"),
        ("measurement", "furlongs", "quarantined_unknown_unit"),
    }


@pytest.mark.slow  # the end-to-end test also checks re-runs
def test_rerun_is_idempotent(built: LocalRuntime) -> None:
    before = {t: sorted(map(tuple, gold(built, t))) for t in ("person", "measurement")}
    config = PipelineConfig(
        built.raw_root, built.vocab_root, max_quarantine_rate=1.0, deid_key="test-key"
    )
    run(built, config, LAYERS)
    after = {t: sorted(map(tuple, gold(built, t))) for t in ("person", "measurement")}
    assert after == before


def test_not_null_omop_column_breaks_the_contract(spark: SparkSession, tmp_path: Path) -> None:
    rows = spark.createDataFrame([("center_a", 1, None)], "tenant_id string, person_id int, x int")
    with pytest.raises(ContractViolationError, match="gender_concept_id"):
        check_omop_contract(conform(rows, "person"), "person")
