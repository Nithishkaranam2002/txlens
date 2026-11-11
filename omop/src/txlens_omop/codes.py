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
# Diagnoses the biopsy document extraction writes as conditions (Step 2.1). No SNOMED concept
# names T-cell mediated rejection as such; the graded "acute rejection of renal transplant"
# concepts carry it (Banff IA/IB = grade I, IIA/IIB = grade II, III = grade III).
EXTRACTION_CODES = {
    ("SNOMED", "236574008"): ("acute rejection of renal transplant (TCMR)", "Condition"),
    ("SNOMED", "236575009"): (
        "acute renal transplant rejection, grade I (Banff IA/IB)",
        "Condition",
    ),
    ("SNOMED", "236576005"): (
        "acute renal transplant rejection, grade II (Banff IIA/IIB)",
        "Condition",
    ),
    ("SNOMED", "236577001"): (
        "acute renal transplant rejection, grade III (Banff III)",
        "Condition",
    ),
    ("SNOMED", "1381981002"): ("antibody-mediated rejection of transplant", "Condition"),
    ("SNOMED", "432843002"): ("acute rejection of cardiac transplant (ISHLT 2R/3R)", "Condition"),
    ("SNOMED", "713886006"): ("BK virus nephropathy", "Condition"),
    ("SNOMED", "35455006"): (
        "acute tubular necrosis (reported as acute tubular injury)",
        "Condition",
    ),
    ("SNOMED", "28944009"): ("cytomegalovirus infection", "Condition"),
}
# Concepts that free-text condition names (FHIR text, clinic notes) map to by name or synonym
# (Step 2.2). The qualifier variants (type 1 diabetes, CKD stages, chronic rejection) are here
# so the matcher can tell them apart instead of falling back to the nearest name.
TEXT_CONDITION_CODES = {
    ("SNOMED", "236407003"): ("IgA nephropathy", "Condition"),
    ("SNOMED", "236403004"): ("focal segmental glomerulosclerosis", "Condition"),
    ("SNOMED", "473392002"): ("hypertensive nephrosclerosis", "Condition"),
    ("SNOMED", "127013003"): ("diabetic kidney disorder (diabetic nephropathy)", "Condition"),
    ("SNOMED", "68815009"): ("SLE glomerulonephritis (lupus nephritis)", "Condition"),
    ("SNOMED", "82525005"): ("multiple congenital kidney cysts (polycystic kidney disease)",
                             "Condition"),
    ("SNOMED", "399020009"): ("dilated cardiomyopathy", "Condition"),
    ("SNOMED", "233873004"): ("hypertrophic cardiomyopathy", "Condition"),
    ("SNOMED", "194849004"): ("ischemic myocardial dysfunction (ischemic cardiomyopathy)",
                              "Condition"),
    ("SNOMED", "161665007"): ("history of renal transplant (History of event + value)",
                              "Observation"),
    ("SNOMED", "46635009"): ("type 1 diabetes mellitus", "Condition"),
    ("SNOMED", "433144002"): ("chronic kidney disease stage 3", "Condition"),
    ("SNOMED", "431857002"): ("chronic kidney disease stage 4", "Condition"),
    ("SNOMED", "236578006"): ("chronic rejection of renal transplant", "Condition"),
    ("SNOMED", "426136000"): ("delayed renal graft function", "Condition"),
    ("SNOMED", "386661006"): ("fever", "Condition"),
    ("SNOMED", "267038008"): ("edema", "Condition"),
    ("SNOMED", "267036007"): ("dyspnea", "Condition"),
    ("SNOMED", "49650001"): ("dysuria", "Condition"),
    ("SNOMED", "80313002"): ("palpitations", "Condition"),
    ("SNOMED", "53741008"): ("coronary arteriosclerosis", "Condition"),
    ("SNOMED", "213148006"): ("transplanted organ rejection", "Condition"),
    ("SNOMED", "1085381000119108"): ("cytomegalovirus viremia", "Condition"),
}  # fmt: skip
# TxLens local concepts (LOCAL_CONCEPT.csv, by concept_code) the extraction writes. They are
# non-standard: OMOP rows get concept_id 0 and the local id as the source concept.
EXTRACTION_LOCAL_CODES = {
    "BORDERLINE_REJECTION": "Condition",
    "BANFF_I": "Measurement",
    "BANFF_T": "Measurement",
    "BANFF_V": "Measurement",
    "BANFF_G": "Measurement",
    "BANFF_PTC": "Measurement",
    "BANFF_CI": "Measurement",
    "BANFF_CT": "Measurement",
    "BANFF_CV": "Measurement",
    "BANFF_C4D": "Measurement",
    "BANFF_ADEQUACY": "Measurement",
    "ISHLT_ACR_GRADE": "Measurement",
    "ISHLT_PAMR_GRADE": "Measurement",
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


def extraction_codes() -> list[CodeUse]:
    """Standard condition codes written by the biopsy document extraction."""
    return [
        CodeUse(vocabulary, code, label, domain, "codes.py EXTRACTION_CODES")
        for (vocabulary, code), (label, domain) in EXTRACTION_CODES.items()
    ]


def text_condition_codes() -> list[CodeUse]:
    """Condition concepts that free text (FHIR text, notes) maps to by name or synonym."""
    return [
        CodeUse(vocabulary, code, label, domain, "codes.py TEXT_CONDITION_CODES")
        for (vocabulary, code), (label, domain) in TEXT_CONDITION_CODES.items()
    ]


def all_codes() -> list[CodeUse]:
    """Generator, planned source, pipeline, extraction and text-condition codes."""
    return (
        generator_codes()
        + planned_codes()
        + pipeline_codes()
        + extraction_codes()
        + text_condition_codes()
    )


def uncoded_drugs() -> list[str]:
    """Drugs the generator writes without an RxNorm code (left for mapping or review)."""
    return sorted(set(DRUG_NAMES) - set(RXNORM))
