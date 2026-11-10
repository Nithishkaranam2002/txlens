"""Bulk-load an Athena download into the `vocab` schema with COPY.

Everything happens in one transaction: if anything fails, the previous vocabulary stays.
Secondary indexes are dropped before loading and rebuilt afterwards (much faster than updating
them row by row). Memory use is the set of kept concept ids; every file is streamed.
"""

import logging
import time
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import psycopg
from psycopg import sql

from txlens_omop import ddl
from txlens_omop.athena import (
    CONCEPT_ID_COLUMNS,
    REQUIRED_VOCABULARIES,
    TABLE_FILES,
    VOCABULARY_ID_COLUMN,
    AthenaSource,
    links_kept,
    wanted_vocabularies,
)
from txlens_omop.mappings import LOCAL_CONCEPT_FILE, STCM_FILE, mappings_dir

log = logging.getLogger(__name__)
Connection = psycopg.Connection[tuple[object, ...]]
Cursor = psycopg.Cursor[tuple[object, ...]]
BATCH_BYTES = 8 * 1024 * 1024
# Small reference tables are loaded whole; concept-linked tables are filtered.
WHOLE_TABLES = ("vocabulary", "domain", "concept_class", "relationship")
LINK_TABLES = ("concept_relationship", "concept_ancestor", "concept_synonym")


class MissingVocabularyError(RuntimeError):
    """A required vocabulary is not in the download."""


@dataclass
class LoadReport:
    """What was loaded."""

    rows: dict[str, int] = field(default_factory=dict)
    concepts_by_vocabulary: Counter[str] = field(default_factory=Counter)
    missing_vocabularies: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def copy_statement(table: str, columns: list[str]) -> sql.Composed:
    """COPY for raw Athena lines: tabs, no quoting, empty = NULL except NOT NULL text columns."""
    spec = {c.name: c for c in ddl.tables()[table].columns}
    unknown = [c for c in columns if c not in spec]
    if unknown:
        raise ValueError(f"{TABLE_FILES[table]} has columns not in the DDL: {unknown}")
    not_null_text = [
        c for c in columns if not spec[c].nullable and spec[c].data_type.startswith("varchar")
    ]
    force = (
        sql.SQL(", FORCE_NOT_NULL ({})").format(
            sql.SQL(", ").join(map(sql.Identifier, not_null_text))
        )
        if not_null_text
        else sql.SQL("")
    )
    return sql.SQL(
        "COPY vocab.{} ({}) FROM STDIN WITH (FORMAT csv, DELIMITER E'\\t', QUOTE E'\\b', NULL ''{})"
    ).format(sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns)), force)


def copy_lines(cur: Cursor, statement: sql.Composed, lines: Iterable[bytes]) -> int:
    """Stream lines into COPY in large batches; return the row count."""
    count, size, batch = 0, 0, []
    with cur.copy(statement) as copy:
        for line in lines:
            batch.append(line)
            size += len(line)
            count += 1
            if size >= BATCH_BYTES:
                copy.write(b"".join(batch))
                batch, size = [], 0
        if batch:
            copy.write(b"".join(batch))
    return count


def drop_secondary_indexes(cur: Cursor) -> list[str]:
    """Drop vocab indexes that do not back a constraint; return their definitions."""
    cur.execute(
        "SELECT i.indexname, i.indexdef FROM pg_indexes i "
        "WHERE i.schemaname = 'vocab' AND NOT EXISTS (SELECT 1 FROM pg_constraint c "
        "  WHERE c.conindid = format('vocab.%I', i.indexname)::regclass)"
    )
    rows = cur.fetchall()
    for name, _ in rows:
        cur.execute(sql.SQL("DROP INDEX vocab.{}").format(sql.Identifier(str(name))))
    return [str(definition) for _, definition in rows]


