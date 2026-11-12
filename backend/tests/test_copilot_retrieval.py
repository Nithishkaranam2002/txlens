"""Literature retrieval: RRF, verbatim snippets, the pgvector store, incremental indexing."""

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from literature_testutils import CORPUS, INJECTED_PMID, FakeEmbedder
from sqlalchemy import Engine, text
from sqlalchemy.exc import ProgrammingError

from txlens_backend.copilot import literature
from txlens_backend.copilot.retrieval import (
    Document,
    PgVectorStore,
    fuse,
    hybrid_search,
    snippet,
)
from txlens_backend.documents.budget import Budget, BudgetExceededError


def test_rrf_rewards_agreement_between_rankings() -> None:
    fused = fuse([[1, 2, 3], [3, 1, 4]])
    assert [pmid for pmid, _ in fused] == [1, 3, 2, 4]
    assert fused[0][1] == pytest.approx(1 / 61 + 1 / 62)


def test_rrf_breaks_ties_by_pmid() -> None:
    assert [p for p, _ in fuse([[5], [4]])] == [4, 5]


def test_snippets_are_verbatim_sentences_in_their_order() -> None:
    abstract = CORPUS[0].abstract
    found = snippet(abstract, "what dd-cfDNA fraction identified antibody-mediated rejection")
    parts = found.split(" … ")
    assert all(part in abstract for part in parts)
    assert parts == sorted(parts, key=abstract.index)
    assert "1% or more identified antibody-mediated rejection" in found


def test_documents_round_trip_through_jsonl(tmp_path: Path) -> None:
    source = tmp_path / "abstracts.jsonl"
    source.write_text("\n".join(json.dumps(d.model_dump()) for d in CORPUS) + "\n")
    assert literature.read(source) == CORPUS


@pytest.fixture
def indexed(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeEmbedder]:
    """The test corpus indexed (committed) with the fake embedder; removed afterwards."""
    monkeypatch.setattr(literature, "max_cost", lambda model, texts: 0.001 * len(texts))
    embedder = FakeEmbedder()
    budget = Budget(engine, "test-literature-index", 1.0)
    budget.open()
    literature.index(engine, CORPUS, embedder, budget)
    yield embedder
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM lit.abstract"))
        conn.execute(text("DELETE FROM app.llm_budget WHERE batch_id LIKE 'test-literature%'"))


@pytest.mark.db
def test_text_search_finds_exact_terms(engine: Engine, indexed: FakeEmbedder) -> None:
    with engine.connect() as conn:
        assert PgVectorStore(conn).search_text("C4d staining", 5)[0] == 90000002
        assert PgVectorStore(conn).search_text("", 5) == []


@pytest.mark.db
def test_hybrid_search_ranks_the_relevant_abstract_first(
    engine: Engine, indexed: FakeEmbedder
) -> None:
    with engine.connect() as conn:
        passages, cost = hybrid_search(
            PgVectorStore(conn), indexed, "BK polyomavirus viremia in kidney recipients", k=3
        )
    assert passages[0].pmid == 90000003
    assert all(part in passages[0].abstract for part in passages[0].snippet.split(" … "))
    assert cost > 0 and len(passages) == 3


@pytest.mark.db
def test_vector_search_uses_only_the_configured_model(
    engine: Engine, indexed: FakeEmbedder
) -> None:
    with engine.connect() as conn:
        store = PgVectorStore(conn)
        vector = FakeEmbedder.vector("donor-specific antibodies")
        assert store.search_vector(vector, indexed.model, 3)[0] == 90000004
        assert store.search_vector(vector, "another/model", 3) == []


@pytest.mark.db
def test_reindexing_embeds_only_what_changed(
    engine: Engine, indexed: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = Budget(engine, "test-literature-index", 1.0)
    assert literature.index(engine, CORPUS, indexed, budget).embedded == 0
    changed = CORPUS[0].model_copy(update={"abstract": CORPUS[0].abstract + " Updated."})
    report = literature.index(engine, [changed, *CORPUS[1:]], indexed, budget)
    assert report.embedded == 1 and indexed.calls[-1][0].endswith("Updated.")


@pytest.mark.db
def test_indexing_stops_at_the_budget(engine: Engine, indexed: FakeEmbedder) -> None:
    budget = Budget(engine, "test-literature-index-small", 0.0005)
    budget.open()
    extra = Document(pmid=90000010, title="t", abstract="a", journal="j")
    with pytest.raises(BudgetExceededError):
        literature.index(engine, [extra], indexed, budget)


@pytest.mark.db
def test_the_app_role_may_read_but_not_change_the_corpus(
    app_engine: Engine, indexed: FakeEmbedder
) -> None:
    with app_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM lit.abstract")).scalar_one() == len(CORPUS)
        with pytest.raises(ProgrammingError, match="permission denied"):
            conn.execute(text(f"DELETE FROM lit.abstract WHERE pmid = {INJECTED_PMID}"))


@pytest.mark.db
def test_abstracts_are_stored_exactly_as_given(engine: Engine, indexed: FakeEmbedder) -> None:
    with engine.connect() as conn:
        docs = PgVectorStore(conn).get([d.pmid for d in CORPUS])
    assert [docs[d.pmid] for d in CORPUS] == CORPUS
