"""Evidence check: values must be quoted from lines that were sent, and readable from the quote."""

from datetime import date
from typing import Any

import pytest

from txlens_backend.documents import evidence, phi
from txlens_backend.documents.ocr import Line, OcrResult, PageSize, Word
from txlens_backend.documents.schema import Evidence, Finding

# Lines as Tesseract really read them from generated reports (look-alike characters included).
LINES = (
    "MRN: A1000123 DOB: 02/08/1964 Sex: M",
    "Procedure date: 12/01/2023 Reported: 12/02/2023",
    "Specimen adequacy: inadequale",
    "Banff lesion scores: i2 {2 v1 gO ptcd cil ctl cv1",
    "C4d (IHC, peritubular capillaries); C400",
    "- Acute T-cell mediated rejection, Banff grade liA.",
    "- Polyomavirus (BK) nephropathy, SV40 immunostain positive.",
    "ACR grade (ISHLT 2004): 2R",
    "pAMR grade (ISHLT 2013): pAMR 1 (I+)",
)


def make_ocr() -> OcrResult:
    lines = []
    for n, text in enumerate(LINES, start=1):
        words = tuple(
            Word(w, (10.0 * i, 20.0 * n, 10.0 * i + 8, 20.0 * n + 10), 0.5 + i / 100)
            for i, w in enumerate(text.split())
        )
        lines.append(Line(n, 1, words))
    return OcrResult("tesseract", (PageSize(612, 792),), tuple(lines))


OCR = make_ocr()
SENT = phi.minimize(OCR, phi.parse_header(OCR))


def check(field: str, value: Any, line: int, quote: str) -> evidence.VerifiedField:
    return evidence.verify(
        field, Finding(value=value, evidence=Evidence(line=line, quote=quote)), OCR, SENT
    )


@pytest.mark.parametrize(
    ("field", "value", "line", "quote"),
    [
        ("biopsy_date", date(2023, 12, 1), 2, "Procedure date: 12/01/2023"),
        ("adequacy", "inadequate", 3, "Specimen adequacy: inadequale"),
        ("banff_i", 2, 4, "i2"),
        ("banff_t", 2, 4, "{2"),  # "t" lost to OCR, the position is clear
        ("banff_g", 0, 4, "gO"),
        ("banff_ptc", 0, 4, "ptcd"),
        ("banff_ci", 1, 4, "cil"),
        ("banff_ct", 1, 4, "ctl"),
        ("banff_cv", 1, 4, "cv1"),
        ("c4d", 0, 5, "C400"),
        ("diagnosis", "TCMR", 6, "Acute T-cell mediated rejection"),
        ("tcmr_grade", "IIA", 6, "Banff grade liA."),
        ("additional_finding", "BK_nephropathy", 7, "Polyomavirus (BK) nephropathy"),
        ("acr_grade", "2R", 8, "ACR grade (ISHLT 2004): 2R"),
        ("pamr_grade", "pAMR 1 (I+)", 9, "pAMR 1 (I+)"),
    ],
)
def test_values_backed_by_ocr_text_are_kept(field: str, value: Any, line: int, quote: str) -> None:
    result = check(field, value, line, quote)
    assert result.problems == ()
    assert (result.value, result.line_no, result.page) == (value, line, 1)
    assert result.bbox is not None and result.source_text
    assert result.ocr_confidence is not None and 0.5 <= result.ocr_confidence <= 0.6


@pytest.mark.parametrize(
    ("field", "value", "line", "quote", "problem"),
    [
        ("banff_t", 3, 4, "{2", "quote does not state 3"),
        ("banff_ci", 1, 4, "ctl", "quote does not state 1"),  # ct is another label
        ("adequacy", "adequate", 3, "Specimen adequacy: inadequale", "does not state adequate"),
        ("tcmr_grade", "IB", 6, "Banff grade liA.", "does not state IB"),
        ("diagnosis", "ABMR", 6, "Acute T-cell mediated rejection", "does not state ABMR"),
        ("banff_i", 2, 4, "i2 t2 v0 in a sentence that is not there", "quote not found on line 4"),
        ("banff_i", 2, 99, "i2", "cited line 99 was not in the document"),
        ("biopsy_date", date(1964, 2, 8), 1, "DOB: 02/08/1964", "line 1 was not in the document"),
    ],
)
def test_unsupported_values_are_rejected(
    field: str, value: Any, line: int, quote: str, problem: str
) -> None:
    result = check(field, value, line, quote)
    assert result.value is None
    assert result.extracted_value == value
    assert len(result.problems) == 1 and problem in result.problems[0]


def test_a_value_without_evidence_is_rejected() -> None:
    result = evidence.verify("banff_i", Finding(value=2, evidence=None), OCR, SENT)
    assert (result.value, result.problems) == (None, ("value has no evidence",))


def test_not_reported_needs_no_evidence() -> None:
    result = evidence.verify("banff_cv", Finding(value=None, evidence=None), OCR, SENT)
    assert (result.value, result.problems) == (None, ())


def test_bbox_covers_only_the_quoted_words() -> None:
    result = check("banff_v", 1, 4, "v1")
    assert result.source_text == "v1"
    assert result.bbox == (50.0, 80.0, 58.0, 90.0)  # fifth word on line 4


@pytest.mark.parametrize(
    ("token", "label", "expected"),
    [
        ("ptc2", "ptc", 2),
        ("ptcd", "ptc", 0),
        ("cil", "ci", 1),
        ("{2", "t", 2),
        ("_ptc0,", "ptc", 0),
        ("ct1", "ci", None),
        ("x", "i", None),
    ],
)
def test_score_tokens(token: str, label: str, expected: int | None) -> None:
    assert evidence.score_in_token(token, label) == expected
