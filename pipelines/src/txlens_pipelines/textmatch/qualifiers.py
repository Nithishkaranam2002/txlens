"""Clinical qualifiers that two names must share before one may stand for the other.

"Type 1 diabetes" is not "type 2 diabetes", "chronic rejection" is not "acute rejection" and
"CKD stage 3" is not "stage 4", however similar the strings look. Matching therefore never
strips these words: it extracts them from both names and an automatic match is only accepted
when the two sets are identical (missing or extra qualifiers both count as a difference).
"""

import re

Qualifier = tuple[str, str]

ROMAN = {"i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6"}
NUMBER = r"(\d+|i{1,3}|iv|vi?)"
# (kind, pattern); the first group is the value, optional letter suffixes (stage 3a) kept.
LABELLED = (
    ("type", rf"\btype\s*{NUMBER}\b"),
    ("grade", rf"\bgrade\s*{NUMBER}([a-c]?)\b"),
    ("stage", rf"\bstage\s*{NUMBER}([a-c]?)\b"),
    ("class", rf"\bclass\s*{NUMBER}\b"),
)
ABBREVIATED_TYPE = re.compile(r"\bt([12])dm\b")
WORDS = {
    "acuity": ("acute", "chronic", "subacute", "hyperacute", "accelerated"),
    "side": ("left", "right", "bilateral"),
}
# Abbreviations that carry a qualifier: "CKD" is chronic kidney disease.
IMPLIED = {"ckd": ("acuity", "chronic"), "aki": ("acuity", "acute")}
BARE_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")


def arabic(value: str) -> str:
    """'ii' -> '2'; digits unchanged."""
    return ROMAN.get(value, value)


def qualifiers(text: str) -> frozenset[Qualifier]:
    """Every type, grade, stage, class, acuity, side and bare number in `text`."""
    lowered = text.lower()
    found: set[Qualifier] = set()
    rest = lowered
    for kind, pattern in LABELLED:
        for match in re.finditer(pattern, lowered):
            suffix = match.group(2) if match.lastindex and match.lastindex > 1 else ""
            found.add((kind, arabic(match.group(1)) + (suffix or "")))
        rest = re.sub(pattern, " ", rest)
    for match in ABBREVIATED_TYPE.finditer(lowered):
        found.add(("type", match.group(1)))
    rest = ABBREVIATED_TYPE.sub(" ", rest)
    for kind, words in WORDS.items():
        found |= {(kind, w) for w in words if re.search(rf"\b{w}\b", rest)}
    found |= {q for abbreviation, q in IMPLIED.items() if re.search(rf"\b{abbreviation}\b", rest)}
    found |= {("number", n) for n in BARE_NUMBER.findall(rest)}
    return frozenset(found)


def same_qualifiers(source: str, *candidates: str) -> bool:
    """True when every candidate name carries exactly the qualifiers of `source`."""
    wanted = qualifiers(source)
    return all(qualifiers(c) == wanted for c in candidates)
