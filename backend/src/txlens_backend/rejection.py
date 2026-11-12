"""The graft-rejection concept set, shared by the note NLP review rule and the review queue."""

from sqlalchemy import Connection, text

from txlens_backend.clinical_codes import REJECTION

REJECTION_SQL = text(
    "SELECT c.concept_id, coalesce(a.descendant_concept_id, c.concept_id) "
    "FROM vocab.concept c LEFT JOIN vocab.concept_ancestor a "
    "ON a.ancestor_concept_id = c.concept_id "
    "WHERE (c.vocabulary_id, c.concept_code) IN ((:v1, :c1), (:v2, :c2))"
)


def rejection_concept_ids(conn: Connection) -> frozenset[int]:
    """Concept ids of graft rejection: the SNOMED concept, its descendants and borderline."""
    (v1, c1), (v2, c2) = REJECTION
    rows = conn.execute(REJECTION_SQL, {"v1": v1, "c1": c1, "v2": v2, "c2": c2})
    return frozenset(int(i) for row in rows for i in row)
