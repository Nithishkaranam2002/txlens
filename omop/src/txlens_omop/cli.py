"""`txlens-vocab`: load an Athena vocabulary download, or extract a small subset of it.

Uses the owner database URL (DATABASE_URL): the app role can only read vocabularies.
"""

import argparse
import logging
import os
import time
from pathlib import Path

import psycopg

from txlens_omop.athena import AthenaSource
from txlens_omop.codes import all_codes
from txlens_omop.loader import load_vocabulary, vocab_table_sizes
from txlens_omop.subset import extract_subset

DEFAULT_SOURCE = Path("omop/athena")
DEFAULT_SUBSET = Path("omop/vocab_subset")


def libpq_url(url: str) -> str:
    """Accept SQLAlchemy-style URLs (`postgresql+psycopg://`) as well as plain libpq ones."""
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Read command-line options."""
    parser = argparse.ArgumentParser(description="OMOP vocabulary tools.")
    commands = parser.add_subparsers(dest="command", required=True)
    load = commands.add_parser("load", help="bulk-load vocabularies into the vocab schema")
    load.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="zip, or folder of CSVs")
    load.add_argument("--database-url", default=os.environ.get("DATABASE_URL", ""))
    subset = commands.add_parser("subset", help="extract a small subset around our codes")
    subset.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    subset.add_argument("--out", type=Path, default=DEFAULT_SUBSET)
    return parser.parse_args(argv)


def resolve_source(path: Path) -> AthenaSource:
    """A zip file, or a folder holding one zip or the CSV files."""
    return AthenaSource(path) if path.is_file() else AthenaSource.find(path)


def run_load(source: AthenaSource, database_url: str) -> None:
    """Load and print row counts, warnings and table sizes."""
    if not database_url:
        raise SystemExit("Set DATABASE_URL (owner role) or pass --database-url.")
    started = time.monotonic()
    with psycopg.connect(libpq_url(database_url)) as conn:
        report = load_vocabulary(conn, source)
        sizes = vocab_table_sizes(conn)
    print(f"Loaded {source.path.name} in {time.monotonic() - started:.0f}s")
    for table, count in report.rows.items():
        print(f"  {table:<22} {count:>12,} rows  {sizes.get(table, 0) / 1e6:>9,.1f} MB")
    total_rows, total_mb = sum(report.rows.values()), sum(sizes.values()) / 1e6
    print(f"  {'total':<22} {total_rows:>12,} rows  {total_mb:>9,.1f} MB")
    print(
        "Concepts by vocabulary: "
        + ", ".join(f"{v}={n:,}" for v, n in report.concepts_by_vocabulary.most_common())
    )
    for warning in report.warnings:
        print(f"WARNING: {warning}")


def run_subset(source: AthenaSource, out: Path) -> None:
    """Extract the subset and print what it contains."""
    seeds = [(c.vocabulary_id, c.code) for c in all_codes()]
    report = extract_subset(source, seeds, out)
    print(f"Subset written to {out}/: {report.concepts:,} concepts")
    for table, count in report.rows.items():
        print(f"  {table:<22} {count:>10,} rows")
    for vocabulary, code in report.seeds_missing:
        print(f"WARNING: seed code not found: {vocabulary} {code}")


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    args = parse_args(argv)
    source = resolve_source(args.source)
    if args.command == "load":
        run_load(source, args.database_url)
    else:
        run_subset(source, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
