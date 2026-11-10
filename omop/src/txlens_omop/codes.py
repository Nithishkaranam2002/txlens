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


PLANNED_ICD10CM = {
    "T86.11": ("kidney transplant rejection", "Condition"),
    "T86.12": ("kidney transplant failure", "Condition"),
    "T86.21": ("heart transplant rejection", "Condition"),
    "N18.6": ("end stage renal disease", "Condition"),
    "E11.9": ("type 2 diabetes without complications", "Condition"),
    "I10": ("essential hypertension", "Condition"),
    "B25.9": ("cytomegaloviral disease", "Condition"),
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


def all_codes() -> list[CodeUse]:
    """Generator codes plus planned source codes."""
    return generator_codes() + planned_codes()


def uncoded_drugs() -> list[str]:
    """Drugs the generator writes without an RxNorm code (left for mapping or review)."""
    return sorted(set(DRUG_NAMES) - set(RXNORM))
