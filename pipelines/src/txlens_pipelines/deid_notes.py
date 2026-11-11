"""Note de-identification: identifiers out, surrogates in, before any NLP or LLM sees a note.

What is replaced:
- the patient's own names (any case, whole words) -> consistent surrogate names, chosen by
  HMAC so the same patient always gets the same ones;
- MRNs and other long identifiers -> "[MRN]" / "[ID]" (a realistic fake MRN could be
  mistaken for another patient's);
- dates (ISO, mm/dd/yyyy, m/d/yy and year-less mm/dd) -> the same date moved by this patient's
  shift (the backend's date_shift_days, so data stewards see one consistent calendar), written
  in the original style. Intervals and temporal wording stay intact for NLP;
- ages of 90 and over -> "90+"; phone numbers, e-mail and street addresses -> tags.

The result keeps a replacement list (offsets in both texts plus the original words). It lets
the API restore the original sentence for clinicians and lets the evaluation map ground-truth
offsets, without the identified note text ever entering the database.

Pure Python (no Spark), so it is unit-tested directly and runs inside a UDF on Databricks.
"""

import hashlib
import hmac
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

MAX_SHIFT_DAYS = 182
TOP_AGE = 90
SURROGATE_GIVEN = (
    "Maren",
    "Tobin",
    "Ilse",
    "Corwin",
    "Wren",
    "Anselm",
    "Petra",
    "Lucan",
    "Odile",
    "Ambrose",
    "Sabine",
    "Rurik",
    "Tamsin",
    "Evander",
    "Liesel",
    "Darby",
)
SURROGATE_FAMILY = ("Hollis", "Brask", "Ventnor", "Quill", "Ashdown", "Merrow", "Calloway",
                    "Thistlewood", "Pemberly", "Rookwood", "Stanmore", "Larkin", "Fairbairn",
                    "Holloway", "Kestrel", "Ormsby")  # fmt: skip
TAGS = {"mrn": "[MRN]", "id": "[ID]", "phone": "[PHONE]", "email": "[EMAIL]",
        "address": "[ADDRESS]", "date": "[DATE]"}  # fmt: skip

ISO_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
US_DATE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})(?![\d/])")
# Year-less dates need two digits each, so "2/2" (secondary to) is not a date.
SHORT_DATE = re.compile(r"(?<![\d/])(\d{2})/(\d{2})(?![\d/])")
AGE = re.compile(r"\b(\d{2,3})(\s?(?:y/o|yo|year-old|years old|year old)\b)", re.IGNORECASE)
MRN_FIELD = re.compile(r"MRN:\s*(\S+)")
LONG_ID = re.compile(r"\b(?=[A-Z0-9-]*\d{6})[A-Z]{0,4}-?(?:[A-Z]-)?\d{6,}\b")
PHONE = re.compile(r"(?<!\d)(?:\(\d{3}\)\s?|\d{3}[-. ])\d{3}[-.]\d{4}(?!\d)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
ADDRESS = re.compile(
    r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s+){1,3}"
    r"(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Drive|Dr|Boulevard|Blvd|Court|Ct|Way)\b"
)


@dataclass(frozen=True)
class Identity:
    """The patient's direct identifiers (from the identity table)."""

    mrn: str
    given_name: str | None
    family_name: str | None
    birth_date: date | None


@dataclass(frozen=True)
class Replacement:
    """One replaced span: where it is in the de-identified and in the original text."""

    deid_start: int
    deid_end: int
    orig_start: int
    orig_end: int
    kind: str
    original: str


@dataclass(frozen=True)
class Deidentified:
    """De-identified text and its replacement list (in text order)."""

    text: str
    spans: tuple[Replacement, ...]


@dataclass(frozen=True)
class Found:
    """A span of the original text and what replaces it."""

    start: int
    end: int
    kind: str
    surrogate: str


def _digest(key: str, label: str, tenant_id: str, person_id: int) -> bytes:
    message = f"{label}:{tenant_id}:{person_id}".encode()
    return hmac.new(key.encode(), message, hashlib.sha256).digest()


def date_shift_days(key: str, tenant_id: str, person_id: int) -> int:
    """This patient's shift in days (-182..-1 or 1..182); same as txlens_backend.deid."""
    n = int.from_bytes(_digest(key, "shift", tenant_id, person_id)[:4], "big") % (
        2 * MAX_SHIFT_DAYS
    )
    return n - MAX_SHIFT_DAYS if n < MAX_SHIFT_DAYS else n - MAX_SHIFT_DAYS + 1


def surrogate_names(
    key: str, tenant_id: str, person_id: int, identity: Identity
) -> tuple[str, str]:
    """(given, family) surrogates for this patient, never equal to either real name."""
    digest = _digest(key, "name", tenant_id, person_id)
    real = {(n or "").strip().lower() for n in (identity.given_name, identity.family_name)}

    def pick(options: tuple[str, ...], seed: int) -> str:
        for step in range(len(options)):
            name = options[(seed + step) % len(options)]
            if name.lower() not in real:
                return name
        raise ValueError("no surrogate name differs from the real ones")

    return pick(SURROGATE_GIVEN, digest[0]), pick(SURROGATE_FAMILY, digest[1])


