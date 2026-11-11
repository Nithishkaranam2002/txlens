"""`txlens-biopsies process PATH...`: run biopsy reports through the document workflow.

Each PDF's tenant is its folder name (raw/pathology/<tenant>/<file>.pdf), as in the pipeline.
The batch shares one LLM budget (LLM_BUDGET_USD) and stops starting documents when it runs
out. Documents that need a human keep waiting for review after this command returns.
"""

import argparse
import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from txlens_backend.config import get_settings
from txlens_backend.workflows.biopsy import (
    TASK_QUEUE,
    BatchRequest,
    BatchResult,
    BiopsyBatchWorkflow,
    DocumentRequest,
)
from txlens_backend.workflows.cli import connect


def pdfs(paths: list[Path]) -> list[Path]:
    """Every PDF in the given files and folders, sorted."""
    found: set[Path] = set()
    for path in paths:
        found |= set(path.rglob("*.pdf")) if path.is_dir() else {path}
    return sorted(p.resolve() for p in found)


def document_request(path: Path, batch_id: str, timeout_seconds: int) -> DocumentRequest:
    """A request for one PDF; its tenant is the folder it sits in."""
    tenant = path.parent.name
    source = f"pathology/{tenant}/{path.name}"
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    return DocumentRequest(tenant, sha, str(path), source, batch_id, timeout_seconds)


async def process(paths: list[Path], batch_id: str, concurrency: int) -> BatchResult:
    """Start the batch workflow and wait until every document is assessed."""
    timeout = int(timedelta(days=get_settings().review_timeout_days).total_seconds())
    documents = [document_request(p, batch_id, timeout) for p in pdfs(paths)]
    client = await connect()
    return await client.execute_workflow(
        BiopsyBatchWorkflow.run,
        BatchRequest(batch_id, documents, concurrency),
        id=f"biopsy-batch-{batch_id}",
        task_queue=TASK_QUEUE,
    )


def main() -> None:
    """Entry point."""
    settings = get_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("process", help="process PDF files or folders")
    run.add_argument("paths", nargs="+", type=Path)
    run.add_argument("--batch-id", default=f"batch-{datetime.now(UTC):%Y%m%d-%H%M%S}")
    run.add_argument("--concurrency", type=int, default=settings.llm_concurrency)
    args = parser.parse_args()
    result = asyncio.run(process(args.paths, args.batch_id, args.concurrency))
    print(f"Batch {args.batch_id}: {result.statuses}, not started: {result.not_started}, "
          f"cost ${result.cost_usd:.4f} (budget ${settings.llm_budget_usd:.2f})")  # fmt: skip
