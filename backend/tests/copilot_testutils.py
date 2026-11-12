"""A scripted stand-in for the copilot's model (no real LLM call in any test)."""

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import litellm


class ScriptedModel:
    """Answers like litellm.completion, by request kind:
    - the classifier (schema "Screening") with `kind` / `reason`;
    - the planner (schema "Step") with the next of `steps`, then "answer";
    - the streamed answer with `answer`, in small pieces.
    Every request is recorded, so tests can check what the model was shown."""

    def __init__(self, *, steps: list[dict[str, Any]] | None = None, answer: str = "Done.",
                 kind: str = "data", reason: str | None = None) -> None:  # fmt: skip
        self.steps = list(steps or [])
        self.answer, self.kind, self.reason = answer, kind, reason
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("stream"):
            return self.chunks(self.answer)
        schema = kwargs["response_format"]["json_schema"]["name"]
        if schema == "Screening":
            content: dict[str, Any] = {"kind": self.kind, "reason": self.reason}
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
