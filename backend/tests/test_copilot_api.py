"""Copilot API: roles, CSRF, conversation ownership, and the server-sent event stream."""

from collections.abc import Iterator

import pytest
from api_testutils import login
from copilot_testutils import ScriptedModel, events, new_conversation
from fastapi import FastAPI
from fastapi.testclient import TestClient
from literature_testutils import FakeEmbedder
from sqlalchemy import Engine, text

from txlens_backend.api.copilot import copilot_deps
from txlens_backend.api.deps import CSRF_HEADER, Role
from txlens_backend.config import Settings
from txlens_backend.copilot import service

pytestmark = pytest.mark.db


@pytest.fixture
def model(api_app: FastAPI, api_app_engine: Engine, api_settings: Settings,
          api_owner: Engine) -> Iterator[ScriptedModel]:  # fmt: skip
    """The app's copilot answers with this scripted model (and the fake embedder)."""
    scripted = ScriptedModel(steps=[{"tool": "preview_cohort", "rules": {"organ": "heart"}}],
                             answer="Here is the count.")  # fmt: skip
    deps = service.CopilotDeps(api_settings, api_app_engine, FakeEmbedder(), scripted)
    api_app.dependency_overrides[copilot_deps] = lambda: deps
    with api_owner.begin() as conn:
        conn.execute(text("DELETE FROM app.llm_cache"))
    yield scripted
    api_app.dependency_overrides.pop(copilot_deps)


def test_an_answer_streams_as_server_sent_events(client: TestClient, model: ScriptedModel) -> None:
    login(client, Role.RESEARCHER)
    conversation = new_conversation(client)
    response = client.post(f"/copilot/conversations/{conversation}/messages",
                           json={"question": "How many heart recipients?"})  # fmt: skip
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    stream = events(response.text)
    kinds = [e["type"] for e in stream]
    assert kinds[0] == "status" and kinds[-1] == "done" and "tool" in kinds and "token" in kinds
    assert stream[-1]["outcome"] == "answered"
    stored = client.get(f"/copilot/conversations/{conversation}").json()
    assert [m["role"] for m in stored["messages"]] == ["user", "assistant"]
    assert stored["messages"][1]["trace"][0]["label"] == "built cohort"


def test_refusals_stream_too(client: TestClient, model: ScriptedModel) -> None:
    login(client, Role.CLINICIAN)
    conversation = new_conversation(client)
    response = client.post(f"/copilot/conversations/{conversation}/messages",
                           json={"question": "List the MRNs of heart recipients"})  # fmt: skip
    stream = events(response.text)
    assert stream[-2]["type"] == "refusal" and stream[-1]["outcome"] == "refused"


def test_conversations_are_private_to_their_user(client: TestClient, model: ScriptedModel) -> None:
    login(client, Role.RESEARCHER)
    conversation = new_conversation(client)
    assert any(c["conversation_id"] == conversation
               for c in client.get("/copilot/conversations").json())  # fmt: skip
    login(client, Role.CLINICIAN)  # same center, another user
    assert client.get(f"/copilot/conversations/{conversation}").status_code == 404
    response = client.post(f"/copilot/conversations/{conversation}/messages",
                           json={"question": "How many?"})  # fmt: skip
    assert response.status_code == 404
    login(client, Role.RESEARCHER, "center_b")
    assert client.get(f"/copilot/conversations/{conversation}").status_code == 404


@pytest.mark.parametrize("role", [Role.DATA_STEWARD, Role.ADMIN])
def test_only_clinicians_and_researchers_may_use_it(client: TestClient, role: Role) -> None:
    login(client, role)
    assert client.get("/copilot/conversations").status_code == 403
    assert client.post("/copilot/conversations", json={}).status_code == 403


def test_asking_needs_the_csrf_header(client: TestClient, model: ScriptedModel) -> None:
    login(client, Role.RESEARCHER)
    conversation = new_conversation(client)
    del client.headers[CSRF_HEADER]
    response = client.post(f"/copilot/conversations/{conversation}/messages",
                           json={"question": "How many heart recipients?"})  # fmt: skip
    assert response.status_code == 403


@pytest.mark.parametrize("question", ["", "x", "y" * 2001])
def test_questions_are_validated(client: TestClient, model: ScriptedModel, question: str) -> None:
    login(client, Role.RESEARCHER)
    conversation = new_conversation(client)
    response = client.post(f"/copilot/conversations/{conversation}/messages",
                           json={"question": question})  # fmt: skip
    assert response.status_code == 422