def safe_date(year: int, month: int, day: int) -> date | None:
    """The date, or None when it does not exist."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def full_year(two_digits: int, note_date: date) -> int:
    """'24' -> 2024; a year after the note's year is taken as the previous century."""
    year = 2000 + two_digits
    return year if year <= note_date.year else year - 100


def date_finders(shift: int, note_date: date) -> list[tuple[re.Pattern[str], Callable[..., str]]]:
    """(pattern, match -> surrogate) for every date style, shifted by `shift` days."""
    move = timedelta(days=shift)

    def iso(m: re.Match[str]) -> str:
        day = safe_date(int(m[1]), int(m[2]), int(m[3]))
        return (day + move).isoformat() if day else TAGS["date"]

    def us(m: re.Match[str]) -> str:
        year = int(m[3]) if len(m[3]) == 4 else full_year(int(m[3]), note_date)
        day = safe_date(year, int(m[1]), int(m[2]))
        if day is None:
            return TAGS["date"]
        moved = day + move
        if len(m[3]) == 4:
            return moved.strftime("%m/%d/%Y")
        return f"{moved.month}/{moved.day}/{moved:%y}"

    def short(m: re.Match[str]) -> str:
        day = safe_date(note_date.year, int(m[1]), int(m[2]))
        if day is not None and day > note_date:
            day = safe_date(note_date.year - 1, int(m[1]), int(m[2]))
        return (day + move).strftime("%m/%d") if day else TAGS["date"]

    return [(ISO_DATE, iso), (US_DATE, us), (SHORT_DATE, short)]


def find_phi(text: str, identity: Identity, names: tuple[str, str], shift: int,
             note_date: date) -> list[Found]:  # fmt: skip
    """Every identifier span with its surrogate; overlaps keep the earliest, longest span."""
    found: list[Found] = []

    def add(pattern: re.Pattern[str], kind: str,
            surrogate: str | Callable[[re.Match[str]], str], group: int = 0) -> None:  # fmt: skip
        for m in pattern.finditer(text):
            value = surrogate if isinstance(surrogate, str) else surrogate(m)
            found.append(Found(m.start(group), m.end(group), kind, value))

    add(MRN_FIELD, "mrn", TAGS["mrn"], group=1)
    if identity.mrn:
        add(re.compile(re.escape(identity.mrn)), "mrn", TAGS["mrn"])
    for real, surrogate in zip((identity.given_name, identity.family_name), names, strict=True):
        if real and len(real.strip()) > 1:
            pattern = re.compile(rf"\b{re.escape(real.strip())}\b", re.IGNORECASE)
            add(pattern, "name", surrogate)
    for pattern, replace in date_finders(shift, note_date):
        add(pattern, "date", replace)
    add(AGE, "age", lambda m: f"{TOP_AGE}+" if int(m[1]) >= TOP_AGE else m[1], group=1)
    for kind, pattern in (("email", EMAIL), ("phone", PHONE), ("address", ADDRESS),
                          ("id", LONG_ID)):  # fmt: skip
        add(pattern, kind, TAGS[kind])
    kept: list[Found] = []
    for f in sorted(found, key=lambda f: (f.start, -(f.end - f.start))):
        if kept and f.start < kept[-1].end:
            continue
        if text[f.start : f.end] != f.surrogate:  # e.g. an age under 90 stays as is
            kept.append(f)
    return kept


def deidentify(text: str, identity: Identity, names: tuple[str, str], shift: int,
               note_date: date) -> Deidentified:  # fmt: skip
    """The note with every identifier replaced, plus the replacement list."""
    parts: list[str] = []
    spans: list[Replacement] = []
    cursor = 0
    length = 0
    for f in find_phi(text, identity, names, shift, note_date):
        parts.append(text[cursor : f.start])
        length += f.start - cursor
        original = text[f.start : f.end]
        spans.append(
            Replacement(length, length + len(f.surrogate), f.start, f.end, f.kind, original)
        )
        parts.append(f.surrogate)
        length += len(f.surrogate)
        cursor = f.end
    parts.append(text[cursor:])
    return Deidentified("".join(parts), tuple(spans))


def reidentify(text: str, spans: tuple[Replacement, ...] | list[Replacement],
               start: int = 0) -> str:  # fmt: skip
    """`text` (a de-identified slice beginning at `start`) with the original words put back."""
    out, cursor = [], start
    for s in sorted(spans, key=lambda s: s.deid_start):
        if s.deid_start < cursor or s.deid_end > start + len(text):
            continue
        out.append(text[cursor - start : s.deid_start - start])
        out.append(s.original)
        cursor = s.deid_end
    out.append(text[cursor - start :])
    return "".join(out)


def to_original_offset(spans: tuple[Replacement, ...] | list[Replacement], offset: int) -> int:
    """Where de-identified offset `offset` falls in the original text."""
    delta = 0
    for s in sorted(spans, key=lambda s: s.deid_start):
        if offset < s.deid_start:
            break
        if offset < s.deid_end:  # inside a surrogate: its original start
            return s.orig_start
        delta = s.orig_end - s.deid_end
    return offset + delta
