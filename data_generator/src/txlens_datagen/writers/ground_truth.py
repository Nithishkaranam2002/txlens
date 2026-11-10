"""Ground-truth files: the correct structured answers, written as CSV and JSONL."""

import csv
import dataclasses
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, TextIO

from txlens_datagen.models import (
    Biopsy,
    Encounter,
    InjuryEpisode,
    LabResult,
    MedOrder,
    Outcome,
    Patient,
    PatientRecord,
    RejectionEpisode,
    ScheduledTest,
)
from txlens_datagen.writers.lab_feeds import FeedIssue, RawLocation

TABLES: dict[str, type] = {
    "patients": Patient,
    "outcomes": Outcome,
    "rejection_episodes": RejectionEpisode,
    "injury_episodes": InjuryEpisode,
    "biopsies": Biopsy,
    "medications": MedOrder,
    "surveillance_schedule": ScheduledTest,
    "encounters": Encounter,
    "lab_feed_issues": FeedIssue,
}
LAB_COLUMNS = [f.name for f in dataclasses.fields(LabResult)] + ["raw_file", "raw_row"]
NOTE_COLUMNS = ["note_id", "patient_id", "encounter_id", "note_date", "note_type", "file"]


def cell(value: Any) -> str:
    """CSV text for one value: ISO dates, lowercase booleans, empty for None."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


class GroundTruthWriter:
    """Streams ground-truth rows into one open file per table."""

    def __init__(self, out_dir: Path) -> None:
        out_dir.mkdir(parents=True, exist_ok=True)
        self.files: list[TextIO] = []
        self.tables = {
            name: self.open_csv(out_dir / f"{name}.csv", fields_of(cls))
            for name, cls in TABLES.items()
        }
        self.labs = self.open_csv(out_dir / "labs.csv", LAB_COLUMNS)
        self.notes = self.open_csv(out_dir / "notes.csv", NOTE_COLUMNS)
        self.facts = (out_dir / "note_facts.jsonl").open("w", encoding="utf-8")
        self.files.append(self.facts)

    def open_csv(self, path: Path, columns: list[str]) -> "csv.DictWriter[str]":
        """Open a CSV and write its header."""
        handle = path.open("w", newline="", encoding="utf-8")
        self.files.append(handle)
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        return writer

    def rows(self, table: str, items: list[Any]) -> None:
        """Write dataclass instances to a table."""
        writer = self.tables[table]
        for item in items:
            writer.writerow({k: cell(v) for k, v in dataclasses.asdict(item).items()})

    def add(
        self,
        record: PatientRecord,
        locations: dict[str, RawLocation],
        issues: list[FeedIssue],
        note_files: dict[str, str],
    ) -> None:
        """Write every ground-truth row for one patient."""
        self.rows("patients", [record.patient])
        self.rows("outcomes", [record.outcome])
        self.rows("rejection_episodes", record.rejections)
        self.rows("injury_episodes", record.injuries)
        self.rows("biopsies", record.biopsies)
        self.rows("medications", record.meds)
        self.rows("surveillance_schedule", record.schedule)
        self.rows("encounters", record.encounters)
        self.rows("lab_feed_issues", issues)
        for lab in record.labs:
            row = {k: cell(v) for k, v in dataclasses.asdict(lab).items()}
            where = locations[lab.lab_id]
            self.labs.writerow(row | {"raw_file": where.raw_file, "raw_row": where.raw_row})
        for note in record.notes:
            meta = {k: cell(getattr(note, k)) for k in NOTE_COLUMNS[:-1]}
            self.notes.writerow(meta | {"file": note_files.get(note.note_id, "")})
            for fact in note.facts:
                self.facts.write(json.dumps(dataclasses.asdict(fact)) + "\n")

    def close(self) -> None:
        """Close all files."""
        for handle in self.files:
            handle.close()


def fields_of(cls: type) -> list[str]:
    """Column names of a dataclass."""
    return [f.name for f in dataclasses.fields(cls)]
