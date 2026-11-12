"""Restricted names: real company and product names our own text must not contain.

The project rule: no real transplant-diagnostics company or product name in our code, UI,
docs, generated data or the copilot's own wording. The copilot says "a commercial dd-cfDNA
assay" instead; only verbatim snippets of cited abstracts may contain such names.

The names themselves must not be in the repository either, so restricted_terms.tsv holds only
salted SHA-256 hashes of each lower-cased name (1-3 words) and the generic wording that
replaces it. Text is checked by hashing its 1-, 2- and 3-word sequences.

Add a name (it is hashed; the name is not written anywhere):
    uv run python -m txlens_backend.copilot.restricted add "<name>" "<generic replacement>"
"""

import hashlib
import re
import sys
from dataclasses import dataclass
from functools import cache
from pathlib import Path

SALT = "txlens-restricted-v1"
TERMS_FILE = Path(__file__).with_name("restricted_terms.tsv")
TOKEN = re.compile(r"[A-Za-z0-9]+")
JOINER = re.compile(r"[ \t-]+")  # words of one name: separated by spaces or hyphens only
MAX_WORDS = 3


def digest(term: str) -> str:
    """The salted hash of a name: lower-cased words joined by single spaces."""
    words = " ".join(t.lower() for t in TOKEN.findall(term))
    return hashlib.sha256(f"{SALT}:{words}".encode()).hexdigest()


@dataclass(frozen=True)
class Match:
    """A restricted name found in a text: where it is and what replaces it."""

    start: int
    end: int
    replacement: str


@cache
def load(path: Path = TERMS_FILE) -> dict[str, str]:
    """hash -> generic replacement."""
    terms: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            hashed, replacement = line.split("\t", 1)
            terms[hashed] = replacement
    return terms


def find(value: str, terms: dict[str, str] | None = None) -> list[Match]:
    """Restricted names in a text, longest first where they overlap, left to right."""
    terms = load() if terms is None else terms
    tokens = list(TOKEN.finditer(value))
    found: list[Match] = []
    i = 0
    while i < len(tokens):
        span = 1  # how many words from i form one phrase (no punctuation or line break between)
        while (
            span < MAX_WORDS
            and i + span < len(tokens)
            and JOINER.fullmatch(value[tokens[i + span - 1].end() : tokens[i + span].start()])
        ):
            span += 1
        for n in range(span, 0, -1):
            words = " ".join(t.group().lower() for t in tokens[i : i + n])
            hashed = hashlib.sha256(f"{SALT}:{words}".encode()).hexdigest()
            if hashed in terms:
                found.append(Match(tokens[i].start(), tokens[i + n - 1].end(), terms[hashed]))
                i += n - 1
                break
        i += 1
    return found


def neutralize(value: str, terms: dict[str, str] | None = None) -> tuple[str, int]:
    """The text with each restricted name replaced by its generic wording, and how many."""
    matches = find(value, terms)
    for m in reversed(matches):
        value = value[: m.start] + m.replacement + value[m.end :]
    return value, len(matches)


def main(argv: list[str] | None = None) -> None:
    """`add <name> <replacement>`: append the name's hash to restricted_terms.tsv."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 3 or args[0] != "add":
        raise SystemExit(__doc__)
    _, name, replacement = args
    hashed = digest(name)
    if hashed in load():
        raise SystemExit("already listed")
    with TERMS_FILE.open("a") as out:
        out.write(f"{hashed}\t{replacement}\n")


if __name__ == "__main__":
    main()
