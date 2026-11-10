"""Render the pinned official OHDSI DDL for our database layout.

The upstream files put every table in one `@cdmDatabaseSchema`. We keep only the tables we
use, put clinical tables in schema `cdm` and vocabulary tables in schema `vocab`, drop the
`CLUSTER` statements, and keep only foreign keys between clinical tables (no foreign keys
into `vocab`, so the vocabulary can be reloaded on its own; see ADR 0005).
"""

import hashlib
import re
from dataclasses import dataclass
from functools import cache
from importlib.resources import files
from pathlib import Path

CDM_VERSION = "v5.4.3"
CDM_SCHEMA = "cdm"
VOCAB_SCHEMA = "vocab"
PLACEHOLDER = "@cdmDatabaseSchema"

CLINICAL_TABLES = (
    "person",
    "observation_period",
    "visit_occurrence",
    "condition_occurrence",
    "drug_exposure",
    "measurement",
    "procedure_occurrence",
    "observation",
    "death",
    "note",
    "note_nlp",
    "specimen",
    "cdm_source",
)
VOCAB_TABLES = (
    "concept",
    "concept_relationship",
    "concept_ancestor",
    "concept_synonym",
    "vocabulary",
    "domain",
    "concept_class",
    "relationship",
)
FILES = {
    "tables": "OMOPCDM_postgresql_5.4_ddl.sql",
    "primary_keys": "OMOPCDM_postgresql_5.4_primary_keys.sql",
    "indices": "OMOPCDM_postgresql_5.4_indices.sql",
    "constraints": "OMOPCDM_postgresql_5.4_constraints.sql",
}

TABLE_REF = re.compile(rf"{PLACEHOLDER}\.(\w+)")
CREATE_TABLE = re.compile(rf"CREATE TABLE {PLACEHOLDER}\.(\w+) \((.*)\)", re.DOTALL)
PRIMARY_KEY = re.compile(r"ADD CONSTRAINT (\w+) PRIMARY KEY \(([^)]*)\)")
FOREIGN_KEY = re.compile(r"FOREIGN KEY \((\w+)\) REFERENCES \S+ \((\w+)\)")


@dataclass(frozen=True)
class Column:
    """One column as written in the official DDL."""

    name: str
    data_type: str
    nullable: bool


@dataclass(frozen=True)
class Table:
    """One table: its columns and primary key (empty if upstream defines none)."""

    name: str
    schema: str
    columns: tuple[Column, ...]
    primary_key: tuple[str, ...]


@dataclass(frozen=True)
class ForeignKey:
    """A foreign key between two of our clinical tables."""

    name: str
    table: str
    column: str
    ref_table: str
    ref_column: str


def sql_dir(version: str = CDM_VERSION) -> Path:
    """Folder holding the pinned upstream SQL files for one CDM release."""
    return Path(str(files("txlens_omop") / "sql" / version))


def read_sql(kind: str, version: str = CDM_VERSION) -> str:
    """Contents of one pinned upstream file."""
    return (sql_dir(version) / FILES[kind]).read_text(encoding="utf-8")


def statements(sql: str) -> list[str]:
    """Split a file into statements, dropping `--` comment lines."""
    code = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in code.split(";") if s.strip()]


def schema_for(table: str) -> str | None:
    """Target schema for a table, or None if we do not create it."""
    if table in CLINICAL_TABLES:
        return CDM_SCHEMA
    if table in VOCAB_TABLES:
        return VOCAB_SCHEMA
    return None


def referenced_tables(statement: str) -> list[str]:
    """Lower-case names of all tables a statement refers to, in order."""
    return [name.lower() for name in TABLE_REF.findall(statement)]


def qualify(statement: str) -> str:
    """Replace every `@cdmDatabaseSchema.<table>` with `<schema>.<table>`."""

    def target(match: re.Match[str]) -> str:
        table = match.group(1).lower()
        schema = schema_for(table)
        if schema is None:
            raise ValueError(f"statement refers to a table we do not create: {table}")
        return f"{schema}.{table}"

    return TABLE_REF.sub(target, statement)


def is_ours(statement: str) -> bool:
    """True if the statement's main table is one we create."""
    tables = referenced_tables(statement)
    return bool(tables) and schema_for(tables[0]) is not None


def is_kept_foreign_key(statement: str) -> bool:
    """Keep a foreign key only if both ends are clinical tables we create."""
    tables = referenced_tables(statement)
    return len(tables) == 2 and all(t in CLINICAL_TABLES for t in tables)


def parse_column(line: str) -> Column:
    """Parse `name type NULL|NOT NULL` (reserved names such as "offset" are quoted upstream)."""
    parts = line.split()
    nullable = parts[-1].upper() == "NULL" and parts[-2].upper() != "NOT"
    return Column(parts[0].strip('"').lower(), parts[1].lower(), nullable)


@cache
def tables(version: str = CDM_VERSION) -> dict[str, Table]:
    """Our tables as defined upstream, keyed by name."""
    keys: dict[str, tuple[str, ...]] = {}
    for stmt in statements(read_sql("primary_keys", version)):
        match = PRIMARY_KEY.search(stmt)
        if match and is_ours(stmt):
            keys[referenced_tables(stmt)[0]] = tuple(c.strip() for c in match.group(2).split(","))
    result = {}
    for stmt in statements(read_sql("tables", version)):
        match = CREATE_TABLE.match(stmt)
        if match is None or schema_for(match.group(1).lower()) is None:
            continue
        name = match.group(1).lower()
        columns = tuple(parse_column(c) for c in match.group(2).split(",") if c.strip())
        result[name] = Table(name, schema_for(name) or "", columns, keys.get(name, ()))
    return result


def foreign_keys(version: str = CDM_VERSION) -> list[ForeignKey]:
    """Upstream foreign keys we keep (clinical table to clinical table)."""
    result = []
    for stmt in statements(read_sql("constraints", version)):
        match = FOREIGN_KEY.search(stmt)
        if match and is_kept_foreign_key(stmt):
            table, ref_table = referenced_tables(stmt)
            name = stmt.split("ADD CONSTRAINT ")[1].split()[0]
            result.append(
                ForeignKey(name, table, match.group(1), ref_table, match.group(2).lower())
            )
    return result


def render_tables(version: str = CDM_VERSION) -> list[str]:
    """CREATE TABLE statements for our tables, schema-qualified."""
    return [qualify(s) for s in statements(read_sql("tables", version)) if is_ours(s)]


def render_primary_keys(version: str = CDM_VERSION) -> list[str]:
    """Primary-key constraints for our tables."""
    return [qualify(s) for s in statements(read_sql("primary_keys", version)) if is_ours(s)]


def render_foreign_keys(version: str = CDM_VERSION) -> list[str]:
    """Foreign keys between clinical tables only (none into vocab, none to absent tables)."""
    return [
        qualify(s) for s in statements(read_sql("constraints", version)) if is_kept_foreign_key(s)
    ]


def render_indices(version: str = CDM_VERSION) -> list[str]:
    """Upstream indexes for our tables; CLUSTER statements are dropped."""
    return [
        qualify(s)
        for s in statements(read_sql("indices", version))
        if s.upper().startswith("CREATE INDEX") and is_ours(s)
    ]


def checksums() -> dict[str, str]:
    """SHA-256 of each pinned file, as listed in SHA256SUMS."""
    lines = (sql_dir() / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    return {name: digest for digest, name in (line.split() for line in lines if line)}


def sha256(name: str) -> str:
    """Actual SHA-256 of one pinned file."""
    return hashlib.sha256((sql_dir() / name).read_bytes()).hexdigest()
