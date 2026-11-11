"""OCR for scanned PDFs: every word with its page, bounding box and confidence, grouped in lines.

Boxes are in PDF points (1/72 inch) with the origin at the page's top-left corner, so they do
not depend on the resolution the page was rendered at. Confidence is 0..1.

Two engines produce the same `OcrResult`:
- Tesseract (default): renders each page at 400 dpi, removes speckles with a median filter and
  reads words with `image_to_data`, which gives a confidence per word. These settings read the
  most key tokens correctly on a 30-report sample (see docs/adr/0013).
- Docling (`OCR_ENGINE=docling`, opt-in dependency group): layout-aware conversion with
  Tesseract inside. On our one-column reports it read as many key tokens as plain Tesseract,
  more slowly and without word confidence, so it is not the default. If it is missing, fails
  or reads almost nothing, the Tesseract engine runs instead.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pymupdf
import pytesseract
from PIL import Image, ImageFilter

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in points, origin top-left
Engine = Literal["tesseract", "docling"]
POINTS_PER_INCH = 72
RENDER_DPI = 400
MEDIAN_FILTER = 3  # pixels; removes scanner speckles without eroding thin strokes
TESSERACT_CONFIG = "--psm 4"  # one column of text of variable sizes
MIN_WORDS = 20  # fewer words than this from Docling means it failed; fall back to Tesseract


@dataclass(frozen=True)
class Word:
    """One recognized word."""

    text: str
    bbox: BBox
    confidence: float


@dataclass(frozen=True)
class Line:
    """One line of text. `line_no` counts from 1 across the whole document."""

    line_no: int
    page: int
    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        """The words joined by single spaces."""
        return " ".join(w.text for w in self.words)

    @property
    def bbox(self) -> BBox:
        """The smallest box around all words."""
        return union(w.bbox for w in self.words)

    @property
    def confidence(self) -> float:
        """Mean word confidence."""
        return sum(w.confidence for w in self.words) / len(self.words)


@dataclass(frozen=True)
class PageSize:
    """Page width and height in points."""

    width: float
    height: float


@dataclass(frozen=True)
class OcrResult:
    """All lines of a document, in reading order, and the engine that read them."""

    engine: str
    pages: tuple[PageSize, ...]
    lines: tuple[Line, ...]

    def line(self, line_no: int) -> Line | None:
        """The line with this number, if any."""
        return next((line for line in self.lines if line.line_no == line_no), None)

    @property
    def word_count(self) -> int:
        """Number of words read."""
        return sum(len(line.words) for line in self.lines)


def union(boxes: Iterable[BBox]) -> BBox:
    """The smallest box containing all boxes."""
    x0s, y0s, x1s, y1s = zip(*boxes, strict=True)
    return (min(x0s), min(y0s), max(x1s), max(y1s))


def render_page(page: pymupdf.Page, dpi: int = RENDER_DPI) -> Image.Image:
    """A grayscale image of the page, speckles removed."""
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    return image.filter(ImageFilter.MedianFilter(MEDIAN_FILTER))


def tesseract_lines(image: Image.Image, scale: float) -> list[list[Word]]:
    """Words of one page image grouped by Tesseract's (block, paragraph, line)."""
    data = pytesseract.image_to_data(
        image, config=TESSERACT_CONFIG, output_type=pytesseract.Output.DICT
    )
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for i, text in enumerate(data["text"]):
        confidence = float(data["conf"][i])
        if not text.strip() or confidence < 0:
            continue
        x, y, w, h = (data[k][i] * scale for k in ("left", "top", "width", "height"))
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append(
            Word(text.strip(), (x, y, x + w, y + h), confidence / 100)
        )
    return list(grouped.values())


def split_words(text: str, box: BBox, confidence: float) -> list[Word]:
    """Words of a line, each with a slice of the line box proportional to its characters."""
    x0, y0, x1, y1 = box
    per_char = (x1 - x0) / max(len(text), 1)
    words, start = [], 0
    for token in text.split():
        start = text.index(token, start)
        left = x0 + start * per_char
        words.append(Word(token, (left, y0, left + len(token) * per_char, y1), confidence))
        start += len(token)
    return words


def group_into_lines(words: Iterable[Word]) -> list[list[Word]]:
    """Words of one page grouped into lines (top to bottom, then left to right).

    A word joins the current line when its vertical centre lies inside the line's first word.
    """
    lines: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.bbox[1] + w.bbox[3]) / 2):
        centre = (w.bbox[1] + w.bbox[3]) / 2
        if lines and lines[-1][0].bbox[1] <= centre <= lines[-1][0].bbox[3]:
            lines[-1].append(w)
        else:
            lines.append([w])
    return [sorted(line, key=lambda w: w.bbox[0]) for line in lines]


def number_lines(pages: Iterable[tuple[int, list[list[Word]]]]) -> tuple[Line, ...]:
    """Lines numbered from 1 in page order."""
    lines: list[Line] = []
    for page, word_lines in pages:
        for words in word_lines:
            lines.append(Line(len(lines) + 1, page, tuple(words)))
    return tuple(lines)


def ocr_with_tesseract(path: Path) -> OcrResult:
    """Read every page with Tesseract."""
    scale = POINTS_PER_INCH / RENDER_DPI
    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]  # pymupdf is untyped here
        sizes = tuple(PageSize(p.rect.width, p.rect.height) for p in doc)
        pages = [
            (number, tesseract_lines(render_page(page), scale))
            for number, page in enumerate(doc, start=1)
        ]
    return OcrResult("tesseract", sizes, number_lines(pages))


def docling_reader() -> Callable[[Path], OcrResult] | None:
    """The Docling engine, or None when the optional `docling` group is not installed."""
    try:
        from txlens_backend.documents.docling_ocr import ocr_with_docling
    except ImportError:
        return None
    return ocr_with_docling


def ocr_pdf(path: Path, engine: Engine = "tesseract") -> OcrResult:
    """OCR a PDF. Docling, when asked for, falls back to Tesseract if missing or failing."""
    reader = docling_reader() if engine == "docling" else None
    if reader is not None:
        try:
            result = reader(path)
        except Exception:  # Docling failing on one file must not stop the document
            result = None
        if result is not None and result.word_count >= MIN_WORDS:
            return result
    return ocr_with_tesseract(path)
