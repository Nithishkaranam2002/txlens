"""Indexes: the official OHDSI ones for our tables, plus tenant and lookup indexes.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-10
"""

from collections.abc import Sequence

from alembic import op

from txlens_omop import ddl

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CDM_RELEASE = "v5.4.3"
EXTRA_INDEXES = {
    # The surveillance engine reads one patient's marker time series.
    "ix_measurement_person_concept_date": (
        "cdm.measurement (person_id, measurement_concept_id, measurement_date)"
    ),
    # Source-code lookups: (vocabulary, code) -> concept.
    "ix_concept_vocabulary_code": "vocab.concept (vocabulary_id, concept_code)",
    # "Maps to" lookups from a source concept.
    "ix_concept_relationship_id_1_rel": (
        "vocab.concept_relationship (concept_id_1, relationship_id)"
    ),
}


def tenant_index_columns(table: str) -> str:
    """Columns for the per-tenant index: tenant plus the table's usual lookup key."""
    columns = {c.name for c in ddl.tables(CDM_RELEASE)[table].columns}
    if "person_id" in columns:
        return "tenant_id, person_id"
    if table == "note_nlp":
        return "tenant_id, note_id"
    return "tenant_id"


def extra_indexes() -> dict[str, str]:
    """Our indexes on top of the official ones."""
    tenant = {f"ix_{t}_tenant": f"cdm.{t} ({tenant_index_columns(t)})" for t in ddl.CLINICAL_TABLES}
    return tenant | EXTRA_INDEXES


def upgrade() -> None:
    for statement in ddl.render_indices(CDM_RELEASE):
        op.execute(statement)
    for name, target in extra_indexes().items():
        op.execute(f"CREATE INDEX {name} ON {target}")


def downgrade() -> None:
    for statement in ddl.render_indices(CDM_RELEASE):
        name, schema = statement.split()[2], statement.split(" ON ")[1].split(".")[0]
        op.execute(f"DROP INDEX {schema}.{name}")
    for name, target in extra_indexes().items():
        op.execute(f"DROP INDEX {target.split('.')[0]}.{name}")
