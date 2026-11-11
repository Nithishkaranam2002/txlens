"""One extraction pass with its guards: cache first, then budget, then the model.

A cached answer is returned at no cost. Otherwise the pass's worst-case cost is reserved
(raising BudgetExceededError if the batch cap would be passed), the model is called, and the
real cost replaces the reservation, also when the call fails.
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine

from txlens_backend.db import tenant_transaction
from txlens_backend.documents import cache
from txlens_backend.documents.budget import Budget
from txlens_backend.documents.llm import (
    PROMPT_VERSION,
    CallMeta,
    ExtractionFailedError,
    Extractor,
    Variant,
    document_block,
    max_call_cost,
)
from txlens_backend.documents.phi import MinimizedText
from txlens_backend.documents.schema import SCHEMAS, Organ


@dataclass(frozen=True)
class PassOutcome:
    """The answer of one pass (or its error) and what it cost in this run."""

    variant: Variant
    model: str
    prompt_version: str
    output: dict[str, Any] | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int
    cached: bool


def run_pass(
    engine: Engine,
    tenant_id: str,
    extractor: Extractor,
    budget: Budget,
    organ: Organ,
    sent: MinimizedText,
    variant: Variant,
    file_sha256: str,
) -> PassOutcome:
    """Run (or reuse) one extraction pass over the minimized report."""
    document = document_block(sent)
    key = cache.cache_key(file_sha256, PROMPT_VERSION, extractor.model, variant, document)
    with tenant_transaction(engine, tenant_id) as conn:
        hit = cache.get(conn, key)
    if hit is not None:
        return PassOutcome(
            variant, extractor.model, PROMPT_VERSION, hit.response, None,
            hit.input_tokens, hit.output_tokens, 0.0, 0, True,
        )  # fmt: skip
    reserved = max_call_cost(extractor.model, document)
    budget.reserve(reserved)
    spent = 0.0
    try:
        answer, usage = extractor.extract(
            SCHEMAS[organ], document, variant, CallMeta(file_sha256, budget.batch_id)
        )
        spent = usage.cost_usd
    except ExtractionFailedError as error:
        spent = error.usage.cost_usd
        return PassOutcome(
            variant, extractor.model, PROMPT_VERSION, None, str(error),
            error.usage.input_tokens, error.usage.output_tokens, spent,
            error.usage.latency_ms, False,
        )  # fmt: skip
    finally:
        budget.settle(reserved, spent)
    response = answer.model_dump(mode="json")
    stored = cache.CachedAnswer(response, usage.input_tokens, usage.output_tokens, spent)
    with tenant_transaction(engine, tenant_id) as conn:
        cache.put(conn, key, extractor.model, PROMPT_VERSION, stored)
    return PassOutcome(
        variant, extractor.model, PROMPT_VERSION, response, None,
        usage.input_tokens, usage.output_tokens, spent, usage.latency_ms, False,
    )  # fmt: skip
