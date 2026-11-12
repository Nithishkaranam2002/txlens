"""Graft-rejection records in the database vs the generator's ground-truth rejection episodes.

A past rejection that the note NLP reads as current becomes a rejection condition dated at a
later clinic visit. The patient is still a rejection patient, but now with a rejection at
the wrong time, so time-windowed cohorts ("rejection more than 180 days after transplant")
gain patients who do not belong. `make rejection-report` checks both, as the app login (Row-
Level Security on, one center at a time), and writes evals/reports/rejection_records.md:
- every note-derived rejection condition must sit just after a true rejection biopsy (the
  biopsy-result note); one that does not is an unsupported record;
- late-rejection cohorts from all rejection records (pathology reports, notes, coded data)
  vs the ground truth, matched by (center, MRN).
"""

import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from sqlalchemy import Engine, text

from txlens_backend.config import get_settings
from txlens_backend.db import check_role_is_rls_bound, make_engine, tenant_transaction
from txlens_backend.db_setup import center_tenants
from txlens_backend.rejection import rejection_concept_ids

Key = tuple[str, str]  # (center, MRN)
# A biopsy-result note is written within this many days after the biopsy.
RESULT_NOTE_DAYS = 14
LATE_AFTER_DAYS = (180, 365)
RECORDS_SQL = text(
    """
    SELECT i.mrn, e.condition_start_date,
           EXISTS (SELECT 1 FROM app.note_cdm_row r
                   WHERE r.cdm_id = e.condition_occurrence_id) AS from_note
    FROM cdm.condition_occurrence e
    JOIN app.patient_identity i ON i.person_id = e.person_id
    WHERE e.condition_concept_id = ANY(:ids) OR e.condition_source_concept_id = ANY(:ids)
    """
)
IN_REVIEW_SQL = text(
    "SELECT DISTINCT i.mrn FROM app.document d "
    "JOIN app.patient_identity i ON i.person_id = d.person_id WHERE d.status = 'in_review'"
)


@dataclass(frozen=True)
class Record:
    """One rejection condition in the database."""

    key: Key
    on: date
    from_note: bool


@dataclass(frozen=True)
class Truth:
    """Transplant date and true rejection biopsy dates per patient."""

    transplant: dict[Key, date]
    rejections: dict[Key, list[date]]


def load_truth(folder: Path) -> Truth:
    with (folder / "patients.csv").open(encoding="utf-8") as handle:
        patients = list(csv.DictReader(handle))
    key_of = {p["patient_id"]: (p["tenant_id"], p["mrn"]) for p in patients}
    transplant = {key_of[p["patient_id"]]: date.fromisoformat(p["transplant_date"])
                  for p in patients}  # fmt: skip
    rejections: dict[Key, list[date]] = defaultdict(list)
    with (folder / "rejection_episodes.csv").open(encoding="utf-8") as handle:
        for episode in csv.DictReader(handle):
            rejections[key_of[episode["patient_id"]]].append(
                date.fromisoformat(episode["biopsy_date"])
            )
    return Truth(transplant, dict(rejections))


def read_records(engine: Engine, tenants: list[str]) -> tuple[list[Record], set[Key]]:
    """Every rejection condition of every center, and the patients with a pathology report
    waiting for review, read center by center under RLS."""
    records, waiting = [], set()
    for tenant in tenants:
        with tenant_transaction(engine, tenant) as conn:
            ids = sorted(rejection_concept_ids(conn))
            for mrn, on, from_note in conn.execute(RECORDS_SQL, {"ids": ids}):
                records.append(Record((tenant, str(mrn)), on, bool(from_note)))
            waiting |= {(tenant, str(mrn)) for mrn in conn.execute(IN_REVIEW_SQL).scalars()}
    return records, waiting


def supported(record: Record, truth: Truth) -> bool:
    """True if a true rejection biopsy lies 0..RESULT_NOTE_DAYS days before the record."""
    return any(
        timedelta(0) <= record.on - biopsy <= timedelta(RESULT_NOTE_DAYS)
        for biopsy in truth.rejections.get(record.key, [])
    )


def late(dates: dict[Key, list[date]], transplant: dict[Key, date], days: int) -> set[Key]:
    """Patients with a rejection more than `days` days after their transplant."""
    return {k for k, ds in dates.items()
            if k in transplant and any((d - transplant[k]).days > days for d in ds)}  # fmt: skip


def render(records: list[Record], waiting: set[Key], truth: Truth) -> str:
    notes = [r for r in records if r.from_note]
    unsupported = [r for r in notes if not supported(r, truth)]
    found: dict[Key, list[date]] = defaultdict(list)
    for r in records:
        found[r.key].append(r.on)
    lines = [
        "# Rejection records: database vs ground truth",
        "",
        f"Generated {date.today().isoformat()} by `make rejection-report` "
        "(evals/src/txlens_evals/rejection_records.py). Synthetic data only.",
        "",
        "## Rejection conditions read from notes",
        "",
        "| Records | Patients | Supported by a true rejection biopsy | Unsupported |",
        "|---|---|---|---|",
        f"| {len(notes):,} | {len({r.key for r in notes}):,} | {len(notes) - len(unsupported):,} "
        f"| {len(unsupported):,} |",
        "",
        f"Supported: a true rejection biopsy 0-{RESULT_NOTE_DAYS} days before the record (the "
        "biopsy-result note). An unsupported record is a rejection dated where none happened, "
        "usually a past rejection read as current.",
        "",
        "## Late-rejection cohorts (all rejection records)",
        "",
        "| Rule | Expected | Actual | Missing | of which report in review | Extra | Result |",
        "|---|---|---|---|---|---|---|",
    ]
    for days in LATE_AFTER_DAYS:
        expected = late(truth.rejections, truth.transplant, days)
        actual = late(found, truth.transplant, days)
        missing, extra = expected - actual, len(actual - expected)
        queued = len(missing & waiting)
        result = "FAIL" if extra or queued < len(missing) else "PASS"
        if result == "PASS" and missing:
            result = "PASS once reviewed"
        lines.append(
            f"| rejection > {days} days after transplant | {len(expected):,} | {len(actual):,} "
            f"| {len(missing):,} | {queued:,} | {extra:,} | {result} |"
        )  # fmt: skip
    lines += [
        "",
        "Extra patients would mean a rejection dated where none happened. A missing patient "
        "whose pathology report is in the review queue appears once a person approves it.",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--truth", type=Path, default=Path("data/ground_truth"))
    parser.add_argument("--out", type=Path, default=Path("evals/reports/rejection_records.md"))
    args = parser.parse_args(argv)
    engine = make_engine(get_settings().app_database_url.get_secret_value())
    check_role_is_rls_bound(engine)
    records, waiting = read_records(engine, list(center_tenants()))
    report = render(records, waiting, load_truth(args.truth))
    engine.dispose()
    args.out.write_text(report, encoding="utf-8")
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
