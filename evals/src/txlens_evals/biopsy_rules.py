"""A rules-only biopsy extractor: regular expressions over the OCR text, no LLM.

It is the baseline the LLM is compared with. It knows the report's labels ("Banff lesion
scores:", "ACR grade (ISHLT 2004):", "Procedure date:") and the diagnosis keywords, and it
tolerates the most common OCR look-alikes in scores (O for 0, l for 1). Being written for
one report template is the point: it shows how far exact rules get on text like this, and
real reports with other layouts would break it first.
"""

import re
from typing import Any

from txlens_backend.documents.ocr import OcrResult
from txlens_backend.documents.phi import parse_header
from txlens_backend.documents.schema import heart_diagnosis

DIGIT = {"o": "0", "d": "0", "l": "1", "i": "1", "|": "1", "!": "1", "z": "2"}
SCORE = re.compile(r"\b(ptc|ci|ct|cv|i|t|v|g)\s?([0-3OoDlI|!zZ])\b", re.IGNORECASE)
C4D = re.compile(r"C4d\s?([0-3OoDlI|!zZ])\b", re.IGNORECASE)
ADEQUACY = re.compile(r"adequacy\W+(adequate|marginal|inadequate)", re.IGNORECASE)
TCMR_GRADE = re.compile(r"grade\s+(IIA|IIB|III|IA|IB)\b")
ACR = re.compile(r"ACR grade.*?([0-3OoD])\s?R\b", re.IGNORECASE)
PAMR = re.compile(r"pAMR\s?([0-3OoD])(?:\s?\(([HI])\+\))?", re.IGNORECASE)
FINDINGS = (("polyomavirus", "BK_nephropathy"), ("acute tubular", "ATN"), ("cmv", "CMV"),
            ("cytomegalovirus", "CMV"))  # fmt: skip


def digit(value: str) -> int | None:
    """A score digit, OCR look-alikes (any case) read as digits; None if unreadable."""
    value = DIGIT.get(value.lower(), value)
    return int(value) if value.isdigit() else None


def section(lines: list[str], heading: str) -> list[str]:
    """The lines after a heading (to the next all-capitals heading)."""
    out: list[str] = []
    inside = False
    for line in lines:
        if heading in line.upper():
            inside = True
            continue
        if inside and line.strip().isupper() and len(line.strip()) > 8:
            break
        if inside:
            out.append(line)
    return out


def kidney_diagnosis(text: str) -> str | None:
    """The diagnosis from the DIAGNOSIS section's words."""
    low = text.lower()
    cellular = "t-cell mediated" in low and "borderline" not in low
    humoral = "antibody-mediated" in low
    if cellular and humoral:
        return "mixed"
    if cellular:
        return "TCMR"
    if humoral:
        return "ABMR"
    if "borderline" in low:
        return "borderline"
    if "no evidence of rejection" in low:
        return "no_rejection"
    return None


def kidney(lines: list[str]) -> dict[str, Any]:
    """Every kidney field the rules can find."""
    values: dict[str, Any] = {}
    for line in lines:
        if "lesion scores" in line.lower():
            for name, value in SCORE.findall(line.split(":", 1)[-1]):
                values.setdefault(f"banff_{name.lower()}", digit(value))
        if "ihc" in line.lower() and (c4d := C4D.findall(line)):
            values["c4d"] = digit(c4d[-1])
        if found := ADEQUACY.search(line):
            values["adequacy"] = found.group(1).lower()
    diagnosis_lines = section(lines, "DIAGNOSIS")
    text = " ".join(diagnosis_lines)
    values["diagnosis"] = kidney_diagnosis(text)
    grade = TCMR_GRADE.search(text)
    cellular = values["diagnosis"] in ("TCMR", "mixed")
    values["tcmr_grade"] = grade.group(1) if grade and cellular else None
    finding = next((v for word, v in FINDINGS for line in diagnosis_lines
                    if word in line.lower()), None)  # fmt: skip
    values["additional_finding"] = finding
    return values


def heart(lines: list[str]) -> dict[str, Any]:
    """ISHLT grades and the diagnosis they imply."""
    acr = pamr = None
    for line in lines:
        if acr is None and (found := ACR.search(line)):
            acr = f"{digit(found.group(1))}R" if digit(found.group(1)) is not None else None
        if pamr is None and (found := PAMR.search(line)):
            grade = digit(found.group(1))
            suffix = f" ({found.group(2).upper()}+)" if found.group(2) else ""
            pamr = f"pAMR {grade}{suffix}" if grade is not None else None
    diagnosis = heart_diagnosis(acr, pamr) if acr and pamr else None  # type: ignore[arg-type]
    return {"acr_grade": acr, "pamr_grade": pamr, "diagnosis": diagnosis}


def extract(ocr: OcrResult, organ: str) -> dict[str, Any]:
    """Field -> value, read by rules alone (None when not found)."""
    lines = [line.text for line in ocr.lines]
    values = heart(lines) if organ == "heart" else kidney(lines)
    values["biopsy_date"] = parse_header(ocr).procedure_date
    return values
