"""Quote-grounded literature answers: every claim carries verbatim quotes, checked in code.

When an answer rests on retrieved abstracts, the model does not write free text: it returns
claims, each with quotes copied from the abstracts it cites. A claim is shown only if:
- each of its quotes is found in the cited abstract (title or text) by plain string
  matching, after normalizing case, whitespace and typographic punctuation (no fuzzy match:
  abstracts are exact text, unlike OCR'd biopsy reports);
- a quote has at least MIN_QUOTE_WORDS words (a quote like "rejection" proves nothing);
- every number in the claim appears in one of its verified quotes (a claim may not add
  figures its sources do not state); a year may instead appear anywhere in the cited
  abstract, also written as "'13".
Claims without a verified quote are dropped and counted. Claims from the center's data (no
quotes) are allowed only when a data tool actually returned results.
"""

import re
import unicodedata
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from txlens_backend.copilot.retrieval import Document

MIN_QUOTE_WORDS = 6
MAX_CLAIMS = 8
# Typographic quotes, hyphens and dashes, ellipsis, >= / <= signs, odd spaces -> plain ASCII
# (a model copying a quote may well write the ASCII form).
PUNCTUATION = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                             "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-",
                             "\u2014": "-", "\u2212": "-", "\u2026": "...",
                             "\u2265": ">=", "\u2264": "<=", "\u223c": "~", "\u2217": "*",
                             "\u200b": "",
                             "\u00a0": " "})  # fmt: skip
NUMBER = re.compile(r"(?<![\w.-])\d+(?:\.\d+)?(?![\w-])")


class Quote(BaseModel):
    """Words copied exactly from one retrieved abstract."""

    model_config = ConfigDict(extra="forbid")

    pmid: int
    quote: str = Field(description="Copied word for word from that abstract, 6+ words")


class Claim(BaseModel):
    """One sentence of the answer and the quotes that support it."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="One sentence, in your own words, no citation markers")
    quotes: list[Quote] = Field(
        default_factory=list, max_length=3,
        description="Empty only for facts from the center's data",
    )  # fmt: skip


class GroundedAnswer(BaseModel):
    """The answer as claims; a closing note may state what the sources do not cover."""

    model_config = ConfigDict(extra="forbid")

    claims: list[Claim] = Field(max_length=MAX_CLAIMS)
    note: str | None = Field(default=None, description="Optional, e.g. gaps in the evidence")


def normalize(value: str) -> str:
    """Case-, whitespace- and typography-insensitive form used for matching."""
    value = unicodedata.normalize("NFKC", value).translate(PUNCTUATION).lower()
    return " ".join(value.strip(" .\"'").split())


def quote_found(quote: Quote, docs: dict[int, Document]) -> bool:
    """The quote is long enough and appears verbatim in the abstract it names."""
    doc = docs.get(quote.pmid)
    text = normalize(quote.quote)
    if doc is None or len(text.split()) < MIN_QUOTE_WORDS:
        return False
    return text in normalize(f"{doc.title}\n{doc.abstract}")


@dataclass
class Checked:
    """A claim as shown (None when dropped) and why."""

    text: str | None
    quotes: list[Quote] = field(default_factory=list)
    problem: str | None = None


def year_in(number: str, text: str) -> bool:
    """A year (1950-2039) named anywhere in the cited abstract, also as "'13" for 2013: years
    give context ("Banff 2022") and often sit in the title rather than in the quote."""
    if not (len(number) == 4 and number.isdigit() and 1950 <= int(number) <= 2039):
        return False
    return number in NUMBER.findall(text) or f"'{number[2:]}" in text


def check(claim: Claim, docs: dict[int, Document], *, data_allowed: bool) -> Checked:
    """Keep the claim with its verified quotes, or drop it with the reason."""
    if not claim.quotes:
        if data_allowed:
            return Checked(claim.text)
        return Checked(None, problem="no quote")
    verified = [q for q in claim.quotes if quote_found(q, docs)]
    if not verified:
        return Checked(None, problem="quote not found in the cited abstract")
    quoted = {float(n) for q in verified for n in NUMBER.findall(normalize(q.quote))}
    cited = " ".join(normalize(f"{docs[q.pmid].title} {docs[q.pmid].abstract}") for q in verified)
    missing = [n for n in NUMBER.findall(claim.text)
               if float(n) not in quoted and not year_in(n, cited)]  # fmt: skip
    if missing:
        return Checked(None, verified, problem=f"numbers not in the quotes: {missing}")
    return Checked(claim.text, verified)


def render(text: str, quotes: list[Quote]) -> str:
    """The sentence with its citation markers, e.g. '... rejection [PMID:123]'."""
    pmids = list(dict.fromkeys(q.pmid for q in quotes))
    body = text.rstrip().rstrip(".")
    return body + "".join(f" [PMID:{p}]" for p in pmids) + "."
