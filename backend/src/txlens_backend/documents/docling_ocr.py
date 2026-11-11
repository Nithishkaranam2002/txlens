"""Docling OCR engine (opt-in dependency group `docling`), producing the same `OcrResult`.

Docling converts the page with Tesseract as its OCR engine and returns text items with boxes
(bottom-left origin). It does not expose per-word confidence, so every word on a page gets that
page's OCR score from Docling's confidence report. Word boxes are cut from the line box in
proportion to character offsets: good enough to highlight evidence, not exact.
"""

import math
from functools import cache
from pathlib import Path
from typing import Any

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    OcrMode,
    PdfPipelineOptions,
    TesseractCliOcrOptions,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

from txlens_backend.documents.ocr import (
    OcrResult,
    PageSize,
    Word,
    group_into_lines,
    number_lines,
    split_words,
)

UNKNOWN_CONFIDENCE = 0.5


@cache
def converter() -> Any:
    """One converter per process: loading Docling's layout model takes seconds."""
    options = PdfPipelineOptions(
        do_ocr=True,
        do_table_structure=False,
        ocr_options=TesseractCliOcrOptions(mode=OcrMode.FULL_PAGE, lang=["eng"]),
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


def ocr_with_docling(path: Path) -> OcrResult:
    """Read every page with Docling."""
    result = converter().convert(path)
    document = result.document
    sizes = {n: PageSize(p.size.width, p.size.height) for n, p in document.pages.items()}
    scores = {n: p.ocr_score for n, p in result.confidence.pages.items()}
    words: dict[int, list[Word]] = {n: [] for n in sizes}
    for item in document.texts:
        if not item.prov or not item.text.strip():
            continue
        prov = item.prov[0]
        box = prov.bbox.to_top_left_origin(sizes[prov.page_no].height).as_tuple()
        score = scores.get(prov.page_no, UNKNOWN_CONFIDENCE)
        confidence = UNKNOWN_CONFIDENCE if math.isnan(score) else float(score)
        words[prov.page_no] += split_words(item.text, box, confidence)
    # Docling may return one item per word on sharp pages, so lines are rebuilt from positions.
    ordered = [(n, group_into_lines(page_words)) for n, page_words in sorted(words.items())]
    return OcrResult("docling", tuple(sizes[n] for n, _ in ordered), number_lines(ordered))
