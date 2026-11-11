"""Turn a biopsy's final values into OMOP rows: diagnoses as conditions, scores as measurements.

Concepts are looked up by vocabulary code (the codes `make vocab-check` verifies), never by a
typed-in id. Standard SNOMED concepts go in `*_concept_id`. Findings without a standard concept
(borderline, every Banff score and grade) use TxLens local concepts, which OMOP rules keep out
of `*_concept_id`: it is 0 and the local id goes in `*_source_concept_id`. A code that is not
loaded, or an "other" finding, also gets 0, keeps its source value and sends the document to
review.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import Connection, text

from txlens_backend.documents.schema import SCORE_LABELS, Organ

LOCAL = "TxLens"
SNOMED = "SNOMED"
KIDNEY_TCMR = "236574008"  # acute rejection of renal transplant
TCMR_GRADES = {"IA": "236575009", "IB": "236575009", "IIA": "236576005", "IIB": "236576005",
               "III": "236577001"}  # fmt: skip
HEART_ACR = "432843002"  # acute rejection of cardiac transplant
ABMR = "1381981002"  # antibody-mediated rejection of transplant
FINDINGS = {"BK_nephropathy": "713886006", "ATN": "35455006", "CMV": "28944009"}
# Field -> (local concept code, measurement_source_value).
MEASUREMENTS = {
    field: (f"BANFF_{label.upper()}", f"Banff {label}") for field, label in SCORE_LABELS.items()
} | {
    "adequacy": ("BANFF_ADEQUACY", "Banff adequacy"),
    "acr_grade": ("ISHLT_ACR_GRADE", "ISHLT ACR grade"),
    "pamr_grade": ("ISHLT_PAMR_GRADE", "ISHLT pAMR grade"),
}
CdmTable = Literal["condition_occurrence", "measurement"]


@dataclass(frozen=True)
class CdmRow:
    """One OMOP row a document produces, tied to the field it came from."""

    field: str
    cdm_table: CdmTable
    concept_id: int
    source_concept_id: int
    source_value: str
    value_as_number: float | None = None
    value_source_value: str | None = None


class Concepts:
    """Concept ids by (vocabulary, code), looked up once each; 0 when not loaded."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.found: dict[tuple[str, str], int] = {}
        self.missing: list[str] = []

    def id(self, vocabulary: str, code: str) -> int:
        """The concept id, or 0 (and a recorded problem) if the vocabulary lacks it."""
        if (vocabulary, code) not in self.found:
            found = self.conn.execute(
                text(
                    "SELECT concept_id FROM vocab.concept "
                    "WHERE vocabulary_id = :v AND concept_code = :c AND invalid_reason IS NULL"
                ),
                {"v": vocabulary, "c": code},
            ).scalar_one_or_none()
            if found is None:
                self.missing.append(f"concept {vocabulary} {code} is not loaded")
            self.found[(vocabulary, code)] = int(found or 0)
        return self.found[(vocabulary, code)]


def condition(concepts: Concepts, field: str, code: str, source_value: str) -> CdmRow:
    """A condition with a standard SNOMED concept."""
    return CdmRow(field, "condition_occurrence", concepts.id(SNOMED, code), 0, source_value)


def borderline(concepts: Concepts) -> CdmRow:
    """Borderline rejection: no standard concept, so 0 plus our local source concept."""
    local = concepts.id(LOCAL, "BORDERLINE_REJECTION")
    return CdmRow("diagnosis", "condition_occurrence", 0, local, "borderline")


def diagnosis_rows(concepts: Concepts, organ: Organ, values: Mapping[str, Any]) -> list[CdmRow]:
    """Conditions for the rejection diagnosis (none for "no rejection")."""
    diagnosis = values.get("diagnosis")
    rows = []
    if diagnosis == "borderline":
        rows.append(borderline(concepts))
    if diagnosis in ("TCMR", "mixed"):
        if organ == "kidney":
            grade = values.get("tcmr_grade")
            code = TCMR_GRADES.get(grade, KIDNEY_TCMR) if grade else KIDNEY_TCMR
            label = f"TCMR {grade}" if grade else "TCMR"
            rows.append(condition(concepts, "diagnosis", code, label))
        else:
            rows.append(condition(concepts, "diagnosis", HEART_ACR, f"ACR {values['acr_grade']}"))
    if diagnosis in ("ABMR", "mixed"):
        rows.append(condition(concepts, "diagnosis", ABMR, "ABMR"))
    return rows


def finding_rows(concepts: Concepts, values: Mapping[str, Any]) -> tuple[list[CdmRow], list[str]]:
    """A condition for a non-rejection finding; "other" has no concept and needs review."""
    finding = values.get("additional_finding")
    if finding is None:
        return [], []
    if finding not in FINDINGS:
        row = CdmRow("additional_finding", "condition_occurrence", 0, 0, f"{finding} finding")
        return [row], [f"additional finding '{finding}' has no concept"]
    return [condition(concepts, "additional_finding", FINDINGS[finding], finding)], []


def number(field: str, value: Any) -> float | None:
    """The numeric value of a measurement: scores as is, ISHLT grades by their digit."""
    if isinstance(value, int):
        return float(value)
    if field == "acr_grade":
        return float(str(value)[0])
    if field == "pamr_grade":
        return float(str(value).split()[1])
    return None  # adequacy is categorical


def measurement_rows(concepts: Concepts, values: Mapping[str, Any]) -> list[CdmRow]:
    """One measurement per reported score or grade (local concepts)."""
    rows = []
    for field, (code, source_value) in MEASUREMENTS.items():
        value = values.get(field)
        if value is None:
            continue
        local = concepts.id(LOCAL, code)
        rows.append(
            CdmRow(field, "measurement", 0, local, source_value, number(field, value), str(value))
        )
    return rows


def cdm_rows(
    conn: Connection, organ: Organ, values: Mapping[str, Any]
) -> tuple[list[CdmRow], list[str]]:
    """All OMOP rows for a biopsy's final values, and any mapping problems."""
    concepts = Concepts(conn)
    findings, problems = finding_rows(concepts, values)
    rows = diagnosis_rows(concepts, organ, values) + findings + measurement_rows(concepts, values)
    return rows, problems + concepts.missing
