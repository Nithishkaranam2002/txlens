"""Check every code we use against the loaded vocabulary.

For each code: does it exist, is its domain the one we expect, and is it standard (or does it
"Map to" a standard concept)? The vocabulary's own name is shown next to our label, because a
code can exist and still mean something else; that part needs a human eye.
"""

from dataclasses import dataclass

import psycopg

from txlens_omop.codes import CodeUse
from txlens_omop.vocab import Concept, find_concept, get_concept, maps_to

Connection = psycopg.Connection[tuple[object, ...]]
OK = "OK"


@dataclass(frozen=True)
class CodeCheck:
    """Result for one code."""

    use: CodeUse
    concept: Concept | None
    standard: tuple[Concept, ...]
    problems: tuple[str, ...]

    @property
    def status(self) -> str:
        """OK, or the problems joined."""
        return OK if not self.problems else "; ".join(self.problems)


def check_code(conn: Connection, use: CodeUse) -> CodeCheck:
    """Check one code."""
    concept = find_concept(conn, use.vocabulary_id, use.code)
    if concept is None:
        return CodeCheck(use, None, (), ("NOT FOUND",))
    standard = (concept,) if concept.is_standard else tuple(maps_to(conn, concept.concept_id))
    problems = []
    if concept.invalid_reason is not None:
        problems.append(f"DEPRECATED ({concept.invalid_reason})")
    if not standard:
        problems.append("NO STANDARD MAPPING")
    domains = {c.domain_id for c in standard} or {concept.domain_id}
    if use.expected_domain not in domains:
        problems.append(
            f"WRONG DOMAIN ({'/'.join(sorted(domains))}, expected {use.expected_domain})"
        )
    return CodeCheck(use, concept, standard, tuple(problems))


def check_codes(conn: Connection, uses: list[CodeUse]) -> list[CodeCheck]:
    """Check many codes."""
    return [check_code(conn, use) for use in uses]


def suggest_ingredients(conn: Connection, name: str, limit: int = 3) -> list[Concept]:
    """Standard RxNorm ingredients whose name contains `name` (for drugs we left uncoded)."""
    rows = conn.execute(
        "SELECT concept_id FROM vocab.concept WHERE vocabulary_id = 'RxNorm' "
        "AND concept_class_id = 'Ingredient' AND standard_concept = 'S' "
        "AND invalid_reason IS NULL AND concept_name ILIKE %s "
        "ORDER BY length(concept_name), concept_id LIMIT %s",
        (f"%{name}%", limit),
    ).fetchall()
    found = [get_concept(conn, int(str(row[0]))) for row in rows]
    return [c for c in found if c is not None]


def format_report(results: list[CodeCheck]) -> str:
    """A plain-text table, one code per block."""
    lines = []
    for r in results:
        name = r.concept.concept_name if r.concept else "-"
        lines.append(f"[{r.status}] {r.use.vocabulary_id} {r.use.code}  (ours: {r.use.label})")
        lines.append(f"    vocabulary says: {name}")
        if r.concept and not r.concept.is_standard:
            targets = ", ".join(
                f"{c.vocabulary_id} {c.concept_code} {c.concept_name}" for c in r.standard
            )
            lines.append(f"    maps to: {targets or '-'}")
        lines.append(f"    used in: {r.use.used_in}")
    failed = sum(1 for r in results if r.problems)
    lines.append(f"\n{len(results) - failed} OK, {failed} with problems, {len(results)} checked.")
    return "\n".join(lines)
