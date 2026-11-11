"""Keep patient identifiers away from the LLM; read the report header locally for linking.

Three layers, each enough on its own for a clean scan, together robust to OCR damage:
1. Identifier lines are dropped: patient name, MRN, birth date, accession number, address and
   the signer. Labels are matched loosely because OCR mangles them ("MRA", "DOB;").
2. In the lines that remain, every date except the procedure date and every token with five
   or more digits in a row (MRN- and accession-like) is masked.
3. Once the report is linked to a patient, that patient's own name, MRN and birth date are
   scrubbed wherever they still appear.
The header is parsed here, locally: the MRN and procedure date that link the report to a
biopsy never come from the LLM.
"""

import re
from dataclasses import dataclass
from datetime import date

from rapidfuzz import fuzz

from txlens_backend.documents.ocr import OcrResult

# Labels of lines that only identify the patient (or the specimen/signer): never sent.
IDENTIFIER_LABELS = ("patient", "accession", "address", "electronically", "signed", "phone")
SHORT_LABELS = ("mrn", "dob", "ssn")  # too short for fuzzy matching: exact or prefix only
LABEL_SIMILARITY = 80
NAME_SIMILARITY = 85
# m/d/yyyy as OCR reads it: odd separators, and stray spaces inside numbers ("03/1 5/2024").
DATE = re.compile(
    r"(?<!\d)(\d(?: ?\d)?)\s*[/.'\-]\s*(\d(?: ?\d)?)\s*[/.'\-]\s*(\d(?: ?\d){3})(?!\d)"
)
MRN = re.compile(r"M\s?R\s?N\W*([A-Z0-9][A-Z0-9\-]{4,})", re.IGNORECASE)
EDGE_PUNCTUATION = re.compile(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$")
LONG_NUMBER = re.compile(r"\S*\d{5,}\S*")
WORD = re.compile(r"[A-Za-z][A-Za-z'\-]+")
MASK_DATE = "[DATE]"
MASK_ID = "[ID]"
MASK_NAME = "[NAME]"


@dataclass(frozen=True)
class Header:
    """What the report header says about whose biopsy this is, read without the LLM."""

    mrn: str | None
    procedure_date: date | None
    procedure_line: int | None


@dataclass(frozen=True)
class Identity:
    """A linked patient's identifiers (from app.patient_identity), used only for scrubbing."""

    mrn: str
    given_name: str | None
    family_name: str | None
    birth_date: date


@dataclass(frozen=True)
class MinimizedText:
    """The lines the LLM may see (line number, text) and the numbers of dropped lines."""

    lines: tuple[tuple[int, str], ...]
    removed: tuple[int, ...]

    def text_of(self, line_no: int) -> str | None:
        """The (masked) text sent for a line, or None if it was not sent."""
        return next((text for n, text in self.lines if n == line_no), None)


def tokens(text: str) -> list[str]:
    """Lower-case words with OCR junk around them stripped ("_MRN:" -> "mrn")."""
    stripped = (EDGE_PUNCTUATION.sub("", t).lower() for t in text.split())
    return [t for t in stripped if t]


def is_label(token: str, label: str) -> bool:
    """True if an OCR token is (a damaged spelling of) a label."""
    if label in SHORT_LABELS:
        return token.startswith(label)
    return fuzz.ratio(token, label) >= LABEL_SIMILARITY


def is_identifier_line(text: str) -> bool:
    """True for lines that carry a patient identifier label (anywhere in the line)."""
    words = tokens(text)
    labels = (*IDENTIFIER_LABELS, *SHORT_LABELS)
    return any(is_label(w, label) for w in words for label in labels)


def parse_date(text: str) -> date | None:
    """The first m/d/yyyy date in the text (separators as OCR reads them), if valid."""
    for match in DATE.finditer(text):
        month, day, year = (int(g.replace(" ", "")) for g in match.groups())
        try:
            return date(year, month, day)
        except ValueError:
            continue
    return None


def is_procedure_date_line(text: str) -> bool:
    """True for the 'Procedure date: ...' line."""
    words = tokens(text)
    return len(words) >= 2 and fuzz.ratio(" ".join(words[:2]), "procedure date") >= 80


def parse_mrn(text: str) -> str | None:
    """The identifier after the MRN label ("MRN: A1000005", "MRN:A1000005")."""
    match = MRN.search(text)
    if match is None or sum(c.isdigit() for c in match.group(1)) < 4:
        return None
    return match.group(1)


def parse_header(ocr: OcrResult) -> Header:
    """MRN and procedure date from the report header."""
    mrn = procedure_date = procedure_line = None
    for line in ocr.lines:
        if mrn is None:
            mrn = parse_mrn(line.text)
        if procedure_line is None and is_procedure_date_line(line.text):
            procedure_line, procedure_date = line.line_no, parse_date(line.text)
    return Header(mrn, procedure_date, procedure_line)


def mask_dates(text: str, keep_first: bool) -> str:
    """Replace dates with [DATE], keeping the first one when asked (the procedure date)."""
    seen = 0

    def replace(match: re.Match[str]) -> str:
        nonlocal seen
        seen += 1
        return match.group(0) if keep_first and seen == 1 else MASK_DATE

    return DATE.sub(replace, text)


def scrub_identity(text: str, identity: Identity) -> str:
    """Replace the patient's own name and MRN, even slightly misspelled by OCR."""
    names = [n.lower() for n in (identity.given_name, identity.family_name) if n]

    def name(match: re.Match[str]) -> str:
        word = match.group(0).lower()
        return (
            MASK_NAME
            if any(fuzz.ratio(word, n) >= NAME_SIMILARITY for n in names)
            else match.group(0)
        )

    text = WORD.sub(name, text) if names else text
    mrn = identity.mrn.lower()
    return " ".join(
        MASK_ID if fuzz.ratio(t.strip(".,:;").lower(), mrn) >= NAME_SIMILARITY else t
        for t in text.split(" ")
    )


def minimize(ocr: OcrResult, header: Header, identity: Identity | None = None) -> MinimizedText:
    """The report text the LLM may see, with identifiers removed or masked."""
    kept, removed = [], []
    for line in ocr.lines:
        if is_identifier_line(line.text):
            removed.append(line.line_no)
            continue
        text = mask_dates(line.text, keep_first=line.line_no == header.procedure_line)
        text = LONG_NUMBER.sub(MASK_ID, text)
        if identity is not None:
            text = scrub_identity(text, identity)
        kept.append((line.line_no, text))
    return MinimizedText(tuple(kept), tuple(removed))
