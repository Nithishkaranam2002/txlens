"""Condition concept search for the cohort builder.

Searches standard SNOMED condition concepts in the shared vocabulary (no patient data). A cohort
rule on a concept also matches its descendants, so each hit says how many more specific
concepts it covers.
"""

from pydantic import BaseModel
from sqlalchemy import Connection, text

SEARCH = text(
    "SELECT c.concept_id, c.concept_name, c.concept_code, "
    "  (SELECT count(*) - 1 FROM vocab.concept_ancestor ca "
    "   WHERE ca.ancestor_concept_id = c.concept_id) AS descendants "
    "FROM vocab.concept c "
    "WHERE c.domain_id = 'Condition' AND c.vocabulary_id = 'SNOMED' "
    "AND c.standard_concept = 'S' AND c.invalid_reason IS NULL "
    "AND (c.concept_name ILIKE :pattern OR c.concept_code = :q) "
    "ORDER BY c.concept_code = :q DESC, c.concept_name ILIKE :prefix DESC, "
    "length(c.concept_name), c.concept_id LIMIT :limit"
)


class ConceptHit(BaseModel):
    """A condition concept a cohort rule can use."""

    concept_id: int
    name: str
    code: str
    descendants: int  # more specific concepts the rule also matches


def escape_like(value: str) -> str:
    """The value with LIKE wildcards taken literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_conditions(conn: Connection, q: str, limit: int) -> list[ConceptHit]:
    """Conditions whose name contains `q` or whose SNOMED code equals it; exact codes, then
    name prefixes, then shorter names first."""
    term = escape_like(q.strip())
    rows = conn.execute(
        SEARCH, {"q": q.strip(), "pattern": f"%{term}%", "prefix": f"{term}%", "limit": limit}
    ).all()
    return [
        ConceptHit(concept_id=r.concept_id, name=r.concept_name, code=r.concept_code,
                   descendants=max(r.descendants, 0))
        for r in rows
    ]  # fmt: skip
