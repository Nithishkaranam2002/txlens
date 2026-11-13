"""`txlens-notes process`: run clinic notes through the note NLP (Temporal batch workflow).

`txlens-notes rebuild` recomputes the OMOP rows of notes already processed (after a change in
how mentions become OMOP rows) without running the NLP again.

`process` needs a running worker (`txlens-worker`). The batch shares one LLM budget (LLM_BUDGET_USD,
capped again by --max-cost); notes already processed with the same text and system are skipped.
"""

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import text

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.notes.results import rebuild_cdm_rows
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


def rebuild(tenants: list[str]) -> None:
    """Recompute every processed note's OMOP rows, one tenant at a time (RLS on)."""
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    for tenant in tenants or list(center_tenants()):
        with tenant_transaction(engine, tenant) as conn:
            notes = conn.execute(text("SELECT note_source_value FROM app.note_processed"))
            totals = [rebuild_cdm_rows(conn, n) for (n,) in notes.all()]
        print(
            f"{tenant}: {len(totals):,} notes, {sum(c for c, _ in totals):,} conditions, "
            f"{sum(h for _, h in totals):,} history observations"
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
    again = commands.add_parser("rebuild", help="recompute OMOP rows of processed notes")
    again.add_argument("--tenant", action="append", default=[], help="repeat for several")
    args = parser.parse_args()
    if args.command == "rebuild":
        rebuild(args.tenant)
        return
    request = NotesBatchRequest(args.batch_id, args.tenant, args.limit, not args.no_llm)
    result = asyncio.run(process(request))
    budget = get_settings().llm_budget_usd
    reached = " (budget reached: the rest is rules-only)" if result.budget_exhausted else ""
    if result.llm_pending:
        reached += (f"; {result.llm_pending:,} notes pending (no API key and no cached answer: "
                    "rules-only until a run with OPENAI_API_KEY)")  # fmt: skip
    print(f"Batch {args.batch_id}: {result.processed:,} notes processed, {result.skipped:,} "
          f"unchanged; {result.mentions:,} mentions ({result.review:,} to review), "
          f"{result.conditions:,} conditions; LLM ${result.llm_cost_usd:.4f} of ${budget:.2f}"
          f"{reached}")  # fmt: skip
