"""A small SYNTHETIC oncology cohort with a generic circulating tumor DNA marker.

It exists to show that the surveillance engine is disease-agnostic: the same engine runs it
with a different config. Patients finish curative treatment, then have a tumor DNA test every
3 months. Some patients recur: their marker becomes detectable and rises 30-120 days before
the recurrence is found clinically (imaging). A few healthy patients get a one-off low
positive (a false alarm). Written to `<out>/oncology/` as CSV, ground truth included.
"""

import csv
import dataclasses
import math
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from txlens_datagen.config import DEFAULT_CENTERS, Center
from txlens_datagen.rand import (
    STREAM_ONCOLOGY,
    chance,
    make_uuid,
    pick_weighted,
    randint,
    rng_for,
    uniform,
)
from txlens_datagen.schedule import month_to_day
from txlens_datagen.writers.ground_truth import cell

MARKER = "tumor_dna"
UNIT = "MTM/mL"  # mean tumor molecules per mL of plasma
LIMIT_OF_DETECTION = 0.1


@dataclass(frozen=True)
class OncologyConfig:
    """Size and behaviour of the oncology cohort."""

    n_patients: int = 200
    centers: tuple[Center, ...] = DEFAULT_CENTERS
    treatment_end_window: tuple[date, date] = (date(2022, 1, 1), date(2024, 12, 31))
    data_cutoff: date = date(2026, 9, 30)
    max_follow_up_days: int = 1096
    interval_months: int = 3
    jitter_days: int = 7
    missed_rate: float = 0.07
    recurrence_rate: float = 0.30
    recurrence_day: tuple[int, int] = (150, 900)  # after treatment end
    lead_days: tuple[int, int] = (30, 120)  # marker signal before clinical recurrence
    false_positive_rate: float = 0.02  # per test, healthy patients
    death_rate: float = 0.03


@dataclass(frozen=True)
class OncologyPatient:
    """One patient after curative cancer treatment."""

    patient_id: str
    tenant_id: str
    program: str
    treatment_end_date: date


@dataclass(frozen=True)
class OncologyOutcome:
    """How follow-up ended. Surveillance stops at recurrence or death."""

    patient_id: str
    last_follow_up_date: date
    recurrence_date: date | None
    signal_onset_date: date | None
    death_date: date | None


@dataclass(frozen=True)
class TumorDnaResult:
    """One tumor DNA test result (0.0 = not detected)."""

    lab_id: str
    patient_id: str
    collected_at: datetime
    lab: str
    value: float
    unit: str
    reason: str


@dataclass(frozen=True)
class DueTest:
    """A scheduled test: due month, actual draw date, whether it was done."""

    patient_id: str
    month: int
    due_date: date
    done: bool
    lab_id: str | None


@dataclass
class OncologyRecord:
    """Everything generated for one patient."""

    patient: OncologyPatient
    outcome: OncologyOutcome
    labs: list[TumorDnaResult] = field(default_factory=list)
    schedule: list[DueTest] = field(default_factory=list)


def marker_value(day: int, onset: int | None, peak_day: int | None, noise: float) -> float:
    """Signal on a day after treatment end: rises exponentially from onset to ~1-5 at peak."""
    if onset is None or peak_day is None or day < onset:
        return 0.0
    growth = math.log(40) / max(peak_day - onset, 1)  # 0.05 at onset -> ~2 at clinical recurrence
    return round(0.05 * math.exp(growth * (day - onset)) * noise, 2)


def build(index: int, config: OncologyConfig, seed: int) -> OncologyRecord:
    """One oncology patient, reproducible from (seed, index)."""
    rng = rng_for(seed, STREAM_ONCOLOGY, index)
    tenant = pick_weighted(rng, {c.tenant_id: c.weight for c in config.centers})
    first, last = config.treatment_end_window
    end = first + timedelta(days=randint(rng, 0, (last - first).days))
    follow_up = min(config.max_follow_up_days, (config.data_cutoff - end).days)
    recurrence = onset = death = None
    if chance(rng, config.recurrence_rate):
        day = randint(rng, *config.recurrence_day)
        if day < follow_up:
            recurrence, onset = day, day - randint(rng, *config.lead_days)
    elif chance(rng, config.death_rate):
        death = randint(rng, 180, follow_up)
    stop = min(d for d in (recurrence, death, follow_up) if d is not None)
    patient = OncologyPatient(make_uuid(rng), tenant, "oncology", end)
    record = OncologyRecord(patient, OncologyOutcome(
        patient.patient_id, end + timedelta(days=stop),
        end + timedelta(days=recurrence) if recurrence is not None else None,
        end + timedelta(days=onset) if onset is not None else None,
        end + timedelta(days=death) if death is not None else None,
    ))  # fmt: skip
    month = config.interval_months
    while month_to_day(month) < stop:
        day = month_to_day(month) + randint(rng, -config.jitter_days, config.jitter_days)
        drawn = day < stop and not chance(rng, config.missed_rate)
        lab_id = f"OL-{index + 1:06d}-{len(record.labs) + 1:04d}" if drawn else None
        record.schedule.append(DueTest(patient.patient_id, month, end + timedelta(days=day),
                                       drawn, lab_id))  # fmt: skip
        if lab_id is not None:
            value = marker_value(day, onset, recurrence, uniform(rng, 0.7, 1.4))
            if value == 0.0 and onset is None and chance(rng, config.false_positive_rate):
                value = round(uniform(rng, LIMIT_OF_DETECTION, 0.3), 2)
            at = datetime.combine(end + timedelta(days=day), time(8, randint(rng, 0, 59)))
            record.labs.append(TumorDnaResult(lab_id, patient.patient_id, at, MARKER, value,
                                              UNIT, "surveillance"))  # fmt: skip
        month += config.interval_months
    return record


def write_csv(path: Path, cls: type, items: Sequence[Any]) -> None:
    """Dataclass instances as CSV (ISO dates, lowercase booleans, empty for None)."""
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[f.name for f in dataclasses.fields(cls)])
        writer.writeheader()
        for item in items:
            writer.writerow({k: cell(v) for k, v in dataclasses.asdict(item).items()})


def generate_oncology(config: OncologyConfig, seed: int, out_dir: Path) -> list[OncologyRecord]:
    """Write the cohort to `out_dir/oncology/*.csv` (replacing an earlier run)."""
    folder = out_dir / "oncology"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    records = [build(i, config, seed) for i in range(config.n_patients)]
    write_csv(folder / "patients.csv", OncologyPatient, [r.patient for r in records])
    write_csv(folder / "outcomes.csv", OncologyOutcome, [r.outcome for r in records])
    write_csv(folder / "labs.csv", TumorDnaResult, [lab for r in records for lab in r.labs])
    write_csv(folder / "surveillance_schedule.csv", DueTest,
              [t for r in records for t in r.schedule])  # fmt: skip
    return records
