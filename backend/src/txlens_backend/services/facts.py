"""Per-patient facts (organ, donor type, transplant date, status) as one reusable SQL CTE.

Everything is found by vocabulary code and `concept_ancestor`, so a more specific concept
(e.g. "Live donor renal transplant") still counts as a kidney transplant. All queries run
inside a tenant transaction, so RLS limits them to the caller's center.
"""

from collections.abc import Mapping

from sqlalchemy import Connection, text

from txlens_backend.clinical_codes import DONOR_TYPE, GRAFT_FAILURE, TRANSPLANT

FACTS_CTE = """
transplant AS (
    SELECT DISTINCT ON (po.person_id)
        po.person_id,
        po.procedure_date AS transplant_date,
        CASE a.concept_code WHEN :kidney_code THEN 'kidney' ELSE 'heart' END AS organ,
        CASE pc.concept_code WHEN :living_code THEN 'living'
                             WHEN :deceased_code THEN 'deceased' END AS donor_type
    FROM cdm.procedure_occurrence po
    JOIN vocab.concept pc ON pc.concept_id = po.procedure_concept_id
    JOIN vocab.concept_ancestor ca ON ca.descendant_concept_id = po.procedure_concept_id
    JOIN vocab.concept a ON a.concept_id = ca.ancestor_concept_id
    WHERE a.vocabulary_id = 'SNOMED' AND a.concept_code IN (:kidney_code, :heart_code)
    ORDER BY po.person_id, po.procedure_date
),
graft_failure AS (
    SELECT co.person_id, min(co.condition_start_date) AS failure_date
    FROM cdm.condition_occurrence co
    JOIN vocab.concept_ancestor ca ON ca.descendant_concept_id = co.condition_concept_id
    JOIN vocab.concept a ON a.concept_id = ca.ancestor_concept_id
    WHERE a.vocabulary_id = 'SNOMED' AND a.concept_code = ANY(:graft_failure_codes)
    GROUP BY co.person_id
),
facts AS (
    SELECT
        p.person_id, p.tenant_id, p.year_of_birth,
        CASE p.gender_concept_id WHEN 8507 THEN 'male' WHEN 8532 THEN 'female' END AS sex,
        t.transplant_date, t.organ, t.donor_type, d.death_date,
        CASE WHEN d.death_date IS NOT NULL THEN 'deceased'
             WHEN g.person_id IS NOT NULL THEN 'graft_failure'
             ELSE 'active' END AS status
    FROM cdm.person p
    LEFT JOIN transplant t ON t.person_id = p.person_id
    LEFT JOIN cdm.death d ON d.person_id = p.person_id
    LEFT JOIN graft_failure g ON g.person_id = p.person_id
)
"""
FACTS_PARAMS: Mapping[str, object] = {
    "kidney_code": TRANSPLANT["kidney"][1],
    "heart_code": TRANSPLANT["heart"][1],
    "living_code": next(code for code, kind in DONOR_TYPE.items() if kind == "living"),
    "deceased_code": next(code for code, kind in DONOR_TYPE.items() if kind == "deceased"),
    "graft_failure_codes": [code for _, code in GRAFT_FAILURE],
}


def concept_keys(conn: Connection, codes: Mapping[str, tuple[str, str]]) -> dict[int, str]:
    """concept_id -> our key, for a {key: (vocabulary, code)} map; codes not loaded are skipped."""
    found: dict[int, str] = {}
    for key, (vocabulary, code) in codes.items():
        concept_id = conn.execute(
            text(
                "SELECT concept_id FROM vocab.concept "
                "WHERE vocabulary_id = :v AND concept_code = :c"
            ),
            {"v": vocabulary, "c": code},
        ).scalar_one_or_none()
        if concept_id is not None:
            found[int(concept_id)] = key
    return found


def descendant_ids(conn: Connection, ancestors: list[int]) -> set[int]:
    """Every concept under (and including) these concepts."""
    rows = conn.execute(
        text(
            "SELECT descendant_concept_id FROM vocab.concept_ancestor "
            "WHERE ancestor_concept_id = ANY(:ids)"
        ),
        {"ids": ancestors},
    ).scalars()
    return {int(r) for r in rows} | set(ancestors)
