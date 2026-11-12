"""Hybrid literature retrieval: vector search + full-text search, merged by Reciprocal Rank Fusion.

- Vector search finds abstracts that mean the same as the question in other words.
- Full-text search (Postgres tsvector, any query term may match) finds exact terms such as
  "Banff", "C4d" or "BKPyV" that embeddings blur.
- Reciprocal Rank Fusion: each abstract scores sum(1 / (k + rank)) over the two rankings
  (k = 60, the usual constant), so agreeing lists win without comparing raw scores.

Storage is behind the `VectorStore` protocol, so pgvector can be replaced (e.g. by Milvus or
Databricks Vector Search) without touching the copilot. Snippets are whole sentences copied
verbatim from the abstract, never rewritten.
"""

import re
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy import Connection, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.sql.expression import bindparam
from sqlalchemy.types import Text

from txlens_backend.copilot.embeddings import Embedder

RRF_K = 60
DEPTH = 50  # candidates taken from each ranking before fusion
SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])|\n+")
WORD = re.compile(r"[a-z0-9][a-z0-9-]+")
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for", "from", "has", "have",
    "how", "in", "is", "it", "of", "on", "or", "that", "the", "this", "to", "was", "what",
    "when", "which", "who", "why", "with", "after", "about", "vs", "versus",
})  # fmt: skip


class Document(BaseModel):
    """One abstract, exactly as published."""

    pmid: int
    title: str
    abstract: str
    journal: str
    year: int | None = None
    doi: str | None = None
    pmcid: str | None = None
    topics: list[str] = []


class Passage(BaseModel):
    """A retrieved abstract: the document, a verbatim snippet and its fused score."""

    pmid: int
    title: str
    journal: str
    year: int | None
    abstract: str
    snippet: str
    score: float


class VectorStore(Protocol):
    """Where abstracts and their vectors live."""

    def add(self, docs: Sequence[Document], vectors: Sequence[Sequence[float]],
            model: str) -> None:  # fmt: skip
        """Store (or replace) documents with their vectors from `model`."""
        ...

    def search_vector(self, vector: Sequence[float], model: str, k: int) -> list[int]:
        """PMIDs nearest to the vector (cosine), among vectors made by `model`."""
        ...

    def search_text(self, query: str, k: int) -> list[int]:
        """PMIDs best matching the query's words (any of them)."""
        ...

    def get(self, pmids: Sequence[int]) -> dict[int, Document]:
        """Documents by PMID."""
        ...


def literal(vector: Sequence[float]) -> str:
    """pgvector's text form: '[0.1,0.2,...]'."""
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"


class PgVectorStore:
    """lit.abstract in Postgres: pgvector HNSW + tsvector GIN."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def add(self, docs: Sequence[Document], vectors: Sequence[Sequence[float]],
            model: str) -> None:  # fmt: skip
        """Upsert documents with their vectors (owner only: the app may just read)."""
        statement = text(
            "INSERT INTO lit.abstract (pmid, title, abstract, journal, year, doi, pmcid, topics, "
            "content_sha256, embedding, embedding_model) VALUES (:pmid, :title, :abstract, "
            ":journal, :year, :doi, :pmcid, :topics, encode(sha256(convert_to(:title || "
            "chr(10) || :abstract, 'UTF8')), 'hex'), CAST(:vector AS vector), :model) "
            "ON CONFLICT (pmid) DO UPDATE SET title = excluded.title, abstract = "
            "excluded.abstract, journal = excluded.journal, year = excluded.year, doi = "
            "excluded.doi, pmcid = excluded.pmcid, topics = excluded.topics, content_sha256 = "
            "excluded.content_sha256, embedding = excluded.embedding, embedding_model = "
            "excluded.embedding_model, loaded_at = now()"
        ).bindparams(bindparam("topics", type_=ARRAY(Text)))
        self.conn.execute(statement, [
            d.model_dump() | {"vector": literal(v), "model": model}
            for d, v in zip(docs, vectors, strict=True)
        ])  # fmt: skip

    def search_vector(self, vector: Sequence[float], model: str, k: int) -> list[int]:
        """Nearest by cosine distance."""
        rows = self.conn.execute(
            text(
                "SELECT pmid FROM lit.abstract WHERE embedding_model = :m "
                "ORDER BY embedding <=> CAST(:v AS vector), pmid LIMIT :k"
            ),
            {"m": model, "v": literal(vector), "k": k},
        ).scalars()
        return [int(p) for p in rows]

    def search_text(self, query: str, k: int) -> list[int]:
        """ts_rank_cd over title (weight A) and abstract (B); the query's words are OR-ed, so a
        question need not contain every word of an abstract to find it."""
        rows = self.conn.execute(
            text(
                "WITH q AS (SELECT NULLIF(replace(plainto_tsquery('english', :q)::text, '&', "
                "'|'), '')::tsquery AS query) "
                "SELECT pmid FROM lit.abstract, q WHERE q.query IS NOT NULL AND tsv @@ q.query "
                "ORDER BY ts_rank_cd(tsv, q.query) DESC, pmid LIMIT :k"
            ),
            {"q": query, "k": k},
        ).scalars()
        return [int(p) for p in rows]

    def get(self, pmids: Sequence[int]) -> dict[int, Document]:
        """Documents by PMID."""
        rows = self.conn.execute(
            text(
                "SELECT pmid, title, abstract, journal, year, doi, pmcid, topics "
                "FROM lit.abstract WHERE pmid = ANY(:p)"
            ),
            {"p": list(pmids)},
        ).mappings()
        return {int(r["pmid"]): Document(**r) for r in rows}


def fuse(rankings: Sequence[Sequence[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal Rank Fusion: (pmid, score), best first (ties by PMID, for stable output)."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, pmid in enumerate(ranking, start=1):
            scores[pmid] = scores.get(pmid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def words(value: str) -> set[str]:
    """Lower-case content words."""
    return {w for w in WORD.findall(value.lower()) if w not in STOPWORDS}


def snippet(abstract: str, query: str, sentences: int = 2) -> str:
    """The abstract's sentences sharing most words with the query, verbatim, in their order."""
    parts = [s.strip() for s in SENTENCE.split(abstract) if s.strip()]
    wanted = words(query)
    ranked = sorted(range(len(parts)), key=lambda i: (-len(words(parts[i]) & wanted), i))
    return " … ".join(parts[i] for i in sorted(ranked[:sentences]))


def hybrid_search(
    store: VectorStore, embedder: Embedder, query: str, k: int = 8
) -> tuple[list[Passage], float]:
    """The top k abstracts for a question, and the cost of embedding it."""
    vectors, cost = embedder.embed([query])
    fused = fuse([store.search_vector(vectors[0], embedder.model, DEPTH),
                  store.search_text(query, DEPTH)])[:k]  # fmt: skip
    docs = store.get([pmid for pmid, _ in fused])
    passages = [
        Passage(pmid=pmid, title=docs[pmid].title, journal=docs[pmid].journal,
                year=docs[pmid].year, abstract=docs[pmid].abstract,
                snippet=snippet(docs[pmid].abstract, query), score=score)
        for pmid, score in fused if pmid in docs
    ]  # fmt: skip
    return passages, cost
