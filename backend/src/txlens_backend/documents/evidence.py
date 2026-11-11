"""Check that every extracted value is backed by the document, and locate it on the page.

A value is kept only if (1) it has evidence, (2) the cited line was sent to the model, (3) the
quote matches words on that line, and (4) the value can be read back out of the quote, e.g.
`banff_t = 2` needs a quote such as "t2". The page, bounding box and OCR confidence come from
our own OCR words, never from the model. Otherwise the value is dropped and the problem
recorded, which sends the document to review.

OCR confuses look-alike characters in short tokens ("gO" for g0, "cil" for ci1, "liA" for
IIA). Score and grade tokens are normalized before comparison; other words are not touched.
"""

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from txlens_backend.documents.ocr import Line, OcrResult, union
from txlens_backend.documents.phi import EDGE_PUNCTUATION, MinimizedText, parse_date
from txlens_backend.documents.schema import SCORE_LABELS, Finding

FieldValue = str | int | date | None
QUOTE_SIMILARITY = 85
KEYWORD_SIMILARITY = 85
DIGIT_LOOKALIKES = {"o": "0", "d": "0", "q": "0", "l": "1", "i": "1", "|": "1", "!": "1", "z": "2"}
ROMAN_LOOKALIKES = str.maketrans({"l": "i", "1": "i", "|": "i", "!": "i", "j": "i"})
DIAGNOSIS_WORDS = {
    "no_rejection": ("no evidence of rejection", "no rejection"),
    "borderline": ("borderline",),
    "TCMR": ("t-cell mediated", "cellular rejection"),
    "ABMR": ("antibody-mediated",),
    "mixed": ("t-cell mediated", "antibody-mediated"),
}
FINDING_WORDS = {
    "BK_nephropathy": ("polyomavirus", "bk"),
    "ATN": ("tubular injury", "tubular necrosis"),
    "CMV": ("cmv", "cytomegalovirus"),
}
SCORE_PREFIXES = {label.lower() for label in SCORE_LABELS.values()}
LEADING_JUNK = re.compile(r"^[^A-Za-z0-9]+")
PAMR = re.compile(r"pAMR\s*([0-3OoDd])", re.IGNORECASE)
TRAILING_JUNK = re.compile(r"[^A-Za-z0-9|!]+$")


@dataclass(frozen=True)
class VerifiedField:
    """One field after the evidence check."""

    field: str
    value: FieldValue
    extracted_value: FieldValue
    line_no: int | None
    page: int | None
    bbox: tuple[float, float, float, float] | None
    source_text: str | None
    ocr_confidence: float | None
    problems: tuple[str, ...]


def bare(token: str) -> str:
    """A token without OCR junk around it, lower case."""
    return EDGE_PUNCTUATION.sub("", token).lower()


def score_in_token(token: str, label: str) -> int | None:
    """The score a token like "ptc2" (or "ptcd", "{2", "cil") gives for a label, if any."""
    text = TRAILING_JUNK.sub("", token).lower()
    if len(text) < 2:
        return None
    last = DIGIT_LOOKALIKES.get(text[-1], text[-1])
    if not last.isdigit():
        return None
    raw_prefix, wanted = text[:-1], label.lower()
    prefix = LEADING_JUNK.sub("", raw_prefix)
    if prefix == wanted:
        return int(last)
    # A damaged label ("{" for "t", "c40" for "c4d") counts if it is not another label itself.
    damaged = Levenshtein.distance(raw_prefix, wanted) <= 1 and prefix not in SCORE_PREFIXES
    return int(last) if damaged else None


def roman(token: str) -> str:
    """A grade token with look-alikes for I mapped to I ("liA" -> "IIA")."""
    return bare(token).translate(ROMAN_LOOKALIKES).upper()


def acr(token: str) -> str:
    """An ISHLT grade token with look-alike digits fixed ("OR" -> "0R", "lR" -> "1R")."""
    text = bare(token)
    return (DIGIT_LOOKALIKES.get(text[:1], text[:1]) + text[1:]).upper()


def has_words(quote: str, phrases: tuple[str, ...]) -> bool:
    """True if any phrase appears in the quote (tolerating small OCR errors)."""
    lowered = quote.lower()
    return any(fuzz.partial_ratio(p, lowered) >= KEYWORD_SIMILARITY for p in phrases)


def value_in_quote(field: str, value: Any, quote: str) -> bool:
    """True if the value can be read out of the quoted words."""
    words = quote.split()
    if field == "biopsy_date":
        return bool(parse_date(quote) == value)
    if field in SCORE_LABELS:
        return any(score_in_token(w, SCORE_LABELS[field]) == value for w in words)
    if field == "adequacy":
        return any(bare(w) == value for w in words) or (
            # One OCR slip is fine, but "adequate" must not match "inadequate".
            any(Levenshtein.distance(bare(w), value) == 1 for w in words)
            and not any(bare(w).endswith("adequate") and bare(w) != value for w in words)
        )
    if field == "tcmr_grade":
        return any(roman(w) == value for w in words)
    if field == "acr_grade":
        return any(acr(w) == value for w in words)
    if field == "pamr_grade":
        # "pAMR 0", or as OCR may read it: "pAMRO", "pAMR O".
        found = PAMR.search(quote)
        if found is None:
            return False
        digit = found[1].lower()
        return bool(DIGIT_LOOKALIKES.get(digit, digit) == value.split()[1])
    if field == "diagnosis":
        return has_words(quote, DIAGNOSIS_WORDS[value])
    if field == "additional_finding":
        return value == "other" or has_words(quote, FINDING_WORDS[value])
    raise ValueError(f"no evidence rule for field {field}")


def best_span(line: Line, quote: str) -> tuple[int, int, float]:
    """The run of words on the line that best matches the quote: (start, end, similarity)."""
    target, size = quote.lower(), max(len(quote.split()), 1)
    best = (0, 0, 0.0)
    for length in range(max(size - 1, 1), size + 2):
        for start in range(0, max(len(line.words) - length, 0) + 1):
            text = " ".join(w.text for w in line.words[start : start + length]).lower()
            score = fuzz.ratio(text, target)
            if score > best[2]:
                best = (start, start + length, score)
    return best


def rejected(field: str, value: FieldValue, problem: str) -> VerifiedField:
    """A field whose value was dropped for lack of valid evidence."""
    return VerifiedField(field, None, value, None, None, None, None, None, (problem,))


def verify(field: str, finding: Finding[Any], ocr: OcrResult, sent: MinimizedText) -> VerifiedField:
    """Check one field's evidence and attach its location on the page."""
    value, evidence = finding.value, finding.evidence
    if value is None:
        return VerifiedField(field, None, None, None, None, None, None, None, ())
    if evidence is None:
        return rejected(field, value, "value has no evidence")
    line = ocr.line(evidence.line)
    if line is None or sent.text_of(evidence.line) is None:
        return rejected(field, value, f"cited line {evidence.line} was not in the document")
    start, end, similarity = best_span(line, evidence.quote)
    if similarity < QUOTE_SIMILARITY:
        return rejected(field, value, f"quote not found on line {evidence.line}")
    if not value_in_quote(field, value, evidence.quote):
        return rejected(field, value, f"quote does not state {value}")
    words = line.words[start:end]
    return VerifiedField(
        field,
        value,
        value,
        line.line_no,
        line.page,
        union(w.bbox for w in words),
        " ".join(w.text for w in words),
        min(w.confidence for w in words),
        (),
    )
