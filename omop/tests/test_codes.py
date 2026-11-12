import csv

from txlens_datagen.meds import RXNORM
from txlens_datagen.notes import DRUG_NAMES
from txlens_datagen.writers.fhir import SNOMED_CODES
from txlens_datagen.writers.lab_feeds import DIALECTS, LOINC
from txlens_omop.check import MappingCheck, check_value_pairs
from txlens_omop.codes import (
    EXTRACTION_LOCAL_CODES,
    KNOWN_GAPS,
    all_codes,
    extraction_codes,
    generator_codes,
    pipeline_codes,
    planned_codes,
    survival_codes,
    text_condition_codes,
    uncoded_drugs,
)
from txlens_omop.mappings import (
    CURATED_PENDING_REVIEW,
    LOCAL_CONCEPT_FILE,
    LOCAL_CONCEPT_MIN,
    LOCAL_VOCABULARY,
    PENDING_REVIEW_LABEL,
    SOURCE_VOCABULARIES,
    MappingRow,
    mappings_dir,
    normalize_source_code,
    read_mappings,
)
from txlens_omop.vocab import Concept

BRANDS = {"Prograf", "CellCept", "Solu-Medrol", "Thymoglobulin", "Valcyte"}


def test_inventory_tracks_every_generator_code() -> None:
    codes = {(c.vocabulary_id, c.code) for c in generator_codes()}
    expected = {("LOINC", c) for c in LOINC.values()} | {("RxNorm", c) for c in RXNORM.values()}
    expected |= {("SNOMED", code) for code, _ in SNOMED_CODES.values()}
    assert codes == expected


def test_domains_follow_vocabulary() -> None:
    for code in generator_codes():
        if code.vocabulary_id == "LOINC":
            assert code.expected_domain == "Measurement"
        if code.vocabulary_id == "RxNorm":
            assert code.expected_domain == "Drug"
    snomed = {c.label: c.expected_domain for c in generator_codes() if c.vocabulary_id == "SNOMED"}
    assert snomed["Transplantation of kidney"] == "Procedure"


def test_planned_icd10cm_codes_are_included() -> None:
    assert {c.vocabulary_id for c in planned_codes()} == {"ICD10CM"}
    assert len(all_codes()) == (
        len(generator_codes())
        + len(planned_codes())
        + len(pipeline_codes())
        + len(extraction_codes())
        + len(text_condition_codes())
        + len(survival_codes())
    )


def test_survival_codes_route_delayed_graft_function_through_history_of_event() -> None:
    codes = {(c.vocabulary_id, c.code): c.expected_domain for c in survival_codes()}
    assert codes[("SNOMED", "426136000")] == "Condition"
    assert codes[("OMOP Extension", "OMOP5165859")] == "Observation"


def test_text_condition_codes_include_the_qualifier_variants() -> None:
    codes = {c.code: c.expected_domain for c in text_condition_codes()}
    for code in ("46635009", "433144002", "431857002", "236578006"):
        assert codes[code] == "Condition"
    assert codes["161665007"] == "Observation"  # history of renal transplant


def test_new_step_1_4_codes_are_checked() -> None:
    codes = {(c.vocabulary_id, c.code): c.expected_domain for c in all_codes()}
    assert codes[("LOINC", "103142-6")] == "Measurement"  # dd-cfDNA
    assert codes[("SNOMED", "1293269004")] == "Procedure"  # kidney graft biopsy
    assert codes[("SNOMED", "387829002")] == "Procedure"  # endomyocardial biopsy
    assert codes[("SNOMED", "236583003")] == "Condition"  # failed renal transplant
    assert codes[("SNOMED", "233934000")] == "Condition"  # cardiac transplant failure
    assert "dsa_mfi" in KNOWN_GAPS


def test_donor_transplant_codes_are_checked_under_kidney_transplant() -> None:
    codes = {(c.vocabulary_id, c.code): c for c in all_codes()}
    for code in ("175901007", "175902000"):
        use = codes[("SNOMED", code)]
        assert (use.expected_domain, use.descends_from) == ("Procedure", ("SNOMED", "70536003"))


def test_every_lab_name_variant_and_local_code_is_mapped() -> None:
    mapped = {(m.source_vocabulary_id, m.source_code) for m in read_mappings()}
    for dialect in DIALECTS.values():
        for names in dialect.test_names.values():
            for name in names:
                assert ("TXLENS_LAB_NAME", normalize_source_code(name)) in mapped, name
    for code in ("cre", "egfr", "tact", "ddcf", "dsamfi"):
        assert ("TXLENS_LAB_CODE", code) in mapped


