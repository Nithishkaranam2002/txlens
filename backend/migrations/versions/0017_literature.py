"""Literature corpus for the copilot: open-access PubMed abstracts with full-text and vectors.

`lit.abstract` is shared by all tenants (public literature, like the vocabulary): no
tenant_id, no RLS; the app may only read it, the owner loads it (`make literature-index`).
Abstracts are stored exactly as published. Two indexes serve hybrid retrieval:
- a GIN index on an English `tsvector` of title + abstract (Postgres full-text search);
- an HNSW index (cosine) on a 768-dimension embedding. 768 fits both the default OpenAI model
  (asked for 768 dimensions) and the local nomic-embed-text model; `embedding_model` says which
  model made a row's vector, and search uses only vectors of the configured model.

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-12
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIMENSIONS = 768


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE SCHEMA lit")
    op.execute(
        f"""
        CREATE TABLE lit.abstract (
            pmid bigint PRIMARY KEY,
            title text NOT NULL,
            abstract text NOT NULL,
            journal text NOT NULL,
            year integer,
            doi text,
            pmcid text,
            topics text[] NOT NULL DEFAULT '{{}}',
            content_sha256 text NOT NULL,
            tsv tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('english', title), 'A') ||
                setweight(to_tsvector('english', abstract), 'B')) STORED,
            embedding vector({DIMENSIONS}),
            embedding_model text,
            loaded_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_abstract_tsv ON lit.abstract USING gin (tsv)")
    op.execute(
        "CREATE INDEX ix_abstract_embedding ON lit.abstract "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute("GRANT USAGE ON SCHEMA lit TO txlens_app")
    op.execute("GRANT SELECT ON lit.abstract TO txlens_app")


def downgrade() -> None:
    op.execute("DROP SCHEMA lit CASCADE")
    # The vector extension may be used by others in this database: it is kept.
