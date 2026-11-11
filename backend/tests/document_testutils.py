"""Helpers for document tests: render scanned-looking report PDFs with chosen lines."""

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import litellm
import pytest
from sqlalchemy import Connection, text

from txlens_backend.documents.ocr import Line as OcrLine
from txlens_backend.documents.ocr import OcrResult, PageSize, Word, to_json
from txlens_datagen.rand import rng_for
from txlens_datagen.writers.pathology_pdf import BODY, HEADING, TITLE, Line, draw_page, scan_effects

DPI = 120  # same as the generator's pathology PDFs
KIDNEY_LINES: list[Line] = [
    (TITLE, "TEST ONLY MEDICAL CENTER"),
    (HEADING, "SURGICAL PATHOLOGY REPORT"),
    (BODY, "Patient: TESTPERSON, ALEX"),
    (BODY, "MRN: T1234567    DOB: 03/14/1961    Sex: F"),
    (BODY, "Accession: S24-12345"),
    (BODY, "Procedure date: 05/06/2024    Reported: 05/08/2024"),
    (BODY, "Specimen: Kidney allograft, percutaneous needle core biopsy"),
    (HEADING, "MICROSCOPIC DESCRIPTION"),
    (BODY, "Glomeruli: 14 (1 globally sclerosed)"),
    (BODY, "Specimen adequacy: adequate"),
    (BODY, "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0"),
    (BODY, "C4d (IHC, peritubular capillaries): C4d0"),
    (HEADING, "DIAGNOSIS"),
    (BODY, "  - Acute T-cell mediated rejection, Banff grade IA."),
    (BODY, "Electronically signed: Testa Reviewer, MD"),
]


def write_report_pdf(path: Path, lines: list[Line] = KIDNEY_LINES, *, scanned: bool = True) -> Path:
    """A one-page, image-only report PDF like the generator's (optionally without scan noise)."""
    page = draw_page(lines, DPI)
    if scanned:
        page = scan_effects(page, rng_for(0, 99))
    page.save(path, "PDF", resolution=DPI)
    return path


def require_tesseract() -> None:
    """Skip without the tesseract binary, except in CI, where it must be installed."""
    if shutil.which("tesseract") is None:
        if os.environ.get("CI"):
            pytest.fail("tesseract is not installed (CI installs tesseract-ocr)")
        pytest.skip("tesseract is not installed (brew install tesseract)")


def finding(value: Any, line: int | None = None, quote: str | None = None) -> dict[str, Any]:
    """One field of a model answer (no evidence when line is None)."""
    evidence = None if line is None else {"line": line, "quote": quote or str(value)}
    return {"value": value, "evidence": evidence}


