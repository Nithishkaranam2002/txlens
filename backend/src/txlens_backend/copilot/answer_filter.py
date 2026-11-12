"""Checks every sentence of a streamed answer before the user sees it.

- Citations: only PMIDs that retrieval returned may be cited; others are removed and counted.
- Grounding: when the answer rests on the literature alone, a sentence stating a finding must
  cite a retrieved abstract, or it is dropped ("refuse claims without a retrieved source").
  A closing sentence starting with "Note:" may be uncited. If nothing survives, a fixed
  sentence says the abstracts did not support an answer.
- Wording: real company and product names in the copilot's own text are replaced by generic
  wording (verbatim snippets of cited abstracts are shown separately and keep theirs).
- Numbers: numbers not found in the tool results are listed (for the evaluation), not hidden.
Sentences are released as they complete, so the answer still streams.
"""

import re
from dataclasses import dataclass, field

from txlens_backend.copilot import restricted

CITATION = re.compile(r"\s*\[PMID:\s*(\d{1,9})\]")
# A sentence ends at . ! ? once the next sentence has visibly begun (so a citation written
# after the period, "... rejection. [PMID:1]", still belongs to it), after a closing citation
# followed by a capital, or at a line break.
BOUNDARY = re.compile(r"(?<=[.!?])\s+(?=[^\s\[])|(?<=\])\s+(?=[A-Z])|\n+")
NUMBER = re.compile(r"(?<![\w.-])\d+(?:\.\d+)?(?![\w-])")  # not labels like "1-year"
UNSUPPORTED = "I could not find support for an answer in the retrieved abstracts."


@dataclass
class FilterStats:
    """What the filter changed."""

    sentences: int = 0
    dropped_uncited: int = 0
    invalid_citations: int = 0
    names_replaced: int = 0
    cited: set[int] = field(default_factory=set)
    ungrounded_numbers: list[str] = field(default_factory=list)


class AnswerFilter:
    """Feed it streamed text; it returns the sentences that may be shown."""

    def __init__(self, retrieved: set[int], *, literature_only: bool, known_numbers: set[str]):
        self.retrieved, self.literature_only = retrieved, literature_only
        self.known_numbers = known_numbers
        self.buffer = ""
        self.released: list[str] = []
        self.stats = FilterStats()

    def feed(self, text: str) -> list[str]:
        """Sentences completed by this piece of text, filtered, each with its separator."""
        self.buffer += text
        out: list[str] = []
        while (boundary := BOUNDARY.search(self.buffer)) is not None:
            sentence = self.check(self.buffer[: boundary.start()])
            separator = "\n" if "\n" in boundary.group() else " "
            self.buffer = self.buffer[boundary.end() :]
            if sentence:
                out.append(sentence + separator)
        return out

    def finish(self) -> list[str]:
        """The last sentence; the fallback if nothing was released."""
        last = self.check(self.buffer)
        self.buffer = ""
        if last:
            return [last]
        if not self.released and self.literature_only:
            self.released.append(UNSUPPORTED)
            return [UNSUPPORTED]
        return []

    def check(self, sentence: str) -> str:
        """The sentence as shown ('' when dropped)."""
        sentence = sentence.strip()
        if not sentence:
            return ""
        self.stats.sentences += 1
        cited = [int(m) for m in CITATION.findall(sentence)]
        valid = [p for p in cited if p in self.retrieved]
        self.stats.invalid_citations += len(cited) - len(valid)
        self.stats.cited.update(valid)
        text = CITATION.sub(lambda m: m.group() if int(m.group(1)) in self.retrieved else "",
                            sentence)  # fmt: skip
        if self.literature_only and not valid and not text.startswith("Note:"):
            self.stats.dropped_uncited += 1
            return ""
        text, replaced = restricted.neutralize(text)
        self.stats.names_replaced += replaced
        if not self.literature_only:
            plain = CITATION.sub("", text)
            self.stats.ungrounded_numbers += [n for n in NUMBER.findall(plain)
                                              if not grounded(n, self.known_numbers)]  # fmt: skip
        self.released.append(text)
        return text


def grounded(number: str, known: set[str]) -> bool:
    """A number appears in the tool results (as given, or rounded to fewer decimals)."""
    if number in known:
        return True
    value = float(number)
    decimals = len(number.split(".")[1]) if "." in number else 0
    return any(round(float(k), decimals) == value for k in known if k.replace(".", "").isdigit())


def numbers_in(value: object) -> set[str]:
    """Every number in a tool result, as text (percentages also as 0-100)."""
    found: set[str] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        found.add(str(value))
        if isinstance(value, float) and 0 <= value <= 1:
            found.add(str(round(value * 100, 4)))
    elif isinstance(value, str):
        found.update(NUMBER.findall(value))
    elif isinstance(value, dict):
        for item in value.values():
            found |= numbers_in(item)
    elif isinstance(value, list | tuple):
        for item in value:
            found |= numbers_in(item)
    return found
