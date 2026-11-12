"""`txlens-literature index`: load downloaded abstracts into lit.abstract with embeddings.

Reads data/literature/abstracts.jsonl (from `make literature-download`). Only abstracts that
are new, changed, or embedded by another model are embedded again, so a re-run costs nothing.
Spending is capped by a Budget (batch "literature-index:<model>"); each batch of 100 is
committed on its own, so an interrupted run resumes where it stopped. Runs as the database
owner: the app role may only read the corpus.
"""

import argparse
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, create_engine, text

from txlens_backend.config import get_settings
from txlens_backend.copilot.embeddings import BATCH, Embedder, make_embedder, max_cost
from txlens_backend.copilot.retrieval import Document, PgVectorStore
from txlens_backend.documents.budget import Budget


@dataclass(frozen=True)
class IndexReport:
    """What one run did."""

    read: int
    embedded: int
    cost_usd: float


def content_sha256(doc: Document) -> str:
    """The same hash the database stores (title, newline, abstract)."""
    return hashlib.sha256(f"{doc.title}\n{doc.abstract}".encode()).hexdigest()


def embedding_text(doc: Document) -> str:
    """What is embedded: title and abstract."""
    return f"{doc.title}\n{doc.abstract}"


def read(path: Path) -> list[Document]:
    """Abstracts from a JSON-lines file."""
    with path.open() as lines:
        return [Document(**json.loads(line)) for line in lines if line.strip()]


def stale(engine: Engine, docs: Sequence[Document], model: str) -> list[Document]:
    """Documents missing from the index, changed, or embedded by another model."""
    with engine.connect() as conn:
        known: dict[int, str] = dict(conn.execute(
            text("SELECT pmid, content_sha256 FROM lit.abstract WHERE embedding_model = :m"),
            {"m": model},
        ).tuples().all())  # fmt: skip
    return [d for d in docs if known.get(d.pmid) != content_sha256(d)]


def index(engine: Engine, docs: Sequence[Document], embedder: Embedder,
          budget: Budget) -> IndexReport:  # fmt: skip
    """Embed and store what is stale, batch by batch, within the budget."""
    todo = stale(engine, docs, embedder.model)
    spent = 0.0
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        texts = [embedding_text(d) for d in batch]
        reserved, cost = max_cost(embedder.model, texts), 0.0
        budget.reserve(reserved)
        try:
            vectors, cost = embedder.embed(texts)
        finally:
            budget.settle(reserved, cost)
        spent += cost
        with engine.begin() as conn:
            PgVectorStore(conn).add(batch, vectors, embedder.model)
    return IndexReport(read=len(docs), embedded=len(todo), cost_usd=spent)


def main(argv: list[str] | None = None) -> None:
    """`txlens-literature index [--source FILE] [--max-cost USD]`."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("index", help="embed and load the downloaded abstracts")
    run.add_argument("--source", type=Path, default=Path("data/literature/abstracts.jsonl"))
    run.add_argument("--max-cost", type=float, default=0.10, help="dollars (default 0.10)")
    args = parser.parse_args(argv)
    settings = get_settings()
    engine = create_engine(settings.database_url.get_secret_value())
    embedder = make_embedder(settings)
    cap = min(settings.llm_budget_usd, args.max_cost)
    budget = Budget(engine, f"literature-index:{embedder.model}", cap)
    budget.open()
    report = index(engine, read(args.source), embedder, budget)
    print(f"{report.read} abstracts read, {report.embedded} embedded, ${report.cost_usd:.4f}")
