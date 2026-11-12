"""`txlens-notes process`: run clinic notes through the note NLP (Temporal batch workflow).

Needs a running worker (`txlens-worker`). The batch shares one LLM budget (LLM_BUDGET_USD,
capped again by --max-cost); notes already processed with the same text and system are skipped.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from txlens_backend.config import get_settings
from txlens_backend.workflows.biopsy import TASK_QUEUE
from txlens_backend.workflows.cli import connect
from txlens_backend.workflows.notes import NoteNlpBatchWorkflow, NotesBatchRequest, NotesBatchResult


async def process(request: NotesBatchRequest) -> NotesBatchResult:
    """Start the batch workflow and wait for it."""
    client = await connect()
    return await client.execute_workflow(
        NoteNlpBatchWorkflow.run,
        request,
        id=f"notes-batch-{request.batch_id}",
        task_queue=TASK_QUEUE,
    )


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("process", help="process the notes of every (or some) tenant")
    run.add_argument("--tenant", action="append", default=[], help="repeat for several")
    run.add_argument("--limit", type=int, default=0, help="notes per tenant (0: all)")
    run.add_argument("--no-llm", action="store_true", help="rules only (no paid calls)")
    run.add_argument("--batch-id", default=f"notes-{datetime.now(UTC):%Y%m%d-%H%M%S}")
    args = parser.parse_args()
    request = NotesBatchRequest(args.batch_id, args.tenant, args.limit, not args.no_llm)
    result = asyncio.run(process(request))
    budget = get_settings().llm_budget_usd
    reached = " (budget reached: the rest is rules-only)" if result.budget_exhausted else ""
    print(f"Batch {args.batch_id}: {result.processed:,} notes processed, {result.skipped:,} "
          f"unchanged; {result.mentions:,} mentions ({result.review:,} to review), "
          f"{result.conditions:,} conditions; LLM ${result.llm_cost_usd:.4f} of ${budget:.2f}"
          f"{reached}")  # fmt: skip
