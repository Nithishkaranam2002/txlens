"""The terms the note NLP looks for, each tied to one concept, read from the OMOP vocabulary.

Sources:
- curated: `TXLENS_NOTE_ABBREV` (clinic shorthand such as Cr, tacro, DGF, KTx; a code may be
  organ-specific, "kidney:tcmr") and `TXLENS_DRUG` rows of source_to_concept_map. They are
  used as they are and win over automatic names. The TXLENS_NOTE_ABBREV rows were curated by
  an AI assistant and are pending clinical review;
- vocabulary: preferred names and English synonyms of the SNOMED conditions and procedures
  and the RxNorm ingredients TxLens uses (txlens_omop.codes), plus RxNorm brand names. These
  are automatic, so a name is kept only when it carries the same clinical qualifiers as the
  concept's preferred name (the guard shared with the pipeline: "Type 1 diabetes" never
  stands for a type 2 concept).

A term whose text names two different concepts is marked ambiguous; the NLP gives such a
mention concept 0 and sends it to review.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import Literal

from sqlalchemy import Connection, text

from txlens_backend.rejection import rejection_concept_ids
from txlens_omop.codes import CodeUse, all_codes
from txlens_pipelines.textmatch.qualifiers import same_qualifiers

Category = Literal["condition", "medication", "lab", "procedure"]
CATEGORY_BY_DOMAIN: dict[str, Category] = {
    "Condition": "condition",
    "Drug": "medication",
    "Measurement": "lab",
    "Procedure": "procedure",
}
ORGANS = ("kidney", "heart")
CURATED = ("TXLENS_NOTE_ABBREV", "TXLENS_DRUG")
ENGLISH = 4180186
MAX_WORDS = 6
SEMANTIC_TAG = re.compile(r"\s*\([a-z ]+\)$")


@dataclass(frozen=True)
class Term:
    """One surface form and the concept it names (organ: only for organ-specific terms)."""

    text: str
    concept_id: int
    category: Category
    curated: bool
    organ: str | None = None


@dataclass(frozen=True)
class Vocabulary:
    """Terms by lower-case text: the organ-specific ones and the general ones."""

    terms: dict[tuple[str | None, str], Term]
    ambiguous: frozenset[str]
    rejection: frozenset[int] = frozenset()  # graft-rejection concepts (see rejection.py)

    def lookup(self, surface: str, organ: str | None) -> Term | None:
        """The term for this text in this organ's notes (organ-specific first)."""
        key = " ".join(surface.lower().split())
        return self.terms.get((organ, key)) or self.terms.get((None, key))

    def texts(self) -> set[str]:
        """Every surface form to match."""
        return {t for _, t in self.terms} | set(self.ambiguous)


def seed_codes(uses: Iterable[CodeUse]) -> tuple[list[str], list[str]]:
    """(SNOMED condition/procedure codes, RxNorm codes) TxLens relies on."""
    snomed = [
        u.code
        for u in uses
        if u.vocabulary_id == "SNOMED" and u.expected_domain in ("Condition", "Procedure")
    ]
    rxnorm = [u.code for u in uses if u.vocabulary_id == "RxNorm"]  # fmt: skip
    return snomed, rxnorm


CURATED_SQL = text(
    "SELECT m.source_code, c.concept_id, c.domain_id FROM vocab.source_to_concept_map m "
    "JOIN vocab.concept c ON c.concept_id = m.target_concept_id "
    "WHERE m.source_vocabulary_id = ANY(:vocabularies) AND m.invalid_reason IS NULL"
)
# Standard concepts of the seed codes, their preferred names and English synonyms, and the
# brand names of seed ingredients ("Brand name of").
NAMES_SQL = text(
    """
    WITH seeds AS (
        SELECT concept_id, concept_name, domain_id FROM vocab.concept
        WHERE invalid_reason IS NULL AND standard_concept = 'S' AND (
            (vocabulary_id = 'SNOMED' AND concept_code = ANY(:snomed))
            OR (vocabulary_id = 'RxNorm' AND concept_code = ANY(:rxnorm)))
    )
    SELECT s.concept_id, s.concept_name AS name, s.concept_name AS preferred, s.domain_id
    FROM seeds s
    UNION ALL
    SELECT s.concept_id, y.concept_synonym_name, s.concept_name, s.domain_id
    FROM seeds s JOIN vocab.concept_synonym y ON y.concept_id = s.concept_id
    WHERE y.language_concept_id = :english
    UNION ALL
    SELECT s.concept_id, b.concept_name, b.concept_name, s.domain_id
    FROM seeds s
    JOIN vocab.concept_relationship r
      ON r.concept_id_2 = s.concept_id AND r.relationship_id = 'Brand name of'
      AND r.invalid_reason IS NULL
    JOIN vocab.concept b ON b.concept_id = r.concept_id_1 AND b.invalid_reason IS NULL
    """
)


def clean(name: str) -> str:
    """Lower case, SNOMED tag such as '(disorder)' removed, spaces collapsed."""
    return " ".join(SEMANTIC_TAG.sub("", name.strip()).lower().split())


def split_organ(code: str) -> tuple[str | None, str]:
    """'kidney:tcmr' -> ('kidney', 'tcmr'); 'cr' -> (None, 'cr')."""
    head, sep, rest = code.partition(":")
    return (head, rest) if sep and head in ORGANS else (None, code)


def build(curated: Iterable[Term], automatic: Iterable[Term]) -> Vocabulary:
    """Curated terms win; automatic terms naming two concepts become ambiguous."""
    terms: dict[tuple[str | None, str], Term] = {(t.organ, t.text): t for t in curated}
    seen: dict[str, int] = {}
    ambiguous: set[str] = set()
    for term in automatic:
        key = (None, term.text)
        if key in terms and terms[key].curated:
            continue
        if seen.get(term.text, term.concept_id) != term.concept_id:
            ambiguous.add(term.text)
        seen[term.text] = term.concept_id
        terms[key] = term
    for name in ambiguous:
        terms.pop((None, name), None)
    return Vocabulary(terms, frozenset(ambiguous))


def load_vocabulary(conn: Connection) -> Vocabulary:
    """Every note term from the loaded vocabulary (curated and guarded automatic ones)."""
    curated = []
    for code, concept_id, domain in conn.execute(CURATED_SQL, {"vocabularies": list(CURATED)}):
        organ, surface = split_organ(code)
        if domain in CATEGORY_BY_DOMAIN:
            curated.append(
                Term(clean(surface), int(concept_id), CATEGORY_BY_DOMAIN[domain], True, organ)
            )
    snomed, rxnorm = seed_codes(all_codes())
    automatic = []
    rows = conn.execute(NAMES_SQL, {"snomed": snomed, "rxnorm": rxnorm, "english": ENGLISH})
    for concept_id, name, preferred, domain in rows:
        surface = clean(name)
        if (domain in CATEGORY_BY_DOMAIN and len(surface.split()) <= MAX_WORDS
                and same_qualifiers(surface, preferred)):  # fmt: skip
            automatic.append(Term(surface, int(concept_id), CATEGORY_BY_DOMAIN[domain], False))
    return replace(build(curated, automatic), rejection=rejection_concept_ids(conn))
