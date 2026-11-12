"""The copilot's LLM calls through LiteLLM: structured (Instructor) and streamed text.

Every call is:
- cached in app.llm_cache by prompt version, model, kind and the exact messages (per tenant),
  so asking the same question about the same data again costs nothing;
- paid for from the copilot Budget: its worst case is reserved before the call and the real
  cost settled after; a call that would pass the cap is not made (BudgetExceededError);
- traced as a generation of the question's Langfuse trace.
Structured answers use Instructor's JSON-schema mode (no tool calls: LiteLLM 1.106 imports its
optional MCP proxy for any call with tools). The model never sees patient rows.
"""

import hashlib
import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

import instructor
import litellm
from pydantic import BaseModel
from sqlalchemy import Engine

from txlens_backend.copilot.tracing import QuestionTrace
from txlens_backend.db import tenant_transaction
from txlens_backend.documents import cache
from txlens_backend.documents.budget import Budget
from txlens_backend.documents.llm import price_per_token

PROMPT_VERSION = "copilot-2026-11-12.2"  # .2: rounding, no tool names
MAX_OUTPUT_TOKENS = 2500  # includes reasoning tokens
VALIDATION_RETRIES = 1
NETWORK_RETRIES = 2
T = TypeVar("T", bound=BaseModel)
Messages = list[dict[str, str]]


@dataclass
class Spend:
    """What one question's LLM calls used."""

    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    cost_without_cache_usd: float = 0.0
    models: set[str] = field(default_factory=set)


class CopilotLlm:
    """LLM calls for one question: one tenant, one budget, one trace."""

    def __init__(
        self, model: str, *, engine: Engine, tenant_id: str, budget: Budget,
        trace: QuestionTrace, reasoning_effort: str = "low", timeout_seconds: int = 60,
        api_base: str | None = None, completion: Callable[..., Any] | None = None,
    ) -> None:  # fmt: skip
        self.model, self.engine, self.tenant_id = model, engine, tenant_id
        self.budget, self.trace = budget, trace
        self.reasoning_effort, self.timeout_seconds = reasoning_effort, timeout_seconds
        self.api_base = api_base if model.startswith("ollama/") else None
        self.completion = completion or litellm.completion
        self.spend = Spend()
        litellm.drop_params = True

    def key(self, kind: str, messages: Messages) -> str:
        """Cache key: prompt version, model, kind (schema or 'text') and exact messages."""
        body = json.dumps([PROMPT_VERSION, self.model, kind, messages], sort_keys=True)
        return hashlib.sha256(body.encode()).hexdigest()

    def cached(self, key: str) -> cache.CachedAnswer | None:
        with tenant_transaction(self.engine, self.tenant_id) as conn:
            return cache.get(conn, key)

    def store(self, key: str, answer: cache.CachedAnswer) -> None:
        with tenant_transaction(self.engine, self.tenant_id) as conn:
            cache.put(conn, key, self.model, PROMPT_VERSION, answer)

    def worst_case(self, messages: Messages) -> float:
        """Upper bound on one call: generous input estimate, full output, one re-ask."""
        input_price, output_price = price_per_token(self.model)
        prompt_tokens = sum(len(m["content"]) for m in messages) // 3 + 200
        return (1 + VALIDATION_RETRIES) * (prompt_tokens * input_price
                                           + MAX_OUTPUT_TOKENS * output_price)  # fmt: skip

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        """Dollars for these tokens at the model's price (local models are free)."""
        input_price, output_price = price_per_token(self.model)
        return input_tokens * input_price + output_tokens * output_price

    def record(self, name: str, messages: Messages, output: Any, answer: cache.CachedAnswer,
               started: datetime, *, cached: bool) -> None:  # fmt: skip
        """Count the call and trace it."""
        self.spend.calls += 1
        self.spend.cached += cached
        self.spend.models.add(self.model)
        self.spend.cost_without_cache_usd += answer.cost_usd
        if not cached:
            self.spend.input_tokens += answer.input_tokens
            self.spend.output_tokens += answer.output_tokens
            self.spend.cost_usd += answer.cost_usd
        self.trace.generation(
            name, self.model, messages, output, (answer.input_tokens, answer.output_tokens),
            0.0 if cached else answer.cost_usd, started, datetime.now(UTC), cached=cached,
        )  # fmt: skip

    def structured(self, schema: type[T], messages: Messages, name: str) -> T:
        """An answer validated against `schema` (cached, budgeted, traced)."""
        key, started = self.key(schema.__name__, messages), datetime.now(UTC)
        hit = self.cached(key)
        if hit is not None:
            self.record(name, messages, hit.response, hit, started, cached=True)
            return schema.model_validate(hit.response)
        tokens = [0, 0]

        def count(response: Any) -> None:
            usage = getattr(response, "usage", None)
            tokens[0] += int(getattr(usage, "prompt_tokens", 0) or 0)
            tokens[1] += int(getattr(usage, "completion_tokens", 0) or 0)

        client = instructor.from_litellm(self.completion, mode=instructor.Mode.JSON_SCHEMA)
        client.on("completion:response", count)
        reserved = self.worst_case(messages)
        self.budget.reserve(reserved)
        try:
            result = client.chat.completions.create(
                model=self.model, response_model=schema, messages=messages,  # type: ignore[arg-type]
                max_retries=VALIDATION_RETRIES, max_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort=self.reasoning_effort, timeout=self.timeout_seconds,
                num_retries=NETWORK_RETRIES, api_base=self.api_base,
            )  # fmt: skip
        finally:
            self.budget.settle(reserved, self.cost(*tokens))
        output = result.model_dump(mode="json")
        answer = cache.CachedAnswer(output, tokens[0], tokens[1], self.cost(*tokens))
        self.store(key, answer)
        self.record(name, messages, output, answer, started, cached=False)
        return schema.model_validate(output)

    def stream(self, messages: Messages, name: str) -> Iterator[str]:
        """The answer text as it is generated (a cached answer is replayed in pieces)."""
        key, started = self.key("text", messages), datetime.now(UTC)
        hit = self.cached(key)
        if hit is not None:
            self.record(name, messages, hit.response["text"], hit, started, cached=True)
            words = hit.response["text"].split(" ")
            for i in range(0, len(words), 4):
                yield " ".join(words[i : i + 4]) + (" " if i + 4 < len(words) else "")
            return
        reserved, parts, tokens = self.worst_case(messages), [], (0, 0)
        self.budget.reserve(reserved)
        try:
            chunks = self.completion(
                model=self.model, messages=messages, stream=True,
                stream_options={"include_usage": True}, max_tokens=MAX_OUTPUT_TOKENS,
                reasoning_effort=self.reasoning_effort, timeout=self.timeout_seconds,
                num_retries=NETWORK_RETRIES, api_base=self.api_base,
            )  # fmt: skip
            for chunk in chunks:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    tokens = (int(usage.prompt_tokens or 0), int(usage.completion_tokens or 0))
                piece = chunk.choices[0].delta.content if chunk.choices else None
                if piece:
                    parts.append(piece)
                    yield piece
        finally:
            self.budget.settle(reserved, self.cost(*tokens))
        answer = cache.CachedAnswer({"text": "".join(parts)}, *tokens, self.cost(*tokens))
        self.store(key, answer)
        self.record(name, messages, answer.response["text"], answer, started, cached=False)
