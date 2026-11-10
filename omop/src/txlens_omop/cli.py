"""`txlens-vocab`: load an Athena vocabulary download, or extract a small subset of it.

Uses the owner database URL (DATABASE_URL): the app role can only read vocabularies.
"""

import argparse
import logging
import time
from pathlib import Path

import psycopg
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from txlens_omop.athena import AthenaSource
from txlens_omop.check import check_codes, format_report, suggest_ingredients
from txlens_omop.codes import all_codes, uncoded_drugs
from txlens_omop.loader import load_vocabulary, vocab_table_sizes
from txlens_omop.subset import extract_subset


class VocabSettings(BaseSettings):
    """DATABASE_URL from the environment or `.env`, read exactly as the backend reads it."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: SecretStr = SecretStr("")


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
    load.add_argument("--database-url", help="default: DATABASE_URL from the environment or .env")
    check = commands.add_parser("check", help="check every code we use against the vocabulary")
    check.add_argument("--database-url", help="default: DATABASE_URL from the environment or .env")
    subset = commands.add_parser("subset", help="extract a small subset around our codes")
    subset.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    subset.add_argument("--out", type=Path, default=DEFAULT_SUBSET)
    return parser.parse_args(argv)


def resolve_source(path: Path) -> AthenaSource:
    """A zip file, or a folder holding one zip or the CSV files."""
    return AthenaSource(path) if path.is_file() else AthenaSource.find(path)


def run_load(source: AthenaSource, database_url: str) -> None:
    """Load and print row counts, warnings and table sizes."""
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


def run_check(database_url: str) -> int:
    """Print the code report and RxNorm suggestions; 1 if any code has a problem."""
    with psycopg.connect(libpq_url(database_url)) as conn:
        results = check_codes(conn, all_codes())
        print(format_report(results))
        uncoded = uncoded_drugs()
        print(
            "\nSuggestions for drugs we write without an RxNorm code:"
            + ("" if uncoded else " none")
        )
        for drug in uncoded:
            found = suggest_ingredients(conn, drug)
            options = (
                ", ".join(f"{c.concept_code} ({c.concept_name})" for c in found) or "none found"
            )
            print(f"  {drug}: {options}")
    return 1 if any(r.problems for r in results) else 0


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
    if args.command == "check":
        return run_check(database_url(args.database_url))
    source = resolve_source(args.source)
    if args.command == "load":
        run_load(source, database_url(args.database_url))
    else:
        run_subset(source, args.out)
    return 0


def database_url(given: str | None) -> str:
    """--database-url, else DATABASE_URL from the environment or .env."""
    url = given or VocabSettings().database_url.get_secret_value()
    if not url:
        raise SystemExit("Set DATABASE_URL (owner role) or pass --database-url.")
    return url


if __name__ == "__main__":
    raise SystemExit(main())
