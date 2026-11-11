"""Helpers for document tests: render scanned-looking report PDFs with chosen lines."""

import json
import os
import shutil
from pathlib import Path
from typing import Any

import litellm
import pytest

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
