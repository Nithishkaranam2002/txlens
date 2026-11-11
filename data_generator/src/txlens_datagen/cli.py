"""Command-line entry point for the synthetic data generator (`make generate-data`)."""

import argparse
from dataclasses import replace
from pathlib import Path

from txlens_datagen.config import GeneratorConfig
from txlens_datagen.generate import Summary, generate
from txlens_datagen.oncology import OncologyConfig, generate_oncology


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Read command-line options."""
    defaults = GeneratorConfig()
    parser = argparse.ArgumentParser(description="Generate SYNTHETIC transplant patient data.")
    parser.add_argument("--seed", type=int, default=defaults.seed)
    parser.add_argument("--n-patients", type=int, default=defaults.n_patients)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--no-pdfs", action="store_true", help="skip pathology PDFs")
    parser.add_argument("--no-note-text", action="store_true", help="skip clinical note text")
    parser.add_argument(
        "--n-oncology", type=int, default=OncologyConfig().n_patients,
        help="size of the small oncology cohort (0 = none)",
    )  # fmt: skip
    return parser.parse_args(argv)


def format_summary(summary: Summary) -> str:
    """Human-readable end-of-run report."""

    def counts(counter: dict[str, int]) -> str:
        return ", ".join(f"{k}={v}" for k, v in sorted(counter.items())) or "none"

    return "\n".join(
        [
            f"Patients by center/organ: {counts(summary.patients_by_center_organ)}",
            f"Rejections by type:       {counts(summary.rejections_by_type)}",
            f"Injury false alarms:      {counts(summary.injury_false_alarms_by_cause)}",
            f"Graft losses: {summary.graft_losses}   Deaths: {summary.deaths} "
            f"(with functioning graft: {summary.deaths_with_functioning_graft})",
            f"Lab results: {summary.lab_results}   Raw lab rows: {summary.raw_lab_rows} "
            f"(injected issues: {summary.lab_feed_issues})",
            f"Notes: {summary.notes} ({summary.note_facts} facts)   Biopsies: {summary.biopsies}"
            f"   PDFs: {summary.pdfs}",
            f"Total size on disk: {summary.total_bytes / 1_000_000:.1f} MB",
        ]
    )


def main(argv: list[str] | None = None) -> int:
    """Run the generator and print a summary."""
    args = parse_args(argv)
    config = replace(GeneratorConfig(), seed=args.seed, n_patients=args.n_patients)
    summary = generate(config, args.out, pdfs=not args.no_pdfs, note_text=not args.no_note_text)
    print(f"Generated {config.n_patients} synthetic patients (seed {config.seed}) in {args.out}/")
    print(format_summary(summary))
    if args.n_oncology:
        oncology = replace(OncologyConfig(), n_patients=args.n_oncology)
        records = generate_oncology(oncology, config.seed, args.out)
        recurrences = sum(r.outcome.recurrence_date is not None for r in records)
        print(f"Oncology cohort: {len(records)} patients, {recurrences} recurrences "
              f"({args.out}/oncology/)")  # fmt: skip
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
