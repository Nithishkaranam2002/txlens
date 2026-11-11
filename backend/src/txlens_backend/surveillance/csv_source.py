"""Patient series from the generator's ground-truth CSVs (the replay path).

The evaluation replays the engine over these files, and a consistency test checks that they
give the same flags as the database path. Expected files in `root`:

- `patients.csv`: patient_id, tenant_id, the program (`program`, or `organ` for transplant
  data) and the anchor date column named by the config (e.g. `transplant_date`);
- `labs.csv`: patient_id, collected_at, lab (= marker key), value;
- optional `outcomes.csv`: death_date and/or graft_loss_date end surveillance;
- optional `lab_feed_issues.csv`: labs the pipeline cannot load (see `skip_feed_issues`).
"""

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from txlens_backend.surveillance.config import SurveillanceConfig
from txlens_backend.surveillance.model import Observation, PatientSeries

# Labs whose raw feed row is unusable, so they never reach the database.
UNLOADABLE_ISSUES = frozenset({"bad_date", "missing_value"})


@dataclass(frozen=True)
class CsvPatient:
    """A patient's series plus the tenant it belongs to."""

    tenant_id: str
    series: PatientSeries


def rows(path: Path) -> list[dict[str, str]]:
    """All rows of a CSV file, or none if it does not exist."""
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def end_dates(root: Path) -> dict[str, date]:
    """Earliest of death and graft loss per patient."""
    ends: dict[str, date] = {}
    for row in rows(root / "outcomes.csv"):
        found = [date.fromisoformat(row[c]) for c in ("death_date", "graft_loss_date")
                 if row.get(c)]  # fmt: skip
        if found:
            ends[row["patient_id"]] = min(found)
    return ends


def load(
    root: Path, config: SurveillanceConfig, *, skip_feed_issues: bool
) -> dict[str, CsvPatient]:
    """Series for every patient in the config's program, keyed by patient id.

    `skip_feed_issues=True` leaves out labs the pipeline cannot load, so the result matches
    what a pipeline-loaded database holds.
    """
    skipped = (
        {r["lab_id"] for r in rows(root / "lab_feed_issues.csv") if r["issue"] in UNLOADABLE_ISSUES}
        if skip_feed_issues else set()
    )  # fmt: skip
    patients = {
        r["patient_id"]: r for r in rows(root / "patients.csv")
        if r.get("program", r.get("organ")) == config.program
    }  # fmt: skip
    # Collected-at timestamps keep same-day results in the order the database returns them.
    values: dict[str, dict[str, list[tuple[datetime, Observation]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for lab in rows(root / "labs.csv"):
        wanted = lab["patient_id"] in patients and lab["lab"] in config.markers
        if wanted and lab["value"] and lab.get("lab_id") not in skipped:
            at = datetime.fromisoformat(lab["collected_at"])
            values[lab["patient_id"]][lab["lab"]].append(
                (at, Observation(at.date(), float(lab["value"])))
            )
    ends = end_dates(root)
    return {
        pid: CsvPatient(row["tenant_id"], PatientSeries(
            patient_key=pid,
            program=config.program,
            anchor_date=date.fromisoformat(row[config.anchor]),
            end_date=ends.get(pid),
            observations={m: tuple(o for _, o in sorted(obs, key=lambda x: x[0]))
                          for m, obs in values[pid].items()},
        ))
        for pid, row in patients.items()
    }  # fmt: skip
