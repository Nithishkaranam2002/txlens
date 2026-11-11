"""PHI minimization: identifier lines dropped, dates and ids masked, header parsed locally."""

from datetime import date

import pytest

from txlens_backend.documents import phi
from txlens_backend.documents.ocr import Line, OcrResult, PageSize, Word
from txlens_datagen.config import GeneratorConfig
from txlens_datagen.rand import rng_for
from txlens_datagen.record import build_record
from txlens_datagen.writers.pathology_pdf import report_lines

IDENTITY = phi.Identity("A1000123", "Brannoc", "Elmdale", date(1964, 2, 8))
IDENTIFIER_PREFIXES = ("Patient:", "MRN:", "Accession:", "Electronically signed:")
QUOTE = "\N{LEFT SINGLE QUOTATION MARK}"  # OCR often reads speckles as a curly quote


def ocr_of(*texts: str) -> OcrResult:
    lines = tuple(
        Line(n, 1, tuple(Word(w, (0, n, 1, n + 1), 0.9) for w in text.split()))
        for n, text in enumerate(texts, start=1)
    )
    return OcrResult("tesseract", (PageSize(612, 792),), lines)


@pytest.mark.parametrize(
    "text",
    [
        "Patient: ELMDALE, BRANNOC",
        "Patient ELMDALE, BRANNOC",  # colon lost
        QUOTE + "MRN:A1000005 DOB:08/15/1983 Sex M",  # label glued
        "_MRN: 730000119 QOB: 02/03/1954 | SexiM",
        "Accession: $23-48101",
        QUOTE + "cna signed: Quilla Fenthorpe, MD",  # "Electronically" unreadable
        "Address: 12 Mill Lane",
    ],
)
def test_identifier_lines_are_recognized_even_when_damaged(text: str) -> None:
    assert phi.is_identifier_line(text)


def test_no_clinical_line_of_any_generated_report_is_dropped() -> None:
    config = GeneratorConfig()
    checked = 0
    for index in range(150):
        record = build_record(index, config, with_notes=False)
        for biopsy in record.biopsies:
            for _, text in report_lines(biopsy, record.patient, "Test Center", rng_for(0)):
                expected = text.startswith(IDENTIFIER_PREFIXES)
                assert phi.is_identifier_line(text) == expected, text
                checked += 1
    assert checked > 1000


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Procedure date: 09/24/2025 Reported: 09/25/2025", date(2025, 9, 24)),
        ("_ Procedure date: 02/16/2025 Reported: 02/18/2025", date(2025, 2, 16)),
        ("Procedure date: 03/1 5/2024 Reported: 03/1 8/2024", date(2024, 3, 15)),
        ("Procedure dete 12'01/2023 Reported. 12/02/2023", date(2023, 12, 1)),
        ("Procedure date: 13/45/2024", None),  # not a real date
    ],
)
def test_procedure_date_is_read_from_the_header(text: str, expected: date | None) -> None:
    header = phi.parse_header(ocr_of("SURGICAL PATHOLOGY REPORT", text))
    assert header.procedure_line == 2
    assert header.procedure_date == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MRN: MRN-B-000013 DOB: 02/08/1964 Sex: M", "MRN-B-000013"),
        (QUOTE + "MRN:A1000005 DOB:08/15/1983 Sex M", "A1000005"),
        ("_MRN: 730000119 QOB: 02/03/1954", "730000119"),
        ("MRN: unreadable", None),
    ],
)
def test_mrn_is_read_from_the_header(text: str, expected: str | None) -> None:
    assert phi.parse_header(ocr_of(text)).mrn == expected


def test_minimized_text_keeps_findings_and_the_procedure_date_only() -> None:
    ocr = ocr_of(
        "Patient: ELMDALE, BRANNOC",
        "MRN: A1000123 DOB: 02/08/1964 Sex: M",
        "Accession: S23-48101",
        "Procedure date: 12/01/2023 Reported: 12/02/2023",
        "Clinical history: kidney transplant 07/21/2023;",
        "Banff lesion scores: i2 t2 v1 g0 ptc0 ci1 ct1 cv1",
        "Seen by Elmdale's team, ref A1000123",  # identifiers outside the header
        "Electronically signed: Perran Pellby, MD",
    )
    minimized = phi.minimize(ocr, phi.parse_header(ocr), IDENTITY)
    assert minimized.removed == (1, 2, 3, 8)
    assert dict(minimized.lines) == {
        4: "Procedure date: 12/01/2023 Reported: [DATE]",
        5: "Clinical history: kidney transplant [DATE];",
        6: "Banff lesion scores: i2 t2 v1 g0 ptc0 ci1 ct1 cv1",
        7: "Seen by [NAME] team, ref [ID]",
    }
    assert minimized.text_of(6) == "Banff lesion scores: i2 t2 v1 g0 ptc0 ci1 ct1 cv1"
    assert minimized.text_of(1) is None


def test_identity_scrub_catches_ocr_misspellings() -> None:
    assert phi.scrub_identity("ELMDAIE BRANNOC A1O00123", IDENTITY) == "[NAME] [NAME] [ID]"
    assert phi.scrub_identity("Banff grade IA", IDENTITY) == "Banff grade IA"


def test_long_numbers_are_masked_even_without_a_label() -> None:
    ocr = ocr_of("Procedure date: 12/01/2023", "ref 730000119 and S23-48101")
    assert phi.minimize(ocr, phi.parse_header(ocr)).text_of(2) == "ref [ID] and [ID]"