class FakeCompletion:
    """Stands in for litellm.completion: records each request, answers with JSON content."""

    def __init__(self, *answers: dict[str, Any], prompt_tokens: int = 1500) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []
        self.prompt_tokens = prompt_tokens

    def __call__(self, **kwargs: Any) -> litellm.ModelResponse:
        self.calls.append(kwargs)
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        return litellm.ModelResponse(
            model="gpt-6-luna",
            choices=[{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": json.dumps(answer)}}],
            usage={"prompt_tokens": self.prompt_tokens, "completion_tokens": 400,
                   "total_tokens": self.prompt_tokens + 400},
        )  # fmt: skip

    def schema_fields(self, call: int) -> list[str]:
        """Field names of the schema sent in a call, in order."""
        schema = self.calls[call]["response_format"]["json_schema"]["schema"]
        return list(schema["properties"])


# --- A kidney and a heart report as OCR lines, and the answers a model should give ---

MODEL = "openai/gpt-6-luna"
MODELS = {"a": MODEL, "b": MODEL}
# TEST ONLY concept ids for the standard codes the workflow uses.
SNOMED_IDS = {"1293269004": 900001, "387829002": 900002, "236575009": 900010,
              "236576005": 900011, "236577001": 900012, "236574008": 900013,
              "1381981002": 900014, "432843002": 900015, "713886006": 900016,
              "35455006": 900017, "28944009": 900018}  # fmt: skip
KIDNEY_OCR_LINES = (
    "TEST ONLY MEDICAL CENTER",
    "Patient: ELMDALE, BRANNOC",
    "MRN: A1000123 DOB: 02/08/1964 Sex: M",
    "Procedure date: 05/06/2024 Reported: 05/08/2024",
    "Specimen: Kidney allograft, percutaneous needle core biopsy",
    "Specimen adequacy: adequate",
    "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0",
    "C4d (IHC, peritubular capillaries): C4d0",
    "- Acute T-cell mediated rejection, Banff grade IA.",
    "Electronically signed: Testa Reviewer, MD",
)
KIDNEY_ANSWER = {
    "biopsy_date": finding("2024-05-06", 4, "05/06/2024"),
    "adequacy": finding("adequate", 6, "adequate"),
    "banff_i": finding(2, 7, "i2"),
    "banff_t": finding(2, 7, "t2"),
    "banff_v": finding(0, 7, "v0"),
    "banff_g": finding(0, 7, "g0"),
    "banff_ptc": finding(0, 7, "ptc0"),
    "banff_ci": finding(1, 7, "ci1"),
    "banff_ct": finding(1, 7, "ct1"),
    "banff_cv": finding(0, 7, "cv0"),
    "c4d": finding(0, 8, "C4d0"),
    "diagnosis": finding("TCMR", 9, "Acute T-cell mediated rejection"),
    "tcmr_grade": finding("IA", 9, "grade IA."),
    "additional_finding": finding(None),
}
HEART_OCR_LINES = (
    "Patient: TESTER, HEARTLY",
    "MRN: A1000456 DOB: 01/01/1950",
    "Procedure date: 07/01/2024 Reported: 07/02/2024",
    "Specimen: Endomyocardial biopsy, right ventricle",
    "ACR grade (ISHLT 2004): 2R",
    "pAMR grade (ISHLT 2013): pAMR 0",
)
HEART_ANSWER = {
    "biopsy_date": finding("2024-07-01", 3, "07/01/2024"),
    "acr_grade": finding("2R", 5, "2R"),
    "pamr_grade": finding("pAMR 0", 6, "pAMR 0"),
}


def ocr_of(lines: tuple[str, ...], confidence: float = 0.95) -> OcrResult:
    built = tuple(
        OcrLine(n, 1, tuple(Word(w, (10.0 * i, 20.0 * n, 10.0 * i + 8, 20.0 * n + 9), confidence)
                         for i, w in enumerate(t.split())))
        for n, t in enumerate(lines, start=1)
    )  # fmt: skip
    return OcrResult("tesseract", (PageSize(612, 792),), built)


@dataclass(frozen=True)
class SeededDocument:
    """A document put straight into a database for API tests."""

    document_id: str
    tenant_id: str
    person_id: int | None
    status: str


def report_lines_for(mrn: str, given: str, family: str, birth: date, day: date) -> tuple[str, ...]:
    """OCR lines of a kidney report for this patient; line 8 names the patient on purpose."""
    return (
        "TEST ONLY MEDICAL CENTER",
        f"Patient: {family.upper()}, {given.upper()}",
        f"MRN: {mrn} DOB: {birth:%m/%d/%Y} Sex: F",
        f"Procedure date: {day:%m/%d/%Y} Reported: {day:%m/%d/%Y}",
        "Specimen: Kidney allograft, percutaneous needle core biopsy",
        "- Acute T-cell mediated rejection, Banff grade IA.",
        "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0",
        f"Discussed with {given} {family} on {day:%m/%d/%Y}; ref {mrn}.",
    )


SEEDED_FIELDS = (  # field, value, line, source text
    ("biopsy_date", None, 4, None),
    ("diagnosis", "TCMR", 6, "Acute T-cell mediated rejection"),
    ("tcmr_grade", "IA", 6, "grade IA."),
    ("banff_t", "2", 7, "t2"),
    ("additional_finding", "other", 8, None),
)


def seed_document(
    conn: Connection,
    tenant_id: str,
    lines: tuple[str, ...],
    status: str,
    person_id: int | None = None,
    procedure_id: int | None = None,
    day: date | None = None,
) -> SeededDocument:
    """A kidney document with fields and evidence, as the workflow would leave it."""
    ocr = ocr_of(lines)
    document_id = str(uuid.uuid4())
    conn.execute(
        text(
            "INSERT INTO app.document (document_id, tenant_id, sha256, source_path, workflow_id, "
            "organ, status, person_id, procedure_occurrence_id, confidence, review_reasons, "
            "ocr, ocr_engine) VALUES (:d, :t, :h, 'pathology/x.pdf', :w, 'kidney', :s, :p, :r, "
            "0.9, CAST(:reasons AS jsonb), CAST(:o AS jsonb), 'tesseract')"
        ),
        {
            "d": document_id,
            "t": tenant_id,
            "h": uuid.uuid4().hex * 2,
            "w": f"wf-{document_id}",
            "s": status,
            "p": person_id,
            "r": procedure_id,
            "o": json.dumps(to_json(ocr)),
            "reasons": json.dumps([f"biopsy date is 9 days from the procedure on {day}"]),
        },
    )
    for field, value, line, source in SEEDED_FIELDS:
        value = (day.isoformat() if day else None) if field == "biopsy_date" else value
        source = source or (lines[line - 1] if line <= len(lines) else None)
        conn.execute(
            text(
                "INSERT INTO app.extracted_field (tenant_id, document_id, field, value, "
                "extracted_value, page, bbox, line_no, source_text, ocr_confidence, "
                "passes_agree, confidence, problems) VALUES (:t, :d, :f, :v, :v, 1, "
                "'[10, 20, 30, 40]', :l, :s, 0.9, true, 0.9, CAST(:p AS jsonb))"
            ),
            {
                "t": tenant_id,
                "d": document_id,
                "f": field,
                "v": value,
                "l": line,
                "s": source,
                "p": json.dumps([f"passes disagree (A: {value}, B: None)"]),
            },
        )
    return SeededDocument(document_id, tenant_id, person_id, status)
