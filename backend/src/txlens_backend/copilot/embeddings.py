"""Text embeddings through LiteLLM (the only way out to a model), 768 dimensions.

OpenAI's text-embedding-3 models are asked for 768 dimensions; the local nomic-embed-text
model (Ollama) has 768 natively. The cost of a call is its input tokens at the model's price
(local models are free), so a Budget can cap indexing like any other LLM batch.
"""

import os
from collections.abc import Callable, Sequence
from typing import Any, Protocol

import litellm

from txlens_backend.config import Settings
from txlens_backend.documents.llm import price_per_token

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
