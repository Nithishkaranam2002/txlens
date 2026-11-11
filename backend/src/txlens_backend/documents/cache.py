"""LLM answers cached by what determines them, so re-running a batch costs nothing.

The key is the file hash, prompt version, model and pass, plus a hash of the exact text the
model saw: if OCR or minimization ever changes the text, the old answer is not reused.
Rows are tenant-isolated like the documents they came from.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Connection, text


@dataclass(frozen=True)
class CachedAnswer:
    """A stored answer and what it cost when it was first bought."""

    response: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost_usd: float


def cache_key(
    file_sha256: str, prompt_version: str, model: str, variant: str, document: str
) -> str:
    """One key per (file, prompt, model, pass, exact text)."""
    text_sha = hashlib.sha256(document.encode()).hexdigest()
    parts = (file_sha256, prompt_version, model, variant, text_sha)
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def get(conn: Connection, key: str) -> CachedAnswer | None:
    """The cached answer, if any (within the current tenant)."""
    row = conn.execute(
        text(
            "SELECT response, input_tokens, output_tokens, cost_usd FROM app.llm_cache "
            "WHERE cache_key = :k"
        ),
        {"k": key},
    ).first()
    if row is None:
        return None
    return CachedAnswer(row.response, row.input_tokens, row.output_tokens, float(row.cost_usd))


def put(conn: Connection, key: str, model: str, prompt_version: str, answer: CachedAnswer) -> None:
    """Store an answer; a concurrent duplicate is ignored."""
    conn.execute(
        text(
            "INSERT INTO app.llm_cache (cache_key, model, prompt_version, response, "
            "input_tokens, output_tokens, cost_usd) VALUES (:k, :m, :p, CAST(:r AS jsonb), "
            ":i, :o, :c) ON CONFLICT (tenant_id, cache_key) DO NOTHING"
        ),
        {
            "k": key,
            "m": model,
            "p": prompt_version,
            "r": json.dumps(answer.response),
            "i": answer.input_tokens,
            "o": answer.output_tokens,
            "c": answer.cost_usd,
        },
    )
