"""Langfuse traces of copilot questions (Langfuse v2 SDK, as for document extraction).

One trace per question, with a generation per LLM call (planner, classifier, answer) and a
span per tool call. Inputs are the question and the aggregate tool results the model saw:
no patient rows ever reach the copilot, so none reach Langfuse. Metadata: tenant,
conversation, message and prompt version. A tracing failure never stops an answer.
"""

import logging
from datetime import datetime
from typing import Any, Protocol

from txlens_backend.config import Settings
from txlens_backend.documents.llm import tracer_for
from txlens_backend.documents.tracing import LangfuseTracer

log = logging.getLogger(__name__)


class QuestionTrace(Protocol):
    """Where one question's steps are recorded."""

    def generation(self, name: str, model: str, messages: list[dict[str, str]], output: Any,
                   tokens: tuple[int, int], cost_usd: float, started: datetime,
                   ended: datetime, *, cached: bool = False,
                   error: str | None = None) -> None:  # fmt: skip
        """One LLM call."""
        ...

    def span(self, name: str, input: Any, output: Any, started: datetime, ended: datetime,
             error: str | None = None) -> None:  # fmt: skip
        """One tool call or deterministic step."""
        ...

    def end(self, output: Any) -> None:
        """The question's outcome; flushes."""
        ...


class NullTrace:
    """Records nothing (Langfuse not configured, or tests)."""

    def generation(self, *_: Any, **__: Any) -> None:
        return None

    def span(self, *_: Any, **__: Any) -> None:
        return None

    def end(self, output: Any) -> None:
        return None


class LangfuseQuestionTrace:
    """A Langfuse trace for one question."""

    def __init__(self, client: Any, name: str, question: str, metadata: dict[str, str]) -> None:
        self.client = client
        self.trace = client.trace(name=name, input=question, metadata=metadata,
                                  session_id=metadata.get("conversation_id"))  # fmt: skip

    def generation(self, name: str, model: str, messages: list[dict[str, str]], output: Any,
                   tokens: tuple[int, int], cost_usd: float, started: datetime,
                   ended: datetime, *, cached: bool = False,
                   error: str | None = None) -> None:  # fmt: skip
        try:
            self.trace.generation(
                name=name, model=model, input=messages, output=output, start_time=started,
                end_time=ended, metadata={"cached": cached},
                usage={"input": tokens[0], "output": tokens[1], "unit": "TOKENS",
                       "total_cost": cost_usd},
                level="ERROR" if error else "DEFAULT", status_message=error,
            )  # fmt: skip
        except Exception:  # tracing must never stop an answer
            log.warning("could not record a Langfuse generation", exc_info=True)

    def span(self, name: str, input: Any, output: Any, started: datetime, ended: datetime,
             error: str | None = None) -> None:  # fmt: skip
        try:
            self.trace.span(name=name, input=input, output=output, start_time=started,
                            end_time=ended, level="ERROR" if error else "DEFAULT",
                            status_message=error)  # fmt: skip
        except Exception:
            log.warning("could not record a Langfuse span", exc_info=True)

    def end(self, output: Any) -> None:
        try:
            self.trace.update(output=output)
            self.client.flush()
        except Exception:
            log.warning("could not finish a Langfuse trace", exc_info=True)


def start_trace(settings: Settings, question: str, metadata: dict[str, str]) -> QuestionTrace:
    """A Langfuse trace when Langfuse is configured, else one that records nothing."""
    tracer = tracer_for(settings)
    if not isinstance(tracer, LangfuseTracer):
        return NullTrace()
    try:
        return LangfuseQuestionTrace(tracer.client, "copilot-question", question, metadata)
    except Exception:
        log.warning("could not start a Langfuse trace", exc_info=True)
        return NullTrace()
