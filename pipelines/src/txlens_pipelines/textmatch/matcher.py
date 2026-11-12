"""Short clinical text -> one concept, with the method and a confidence, or a review item.

Tiers (the first that gives exactly one concept wins):
1. curated source_to_concept_map row (TxLens's own map; confidence 1.0)
2. exact concept name or synonym, case and spacing ignored (1.0)
3. normalized: same words in any order, punctuation, SNOMED tags and filler words ignored (0.95)
4. fuzzy: closest name by token-sorted similarity, accepted at >= the threshold (the score)

Tiers 2-4 are automatic, so their answer must also pass the qualifier guard (qualifiers.py)
against the matched name and the concept's preferred name. Anything that fails, ties between
two concepts, or scores too low becomes a review item that carries the best suggestion.
Fuzzy uses difflib, so the Databricks job needs no extra library.
"""

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher

from txlens_pipelines.textmatch.qualifiers import ROMAN, same_qualifiers

FUZZY_THRESHOLD = 0.90
NORMALIZED_CONFIDENCE = 0.95
STOPWORDS = frozenset({"a", "an", "and", "by", "due", "in", "of", "the", "to", "with"})
SEMANTIC_TAG = re.compile(r"\s*\((disorder|finding|situation|procedure|morphologic abnormality)\)$")
METHODS = ("curated", "exact", "normalized", "fuzzy")


@dataclass(frozen=True)
class Term:
    """One name of a concept (its preferred name or a synonym)."""

    concept_id: int
    text: str


@dataclass(frozen=True)
class Match:
    """The outcome for one text: a concept, or a review item with the best suggestion."""

    concept_id: int | None
    method: str  # one of METHODS, or "review"
    confidence: float
    suggestion: int | None = None
    reason: str | None = None  # why it went to review


def exact_key(text: str) -> str:
    """Lower case, trimmed, inner whitespace collapsed (same as normalize_source_code)."""
    return re.sub(r"\s+", " ", text.strip().lower())


def token_key(text: str) -> str:
    """Words without punctuation, SNOMED semantic tags or filler, sorted; roman numerals as
    digits so 'type II' equals 'type 2'. Qualifier words are kept."""
    lowered = SEMANTIC_TAG.sub("", text.strip().lower())
    words = re.findall(r"[a-z0-9]+", lowered)
    return " ".join(sorted(ROMAN.get(w, w) for w in words if w not in STOPWORDS))


class ConceptMatcher:
    """Matches texts against a fixed set of concept names and curated mappings."""

    def __init__(
        self,
        terms: Iterable[Term],
        preferred: Mapping[int, str],
        curated: Mapping[str, int],
        fuzzy_threshold: float = FUZZY_THRESHOLD,
    ) -> None:
        self.preferred = dict(preferred)
        self.curated = {exact_key(k): v for k, v in curated.items()}
        self.threshold = fuzzy_threshold
        self.exact: dict[str, set[int]] = defaultdict(set)
        self.tokens: dict[str, set[int]] = defaultdict(set)
        self.names: dict[tuple[str, int], str] = {}
        for term in terms:
            self.exact[exact_key(term.text)].add(term.concept_id)
            key = token_key(term.text)
            self.tokens[key].add(term.concept_id)
            self.names.setdefault((key, term.concept_id), term.text)

    def match(self, text: str) -> Match:
        """The concept `text` names, or a review item."""
        key = exact_key(text)
        if key in self.curated:
            return Match(self.curated[key], "curated", 1.0)
        if key in self.exact:
            return self.accept(text, self.exact[key], "exact", 1.0, text)
        tokens = token_key(text)
        if tokens in self.tokens:
            ids = self.tokens[tokens]
            name = self.names[(tokens, min(ids))]
            return self.accept(text, ids, "normalized", NORMALIZED_CONFIDENCE, name)
        return self.fuzzy(text, tokens)

    def accept(self, text: str, ids: set[int], method: str, score: float, name: str) -> Match:
        """One concept that keeps the source's qualifiers, else a review item."""
        best = min(ids)
        if len(ids) > 1:
            return Match(None, "review", score, best, "ambiguous")
        if not same_qualifiers(text, name, self.preferred.get(best, name)):
            return Match(None, "review", score, best, "qualifier_mismatch")
        return Match(best, method, score)

    def fuzzy(self, text: str, tokens: str) -> Match:
        """Closest token-sorted name; accepted only above the threshold."""
        scored: dict[int, tuple[float, str]] = {}
        matcher = SequenceMatcher(None, "", tokens, autojunk=False)  # b is cached
        for key, ids in self.tokens.items():
            matcher.set_seq1(key)
            if matcher.real_quick_ratio() < self.threshold * 0.5:
                continue
            score = matcher.ratio()
            for cid in ids:
                if score > scored.get(cid, (0.0, ""))[0]:
                    scored[cid] = (score, self.names[(key, cid)])
        if not scored:
            return Match(None, "review", 0.0, None, "no_candidate")
        top = max(s for s, _ in scored.values())
        best = {cid for cid, (s, _) in scored.items() if s == top}
        if top < self.threshold:
            return Match(None, "review", round(top, 3), min(best), "low_score")
        name = scored[min(best)][1]
        return self.accept(text, best, "fuzzy", round(top, 3), name)
