"""Check every code we use against the loaded vocabulary.

For each code: does it exist, is its domain the one we expect, is it standard (or does it
"Map to" a standard concept), and, where queries rely on it, does it sit under the expected
ancestor? The vocabulary's own name is shown next to our label, because a
code can exist and still mean something else; that part needs a human eye.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import psycopg

from txlens_omop.codes import CodeUse
from txlens_omop.mappings import (
    LOCAL_CONCEPT_MIN,
    LOCAL_VOCABULARY,
    SOURCE_VOCABULARIES,
    MappingRow,
)
from txlens_omop.vocab import Concept, find_concept, get_concept, ingredients_for_name, maps_to

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
    if use.descends_from and standard and not descends_from(conn, standard, use.descends_from):
        problems.append(f"NOT UNDER {' '.join(use.descends_from)}")
    return CodeCheck(use, concept, standard, tuple(problems))


def descends_from(conn: Connection, standard: tuple[Concept, ...], parent: tuple[str, str]) -> bool:
    """True if every standard concept is a descendant of the parent code's concept."""
    ancestor = find_concept(conn, *parent)
    if ancestor is None:
        return False
    found = conn.execute(
        "SELECT count(DISTINCT descendant_concept_id) FROM vocab.concept_ancestor "
        "WHERE ancestor_concept_id = %s AND descendant_concept_id = ANY(%s)",
        (ancestor.concept_id, [c.concept_id for c in standard]),
    ).fetchone()
    return found is not None and found[0] == len(standard)


def check_codes(conn: Connection, uses: list[CodeUse]) -> list[CodeCheck]:
    """Check many codes."""
    return [check_code(conn, use) for use in uses]


@dataclass(frozen=True)
class MappingCheck:
    """Result for one reviewed source_to_concept_map row."""

    row: MappingRow
    target: Concept | None
    problems: tuple[str, ...]


def check_mapping(conn: Connection, row: MappingRow) -> MappingCheck:
    """The target exists, is valid, has the right vocabulary and domain, and is standard (or is
    one of our non-standard local concepts)."""
    target = get_concept(conn, row.target_concept_id)
    if target is None:
        return MappingCheck(row, None, ("TARGET NOT FOUND",))
    problems = []
    if target.invalid_reason is not None:
        problems.append(f"TARGET DEPRECATED ({target.invalid_reason})")
    if target.vocabulary_id != row.target_vocabulary_id:
        problems.append(
            f"VOCABULARY IS {target.vocabulary_id}, ROW SAYS {row.target_vocabulary_id}"
        )
    expected = SOURCE_VOCABULARIES.get(row.source_vocabulary_id, ())
    is_local = target.vocabulary_id == LOCAL_VOCABULARY and target.concept_id >= LOCAL_CONCEPT_MIN
    if not target.is_standard and not is_local:
        problems.append("TARGET NOT STANDARD")
    if target.domain_id not in expected:
        problems.append(f"WRONG DOMAIN ({target.domain_id}, expected {'/'.join(expected)})")
    return MappingCheck(row, target, tuple(problems))


def check_value_pairs(checks: list[MappingCheck]) -> list[str]:
    """Problems with "history of" pairs: every TXLENS_COND_VALUE row needs a
    TXLENS_CONDITION row with the same source code whose target is an Observation."""
    observations = {
        c.row.source_code: c.target.domain_id if c.target else None
        for c in checks
        if c.row.source_vocabulary_id == "TXLENS_CONDITION"
    }
    problems = []
    for c in checks:
        if c.row.source_vocabulary_id != "TXLENS_COND_VALUE":
            continue
        domain = observations.get(c.row.source_code)
        if domain != "Observation":
            found = (
                f"its TXLENS_CONDITION target is {domain}" if domain else "no TXLENS_CONDITION row"
            )
            problems.append(f"'{c.row.source_code}': value without an observation ({found})")
    return problems


@dataclass(frozen=True)
class LocalCheck:
    """Result for one TxLens local concept we write."""

    code: str
    expected_domain: str
    concept: Concept | None
    problems: tuple[str, ...]


def check_local_concept(conn: Connection, code: str, expected_domain: str) -> LocalCheck:
    """The local concept is loaded, in our id range, non-standard, valid, in the right domain."""
    concept = find_concept(conn, LOCAL_VOCABULARY, code)
    if concept is None:
        return LocalCheck(code, expected_domain, None, ("NOT FOUND",))
    problems = []
    if concept.concept_id < LOCAL_CONCEPT_MIN:
        problems.append(f"ID BELOW {LOCAL_CONCEPT_MIN:,}")
    if concept.is_standard:
        problems.append("MARKED STANDARD")
    if concept.invalid_reason is not None:
        problems.append(f"DEPRECATED ({concept.invalid_reason})")
    if concept.domain_id != expected_domain:
        problems.append(f"WRONG DOMAIN ({concept.domain_id}, expected {expected_domain})")
    return LocalCheck(code, expected_domain, concept, tuple(problems))


def format_local_report(results: list[LocalCheck]) -> str:
    """One line per local concept, then a summary."""
    lines = []
    for r in results:
        status = "; ".join(r.problems) or OK
        found = f"{r.concept.concept_id} {r.concept.concept_name}" if r.concept else "-"
        lines.append(f"[{status}] {LOCAL_VOCABULARY} {r.code} -> {found}")
    failed = sum(1 for r in results if r.problems)
    lines.append(f"\n{len(results) - failed} OK, {failed} with problems, {len(results)} checked.")
    return "\n".join(lines)


def check_drug_names(
    conn: Connection, names: Mapping[str, tuple[str, ...]], rxnorm: Mapping[str, str]
) -> list[tuple[str, str, str]]:
    """(drug, surface name, problem) for every surface name not resolving to its ingredient."""
    problems = []
    for drug, surface_names in names.items():
        for name in surface_names:
            found = [c.concept_code for c in ingredients_for_name(conn, name)]
            if found != [rxnorm[drug]]:
                problems.append((drug, name, f"resolves to {found or 'nothing'}"))
    return problems


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


def format_mapping_report(results: list[MappingCheck]) -> str:
    """One line per reviewed mapping row, then a summary."""
    lines = []
    for r in results:
        status = "; ".join(r.problems) or OK
        name = r.target.concept_name if r.target else "-"
        lines.append(
            f"[{status}] {r.row.source_vocabulary_id} '{r.row.source_code}' -> "
            f"{r.row.target_vocabulary_id} {r.row.target_concept_id} {name}"
        )
    failed = sum(1 for r in results if r.problems)
    lines.append(f"\n{len(results) - failed} OK, {failed} with problems, {len(results)} mappings.")
    return "\n".join(lines)
