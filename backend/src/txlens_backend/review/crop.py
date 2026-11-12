"""Cropped, redacted line images of a pathology report, for data stewards.

A data steward checks an extraction against the scan without seeing who the patient is. They
get the image of one evidence line, never a page, and only when all of these hold:
1. Geometry: the padded crop lies wholly below the report's identity header. The header band
   (center, patient name, MRN, birth date, accession, procedure date) is the top
   HEADER_FRACTION of the first page, from the report layout. This does not depend on OCR, so
   an identifier OCR failed to read is still never shown.
2. Text: the line is not an identifier line by its label (signer, address, phone ...).
3. Inside the crop, every word that redaction would change (a date, a long number, the
   patient's name or MRN, even misspelled by OCR) is painted over in black.
"""

import io
import re
from collections.abc import Callable, Iterable
from pathlib import Path

import pymupdf
from PIL import Image, ImageDraw
from sqlalchemy import Connection

from txlens_backend.documents import store
from txlens_backend.documents.ocr import BBox, Line, OcrResult
from txlens_backend.documents.phi import is_identifier_line

# Report layout (8.5 x 11 in page, 0.75 in margin): the identity lines end about 2.35 in
# from the top, so the band is the top 2.4 in of page 1.
HEADER_FRACTION = 2.4 / 11.0
PAD = 4.0  # points around the line
CROP_DPI = 150
DATE_LIKE = re.compile(r"\d+\s*[/.'\-]\s*\d+")
DIGITS = re.compile(r"\d{5,}")


class CropRefusedError(LookupError):
    """This line may not be shown as an image to a de-identified role."""


def padded(box: BBox, width: float, height: float) -> BBox:
    """The box grown by PAD on every side, kept on the page."""
    x0, y0, x1, y1 = box
    return (max(0.0, x0 - PAD), max(0.0, y0 - PAD), min(width, x1 + PAD), min(height, y1 + PAD))


def refusal(page: int, clip: BBox, page_height: float, line_text: str) -> str | None:
    """Why this crop may not be shown, or None if it may."""
    if page == 1 and clip[1] < page_height * HEADER_FRACTION:
        return "the line overlaps the report's identity header"
    if is_identifier_line(line_text):
        return "the line carries a patient identifier label"
    return None


def masked_words(lines: Iterable[Line], clip: BBox, redact: Callable[[str], str]) -> list[BBox]:
    """Boxes of words inside the crop that redaction would change, or that look like a date
    or an identifier number."""
    boxes = []
    for line in lines:
        for word in line.words:
            x0, y0, x1, y1 = word.bbox
            if x1 < clip[0] or x0 > clip[2] or y1 < clip[1] or y0 > clip[3]:
                continue
            if (redact(word.text) != word.text or DATE_LIKE.search(word.text)
                    or DIGITS.search(word.text)):  # fmt: skip
                boxes.append(word.bbox)
    return boxes


def render(path: Path, page: int, clip: BBox, masks: Iterable[BBox]) -> bytes:
    """The clipped region as a PNG with the masked words painted black."""
    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]
        pix = doc[page - 1].get_pixmap(dpi=CROP_DPI, clip=pymupdf.Rect(*clip))  # type: ignore[no-untyped-call]
        image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("L")
    scale = CROP_DPI / 72
    draw = ImageDraw.Draw(image)
    for x0, y0, x1, y1 in masks:
        draw.rectangle(
            ((x0 - clip[0]) * scale - 1, (y0 - clip[1]) * scale - 1,
             (x1 - clip[0]) * scale + 1, (y1 - clip[1]) * scale + 1),
            fill=0,
        )  # fmt: skip
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def line_clip(ocr: OcrResult, line_no: int | None) -> tuple[Line, BBox] | None:
    """The OCR line and its padded box, if the line exists."""
    line = ocr.line(line_no) if line_no is not None else None
    if line is None:
        return None
    size = ocr.pages[line.page - 1]
    return line, padded(line.bbox, size.width, size.height)


def allowed_fields(conn: Connection, row: store.DocumentRow) -> dict[str, int]:
    """field -> line number, for every field whose evidence line may be shown as a crop."""
    ocr = store.ocr_of(conn, row.document_id)
    allowed = {}
    for field, line_no in store.field_lines(conn, row.document_id).items():
        found = line_clip(ocr, line_no)
        if found is None:
            continue
        line, clip = found
        if refusal(line.page, clip, ocr.pages[line.page - 1].height, line.text) is None:
            allowed[field] = line.line_no
    return allowed


def crop_urls(conn: Connection, row: store.DocumentRow) -> dict[str, str]:
    """field -> URL of its redacted line image."""
    return {
        field: f"/documents/{row.document_id}/fields/{field}/crop"
        for field in allowed_fields(conn, row)
    }


def field_crop(
    conn: Connection, row: store.DocumentRow, field: str, path: Path,
    redact: Callable[[str], str],
) -> bytes:  # fmt: skip
    """The redacted line image of one field; CropRefusedError if it may not be shown."""
    ocr = store.ocr_of(conn, row.document_id)
    found = line_clip(ocr, store.field_lines(conn, row.document_id).get(field))
    if found is None:
        raise CropRefusedError("the field has no evidence line")
    line, clip = found
    reason = refusal(line.page, clip, ocr.pages[line.page - 1].height, line.text)
    if reason is not None:
        raise CropRefusedError(reason)
    return render(path, line.page, clip, masked_words(ocr.lines, clip, redact))