def load_concepts(
    cur: Cursor, source: AthenaSource, wanted: frozenset[str], report: LoadReport
) -> set[bytes]:
    """Load concepts of the wanted vocabularies; return their ids (as raw bytes)."""
    kept: set[bytes] = set()
    wanted_bytes = {v.encode() for v in wanted}

    def keep(lines: Iterable[bytes]) -> Iterator[bytes]:
        for line in lines:
            parts = line.split(b"\t", VOCABULARY_ID_COLUMN + 1)
            vocabulary = parts[VOCABULARY_ID_COLUMN]
            if vocabulary in wanted_bytes:
                kept.add(parts[0])
                report.concepts_by_vocabulary[vocabulary.decode()] += 1
                yield line

    with source.rows("concept") as (header, lines):
        report.rows["concept"] = copy_lines(cur, copy_statement("concept", header), keep(lines))
    return kept


def check_vocabularies(wanted: frozenset[str], report: LoadReport) -> None:
    """Fail on missing required vocabularies; warn about missing optional ones."""
    report.missing_vocabularies = sorted(wanted - report.concepts_by_vocabulary.keys())
    required = sorted(REQUIRED_VOCABULARIES & set(report.missing_vocabularies))
    if required:
        raise MissingVocabularyError(f"Required vocabularies missing from the download: {required}")
    for vocabulary in report.missing_vocabularies:
        message = f"Vocabulary '{vocabulary}' is not in the download; skipped."
        if vocabulary == "UCUM":
            message += " Units will stay unit_concept_id = 0 until UCUM is loaded."
        report.warnings.append(message)
        log.warning(message)


def load_local_mappings(cur: Cursor, report: LoadReport) -> None:
    """Add our reviewed local concepts and source_to_concept_map rows (see mappings.py)."""
    folder = AthenaSource(mappings_dir())
    with folder.open(LOCAL_CONCEPT_FILE) as handle:
        header = handle.readline().decode("utf-8").rstrip("\r\n").split("\t")
        report.rows["local_concept"] = copy_lines(cur, copy_statement("concept", header), handle)
    with folder.open(STCM_FILE) as handle:
        header = handle.readline().decode("utf-8").rstrip("\r\n").split("\t")
        statement = copy_statement("source_to_concept_map", header)
        report.rows["source_to_concept_map"] = copy_lines(cur, statement, handle)


def load_vocabulary(
    conn: Connection,
    source: AthenaSource,
    requested: frozenset[str] | None = None,
) -> LoadReport:
    """Replace the vocab schema contents with the wanted vocabularies from `source`."""
    wanted = wanted_vocabularies(requested) if requested is not None else wanted_vocabularies()
    report = LoadReport()
    with conn.transaction(), conn.cursor() as cur:
        tables = sql.SQL(", ").join(
            sql.SQL("vocab.{}").format(sql.Identifier(t))
            for t in (*TABLE_FILES, *ddl.MAPPING_TABLES)
        )
        cur.execute(sql.SQL("TRUNCATE {}").format(tables))
        index_definitions = drop_secondary_indexes(cur)
        for table in WHOLE_TABLES:
            with source.rows(table) as (header, lines):
                report.rows[table] = copy_lines(cur, copy_statement(table, header), lines)
        started = time.monotonic()
        kept = load_concepts(cur, source, wanted, report)
        log.info(
            "concept: %s rows in %.0fs", f"{report.rows['concept']:,}", time.monotonic() - started
        )
        check_vocabularies(wanted, report)
        for table in LINK_TABLES:
            started = time.monotonic()
            columns = CONCEPT_ID_COLUMNS[table]
            with source.rows(table) as (header, lines):
                filtered = (line for line in lines if links_kept(line, columns, kept))
                report.rows[table] = copy_lines(cur, copy_statement(table, header), filtered)
            log.info(
                "%s: %s rows in %.0fs", table, f"{report.rows[table]:,}", time.monotonic() - started
            )
        load_local_mappings(cur, report)
        for definition in index_definitions:
            cur.execute(definition)
        log.info("Rebuilt %d indexes", len(index_definitions))
    with conn.cursor() as cur:
        cur.execute("ANALYZE vocab.concept, vocab.concept_relationship, vocab.concept_ancestor")
    return report


def vocab_table_sizes(conn: Connection) -> dict[str, int]:
    """Total on-disk bytes (data + indexes) of each vocab table."""
    rows = conn.execute(
        "SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'vocab' AND c.relkind = 'r' ORDER BY 2 DESC"
    ).fetchall()
    return {str(name): int(str(size)) for name, size in rows}
