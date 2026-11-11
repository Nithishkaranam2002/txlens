"""OCR for scanned PDFs: every word with its page, bounding box and confidence, grouped in lines.

Boxes are in PDF points (1/72 inch) with the origin at the page's top-left corner, so they do
not depend on the resolution the page was rendered at. Confidence is 0..1.

Two engines produce the same `OcrResult`:
- Tesseract (default): renders each page at 300 dpi, straightens it, removes speckles with a
  median filter and reads words with `image_to_data`, which gives a confidence per word. Then
  score and grade tokens after known anchors ("scores:", "Banff grade", ...) are read a second
  time allowing only the characters such a token can contain ("vO" -> "v0"). This read 96.7%
  of key tokens exactly on 100 reports (`make ocr-report`, evals/reports/ocr.md).
- Docling (`OCR_ENGINE=docling`, opt-in dependency group): layout-aware conversion with
  Tesseract inside. On our one-column reports it read as many key tokens as plain Tesseract,
  more slowly and without word confidence, so it is not the default. If it is missing, fails
  or reads almost nothing, the Tesseract engine runs instead.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pymupdf
import pytesseract
from PIL import Image

from txlens_backend.documents.preprocess import OcrSettings, prepare

BBox = tuple[float, float, float, float]  # x0, y0, x1, y1 in points, origin top-left
Engine = Literal["tesseract", "docling"]
POINTS_PER_INCH = 72
DEFAULT_SETTINGS = OcrSettings()
# Second reading of tokens whose characters are known: (the line contains, anchor word,
# characters allowed in the words after it, how many words (None = to the line end), what a
# valid token looks like). A word is replaced only by a valid re-read.
REREADS = (
    ("banff lesion", "scores", "itvgpc0123", None, re.compile(r"(i|t|v|g|ptc|ci|ct|cv)[0-3]")),
    ("capillaries", "capillaries", "C4d0123", 1, re.compile(r"C4d[0-3]")),
    ("banff grade", "grade", "IAB.", 1, re.compile(r"(IA|IB|IIA|IIB|III)\.?")),
    ("ishlt 2004", "2004", "0123R", 1, re.compile(r"[0-3]R")),
    ("ishlt grade", "grade", "0123R", 1, re.compile(r"[0-3]R")),
)
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


def render_page(page: pymupdf.Page, settings: OcrSettings) -> Image.Image:
    """The page as a grayscale image, prepared for Tesseract."""
    pix = page.get_pixmap(dpi=settings.dpi, colorspace=pymupdf.csGRAY)
    return prepare(Image.frombytes("L", (pix.width, pix.height), pix.samples), settings)


def tesseract_words(image: Image.Image, config: str) -> list[list[Word]]:
    """Words of an image (pixel boxes) grouped by Tesseract's (block, paragraph, line)."""
    data = pytesseract.image_to_data(image, config=config, output_type=pytesseract.Output.DICT)
    grouped: dict[tuple[int, int, int], list[Word]] = {}
    for i, text in enumerate(data["text"]):
        confidence = float(data["conf"][i])
        if not text.strip() or confidence < 0:
            continue
        x, y, w, h = (float(data[k][i]) for k in ("left", "top", "width", "height"))
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        grouped.setdefault(key, []).append(
            Word(text.strip(), (x, y, x + w, y + h), confidence / 100)
        )
    return list(grouped.values())


def reread(
    image: Image.Image, words: list[Word], allowed: str, valid: re.Pattern[str]
) -> list[Word]:
    """The same words read again as one line with only the allowed characters; a word is
    replaced only when its re-read is a valid token."""
    x0, y0, x1, y1 = union(w.bbox for w in words)
    pad = (y1 - y0) / 2
    box = (max(x0 - pad, 0), max(y0 - pad, 0), x1 + pad, y1 + pad)
    config = f'--psm 7 -c "tessedit_char_whitelist={allowed} "'
    found = [w for line in tesseract_words(image.crop(box), config) for w in line]
    if len(found) != len(words):
        return words  # the re-read split differently: keep the first reading
    chosen = []
    for first, again in zip(words, found, strict=True):
        if not valid.fullmatch(again.text):
            chosen.append(first)
            continue
        x0, y0, x1, y1 = again.bbox
        chosen.append(
            Word(again.text, (x0 + box[0], y0 + box[1], x1 + box[0], y1 + box[1]), again.confidence)
        )
    return chosen


def apply_rereads(image: Image.Image, line: list[Word]) -> list[Word]:
    """Re-read score and grade tokens after their anchor words (see REREADS)."""
    text = " ".join(w.text for w in line).lower()
    for context, anchor, allowed, count, valid in REREADS:
        if context not in text:
            continue
        at = next((i for i, w in enumerate(line) if anchor in w.text.lower()), None)
        if at is None or at + 1 >= len(line):
            continue
        end = len(line) if count is None else at + 1 + count
        line = [*line[: at + 1], *reread(image, line[at + 1 : end], allowed, valid), *line[end:]]
    return line


def to_points(line: list[Word], scale: float) -> list[Word]:
    """Pixel boxes -> PDF points."""
    return [
        Word(
            w.text,
            (w.bbox[0] * scale, w.bbox[1] * scale, w.bbox[2] * scale, w.bbox[3] * scale),
            w.confidence,
        )
        for w in line
    ]


def read_page(page: pymupdf.Page, settings: OcrSettings) -> list[list[Word]]:
    """One page's lines of words, boxes in points."""
    image = render_page(page, settings)
    lines = tesseract_words(image, f"--psm {settings.psm}")
    if settings.whitelist_rereads:
        lines = [apply_rereads(image, line) for line in lines]
    scale = POINTS_PER_INCH / settings.pixels_per_inch
    return [to_points(line, scale) for line in lines]


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


def ocr_with_tesseract(path: Path, settings: OcrSettings = DEFAULT_SETTINGS) -> OcrResult:
    """Read every page with Tesseract."""
    with pymupdf.open(path) as doc:  # type: ignore[no-untyped-call]  # pymupdf is untyped here
        sizes = tuple(PageSize(p.rect.width, p.rect.height) for p in doc)
        pages = [(number, read_page(page, settings)) for number, page in enumerate(doc, start=1)]
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


def to_json(result: OcrResult) -> dict[str, Any]:
    """The result as plain JSON (stored on app.document)."""
    return {
        "engine": result.engine,
        "pages": [[p.width, p.height] for p in result.pages],
        "lines": [
            {
                "line_no": line.line_no,
                "page": line.page,
                "words": [[w.text, list(w.bbox), w.confidence] for w in line.words],
            }
            for line in result.lines
        ],
    }


def from_json(data: dict[str, Any]) -> OcrResult:
    """Rebuild a result stored with `to_json`."""
    lines = tuple(
        Line(
            line["line_no"],
            line["page"],
            tuple(
                Word(text, (box[0], box[1], box[2], box[3]), conf)
                for text, box, conf in line["words"]
            ),
        )
        for line in data["lines"]
    )
    pages = tuple(PageSize(width, height) for width, height in data["pages"])
    return OcrResult(data["engine"], pages, lines)
