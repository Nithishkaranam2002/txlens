"""Steward crops: refused for the identity header by page geometry, identifiers painted over."""

import io
from datetime import date
from pathlib import Path

from document_testutils import require_tesseract, write_report_pdf
from PIL import Image

from txlens_backend.documents.ocr import Line, Word, ocr_pdf
from txlens_backend.documents.phi import Identity, is_identifier_line
from txlens_backend.review.crop import HEADER_FRACTION, masked_words, padded, refusal, render
from txlens_backend.services.biopsies import redactor
from txlens_datagen.writers.pathology_pdf import BODY, HEADING, TITLE
from txlens_datagen.writers.pathology_pdf import Line as ReportLine

PAGE_HEIGHT = 792.0
BAND = PAGE_HEIGHT * HEADER_FRACTION
IDENTITY = Identity("T1234567", "Alex", "Testperson", date(1961, 3, 14))
# The generator's report layout (writers/pathology_pdf.report_lines), test values.
REPORT: list[ReportLine] = [
    (TITLE, "TEST ONLY MEDICAL CENTER"),
    (BODY, "Department of Pathology and Laboratory Medicine"),
    (HEADING, "SURGICAL PATHOLOGY REPORT"),
    (BODY, "Patient: TESTPERSON, ALEX"),
    (BODY, "MRN: T1234567    DOB: 03/14/1961    Sex: F"),
    (BODY, "Accession: S24-12345"),
    (BODY, "Procedure date: 05/06/2024    Reported: 05/08/2024"),
    (BODY, "Specimen: Kidney allograft, percutaneous needle core biopsy"),
    (BODY, "Clinical history: kidney transplant 01/02/2023;"),
    (BODY, "  biopsy indication: for-cause."),
    (HEADING, "MICROSCOPIC DESCRIPTION"),
    (BODY, "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0"),
    (HEADING, "DIAGNOSIS"),
    (BODY, "  - Acute T-cell mediated rejection, Banff grade IA."),
    (BODY, "Discussed with Alex Testperson on 05/07/2024."),
]


def line(text: str, top: float, line_no: int = 1) -> Line:
    words = tuple(Word(w, (60.0 + 40 * i, top, 95.0 + 40 * i, top + 9), 0.9) for i, w in
                  enumerate(text.split()))  # fmt: skip
    return Line(line_no, 1, words)


def test_a_header_line_ocr_could_not_read_is_still_refused_by_geometry() -> None:
    garbled = line("P@t: T E S T P E R S O N", top=110)  # the patient line, mangled by OCR
    assert not is_identifier_line(garbled.text)  # the label check alone would let it through
    clip = padded(garbled.bbox, 612, PAGE_HEIGHT)
    assert refusal(1, clip, PAGE_HEIGHT, garbled.text) == (
        "the line overlaps the report's identity header"
    )


def test_a_crop_reaching_into_the_header_band_by_its_padding_is_refused() -> None:
    just_below = line("Specimen adequacy: adequate", top=BAND + 1)
    clip = padded(just_below.bbox, 612, PAGE_HEIGHT)
    assert refusal(1, clip, PAGE_HEIGHT, just_below.text) is not None


def test_body_lines_are_allowed_but_identifier_lines_are_not() -> None:
    body = line("Banff lesion scores: i2 t2", top=400)
    signer = line("Electronically signed: Testa Reviewer, MD", top=600)
    assert refusal(1, padded(body.bbox, 612, PAGE_HEIGHT), PAGE_HEIGHT, body.text) is None
    assert refusal(1, padded(signer.bbox, 612, PAGE_HEIGHT), PAGE_HEIGHT, signer.text) == (
        "the line carries a patient identifier label"
    )
    assert refusal(2, (0, 10, 100, 20), PAGE_HEIGHT, body.text) is None  # header is on page 1


def test_names_dates_and_long_numbers_inside_the_crop_are_masked() -> None:
    discussed = line("Discussed with Alex Testperson on 05/07/2024 ref 1234567", top=500)
    clip = padded(discussed.bbox, 612, PAGE_HEIGHT)
    boxes = masked_words([discussed], clip, redactor(IDENTITY))
    masked = {w.text for w in discussed.words if w.bbox in boxes}
    assert masked == {"Alex", "Testperson", "05/07/2024", "1234567"}


def test_masked_boxes_are_black_in_the_image(tmp_path: Path) -> None:
    pdf = write_report_pdf(tmp_path / "r.pdf", scanned=False)
    clip = (40.0, 300.0, 400.0, 340.0)
    image = Image.open(io.BytesIO(render(pdf, 1, clip, [(100.0, 310.0, 150.0, 330.0)])))
    scale = image.width / (clip[2] - clip[0])
    centre = (round((125 - clip[0]) * scale), round((320 - clip[1]) * scale))
    assert image.getpixel(centre) == 0


def test_the_generator_layout_keeps_every_identity_line_inside_the_band(tmp_path: Path) -> None:
    require_tesseract()
    pdf = write_report_pdf(tmp_path / "r.pdf", REPORT, scanned=False)
    lines = ocr_pdf(pdf).lines
    height = PAGE_HEIGHT

    def bottom(word: str) -> float:
        return next(ln.bbox[3] for ln in lines if word in ln.text)

    def top(word: str) -> float:
        return next(ln.bbox[1] for ln in lines if word in ln.text)

    for identity_line in ("Patient", "MRN", "Accession", "Procedure"):
        assert bottom(identity_line) < height * HEADER_FRACTION, identity_line
    assert top("Banff lesion") > height * HEADER_FRACTION
    assert top("T-cell") > height * HEADER_FRACTION
