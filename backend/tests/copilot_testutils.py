"""A scripted stand-in for the copilot's model (no real LLM call in any test)."""

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import litellm
from fastapi.testclient import TestClient


class ScriptedModel:
    """Answers like litellm.completion, by request kind:
    - the classifier (schema "Screening") with `kind` / `reason`;
    - the planner (schema "Step") with the next of `steps`, then "answer";
    - the streamed answer with `answer`, in small pieces;
    - a quote-grounded answer (schema "GroundedAnswer") with `claims` and `note`.
    Every request is recorded, so tests can check what the model was shown."""

    def __init__(
        self, *, steps: list[dict[str, Any]] | None = None, answer: str = "Done.",
        kind: str = "data", reason: str | None = None,
        claims: list[dict[str, Any]] | None = None, note: str | None = None,
    ) -> None:  # fmt: skip
        self.steps = list(steps or [])
        self.answer, self.kind, self.reason = answer, kind, reason
        self.claims, self.note = list(claims or []), note
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self.chunks(self.answer)
        schema = kwargs["response_format"]["json_schema"]["name"]
        if schema == "Screening":
            content: dict[str, Any] = {"kind": self.kind, "reason": self.reason}
        elif schema == "GroundedAnswer":
            content = {"claims": self.claims, "note": self.note}
        else:
            content = {"action": self.steps.pop(0) if self.steps else {"tool": "answer"}}
        return litellm.ModelResponse(
            model="gpt-6-luna",
            choices=[{"index": 0, "finish_reason": "stop",
                      "message": {"role": "assistant", "content": json.dumps(content)}}],
            usage={"prompt_tokens": 2000, "completion_tokens": 200, "total_tokens": 2200},
        )  # fmt: skip

    @staticmethod
    def chunks(text: str) -> Iterator[Any]:
        """Streamed pieces of 7 characters, then a usage-only chunk."""
        for i in range(0, len(text), 7):
            delta = SimpleNamespace(content=text[i : i + 7])
            yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)
        usage = SimpleNamespace(prompt_tokens=3000, completion_tokens=150)
        yield SimpleNamespace(choices=[], usage=usage)

    def shown(self, kind: str) -> list[str]:
        """Everything sent to the model in requests of one kind ("Step", "Screening", "text")."""
        sent = [
            c
            for c in self.calls
            if (c.get("stream") and kind == "text")
            or (not c.get("stream") and c["response_format"]["json_schema"]["name"] == kind)
        ]
        return [m["content"] for c in sent for m in c["messages"]]


def question_of(messages: list[dict[str, str]]) -> str:
    """The question: a "Question: ..." line (planner, answer) or the classifier's data block."""
    for message in reversed(messages):
        content = message["content"]
        if content.startswith("Question: "):
            return content[len("Question: ") :]
        if content.startswith('<data source="question">'):
            return str(json.loads(content.split(">", 1)[1].rsplit("<", 1)[0]))
    return ""


class KeywordModel(ScriptedModel):
    """A stateless scripted model for the e2e server: the plan follows the question's words.
    - "literature" -> literature_search, then an answer citing the first abstract;
    - "by organ" -> sql_query counting patients by organ;
    - otherwise -> preview_cohort (kidney if the question says so).
    The step comes from how many tool results the planner has already been shown."""

    def __call__(self, **kwargs: Any) -> Any:
        messages = kwargs["messages"]
        question = question_of(messages)
        literature = "literature" in question.lower()
        self.kind = "literature" if literature else "data"
        done = sum(m["role"] == "assistant" and m["content"].startswith("{") for m in messages)
        plan: list[dict[str, Any]]
        if literature:
            plan = [{"tool": "literature_search", "query": question}]
            self.claims = [{"text": "Donor-derived cell-free DNA of 1% or more identified "
                            "antibody-mediated rejection with 85% sensitivity.",
                            "quotes": [{"pmid": 90000001, "quote": "A fraction of 1% or more "
                                        "identified antibody-mediated rejection with 85% "
                                        "sensitivity"}]}]  # fmt: skip
        elif "by organ" in question.lower():
            plan = [{"tool": "sql_query", "purpose": "patients by organ",
                     "sql": "SELECT organ, count(DISTINCT person_id) AS patients "
                            "FROM copilot.patient GROUP BY organ ORDER BY organ"}]  # fmt: skip
            self.answer = "The table above counts patients by organ."
        else:
            organ = "kidney" if "kidney" in question.lower() else "heart"
            plan = [{"tool": "preview_cohort", "rules": {"organ": organ}}]
            self.answer = f"The cohort tool counted the {organ} recipients shown above."
        self.steps = plan[done:]
        return super().__call__(**kwargs)


def events(body: str) -> list[dict[str, Any]]:
    """Parse a text/event-stream body."""
    found = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        event = json.loads(lines["data"])
        assert event["type"] == lines["event"]
        found.append(event)
    return found


def new_conversation(client: TestClient) -> str:
    response = client.post("/copilot/conversations", json={"title": "Heart counts"})
    assert response.status_code == 201, response.text
    return str(response.json()["conversation_id"])
