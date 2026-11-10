"""Extract a small vocabulary subset from an Athena download, for fast local dev.

Kept concepts: the seed codes, the standard concepts they "Map to", all ancestors and
descendants of those (so hierarchy searches still work), and every concept of the small
support vocabularies (units, types, visits, demographics, metadata). The output has the same
file format as Athena, so the normal loader reads it.

The output folder (`omop/vocab_subset/`) is git-ignored: it is still licensed content.
"""

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from txlens_omop.athena import (
    CONCEPT_ID_COLUMNS,
    SUPPORT_VOCABULARIES,
    TABLE_FILES,
    VOCABULARY_ID_COLUMN,
    AthenaSource,
    links_kept,
)

log = logging.getLogger(__name__)
MAPS_TO = b"Maps to"
CONCEPT_CODE_COLUMN = 6


@dataclass
class SubsetReport:
    """What the subset contains."""

    seeds_found: int = 0
    seeds_missing: list[tuple[str, str]] = field(default_factory=list)
    concepts: int = 0
    rows: dict[str, int] = field(default_factory=dict)


def scan_concepts(
    source: AthenaSource, seeds: set[tuple[bytes, bytes]]
) -> tuple[set[bytes], set[bytes], set[tuple[bytes, bytes]]]:
    """Ids of seed concepts, ids of support-vocabulary concepts, and the seeds found."""
    support = {v.encode() for v in SUPPORT_VOCABULARIES}
    seed_ids: set[bytes] = set()
    support_ids: set[bytes] = set()
    found: set[tuple[bytes, bytes]] = set()
    with source.rows("concept") as (_, lines):
        for line in lines:
            parts = line.split(b"\t", CONCEPT_CODE_COLUMN + 1)
            key = (parts[VOCABULARY_ID_COLUMN], parts[CONCEPT_CODE_COLUMN])
            if key in seeds:
                seed_ids.add(parts[0])
                found.add(key)
            elif parts[VOCABULARY_ID_COLUMN] in support:
                support_ids.add(parts[0])
    return seed_ids, support_ids, found


def mapped_targets(source: AthenaSource, ids: set[bytes]) -> set[bytes]:
    """Concepts that `ids` "Map to" (their standard equivalents)."""
    targets: set[bytes] = set()
    with source.rows("concept_relationship") as (_, lines):
        for line in lines:
            first, second, relationship, _ = line.split(b"\t", 3)
            if relationship == MAPS_TO and first in ids:
                targets.add(second)
    return targets


def hierarchy(source: AthenaSource, ids: set[bytes]) -> set[bytes]:
    """All ancestors and descendants of `ids`."""
    related: set[bytes] = set()
    with source.rows("concept_ancestor") as (_, lines):
        for line in lines:
            ancestor, descendant, _ = line.split(b"\t", 2)
            if descendant in ids:
                related.add(ancestor)
            if ancestor in ids:
                related.add(descendant)
    return related


def write_table(
    source: AthenaSource, table: str, out_dir: Path, keep: Callable[[bytes], bool]
) -> int:
    """Copy one table to `out_dir`, keeping lines for which `keep(line)` is true."""
    count = 0
    with source.rows(table) as (header, lines), (out_dir / TABLE_FILES[table]).open("wb") as out:
        out.write(("\t".join(header) + "\n").encode())
        for line in lines:
            if keep(line):
                out.write(line)
                count += 1
    return count


def extract_subset(
    source: AthenaSource, seeds: Iterable[tuple[str, str]], out_dir: Path
) -> SubsetReport:
    """Write a subset of `source` around the (vocabulary_id, concept_code) seeds."""
    wanted = {(v.encode(), c.encode()) for v, c in seeds}
    report = SubsetReport()
    seed_ids, support_ids, found = scan_concepts(source, wanted)
    report.seeds_found = len(found)
    report.seeds_missing = sorted((v.decode(), c.decode()) for v, c in wanted - found)
    core = seed_ids | mapped_targets(source, seed_ids)
    kept = core | hierarchy(source, core) | support_ids
    report.concepts = len(kept)
    log.info("Subset: %d seeds found, %d concepts kept", report.seeds_found, report.concepts)

    out_dir.mkdir(parents=True, exist_ok=True)
    for table in ("vocabulary", "domain", "concept_class", "relationship"):
        report.rows[table] = write_table(source, table, out_dir, lambda _: True)
    report.rows["concept"] = write_table(
        source, "concept", out_dir, lambda line: line.split(b"\t", 1)[0] in kept
    )
    for table, columns in CONCEPT_ID_COLUMNS.items():
        report.rows[table] = write_table(source, table, out_dir, linked_to(columns, kept))
    return report


def linked_to(columns: tuple[int, ...], kept: set[bytes]) -> Callable[[bytes], bool]:
    """Filter for rows whose linked concept ids were all kept."""
    return lambda line: links_kept(line, columns, kept)