def test_each_lab_name_maps_to_one_target() -> None:
    targets: dict[tuple[str, str], set[int]] = {}
    for m in read_mappings():
        targets.setdefault((m.source_vocabulary_id, m.source_code), set()).add(m.target_concept_id)
    assert all(len(t) == 1 for t in targets.values())


def test_every_drug_surface_name_is_a_name_brand_or_reviewed_abbreviation() -> None:
    abbreviations = {
        m.source_code for m in read_mappings() if m.source_vocabulary_id == "TXLENS_DRUG"
    }
    for drug, names in DRUG_NAMES.items():
        for name in names:
            known = name == drug or name in BRANDS
            assert known or normalize_source_code(name) in abbreviations, name


def test_mapping_rows_use_known_source_vocabularies_and_normalized_codes() -> None:
    for m in read_mappings():
        assert m.source_vocabulary_id in SOURCE_VOCABULARIES
        assert m.source_code == normalize_source_code(m.source_code)


def test_every_generated_drug_has_an_rxnorm_code() -> None:
    assert uncoded_drugs() == []


def test_every_uncoded_transplant_text_is_mapped() -> None:
    rows = read_mappings()
    mapped = {m.source_code for m in rows if m.source_vocabulary_id == "TXLENS_PROCEDURE"}
    for organ, donors in (("kidney", ("living", "deceased")), ("heart", ("deceased",))):
        for donor in donors:
            assert f"{organ} transplant - {donor} donor" in mapped


def test_biopsy_diagnosis_codes_are_checked_as_conditions() -> None:
    codes = {(c.vocabulary_id, c.code): c.expected_domain for c in all_codes()}
    for code in ("236574008", "1381981002", "432843002", "713886006", "35455006"):
        assert codes[("SNOMED", code)] == "Condition"


def test_every_extraction_local_code_is_a_non_standard_local_concept() -> None:
    with (mappings_dir() / LOCAL_CONCEPT_FILE).open(encoding="utf-8", newline="") as handle:
        rows = {r["concept_code"]: r for r in csv.DictReader(handle, delimiter="\t")}
    ids = [int(r["concept_id"]) for r in rows.values()]
    assert len(ids) == len(set(ids))
    for code, domain in EXTRACTION_LOCAL_CODES.items():
        row = rows[code]
        assert int(row["concept_id"]) >= LOCAL_CONCEPT_MIN
        assert (row["vocabulary_id"], row["domain_id"]) == (LOCAL_VOCABULARY, domain)
        assert row["standard_concept"] == ""


def test_curated_rows_are_labelled_pending_or_clinician_reviewed() -> None:
    curated = [m for m in read_mappings() if m.source_vocabulary_id in CURATED_PENDING_REVIEW]
    assert curated and all(
        PENDING_REVIEW_LABEL in m.source_code_description
        or "[clinician-reviewed " in m.source_code_description
        for m in curated
    )


def test_every_history_value_row_has_its_observation_row() -> None:
    rows = read_mappings()
    observations = {m.source_code for m in rows if m.source_vocabulary_id == "TXLENS_CONDITION"}
    values = {m.source_code for m in rows if m.source_vocabulary_id == "TXLENS_COND_VALUE"}
    assert values == {"history of prior kidney transplant", "history of prior heart transplant"}
    assert values <= observations


def concept(concept_id: int, domain: str) -> Concept:
    return Concept(concept_id, "test", domain, "SNOMED", "x", "S", str(concept_id), None)


def check(code: str, vocabulary: str, domain: str) -> MappingCheck:
    return MappingCheck(MappingRow(code, vocabulary, "test", 1, "SNOMED"), concept(1, domain), ())


def test_value_pair_check_needs_an_observation_with_the_same_text() -> None:
    checks = [
        check("h/o kidney tx", "TXLENS_CONDITION", "Observation"),
        check("h/o kidney tx", "TXLENS_COND_VALUE", "Procedure"),
        check("h/o heart tx", "TXLENS_CONDITION", "Condition"),
        check("h/o heart tx", "TXLENS_COND_VALUE", "Procedure"),
        check("h/o lung tx", "TXLENS_COND_VALUE", "Procedure"),
    ]
    assert check_value_pairs(checks) == [
        "'h/o heart tx': value without an observation (its TXLENS_CONDITION target is Condition)",
        "'h/o lung tx': value without an observation (no TXLENS_CONDITION row)",
    ]
