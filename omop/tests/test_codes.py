from txlens_datagen.meds import RXNORM
from txlens_datagen.notes import DRUG_NAMES
from txlens_datagen.writers.fhir import SNOMED_CODES
from txlens_datagen.writers.lab_feeds import DIALECTS, LOINC
from txlens_omop.codes import (
    KNOWN_GAPS,
    all_codes,
    generator_codes,
    pipeline_codes,
    planned_codes,
    uncoded_drugs,
)
from txlens_omop.mappings import SOURCE_VOCABULARIES, normalize_source_code, read_mappings

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
        len(generator_codes()) + len(planned_codes()) + len(pipeline_codes())
    )


def test_new_step_1_4_codes_are_checked() -> None:
    codes = {(c.vocabulary_id, c.code): c.expected_domain for c in all_codes()}
    assert codes[("LOINC", "103142-6")] == "Measurement"  # dd-cfDNA
    assert codes[("SNOMED", "1293269004")] == "Procedure"  # kidney graft biopsy
    assert codes[("SNOMED", "387829002")] == "Procedure"  # endomyocardial biopsy
    assert codes[("SNOMED", "236583003")] == "Condition"  # failed renal transplant
    assert codes[("SNOMED", "233934000")] == "Condition"  # cardiac transplant failure
    assert "dsa_mfi" in KNOWN_GAPS


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
