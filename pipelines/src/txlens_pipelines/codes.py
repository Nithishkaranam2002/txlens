"""Standard codes and unit rules the pipeline applies.

Every code here is also listed in txlens_omop.codes, so `make vocab-check` validates it against
the loaded vocabulary (a test keeps the two lists in step). They are repeated here because the
Databricks job ships only this package.
"""

from dataclasses import dataclass

UMOL_PER_MG_DL = 88.4  # creatinine: 1 mg/dL = 88.4 umol/L


@dataclass(frozen=True)
class LabRule:
    """One lab: the concept it maps to, its standard unit, and accepted source units."""

    lab: str
    vocabulary_id: str
    concept_code: str
    standard_unit: str  # what value_as_number is expressed in
    ucum_code: str | None  # UCUM concept_code for unit_concept_id (None: no UCUM unit exists)
    factors: dict[str, float]  # normalized source unit -> multiply by this to get standard unit


LAB_RULES = (
    LabRule(
        "creatinine", "LOINC", "2160-0", "mg/dL", "mg/dL",
        {"mg/dl": 1.0, "umol/l": 1 / UMOL_PER_MG_DL},
    ),
    LabRule(
        "egfr", "LOINC", "98979-8", "mL/min/1.73m2", "mL/min/(173.10*-2.m2)",
        {"ml/min/1.73m2": 1.0},
    ),
    LabRule(
        "tacrolimus_trough", "LOINC", "77348-1", "ng/mL", "ng/mL",
        {"ng/ml": 1.0, "ug/l": 1.0},
    ),
    LabRule("dd_cfdna", "LOINC", "103142-6", "%", "%", {"%": 1.0, "fraction": 100.0}),
    LabRule("dsa_mfi", "TxLens", "DSA_MAX_MFI", "MFI", None, {"mfi": 1.0}),
)  # fmt: skip

# Graft biopsy procedure by transplanted organ.
BIOPSY_CODES = {"kidney": ("SNOMED", "1293269004"), "heart": ("SNOMED", "387829002")}
# Transplant by (organ, donor type), when a more specific standard concept exists. Both are
# descendants of "Transplant of kidney" (70536003), so organ queries still find them.
DONOR_TRANSPLANT_CODES = {
    ("kidney", "living"): ("SNOMED", "175901007"),  # Live donor renal transplant
    ("kidney", "deceased"): ("SNOMED", "175902000"),  # Cadaveric renal transplant
}
# Routes as written in orders (lower case) -> SNOMED route.
ROUTE_CODES = {"oral": ("SNOMED", "26643006"), "iv": ("SNOMED", "47625008")}
NOTE_CLASS = ("LOINC", "11506-3")  # progress note
NOTE_LANGUAGE = ("SNOMED", "297487008")  # English

# OMOP-generated concepts (Type Concept, Gender, Visit, Metadata, CDM vocabularies): their ids
# are fixed by OHDSI and are part of the vocabulary's support tables.
TYPE_EHR = 32817
TYPE_PRESCRIPTION = 32838
TYPE_LAB = 32856
TYPE_PROBLEM_LIST = 32840
TYPE_NOTE = 32831
GENDER = {"male": 8507, "female": 8532}
VISIT = {"AMB": 9202, "IMP": 9201}  # outpatient, inpatient
OPERATORS = {"<": 4171756, "<=": 4171754, ">": 4172704, ">=": 4171755}
ENCODING_UTF8 = 32678
CDM_V543 = 902983
