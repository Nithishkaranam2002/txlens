"""Every vocabulary code TxLens relies on, with the OMOP domain we expect for it.

Generator codes are read straight from the data generator's own tables, so this list can never
drift from the data. Planned ICD-10-CM source codes are what transplant centers send for our
conditions; the pipeline maps them to SNOMED through "Maps to".
"""

from dataclasses import dataclass

from txlens_datagen.meds import RXNORM
from txlens_datagen.notes import DRUG_NAMES
from txlens_datagen.writers.fhir import SNOMED_CODES
from txlens_datagen.writers.lab_feeds import LOINC


@dataclass(frozen=True)
class CodeUse:
    """One code we use, where it comes from, and the domain it should have."""

    vocabulary_id: str
    code: str
    label: str
    expected_domain: str
    used_in: str
    # (vocabulary, code) this code's standard concept must descend from, when queries rely on it.
    descends_from: tuple[str, str] | None = None


PLANNED_ICD10CM = {
    "T86.11": ("kidney transplant rejection", "Condition"),
    "T86.12": ("kidney transplant failure", "Condition"),
    "T86.21": ("heart transplant rejection", "Condition"),
    "N18.6": ("end stage renal disease", "Condition"),
    "E11.9": ("type 2 diabetes without complications", "Condition"),
    "I10": ("essential hypertension", "Condition"),
    "B25.9": ("cytomegaloviral disease", "Condition"),
}


# Codes the pipeline writes itself (not present in the raw feeds).
PIPELINE_CODES = {
    ("LOINC", "103142-6"): ("dd-cfDNA donor fraction", "Measurement"),
    ("SNOMED", "1293269004"): ("kidney graft biopsy", "Procedure"),
    ("SNOMED", "387829002"): ("heart graft (endomyocardial) biopsy", "Procedure"),
    ("SNOMED", "175901007"): ("live donor kidney transplant", "Procedure"),
    ("SNOMED", "175902000"): ("deceased (cadaveric) donor kidney transplant", "Procedure"),
    ("UCUM", "mg/dL"): ("creatinine unit", "Unit"),
    ("UCUM", "mL/min/(173.10*-2.m2)"): ("eGFR unit (mL/min/1.73 m2)", "Unit"),
    ("UCUM", "ng/mL"): ("tacrolimus trough unit", "Unit"),
    ("UCUM", "%"): ("dd-cfDNA unit", "Unit"),
    ("SNOMED", "26643006"): ("oral route", "Route"),
    ("SNOMED", "47625008"): ("intravenous route", "Route"),
    ("LOINC", "11506-3"): ("progress note (note class)", "Note"),
    ("SNOMED", "297487008"): ("English language (note language)", "Language"),
}
# Codes that queries find through an ancestor (e.g. "any kidney transplant" must also match
# the donor-specific ones), so the hierarchy is checked too.
ANCESTORS = {
    ("SNOMED", "175901007"): ("SNOMED", "70536003"),
    ("SNOMED", "175902000"): ("SNOMED", "70536003"),
}
# Facts with no standard concept: reviewed, mapped to 0 in OMOP and listed in the gaps report.
KNOWN_GAPS = {
    "dsa_mfi": "No standard LOINC holds a numeric donor-specific antibody MFI (LOINC has only "
    "[Identifier] and [Presence] DSA codes). Mapped to TxLens local concept 2000000001; "
    "measurement_concept_id = 0.",
    "MFI": "Mean fluorescence intensity is not a UCUM unit; unit_concept_id = 0.",
}


def generator_codes() -> list[CodeUse]:
    """Codes written by data_generator (LOINC labs, RxNorm drugs, SNOMED conditions/procedures)."""
    codes = [
        CodeUse("LOINC", code, lab, "Measurement", "writers/lab_feeds.py LOINC")
        for lab, code in LOINC.items()
    ]
    codes += [
        CodeUse("RxNorm", code, drug, "Drug", "meds.py RXNORM") for drug, code in RXNORM.items()
    ]
    for concept, (code, display) in SNOMED_CODES.items():
        domain = "Procedure" if concept.endswith("_transplant") else "Condition"
        codes.append(CodeUse("SNOMED", code, display, domain, "writers/fhir.py SNOMED_CODES"))
    return codes


def planned_codes() -> list[CodeUse]:
    """ICD-10-CM source codes the pipeline is expected to receive and map."""
    return [
        CodeUse("ICD10CM", code, label, domain, "codes.py PLANNED_ICD10CM")
        for code, (label, domain) in PLANNED_ICD10CM.items()
    ]


def pipeline_codes() -> list[CodeUse]:
    """Standard codes the pipeline writes (biopsies, dd-cfDNA, units)."""
    return [
        CodeUse(
            vocabulary,
            code,
            label,
            domain,
            "codes.py PIPELINE_CODES",
            ANCESTORS.get((vocabulary, code)),
        )
        for (vocabulary, code), (label, domain) in PIPELINE_CODES.items()
    ]


def all_codes() -> list[CodeUse]:
    """Generator codes, planned source codes and pipeline codes."""
    return generator_codes() + planned_codes() + pipeline_codes()


def uncoded_drugs() -> list[str]:
    """Drugs the generator writes without an RxNorm code (left for mapping or review)."""
    return sorted(set(DRUG_NAMES) - set(RXNORM))
