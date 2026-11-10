from txlens_datagen.meds import RXNORM
from txlens_datagen.writers.fhir import SNOMED_CODES
from txlens_datagen.writers.lab_feeds import LOINC
from txlens_omop.codes import all_codes, generator_codes, planned_codes, uncoded_drugs


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
    assert len(all_codes()) == len(generator_codes()) + len(planned_codes())


def test_every_generated_drug_has_an_rxnorm_code() -> None:
    assert uncoded_drugs() == []
