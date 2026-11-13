"""The copilot without an API key (the public demo): cached answers replay exactly, anything
else gets a friendly "live AI is disabled" message without calling a model. Question
embeddings are cached too, and the suggested questions come from the API."""

from collections.abc import Iterator

import pytest
from api_testutils import login
from copilot_testutils import ScriptedModel, events, new_conversation
from fastapi import FastAPI
from fastapi.testclient import TestClient
from literature_testutils import FakeEmbedder
from sqlalchemy import Engine, text

from txlens_backend.api.copilot import copilot_deps
from txlens_backend.api.deps import Role
from txlens_backend.config import Settings
from txlens_backend.copilot import service
from txlens_backend.copilot.embeddings import CachedEmbedder
from txlens_backend.copilot.suggested import SUGGESTED
from txlens_backend.documents.llm import LlmOfflineError

pytestmark = pytest.mark.db

QUESTION = "How many heart recipients are there?"


@pytest.fixture
def use_deps(api_app: FastAPI, api_owner: Engine) -> Iterator[None]:
    """An empty LLM cache; the test installs its own copilot deps."""
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.llm_cache"))
    yield
    api_app.dependency_overrides.pop(copilot_deps, None)


def ask(api_app: FastAPI, client: TestClient, deps: service.CopilotDeps) -> list[dict[str, object]]:
    """Ask QUESTION in a new conversation with these deps; the stream's events."""
    api_app.dependency_overrides[copilot_deps] = lambda: deps
    conversation = new_conversation(client)
    response = client.post(f"/copilot/conversations/{conversation}/messages",
                           json={"question": QUESTION})  # fmt: skip
    return events(response.text)


def scripted() -> ScriptedModel:
    return ScriptedModel(steps=[{"tool": "preview_cohort", "rules": {"organ": "heart"}}],
                         answer="Here is the count.")  # fmt: skip


def test_offline_without_a_cached_answer_says_live_ai_is_off_and_calls_nothing(
    api_app: FastAPI, client: TestClient, use_deps: None, api_settings: Settings,
    api_app_engine: Engine,
) -> None:  # fmt: skip
    login(client, Role.RESEARCHER)
    model = scripted()
    deps = service.CopilotDeps(api_settings, api_app_engine, FakeEmbedder(), model, offline=True)
    found = ask(api_app, client, deps)
    assert {"type": "error", "message": service.LIVE_AI_DISABLED} in found
    done = found[-1]
    assert done["type"] == "done" and done["outcome"] == "error"
    assert model.calls == []


def test_an_answer_cached_online_replays_offline_without_a_model_call(
    api_app: FastAPI, client: TestClient, use_deps: None, api_settings: Settings,
    api_app_engine: Engine,
) -> None:  # fmt: skip
    login(client, Role.RESEARCHER)
    model = scripted()
    online = service.CopilotDeps(api_settings, api_app_engine, FakeEmbedder(), model)
    first = ask(api_app, client, online)[-1]
    calls = len(model.calls)
    offline = service.CopilotDeps(api_settings, api_app_engine, FakeEmbedder(), scripted(),
                                  offline=True)  # fmt: skip
    again = ask(api_app, client, offline)[-1]
    assert first["outcome"] == again["outcome"] == "answered"
    assert again["answer"] == first["answer"] and again["cached_calls"] == again["llm_calls"]
    assert len(model.calls) == calls


def test_question_embeddings_are_cached_and_offline_refuses_a_new_text(
    api_app_engine: Engine, api_owner: Engine, use_deps: None
) -> None:
    inner = FakeEmbedder()
    online = CachedEmbedder(inner, api_app_engine, "center_a", offline=False)
    vectors, _ = online.embed(["dd-cfDNA thresholds", "heart rejection", "dd-cfDNA thresholds"])
    again, cost = online.embed(["heart rejection"])
    assert inner.calls == [["dd-cfDNA thresholds", "heart rejection"]]  # each text once
    assert again == [vectors[1]] and cost == 0.0 and vectors[0] == vectors[2]
    offline = CachedEmbedder(FakeEmbedder(), api_app_engine, "center_a", offline=True)
    assert offline.embed(["heart rejection"])[0] == [vectors[1]]
    with pytest.raises(LlmOfflineError):
        offline.embed(["a text never embedded"])
    other_center = CachedEmbedder(FakeEmbedder(), api_app_engine, "center_b", offline=True)
    with pytest.raises(LlmOfflineError):  # the cache is per center, like every LLM answer
        other_center.embed(["heart rejection"])


def test_suggested_questions_come_from_the_api_for_copilot_roles_only(client: TestClient) -> None:
    login(client, Role.RESEARCHER)
    assert client.get("/copilot/suggestions").json() == list(SUGGESTED)
    login(client, Role.DATA_STEWARD)
    assert client.get("/copilot/suggestions").status_code == 403


def test_the_suggestions_cover_count_cohort_survival_literature_and_a_refusal() -> None:
    joined = " ".join(SUGGESTED).lower()
    assert len(SUGGESTED) == 6
    assert "survival" in joined and "literature" in joined and "mrn" in joined
