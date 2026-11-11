"""OCR: line grouping and numbering, word boxes, engine fallback, and real Tesseract runs."""

from pathlib import Path

import pytest
from document_testutils import require_tesseract, write_report_pdf
from PIL import Image

from txlens_backend.documents import ocr
from txlens_backend.documents.ocr import Line, OcrResult, PageSize, Word

LETTER = PageSize(612, 792)


def word(text: str, x: float, confidence: float = 0.9) -> Word:
    return Word(text, (x, 10, x + 10, 20), confidence)


def test_lines_are_numbered_across_pages() -> None:
    pages = [(1, [[word("a", 0)], [word("b", 0)]]), (2, [[word("c", 0)]])]
    lines = ocr.number_lines(pages)
    assert [(line.line_no, line.page, line.text) for line in lines] == [
        (1, 1, "a"),
        (2, 1, "b"),
        (3, 2, "c"),
    ]


def test_line_text_box_and_confidence_come_from_its_words() -> None:
    line = Line(1, 1, (word("Banff", 0, 0.8), word("i2", 20, 0.4)))
    assert line.text == "Banff i2"
    assert line.bbox == (0, 10, 30, 20)
    assert line.confidence == pytest.approx(0.6)


def test_split_words_gives_each_word_a_proportional_box() -> None:
    words = ocr.split_words("ab  cd", (0, 0, 60, 10), 0.7)
    assert [w.text for w in words] == ["ab", "cd"]
    assert words[0].bbox == (0, 0, 20, 10)
    assert words[1].bbox == (40, 0, 60, 10)
    assert {w.confidence for w in words} == {0.7}


def test_words_are_grouped_into_lines_by_position() -> None:
    a = Word("scores:", (100, 230, 128, 237), 0.9)
    b = Word("t2", (140, 231, 148, 238), 0.9)
    c = Word("Banff", (55, 229, 74, 237), 0.9)
    d = Word("C4d0", (185, 245, 206, 252), 0.9)
    lines = ocr.group_into_lines([d, b, a, c])
    assert [[w.text for w in line] for line in lines] == [["Banff", "scores:", "t2"], ["C4d0"]]


def test_result_finds_lines_and_counts_words() -> None:
    lines = ocr.number_lines([(1, [[word("a", 0), word("b", 20)], [word("c", 0)]])])
    result = OcrResult("tesseract", (LETTER,), lines)
    assert result.word_count == 3
    found = result.line(2)
    assert found is not None
    assert found.text == "c"
    assert result.line(9) is None


def fake_result(engine: str, words: int) -> OcrResult:
    lines = ocr.number_lines([(1, [[word("w", 0)] * words])]) if words else ()
    return OcrResult(engine, (LETTER,), lines)


@pytest.mark.parametrize(
    ("docling", "expected"),
    [
        (lambda _: fake_result("docling", 30), "docling"),
        (lambda _: fake_result("docling", 3), "tesseract"),  # read almost nothing
        (None, "tesseract"),  # not installed
    ],
)
def test_docling_falls_back_to_tesseract(
    monkeypatch: pytest.MonkeyPatch, docling: object, expected: str
) -> None:
    monkeypatch.setattr(ocr, "docling_reader", lambda: docling)
    monkeypatch.setattr(ocr, "ocr_with_tesseract", lambda _: fake_result("tesseract", 30))
    assert ocr.ocr_pdf(Path("x.pdf"), "docling").engine == expected


def test_docling_crash_falls_back_to_tesseract(monkeypatch: pytest.MonkeyPatch) -> None:
    def crash(_: Path) -> OcrResult:
        raise ValueError("broken page")

    monkeypatch.setattr(ocr, "docling_reader", lambda: crash)
    monkeypatch.setattr(ocr, "ocr_with_tesseract", lambda _: fake_result("tesseract", 30))
    assert ocr.ocr_pdf(Path("x.pdf"), "docling").engine == "tesseract"


def test_tesseract_is_the_default_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ocr, "docling_reader", lambda: pytest.fail("docling must not run"))
    monkeypatch.setattr(ocr, "ocr_with_tesseract", lambda _: fake_result("tesseract", 30))
    assert ocr.ocr_pdf(Path("x.pdf")).engine == "tesseract"


def test_tesseract_reads_a_clean_report_exactly(tmp_path: Path) -> None:
    require_tesseract()
    result = ocr.ocr_with_tesseract(write_report_pdf(tmp_path / "r.pdf", scanned=False))
    text = "\n".join(line.text for line in result.lines)
    assert "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0" in text
    assert "Procedure date: 05/06/2024" in text
    assert result.pages == (LETTER,)


def test_tesseract_words_have_boxes_on_the_page_and_confidence(tmp_path: Path) -> None:
    require_tesseract()
    result = ocr.ocr_with_tesseract(write_report_pdf(tmp_path / "r.pdf"))
    assert result.word_count > 40
    for line in result.lines:
        x0, y0, x1, y1 = line.bbox
        assert 0 <= x0 < x1 <= LETTER.width
        assert 0 <= y0 < y1 <= LETTER.height
        assert all(0 <= w.confidence <= 1 for w in line.words)
    tops = [line.bbox[1] for line in result.lines]
    assert tops == sorted(tops)  # reading order: top to bottom


def test_docling_engine_reads_the_report(tmp_path: Path) -> None:
    pytest.importorskip("docling")
    require_tesseract()
    from txlens_backend.documents.docling_ocr import ocr_with_docling

    result = ocr_with_docling(write_report_pdf(tmp_path / "r.pdf", scanned=False))
    assert result.engine == "docling"
    assert any("Procedure date" in line.text for line in result.lines)


@pytest.mark.parametrize("scanned", [False, True])
def test_score_and_grade_tokens_are_read_exactly(tmp_path: Path, scanned: bool) -> None:
    require_tesseract()
    result = ocr.ocr_with_tesseract(write_report_pdf(tmp_path / "r.pdf", scanned=scanned))
    texts = {line.text for line in result.lines}
    assert "Banff lesion scores: i2 t2 v0 g0 ptc0 ci1 ct1 cv0" in texts
    assert "C4d (IHC, peritubular capillaries): C4d0" in texts
    assert "- Acute T-cell mediated rejection, Banff grade IA." in texts


def score_line(*tokens: str) -> list[Word]:
    words = ["Banff", "lesion", "scores:", *tokens]
    return [Word(w, (10.0 * i, 0, 10.0 * i + 8, 10), 0.9) for i, w in enumerate(words)]


@pytest.mark.parametrize(
    ("first", "again", "expected"),
    [
        (["vO", "gO"], ["v0", "g0"], ["v0", "g0"]),  # valid re-reads replace look-alikes
        (["i2", "cvO"], ["12", "cv"], ["i2", "cvO"]),  # invalid re-reads keep the first reading
        (["vO", "gO"], ["v0g0"], ["vO", "gO"]),  # split differently: first reading kept
    ],
)
def test_rereads_only_replace_words_with_valid_tokens(
    monkeypatch: pytest.MonkeyPatch, first: list[str], again: list[str], expected: list[str]
) -> None:
    second = [[Word(w, (0, 0, 8, 10), 0.8) for w in again]]
    monkeypatch.setattr(ocr, "tesseract_words", lambda _image, _config: second)
    line = ocr.apply_rereads(Image.new("L", (200, 40), 255), score_line(*first))
    assert [w.text for w in line[3:]] == expected
