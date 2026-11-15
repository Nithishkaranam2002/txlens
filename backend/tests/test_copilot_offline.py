"""The copilot without an API key (the public demo): cached answers replay exactly, also for a
suggested question typed a little differently or asked after other questions; anything else
gets a friendly demo note (or, for "help", what the copilot does) without calling a model.
Question embeddings are cached too, and the suggested questions come from the API."""

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
from txlens_backend.copilot.suggested import DEMO_NO_LIVE_AI, HELP, SUGGESTED
from txlens_backend.documents.llm import LlmOfflineError

pytestmark = pytest.mark.db

QUESTION = "How many heart recipients are there?"
SAVED = SUGGESTED[0]
Events = list[dict[str, object]]


@pytest.fixture
def use_deps(api_app: FastAPI, api_owner: Engine) -> Iterator[None]:
    """An empty LLM cache; the test installs its own copilot deps."""
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.llm_cache"))
    yield
    api_app.dependency_overrides.pop(copilot_deps, None)


def ask_in(api_app: FastAPI, client: TestClient, deps: service.CopilotDeps, conversation: str,
           question: str) -> Events:  # fmt: skip
    """Ask `question` in `conversation` with these deps; the stream's events."""
    api_app.dependency_overrides[copilot_deps] = lambda: deps
    response = client.post(f"/copilot/conversations/{conversation}/messages",
                           json={"question": question})  # fmt: skip
    assert response.status_code == 200, response.text
    return events(response.text)


def ask(api_app: FastAPI, client: TestClient, deps: service.CopilotDeps,
        question: str = QUESTION) -> Events:  # fmt: skip
    """Ask `question` in a new conversation."""
    return ask_in(api_app, client, deps, new_conversation(client), question)


def scripted() -> ScriptedModel:
    return ScriptedModel(steps=[{"tool": "preview_cohort", "rules": {"organ": "heart"}}],
                         answer="Here is the count.")  # fmt: skip


@pytest.fixture
def demo(api_settings: Settings, api_app_engine: Engine) -> tuple[service.CopilotDeps,
                                                                  ScriptedModel]:  # fmt: skip
    """Offline deps (the public demo) and their model, which must never be called."""
    model = scripted()
    return service.CopilotDeps(api_settings, api_app_engine, FakeEmbedder(), model,
                               offline=True), model  # fmt: skip


def cache_saved_answer(api_app: FastAPI, client: TestClient, api_settings: Settings,
                       api_app_engine: Engine) -> dict[str, object]:  # fmt: skip
    """Fill the cache like `txlens-demo copilot-cache`: SAVED, alone, with a model."""
    online = service.CopilotDeps(api_settings, api_app_engine, FakeEmbedder(), scripted())
    return ask(api_app, client, online, SAVED)[-1]


def replays(found: Events, first: dict[str, object]) -> None:
    """The saved answer, with its tool, from the cache only, and its timing."""
    done = found[-1]
    assert done["outcome"] == "answered" and done["answer"] == first["answer"]
    assert done["cached_calls"] == done["llm_calls"] and isinstance(done["latency_ms"], int)
    assert [e["label"] for e in found if e["type"] == "tool"] == ["built cohort"]
    assert not any(e["type"] in ("error", "notice") for e in found)


def test_an_unknown_question_gets_the_demo_note_once_and_calls_nothing(
    api_app: FastAPI, client: TestClient, use_deps: None,
    demo: tuple[service.CopilotDeps, ScriptedModel],
) -> None:  # fmt: skip
    login(client, Role.RESEARCHER)
    deps, model = demo
    conversation = new_conversation(client)
    found = ask_in(api_app, client, deps, conversation, QUESTION)
    assert [e["type"] for e in found if e["type"] != "status"] == ["notice", "done"]
    assert {"type": "notice", "text": DEMO_NO_LIVE_AI} in found
    assert DEMO_NO_LIVE_AI.endswith("Try one of these:")
    assert found[-1]["outcome"] == "demo" and found[-1]["llm_calls"] == 0
    assert model.calls == []
    stored = client.get(f"/copilot/conversations/{conversation}").json()["messages"]
    assert [(m["role"], m["outcome"]) for m in stored] == [("user", None), ("assistant", "demo")]


def test_a_help_question_explains_the_copilot(
    api_app: FastAPI, client: TestClient, use_deps: None,
    demo: tuple[service.CopilotDeps, ScriptedModel],
) -> None:  # fmt: skip
    login(client, Role.RESEARCHER)
    deps, model = demo
    found = ask(api_app, client, deps, "What is this about?")
    assert found[0] == {"type": "notice", "text": HELP}
    for part in ("data tools", "PMID", "de-identified", "1 to 10 are hidden"):
        assert part in HELP
    assert model.calls == []


@pytest.mark.parametrize("typed", [
    "median DD-CFDNA in the first year after transplant by organ?",
    "  Median dd-cfDNA  in the first year after transplant, by organ  ",
    "Median dd-cfDNA in the frist year after transplant, by organ",
])  # fmt: skip
def test_a_saved_question_typed_differently_replays_its_answer(
    api_app: FastAPI, client: TestClient, use_deps: None, api_settings: Settings,
    api_app_engine: Engine, demo: tuple[service.CopilotDeps, ScriptedModel], typed: str,
) -> None:  # fmt: skip
    login(client, Role.RESEARCHER)
    first = cache_saved_answer(api_app, client, api_settings, api_app_engine)
    deps, model = demo
    replays(ask(api_app, client, deps, typed), first)
    assert model.calls == []


def test_a_suggestion_clicked_after_other_questions_replays_its_answer(
    api_app: FastAPI, client: TestClient, use_deps: None, api_settings: Settings,
    api_app_engine: Engine, demo: tuple[service.CopilotDeps, ScriptedModel],
) -> None:  # fmt: skip
    """A chip sends the suggestion's text; earlier turns must not change the cache key."""
    login(client, Role.RESEARCHER)
    first = cache_saved_answer(api_app, client, api_settings, api_app_engine)
    deps, model = demo
    conversation = new_conversation(client)
    ask_in(api_app, client, deps, conversation, QUESTION)
    ask_in(api_app, client, deps, conversation, "help")
    replays(ask_in(api_app, client, deps, conversation, SAVED), first)
    replays(ask_in(api_app, client, deps, conversation, SAVED), first)
    assert model.calls == []


def test_a_suggested_question_not_cached_yet_gets_the_demo_note_not_an_error(
    api_app: FastAPI, client: TestClient, use_deps: None,
    demo: tuple[service.CopilotDeps, ScriptedModel],
) -> None:  # fmt: skip
    login(client, Role.RESEARCHER)
    deps, model = demo
    found = ask(api_app, client, deps, SAVED)
    assert {"type": "notice", "text": DEMO_NO_LIVE_AI} in found
    assert not any(e["type"] == "error" for e in found)
    assert found[-1]["outcome"] == "demo" and model.calls == []


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
