"""Run the generator: build each patient's truth, then stream raw files and ground truth."""

import json
import shutil
from collections import Counter
from dataclasses import dataclass, field, fields
from pathlib import Path

from txlens_datagen.config import Center, GeneratorConfig
from txlens_datagen.models import PatientRecord
from txlens_datagen.rand import STREAM_FEED, STREAM_PDF, rng_for
from txlens_datagen.record import build_record
from txlens_datagen.writers.fhir import write_bundle
from txlens_datagen.writers.ground_truth import GroundTruthWriter
from txlens_datagen.writers.lab_feeds import LabFeedWriter
from txlens_datagen.writers.notes import write_note
from txlens_datagen.writers.pathology_pdf import write_report

FEED_PART_LABS, FEED_PART_FHIR = 0, 1


@dataclass
class Summary:
    """Counts reported at the end of a run and saved in manifest.json."""

    patients_by_center_organ: Counter[str] = field(default_factory=Counter)
    rejections_by_type: Counter[str] = field(default_factory=Counter)
    injury_false_alarms_by_cause: Counter[str] = field(default_factory=Counter)
    graft_losses: int = 0
    deaths: int = 0
    deaths_with_functioning_graft: int = 0
    lab_results: int = 0
    raw_lab_rows: int = 0
    lab_feed_issues: int = 0
    notes: int = 0
    note_facts: int = 0
    biopsies: int = 0
    pdfs: int = 0
    total_bytes: int = 0

    def add(self, record: PatientRecord, raw_rows: int, issues: int) -> None:
        """Count one patient."""
        self.patients_by_center_organ[f"{record.patient.tenant_id}/{record.patient.organ}"] += 1
        self.rejections_by_type.update(e.rejection_type for e in record.rejections)
        self.injury_false_alarms_by_cause.update(e.cause for e in record.injuries)
        self.graft_losses += record.outcome.graft_loss_date is not None
        self.deaths += record.outcome.death_date is not None
        self.deaths_with_functioning_graft += record.outcome.death_with_functioning_graft
        self.lab_results += len(record.labs)
        self.raw_lab_rows += raw_rows
        self.lab_feed_issues += issues
        self.notes += len(record.notes)
        self.note_facts += sum(len(n.facts) for n in record.notes)
        self.biopsies += len(record.biopsies)

    def as_dict(self) -> dict[str, object]:
        """Plain-dict form for JSON (Counters become sorted dicts)."""
        return {
            f.name: dict(sorted(v.items())) if isinstance(v, Counter) else v
            for f in fields(self)
            for v in [getattr(self, f.name)]
        }


def reset_output(out_dir: Path) -> tuple[Path, Path]:
    """Remove output from an earlier run so stale files never mix with new ones."""
    raw, truth = out_dir / "raw", out_dir / "ground_truth"
    for path in (raw, truth):
        if path.exists():
            shutil.rmtree(path)
    return raw, truth


def directory_size(path: Path) -> int:
    """Total bytes of all files under a directory."""
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def generate(
    config: GeneratorConfig, out_dir: Path, *, pdfs: bool = True, note_text: bool = True
) -> Summary:
    """Generate `config.n_patients` patients into out_dir/raw and out_dir/ground_truth."""
    raw, truth_dir = reset_output(out_dir)
    centers: dict[str, Center] = {c.tenant_id: c for c in config.centers}
    feeds = LabFeedWriter(raw / "labs", config.mess)
    feeds.open(config.centers)
    truth = GroundTruthWriter(truth_dir)
    summary = Summary()
    try:
        for index in range(config.n_patients):
            record = build_record(index, config, with_notes=note_text)
            center = centers[record.patient.tenant_id]
            locations, issues = feeds.add(
                record, center, rng_for(config.seed, STREAM_FEED, index, FEED_PART_LABS)
            )
            write_bundle(
                record,
                center,
                raw / "fhir",
                rng_for(config.seed, STREAM_FEED, index, FEED_PART_FHIR),
            )
            note_files = {n.note_id: write_note(n, center.tenant_id, raw) for n in record.notes}
            pdf_files = write_pdfs(record, center, raw, config, index) if pdfs else {}
            truth.add(record, locations, issues, note_files, pdf_files)
            summary.pdfs += len(pdf_files)
            duplicates = sum(i.issue == "duplicate" for i in issues)
            summary.add(record, len(record.labs) + duplicates, len(issues))
    finally:
        feeds.close()
        truth.close()
    write_manifest(config, out_dir, summary, pdfs=pdfs, note_text=note_text)
    return summary


def write_pdfs(
    record: PatientRecord, center: Center, raw: Path, config: GeneratorConfig, index: int
) -> dict[str, str]:
    """One scanned-looking pathology PDF per biopsy; returns biopsy_id -> relative path."""
    return {
        b.biopsy_id: write_report(
            b,
            record.patient,
            center.name,
            raw,
            config.pdf_dpi,
            rng_for(config.seed, STREAM_PDF, index, n),
        )
        for n, b in enumerate(record.biopsies)
    }


def write_manifest(
    config: GeneratorConfig, out_dir: Path, summary: Summary, *, pdfs: bool, note_text: bool
) -> None:
    """Record the run settings and counts next to the data."""
    summary.total_bytes = directory_size(out_dir / "raw") + directory_size(out_dir / "ground_truth")
    manifest = {
        "seed": config.seed,
        "n_patients": config.n_patients,
        "data_cutoff": config.data_cutoff.isoformat(),
        "pdfs": pdfs,
        "note_text": note_text,
        "summary": summary.as_dict(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
