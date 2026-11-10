"""Vocabulary lookups: source code -> concept -> standard concept, and hierarchy searches.

A *source* concept is the code as written in a source system (for example ICD-10-CM T86.11).
OMOP analytics use *standard* concepts (mostly SNOMED, LOINC, RxNorm). A non-standard concept
reaches its standard equivalent through the "Maps to" relationship. `concept_ancestor` holds
every ancestor/descendant pair of standard concepts, so one lookup finds all sub-types.
"""

from dataclasses import dataclass

import psycopg
from psycopg.rows import class_row

from txlens_omop.mappings import normalize_source_code

Connection = psycopg.Connection[tuple[object, ...]]
# Column list for SELECTs; a constant, so building queries with it is safe.
COLUMNS = (
    "c.concept_id, c.concept_name, c.domain_id, c.vocabulary_id, c.concept_class_id, "
    "c.standard_concept, c.concept_code, c.invalid_reason"
)


@dataclass(frozen=True)
class Concept:
    """One row of vocab.concept."""

    concept_id: int
    concept_name: str
    domain_id: str
    vocabulary_id: str
    concept_class_id: str
    standard_concept: str | None
    concept_code: str
    invalid_reason: str | None

    @property
    def is_standard(self) -> bool:
        """True for valid standard concepts (standard_concept = 'S')."""
        return self.standard_concept == "S" and self.invalid_reason is None


def find_concept(conn: Connection, vocabulary_id: str, code: str) -> Concept | None:
    """The concept for a code in a vocabulary, or None."""
    with conn.cursor(row_factory=class_row(Concept)) as cur:
        cur.execute(
            f"SELECT {COLUMNS} FROM vocab.concept c "
            "WHERE c.vocabulary_id = %s AND c.concept_code = %s",
            (vocabulary_id, code),
        )
        return cur.fetchone()


def get_concept(conn: Connection, concept_id: int) -> Concept | None:
    """The concept with this id, or None."""
    with conn.cursor(row_factory=class_row(Concept)) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM vocab.concept c WHERE c.concept_id = %s", (concept_id,))
        return cur.fetchone()


def maps_to(conn: Connection, concept_id: int) -> list[Concept]:
    """Valid standard concepts that a concept "Maps to"."""
    with conn.cursor(row_factory=class_row(Concept)) as cur:
        cur.execute(
            f"SELECT {COLUMNS} FROM vocab.concept_relationship r "
            "JOIN vocab.concept c ON c.concept_id = r.concept_id_2 "
            "WHERE r.concept_id_1 = %s AND r.relationship_id = 'Maps to' "
            "AND r.invalid_reason IS NULL "
            "AND c.standard_concept = 'S' AND c.invalid_reason IS NULL "
            "ORDER BY c.concept_id",
            (concept_id,),
        )
        return cur.fetchall()


def standard_concepts(conn: Connection, vocabulary_id: str, code: str) -> list[Concept]:
    """Standard concept(s) for a source code: itself if standard, else its "Maps to" targets.

    An empty list means the code is unknown or unmapped: store concept_id 0, keep the source
    value, and send it to human review.
    """
    concept = find_concept(conn, vocabulary_id, code)
    if concept is None:
        return []
    if concept.is_standard:
        return [concept]
    return maps_to(conn, concept.concept_id)


def descendants(conn: Connection, concept_id: int, include_self: bool = True) -> list[Concept]:
    """All descendants of a standard concept (its sub-types), via concept_ancestor."""
    with conn.cursor(row_factory=class_row(Concept)) as cur:
        cur.execute(
            f"SELECT {COLUMNS} FROM vocab.concept_ancestor a "
            "JOIN vocab.concept c ON c.concept_id = a.descendant_concept_id "
            "WHERE a.ancestor_concept_id = %s AND (%s OR a.min_levels_of_separation > 0) "
            "ORDER BY c.concept_id",
            (concept_id, include_self),
        )
        return cur.fetchall()


def ingredients_for_name(conn: Connection, name: str) -> list[Concept]:
    """Standard RxNorm ingredient(s) for a free-text drug name, in priority order.

    1. a reviewed source_to_concept_map row (TXLENS_DRUG), which wins because a person checked it
    2. an exact (case-insensitive) ingredient name or synonym
    3. an exact Brand Name, followed through "Brand name of" to its ingredient(s)
    Never a substring match. An empty list means unmapped: concept_id 0 and human review.
    """
    key = normalize_source_code(name)
    steps = (
        "SELECT target_concept_id FROM vocab.source_to_concept_map "
        "WHERE source_vocabulary_id = 'TXLENS_DRUG' AND source_code = %(key)s "
        "AND invalid_reason IS NULL",
        "SELECT c.concept_id FROM vocab.concept c WHERE c.vocabulary_id = 'RxNorm' "
        "AND c.concept_class_id = 'Ingredient' AND lower(c.concept_name) = %(key)s "
        "UNION SELECT s.concept_id FROM vocab.concept_synonym s "
        "JOIN vocab.concept c ON c.concept_id = s.concept_id "
        "WHERE c.vocabulary_id = 'RxNorm' AND c.concept_class_id = 'Ingredient' "
        "AND lower(s.concept_synonym_name) = %(key)s",
        "SELECT r.concept_id_2 FROM vocab.concept b "
        "JOIN vocab.concept_relationship r ON r.concept_id_1 = b.concept_id "
        "AND r.relationship_id = 'Brand name of' AND r.invalid_reason IS NULL "
        "JOIN vocab.concept i ON i.concept_id = r.concept_id_2 "
        "AND i.concept_class_id = 'Ingredient' "
        "WHERE b.vocabulary_id = 'RxNorm' AND b.concept_class_id = 'Brand Name' "
        "AND b.invalid_reason IS NULL AND lower(b.concept_name) = %(key)s",
    )
    for query in steps:
        ids = sorted(int(str(row[0])) for row in conn.execute(query, {"key": key}).fetchall())
        found = [c for c in (get_concept(conn, i) for i in ids) if c is not None]
        standard = [c for c in found if c.is_standard]
        if standard:
            return standard
    return []
