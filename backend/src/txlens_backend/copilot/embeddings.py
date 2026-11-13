"""Text embeddings through LiteLLM (the only way out to a model), 768 dimensions.

OpenAI's text-embedding-3 models are asked for 768 dimensions; the local nomic-embed-text
model (Ollama) has 768 natively. The cost of a call is its input tokens at the model's price
(local models are free), so a Budget can cap indexing like any other LLM batch.
"""

import hashlib
import json
import os
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import litellm
from sqlalchemy import Engine

from txlens_backend.config import Settings
from txlens_backend.db import tenant_transaction
from txlens_backend.documents import cache
from txlens_backend.documents.llm import LlmOfflineError, price_per_token

DIMENSIONS = 768
BATCH = 100


class Embedder(Protocol):
    """Turns texts into vectors; reports what it cost."""

    model: str

    def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], float]:
        """(one vector per text, cost in dollars)."""
        ...


def max_cost(model: str, texts: Sequence[str]) -> float:
    """An upper bound on embedding these texts (about 3 characters per token, generously)."""
    return price_per_token(model)[0] * sum(len(t) // 3 + 8 for t in texts)


class LiteLlmEmbedder:
    """Embeddings from LiteLLM, in batches."""

    def __init__(self, model: str, *, api_base: str | None = None,
                 embedding: Callable[..., Any] | None = None) -> None:  # fmt: skip
        self.model, self.api_base = model, api_base
        self.embedding = embedding or litellm.embedding

    def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], float]:
        """Vectors in input order; raises if the model returns another dimension."""
        vectors: list[list[float]] = []
        tokens = 0
        for start in range(0, len(texts), BATCH):
            response = self.embedding(
                model=self.model, input=list(texts[start : start + BATCH]),
                dimensions=DIMENSIONS, api_base=self.api_base, drop_params=True,
            )  # fmt: skip
            vectors += [list(item["embedding"]) for item in response.data]
            tokens += int(getattr(response.usage, "prompt_tokens", 0) or 0)
        if any(len(v) != DIMENSIONS for v in vectors):
            raise ValueError(f"{self.model} did not return {DIMENSIONS}-dimension vectors")
        return vectors, tokens * price_per_token(self.model)[0]


def make_embedder(settings: Settings) -> LiteLlmEmbedder:
    """The configured embedding model."""
    model = settings.embedding_model
    if model.startswith("openai/"):
        os.environ.setdefault("OPENAI_API_KEY", settings.openai_api_key.get_secret_value())
    api_base = settings.ollama_base_url if model.startswith("ollama/") else None
    return LiteLlmEmbedder(model, api_base=api_base)


class CachedEmbedder:
    """An embedder whose vectors are kept in app.llm_cache (per tenant): a question asked
    before needs no API call. `offline` (no API key): a text not cached raises
    LlmOfflineError instead of calling the model."""

    PROMPT_VERSION = "embedding"

    def __init__(self, inner: Embedder, engine: Engine, tenant_id: str, *, offline: bool) -> None:
        self.inner, self.engine, self.tenant_id, self.offline = inner, engine, tenant_id, offline
        self.model = inner.model

    def key(self, text: str) -> str:
        """Cache key: model, dimensions and the exact text."""
        body = json.dumps(["embedding", self.model, DIMENSIONS, text])
        return hashlib.sha256(body.encode()).hexdigest()

    def embed(self, texts: Sequence[str]) -> tuple[list[list[float]], float]:
        """Vectors in input order; only the texts not cached are sent to the model."""
        with tenant_transaction(self.engine, self.tenant_id) as conn:
            hits = {t: cache.get(conn, self.key(t)) for t in set(texts)}
        found = {t: hit.response["vector"] for t, hit in hits.items() if hit is not None}
        missing = [t for t in dict.fromkeys(texts) if t not in found]
        cost = 0.0
        if missing:
            if self.offline:
                raise LlmOfflineError(f"{self.model}: no API key and no cached embedding")
            vectors, cost = self.inner.embed(missing)
            share = cost / len(missing)
            with tenant_transaction(self.engine, self.tenant_id) as conn:
                for text, vector in zip(missing, vectors, strict=True):
                    answer = cache.CachedAnswer({"vector": vector}, 0, 0, share)
                    cache.put(conn, self.key(text), self.model, self.PROMPT_VERSION, answer)
            found.update(zip(missing, vectors, strict=True))
        return [found[t] for t in texts], cost
