"""Budget cap, answer cache and the guarded extraction pass, against a real database."""

import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

import pytest
from document_testutils import FakeCompletion, finding
from sqlalchemy import Engine

from txlens_backend.db import tenant_transaction
from txlens_backend.documents import cache, llm
from txlens_backend.documents.budget import Budget, BudgetExceededError
from txlens_backend.documents.passes import run_pass
from txlens_backend.documents.phi import MinimizedText

pytestmark = pytest.mark.db
SENT = MinimizedText(((1, "ACR grade (ISHLT 2004): 2R"), (2, "pAMR grade: pAMR 0")), ())
ANSWER = {
    "biopsy_date": finding(None),
    "acr_grade": finding("2R", 1, "2R"),
    "pamr_grade": finding("pAMR 0", 2, "pAMR 0"),
}


def new_budget(engine: Engine, cap: float) -> Budget:
    budget = Budget(engine, f"test-{uuid.uuid4()}", cap)
    budget.open()
    return budget


def test_reservations_stop_at_the_cap(documents_engine: Engine) -> None:
    budget = new_budget(documents_engine, 1.0)
    budget.reserve(0.6)
    with pytest.raises(BudgetExceededError, match=r"\$0.0000 spent of \$1.00"):
        budget.reserve(0.5)
    budget.settle(0.6, 0.2)
    budget.reserve(0.5)  # the unused part of the first reservation came back
    assert budget.totals() == (Decimal("0.2"), Decimal("1"))


def test_concurrent_reservations_never_pass_the_cap(documents_engine: Engine) -> None:
    budget = new_budget(documents_engine, 1.0)

    def try_reserve(_: int) -> bool:
        try:
            budget.reserve(0.1)
        except BudgetExceededError:
            return False
        return True

    with ThreadPoolExecutor(10) as pool:
        granted = sum(pool.map(try_reserve, range(50)))
    assert granted == 10


def test_reopening_a_batch_keeps_its_spending(documents_engine: Engine) -> None:
    budget = new_budget(documents_engine, 1.0)
    budget.reserve(0.3)
    budget.settle(0.3, 0.3)
    Budget(documents_engine, budget.batch_id, 99.0).open()
    assert budget.totals() == (Decimal("0.3"), Decimal("1"))


def test_cache_is_isolated_per_tenant(documents_engine: Engine) -> None:
    key = cache.cache_key("f" * 64, "v1", "m", "a", f"text {uuid.uuid4()}")
    answer = cache.CachedAnswer({"x": 1}, 10, 5, 0.01)
    with tenant_transaction(documents_engine, "center_a") as conn:
        cache.put(conn, key, "m", "v1", answer)
        cache.put(conn, key, "m", "v1", answer)  # duplicate is ignored
    with tenant_transaction(documents_engine, "center_a") as conn:
        assert cache.get(conn, key) == answer
    with tenant_transaction(documents_engine, "center_b") as conn:
        assert cache.get(conn, key) is None


def test_cache_key_changes_with_the_text_the_model_saw() -> None:
    keys = {cache.cache_key("f" * 64, "v1", "m", "a", t) for t in ("one", "two")}
    assert len(keys) == 2


def run(engine: Engine, budget: Budget, fake: Any, file_sha: str) -> Any:
    extractor = llm.LiteLlmExtractor("openai/gpt-6-luna", completion=fake)
    return run_pass(engine, "center_a", extractor, budget, "heart", SENT, "a", file_sha)


def test_a_rerun_is_served_from_cache_at_no_cost(documents_engine: Engine) -> None:
    budget, fake, file_sha = (
        new_budget(documents_engine, 1.0),
        FakeCompletion(ANSWER),
        uuid.uuid4().hex * 2,
    )
    first = run(documents_engine, budget, fake, file_sha)
    second = run(documents_engine, budget, fake, file_sha)
    assert (first.cached, second.cached) == (False, True)
    assert first.cost_usd > 0 and second.cost_usd == 0
    assert first.output == second.output
    assert first.output is not None and first.output["acr_grade"]["value"] == "2R"
    assert len(fake.calls) == 1
    spent, _ = budget.totals()
    assert spent == pytest.approx(Decimal(str(first.cost_usd)))


def test_an_exhausted_budget_stops_before_the_model_is_called(documents_engine: Engine) -> None:
    budget, fake = new_budget(documents_engine, 0.001), FakeCompletion(ANSWER)
    with pytest.raises(BudgetExceededError):
        run(documents_engine, budget, fake, uuid.uuid4().hex * 2)
    assert fake.calls == []


def test_a_failed_pass_is_recorded_and_its_cost_counted(documents_engine: Engine) -> None:
    budget = new_budget(documents_engine, 1.0)
    fake = FakeCompletion(ANSWER | {"acr_grade": finding("9R", 1, "9R")})
    outcome = run(documents_engine, budget, fake, uuid.uuid4().hex * 2)
    assert outcome.output is None
    assert outcome.error is not None and outcome.error.startswith("InstructorRetryException")
    assert len(outcome.error) <= llm.MAX_ERROR_CHARS
    spent, _ = budget.totals()
    assert spent > 0 and float(spent) == pytest.approx(outcome.cost_usd)
