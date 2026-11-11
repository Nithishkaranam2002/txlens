"""Langfuse traces of extraction passes, written with the Langfuse v2 SDK.

The local Langfuse server is v2 (ADR 0002). LiteLLM 1.106's own Langfuse callback needs the
v4 SDK (and a server that accepts OpenTelemetry), so passes are recorded here instead: one
trace per pass with one generation holding the minimized messages, the answer, tokens, cost,
latency, model and prompt version. Metadata is limited to the file hash, batch and pass: no
path, name or MRN. A tracing failure is logged and never stops an extraction.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any, Protocol

from langfuse import Langfuse

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TracedCall:
    """One extraction pass as Langfuse should see it."""

    file_sha256: str
    batch_id: str
    variant: str
    model: str
    prompt_version: str
    messages: list[dict[str, str]]
    output: dict[str, Any] | None
    error: str | None
    input_tokens: int
    output_tokens: int
    cost_usd: float
    started: datetime
    ended: datetime


class Tracer(Protocol):
    """Records extraction passes somewhere people can inspect them."""

    def record(self, call: TracedCall) -> None:
        """Record one pass."""
        ...

    def flush(self) -> None:
        """Send anything still queued."""
        ...


class LangfuseTracer:
    """Writes each pass to Langfuse (batched in the background by the SDK)."""

    def __init__(self, client: Any) -> None:
        self.client = client

    def record(self, call: TracedCall) -> None:
        metadata = {
            "file_sha256": call.file_sha256,
            "prompt_version": call.prompt_version,
            "pass": call.variant,
        }
        try:
            trace = self.client.trace(
                name="biopsy-extraction",
                session_id=call.batch_id,
                metadata=metadata,
                tags=[call.prompt_version, f"pass-{call.variant}"],
                input=call.messages,
                output=call.output,
            )
            trace.generation(
                name=f"biopsy-pass-{call.variant}",
                model=call.model,
                input=call.messages,
                output=call.output,
                start_time=call.started,
                end_time=call.ended,
                metadata=metadata,
                # The v2 server reads `usage` (tokens and our computed cost), not usage_details.
                usage={
                    "input": call.input_tokens,
                    "output": call.output_tokens,
                    "unit": "TOKENS",
                    "total_cost": call.cost_usd,
                },
                level="ERROR" if call.error else "DEFAULT",
                status_message=call.error,
            )
        except Exception:  # tracing must never stop an extraction
            log.warning("could not record a Langfuse trace", exc_info=True)

    def flush(self) -> None:
        """Send everything still queued (call before a short-lived process exits)."""
        self.client.flush()


@cache
def langfuse_tracer(public_key: str, secret_key: str, host: str) -> LangfuseTracer:
    """One Langfuse client per process and configuration."""
    return LangfuseTracer(Langfuse(public_key=public_key, secret_key=secret_key, host=host))
